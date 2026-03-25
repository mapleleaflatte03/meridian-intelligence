#!/usr/bin/env python3
"""File-backed persistence and observability snapshots for the live workspace."""

from __future__ import annotations

import datetime
import os

from audit import stats as audit_stats
from audit import tail_events as audit_tail_events
from capsule import capsule_path
from metering import get_usage as metering_usage
from metering import summary as metering_summary


PLATFORM_DIR = os.path.dirname(os.path.abspath(__file__))
WORKSPACE = os.path.dirname(os.path.dirname(PLATFORM_DIR))


def _utc_now():
    return datetime.datetime.utcnow()


def _iso_utc(dt_value):
    if not isinstance(dt_value, datetime.datetime):
        return ''
    return dt_value.strftime('%Y-%m-%dT%H:%M:%SZ')


def _age_seconds(dt_value):
    if not isinstance(dt_value, datetime.datetime):
        return None
    delta = _utc_now() - dt_value
    return max(int(delta.total_seconds()), 0)


def _safe_relpath(path):
    if not path:
        return ''
    try:
        return os.path.relpath(path, WORKSPACE)
    except ValueError:
        return path


def _safe_capsule_path(org_id, filename):
    try:
        return capsule_path(org_id, filename)
    except Exception:
        return ''


def _file_snapshot(path, *, kind, owner, append_only=False, role='canonical'):
    snapshot = {
        'path': _safe_relpath(path),
        'kind': kind,
        'owner': owner,
        'role': role,
        'append_only': append_only,
    }
    if not path:
        snapshot.update({
            'status': 'unresolved',
            'exists': False,
            'size_bytes': 0,
            'modified_at': '',
            'age_seconds': None,
        })
        return snapshot
    if not os.path.exists(path):
        snapshot.update({
            'status': 'missing',
            'exists': False,
            'size_bytes': 0,
            'modified_at': '',
            'age_seconds': None,
        })
        return snapshot
    stat = os.stat(path)
    modified_at = datetime.datetime.utcfromtimestamp(stat.st_mtime)
    snapshot.update({
        'status': 'present',
        'exists': True,
        'size_bytes': stat.st_size,
        'modified_at': _iso_utc(modified_at),
        'age_seconds': _age_seconds(modified_at),
    })
    return snapshot


def persistence_snapshot(org_id=None):
    """Return the concrete file-backed persistence seams for the workspace."""
    seams = [
        _file_snapshot(
            os.path.join(PLATFORM_DIR, 'organizations.json'),
            kind='json',
            owner='organizations.py',
        ),
        _file_snapshot(
            os.path.join(PLATFORM_DIR, 'agent_registry.json'),
            kind='json',
            owner='agent_registry.py',
        ),
        _file_snapshot(
            os.path.join(PLATFORM_DIR, 'authority_queue.json'),
            kind='json',
            owner='authority.py',
            role='legacy_input',
        ),
        _file_snapshot(
            os.path.join(PLATFORM_DIR, 'court_records.json'),
            kind='json',
            owner='court.py',
            role='legacy_input',
        ),
        _file_snapshot(
            os.path.join(PLATFORM_DIR, 'audit_log.jsonl'),
            kind='jsonl',
            owner='audit.py',
            append_only=True,
        ),
        _file_snapshot(
            os.path.join(PLATFORM_DIR, 'metering.jsonl'),
            kind='jsonl',
            owner='metering.py',
            append_only=True,
        ),
        _file_snapshot(
            _safe_capsule_path(org_id, 'ledger.json'),
            kind='json',
            owner='treasury.py',
        ),
        _file_snapshot(
            _safe_capsule_path(org_id, 'revenue.json'),
            kind='json',
            owner='treasury.py',
        ),
        _file_snapshot(
            _safe_capsule_path(org_id, 'transactions.jsonl'),
            kind='jsonl',
            owner='treasury.py',
            append_only=True,
        ),
        _file_snapshot(
            _safe_capsule_path(org_id, 'subscriptions.json'),
            kind='json',
            owner='subscription_service.py',
        ),
        _file_snapshot(
            _safe_capsule_path(org_id, 'owner_ledger.json'),
            kind='json',
            owner='accounting_service.py',
        ),
        _file_snapshot(
            _safe_capsule_path(org_id, 'cases.json'),
            kind='json',
            owner='cases.py',
        ),
        _file_snapshot(
            _safe_capsule_path(org_id, 'commitments.json'),
            kind='json',
            owner='commitments.py',
        ),
        _file_snapshot(
            _safe_capsule_path(org_id, 'federation_inbox.json'),
            kind='json',
            owner='federation_inbox.py',
        ),
    ]
    return {
        'backend': 'file-backed-json-jsonl',
        'db': {
            'status': 'absent',
            'reason': 'no relational database layer is deployed; workspace state is persisted through files',
        },
        'seams': seams,
    }


def observability_snapshot(org_id):
    """Return the file-backed metrics inputs and an explicit SLO status."""
    audit_summary = audit_stats(org_id)
    audit_events = audit_tail_events(1, org_id=org_id)
    audit_latest = audit_events[-1] if audit_events else {}

    metering_month = metering_summary(org_id, period='month')
    metering_events = metering_usage(org_id)
    metering_latest = metering_events[-1] if metering_events else {}

    return {
        'backend': 'file-backed-jsonl',
        'metrics': {
            'audit': {
                **audit_summary,
                'latest_at': audit_latest.get('timestamp', ''),
                'latest_action': audit_latest.get('action', ''),
                'latest_outcome': audit_latest.get('outcome', ''),
            },
            'metering': {
                **metering_month,
                'latest_at': metering_latest.get('timestamp', ''),
                'latest_metric': metering_latest.get('metric', ''),
                'latest_cost_usd': round(float(metering_latest.get('cost_usd', 0.0) or 0.0), 4),
            },
        },
        'slo': {
            'status': 'not_formalized',
            'reason': 'no dedicated SLO engine or metrics exporter exists yet',
            'signals': {
                'audit_total_events': audit_summary.get('total_events', 0),
                'audit_latest_at': audit_latest.get('timestamp', ''),
                'metering_total_events_month': metering_month.get('total_events', 0),
                'metering_latest_at': metering_latest.get('timestamp', ''),
                'metering_total_cost_usd_month': metering_month.get('total_cost_usd', 0.0),
            },
        },
    }
