#!/usr/bin/env python3
"""Unified Meridian gateway with Markdown memory, proactive heartbeat, and dynamic skills."""

from __future__ import annotations

import base64
import json
import os
import queue
import re
import subprocess
import sys
import tempfile
import textwrap
import threading
import time
import urllib.error
import urllib.request
from urllib.parse import urlparse
from abc import ABC, abstractmethod
from html import unescape
from html.parser import HTMLParser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from meridian_config import load_config

HOST = "127.0.0.1"
PORT = 8266
WORKSPACE_DIR = Path(__file__).resolve().parent
COMPANY_DIR = WORKSPACE_DIR / "company"
PLATFORM_DIR = COMPANY_DIR / "meridian_platform"
for _path in (WORKSPACE_DIR, COMPANY_DIR, PLATFORM_DIR):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from company import mcp_server
from loom_runtime_discovery import preferred_loom_bin, preferred_loom_root, runtime_value

SOUL_PATH = WORKSPACE_DIR / "SOUL.md"
MEMORY_PATH = WORKSPACE_DIR / "MEMORY.md"
SKILLS_DIR = WORKSPACE_DIR / "skills"
LOOM_MEMORY_PATH = "workspace/MEMORY.md"
LOOM_BIN = runtime_value('binary_path', preferred_loom_bin())
LOOM_ROOT = runtime_value('runtime_root', preferred_loom_root())
LOOM_ORG_ID = (
    os.environ.get("MERIDIAN_LOOM_ORG_ID")
    or os.environ.get("MERIDIAN_WORKSPACE_ORG_ID")
    or runtime_value('org_id', '')
    or "org_48b05c21"
)
LOOM_AGENT_ID = os.environ.get("MERIDIAN_LOOM_AGENT_ID", "atlas")
MERIDIAN_CODEX_HOME = os.environ.get(
    "MERIDIAN_CODEX_HOME",
    "/home/ubuntu/.meridian/auth/codex/login-home",
)
MERIDIAN_CODEX_BIN = os.environ.get(
    "MERIDIAN_CODEX_BIN",
    "/home/ubuntu/.npm-global/bin/codex",
)
MAX_STEPS = int(os.environ.get("MERIDIAN_GATEWAY_MAX_STEPS", "6"))
REQUEST_TIMEOUT_SECONDS = int(os.environ.get("MERIDIAN_GATEWAY_TIMEOUT_SECONDS", "90"))
HEARTBEAT_INTERVAL_SECONDS = 60
WORKSPACE_API_BASE = os.environ.get("MERIDIAN_WORKSPACE_API_BASE", "http://127.0.0.1:18901").rstrip("/")
WORKSPACE_CREDENTIALS_FILE = Path(
    os.environ.get("MERIDIAN_WORKSPACE_CREDENTIALS_FILE", "/home/ubuntu/.meridian/.workspace_credentials")
)

ANSI_RESET = "\033[0m"
ANSI_BOLD = "\033[1m"
ANSI_CYAN = "\033[36m"
ANSI_GREEN = "\033[32m"
ANSI_YELLOW = "\033[33m"
ANSI_RED = "\033[31m"

LLM_BASE_URL = ""
LLM_MODEL = ""
LLM_API_KEY = ""
TEAM_MANAGER_AGENT_ID = os.environ.get("MERIDIAN_TEAM_MANAGER_AGENT_ID", "leviathann").strip() or "leviathann"
TEAM_RESEARCH_AGENT_ID = os.environ.get("MERIDIAN_TEAM_RESEARCH_AGENT_ID", "agent_atlas").strip() or "agent_atlas"
TEAM_VERIFY_AGENT_ID = os.environ.get("MERIDIAN_TEAM_VERIFY_AGENT_ID", "agent_aegis").strip() or "agent_aegis"


def _loom_manager_defaults() -> dict[str, str]:
    provider_profile = "manager_frontier"
    model = "gpt-5.4"
    manifest_path = Path(LOOM_ROOT) / "state" / "onboard.json"
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        brain = payload.get("brain") or {}
        lane = str(brain.get("managerLane") or "frontier").strip().lower()
        configured_model = str(brain.get("managerModel") or "").strip()
        if lane != "frontier":
            provider_profile = "local_ollama"
        if configured_model:
            model = configured_model
    except Exception:
        pass
    return {"provider_profile": provider_profile, "model": model}


