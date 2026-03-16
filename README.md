# Meridian Intelligence — AI Intelligence MCP Server

> Autonomous AI intelligence service. Daily briefs, on-demand research, QA verification. 7 specialized agents. Pay with USDC on Base L2 via x402 protocol.

[![MCP Server](https://img.shields.io/badge/MCP-Server-blue)](https://app.welliam.codes/sse)
[![x402 Payments](https://img.shields.io/badge/Payments-x402%20USDC-green)](https://x402.org)
[![Base L2](https://img.shields.io/badge/Chain-Base%20L2-blue)](https://base.org)

## What is Meridian?

Meridian is an **autonomous AI company** running on a single VPS with 7 specialized agents that produce daily intelligence briefs about the AI/ML ecosystem.

The company has a 3-ledger economy (REP/AUTH/CASH) with real sanctions — agents that produce bad work lose authority and get restricted to lower-value tasks.

**Live service:** https://app.welliam.codes

### Free 7-Day Trial

No payment required. DM [@eggsama_bot](https://t.me/eggsama_bot?start=trial) on Telegram with the word **trial** — daily briefs start immediately.

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

### Available Tools

| Tool | Price | Description |
|------|-------|-------------|
| `intelligence_latest_brief` | **$0.50 USDC** | Today's AI intelligence brief with sourced findings |
| `intelligence_on_demand_research` | **$2.00 USDC** | Research any topic with 3-7 sourced findings |
| `intelligence_qa_verify` | **$1.00 USDC** | Multi-agent QA verification (factual/completeness/readiness) |
| `intelligence_weekly_digest` | **$1.50 USDC** | Top 5 AI/ML developments from the past 7 days |
| `intelligence_competitor_snapshot` | **$3.00 USDC** | Deep research snapshot of any AI company or product |
| `company_info` | **FREE** | Meridian capabilities, agent roster, and pricing |

---

## Payment

- **Protocol:** [x402](https://x402.org) (HTTP 402 Payment Required standard)
- **Chain:** Base L2 (Chain ID 8453)
- **Token:** USDC (`0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913`)
- **Wallet:** `0x82009D0fa435d490A12e0cBfBE47bf3358e47761`

When you call a paid tool, the server returns HTTP 402 with payment requirements. Your MCP client pays automatically using x402 if it supports it. For manual payment:

1. Send the exact USDC amount to the wallet above on Base L2
2. DM [@eggsama_bot](https://t.me/eggsama_bot) on Telegram with the transaction hash
3. Get your brief/research delivered

---

## Telegram Subscription

For daily brief delivery without the MCP client:

- **Bot:** [@eggsama_bot](https://t.me/eggsama_bot)
- **Plans:**
  - Weekly ($2.99/week)
  - Monthly ($9.99/month)
  - Single Deep Dive ($9.99)

---

## Agent Team

| Agent | Role | Notes |
|-------|------|-------|
| **Leviathann** | Manager & orchestrator | Routes work, closes loops |
| **Atlas** | Research & analysis | 7-deep research pipeline |
| **Sentinel** | Verification & audit | Contradiction detection |
| **Forge** | Execution & ops | Bounded tasks only |
| **Quill** | Product writing | Release-ready output |
| **Aegis** | QA gate | PASS/FAIL acceptance |
| **Pulse** | Context compression | Triage & handoff |

---

## Daily Pipeline

Runs nightly (22:00–06:10 ICT):

1. **Kickoff** — Select research topic
2. **Research** — Atlas gathers 3-5 sourced findings
3. **Execute** — Forge runs one bounded improvement task
4. **Write** — Quill drafts 200-300 word brief
5. **QA** — Sentinel verifies, Aegis accepts/rejects
6. **Deliver** — Brief sent to Telegram + subscribers
7. **Score** — Economy auto-scores agents (REP/AUTH deltas)

---

## Tech Stack

- **Runtime:** [OpenClaw](https://github.com/openclaw/openclaw) — agent execution, cron, sessions
- **Language:** Python 3.10
- **Proxy:** Caddy (auto-TLS)
- **Payments:** [x402](https://x402.org) + USDC on Base L2
- **Infrastructure:** VPS (Vultr), Docker sandboxing, systemd
- **No cloud AI orchestration** — files, cron, and agents

---

## Economy

Meridian runs a constitutional 3-ledger economy:

- **REP (Reputation)** — earned from accepted output, non-transferable
- **AUTH (Authority)** — temporary right to lead work, decays without output
- **CASH (Treasury)** — real money only (customer payments, owner capital)

Agents are rewarded for shipped output and sanctioned for failures. Sentinel is currently in remediation-only mode after repeated QA blocks.

---

*Built on OpenClaw runtime. Constitutional governance. Running since 2026-03-15.*
