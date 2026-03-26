#!/usr/bin/env python3
"""
Meridian MCP Server — x402-capable intelligence tools.

Three tools:
  1. intelligence/latest-brief     — $0.50  — Today's intelligence brief
  2. intelligence/on-demand-research — $2.00 — On-demand research on any topic
  3. intelligence/qa-verify         — $1.00  — QA verification of any text

Payment: USDC on Base L2 via x402-capable settlement.

Run:
  python3 mcp_server.py                    # MCP stdio mode (default)
  python3 mcp_server.py --http 18900       # HTTP mode on port 18900
  python3 mcp_server.py --free             # Free mode (no payment required, for testing)

Institution scope:
  This server is bound to the founding Meridian institution (slug='meridian').
  All audit, metering, and revenue recording attaches to DEFAULT_ORG_ID, which
  is resolved once at startup from organizations.json.  There is no per-session
  or per-request org routing.  Revenue writes flow through the live
  economy/revenue.py service layer, which resolves through the founding
  institution's capsule aliases.  This x402 settlement flow is a deployment
  integration path, not an OSS substrate primitive.

  Multi-institution MCP support requires an org-scoped auth model that does
  not yet exist.  Until then, this server serves exactly one institution.
"""
from __future__ import annotations

import argparse
import contextlib
import datetime
import fcntl
import glob
import json
import logging
import os
import re
import subprocess
import sys
import time
from urllib.parse import quote_plus

from mcp.server.fastmcp import FastMCP
from brief_quality import analyze_brief

# ── Configuration ────────────────────────────────────────────────────────────

MCP_SERVER_FILE = os.path.abspath(__file__)
COMPANY_DIR = os.path.dirname(MCP_SERVER_FILE)
WORKSPACE = os.path.dirname(COMPANY_DIR)
OPENCLAW_HOME = os.path.dirname(WORKSPACE)
NIGHT_SHIFT_DIR = os.path.join(WORKSPACE, 'night-shift')
ECONOMY_DIR = os.path.join(WORKSPACE, 'economy')
PLATFORM_DIR = os.path.join(COMPANY_DIR, 'meridian_platform')
WALLET_FILE = os.path.join(OPENCLAW_HOME, 'credentials', 'base_wallet.json')
MCP_SETTLEMENT_LOG = os.path.join(COMPANY_DIR, 'mcp_settlements.jsonl')
MCP_SETTLEMENT_LOCK = os.path.join(COMPANY_DIR, '.mcp_settlement.lock')

# Platform integration — audit, metering, authority, treasury
sys.path.insert(0, PLATFORM_DIR)
try:
    from audit import log_event as audit_log
    from metering import record as meter_record
    PLATFORM_ENABLED = True
except ImportError:
    PLATFORM_ENABLED = False

try:
    from organizations import load_orgs, get_org
    from institution_context import InstitutionContext, MCP_SERVICE_BOUNDARY
    from capsule import ensure_payment_monitor_aliases
    from capsule import payment_events_log_path as capsule_payment_events_log_path
    ORG_CONTEXT_ENABLED = True
except ImportError:
    ORG_CONTEXT_ENABLED = False

# Constitutional OS primitives
try:
    from authority import check_authority as _authority_check, is_kill_switch_engaged as _kill_switch_check
    from treasury import check_budget as _treasury_check_budget
    CONSTITUTIONAL_ENABLED = True
except ImportError:
    CONSTITUTIONAL_ENABLED = False

# Founding institution binding — resolved once at startup.
# All audit, metering, treasury, and revenue calls use this org_id.
# There is no per-session or per-request org routing.
MCP_ORG_ID = (os.environ.get('MERIDIAN_MCP_ORG_ID') or '').strip() or None
DEFAULT_ORG_ID = None
try:
    if ORG_CONTEXT_ENABLED:
        from organizations import load_orgs
        _orgs = load_orgs()
        for _oid, _org in _orgs.get('organizations', {}).items():
            if _org.get('slug') == 'meridian':
                DEFAULT_ORG_ID = _oid
                break
except Exception:
    pass

# USDC on Base L2
BASE_NETWORK = 'eip155:8453'
USDC_ASSET = '0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913'
USDC_DECIMALS = 6

# Pricing (in USDC, smallest unit = 1e-6)
PRICES = {
    'latest-brief': 0.50,
    'on-demand-research': 2.00,
    'qa-verify': 1.00,
    'weekly-digest': 1.50,
    'competitor-snapshot': 3.00,
}

SUPPORTED_LOOM_IMPORT_RUNTIME_LANE = 'python_host_process/imported_workspace_skill'
SUPPORTED_LOOM_IMPORT_WORKER_KIND = 'python'
SUPPORTED_LOOM_IMPORT_PAYLOAD_MODE = 'json'
SUPPORTED_LOOM_IMPORT_ADAPTER_KIND = 'url_report_v0'
SUPPORTED_LOOM_IMPORT_DEPENDENCY_MODE = 'workspace_host_python'
SUPPORTED_LOOM_IMPORT_PROVENANCE = 'clawfamily_skill_contract_v0/workspace_python_entrypoint'

_LOOM_SKILL_MANIFEST_RE = re.compile(r'(?P<root>.*/skills/(?P<skill_slug>[^/]+)/SKILL\.md)$')
_LOOM_SKILL_PATH_RE = re.compile(r'(?P<root>.*/skills/(?P<skill_slug>[^/]+))$')
_LOOM_IMPORTED_WORKER_ENTRY_RE = re.compile(r'^workers/python/imported-(?P<import_token>[^/]+)\.py$')

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(name)s] %(message)s')
logger = logging.getLogger('meridian.mcp')


def _normalize_runtime(runtime: str) -> str:
    runtime = (runtime or '').strip().lower()
    return 'loom' if runtime == 'loom' else 'openclaw'


