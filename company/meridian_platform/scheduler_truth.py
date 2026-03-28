#!/usr/bin/env python3
"""
Scheduler truth helper with explicit separation of concerns.

`jobs.json` is configuration plus an embedded last-run cache. The per-job run
logs under `~/.meridian/cron/runs/*.jsonl` are the best source for the latest
recorded execution. Current host runtime health is a separate live check.

This helper reports those layers separately instead of flattening them into one
synthetic status record.

Usage:
  python3 scheduler_truth.py
  python3 scheduler_truth.py --job revenue-dashboard
  python3 scheduler_truth.py --job night-shift-deliver --json
"""
import argparse
import datetime as dt
import json
import os
import subprocess


CRON_DIR = os.path.expanduser('~/.meridian/cron')
JOBS_FILE = os.path.join(CRON_DIR, 'jobs.json')
RUNS_DIR = os.path.join(CRON_DIR, 'runs')


def _fmt_ms(ms):
    if not ms:
        return ''
    return dt.datetime.utcfromtimestamp(ms / 1000).strftime('%Y-%m-%dT%H:%M:%SZ')


def load_jobs():
    if not os.path.exists(JOBS_FILE):
        return []
    with open(JOBS_FILE) as f:
        return json.load(f).get('jobs', [])


def load_run_entries(job_id):
    path = os.path.join(RUNS_DIR, f'{job_id}.jsonl')
    if not os.path.exists(path):
        return []
    entries = []
    with open(path) as f:
        for raw in f:
            raw = raw.strip()
            if not raw:
                continue
            try:
                entries.append(json.loads(raw))
            except json.JSONDecodeError:
                continue
    return entries


def latest_run_entry(job_id):
    entries = load_run_entries(job_id)
    if not entries:
        return None
    return max(entries, key=lambda entry: entry.get('ts', 0))


def _runtime_ok():
    try:
        result = subprocess.run(
            ['loom', 'doctor', '--format', 'json'],
            capture_output=True,
            text=True,
            timeout=20,
        )
        return result.returncode == 0
    except Exception:
        return False


def _error_code(text):
    if not text:
        return ''
    try:
        parsed = json.loads(text)
    except Exception:
        return ''
    return parsed.get('detail', {}).get('code', '')


def _schedule_config(job):
    return {
        'enabled': job.get('enabled', False),
        'schedule': job.get('schedule', {}),
        'session_target': job.get('sessionTarget', ''),
        'wake_mode': job.get('wakeMode', ''),
    }


def _latest_run(job):
    state = job.get('state', {})
    latest = latest_run_entry(job['id'])
    latest_run = {
        'source': 'jobs_state',
        'run_status': state.get('lastRunStatus', ''),
        'delivered': bool(state.get('lastDelivered', False)),
        'delivery_status': state.get('lastDeliveryStatus', ''),
        'error': state.get('lastError') or state.get('lastDeliveryError') or '',
        'run_at_ms': state.get('lastRunAtMs'),
        'finished_at_ms': state.get('lastRunAtMs'),
        'summary': '',
    }

    if latest:
        latest_run.update({
            'source': 'run_log',
            'run_status': latest.get('status', latest_run['run_status']),
            'delivered': bool(latest.get('delivered', latest_run['delivered'])),
            'delivery_status': latest.get('deliveryStatus', latest_run['delivery_status']),
            'error': latest.get('error') or latest.get('deliveryError') or latest_run['error'],
            'run_at_ms': latest.get('runAtMs') or latest_run['run_at_ms'],
            'finished_at_ms': latest.get('ts') or latest_run['finished_at_ms'],
            'summary': latest.get('summary', ''),
        })

    latest_run['raw_delivered'] = latest_run['delivered']
    latest_run['raw_delivery_status'] = latest_run['delivery_status']
    latest_run['delivery_truth_note'] = ''
    summary = (latest_run.get('summary') or '').lower()
    blocked_summary = any(
        marker in summary
        for marker in (
            'blocked by constitutional preflight',
            'no brief',
            'cannot be treated as ready for delivery',
        )
    )
    if latest_run['run_status'] != 'ok' or latest_run['error'] or blocked_summary:
        latest_run['delivered'] = False
        latest_run['delivery_status'] = 'blocked'
        if latest_run['raw_delivered']:
            latest_run['delivery_truth_note'] = 'Raw run log marked delivered, but summary/error shows no real delivery.'

    latest_run['run_at'] = _fmt_ms(latest_run['run_at_ms'])
    latest_run['finished_at'] = _fmt_ms(latest_run['finished_at_ms'])
    latest_run['error_code'] = _error_code(latest_run['error'])
    return latest_run


