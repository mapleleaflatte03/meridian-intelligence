#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORKSPACE_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
LAUNCH_DIR="${WORKSPACE_DIR}/company/launch"
ARTIFACT_DIR="$(mktemp -d /tmp/meridian_publish_artifacts.XXXXXX)"

python3 "${WORKSPACE_DIR}/scripts/test_publish_live_lane.py"

python3 "${LAUNCH_DIR}/publish_live.py" \
  --launch-dir "${LAUNCH_DIR}" \
  --artifact-dir "${ARTIFACT_DIR}" \
  --dry-run \
  --channels x,reddit,hn,discord \
  --site "https://app.welliam.codes" >/tmp/meridian_publish_dryrun.json

python3 - <<'PY'
import json
from pathlib import Path
payload = json.loads(Path("/tmp/meridian_publish_dryrun.json").read_text(encoding="utf-8"))
assert payload["status"] == "ok", payload
for channel in ("x", "reddit", "hn", "discord"):
    assert payload["results_by_channel"][channel]["status"] == "dry_run", payload
PY

MOCK_SERVER_PY="$(mktemp)"
MOCK_PORT=18777
cat >"${MOCK_SERVER_PY}" <<'PY'
#!/usr/bin/env python3
from __future__ import annotations
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class Handler(BaseHTTPRequestHandler):
    def _send(self, status: int, body: str, content_type: str = "application/json") -> None:
        data = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):  # noqa: N802
        if self.path == "/hn/auth":
            self._send(200, '<html><body><input type="hidden" name="goto" value="news"></body></html>', "text/html")
            return
        if self.path == "/hn/submit":
            self._send(200, '<html><body><input type="hidden" name="fnid" value="fn-123"></body></html>', "text/html")
            return
        self._send(404, json.dumps({"error": "not_found"}))

    def do_POST(self):  # noqa: N802
        if self.path == "/x/posts":
            self._send(200, json.dumps({"data": {"id": "190000001"}}))
            return
        if self.path == "/reddit/token":
            self._send(200, json.dumps({"access_token": "mock-reddit-token"}))
            return
        if self.path == "/reddit/submit":
            self._send(200, json.dumps({"json": {"errors": []}}))
            return
        if self.path == "/hn/auth":
            self._send(200, "ok", "text/plain")
            return
        if self.path == "/hn/submit-action":
            self._send(200, '<html><body><a href="item?id=456789">item</a></body></html>', "text/html")
            return
        if self.path == "/discord/webhook":
            self._send(200, json.dumps({"ok": True}))
            return
        self._send(404, json.dumps({"error": "not_found"}))

    def log_message(self, *_args, **_kwargs):  # noqa: D401
        return


if __name__ == "__main__":
    server = ThreadingHTTPServer(("127.0.0.1", 18777), Handler)
    server.serve_forever()
PY

python3 "${MOCK_SERVER_PY}" &
MOCK_PID=$!
trap 'kill ${MOCK_PID} >/dev/null 2>&1 || true; rm -f "${MOCK_SERVER_PY}"; rm -rf "${ARTIFACT_DIR}"' EXIT
sleep 1

