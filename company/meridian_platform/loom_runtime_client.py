#!/usr/bin/env python3
"""Shared Loom runtime client helpers for Intelligence surfaces."""

from __future__ import annotations

import json
import os
import subprocess
import time
from dataclasses import dataclass
from typing import Callable, Mapping, Optional


Runner = Callable[..., subprocess.CompletedProcess]
Sleeper = Callable[[float], None]
ResultLoader = Callable[[str, object], object]
CapabilityNormalizer = Callable[[dict], dict]


@dataclass(frozen=True)
class LoomRuntimeContext:
    loom_bin: str
    loom_root: str
    org_id: str
    agent_id: str
    service_token: str = ''
    cwd: str = '.'
    runtime_env: Optional[Mapping[str, str]] = None

    def env(self) -> dict[str, str]:
        env = dict(self.runtime_env or os.environ)
        if self.service_token:
            env['LOOM_SERVICE_TOKEN'] = self.service_token
        return env


def _job_record_path(context: LoomRuntimeContext, job_id: str) -> str:
    return os.path.join(context.loom_root, 'state', 'runtime', 'jobs', job_id, 'job.json')


def _load_job_record(context: LoomRuntimeContext, job_id: str) -> dict:
    path = _job_record_path(context, job_id)
    try:
        with open(path, 'r', encoding='utf-8') as handle:
            payload = json.load(handle)
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def capability_preflight(
    context: LoomRuntimeContext,
    capability_name: str,
    *,
    route: str,
    runner: Runner = subprocess.run,
    normalize_capability: CapabilityNormalizer | None = None,
    transport_allowlist: tuple[str, ...] = ('http', 'socket+http'),
) -> dict:
    preflight = {
        'ok': False,
        'runtime': 'loom',
        'route': route,
        'capability_name': capability_name,
        'errors': [],
    }
    if not capability_name:
        preflight['errors'].append(f'{route} capability is not configured')
        return preflight

    env = context.env()
    service_cmd = [context.loom_bin, 'service', 'status', '--root', context.loom_root, '--format', 'json']
    capability_cmd = [
        context.loom_bin,
        'capability',
        'show',
        '--root',
        context.loom_root,
        '--name',
        capability_name,
        '--format',
        'json',
    ]

    try:
        service = runner(service_cmd, capture_output=True, text=True, timeout=15, cwd=context.cwd, env=env)
    except subprocess.TimeoutExpired:
        preflight['errors'].append('loom service status timed out')
        service = None
    except Exception as exc:
        preflight['errors'].append(str(exc))
        service = None

    if service is not None:
        if service.returncode != 0:
            message = (service.stderr or service.stdout or 'unknown error').strip()
            preflight['errors'].append(f'loom service status failed: {message[:500]}')
        else:
            try:
                service_payload = json.loads((service.stdout or '').strip())
            except json.JSONDecodeError:
                preflight['errors'].append('loom service status returned non-JSON output')
            else:
                preflight['service'] = service_payload
                if not service_payload.get('running'):
                    preflight['errors'].append('loom service is not running')
                if service_payload.get('service_status') != 'running':
                    preflight['errors'].append(f"loom service_status={service_payload.get('service_status', '')}")
                if service_payload.get('health') != 'healthy':
                    preflight['errors'].append(f"loom health={service_payload.get('health', '')}")
                transport = (service_payload.get('transport') or '').strip()
                if transport_allowlist and transport not in transport_allowlist:
                    preflight['errors'].append(f'loom transport={transport}')

    try:
        capability = runner(capability_cmd, capture_output=True, text=True, timeout=15, cwd=context.cwd, env=env)
    except subprocess.TimeoutExpired:
        preflight['errors'].append('loom capability show timed out')
        capability = None
    except Exception as exc:
        preflight['errors'].append(str(exc))
        capability = None

    if capability is not None:
        if capability.returncode != 0:
            message = (capability.stderr or capability.stdout or 'unknown error').strip()
            preflight['errors'].append(f'loom capability show failed: {message[:500]}')
        else:
            try:
                capability_payload = json.loads((capability.stdout or '').strip())
            except json.JSONDecodeError:
                preflight['errors'].append('loom capability show returned non-JSON output')
            else:
                preflight['capability'] = capability_payload
                if normalize_capability is not None:
                    normalized = normalize_capability(capability_payload)
                    preflight['normalized_import_metadata'] = normalized
                    if normalized.get('source_kind'):
                        preflight['capability']['source_kind'] = normalized['source_kind']
                    if normalized.get('worker_entry'):
                        preflight['capability']['worker_entry'] = normalized['worker_entry']
                    if normalized.get('source_manifest'):
                        preflight['capability']['source_manifest'] = normalized['source_manifest']
                    if normalized.get('source_path'):
                        preflight['capability']['source_path'] = normalized['source_path']
                    if not normalized.get('supported', True):
                        reason = normalized.get('unsupported_reason') or 'unknown'
                        preflight['errors'].append(f'loom imported skill subset unsupported: {reason}')
                if not capability_payload.get('enabled', False):
                    preflight['errors'].append('loom capability is disabled')
                if capability_payload.get('verification_status') not in {'verified', 'builtin'}:
                    preflight['errors'].append(
                        f"loom capability verification={capability_payload.get('verification_status', '')}"
                    )
                if capability_payload.get('promotion_state') not in {'promoted', 'builtin'}:
                    preflight['errors'].append(
                        f"loom capability promotion={capability_payload.get('promotion_state', '')}"
                    )

    preflight['ok'] = not preflight['errors']
    return preflight


