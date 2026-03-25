#!/usr/bin/env python3
"""SQLite-backed storage helpers for Meridian observability streams."""

from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timezone
from typing import Any


def db_path_for_log(log_path: str) -> str:
    directory = os.path.dirname(os.path.abspath(log_path))
    return os.path.join(directory, 'observability.db')


def _connect(db_path: str) -> sqlite3.Connection:
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    conn = sqlite3.connect(db_path, timeout=5.0)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA journal_mode=WAL')
    conn.execute('PRAGMA synchronous=NORMAL')
    conn.execute('PRAGMA foreign_keys=ON')
    return conn


def _ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS audit_events (
            id TEXT PRIMARY KEY,
            timestamp TEXT NOT NULL,
            org_id TEXT,
            agent_id TEXT,
            actor_type TEXT,
            action TEXT NOT NULL,
            resource TEXT,
            outcome TEXT,
            details_json TEXT,
            policy_ref TEXT,
            session_id TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS metering_events (
            id TEXT PRIMARY KEY,
            timestamp TEXT NOT NULL,
            org_id TEXT NOT NULL,
            agent_id TEXT,
            metric TEXT NOT NULL,
            quantity REAL NOT NULL,
            unit TEXT,
            cost_usd REAL NOT NULL,
            run_id TEXT,
            details_json TEXT
        )
        """
    )
    conn.execute('CREATE INDEX IF NOT EXISTS idx_audit_org_time ON audit_events(org_id, timestamp DESC)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_meter_org_time ON metering_events(org_id, timestamp DESC)')


def _write_audit_row(conn: sqlite3.Connection, event: dict[str, Any]) -> None:
    _ensure_schema(conn)
    conn.execute(
        """
        INSERT OR REPLACE INTO audit_events (
            id, timestamp, org_id, agent_id, actor_type, action, resource,
            outcome, details_json, policy_ref, session_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            event.get('id', ''),
            event.get('timestamp', ''),
            event.get('org_id', ''),
            event.get('agent_id', ''),
            event.get('actor_type', ''),
            event.get('action', ''),
            event.get('resource', ''),
            event.get('outcome', ''),
            json.dumps(event.get('details') or {}, sort_keys=True),
            event.get('policy_ref', ''),
            event.get('session_id', ''),
        ),
    )