MERIDIAN_X_API_TOKEN=mock-x-token \
MERIDIAN_X_POST_URL="http://127.0.0.1:${MOCK_PORT}/x/posts" \
MERIDIAN_REDDIT_CLIENT_ID=cid \
MERIDIAN_REDDIT_CLIENT_SECRET=csecret \
MERIDIAN_REDDIT_USERNAME=user \
MERIDIAN_REDDIT_PASSWORD=pass \
MERIDIAN_REDDIT_TOKEN_URL="http://127.0.0.1:${MOCK_PORT}/reddit/token" \
MERIDIAN_REDDIT_SUBMIT_URL="http://127.0.0.1:${MOCK_PORT}/reddit/submit" \
MERIDIAN_HN_USERNAME=hn_user \
MERIDIAN_HN_PASSWORD=hn_pass \
MERIDIAN_HN_LOGIN_URL="http://127.0.0.1:${MOCK_PORT}/hn/auth" \
MERIDIAN_HN_SUBMIT_URL="http://127.0.0.1:${MOCK_PORT}/hn/submit" \
MERIDIAN_HN_SUBMIT_ACTION_URL="http://127.0.0.1:${MOCK_PORT}/hn/submit-action" \
MERIDIAN_DISCORD_WEBHOOK_URL="http://127.0.0.1:${MOCK_PORT}/discord/webhook" \
python3 "${LAUNCH_DIR}/publish_live.py" \
  --launch-dir "${LAUNCH_DIR}" \
  --artifact-dir "${ARTIFACT_DIR}" \
  --channels x,reddit,hn,discord \
  --site "https://app.welliam.codes" >/tmp/meridian_publish_mock_live.json

python3 - <<'PY'
import json
from pathlib import Path
payload = json.loads(Path("/tmp/meridian_publish_mock_live.json").read_text(encoding="utf-8"))
assert payload["status"] == "ok", payload
expected = {
    "x": "posted",
    "reddit": "posted",
    "hn": "posted",
    "discord": "posted",
}
for channel, state in expected.items():
    assert payload["results_by_channel"][channel]["status"] == state, payload
PY

python3 - <<'PY'
import json
import urllib.request

BASE = "https://app.welliam.codes"
checks = [
    ("/api/institution/template", "json"),
    ("/api/institution/license/catalog", "json"),
    ("/", "html"),
    ("/proofs", "html_proofs"),
    ("/workflows", "html_workflows"),
]

def fetch(path: str):
    with urllib.request.urlopen(BASE + path, timeout=20) as response:
        return response.read().decode("utf-8", "ignore")

for path, mode in checks:
    body = fetch(path)
    if mode == "json":
        payload = json.loads(body)
        if path.endswith("/template"):
            assert payload.get("schema_version") == "meridian.institution_template.v1", payload
            assert len(payload.get("court_rule_set") or []) >= 3, payload
        if path.endswith("/catalog"):
            catalog = payload.get("catalog") or {}
            assert catalog.get("default_plan") == "institution-license-foundation", payload
            assert (catalog.get("checkout_capture_path") or "").endswith("/api/institution/license/checkout-capture"), payload
            assert len(catalog.get("plans") or []) >= 2, payload
    elif mode == "html":
        assert "Constitutional Institution License" in body, "Missing 'Constitutional Institution License' in homepage"
        assert "data-institution-license-checkout-form" in body, "Missing checkout form marker"
        assert "/api/institution/license/catalog" in body, "Missing catalog API reference"
        assert "$299" in body or "299" in body, "Missing foundation pricing value"
        assert "$79" in body or "79" in body, "Missing maintenance pricing value"
        assert "data-checkout-submit" in body, "Missing checkout submit button marker"
        assert "data-tx-hash-input" in body, "Missing tx-hash input marker"
        assert "trust-bar" in body or "Local-first" in body, "Missing trust bar section"
        assert "pricing" in body.lower(), "Missing pricing section"
        assert "premium-footer" in body or "footer-nav-group" in body, "Missing premium footer"
    elif mode == "html_proofs":
        assert "site-nav" in body, "Missing site nav on /proofs"
        assert "Docs" in body, "Missing Docs nav item on /proofs"
        assert "nav-cta" in body, "Missing nav CTA on /proofs"
        assert "premium-footer" in body or "footer-nav-group" in body, "Missing premium footer on /proofs"
    elif mode == "html_workflows":
        assert "site-nav" in body, "Missing site nav on /workflows"
        assert "Docs" in body, "Missing Docs nav item on /workflows"
        assert "nav-cta" in body, "Missing nav CTA on /workflows"
        assert "premium-footer" in body or "footer-nav-group" in body, "Missing premium footer on /workflows"
PY

echo "acceptance_publish_live_lane: PASS"