def run_capability(
    context: LoomRuntimeContext,
    capability_name: str,
    payload: dict,
    timeout: int,
    *,
    agent_id: str | None = None,
    session_id: str = '',
    action_type: str = '',
    resource: str = '',
    runner: Runner = subprocess.run,
    sleeper: Sleeper = time.sleep,
    result_loader: ResultLoader | None = None,
) -> dict:
    if not capability_name:
        return {
            'ok': False,
            'runtime': 'loom',
            'capability_name': '',
            'error': 'Loom runtime selected but capability is not configured',
        }

    env = context.env()
    submit_cmd = [
        context.loom_bin,
        'service',
        'submit',
        '--root',
        context.loom_root,
        '--org-id',
        context.org_id,
        '--agent-id',
        (agent_id or context.agent_id).strip(),
        '--capability',
        capability_name,
        '--estimated-cost-usd',
        '0',
        '--payload-json',
        json.dumps(payload),
    ]
    if action_type:
        submit_cmd.extend(['--action-type', action_type])
    if resource:
        submit_cmd.extend(['--resource', resource])
    if session_id:
        submit_cmd.extend(['--session-id', session_id])
    if context.service_token:
        submit_cmd.extend(['--service-token', context.service_token])
    submit_cmd.extend(['--format', 'json'])

    try:
        submit = runner(
            submit_cmd,
            capture_output=True,
            text=True,
            timeout=min(timeout, 30),
            cwd=context.cwd,
            env=env,
        )
    except subprocess.TimeoutExpired:
        return {
            'ok': False,
            'runtime': 'loom',
            'capability_name': capability_name,
            'error': 'Loom service submit timed out',
        }
    except Exception as exc:
        return {
            'ok': False,
            'runtime': 'loom',
            'capability_name': capability_name,
            'error': str(exc),
        }

    if submit.returncode != 0:
        message = (submit.stderr or submit.stdout or 'unknown error').strip()
        return {
            'ok': False,
            'runtime': 'loom',
            'capability_name': capability_name,
            'error': f'Loom service submit failed: {message[:500]}',
        }

    try:
        submit_payload = json.loads((submit.stdout or '').strip())
    except json.JSONDecodeError:
        return {
            'ok': False,
            'runtime': 'loom',
            'capability_name': capability_name,
            'error': 'Loom service submit returned non-JSON output',
        }

    job_id = (submit_payload.get('job_id') or '').strip()
    if not job_id:
        return {
            'ok': False,
            'runtime': 'loom',
            'capability_name': capability_name,
            'error': 'Loom service submit did not return a job_id',
            'submit': submit_payload,
        }

    inspect_cmd = [
        context.loom_bin,
        'job',
        'inspect',
        '--root',
        context.loom_root,
        '--job-id',
        job_id,
        '--format',
        'json',
    ]
    deadline = time.time() + timeout
    last_snapshot = None
    while time.time() < deadline:
        try:
            inspect = runner(
                inspect_cmd,
                capture_output=True,
                text=True,
                timeout=15,
                cwd=context.cwd,
                env=env,
            )
        except subprocess.TimeoutExpired:
            inspect = None
        if inspect and inspect.returncode == 0:
            try:
                last_snapshot = json.loads((inspect.stdout or '').strip())
            except json.JSONDecodeError:
                last_snapshot = None
            if isinstance(last_snapshot, dict):
                status = (last_snapshot.get('job_status') or '').strip().lower()
                if status == 'completed':
                    result_path = os.path.join(context.loom_root, 'state', 'runtime', 'jobs', job_id, 'result.json')
                    worker_result = {}
                    if result_loader is not None:
                        loaded = result_loader(result_path, default={})
                        worker_result = loaded or {}
                    return {
                        'ok': True,
                        'runtime': 'loom',
                        'capability_name': capability_name,
                        'job_id': job_id,
                        'submit': submit_payload,
                        'snapshot': last_snapshot,
                        'worker_result': worker_result,
                    }
                if status in {'failed', 'denied', 'cancelled', 'hard_deny'}:
                    return {
                        'ok': False,
                        'runtime': 'loom',
                        'capability_name': capability_name,
                        'job_id': job_id,
                        'submit': submit_payload,
                        'snapshot': last_snapshot,
                        'error': f'Loom job ended with status={status}',
                    }
        sleeper(1)

    job_record = _load_job_record(context, job_id)
    job_status = str(job_record.get('job_status') or '').strip().lower()
    if job_status in {'failed', 'denied', 'cancelled', 'hard_deny'}:
        note = str(job_record.get('budget_reservation_reason') or job_record.get('note') or '').strip()
        message = f'Loom job ended with status={job_status}'
        if note:
            message = f'{message}: {note}'
        return {
            'ok': False,
            'runtime': 'loom',
            'capability_name': capability_name,
            'job_id': job_id,
            'submit': submit_payload,
            'snapshot': last_snapshot or job_record,
            'error': message,
        }

    return {
        'ok': False,
        'runtime': 'loom',
        'capability_name': capability_name,
        'job_id': job_id,
        'submit': submit_payload,
        'snapshot': last_snapshot or job_record,
        'error': f'Loom job timed out ({timeout}s limit)',
    }
