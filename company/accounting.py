#!/usr/bin/env python3
"""
Owner money flow accounting.
Separates owner capital from company treasury cash.

Institution scope:
  Founding-service-only module.  Ledger reads/writes go through capsule
  aliases (ensure_treasury_aliases, capsule_ledger_path) which resolve to
  economy/ledger.json for the founding institution.  Transaction appends
  also resolve through the founding institution's capsule alias, which
  points at the canonical economy/transactions.jsonl file.  This module
  does not support multi-institution operation and is not part of the
  OSS kernel.

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
if MERIDIAN_PLATFORM_DIR not in sys.path:
    sys.path.insert(0, MERIDIAN_PLATFORM_DIR)

from capsule import ensure_treasury_aliases, ledger_path as capsule_ledger_path
from capsule import transactions_path as capsule_transactions_path

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
    data = load_json(OWNER_LEDGER)
    if not data:
        data = {
            'version': 1,
            'owner':   'Son Nguyen The',
            'created_at': now_ts(),
            'capital_contributed_usd':     0.0,
            'expenses_paid_usd':           0.0,
            'reimbursements_received_usd': 0.0,
            'draws_taken_usd':             0.0,
            'entries': [],
        }
    return data

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
    save_json(OWNER_LEDGER, owner)

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


def update_reserve_floor(new_floor_usd, note='', actor='owner'):
    """Update the treasury reserve floor as an explicit policy action."""
    amount = float(new_floor_usd)
    if amount < 0:
        raise ValueError('Reserve floor cannot be negative')

    ledger = load_ledger()
    t = ledger['treasury']
    old = float(t.get('reserve_floor_usd', 50.0))
    t['reserve_floor_usd'] = amount
    save_ledger(ledger)

    append_tx({
        'type': 'treasury_policy_update',
        'policy': 'reserve_floor_usd',
        'old_value': old,
        'new_value': amount,
        'cash_after': t['cash_usd'],
        'note': note,
        'by': actor,
    })
    return {
        'old_reserve_floor_usd': old,
        'new_reserve_floor_usd': amount,
        'cash_usd': t['cash_usd'],
    }

# ── commands ──────────────────────────────────────────────────────────────────

def cmd_contribute(args):
    """Owner contributes capital → deposits into company treasury."""
    result = contribute_capital(args.amount, args.note, actor='owner')
    print(f"Capital contribution: +${result['amount_usd']:.2f} | Treasury cash: ${result['cash_after_usd']:.2f}")

def cmd_expense(args):
    """Record owner-paid expense. Does NOT touch treasury (owner paid out-of-pocket)."""
    amount = float(args.amount)
    owner  = load_owner()
    owner['expenses_paid_usd'] += amount
    owner['entries'].append({'type': 'owner_expense', 'amount_usd': amount,
                             'note': args.note, 'at': now_ts()})
    save_json(OWNER_LEDGER, owner)
    append_tx({'type': 'owner_expense_recorded', 'amount_usd': amount, 'note': args.note})
    unreimbursed = owner['expenses_paid_usd'] - owner['reimbursements_received_usd']
    print(f"Expense recorded: ${amount:.2f} | Unreimbursed total: ${unreimbursed:.2f}")

def cmd_reimburse(args):
    """Owner draws from treasury to reimburse a previous expense."""
    amount = float(args.amount)
    owner  = load_owner()
    ledger = load_ledger()
    t      = ledger['treasury']

    unreimbursed = owner['expenses_paid_usd'] - owner['reimbursements_received_usd']
    if amount > unreimbursed:
        print(f"ERROR: reimbursement ${amount:.2f} exceeds unreimbursed expenses ${unreimbursed:.2f}")
        sys.exit(1)
    if t['cash_usd'] - amount < t['reserve_floor_usd']:
        print(f"ERROR: would breach reserve floor ${t['reserve_floor_usd']:.2f} "
              f"(cash ${t['cash_usd']:.2f})")
        sys.exit(1)

    t['cash_usd']         -= amount
    t['owner_draws_usd']  += amount
    owner['reimbursements_received_usd'] += amount
    owner['entries'].append({'type': 'reimbursement', 'amount_usd': amount,
                             'note': args.note, 'at': now_ts()})
    save_json(OWNER_LEDGER, owner)
    save_ledger(ledger)
    append_tx({'type': 'treasury_withdraw', 'withdraw_type': 'owner_reimbursement',
               'amount_usd': amount, 'cash_after': t['cash_usd'], 'note': args.note})
    print(f"Reimbursement: -${amount:.2f} | Treasury cash: ${t['cash_usd']:.2f}")

def cmd_draw(args):
    """Owner takes a profit draw. Respects reserve floor."""
    amount = float(args.amount)
    ledger = load_ledger()
    t      = ledger['treasury']
    floor  = t['reserve_floor_usd']

    available = max(0.0, t['cash_usd'] - floor)
    if amount > available:
        print(f"ERROR: draw ${amount:.2f} exceeds available above floor "
              f"(cash ${t['cash_usd']:.2f}, floor ${floor:.2f}, available ${available:.2f})")
        sys.exit(1)

    owner = load_owner()
    t['cash_usd']        -= amount
    t['owner_draws_usd'] += amount
    owner['draws_taken_usd'] += amount
    owner['entries'].append({'type': 'owner_draw', 'amount_usd': amount,
                             'note': args.note, 'at': now_ts()})
    save_json(OWNER_LEDGER, owner)
    save_ledger(ledger)
    append_tx({'type': 'treasury_withdraw', 'withdraw_type': 'owner_draw',
               'amount_usd': amount, 'cash_after': t['cash_usd'], 'note': args.note})
    print(f"Owner draw: -${amount:.2f} | Treasury cash: ${t['cash_usd']:.2f}")

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