def _run_loom_json(command: list[str], *, timeout: int = REQUEST_TIMEOUT_SECONDS) -> dict[str, Any]:
    try:
        completed = subprocess.run(
            _loom_cli_prefix() + command,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except Exception as exc:
        return {"ok": False, "error": f"{exc.__class__.__name__}: {exc}"}
    stdout = (completed.stdout or "").strip()
    stderr = (completed.stderr or "").strip()
    payload = _extract_json(stdout) or {}
    result = {
        "ok": completed.returncode == 0,
        "returncode": completed.returncode,
        "stdout": stdout,
        "stderr": stderr,
        "payload": payload,
    }
    if completed.returncode != 0 and not stderr and not payload:
        result["error"] = stdout[:500]
    elif completed.returncode != 0:
        result["error"] = stderr[:500] or stdout[:500]
    return result


def _loom_channel_ingest(channel_id: str, peer_id: str, text: str, *, thread_id: str = "") -> dict[str, Any]:
    command = [
        LOOM_BIN,
        "channel",
        "ingest",
        "--root",
        LOOM_ROOT,
        "--channel",
        channel_id,
        "--peer",
        str(peer_id),
        "--text",
        text,
        "--format",
        "json",
    ]
    if thread_id:
        command.extend(["--thread", str(thread_id)])
    result = _run_loom_json(command)
    if not result.get("ok"):
        _log(f"loom channel ingest failed for {channel_id}:{peer_id}: {result.get('error') or result.get('stderr') or result.get('stdout')}", color=ANSI_YELLOW)
    return result


def _loom_channel_send(channel_id: str, recipient: str, text: str) -> dict[str, Any]:
    command = [
        LOOM_BIN,
        "channel",
        "send",
        "--root",
        LOOM_ROOT,
        "--channel",
        channel_id,
        "--recipient",
        str(recipient),
        "--text",
        text,
        "--format",
        "json",
    ]
    result = _run_loom_json(command)
    if not result.get("ok"):
        _log(f"loom channel send failed for {channel_id}:{recipient}: {result.get('error') or result.get('stderr') or result.get('stdout')}", color=ANSI_YELLOW)
    return result


def _loom_channel_update(delivery_id: str, status: str, *, external_ref: str = "", detail: str = "") -> dict[str, Any]:
    delivery_id = str(delivery_id or "").strip()
    status = str(status or "").strip()
    if not delivery_id or not status:
        return {}
    command = [
        LOOM_BIN,
        "channel",
        "update",
        "--root",
        LOOM_ROOT,
        "--delivery-id",
        delivery_id,
        "--status",
        status,
        "--format",
        "json",
    ]
    if external_ref:
        command.extend(["--external-ref", external_ref])
    if detail:
        command.extend(["--detail", detail])
    result = _run_loom_json(command)
    if not result.get("ok"):
        _log(
            f"loom channel update failed for {delivery_id}: {result.get('error') or result.get('stderr') or result.get('stdout')}",
            color=ANSI_YELLOW,
        )
    return result


def _loom_manager_route(*, agent_id: str = "", org_id: str = "") -> dict[str, Any]:
    command = [
        LOOM_BIN,
        "provider",
        "route",
        "--root",
        LOOM_ROOT,
        "--capability",
        "loom.llm.inference.v1",
        "--format",
        "json",
    ]
    if agent_id:
        command.extend(["--agent-id", agent_id])
    if org_id:
        command.extend(["--org-id", org_id])
    result = _run_loom_json(command)
    return result.get("payload") if result.get("ok") and isinstance(result.get("payload"), dict) else {}


def _loom_session_route(
    session_key: str,
    *,
    agent_id: str = "",
    org_id: str = "",
    ingress_request_id: str = "",
    delivery_id: str = "",
    job_id: str = "",
) -> dict[str, Any]:
    channel_id, _, peer_id = session_key.partition(":")
    binding_id = f"binding-{channel_id}" if channel_id else ""
    route = _loom_manager_route(agent_id=agent_id, org_id=org_id)
    provider_profile = str(route.get("profile") or "").strip()
    model = str(route.get("model") or "").strip()
    transport_kind = str(route.get("transport_kind") or "").strip()
    auth_mode = str(route.get("auth_mode") or "").strip()
    execution_owner = str(route.get("execution_owner") or "").strip()
    command = [
        LOOM_BIN,
        "session",
        "route",
        "--root",
        LOOM_ROOT,
        "--session-key",
        session_key,
        "--channel-id",
        channel_id,
        "--peer-id",
        peer_id,
        "--binding-id",
        binding_id,
        "--agent-id",
        agent_id,
        "--override-source",
        "default",
        "--format",
        "json",
    ]
    if provider_profile:
        command.extend(["--provider-profile", provider_profile])
    if model:
        command.extend(["--model", model])
    if transport_kind:
        command.extend(["--transport-kind", transport_kind])
    if auth_mode:
        command.extend(["--auth-mode", auth_mode])
    if execution_owner:
        command.extend(["--execution-owner", execution_owner])
    if ingress_request_id:
        command.extend(["--ingress-request-id", ingress_request_id])
    if delivery_id:
        command.extend(["--delivery-id", delivery_id])
    if job_id:
        command.extend(["--job-id", job_id])
    result = _run_loom_json(command)
    if not result.get("ok"):
        _log(
            f"loom session route failed for {session_key}: {result.get('error') or result.get('stderr') or result.get('stdout')}",
            color=ANSI_YELLOW,
        )
    return result


def _run_codex_exec(*, system_prompt: str, user_prompt: str, model: str, timeout: int = REQUEST_TIMEOUT_SECONDS) -> dict[str, Any]:
    codex_bin = str(MERIDIAN_CODEX_BIN).strip() or "codex"
    codex_home = str(MERIDIAN_CODEX_HOME).strip() or "/home/ubuntu/.meridian/auth/codex/login-home"
    prompt = textwrap.dedent(
        f"""
        System instructions:
        {system_prompt.strip()}

        User request:
        {user_prompt.strip()}

        Return only the final answer for the user.
        """
    ).strip()
    output_path = None
    env = os.environ.copy()
    env["HOME"] = codex_home
    command = [
        codex_bin,
        "exec",
        "-m",
        model,
        "--skip-git-repo-check",
        "--ephemeral",
        "--color",
        "never",
        "-C",
        "/home/ubuntu",
    ]
    try:
        with tempfile.NamedTemporaryFile(prefix="meridian-codex-", suffix=".txt", delete=False) as handle:
            output_path = handle.name
        command.extend(["-o", output_path, prompt])
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            env=env,
        )
        output_text = ""
        if output_path:
            candidate = Path(output_path)
            if candidate.exists():
                output_text = candidate.read_text(encoding="utf-8").strip()
        result = {
            "ok": completed.returncode == 0 and bool(output_text),
            "returncode": completed.returncode,
            "stdout": (completed.stdout or "").strip(),
            "stderr": (completed.stderr or "").strip(),
            "output_text": output_text,
            "model": model,
            "provider_profile": "manager_frontier",
        }
        if completed.returncode != 0 and not result["stderr"]:
            result["stderr"] = result["stdout"][-500:]
        if not output_text and completed.returncode == 0:
            result["ok"] = False
            result["stderr"] = result["stderr"] or "Codex exec returned empty output"
        return result
    except Exception as exc:
        return {
            "ok": False,
            "returncode": -1,
            "stderr": f"{exc.__class__.__name__}: {exc}",
            "stdout": "",
            "output_text": "",
            "model": model,
            "provider_profile": "manager_frontier",
        }
    finally:
        if output_path:
            try:
                Path(output_path).unlink(missing_ok=True)
            except Exception:
                pass


def _telegram_help_text() -> str:
    return (
        "Telegram team routes:\n"
        "/team <request> -> Leviathann coordinates Atlas + Aegis and replies with a managed answer.\n"
        "/atlas <topic> -> Atlas research only.\n"
        "/aegis <text> or /aegis <criteria>::<text> -> Aegis verification only.\n"
        "Plain text defaults to the managed team route."
    )


def _parse_telegram_command(text: str) -> dict[str, str]:
    stripped = text.strip()
    if not stripped:
        return {"mode": "empty", "arg": ""}
    if stripped == "/help" or stripped.startswith("/help "):
        return {"mode": "help", "arg": stripped[5:].strip()}
    for mode in ("team", "atlas", "aegis"):
        prefix = f"/{mode}"
        if stripped == prefix:
            return {"mode": mode, "arg": ""}
        if stripped.startswith(prefix + " "):
            return {"mode": mode, "arg": stripped[len(prefix) + 1 :].strip()}
    return {"mode": "team", "arg": stripped}


def _team_route_plan(text: str) -> dict[str, Any]:
    stripped = text.strip()
    if not stripped:
        return {"mode": "direct", "reason": "empty"}
    if stripped.lower() in {"hi", "hello", "hey", "yo", "ping"}:
        return {"mode": "direct", "reason": "greeting"}
    manager = _loom_manager_defaults()
    plan = _run_codex_exec(
        system_prompt=(
            "You are Leviathann, Meridian's manager and orchestrator. "
            "Classify whether the user request should go through the managed team route or be answered directly. "
            "Return strict JSON only with keys: mode, topic, depth, criteria, reason. "
            "mode must be direct or team. depth must be quick, standard, or deep. criteria must be factual, readiness, or consistency."
        ),
        user_prompt=f"User request:\n{stripped}",
        model=manager["model"],
        timeout=min(REQUEST_TIMEOUT_SECONDS, 45),
    )
    payload = _extract_json(plan.get("output_text", "")) if plan.get("ok") else None
    if not isinstance(payload, dict):
        return {
            "mode": "team" if len(stripped.split()) >= 4 else "direct",
            "topic": stripped,
            "depth": "standard",
            "criteria": "factual",
            "reason": "planner_fallback",
        }
    mode = str(payload.get("mode") or "team").strip().lower()
    if mode not in {"direct", "team"}:
        mode = "team"
    depth = str(payload.get("depth") or "standard").strip().lower()
    if depth not in {"quick", "standard", "deep"}:
        depth = "standard"
    criteria = str(payload.get("criteria") or "factual").strip().lower()
    if criteria not in {"factual", "readiness", "consistency"}:
        criteria = "factual"
    topic = str(payload.get("topic") or stripped).strip() or stripped
    return {
        "mode": mode,
        "topic": topic,
        "depth": depth,
        "criteria": criteria,
        "reason": str(payload.get("reason") or "").strip(),
    }


