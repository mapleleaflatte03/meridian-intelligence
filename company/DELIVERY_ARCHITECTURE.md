# Meridian Delivery Architecture

Last updated: 2026-03-20

---

## Intended Model

The Meridian Telegram system has three distinct surfaces with distinct roles.
They are not interchangeable.

### 1. @MeridianIntelligence (channel)

**Role:** Public broadcast. Free preview. Acquisition surface.

- Anyone can follow without a Telegram account link to the bot.
- Posts an abbreviated daily brief preview (600 chars) after the night-shift pipeline.
- Includes a CTA to start a trial via @eggsama_bot.
- No subscriber interaction. Broadcast only.
- Managed by `channel_deliver.py`. Runs after `night-shift-write` + `night-shift-qa` complete.

**Who sees it:** Public. Designed to be found via Telegram search or shared links.

### 2. @eggsama_bot (subscriber bot)

**Role:** Subscriber lifecycle. Trial start. Direct delivery. Subscription management.

- Users start a 7-day free trial via `/start trial` or `t.me/eggsama_bot?start=trial`.
- Receives full daily brief as a private DM — separate from the channel preview.
- Handles: `subscribe`, `cancel`, `status` commands.
- Trial expiry reminders sent at 2 days, 1 day, and expiry day via `trial_reminder.py`.
- Paid subscribers (once any exist) receive the premium brief through this same channel.
- Managed by `premium_deliver.py` for the actual content delivery.

**Who uses it:** Trial users. Paid subscribers. Anyone who started a trial.

### 3. Owner Telegram (5322393870)

**Role:** Operator control surface. Morning brief delivery. System alerts.

- The `night-shift-deliver` cron job delivers the full morning brief directly to the owner.
- Revenue dashboard (`revenue-dashboard` cron) reports weekly to the owner.
- This is a private channel for Sơn, not a customer channel.

---

## Delivery Flow (Intended)

```
Night-shift pipeline (22:05 – 05:40 ICT nightly)
│
├── [05:40 ICT] night-shift-deliver
│     → Delivers full brief to owner Telegram (5322393870)
│     → Requires: constitutional preflight PASS
│
├── [05:50 ICT] premium-deliver
│     → Runs premium_deliver.py
│     → Delivers full brief to all active trial + paid subscribers via @eggsama_bot
│     → Requires: constitutional preflight PASS + active subscribers
│
├── [22:45 ICT] channel_deliver.py (run separately or via night-shift)
│     → Posts abbreviated preview to @MeridianIntelligence
│     → Requires: brief available + quality PASS
│     → Not in cron — must be triggered after night-shift-write completes
│
└── [09:00 ICT daily] trial_reminder.py
      → Sends expiry nudges to active trial users at days 2, 1, 0 before expiry
      → Reads subscriptions.json directly
      → No constitutional preflight required (it's a notification, not a pipeline step)
```

---

## Constitutional Preflight in Delivery

Both `channel_deliver.py` and `premium_deliver.py` call `ci_vertical.py preflight`
before any delivery attempt. If preflight returns non-zero, delivery is blocked.

Preflight blocks when:
- Treasury balance below reserve floor (currently $48.00 below floor)
- Kill switch engaged
- Agent authority blocked

This is by design. Uncontrolled spend requires treasury funding. Manual delivery
bypasses this via `--skip-preflight` flag — for operator use only during recovery.

---

## Channel vs Bot vs Premium — Summary

| Surface | Content | Audience | Interaction | Gated by |
|---------|---------|----------|-------------|----------|
| @MeridianIntelligence | 600-char brief preview | Public | None (broadcast) | Brief quality + channel_deliver.py |
| @eggsama_bot | Full daily brief | Trial + paid subscribers | Yes — commands | Constitutional preflight + active subs |
| Owner DM (5322393870) | Full morning brief | Owner only | None | Constitutional preflight |
| Premium DM | Full brief + QA metadata | Paid/trial subscribers | None | Constitutional preflight + subscriptions |

---

## Current Operational Truth (2026-03-20, updated)

See `OPERATOR_STATUS.md` for the full breakdown of what is blocked and why.

Short version:
- `deactivated_workspace` is resolved, but direct runtime checks are currently unstable:
  `openclaw health` fails with gateway 1006, and the canonical PONG probe falls
  back to embedded timeout.
- Constitutional preflight is blocked by treasury shortfall ($-48 vs reserve floor).
- No brief files exist for recent dates — pipeline has not run since treasury block.
- The two "active" trial subscriptions are owner-controlled internal tests.
- No external customers exist. No external deliveries have occurred.
- Manual delivery to the owner is possible using `--skip-preflight` once a brief exists.
- `channel_deliver.py` is NOT in the cron schedule — it must be triggered manually
  or integrated into the night-shift pipeline after `night-shift-write` completes.

**Brief source of truth:** `night-shift/brief-YYYY-MM-DD.md` files. Both
`channel_deliver.py` and `premium_deliver.py` use `get_today_brief()` which checks
today's date first, then falls back to the most recent brief file.
