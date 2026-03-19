#!/usr/bin/env python3
"""
Court primitive for Meridian Constitutional OS.

Composes over economy/sanctions.py — adds violation records, appeals,
and structured policy enforcement per CLAUDE.md sections 9-10.

Usage:
  python3 court.py file --agent <id> --org <org> --type <type> --severity <1-6> --evidence "..."
  python3 court.py violations [--agent <id>] [--status open]
  python3 court.py resolve --violation_id <id> --note "..."
  python3 court.py appeal --violation_id <id> --agent <id> --grounds "..."
  python3 court.py decide-appeal --appeal_id <id> --decision upheld|overturned|dismissed --by <who>
  python3 court.py record --agent <id>
  python3 court.py auto-review
  python3 court.py show
"""
import argparse
import datetime
import json
import os
import sys
import uuid

PLATFORM_DIR = os.path.dirname(os.path.abspath(__file__))
WORKSPACE = os.path.dirname(os.path.dirname(PLATFORM_DIR))
ECONOMY_DIR = os.path.join(WORKSPACE, 'economy')
RECORDS_FILE = os.path.join(PLATFORM_DIR, 'court_records.json')

# Import economy sanctions module (avoid name collision)
import importlib.util
_spec = importlib.util.spec_from_file_location('econ_sanctions', os.path.join(ECONOMY_DIR, 'sanctions.py'))
_econ_sanctions_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_econ_sanctions_mod)
_econ_apply_sanction = _econ_sanctions_mod.apply_sanction
_econ_lift_sanction = _econ_sanctions_mod.lift_sanction
_econ_check_auto_sanctions = _econ_sanctions_mod.check_auto_sanctions
_econ_get_restrictions = _econ_sanctions_mod.get_restrictions
_econ_load_ledger = _econ_sanctions_mod.load_ledger
_econ_save_ledger = _econ_sanctions_mod.save_ledger

# Violation types per CLAUDE.md section 9
VIOLATION_TYPES = (
    'weak_output',        # 9.1 light failure
    'rejected_output',    # 9.2 rejected output
    'rework',             # 9.3 rework creation
    'token_waste',        # 9.4 token/meta-work waste
    'false_confidence',   # 9.5 false confidence / unverifiable claim
    'critical_failure',   # 9.6 critical failure / repeat violation
)

# Severity-to-sanction mapping (CLAUDE.md section 9)
SEVERITY_SANCTIONS = {
    1: None,                # light failure — no sanction, just no reward
    2: None,                # light failure
    3: 'probation',         # rejected output
    4: 'lead_ban',          # rework creation
    5: 'zero_authority',    # false confidence
    6: 'remediation_only',  # critical failure
}


def _now():
    return datetime.datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ')


def _load_records():
    if os.path.exists(RECORDS_FILE):
        with open(RECORDS_FILE) as f:
            return json.load(f)
    return {'violations': {}, 'appeals': {}, 'updatedAt': _now()}


def _save_records(data):
    data['updatedAt'] = _now()
    with open(RECORDS_FILE, 'w') as f:
        json.dump(data, f, indent=2)


# ── Core functions ───────────────────────────────────────────────────────────

def file_violation(agent_id, org_id, violation_type, severity, evidence, policy_ref=''):
    """Create a violation record. Auto-applies sanction if severity >= 3."""
    if violation_type not in VIOLATION_TYPES:
        raise ValueError(f'Invalid violation type: {violation_type}. Must be one of {VIOLATION_TYPES}')
    if severity < 1 or severity > 6:
        raise ValueError(f'Severity must be 1-6, got {severity}')

    records = _load_records()
    violation_id = f'vio_{uuid.uuid4().hex[:8]}'

    sanction_type = SEVERITY_SANCTIONS.get(severity)
    sanction_applied = None

    # Auto-apply sanction if severity >= 3
    if sanction_type:
        try:
            ledger = _econ_load_ledger()
            result = _econ_apply_sanction(
                ledger, agent_id, sanction_type,
                f'Court violation {violation_id}: {violation_type} (severity {severity})',
                level=severity
            )
            if result:
                _econ_save_ledger(ledger)
                sanction_applied = sanction_type
        except Exception as e:
            print(f'WARN: could not apply sanction: {e}')

    # Record incident in agent registry
    try:
        sys.path.insert(0, PLATFORM_DIR)
        from agent_registry import record_incident
        record_incident(agent_id)
    except Exception:
        pass

    records['violations'][violation_id] = {
        'id': violation_id,
        'agent_id': agent_id,
        'org_id': org_id,
        'type': violation_type,
        'severity': severity,
        'evidence': evidence,
        'policy_ref': policy_ref,
        'sanction_applied': sanction_applied,
        'status': 'sanctioned' if sanction_applied else 'open',
        'created_at': _now(),
        'resolved_at': None,
    }
    _save_records(records)

    # Audit log
    try:
        from audit import log_event
        log_event(org_id, agent_id, 'court_violation_filed',
                  resource=violation_id, outcome='success',
                  details={'type': violation_type, 'severity': severity,
                           'sanction_applied': sanction_applied},
                  policy_ref=policy_ref)
    except Exception:
        pass

    return violation_id


