"""Durable SLO alert transitions and truthful optional delivery hooks."""

from __future__ import annotations

import datetime
import json
import os
import uuid
from typing import Any, Callable

import observability_store


PLATFORM_DIR = os.path.dirname(os.path.abspath(__file__))
ALERT_LOG_FILE = os.path.join(PLATFORM_DIR, 'slo_alert_log.jsonl')
ACTIVE_STATUSES = {'warning', 'breached'}
HOOK_SUCCESS_STATUSES = {'delivered', 'success', 'sent', 'ok'}


AlertHook = Callable[[dict[str, Any]], Any]


def _now() -> str:
    return datetime.datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ')


def _append_jsonl(path: str, record: dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'a') as f:
        f.write(json.dumps(record, sort_keys=True) + '\n')


def _objective_fingerprint(objective: dict[str, Any]) -> str:
    status = str(objective.get('status', 'unknown') or 'unknown')
    name = str(objective.get('name', '') or '')
    return f'{name}:{status}'


def _normalize_hook_result(result: Any) -> dict[str, Any]:
    if result is None:
        return {
            'status': 'hook_returned_none',
            'delivered': False,
            'details': {'hook_response': None},
        }
    if isinstance(result, bool):
        return {
            'status': 'delivered' if result else 'failed',
            'delivered': bool(result),
            'details': {'hook_response': result},
        }
    if isinstance(result, dict):
        status = str(result.get('status') or result.get('delivery_status') or '').strip().lower()
        if not status:
            if result.get('delivered') is True or result.get('ok') is True:
                status = 'delivered'
            else:
                status = 'reported'
        delivered = bool(result.get('delivered')) or bool(result.get('ok')) or status in HOOK_SUCCESS_STATUSES
        return {
            'status': status,
            'delivered': delivered,
            'details': {'hook_response': result},
        }
    return {
        'status': 'hook_returned_non_mapping',
        'delivered': False,
        'details': {
            'hook_response': repr(result),
            'hook_response_type': type(result).__name__,
        },
    }


def _load_current_state(org_id: str, policy_name: str, objective_name: str) -> dict[str, Any] | None:
    return observability_store.get_slo_alert_state(
        ALERT_LOG_FILE,
        org_id=org_id,
        policy_name=policy_name,
        objective=objective_name,
    )


def _persist_jsonl_record(record: dict[str, Any]) -> bool:
    try:
        _append_jsonl(ALERT_LOG_FILE, record)
        return True
    except OSError:
        return False


def _event_record(org_id: str, policy_name: str, objective: dict[str, Any], state_change: str, *, evaluated_at: str) -> dict[str, Any]:
    event_id = f'sloalert_{uuid.uuid4().hex[:12]}'
    status = str(objective.get('status', 'unknown') or 'unknown')
    message = str(objective.get('message', '') or '')
    return {
        'record_type': 'event',
        'id': event_id,
        'timestamp': _now(),
        'org_id': org_id,
        'policy_name': policy_name,
        'objective': str(objective.get('name', '') or ''),
        'status': status,
        'message': message,
        'evaluated_at': evaluated_at,
        'active': status in ACTIVE_STATUSES,
        'state_change': state_change,
        'fingerprint': _objective_fingerprint(objective),
        'details': {
            'warning_after_seconds': objective.get('warning_after_seconds'),
            'breach_after_seconds': objective.get('breach_after_seconds'),
            'warning_at_usd': objective.get('warning_at_usd'),
            'breach_at_usd': objective.get('breach_at_usd'),
            'observed_seconds': objective.get('observed_seconds'),
            'observed_usd': objective.get('observed_usd'),
            'metric': objective.get('metric'),
        },
    }


def _delivery_record(org_id: str, alert_event_id: str, hook_name: str, result: Any, *, evaluated_at: str) -> dict[str, Any]:
    normalized = _normalize_hook_result(result)
    return {
        'record_type': 'delivery',
        'id': f'sloalertdel_{uuid.uuid4().hex[:12]}',
        'timestamp': _now(),
        'org_id': org_id,
        'alert_event_id': alert_event_id,
        'hook_name': hook_name,
        'status': normalized['status'],
        'delivered': normalized['delivered'],
        'evaluated_at': evaluated_at,
        'details': normalized['details'],
    }