def _manager_synthesis(goal: str, research: dict[str, Any], verification: dict[str, Any]) -> str:
    manager = _loom_manager_defaults()
    research_text = str(research.get("research") or research.get("error") or "").strip()
    verification_text = str(verification.get("verification") or verification.get("error") or "").strip()
    result = _run_codex_exec(
        system_prompt=(
            "You are Leviathann, Meridian's manager. "
            "Given specialist outputs from Atlas and Aegis, produce the final user-facing Telegram reply. "
            "Be concise, practical, and explicit about uncertainty if verification raises concerns."
        ),
        user_prompt=(
            f"Original user request:\n{goal.strip()}\n\n"
            f"Atlas research:\n{research_text}\n\n"
            f"Aegis verification:\n{verification_text}"
        ),
        model=manager["model"],
        timeout=REQUEST_TIMEOUT_SECONDS,
    )
    if result.get("ok") and str(result.get("output_text") or "").strip():
        return str(result.get("output_text") or "").strip()
    if research_text:
        return research_text
    return verification_text or "Unable to complete the managed team response."


def _run_team_route(text: str, session_key: str, runtime: AgentRuntime) -> tuple[str, dict[str, Any]]:
    parsed = _parse_telegram_command(text)
    mode = parsed["mode"]
    arg = parsed["arg"].strip()
    if mode == "help":
        return _telegram_help_text(), {"mode": "help", "steps": []}
    if mode == "atlas":
        topic = arg or text.strip()
        result = mcp_server.do_on_demand_research_route(topic, "standard", agent_id=TEAM_RESEARCH_AGENT_ID, session_id=session_key)
        answer = str(result.get("research") or result.get("error") or "").strip() or "Atlas returned no output."
        return answer, {"mode": "atlas", "steps": [result], "job_id": str(result.get("job_id") or "").strip()}
    if mode == "aegis":
        criteria = "factual"
        qa_text = arg or text.strip()
        if "::" in qa_text:
            criteria_part, payload_part = qa_text.split("::", 1)
            if payload_part.strip():
                criteria = criteria_part.strip() or "factual"
                qa_text = payload_part.strip()
        result = mcp_server.do_qa_verify_route(qa_text, criteria, agent_id=TEAM_VERIFY_AGENT_ID, session_id=session_key)
        answer = str(result.get("verification") or result.get("error") or "").strip() or "Aegis returned no output."
        return answer, {"mode": "aegis", "steps": [result], "job_id": str(result.get("job_id") or "").strip()}

    request = arg or text.strip()
    plan = _team_route_plan(request)
    if plan.get("mode") == "direct":
        return runtime.run_goal(request), {"mode": "direct", "steps": [], "plan": plan}

    research = mcp_server.do_on_demand_research_route(
        str(plan.get("topic") or request),
        str(plan.get("depth") or "standard"),
        agent_id=TEAM_RESEARCH_AGENT_ID,
        session_id=session_key,
    )
    if research.get("error"):
        answer = str(research.get("error") or "Managed research failed").strip()
        return answer, {"mode": "team", "steps": [research], "plan": plan, "job_id": str(research.get("job_id") or "").strip()}

    verification = mcp_server.do_qa_verify_route(
        str(research.get("research") or ""),
        str(plan.get("criteria") or "factual"),
        agent_id=TEAM_VERIFY_AGENT_ID,
        session_id=session_key,
    )
    answer = _manager_synthesis(request, research, verification)
    final_job_id = str(verification.get("job_id") or research.get("job_id") or "").strip()
    return answer, {
        "mode": "team",
        "plan": plan,
        "steps": [research, verification],
        "job_id": final_job_id,
    }


class _TitleParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._in_title = False
        self._parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() == "title":
            self._in_title = True

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "title":
            self._in_title = False

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self._parts.append(data)

    @property
    def title(self) -> str:
        return " ".join(" ".join(self._parts).split()).strip()


class SkillRegistry:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.items: list[dict[str, str]] = []

    def load(self) -> list[dict[str, str]]:
        items: list[dict[str, str]] = []
        if not self.root.exists():
            self.items = []
            return self.items
        for path in sorted(self.root.rglob("*")):
            if not path.is_file():
                continue
            rel = str(path.relative_to(self.root))
            if path.suffix.lower() == ".json":
                try:
                    payload = json.loads(path.read_text(encoding="utf-8"))
                except Exception:
                    continue
                if not isinstance(payload, dict):
                    continue
                items.append(
                    {
                        "name": str(payload.get("name") or path.stem).strip(),
                        "description": str(payload.get("description") or "").strip(),
                        "capability": str(payload.get("capability") or "").strip(),
                        "source": rel,
                    }
                )
            elif path.suffix.lower() == ".md":
                first_line = ""
                for line in path.read_text(encoding="utf-8").splitlines():
                    stripped = line.strip().lstrip("#").strip()
                    if stripped:
                        first_line = stripped
                        break
                items.append(
                    {
                        "name": path.stem,
                        "description": first_line,
                        "capability": "",
                        "source": rel,
                    }
                )
        self.items = items
        return self.items

    def prompt_block(self) -> str:
        if not self.items:
            return "- none"
        lines = []
        for item in self.items[:24]:
            detail = item["description"] or item["source"]
            if item["capability"]:
                lines.append(f"- {item['name']}: {detail} (capability: {item['capability']})")
            else:
                lines.append(f"- {item['name']}: {detail}")
        return "\n".join(lines)


def _log(message: str, *, color: str = ANSI_CYAN) -> None:
    print(f"{ANSI_BOLD}{color}{message}{ANSI_RESET}", flush=True)


def _strip_ansi(text: str) -> str:
    return re.sub(r"\x1B\[[0-?]*[ -/]*[@-~]", "", text)


def _extract_json(text: str) -> dict[str, Any] | None:
    raw = text.strip()
    if not raw:
        return None
    candidates = [raw]
    fence_match = re.search(r"```(?:json)?\s*(\{.*\})\s*```", raw, flags=re.DOTALL)
    if fence_match:
        candidates.append(fence_match.group(1))
    first = raw.find("{")
    last = raw.rfind("}")
    if first != -1 and last != -1 and last > first:
        candidates.append(raw[first : last + 1])
    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    return None


def _extract_title(html: str) -> str:
    parser = _TitleParser()
    parser.feed(html)
    return parser.title


def _strip_html(html: str) -> str:
    body = re.sub(r"<script\b[^>]*>.*?</script>", " ", html, flags=re.IGNORECASE | re.DOTALL)
    body = re.sub(r"<style\b[^>]*>.*?</style>", " ", body, flags=re.IGNORECASE | re.DOTALL)
    body = re.sub(r"<[^>]+>", " ", body)
    body = unescape(body)
    return " ".join(body.split()).strip()


