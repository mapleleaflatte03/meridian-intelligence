#!/usr/bin/env python3
"""
Competitive Intelligence Vertical — mapped onto Constitutional Primitives.

This module makes the CI pipeline a first-class constitutional workflow:
- Runs within one Institution (Meridian)
- Uses specific Agents (Atlas, Quill, Aegis, Sentinel, Forge, Pulse, Leviathann)
- Checks Authority before executing each phase
- Constrains against Treasury budget
- Records Court violations on failures

Usage:
  python3 ci_vertical.py status         # Show CI vertical state mapped to primitives
  python3 ci_vertical.py preflight      # Check all constitutional gates before pipeline run
  python3 ci_vertical.py post-mortem    # Analyze last pipeline run and file court records
"""
import argparse
import datetime
import glob
import json
import os
import sys

PLATFORM_DIR = os.path.dirname(os.path.abspath(__file__))
WORKSPACE = os.path.dirname(os.path.dirname(PLATFORM_DIR))
NS_DIR = os.path.join(WORKSPACE, 'night-shift')
ECONOMY_DIR = os.path.join(WORKSPACE, 'economy')

sys.path.insert(0, PLATFORM_DIR)

from organizations import load_orgs
from agent_registry import load_registry
from authority import check_authority, is_kill_switch_engaged, get_sprint_lead
from treasury import get_balance, get_runway, check_budget
from court import file_violation, get_violations, get_restrictions
from audit import log_event


def _now():
    return datetime.datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ')


def _get_org():
    orgs = load_orgs()
    for oid, org in orgs['organizations'].items():
        if org.get('slug') == 'meridian':
            return oid, org
    return None, None


# ── Pipeline phase → Agent mapping ───────────────────────────────────────────

PIPELINE_PHASES = [
    {'phase': 'research',    'agent': 'atlas',     'action': 'execute', 'description': 'Fetch 30+ sources, extract findings'},
    {'phase': 'write',       'agent': 'quill',     'action': 'execute', 'description': 'Write cited intelligence brief'},
    {'phase': 'qa_sentinel', 'agent': 'sentinel',  'action': 'review',  'description': 'Verify sources, check contradictions'},
    {'phase': 'qa_aegis',    'agent': 'aegis',     'action': 'review',  'description': 'PASS/FAIL acceptance gate'},
    {'phase': 'execute',     'agent': 'forge',     'action': 'execute', 'description': 'Execute bounded improvement task'},
    {'phase': 'compress',    'agent': 'pulse',     'action': 'execute', 'description': 'Compress context for delivery'},
    {'phase': 'deliver',     'agent': 'main',      'action': 'execute', 'description': 'Deliver brief to subscribers'},
    {'phase': 'score',       'agent': 'main',      'action': 'execute', 'description': 'Auto-score agents, advance epoch'},
]


def status():
    """Show CI vertical mapped to five constitutional primitives."""
    org_id, org = _get_org()
    reg = load_registry()
    lead_id, lead_auth = get_sprint_lead()

    print(f"\n{'='*60}")
    print(f"COMPETITIVE INTELLIGENCE VERTICAL — Constitutional Map")
    print(f"{'='*60}")

    # Institution
    print(f"\n--- INSTITUTION ---")
    print(f"  Name:      {org['name']}")
    print(f"  Charter:   {org.get('charter', '(not set)')[:80] or '(not set)'}")
    print(f"  Lifecycle: {org.get('lifecycle_state', '?')}")
    print(f"  Policies:  auto_sanctions={org.get('policy_defaults',{}).get('auto_sanctions_enabled', '?')}")

    # Agents in this vertical
    print(f"\n--- AGENTS (CI Vertical) ---")
    print(f"  {'Phase':<14} {'Agent':<12} {'Role':<12} {'REP':>4} {'AUTH':>4} {'Risk':<10} {'Restrictions'}")
    print(f"  {'-'*80}")
    for phase in PIPELINE_PHASES:
        ekey = phase['agent']
        for a in reg['agents'].values():
            if a.get('economy_key') == ekey:
                restrictions = get_restrictions(ekey)
                lead_marker = ' *LEAD*' if ekey == lead_id else ''
                print(f"  {phase['phase']:<14} {a['name']:<12} {a['role']:<12} "
                      f"{a['reputation_units']:>4} {a['authority_units']:>4} "
                      f"{a.get('risk_state','?'):<10} {', '.join(restrictions) or '-'}{lead_marker}")
                break

    # Authority gates
    print(f"\n--- AUTHORITY (Pipeline Gates) ---")
    ks = is_kill_switch_engaged()
    print(f"  Kill switch: {'ENGAGED (pipeline blocked)' if ks else 'off'}")
    print(f"  Sprint lead: {lead_id or 'NONE'} (AUTH={lead_auth})")
    for phase in PIPELINE_PHASES:
        allowed, reason = check_authority(phase['agent'], phase['action'])
        status_str = 'PASS' if allowed else f'BLOCKED: {reason}'
        print(f"  {phase['phase']:<14} {phase['agent']:<10} {phase['action']:<8} -> {status_str}")

    # Treasury
    print(f"\n--- TREASURY (Budget Constraints) ---")
    balance = get_balance()
    runway = get_runway()
    print(f"  Balance: ${balance:.2f} | Runway: ${runway:.2f}")
    # Check budget for the pipeline agents
    for ekey in ['atlas', 'quill', 'forge']:
        for a in reg['agents'].values():
            if a.get('economy_key') == ekey:
                allowed, reason = check_budget(a['id'], a['budget']['max_per_run_usd'])
                status_str = 'OK' if allowed else f'BLOCKED: {reason}'
                print(f"  {a['name']:<12} budget=${a['budget']['max_per_run_usd']:.2f}/run -> {status_str}")
                break

    # Court
    print(f"\n--- COURT (Active Enforcement) ---")
    open_v = get_violations(status='open') + get_violations(status='sanctioned')
    if open_v:
        for v in open_v:
            print(f"  {v['id']} agent={v['agent_id']} type={v['type']} sev={v['severity']} "
                  f"sanction={v.get('sanction_applied', '-')}")
    else:
        print(f"  No active violations affecting pipeline")

    # Latest artifacts
    print(f"\n--- LATEST ARTIFACTS ---")
    briefs = sorted(glob.glob(os.path.join(NS_DIR, 'brief-*.md')))
    reports = sorted(glob.glob(os.path.join(NS_DIR, 'reports', '*.md')))
    findings = sorted(glob.glob(os.path.join(NS_DIR, 'findings-*.md')))
    print(f"  Briefs:   {len(briefs)} (latest: {os.path.basename(briefs[-1]) if briefs else 'none'})")
    print(f"  Reports:  {len(reports)} (latest: {os.path.basename(reports[-1]) if reports else 'none'})")
    print(f"  Findings: {len(findings)} (latest: {os.path.basename(findings[-1]) if findings else 'none'})")


