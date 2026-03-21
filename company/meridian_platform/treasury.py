#!/usr/bin/env python3
"""
Treasury primitive for Meridian Constitutional OS.

Read facade over economy/ledger.json treasury section + economy/revenue.py +
meridian_platform/metering.py. Does NOT duplicate ledger writes — reads from
authoritative sources.

Usage:
  python3 treasury.py balance
  python3 treasury.py runway
  python3 treasury.py spend [--org_id <org>] [--days 30]
  python3 treasury.py snapshot
  python3 treasury.py check-budget --agent_id <id> --cost 2.00
  python3 treasury.py contribute --amount 50.00 --note "owner top-up"
  python3 treasury.py set-reserve-floor --amount 20.00 --note "policy change"
"""
import argparse
import datetime
import json
import os
import sys

PLATFORM_DIR = os.path.dirname(os.path.abspath(__file__))
WORKSPACE = os.path.dirname(os.path.dirname(PLATFORM_DIR))
ECONOMY_DIR = os.path.join(WORKSPACE, 'economy')

# Import economy modules (avoid name collision)
import importlib.util
_spec = importlib.util.spec_from_file_location('econ_revenue', os.path.join(ECONOMY_DIR, 'revenue.py'))
_econ_revenue_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_econ_revenue_mod)
load_revenue = _econ_revenue_mod.load_revenue
load_ledger = _econ_revenue_mod.load_ledger
customer_client_ids = _econ_revenue_mod.customer_client_ids
customer_orders = _econ_revenue_mod.customer_orders

_accounting_spec = importlib.util.spec_from_file_location(
    'company_accounting', os.path.join(WORKSPACE, 'company', 'accounting.py')
)
_accounting_mod = importlib.util.module_from_spec(_accounting_spec)
_accounting_spec.loader.exec_module(_accounting_mod)
_owner_contribute_capital = _accounting_mod.contribute_capital
_update_reserve_floor = _accounting_mod.update_reserve_floor

# Import platform metering
sys.path.insert(0, PLATFORM_DIR)
from metering import get_spend, summary as metering_summary
from agent_registry import check_budget as _agent_check_budget


def _now():
    return datetime.datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ')


# ── Core functions ───────────────────────────────────────────────────────────

def get_balance():
    """Read treasury.cash_usd from ledger.json."""
    ledger = load_ledger()
    return ledger['treasury']['cash_usd']


def get_reserve_floor():
    """Read treasury.reserve_floor_usd from ledger.json."""
    ledger = load_ledger()
    return ledger['treasury'].get('reserve_floor_usd', 50.0)


def get_runway():
    """Balance minus reserve floor. Negative means below reserve."""
    return get_balance() - get_reserve_floor()


def get_revenue_summary():
    """Read revenue state from economy/revenue.py."""
    rev = load_revenue()
    ledger = load_ledger()
    t = ledger['treasury']
    orders = customer_orders(rev)
    paid = [o for o in orders.values() if o['status'] == 'paid']
    open_orders = [o for o in orders.values() if o['status'] not in ('paid', 'rejected')]
    return {
        'total_revenue_usd': t.get('total_revenue_usd', 0.0),
        'support_received_usd': t.get('support_received_usd', 0.0),
        'owner_capital_contributed_usd': t.get('owner_capital_contributed_usd', 0.0),
        'receivables_usd': rev.get('receivables_usd', 0.0),
        'clients': len(customer_client_ids(rev)),
        'paid_orders': len(paid),
        'open_orders': len(open_orders),
    }


def get_spend_summary(org_id, period_days=30):
    """Aggregate spend from metering.jsonl."""
    since = (datetime.datetime.utcnow() -
             datetime.timedelta(days=period_days)).strftime('%Y-%m-%dT%H:%M:%SZ')
    total = get_spend(org_id, since=since)
    return {
        'org_id': org_id,
        'period_days': period_days,
        'total_spend_usd': round(total, 4),
    }


