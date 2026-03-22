#!/usr/bin/env python3
"""
Live-only OpenClaw deployment proof helpers.

This module intentionally stays read-only. It maps governed Meridian agent
records to live OpenClaw handles and parses `openclaw health` output into a
structured proof object for the current single-host deployment.
"""
from __future__ import annotations

import datetime as _dt
import os
import re
import subprocess
import sys
from typing import Any, Dict, Iterable, List, Mapping, Optional


PLATFORM_DIR = os.path.dirname(os.path.abspath(__file__))
WORKSPACE_DIR = os.path.dirname(PLATFORM_DIR)
REPO_ROOT = os.path.dirname(WORKSPACE_DIR)

if PLATFORM_DIR not in sys.path:
    sys.path.insert(0, PLATFORM_DIR)

from agent_registry import load_registry, normalize_agent_record  # noqa: E402


AGENT_HANDLE_FIELDS = ('economy_key', 'name', 'id')
DEFAULT_HEALTH_COMMAND = ['openclaw', 'health']
DEFAULT_PONG_COMMAND = [
    'openclaw',
    'agent',
    '--agent',
    'main',
    '--message',
    'respond with PONG',
    '--timeout',
    '15000',
]


def _now() -> str:
    return _dt.datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ')


def _slugify_handle(value: str) -> str:
    value = (value or '').strip().lower()
    if not value:
        return ''
    return re.sub(r'[^a-z0-9]+', '_', value).strip('_')


def _agent_handle(agent: Mapping[str, Any]) -> str:
    for field in AGENT_HANDLE_FIELDS:
        raw = (agent.get(field) or '').strip()
        if not raw:
            continue
        if field == 'economy_key':
            return raw
        if field == 'name':
            return _slugify_handle(raw)
        return _slugify_handle(raw)
    return ''


def map_governed_agents_to_openclaw_handles(
    agents: Optional[Iterable[Mapping[str, Any]]] = None,
) -> List[Dict[str, Any]]:
    if agents is None:
        registry = load_registry()
        agents = registry.get('agents', {}).values()

    mapped = []
    for agent in agents:
        normalized = normalize_agent_record(agent)
        handle = _agent_handle(normalized)
        mapped.append({
            'agent_id': normalized.get('id'),
            'agent_name': normalized.get('name'),
            'org_id': normalized.get('org_id'),
            'role': normalized.get('role'),
            'openclaw_handle': handle,
            'runtime_binding': dict(normalized.get('runtime_binding') or {}),
            'has_openclaw_handle': bool(handle),
            'handle_source': 'economy_key' if (normalized.get('economy_key') or '').strip() else 'name',
        })
    return mapped


_HEALTH_AGENT_RE = re.compile(r'^Agents:\s*(?P<agents>.+)$')
_HEALTH_HEARTBEAT_RE = re.compile(r'^Heartbeat interval:\s*(?P<interval>.+?)(?:\s+\((?P<primary>[^)]+)\))?$')
_HEALTH_SESSION_RE = re.compile(r'^Session store \((?P<agent>[^)]+)\):\s*(?P<path>.+?)\s+\((?P<count>\d+)\s+entries?\)$')
_HEALTH_PONG_RE = re.compile(r'^-?\s*(?P<agent>[^:]+):(?P<scope>[^:]+):(?P<session>[^ ]+)\s+\((?P<age>[^)]+)\)$')
_HEALTH_TELEGRAM_RE = re.compile(
    r'^Telegram:\s*(?P<status>[^()]+?)'
    r'(?:\s+\((?P<detail1>[^)]+)\))?'
    r'(?:\s+\((?P<detail2>[^)]+)\))?$'
)


def _split_csv_agents(raw: str) -> List[Dict[str, Any]]:
    agents = []
    for chunk in (raw or '').split(','):
        chunk = chunk.strip()
        if not chunk:
            continue
        match = re.match(r'^(?P<name>.+?)(?:\s+\((?P<role>[^)]+)\))?$', chunk)
        name = (match.group('name') if match else chunk).strip()
        role = (match.group('role') if match and match.group('role') else None)
        agents.append({
            'name': name,
            'role': role,
            'handle': _slugify_handle(name),
            'is_default': bool(role and role == 'default'),
        })
    return agents


