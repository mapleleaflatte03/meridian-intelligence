#!/usr/bin/env python3
"""
One-command operational readiness check for Meridian.

Usage:
  python3 company/meridian_platform/readiness.py
  python3 company/meridian_platform/readiness.py --json
"""
import argparse
import datetime as dt
import glob
import importlib.util
import json
import os
import shlex
import shutil
import subprocess
import sys

from treasury import treasury_snapshot
from organizations import load_orgs
import status_surface


PLATFORM_DIR = os.path.dirname(os.path.abspath(__file__))
COMPANY_DIR = os.path.dirname(PLATFORM_DIR)
if COMPANY_DIR not in sys.path:
    sys.path.insert(0, COMPANY_DIR)
WORKSPACE = os.path.dirname(COMPANY_DIR)
NIGHT_SHIFT_DIR = os.path.join(WORKSPACE, "night-shift")
SUBSCRIPTIONS_PY = os.path.join(COMPANY_DIR, "subscriptions.py")
CI_VERTICAL_PY = os.path.join(PLATFORM_DIR, "ci_vertical.py")

_subs_spec = importlib.util.spec_from_file_location(
    'company_subscriptions', os.path.join(COMPANY_DIR, 'subscriptions.py')
)
_subs_mod = importlib.util.module_from_spec(_subs_spec)
_subs_spec.loader.exec_module(_subs_mod)
internal_test_ids = _subs_mod.internal_test_ids

_phase_spec = importlib.util.spec_from_file_location(
    'company_phase_machine', os.path.join(COMPANY_DIR, 'phase_machine.py')
)
_phase_mod = importlib.util.module_from_spec(_phase_spec)
_phase_spec.loader.exec_module(_phase_mod)

_mcp_spec = importlib.util.spec_from_file_location(
    'company_mcp_server', os.path.join(COMPANY_DIR, 'mcp_server.py')
)
_mcp_mod = importlib.util.module_from_spec(_mcp_spec)
_mcp_spec.loader.exec_module(_mcp_mod)


