# Meridian Cutover State 2026-03-25

## Repo Truth
- meridian-intelligence: commit 153ee32bab6f91a6b2487e32c6b6e31c94ce2d42 landed.
- meridian-loom: commits e2e0d8e, 88f0793, and b7cf5dd6105b0a6c5dcc41c8b6166ee3a84a51d9 landed.
- meridian-kernel: commit 598345d56a658b648edeff61a8da91718c9cb5df landed.
- remote meridian-loom branches origin/codex-push (0e4b86c), origin/phase3/minimal-gap-replay-slice (ff618f3), origin/phase3/gap-replay-seam, and origin/phase3/evidence-slice are historical/non-main/superseded; keep them non-main.
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

- 2026-03-25T19:51Z: reran the cutover rehearsal with `--restart-check` plus a second stability pass; the live readiness surface still reports `owner=loom`, `fallback=on` (`fallback_enabled=true`), and `clawskill.safe-web-research.v0` with Loom preflight OK.
- 2026-03-25T19:51Z: direct `readiness.py` output stayed consistent after the restart, so the checked-in transcript remains valid but now has a fresh canary evidence point.
- 2026-03-25T19:58Z: reran `rehearse_on_demand_research_cutover.sh` cleanly; off-path, loom-on-path, and rollback-path all completed, and the final readiness refresh still reports `owner=loom`, `fallback=on`, `clawskill.safe-web-research.v0`, and Loom preflight OK while the overall verdict remains `OWNER_BLOCKED_TREASURY`.
