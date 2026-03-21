#!/usr/bin/env python3
"""
Governed Workspace — Owner-facing surface for the Constitutional OS.

Serves an HTML dashboard + JSON API for all five primitives:
  Institution, Agent, Authority, Treasury, Court.

Endpoints:
  GET  /                          → Dashboard HTML
  GET  /api/status                → Full system snapshot (JSON)
  GET  /api/institution           → Institution state
  GET  /api/agents                → Agent registry
  GET  /api/authority             → Authority state (kill switch, approvals, delegations)
  GET  /api/treasury              → Treasury snapshot
  GET  /api/court                 → Court records
  POST /api/authority/kill-switch → Engage/disengage kill switch
  POST /api/authority/approve     → Decide an approval
  POST /api/authority/request     → Request approval
  POST /api/authority/delegate    → Create delegation
  POST /api/authority/revoke      → Revoke delegation
  POST /api/court/file            → File a violation
  POST /api/court/resolve         → Resolve a violation
  POST /api/court/appeal          → File an appeal
  POST /api/court/decide-appeal   → Decide an appeal
  POST /api/court/remediate       → Lift lingering sanctions after review
  POST /api/treasury/contribute   → Record owner capital contribution
  POST /api/treasury/reserve-floor → Update reserve floor policy
  POST /api/institution/charter   → Set charter
  POST /api/institution/lifecycle → Transition lifecycle

Run:
  python3 workspace.py                    # port 18901
  python3 workspace.py --port 18902

When workspace credentials are configured, the dashboard and JSON API are
owner-authenticated with HTTP Basic auth.
"""
import argparse
import base64
import datetime
import hmac
import json
import os
import sys
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

PLATFORM_DIR = os.path.dirname(os.path.abspath(__file__))
WORKSPACE = os.path.dirname(os.path.dirname(PLATFORM_DIR))
PHASE_MACHINE_FILE = os.path.join(WORKSPACE, 'company', 'phase_machine.py')
WORKSPACE_CREDENTIALS_FILE = os.environ.get(
    'MERIDIAN_WORKSPACE_CREDENTIALS_FILE',
    '/etc/caddy/.workspace_credentials',
)
WORKSPACE_AUTH_REQUIRED = os.environ.get('MERIDIAN_WORKSPACE_AUTH_REQUIRED', '').lower() in (
    '1', 'true', 'yes', 'on'
)
sys.path.insert(0, PLATFORM_DIR)

from organizations import (load_orgs, set_charter, set_policy_defaults,
                           transition_lifecycle as org_transition_lifecycle)
from agent_registry import load_registry, sync_from_economy
from audit import log_event, query_events

import importlib.util

_phase_spec = importlib.util.spec_from_file_location('company_phase_machine', PHASE_MACHINE_FILE)
_phase_mod = importlib.util.module_from_spec(_phase_spec)
_phase_spec.loader.exec_module(_phase_mod)

# Import authority, treasury, court via their public APIs
from authority import (check_authority, request_approval, decide_approval,
                       delegate, revoke_delegation, engage_kill_switch,
                       disengage_kill_switch, get_pending_approvals,
                       get_sprint_lead, is_kill_switch_engaged, _load_queue)
from treasury import (treasury_snapshot, get_balance, get_runway, check_budget,
                      contribute_owner_capital, set_reserve_floor_policy)
from court import (file_violation, get_violations, resolve_violation,
                   file_appeal, decide_appeal, get_agent_record, auto_review,
                   get_restrictions, remediate, _load_records, VIOLATION_TYPES)
from capsule import capsule_dir
from ci_vertical import PIPELINE_PHASES, _phase_gate_snapshot, get_agent_remediation


def _now():
    return datetime.datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ')


def _load_workspace_credentials():
    env_user = os.environ.get('MERIDIAN_WORKSPACE_USER')
    env_password = os.environ.get('MERIDIAN_WORKSPACE_PASS')
    if env_user and env_password:
        return env_user, env_password
    if not os.path.exists(WORKSPACE_CREDENTIALS_FILE):
        return None, None
    user = None
    password = None
    with open(WORKSPACE_CREDENTIALS_FILE) as f:
        for raw_line in f:
            line = raw_line.strip()
            if line.startswith('user:'):
                user = line.split(':', 1)[1].strip()
            elif line.startswith('pass:'):
                password = line.split(':', 1)[1].strip()
    return user, password


def _get_founding_org():
    orgs = load_orgs()
    for oid, org in orgs['organizations'].items():
        if org.get('slug') == 'meridian':
            return oid, org
    return None, None


# ── API data builders ────────────────────────────────────────────────────────

def api_status():
    org_id, org = _get_founding_org()
    reg = load_registry()
    queue = _load_queue(org_id)
    snap = treasury_snapshot(org_id)
    phase_num, phase_details = _phase_mod.evaluate(org_id)
    records = _load_records(org_id)
    lead_id, lead_auth = get_sprint_lead(org_id)

    agents = []
    remediations = []
    for a in reg['agents'].values():
        if a.get('org_id') not in (None, '', org_id):
            continue
        restrictions = get_restrictions(a.get('economy_key', a['name'].lower()), org_id=org_id)
        remediation = get_agent_remediation(a.get('economy_key', a['name'].lower()), reg, org_id=org_id)
        if remediation:
            remediations.append(remediation)
        agents.append({
            'id': a['id'], 'name': a['name'], 'role': a['role'],
            'purpose': a['purpose'],
            'rep': a['reputation_units'], 'auth': a['authority_units'],
            'risk_state': a.get('risk_state', 'nominal'),
            'lifecycle_state': a.get('lifecycle_state', 'active'),
            'economy_key': a.get('economy_key'),
            'incident_count': a.get('incident_count', 0),
            'restrictions': restrictions,
            'is_sprint_lead': a.get('economy_key') == lead_id,
            'remediation': remediation,
        })

    open_violations = [
        v for v in records['violations'].values()
        if v['status'] in ('open', 'sanctioned', 'appealed') and v.get('org_id') in (None, '', org_id)
    ]
    pending_appeals = [
        a for a in records['appeals'].values()
        if a['status'] == 'pending' and a.get('org_id') in (None, '', org_id)
    ]
    pending_approvals = get_pending_approvals(org_id=org_id)
    active_delegations = [
        d for d in queue['delegations'].values()
        if d.get('expires_at', '') > _now() and d.get('org_id') in (None, '', org_id)
    ]

    return {
        'institution': {
            'id': org_id,
            'name': org.get('name', ''),
            'slug': org.get('slug', ''),
            'charter': org.get('charter', ''),
            'lifecycle_state': org.get('lifecycle_state', 'active'),
            'policy_defaults': org.get('policy_defaults', {}),
            'plan': org.get('plan', ''),
            'owner_id': org.get('owner_id', ''),
            'state_capsule': os.path.relpath(capsule_dir(org_id), WORKSPACE) if org_id else None,
            'treasury_id': org.get('treasury_id'),
        } if org else None,
        'agents': agents,
        'authority': {
            'kill_switch': queue['kill_switch'],
            'pending_approvals': pending_approvals,
            'active_delegations': active_delegations,
            'sprint_lead': {'agent_id': lead_id, 'auth': lead_auth},
        },
        'treasury': snap,
        'phase_machine': {
            'number': phase_num,
            'name': phase_details['name'],
            'next_phase': phase_details.get('next_phase'),
            'next_phase_name': phase_details.get('next_phase_name'),
            'next_unlock': phase_details.get('next_unlock'),
        },
        'court': {
            'open_violations': open_violations,
            'pending_appeals': pending_appeals,
            'total_violations': len(records['violations']),
            'total_appeals': len(records['appeals']),
        },
        'ci_vertical': _ci_vertical_status(reg, lead_id, org_id),
        'remediations': remediations,
        'timestamp': _now(),
    }


