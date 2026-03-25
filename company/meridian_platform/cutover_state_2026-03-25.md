# Meridian Cutover State 2026-03-25

## Repo Truth
- meridian-intelligence: /home/ubuntu/.openclaw/workspace is main...origin/main [ahead 18] and dirty; cutover commits 7312491 and 736624b landed.
- meridian-loom: cutover commits 5622c5651a6a98a2ca79edacf52d106f04926774 and 1eca5ea3e09381008cca4a190c2939f613dcfa7b landed.
- meridian-kernel: cutover commit 598345d56a658b648edeff61a8da91718c9cb5df landed.
- safety snapshot: /home/ubuntu/.openclaw/workspace/.openclaw/migration-safety/2026-03-25/vps-exit/meridian-intelligence-prep.

## Active Services
- meridian-loom.service active
- meridian-workspace.service active
- soncompany-mcp.service active
- meridian-payment-monitor.service active
- caddy.service active

## Phase 1 Focus
- Route candidate: intelligence_on_demand_research
- Scope: route-specific cutover seam in company/mcp_server.py
- Keep do_on_demand_research() generic for shared callers
- Add route-level runtime ownership and fallback visibility

## Phase 1 Progress
- Route seam implemented in company/mcp_server.py for intelligence_on_demand_research
- Route env defaults set in /etc/default/meridian-mcp-runtime
- Current host canary default: MERIDIAN_INTELLIGENCE_ON_DEMAND_RESEARCH_RUNTIME=loom with fallback=1 and capability=clawskill.safe-web-research.v0
- Readiness now reports route ownership from deploy env truth
- Rehearsal script covers off_path, loom_on_path, rollback_path, and --restart-check
- loom_on_path now completes through Loom service boundary with clawskill.safe-web-research.v0
- rollback_path falls back to OpenClaw with explicit route_cutover.fallback metadata
