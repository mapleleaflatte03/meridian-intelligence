#!/usr/bin/env python3
"""
Live institution state capsule helpers.

The live system is still operationally single-org, but runtime governance state
should already live behind an institution-owned boundary instead of ad hoc files
in meridian_platform/.
"""
import json
import os


PLATFORM_DIR = os.path.dirname(os.path.abspath(__file__))
WORKSPACE = os.path.dirname(os.path.dirname(PLATFORM_DIR))
CAPSULES_DIR = os.path.join(WORKSPACE, 'economy', 'capsules')
ORGS_FILE = os.path.join(PLATFORM_DIR, 'organizations.json')
LEGACY_LEDGER_FILE = os.path.join(WORKSPACE, 'economy', 'ledger.json')
LEGACY_REVENUE_FILE = os.path.join(WORKSPACE, 'economy', 'revenue.json')
LEGACY_TRANSACTIONS_FILE = os.path.join(WORKSPACE, 'economy', 'transactions.jsonl')


def _load_orgs():
    if not os.path.exists(ORGS_FILE):
        return {}
    with open(ORGS_FILE) as f:
        return json.load(f).get('organizations', {})


def default_org_id():
    for oid, org in _load_orgs().items():
        if org.get('slug') == 'meridian':
            return oid
    return None


def resolve_org_id(org_id=None):
    founding_org_id = default_org_id()
    if org_id and founding_org_id and org_id != founding_org_id:
        raise ValueError(
            f'Live capsule only supports founding org {founding_org_id}, got {org_id}'
        )
    return org_id or founding_org_id


def capsule_dir(org_id=None):
    resolved_org_id = resolve_org_id(org_id)
    if not resolved_org_id:
        raise ValueError('Founding org is not initialized')
    return os.path.join(CAPSULES_DIR, resolved_org_id)


def ensure_capsule(org_id=None):
    target = capsule_dir(org_id)
    os.makedirs(target, exist_ok=True)
    return target


def capsule_path(org_id, filename):
    return os.path.join(capsule_dir(org_id), filename)


def _load_alias_content(path):
    if not os.path.exists(path):
        return None
    if path.endswith('.jsonl'):
        with open(path) as f:
            return f.read()
    with open(path) as f:
        return json.load(f)


def _ensure_alias(path, target):
    ensure_capsule(os.path.basename(os.path.dirname(path)))
    if os.path.islink(path):
        current = os.path.realpath(path)
        if os.path.realpath(target) != current:
            os.unlink(path)
            os.symlink(target, path)
        return path

    if os.path.exists(path):
        current = _load_alias_content(path)
        target_data = _load_alias_content(target)
        if current != target_data:
            raise ValueError(
                f'Capsule alias collision at {path}: existing file diverges from {target}'
            )
        os.unlink(path)
    os.symlink(target, path)
    return path


def ensure_treasury_aliases(org_id=None):
    resolved_org_id = resolve_org_id(org_id)
    if not os.path.exists(LEGACY_LEDGER_FILE):
        raise FileNotFoundError(f'Missing live ledger: {LEGACY_LEDGER_FILE}')
    if not os.path.exists(LEGACY_REVENUE_FILE):
        raise FileNotFoundError(f'Missing live revenue state: {LEGACY_REVENUE_FILE}')
    if not os.path.exists(LEGACY_TRANSACTIONS_FILE):
        open(LEGACY_TRANSACTIONS_FILE, 'a').close()

    ledger_alias = capsule_path(resolved_org_id, 'ledger.json')
    revenue_alias = capsule_path(resolved_org_id, 'revenue.json')
    transactions_alias = capsule_path(resolved_org_id, 'transactions.jsonl')
    _ensure_alias(ledger_alias, LEGACY_LEDGER_FILE)
    _ensure_alias(revenue_alias, LEGACY_REVENUE_FILE)
    _ensure_alias(transactions_alias, LEGACY_TRANSACTIONS_FILE)
    return {
        'ledger': ledger_alias,
        'revenue': revenue_alias,
        'transactions': transactions_alias,
    }


def ledger_path(org_id=None):
    return ensure_treasury_aliases(org_id)['ledger']


def revenue_path(org_id=None):
    return ensure_treasury_aliases(org_id)['revenue']


def transactions_path(org_id=None):
    return ensure_treasury_aliases(org_id)['transactions']