def contribute_owner_capital(amount_usd, note='', by='owner'):
    """Record owner capital contribution via the accounting layer."""
    return _owner_contribute_capital(amount_usd, note, actor=by)


def set_reserve_floor_policy(amount_usd, note='', by='owner'):
    """Update reserve floor policy via the accounting layer."""
    return _update_reserve_floor(amount_usd, note, actor=by)


def check_budget(agent_id, cost_usd):
    """Check agent budget + treasury runway. Returns (allowed, reason)."""
    # Try economy_key → registry ID mapping first
    from agent_registry import get_agent_by_economy_key
    reg_agent = get_agent_by_economy_key(agent_id)
    lookup_id = reg_agent['id'] if reg_agent else agent_id

    # Check agent-level budget
    allowed, reason = _agent_check_budget(lookup_id, cost_usd)
    if not allowed:
        return False, reason
    # Then check treasury runway — negative runway blocks all spending
    runway = get_runway()
    if runway < 0:
        return False, f'Treasury below reserve floor (runway ${runway:.2f}). Recapitalize before spending.'
    if runway < cost_usd:
        return False, f'Treasury runway insufficient (${runway:.2f} available, ${cost_usd:.2f} requested)'
    return True, 'ok'


def record_expense(org_id, agent_id, amount_usd, category, description):
    """Record expense via metering + audit."""
    from metering import record as meter_record
    try:
        from audit import log_event
        log_event(org_id, agent_id, 'expense_recorded',
                  resource=category, outcome='success',
                  details={'amount_usd': amount_usd, 'description': description})
    except Exception:
        pass
    meter_record(org_id, agent_id, f'expense:{category}',
                 quantity=1, unit='transactions', cost_usd=amount_usd,
                 details={'description': description})


def can_payout(amount_usd):
    """Check if a payout is possible (balance > reserve_floor + amount)."""
    balance = get_balance()
    floor = get_reserve_floor()
    return balance >= floor + amount_usd


def treasury_snapshot():
    """Combined view: balance, revenue, spend, runway, reserve status."""
    ledger = load_ledger()
    t = ledger['treasury']
    rev_summary = get_revenue_summary()

    # Try to get default org for spend
    spend_usd = 0.0
    org_id = None
    try:
        from organizations import load_orgs
        for oid, org in load_orgs().get('organizations', {}).items():
            if org.get('slug') == 'meridian':
                org_id = oid
                break
        if org_id:
            spend_usd = get_spend_summary(org_id, 30)['total_spend_usd']
    except Exception:
        pass

    balance = t['cash_usd']
    floor = t.get('reserve_floor_usd', 50.0)
    runway = balance - floor
    shortfall = max(0.0, floor - balance)
    remediation = {
        'blocked': runway < 0,
        'shortfall_usd': shortfall,
        'recommended_owner_capital_usd': shortfall,
        'recommended_reserve_floor_usd': max(0.0, balance),
        'next_steps': [],
    }
    if shortfall > 0:
        remediation['next_steps'].append(
            f"Record at least ${shortfall:.2f} of real treasury cash (owner capital, support, or customer cash) to clear the reserve gate."
        )
        remediation['next_steps'].append(
            f"If policy truly changed, explicitly lower reserve floor from ${floor:.2f} with an auditable note."
        )
        remediation['next_steps'].append(
            "Clearing the reserve gate is not the same as automation readiness; customer-backed phase progression and preflight still govern delivery."
        )

    return {
        'balance_usd': balance,
        'reserve_floor_usd': floor,
        'runway_usd': runway,
        'shortfall_usd': shortfall,
        'above_reserve': runway >= 0,
        'total_revenue_usd': t.get('total_revenue_usd', 0.0),
        'support_received_usd': t.get('support_received_usd', 0.0),
        'owner_capital_usd': t.get('owner_capital_contributed_usd', 0.0),
        'owner_draws_usd': t.get('owner_draws_usd', 0.0),
        'receivables_usd': rev_summary['receivables_usd'],
        'spend_30d_usd': spend_usd,
        'clients': rev_summary['clients'],
        'paid_orders': rev_summary['paid_orders'],
        'remediation': remediation,
        'snapshot_at': _now(),
    }


