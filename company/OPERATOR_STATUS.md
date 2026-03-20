# Operator Status — Meridian

Last updated: 2026-03-20
Evidence base: direct commands and local file inspection (see Evidence column).

This document records the current operational truth of the Meridian system.
It is not promotional copy. Every claim is tagged verified, inferred, or unknown.

---

## System Status at a Glance

| Component | Status | Source |
|-----------|--------|--------|
| Night-shift pipeline | BLOCKED — `deactivated_workspace` | jobs.json consecutiveErrors |
| Constitutional preflight | BLOCKED — treasury $48 below reserve floor | `treasury.py runway` |
| Channel delivery (@MeridianIntelligence) | NOT RUNNING — pipeline blocked, no new briefs | preflight output |
| Premium delivery (@eggsama_bot) | NOT RUNNING — pipeline blocked, workspace error | jobs.json |
| Trial reminders | Would run for 2 active IDs (owner test accounts) | subscriptions.json |
| Revenue dashboard | Last OK 2026-03-16; runs weekly, next 2026-03-23 | jobs.json state |
| External customers | ZERO — all subscription entries are owner test or synthetic residue | subscriptions.json `_meta` |
| Customer revenue received | $0.00 | ledger.json `revenue_received_usd` |
| Owner capital in treasury | $2.00 USDC | ledger.json `cash_usd`, transactions.jsonl |
| Reserve floor | $50.00 | ledger.json `reserve_floor_usd` |
| Runway | $-48.00 (below floor) | `python3 meridian_platform/treasury.py runway` |

---

## Treasury — Verified Facts

**Balance:** $2.00 (owner capital only, no customer revenue — originated as 2.00 USDC on Base L2, recorded as USD in ledger)
**Reserve floor:** $50.00
**Runway:** $-48.00 — treasury is $48 below the reserve floor

**Evidence for the $2.00:**
- `economy/ledger.json`: `"cash_usd": 2.0`, `"owner_capital_contributed_usd": 2.0`, `"revenue_received_usd": 0.0`
- `economy/transactions.jsonl` (line 35-36): 2.00 USDC received 2026-03-16, initially detected as `customer_payment`, reclassified to `owner_capital` per CLAUDE.md §12 (owner wallet)
- TX hash: `0xde03d0dc2602b815f2bebd3ee7aae005e8a4d3d44fa23c08ba918512c337db93`
- Note in ledger: "Clean treasury. $2 USDC owner capital deposit 2026-03-16 reclassified from customer_payment to owner_capital per CLAUDE.md §12."

**Why the pipeline is still blocked:** The reserve floor of $50 was set when the pipeline was being configured. With only $2 in treasury, the floor blocks all spending. The floor policy needs to be adjusted for pre-revenue operation OR the treasury needs $48+ recapitalization.

---

## Blocker 1: Workspace Deactivated

**Status:** VERIFIED

All cron jobs with `"agentId": "main"` fail with:
```
{"detail":{"code":"deactivated_workspace"}}
```

**Jobs affected (all at consecutiveErrors: 2):**
- night-shift-kickoff, night-shift-research, night-shift-execute
- night-shift-write, night-shift-qa, night-shift-deliver
- night-shift-score, premium-deliver, gen-sample-brief

**What still runs:**
- `revenue-dashboard` — last ok 2026-03-16, weekly, no agentId restriction

**Required owner action:** Reactivate the OpenClaw workspace. This is a runtime-level
state that cannot be changed by editing files in this repo.

---

## Blocker 2: Constitutional Preflight — Treasury Below Reserve Floor

**Status:** VERIFIED
**Command:** `python3 company/meridian_platform/ci_vertical.py preflight`
**Output (2026-03-20):**
```
BLOCKED: research — budget: Treasury below reserve floor (runway $-48.00)
BLOCKED: write   — budget: Treasury below reserve floor (runway $-48.00)
BLOCKED: qa_sentinel, qa_aegis, execute, compress, deliver, score
PREFLIGHT: BLOCKED — pipeline should not run
```

**Two paths to unblock preflight:**

Option A — Recapitalize treasury:
```bash
python3 company/meridian_platform/treasury.py contribute \
  --amount 50 --note "operator recapitalization" --by owner
```
This brings treasury to $52, which clears the $50 floor ($2 runway).

Option B — Lower the reserve floor to match actual operating level:
```bash
python3 company/meridian_platform/treasury.py set-reserve-floor \
  --amount 0 --note "pre-revenue operating mode" --by owner
```
This is a policy decision. It means the pipeline can run on any treasury balance.
Document the reason. Only the owner should make this call.

