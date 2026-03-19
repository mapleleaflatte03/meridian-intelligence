# Meridian Architecture — Constitutional Operating System for Autonomous Institutions

## What Meridian Is

Meridian is a constitutional operating system for running AI agents as managed, governed, billable digital labor. It is built on exactly five primitives: **Institution, Agent, Authority, Treasury, Court**.

Organizations use Meridian to:
- Found charter-governed institutions with lifecycle management
- Register and govern AI agents as first-class entities
- Enforce authority through approval queues, delegations, and kill switch
- Track real money through treasury with reserve floors and runway
- Adjudicate violations through a court system with sanctions and appeals
- Run agent workflows with identity, permissions, budget, and audit
- Meter usage and manage spend per organization and per agent

## What Meridian Is Not

- Not a chatbot or assistant product
- Not a PMM/CI tool (competitive intelligence is the first *managed vertical*, not the company)
- Not an open marketplace (ecosystem comes after trust primitives exist)
- Not an uncontrolled autonomy platform

## Five Primitives

```
┌─────────────────────────────────────────────────────┐
│  Institution                                         │
│  Charter, policy defaults, lifecycle, settings        │
│  (the governed container for everything below)       │
├─────────────────────────────────────────────────────┤
│  Agent                                               │
│  Identity, purpose, scopes, budget, risk state,      │
│  lifecycle, economy participation                    │
├─────────────────────────────────────────────────────┤
│  Authority                                           │
│  Approval queues, delegations, kill switch,          │
│  sprint leadership, action rights                    │
├─────────────────────────────────────────────────────┤
│  Treasury                                            │
│  Balance, runway, reserve floor, spend tracking,     │
│  revenue summary, budget enforcement                 │
├─────────────────────────────────────────────────────┤
│  Court                                               │
│  Violations, sanctions, appeals, auto-review,        │
│  severity-based enforcement (CLAUDE.md §9)           │
├─────────────────────────────────────────────────────┤
│  Runtime (OpenClaw)                                  │
│  Agent execution, sessions, channels, tools,         │
│  Docker sandbox, cron/scheduling                     │
└─────────────────────────────────────────────────────┘
```

## Composition Pattern

The five primitives **compose over** the existing economy layer — they import and extend, never rewrite.

| Economy Module | Platform Primitive | What's Composed |
|---------------|-------------------|-----------------|
| `economy/authority.py` | `meridian_platform/authority.py` | `check_rights()`, `get_sprint_lead()`, `BLOCK_MATRIX` |
| `economy/sanctions.py` | `meridian_platform/court.py` | `apply_sanction()`, `lift_sanction()`, `check_auto_sanctions()`, `get_restrictions()` |
| `economy/revenue.py` | `meridian_platform/treasury.py` | `load_revenue()`, `load_ledger()` |
| `economy/ledger.json` | `meridian_platform/treasury.py`, `agent_registry.py` | Treasury section, agent scores |
| `meridian_platform/metering.py` | `meridian_platform/treasury.py` | `get_spend()`, `budget_check()` |
| `meridian_platform/audit.py` | All primitives | `log_event()` |

## Data Model

### Institution (organizations.py)
```
Institution {
  id: string
  name: string
  slug: string
  owner_id: string
  members: Member[]
  plan: string
  status: string
  charter: string                    # Founding purpose / constitutional text
  policy_defaults: {                 # Default policies for agents
    max_budget_per_agent_usd: float
    require_approval_above_usd: float
    auto_sanctions_enabled: bool
    auth_decay_per_epoch: int
  }
  treasury_id: string               # Pointer to treasury state
  lifecycle_state: founding|active|suspended|dissolved
  settings: object
}
```

### Agent (agent_registry.py)
```
Agent {
  id: string
  org_id: string
  name: string
  purpose: string
  role: string
  model_policy: object
  scopes: string[]
  budget: object
  approval_required: bool
  rollout_state: string
  sla: object
  reputation_units: int
  authority_units: int
  sponsor_id: string                 # Who sponsored creation
  risk_state: nominal|elevated|critical|suspended
  lifecycle_state: provisioned|active|quarantined|decommissioned
  economy_key: string                # Key in economy/ledger.json
  incident_count: int                # Running count of court incidents
  escalation_path: string[]          # Ordered escalation chain
}
```

