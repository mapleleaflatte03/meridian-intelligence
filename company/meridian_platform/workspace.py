#!/usr/bin/env python3
"""
Governed Workspace — Owner-facing surface for the Constitutional OS.

Serves an HTML dashboard + JSON API for all six primitives:
  Institution, Agent, Authority, Treasury, Court, Commitment.

Endpoints:
  GET  /                          → Dashboard HTML
  GET  /api/status                → Full system snapshot (JSON)
  GET  /api/institution           → Institution state
  GET  /api/agents                → Agent registry
  GET  /api/authority             → Authority state (kill switch, approvals, delegations)
  GET  /api/treasury              → Treasury snapshot
  GET  /api/court                 → Court records
  GET  /api/warrants              → Warrant records and summary
  GET  /api/commitments           → Commitment records and summary
  GET  /api/cases                 → Founding-workspace case records and summary
  GET  /api/admission             → Host admission state
  GET  /api/federation            → Federation gateway state
  GET  /api/federation/peers      → Federation peer registry state
  GET  /api/federation/manifest   → Public host federation manifest
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
  POST /api/warrants/issue        → Issue a warrant record
  POST /api/warrants/approve      → Approve a warrant for execution
  POST /api/warrants/stay         → Stay a warrant before execution
  POST /api/warrants/revoke       → Revoke a warrant before execution
  POST /api/commitments/propose   → Propose a founding-org commitment
  POST /api/commitments/accept    → Accept a commitment
  POST /api/commitments/reject    → Reject a commitment
  POST /api/commitments/breach    → Mark a commitment breached
  POST /api/commitments/settle    → Settle a commitment
  POST /api/cases/open            → Open a founding-workspace case
  POST /api/cases/stay            → Stay a founding-workspace case
  POST /api/cases/resolve         → Resolve a founding-workspace case
  POST /api/treasury/contribute   → Record owner capital contribution
  POST /api/treasury/reserve-floor → Update reserve floor policy
  POST /api/admission/admit       → Structurally reject non-founding admission changes
  POST /api/admission/suspend     → Structurally reject admission suspension changes
  POST /api/admission/revoke      → Structurally reject admission revocation changes
  POST /api/federation/peers/upsert → Structurally reject peer registry mutation on live
  POST /api/federation/peers/refresh → Structurally reject peer capability refresh on live
  POST /api/federation/peers/suspend → Structurally reject peer suspension changes on live
  POST /api/federation/peers/revoke → Structurally reject peer revocation changes on live
  POST /api/federation/send       → Attempt a federation delivery and fail closed when disabled
  POST /api/federation/receive    → Validate and consume a federation envelope
  POST /api/institution/charter   → Set charter
  POST /api/institution/lifecycle → Transition lifecycle

Run:
  python3 workspace.py                    # port 18901
  python3 workspace.py --port 18902
  python3 workspace.py --org-id <org>    # must still be the founding Meridian org on live

When workspace credentials are configured, the dashboard and JSON API are
owner-authenticated with HTTP Basic auth.
"""
import argparse
import base64
import datetime
import hashlib
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
RUNTIME_HOST_IDENTITY_FILE = os.environ.get(
    'MERIDIAN_RUNTIME_HOST_IDENTITY_FILE',
    os.path.join(PLATFORM_DIR, 'host_identity.json'),
)
RUNTIME_ADMISSION_FILE = os.environ.get(
    'MERIDIAN_RUNTIME_ADMISSION_FILE',
    os.path.join(PLATFORM_DIR, 'institution_admissions.json'),
)
FEDERATION_PEERS_FILE = os.environ.get(
    'MERIDIAN_FEDERATION_PEERS_FILE',
    os.path.join(PLATFORM_DIR, 'federation_peers.json'),
)
FEDERATION_REPLAY_FILE = os.environ.get(
    'MERIDIAN_FEDERATION_REPLAY_FILE',
    os.path.join(PLATFORM_DIR, '.federation_replay'),
)
FEDERATION_SIGNING_SECRET = (
    os.environ.get('MERIDIAN_FEDERATION_SIGNING_SECRET', '').strip() or None
)
WORKSPACE_ORG_ID = (os.environ.get('MERIDIAN_WORKSPACE_ORG_ID') or '').strip() or None
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
from session import SessionAuthority
from warrants import (
    list_warrants,
    issue_warrant,
    review_warrant,
    validate_warrant_for_execution,
    mark_warrant_executed,
    warrant_action_for_message,
)
import commitments
import cases
from federation import (
    FederationAuthority,
    ReplayStore,
    load_peer_registry,
    FederationUnavailable,
    FederationDeliveryError,
    FederationValidationError,
    FederationReplayError,
)
from capsule import capsule_dir
from ci_vertical import PIPELINE_PHASES, _phase_gate_snapshot, get_agent_remediation
from institution_context import InstitutionContext, WORKSPACE_BOUNDARY, runtime_core_snapshot
from runtime_host import (
    load_host_identity,
    load_admission_registry,
    ensure_org_admitted,
)

# Process-level session authority (tokens do not survive restarts unless
# MERIDIAN_SESSION_SECRET is set in the environment).
_session_revocation_file = (
    os.environ.get('MERIDIAN_SESSION_REVOCATIONS_FILE', '').strip() or None
)
if not _session_revocation_file and os.environ.get('MERIDIAN_SESSION_SECRET', '').strip():
    _session_revocation_file = os.path.join(PLATFORM_DIR, '.session_revocations')
_session_authority = SessionAuthority(revocation_file=_session_revocation_file)


def _now():
    return datetime.datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ')


ROLE_RANK = {
    'viewer': 0,
    'member': 1,
    'admin': 2,
    'owner': 3,
}

MUTATION_ROLE_REQUIREMENTS = {
    '/api/authority/kill-switch': 'admin',
    '/api/authority/approve': 'admin',
    '/api/authority/request': 'member',
    '/api/authority/delegate': 'admin',
    '/api/authority/revoke': 'admin',
    '/api/court/file': 'member',
    '/api/court/resolve': 'admin',
    '/api/court/appeal': 'member',
    '/api/court/decide-appeal': 'admin',
    '/api/court/auto-review': 'admin',
    '/api/court/remediate': 'admin',
    '/api/warrants/issue': 'admin',
    '/api/warrants/approve': 'admin',
    '/api/warrants/stay': 'admin',
    '/api/warrants/revoke': 'admin',
    '/api/commitments/propose': 'admin',
    '/api/commitments/accept': 'admin',
    '/api/commitments/reject': 'admin',
    '/api/commitments/breach': 'admin',
    '/api/commitments/settle': 'admin',
    '/api/cases/open': 'admin',
    '/api/cases/stay': 'admin',
    '/api/cases/resolve': 'admin',
    '/api/treasury/contribute': 'owner',
    '/api/treasury/reserve-floor': 'owner',
    '/api/admission/admit': 'owner',
    '/api/admission/suspend': 'owner',
    '/api/admission/revoke': 'owner',
    '/api/federation/send': 'admin',
    '/api/federation/peers/upsert': 'owner',
    '/api/federation/peers/refresh': 'owner',
    '/api/federation/peers/suspend': 'owner',
    '/api/federation/peers/revoke': 'owner',
    '/api/institution/charter': 'admin',
    '/api/institution/lifecycle': 'owner',
    '/api/session/issue': 'member',
    '/api/session/revoke': 'admin',
}


def _load_workspace_credentials():
    env_user = os.environ.get('MERIDIAN_WORKSPACE_USER')
    env_password = os.environ.get('MERIDIAN_WORKSPACE_PASS')
    env_org_id = (os.environ.get('MERIDIAN_WORKSPACE_AUTH_ORG_ID') or '').strip() or None
    env_user_id = (os.environ.get('MERIDIAN_WORKSPACE_USER_ID') or '').strip() or None
    if env_user and env_password:
        return env_user, env_password, env_org_id, env_user_id
    if not os.path.exists(WORKSPACE_CREDENTIALS_FILE):
        return None, None, None, None
    user = None
    password = None
    org_id = None
    user_id = None
    with open(WORKSPACE_CREDENTIALS_FILE) as f:
        for raw_line in f:
            line = raw_line.strip()
            if line.startswith('user:'):
                user = line.split(':', 1)[1].strip()
            elif line.startswith('pass:'):
                password = line.split(':', 1)[1].strip()
            elif line.startswith('org_id:'):
                org_id = line.split(':', 1)[1].strip() or None
            elif line.startswith('user_id:'):
                user_id = line.split(':', 1)[1].strip() or None
    return user, password, org_id, user_id


def _get_founding_org():
    orgs = load_orgs()
    for oid, org in orgs['organizations'].items():
        if org.get('slug') == 'meridian':
            return oid, org
    return None, None


def _resolve_workspace_context():
    """Bind this live workspace process to the founding Meridian institution."""
    founding_org_id, founding_org = _get_founding_org()
    configured_org_id = WORKSPACE_ORG_ID
    cred_user, cred_password, credential_org_id, _credential_user_id = _load_workspace_credentials()
    credential_scope_active = bool(cred_user and cred_password and credential_org_id)
    if configured_org_id and configured_org_id != founding_org_id:
        raise RuntimeError(
            f"Live workspace only supports founding org '{founding_org_id}', got '{configured_org_id}'"
        )
    if credential_scope_active and credential_org_id != founding_org_id:
        raise RuntimeError(
            f"Live workspace credentials must scope to founding org '{founding_org_id}', got '{credential_org_id}'"
        )
    ctx = InstitutionContext.bind(
        founding_org_id,
        founding_org,
        ('configured_org' if configured_org_id else 'founding_default'),
        WORKSPACE_BOUNDARY,
    )
    _runtime_host_state(ctx.org_id)
    return ctx


