#!/usr/bin/env python3
"""Read-only service-state snapshots for founding-only live services."""
import datetime
import json
import os

from capsule import subscriptions_path, owner_ledger_path


PLATFORM_DIR = os.path.dirname(os.path.abspath(__file__))
WORKSPACE = os.path.dirname(os.path.dirname(PLATFORM_DIR))
LEGACY_SUBSCRIPTIONS_FILE = os.path.join(WORKSPACE, 'company', 'subscriptions.json')
LEGACY_OWNER_LEDGER_FILE = os.path.join(WORKSPACE, 'company', 'owner_ledger.json')


def _now():
    return datetime.datetime.utcnow()


def _load_json(path, default):
    if not os.path.exists(path):
        return default
    with open(path) as f:
        return json.load(f)


def _default_subscriptions(org_id=None):
    return {
        'subscribers': {},
        'delivery_log': [],
        'updatedAt': '',
        '_meta': {
            'service_scope': 'founding_meridian_service',
            'bound_org_id': org_id or '',
            'internal_test_ids': [],
        },
    }


def _default_owner(org_id=None):
    return {
        'version': 1,
        'owner': 'Son Nguyen The',
        'created_at': '',
        'capital_contributed_usd': 0.0,
        'expenses_paid_usd': 0.0,
        'reimbursements_received_usd': 0.0,
        'draws_taken_usd': 0.0,
        'entries': [],
        '_meta': {
            'service_scope': 'founding_meridian_service',
            'bound_org_id': org_id or '',
        },
    }


def load_subscription_state(org_id=None):
    payload = dict(_default_subscriptions(org_id))
    payload.update(_load_json(subscriptions_path(org_id), payload))
    payload.setdefault('subscribers', {})
    payload.setdefault('delivery_log', [])
    payload.setdefault('_meta', {})
    payload['_meta'].setdefault('service_scope', 'founding_meridian_service')
    payload['_meta'].setdefault('bound_org_id', org_id or '')
    payload['_meta'].setdefault('internal_test_ids', [])
    return payload


def load_owner_ledger_state(org_id=None):
    payload = dict(_default_owner(org_id))
    payload.update(_load_json(owner_ledger_path(org_id), payload))
    payload.setdefault('entries', [])
    payload.setdefault('_meta', {})
    payload['_meta'].setdefault('service_scope', 'founding_meridian_service')
    payload['_meta'].setdefault('bound_org_id', org_id or '')
    return payload


def subscription_snapshot(org_id=None):
    payload = load_subscription_state(org_id)
    internal_ids = set(payload.get('_meta', {}).get('internal_test_ids', []))

    active_subscriptions = 0
    active_paid = 0
    external_targets = set()
    now = _now()
    for telegram_id, records in payload.get('subscribers', {}).items():
        for record in records:
            if record.get('status') != 'active':
                continue
            expires_at = (record.get('expires_at') or '').strip()
            if expires_at:
                expires = datetime.datetime.strptime(expires_at, '%Y-%m-%dT%H:%M:%SZ')
                if expires < now:
                    continue
            active_subscriptions += 1
            if record.get('plan') != 'trial' and record.get('payment_verified'):
                active_paid += 1
            if str(telegram_id) not in internal_ids:
                external_targets.add(str(telegram_id))

    return {
        'bound_org_id': payload.get('_meta', {}).get('bound_org_id', org_id or ''),
        'management_mode': 'founding_service_operator',
        'mutation_enabled': False,
        'mutation_disabled_reason': 'managed_via_founding_service_cli',
        'storage_model': 'capsule_canonical_with_legacy_symlink',
        'boundary_name': 'subscriptions',
        'identity_model': 'none',
        'canonical_path': os.path.relpath(subscriptions_path(org_id), WORKSPACE),
        'legacy_path': os.path.relpath(LEGACY_SUBSCRIPTIONS_FILE, WORKSPACE),
        'summary': {
            'subscriber_count': len(payload.get('subscribers', {})),
            'active_subscription_count': active_subscriptions,
            'verified_paid_subscription_count': active_paid,
            'delivery_log_count': len(payload.get('delivery_log', [])),
            'internal_test_id_count': len(internal_ids),
            'external_target_count': len(external_targets),
        },
        'meta': payload.get('_meta', {}),
        'subscribers': payload.get('subscribers', {}),
        'delivery_log_tail': payload.get('delivery_log', [])[-20:],
    }


def accounting_snapshot(org_id=None):
    payload = load_owner_ledger_state(org_id)
    expenses_paid = float(payload.get('expenses_paid_usd', 0.0) or 0.0)
    reimbursements = float(payload.get('reimbursements_received_usd', 0.0) or 0.0)
    draws_taken = float(payload.get('draws_taken_usd', 0.0) or 0.0)
    capital = float(payload.get('capital_contributed_usd', 0.0) or 0.0)

    return {
        'bound_org_id': payload.get('_meta', {}).get('bound_org_id', org_id or ''),
        'management_mode': 'workspace_service_api',
        'mutation_enabled': True,
        'mutation_disabled_reason': '',
        'storage_model': 'capsule_canonical_with_legacy_symlink',
        'boundary_name': 'accounting',
        'identity_model': 'session',
        'canonical_path': os.path.relpath(owner_ledger_path(org_id), WORKSPACE),
        'legacy_path': os.path.relpath(LEGACY_OWNER_LEDGER_FILE, WORKSPACE),
        'mutation_paths': [
            '/api/accounting/expense',
            '/api/accounting/reimburse',
            '/api/accounting/draw',
        ],
        'summary': {
            'capital_contributed_usd': capital,
            'expenses_paid_usd': expenses_paid,
            'reimbursements_received_usd': reimbursements,
            'draws_taken_usd': draws_taken,
            'unreimbursed_expenses_usd': round(expenses_paid - reimbursements, 2),
            'entry_count': len(payload.get('entries', [])),
        },
        'meta': payload.get('_meta', {}),
        'owner': payload.get('owner', ''),
        'entries_tail': payload.get('entries', [])[-20:],
    }
