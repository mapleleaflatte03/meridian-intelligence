#!/usr/bin/env python3
"""Workspace-facing snapshots for institution-owned live services."""
import datetime
import json
import os

from capsule import ledger_path, subscriptions_path, owner_ledger_path
import subscription_service


PLATFORM_DIR = os.path.dirname(os.path.abspath(__file__))
WORKSPACE = os.path.dirname(os.path.dirname(PLATFORM_DIR))
LEGACY_SUBSCRIPTIONS_FILE = os.path.join(WORKSPACE, 'company', 'subscriptions.json')
LEGACY_OWNER_LEDGER_FILE = os.path.join(WORKSPACE, 'company', 'owner_ledger.json')
SUBSCRIPTIONS_MUTATION_PATHS = [
    '/api/subscriptions/add',
    '/api/subscriptions/convert',
    '/api/subscriptions/verify-payment',
    '/api/subscriptions/set-email',
    '/api/subscriptions/remove',
    '/api/subscriptions/record-delivery',
]
ACCOUNTING_MUTATION_PATHS = [
    '/api/accounting/expense',
    '/api/accounting/reimburse',
    '/api/accounting/draw',
]


def _now():
    return datetime.datetime.utcnow()


def _load_json(path, default):
    if not os.path.exists(path):
        return default
    with open(path) as f:
        return json.load(f)


def _save_json(path, payload):
    with open(path, 'w') as f:
        json.dump(payload, f, indent=2)


def _default_subscriptions(org_id=None):
    return {
        'subscribers': {},
        'delivery_log': [],
        'updatedAt': '',
        '_meta': {
            'service_scope': 'institution_owned_subscription_service',
            'bound_org_id': org_id or '',
            'boundary_name': 'subscriptions',
            'identity_model': 'session',
            'storage_model': 'capsule_canonical_with_legacy_symlink',
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
            'service_scope': 'institution_owned_service',
            'bound_org_id': org_id or '',
            'boundary_name': 'accounting',
            'identity_model': 'session',
            'storage_model': 'capsule_owned_owner_ledger',
        },
    }


def load_subscription_state(org_id=None):
    payload = dict(_default_subscriptions(org_id))
    payload.update(_load_json(subscriptions_path(org_id), payload))
    payload.setdefault('subscribers', {})
    payload.setdefault('delivery_log', [])
    payload.setdefault('_meta', {})
    payload['_meta']['service_scope'] = 'institution_owned_subscription_service'
    payload['_meta']['bound_org_id'] = org_id or payload['_meta'].get('bound_org_id', '')
    payload['_meta']['boundary_name'] = 'subscriptions'
    payload['_meta']['identity_model'] = 'session'
    payload['_meta']['storage_model'] = 'capsule_canonical_with_legacy_symlink'
    payload['_meta'].setdefault('internal_test_ids', [])
    return payload


def load_owner_ledger_state(org_id=None):
    payload = dict(_default_owner(org_id))
    payload.update(_load_json(owner_ledger_path(org_id), payload))
    payload.setdefault('entries', [])
    payload.setdefault('_meta', {})
    payload['_meta']['service_scope'] = 'institution_owned_service'
    payload['_meta']['bound_org_id'] = org_id or payload['_meta'].get('bound_org_id', '')
    payload['_meta']['boundary_name'] = 'accounting'
    payload['_meta']['identity_model'] = 'session'
    payload['_meta']['storage_model'] = 'capsule_owned_owner_ledger'
    treasury = _load_json(ledger_path(org_id), {'treasury': {}}).get('treasury', {})
    treasury_owner_capital = round(float(treasury.get('owner_capital_contributed_usd', 0.0) or 0.0), 4)
    owner_capital = round(float(payload.get('capital_contributed_usd', 0.0) or 0.0), 4)
    if treasury_owner_capital > owner_capital:
        payload['capital_contributed_usd'] = treasury_owner_capital
        payload['_meta']['capital_sync_source'] = 'treasury_ledger'
        payload['_meta']['capital_sync_backfilled'] = True
        backfill_exists = any(
            entry.get('type') == 'capital_contribution_backfill'
            and round(float(entry.get('metadata', {}).get('target_owner_capital_usd', -1.0) or -1.0), 4) == treasury_owner_capital
            for entry in payload['entries']
        )
        if not backfill_exists:
            payload['entries'].append({
                'type': 'capital_contribution_backfill',
                'amount_usd': round(treasury_owner_capital - owner_capital, 4),
                'note': 'Backfilled from treasury owner_capital_contributed_usd',
                'by': 'system:service_state',
                'at': _now().strftime('%Y-%m-%dT%H:%M:%SZ'),
                'metadata': {
                    'derived_from_treasury_ledger': True,
                    'target_owner_capital_usd': treasury_owner_capital,
                },
            })
        _save_json(owner_ledger_path(org_id), payload)
    return payload


def subscription_snapshot(org_id=None):
    payload = load_subscription_state(org_id)
    meta = payload.get('_meta', {})
    summary = subscription_service.subscription_summary(org_id)

    return {
        'bound_org_id': meta.get('bound_org_id', org_id or ''),
        'management_mode': 'institution_owned_service',
        'mutation_enabled': True,
        'mutation_disabled_reason': '',
        'storage_model': meta.get('storage_model', 'capsule_canonical_with_legacy_symlink'),
        'boundary_name': meta.get('boundary_name', 'subscriptions'),
        'identity_model': meta.get('identity_model', 'session'),
        'canonical_path': os.path.relpath(subscriptions_path(org_id), WORKSPACE),
        'legacy_path': os.path.relpath(LEGACY_SUBSCRIPTIONS_FILE, WORKSPACE),
        'mutation_paths': list(SUBSCRIPTIONS_MUTATION_PATHS),
        'summary': summary,
        'meta': meta,
        'subscribers': payload.get('subscribers', {}),
        'delivery_log_tail': payload.get('delivery_log', [])[-20:],
    }


def accounting_snapshot(org_id=None):
    payload = load_owner_ledger_state(org_id)
    meta = payload.get('_meta', {})
    expenses_paid = float(payload.get('expenses_paid_usd', 0.0) or 0.0)
    reimbursements = float(payload.get('reimbursements_received_usd', 0.0) or 0.0)
    draws_taken = float(payload.get('draws_taken_usd', 0.0) or 0.0)
    capital = float(payload.get('capital_contributed_usd', 0.0) or 0.0)

    return {
        'bound_org_id': meta.get('bound_org_id', org_id or ''),
        'management_mode': 'institution_owned_service',
        'mutation_enabled': True,
        'mutation_disabled_reason': '',
        'storage_model': meta.get('storage_model', 'capsule_owned_owner_ledger'),
        'boundary_name': meta.get('boundary_name', 'accounting'),
        'identity_model': meta.get('identity_model', 'session'),
        'canonical_path': os.path.relpath(owner_ledger_path(org_id), WORKSPACE),
        'legacy_path': os.path.relpath(LEGACY_OWNER_LEDGER_FILE, WORKSPACE),
        'mutation_paths': list(ACCOUNTING_MUTATION_PATHS),
        'summary': {
            'capital_contributed_usd': capital,
            'expenses_paid_usd': expenses_paid,
            'reimbursements_received_usd': reimbursements,
            'draws_taken_usd': draws_taken,
            'unreimbursed_expenses_usd': round(expenses_paid - reimbursements, 2),
            'entry_count': len(payload.get('entries', [])),
        },
        'meta': meta,
        'owner': payload.get('owner', ''),
        'entries_tail': payload.get('entries', [])[-20:],
    }