def _runtime_host_state(bound_org_id):
    host_identity = load_host_identity(
        RUNTIME_HOST_IDENTITY_FILE,
        supported_boundaries=[
            'workspace',
            'cli',
            'federation_gateway',
            'mcp_service',
            'payment_monitor',
            'subscriptions',
            'accounting',
        ],
        fallback_label='Meridian Live Host',
        fallback_federation=False,
    )
    admission_registry = load_admission_registry(
        RUNTIME_ADMISSION_FILE,
        bound_org_id=bound_org_id,
        host_identity=host_identity,
    )
    ensure_org_admitted(bound_org_id, admission_registry)
    return host_identity, admission_registry


def _federation_authority(host_identity, peer_registry=None):
    return FederationAuthority(
        host_identity,
        signing_secret=FEDERATION_SIGNING_SECRET,
        peer_registry=(
            peer_registry
            if peer_registry is not None else
            load_peer_registry(FEDERATION_PEERS_FILE, host_identity=host_identity)
        ),
        replay_store=ReplayStore(FEDERATION_REPLAY_FILE),
    )


def _federation_management_state():
    return {
        'management_mode': 'founding_locked',
        'mutation_enabled': False,
        'mutation_disabled_reason': 'single_institution_deployment',
    }


def _federation_snapshot(bound_org_id, host_identity=None, admission_registry=None, peer_registry=None):
    if host_identity is None or admission_registry is None:
        host_identity, admission_registry = _runtime_host_state(bound_org_id)
    snapshot = _federation_authority(host_identity, peer_registry=peer_registry).snapshot(
        bound_org_id=bound_org_id,
        admission_registry=admission_registry,
    )
    snapshot.update(_federation_management_state())
    return snapshot


def _federation_manifest(context, host_identity=None, admission_registry=None):
    if host_identity is None or admission_registry is None:
        host_identity, admission_registry = _runtime_host_state(context.org_id)
    admission_management = _admission_management_state()
    runtime_core = runtime_core_snapshot(
        context,
        additional_institutions_allowed=bool(
            admission_management['mutation_enabled']
            or len(admission_registry.get('admitted_org_ids', [])) > 1
        ),
        host_identity=host_identity,
        admission_registry=admission_registry,
        admission_management_mode=admission_management['management_mode'],
        admission_mutation_enabled=admission_management['mutation_enabled'],
        admission_mutation_disabled_reason=admission_management['mutation_disabled_reason'],
    )
    federation = _federation_snapshot(
        context.org_id,
        host_identity=host_identity,
        admission_registry=admission_registry,
    )
    return {
        'manifest_version': 1,
        'generated_at': _now(),
        'host_identity': runtime_core['host_identity'],
        'institution_context': runtime_core['institution_context'],
        'admission': runtime_core['admission'],
        'service_registry': runtime_core['service_registry'],
        'federation': {
            key: value
            for key, value in federation.items()
            if key not in ('peers', 'trusted_peers', 'trusted_peer_ids')
        },
    }


def _admission_management_state():
    return {
        'management_mode': 'founding_locked',
        'mutation_enabled': False,
        'mutation_disabled_reason': 'single_institution_deployment',
    }


def _admission_snapshot(bound_org_id, host_identity=None, admission_registry=None):
    if host_identity is None or admission_registry is None:
        host_identity, admission_registry = _runtime_host_state(bound_org_id)
    institutions = {}
    for org_id, entry in sorted(admission_registry.get('institutions', {}).items()):
        data = dict(entry or {})
        data['org_id'] = org_id
        institutions[org_id] = data
    return {
        'bound_org_id': bound_org_id,
        'host_id': host_identity.host_id,
        'host_role': host_identity.role,
        'source': admission_registry.get('source', 'none'),
        'admitted_org_ids': list(admission_registry.get('admitted_org_ids', [])),
        'institutions': institutions,
        **_admission_management_state(),
    }


def _mutate_admission(bound_org_id, action, target_org_id):
    raise PermissionError(
        f"Live runtime remains founding-only; admission mutation '{action}' is disabled "
        f"for institution '{bound_org_id}'"
    )


def _commitment_management_state():
    return {
        'management_mode': 'founding_workspace_local',
        'mutation_enabled': True,
        'mutation_disabled_reason': '',
    }


def _commitment_snapshot(bound_org_id):
    summary = commitments.commitment_summary(bound_org_id)
    return {
        'bound_org_id': bound_org_id,
        **_commitment_management_state(),
        **summary,
        'commitments': commitments.list_commitments(bound_org_id),
    }


def _case_management_state():
    return {
        'management_mode': 'founding_workspace_local',
        'mutation_enabled': True,
        'mutation_disabled_reason': '',
    }


def _case_snapshot(bound_org_id):
    return {
        'bound_org_id': bound_org_id,
        **_case_management_state(),
        **cases.case_summary(bound_org_id),
        'blocking_commitment_ids': cases.blocking_commitment_ids(bound_org_id),
        'blocked_peer_host_ids': cases.blocked_peer_host_ids(bound_org_id),
        'cases': cases.list_cases(bound_org_id),
    }


def _maybe_open_case_for_commitment_breach(commitment_record, actor_id, *, org_id, note=''):
    return cases.ensure_case_for_commitment_breach(
        commitment_record,
        actor_id,
        org_id=org_id,
        note=note,
    )


def _maybe_stay_warrant_for_case(case_record, actor_id, *, org_id, session_id=None, note=''):
    warrant_id = (case_record or {}).get('linked_warrant_id', '').strip()
    if not warrant_id:
        return None
    record = next(
        (item for item in list_warrants(org_id) if item.get('warrant_id') == warrant_id),
        None,
    )
    if not record:
        return {
            'applied': False,
            'warrant_id': warrant_id,
            'reason': 'warrant_not_found',
            'court_review_state': '',
            'execution_state': '',
        }
    review_state = record.get('court_review_state', '')
    execution_state = record.get('execution_state', '')
    if execution_state != 'ready':
        return {
            'applied': False,
            'warrant_id': warrant_id,
            'reason': f'execution_state_{execution_state or "unknown"}',
            'court_review_state': review_state,
            'execution_state': execution_state,
            'warrant': record,
        }
    if review_state == 'stayed':
        return {
            'applied': False,
            'warrant_id': warrant_id,
            'reason': 'already_stayed',
            'court_review_state': review_state,
            'execution_state': execution_state,
            'warrant': record,
        }
    if review_state == 'revoked':
        return {
            'applied': False,
            'warrant_id': warrant_id,
            'reason': 'already_revoked',
            'court_review_state': review_state,
            'execution_state': execution_state,
            'warrant': record,
        }
    stayed = review_warrant(
        warrant_id,
        'stay',
        actor_id,
        org_id=org_id,
        note=note or f"Automatically stayed because case {case_record.get('case_id', '')} is active",
    )
    log_event(
        org_id,
        actor_id,
        'warrant_stayed_for_case',
        outcome='success',
        resource=warrant_id,
        details={
            'case_id': case_record.get('case_id', ''),
            'claim_type': case_record.get('claim_type', ''),
            'previous_court_review_state': review_state,
            'court_review_state': stayed.get('court_review_state', ''),
        },
        session_id=session_id,
    )
    return {
        'applied': True,
        'warrant_id': warrant_id,
        'reason': 'case_hold_applied',
        'court_review_state': stayed.get('court_review_state', ''),
        'execution_state': stayed.get('execution_state', ''),
        'warrant': stayed,
    }


def _blocking_case_for_delivery(*, org_id, commitment_id='', target_host_id=''):
    try:
        commitment_case = cases.blocking_commitment_case(commitment_id, org_id=org_id)
        if commitment_case:
            return commitment_case
        return cases.blocking_peer_case(target_host_id, org_id=org_id)
    except (SystemExit, ValueError):
        return None


def _maybe_suspend_peer_for_case(case_record, actor_id, *, org_id, session_id=None):
    if not cases.case_requires_peer_block(case_record):
        return None
    target_host_id = (case_record.get('target_host_id') or '').strip()
    if not target_host_id:
        return None
    management = _federation_management_state()
    return {
        'applied': False,
        'peer_host_id': target_host_id,
        'reason': management['mutation_disabled_reason'],
        'trust_state': '',
    }


def _delivery_failure_claim_type(error_message):
    message = (error_message or '').lower()
    if not message:
        return ''
    if 'signature verification failed' in message:
        return 'fraudulent_proof'
    if 'receiver_host_id' in message or 'receiver_institution_id' in message:
        return 'misrouted_execution'
    if 'receipt' in message:
        return 'invalid_settlement_notice'
    return ''