def _env_truthy(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {'1', 'true', 'yes', 'on'}


def _intelligence_exec_runtime(tool: str | None = None) -> str:
    if tool:
        scoped = os.environ.get(f'MERIDIAN_INTELLIGENCE_{tool.upper()}_RUNTIME')
        if scoped:
            return _normalize_runtime(scoped)
    return _normalize_runtime(os.environ.get('MERIDIAN_INTELLIGENCE_EXEC_RUNTIME') or 'openclaw')


def _intelligence_route_runtime(route: str, tool: str | None = None) -> str:
    scoped = os.environ.get(f'MERIDIAN_INTELLIGENCE_{route.upper()}_RUNTIME')
    if scoped:
        return _normalize_runtime(scoped)
    return _intelligence_exec_runtime(tool)


def _intelligence_route_fallback(route: str, default: bool = False) -> bool:
    return _env_truthy(f'MERIDIAN_INTELLIGENCE_{route.upper()}_ALLOW_FALLBACK', default=default)


def _loom_bin() -> str:
    return (
        os.environ.get('MERIDIAN_LOOM_BIN')
        or '/root/.local/share/meridian-loom/current/bin/loom'
    ).strip()


def _loom_root() -> str:
    return (os.environ.get('MERIDIAN_LOOM_ROOT') or '/root/.local/share/meridian-loom/runtime/default').strip()


def _loom_agent_id() -> str:
    return (os.environ.get('MERIDIAN_LOOM_AGENT_ID') or 'agent_leviathann').strip()


def _loom_service_token() -> str:
    return (
        os.environ.get('MERIDIAN_LOOM_SERVICE_TOKEN')
        or os.environ.get('LOOM_SERVICE_TOKEN')
        or ''
    ).strip()


def _loom_research_capability() -> str:
    return (os.environ.get('MERIDIAN_LOOM_RESEARCH_CAPABILITY') or '').strip()


def _loom_qa_capability() -> str:
    return (os.environ.get('MERIDIAN_LOOM_QA_CAPABILITY') or '').strip()


def _load_json_file(path: str, default=None):
    if not os.path.exists(path):
        return default
    with open(path) as f:
        return json.load(f)


def _openclaw_response_content(stdout: str):
    try:
        response = json.loads(stdout)
        return response.get('response', response.get('message', stdout))
    except json.JSONDecodeError:
        return stdout


def _run_openclaw_agent(agent: str, prompt: str, timeout: int) -> dict:
    try:
        result = subprocess.run(
            ['openclaw', 'agent', '--agent', agent, '--message', prompt, '--json'],
            capture_output=True, text=True, timeout=timeout, cwd=WORKSPACE
        )
        if result.returncode != 0:
            return {
                'ok': False,
                'runtime': 'openclaw',
                'agent': agent,
                'error': f'OpenClaw agent returned error: {result.stderr[:500]}',
            }
        return {
            'ok': True,
            'runtime': 'openclaw',
            'agent': agent,
            'content': _openclaw_response_content(result.stdout),
        }
    except subprocess.TimeoutExpired:
        return {
            'ok': False,
            'runtime': 'openclaw',
            'agent': agent,
            'error': f'OpenClaw agent timed out ({timeout}s limit)',
        }
    except Exception as exc:
        return {
            'ok': False,
            'runtime': 'openclaw',
            'agent': agent,
            'error': str(exc),
        }


def _extract_loom_content(worker_result: dict, preferred_keys: tuple[str, ...]) -> str:
    payload = worker_result.get('skill_output')
    if isinstance(payload, dict):
        for key in preferred_keys:
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value
    summary = worker_result.get('summary')
    if isinstance(summary, str) and summary.strip():
        return summary
    return json.dumps(payload if payload is not None else worker_result)


def _looks_like_http_url(value: str) -> bool:
    raw = (value or '').strip().lower()
    return raw.startswith('http://') or raw.startswith('https://')


def _loom_research_url(topic: str) -> str:
    topic = (topic or '').strip()
    if _looks_like_http_url(topic):
        return topic
    if not topic:
        return ''
    return f'https://duckduckgo.com/html/?q={quote_plus(topic)}'


def _loom_research_payload(topic: str, depth: str, prompt: str) -> dict:
    payload = {'topic': topic, 'depth': depth, 'prompt': prompt}
    loom_url = _loom_research_url(topic)
    if loom_url:
        payload['url'] = loom_url
        payload['urls'] = [loom_url]
    return payload


def _normalize_loom_skill_slug(value: str) -> str:
    value = (value or '').strip().lower().replace('_', '-')
    value = re.sub(r'[^a-z0-9-]+', '-', value)
    return re.sub(r'-{2,}', '-', value).strip('-')


def _normalize_loom_import_metadata(capability_payload: dict) -> dict:
    payload = dict(capability_payload or {})
    source_manifest = (payload.get('source_manifest') or '').strip()
    source_path = (payload.get('source_path') or '').strip()
    worker_entry = (payload.get('worker_entry') or '').strip()
    worker_kind = (payload.get('worker_kind') or '').strip().lower()
    runtime_lane = (payload.get('runtime_lane') or '').strip()
    payload_mode = (payload.get('payload_mode') or '').strip().lower()
    adapter_kind = (payload.get('adapter_kind') or '').strip()
    dependency_mode = (payload.get('dependency_mode') or '').strip()
    import_provenance = (payload.get('import_provenance') or '').strip()
    source_kind = (payload.get('source_kind') or '').strip()
    env_contract = (payload.get('env_contract') or '').strip()
    capability_name = (payload.get('name') or '').strip()

    unsupported_reasons = []
    skill_slug = ''
    import_token = ''

    manifest_match = _LOOM_SKILL_MANIFEST_RE.match(source_manifest)
    if manifest_match:
        skill_slug = _normalize_loom_skill_slug(manifest_match.group('skill_slug'))
    elif source_manifest:
        unsupported_reasons.append('source_manifest must end in /skills/<skill>/SKILL.md')

    path_match = _LOOM_SKILL_PATH_RE.match(source_path)
    if path_match:
        path_slug = _normalize_loom_skill_slug(path_match.group('skill_slug'))
        if skill_slug and path_slug != skill_slug:
            unsupported_reasons.append(
                f'source_path skill slug {path_slug} does not match source_manifest skill slug {skill_slug}'
            )
        skill_slug = skill_slug or path_slug
    elif source_path:
        unsupported_reasons.append('source_path must end in /skills/<skill>')

    worker_match = _LOOM_IMPORTED_WORKER_ENTRY_RE.match(worker_entry)
    if worker_match and not import_token:
        import_token = _normalize_loom_skill_slug(worker_match.group('import_token'))
        derived_skill_slug = import_token
        if derived_skill_slug.startswith('clawskill-'):
            derived_skill_slug = derived_skill_slug[len('clawskill-'):]
        if derived_skill_slug.endswith('-v0'):
            derived_skill_slug = derived_skill_slug[:-3]
        skill_slug = skill_slug or _normalize_loom_skill_slug(derived_skill_slug)

    normalized_source_path = source_path or (source_manifest.rsplit('/SKILL.md', 1)[0] if source_manifest else '')
    normalized_source_manifest = source_manifest or (f'{normalized_source_path}/SKILL.md' if normalized_source_path else '')
    if capability_name:
        import_token = _normalize_loom_skill_slug(capability_name.replace('.', '-'))
    elif not import_token and skill_slug:
        import_token = f'clawskill-{skill_slug}-v0'
    normalized_worker_entry = f'workers/python/imported-{import_token}.py' if import_token else worker_entry

    if not skill_slug:
        unsupported_reasons.append('could not derive a skill slug from import metadata')
    if worker_kind and worker_kind != SUPPORTED_LOOM_IMPORT_WORKER_KIND:
        unsupported_reasons.append(f'worker_kind={worker_kind}')
    if runtime_lane and runtime_lane != SUPPORTED_LOOM_IMPORT_RUNTIME_LANE:
        unsupported_reasons.append(f'runtime_lane={runtime_lane}')
    if payload_mode and payload_mode != SUPPORTED_LOOM_IMPORT_PAYLOAD_MODE:
        unsupported_reasons.append(f'payload_mode={payload_mode}')
    if adapter_kind and adapter_kind != SUPPORTED_LOOM_IMPORT_ADAPTER_KIND:
        unsupported_reasons.append(f'adapter_kind={adapter_kind}')
    if dependency_mode and dependency_mode != SUPPORTED_LOOM_IMPORT_DEPENDENCY_MODE:
        unsupported_reasons.append(f'dependency_mode={dependency_mode}')
    if import_provenance and import_provenance != SUPPORTED_LOOM_IMPORT_PROVENANCE:
        unsupported_reasons.append(f'import_provenance={import_provenance}')
    if normalized_worker_entry and worker_entry and worker_entry != normalized_worker_entry:
        unsupported_reasons.append(f'worker_entry={worker_entry}')
    if source_kind and source_kind not in {'openclaw_workspace_skill', 'openclaw_plugin_skill', 'openclaw_plugin_packaged_skill'}:
        unsupported_reasons.append(f'source_kind={source_kind}')

    supported = not unsupported_reasons
    return {
        'subset': 'openclaw_plugin_skill_subset',
        'supported': supported,
        'unsupported_reasons': unsupported_reasons,
        'unsupported_reason': '; '.join(unsupported_reasons) if unsupported_reasons else '',
        'skill_slug': skill_slug,
        'capability_name': capability_name,
        'import_token': import_token,
        'source_kind': source_kind,
        'source_manifest': normalized_source_manifest,
        'source_path': normalized_source_path,
        'worker_kind': worker_kind or SUPPORTED_LOOM_IMPORT_WORKER_KIND,
        'worker_entry': normalized_worker_entry,
        'runtime_lane': runtime_lane,
        'isolation_lane': runtime_lane,
        'payload_mode': payload_mode or SUPPORTED_LOOM_IMPORT_PAYLOAD_MODE,
        'adapter_kind': adapter_kind,
        'dependency_mode': dependency_mode,
        'env_contract': env_contract,
        'import_provenance': import_provenance,
    }


def _on_demand_research_prompt(topic: str, depth: str = 'standard') -> str:
    depth_map = {'quick': 3, 'standard': 5, 'deep': 7}
    n_findings = depth_map.get(depth, 5)
    return (
        f"Research the following topic and provide {n_findings} sourced findings. "
        f"Be specific and cite sources. Topic: {topic}"
    )


def _loom_capability_preflight(capability_name: str, *, route: str) -> dict:
    preflight = {
        'ok': False,
        'runtime': 'loom',
        'route': route,
        'capability_name': capability_name,
        'errors': [],
    }
    env = os.environ.copy()
    service_token = _loom_service_token()
    if service_token:
        env['LOOM_SERVICE_TOKEN'] = service_token

    if not capability_name:
        preflight['errors'].append(f'{route} capability is not configured')
        return preflight

    service_cmd = [_loom_bin(), 'service', 'status', '--root', _loom_root(), '--format', 'json']
    capability_cmd = [_loom_bin(), 'capability', 'show', '--root', _loom_root(), '--name', capability_name, '--format', 'json']

    try:
        service = subprocess.run(service_cmd, capture_output=True, text=True, timeout=15, cwd=WORKSPACE, env=env)
    except subprocess.TimeoutExpired:
        preflight['errors'].append('loom service status timed out')
        service = None
    except Exception as exc:
        preflight['errors'].append(str(exc))
        service = None

    if service is not None:
        if service.returncode != 0:
            preflight['errors'].append(f'loom service status failed: {service.stderr[:500]}')
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
                if service_payload.get('transport') not in {'http', 'socket+http'}:
                    preflight['errors'].append(f"loom transport={service_payload.get('transport', '')}")

    try:
        capability = subprocess.run(capability_cmd, capture_output=True, text=True, timeout=15, cwd=WORKSPACE, env=env)
    except subprocess.TimeoutExpired:
        preflight['errors'].append('loom capability show timed out')
        capability = None
    except Exception as exc:
        preflight['errors'].append(str(exc))
        capability = None

    if capability is not None:
        if capability.returncode != 0:
            message = capability.stderr.strip() or capability.stdout.strip() or 'unknown error'
            preflight['errors'].append(f'loom capability show failed: {message[:500]}')
        else:
            try:
                capability_payload = json.loads((capability.stdout or '').strip())
            except json.JSONDecodeError:
                preflight['errors'].append('loom capability show returned non-JSON output')
            else:
                preflight['capability'] = capability_payload
                normalized_import_metadata = _normalize_loom_import_metadata(capability_payload)
                preflight['normalized_import_metadata'] = normalized_import_metadata
                if not normalized_import_metadata['supported']:
                    preflight['errors'].append(
                        'loom imported skill subset unsupported: '
                        + normalized_import_metadata['unsupported_reason']
                    )
                if not capability_payload.get('enabled', False):
                    preflight['errors'].append('loom capability is disabled')
                if capability_payload.get('verification_status') != 'verified':
                    preflight['errors'].append(
                        f"loom capability verification={capability_payload.get('verification_status', '')}"
                    )
                if capability_payload.get('promotion_state') != 'promoted':
                    preflight['errors'].append(
                        f"loom capability promotion={capability_payload.get('promotion_state', '')}"
                    )

    preflight['ok'] = not preflight['errors']
    return preflight


def _loom_research_preflight(capability_name: str) -> dict:
    return _loom_capability_preflight(capability_name, route='intelligence_on_demand_research')


def _loom_qa_preflight(capability_name: str) -> dict:
    return _loom_capability_preflight(capability_name, route='intelligence_qa_verify')

def _route_cutover_state(
    route_name: str,
    requested_runtime: str,
    selected_runtime: str,
    fallback_enabled: bool,
    *,
    fallback_state: dict | None = None,
    loom_result: dict | None = None,
    loom_preflight: dict | None = None,
) -> dict:
    state = {
        'route': route_name,
        'requested_runtime': requested_runtime,
        'selected_runtime': selected_runtime,
        'fallback_enabled': fallback_enabled,
    }
    if fallback_state:
        state['fallback'] = fallback_state
    if isinstance(loom_preflight, dict):
        state['loom_preflight'] = loom_preflight
    transcript_bits = [
        f'route={route_name}',
        f'requested={requested_runtime}',
        f'selected={selected_runtime}',
        f"fallback={'on' if fallback_enabled else 'off'}",
    ]
    if isinstance(loom_preflight, dict):
        transcript_bits.append(f"preflight={'ok' if loom_preflight.get('ok') else 'blocked'}")
        capability_name = (loom_preflight.get('capability_name') or '').strip()
        if capability_name:
            transcript_bits.append(f'capability={capability_name}')
    if isinstance(fallback_state, dict):
        fallback_used = fallback_state.get('used')
        if fallback_used is not None:
            transcript_bits.append(f"fallback_used={'true' if fallback_used else 'false'}")
        fallback_state_name = (fallback_state.get('state') or '').strip()
        if fallback_state_name:
            transcript_bits.append(f"fallback_state={fallback_state_name}")
        fallback_reason = (fallback_state.get('reason') or '').strip()
        if fallback_reason:
            transcript_bits.append(f'fallback_reason={fallback_reason}')
    if isinstance(loom_result, dict):
        job_id = (loom_result.get('job_id') or '').strip()
        if job_id:
            transcript_bits.append(f'job_id={job_id}')
        submit = loom_result.get('submit')
        if isinstance(submit, dict):
            transport = (submit.get('transport') or '').strip()
            if transport:
                transcript_bits.append(f'transport={transport}')
        snapshot = loom_result.get('snapshot')
        if isinstance(snapshot, dict):
            job_status = (snapshot.get('job_status') or '').strip()
            worker_status = (snapshot.get('worker_status') or '').strip()
            if job_status:
                transcript_bits.append(f'job_status={job_status}')
            if worker_status:
                transcript_bits.append(f'worker_status={worker_status}')
    state['transcript'] = ' | '.join(transcript_bits)
    if not isinstance(loom_result, dict):
        return state
    loom_meta = {}
    capability_name = (loom_result.get('capability_name') or '').strip()
    job_id = (loom_result.get('job_id') or '').strip()
    if capability_name:
        loom_meta['capability_name'] = capability_name
    if job_id:
        loom_meta['job_id'] = job_id
    submit = loom_result.get('submit')
    if isinstance(submit, dict):
        for key in ('transport', 'service_target', 'queue_path', 'ingress_request_path', 'ingress_receipt_path', 'policy_class'):
            value = submit.get(key)
            if value:
                loom_meta[key] = value
    snapshot = loom_result.get('snapshot')
    if isinstance(snapshot, dict):
        for key in ('job_status', 'job_stage', 'runtime_outcome', 'worker_status', 'job_path', 'event_path', 'audit_log_path', 'parity_report_path'):
            value = snapshot.get(key)
            if value:
                loom_meta[key] = value
        job_path = snapshot.get('job_path')
        if isinstance(job_path, str) and job_path.strip():
            loom_meta['result_path_hint'] = os.path.join(os.path.dirname(job_path), 'result.json')
    if loom_meta:
        state['loom'] = loom_meta
    return state


def _on_demand_research_cutover_state(
    requested_runtime: str,
    selected_runtime: str,
    fallback_enabled: bool,
    *,
    fallback_state: dict | None = None,
    loom_result: dict | None = None,
    loom_preflight: dict | None = None,
) -> dict:
    return _route_cutover_state(
        'intelligence_on_demand_research',
        requested_runtime,
        selected_runtime,
        fallback_enabled,
        fallback_state=fallback_state,
        loom_result=loom_result,
        loom_preflight=loom_preflight,
    )


def _qa_verify_cutover_state(
    requested_runtime: str,
    selected_runtime: str,
    fallback_enabled: bool,
    *,
    fallback_state: dict | None = None,
    loom_result: dict | None = None,
    loom_preflight: dict | None = None,
) -> dict:
    return _route_cutover_state(
        'intelligence_qa_verify',
        requested_runtime,
        selected_runtime,
        fallback_enabled,
        fallback_state=fallback_state,
        loom_result=loom_result,
        loom_preflight=loom_preflight,
    )


def _with_on_demand_research_cutover(
    result: dict,
    requested_runtime: str,
    selected_runtime: str,
    fallback_enabled: bool,
    *,
    fallback_state: dict | None = None,
    loom_result: dict | None = None,
    loom_preflight: dict | None = None,
) -> dict:
    enriched = dict(result)
    enriched['route_cutover'] = _on_demand_research_cutover_state(
        requested_runtime,
        selected_runtime,
        fallback_enabled,
        fallback_state=fallback_state,
        loom_result=loom_result,
        loom_preflight=loom_preflight,
    )
    return enriched


def _with_qa_verify_cutover(
    result: dict,
    requested_runtime: str,
    selected_runtime: str,
    fallback_enabled: bool,
    *,
    fallback_state: dict | None = None,
    loom_result: dict | None = None,
    loom_preflight: dict | None = None,
) -> dict:
    enriched = dict(result)
    enriched['route_cutover'] = _qa_verify_cutover_state(
        requested_runtime,
        selected_runtime,
        fallback_enabled,
        fallback_state=fallback_state,
        loom_result=loom_result,
        loom_preflight=loom_preflight,
    )
    return enriched

def _extract_loom_research_content(worker_result: dict) -> str:
    payload = worker_result.get('skill_output')
    if isinstance(payload, dict):
        for key in ('research', 'response', 'message', 'text'):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value
        results = payload.get('results')
        if isinstance(results, list):
            normalized = []
            for item in results:
                if not isinstance(item, dict):
                    continue
                text = item.get('normalized_text')
                if isinstance(text, str) and text.strip():
                    normalized.append(text.strip())
            if normalized:
                return '\n\n'.join(normalized)
    summary = worker_result.get('summary')
    if isinstance(summary, str) and summary.strip():
        return summary
    return json.dumps(payload if payload is not None else worker_result)


def _run_loom_capability(capability_name: str, payload: dict, timeout: int) -> dict:
    if not capability_name:
        return {
            'ok': False,
            'runtime': 'loom',
            'capability_name': '',
            'error': 'Loom runtime selected but capability is not configured',
        }

    env = os.environ.copy()
    service_token = _loom_service_token()
    if service_token:
        env['LOOM_SERVICE_TOKEN'] = service_token

    submit_cmd = [
        _loom_bin(),
        'service',
        'submit',
        '--root',
        _loom_root(),
        '--agent-id',
        _loom_agent_id(),
        '--capability',
        capability_name,
        '--estimated-cost-usd',
        '0',
        '--payload-json',
        json.dumps(payload),
    ]
    if service_token:
        submit_cmd.extend(['--service-token', service_token])
    submit_cmd.extend([
        '--format',
        'json',
    ])

    try:
        submit = subprocess.run(
            submit_cmd,
            capture_output=True,
            text=True,
            timeout=min(timeout, 30),
            cwd=WORKSPACE,
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
        return {
            'ok': False,
            'runtime': 'loom',
            'capability_name': capability_name,
            'error': f'Loom service submit failed: {submit.stderr[:500]}',
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
        _loom_bin(),
        'job',
        'inspect',
        '--root',
        _loom_root(),
        '--job-id',
        job_id,
        '--format',
        'json',
    ]
    deadline = time.time() + timeout
    last_snapshot = None
    while time.time() < deadline:
        try:
            inspect = subprocess.run(
                inspect_cmd,
                capture_output=True,
                text=True,
                timeout=15,
                cwd=WORKSPACE,
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
                    result_path = os.path.join(_loom_root(), 'state', 'runtime', 'jobs', job_id, 'result.json')
                    worker_result = _load_json_file(result_path, default={}) or {}
                    return {
                        'ok': True,
                        'runtime': 'loom',
                        'capability_name': capability_name,
                        'job_id': job_id,
                        'submit': submit_payload,
                        'snapshot': last_snapshot,
                        'worker_result': worker_result,
                    }
                if status in {'failed', 'denied', 'cancelled'}:
                    return {
                        'ok': False,
                        'runtime': 'loom',
                        'capability_name': capability_name,
                        'job_id': job_id,
                        'submit': submit_payload,
                        'snapshot': last_snapshot,
                        'error': f'Loom job ended with status={status}',
                    }
        time.sleep(1)

    return {
        'ok': False,
        'runtime': 'loom',
        'capability_name': capability_name,
        'job_id': job_id,
        'submit': submit_payload,
        'snapshot': last_snapshot,
        'error': f'Loom job timed out ({timeout}s limit)',
    }


def _resolve_mcp_context():
    """Return the single institution context this live MCP service may use."""
    if not ORG_CONTEXT_ENABLED:
        raise RuntimeError('Institution context primitives unavailable for live MCP service')
    if not DEFAULT_ORG_ID:
        raise RuntimeError('Founding Meridian institution could not be resolved for MCP service')
    if MCP_ORG_ID:
        if MCP_ORG_ID != DEFAULT_ORG_ID:
            raise RuntimeError(
                f"Live MCP only supports founding org '{DEFAULT_ORG_ID}', got '{MCP_ORG_ID}'"
            )
        source = 'configured_org'
    else:
        source = 'founding_default'
    org = get_org(DEFAULT_ORG_ID)
    if not org:
        raise RuntimeError(f"Founding Meridian institution not found: {DEFAULT_ORG_ID}")
    ctx = InstitutionContext.bind(DEFAULT_ORG_ID, org, source, MCP_SERVICE_BOUNDARY)
    return {
        **ctx.to_dict(),
        'org_id': ctx.org_id,
        'source': ctx.context_source,
        'service_scope': 'founding_meridian_service',
        'supports_institution_routing': False,
    }


def _payment_events_log_path():
    if ORG_CONTEXT_ENABLED:
        ensure_payment_monitor_aliases()
        return capsule_payment_events_log_path()
    return os.path.join(COMPANY_DIR, 'payment_events.log')


@contextlib.contextmanager
def settlement_lock():
    os.makedirs(COMPANY_DIR, exist_ok=True)
    with open(MCP_SETTLEMENT_LOCK, 'a+') as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def load_wallet_address() -> str:
    """Load wallet address from credentials."""
    if os.path.exists(WALLET_FILE):
        with open(WALLET_FILE) as f:
            return json.load(f)['address']
    raise RuntimeError(f'Wallet not found at {WALLET_FILE}. Run: python3 company/wallet.py generate')


def usd_to_amount(usd: float) -> str:
    """Convert USD amount to USDC smallest unit string."""
    return str(int(usd * (10 ** USDC_DECIMALS)))


def append_mcp_settlement_event(event: str, tool_name: str, product: str,
                                amount_usd: float, payment_ref: str,
                                details: dict | None = None):
    context = _resolve_mcp_context()
    entry = {
        'ts': datetime.datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ'),
        'event': event,
        'tool_name': tool_name,
        'product': product,
        'amount_usd': float(amount_usd),
        'payment_ref': payment_ref,
        'org_id': context.get('org_id'),
        'context_source': context.get('source'),
        'service_scope': context.get('service_scope'),
    }
    if details:
        entry['details'] = details
    with settlement_lock():
        with open(MCP_SETTLEMENT_LOG, 'a') as f:
            f.write(json.dumps(entry) + '\n')


def load_mcp_settlement_events() -> list[dict]:
    if not os.path.exists(MCP_SETTLEMENT_LOG):
        return []
    events = []
    with settlement_lock():
        with open(MCP_SETTLEMENT_LOG) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    events.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    return events


def pending_mcp_settlements(events: list[dict] | None = None) -> list[dict]:
    events = events if events is not None else load_mcp_settlement_events()
    pending: dict[str, dict] = {}
    for entry in events:
        payment_ref = entry.get('payment_ref')
        if not payment_ref:
            continue
        if entry.get('event') == 'settled':
            pending[payment_ref] = {
                'payment_ref': payment_ref,
                'tool_name': entry.get('tool_name', ''),
                'product': entry.get('product', ''),
                'amount_usd': float(entry.get('amount_usd', 0.0)),
            }
            continue
        if entry.get('event') in {'recorded', 'duplicate'}:
            pending.pop(payment_ref, None)
    return list(pending.values())


def recover_pending_mcp_settlements(record_fn=None, events: list[dict] | None = None) -> list[dict]:
    record_fn = record_fn or record_revenue
    results = []
    for settlement in pending_mcp_settlements(events):
        result = record_fn(
            settlement['product'],
            settlement['amount_usd'],
            payment_ref=settlement['payment_ref'],
            tool_name=settlement['tool_name'],
        )
        results.append({'payment_ref': settlement['payment_ref'], **result})
    return results


def record_revenue(product: str, amount_usd: float, payment_ref: str = '', tool_name: str = ''):
    """Record founding-service revenue via the live revenue layer.

    This is deployment-specific settlement handling for the founding Meridian
    service.  It is intentionally not a generic OSS substrate contract.
    """
    if not payment_ref:
        logger.warning(f'record_revenue called without payment_ref for {product} — skipping (settlement not captured)')
        return {'status': 'missing_ref', 'product': product}

    try:
        context = _resolve_mcp_context()
        # Direct import instead of subprocess
        sys.path.insert(0, ECONOMY_DIR)
        now = datetime.datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ')
        from revenue import record_external_customer_payment

        client_id = payment_ref[:8] if payment_ref else 'mcp-anon'
        result = record_external_customer_payment(
            product,
            amount_usd,
            payment_key=f'ref:{payment_ref}',
            client_name=f'mcp:{client_id}',
            client_contact=f'x402:{payment_ref}',
            note=f'x402 MCP payment ref:{payment_ref}',
            payment_ref=payment_ref,
            payment_source='mcp_x402',
        )

        # Persist to payment events log
        log_path = _payment_events_log_path()
        with open(log_path, 'a') as f:
            event = 'DUPLICATE' if result['duplicate'] else 'REVENUE'
            f.write(
                f"{now} {event} ${amount_usd:.2f} product={product} order={result['order_id']} ref={payment_ref}\n"
            )

        if result['duplicate']:
            append_mcp_settlement_event(
                'duplicate',
                tool_name or product,
                product,
                amount_usd,
                payment_ref,
                details={'order_id': result['order_id']},
            )
            logger.warning(f'Duplicate payment_ref {payment_ref} for {product} — skipping')
            return {'status': 'duplicate', 'order_id': result['order_id'], 'product': product}

        append_mcp_settlement_event(
            'recorded',
            tool_name or product,
            product,
            amount_usd,
            payment_ref,
            details={'order_id': result['order_id']},
        )
        logger.info(f'Revenue recorded: ${amount_usd} for {product} (order {result["order_id"]})')

        # Founding-institution metadata tagging (append-only observability).
        # These calls tag audit/metering records with the founding org_id.
        # This is NOT per-request org routing — the server is single-tenant.
        if PLATFORM_ENABLED and DEFAULT_ORG_ID:
            audit_log(context['org_id'], 'system', 'revenue_recorded',
                      resource=product, outcome='success',
                      details={
                          'amount_usd': amount_usd,
                          'order_id': result['order_id'],
                          'payment_ref': payment_ref,
                          'service_scope': context['service_scope'],
                          'context_source': context['source'],
                      })
            meter_record(context['org_id'], '', 'payment_received',
                         quantity=1, unit='transactions', cost_usd=amount_usd,
                         details={'service_scope': context['service_scope']})
        return {'status': 'recorded', 'order_id': result['order_id'], 'product': product}

    except Exception as e:
        logger.error(f'Failed to record revenue: {e}')
        append_mcp_settlement_event(
            'failed',
            tool_name or product,
            product,
            amount_usd,
            payment_ref,
            details={'error': str(e)},
        )
        # Fallback: at least log the event
        try:
            log_path = _payment_events_log_path()
            with open(log_path, 'a') as f:
                f.write(f"{datetime.datetime.utcnow().isoformat()}Z FAILED ${amount_usd:.2f} product={product} error={e}\n")
        except Exception:
            pass
        return {'status': 'failed', 'error': str(e), 'product': product}


def _audit_tool_call(tool_name: str, price_usd: float, outcome: str = 'success',
                     agent_id: str = '', details: dict = None):
    """Log audit event and meter record for an MCP tool call.
    Also checks authority and treasury if constitutional primitives are available."""
    context = _resolve_mcp_context()
    # Constitutional checks for paid tools
    if CONSTITUTIONAL_ENABLED and price_usd > 0:
        if _kill_switch_check():
            raise RuntimeError('Kill switch engaged — all paid operations halted')
        if agent_id:
            allowed, reason = _treasury_check_budget(agent_id, price_usd, org_id=context['org_id'])
            if not allowed:
                raise RuntimeError(f'Treasury check failed: {reason}')

    if not PLATFORM_ENABLED or not context['org_id']:
        return
    # Founding-institution metadata tagging (append-only observability).
    audit_log(context['org_id'], agent_id or 'mcp_client', 'mcp_tool_call',
              resource=tool_name, outcome=outcome, actor_type='user',
              details={**(details or {}), 'service_scope': context['service_scope'],
                       'context_source': context['source']})
    meter_record(context['org_id'], agent_id, 'mcp_tool_call',
                 quantity=1, unit='calls', cost_usd=price_usd,
                 details={'tool': tool_name, 'service_scope': context['service_scope']})


# ── Tool implementations ─────────────────────────────────────────────────────

def get_latest_brief(topic_filter: str = '') -> dict:
    """Fetch today's (or most recent) intelligence brief."""
    today = datetime.date.today().isoformat()
    brief_path = os.path.join(NIGHT_SHIFT_DIR, f'brief-{today}.md')

    # If today's brief doesn't exist, find most recent
    if not os.path.exists(brief_path):
        briefs = sorted(glob.glob(os.path.join(NIGHT_SHIFT_DIR, 'brief-*.md')))
        if not briefs:
            return {'error': 'No briefs available', 'date': today}
        brief_path = briefs[-1]

    with open(brief_path) as f:
        content = f.read()
    full_content = content

    date = os.path.basename(brief_path).replace('brief-', '').replace('.md', '')
    audit = analyze_brief(brief_path)

    # Apply topic filter if provided
    if topic_filter:
        lines = full_content.split('\n')
        filtered = [l for l in lines if topic_filter.lower() in l.lower()]
        if filtered:
            content = '\n'.join(filtered)
        else:
            content = full_content
    else:
        content = full_content

    # Also get findings if available
    findings_path = os.path.join(NIGHT_SHIFT_DIR, f'findings-{date}.md')
    findings = ''
    if os.path.exists(findings_path):
        with open(findings_path) as f:
            findings = f.read()

    return {
        'date': date,
        'brief': content,
        'findings': findings[:2000] if findings else None,
        'source': 'Meridian Night-Shift Pipeline',
        'qa_status': 'PASS' if audit['passed'] else 'FAIL',
        'quality_audit': audit,
    }


def get_weekly_digest() -> dict:
    """Return top findings from the past 7 days."""
    findings = []
    today = datetime.date.today()
    for i in range(7):
        d = today - datetime.timedelta(days=i)
        f_path = os.path.join(NIGHT_SHIFT_DIR, f'findings-{d.isoformat()}.md')
        if os.path.exists(f_path):
            with open(f_path) as fh:
                findings.append({'date': d.isoformat(), 'content': fh.read()[:600]})
    period_start = (today - datetime.timedelta(days=6)).isoformat()
    return {
        'period': f'{period_start} to {today.isoformat()}',
        'count': len(findings),
        'findings': findings[:5],
        'source': 'Meridian Night-Shift Pipeline',
        'qa_status': 'ARTIFACT_DERIVED_NOT_REVALIDATED',
    }


def _run_openclaw_on_demand_research(topic: str, depth: str, prompt: str) -> dict:
    result = _run_openclaw_agent('atlas', prompt, timeout=120)
    if result.get('ok'):
        return {
            'topic': topic,
            'depth': depth,
            'research': result.get('content', ''),
            'agent': 'Atlas',
            'runtime': 'openclaw',
            'source': 'Meridian Research Pipeline',
        }
    return {
        'topic': topic,
        'depth': depth,
        'runtime': 'openclaw',
        'error': result.get('error', 'OpenClaw runtime failed'),
    }


def _run_loom_on_demand_research(topic: str, depth: str, prompt: str) -> tuple[dict, dict]:
    loom_result = _run_loom_capability(
        _loom_research_capability(),
        _loom_research_payload(topic, depth, prompt),
        timeout=150,
    )
    if loom_result.get('ok'):
        worker_result = loom_result.get('worker_result') or {}
        return ({
            'topic': topic,
            'depth': depth,
            'research': _extract_loom_research_content(worker_result),
            'runtime': 'loom',
            'capability_name': loom_result.get('capability_name', ''),
            'job_id': loom_result.get('job_id', ''),
            'source': 'Meridian Research Pipeline',
        }, loom_result)
    return ({
        'topic': topic,
        'depth': depth,
        'runtime': 'loom',
        'capability_name': loom_result.get('capability_name', ''),
        'job_id': loom_result.get('job_id', ''),
        'error': loom_result.get('error', 'Loom runtime failed'),
    }, loom_result)


def do_on_demand_research(topic: str, depth: str = 'standard') -> dict:
    """Run research through the configured runtime adapter."""
    prompt = _on_demand_research_prompt(topic, depth)
    if _intelligence_exec_runtime('research') == 'loom':
        result, _ = _run_loom_on_demand_research(topic, depth, prompt)
        return result
    return _run_openclaw_on_demand_research(topic, depth, prompt)


def do_on_demand_research_route(topic: str, depth: str = 'standard') -> dict:
    """Run the paid on-demand research route with route-specific cutover controls."""
    requested_runtime = _intelligence_route_runtime('on_demand_research', tool='research')
    prompt = _on_demand_research_prompt(topic, depth)

    if requested_runtime != 'loom':
        fallback_enabled = _intelligence_route_fallback('on_demand_research', default=False)
        result = _run_openclaw_on_demand_research(topic, depth, prompt)
        return _with_on_demand_research_cutover(result, requested_runtime, 'openclaw', fallback_enabled)

    loom_preflight = _loom_research_preflight(_loom_research_capability())
    if not loom_preflight.get('ok'):
        fallback_state = {
            'used': False,
            'from_runtime': 'loom',
            'reason': '; '.join(loom_preflight.get('errors') or ['loom preflight failed']),
            'state': 'preflight_failed',
        }
        result = {
            'topic': topic,
            'depth': depth,
            'runtime': 'loom',
            'capability_name': _loom_research_capability(),
            'error': f"Loom research preflight failed: {fallback_state['reason']}",
        }
        return _with_on_demand_research_cutover(
            result,
            requested_runtime,
            'loom',
            False,
            fallback_state=fallback_state,
            loom_preflight=loom_preflight,
        )

    result, loom_result = _run_loom_on_demand_research(topic, depth, prompt)
    if not result.get('error'):
        return _with_on_demand_research_cutover(
            result,
            requested_runtime,
            'loom',
            False,
            loom_result=loom_result,
            loom_preflight=loom_preflight,
        )

    fallback_state = {
        'used': False,
        'from_runtime': 'loom',
        'reason': result.get('error', 'loom runtime failed'),
        'state': 'loom_failed',
    }
    return _with_on_demand_research_cutover(
        result,
        requested_runtime,
        'loom',
        False,
        fallback_state=fallback_state,
        loom_result=loom_result,
        loom_preflight=loom_preflight,
    )

def do_qa_verify(text: str, criteria: str = 'factual') -> dict:
    """Run QA verification through the configured runtime adapter."""
    prompt = (
        f"Verify the following text for {criteria} quality. "
        f"Return PASS or FAIL with specific reasons and confidence score (0-100). "
        f"Text to verify:\n\n{text[:3000]}"
    )

    if _intelligence_exec_runtime('qa') == 'loom':
        loom_result = _run_loom_capability(
            _loom_qa_capability(),
            {'text': text, 'criteria': criteria, 'prompt': prompt},
            timeout=90,
        )
        if loom_result.get('ok'):
            worker_result = loom_result.get('worker_result') or {}
            return {
                'criteria': criteria,
                'verification': _extract_loom_content(worker_result, ('verification', 'response', 'message', 'text')),
                'runtime': 'loom',
                'capability_name': loom_result.get('capability_name', ''),
                'job_id': loom_result.get('job_id', ''),
                'source': 'Meridian QA Pipeline',
            }
        return {
            'criteria': criteria,
            'runtime': 'loom',
            'capability_name': loom_result.get('capability_name', ''),
            'job_id': loom_result.get('job_id', ''),
            'error': loom_result.get('error', 'Loom runtime failed'),
        }

    result = _run_openclaw_agent('aegis', prompt, timeout=60)
    if result.get('ok'):
        return {
            'criteria': criteria,
            'verification': result.get('content', ''),
            'agent': 'Aegis',
            'runtime': 'openclaw',
            'source': 'Meridian QA Pipeline',
        }
    return {
        'criteria': criteria,
        'runtime': 'openclaw',
        'error': result.get('error', 'OpenClaw runtime failed'),
    }


def do_qa_verify_route(text: str, criteria: str = 'factual') -> dict:
    """Run QA verification with truthful route preflight and cutover state."""
    requested_runtime = _intelligence_route_runtime('qa_verify', tool='qa')
    fallback_enabled = _intelligence_route_fallback('qa_verify', default=False)
    prompt = (
        f"Verify the following text for {criteria} quality. "
        f"Return PASS or FAIL with specific reasons and confidence score (0-100). "
        f"Text to verify:\n\n{text[:3000]}"
    )

    if requested_runtime != 'loom':
        result = _run_openclaw_agent('aegis', prompt, timeout=60)
        if result.get('ok'):
            result = {
                'criteria': criteria,
                'verification': result.get('content', ''),
                'agent': 'Aegis',
                'runtime': 'openclaw',
                'source': 'Meridian QA Pipeline',
            }
        else:
            result = {
                'criteria': criteria,
                'runtime': 'openclaw',
                'error': result.get('error', 'OpenClaw runtime failed'),
            }
        return _with_qa_verify_cutover(result, requested_runtime, 'openclaw', fallback_enabled)

    loom_preflight = _loom_qa_preflight(_loom_qa_capability())
    if not loom_preflight.get('ok'):
        fallback_state = {
            'used': False,
            'from_runtime': 'loom',
            'reason': '; '.join(loom_preflight.get('errors') or ['loom preflight failed']),
            'state': 'preflight_failed',
        }
        result = {
            'criteria': criteria,
            'runtime': 'loom',
            'capability_name': _loom_qa_capability(),
            'error': f"Loom QA preflight failed: {fallback_state['reason']}",
        }
        return _with_qa_verify_cutover(
            result,
            requested_runtime,
            'loom',
            False,
            fallback_state=fallback_state,
            loom_preflight=loom_preflight,
        )

    loom_result = _run_loom_capability(
        _loom_qa_capability(),
        {'text': text, 'criteria': criteria, 'prompt': prompt},
        timeout=90,
    )
    if loom_result.get('ok'):
        worker_result = loom_result.get('worker_result') or {}
        result = {
            'criteria': criteria,
            'verification': _extract_loom_content(worker_result, ('verification', 'response', 'message', 'text')),
            'runtime': 'loom',
            'capability_name': loom_result.get('capability_name', ''),
            'job_id': loom_result.get('job_id', ''),
            'source': 'Meridian QA Pipeline',
        }
        return _with_qa_verify_cutover(
            result,
            requested_runtime,
            'loom',
            False,
            loom_result=loom_result,
            loom_preflight=loom_preflight,
        )

    fallback_state = {
        'used': False,
        'from_runtime': 'loom',
        'reason': loom_result.get('error', 'loom runtime failed'),
        'state': 'loom_failed',
    }

    result = {
        'criteria': criteria,
        'runtime': 'loom',
        'capability_name': loom_result.get('capability_name', ''),
        'job_id': loom_result.get('job_id', ''),
        'error': loom_result.get('error', 'Loom runtime failed'),
    }
    return _with_qa_verify_cutover(
        result,
        requested_runtime,
        'loom',
        False,
        fallback_state=fallback_state,
        loom_result=loom_result,
        loom_preflight=loom_preflight,
    )


# ── MCP Server Setup ─────────────────────────────────────────────────────────

def create_server(free_mode: bool = False) -> FastMCP:
    """Create the MCP server with optional x402 payment gating."""

    mcp = FastMCP("Meridian Intelligence")

    if not free_mode:
        try:
            wallet_address = load_wallet_address()
            from x402 import PaymentRequirements, ResourceInfo, x402ResourceServerSync
            from x402.http.facilitator_client import HTTPFacilitatorClientSync
            from x402.mcp.server import create_payment_wrapper, PaymentWrapperHooks

            # Create resource server WITH facilitator (fixes SchemeNotFoundError)
            facilitator = HTTPFacilitatorClientSync()  # default: https://x402.org/facilitator
            resource_server = x402ResourceServerSync(facilitator_clients=[facilitator])
            resource_server.initialize()  # fetch supported schemes from facilitator
            logger.info('x402 facilitator initialized (https://x402.org/facilitator)')

            recovered = recover_pending_mcp_settlements()
            if recovered:
                statuses = ', '.join(
                    f"{item['payment_ref']}:{item['status']}" for item in recovered
                )
                logger.warning(f'Recovered pending MCP settlements on startup: {statuses}')

            def _make_settlement_hook(tool_name: str, product: str, price_usd: float):
                def on_after_settlement(ctx):
                    tx = getattr(ctx, 'settlement', None)
                    if tx:
                        ref = getattr(tx, 'transaction', 'unknown')
                        append_mcp_settlement_event('settled', tool_name, product, price_usd, ref)
                        outcome = record_revenue(product, price_usd, payment_ref=ref, tool_name=tool_name)
                        logger.info(f'Payment settled for {tool_name}: tx={ref} status={outcome["status"]}')
                return on_after_settlement

            def make_requirements(tool_name: str, price_usd: float) -> list[PaymentRequirements]:
                return [PaymentRequirements(
                    scheme='exact',
                    network=BASE_NETWORK,
                    asset=USDC_ASSET,
                    amount=usd_to_amount(price_usd),
                    pay_to=wallet_address,
                    max_timeout_seconds=300,
                    extra={
                        'name': f'Meridian: {tool_name}',
                        'description': f'Intelligence tool: {tool_name}',
                    },
                )]

            # Paid Tool 1: Latest Brief
            brief_wrapper = create_payment_wrapper(
                resource_server,
                accepts=make_requirements('latest-brief', PRICES['latest-brief']),
                resource=ResourceInfo(
                    url='mcp://meridian/intelligence/latest-brief',
                    description='Get latest AI intelligence brief ($0.50)',
                    mime_type='application/json',
                ),
                hooks=PaymentWrapperHooks(
                    on_after_settlement=_make_settlement_hook(
                        'latest-brief',
                        'intelligence/latest-brief',
                        PRICES['latest-brief'],
                    )
                ),
            )

            @mcp.tool(name='intelligence_latest_brief',
                       description='Get the latest AI intelligence brief when the governed workflow is active. Price: $0.50 USDC on Base L2.')
            @brief_wrapper
            async def latest_brief(topic_filter: str = '') -> str:
                _audit_tool_call('latest-brief', PRICES['latest-brief'],
                                 agent_id='agent_quill', details={'topic_filter': topic_filter})
                result = get_latest_brief(topic_filter)
                return json.dumps(result)

            # Paid Tool 2: On-Demand Research
            research_wrapper = create_payment_wrapper(
                resource_server,
                accepts=make_requirements('on-demand-research', PRICES['on-demand-research']),
                resource=ResourceInfo(
                    url='mcp://meridian/intelligence/on-demand-research',
                    description='On-demand AI research on any topic ($2.00)',
                    mime_type='application/json',
                ),
                hooks=PaymentWrapperHooks(
                    on_after_settlement=_make_settlement_hook(
                        'on-demand-research',
                        'intelligence/on-demand-research',
                        PRICES['on-demand-research'],
                    )
                ),
            )

            @mcp.tool(name='intelligence_on_demand_research',
                       description='Research any topic with sourced findings. Delegated to Atlas. Live Loom cutover only for this route on this host. Depth: quick/standard/deep. Price: $2.00 USDC on Base L2.')
            @research_wrapper
            async def on_demand_research(topic: str, depth: str = 'standard') -> str:
                _audit_tool_call('on-demand-research', PRICES['on-demand-research'],
                                 agent_id='agent_atlas', details={'topic': topic, 'depth': depth})
                result = do_on_demand_research_route(topic, depth)
                return json.dumps(result)

            # Paid Tool 3: QA Verify
            qa_wrapper = create_payment_wrapper(
                resource_server,
                accepts=make_requirements('qa-verify', PRICES['qa-verify']),
                resource=ResourceInfo(
                    url='mcp://meridian/intelligence/qa-verify',
                    description='QA verification of any text ($1.00)',
                    mime_type='application/json',
                ),
                hooks=PaymentWrapperHooks(
                    on_after_settlement=_make_settlement_hook(
                        'qa-verify',
                        'intelligence/qa-verify',
                        PRICES['qa-verify'],
                    )
                ),
            )

            @mcp.tool(name='intelligence_qa_verify',
                       description='Verify text for factual accuracy, completeness, or readiness. Multi-agent QA pipeline. Price: $1.00 USDC on Base L2.')
            @qa_wrapper
            async def qa_verify(text: str, criteria: str = 'factual') -> str:
                _audit_tool_call('qa-verify', PRICES['qa-verify'],
                                 agent_id='agent_aegis', details={'criteria': criteria})
                result = do_qa_verify_route(text, criteria)
                return json.dumps(result)

            # Paid Tool 4: Weekly Digest
            weekly_wrapper = create_payment_wrapper(
                resource_server,
                accepts=make_requirements('weekly-digest', PRICES['weekly-digest']),
                resource=ResourceInfo(
                    url='mcp://meridian/intelligence/weekly-digest',
                    description='Top 5 AI developments this week ($1.50)',
                    mime_type='application/json',
                ),
                hooks=PaymentWrapperHooks(
                    on_after_settlement=_make_settlement_hook(
                        'weekly-digest',
                        'intelligence/weekly-digest',
                        PRICES['weekly-digest'],
                    )
                ),
            )

            @mcp.tool(name='intelligence_weekly_digest',
                       description='Top 5 AI/ML developments from the past 7 days, sourced and dated. Price: $1.50 USDC on Base L2.')
            @weekly_wrapper
            async def weekly_digest() -> str:
                _audit_tool_call('weekly-digest', PRICES['weekly-digest'],
                                 agent_id='agent_quill')
                result = get_weekly_digest()
                return json.dumps(result)

            # Paid Tool 5: Competitor Snapshot
            competitor_wrapper = create_payment_wrapper(
                resource_server,
                accepts=make_requirements('competitor-snapshot', PRICES['competitor-snapshot']),
                resource=ResourceInfo(
                    url='mcp://meridian/intelligence/competitor-snapshot',
                    description='Snapshot analysis of a specific AI company or product ($3.00)',
                    mime_type='application/json',
                ),
                hooks=PaymentWrapperHooks(
                    on_after_settlement=_make_settlement_hook(
                        'competitor-snapshot',
                        'intelligence/competitor-snapshot',
                        PRICES['competitor-snapshot'],
                    )
                ),
            )

            @mcp.tool(name='intelligence_competitor_snapshot',
                       description='Snapshot analysis of a specific AI company or product. Deep research pipeline. Price: $3.00 USDC on Base L2.')
            @competitor_wrapper
            async def competitor_snapshot(company: str) -> str:
                _audit_tool_call('competitor-snapshot', PRICES['competitor-snapshot'],
                                 agent_id='agent_atlas', details={'company': company})
                result = do_on_demand_research(
                    topic=f'Latest developments, products, and strategic moves of {company} in AI',
                    depth='deep'
                )
                result['type'] = 'competitor-snapshot'
                result['target'] = company
                return json.dumps(result)

            logger.info(f'x402 payment gating ENABLED. Wallet: {wallet_address}')

        except Exception as e:
            logger.error(f'x402 setup failed: {e}')
            raise RuntimeError(f'x402 payment gating failed to initialize: {e}') from e

    if free_mode:
        # Free mode — tools work without payment (for testing)
        @mcp.tool(name='intelligence_latest_brief',
                   description='[FREE/TEST] Get the latest AI intelligence brief.')
        async def latest_brief_free(topic_filter: str = '') -> str:
            return json.dumps(get_latest_brief(topic_filter))

        @mcp.tool(name='intelligence_on_demand_research',
                   description='[FREE/TEST] Research any topic with sourced findings.')
        async def on_demand_research_free(topic: str, depth: str = 'standard') -> str:
            return json.dumps(do_on_demand_research(topic, depth))

        @mcp.tool(name='intelligence_qa_verify',
                   description='[FREE/TEST] QA verification of any text.')
        async def qa_verify_free(text: str, criteria: str = 'factual') -> str:
            return json.dumps(do_qa_verify_route(text, criteria))

        @mcp.tool(name='intelligence_weekly_digest',
                   description='[FREE/TEST] Top 5 AI/ML developments from the past 7 days.')
        async def weekly_digest_free() -> str:
            return json.dumps(get_weekly_digest())

        @mcp.tool(name='intelligence_competitor_snapshot',
                   description='[FREE/TEST] Snapshot analysis of a specific AI company or product.')
        async def competitor_snapshot_free(company: str) -> str:
            result = do_on_demand_research(
                topic=f'Latest developments, products, and strategic moves of {company} in AI',
                depth='deep'
            )
            result['type'] = 'competitor-snapshot'
            result['target'] = company
            return json.dumps(result)

        logger.info('Running in FREE mode (no payment required)')

    # Info tool (always free)
    @mcp.tool(name='company_info',
               description='Get Meridian information, capabilities, and pricing.')
    async def company_info() -> str:
        wallet_addr = 'not configured'
        context = _resolve_mcp_context()
        try:
            wallet_addr = load_wallet_address()
        except Exception:
            pass

        return json.dumps({
            'company': 'Meridian',
            'tagline': 'Constitutional Operating System for Autonomous Institutions',
            'description': 'Meridian is a constitutional operating system for running AI agents as managed, governed, billable digital labor. Built on five primitives: Institution, Agent, Authority, Treasury, and Court.',
            'current_workflows': [
                'Competitive Intelligence — daily cited alerts, weekly briefs, battlecards (live)',
                'On-demand research — topic-driven sourced findings (live)',
                'QA verification — factual accuracy and claim checking (live)',
            ],
            'primitives': {
                'Institution': 'Charter-governed organizations with lifecycle management and policy defaults',
                'Agent': 'First-class managed entities with identity, scopes, budget, risk state, and economy participation',
                'Authority': 'Approval queues, delegations, and kill switch — who can act and when',
                'Treasury': 'Real-money accounting — balance, runway, reserve floor, spend tracking',
                'Court': 'Violation records, sanctions, and appeals — constitutional enforcement',
            },
            'platform_capabilities': [
                'Five constitutional primitives (Institution, Agent, Authority, Treasury, Court)',
                'Agent registry with identity, scopes, budget, risk state, and lifecycle',
                'Organization-scoped resources with charter and policy defaults',
                'Audit logging for every significant action',
                'Usage metering per org and per agent',
                'Authority approval queues and kill switch',
                'Court system with violations, sanctions, and appeals',
            ],
            'tools': {
                'intelligence_latest_brief': {
                    'price': '$0.50 USDC',
                    'description': 'Daily competitor intelligence alert with cited findings',
                },
                'intelligence_on_demand_research': {
                    'price': '$2.00 USDC',
                    'description': 'On-demand competitive research on any company or topic',
                },
                'intelligence_competitor_snapshot': {
                    'price': '$3.00 USDC',
                    'description': 'Battlecard-ready competitor snapshot: recent moves, pricing, product, talking points',
                },
                'intelligence_qa_verify': {
                    'price': '$1.00 USDC',
                    'description': 'QA verification of competitive claims or intelligence text',
                },
                'intelligence_weekly_digest': {
                    'price': '$1.50 USDC',
                    'description': 'Weekly competitive digest: top developments across tracked competitors',
                },
                'company_info': {
                    'price': 'FREE',
                    'description': 'Meridian capabilities and pricing',
                },
            },
            'payment': {
                'protocol': 'x402',
                'chain': 'Base L2',
                'asset': 'USDC',
                'wallet': wallet_addr,
            },
            'institution_scope': context,
            'sources_monitored': '30+ including provider blogs, changelogs, pricing pages, tech aggregators',
            'pipeline': '7-agent nightly pipeline with multi-stage QA verification',
        })

    return mcp


# ── Entry point ──────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description='Meridian MCP Server')
    parser.add_argument('--http', type=int, default=None, help='Run as HTTP server on this port')
    parser.add_argument('--free', action='store_true', help='Free mode (no payment required)')
    args = parser.parse_args()

    mcp = create_server(free_mode=args.free)
    context = _resolve_mcp_context()
    logger.info(f"Bound institution: {context['org_id']} via {context['source']} ({context['service_scope']})")

    if args.http:
        # HTTP/SSE transport
        mcp.settings.port = args.http
        mcp.settings.host = '127.0.0.1'
        logger.info(f'Starting MCP HTTP server on 127.0.0.1:{args.http}')
        mcp.run(transport='sse')
    else:
        # stdio transport (default for MCP)
        logger.info('Starting MCP server on stdio')
        mcp.run()


if __name__ == '__main__':
    main()
