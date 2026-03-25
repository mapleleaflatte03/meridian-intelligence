# Host-Only Deploy Truth 2026-03-25

This note records the current host-facing deploy truth for Meridian Intelligence. It is intentionally non-secret and limited to file/path references.

## Live host-only deploy files
- `/etc/default/meridian-mcp-runtime`
- `company/meridian_platform/agent_registry.py`
- `company/meridian_platform/workspace.py`

## Snapshot bundle path
- `/home/ubuntu/.openclaw/workspace/.openclaw/migration-safety/2026-03-25/vps-exit/meridian-intelligence-prep`

## Snapshot-only items
- `README.md`
- `manifest.sha256`
- `company/meridian_platform/agent_registry.py`
- `company/meridian_platform/workspace.py`
- `etc/default/meridian-mcp-runtime.redacted`

## Inert-confirm-needed items
- Live `/etc/default/meridian-mcp-runtime` on the host, because it is the active runtime selector and includes the non-redacted service-token slot.