def _maybe_open_case_for_delivery_failure(exc, actor_id, *, org_id, target_host_id,
                                          target_institution_id, commitment_id='',
                                          warrant_id='', session_id=None):
    claim_type = _delivery_failure_claim_type(str(exc))
    if not claim_type:
        return None, None
    try:
        case_record, created = cases.ensure_case_for_delivery_failure(
            claim_type,
            actor_id,
            org_id=org_id,
            target_host_id=target_host_id,
            target_institution_id=target_institution_id,
            linked_commitment_id=commitment_id,
            linked_warrant_id=warrant_id,
            note=str(exc),
            metadata={
                'peer_host_id': exc.peer_host_id,
                'error': str(exc),
            },
        )
    except (SystemExit, ValueError):
        return None, None
    if created:
        log_event(
            org_id,
            actor_id,
            'case_opened',
            resource=case_record['case_id'],
            outcome='success',
            details={
                'claim_type': case_record.get('claim_type', ''),
                'linked_commitment_id': case_record.get('linked_commitment_id', ''),
                'source': 'federation_delivery_failure',
            },
            session_id=session_id,
        )
    return case_record, _maybe_suspend_peer_for_case(
        case_record,
        actor_id,
        org_id=org_id,
        session_id=session_id,
    )


def _accept_federation_request(bound_org_id, envelope, payload=None):
    host_identity, admission_registry = _runtime_host_state(bound_org_id)
    authority = _federation_authority(host_identity)
    claims = authority.accept(
        envelope,
        payload=payload,
        expected_target_host_id=host_identity.host_id,
        expected_target_org_id=bound_org_id,
        expected_boundary_name='federation_gateway',
    )
    return claims, authority.snapshot(
        bound_org_id=bound_org_id,
        admission_registry=admission_registry,
    )


def _mutate_federation_peer(bound_org_id, action, payload):
    raise PermissionError(
        f"Live runtime remains founding-only; federation peer mutation '{action}' is disabled "
        f"for institution '{bound_org_id}'"
    )


def _federation_claims_dict(claims):
    if not claims:
        return {}
    if isinstance(claims, dict):
        return dict(claims)
    if hasattr(claims, 'to_dict'):
        return claims.to_dict()
    return {}


def _federation_audit_details(claims, **extra):
    claim_data = _federation_claims_dict(claims)
    details = {
        'envelope_id': claim_data.get('envelope_id', ''),
        'source_host_id': claim_data.get('source_host_id', ''),
        'source_institution_id': claim_data.get('source_institution_id', ''),
        'target_host_id': claim_data.get('target_host_id', ''),
        'target_institution_id': claim_data.get('target_institution_id', ''),
        'nonce': claim_data.get('nonce', ''),
        'boundary_name': claim_data.get('boundary_name', ''),
        'warrant_id': claim_data.get('warrant_id', ''),
        'commitment_id': claim_data.get('commitment_id', ''),
    }
    for key, value in extra.items():
        if value not in (None, ''):
            details[key] = value
    return details


def _federation_receipt(bound_org_id, receiver_host_id, claims):
    claim_data = _federation_claims_dict(claims)
    envelope_id = claim_data.get('envelope_id', '')
    receipt_material = ':'.join((
        (receiver_host_id or '').strip(),
        (bound_org_id or '').strip(),
        envelope_id,
    ))
    receipt_id = 'fedrcpt_' + hashlib.sha256(receipt_material.encode('utf-8')).hexdigest()[:12]
    return {
        'receipt_id': receipt_id,
        'envelope_id': envelope_id,
        'accepted_at': _now(),
        'receiver_host_id': (receiver_host_id or '').strip(),
        'receiver_institution_id': (bound_org_id or '').strip(),
        'message_type': claim_data.get('message_type', ''),
        'boundary_name': claim_data.get('boundary_name', ''),
        'identity_model': 'signed_host_service',
    }


def _federation_peer_state(peer_host_id, *, host_identity=None):
    peer_host_id = (peer_host_id or '').strip()
    if not peer_host_id:
        return None
    try:
        registry = load_peer_registry(
            FEDERATION_PEERS_FILE,
            host_identity=host_identity or load_host_identity(RUNTIME_HOST_IDENTITY_FILE),
        )
    except RuntimeError:
        return {
            'applied': False,
            'peer_host_id': peer_host_id,
            'reason': 'peer_registry_unavailable',
        }
    peer = registry.get('peers', {}).get(peer_host_id)
    if not peer:
        return {
            'applied': False,
            'peer_host_id': peer_host_id,
            'reason': 'peer_not_registered',
        }
    return {
        'applied': False,
        'peer_host_id': peer.host_id,
        'trust_state': peer.trust_state,
        'admitted_org_ids': list(peer.admitted_org_ids),
        'label': peer.label,
        'reason': 'case_blocked',
    }


def _deliver_federation_envelope(bound_org_id, target_host_id, target_org_id,
                                 message_type, payload=None, *,
                                 actor_type='host_service', actor_id='',
                                 session_id='', warrant_id='',
                                 commitment_id='', ttl_seconds=None):
    host_identity, admission_registry = _runtime_host_state(bound_org_id)
    authority = _federation_authority(host_identity)
    authority.ensure_enabled()
    execution_warrant = None
    required_action = warrant_action_for_message(message_type)
    if required_action:
        if not warrant_id:
            message = (
                f"Federation message_type '{message_type}' requires warrant_id "
                f"for action_class '{required_action}'"
            )
            log_event(
                bound_org_id,
                actor_id or f'host:{host_identity.host_id}',
                'federation_warrant_blocked',
                resource=message_type,
                outcome='blocked',
                actor_type=actor_type or 'service',
                details={
                    'target_host_id': target_host_id,
                    'target_institution_id': target_org_id,
                    'required_action_class': required_action,
                    'error': message,
                },
                session_id=session_id or None,
            )
            raise PermissionError(message)
        try:
            execution_warrant = validate_warrant_for_execution(
                warrant_id,
                org_id=bound_org_id,
                action_class=required_action,
                boundary_name='federation_gateway',
                actor_id=actor_id,
                session_id=session_id,
                request_payload=payload,
            )
        except PermissionError as exc:
            log_event(
                bound_org_id,
                actor_id or f'host:{host_identity.host_id}',
                'federation_warrant_blocked',
                resource=message_type,
                outcome='blocked',
                actor_type=actor_type or 'service',
                details={
                    'target_host_id': target_host_id,
                    'target_institution_id': target_org_id,
                    'warrant_id': warrant_id,
                    'required_action_class': required_action,
                    'error': str(exc),
                },
                session_id=session_id or None,
            )
            raise
    commitment_record = None
    if commitment_id:
        try:
            commitment_record = commitments.validate_commitment_for_delivery(
                commitment_id,
                target_host_id=target_host_id,
                target_institution_id=target_org_id,
                org_id=bound_org_id,
            )
        except (PermissionError, ValueError) as exc:
            log_event(
                bound_org_id,
                actor_id or f'host:{host_identity.host_id}',
                'federation_commitment_blocked',
                resource=message_type,
                outcome='blocked',
                actor_type=actor_type or 'service',
                details={
                    'target_host_id': target_host_id,
                    'target_institution_id': target_org_id,
                    'commitment_id': commitment_id,
                    'error': str(exc),
                },
                session_id=session_id or None,
            )
            raise
    blocking_case = _blocking_case_for_delivery(
        org_id=bound_org_id,
        commitment_id=commitment_id,
        target_host_id=target_host_id,
    )
    if blocking_case:
        message = (
            f"Federation delivery is blocked by case '{blocking_case.get('case_id', '')}' "
            f"(claim_type={blocking_case.get('claim_type', '')}, "
            f"status={blocking_case.get('status', '')})"
        )
        log_event(
            bound_org_id,
            actor_id or f'host:{host_identity.host_id}',
            'federation_case_blocked',
            resource=message_type,
            outcome='blocked',
            actor_type=actor_type or 'service',
            details={
                'target_host_id': target_host_id,
                'target_institution_id': target_org_id,
                'commitment_id': commitment_id,
                'case_id': blocking_case.get('case_id', ''),
                'claim_type': blocking_case.get('claim_type', ''),
                'case_status': blocking_case.get('status', ''),
                'error': message,
            },
            session_id=session_id or None,
        )
        error = PermissionError(message)
        error.case_record = blocking_case
        error.federation_peer = _federation_peer_state(
            target_host_id,
            host_identity=host_identity,
        )
        error.warrant = _maybe_stay_warrant_for_case(
            blocking_case,
            actor_id or f'host:{host_identity.host_id}',
            org_id=bound_org_id,
            session_id=session_id or None,
            note=message,
        )
        raise error
    try:
        delivery = authority.deliver(
            target_host_id,
            bound_org_id,
            target_org_id,
            message_type,
            payload=payload,
            actor_type=actor_type,
            actor_id=actor_id,
            session_id=session_id,
            warrant_id=warrant_id,
            commitment_id=commitment_id,
            ttl_seconds=ttl_seconds,
        )
    except FederationDeliveryError as exc:
        case_record, federation_peer = _maybe_open_case_for_delivery_failure(
            exc,
            actor_id or f'host:{host_identity.host_id}',
            org_id=bound_org_id,
            target_host_id=target_host_id,
            target_institution_id=target_org_id,
            commitment_id=commitment_id,
            warrant_id=warrant_id,
            session_id=session_id or None,
        )
        warrant_hold = _maybe_stay_warrant_for_case(
            case_record,
            actor_id or f'host:{host_identity.host_id}',
            org_id=bound_org_id,
            session_id=session_id or None,
            note=str(exc),
        )
        log_event(
            bound_org_id,
            actor_id or f'host:{host_identity.host_id}',
            'federation_envelope_delivery_failed',
            resource=message_type,
            outcome='failed',
            actor_type=actor_type or 'service',
            details=_federation_audit_details(
                exc.claims,
                target_host_id=exc.peer_host_id or target_host_id,
                target_institution_id=target_org_id,
                error=str(exc),
            ),
            session_id=session_id or None,
        )
        exc.case_record = case_record
        exc.federation_peer = federation_peer
        exc.warrant = warrant_hold
        raise

    claims = delivery.get('claims')
    receipt = dict(delivery.get('receipt') or {})
    if not receipt and isinstance(delivery.get('response'), dict):
        receipt = dict(delivery['response'].get('receipt') or {})
    commitment_delivery_ref = None
    if commitment_record:
        commitment_delivery_ref = commitments.record_delivery_ref(
            commitment_record['commitment_id'],
            {
                'delivered_at': _now(),
                'target_host_id': target_host_id,
                'target_institution_id': target_org_id,
                'message_type': message_type,
                'peer_transport': (delivery.get('peer') or {}).get('transport', ''),
                'envelope_id': (claims or {}).get('envelope_id', ''),
                'receipt_id': receipt.get('receipt_id', ''),
                'receiver_host_id': receipt.get('receiver_host_id', ''),
                'receiver_institution_id': receipt.get('receiver_institution_id', ''),
                'actor_id': actor_id or f'host:{host_identity.host_id}',
                'session_id': session_id or '',
                'warrant_id': warrant_id or '',
            },
            org_id=bound_org_id,
        )
        log_event(
            bound_org_id,
            actor_id or f'host:{host_identity.host_id}',
            'commitment_delivery_recorded',
            resource=commitment_record['commitment_id'],
            outcome='success',
            actor_type=actor_type or 'service',
            details={
                'target_host_id': target_host_id,
                'target_institution_id': target_org_id,
                'message_type': message_type,
                'receipt_id': receipt.get('receipt_id', ''),
            },
            session_id=session_id or None,
        )
    if execution_warrant:
        mark_warrant_executed(
            warrant_id,
            org_id=bound_org_id,
            execution_refs={
                'message_type': message_type,
                'envelope_id': (claims or {}).get('envelope_id', ''),
                'target_host_id': target_host_id,
                'target_institution_id': target_org_id,
                'receipt_id': receipt.get('receipt_id', ''),
                'receiver_host_id': receipt.get('receiver_host_id', ''),
                'receiver_institution_id': receipt.get('receiver_institution_id', ''),
            },
        )
    log_event(
        bound_org_id,
        actor_id or f'host:{host_identity.host_id}',
        'federation_envelope_sent',
        resource=message_type,
        outcome='accepted',
        actor_type=actor_type or 'service',
        details=_federation_audit_details(
            claims,
            peer_transport=(delivery.get('peer') or {}).get('transport', ''),
            receipt_id=receipt.get('receipt_id', ''),
            receiver_host_id=receipt.get('receiver_host_id', ''),
            receiver_institution_id=receipt.get('receiver_institution_id', ''),
            commitment_delivery_recorded=bool(commitment_delivery_ref),
        ),
        session_id=session_id or None,
    )
    return delivery, authority.snapshot(
        bound_org_id=bound_org_id,
        admission_registry=admission_registry,
    )


