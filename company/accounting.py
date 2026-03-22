#!/usr/bin/env python3
"""
Owner money flow accounting.
Separates owner capital from company treasury cash.

Institution scope:
  Founding-service-only module.  Ledger reads/writes go through capsule
  aliases (ensure_treasury_aliases, capsule_ledger_path) which resolve to
  economy/ledger.json for the founding institution.  Transaction appends
  also resolve through the founding institution's capsule alias, which
  points at the canonical economy/transactions.jsonl file.  Owner-ledger
  state is now canonical in the founding institution capsule, while the
  legacy `company/owner_ledger.json` path is retained only as a compatibility
  symlink.  This module does not support multi-institution operation and is
  not part of the OSS kernel.

Usage:
  python3 accounting.py contribute --amount <USD> --note "..."
  python3 accounting.py expense    --amount <USD> --note "..."   # records owner-paid expense
  python3 accounting.py reimburse  --amount <USD> --note "..."   # draws from treasury to owner
  python3 accounting.py draw       --amount <USD> --note "..."   # profit draw (respects reserve floor)
  python3 accounting.py show
"""
import json, sys, os, argparse, datetime

COMPANY_DIR      = os.path.dirname(os.path.abspath(__file__))
ECONOMY_DIR      = os.path.join(COMPANY_DIR, '..', 'economy')
MERIDIAN_PLATFORM_DIR = os.path.join(COMPANY_DIR, 'meridian_platform')
OWNER_LEDGER     = os.path.join(COMPANY_DIR, 'owner_ledger.json')
DEFAULT_OWNER_LEDGER = OWNER_LEDGER
if MERIDIAN_PLATFORM_DIR not in sys.path:
    sys.path.insert(0, MERIDIAN_PLATFORM_DIR)

import capsule
from capsule import ensure_treasury_aliases, ledger_path as capsule_ledger_path
from capsule import transactions_path as capsule_transactions_path
from capsule import ensure_accounting_aliases, owner_ledger_path as capsule_owner_ledger_path

def now_ts():
    return datetime.datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ')

def load_json(path, default=None):
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return default if default is not None else {}

def save_json(path, data):
    with open(path, 'w') as f:
        json.dump(data, f, indent=2)


def _default_owner():
    return {
        'version': 1,
        'owner': 'Son Nguyen The',
        'created_at': now_ts(),
        'capital_contributed_usd': 0.0,
        'expenses_paid_usd': 0.0,
        'reimbursements_received_usd': 0.0,
        'draws_taken_usd': 0.0,
        'entries': [],
        '_meta': {
            'service_scope': 'founding_meridian_service',
            'bound_org_id': capsule.default_org_id() or '',
        },
    }


def _normalize_owner(data):
    if not isinstance(data, dict):
        return _default_owner()

    payload = dict(data)
    defaults = _default_owner()
    for key, value in defaults.items():
        if key == '_meta':
            continue
        payload.setdefault(key, value)

    payload.setdefault('_meta', {})
    payload['_meta']['service_scope'] = 'founding_meridian_service'
    payload['_meta']['bound_org_id'] = capsule.default_org_id() or ''
    return payload


def owner_ledger_path():
    if OWNER_LEDGER != DEFAULT_OWNER_LEDGER:
        return OWNER_LEDGER
    ensure_accounting_aliases()
    return capsule_owner_ledger_path()

def load_ledger():
    ensure_treasury_aliases()
    with open(capsule_ledger_path()) as f:
        return json.load(f)

def save_ledger(data):
    data['updatedAt'] = now_ts()
    ensure_treasury_aliases()
    with open(capsule_ledger_path(), 'w') as f:
        json.dump(data, f, indent=2)

def append_tx(entry):
    entry['ts'] = now_ts()
    ensure_treasury_aliases()
    with open(capsule_transactions_path(), 'a') as f:
        f.write(json.dumps(entry) + '\n')

def load_owner():
    return _normalize_owner(load_json(owner_ledger_path()))

# ── reusable helpers ─────────────────────────────────────────────────────────