def _write_meter_row(conn: sqlite3.Connection, event: dict[str, Any]) -> None:
    _ensure_schema(conn)
    conn.execute(
        """
        INSERT OR REPLACE INTO metering_events (
            id, timestamp, org_id, agent_id, metric, quantity, unit,
            cost_usd, run_id, details_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            event.get('id', ''),
            event.get('timestamp', ''),
            event.get('org_id', ''),
            event.get('agent_id', ''),
            event.get('metric', ''),
            float(event.get('quantity', 0.0) or 0.0),
            event.get('unit', ''),
            float(event.get('cost_usd', 0.0) or 0.0),
            event.get('run_id', ''),
            json.dumps(event.get('details') or {}, sort_keys=True),
        ),
    )


def _row_to_event(row: sqlite3.Row, *, details_key: str) -> dict[str, Any]:
    event = dict(row)
    details = event.pop(details_key, '{}')
    try:
        event['details'] = json.loads(details) if details else {}
    except json.JSONDecodeError:
        event['details'] = {}
    return event


def write_audit_event(log_path: str, event: dict[str, Any]) -> bool:
    db_path = db_path_for_log(log_path)
    try:
        with _connect(db_path) as conn:
            _write_audit_row(conn, event)
            conn.commit()
        return True
    except sqlite3.Error:
        return False


def write_metering_event(log_path: str, event: dict[str, Any]) -> bool:
    db_path = db_path_for_log(log_path)
    try:
        with _connect(db_path) as conn:
            _write_meter_row(conn, event)
            conn.commit()
        return True
    except sqlite3.Error:
        return False


def db_status_for_log(log_path: str) -> dict[str, Any]:
    db_path = db_path_for_log(log_path)
    snapshot = {
        'path': db_path,
        'status': 'absent',
        'reason': 'sqlite observability mirror has not been initialized yet',
    }
    if not os.path.exists(db_path):
        return snapshot
    try:
        with _connect(db_path) as conn:
            _ensure_schema(conn)
            audit_count = conn.execute('SELECT COUNT(*) FROM audit_events').fetchone()[0]
            meter_count = conn.execute('SELECT COUNT(*) FROM metering_events').fetchone()[0]
        snapshot.update({
            'status': 'present',
            'reason': '',
            'size_bytes': os.path.getsize(db_path),
            'audit_events': int(audit_count or 0),
            'metering_events': int(meter_count or 0),
        })
    except sqlite3.Error as exc:
        snapshot.update({
            'status': 'degraded',
            'reason': str(exc),
        })
    return snapshot


def query_audit_events(
    log_path: str,
    *,
    org_id: str | None = None,
    agent_id: str | None = None,
    action: str | None = None,
    since: str | None = None,
    outcome: str | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    db_path = db_path_for_log(log_path)
    if os.path.exists(db_path):
        try:
            clauses = []
            params: list[Any] = []
            if org_id:
                clauses.append('org_id = ?')
                params.append(org_id)
            if agent_id:
                clauses.append('agent_id = ?')
                params.append(agent_id)
            if action:
                clauses.append('action = ?')
                params.append(action)
            if outcome:
                clauses.append('outcome = ?')
                params.append(outcome)
            if since:
                clauses.append('timestamp >= ?')
                params.append(since)
            where = f"WHERE {' AND '.join(clauses)}" if clauses else ''
            query = (
                'SELECT id, timestamp, org_id, agent_id, actor_type, action, resource, '
                'outcome, details_json, policy_ref, session_id '
                f'FROM audit_events {where} '
                'ORDER BY timestamp DESC, rowid DESC LIMIT ?'
            )
            params.append(limit)
            with _connect(db_path) as conn:
                _ensure_schema(conn)
                rows = conn.execute(query, params).fetchall()
            return [_row_to_event(row, details_key='details_json') for row in rows]
        except sqlite3.Error:
            pass
    if not os.path.exists(log_path):
        return []
    results: list[dict[str, Any]] = []
    with open(log_path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if org_id and event.get('org_id') != org_id:
                continue
            if agent_id and event.get('agent_id') != agent_id:
                continue
            if action and event.get('action') != action:
                continue
            if outcome and event.get('outcome') != outcome:
                continue
            if since and event.get('timestamp', '') < since:
                continue
            results.append(event)
    results.reverse()
    return results[:limit]


def query_metering_events(
    log_path: str,
    *,
    org_id: str,
    agent_id: str | None = None,
    since: str | None = None,
    metric: str | None = None,
) -> list[dict[str, Any]]:
    db_path = db_path_for_log(log_path)
    if os.path.exists(db_path):
        try:
            clauses = ['org_id = ?']
            params: list[Any] = [org_id]
            if agent_id:
                clauses.append('agent_id = ?')
                params.append(agent_id)
            if metric:
                clauses.append('metric = ?')
                params.append(metric)
            if since:
                clauses.append('timestamp >= ?')
                params.append(since)
            query = (
                'SELECT id, timestamp, org_id, agent_id, metric, quantity, unit, '
                'cost_usd, run_id, details_json '
                f"FROM metering_events WHERE {' AND '.join(clauses)} "
                'ORDER BY timestamp ASC, rowid ASC'
            )
            with _connect(db_path) as conn:
                _ensure_schema(conn)
                rows = conn.execute(query, params).fetchall()
            return [_row_to_event(row, details_key='details_json') for row in rows]
        except sqlite3.Error:
            pass
    if not os.path.exists(log_path):
        return []
    results: list[dict[str, Any]] = []
    with open(log_path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if event.get('org_id') != org_id:
                continue
            if agent_id and event.get('agent_id') != agent_id:
                continue
            if metric and event.get('metric') != metric:
                continue
            if since and event.get('timestamp', '') < since:
                continue
            results.append(event)
    return results


def _timestamp_to_epoch_seconds(timestamp: str) -> int | None:
    if not timestamp:
        return None
    try:
        dt_value = datetime.fromisoformat(timestamp.replace('Z', '+00:00'))
    except ValueError:
        return None
    if dt_value.tzinfo is None:
        dt_value = dt_value.replace(tzinfo=timezone.utc)
    return int(dt_value.timestamp())


def prometheus_text(*, audit_log_path: str, metering_log_path: str, org_id: str | None = None) -> str:
    audit_events = query_audit_events(audit_log_path, org_id=org_id, limit=10000)
    metering_events = query_metering_events(metering_log_path, org_id=org_id or '')
    labels = f'org_id="{org_id or ""}"'
    total_cost = sum(float(event.get('cost_usd', 0.0) or 0.0) for event in metering_events)
    lines = [
        '# HELP meridian_audit_events_total Total audit events recorded for the organization.',
        '# TYPE meridian_audit_events_total counter',
        f'meridian_audit_events_total{{{labels}}} {len(audit_events)}',
        '# HELP meridian_metering_events_total Total metering events recorded for the organization.',
        '# TYPE meridian_metering_events_total counter',
        f'meridian_metering_events_total{{{labels}}} {len(metering_events)}',
        '# HELP meridian_metering_cost_usd_total Total metering cost recorded for the organization.',
        '# TYPE meridian_metering_cost_usd_total counter',
        f'meridian_metering_cost_usd_total{{{labels}}} {total_cost:.4f}',
    ]
    if audit_events:
        audit_latest = _timestamp_to_epoch_seconds(audit_events[0].get('timestamp', ''))
        if audit_latest is not None:
            lines.extend([
                '# HELP meridian_audit_latest_timestamp_seconds Unix timestamp for the latest audit event.',
                '# TYPE meridian_audit_latest_timestamp_seconds gauge',
                f'meridian_audit_latest_timestamp_seconds{{{labels}}} {audit_latest}',
            ])
    if metering_events:
        metering_latest = _timestamp_to_epoch_seconds(metering_events[-1].get('timestamp', ''))
        if metering_latest is not None:
            lines.extend([
                '# HELP meridian_metering_latest_timestamp_seconds Unix timestamp for the latest metering event.',
                '# TYPE meridian_metering_latest_timestamp_seconds gauge',
                f'meridian_metering_latest_timestamp_seconds{{{labels}}} {metering_latest}',
            ])
    return '\n'.join(lines) + '\n'