def record_slo_alerts(
    evaluation: dict[str, Any],
    *,
    org_id: str,
    delivery_hook: AlertHook | None = None,
    hook_name: str = '',
) -> dict[str, Any]:
    policy_name = str(evaluation.get('policy_name') or 'meridian_observability_slo_v1')
    evaluated_at = str(evaluation.get('evaluated_at') or _now())
    events: list[dict[str, Any]] = []
    deliveries: list[dict[str, Any]] = []
    changed_objectives: list[str] = []

    for objective in evaluation.get('objectives', []) or []:
        objective_name = str(objective.get('name', '') or '')
        current_status = str(objective.get('status', 'unknown') or 'unknown')
        active = current_status in ACTIVE_STATUSES
        previous_state = _load_current_state(org_id, policy_name, objective_name)
        previous_status = str((previous_state or {}).get('current_status', '') or '')
        previous_active = previous_status in ACTIVE_STATUSES
        current_fingerprint = _objective_fingerprint(objective)

        if previous_state is None:
            state_change = 'opened' if active else 'observed'
        elif previous_status == current_status:
            state_change = 'steady'
        elif previous_active and not active:
            state_change = 'resolved'
        elif not previous_active and active:
            state_change = 'opened'
        elif previous_status == 'warning' and current_status == 'breached':
            state_change = 'escalated'
        elif previous_status == 'breached' and current_status == 'warning':
            state_change = 'deescalated'
        else:
            state_change = 'updated'

        state_record = {
            'org_id': org_id,
            'policy_name': policy_name,
            'objective': objective_name,
            'current_status': current_status,
            'current_fingerprint': current_fingerprint,
            'first_seen_at': (previous_state or {}).get('first_seen_at') or evaluated_at,
            'last_seen_at': evaluated_at,
            'last_evaluated_at': evaluated_at,
            'current_event_id': (previous_state or {}).get('current_event_id') or '',
            'current_message': str(objective.get('message', '') or ''),
        }

        event_needed = state_change in {'opened', 'resolved', 'escalated', 'deescalated', 'updated'} and (previous_state is not None or active)
        event_record = None
        if event_needed and state_change not in {'steady'}:
            event_record = _event_record(org_id, policy_name, objective, state_change, evaluated_at=evaluated_at)
            state_record['current_event_id'] = event_record['id']
            events.append(event_record)
            changed_objectives.append(objective_name)
            _persist_jsonl_record(event_record)
            observability_store.write_slo_alert_event(ALERT_LOG_FILE, event_record)
        else:
            state_record['current_event_id'] = (previous_state or {}).get('current_event_id') or ''

        observability_store.upsert_slo_alert_state(ALERT_LOG_FILE, state_record)

        if event_record and active:
            if delivery_hook is None:
                delivery_result = _delivery_record(org_id, event_record['id'], hook_name or '', None, evaluated_at=evaluated_at)
                deliveries.append(delivery_result)
                _persist_jsonl_record(delivery_result)
                observability_store.write_slo_alert_delivery(ALERT_LOG_FILE, delivery_result)
            else:
                try:
                    hook_result = delivery_hook(event_record)
                except Exception as exc:  # pragma: no cover - exercised via tests
                    delivery_result = _delivery_record(
                        org_id,
                        event_record['id'],
                        hook_name or getattr(delivery_hook, '__name__', ''),
                        {'status': 'failed', 'error': str(exc)},
                        evaluated_at=evaluated_at,
                    )
                    deliveries.append(delivery_result)
                    _persist_jsonl_record(delivery_result)
                    observability_store.write_slo_alert_delivery(ALERT_LOG_FILE, delivery_result)
                else:
                    delivery_result = _delivery_record(
                        org_id,
                        event_record['id'],
                        hook_name or getattr(delivery_hook, '__name__', ''),
                        hook_result,
                        evaluated_at=evaluated_at,
                    )
                    deliveries.append(delivery_result)
                    _persist_jsonl_record(delivery_result)
                    observability_store.write_slo_alert_delivery(ALERT_LOG_FILE, delivery_result)

    active_events = [event for event in events if event.get('active')]
    resolved_events = [event for event in events if event.get('state_change') == 'resolved']
    return {
        'log_path': ALERT_LOG_FILE,
        'policy_name': policy_name,
        'evaluated_at': evaluated_at,
        'event_count': len(events),
        'delivery_count': len(deliveries),
        'active_alert_count': len(active_events),
        'resolved_alert_count': len(resolved_events),
        'changed_objectives': changed_objectives,
        'events': events,
        'deliveries': deliveries,
        'hook': {
            'configured': delivery_hook is not None,
            'name': hook_name or (getattr(delivery_hook, '__name__', '') if delivery_hook is not None else ''),
        },
    }


def alert_surface_snapshot(org_id: str, *, limit: int = 20) -> dict[str, Any]:
    events = observability_store.query_slo_alert_events(ALERT_LOG_FILE, org_id=org_id, limit=limit)
    deliveries = observability_store.query_slo_alert_deliveries(ALERT_LOG_FILE, org_id=org_id, limit=limit)
    state_rows = []
    for event in events:
        state_rows.append({
            'objective': event.get('objective', ''),
            'status': event.get('status', 'unknown'),
            'state_change': event.get('state_change', ''),
            'timestamp': event.get('timestamp', ''),
        })
    active_count = sum(1 for event in events if event.get('active'))
    return {
        'log_path': ALERT_LOG_FILE,
        'event_count': len(events),
        'delivery_count': len(deliveries),
        'active_alert_count': active_count,
        'events': events,
        'deliveries': deliveries,
        'state': state_rows,
        'db': observability_store.db_status_for_log(ALERT_LOG_FILE),
    }
