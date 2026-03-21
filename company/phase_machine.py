#!/usr/bin/env python3
"""
Phase Machine for the Meridian institution.

Wraps kernel/phase_machine.py for the live Meridian deployment.
Reads from economy/ (the Meridian capsule) and adds deployment-specific
context like known internal test IDs.

Usage:
  python3 phase_machine.py              # Show current phase
  python3 phase_machine.py --json       # JSON output for pipeline integration
"""
import argparse
import json
import os
import sys

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_WORKSPACE = os.path.dirname(_THIS_DIR)
ECONOMY_DIR = os.path.join(_WORKSPACE, 'economy')

# -- Known internal test Telegram IDs (must never count as external traction) --
INTERNAL_TEST_IDS = frozenset({
    '6114408283',
    '1053016694',
})

# -- Direct state readers (no kernel import dependency) -----------------------

def _load_json(path):
    if not os.path.exists(path):
        return {}
    with open(path) as f:
        return json.load(f)


def _load_ledger():
    return _load_json(os.path.join(ECONOMY_DIR, 'ledger.json'))


def _load_revenue():
    return _load_json(os.path.join(ECONOMY_DIR, 'revenue.json'))


# -- Phase checks (mirrors kernel/phase_machine.py with deployment context) ---

PHASES = {
    0: 'Founder-Backed Build',
    1: 'Support-Backed Build',
    2: 'Manual Pilot',
    3: 'Customer-Backed Treasury',
    4: 'Treasury-Cleared Automation',
    5: 'Surplus-Backed Contributor Payouts',
    6: 'Inter-Institution Commitments',
}


def evaluate():
    """Evaluate Meridian's current phase. Returns (phase_num, details)."""
    ledger = _load_ledger()
    revenue = _load_revenue()
    t = ledger.get('treasury', {})
    checks = []

    # Phase 0: founder-backed build starts as soon as the ledger exists.
    epoch = ledger.get('epoch', {})
    epoch_num = epoch.get('number', epoch) if isinstance(epoch, dict) else epoch
    p0 = bool(ledger)
    checks.append({'phase': 0, 'met': p0,
                    'reason': f'Ledger exists (epoch {epoch_num})' if p0 else 'No ledger found'})

    # Phase 1: support_received_usd > 0
    support = t.get('support_received_usd', 0)
    p1 = p0 and support > 0
    checks.append({'phase': 1, 'met': p1,
                    'reason': f'Support: ${support:.2f}' if p1 else 'No support recorded'})

    # Phase 2: at least 1 real customer order paid
    orders = revenue.get('orders', {})
    real_paid = []
    for oid, order in orders.items():
        if order.get('status') != 'paid':
            continue
        cid = order.get('client_id', '')
        if cid in INTERNAL_TEST_IDS:
            continue
        if order.get('product') in ('owner-capital-contribution',):
            continue
        real_paid.append(order)
    p2 = p1 and len(real_paid) >= 1
    checks.append({'phase': 2, 'met': p2,
                    'reason': f'{len(real_paid)} real paid orders' if p2 else f'{len(real_paid)} real paid orders (need >= 1)'})

    # Phase 3: revenue > owner capital, >= 3 payments, >= 2 clients
    total_rev = t.get('total_revenue_usd', 0)
    owner_cap = t.get('owner_capital_contributed_usd', 0)
    client_ids = set(o.get('client_id', '') for o in real_paid) - INTERNAL_TEST_IDS
    p3 = p2 and total_rev > owner_cap and len(real_paid) >= 3 and len(client_ids) >= 2
    p3_reason = f'Rev ${total_rev:.2f} vs cap ${owner_cap:.2f}, {len(real_paid)} orders, {len(client_ids)} clients'
    checks.append({'phase': 3, 'met': p3, 'reason': p3_reason})

    # Phase 4: cash >= reserve floor
    cash = t.get('cash_usd', 0)
    floor = t.get('reserve_floor_usd', 50)
    p4 = p3 and cash >= floor
    checks.append({'phase': 4, 'met': p4,
                    'reason': f'Cash ${cash:.2f} vs floor ${floor:.2f}'})

    # Phase 5: surplus for payouts
    p5 = p4 and cash > floor
    checks.append({'phase': 5, 'met': p5,
                    'reason': f'Surplus ${cash - floor:.2f}' if p5 else 'No surplus'})

    # Phase 6: inter-institution (needs multi-capsule)
    checks.append({'phase': 6, 'met': False,
                    'reason': 'Requires another institution on same kernel'})

    # Find highest passing phase
    highest = -1
    for c in checks:
        if c['met']:
            highest = c['phase']
        else:
            break

    # Next unlock
    next_unlock = None
    for c in checks:
        if not c['met']:
            next_unlock = c['reason']
            break

    return highest, {
        'phase': highest,
        'name': PHASES.get(highest, 'Pre-Foundation'),
        'next_phase': highest + 1 if highest < 6 else None,
        'next_phase_name': PHASES.get(highest + 1) if highest < 6 else None,
        'next_unlock': next_unlock,
        'treasury': {
            'cash_usd': t.get('cash_usd', 0),
            'reserve_floor_usd': t.get('reserve_floor_usd', 50),
            'total_revenue_usd': total_rev,
            'support_received_usd': support,
            'owner_capital_usd': owner_cap,
        },
        'checks': checks,
    }


def main():
    parser = argparse.ArgumentParser(description='Meridian Phase Machine')
    parser.add_argument('--json', action='store_true', help='JSON output')
    args = parser.parse_args()

    phase_num, details = evaluate()

    if args.json:
        print(json.dumps(details, indent=2))
        return

    print(f"\n=== Meridian Phase Machine ===")
    print(f"Current Phase: {phase_num} — {details['name']}")
    if details['next_phase'] is not None:
        print(f"Next Phase:    {details['next_phase']} — {details['next_phase_name']}")
        print(f"Unlock needs:  {details['next_unlock']}")
    print()
    for c in details['checks']:
        status = 'PASS' if c['met'] else 'FAIL'
        print(f"  Phase {c['phase']} ({PHASES[c['phase']]}): {status} — {c['reason']}")
    print()
    t = details['treasury']
    print(f"  Treasury: ${t['cash_usd']:.2f} cash, ${t['reserve_floor_usd']:.2f} floor, ${t['total_revenue_usd']:.2f} revenue, ${t['support_received_usd']:.2f} support, ${t['owner_capital_usd']:.2f} capital")


if __name__ == '__main__':
    main()
