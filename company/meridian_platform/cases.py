#!/usr/bin/env python3
"""
Founding-workspace case primitives for live Meridian.

This mirrors the kernel-side inter-institution case object, but keeps the live
surface honest: cases are still local founding-workspace records, not proof
that live cross-host dispute execution is enabled.
"""
from __future__ import annotations

import datetime
import json
import os
import uuid

from capsule import capsule_path


CASE_STATES = (
    'open',
    'stayed',
    'resolved',
)

CLAIM_TYPES = (
    'non_delivery',
    'fraudulent_proof',
    'breach_of_commitment',
    'invalid_settlement_notice',
    'misrouted_execution',
)


def _default_org_id():
    return 'org_48b05c21'


def _now():
    return datetime.datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ')


def _store_path(org_id=None):
    return capsule_path(org_id or _default_org_id(), 'cases.json')


def _empty_store():
    return {
        'cases': {},
        'updatedAt': _now(),
        'states': list(CASE_STATES),
        'claim_types': list(CLAIM_TYPES),
    }


def _load_store(org_id=None):
    path = _store_path(org_id)
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return _empty_store()


def _save_store(data, org_id=None):
    data['updatedAt'] = _now()
    path = _store_path(org_id)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w') as f:
        json.dump(data, f, indent=2, sort_keys=True)


def open_case(org_id, claim_type, actor_id, *, target_host_id='',
              target_institution_id='', linked_commitment_id='',
              linked_warrant_id='', evidence_refs=None, note='',
              metadata=None):
    claim_type = (claim_type or '').strip()
    actor_id = (actor_id or '').strip()
    target_host_id = (target_host_id or '').strip()
    target_institution_id = (target_institution_id or '').strip()
    if claim_type not in CLAIM_TYPES:
        raise ValueError(f'Unknown claim_type {claim_type!r}. Must be one of {CLAIM_TYPES}')
    if not actor_id:
        raise ValueError('actor_id is required')
    timestamp = _now()
    case_id = f'case_{uuid.uuid4().hex[:12]}'
    record = {
        'case_id': case_id,
        'institution_id': org_id,
        'source_institution_id': org_id,
        'target_host_id': target_host_id,
        'target_institution_id': target_institution_id,
        'claim_type': claim_type,
        'linked_commitment_id': (linked_commitment_id or '').strip(),
        'linked_warrant_id': (linked_warrant_id or '').strip(),
        'evidence_refs': list(evidence_refs or []),
        'status': 'open',
        'opened_by': actor_id,
        'opened_at': timestamp,
        'updated_at': timestamp,
        'reviewed_by': '',
        'reviewed_at': '',
        'review_note': '',
        'resolution': '',
        'note': note or '',
        'metadata': dict(metadata or {}),
    }
    store = _load_store(org_id)
    store.setdefault('cases', {})[case_id] = record
    _save_store(store, org_id)
    return record


def list_cases(org_id=None, *, status=None, claim_type=None):
    store = _load_store(org_id)
    rows = list(store.get('cases', {}).values())
    if status:
        rows = [row for row in rows if row.get('status') == status]
    if claim_type:
        rows = [row for row in rows if row.get('claim_type') == claim_type]
    rows.sort(key=lambda row: row.get('opened_at', ''), reverse=True)
    return rows


def review_case(case_id, decision, by, *, org_id=None, note=''):
    decision = (decision or '').strip()
    state_map = {
        'stay': 'stayed',
        'resolve': 'resolved',
    }
    if decision not in state_map:
        raise ValueError(f'Unsupported case decision: {decision}')
    store = _load_store(org_id)
    record = store.get('cases', {}).get(case_id)
    if not record:
        raise ValueError(f'Case not found: {case_id}')
    timestamp = _now()
    record['status'] = state_map[decision]
    record['reviewed_by'] = (by or '').strip()
    record['reviewed_at'] = timestamp
    record['review_note'] = note or ''
    record['updated_at'] = timestamp
    if decision == 'resolve':
        record['resolution'] = note or 'resolved'
    _save_store(store, org_id)
    return record


def stay_case(case_id, by, *, org_id=None, note=''):
    return review_case(case_id, 'stay', by, org_id=org_id, note=note)


def resolve_case(case_id, by, *, org_id=None, note=''):
    return review_case(case_id, 'resolve', by, org_id=org_id, note=note)


def case_summary(org_id=None):
    rows = list_cases(org_id)
    summary = {
        'total': len(rows),
        'open': 0,
        'stayed': 0,
        'resolved': 0,
    }
    for row in rows:
        status = row.get('status', '')
        if status in summary:
            summary[status] += 1
    return summary


def ensure_case_for_commitment_breach(commitment_record, actor_id, *, org_id=None, note=''):
    commitment_id = (commitment_record or {}).get('commitment_id', '')
    if not commitment_id:
        raise ValueError('commitment_record.commitment_id is required')
    target_host_id = (commitment_record or {}).get('target_host_id', '')
    target_institution_id = (commitment_record or {}).get('target_institution_id', '')
    for existing in list_cases(org_id):
        if (
            existing.get('claim_type') == 'breach_of_commitment'
            and existing.get('linked_commitment_id') == commitment_id
            and existing.get('status') in ('open', 'stayed')
        ):
            return existing, False
    return open_case(
        org_id or _default_org_id(),
        'breach_of_commitment',
        actor_id,
        target_host_id=target_host_id,
        target_institution_id=target_institution_id,
        linked_commitment_id=commitment_id,
        note=note,
        metadata={'source': 'commitment_breach'},
    ), True
