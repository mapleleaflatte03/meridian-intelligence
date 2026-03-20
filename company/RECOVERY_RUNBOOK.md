# Meridian Recovery Runbook

**Date:** 2026-03-20
**Status:** Operationally blocked — two owner actions required

---

## What Is Broken

Two independent blockers prevent the Meridian pipeline from running:

1. **OpenAI Codex workspace deactivated** — the upstream model provider (openai-codex) rejects all agent requests with `{"detail":{"code":"deactivated_workspace"}}`
2. **Treasury below reserve floor** — $2.00 cash vs $50.00 reserve floor = $-48.00 runway, blocking all budget-gated pipeline phases

## What Was Repaired (Engineering-Owned)

| Fix | Detail |
|-----|--------|
| Workspace auth hardened | All POST endpoints now enforce `by='owner'` server-side |
| Caddy basicauth | Mutating `/api/*` routes require HTTP Basic Auth |
| Treasury route fixed | `/api/treasury/*` now routes to workspace server (was falling to MCP → 404) |
| MCP paid-tool integrity | All 5 tools have `agent_id`; x402 fails closed; settlement refs are FIFO-safe; revenue recording is idempotent |
| Revenue accounting unified | All codepaths use `total_revenue_usd` (stale `revenue_received_usd` eliminated) |
| Payment email attribution | Empty wallet no longer matches any payment |
| Premium delivery dedup | `brief_date` field for reliable dedup; `record_delivery()` returns success/failure |
| Tests rewritten | `unittest.TestCase` — 5/5 pass (workspace), 4/4 pass (kernel) |
| Pipeline bootstrap files | Created `night-shift/BACKLOG.md` and `night-shift/LAST_HANDOFF.md` |
| Caddy credentials | Stored at `/etc/caddy/.workspace_credentials` (mode 0600) |

## What Still Requires Owner Action

### Action 1: Reactivate OpenAI Codex Workspace

**What:** The OpenAI Codex API workspace associated with your OAuth login is deactivated. The error is visible in scheduler state after roughly 2026-03-19T19:34Z. The exact upstream cause is not locally provable from this repo alone.

**Where:** This appears to be upstream workspace/account state on the OpenAI Codex side, not a local code or file-state issue inside this repo.

**Likely recovery path:**
1. Open the same OpenAI Codex account/workspace used by `openclaw configure`
2. Check whether the workspace is deactivated, suspended, or blocked by billing/usage limits
3. Reactivate or re-enable that workspace if the UI offers it
4. If there is no reactivation control, resolve billing/account issues first or re-bind this machine with `openclaw configure`

**If the workspace was permanently removed or the account changed:**
```bash
openclaw configure   # re-run OAuth flow
```

**Verify it worked:**
```bash
openclaw agent --agent main --message "respond with PONG" --timeout 15000
```
Expected: agent responds with "PONG" (not `deactivated_workspace`)

### Action 2: Unblock Treasury Gating

**What:** The reserve floor is $50 but treasury only has $2 of owner capital and $0 revenue. Pipeline is correctly blocked.

**Choose one:**

**Option A — Recapitalize (add real money):**
```bash
cd /root/.openclaw/workspace
python3 company/meridian_platform/treasury.py contribute \
  --amount 50 --note "operator recapitalization for pipeline restart" --by owner
```
This brings treasury to $52, clearing the $50 floor ($2 runway).

**Option B — Lower reserve floor for pre-revenue mode:**
```bash
cd /root/.openclaw/workspace
python3 company/meridian_platform/treasury.py set-reserve-floor \
  --amount 0 --note "pre-revenue operating mode — no external customers yet" --by owner
```
This is a policy decision. It allows the pipeline to run on any treasury balance. Only choose this if you accept that the reserve floor was premature for a pre-revenue system.

**Verify it worked:**
```bash
python3 company/meridian_platform/ci_vertical.py preflight
```
Expected: `PREFLIGHT: OK` (no BLOCKED phases except possibly sentinel zero-authority)

---

## After Both Actions — Controlled Verification Ladder

Run these in order to confirm the system is fully operational:

```bash
# Step 1: Workspace health
openclaw agent --agent main --message "respond with PONG" --timeout 15000

# Step 2: Preflight check
cd /root/.openclaw/workspace
python3 company/meridian_platform/ci_vertical.py preflight

# Step 3: Dry-run delivery
python3 company/premium_deliver.py --dry-run --skip-preflight
python3 company/channel_deliver.py --dry-run --skip-preflight

# Current expected result before a fresh brief exists:
# - premium_deliver.py: "No brief available for delivery."
# - channel_deliver.py: "No brief available for channel delivery."
# After one successful pipeline cycle creates a brief, rerun these commands.

# Step 4: Trigger one controlled pipeline cycle
openclaw cron run "7274a600-3588-430d-807c-4286bff20f5a" --timeout 60000

# Step 5: Wait for pipeline to complete (jobs run on schedule)
# Or manually trigger each stage:
openclaw cron run "50390799-0f7d-4a62-9c88-52e8290d2604" --timeout 120000  # research
openclaw cron run "c48e2244-e47d-47e7-9bc4-ad053ba4f8c1" --timeout 120000  # write
openclaw cron run "2581a129-4735-4c3e-a0ec-924689dcda39" --timeout 120000  # qa
openclaw cron run "25911223-5a4a-44ae-a089-c1d8527e4e58" --timeout 120000  # deliver
```

---

## What "Green" Looks Like

| Check | Expected |
|-------|----------|
| `openclaw agent --agent main --message "PONG"` | Agent responds (no deactivated_workspace) |
| `python3 ci_vertical.py preflight` | `PREFLIGHT: OK` |
| `python3 treasury.py runway` | `Runway: $X.XX` (positive number) |
| `ls night-shift/brief-YYYY-MM-DD.md` | File exists after one pipeline cycle |
| `python3 premium_deliver.py --dry-run --skip-preflight` | Before a fresh brief exists: reports `No brief available for delivery.` After one successful cycle creates a brief: shows dry-run delivery output for the 2 test subscribers |
| `python3 channel_deliver.py --dry-run --skip-preflight` | Before a fresh brief exists: reports `No brief available for channel delivery.` After one successful cycle creates a brief: renders the channel preview |
| Telegram @eggsama_bot `/start` | Bot responds |
| `curl https://app.welliam.codes/api/status` | JSON with institution active |

---

## What Is Healthy (Verified 2026-03-20)

- All local services running: caddy, meridian-workspace, soncompany-mcp, openclaw-do-proxy
- Website serving at https://app.welliam.codes (200 OK)
- Workspace API: /api/status, /api/treasury, /api/agents all responding
- Mutating API endpoints: auth-protected (401 without credentials)
- Treasury route: /api/treasury/* correctly proxied to workspace server
- Telegram bot @eggsama_bot: connected and healthy
- Trial reminders: working (2 owner test accounts, expiring 2026-03-23)
- Economy tests: 5/5 passing
- Git repos: tracked changes were pushed at verification time; re-check local workspace status before making further edits
- Pipeline bootstrap files: created
- Cron scheduler: enabled, 12 jobs configured