**Note:** These commands update ledger.json only. They are not on-chain.

---

## Blocker 3: No Recent Brief Files

**Status:** VERIFIED (consequence of Blocker 1)

Because the night-shift pipeline has not run since the workspace was deactivated,
there are no brief files at `night-shift/brief-YYYY-MM-DD.md` for recent dates.

Manual pilot delivery requires an existing brief. Without one, premium_deliver.py
and channel_deliver.py have nothing to send.

**Path to unblock:** Resolve Blocker 1 and Blocker 2, then allow the pipeline to
run one full cycle.

---

## Sentinel Zero-Authority State (Additional Preflight Note)

**Status:** VERIFIED

Sentinel has `zero_authority: true` (AUTH=0). This was last applied 2026-03-19T23:10:01Z
when AUTH reached 0 through scoring. Preflight includes a remediation note:

```
NOTE: Sentinel remediation path active
  - Run a remediation-only task and record the evidence in court/audit.
  - Recover AUTH above 15 or manually lift zero_authority before execute rights return.
  - Current restrictions to clear: assign, execute, lead.
```

Sentinel in zero_authority does not block the full pipeline — research, write, execute,
deliver, score phases use other agents. Only `qa_sentinel` is affected.
The treasury blocker is the binding constraint, not Sentinel's state.

---

## Subscription State

**Status:** VERIFIED via subscriptions.json and _meta block

**Active (trial):**
- `6114408283` — owner-controlled internal test account, expires 2026-03-23
- `1053016694` — owner-controlled internal test account, expires 2026-03-23

**Cancelled test residue:**
- test123, verify-test, 999888777, 999000111, 999999999 — all synthetic test entries from 2026-03-16 integration testing

**External customers:** ZERO
**Customer revenue:** $0.00

**Trial reminders (upcoming, for owner test IDs):**
- 2026-03-21: 2-day reminder (inferred — trial_reminder.py will reach these IDs)
- 2026-03-22: 1-day reminder
- 2026-03-23: Expiry-day reminder
These reminders go to owner-controlled accounts. Not funnel activity.

---

## What Can Be Done Without Owner Action

- Read and inspect all state files
- Run `--dry-run` on any delivery script to see what would be sent
- Improve product surfaces (docs, site copy, architecture docs)
- File court records, review economy ledger
- Run `ci_vertical.py preflight` to get current gate status

---

## Required Owner Actions (Priority Order)

1. **Reactivate the OpenClaw workspace** — nothing automated runs without this.
   Cannot be done by editing files.

2. **Resolve the treasury blocker** — choose one:
   - Contribute $48+ to clear the reserve floor, OR
   - Lower the reserve floor for pre-revenue operating mode.
   See command examples in Blocker 2 section above.

3. **Acquire first external customer** — zero external traction exists.
   Manual pilot path is available via email or Telegram DM (see pilot.html).

---

## What Is Healthy (Verified)

- Codebase intact, no corruption
- Constitutional primitives (institution, agents, authority, court) initialized and enforced
- Website accessible: app.welliam.codes (index.html, pilot.html, demo.html)
- Public Telegram channel @MeridianIntelligence exists
- Bot @eggsama_bot exists and can accept /start commands
- GitHub repo mapleleaflatte03/meridian-intelligence is public
- OSS kernel mapleleaflatte03/meridian-kernel is public (Apache-2.0)
- x402 payment infrastructure is code-complete (unproven against real external customer)
- $2.00 USDC owner capital on record in treasury

---

## Delivery Architecture

See `DELIVERY_ARCHITECTURE.md` in this directory for the full Telegram channel/bot/premium model.

---

## STATUS DOC RULES — Anti-Regression Guardrail

These rules apply to this document and any future operator status docs:

1. **Every financial claim must cite a source file or command.**
   Wrong: "Treasury is $0."
   Right: "Treasury cash_usd=2.0 per economy/ledger.json, revenue_received_usd=0.0."

2. **Every command example must be tested before publication.**
   Wrong: `treasury.py deposit --amount 50` (command does not exist)
   Right: `treasury.py contribute --amount 50 --note "..." --by owner` (verified via --help)

3. **Repo visibility claims must be confirmed by direct check, not assumed.**
   Check `gh repo view <repo> --json isPrivate` or inspect the repo directly.

4. **Every unverified claim must be labeled `[inferred]` or `[unknown]`.**
   Do not write confident prose for things you have not directly checked.

5. **Status docs are not promotional copy.**
   If something is broken, say it is broken and state the exact reason.
   Softening operational failures misleads operators and creates rework.