def get_violations(agent_id=None, status=None):
    """Query violations, optionally filtered by agent and/or status."""
    records = _load_records()
    violations = list(records['violations'].values())
    if agent_id:
        violations = [v for v in violations if v['agent_id'] == agent_id]
    if status:
        violations = [v for v in violations if v['status'] == status]
    return violations


def resolve_violation(violation_id, resolution_note):
    """Close a violation."""
    records = _load_records()
    v = records['violations'].get(violation_id)
    if not v:
        raise ValueError(f'Violation not found: {violation_id}')
    v['status'] = 'resolved'
    v['resolved_at'] = _now()
    v['resolution_note'] = resolution_note
    _save_records(records)


def file_appeal(violation_id, agent_id, grounds):
    """Create an appeal for a violation."""
    records = _load_records()
    v = records['violations'].get(violation_id)
    if not v:
        raise ValueError(f'Violation not found: {violation_id}')

    appeal_id = f'apl_{uuid.uuid4().hex[:8]}'
    records['appeals'][appeal_id] = {
        'id': appeal_id,
        'violation_id': violation_id,
        'agent_id': agent_id,
        'grounds': grounds,
        'status': 'pending',
        'decided_by': None,
        'decided_at': None,
        'created_at': _now(),
    }
    v['status'] = 'appealed'
    _save_records(records)
    return appeal_id


def decide_appeal(appeal_id, decision, decided_by):
    """Decide an appeal. If overturned, lift the associated sanction."""
    if decision not in ('upheld', 'overturned', 'dismissed'):
        raise ValueError(f'Invalid decision: {decision}')
    records = _load_records()
    appeal = records['appeals'].get(appeal_id)
    if not appeal:
        raise ValueError(f'Appeal not found: {appeal_id}')
    if appeal['status'] != 'pending':
        raise ValueError(f'Appeal {appeal_id} is already {appeal["status"]}')

    appeal['status'] = decision
    appeal['decided_by'] = decided_by
    appeal['decided_at'] = _now()

    # If overturned, lift the sanction and resolve the violation
    violation = records['violations'].get(appeal['violation_id'])
    if decision == 'overturned' and violation:
        violation['status'] = 'dismissed'
        violation['resolved_at'] = _now()
        violation['resolution_note'] = f'Appeal {appeal_id} overturned by {decided_by}'
        if violation.get('sanction_applied'):
            try:
                ledger = _econ_load_ledger()
                _econ_lift_sanction(
                    ledger, violation['agent_id'], violation['sanction_applied'],
                    f'Appeal {appeal_id} overturned')
                _econ_save_ledger(ledger)
            except Exception as e:
                print(f'WARN: could not lift sanction: {e}')
    elif decision == 'upheld' and violation:
        violation['status'] = 'sanctioned'

    _save_records(records)


def get_agent_record(agent_id):
    """Get full court record for an agent: violations, sanctions, appeals."""
    records = _load_records()
    violations = [v for v in records['violations'].values() if v['agent_id'] == agent_id]
    appeals = [a for a in records['appeals'].values() if a['agent_id'] == agent_id]

    # Current restrictions from economy
    try:
        ledger = _econ_load_ledger()
        restrictions = _econ_get_restrictions(ledger, agent_id)
    except Exception:
        restrictions = []

    return {
        'agent_id': agent_id,
        'violations': violations,
        'appeals': appeals,
        'active_restrictions': restrictions,
        'open_violations': len([v for v in violations if v['status'] in ('open', 'sanctioned')]),
        'total_violations': len(violations),
    }


def auto_review(ledger_data=None):
    """Wrap economy auto-sanctions and create violation records for any auto-applied sanctions."""
    if ledger_data is None:
        ledger_data = _econ_load_ledger()

    changes = _econ_check_auto_sanctions(ledger_data, dry_run=True)
    violations_created = []

    for kind, agent_id, stype, note, level in changes:
        if kind == 'apply':
            # Create a violation record for the auto-sanction
            try:
                # Determine org_id from agent registry
                org_id = ''
                try:
                    sys.path.insert(0, PLATFORM_DIR)
                    from agent_registry import load_registry
                    reg = load_registry()
                    for a in reg['agents'].values():
                        ekey = a.get('economy_key', '')
                        if ekey == agent_id or a['name'].lower() == agent_id:
                            org_id = a.get('org_id', '')
                            break
                except Exception:
                    pass

                severity = level if isinstance(level, int) else 3
                vid = file_violation(
                    agent_id=agent_id,
                    org_id=org_id,
                    violation_type='weak_output' if severity <= 2 else 'rejected_output',
                    severity=min(severity, 6),
                    evidence=note,
                    policy_ref='CLAUDE.md section 9 (auto-sanction)',
                )
                violations_created.append(vid)
            except Exception as e:
                print(f'WARN: auto_review could not file violation for {agent_id}: {e}')

    # Now actually apply the sanctions
    _econ_check_auto_sanctions(ledger_data, dry_run=False)

    return violations_created


