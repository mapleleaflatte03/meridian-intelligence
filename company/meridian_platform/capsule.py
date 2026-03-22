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
LEGACY_SUBSCRIPTIONS_FILE = os.path.join(WORKSPACE, 'company', 'subscriptions.json')
LEGACY_SUBSCRIPTIONS_BACKUP_FILE = os.path.join(WORKSPACE, 'company', 'subscriptions.json.bak')
LEGACY_SUBSCRIPTIONS_LOCK_FILE = os.path.join(WORKSPACE, 'company', '.subscriptions.lock')
LEGACY_OWNER_LEDGER_FILE = os.path.join(WORKSPACE, 'company', 'owner_ledger.json')
LEGACY_PAYMENT_MONITOR_STATE_FILE = os.path.join(WORKSPACE, 'company', 'payment_monitor_state.json')
LEGACY_PAYMENT_EVENTS_LOG_FILE = os.path.join(WORKSPACE, 'company', 'payment_events.log')
LEGACY_PAYMENT_INTEGRITY_LOCK_FILE = os.path.join(WORKSPACE, 'economy', '.payment_integrity.lock')


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


def _write_json(path, payload):
    with open(path, 'w') as f:
        json.dump(payload, f, indent=2)


def _merge_revenue_state(current, target_data):
    """Heal a split-brain revenue alias in the single-org live runtime.

    This is intentionally narrow: only the founding revenue state is merged,
    because it is the one live treasury path that may already have diverged
    between a regular capsule file and the legacy target during earlier
    cutover work.
    """
    if not isinstance(current, dict) or not isinstance(target_data, dict):
        return None

    current_ts = current.get('updatedAt', '') or ''
    target_ts = target_data.get('updatedAt', '') or ''
    primary, secondary = (
        (current, target_data) if current_ts >= target_ts else (target_data, current)
    )

    merged = dict(secondary)
    merged.update(primary)
    merged['clients'] = {
        **target_data.get('clients', {}),
        **current.get('clients', {}),
    }
    merged['orders'] = {
        **target_data.get('orders', {}),
        **current.get('orders', {}),
    }
    merged['receivables_usd'] = max(
        float(target_data.get('receivables_usd', 0.0) or 0.0),
        float(current.get('receivables_usd', 0.0) or 0.0),
    )
    merged['updatedAt'] = max(current_ts, target_ts)
    return merged


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
            merged = None
            if os.path.basename(path) == 'revenue.json':
                merged = _merge_revenue_state(current, target_data)
            if merged is None:
                raise ValueError(
                    f'Capsule alias collision at {path}: existing file diverges from {target}'
                )
            _write_json(target, merged)
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


def _ensure_default_json(path, payload):
    if os.path.exists(path):
        return
    _write_json(path, payload)


def _ensure_default_text(path, content=''):
    if os.path.exists(path):
        return
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(path, 'w') as f:
        f.write(content)


def ensure_subscription_aliases(org_id=None):
    resolved_org_id = resolve_org_id(org_id)
    _ensure_default_json(
        LEGACY_SUBSCRIPTIONS_FILE,
        {
            'subscribers': {},
            'delivery_log': [],
            'updatedAt': '',
            '_meta': {'service_scope': 'founding_meridian_service'},
        },
    )
    _ensure_default_json(
        LEGACY_SUBSCRIPTIONS_BACKUP_FILE,
        {
            'subscribers': {},
            'delivery_log': [],
            'updatedAt': '',
            '_meta': {'service_scope': 'founding_meridian_service'},
        },
    )
    _ensure_default_text(LEGACY_SUBSCRIPTIONS_LOCK_FILE, '')

    subscriptions_alias = capsule_path(resolved_org_id, 'subscriptions.json')
    subscriptions_backup_alias = capsule_path(resolved_org_id, 'subscriptions.json.bak')
    subscriptions_lock_alias = capsule_path(resolved_org_id, '.subscriptions.lock')
    _ensure_alias(subscriptions_alias, LEGACY_SUBSCRIPTIONS_FILE)
    _ensure_alias(subscriptions_backup_alias, LEGACY_SUBSCRIPTIONS_BACKUP_FILE)
    _ensure_alias(subscriptions_lock_alias, LEGACY_SUBSCRIPTIONS_LOCK_FILE)
    return {
        'subscriptions': subscriptions_alias,
        'subscriptions_backup': subscriptions_backup_alias,
        'subscriptions_lock': subscriptions_lock_alias,
    }


def ensure_accounting_aliases(org_id=None):
    resolved_org_id = resolve_org_id(org_id)
    _ensure_default_json(LEGACY_OWNER_LEDGER_FILE, {})
    owner_ledger_alias = capsule_path(resolved_org_id, 'owner_ledger.json')
    _ensure_alias(owner_ledger_alias, LEGACY_OWNER_LEDGER_FILE)
    return {
        'owner_ledger': owner_ledger_alias,
    }


def ensure_payment_monitor_aliases(org_id=None):
    resolved_org_id = resolve_org_id(org_id)
    _ensure_default_json(LEGACY_PAYMENT_MONITOR_STATE_FILE, {'last_block': 0})
    _ensure_default_text(LEGACY_PAYMENT_EVENTS_LOG_FILE, '')
    state_alias = capsule_path(resolved_org_id, 'payment_monitor_state.json')
    events_alias = capsule_path(resolved_org_id, 'payment_events.log')
    _ensure_alias(state_alias, LEGACY_PAYMENT_MONITOR_STATE_FILE)
    _ensure_alias(events_alias, LEGACY_PAYMENT_EVENTS_LOG_FILE)
    return {
        'payment_monitor_state': state_alias,
        'payment_events_log': events_alias,
    }


def ensure_revenue_integrity_aliases(org_id=None):
    resolved_org_id = resolve_org_id(org_id)
    _ensure_default_text(LEGACY_PAYMENT_INTEGRITY_LOCK_FILE, '')
    integrity_alias = capsule_path(resolved_org_id, '.payment_integrity.lock')
    _ensure_alias(integrity_alias, LEGACY_PAYMENT_INTEGRITY_LOCK_FILE)
    return {
        'payment_integrity_lock': integrity_alias,
    }


def ledger_path(org_id=None):
    return ensure_treasury_aliases(org_id)['ledger']


def revenue_path(org_id=None):
    return ensure_treasury_aliases(org_id)['revenue']


def transactions_path(org_id=None):
    return ensure_treasury_aliases(org_id)['transactions']


def subscriptions_path(org_id=None):
    return ensure_subscription_aliases(org_id)['subscriptions']


def subscriptions_backup_path(org_id=None):
    return ensure_subscription_aliases(org_id)['subscriptions_backup']


def subscriptions_lock_path(org_id=None):
    return ensure_subscription_aliases(org_id)['subscriptions_lock']


def owner_ledger_path(org_id=None):
    return ensure_accounting_aliases(org_id)['owner_ledger']


def payment_monitor_state_path(org_id=None):
    return ensure_payment_monitor_aliases(org_id)['payment_monitor_state']


def payment_events_log_path(org_id=None):
    return ensure_payment_monitor_aliases(org_id)['payment_events_log']


def payment_integrity_lock_path(org_id=None):
    return ensure_revenue_integrity_aliases(org_id)['payment_integrity_lock']