def _requested_org_override(parsed_url, headers):
    query_org_ids = [value for value in parse_qs(parsed_url.query).get('org_id', []) if value]
    header_org_id = (headers.get('X-Meridian-Org-Id', '') or '').strip()
    requested = set(query_org_ids + ([header_org_id] if header_org_id else []))
    if not requested:
        return None
    if len(requested) > 1:
        raise ValueError('Conflicting institution context hints in request')
    return requested.pop()


def _enforce_request_context(parsed_url, headers, bound_org_id):
    requested_org_id = _requested_org_override(parsed_url, headers)
    if requested_org_id and requested_org_id != bound_org_id:
        raise ValueError(
            f"Workspace is bound to institution '{bound_org_id}'. "
            f"Request-level override '{requested_org_id}' is not allowed."
        )
    return {
        'mode': 'process_bound',
        'bound_org_id': bound_org_id,
        'request_override': 'exact-match-only',
        'requested_org_id': requested_org_id,
    }


def _resolve_auth_context(bound_org_id):
    user, password, credential_org_id, credential_user_id = _load_workspace_credentials()
    auth_enabled = bool(user and password)
    if credential_org_id and auth_enabled and credential_org_id != bound_org_id:
        raise RuntimeError(
            f"Live workspace credentials must scope to bound org '{bound_org_id}', got '{credential_org_id}'"
        )
    resolved_user_id = None
    actor_source = None
    if credential_user_id:
        resolved_user_id = credential_user_id
        actor_source = 'credentials'
    elif user and _member_role(bound_org_id, user):
        resolved_user_id = user
        actor_source = 'basic_user_id'
    elif user == 'owner':
        org = load_orgs().get('organizations', {}).get(bound_org_id)
        owner_id = (org or {}).get('owner_id')
        if owner_id:
            resolved_user_id = owner_id
            actor_source = 'owner_alias'
    role = _member_role(bound_org_id, resolved_user_id)
    actor_id = resolved_user_id or (f'workspace_user:{user}' if user else None)
    if not auth_enabled:
        return {
            'enabled': False,
            'mode': 'required_missing' if WORKSPACE_AUTH_REQUIRED else 'disabled',
            'org_id': None,
            'user_id': None,
            'role': None,
            'actor_id': None,
            'actor_source': None,
        }
    if credential_org_id:
        return {
            'enabled': True,
            'mode': 'credential_bound',
            'org_id': credential_org_id,
            'user_id': resolved_user_id,
            'role': role,
            'actor_id': actor_id,
            'actor_source': actor_source or 'credentials',
        }
    return {
        'enabled': True,
        'mode': 'process_bound_basic',
        'org_id': bound_org_id,
        'user_id': resolved_user_id,
        'role': role,
        'actor_id': actor_id,
        'actor_source': actor_source or 'basic_user',
    }


def _resolve_auth_context_from_session(claims, bound_org_id):
    """Build auth context from validated session claims.

    The session records the role at issuance, but we re-check live membership
    so that removed or downgraded members lose access immediately.
    """
    if claims.org_id != bound_org_id:
        raise ValueError(
            f"Session is bound to institution '{claims.org_id}', "
            f"but workspace is bound to '{bound_org_id}'"
        )
    current_role = _member_role(bound_org_id, claims.user_id)
    return {
        'enabled': True,
        'mode': 'session_bound',
        'org_id': bound_org_id,
        'user_id': claims.user_id,
        'role': current_role,
        'actor_id': claims.user_id,
        'actor_source': 'session',
        'session_id': claims.session_id,
    }


def _member_role(org_id, user_id):
    if not org_id or not user_id:
        return None
    org = load_orgs().get('organizations', {}).get(org_id)
    if not org:
        return None
    for member in org.get('members', []):
        if member.get('user_id') == user_id:
            return member.get('role')
    return None


def _required_mutation_role(path):
    return MUTATION_ROLE_REQUIREMENTS.get(path, 'admin')


def _enforce_mutation_authorization(auth_context, org_id, path):
    if not auth_context.get('enabled'):
        raise PermissionError('Workspace auth is required for mutations')
    role = auth_context.get('role')
    if not role:
        raise PermissionError(
            f"Workspace credential actor is not a member of institution '{org_id}'"
        )
    required_role = _required_mutation_role(path)
    if ROLE_RANK.get(role, -1) < ROLE_RANK.get(required_role, 99):
        raise PermissionError(
            f"Workspace actor role '{role}' cannot mutate '{path}'; requires {required_role}"
        )
    return required_role


def _permission_snapshot(auth_context):
    role = auth_context.get('role')
    permissions = {}
    for path, required_role in MUTATION_ROLE_REQUIREMENTS.items():
        permissions[path] = {
            'required_role': required_role,
            'allowed': bool(
                auth_context.get('enabled')
                and role
                and ROLE_RANK.get(role, -1) >= ROLE_RANK.get(required_role, 99)
            ),
        }
    return {
        'read_allowed': bool(auth_context.get('enabled') or not WORKSPACE_AUTH_REQUIRED),
        'mutation_paths': permissions,
    }


def _warrant_summary(org_id):
    warrants = list_warrants(org_id)
    pending_review = 0
    executable = 0
    executed = 0
    for record in warrants:
        if record.get('court_review_state') == 'pending_review':
            pending_review += 1
        if record.get('court_review_state') in ('auto_issued', 'approved') and record.get('execution_state') == 'ready':
            executable += 1
        if record.get('execution_state') == 'executed':
            executed += 1
    return {
        'total': len(warrants),
        'pending_review': pending_review,
        'executable': executable,
        'executed': executed,
    }


# ── API data builders ────────────────────────────────────────────────────────