def contribute_capital(amount_usd, note='', actor='owner'):
    """Record an owner capital contribution and deposit it into treasury."""
    amount = float(amount_usd)
    if amount <= 0:
        raise ValueError('Capital contribution must be greater than 0')

    owner = load_owner()
    ledger = load_ledger()

    owner['capital_contributed_usd'] += amount
    owner['entries'].append({
        'type': 'capital_contribution',
        'amount_usd': amount,
        'note': note,
        'by': actor,
        'at': now_ts(),
    })
    save_json(owner_ledger_path(), owner)

    t = ledger['treasury']
    t['cash_usd'] += amount
    t['owner_capital_contributed_usd'] += amount
    save_ledger(ledger)

    append_tx({
        'type': 'treasury_deposit',
        'deposit_type': 'owner_capital',
        'amount_usd': amount,
        'cash_after': t['cash_usd'],
        'note': note,
        'by': actor,
    })
    return {
        'amount_usd': amount,
        'cash_after_usd': t['cash_usd'],
        'reserve_floor_usd': t['reserve_floor_usd'],
    }


def record_owner_expense(amount_usd, note='', actor='owner'):
    """Record an owner-paid expense without mutating treasury cash."""
    amount = float(amount_usd)
    if amount <= 0:
        raise ValueError('Expense amount must be greater than 0')

    owner = load_owner()
    owner['expenses_paid_usd'] += amount
    owner['entries'].append({
        'type': 'owner_expense',
        'amount_usd': amount,
        'note': note,
        'by': actor,
        'at': now_ts(),
    })
    save_json(owner_ledger_path(), owner)
    append_tx({
        'type': 'owner_expense_recorded',
        'amount_usd': amount,
        'note': note,
        'by': actor,
    })
    unreimbursed = owner['expenses_paid_usd'] - owner['reimbursements_received_usd']
    return {
        'amount_usd': amount,
        'unreimbursed_expenses_usd': round(unreimbursed, 2),
    }


def reimburse_owner(amount_usd, note='', actor='owner'):
    """Draw from treasury to reimburse a previously recorded owner expense."""
    amount = float(amount_usd)
    if amount <= 0:
        raise ValueError('Reimbursement amount must be greater than 0')

    owner = load_owner()
    ledger = load_ledger()
    t = ledger['treasury']

    unreimbursed = owner['expenses_paid_usd'] - owner['reimbursements_received_usd']
    if amount > unreimbursed:
        raise ValueError(
            f'Reimbursement ${amount:.2f} exceeds unreimbursed expenses ${unreimbursed:.2f}'
        )
    if t['cash_usd'] - amount < t['reserve_floor_usd']:
        raise PermissionError(
            f"Reimbursement ${amount:.2f} would breach reserve floor ${t['reserve_floor_usd']:.2f}"
        )

    t['cash_usd'] -= amount
    t['owner_draws_usd'] += amount
    owner['reimbursements_received_usd'] += amount
    owner['entries'].append({
        'type': 'reimbursement',
        'amount_usd': amount,
        'note': note,
        'by': actor,
        'at': now_ts(),
    })
    save_json(owner_ledger_path(), owner)
    save_ledger(ledger)
    append_tx({
        'type': 'treasury_withdraw',
        'withdraw_type': 'owner_reimbursement',
        'amount_usd': amount,
        'cash_after': t['cash_usd'],
        'note': note,
        'by': actor,
    })
    return {
        'amount_usd': amount,
        'cash_after_usd': t['cash_usd'],
        'unreimbursed_expenses_usd': round(
            owner['expenses_paid_usd'] - owner['reimbursements_received_usd'],
            2,
        ),
    }


def take_owner_draw(amount_usd, note='', actor='owner'):
    """Take a profit draw from treasury cash while respecting the reserve floor."""
    amount = float(amount_usd)
    if amount <= 0:
        raise ValueError('Draw amount must be greater than 0')

    ledger = load_ledger()
    t = ledger['treasury']
    floor = t['reserve_floor_usd']
    available = max(0.0, t['cash_usd'] - floor)
    if amount > available:
        raise ValueError(
            f'Draw ${amount:.2f} exceeds available above floor ${available:.2f}'
        )

    owner = load_owner()
    t['cash_usd'] -= amount
    t['owner_draws_usd'] += amount
    owner['draws_taken_usd'] += amount
    owner['entries'].append({
        'type': 'owner_draw',
        'amount_usd': amount,
        'note': note,
        'by': actor,
        'at': now_ts(),
    })
    save_json(owner_ledger_path(), owner)
    save_ledger(ledger)
    append_tx({
        'type': 'treasury_withdraw',
        'withdraw_type': 'owner_draw',
        'amount_usd': amount,
        'cash_after': t['cash_usd'],
        'note': note,
        'by': actor,
    })
    return {
        'amount_usd': amount,
        'cash_after_usd': t['cash_usd'],
        'available_for_draw_usd': round(max(0.0, t['cash_usd'] - floor), 2),
    }

