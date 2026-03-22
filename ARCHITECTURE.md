# Meridian Architecture — Constitutional Operating System for Autonomous Institutions

## What Meridian Is

Meridian is a constitutional operating system for running AI agents as managed, governed digital labor. It is built on exactly six primitives: **Institution, Agent, Authority, Treasury, Court, Commitment**.

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

## Six Primitives

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
│  Commitment                                          │
│  Capsule-backed obligations, lifecycle transitions,  │
│  and federation delivery references                 │
├─────────────────────────────────────────────────────┤
│  Runtime (OpenClaw)                                  │
│  Agent execution, sessions, channels, tools,         │
│  Docker sandbox, cron/scheduling                     │
└─────────────────────────────────────────────────────┘
```

## Composition Pattern

The six primitives **compose over** the existing economy layer — they import and extend, never rewrite.

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
  treasury_id: string               # Current treasury pointer until treasury registry cutover
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

On the live host today, treasury resolves through founding-institution capsule
aliases backed by the live ledger and revenue state. Authority and court state
have already moved behind the founding institution capsule boundary.

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

### Warrants (warrants.py)
```
Warrant {
  warrant_id: string
  institution_id: string
  boundary_name: string
  action_class: routine_internal|budget_spend|payout_execution|cross_institution_commitment|sanction_execution|federated_execution
  risk_class: low|moderate|high|critical
  actor_id: string
  session_id: string
  request_hash: string
  court_review_state: auto_issued|pending_review|approved|stayed|revoked
  execution_state: ready|executed
  expires_at: timestamp
  execution_refs: object
}
```

Today the live warrant surface is still founding-org-only, but it is already a
first-class capsule-backed record instead of an ad hoc field on delivery
payloads.

### Commitments (commitments.py)
```
Commitment {
  commitment_id: string
  institution_id: string
  target_host_id: string
  target_institution_id: string
  summary: string
  note: string
  status: proposed|accepted|rejected|breached|settled
  proposed_by: string
  accepted_by: string
  rejected_by: string
  breached_by: string
  settled_by: string
  delivery_refs: object[]
}
```

The live commitment surface is founding-only and capsule-backed. When a
`commitment_id` is supplied to federation send, it must resolve to the target
host and institution before delivery, and successful sends append a local
sender-side delivery reference back to the commitment record. Live federation
is still disabled on the host today, so this is not yet cross-host execution
proof.

### Cases (cases.py)
```
Case {
  case_id: string
  institution_id: string
  target_host_id: string
  target_institution_id: string
  claim_type: non_delivery|fraudulent_proof|breach_of_commitment|invalid_settlement_notice|misrouted_execution
  linked_commitment_id: string
  linked_warrant_id: string
  status: open|stayed|resolved
  opened_by: string
  reviewed_by: string
}
```

The live case surface is founding-only and capsule-backed. Commitment breach
can auto-open a linked local case record, but live federation remains
disabled, so these are local court-network records rather than live cross-host
dispute execution proof. Blocking commitment IDs / peer host IDs are surfaced
for runtime truth, while any peer-suspension path stays fail-closed until live
federation is actually enabled. Contradictory delivery proofs can still be
classified into local case records, but that remains a founding-workspace
mirror until live federation is truly enabled.

## State File Locations

| File | Owner | Purpose |
|------|-------|---------|
| `meridian_platform/organizations.json` | Institution | Org/institution state |
| `meridian_platform/agent_registry.json` | Agent | Agent registry |
| `economy/capsules/<org_id>/authority_queue.json` | Authority | Approvals, delegations, kill switch |
| `economy/capsules/<org_id>/court_records.json` | Court | Violations, appeals |
| `economy/capsules/<org_id>/commitments.json` | Commitment | Proposed/accepted/breached/settled obligations |
| `economy/capsules/<org_id>/cases.json` | Court-network | Local inter-institution case records |
| `economy/capsules/<org_id>/ledger.json` | Treasury pointer | Live treasury alias to current ledger state |
| `economy/capsules/<org_id>/revenue.json` | Treasury pointer | Live treasury alias to current revenue state |
| `meridian_platform/audit_log.jsonl` | Audit | Event stream (append-only) |
| `meridian_platform/metering.jsonl` | Metering | Usage meters (append-only) |
| `economy/ledger.json` | Economy | 3-ledger (REP/AUTH/CASH) |
| `economy/revenue.json` | Economy | Orders, clients, receivables |
| `economy/transactions.jsonl` | Economy | Transaction log |

Legacy `meridian_platform/authority_queue.json` and
`meridian_platform/court_records.json` are now migration inputs for the founding
institution, not the live source of truth.

## Workflow Verticals

### Competitive Intelligence (current proving vertical)
- Cited intelligence workflow for AI/product teams
- Watchlist-driven research, writing, QA, and scoring
- Battlecard-style snapshots and research-on-demand outputs
- Delivery promise currently narrowed to founder-led manual pilot
- Workflow shape: research -> extract -> write -> QA -> deliver -> score -> court-review

### Research-on-Demand (live, via MCP)
- Topic-driven research with sourced findings
- Delegated to Atlas agent

## Current State (2026-03-21)

### What works:
- Six constitutional primitives (Institution, Agent, Authority, Treasury, Court, Commitment)
- Healthy runtime on the live host
- Governed CI workflow logic and reference pipeline
- MCP server with 5 paid tools + x402 payment gating for the founding Meridian service
- 3-ledger economy (REP/AUTH/CASH) with auto-scoring
- Court auto-review wired into scoring pipeline
- Authority checks wired into MCP tool calls
- Treasury budget enforcement on paid operations
- Public web surface (landing, demo, pilot, support pages)
- Founder-led manual pilot path
- Support path separated from customer revenue in doctrine and public surfaces
- Founding-org authority and court state moved behind capsule-backed paths
- Founding-org warrant state exposed through `/api/warrants` and reflected in `/api/status`
- Founding-org commitment state exposed through `/api/commitments` and reflected in `/api/status`
- Live boundary registry now surfaces warrant requirements for `federation_gateway`
- Sender-side federated `execution_request` delivery path now requires an executable warrant in code, even though live federation remains disabled

### What is intentionally not claimed as live:
- Automated subscriber delivery for external customers
- Broad self-serve trial or paid subscription flow
- Institution-scoped subscription storage or MCP session routing
- Telegram bot/channel as the honest default customer path
- Multi-institution isolation with zero founding-org shared state in the live system
- Active cross-host federation execution on the live host
- Treasury registries fully cut over from founding ledger pointers into capsule-owned state

The owner-facing workspace API is process-bound to the founding Meridian
institution. `/api/context` reports that bound context, and request-level
`org_id` or `X-Meridian-Org-Id` hints are only accepted on exact match. This is
an explicit single-org boundary, not live multi-institution routing. When
workspace credentials carry an explicit `org_id` scope, startup rejects any
mismatch with the founding Meridian institution. If credentials also carry a
`user_id`, workspace mutations resolve the actor through the founding org
membership and enforce role-based mutation guards on top of Basic auth.
`/api/context` now returns the effective mutation permission snapshot for that
bound actor. `/api/context` and `/api/status` now also surface `runtime_core`,
which exposes:
- the bound institution context
- the serving host identity
- the current boundary identity model
- the live boundary registry (`workspace`, `federation_gateway`, `mcp_service`, `payment_monitor`, `subscriptions`, `accounting`, `cli`)
- the live admission model
- the live federation-gateway state

That admission model is explicitly `single_institution_deployment`. The live
workspace is institution-bound, but this deployment does not admit additional
institutions beyond the founding Meridian org. The live federation gateway is
also explicit now: the boundary exists in surfaced state, but this deployment
keeps it disabled until a real peer transport, signing secret, and trusted
peer registry are configured.
`/api/admission` now exposes that founding-only admission state directly, and
the matching `POST /api/admission/admit|suspend|revoke` routes fail closed with
an explicit `founding_locked` rejection instead of implying shared admission.

### Hard numbers:
- Treasury: $2.00 (owner capital, not customer revenue)
- Customer revenue: $0.00
- External paying customers: 0
- Deliverable targets today: 0
- Internal test targets today: 2
- Latest brief available for delivery: none

## Principles

1. Five primitives govern everything — Institution, Agent, Authority, Treasury, Court
2. Compose over economy — import and extend, never rewrite
3. Every action is auditable — who did what, when, with what authority
4. Institutions should own their own resources; remaining founding-org shared state is technical debt, not doctrine
5. Metering is core product, not internal tooling
6. Trust before marketplace — control primitives before ecosystem
7. Revenue before tokenization — prove the money path first
8. Public surface must be truthful — no claim outruns repo reality