def _excerpt(text: str, limit: int = 320) -> str:
    value = " ".join(text.split()).strip()
    if len(value) <= limit:
        return value
    return value[: limit - 3].rstrip() + "..."


def _mirror_fetch(url: str) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "MeridianGateway/1.0",
            "Accept": "text/html,application/xhtml+xml",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            html = response.read().decode("utf-8", "replace")
            return {
                "ok": True,
                "final_url": response.geturl(),
                "http_status": getattr(response, "status", None),
                "title": _extract_title(html),
                "excerpt": _excerpt(_strip_html(html)),
                "note": "Local urllib fallback after truthful Loom browser observation.",
            }
    except urllib.error.HTTPError as exc:
        return {"ok": False, "error": f"HTTPError {exc.code}: {exc.reason}", "final_url": url}
    except urllib.error.URLError as exc:
        return {"ok": False, "error": f"URLError: {exc.reason}", "final_url": url}
    except Exception as exc:
        return {"ok": False, "error": f"{exc.__class__.__name__}: {exc}", "final_url": url}


def _loom_cli_prefix() -> list[str]:
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        return ["sudo", "-u", "ubuntu", "-H"]
    return []


def _load_runtime_config_or_exit() -> dict[str, Any]:
    global LLM_BASE_URL, LLM_MODEL, LLM_API_KEY
    try:
        config = load_config(required=True)
    except FileNotFoundError:
        print("Configuration missing. Run python3 meridian_setup.py first.", file=sys.stderr)
        raise SystemExit(1)
    except Exception as exc:
        print(f"Failed to load meridian_config.json: {exc}", file=sys.stderr)
        raise SystemExit(1)

    config["telegram_bot_token"] = (
        os.environ.get("MERIDIAN_TELEGRAM_BOT_TOKEN")
        or os.environ.get("TELEGRAM_BOT_TOKEN")
        or str(config.get("telegram_bot_token") or "")
    ).strip()
    config["allowed_origin"] = (
        os.environ.get("MERIDIAN_ALLOWED_ORIGIN")
        or str(config.get("allowed_origin") or "")
    ).strip()

    LLM_BASE_URL = str(config.get("llm_base_url") or "").strip()
    LLM_MODEL = str(config.get("llm_model") or "").strip()
    LLM_API_KEY = str(config.get("llm_api_key") or "").strip()
    return config


def _load_workspace_basic_credentials() -> tuple[str, str]:
    user = ""
    password = ""
    try:
        for raw_line in WORKSPACE_CREDENTIALS_FILE.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or ":" not in line:
                continue
            key, value = line.split(":", 1)
            key = key.strip()
            value = value.strip()
            if key == "user":
                user = value
            elif key == "pass":
                password = value
    except Exception:
        return "", ""
    return user, password


def _workspace_api_get_json(path: str) -> dict[str, Any]:
    normalized_path = path if path.startswith("/") else f"/{path}"
    url = f"{WORKSPACE_API_BASE}{normalized_path}"
    headers = {
        "Accept": "application/json",
        "User-Agent": "MeridianGateway/1.0",
    }
    user, password = _load_workspace_basic_credentials()
    if user and password:
        token = base64.b64encode(f"{user}:{password}".encode("utf-8")).decode("ascii")
        headers["Authorization"] = f"Basic {token}"
    request = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            payload = json.loads(response.read().decode("utf-8", "replace"))
            return {
                "ok": True,
                "status_code": getattr(response, "status", 200),
                "payload": payload if isinstance(payload, dict) else {"status": "success", "output": payload},
            }
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")
        payload = _extract_json(detail)
        return {
            "ok": False,
            "status_code": exc.code,
            "payload": payload if isinstance(payload, dict) else {"status": "error", "output": detail or exc.reason},
        }
    except urllib.error.URLError as exc:
        return {
            "ok": False,
            "status_code": 502,
            "payload": {"status": "error", "output": f"workspace_unreachable: {exc.reason}"},
        }
    except Exception as exc:
        return {
            "ok": False,
            "status_code": 502,
            "payload": {"status": "error", "output": f"{exc.__class__.__name__}: {exc}"},
        }


def _workspace_api_post_json(path: str, payload: dict[str, Any]) -> dict[str, Any]:
    normalized_path = path if path.startswith("/") else f"/{path}"
    url = f"{WORKSPACE_API_BASE}{normalized_path}"
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "User-Agent": "MeridianGateway/1.0",
    }
    user, password = _load_workspace_basic_credentials()
    if user and password:
        token = base64.b64encode(f"{user}:{password}".encode("utf-8")).decode("ascii")
        headers["Authorization"] = f"Basic {token}"
    body = json.dumps(payload or {}).encode("utf-8")
    request = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            raw = response.read().decode("utf-8", "replace")
            parsed = _extract_json(raw)
            return {
                "ok": True,
                "status_code": getattr(response, "status", 200),
                "payload": parsed if isinstance(parsed, dict) else {"status": "success", "output": raw},
            }
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")
        payload = _extract_json(detail)
        return {
            "ok": False,
            "status_code": exc.code,
            "payload": payload if isinstance(payload, dict) else {"status": "error", "output": detail or exc.reason},
        }
    except urllib.error.URLError as exc:
        return {
            "ok": False,
            "status_code": 502,
            "payload": {"status": "error", "output": f"workspace_unreachable: {exc.reason}"},
        }
    except Exception as exc:
        return {
            "ok": False,
            "status_code": 502,
            "payload": {"status": "error", "output": f"{exc.__class__.__name__}: {exc}"},
        }