def _contradictions(job, latest_run):
    state = job.get('state', {})
    contradictions = []
    if latest_run['source'] == 'run_log':
        if state.get('lastRunStatus') and state.get('lastRunStatus') != latest_run['run_status']:
            contradictions.append(
                f"jobs.json lastRunStatus={state.get('lastRunStatus')} but run log status={latest_run['run_status']}"
            )
        if state.get('lastDelivered') is not None and bool(state.get('lastDelivered')) != latest_run['delivered']:
            contradictions.append(
                f"jobs.json lastDelivered={bool(state.get('lastDelivered'))} but run log delivered={latest_run['delivered']}"
            )
        if latest_run.get('delivery_truth_note'):
            contradictions.append(latest_run['delivery_truth_note'])
        state_error = state.get('lastError') or state.get('lastDeliveryError') or ''
        if state_error and state_error != latest_run['error']:
            contradictions.append('jobs.json last error differs from latest run log error')
    return contradictions


def job_view(job, runtime_ok=False):
    latest_run = _latest_run(job)
    note = ''
    if latest_run['error_code'] == 'deactivated_workspace' and runtime_ok:
        note = 'historical deactivated_workspace failure; current host runtime is healthy'
    return {
        'id': job['id'],
        'name': job.get('name', job['id']),
        'schedule_config': _schedule_config(job),
        'latest_run': latest_run,
        'current_runtime': {
            'loom_health_ok': runtime_ok,
        },
        'contradictions': _contradictions(job, latest_run),
        'note': note,
    }


def all_job_truth():
    runtime_ok = _runtime_ok()
    return {
        'current_runtime': {
            'loom_health_ok': runtime_ok,
        },
        'jobs': [job_view(job, runtime_ok=runtime_ok) for job in load_jobs()],
    }


def job_truth(job_name_or_id, runtime_ok=None):
    runtime_ok = _runtime_ok() if runtime_ok is None else runtime_ok
    for job in load_jobs():
        if job['id'] == job_name_or_id or job.get('name') == job_name_or_id:
            return job_view(job, runtime_ok=runtime_ok)
    return None


def _print_table(rows, runtime_ok):
    runtime_label = 'healthy' if runtime_ok else 'unhealthy'
    print(f'Current runtime: {runtime_label}')
    print(f"{'Job':<22} {'Run':<8} {'Delivery':<12} {'Source':<8} {'Finished'}")
    print(f"{'-'*22} {'-'*8} {'-'*12} {'-'*8} {'-'*20}")
    for row in rows:
        latest_run = row['latest_run']
        print(
            f"{row['name']:<22} {latest_run['run_status'] or '-':<8} "
            f"{latest_run['delivery_status'] or '-':<12} {latest_run['source']:<8} {latest_run['finished_at'] or '-'}"
        )
        if row.get('note'):
            print(f"  · {row['note']}")
        for contradiction in row['contradictions']:
            print(f"  ! {contradiction}")


def main():
    parser = argparse.ArgumentParser(description='Canonical scheduler truth from Meridian run logs')
    parser.add_argument('--job', help='Job name or id')
    parser.add_argument('--json', action='store_true', help='Output JSON')
    args = parser.parse_args()

    if args.job:
        runtime_ok = _runtime_ok()
        data = job_truth(args.job, runtime_ok=runtime_ok)
        if data is None:
            raise SystemExit(f'Unknown job: {args.job}')
    else:
        data = all_job_truth()

    if args.json:
        print(json.dumps(data, indent=2))
    else:
        if isinstance(data, dict) and 'jobs' in data:
            _print_table(data['jobs'], data['current_runtime']['loom_health_ok'])
        else:
            _print_table([data], data['current_runtime']['loom_health_ok'])


if __name__ == '__main__':
    main()