def get_restrictions(agent_id):
    """Pass-through to economy sanctions."""
    ledger = _econ_load_ledger()
    return _econ_get_restrictions(ledger, agent_id)


# ── CLI ──────────────────────────────────────────────────────────────────────

def main():
    p = argparse.ArgumentParser(description='Court primitive — violations, sanctions, appeals')
    sub = p.add_subparsers(dest='command')

    fl = sub.add_parser('file')
    fl.add_argument('--agent', required=True)
    fl.add_argument('--org', required=True)
    fl.add_argument('--type', required=True, choices=list(VIOLATION_TYPES))
    fl.add_argument('--severity', type=int, required=True, choices=range(1, 7))
    fl.add_argument('--evidence', required=True)
    fl.add_argument('--policy_ref', default='')

    vl = sub.add_parser('violations')
    vl.add_argument('--agent', default=None)
    vl.add_argument('--status', default=None)

    rs = sub.add_parser('resolve')
    rs.add_argument('--violation_id', required=True)
    rs.add_argument('--note', required=True)

    ap = sub.add_parser('appeal')
    ap.add_argument('--violation_id', required=True)
    ap.add_argument('--agent', required=True)
    ap.add_argument('--grounds', required=True)

    da = sub.add_parser('decide-appeal')
    da.add_argument('--appeal_id', required=True)
    da.add_argument('--decision', required=True, choices=['upheld', 'overturned', 'dismissed'])
    da.add_argument('--by', required=True)

    rec = sub.add_parser('record')
    rec.add_argument('--agent', required=True)

    sub.add_parser('auto-review')
    sub.add_parser('show')

    args = p.parse_args()

    if args.command == 'file':
        vid = file_violation(args.agent, args.org, args.type, args.severity,
                             args.evidence, args.policy_ref)
        print(f'Violation filed: {vid}')
    elif args.command == 'violations':
        violations = get_violations(args.agent, args.status)
        if not violations:
            print('No violations found.')
        else:
            for v in violations:
                print(f"  {v['id']}  agent={v['agent_id']}  type={v['type']}  "
                      f"sev={v['severity']}  status={v['status']}  sanction={v.get('sanction_applied', '-')}")
    elif args.command == 'resolve':
        resolve_violation(args.violation_id, args.note)
        print(f'Violation {args.violation_id} resolved')
    elif args.command == 'appeal':
        aid = file_appeal(args.violation_id, args.agent, args.grounds)
        print(f'Appeal filed: {aid}')
    elif args.command == 'decide-appeal':
        decide_appeal(args.appeal_id, args.decision, args.by)
        print(f'Appeal {args.appeal_id}: {args.decision}')
    elif args.command == 'record':
        rec = get_agent_record(args.agent)
        print(f"\n=== Court Record: {args.agent} ===")
        print(f"Total violations: {rec['total_violations']}")
        print(f"Open violations:  {rec['open_violations']}")
        print(f"Active restrictions: {', '.join(rec['active_restrictions']) or 'none'}")
        for v in rec['violations']:
            print(f"  {v['id']}  {v['type']}  sev={v['severity']}  status={v['status']}  {v['created_at']}")
    elif args.command == 'auto-review':
        vids = auto_review()
        if vids:
            print(f'Auto-review created {len(vids)} violation(s): {vids}')
        else:
            print('Auto-review: no new violations')
    elif args.command == 'show':
        records = _load_records()
        violations = list(records['violations'].values())
        appeals = list(records['appeals'].values())
        open_v = [v for v in violations if v['status'] in ('open', 'sanctioned', 'appealed')]
        pending_a = [a for a in appeals if a['status'] == 'pending']

        print(f"\n=== Court Records ===")
        print(f"Total violations: {len(violations)}")
        print(f"Open violations:  {len(open_v)}")
        print(f"Total appeals:    {len(appeals)}")
        print(f"Pending appeals:  {len(pending_a)}")

        if open_v:
            print(f"\nOpen violations:")
            for v in open_v:
                print(f"  {v['id']}  agent={v['agent_id']}  type={v['type']}  "
                      f"sev={v['severity']}  status={v['status']}")
        if pending_a:
            print(f"\nPending appeals:")
            for a in pending_a:
                print(f"  {a['id']}  violation={a['violation_id']}  agent={a['agent_id']}")
    else:
        p.print_help()


if __name__ == '__main__':
    main()
