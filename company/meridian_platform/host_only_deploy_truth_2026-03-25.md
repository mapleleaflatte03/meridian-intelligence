# Host-Only Deploy Truth 2026-03-25

This note records the current host-facing deploy truth for Meridian Intelligence. It is intentionally non-secret and limited to file/path references.

## Live host-only deploy files
- `/etc/default/meridian-mcp-runtime`

## Snapshot bundle path
- `/home/ubuntu/.openclaw/workspace/.openclaw/migration-safety/2026-03-25/vps-exit/meridian-intelligence-prep`

## Snapshot-only items
- `README.md`
- `manifest.sha256`
- `company/meridian_platform/agent_registry.py` (captured in the existing safety bundle)
- `company/meridian_platform/workspace.py` (captured in the existing safety bundle)
- `etc/default/meridian-mcp-runtime.redacted`

## Inert / non-main unless later proven otherwise
- `company/lead_tracker.py`
- `company/meridian_platform/bootstrap.py`
- `company/meridian_platform/ci_vertical.py`
- Live `/etc/default/meridian-mcp-runtime` on the host, because it is the active runtime selector and includes the non-redacted service-token slot.

## Runtime-audit / runtime-state artifacts
- `company/meridian_platform/audit_log.jsonl` is gitignored runtime audit data and stays non-main.
- `company/meridian_platform/metering.jsonl`, `company/meridian_platform/authority_queue.json`, and `company/meridian_platform/court_records.json` are also gitignored runtime state and should remain outside main unless they are explicitly snapshotted later.