# ── commands ──────────────────────────────────────────────────────────────────

def cmd_contribute(args):
    """Owner contributes capital → deposits into company treasury."""
    result = contribute_capital(args.amount, args.note, actor='owner')
    print(f"Capital contribution: +${result['amount_usd']:.2f} | Treasury cash: ${result['cash_after_usd']:.2f}")

def cmd_expense(args):
    """Record owner-paid expense. Does NOT touch treasury (owner paid out-of-pocket)."""
    result = record_owner_expense(args.amount, args.note, actor='owner')
    print(
        f"Expense recorded: ${result['amount_usd']:.2f} | "
        f"Unreimbursed total: ${result['unreimbursed_expenses_usd']:.2f}"
    )

def cmd_reimburse(args):
    """Owner draws from treasury to reimburse a previous expense."""
    try:
        result = reimburse_owner(args.amount, args.note, actor='owner')
    except (ValueError, PermissionError) as exc:
        print(f'ERROR: {exc}')
        sys.exit(1)
    print(
        f"Reimbursement: -${result['amount_usd']:.2f} | "
        f"Treasury cash: ${result['cash_after_usd']:.2f}"
    )

def cmd_draw(args):
    """Owner takes a profit draw. Respects reserve floor."""
    try:
        result = take_owner_draw(args.amount, args.note, actor='owner')
    except ValueError as exc:
        print(f'ERROR: {exc}')
        sys.exit(1)
    print(
        f"Owner draw: -${result['amount_usd']:.2f} | "
        f"Treasury cash: ${result['cash_after_usd']:.2f}"
    )

def cmd_show(args):
    owner  = load_owner()
    ledger = load_ledger()
    t      = ledger['treasury']

    unreimbursed   = owner['expenses_paid_usd'] - owner['reimbursements_received_usd']
    avail_for_draw = max(0.0, t['cash_usd'] - t['reserve_floor_usd'])

    print("\n=== OWNER LEDGER (separated from company treasury) ===")
    print(f"  Capital contributed:         ${owner['capital_contributed_usd']:.2f}")
    print(f"  Expenses paid out-of-pocket: ${owner['expenses_paid_usd']:.2f}")
    print(f"  Reimbursements received:     ${owner['reimbursements_received_usd']:.2f}")
    print(f"  Draws taken:                 ${owner['draws_taken_usd']:.2f}")
    print(f"  Unreimbursed expenses:       ${unreimbursed:.2f}")
    print(f"\n=== COMPANY TREASURY ===")
    print(f"  Cash (USD):                  ${t['cash_usd']:.2f}")
    print(f"  Reserve floor:               ${t['reserve_floor_usd']:.2f}")
    print(f"  Available for draw:          ${avail_for_draw:.2f}")
    print(f"  Revenue from customers:      ${t.get('total_revenue_usd', 0):.2f}")
    print(f"  Owner capital deposited:     ${t['owner_capital_contributed_usd']:.2f}")

def main():
    p   = argparse.ArgumentParser(description='Owner money flow accounting')
    sub = p.add_subparsers(dest='command')

    for cmd, hlp in [('contribute', 'Deposit owner capital into treasury'),
                     ('expense',    'Record owner-paid expense (no treasury change)'),
                     ('reimburse',  'Draw from treasury to reimburse owner expense'),
                     ('draw',       'Take profit draw from treasury')]:
        sp = sub.add_parser(cmd, help=hlp)
        sp.add_argument('--amount', required=True)
        sp.add_argument('--note',   default=cmd)

    sub.add_parser('show')

    args = p.parse_args()
    if   args.command == 'contribute': cmd_contribute(args)
    elif args.command == 'expense':    cmd_expense(args)
    elif args.command == 'reimburse':  cmd_reimburse(args)
    elif args.command == 'draw':       cmd_draw(args)
    elif args.command == 'show':       cmd_show(args)
    else:                              p.print_help()

if __name__ == '__main__':
    main()
