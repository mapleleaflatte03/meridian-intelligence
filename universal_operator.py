#!/usr/bin/env python3
"""Dynamic ReAct operator that uses Loom capabilities through a local LLM."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import textwrap
import urllib.error
import urllib.request
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
from typing import Any


LLM_BASE_URL = os.environ.get("LLM_BASE_URL", "http://127.0.0.1:11434/v1/chat/completions")
LLM_MODEL = os.environ.get("LLM_MODEL", "gpt-3.5-turbo")
LOOM_BIN = os.environ.get(
    "MERIDIAN_LOOM_BIN",
    "/home/ubuntu/.local/share/meridian-loom/current/bin/loom",
)
LOOM_ROOT = os.environ.get(
    "MERIDIAN_LOOM_ROOT",
    "/home/ubuntu/.local/share/meridian-loom/runtime/default",
)
LOOM_ORG_ID = os.environ.get("MERIDIAN_LOOM_ORG_ID", "org_51fcd87f")
LOOM_AGENT_ID = os.environ.get("MERIDIAN_LOOM_AGENT_ID", "agent_atlas")
MAX_STEPS = int(os.environ.get("MERIDIAN_OPERATOR_MAX_STEPS", "6"))
REQUEST_TIMEOUT_SECONDS = int(os.environ.get("MERIDIAN_OPERATOR_TIMEOUT_SECONDS", "90"))
RESET = "\033[0m"
BOLD = "\033[1m"
MAGENTA = "\033[35m"
CYAN = "\033[36m"
GREEN = "\033[32m"
YELLOW = "\033[33m"

USER_GOAL_HEADING = "[👤 USER GOAL]"
THOUGHT_HEADING = "[🤔 THOUGHT]"
ACTION_HEADING = "[🛠️ ACTING via LOOM WASM]"
OBSERVATION_HEADING = "[👁️ OBSERVATION]"
FINAL_HEADING = "[✅ FINAL ANSWER]"

SYSTEM_PROMPT = textwrap.dedent(
    f"""
    You are Meridian Governed Operator.

    You must return strictly valid JSON only. No markdown fences. No commentary outside JSON.

    Required schema:
    {{
      "thought": "string",
      "tool_call": null or {{
        "capability": "string",
        "payload": {{}}
      }},
      "final_answer": "optional string when done"
    }}

    Runtime details:
    - Loom binary: {LOOM_BIN}
    - Loom root: {LOOM_ROOT}
    - Loom org_id: {LOOM_ORG_ID}
    - Loom agent_id: {LOOM_AGENT_ID}

    Available Loom capabilities:
    - loom.browser.navigate.v1 -> fetches a URL. Payload shape: {{"url": "https://example.com"}}
    - loom.system.info.v1 -> safe bounded host OS/hardware info. Payload shape: {{}}
    - loom.fs.read.v1 -> reads only allowed workspace/diagnostic files. Payload shape: {{"path": "workspace/example.txt"}}
    - loom.fs.write.v1 -> writes data to a file. Payload shape: {{"path": "workspace/file.txt", "content": "text"}}

    Operating rules:
    - Use one tool call at a time.
    - Use tool calls when you need evidence.
    - If an observation reports a failure, recover truthfully or finish truthfully.
    - Only provide final_answer when tool_call is null and the work is done.
    - Keep final answers short and factual.
    """
).strip()


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


def _heading(text: str, color: str) -> None:
    print(f"{BOLD}{color}{text}{RESET}")


def _pretty(value: Any) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value, indent=2, ensure_ascii=False, sort_keys=True)


def _ensure_legacy_v1_adapter_alias() -> None:
    alias_path = Path("/tmp/meridian-kernel/kernel/adapters/openclaw_compatible.py")
    target_path = Path("/tmp/meridian-kernel/kernel/adapters/legacy_v1_compatible.py")
    if alias_path.exists() or not target_path.exists():
        return
    alias_path.parent.mkdir(parents=True, exist_ok=True)
    alias_path.write_text(
        "from adapters.legacy_v1_compatible import *  # noqa: F401,F403\n",
        encoding="utf-8",
    )


def _loom_cli_prefix() -> list[str]:
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        return ["sudo", "-u", "ubuntu", "-H"]
    return []


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


def _chat(messages: list[dict[str, str]], timeout: int = REQUEST_TIMEOUT_SECONDS) -> str:
    request = urllib.request.Request(
        LLM_BASE_URL,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {os.environ.get('OPENAI_API_KEY', 'meridian-local-key')}",
        },
        data=json.dumps(
            {
                "model": LLM_MODEL,
                "messages": messages,
                "temperature": 0.2,
                "stream": False,
            }
        ).encode("utf-8"),
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        payload = json.loads(response.read().decode("utf-8", "replace"))
    return payload["choices"][0]["message"]["content"]


def _valid_step_response(parsed: dict[str, Any] | None) -> bool:
    if not isinstance(parsed, dict):
        return False
    if not isinstance(parsed.get("thought"), str):
        return False
    tool_call = parsed.get("tool_call")
    final_answer = parsed.get("final_answer")
    if tool_call is None and not str(final_answer or "").strip():
        return False
    if tool_call is not None and not isinstance(tool_call, dict):
        return False
    return True


def _llm_step(goal: str, history: list[dict[str, Any]]) -> dict[str, Any]:
    base_messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {
            "role": "user",
            "content": json.dumps(
                {
                    "goal": goal,
                    "history": history,
                    "instruction": "Decide the next single action. Return strict JSON only. Include a tool_call or a final_answer.",
                },
                indent=2,
                ensure_ascii=False,
            ),
        },
    ]
    raw_response = _chat(base_messages)
    parsed = _extract_json(raw_response)
    if _valid_step_response(parsed):
        return parsed

    last_response = raw_response
    for _attempt in range(2):
        repair_messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "goal": goal,
                        "history": history,
                        "bad_response": last_response,
                        "instruction": "Repair bad_response into strict JSON with keys thought, tool_call, final_answer. The repaired JSON must include either a tool_call or a final_answer. Return JSON only.",
                    },
                    indent=2,
                    ensure_ascii=False,
                ),
            },
        ]
        last_response = _chat(repair_messages)
        parsed = _extract_json(last_response)
        if _valid_step_response(parsed):
            return parsed

    raise ValueError(f"LLM did not return valid JSON after repair attempts: {last_response}")


def _normalize_tool_call(tool_call: Any) -> tuple[str, dict[str, Any]]:
    if not isinstance(tool_call, dict):
        raise ValueError(f"tool_call must be an object, got: {tool_call!r}")
    capability = str(tool_call.get("capability") or "").strip()
    if not capability:
        raise ValueError(f"tool_call missing capability: {tool_call!r}")
    payload = tool_call.get("payload")
    if not isinstance(payload, dict):
        payload = {}
    if capability == "loom.browser.navigate.v1":
        payload = {
            "url": str(payload.get("url") or payload.get("href") or payload.get("target_url") or "").strip(),
        }
    elif capability == "loom.fs.write.v1":
        payload = {
            "path": str(
                payload.get("path")
                or payload.get("file_path")
                or payload.get("target_path")
                or payload.get("file")
                or "workspace/output.txt"
            ).strip(),
            "content": str(
                payload.get("content")
                or payload.get("text")
                or payload.get("body")
                or payload.get("data")
                or ""
            ),
        }
    elif capability == "loom.system.info.v1":
        payload = {}
    elif capability == "loom.fs.read.v1":
        payload = {
            "path": str(
                payload.get("path")
                or payload.get("file_path")
                or payload.get("target_path")
                or payload.get("file")
                or ""
            ).strip(),
        }
    return capability, payload



def _load_json_file(path: str) -> dict[str, Any]:
    candidate = str(path or "").strip()
    if not candidate or not os.path.exists(candidate):
        return {}
    with open(candidate, encoding="utf-8") as handle:
        return json.load(handle)


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


def _excerpt(text: str, limit: int = 400) -> str:
    value = " ".join(text.split()).strip()
    if len(value) <= limit:
        return value
    return value[: limit - 3].rstrip() + "..."


def _mirror_fetch(url: str) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "MeridianUniversalOperator/1.0",
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


def _run_tool(capability: str, payload: dict[str, Any]) -> dict[str, Any]:
    executed_capability = capability
    executed_payload = payload
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
        executed_capability,
        "--payload-json",
        json.dumps(executed_payload, ensure_ascii=False),
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
        return {
            "ok": False,
            "capability": capability,
            "payload": payload,
            "executed_capability": executed_capability,
            "executed_payload": executed_payload,
            "command": command,
            "error": f"{exc.__class__.__name__}: {exc}",
        }

    stdout_text = (completed.stdout or "").strip()
    stderr_text = (completed.stderr or "").strip()
    parsed_stdout = _extract_json(stdout_text) or {}

    observation: dict[str, Any] = {
        "ok": completed.returncode == 0,
        "capability": capability,
        "payload": payload,
        "executed_capability": executed_capability,
        "executed_payload": executed_payload,
        "command": command,
        "returncode": completed.returncode,
        "stdout": stdout_text,
        "stderr": stderr_text,
    }
    if parsed_stdout:
        observation["parsed_stdout"] = parsed_stdout

    worker_result = _load_json_file(parsed_stdout.get("worker_result_path", ""))
    if worker_result:
        observation["worker_result"] = worker_result

    if capability == "loom.browser.navigate.v1":
        host_response = worker_result.get("host_response_json", {})
        if isinstance(host_response, str):
            try:
                host_response = json.loads(host_response)
            except json.JSONDecodeError:
                host_response = {}
        if isinstance(host_response, dict):
            observation["browser_view"] = {
                "decision": host_response.get("decision"),
                "title": str(host_response.get("title") or "").strip(),
                "excerpt": _excerpt(str(host_response.get("body_excerpt_utf8") or "").strip()),
                "final_url": str(host_response.get("final_url") or payload.get("url") or "").strip(),
                "note": str(host_response.get("note") or "").strip(),
            }
            if not observation["browser_view"]["title"] and not observation["browser_view"]["excerpt"]:
                observation["mirror_fetch"] = _mirror_fetch(payload.get("url", ""))
    elif capability == "loom.system.info.v1":
        host_response = worker_result.get("host_response_json", {})
        if isinstance(host_response, str):
            try:
                host_response = json.loads(host_response)
            except json.JSONDecodeError:
                host_response = {}
        if isinstance(host_response, dict):
            observation["system_info"] = {
                "decision": host_response.get("decision"),
                "hostname": str(host_response.get("hostname_utf8") or "").strip(),
                "uname": str(host_response.get("uname_utf8") or "").strip(),
                "os_release": str(host_response.get("os_release_utf8") or "").strip(),
                "note": str(host_response.get("note") or "").strip(),
                "truncated": host_response.get("truncated"),
            }
    elif capability == "loom.fs.read.v1":
        host_response = worker_result.get("host_response_json", {})
        if isinstance(host_response, str):
            try:
                host_response = json.loads(host_response)
            except json.JSONDecodeError:
                host_response = {}
        if isinstance(host_response, dict):
            observation["fs_read"] = {
                "decision": host_response.get("decision"),
                "path": str(host_response.get("path") or payload.get("path") or "").strip(),
                "content": str(host_response.get("content_utf8") or ""),
                "bytes_read": host_response.get("bytes_read"),
                "note": str(host_response.get("note") or "").strip(),
                "truncated": host_response.get("truncated"),
            }

    return observation


def _history_observation(observation: dict[str, Any]) -> dict[str, Any]:
    history_view = {
        "ok": bool(observation.get("ok")),
        "capability": observation.get("capability"),
        "executed_capability": observation.get("executed_capability"),
        "returncode": observation.get("returncode"),
        "stderr": observation.get("stderr") or "",
    }
    if "browser_view" in observation:
        history_view["browser_view"] = observation.get("browser_view")
    if "mirror_fetch" in observation:
        history_view["mirror_fetch"] = observation.get("mirror_fetch")
    if "system_info" in observation:
        history_view["system_info"] = observation.get("system_info")
    if "fs_read" in observation:
        history_view["fs_read"] = observation.get("fs_read")
    worker_result = observation.get("worker_result")
    if isinstance(worker_result, dict):
        history_view["worker_result"] = {
            "status": worker_result.get("status"),
            "host_calls": worker_result.get("host_calls"),
            "entrypoint_result": worker_result.get("entrypoint_result"),
        }
    parsed_stdout = observation.get("parsed_stdout")
    if isinstance(parsed_stdout, dict):
        history_view["runtime"] = {
            "status": parsed_stdout.get("status"),
            "runtime_outcome": parsed_stdout.get("runtime_outcome"),
            "worker_status": parsed_stdout.get("worker_status"),
            "worker_result_path": parsed_stdout.get("worker_result_path"),
        }
    stdout = str(observation.get("stdout") or "").strip()
    if stdout and "browser_view" not in observation:
        history_view["stdout_excerpt"] = stdout[:500]
    return history_view


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print("usage: universal_operator.py '<goal>'", file=sys.stderr)
        return 2

    goal = argv[1]
    _ensure_legacy_v1_adapter_alias()

    _heading(USER_GOAL_HEADING, MAGENTA)
    print(goal)

    history: list[dict[str, Any]] = []
    for _step in range(1, MAX_STEPS + 1):
        step = _llm_step(goal, history)
        thought = str(step.get("thought") or "").strip() or "<none>"
        tool_call = step.get("tool_call")
        final_answer = step.get("final_answer")

        _heading(THOUGHT_HEADING, CYAN)
        print(thought)

        if tool_call is not None:
            capability, payload = _normalize_tool_call(tool_call)
            action = {"capability": capability, "payload": payload}
            _heading(ACTION_HEADING, GREEN)
            print(_pretty(action))
            observation = _run_tool(capability, payload)
            _heading(OBSERVATION_HEADING, YELLOW)
            print(_pretty(observation))
            history.append({"role": "assistant_thought", "value": thought})
            history.append({"role": "assistant_tool_call", "value": action})
            history.append({"role": "tool_observation", "value": _history_observation(observation)})
            continue

        if final_answer:
            _heading(FINAL_HEADING, GREEN)
            print(str(final_answer))
            return 0

        observation = {
            "ok": False,
            "error": "Model returned neither a tool_call nor a final_answer.",
        }
        _heading(OBSERVATION_HEADING, YELLOW)
        print(_pretty(observation))
        history.append({"role": "assistant_thought", "value": thought})
        history.append({"role": "tool_observation", "value": observation})

    _heading(FINAL_HEADING, GREEN)
    print("Unable to complete the goal within the configured step limit.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