def _ci_vertical_status(reg, lead_id, org_id):
    """Build CI vertical constitutional gate status."""
    phases, blocked_phases = _phase_gate_snapshot(reg, org_id)
    all_clear = all(p['clear'] for p in phases)

    return {
        'preflight': 'CLEAR' if (all_clear and not is_kill_switch_engaged(org_id)) else 'BLOCKED',
        'blocked_phases': blocked_phases,
        'phases': phases,
    }


# ── HTML Dashboard ───────────────────────────────────────────────────────────

DASHBOARD_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Meridian — Governed Workspace</title>
<style>
:root { --bg: #0a0a0f; --fg: #e0e0e0; --accent: #4fc3f7; --card: #151520;
        --border: #2a2a3a; --green: #4caf50; --gold: #ffd54f; --dim: #888;
        --red: #ef5350; --orange: #ff9800; }
* { margin: 0; padding: 0; box-sizing: border-box; }
body { font-family: 'Inter', system-ui, sans-serif; background: var(--bg);
       color: var(--fg); line-height: 1.5; padding: 1.5rem; max-width: 1100px; margin: 0 auto; }
h1 { font-size: 1.5rem; color: #fff; margin-bottom: 0.5rem; }
h2 { font-size: 1.15rem; color: var(--accent); margin: 1.5rem 0 0.75rem;
     border-bottom: 1px solid var(--border); padding-bottom: 0.4rem; }
.subtitle { color: var(--dim); font-size: 0.9rem; margin-bottom: 1.5rem; }
.card { background: var(--card); border: 1px solid var(--border);
        border-radius: 8px; padding: 1rem; margin-bottom: 0.75rem; }
.grid2 { display: grid; grid-template-columns: 1fr 1fr; gap: 0.75rem; }
.grid3 { display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 0.75rem; }
@media (max-width: 700px) { .grid2, .grid3 { grid-template-columns: 1fr; } }
.metric { text-align: center; }
.metric .val { font-size: 1.6rem; font-weight: 700; color: #fff; }
.metric .label { font-size: 0.8rem; color: var(--dim); }
table { width: 100%; border-collapse: collapse; font-size: 0.85rem; }
th { text-align: left; color: var(--dim); font-weight: 600; padding: 0.4rem 0.5rem;
     border-bottom: 1px solid var(--border); }
td { padding: 0.4rem 0.5rem; border-bottom: 1px solid var(--border); }
.tag { display: inline-block; padding: 2px 8px; border-radius: 4px;
       font-size: 0.75rem; font-weight: 700; }
.tag-live { background: #1a3a1a; color: var(--green); }
.tag-warn { background: #3a2a1a; color: var(--orange); }
.tag-crit { background: #3a1a1a; color: var(--red); }
.tag-off  { background: #1a2a1a; color: var(--green); }
.tag-on   { background: #3a1a1a; color: var(--red); }
.action-row { display: flex; gap: 0.5rem; flex-wrap: wrap; margin: 0.5rem 0; }
button { background: var(--accent); color: #000; border: none; padding: 6px 16px;
         border-radius: 4px; cursor: pointer; font-weight: 600; font-size: 0.85rem; }
button:hover { background: #81d4fa; }
button.danger { background: var(--red); color: #fff; }
button.danger:hover { background: #c62828; }
button.secondary { background: transparent; border: 1px solid var(--border);
                   color: var(--fg); }
button.secondary:hover { border-color: var(--accent); color: var(--accent); }
input, select, textarea { background: #1a1a2a; border: 1px solid var(--border);
  color: var(--fg); padding: 6px 10px; border-radius: 4px; font-size: 0.85rem; }
textarea { width: 100%; min-height: 60px; font-family: inherit; }
.form-row { display: flex; gap: 0.5rem; align-items: center; margin: 0.4rem 0; flex-wrap: wrap; }
.form-row label { color: var(--dim); font-size: 0.8rem; min-width: 80px; }
.status-bar { display: flex; gap: 1.5rem; flex-wrap: wrap; margin-bottom: 1rem;
              padding: 0.75rem 1rem; background: var(--card); border-radius: 8px;
              border: 1px solid var(--border); font-size: 0.85rem; }
.status-bar .item { display: flex; align-items: center; gap: 0.4rem; }
#toast { position: fixed; bottom: 1rem; right: 1rem; background: var(--card);
         border: 1px solid var(--accent); color: var(--fg); padding: 0.75rem 1.25rem;
         border-radius: 6px; display: none; z-index: 99; font-size: 0.9rem; }
.empty { color: var(--dim); font-style: italic; padding: 0.5rem 0; }
a { color: var(--accent); text-decoration: none; }
a:hover { text-decoration: underline; }
</style>
</head>
<body>

<h1>Meridian Governed Workspace</h1>
<p class="subtitle">Constitutional Operating System — Five Primitives</p>

<div class="status-bar" id="status-bar">Loading...</div>

<!-- INSTITUTION -->
<h2>Institution</h2>
<div class="card" id="inst-card">Loading...</div>

<!-- AGENTS -->
<h2>Agents</h2>
<div class="card" id="agents-card">Loading...</div>

<!-- AUTHORITY -->
<h2>Authority</h2>
<div id="authority-section">Loading...</div>

<!-- TREASURY -->
<h2>Treasury</h2>
<div class="card" id="treasury-card">Loading...</div>

<!-- COURT -->
<h2>Court</h2>
<div id="court-section">Loading...</div>

<!-- CI VERTICAL -->
<h2>CI Vertical — Constitutional Pipeline</h2>
<div class="card" id="ci-card">Loading...</div>

<!-- RECENT AUDIT -->
<h2>Recent Audit Trail</h2>
<div class="card" id="audit-card">Loading...</div>

<div id="toast"></div>

<script>
function toast(msg, ms) {
  var t = document.getElementById('toast');
  t.textContent = msg; t.style.display = 'block';
  setTimeout(function() { t.style.display = 'none'; }, ms || 3000);
}

function api(method, path, body) {
  var opts = { method: method, headers: { 'Content-Type': 'application/json' } };
  if (body) opts.body = JSON.stringify(body);
  return fetch(path, opts).then(function(r) { return r.json(); });
}

function riskTag(state) {
  if (state === 'critical') return '<span class="tag tag-crit">CRITICAL</span>';
  if (state === 'elevated') return '<span class="tag tag-warn">ELEVATED</span>';
  if (state === 'suspended') return '<span class="tag tag-crit">SUSPENDED</span>';
  return '<span class="tag tag-live">NOMINAL</span>';
}

function render(data) {
  // Status bar
  var ks = data.authority.kill_switch;
  var sb = '';
  sb += '<span class="item">Kill switch: ' + (ks.engaged
    ? '<span class="tag tag-on">ENGAGED</span>' : '<span class="tag tag-off">OFF</span>') + '</span>';
  sb += '<span class="item">Balance: <strong>$' + data.treasury.balance_usd.toFixed(2) + '</strong></span>';
  sb += '<span class="item">Runway: <strong>$' + data.treasury.runway_usd.toFixed(2) + '</strong></span>';
  sb += '<span class="item">CI Gate: <strong>' + data.ci_vertical.preflight + '</strong></span>';
  sb += '<span class="item">Violations: <strong>' + data.court.open_violations.length + ' open</strong></span>';
  sb += '<span class="item">Approvals: <strong>' + data.authority.pending_approvals.length + ' pending</strong></span>';
  sb += '<span class="item">Lead: <strong>' + (data.authority.sprint_lead.agent_id || 'none') + '</strong></span>';
  document.getElementById('status-bar').innerHTML = sb;

  // Institution
  var inst = data.institution;
  if (inst) {
    var ic = '';
    ic += '<div class="grid2"><div>';
    ic += '<div class="form-row"><label>Name</label> <strong>' + inst.name + '</strong></div>';
    ic += '<div class="form-row"><label>Lifecycle</label> <span class="tag tag-live">' + inst.lifecycle_state.toUpperCase() + '</span></div>';
    ic += '<div class="form-row"><label>Plan</label> ' + inst.plan + '</div>';
    ic += '<div class="form-row"><label>Owner</label> ' + inst.owner_id + '</div>';
    ic += '<div class="form-row"><label>State Capsule</label> ' + (inst.state_capsule || 'none') + '</div>';
    ic += '<div class="form-row"><label>Treasury Pointer</label> ' + (inst.treasury_id || 'none') + '</div>';
    ic += '</div><div>';
    ic += '<div class="form-row"><label>Charter</label></div>';
    ic += '<textarea id="charter-text" placeholder="Set institution charter...">' + (inst.charter || '') + '</textarea>';
    ic += '<div class="action-row"><button onclick="setCharter()">Save Charter</button>';
    ic += ' <select id="lifecycle-select"><option value="active">active</option><option value="suspended">suspended</option><option value="dissolved">dissolved</option></select>';
    ic += ' <button class="secondary" onclick="setLifecycle()">Transition Lifecycle</button></div>';
    ic += '<div class="form-row"><label>Policies</label></div>';
    var pd = inst.policy_defaults || {};
    ic += '<div style="font-size:0.8rem;color:var(--dim)">';
    for (var k in pd) ic += k + ': ' + pd[k] + '<br>';
    ic += '</div>';
    ic += '</div></div>';
    document.getElementById('inst-card').innerHTML = ic;
  }

  // Agents
  var at = '<table><tr><th>Agent</th><th>Role</th><th>REP</th><th>AUTH</th><th>Risk</th><th>Lifecycle</th><th>Incidents</th><th>Restrictions</th><th>Lead</th></tr>';
  data.agents.forEach(function(a) {
    at += '<tr><td><strong>' + a.name + '</strong></td><td>' + a.role + '</td>';
    at += '<td>' + a.rep + '</td><td>' + a.auth + '</td>';
    at += '<td>' + riskTag(a.risk_state) + '</td>';
    at += '<td>' + a.lifecycle_state + '</td>';
    at += '<td>' + a.incident_count + '</td>';
    at += '<td>' + (a.restrictions.length ? a.restrictions.join(', ') : '-') + '</td>';
    at += '<td>' + (a.is_sprint_lead ? '<strong>LEAD</strong>' : '-') + '</td></tr>';
  });
  at += '</table>';
  document.getElementById('agents-card').innerHTML = at;

  // Authority
  var au = '';
  // Kill switch control
  au += '<div class="card">';
  au += '<strong>Kill Switch</strong>: ' + (ks.engaged
    ? '<span class="tag tag-on">ENGAGED</span> by ' + ks.engaged_by + ' — ' + ks.reason
      + ' <button onclick="killSwitch(false)">Disengage</button>'
    : '<span class="tag tag-off">OFF</span> <button class="danger" onclick="killSwitch(true)">Engage Kill Switch</button>');
  au += '</div>';

  // Pending approvals
  au += '<div class="card"><strong>Pending Approvals</strong> (' + data.authority.pending_approvals.length + ')';
  if (data.authority.pending_approvals.length === 0) {
    au += '<div class="empty">No pending approvals</div>';
  } else {
    au += '<table><tr><th>ID</th><th>Agent</th><th>Action</th><th>Resource</th><th>Cost</th><th>Actions</th></tr>';
    data.authority.pending_approvals.forEach(function(a) {
      au += '<tr><td>' + a.id + '</td><td>' + a.requester_agent_id + '</td>';
      au += '<td>' + a.action + '</td><td>' + a.resource + '</td>';
      au += '<td>$' + a.cost_usd.toFixed(2) + '</td>';
      au += '<td><button onclick="decideApproval(\'' + a.id + '\',\'approved\')">Approve</button> ';
      au += '<button class="danger" onclick="decideApproval(\'' + a.id + '\',\'denied\')">Deny</button></td></tr>';
    });
    au += '</table>';
  }
  au += '</div>';

  // Active delegations
  au += '<div class="card"><strong>Active Delegations</strong> (' + data.authority.active_delegations.length + ')';
  if (data.authority.active_delegations.length === 0) {
    au += '<div class="empty">No active delegations</div>';
  } else {
    au += '<table><tr><th>ID</th><th>From</th><th>To</th><th>Scopes</th><th>Expires</th><th>Actions</th></tr>';
    data.authority.active_delegations.forEach(function(d) {
      au += '<tr><td>' + d.id + '</td><td>' + d.from_agent_id + '</td>';
      au += '<td>' + d.to_agent_id + '</td><td>' + d.scopes.join(', ') + '</td>';
      au += '<td>' + d.expires_at + '</td>';
      au += '<td><button class="danger" onclick="revokeDelegation(\'' + d.id + '\')">Revoke</button></td></tr>';
    });
    au += '</table>';
  }
  au += '</div>';

  // Request approval form
  au += '<div class="card"><strong>Request Approval</strong>';
  au += '<div class="form-row"><label>Agent</label><select id="apr-agent">';
  data.agents.forEach(function(a) { au += '<option value="' + a.economy_key + '">' + a.name + '</option>'; });
  au += '</select></div>';
  au += '<div class="form-row"><label>Action</label><select id="apr-action"><option>execute</option><option>lead</option><option>assign</option><option>deploy</option></select></div>';
  au += '<div class="form-row"><label>Resource</label><input id="apr-resource" placeholder="what to act on"></div>';
  au += '<div class="form-row"><label>Cost</label><input id="apr-cost" type="number" step="0.01" value="0" style="width:80px"></div>';
  au += '<div class="action-row"><button onclick="requestApproval()">Submit Request</button></div>';
  au += '</div>';

  // Delegate form
  au += '<div class="card"><strong>Create Delegation</strong>';
  au += '<div class="form-row"><label>From</label><select id="dlg-from">';
  data.agents.forEach(function(a) { au += '<option value="' + a.economy_key + '">' + a.name + '</option>'; });
  au += '</select></div>';
  au += '<div class="form-row"><label>To</label><select id="dlg-to">';
  data.agents.forEach(function(a) { au += '<option value="' + a.economy_key + '">' + a.name + '</option>'; });
  au += '</select></div>';
  au += '<div class="form-row"><label>Scopes</label><input id="dlg-scopes" placeholder="lead,assign,execute"></div>';
  au += '<div class="form-row"><label>Hours</label><input id="dlg-hours" type="number" value="24" style="width:60px"></div>';
  au += '<div class="action-row"><button onclick="createDelegation()">Create Delegation</button></div>';
  au += '</div>';

  document.getElementById('authority-section').innerHTML = au;

  // Treasury
  var tr = data.treasury;
  var tc = '<div class="grid3">';
  tc += '<div class="metric"><div class="val">$' + tr.balance_usd.toFixed(2) + '</div><div class="label">Balance</div></div>';
  tc += '<div class="metric"><div class="val' + (tr.runway_usd < 0 ? '" style="color:var(--red)' : '') + '">$' + tr.runway_usd.toFixed(2) + '</div><div class="label">Runway</div></div>';
  tc += '<div class="metric"><div class="val">$' + tr.reserve_floor_usd.toFixed(2) + '</div><div class="label">Reserve Floor</div></div>';
  tc += '</div>';
  tc += '<div class="grid3" style="margin-top:0.75rem">';
  tc += '<div class="metric"><div class="val">$' + tr.total_revenue_usd.toFixed(2) + '</div><div class="label">Customer Revenue</div></div>';
  tc += '<div class="metric"><div class="val">$' + tr.support_received_usd.toFixed(2) + '</div><div class="label">Support</div></div>';
  tc += '<div class="metric"><div class="val">$' + tr.owner_capital_usd.toFixed(2) + '</div><div class="label">Owner Capital</div></div>';
  tc += '</div>';
  tc += '<div style="margin-top:0.75rem;font-size:0.85rem;color:var(--dim)">';
  tc += 'Receivables: $' + tr.receivables_usd.toFixed(2) + ' | Clients: ' + tr.clients;
  tc += ' | Paid orders: ' + tr.paid_orders + ' | Owner draws: $' + tr.owner_draws_usd.toFixed(2);
  tc += ' | Spend (30d): $' + tr.spend_30d_usd.toFixed(4);
  tc += ' | ' + (tr.above_reserve ? '<span style="color:var(--green)">Above reserve</span>' : '<span style="color:var(--red)">BELOW reserve</span>');
  tc += '</div>';
  if (data.phase_machine) {
    tc += '<div style="margin-top:0.75rem;font-size:0.85rem;color:var(--dim)">';
    tc += 'Institution phase: <strong style="color:#fff">' + data.phase_machine.number + ' — ' + data.phase_machine.name + '</strong>';
    if (data.phase_machine.next_phase_name) {
      tc += ' | Next: ' + data.phase_machine.next_phase + ' — ' + data.phase_machine.next_phase_name;
    }
    tc += '</div>';
    if (data.phase_machine.next_unlock) {
      tc += '<div style="margin-top:0.35rem;font-size:0.8rem;color:var(--dim)">Next unlock: ' + data.phase_machine.next_unlock + '</div>';
    }
  }
  if (!tr.above_reserve) {
    tc += '<div style="margin-top:0.75rem;padding:0.75rem;border:1px solid var(--red);border-radius:6px;background:#241414">';
    tc += '<strong style="color:var(--red)">Operating below reserve.</strong> Clear the reserve gate before any budget-gated phase can proceed, but do not treat that alone as automation-ready state.';
    tc += '</div>';
  }
  if (data.phase_machine && data.phase_machine.number < 4) {
    tc += '<div style="margin-top:0.75rem;padding:0.75rem;border:1px solid var(--orange);border-radius:6px;background:#2b2011">';
    tc += '<strong style="color:var(--orange)">Automation still phase-blocked.</strong> Support or owner cash can clear reserve pressure, but automated delivery still waits for customer-backed treasury, treasury-cleared automation, and constitutional preflight.';
    tc += '</div>';
  }
  if (tr.remediation && tr.remediation.blocked) {
    tc += '<div class="card" style="margin-top:0.75rem"><strong>Treasury Remediation</strong>';
    tc += '<div class="form-row"><label>Shortfall</label><strong>$' + tr.remediation.shortfall_usd.toFixed(2) + '</strong></div>';
    tc += '<div class="form-row"><label>Capital</label><input id="cap-amount" type="number" step="0.01" value="' + tr.remediation.recommended_owner_capital_usd.toFixed(2) + '" style="width:120px"> <input id="cap-note" placeholder="Real transfer note" style="flex:1"></div>';
    tc += '<div class="action-row"><button onclick="contributeCapital()">Record Owner Capital</button></div>';
    tc += '<div style="font-size:0.8rem;color:var(--dim);margin-top:0.35rem">Only record this after real money has actually moved into treasury custody.</div>';
    tc += '<div class="form-row" style="margin-top:0.75rem"><label>Reserve</label><input id="reserve-amount" type="number" step="0.01" value="' + tr.remediation.recommended_reserve_floor_usd.toFixed(2) + '" style="width:120px"> <input id="reserve-note" placeholder="Why policy changed" style="flex:1"></div>';
    tc += '<div class="action-row"><button class="secondary" onclick="updateReserveFloor()">Update Reserve Floor</button></div>';
    tc += '<ul style="margin:0.5rem 0 0 1.25rem;font-size:0.82rem;color:var(--dim)">';
    tr.remediation.next_steps.forEach(function(step) { tc += '<li>' + step + '</li>'; });
    tc += '</ul></div>';
  }
  document.getElementById('treasury-card').innerHTML = tc;

  // Court
  var co = '';
  // Open violations
  co += '<div class="card"><strong>Open Violations</strong> (' + data.court.open_violations.length + ')';
  if (data.court.open_violations.length === 0) {
    co += '<div class="empty">No open violations</div>';
  } else {
    co += '<table><tr><th>ID</th><th>Agent</th><th>Type</th><th>Severity</th><th>Status</th><th>Sanction</th><th>Actions</th></tr>';
    data.court.open_violations.forEach(function(v) {
      co += '<tr><td>' + v.id + '</td><td>' + v.agent_id + '</td>';
      co += '<td>' + v.type + '</td><td>' + v.severity + '</td>';
      co += '<td>' + v.status + '</td><td>' + (v.sanction_applied || '-') + '</td>';
      co += '<td><button class="secondary" onclick="resolveViolation(\'' + v.id + '\')">Resolve</button> ';
      co += '<button class="secondary" onclick="fileAppeal(\'' + v.id + '\',\'' + v.agent_id + '\')">Appeal</button></td></tr>';
    });
    co += '</table>';
  }

  // Pending appeals
  if (data.court.pending_appeals.length > 0) {
    co += '<br><strong>Pending Appeals</strong>';
    co += '<table><tr><th>ID</th><th>Violation</th><th>Agent</th><th>Grounds</th><th>Actions</th></tr>';
    data.court.pending_appeals.forEach(function(a) {
      co += '<tr><td>' + a.id + '</td><td>' + a.violation_id + '</td><td>' + a.agent_id + '</td>';
      co += '<td>' + a.grounds + '</td>';
      co += '<td><button onclick="decideAppealAction(\'' + a.id + '\',\'overturned\')">Overturn</button> ';
      co += '<button class="secondary" onclick="decideAppealAction(\'' + a.id + '\',\'upheld\')">Uphold</button> ';
      co += '<button class="secondary" onclick="decideAppealAction(\'' + a.id + '\',\'dismissed\')">Dismiss</button></td></tr>';
    });
    co += '</table>';
  }

  co += '<div style="margin-top:0.5rem;font-size:0.8rem;color:var(--dim)">Total violations: ' + data.court.total_violations + ' | Total appeals: ' + data.court.total_appeals + '</div>';
  co += '</div>';

  // File violation form
  co += '<div class="card"><strong>File Violation</strong>';
  co += '<div class="form-row"><label>Agent</label><select id="vio-agent">';
  data.agents.forEach(function(a) { co += '<option value="' + a.economy_key + '">' + a.name + ' (' + a.economy_key + ')</option>'; });
  co += '</select></div>';
  co += '<div class="form-row"><label>Type</label><select id="vio-type">';
  ['weak_output','rejected_output','rework','token_waste','false_confidence','critical_failure'].forEach(function(t) {
    co += '<option value="' + t + '">' + t + '</option>';
  });
  co += '</select></div>';
  co += '<div class="form-row"><label>Severity</label><select id="vio-severity">';
  for (var s = 1; s <= 6; s++) {
    var desc = ['','light failure','light failure','probation','lead_ban','zero_authority','remediation_only'];
    co += '<option value="' + s + '">' + s + ' (' + desc[s] + ')</option>';
  }
  co += '</select></div>';
  co += '<div class="form-row"><label>Evidence</label><input id="vio-evidence" placeholder="What happened" style="flex:1"></div>';
  co += '<div class="form-row"><label>Policy Ref</label><input id="vio-policy" placeholder="e.g. CLAUDE.md section 9.2" style="flex:1"></div>';
  co += '<div class="action-row"><button class="danger" onclick="fileViolation()">File Violation</button>';
  co += ' <button class="secondary" onclick="autoReview()">Run Auto-Review</button></div>';
  co += '</div>';

  document.getElementById('court-section').innerHTML = co;

  // CI Vertical
  var ci = data.ci_vertical;
  var cv = '<div style="margin-bottom:0.5rem"><strong>Preflight:</strong> ';
  cv += ci.preflight === 'CLEAR'
    ? '<span class="tag tag-live">CLEAR</span>'
    : '<span class="tag tag-crit">BLOCKED</span>';
  if (ci.blocked_phases.length) cv += ' <span style="color:var(--dim)">(' + ci.blocked_phases.join(', ') + ')</span>';
  cv += '</div>';
  cv += '<table><tr><th>Phase</th><th>Agent</th><th>Action</th><th>Authority</th><th>Risk</th><th>Restrictions</th><th>Lead</th></tr>';
  ci.phases.forEach(function(p) {
    cv += '<tr><td><strong>' + p.phase + '</strong></td>';
    cv += '<td>' + p.agent_name + '</td><td>' + p.action + '</td>';
    var gate = p.clear ? '<span style="color:var(--green)">PASS</span>' : '<span style="color:var(--red)">BLOCKED</span>';
    if (!p.clear && p.blockers.length) gate += '<div style="color:var(--dim);font-size:0.75rem">' + p.blockers.join(' | ') + '</div>';
    cv += '<td>' + gate + '</td>';
    cv += '<td>' + riskTag(p.risk_state) + '</td>';
    cv += '<td>' + (p.restrictions.length ? p.restrictions.join(', ') : '-') + '</td>';
    cv += '<td>' + (p.is_lead ? '<strong>LEAD</strong>' : '-') + '</td></tr>';
  });
  cv += '</table>';
  if (data.remediations.length) {
    cv += '<div style="margin-top:0.75rem"><strong>Remediation Paths</strong></div>';
    data.remediations.forEach(function(r) {
      cv += '<div class="card" style="margin-top:0.5rem">';
      cv += '<strong>' + r.agent_name + '</strong> ' + riskTag(r.risk_state);
      cv += '<div style="margin-top:0.35rem;font-size:0.82rem;color:var(--dim)">Restrictions: ' + (r.restrictions.length ? r.restrictions.join(', ') : '-') + ' | Open violations: ' + r.open_violations + ' | Total violations: ' + r.total_violations + '</div>';
      if (r.actions && r.actions.remediate && r.actions.remediate.allowed) {
        cv += '<div class="action-row" style="margin-top:0.5rem"><button class="secondary" onclick="remediateAgent(\\'' + r.agent_key + '\\', \\'' + r.agent_name + '\\')">Run Court Remediation</button></div>';
      }
      cv += '<ul style="margin:0.5rem 0 0 1.25rem">';
      r.next_steps.forEach(function(step) { cv += '<li>' + step + '</li>'; });
      cv += '</ul></div>';
    });
  }
  cv += '<div style="margin-top:0.5rem;font-size:0.8rem;color:var(--dim)">Pipeline phases execute in order: research -> write -> QA -> execute -> compress -> deliver -> score</div>';
  document.getElementById('ci-card').innerHTML = cv;
}

// ── Action handlers ─────────────────────────────────────────────────────────

function killSwitch(engage) {
  var reason = engage ? prompt('Reason for engaging kill switch:') : '';
  if (engage && !reason) return;
  api('POST', '/api/authority/kill-switch', { engage: engage, by: 'owner', reason: reason })
    .then(function(r) { toast(r.message); refresh(); });
}

function decideApproval(id, decision) {
  var reason = prompt('Reason (' + decision + '):') || '';
  api('POST', '/api/authority/approve', { approval_id: id, decision: decision, by: 'owner', reason: reason })
    .then(function(r) { toast(r.message); refresh(); });
}

function requestApproval() {
  api('POST', '/api/authority/request', {
    agent: document.getElementById('apr-agent').value,
    action: document.getElementById('apr-action').value,
    resource: document.getElementById('apr-resource').value,
    cost: parseFloat(document.getElementById('apr-cost').value) || 0
  }).then(function(r) { toast(r.message); refresh(); });
}

function createDelegation() {
  api('POST', '/api/authority/delegate', {
    from: document.getElementById('dlg-from').value,
    to: document.getElementById('dlg-to').value,
    scopes: document.getElementById('dlg-scopes').value,
    hours: parseInt(document.getElementById('dlg-hours').value) || 24
  }).then(function(r) { toast(r.message); refresh(); });
}

function revokeDelegation(id) {
  if (!confirm('Revoke delegation ' + id + '?')) return;
  api('POST', '/api/authority/revoke', { delegation_id: id })
    .then(function(r) { toast(r.message); refresh(); });
}

function fileViolation() {
  api('POST', '/api/court/file', {
    agent: document.getElementById('vio-agent').value,
    type: document.getElementById('vio-type').value,
    severity: parseInt(document.getElementById('vio-severity').value),
    evidence: document.getElementById('vio-evidence').value,
    policy_ref: document.getElementById('vio-policy').value
  }).then(function(r) { toast(r.message); refresh(); });
}

function resolveViolation(id) {
  var note = prompt('Resolution note:');
  if (!note) return;
  api('POST', '/api/court/resolve', { violation_id: id, note: note })
    .then(function(r) { toast(r.message); refresh(); });
}

function fileAppeal(vid, agent) {
  var grounds = prompt('Appeal grounds:');
  if (!grounds) return;
  api('POST', '/api/court/appeal', { violation_id: vid, agent: agent, grounds: grounds })
    .then(function(r) { toast(r.message); refresh(); });
}

function decideAppealAction(id, decision) {
  api('POST', '/api/court/decide-appeal', { appeal_id: id, decision: decision, by: 'owner' })
    .then(function(r) { toast(r.message); refresh(); });
}

function autoReview() {
  api('POST', '/api/court/auto-review', {})
    .then(function(r) { toast(r.message); refresh(); });
}

function remediateAgent(agentId, agentName) {
  var note = prompt('Remediation note for ' + agentName + ':') || '';
  api('POST', '/api/court/remediate', { agent_id: agentId, by: 'owner', note: note })
    .then(function(r) { toast(r.message); refresh(); });
}

function contributeCapital() {
  var amount = parseFloat(document.getElementById('cap-amount').value) || 0;
  var note = document.getElementById('cap-note').value || 'owner capital contribution';
  if (amount <= 0) return toast('Capital amount must be greater than 0');
  api('POST', '/api/treasury/contribute', { amount: amount, note: note, by: 'owner' })
    .then(function(r) { toast(r.message || r.error); refresh(); });
}

function updateReserveFloor() {
  var amount = parseFloat(document.getElementById('reserve-amount').value);
  var note = document.getElementById('reserve-note').value || 'reserve policy change';
  if (isNaN(amount) || amount < 0) return toast('Reserve floor must be 0 or greater');
  if (!confirm('Update reserve floor to $' + amount.toFixed(2) + '?')) return;
  api('POST', '/api/treasury/reserve-floor', { amount: amount, note: note, by: 'owner' })
    .then(function(r) { toast(r.message || r.error); refresh(); });
}

function setCharter() {
  var text = document.getElementById('charter-text').value;
  api('POST', '/api/institution/charter', { text: text })
    .then(function(r) { toast(r.message); refresh(); });
}

function setLifecycle() {
  var state = document.getElementById('lifecycle-select').value;
  if (!confirm('Transition institution lifecycle to ' + state + '?')) return;
  api('POST', '/api/institution/lifecycle', { state: state })
    .then(function(r) { toast(r.message || r.error); refresh(); });
}

function refresh() {
  api('GET', '/api/status').then(render).catch(function(e) {
    document.getElementById('status-bar').innerHTML = '<span style="color:var(--red)">Error loading: ' + e + '</span>';
  });
  // Also load audit trail
  api('GET', '/api/audit').then(function(data) {
    if (!data.events || data.events.length === 0) {
      document.getElementById('audit-card').innerHTML = '<div class="empty">No recent audit events</div>';
      return;
    }
    var at = '<table><tr><th>Time</th><th>Action</th><th>Agent</th><th>Resource</th><th>Outcome</th></tr>';
    data.events.slice(0, 20).forEach(function(e) {
      at += '<tr><td style="font-size:0.8rem">' + e.timestamp + '</td>';
      at += '<td>' + e.action + '</td><td>' + (e.agent_id || '-') + '</td>';
      at += '<td>' + (e.resource || '-') + '</td><td>' + e.outcome + '</td></tr>';
    });
    at += '</table>';
    document.getElementById('audit-card').innerHTML = at;
  }).catch(function(){});
}

refresh();
setInterval(refresh, 15000);
</script>
</body>
</html>"""


# ── HTTP Request Handler ─────────────────────────────────────────────────────

class WorkspaceHandler(BaseHTTPRequestHandler):

    def _json(self, data, status=200):
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(json.dumps(data).encode())

    def _html(self, html):
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.end_headers()
        self.wfile.write(html.encode())

    def _unauthorized(self, is_api=True):
        self.send_response(401)
        self.send_header('WWW-Authenticate', 'Basic realm="Meridian Workspace"')
        if is_api:
            self.send_header('Content-Type', 'application/json')
        else:
            self.send_header('Content-Type', 'text/plain; charset=utf-8')
        self.end_headers()
        if is_api:
            self.wfile.write(json.dumps({'error': 'Unauthorized'}).encode())
        else:
            self.wfile.write(b'Unauthorized')

    def _service_unavailable(self, message, is_api=True):
        self.send_response(503)
        if is_api:
            self.send_header('Content-Type', 'application/json')
        else:
            self.send_header('Content-Type', 'text/plain; charset=utf-8')
        self.end_headers()
        if is_api:
            self.wfile.write(json.dumps({'error': message}).encode())
        else:
            self.wfile.write(message.encode())

    def _is_authorized(self):
        user, password = _load_workspace_credentials()
        if not user or not password:
            return False

        header = self.headers.get('Authorization', '')
        if not header.startswith('Basic '):
            return False
        try:
            decoded = base64.b64decode(header.split(' ', 1)[1]).decode('utf-8')
        except Exception:
            return False
        if ':' not in decoded:
            return False
        supplied_user, supplied_password = decoded.split(':', 1)
        return hmac.compare_digest(supplied_user, user) and hmac.compare_digest(supplied_password, password)

    def _require_auth(self, path):
        protected = path == '/' or path.startswith('/workspace') or path.startswith('/api/')
        if not protected:
            return True
        user, password = _load_workspace_credentials()
        if not user or not password:
            if WORKSPACE_AUTH_REQUIRED:
                self._service_unavailable('Workspace auth is required but credentials are not configured',
                                          is_api=path.startswith('/api/'))
                return False
            return True
        if self._is_authorized():
            return True
        self._unauthorized(is_api=path.startswith('/api/'))
        return False

    def _read_body(self):
        length = int(self.headers.get('Content-Length', 0))
        if length == 0:
            return {}
        return json.loads(self.rfile.read(length))

    def log_message(self, fmt, *args):
        pass  # Suppress default logging

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.end_headers()

    def do_GET(self):
        path = urlparse(self.path).path
        if not self._require_auth(path):
            return

        if path == '/' or path == '/workspace':
            return self._html(DASHBOARD_HTML)
        elif path == '/api/status':
            return self._json(api_status())
        elif path == '/api/institution':
            _, org = _get_founding_org()
            return self._json(org or {})
        elif path == '/api/agents':
            org_id, _ = _get_founding_org()
            reg = load_registry()
            return self._json([a for a in reg['agents'].values() if a.get('org_id') in (None, '', org_id)])
        elif path == '/api/authority':
            org_id, _ = _get_founding_org()
            queue = _load_queue(org_id)
            lead_id, lead_auth = get_sprint_lead(org_id)
            return self._json({
                'kill_switch': queue['kill_switch'],
                'pending_approvals': get_pending_approvals(org_id=org_id),
                'delegations': [d for d in queue['delegations'].values() if d.get('org_id') in (None, '', org_id)],
                'sprint_lead': {'agent_id': lead_id, 'auth': lead_auth},
            })
        elif path == '/api/treasury':
            org_id, _ = _get_founding_org()
            return self._json(treasury_snapshot(org_id))
        elif path == '/api/court':
            org_id, _ = _get_founding_org()
            records = _load_records(org_id)
            return self._json({
                'violations': [v for v in records['violations'].values() if v.get('org_id') in (None, '', org_id)],
                'appeals': [a for a in records['appeals'].values() if a.get('org_id') in (None, '', org_id)],
            })
        elif path == '/api/ci-vertical':
            org_id, _ = _get_founding_org()
            reg = load_registry()
            lead_id, _ = get_sprint_lead(org_id)
            return self._json(_ci_vertical_status(reg, lead_id, org_id))
        elif path == '/api/audit':
            org_id, _ = _get_founding_org()
            events = query_events(org_id=org_id, limit=30)
            events.reverse()
            return self._json({'events': events})
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        path = urlparse(self.path).path
        if not self._require_auth(path):
            return
        org_id, org = _get_founding_org()

        try:
            body = self._read_body()
        except Exception:
            return self._json({'error': 'Invalid JSON'}, 400)

        by = 'owner'  # server-enforced — never trust client-supplied actor identity

        try:
            if path == '/api/authority/kill-switch':
                if body.get('engage'):
                    engage_kill_switch(by, body.get('reason', ''), org_id=org_id)
                    log_event(org_id, 'system', 'kill_switch_engaged', outcome='success',
                              details={'by': by, 'reason': body.get('reason')})
                    return self._json({'message': 'Kill switch ENGAGED'})
                else:
                    disengage_kill_switch(by, org_id=org_id)
                    log_event(org_id, 'system', 'kill_switch_disengaged', outcome='success',
                              details={'by': by})
                    return self._json({'message': 'Kill switch disengaged'})

            elif path == '/api/authority/approve':
                decision = body['decision']
                decide_approval(body['approval_id'], decision,
                               by, body.get('reason', ''), org_id=org_id)
                log_event(org_id, 'system', 'approval_decided', resource=body['approval_id'],
                          outcome='success', details={'decision': decision})
                return self._json({'message': f'Approval {body["approval_id"]}: {decision}'})

            elif path == '/api/authority/request':
                aid = request_approval(body['agent'], body['action'],
                                       body['resource'], body.get('cost', 0), org_id=org_id)
                log_event(org_id, body['agent'], 'approval_requested', resource=aid,
                          outcome='success', details=body)
                return self._json({'message': f'Approval requested: {aid}', 'approval_id': aid})

            elif path == '/api/authority/delegate':
                scopes = [s.strip() for s in body['scopes'].split(',') if s.strip()]
                did = delegate(body['from'], body['to'], scopes, body.get('hours', 24), org_id=org_id)
                log_event(org_id, body['from'], 'delegation_created', resource=did,
                          outcome='success', details=body)
                return self._json({'message': f'Delegation created: {did}', 'delegation_id': did})

            elif path == '/api/authority/revoke':
                revoke_delegation(body['delegation_id'], org_id=org_id)
                log_event(org_id, 'system', 'delegation_revoked', resource=body['delegation_id'],
                          outcome='success')
                return self._json({'message': f'Delegation revoked: {body["delegation_id"]}'})

            elif path == '/api/court/file':
                vid = file_violation(body['agent'], org_id, body['type'],
                                     body['severity'], body['evidence'],
                                     body.get('policy_ref', ''))
                return self._json({'message': f'Violation filed: {vid}', 'violation_id': vid})

            elif path == '/api/court/resolve':
                resolve_violation(body['violation_id'], body['note'], org_id=org_id)
                log_event(org_id, 'system', 'violation_resolved', resource=body['violation_id'],
                          outcome='success')
                return self._json({'message': f'Violation resolved: {body["violation_id"]}'})

            elif path == '/api/court/appeal':
                aid = file_appeal(body['violation_id'], body['agent'], body['grounds'], org_id=org_id)
                log_event(org_id, body['agent'], 'appeal_filed', resource=aid, outcome='success')
                return self._json({'message': f'Appeal filed: {aid}', 'appeal_id': aid})

            elif path == '/api/court/decide-appeal':
                decide_appeal(body['appeal_id'], body['decision'], by, org_id=org_id)
                log_event(org_id, 'system', 'appeal_decided', resource=body['appeal_id'],
                          outcome='success', details={'decision': body['decision']})
                return self._json({'message': f'Appeal {body["appeal_id"]}: {body["decision"]}'})

            elif path == '/api/court/auto-review':
                vids = auto_review(org_id=org_id)
                return self._json({'message': f'Auto-review: {len(vids)} violation(s) created',
                                   'violations': vids})

            elif path == '/api/court/remediate':
                lifted = remediate(body['agent_id'], by,
                                   body.get('note', ''), org_id=org_id)
                log_event(org_id, 'system', 'court_remediation', resource=body['agent_id'],
                          outcome='success', details={'lifted': lifted})
                return self._json({'message': f'Remediation complete: lifted {lifted}',
                                   'lifted': lifted})

            elif path == '/api/treasury/contribute':
                result = contribute_owner_capital(body['amount'], body.get('note', ''),
                                                  by, org_id=org_id)
                log_event(org_id, 'system', 'treasury_owner_capital', outcome='success',
                          details=result)
                return self._json({
                    'message': f'Owner capital recorded: +${result["amount_usd"]:.2f}',
                    'snapshot': treasury_snapshot(org_id),
                })

            elif path == '/api/treasury/reserve-floor':
                result = set_reserve_floor_policy(body['amount'], body.get('note', ''),
                                                  by, org_id=org_id)
                log_event(org_id, 'system', 'treasury_reserve_floor_updated',
                          outcome='success', details=result)
                return self._json({
                    'message': 'Reserve floor updated',
                    'snapshot': treasury_snapshot(org_id),
                })

            elif path == '/api/institution/charter':
                set_charter(org_id, body['text'])
                log_event(org_id, 'system', 'charter_set', outcome='success')
                return self._json({'message': 'Charter saved'})

            elif path == '/api/institution/lifecycle':
                org_transition_lifecycle(org_id, body['state'])
                log_event(org_id, 'system', 'lifecycle_transitioned', outcome='success',
                          details={'new_state': body['state']})
                return self._json({'message': f'Lifecycle transitioned to {body["state"]}'})

            else:
                return self._json({'error': 'Not found'}, 404)

        except Exception as e:
            return self._json({'error': str(e)}, 400)


def main():
    parser = argparse.ArgumentParser(description='Governed Workspace server')
    parser.add_argument('--port', type=int, default=18901)
    args = parser.parse_args()

    server = HTTPServer(('127.0.0.1', args.port), WorkspaceHandler)
    print(f'Governed Workspace running at http://127.0.0.1:{args.port}')
    print(f'Dashboard: http://127.0.0.1:{args.port}/')
    print(f'API:       http://127.0.0.1:{args.port}/api/status')
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print('\nShutdown.')
        server.server_close()


if __name__ == '__main__':
    main()