def parse_openclaw_health(output: str) -> Dict[str, Any]:
    lines = [line.rstrip() for line in (output or '').splitlines() if line.strip()]
    proof = {
        'checked_at': _now(),
        'raw_line_count': len(lines),
        'telegram': {'status': None, 'detail': None, 'ok': False},
        'agents': [],
        'heartbeat': {'interval': None, 'primary_agent': None},
        'session_stores': [],
        'sessions': [],
        'raw': lines,
    }

    for line in lines:
        telegram = _HEALTH_TELEGRAM_RE.match(line)
        if telegram:
            status = (telegram.group('status') or '').strip()
            detail = (telegram.group('detail1') or telegram.group('detail2') or '').strip() or None
            proof['telegram'] = {
                'status': status,
                'detail': detail,
                'ok': status.lower().startswith('ok'),
            }
            continue

        agents = _HEALTH_AGENT_RE.match(line)
        if agents:
            proof['agents'] = _split_csv_agents(agents.group('agents'))
            continue

        heartbeat = _HEALTH_HEARTBEAT_RE.match(line)
        if heartbeat:
            proof['heartbeat'] = {
                'interval': (heartbeat.group('interval') or '').strip() or None,
                'primary_agent': (heartbeat.group('primary') or '').strip() or None,
            }
            continue

        session_store = _HEALTH_SESSION_RE.match(line)
        if session_store:
            proof['session_stores'].append({
                'agent': session_store.group('agent').strip(),
                'path': session_store.group('path').strip(),
                'entries': int(session_store.group('count')),
            })
            continue

        session_entry = _HEALTH_PONG_RE.match(line)
        if session_entry:
            proof['sessions'].append({
                'agent': session_entry.group('agent').strip(),
                'scope': session_entry.group('scope').strip(),
                'session_id': session_entry.group('session').strip(),
                'age': session_entry.group('age').strip(),
            })
            continue

    proof['health_ok'] = bool(proof['telegram']['ok'] and proof['agents'])
    proof['session_total'] = sum(item['entries'] for item in proof['session_stores'])
    proof['agent_count'] = len(proof['agents'])
    return proof


def _run_command(command: List[str], timeout: int) -> Dict[str, Any]:
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return {
            'command': list(command),
            'ok': False,
            'returncode': 124,
            'stdout': '',
            'stderr': f'timeout after {timeout}s',
        }
    return {
        'command': list(command),
        'ok': result.returncode == 0,
        'returncode': result.returncode,
        'stdout': (result.stdout or '').strip(),
        'stderr': (result.stderr or '').strip(),
    }


def collect_openclaw_runtime_proof(
    *,
    health_output: Optional[str] = None,
    health_command: Optional[List[str]] = None,
    agents: Optional[Iterable[Mapping[str, Any]]] = None,
    include_pong: bool = False,
    pong_command: Optional[List[str]] = None,
) -> Dict[str, Any]:
    health_probe = None
    if health_output is None:
        health_probe = _run_command(health_command or DEFAULT_HEALTH_COMMAND, timeout=20)
        health_output = health_probe['stdout']

    health = parse_openclaw_health(health_output)
    mapped_agents = map_governed_agents_to_openclaw_handles(agents=agents)
    health_handles = {agent.get('handle') for agent in health.get('agents', []) if agent.get('handle')}
    mapped_handles = {
        entry['openclaw_handle']
        for entry in mapped_agents
        if entry.get('openclaw_handle')
    }
    pong_probe = {
        'checked': False,
        'ok': False,
        'output': '',
        'stderr': '',
        'command': list(pong_command or DEFAULT_PONG_COMMAND),
    }
    if include_pong:
        probe = _run_command(pong_command or DEFAULT_PONG_COMMAND, timeout=25)
        pong_probe = {
            'checked': True,
            'ok': probe['ok'] and probe['stdout'] == 'PONG',
            'output': probe['stdout'],
            'stderr': probe['stderr'],
            'command': probe['command'],
        }

    return {
        'runtime_id': 'openclaw_compatible',
        'proof_type': 'live_single_host_openclaw_deployment',
        'checked_at': _now(),
        'health': health,
        'health_probe': health_probe or {
            'command': list(health_command or DEFAULT_HEALTH_COMMAND),
            'ok': True,
            'returncode': 0,
            'stdout': health_output,
            'stderr': '',
        },
        'pong_probe': pong_probe,
        'governed_agents': mapped_agents,
        'handle_overlap': sorted(health_handles & mapped_handles),
        'handle_gap': sorted(mapped_handles - health_handles),
        'health_output_supplied': health_output is not None,
        'deployment_truth': {
            'scope': 'single_host',
            'deployment_mode': 'live',
            'proof_level': 'read_only',
            'generic_runtime_claim': False,
        },
    }


