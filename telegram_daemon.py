#!/usr/bin/env python3
"""Zero-dependency Telegram bridge for Meridian Universal Operator."""

from __future__ import annotations

import json
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from meridian_config import load_config

GET_UPDATES_TIMEOUT_SECONDS = 30
LOOP_RETRY_DELAY_SECONDS = 2
MAX_MESSAGE_CHARS = 4000
FINAL_HEADING = "[✅ FINAL ANSWER]"
POGE_PREFIX = "[🛡️ PoGE PROTOCOL]"
MISSING_TOKEN_LINE = "[📡 MERIDIAN TELEGRAM LINK] telegram_bot_token not found in meridian_config.json. Messenger bridge inactive."
WORKSPACE_DIR = Path(__file__).resolve().parent
ANSI_MAGENTA = "[35m"
ANSI_BOLD = "[1m"
ANSI_RESET = "[0m"
ANSI_RE = re.compile(r"\[[0-?]*[ -/]*[@-~]")


def _load_telegram_token_or_notice() -> str:
    try:
        config = load_config(required=True)
    except FileNotFoundError:
        _print_missing_token_notice("[📡 MERIDIAN TELEGRAM LINK] Configuration missing. Run python3 meridian_setup.py first.")
        return ""
    except Exception as exc:
        _print_missing_token_notice(f"[📡 MERIDIAN TELEGRAM LINK] Failed to load meridian_config.json: {exc}")
        return ""
    return str(config.get("telegram_bot_token") or "").strip()


def _print_missing_token_notice(message: str = MISSING_TOKEN_LINE) -> None:
    print(f"{ANSI_BOLD}{ANSI_MAGENTA}{message}{ANSI_RESET}")


def _strip_ansi(text: str) -> str:
    return ANSI_RE.sub("", text)


def _telegram_request(token: str, method: str, payload: dict[str, Any]) -> dict[str, Any]:
    request = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/{method}",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=GET_UPDATES_TIMEOUT_SECONDS + 10) as response:
        body = json.loads(response.read().decode("utf-8", "replace"))
    if not body.get("ok"):
        raise RuntimeError(body.get("description") or f"Telegram API call failed: {method}")
    return body


def _send_message(
    token: str,
    chat_id: int | str,
    text: str,
    *,
    parse_mode: str | None = None,
    reply_to_message_id: int | None = None,
) -> None:
    payload: dict[str, Any] = {"chat_id": chat_id, "text": text}
    if parse_mode:
        payload["parse_mode"] = parse_mode
    if reply_to_message_id is not None:
        payload["reply_to_message_id"] = reply_to_message_id
    _telegram_request(token, "sendMessage", payload)


def _extract_final_answer(stdout: str) -> str:
    cleaned = _strip_ansi(stdout)
    index = cleaned.rfind(FINAL_HEADING)
    if index == -1:
        return ""
    return cleaned[index + len(FINAL_HEADING) :].strip()


def _extract_poge_line(stdout: str) -> str:
    cleaned = _strip_ansi(stdout)
    for line in cleaned.splitlines():
        if POGE_PREFIX in line:
            return line.strip()
    return ""


def _build_reply(stdout: str, stderr: str, returncode: int) -> str:
    final_answer = _extract_final_answer(stdout)
    if final_answer:
        reply = final_answer
    else:
        reply = stdout.strip() or stderr.strip() or f"Operator exited with code {returncode}."
    poge_line = _extract_poge_line(stdout)
    if poge_line and poge_line not in reply:
        reply = f"{reply}\n\n{poge_line}" if reply else poge_line
    if len(reply) > MAX_MESSAGE_CHARS:
        reply = reply[: MAX_MESSAGE_CHARS - 3].rstrip() + "..."
    return reply


def _poll_updates(token: str, offset: int | None) -> list[dict[str, Any]]:
    payload: dict[str, Any] = {"timeout": GET_UPDATES_TIMEOUT_SECONDS, "allowed_updates": ["message"]}
    if offset is not None:
        payload["offset"] = offset
    body = _telegram_request(token, "getUpdates", payload)
    result = body.get("result")
    if isinstance(result, list):
        return [item for item in result if isinstance(item, dict)]
    return []


def _process_message(token: str, message: dict[str, Any]) -> None:
    text = message.get("text")
    chat = message.get("chat") or {}
    chat_id = chat.get("id")
    message_id = message.get("message_id")
    if not isinstance(text, str) or not text.strip() or chat_id is None:
        return

    _send_message(
        token,
        chat_id,
        "⚙️ *Meridian Loom is governing your request...*",
        parse_mode="Markdown",
        reply_to_message_id=message_id if isinstance(message_id, int) else None,
    )

    completed = subprocess.run(
        ["python3", "universal_operator.py", text],
        capture_output=True,
        text=True,
        cwd=str(WORKSPACE_DIR),
        check=False,
    )
    reply = _build_reply(completed.stdout or "", completed.stderr or "", completed.returncode)
    _send_message(
        token,
        chat_id,
        reply,
        reply_to_message_id=message_id if isinstance(message_id, int) else None,
    )


def main() -> int:
    token = _load_telegram_token_or_notice()
    if not token:
        _print_missing_token_notice()
        return 0

    next_offset: int | None = None
    while True:
        try:
            updates = _poll_updates(token, next_offset)
            for update in updates:
                update_id = update.get("update_id")
                if isinstance(update_id, int):
                    next_offset = update_id + 1
                message = update.get("message")
                if isinstance(message, dict):
                    _process_message(token, message)
        except KeyboardInterrupt:
            return 0
        except urllib.error.URLError as exc:
            print(f"Telegram polling failed: {exc}", file=sys.stderr)
            time.sleep(LOOP_RETRY_DELAY_SECONDS)
        except Exception as exc:
            print(f"Telegram daemon error: {exc}", file=sys.stderr)
            time.sleep(LOOP_RETRY_DELAY_SECONDS)


if __name__ == "__main__":
    raise SystemExit(main())
