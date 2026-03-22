# Meridian — Constitutional Operating System for Autonomous Institutions

> Five primitives. One operating system. Governed AI agent operations.

[![MCP Server](https://img.shields.io/badge/MCP-Server-blue)](https://app.welliam.codes/sse)
[![x402 Payments](https://img.shields.io/badge/Payments-x402%20USDC-green)](https://x402.org)
[![Base L2](https://img.shields.io/badge/Chain-Base%20L2-blue)](https://base.org)

## What is Meridian?

Meridian is a constitutional operating system for running AI agents as governed digital labor. It is built on five primitives that compose over a real economy layer and an honest treasury gate.

**Competitive intelligence is the first proving vertical** — a 7-agent governed workflow for cited intelligence output. The current customer path is a founder-led manual pilot; automated delivery remains treasury-gated by policy.

Meridian itself is not the competitor-intelligence vertical. The vertical is
the first managed workflow proving the kernel in public. The kernel is the
governance layer above runtimes: institution context, authority, treasury,
court, audit, and boundary classification. Public proof today is strongest on
the built-in workspace/kernel path; broader adapter proof still requires more
runtime-specific work.

On the live host today, Meridian is still operationally single-org. Authority
and court state already live behind the founding institution capsule boundary;
treasury now resolves through founding-institution capsule aliases backed by
the live ledger/revenue state, while the remaining per-institution treasury
registry cutover is unfinished. The owner-facing workspace is process-bound to
the founding Meridian institution; `/api/context` reports that bound context
and rejects request-level org overrides that do not exactly match it. The same
endpoint now also reports whether Basic auth is simply process-bound or
explicitly credential-bound to the founding org. When credentials also carry a
`user_id`, workspace mutations are role-checked against the founding
institution membership instead of being treated as a generic Basic-auth user.
`/api/context` also exposes the effective mutation permission snapshot for the
bound actor. `/api/context` and `/api/status` now also expose `runtime_core`,
which surfaces the bound institution context, the serving host identity, the
current boundary identity model, the live boundary registry, and the admission
state for this deployment. That admission state is intentionally strict: this
live runtime remains a single-institution deployment for the founding Meridian
org, with the admitted institution list containing only that org.
`/api/admission` now exposes that founding-only admission state directly, and
the corresponding `POST /api/admission/admit|suspend|revoke` routes fail closed
with a structural rejection instead of pretending live multi-institution
admission exists. The same surface now also exposes `runtime_core.federation`: on live today that
federation gateway stays explicitly disabled unless the host is configured with
peer transport, a signing secret, and trusted peers.
The live boundary registry also declares warrant requirements for
`federation_gateway`, so this disabled host can still say honestly which
message types would require court-first execution review if federation were
enabled later.
The owner workspace now also exposes `/api/warrants` plus
`POST /api/warrants/issue|approve|stay|revoke` as founding-org-only court
surfaces. Live federation remains disabled today, but the sender-side delivery
path is already warrant-aware in code: if federated `execution_request`
delivery is ever enabled on this host, it must carry an executable warrant and
the resulting audit trail preserves `warrant_id` provenance.

**Live service:** https://app.welliam.codes
**Product demo:** https://app.welliam.codes/demo.html
**Current go-to-market mode:** founder-led manual pilot until Meridian reaches customer-backed treasury and treasury-cleared automation

Need the plain-language model behind this?
- [Meridian Doctrine](company/MERIDIAN_DOCTRINE.md)

### Five Primitives

| Primitive | Status | What it does |
|-----------|--------|-------------|
| **Institution** | Live | Charter-governed organizations with lifecycle management and policy defaults |
| **Agent** | Live | First-class managed entities with identity, scopes, budget, risk state, economy participation |
| **Authority** | Live | Approval queues, delegations, and kill switch — who can act and when |
| **Treasury** | Live | Real-money accounting — balance, runway, reserve floor, spend tracking |
| **Court** | Live | Violation records, sanctions, appeals — constitutional enforcement |

---

## Intelligence Workflow (Current Vertical)

Competitive intelligence is the first managed workflow vertical. What exists today is the workflow, the runtime, and the product surface. What is intentionally still narrow is the delivery promise.

- **Cited competitor alerts** — findings on pricing changes, launches, API updates, and deprecations
- **Curated intelligence briefs** — top competitive moves with action items
- **Battlecards on demand** — structured competitor snapshots for sales enablement
- **Competitor watchlists** — track specific companies through the governed workflow

### Current Entry Path

Start with the demo and pilot surfaces:
- Demo: https://app.welliam.codes/demo.html
- Pilot: https://app.welliam.codes/pilot.html
- Support the work: https://app.welliam.codes/support.html
- Contact: Telegram [@Enhanhsj](https://t.me/Enhanhsj) or email `nguyensimon186@gmail.com`

The honest current offer is a founder-led manual pilot. More cash in treasury only clears the reserve gate; automated delivery still waits for customer-backed phase progression, treasury-cleared automation, and constitutional preflight.
If you want to back Meridian without pretending that support equals customer delivery, use the dedicated support path instead of the pilot flow.

If you are confused about support vs pilot vs customer revenue vs future contributor payouts, read:
- [Meridian Doctrine](company/MERIDIAN_DOCTRINE.md)

---

## MCP Tools

Connect via SSE: `https://app.welliam.codes/sse`

```json
{
  "mcpServers": {
    "meridian": {
      "url": "https://app.welliam.codes/sse"
    }
  }
}
```

| Tool | Price | Description |
|------|-------|-------------|
| `intelligence_latest_brief` | **$0.50 USDC** | Daily intelligence alert with cited findings |
| `intelligence_on_demand_research` | **$2.00 USDC** | Research any topic with sourced findings |
| `intelligence_competitor_snapshot` | **$3.00 USDC** | Battlecard-ready competitor snapshot |
| `intelligence_qa_verify` | **$1.00 USDC** | QA verification of claims or text |
| `intelligence_weekly_digest` | **$1.50 USDC** | Weekly digest across tracked competitors |
| `company_info` | **FREE** | Meridian capabilities and pricing |

On the live host today, every MCP tool call is audited and metered for the
founding Meridian institution only. The shared runtime-core taxonomy classifies
that path as `mcp_service` with identity model `x402_payment` and scope
`founding_service_only`. Multi-institution MCP routing is not live.

---

## Payment

- **MCP tool calls:** [x402](https://x402.org) over USDC on Base L2
- **Pilot engagements:** manual activation currently available via bank transfer, Wise, or stablecoin while card checkout is not live
- **Support / sponsorship:** use the support page for non-customer backing of the build, infra, and open kernel
- **Chain:** Base L2 (Chain ID 8453)
- **Token:** USDC (`0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913`)
- **Wallet:** `0x82009D0fa435d490A12e0cBfBE47bf3358e47761`

---

## Managed Agent Team

Every agent is a registered entity with identity, scopes, budget, and reputation.

| Agent | Role | Purpose |
|-------|------|---------|
| **Leviathann** | Manager | Orchestrates pipeline, routes work, closes loops |
| **Atlas** | Analyst | Research, sourced findings, competitive analysis |
| **Quill** | Writer | Structured briefs, release-ready deliverables |
| **Aegis** | QA Gate | PASS/FAIL acceptance with evidence |
| **Sentinel** | Verifier | Contradiction detection, risk review |
| **Forge** | Executor | Implementation, operational steps |
| **Pulse** | Compressor | Context compression, triage |

Agents earn REP (reputation) and AUTH (authority) from accepted output. Sanctions apply for failures, fake progress, or wasted resources.

---

## Reference CI Pipeline

The CI workflow is structured as a nightly sequence. The logic is real, but public customer delivery is not being marketed as always-on hosted automation while treasury policy remains blocked.

1. **Research** — Fetch tracked sources. Watchlist competitors get priority.
2. **Extract** — Sourced findings with relevance scoring and deduplication
3. **Write** — Cited intelligence alert in structured format
4. **QA** — Multi-agent verification: source freshness, citation accuracy, quality bar
5. **Deliver** — Approved alert through the current honest path: founder-led pilot now, automated subscriber delivery when treasury policy clears
6. **Score** — Economy auto-scores agents (REP/AUTH deltas). Registry syncs.
7. **Audit** — Every step logged. Usage metered.

---

## Economy

Constitutional 3-ledger internal economy:

- **REP (Reputation)** — earned from accepted output, non-transferable
- **AUTH (Authority)** — temporary right to lead work, decays without output
- **CASH (Treasury)** — real money only (owner capital, support, customer revenue)

---

## Tech Stack

- **Runtime:** [OpenClaw](https://github.com/openclaw/openclaw) — agent execution, cron, sessions
- **Platform:** Python 3.10, JSON state files, JSONL audit/metering logs
- **Proxy:** Caddy (auto-TLS)
- **Payments:** [x402](https://x402.org) + USDC on Base L2
- **Infrastructure:** VPS (Vultr), Docker sandboxing, systemd

---

*Constitutional Operating System for Autonomous Institutions. Built on OpenClaw runtime. Running since 2026-03-15.*