def public_openclaw_runtime_receipt(
    proof: Mapping[str, Any],
    *,
    bound_org_id: Optional[str] = None,
) -> Dict[str, Any]:
    health = dict(proof.get('health') or {})
    governed_agents = list(proof.get('governed_agents') or [])
    return {
        'runtime_id': proof.get('runtime_id', 'openclaw_compatible'),
        'proof_type': proof.get('proof_type', 'live_single_host_openclaw_deployment'),
        'checked_at': proof.get('checked_at', _now()),
        'bound_org_id': bound_org_id,
        'deployment_truth': dict(proof.get('deployment_truth') or {}),
        'health': {
            'health_ok': bool(health.get('health_ok')),
            'telegram_ok': bool((health.get('telegram') or {}).get('ok')),
            'agent_count': health.get('agent_count', 0),
            'agent_handles': [agent.get('handle', '') for agent in health.get('agents', []) if agent.get('handle')],
            'heartbeat_interval': (health.get('heartbeat') or {}).get('interval'),
            'primary_agent': (health.get('heartbeat') or {}).get('primary_agent'),
            'session_total': health.get('session_total', 0),
        },
        'pong_probe': {
            'checked': bool((proof.get('pong_probe') or {}).get('checked')),
            'ok': bool((proof.get('pong_probe') or {}).get('ok')),
            'output': (proof.get('pong_probe') or {}).get('output', ''),
        },
        'governed_agents': [
            {
                'agent_id': entry.get('agent_id'),
                'agent_name': entry.get('agent_name'),
                'org_id': entry.get('org_id'),
                'role': entry.get('role'),
                'openclaw_handle': entry.get('openclaw_handle'),
                'handle_source': entry.get('handle_source'),
                'runtime_binding': {
                    'runtime_id': ((entry.get('runtime_binding') or {}).get('runtime_id', '')),
                    'runtime_registered': bool((entry.get('runtime_binding') or {}).get('runtime_registered', False)),
                    'registration_status': ((entry.get('runtime_binding') or {}).get('registration_status', '')),
                    'bound_org_id': ((entry.get('runtime_binding') or {}).get('bound_org_id', '')),
                },
            }
            for entry in governed_agents
        ],
        'mapping': {
            'handle_overlap': list(proof.get('handle_overlap') or []),
            'handle_gap': list(proof.get('handle_gap') or []),
            'governed_agent_count': len(governed_agents),
        },
    }


def main(argv: Optional[List[str]] = None) -> int:
    argv = list(argv or sys.argv[1:])
    if '--json' in argv:
        import json

        proof = collect_openclaw_runtime_proof()
        print(json.dumps(proof, indent=2, sort_keys=True))
        return 0

    proof = collect_openclaw_runtime_proof()
    print(f"Checked at: {proof['checked_at']}")
    print(f"Health OK: {proof['health']['health_ok']}")
    print(f"Agents mapped: {len(proof['governed_agents'])}")
    print(f"Handle overlap: {', '.join(proof['handle_overlap']) or 'none'}")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