def api_status(context_source='founding_default', institution_context=None):
    if institution_context is not None:
        inst_ctx = institution_context
    else:
        inst_ctx = _resolve_workspace_context()
    org_id = inst_ctx.org_id
    org = inst_ctx.org
    context_source = inst_ctx.context_source
    host_identity, admission_registry = _runtime_host_state(org_id)
    auth_context = _resolve_auth_context(org_id)
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

    result = {
        'context': {
            'mode': 'process_bound',
            'bound_org_id': org_id,
            'source': context_source,
            'request_override': 'exact-match-only',
            'auth': auth_context,
            'permissions': _permission_snapshot(auth_context),
        },
        'runtime_core': runtime_core_snapshot(
            inst_ctx,
            additional_institutions_allowed=False,
            second_institution_path=(
                'Live runtime remains founding-only. Admitting a second institution '
                'requires a new runtime program; this deployment does not expose '
                'shared multi-institution routing.'
            ),
            host_identity=host_identity,
            admission_registry=admission_registry,
            admission_management_mode=_admission_management_state()['management_mode'],
            admission_mutation_enabled=_admission_management_state()['mutation_enabled'],
            admission_mutation_disabled_reason=_admission_management_state()['mutation_disabled_reason'],
        ),
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
        'warrants': _warrant_summary(org_id),
        'commitments': _commitment_snapshot(org_id),
        'cases': _case_snapshot(org_id),
        'ci_vertical': _ci_vertical_status(reg, lead_id, org_id),
        'remediations': remediations,
        'timestamp': _now(),
    }
    result['runtime_core']['federation'] = _federation_snapshot(
        org_id,
        host_identity=host_identity,
        admission_registry=admission_registry,
    )
    return result


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
<p class="subtitle">Constitutional Operating System — Six Primitives</p>

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

var currentContext = null;
function can(path) {
  var perms = currentContext && currentContext.permissions && currentContext.permissions.mutation_paths;
  return !!(perms && perms[path] && perms[path].allowed);
}
function requiredRole(path) {
  var perms = currentContext && currentContext.permissions && currentContext.permissions.mutation_paths;
  return perms && perms[path] ? perms[path].required_role : 'admin';
}
function requirePermission(path) {
  if (can(path)) return true;
  toast('This action requires ' + requiredRole(path) + ' role.');
  return false;
}

function riskTag(state) {
  if (state === 'critical') return '<span class="tag tag-crit">CRITICAL</span>';
  if (state === 'elevated') return '<span class="tag tag-warn">ELEVATED</span>';
  if (state === 'suspended') return '<span class="tag tag-crit">SUSPENDED</span>';
  return '<span class="tag tag-live">NOMINAL</span>';
}

function render(data) {
  currentContext = data.context || null;
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
  if (data.context && data.context.auth && data.context.auth.actor_id) {
    sb += '<span class="item">Actor: <strong>' + data.context.auth.actor_id + '</strong> (' + (data.context.auth.role || 'unbound') + ')</span>';
  }
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
  if (!requirePermission('/api/authority/kill-switch')) return;
  var reason = engage ? prompt('Reason for engaging kill switch:') : '';
  if (engage && !reason) return;
  api('POST', '/api/authority/kill-switch', { engage: engage, reason: reason })
    .then(function(r) { toast(r.message); refresh(); });
}

function decideApproval(id, decision) {
  if (!requirePermission('/api/authority/approve')) return;
  var reason = prompt('Reason (' + decision + '):') || '';
  api('POST', '/api/authority/approve', { approval_id: id, decision: decision, reason: reason })
    .then(function(r) { toast(r.message); refresh(); });
}

function requestApproval() {
  if (!requirePermission('/api/authority/request')) return;
  api('POST', '/api/authority/request', {
    agent: document.getElementById('apr-agent').value,
    action: document.getElementById('apr-action').value,
    resource: document.getElementById('apr-resource').value,
    cost: parseFloat(document.getElementById('apr-cost').value) || 0
  }).then(function(r) { toast(r.message); refresh(); });
}

function createDelegation() {
  if (!requirePermission('/api/authority/delegate')) return;
  api('POST', '/api/authority/delegate', {
    from: document.getElementById('dlg-from').value,
    to: document.getElementById('dlg-to').value,
    scopes: document.getElementById('dlg-scopes').value,
    hours: parseInt(document.getElementById('dlg-hours').value) || 24
  }).then(function(r) { toast(r.message); refresh(); });
}

function revokeDelegation(id) {
  if (!requirePermission('/api/authority/revoke')) return;
  if (!confirm('Revoke delegation ' + id + '?')) return;
  api('POST', '/api/authority/revoke', { delegation_id: id })
    .then(function(r) { toast(r.message); refresh(); });
}

function fileViolation() {
  if (!requirePermission('/api/court/file')) return;
  api('POST', '/api/court/file', {
    agent: document.getElementById('vio-agent').value,
    type: document.getElementById('vio-type').value,
    severity: parseInt(document.getElementById('vio-severity').value),
    evidence: document.getElementById('vio-evidence').value,
    policy_ref: document.getElementById('vio-policy').value
  }).then(function(r) { toast(r.message); refresh(); });
}

function resolveViolation(id) {
  if (!requirePermission('/api/court/resolve')) return;
  var note = prompt('Resolution note:');
  if (!note) return;
  api('POST', '/api/court/resolve', { violation_id: id, note: note })
    .then(function(r) { toast(r.message); refresh(); });
}

function fileAppeal(vid, agent) {
  if (!requirePermission('/api/court/appeal')) return;
  var grounds = prompt('Appeal grounds:');
  if (!grounds) return;
  api('POST', '/api/court/appeal', { violation_id: vid, agent: agent, grounds: grounds })
    .then(function(r) { toast(r.message); refresh(); });
}

function decideAppealAction(id, decision) {
  if (!requirePermission('/api/court/decide-appeal')) return;
  api('POST', '/api/court/decide-appeal', { appeal_id: id, decision: decision })
    .then(function(r) { toast(r.message); refresh(); });
}

function autoReview() {
  if (!requirePermission('/api/court/auto-review')) return;
  api('POST', '/api/court/auto-review', {})
    .then(function(r) { toast(r.message); refresh(); });
}

function remediateAgent(agentId, agentName) {
  if (!requirePermission('/api/court/remediate')) return;
  var note = prompt('Remediation note for ' + agentName + ':') || '';
  api('POST', '/api/court/remediate', { agent_id: agentId, note: note })
    .then(function(r) { toast(r.message); refresh(); });
}

function contributeCapital() {
  if (!requirePermission('/api/treasury/contribute')) return;
  var amount = parseFloat(document.getElementById('cap-amount').value) || 0;
  var note = document.getElementById('cap-note').value || 'owner capital contribution';
  if (amount <= 0) return toast('Capital amount must be greater than 0');
  api('POST', '/api/treasury/contribute', { amount: amount, note: note })
    .then(function(r) { toast(r.message || r.error); refresh(); });
}

function updateReserveFloor() {
  if (!requirePermission('/api/treasury/reserve-floor')) return;
  var amount = parseFloat(document.getElementById('reserve-amount').value);
  var note = document.getElementById('reserve-note').value || 'reserve policy change';
  if (isNaN(amount) || amount < 0) return toast('Reserve floor must be 0 or greater');
  if (!confirm('Update reserve floor to $' + amount.toFixed(2) + '?')) return;
  api('POST', '/api/treasury/reserve-floor', { amount: amount, note: note })
    .then(function(r) { toast(r.message || r.error); refresh(); });
}

function setCharter() {
  if (!requirePermission('/api/institution/charter')) return;
  var text = document.getElementById('charter-text').value;
  api('POST', '/api/institution/charter', { text: text })
    .then(function(r) { toast(r.message); refresh(); });
}