### Authority (authority.py)
```
AuthorityQueue {
  pending_approvals: { id: Approval }
  delegations: { id: Delegation }
  kill_switch: { engaged, engaged_by, engaged_at, reason }
}

Approval { id, requester_agent_id, action, resource, cost_usd, status, decided_by }
Delegation { id, from_agent_id, to_agent_id, scopes, expires_at }
```

### Treasury (treasury.py)
Read facade — no state file. Reads from:
- `economy/ledger.json` (balance, revenue, capital)
- `economy/revenue.json` (orders, receivables)
- `meridian_platform/metering.jsonl` (spend)

### Court (court.py)
```
CourtRecords {
  violations: { id: Violation }
  appeals: { id: Appeal }
}

Violation { id, agent_id, org_id, type, severity(1-6), evidence, policy_ref, sanction_applied, status }
Appeal { id, violation_id, agent_id, grounds, status, decided_by }
```

Severity-to-sanction mapping (CLAUDE.md §9):
- 1-2: No sanction (light failure)
- 3: probation (rejected output)
- 4: lead_ban (rework creation)
- 5: zero_authority (false confidence)
- 6: remediation_only (critical failure)

## State File Locations

| File | Owner | Purpose |
|------|-------|---------|
| `meridian_platform/organizations.json` | Institution | Org/institution state |
| `meridian_platform/agent_registry.json` | Agent | Agent registry |
| `meridian_platform/authority_queue.json` | Authority | Approvals, delegations, kill switch |
| `meridian_platform/court_records.json` | Court | Violations, appeals |
| `meridian_platform/audit_log.jsonl` | Audit | Event stream (append-only) |
| `meridian_platform/metering.jsonl` | Metering | Usage meters (append-only) |
| `economy/ledger.json` | Economy | 3-ledger (REP/AUTH/CASH) |
| `economy/revenue.json` | Economy | Orders, clients, receivables |
| `economy/transactions.jsonl` | Economy | Transaction log |

## Workflow Verticals

### Competitive Intelligence (live — first managed vertical)
- Daily competitor monitoring (30+ sources)
- Cited intelligence briefs
- Watchlist-driven alerts
- Battlecard generation
- Pipeline: research -> extract -> write -> QA -> deliver -> score -> court-review

### Research-on-Demand (live, via MCP)
- Topic-driven research with sourced findings
- Delegated to Atlas agent

## Current State (2026-03-19)

### What works:
- Five constitutional primitives (Institution, Agent, Authority, Treasury, Court)
- Night-shift pipeline producing daily intelligence briefs
- MCP server with 5 paid tools + x402 payment gating
- 3-ledger economy (REP/AUTH/CASH) with auto-scoring
- Court auto-review wired into scoring pipeline
- Authority checks wired into MCP tool calls
- Treasury budget enforcement on paid operations
- Subscription management (trial + paid plans)
- Telegram delivery (bot + channel)
- Public web surface (landing, demo, pilot pages)

### Hard numbers:
- Treasury: $2.00 (owner capital, not customer revenue)
- Customer revenue: $0.00
- External paying customers: 0
- Active trials: 2 (both owner-controlled internal tests)
- Agents: 7 (Leviathann + 6 staff)
- Epochs completed: 7

## Principles

1. Five primitives govern everything — Institution, Agent, Authority, Treasury, Court
2. Compose over economy — import and extend, never rewrite
3. Every action is auditable — who did what, when, with what authority
4. Organizations own resources — no global shared state
5. Metering is core product, not internal tooling
6. Trust before marketplace — control primitives before ecosystem
7. Revenue before tokenization — prove the money path first
8. Public surface must be truthful — no claim outruns repo reality