class AgentRuntime:
    def __init__(self, skills: SkillRegistry) -> None:
        self.skills = skills
        self.lock = threading.Lock()

    def _load_soul(self) -> str:
        return SOUL_PATH.read_text(encoding="utf-8").strip()

    def _load_memory(self) -> str:
        return MEMORY_PATH.read_text(encoding="utf-8").strip()

    def _system_prompt(self, soul: str, memory: str) -> str:
        return textwrap.dedent(
            f"""
            {soul}

            Current Markdown memory:
            {memory}

            You are Meridian Gateway AgentRuntime.
            Return strictly valid JSON with:
            - thought: string
            - tool_call: null or {{ capability: string, payload: object }}
            - final_answer: optional string when done

            Available capabilities:
            - loom.browser.navigate.v1 -> fetches a URL. payload {{"url": "https://example.com"}}
            - loom.fs.write.v1 -> writes content into the bounded Loom workspace. payload {{"path": "workspace/file.txt", "content": "text"}}
            - loom.system.info.v1 -> returns a bounded system snapshot. payload {{}}
            - loom.memory.core.v1 -> safely updates MEMORY.md via Loom. payload {{"markdown": "# MEMORY.md ..."}}

            Loaded skills:
            {self.skills.prompt_block()}

            Rules:
            - Use one tool call at a time.
            - Be truthful about failures.
            - If no proactive action is needed during heartbeat, return final_answer exactly SLEEP.
            - For direct user requests, complete the task if possible and keep final answers concise.
            """
        ).strip()

    def _chat(self, messages: list[dict[str, str]]) -> str:
        system_prompt = ""
        user_parts: list[str] = []
        for message in messages:
            role = str(message.get("role") or "user").strip().lower()
            content = str(message.get("content") or "")
            if role == "system" and not system_prompt:
                system_prompt = content
            elif content.strip():
                if role == "user":
                    user_parts.append(content)
                else:
                    user_parts.append(f"[{role}] {content}")
        defaults = _loom_manager_defaults()
        user_prompt = "\n\n".join(part for part in user_parts if part.strip())
        if defaults["provider_profile"] == "manager_frontier":
            codex_result = _run_codex_exec(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                model=defaults["model"],
            )
            if not codex_result.get("ok"):
                raise RuntimeError(codex_result.get("stderr") or codex_result.get("stdout") or "Codex exec failed")
            output = str(codex_result.get("output_text") or "").strip()
            if not output:
                raise RuntimeError("Codex exec returned empty output")
            return output
        observation = self._run_loom(
            "loom.llm.inference.v1",
            {
                "provider_profile": defaults["provider_profile"],
                "model": defaults["model"],
                "system_prompt": system_prompt,
                "user_prompt": user_prompt,
                "max_tokens": 700,
            },
        )
        if not observation.get("ok"):
            raise RuntimeError(observation.get("error") or observation.get("stderr") or "Loom llm inference failed")
        llm_response = observation.get("llm_response") or {}
        output = str(llm_response.get("output_text") or "").strip()
        if not output:
            raise RuntimeError("Loom llm inference returned empty output_text")
        return output

    def _valid_step(self, payload: dict[str, Any] | None) -> bool:
        if not isinstance(payload, dict):
            return False
        if not isinstance(payload.get("thought"), str):
            return False
        tool_call = payload.get("tool_call")
        final_answer = str(payload.get("final_answer") or "").strip()
        if tool_call is None and not final_answer:
            return False
        if tool_call is not None and not isinstance(tool_call, dict):
            return False
        return True

    def _llm_step(self, goal: str, history: list[dict[str, Any]], *, heartbeat: bool) -> dict[str, Any]:
        soul = self._load_soul()
        memory = self._load_memory()
        system_prompt = self._system_prompt(soul, memory)
        instruction = (
            "Decide the next single action. Return strict JSON only. Include either a tool_call or a final_answer."
        )
        if heartbeat:
            instruction = (
                "Silent heartbeat review: decide whether proactive action is needed right now. "
                "If no action is needed, return final_answer exactly SLEEP and tool_call null. "
                "If action is needed, choose the smallest safe next step. Return strict JSON only."
            )
        base_messages = [
            {"role": "system", "content": system_prompt},
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "goal": goal,
                        "history": history,
                        "instruction": instruction,
                    },
                    indent=2,
                    ensure_ascii=False,
                ),
            },
        ]
        raw = self._chat(base_messages)
        parsed = _extract_json(raw)
        if self._valid_step(parsed):
            return parsed
        last = raw
        for _attempt in range(2):
            repair_messages = [
                {"role": "system", "content": system_prompt},
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "goal": goal,
                            "history": history,
                            "bad_response": last,
                            "instruction": "Repair bad_response into strict JSON with keys thought, tool_call, final_answer. Return JSON only.",
                        },
                        indent=2,
                        ensure_ascii=False,
                    ),
                },
            ]
            last = self._chat(repair_messages)
            parsed = _extract_json(last)
            if self._valid_step(parsed):
                return parsed
        raise ValueError(f"LLM did not return valid JSON after repair attempts: {last}")

    def _normalize_tool_call(self, tool_call: Any) -> tuple[str, dict[str, Any]]:
        if not isinstance(tool_call, dict):
            raise ValueError(f"tool_call must be an object, got: {tool_call!r}")
        capability = str(tool_call.get("capability") or "").strip()
        if not capability:
            raise ValueError("tool_call missing capability")
        payload = tool_call.get("payload")
        if not isinstance(payload, dict):
            payload = {}
        if capability == "loom.browser.navigate.v1":
            payload = {"url": str(payload.get("url") or payload.get("href") or payload.get("target_url") or "").strip()}
        elif capability == "loom.fs.write.v1":
            payload = {
                "path": str(payload.get("path") or payload.get("file_path") or "workspace/output.txt").strip(),
                "content": str(payload.get("content") or payload.get("text") or payload.get("body") or ""),
            }
        elif capability == "loom.memory.core.v1":
            payload = {"markdown": str(payload.get("markdown") or payload.get("content") or payload.get("text") or "").strip()}
        elif capability == "loom.system.info.v1":
            payload = {}
        return capability, payload

    def _load_json_file(self, path: str) -> dict[str, Any]:
        candidate = str(path or "").strip()
        if not candidate or not os.path.exists(candidate):
            return {}
        with open(candidate, encoding="utf-8") as handle:
            return json.load(handle)

    def _run_loom(self, capability: str, payload: dict[str, Any]) -> dict[str, Any]:
        command = _loom_cli_prefix() + [
            LOOM_BIN,
            "action",
            "execute",
            "--root",
            LOOM_ROOT,
            "--org-id",
            LOOM_ORG_ID,
            "--agent-id",
            LOOM_AGENT_ID,
            "--capability",
            capability,
            "--payload-json",
            json.dumps(payload, ensure_ascii=False),
            "--format",
            "json",
        ]
        try:
            completed = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=REQUEST_TIMEOUT_SECONDS,
                check=False,
            )
        except Exception as exc:
            return {"ok": False, "capability": capability, "payload": payload, "error": f"{exc.__class__.__name__}: {exc}"}

        stdout = (completed.stdout or "").strip()
        stderr = (completed.stderr or "").strip()
        parsed_stdout = _extract_json(stdout) or {}
        observation: dict[str, Any] = {
            "ok": completed.returncode == 0,
            "capability": capability,
            "payload": payload,
            "returncode": completed.returncode,
            "stderr": stderr,
        }
        if parsed_stdout:
            observation["runtime"] = {
                "status": parsed_stdout.get("status"),
                "runtime_outcome": parsed_stdout.get("runtime_outcome"),
                "worker_status": parsed_stdout.get("worker_status"),
            }
        worker_result = self._load_json_file(parsed_stdout.get("worker_result_path", ""))
        if worker_result:
            observation["worker_status"] = worker_result.get("status")
            host_response = worker_result.get("host_response_json", {})
            if isinstance(host_response, str):
                try:
                    host_response = json.loads(host_response)
                except json.JSONDecodeError:
                    host_response = {}
            if capability == "loom.browser.navigate.v1" and isinstance(host_response, dict):
                observation["browser_view"] = {
                    "final_url": str(host_response.get("final_url") or payload.get("url") or "").strip(),
                    "title": str(host_response.get("title") or "").strip(),
                    "note": str(host_response.get("note") or "").strip(),
                }
                excerpt = _excerpt(str(host_response.get("body_excerpt_utf8") or "").strip())
                if excerpt:
                    observation["browser_view"]["excerpt"] = excerpt
                if not observation["browser_view"].get("title") and not observation["browser_view"].get("excerpt"):
                    observation["mirror_fetch"] = _mirror_fetch(payload.get("url", ""))
            elif capability == "loom.fs.write.v1" and isinstance(host_response, dict):
                observation["fs_write"] = {
                    "path": str(host_response.get("path") or payload.get("path") or "").strip(),
                    "bytes_written": host_response.get("bytes_written"),
                    "note": str(host_response.get("note") or "").strip(),
                }
            elif capability == "loom.system.info.v1" and isinstance(host_response, dict):
                observation["system_info"] = {
                    "hostname": str(host_response.get("hostname_utf8") or "").strip(),
                    "uname": str(host_response.get("uname_utf8") or "").strip(),
                    "note": str(host_response.get("note") or "").strip(),
                }
            elif capability == "loom.llm.inference.v1" and isinstance(host_response, dict):
                observation["llm_response"] = {
                    "model": str(host_response.get("model") or payload.get("model") or "").strip(),
                    "output_text": str(host_response.get("output_text") or "").strip(),
                    "finish_reason": str(host_response.get("finish_reason") or "").strip(),
                    "prompt_tokens": host_response.get("prompt_tokens"),
                    "completion_tokens": host_response.get("completion_tokens"),
                    "note": str(host_response.get("note") or "").strip(),
                }
        else:
            observation["stdout_excerpt"] = stdout[:500]
        return observation

    def _run_tool(self, capability: str, payload: dict[str, Any]) -> dict[str, Any]:
        if capability == "loom.memory.core.v1":
            markdown = str(payload.get("markdown") or "").strip()
            executed = self._run_loom("loom.fs.write.v1", {"path": LOOM_MEMORY_PATH, "content": markdown})
            executed["capability"] = capability
            if executed.get("ok") and markdown:
                MEMORY_PATH.write_text(markdown + ("\n" if not markdown.endswith("\n") else ""), encoding="utf-8")
                executed["memory_path"] = str(MEMORY_PATH)
                executed["loom_memory_path"] = LOOM_MEMORY_PATH
            return executed
        return self._run_loom(capability, payload)

    def _history_view(self, observation: dict[str, Any]) -> dict[str, Any]:
        history = {
            "ok": bool(observation.get("ok")),
            "capability": observation.get("capability"),
            "worker_status": observation.get("worker_status"),
            "stderr": observation.get("stderr") or "",
        }
        for key in ("browser_view", "mirror_fetch", "fs_write", "system_info", "memory_path", "loom_memory_path"):
            if key in observation:
                history[key] = observation[key]
        return history

    def run_goal(self, goal: str, *, heartbeat: bool = False) -> str:
        with self.lock:
            history: list[dict[str, Any]] = []
            for _step in range(1, MAX_STEPS + 1):
                step = self._llm_step(goal, history, heartbeat=heartbeat)
                thought = str(step.get("thought") or "").strip()
                tool_call = step.get("tool_call")
                final_answer = str(step.get("final_answer") or "").strip()
                if tool_call is not None:
                    capability, payload = self._normalize_tool_call(tool_call)
                    observation = self._run_tool(capability, payload)
                    history.append({"role": "thought", "value": thought})
                    history.append({"role": "tool_call", "value": {"capability": capability, "payload": payload}})
                    history.append({"role": "observation", "value": self._history_view(observation)})
                    continue
                if final_answer:
                    if heartbeat and final_answer.strip().upper() == "SLEEP":
                        return ""
                    return final_answer
                history.append({"role": "thought", "value": thought})
                history.append({"role": "observation", "value": {"ok": False, "error": "missing tool_call and final_answer"}})
            return "Unable to complete the request within the configured step limit."