def preflight():
    """Check all constitutional gates before pipeline run. Returns 0 if clear, 1 if blocked."""
    org_id, org = _get_org()
    blocked = False

    print(f"CI Vertical Preflight — {_now()}")

    # 1. Institution lifecycle
    lifecycle = org.get('lifecycle_state', 'active')
    if lifecycle != 'active':
        print(f"  BLOCKED: Institution lifecycle is '{lifecycle}', not 'active'")
        blocked = True
    else:
        print(f"  OK: Institution active")

    # 2. Kill switch
    if is_kill_switch_engaged():
        print(f"  BLOCKED: Kill switch engaged")
        blocked = True
    else:
        print(f"  OK: Kill switch off")

    # 3. Authority for each phase
    for phase in PIPELINE_PHASES:
        allowed, reason = check_authority(phase['agent'], phase['action'])
        if not allowed:
            print(f"  BLOCKED: {phase['phase']} — {reason}")
            blocked = True
        else:
            print(f"  OK: {phase['phase']} ({phase['agent']} may {phase['action']})")

    # 4. Treasury
    runway = get_runway()
    if runway < -100:  # Allow some deficit but flag extreme
        print(f"  WARN: Treasury runway severely negative (${runway:.2f})")

    # 5. Court — check for remediation_only agents in the pipeline
    reg = load_registry()
    for phase in PIPELINE_PHASES:
        for a in reg['agents'].values():
            if a.get('economy_key') == phase['agent']:
                if a.get('risk_state') == 'suspended':
                    print(f"  BLOCKED: {a['name']} is suspended")
                    blocked = True
                break

    if blocked:
        print(f"\nPREFLIGHT: BLOCKED — pipeline should not run")
        log_event(org_id, 'system', 'ci_preflight', outcome='blocked',
                  details={'reason': 'constitutional gates failed'})
        return 1
    else:
        print(f"\nPREFLIGHT: CLEAR — pipeline may proceed")
        log_event(org_id, 'system', 'ci_preflight', outcome='success')
        return 0


def post_mortem():
    """Analyze last pipeline run and file court records for failures."""
    org_id, _ = _get_org()

    # Read latest report
    reports = sorted(glob.glob(os.path.join(NS_DIR, 'reports', '*.md')),
                     key=os.path.getmtime)
    if not reports:
        print("No reports found for post-mortem.")
        return

    with open(reports[-1]) as f:
        report = f.read()
    lo = report.lower()
    report_name = os.path.basename(reports[-1])

    print(f"Post-mortem for: {report_name}")
    violations_filed = 0

    # Check for Sentinel failure
    if 'sentinel' in lo and any(kw in lo for kw in ['sentinel: fail', 'sentinel fail', 'fail-to-parse']):
        vid = file_violation('sentinel', org_id, 'weak_output', 2,
                             f'Sentinel QA failed in {report_name}',
                             'CLAUDE.md section 9.1')
        print(f"  Filed: {vid} (Sentinel QA failure, severity 2)")
        violations_filed += 1

    # Check for delivery failure
    if any(kw in lo for kw in ['deliver_fail', 'delivery failed', 'delivery error']):
        vid = file_violation('main', org_id, 'weak_output', 2,
                             f'Delivery failed in {report_name}',
                             'CLAUDE.md section 9.1')
        print(f"  Filed: {vid} (delivery failure, severity 2)")
        violations_filed += 1

    # Check for Aegis rejection
    if 'aegis' in lo and any(kw in lo for kw in ['aegis: reject', 'aegis reject']):
        vid = file_violation('quill', org_id, 'rejected_output', 3,
                             f'Brief rejected by Aegis in {report_name}',
                             'CLAUDE.md section 9.2')
        print(f"  Filed: {vid} (Quill output rejected, severity 3)")
        violations_filed += 1

    if violations_filed == 0:
        print("  No court-worthy failures detected.")

    log_event(org_id, 'system', 'ci_post_mortem', outcome='success',
              details={'report': report_name, 'violations_filed': violations_filed})


def main():
    p = argparse.ArgumentParser(description='CI Vertical — constitutional primitive mapping')
    sub = p.add_subparsers(dest='command')
    sub.add_parser('status')
    sub.add_parser('preflight')
    sub.add_parser('post-mortem')
    args = p.parse_args()

    if args.command == 'status':
        status()
    elif args.command == 'preflight':
        rc = preflight()
        sys.exit(rc)
    elif args.command == 'post-mortem':
        post_mortem()
    else:
        p.print_help()


if __name__ == '__main__':
    main()
