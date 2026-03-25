# On-Demand Research Deploy Truth (Redacted)

As of 2026-03-25, current live cutover-relevant truth for `intelligence_on_demand_research`.

- Route owner override: `MERIDIAN_INTELLIGENCE_ON_DEMAND_RESEARCH_RUNTIME=loom`
- Fallback: `MERIDIAN_INTELLIGENCE_ON_DEMAND_RESEARCH_ALLOW_FALLBACK=0`
- Loom bin: `/root/.local/share/meridian-loom/current/bin/loom`
- Loom root: `/root/.local/share/meridian-loom/runtime/default`
- Loom agent: `agent_leviathann`
- Loom service token: `<redacted; required by HTTP surface>`
- Research capability: `clawskill.safe-web-research.v0`

Service semantics:
- `soncompany-mcp.service` runs `/usr/bin/python3 /root/.openclaw/workspace/company/mcp_server.py --http 18900`
- The unit uses `EnvironmentFile=-/etc/default/meridian-mcp-runtime`
- Live readiness reports `http_token_required=true` on the Loom HTTP surface
- The Loom capability is promoted, verified, and currently supports the `url_report_v0` adapter