class ChannelAdapter(ABC):
    def __init__(self, runtime: AgentRuntime, name: str) -> None:
        self.runtime = runtime
        self.name = name
        self._active = False

    @abstractmethod
    def start(self) -> None:
        raise NotImplementedError

    @abstractmethod
    def stop(self) -> None:
        raise NotImplementedError

    @abstractmethod
    def send_message(self, text: str, *, source: str = "runtime") -> None:
        raise NotImplementedError

    def is_active(self) -> bool:
        return self._active


class TelegramAdapter(ChannelAdapter):
    def __init__(self, runtime: AgentRuntime, bot_token: str) -> None:
        super().__init__(runtime, "telegram")
        self.bot_token = bot_token.strip()
        self.thread: threading.Thread | None = None
        self.stop_event = threading.Event()
        self.next_offset: int | None = None
        self.active_chats: set[int | str] = set()
        self.active_lock = threading.Lock()

    def start(self) -> None:
        if not self.bot_token:
            _log("telegram adapter disabled: no bot token configured", color=ANSI_YELLOW)
            return
        self._active = True
        self.thread = threading.Thread(target=self._poll_loop, name="telegram-adapter", daemon=True)
        self.thread.start()
        _log("telegram adapter started: polling active", color=ANSI_GREEN)

    def stop(self) -> None:
        self.stop_event.set()

    def _telegram_request(self, method: str, payload: dict[str, Any]) -> dict[str, Any]:
        request = urllib.request.Request(
            f"https://api.telegram.org/bot{self.bot_token}/{method}",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=40) as response:
            body = json.loads(response.read().decode("utf-8", "replace"))
        if not body.get("ok"):
            raise RuntimeError(body.get("description") or f"Telegram API call failed: {method}")
        return body

    def _send_direct(self, chat_id: int | str, text: str, *, reply_to_message_id: int | None = None) -> dict[str, Any]:
        payload: dict[str, Any] = {"chat_id": chat_id, "text": text}
        if reply_to_message_id is not None:
            payload["reply_to_message_id"] = reply_to_message_id
        body = self._telegram_request("sendMessage", payload)
        result = body.get("result") if isinstance(body, dict) else {}
        return result if isinstance(result, dict) else {}

    def send_message(self, text: str, *, source: str = "runtime") -> None:
        with self.active_lock:
            targets = list(self.active_chats)
        for chat_id in targets:
            session_key = f"telegram:{chat_id}"
            delivery = _loom_channel_send("telegram", str(chat_id), text)
            delivery_payload = delivery.get("payload") if isinstance(delivery, dict) else {}
            delivery_id = str((delivery_payload or {}).get("delivery_id") or "").strip()
            try:
                result = self._send_direct(chat_id, text)
                _loom_channel_update(
                    delivery_id,
                    "delivered",
                    external_ref=str((result or {}).get("message_id") or str(chat_id)).strip(),
                )
                _loom_session_route(
                    session_key,
                    agent_id=TEAM_MANAGER_AGENT_ID,
                    org_id=LOOM_ORG_ID,
                    delivery_id=delivery_id,
                )
            except Exception as exc:
                _loom_channel_update(delivery_id, "failed", detail=f"{exc.__class__.__name__}: {exc}")
                _log(f"telegram proactive delivery failed for {chat_id}: {exc}", color=ANSI_YELLOW)

    def _poll_loop(self) -> None:
        while not self.stop_event.is_set():
            try:
                payload: dict[str, Any] = {"timeout": 30, "allowed_updates": ["message"]}
                if self.next_offset is not None:
                    payload["offset"] = self.next_offset
                body = self._telegram_request("getUpdates", payload)
                for update in body.get("result") or []:
                    if not isinstance(update, dict):
                        continue
                    update_id = update.get("update_id")
                    if isinstance(update_id, int):
                        self.next_offset = update_id + 1
                    message = update.get("message")
                    if isinstance(message, dict):
                        self._handle_message(message)
            except Exception as exc:
                _log(f"telegram adapter warning: {exc}", color=ANSI_YELLOW)
                self.stop_event.wait(2)

    def _handle_message(self, message: dict[str, Any]) -> None:
        text = message.get("text")
        chat = message.get("chat") or {}
        chat_id = chat.get("id")
        message_id = message.get("message_id")
        if not isinstance(text, str) or not text.strip() or chat_id is None:
            return
        with self.active_lock:
            self.active_chats.add(chat_id)
        ingress = _loom_channel_ingest("telegram", str(chat_id), text.strip(), thread_id=str(message_id or ""))
        ingress_payload = ingress.get("payload") if isinstance(ingress, dict) else {}
        session_key = str((ingress_payload or {}).get("session_key") or f"telegram:{chat_id}").strip()
        ingress_request_id = str((ingress_payload or {}).get("ingress_id") or "").strip()
        answer, team_meta = _run_team_route(text.strip(), session_key, self.runtime)
        delivery_id = ""
        if answer:
            delivery = _loom_channel_send("telegram", str(chat_id), answer)
            delivery_payload = delivery.get("payload") if isinstance(delivery, dict) else {}
            delivery_id = str((delivery_payload or {}).get("delivery_id") or "").strip()
            try:
                result = self._send_direct(chat_id, answer, reply_to_message_id=message_id if isinstance(message_id, int) else None)
                _loom_channel_update(
                    delivery_id,
                    "delivered",
                    external_ref=str((result or {}).get("message_id") or str(chat_id)).strip(),
                )
            except Exception as exc:
                _loom_channel_update(delivery_id, "failed", detail=f"{exc.__class__.__name__}: {exc}")
                raise
        _loom_session_route(
            session_key,
            agent_id=TEAM_MANAGER_AGENT_ID,
            org_id=LOOM_ORG_ID,
            ingress_request_id=ingress_request_id,
            delivery_id=delivery_id,
            job_id=str((team_meta or {}).get("job_id") or "").strip(),
        )


