# Meridian — Competitor Intelligence for Product Teams

> Track what your competitors ship, price, and launch. Daily cited alerts, weekly briefs, and battlecard-ready output for Product Marketing and Competitive Intelligence teams.

[![MCP Server](https://img.shields.io/badge/MCP-Server-blue)](https://app.welliam.codes/sse)
[![x402 Payments](https://img.shields.io/badge/Payments-x402%20USDC-green)](https://x402.org)
[![Base L2](https://img.shields.io/badge/Chain-Base%20L2-blue)](https://base.org)

## What is Meridian?

Meridian is a **competitor intelligence service** for B2B software and AI-native companies. It monitors 30+ sources nightly — provider blogs, pricing pages, changelogs, tech news — and delivers cited competitive intelligence to your team every morning.

**Who it's for:** Product Marketing Managers, Competitive Intelligence leads, Product Managers, and Sales Enablement teams who need to know what competitors are doing without manual research overhead.

**What you get:**
- **Daily competitor alerts** — cited findings on pricing changes, product launches, API updates, deprecations
- **Weekly intelligence briefs** — curated top competitive moves with action items
- **Battlecards on demand** — structured competitor snapshots for sales enablement or exec briefings
- **Competitor watchlists** — track specific companies; Meridian monitors their sources automatically

**Live service:** https://app.welliam.codes
**Product demo:** https://app.welliam.codes/demo.html
**Paid pilot offer:** https://app.welliam.codes/pilot.html

### Free 7-Day Trial

No payment required. DM [@eggsama_bot](https://t.me/eggsama_bot?start=trial) on Telegram with the word **trial** — daily competitor alerts start immediately.

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
| `intelligence_latest_brief` | **$0.50 USDC** | Daily competitor intelligence alert with cited findings |
| `intelligence_on_demand_research` | **$2.00 USDC** | On-demand competitive research on any company or topic |
| `intelligence_competitor_snapshot` | **$3.00 USDC** | Battlecard-ready competitor snapshot: recent moves, pricing, product, talking points |
| `intelligence_qa_verify` | **$1.00 USDC** | QA verification of competitive claims or intelligence text |
| `intelligence_weekly_digest` | **$1.50 USDC** | Weekly competitive digest across tracked competitors |
| `company_info` | **FREE** | Meridian capabilities and pricing |

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

## Nightly Pipeline

Runs nightly (22:00–06:10 ICT). Produces competitor intelligence alerts:

1. **Research** — Fetch 30+ sources (provider blogs, changelogs, pricing pages, tech aggregators). Watchlist competitors get priority.
2. **Extract** — 8-12 sourced findings with relevance scoring and deduplication
3. **Write** — 400+ word competitor intelligence alert, each finding framed as competitive intelligence with source citation
4. **QA** — Multi-agent verification: source freshness, citation accuracy, minimum quality bar (200+ words, 5+ sources)
5. **Deliver** — Approved alert sent to all active subscribers via Telegram by 06:00 ICT
6. **Score** — Economy auto-scores agents (REP/AUTH deltas based on output quality)

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