function setLifecycle() {
  if (!requirePermission('/api/institution/lifecycle')) return;
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
        header = self.headers.get('Authorization', '')
        # Bearer (session) auth — always available
        if header.startswith('Bearer '):
            token = header.split(' ', 1)[1].strip()
            return _session_authority.validate(token) is not None
        # Basic auth
        user, password, _credential_org_id, _credential_user_id = _load_workspace_credentials()
        if not user or not password:
            return False
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
        # Session validate is a passive introspection endpoint — the token is the proof.
        protected = (path == '/' or path.startswith('/workspace') or path.startswith('/api/')) \
            and path not in ('/api/session/validate', '/api/federation/manifest')
        if not protected:
            return True
        if self._is_authorized():
            return True
        user, password, _credential_org_id, _credential_user_id = _load_workspace_credentials()
        if not user or not password:
            if WORKSPACE_AUTH_REQUIRED:
                self._service_unavailable('Workspace auth is required but credentials are not configured',
                                          is_api=path.startswith('/api/'))
                return False
            return True
        self._unauthorized(is_api=path.startswith('/api/'))
        return False

    def _session_claims_from_request(self, expected_org_id=None):
        """Extract and validate session claims from a Bearer token."""
        header = self.headers.get('Authorization', '')
        if not header.startswith('Bearer '):
            return None
        token = header.split(' ', 1)[1].strip()
        return _session_authority.validate(token, expected_org_id=expected_org_id)

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
        self.send_header('Access-Control-Allow-Headers', 'Content-Type, Authorization, X-Meridian-Org-Id')
        self.end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        if not self._require_auth(path):
            return
        try:
            inst_ctx = _resolve_workspace_context()
            org_id = inst_ctx.org_id
            org = inst_ctx.org
            context_source = inst_ctx.context_source
            request_context = _enforce_request_context(parsed, self.headers, org_id)
            session_claims = self._session_claims_from_request(expected_org_id=org_id)
            if session_claims:
                auth_context = _resolve_auth_context_from_session(session_claims, org_id)
            elif self.headers.get('Authorization', '').startswith('Bearer '):
                return self._json({
                    'error': 'Session token is not valid for this institution'
                }, 403)
            else:
                auth_context = _resolve_auth_context(org_id)
        except RuntimeError as e:
            return self._service_unavailable(str(e), is_api=path.startswith('/api/'))
        except ValueError as e:
            return self._json({'error': str(e)}, 400)

        if path == '/' or path == '/workspace':
            return self._html(DASHBOARD_HTML)
        elif path == '/api/status':
            return self._json(api_status(institution_context=inst_ctx))
        elif path == '/api/context':
            host_identity, admission_registry = _runtime_host_state(org_id)
            response = {
                **request_context,
                'auth': auth_context,
                'permissions': _permission_snapshot(auth_context),
                'institution': inst_ctx.to_dict(),
                'runtime_core': runtime_core_snapshot(
                    inst_ctx,
                    additional_institutions_allowed=False,
                    second_institution_path=(
                        'Live runtime remains founding-only. Admitting a second '
                        'institution requires a new runtime program; this '
                        'deployment does not expose shared multi-institution routing.'
                    ),
                    host_identity=host_identity,
                    admission_registry=admission_registry,
                    admission_management_mode=_admission_management_state()['management_mode'],
                    admission_mutation_enabled=_admission_management_state()['mutation_enabled'],
                    admission_mutation_disabled_reason=_admission_management_state()['mutation_disabled_reason'],
                ),
            }
            response['runtime_core']['federation'] = _federation_snapshot(
                org_id,
                host_identity=host_identity,
                admission_registry=admission_registry,
            )
            return self._json(response)
        elif path == '/api/institution':
            return self._json(org or {})
        elif path == '/api/agents':
            reg = load_registry()
            return self._json([a for a in reg['agents'].values() if a.get('org_id') in (None, '', org_id)])
        elif path == '/api/authority':
            queue = _load_queue(org_id)
            lead_id, lead_auth = get_sprint_lead(org_id)
            return self._json({
                'kill_switch': queue['kill_switch'],
                'pending_approvals': get_pending_approvals(org_id=org_id),
                'delegations': [d for d in queue['delegations'].values() if d.get('org_id') in (None, '', org_id)],
                'sprint_lead': {'agent_id': lead_id, 'auth': lead_auth},
            })
        elif path == '/api/treasury':
            return self._json(treasury_snapshot(org_id))
        elif path == '/api/federation':
            host_identity, admission_registry = _runtime_host_state(org_id)
            return self._json(_federation_snapshot(
                org_id,
                host_identity=host_identity,
                admission_registry=admission_registry,
            ))
        elif path == '/api/federation/peers':
            host_identity, admission_registry = _runtime_host_state(org_id)
            return self._json(_federation_snapshot(
                org_id,
                host_identity=host_identity,
                admission_registry=admission_registry,
            ))
        elif path == '/api/federation/manifest':
            host_identity, admission_registry = _runtime_host_state(org_id)
            return self._json(_federation_manifest(
                inst_ctx,
                host_identity=host_identity,
                admission_registry=admission_registry,
            ))
        elif path == '/api/admission':
            host_identity, admission_registry = _runtime_host_state(org_id)
            return self._json(_admission_snapshot(
                org_id,
                host_identity=host_identity,
                admission_registry=admission_registry,
            ))
        elif path == '/api/session/validate':
            qs = parse_qs(parsed.query)
            token = None
            token_list = qs.get('token', [])
            if token_list:
                token = token_list[0]
            else:
                auth_header = self.headers.get('Authorization', '')
                if auth_header.startswith('Bearer '):
                    token = auth_header.split(' ', 1)[1].strip()
            if not token:
                return self._json({'error': 'No token provided — pass ?token= or Authorization: Bearer'}, 400)
            claims = _session_authority.validate(token, expected_org_id=org_id)
            if claims is None:
                return self._json({'valid': False})
            return self._json({'valid': True, 'claims': claims.to_dict()})
        elif path == '/api/court':
            records = _load_records(org_id)
            return self._json({
                'violations': [v for v in records['violations'].values() if v.get('org_id') in (None, '', org_id)],
                'appeals': [a for a in records['appeals'].values() if a.get('org_id') in (None, '', org_id)],
            })
        elif path == '/api/warrants':
            return self._json({
                'warrants': list_warrants(org_id),
                'summary': _warrant_summary(org_id),
            })
        elif path == '/api/commitments':
            return self._json(_commitment_snapshot(org_id))
        elif path == '/api/cases':
            return self._json(_case_snapshot(org_id))
        elif path == '/api/ci-vertical':
            reg = load_registry()
            lead_id, _ = get_sprint_lead(org_id)
            return self._json(_ci_vertical_status(reg, lead_id, org_id))
        elif path == '/api/audit':
            events = query_events(org_id=org_id, limit=30)
            events.reverse()
            return self._json({'events': events})
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path
        if path == '/api/federation/receive':
            try:
                inst_ctx = _resolve_workspace_context()
                body = self._read_body()
                envelope = (body.get('envelope') or '').strip()
                if not envelope:
                    return self._json({'error': 'Federation envelope is required'}, 400)
                claims, federation_state = _accept_federation_request(
                    inst_ctx.org_id,
                    envelope,
                    payload=body.get('payload'),
                )
                receipt = _federation_receipt(
                    inst_ctx.org_id,
                    federation_state.get('host_id', ''),
                    claims,
                )
                log_event(
                    inst_ctx.org_id,
                    claims.actor_id or f'peer:{claims.source_host_id}',
                    'federation_envelope_received',
                    resource=claims.message_type,
                    outcome='accepted',
                    actor_type=claims.actor_type or 'service',
                    details={
                        'envelope_id': claims.envelope_id,
                        'source_host_id': claims.source_host_id,
                        'source_institution_id': claims.source_institution_id,
                        'target_host_id': claims.target_host_id,
                        'target_institution_id': claims.target_institution_id,
                        'nonce': claims.nonce,
                        'boundary_name': claims.boundary_name,
                        'warrant_id': claims.warrant_id,
                        'commitment_id': claims.commitment_id,
                        'receipt_id': receipt['receipt_id'],
                    },
                    session_id=claims.session_id or None,
                )
                return self._json({
                    'message': 'Federation envelope accepted',
                    'claims': claims.to_dict(),
                    'receipt': receipt,
                    'runtime_core': {
                        'federation': federation_state,
                    },
                })
            except FederationUnavailable as e:
                return self._json({'error': str(e)}, 503)
            except FederationReplayError as e:
                return self._json({'error': str(e)}, 409)
            except FederationValidationError as e:
                return self._json({'error': str(e)}, 403)
            except RuntimeError as e:
                return self._service_unavailable(str(e), is_api=True)
            except ValueError as e:
                return self._json({'error': str(e)}, 400)

        if not self._require_auth(path):
            return
        try:
            inst_ctx = _resolve_workspace_context()
            org_id = inst_ctx.org_id
            _enforce_request_context(parsed, self.headers, org_id)
            session_claims = self._session_claims_from_request(expected_org_id=org_id)
            if session_claims:
                auth_context = _resolve_auth_context_from_session(session_claims, org_id)
            elif self.headers.get('Authorization', '').startswith('Bearer '):
                return self._json({
                    'error': 'Session token is not valid for this institution'
                }, 403)
            else:
                auth_context = _resolve_auth_context(org_id)
            _enforce_mutation_authorization(auth_context, org_id, path)
        except RuntimeError as e:
            return self._service_unavailable(str(e), is_api=path.startswith('/api/'))
        except ValueError as e:
            return self._json({'error': str(e)}, 400)
        except PermissionError as e:
            return self._json({'error': str(e)}, 403)

        try:
            body = self._read_body()
        except Exception:
            return self._json({'error': 'Invalid JSON'}, 400)

        by = auth_context.get('actor_id') or 'owner'  # server-enforced — never trust client-supplied actor identity
        _sid = auth_context.get('session_id')  # session traceability for audit

        try:
            if path == '/api/authority/kill-switch':
                if body.get('engage'):
                    engage_kill_switch(by, body.get('reason', ''), org_id=org_id)
                    log_event(org_id, by, 'kill_switch_engaged', outcome='success',
                              details={'by': by, 'reason': body.get('reason')},
                              session_id=_sid)
                    return self._json({'message': 'Kill switch ENGAGED'})
                else:
                    disengage_kill_switch(by, org_id=org_id)
                    log_event(org_id, by, 'kill_switch_disengaged', outcome='success',
                              details={'by': by}, session_id=_sid)
                    return self._json({'message': 'Kill switch disengaged'})

            elif path == '/api/authority/approve':
                decision = body['decision']
                decide_approval(body['approval_id'], decision,
                               by, body.get('reason', ''), org_id=org_id)
                log_event(org_id, by, 'approval_decided', resource=body['approval_id'],
                          outcome='success', details={'decision': decision, 'reason': body.get('reason', '')},
                          session_id=_sid)
                return self._json({'message': f'Approval {body["approval_id"]}: {decision}'})

            elif path == '/api/authority/request':
                aid = request_approval(body['agent'], body['action'],
                                       body['resource'], body.get('cost', 0), org_id=org_id)
                log_event(org_id, body['agent'], 'approval_requested', resource=aid,
                          outcome='success', details=body, session_id=_sid)
                return self._json({'message': f'Approval requested: {aid}', 'approval_id': aid})

            elif path == '/api/authority/delegate':
                scopes = [s.strip() for s in body['scopes'].split(',') if s.strip()]
                did = delegate(body['from'], body['to'], scopes, body.get('hours', 24), org_id=org_id)
                log_event(org_id, body['from'], 'delegation_created', resource=did,
                          outcome='success', details=body, session_id=_sid)
                return self._json({'message': f'Delegation created: {did}', 'delegation_id': did})

            elif path == '/api/authority/revoke':
                revoke_delegation(body['delegation_id'], org_id=org_id)
                log_event(org_id, by, 'delegation_revoked', resource=body['delegation_id'],
                          outcome='success', session_id=_sid)
                return self._json({'message': f'Delegation revoked: {body["delegation_id"]}'})

            elif path == '/api/court/file':
                vid = file_violation(body['agent'], org_id, body['type'],
                                     body['severity'], body['evidence'],
                                     body.get('policy_ref', ''))
                return self._json({'message': f'Violation filed: {vid}', 'violation_id': vid})

            elif path == '/api/court/resolve':
                resolve_violation(body['violation_id'], body['note'], org_id=org_id)
                log_event(org_id, by, 'violation_resolved', resource=body['violation_id'],
                          outcome='success', details={'note': body['note']},
                          session_id=_sid)
                return self._json({'message': f'Violation resolved: {body["violation_id"]}'})

            elif path == '/api/court/appeal':
                aid = file_appeal(body['violation_id'], body['agent'], body['grounds'], org_id=org_id)
                log_event(org_id, body['agent'], 'appeal_filed', resource=aid,
                          outcome='success', session_id=_sid)
                return self._json({'message': f'Appeal filed: {aid}', 'appeal_id': aid})

            elif path == '/api/court/decide-appeal':
                decide_appeal(body['appeal_id'], body['decision'], by, org_id=org_id)
                log_event(org_id, by, 'appeal_decided', resource=body['appeal_id'],
                          outcome='success', details={'decision': body['decision']},
                          session_id=_sid)
                return self._json({'message': f'Appeal {body["appeal_id"]}: {body["decision"]}'})

            elif path == '/api/court/auto-review':
                vids = auto_review(org_id=org_id)
                return self._json({'message': f'Auto-review: {len(vids)} violation(s) created',
                                   'violations': vids})

            elif path == '/api/court/remediate':
                lifted = remediate(body['agent_id'], by,
                                   body.get('note', ''), org_id=org_id)
                log_event(org_id, by, 'court_remediation', resource=body['agent_id'],
                          outcome='success', details={'lifted': lifted, 'note': body.get('note', '')},
                          session_id=_sid)
                return self._json({'message': f'Remediation complete: lifted {lifted}',
                                   'lifted': lifted})

            elif path == '/api/treasury/contribute':
                result = contribute_owner_capital(body['amount'], body.get('note', ''),
                                                  by, org_id=org_id)
                log_event(org_id, by, 'treasury_owner_capital', outcome='success',
                          details=result, session_id=_sid)
                return self._json({
                    'message': f'Owner capital recorded: +${result["amount_usd"]:.2f}',
                    'snapshot': treasury_snapshot(org_id),
                })

            elif path == '/api/treasury/reserve-floor':
                result = set_reserve_floor_policy(body['amount'], body.get('note', ''),
                                                  by, org_id=org_id)
                log_event(org_id, by, 'treasury_reserve_floor_updated',
                          outcome='success', details=result, session_id=_sid)
                return self._json({
                    'message': 'Reserve floor updated',
                    'snapshot': treasury_snapshot(org_id),
                })

            elif path == '/api/session/issue':
                user_id = auth_context.get('user_id')
                role = auth_context.get('role')
                if not user_id or not role:
                    return self._json({
                        'error': 'Cannot issue session: actor is not a member of this institution'
                    }, 403)
                ttl = body.get('ttl_seconds')
                token = _session_authority.issue(org_id, user_id, role, ttl_seconds=ttl)
                claims = _session_authority.validate(token)
                log_event(org_id, by, 'session_issued', outcome='success',
                          details={'session_id': claims.session_id,
                                   'user_id': user_id, 'role': role},
                          session_id=_sid)
                return self._json({
                    'token': token,
                    'session_id': claims.session_id,
                    'org_id': org_id,
                    'user_id': user_id,
                    'role': role,
                    'expires_at': claims.expires_at,
                })

            elif path == '/api/session/revoke':
                session_id = body.get('session_id')
                if not session_id:
                    return self._json({'error': 'session_id is required'}, 400)
                _session_authority.revoke(session_id)
                log_event(org_id, by, 'session_revoked', outcome='success',
                          details={'session_id': session_id},
                          session_id=_sid)
                return self._json({'message': f'Session revoked: {session_id}'})

            elif path == '/api/warrants/issue':
                action_class = (body.get('action_class') or '').strip()
                boundary_name = (body.get('boundary_name') or '').strip()
                if not action_class:
                    return self._json({'error': 'action_class is required'}, 400)
                if not boundary_name:
                    return self._json({'error': 'boundary_name is required'}, 400)
                warrant = issue_warrant(
                    org_id,
                    action_class,
                    boundary_name,
                    by,
                    session_id=_sid or '',
                    request_payload=body.get('request_payload'),
                    risk_class=(body.get('risk_class') or 'moderate').strip(),
                    evidence_refs=body.get('evidence_refs'),
                    policy_refs=body.get('policy_refs'),
                    ttl_seconds=body.get('ttl_seconds'),
                    auto_issue=bool(body.get('auto_issue')),
                    note=body.get('note', ''),
                )
                log_event(org_id, by, 'warrant_issued', outcome='success',
                          resource=warrant['warrant_id'],
                          details={
                              'action_class': warrant['action_class'],
                              'boundary_name': warrant['boundary_name'],
                              'court_review_state': warrant['court_review_state'],
                          },
                          session_id=_sid)
                return self._json({
                    'message': f"Warrant issued: {warrant['warrant_id']}",
                    'warrant': warrant,
                })

            elif path in ('/api/warrants/approve', '/api/warrants/stay', '/api/warrants/revoke'):
                warrant_id = (body.get('warrant_id') or '').strip()
                if not warrant_id:
                    return self._json({'error': 'warrant_id is required'}, 400)
                decision = path.rsplit('/', 1)[-1]
                decision_past = {
                    'approve': 'approved',
                    'stay': 'stayed',
                    'revoke': 'revoked',
                }
                warrant = review_warrant(
                    warrant_id,
                    decision,
                    by,
                    org_id=org_id,
                    note=body.get('note', ''),
                )
                log_event(org_id, by, f'warrant_{decision}', outcome='success',
                          resource=warrant_id,
                          details={'court_review_state': warrant['court_review_state']},
                          session_id=_sid)
                return self._json({
                    'message': f"Warrant {decision_past[decision]}: {warrant_id}",
                    'warrant': warrant,
                })

            elif path == '/api/commitments/propose':
                target_host_id = (body.get('target_host_id') or '').strip()
                target_org_id = (
                    body.get('target_institution_id')
                    or body.get('target_org_id')
                    or ''
                ).strip()
                summary = (body.get('summary') or '').strip()
                if not target_host_id:
                    return self._json({'error': 'target_host_id is required'}, 400)
                if not target_org_id:
                    return self._json({'error': 'target_institution_id is required'}, 400)
                if not summary:
                    return self._json({'error': 'summary is required'}, 400)
                commitment = commitments.propose_commitment(
                    target_host_id,
                    target_org_id,
                    summary,
                    commitment_id=(body.get('commitment_id') or '').strip(),
                    proposed_by=by,
                    note=body.get('note', ''),
                    org_id=org_id,
                    metadata=body.get('metadata'),
                )
                log_event(org_id, by, 'commitment_proposed', resource=commitment['commitment_id'],
                          outcome='success', details={
                              'target_host_id': target_host_id,
                              'target_institution_id': target_org_id,
                              'summary': summary,
                          }, session_id=_sid)
                return self._json({
                    'message': f"Commitment proposed: {commitment['commitment_id']}",
                    'commitment': commitment,
                    'summary': commitments.commitment_summary(org_id),
                })

            elif path == '/api/commitments/accept':
                commitment_id = (body.get('commitment_id') or '').strip()
                if not commitment_id:
                    return self._json({'error': 'commitment_id is required'}, 400)
                commitment = commitments.accept_commitment(
                    commitment_id,
                    by,
                    org_id=org_id,
                    note=body.get('note', ''),
                )
                log_event(org_id, by, 'commitment_accepted', resource=commitment_id,
                          outcome='success', details={'status': commitment['status']},
                          session_id=_sid)
                return self._json({
                    'message': f"Commitment accepted: {commitment_id}",
                    'commitment': commitment,
                    'summary': commitments.commitment_summary(org_id),
                })

            elif path == '/api/commitments/reject':
                commitment_id = (body.get('commitment_id') or '').strip()
                if not commitment_id:
                    return self._json({'error': 'commitment_id is required'}, 400)
                commitment = commitments.reject_commitment(
                    commitment_id,
                    by,
                    org_id=org_id,
                    note=body.get('note', ''),
                )
                log_event(org_id, by, 'commitment_rejected', resource=commitment_id,
                          outcome='success', details={'status': commitment['status']},
                          session_id=_sid)
                return self._json({
                    'message': f"Commitment rejected: {commitment_id}",
                    'commitment': commitment,
                    'summary': commitments.commitment_summary(org_id),
                })

            elif path == '/api/commitments/breach':
                commitment_id = (body.get('commitment_id') or '').strip()
                if not commitment_id:
                    return self._json({'error': 'commitment_id is required'}, 400)
                commitment = commitments.breach_commitment(
                    commitment_id,
                    by,
                    org_id=org_id,
                    note=body.get('note', ''),
                )
                log_event(org_id, by, 'commitment_breached', resource=commitment_id,
                          outcome='success', details={'status': commitment['status']},
                          session_id=_sid)
                case_record = None
                warrant = None
                if body.get('open_case', True):
                    case_record, created = _maybe_open_case_for_commitment_breach(
                        commitment,
                        by,
                        org_id=org_id,
                        note=body.get('case_note') or body.get('note', ''),
                    )
                    if created:
                        log_event(
                            org_id,
                            by,
                            'case_opened',
                            resource=case_record['case_id'],
                            outcome='success',
                            details={
                                'claim_type': case_record['claim_type'],
                                'linked_commitment_id': commitment_id,
                                'linked_warrant_id': case_record.get('linked_warrant_id', ''),
                            },
                            session_id=_sid,
                        )
                    warrant = _maybe_stay_warrant_for_case(
                        case_record,
                        by,
                        org_id=org_id,
                        session_id=_sid,
                        note=body.get('case_note') or body.get('note', ''),
                    )
                return self._json({
                    'message': f"Commitment breached: {commitment_id}",
                    'commitment': commitment,
                    'summary': commitments.commitment_summary(org_id),
                    'case': case_record,
                    'warrant': warrant,
                })

            elif path == '/api/commitments/settle':
                commitment_id = (body.get('commitment_id') or '').strip()
                if not commitment_id:
                    return self._json({'error': 'commitment_id is required'}, 400)
                commitment = commitments.settle_commitment(
                    commitment_id,
                    by,
                    org_id=org_id,
                    note=body.get('note', ''),
                )
                log_event(org_id, by, 'commitment_settled', resource=commitment_id,
                          outcome='success', details={'status': commitment['status']},
                          session_id=_sid)
                return self._json({
                    'message': f"Commitment settled: {commitment_id}",
                    'commitment': commitment,
                    'summary': commitments.commitment_summary(org_id),
                })

            elif path == '/api/cases/open':
                claim_type = (body.get('claim_type') or '').strip()
                if not claim_type:
                    return self._json({'error': 'claim_type is required'}, 400)
                case_record = cases.open_case(
                    org_id,
                    claim_type,
                    by,
                    target_host_id=(body.get('target_host_id') or '').strip(),
                    target_institution_id=(
                        body.get('target_institution_id')
                        or body.get('target_org_id')
                        or ''
                    ).strip(),
                    linked_commitment_id=(body.get('linked_commitment_id') or '').strip(),
                    linked_warrant_id=(body.get('linked_warrant_id') or '').strip(),
                    evidence_refs=body.get('evidence_refs') or [],
                    note=body.get('note', ''),
                    metadata=body.get('metadata'),
                )
                log_event(org_id, by, 'case_opened', resource=case_record['case_id'],
                          outcome='success', details={
                              'claim_type': claim_type,
                              'linked_commitment_id': case_record.get('linked_commitment_id', ''),
                              'linked_warrant_id': case_record.get('linked_warrant_id', ''),
                          }, session_id=_sid)
                federation_peer = _maybe_suspend_peer_for_case(
                    case_record,
                    by,
                    org_id=org_id,
                    session_id=_sid,
                )
                warrant = _maybe_stay_warrant_for_case(
                    case_record,
                    by,
                    org_id=org_id,
                    session_id=_sid,
                    note=body.get('note', ''),
                )
                return self._json({
                    'message': f"Case opened: {case_record['case_id']}",
                    'case': case_record,
                    'summary': _case_snapshot(org_id),
                    'federation_peer': federation_peer,
                    'warrant': warrant,
                })

            elif path == '/api/cases/stay':
                case_id = (body.get('case_id') or '').strip()
                if not case_id:
                    return self._json({'error': 'case_id is required'}, 400)
                case_record = cases.stay_case(case_id, by, org_id=org_id, note=body.get('note', ''))
                log_event(org_id, by, 'case_stayed', resource=case_id,
                          outcome='success', details={'status': case_record['status']},
                          session_id=_sid)
                federation_peer = _maybe_suspend_peer_for_case(
                    case_record,
                    by,
                    org_id=org_id,
                    session_id=_sid,
                )
                warrant = _maybe_stay_warrant_for_case(
                    case_record,
                    by,
                    org_id=org_id,
                    session_id=_sid,
                    note=body.get('note', ''),
                )
                return self._json({
                    'message': f"Case stayed: {case_id}",
                    'case': case_record,
                    'summary': _case_snapshot(org_id),
                    'federation_peer': federation_peer,
                    'warrant': warrant,
                })

            elif path == '/api/cases/resolve':
                case_id = (body.get('case_id') or '').strip()
                if not case_id:
                    return self._json({'error': 'case_id is required'}, 400)
                case_record = cases.resolve_case(case_id, by, org_id=org_id, note=body.get('note', ''))
                log_event(org_id, by, 'case_resolved', resource=case_id,
                          outcome='success', details={'status': case_record['status']},
                          session_id=_sid)
                return self._json({
                    'message': f"Case resolved: {case_id}",
                    'case': case_record,
                    'summary': _case_snapshot(org_id),
                    'federation_peer': None,
                })

            elif path == '/api/federation/send':
                target_host_id = (body.get('target_host_id') or '').strip()
                target_org_id = (body.get('target_org_id') or '').strip()
                message_type = (body.get('message_type') or '').strip()
                if not target_host_id:
                    return self._json({'error': 'target_host_id is required'}, 400)
                if not target_org_id:
                    return self._json({'error': 'target_org_id is required'}, 400)
                if not message_type:
                    return self._json({'error': 'message_type is required'}, 400)
                try:
                    delivery, federation_state = _deliver_federation_envelope(
                        org_id,
                        target_host_id,
                        target_org_id,
                        message_type,
                        payload=body.get('payload'),
                        actor_type='user',
                        actor_id=by,
                        session_id=_sid or '',
                        warrant_id=(body.get('warrant_id') or '').strip(),
                        commitment_id=(body.get('commitment_id') or '').strip(),
                        ttl_seconds=body.get('ttl_seconds'),
                    )
                except FederationUnavailable as e:
                    return self._json({'error': str(e)}, 503)
                except PermissionError as e:
                    case_record = getattr(e, 'case_record', None)
                    if case_record:
                        return self._json({
                            'error': str(e),
                            'case': case_record,
                            'federation_peer': getattr(e, 'federation_peer', None),
                            'warrant': getattr(e, 'warrant', None),
                        }, 409)
                    return self._json({'error': str(e)}, 403)
                except FederationDeliveryError as e:
                    return self._json({
                        'error': str(e),
                        'peer_host_id': e.peer_host_id,
                        'claims': _federation_claims_dict(e.claims),
                        'case': getattr(e, 'case_record', None),
                        'federation_peer': getattr(e, 'federation_peer', None),
                        'warrant': getattr(e, 'warrant', None),
                    }, 502)
                return self._json({
                    'message': 'Federation envelope delivered',
                    'delivery': delivery,
                    'runtime_core': {
                        'federation': federation_state,
                    },
                })

            elif path in (
                '/api/federation/peers/upsert',
                '/api/federation/peers/refresh',
                '/api/federation/peers/suspend',
                '/api/federation/peers/revoke',
            ):
                action = path.rsplit('/', 1)[-1]
                _mutate_federation_peer(org_id, action, body)
                return self._json({'message': f'Federation peer {action} applied'})

            elif path in ('/api/admission/admit', '/api/admission/suspend', '/api/admission/revoke'):
                action = path.rsplit('/', 1)[-1]
                _mutate_admission(org_id, action, body.get('org_id'))
                return self._json({'message': f'Admission {action} applied'})

            elif path == '/api/institution/charter':
                set_charter(org_id, body['text'])
                log_event(org_id, by, 'charter_set', outcome='success',
                          session_id=_sid)
                return self._json({'message': 'Charter saved'})

            elif path == '/api/institution/lifecycle':
                org_transition_lifecycle(org_id, body['state'])
                log_event(org_id, by, 'lifecycle_transitioned', outcome='success',
                          details={'new_state': body['state']}, session_id=_sid)
                return self._json({'message': f'Lifecycle transitioned to {body["state"]}'})

            else:
                return self._json({'error': 'Not found'}, 404)

        except PermissionError as e:
            return self._json({'error': str(e)}, 403)
        except Exception as e:
            return self._json({'error': str(e)}, 400)


def main():
    global WORKSPACE_ORG_ID
    parser = argparse.ArgumentParser(description='Governed Workspace server')
    parser.add_argument('--port', type=int, default=18901)
    parser.add_argument('--org-id', default=None,
                        help='Bind this live workspace process to the founding Meridian institution only.')
    args = parser.parse_args()
    if args.org_id:
        WORKSPACE_ORG_ID = args.org_id

    inst_ctx = _resolve_workspace_context()
    org_id, org, context_source = inst_ctx.org_id, inst_ctx.org, inst_ctx.context_source

    server = HTTPServer(('127.0.0.1', args.port), WorkspaceHandler)
    print(f'Governed Workspace running at http://127.0.0.1:{args.port}')
    print(f'Dashboard: http://127.0.0.1:{args.port}/')
    print(f'API:       http://127.0.0.1:{args.port}/api/status')
    print(f'Bound institution: {org.get("slug", "") if org else ""} ({org_id}) via {context_source}')
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print('\nShutdown.')
        server.server_close()


if __name__ == '__main__':
    main()