class WebAPIAdapter(ChannelAdapter):
    def __init__(self, runtime: AgentRuntime, allowed_origin: str) -> None:
        super().__init__(runtime, "web")
        self.allowed_origin = allowed_origin.strip()
        self.notifications: queue.Queue[dict[str, str]] = queue.Queue()
        self.server: ThreadingHTTPServer | None = None
        self.thread: threading.Thread | None = None

    def _make_handler(self):
        adapter = self

        class Handler(BaseHTTPRequestHandler):
            server_version = "MeridianGatewayWeb/0.1"

            def _origin_allowed(self) -> bool:
                return self.headers.get("Origin") == adapter.allowed_origin

            def _send_cors_headers(self) -> None:
                self.send_header("Access-Control-Allow-Origin", adapter.allowed_origin)
                self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
                self.send_header("Access-Control-Allow-Headers", "Content-Type")
                self.send_header("Vary", "Origin")

            def _send_json(self, status_code: int, payload: dict[str, Any]) -> None:
                body = json.dumps(payload).encode("utf-8")
                self.send_response(status_code)
                self._send_cors_headers()
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_OPTIONS(self) -> None:  # noqa: N802
                if not self._origin_allowed():
                    self._send_json(403, {"status": "error", "output": "origin_not_allowed"})
                    return
                self.send_response(204)
                self._send_cors_headers()
                self.send_header("Content-Length", "0")
                self.end_headers()

            def do_GET(self) -> None:  # noqa: N802
                if not self._origin_allowed():
                    self._send_json(403, {"status": "error", "output": "origin_not_allowed"})
                    return
                parsed = urlparse(self.path)
                request_path = parsed.path
                proxied_path = request_path + (f"?{parsed.query}" if parsed.query else "")
                if request_path == "/api/events":
                    events = []
                    while True:
                        try:
                            events.append(adapter.notifications.get_nowait())
                        except queue.Empty:
                            break
                    self._send_json(200, {"status": "success", "events": events})
                    return
                if request_path in {
                    "/api/context",
                    "/api/status",
                    "/api/institution",
                    "/api/agents",
                    "/api/authority",
                    "/api/runtime-proof",
                    "/api/warrants",
                    "/api/commitments",
                    "/api/cases",
                    "/api/court",
                    "/api/admission",
                    "/api/federation",
                    "/api/federation/peers",
                    "/api/federation/inbox",
                    "/api/federation/execution-jobs",
                    "/api/federation/manifest",
                    "/api/federation/witness/archive",
                    "/api/alerts",
                    "/api/session/validate",
                    "/api/subscriptions",
                    "/api/subscriptions/delivery-targets",
                    "/api/subscriptions/loom-delivery-jobs",
                    "/api/subscriptions/loom-delivery-runs",
                    "/api/subscriptions/preview-queue",
                    "/api/pilot/intake",
                    "/api/pilot/intake/operator",
                    "/api/payouts",
                    "/api/accounting",
                    "/api/treasury",
                    "/api/treasury/accounts",
                    "/api/treasury/funding-sources",
                    "/api/treasury/settlement-adapters",
                }:
                    proxied = _workspace_api_get_json(proxied_path)
                    self._send_json(int(proxied.get("status_code") or 200), dict(proxied.get("payload") or {}))
                    return
                self._send_json(404, {"status": "error", "output": "not_found"})

            def do_POST(self) -> None:  # noqa: N802
                if not self._origin_allowed():
                    self._send_json(403, {"status": "error", "output": "origin_not_allowed"})
                    return
                parsed = urlparse(self.path)
                request_path = parsed.path
                try:
                    content_length = int(self.headers.get("Content-Length", "0"))
                except ValueError:
                    self._send_json(400, {"status": "error", "output": "invalid_content_length"})
                    return
                raw_body = self.rfile.read(content_length)
                try:
                    payload = json.loads(raw_body.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                    self._send_json(400, {"status": "error", "output": f"invalid_json: {exc}"})
                    return
                if request_path in {
                    "/api/authority/kill-switch",
                    "/api/authority/approve",
                    "/api/authority/request",
                    "/api/authority/delegate",
                    "/api/authority/revoke",
                    "/api/court/file",
                    "/api/court/resolve",
                    "/api/court/appeal",
                    "/api/court/decide-appeal",
                    "/api/court/auto-review",
                    "/api/court/remediate",
                    "/api/warrants/issue",
                    "/api/warrants/approve",
                    "/api/warrants/stay",
                    "/api/warrants/revoke",
                    "/api/commitments/propose",
                    "/api/commitments/accept",
                    "/api/commitments/reject",
                    "/api/commitments/breach",
                    "/api/commitments/settle",
                    "/api/cases/open",
                    "/api/cases/stay",
                    "/api/cases/resolve",
                    "/api/federation/execution-jobs/execute",
                    "/api/federation/peers/upsert",
                    "/api/federation/peers/refresh",
                    "/api/federation/peers/suspend",
                    "/api/federation/peers/revoke",
                    "/api/federation/witness/archive",
                    "/api/federation/send",
                    "/api/institution/charter",
                    "/api/institution/lifecycle",
                    "/api/session/issue",
                    "/api/session/revoke",
                    "/api/pilot/intake",
                    "/api/pilot/intake/operator/review",
                    "/api/payouts/propose",
                    "/api/payouts/submit",
                    "/api/payouts/review",
                    "/api/payouts/approve",
                    "/api/payouts/open-dispute-window",
                    "/api/payouts/reject",
                    "/api/payouts/cancel",
                    "/api/payouts/execute",
                    "/api/admission/admit",
                    "/api/admission/suspend",
                    "/api/admission/revoke",
                    "/api/alerts/dispatch",
                    "/api/subscriptions/add",
                    "/api/subscriptions/draft-from-preview",
                    "/api/subscriptions/checkout-capture",
                    "/api/subscriptions/activate-from-preview",
                    "/api/subscriptions/loom-delivery-jobs/run",
                    "/api/subscriptions/convert",
                    "/api/subscriptions/verify-payment",
                    "/api/subscriptions/remove",
                    "/api/subscriptions/set-email",
                    "/api/subscriptions/record-delivery",
                    "/api/accounting/expense",
                    "/api/accounting/reimburse",
                    "/api/accounting/draw",
                    "/api/treasury/contribute",
                    "/api/treasury/reserve-floor",
                    "/api/treasury/settlement-adapters/preflight",
                }:
                    proxied = _workspace_api_post_json(request_path, payload if isinstance(payload, dict) else {})
                    self._send_json(int(proxied.get("status_code") or 200), dict(proxied.get("payload") or {}))
                    return
                if request_path != "/api/run":
                    self._send_json(404, {"status": "error", "output": "not_found"})
                    return
                goal = payload.get("goal")
                if not isinstance(goal, str) or not goal.strip():
                    self._send_json(400, {"status": "error", "output": "goal_required"})
                    return
                ingress = _loom_channel_ingest("web_api", LOOM_ORG_ID, goal.strip())
                ingress_payload = ingress.get("payload") if isinstance(ingress, dict) else {}
                session_key = str((ingress_payload or {}).get("session_key") or f"web_api:{LOOM_ORG_ID}").strip()
                ingress_request_id = str((ingress_payload or {}).get("ingress_id") or "").strip()
                answer, team_meta = _run_team_route(goal.strip(), session_key, adapter.runtime)
                delivery_id = ""
                if answer:
                    delivery = _loom_channel_send("web_api", LOOM_ORG_ID, answer)
                    delivery_payload = delivery.get("payload") if isinstance(delivery, dict) else {}
                    delivery_id = str((delivery_payload or {}).get("delivery_id") or "").strip()
                _loom_session_route(
                    session_key,
                    agent_id=TEAM_MANAGER_AGENT_ID,
                    org_id=LOOM_ORG_ID,
                    ingress_request_id=ingress_request_id,
                    delivery_id=delivery_id,
                    job_id=str((team_meta or {}).get("job_id") or "").strip(),
                )
                self._send_json(200, {"status": "success", "output": answer})
                if delivery_id:
                    _loom_channel_update(delivery_id, "delivered", external_ref="http_response")

            def log_message(self, format: str, *args: object) -> None:  # noqa: A003
                return

        return Handler

    def start(self) -> None:
        if not self.allowed_origin:
            _log("web adapter disabled: allowed_origin missing", color=ANSI_YELLOW)
            return
        handler = self._make_handler()
        self.server = ThreadingHTTPServer((HOST, PORT), handler)
        self.thread = threading.Thread(target=self.server.serve_forever, name="web-api-adapter", daemon=True)
        self.thread.start()
        self._active = True
        _log(f"web adapter listening on http://{HOST}:{PORT} origin={self.allowed_origin}", color=ANSI_GREEN)

    def stop(self) -> None:
        if self.server is not None:
            self.server.shutdown()
            self.server.server_close()

    def send_message(self, text: str, *, source: str = "runtime") -> None:
        session_key = f"web_api:{LOOM_ORG_ID}"
        delivery = _loom_channel_send("web_api", LOOM_ORG_ID, text)
        delivery_payload = delivery.get("payload") if isinstance(delivery, dict) else {}
        delivery_id = str((delivery_payload or {}).get("delivery_id") or "").strip()
        self.notifications.put({"source": source, "text": text, "ts": str(int(time.time()))})
        if delivery_id:
            _loom_channel_update(delivery_id, "delivered", external_ref="notification_queue")
            _loom_session_route(
                session_key,
                agent_id=TEAM_MANAGER_AGENT_ID,
                org_id=LOOM_ORG_ID,
                delivery_id=delivery_id,
            )


class HeartbeatEngine(threading.Thread):
    def __init__(self, runtime: AgentRuntime, adapters: list[ChannelAdapter], interval_seconds: int = HEARTBEAT_INTERVAL_SECONDS) -> None:
        super().__init__(name="heartbeat-engine", daemon=True)
        self.runtime = runtime
        self.adapters = adapters
        self.interval_seconds = interval_seconds
        self.stop_event = threading.Event()

    def run(self) -> None:
        while not self.stop_event.wait(self.interval_seconds):
            try:
                answer = self.runtime.run_goal(
                    "Silent heartbeat check. Decide whether proactive action is needed right now.",
                    heartbeat=True,
                )
                if answer:
                    for adapter in self.adapters:
                        if adapter.is_active():
                            adapter.send_message(answer, source="heartbeat")
            except Exception as exc:
                _log(f"heartbeat warning: {exc}", color=ANSI_YELLOW)

    def stop(self) -> None:
        self.stop_event.set()


def main() -> int:
    config = _load_runtime_config_or_exit()
    skills = SkillRegistry(SKILLS_DIR)
    loaded_skills = skills.load()
    runtime = AgentRuntime(skills)
    telegram_adapter = TelegramAdapter(runtime, str(config.get("telegram_bot_token") or ""))
    web_adapter = WebAPIAdapter(runtime, str(config.get("allowed_origin") or ""))
    adapters: list[ChannelAdapter] = [telegram_adapter, web_adapter]
    heartbeat = HeartbeatEngine(runtime, adapters)

    _log("Meridian Gateway starting", color=ANSI_GREEN)
    _log(f"SOUL loaded: {SOUL_PATH}")
    _log(f"MEMORY loaded: {MEMORY_PATH}")
    _log(f"skills loaded: {len(loaded_skills)} definitions from {SKILLS_DIR}")

    web_adapter.start()
    telegram_adapter.start()
    heartbeat.start()
    _log(f"heartbeat started: interval={HEARTBEAT_INTERVAL_SECONDS}s", color=ANSI_GREEN)

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        _log("Meridian Gateway shutting down", color=ANSI_YELLOW)
    finally:
        heartbeat.stop()
        for adapter in adapters:
            adapter.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
