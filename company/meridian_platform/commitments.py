#!/usr/bin/env python3
"""
Founding-service commitment primitives for live Meridian.

This mirrors the kernel-side first-class commitment object, but does not claim
that live already supports broad multi-institution execution. It does ensure
that when live code references `commitment_id`, it resolves to a real
capsule-backed record rather than an inert field.
"""
from __future__ import annotations

import datetime
import hashlib
import json
import os
import uuid

from capsule import capsule_path


COMMITMENT_STATES = (
    'proposed',
    'accepted',
    'rejected',
    'breached',
    'settled',
)


def _default_org_id():
    return 'org_48b05c21'


def _now():
    return datetime.datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ')


def _payload_hash(payload):
    if payload is None:
        return ''
    if isinstance(payload, bytes):
        raw = payload
    elif isinstance(payload, str):
        raw = payload.encode('utf-8')
    else:
        raw = json.dumps(payload, sort_keys=True, separators=(',', ':')).encode('utf-8')
    return hashlib.sha256(raw).hexdigest()


def _store_path(org_id=None):
    return capsule_path(org_id or _default_org_id(), 'commitments.json')


def _empty_store():
    return {
        'commitments': {},
        'updatedAt': _now(),
        'states': list(COMMITMENT_STATES),
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


def _propose_commitment_record(org_id, target_host_id, target_org_id, commitment_type,
                               actor_id, *, commitment_id=None, terms_payload=None,
                               warrant_id='', note='', metadata=None):
    target_host_id = (target_host_id or '').strip()
    target_org_id = (target_org_id or '').strip()
    commitment_type = (commitment_type or '').strip()
    actor_id = (actor_id or '').strip()
    if not target_host_id:
        raise ValueError('target_host_id is required')
    if not target_org_id:
        raise ValueError('target_org_id is required')
    if not commitment_type:
        raise ValueError('commitment_type is required')
    if not actor_id:
        raise ValueError('actor_id is required')

    commitment_id = (commitment_id or '').strip() or f'cmt_{uuid.uuid4().hex[:12]}'
    record = {
        'commitment_id': commitment_id,
        'source_institution_id': org_id,
        'target_host_id': target_host_id,
        'target_institution_id': target_org_id,
        'commitment_type': commitment_type,
        'summary': commitment_type,
        'terms_hash': _payload_hash(terms_payload),
        'terms_payload': terms_payload if terms_payload is not None else {},
        'warrant_id': (warrant_id or '').strip(),
        'state': 'proposed',
        'status': 'proposed',
        'proposed_by': actor_id,
        'proposed_at': _now(),
        'reviewed_by': '',
        'reviewed_at': '',
        'review_note': '',
        'accepted_by': '',
        'accepted_at': '',
        'rejected_by': '',
        'rejected_at': '',
        'breached_by': '',
        'settled_by': '',
        'delivery_refs': [],
        'last_delivery_at': '',
        'breached_at': '',
        'settled_at': '',
        'note': note or '',
        'metadata': dict(metadata or {}),
    }
    store = _load_store(org_id)
    store.setdefault('commitments', {})[commitment_id] = record
    _save_store(store, org_id)
    return record


def propose_commitment(*args, **kwargs):
    if len(args) >= 5:
        org_id, target_host_id, target_org_id, commitment_type, actor_id = args[:5]
        return _propose_commitment_record(
            org_id or _default_org_id(),
            target_host_id,
            target_org_id,
            commitment_type,
            actor_id,
            terms_payload=kwargs.get('terms_payload'),
            warrant_id=kwargs.get('warrant_id', ''),
            note=kwargs.get('note', ''),
            commitment_id=kwargs.get('commitment_id'),
            metadata=kwargs.get('metadata'),
        )
    if len(args) >= 3:
        target_host_id, target_org_id, summary = args[:3]
        return _propose_commitment_record(
            kwargs.get('org_id') or _default_org_id(),
            target_host_id,
            target_org_id,
            summary,
            kwargs.get('proposed_by') or kwargs.get('actor_id') or 'owner',
            terms_payload=kwargs.get('terms_payload'),
            warrant_id=kwargs.get('warrant_id', ''),
            note=kwargs.get('note', ''),
            commitment_id=kwargs.get('commitment_id'),
            metadata=kwargs.get('metadata'),
        )
    raise TypeError('Unsupported propose_commitment call signature')


def list_commitments(org_id=None, *, state=None):
    store = _load_store(org_id)
    commitments = list(store.get('commitments', {}).values())
    if state:
        commitments = [row for row in commitments if row.get('state') == state]
    commitments.sort(key=lambda row: row.get('proposed_at', ''), reverse=True)
    return commitments


def review_commitment(commitment_id, decision, by, *, org_id=None, note=''):
    decision = (decision or '').strip()
    state_map = {
        'accept': 'accepted',
        'reject': 'rejected',
        'breach': 'breached',
        'settle': 'settled',
    }
    if decision not in state_map:
        raise ValueError(f'Unsupported commitment decision: {decision}')
    store = _load_store(org_id)
    record = store.get('commitments', {}).get(commitment_id)
    if not record:
        raise ValueError(f'Commitment not found: {commitment_id}')
    state = state_map[decision]
    record['state'] = state
    record['status'] = state
    record['reviewed_by'] = (by or '').strip()
    record['reviewed_at'] = _now()
    record['review_note'] = note or ''
    if state == 'accepted':
        record['accepted_by'] = record['reviewed_by']
        record['accepted_at'] = record['reviewed_at']
    if state == 'rejected':
        record['rejected_by'] = record['reviewed_by']
        record['rejected_at'] = record['reviewed_at']
    if state == 'breached':
        record['breached_by'] = record['reviewed_by']
        record['breached_at'] = record['reviewed_at']
    if state == 'settled':
        record['settled_by'] = record['reviewed_by']
        record['settled_at'] = record['reviewed_at']
    _save_store(store, org_id)
    return record


def accept_commitment(commitment_id, by, *, org_id=None, note=''):
    return review_commitment(commitment_id, 'accept', by, org_id=org_id, note=note)


def reject_commitment(commitment_id, by, *, org_id=None, note=''):
    return review_commitment(commitment_id, 'reject', by, org_id=org_id, note=note)


def breach_commitment(commitment_id, by, *, org_id=None, note=''):
    return review_commitment(commitment_id, 'breach', by, org_id=org_id, note=note)


def settle_commitment(commitment_id, by, *, org_id=None, note=''):
    return review_commitment(commitment_id, 'settle', by, org_id=org_id, note=note)


def validate_commitment_for_federation(commitment_id, *, org_id=None,
                                       target_host_id='', target_org_id='',
                                       warrant_id=''):
    record = next(
        (item for item in list_commitments(org_id) if item.get('commitment_id') == commitment_id),
        None,
    )
    if not record:
        raise PermissionError(f"Commitment '{commitment_id}' does not exist")
    if record.get('state') != 'accepted':
        raise PermissionError(
            f"Commitment '{commitment_id}' is not active for federation "
            f"(state={record.get('state', '')})"
        )
    if target_host_id and record.get('target_host_id') != target_host_id:
        raise PermissionError(
            f"Commitment '{commitment_id}' target_host_id "
            f"{record.get('target_host_id', '')!r} does not match {target_host_id!r}"
        )
    if target_org_id and record.get('target_institution_id') != target_org_id:
        raise PermissionError(
            f"Commitment '{commitment_id}' target_institution_id "
            f"{record.get('target_institution_id', '')!r} does not match {target_org_id!r}"
        )
    if warrant_id and record.get('warrant_id') and record.get('warrant_id') != warrant_id:
        raise PermissionError(
            f"Commitment '{commitment_id}' warrant_id "
            f"{record.get('warrant_id', '')!r} does not match {warrant_id!r}"
        )
    return record


def validate_commitment_for_delivery(commitment_id, *, target_host_id='',
                                     target_institution_id='', org_id=None,
                                     warrant_id=''):
    try:
        return validate_commitment_for_federation(
            commitment_id,
            org_id=org_id,
            target_host_id=target_host_id,
            target_org_id=target_institution_id,
            warrant_id=warrant_id,
        )
    except PermissionError as exc:
        raise ValueError(str(exc))


def mark_commitment_delivery(commitment_id, *, org_id=None, delivery_ref=None):
    store = _load_store(org_id)
    record = store.get('commitments', {}).get(commitment_id)
    if not record:
        raise ValueError(f'Commitment not found: {commitment_id}')
    refs = list(record.get('delivery_refs', []))
    refs.append(dict(delivery_ref or {}))
    record['delivery_refs'] = refs
    record['last_delivery_at'] = _now()
    _save_store(store, org_id)
    return record


def record_delivery_ref(commitment_id, delivery_ref, *, org_id=None):
    return mark_commitment_delivery(
        commitment_id,
        org_id=org_id,
        delivery_ref=delivery_ref,
    )


def commitment_summary(org_id=None):
    records = list_commitments(org_id)
    summary = {
        'total': len(records),
        'proposed': 0,
        'accepted': 0,
        'rejected': 0,
        'breached': 0,
        'settled': 0,
        'delivery_refs_total': 0,
    }
    for record in records:
        state = record.get('state') or record.get('status') or ''
        if state in summary:
            summary[state] += 1
        summary['delivery_refs_total'] += len(record.get('delivery_refs', []))
    return summary