# ── CLI ──────────────────────────────────────────────────────────────────────

def main():
    p = argparse.ArgumentParser(description='Treasury primitive — financial read facade')
    sub = p.add_subparsers(dest='command')

    sub.add_parser('balance')
    sub.add_parser('runway')

    sp = sub.add_parser('spend')
    sp.add_argument('--org_id', default=None)
    sp.add_argument('--days', type=int, default=30)

    sub.add_parser('snapshot')

    cb = sub.add_parser('check-budget')
    cb.add_argument('--agent_id', required=True)
    cb.add_argument('--cost', type=float, required=True)

    cc = sub.add_parser('contribute')
    cc.add_argument('--amount', type=float, required=True)
    cc.add_argument('--note', default='owner top-up')
    cc.add_argument('--by', default='owner')

    rf = sub.add_parser('set-reserve-floor')
    rf.add_argument('--amount', type=float, required=True)
    rf.add_argument('--note', default='reserve policy update')
    rf.add_argument('--by', default='owner')

    args = p.parse_args()

    if args.command == 'balance':
        print(f'Treasury balance: ${get_balance():.2f}')
    elif args.command == 'runway':
        runway = get_runway()
        floor = get_reserve_floor()
        status = 'ABOVE reserve' if runway >= 0 else 'BELOW reserve'
        print(f'Runway: ${runway:.2f} ({status}, floor=${floor:.2f})')
    elif args.command == 'spend':
        org_id = args.org_id
        if not org_id:
            try:
                from organizations import load_orgs
                for oid, org in load_orgs().get('organizations', {}).items():
                    if org.get('slug') == 'meridian':
                        org_id = oid
                        break
            except Exception:
                pass
        if org_id:
            s = get_spend_summary(org_id, args.days)
            print(f'Spend ({s["period_days"]}d): ${s["total_spend_usd"]:.4f}')
        else:
            print('No org found for spend query')
    elif args.command == 'snapshot':
        snap = treasury_snapshot()
        print(f"\n=== Treasury Snapshot ({snap['snapshot_at']}) ===")
        print(f"Balance:         ${snap['balance_usd']:.2f}")
        print(f"Reserve floor:   ${snap['reserve_floor_usd']:.2f}")
        print(f"Runway:          ${snap['runway_usd']:.2f} {'(OK)' if snap['above_reserve'] else '(BELOW RESERVE)'}")
        print(f"Revenue:         ${snap['total_revenue_usd']:.2f}")
        print(f"Support:         ${snap['support_received_usd']:.2f}")
        print(f"Owner capital:   ${snap['owner_capital_usd']:.2f}")
        print(f"Owner draws:     ${snap['owner_draws_usd']:.2f}")
        print(f"Receivables:     ${snap['receivables_usd']:.2f}")
        print(f"Spend (30d):     ${snap['spend_30d_usd']:.4f}")
        print(f"Clients:         {snap['clients']}")
        print(f"Paid orders:     {snap['paid_orders']}")
    elif args.command == 'check-budget':
        allowed, reason = check_budget(args.agent_id, args.cost)
        status = 'ALLOWED' if allowed else 'BLOCKED'
        print(f'{status}: {reason}')
        sys.exit(0 if allowed else 1)
    elif args.command == 'contribute':
        result = contribute_owner_capital(args.amount, args.note, args.by)
        print(f"Capital contribution recorded: +${result['amount_usd']:.2f} | cash now ${result['cash_after_usd']:.2f}")
    elif args.command == 'set-reserve-floor':
        result = set_reserve_floor_policy(args.amount, args.note, args.by)
        print(f"Reserve floor updated: ${result['old_reserve_floor_usd']:.2f} -> ${result['new_reserve_floor_usd']:.2f}")
    else:
        p.print_help()


if __name__ == '__main__':
    main()