def _run(cmd, cwd=WORKSPACE, timeout=30):
    try:
        result = subprocess.run(
            cmd,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return {
            "ok": result.returncode == 0,
            "returncode": result.returncode,
            "stdout": result.stdout.strip(),
            "stderr": result.stderr.strip(),
        }
    except subprocess.TimeoutExpired:
        return {
            "ok": False,
            "returncode": 124,
            "stdout": "",
            "stderr": f"timeout after {timeout}s",
        }


def _latest_brief():
    briefs = sorted(glob.glob(os.path.join(NIGHT_SHIFT_DIR, "brief-*.md")))
    if not briefs:
        return {"exists": False, "path": None, "date": None}
    path = briefs[-1]
    return {
        "exists": True,
        "path": path,
        "date": os.path.basename(path).replace("brief-", "").replace(".md", ""),
    }


def _delivery_targets():
    result = _run([sys.executable, SUBSCRIPTIONS_PY, "deliver-list"], timeout=15)
    targets = []
    if result["stdout"]:
        targets = [line.strip() for line in result["stdout"].splitlines() if line.strip()]
    internal_ids = internal_test_ids()
    external_targets = [tid for tid in targets if tid not in internal_ids]
    return {
        "ok": result["ok"],
        "count": len(external_targets),
        "internal_test_count": len([tid for tid in targets if tid in internal_ids]),
        "targets": targets,
        "external_targets": external_targets,
        "stderr": result["stderr"],
    }


def _founding_org_id():
    for oid, org in load_orgs().get('organizations', {}).items():
        if org.get('slug') == 'meridian':
            return oid
    return None


def _parse_json_output(result):
    if not result.get('ok'):
        return None
    raw = (result.get('stdout') or '').strip()
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


def _preferred_loom_bin(runtime_env=None):
    env = runtime_env or os.environ
    candidates = [
        (env.get('MERIDIAN_LOOM_BIN') or '').strip(),
        '/home/ubuntu/.local/share/meridian-loom/current/bin/loom',
        '/root/.local/share/meridian-loom/current/bin/loom',
        shutil.which('loom') or '',
    ]
    for candidate in candidates:
        if candidate and os.path.exists(candidate):
            return candidate
    return candidates[0] or 'loom'


def _preferred_loom_root(runtime_env=None):
    env = runtime_env or os.environ
    candidates = [
        (env.get('MERIDIAN_LOOM_ROOT') or '').strip(),
        '/home/ubuntu/.local/share/meridian-loom/runtime/default',
        '/root/.local/share/meridian-loom/runtime/default',
    ]
    for candidate in candidates:
        if candidate and os.path.exists(candidate):
            return candidate
    return candidates[0] or '/home/ubuntu/.local/share/meridian-loom/runtime/default'


def _loom_cmd(runtime_env, *parts):
    return [_preferred_loom_bin(runtime_env), *parts, '--root', _preferred_loom_root(runtime_env), '--format', 'json']


def _health_ok(result):
    payload = _parse_json_output(result) or {}
    status = (payload.get('status') or '').strip().lower()
    return status == 'healthy'


def _service_probe_ok(result):
    payload = _parse_json_output(result)
    if isinstance(payload, dict):
        return bool(payload.get('running')) and payload.get('service_status') == 'running' and payload.get('health') == 'healthy'
    if not result["ok"]:
        return False
    lines = [line.strip() for line in result["stdout"].splitlines() if line.strip()]
    if not lines:
        return False
    return lines[-1] in {"SERVICE_OK", "HEARTBEAT_OK"}


def _runtime_env_defaults():
    env = dict(os.environ)
    env_file = env.get('MERIDIAN_MCP_RUNTIME_ENV_FILE') or '/etc/default/meridian-mcp-runtime'
    if not os.path.exists(env_file):
        return env
    with open(env_file) as f:
        for raw in f:
            line = raw.strip()
            if not line or line.startswith('#') or '=' not in line:
                continue
            key, value = line.split('=', 1)
            env.setdefault(key.strip(), shlex.split(value.strip())[0] if value.strip() else '')
    return env


def _sudo_loom_research_preflight(capability_name: str, runtime_env: dict) -> dict:
    preflight = {
        'ok': False,
        'runtime': 'loom',
        'capability_name': capability_name,
        'errors': [],
    }
    if not capability_name:
        preflight['errors'].append('research capability is not configured')
        return preflight

    loom_bin = (runtime_env.get('MERIDIAN_LOOM_BIN') or '/home/ubuntu/.local/share/meridian-loom/current/bin/loom').strip()
    loom_root = (runtime_env.get('MERIDIAN_LOOM_ROOT') or '/home/ubuntu/.local/share/meridian-loom/runtime/default').strip()

    service_cmd = ['sudo', '-n', loom_bin, 'service', 'status', '--root', loom_root, '--format', 'json']
    capability_cmd = ['sudo', '-n', loom_bin, 'capability', 'show', '--root', loom_root, '--name', capability_name, '--format', 'json']

    service = _run(service_cmd, timeout=15)
    if not service['ok']:
        message = service['stderr'] or service['stdout'] or 'unknown error'
        preflight['errors'].append(f'loom service status failed: {message[:500]}')
    else:
        try:
            service_payload = json.loads(service['stdout'])
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

    capability = _run(capability_cmd, timeout=15)
    if not capability['ok']:
        message = capability['stderr'] or capability['stdout'] or 'unknown error'
        preflight['errors'].append(f'loom capability show failed: {message[:500]}')
    else:
        try:
            capability_payload = json.loads(capability['stdout'])
        except json.JSONDecodeError:
            preflight['errors'].append('loom capability show returned non-JSON output')
        else:
            preflight['capability'] = capability_payload
            if not capability_payload.get('enabled', False):
                preflight['errors'].append('loom capability is disabled')
            if capability_payload.get('verification_status') != 'verified':
                preflight['errors'].append(f"loom capability verification={capability_payload.get('verification_status', '')}")
            if capability_payload.get('promotion_state') != 'promoted':
                preflight['errors'].append(f"loom capability promotion={capability_payload.get('promotion_state', '')}")

    preflight['ok'] = not preflight['errors']
    return preflight


def _route_cutovers():
    runtime_env = _runtime_env_defaults()
    route_override = runtime_env.get('MERIDIAN_INTELLIGENCE_ON_DEMAND_RESEARCH_RUNTIME')
    research_runtime = runtime_env.get('MERIDIAN_INTELLIGENCE_RESEARCH_RUNTIME')
    exec_runtime = runtime_env.get('MERIDIAN_INTELLIGENCE_EXEC_RUNTIME') or 'loom'
    requested_runtime = _mcp_mod._normalize_runtime(route_override or research_runtime or exec_runtime)
    fallback_enabled = (runtime_env.get('MERIDIAN_INTELLIGENCE_ON_DEMAND_RESEARCH_ALLOW_FALLBACK') or '').strip().lower() in {'1', 'true', 'yes', 'on'}
    capability_name = (runtime_env.get('MERIDIAN_LOOM_RESEARCH_CAPABILITY') or '').strip()
    route = {
        'route': 'intelligence_on_demand_research',
        'owner': 'loom' if requested_runtime == 'loom' else 'legacy',
        'requested_runtime': requested_runtime,
        'runtime_source': 'route_override' if route_override else 'research_runtime_inherit',
        'fallback_enabled': fallback_enabled,
        'loom_capability_name': capability_name,
    }
    transcript_bits = [
        'route=intelligence_on_demand_research',
        f'requested={requested_runtime}',
        f"owner={'loom' if requested_runtime == 'loom' else 'legacy'}",
        f"fallback={'on' if fallback_enabled else 'off'}",
        f"source={'route_override' if route_override else 'research_runtime_inherit'}",
    ]
    if capability_name:
        transcript_bits.append(f'capability={capability_name}')
    if requested_runtime == 'loom':
        if os.geteuid() == 0:
            original = {}
            for key, value in runtime_env.items():
                if key.startswith('MERIDIAN_') or key == 'LOOM_SERVICE_TOKEN':
                    original[key] = os.environ.get(key)
                    os.environ[key] = value
            try:
                route['loom_preflight'] = _mcp_mod._loom_research_preflight(capability_name)
            finally:
                for key, value in original.items():
                    if value is None:
                        os.environ.pop(key, None)
                    else:
                        os.environ[key] = value
        else:
            route['loom_preflight'] = _sudo_loom_research_preflight(capability_name, runtime_env)
        preflight = route.get('loom_preflight')
        if isinstance(preflight, dict):
            transcript_bits.append(f"preflight={'ok' if preflight.get('ok') else 'blocked'}")
            preflight_capability = (preflight.get('capability_name') or '').strip()
            if preflight_capability:
                transcript_bits.append(f'preflight_capability={preflight_capability}')
            errors = preflight.get('errors') or []
            if errors:
                transcript_bits.append(f"preflight_errors={len(errors)}")
    else:
        transcript_bits.append('preflight=skipped')
    route['transcript'] = ' | '.join(transcript_bits)
    return {'intelligence_on_demand_research': route}


def collect():
    org_id = _founding_org_id()
    runtime_env = _runtime_env_defaults()
    runtime_health = _run(_loom_cmd(runtime_env, 'health'), timeout=20)
    service_probe = _run(_loom_cmd(runtime_env, 'service', 'status'), timeout=20)
    preflight = _run([sys.executable, CI_VERTICAL_PY, "preflight"], timeout=20)
    treasury = treasury_snapshot(org_id)
    phase_num, phase_details = _phase_mod.evaluate(org_id)
    brief = _latest_brief()
    targets = _delivery_targets()
    persistence = status_surface.persistence_snapshot(org_id)
    observability = status_surface.observability_snapshot(org_id)

    service_probe_ok = _service_probe_ok(service_probe)
    runtime_ok = _health_ok(runtime_health) and service_probe_ok
    preflight_ok = preflight["ok"]
    treasury_blocked = not treasury.get("above_reserve", False)

    if not runtime_ok:
        verdict = "ENGINEERING_BLOCKED_RUNTIME"
    elif treasury_blocked:
        verdict = "OWNER_BLOCKED_TREASURY"
    elif phase_num < 4:
        verdict = "PHASE_BLOCKED_AUTOMATION"
    elif not preflight_ok:
        verdict = "CONSTITUTION_BLOCKED_PREFLIGHT"
    elif not brief["exists"]:
        verdict = "READY_TO_RUN_CYCLE"
    else:
        verdict = "READY_FOR_CONTROLLED_DELIVERY_CHECK"

    return {
        "checked_at": dt.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "verdict": verdict,
        "runtime": {
            "health_ok": _health_ok(runtime_health),
            "service_probe_ok": service_probe_ok,
            "service_probe_output": service_probe["stdout"],
            "health_stderr": runtime_health["stderr"],
            "service_probe_stderr": service_probe["stderr"],
        },
        "treasury": {
            "balance_usd": round(treasury["balance_usd"], 2),
            "reserve_floor_usd": round(treasury["reserve_floor_usd"], 2),
            "runway_usd": round(treasury["runway_usd"], 2),
            "blocked": treasury_blocked,
            "shortfall_usd": round(treasury["shortfall_usd"], 2),
            "customer_revenue_usd": round(treasury.get("total_revenue_usd", 0.0), 2),
            "support_received_usd": round(treasury.get("support_received_usd", 0.0), 2),
            "owner_capital_usd": round(treasury.get("owner_capital_usd", 0.0), 2),
        },
        "phase": {
            "number": phase_num,
            "name": phase_details["name"],
            "next_phase": phase_details.get("next_phase"),
            "next_phase_name": phase_details.get("next_phase_name"),
            "next_unlock": phase_details.get("next_unlock"),
        },
        "preflight": {
            "ok": preflight_ok,
            "returncode": preflight["returncode"],
            "summary": preflight["stdout"].splitlines()[-1] if preflight["stdout"] else "",
        },
        "route_cutovers": _route_cutovers(),
        "brief": brief,
        "delivery_targets": targets,
        "persistence": persistence,
        "observability": observability,
    }


def print_report(report):
    print(f"Meridian readiness — {report['checked_at']}")
    print(f"Verdict: {report['verdict']}")
    print("")
    print(
        "Runtime: "
        + ("OK" if report["runtime"]["health_ok"] and report["runtime"]["service_probe_ok"] else "BLOCKED")
    )
    print(
        "Runtime control probe: "
        + ("OK" if report["runtime"]["service_probe_ok"] else "BLOCKED")
    )
    print(
        "Treasury: "
        + (
            f"BLOCKED runway ${report['treasury']['runway_usd']:.2f}"
            if report["treasury"]["blocked"]
            else f"OK runway ${report['treasury']['runway_usd']:.2f}"
        )
    )
    print(
        "Treasury mix: "
        f"customer ${report['treasury']['customer_revenue_usd']:.2f} | "
        f"support ${report['treasury']['support_received_usd']:.2f} | "
        f"owner capital ${report['treasury']['owner_capital_usd']:.2f}"
    )
    print(f"Phase: {report['phase']['number']} — {report['phase']['name']}")
    print(
        "Preflight: "
        + ("OK" if report["preflight"]["ok"] else f"BLOCKED ({report['preflight']['summary']})")
    )
    route = report.get('route_cutovers', {}).get('intelligence_on_demand_research', {})
    if route:
        print(
            "On-demand research route: "
            f"owner={route.get('owner', '')} source={route.get('runtime_source', '')} "
            f"fallback={'on' if route.get('fallback_enabled') else 'off'}"
        )
        preflight = route.get('loom_preflight')
        if isinstance(preflight, dict):
            print(
                "On-demand research Loom preflight: "
                + ("OK" if preflight.get('ok') else f"BLOCKED ({'; '.join(preflight.get('errors', []))})")
            )
            import_metadata = preflight.get('normalized_import_metadata')
            if isinstance(import_metadata, dict):
                support_text = 'OK' if import_metadata.get('supported') else f"BLOCKED ({import_metadata.get('unsupported_reason', '')})"
                skill_slug = import_metadata.get('skill_slug') or 'unknown'
                print(
                    "On-demand research Loom import metadata: "
                    f"{support_text} skill={skill_slug} worker={import_metadata.get('worker_entry', '')}"
                )
        transcript = route.get('transcript')
        if transcript:
            print(f"On-demand research transcript: {transcript}")
    if report["brief"]["exists"]:
        print(f"Latest brief: {report['brief']['date']} ({report['brief']['path']})")
    else:
        print("Latest brief: none")
    print(f"Deliverable targets today: {report['delivery_targets']['count']}")
    print(f"Internal test targets today: {report['delivery_targets']['internal_test_count']}")
    persistence = report.get('persistence', {})
    db = persistence.get('db', {})
    observability = report.get('observability', {})
    metrics = observability.get('metrics', {})
    slo = observability.get('slo', {})
    alerting_run = observability.get('alerting', {}) if isinstance(observability, dict) else {}
    alert_log = observability.get('alert_log', {}) if isinstance(observability, dict) else {}
    alert_queue = observability.get('alert_queue', {}) if isinstance(observability, dict) else {}
    objectives = slo.get('objectives', []) if isinstance(slo, dict) else []
    alerts = slo.get('alerts', []) if isinstance(slo, dict) else []
    print(
        'Persistence: '
        + f"{persistence.get('backend', 'unknown')} / DB {db.get('status', 'unknown')}"
    )
    print(
        'Observability: '
        + f"audit {metrics.get('audit', {}).get('total_events', 0)} events | "
        + f"metering ${metrics.get('metering', {}).get('total_cost_usd', 0.0):.2f} month | "
        + f"SLO {slo.get('status', 'unknown')}"
    )
    if slo:
        print(
            'SLO policy: '
            + f"{slo.get('policy_name', 'unknown')} | "
            + f"healthy {slo.get('healthy_objective_count', 0)}/{slo.get('objective_count', len(objectives))} | "
            + f"alerts {slo.get('alert_count', len(alerts))}"
        )
        if alerts:
            first_alert = alerts[0]
            print(
                'SLO alert: '
                + f"{first_alert.get('objective', 'unknown')} — {first_alert.get('message', '')}"
            )
    if alerting_run:
        print(
            'Alert log: '
            + f"persisted {alerting_run.get('event_count', 0)} event(s) | "
            + f"deliveries {alerting_run.get('delivery_count', 0)} | "
            + f"active {alerting_run.get('active_alert_count', 0)}"
        )
    if alert_log:
        print(
            'Alert surface: '
            + f"recent {alert_log.get('event_count', 0)} event(s) | "
            + f"deliveries {alert_log.get('delivery_count', 0)}"
        )
    if alert_queue:
        print(
            'Alert queue: '
            + f"queued {alert_queue.get('queue_count', 0)} | "
            + f"pending {alert_queue.get('pending_delivery_count', 0)} | "
            + f"delivered {alert_queue.get('delivered_count', 0)}"
        )

    if report["verdict"] == "ENGINEERING_BLOCKED_RUNTIME":
        print("Next action: clear Loom health or service-state blockers before attempting pipeline execution.")
    elif report["verdict"] == "OWNER_BLOCKED_TREASURY":
        print(
            "Next action: owner must recapitalize treasury or lower reserve floor before any budget-gated phase can run."
        )
    elif report["verdict"] == "PHASE_BLOCKED_AUTOMATION":
        print(
            "Next action: do not treat the system as automation-ready yet; the institution has not reached treasury-cleared automation."
        )
    elif report["verdict"] == "CONSTITUTION_BLOCKED_PREFLIGHT":
        print("Next action: clear the current constitutional blocker before attempting a controlled cycle.")
    elif report["verdict"] == "READY_TO_RUN_CYCLE":
        print("Next action: trigger one controlled pipeline cycle to generate a fresh brief.")
    else:
        print("Next action: run dry-run delivery checks, then one controlled delivery cycle.")


def main():
    parser = argparse.ArgumentParser(description="Operational readiness check for Meridian")
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON")
    args = parser.parse_args()

    report = collect()
    if args.json:
        print(json.dumps(report, indent=2))
        return
    print_report(report)


if __name__ == "__main__":
    main()
