#!/usr/bin/env python3
import importlib.util
import os
import unittest
from unittest import mock
import types
from urllib.parse import urlparse


PLATFORM_DIR = os.path.dirname(os.path.abspath(__file__))
WORKSPACE_PY = os.path.join(PLATFORM_DIR, 'workspace.py')


def _load_workspace(name):
    spec = importlib.util.spec_from_file_location(name, WORKSPACE_PY)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _Headers(dict):
    def get(self, key, default=None):
        return super().get(key, default)


class LiveWorkspaceContextTests(unittest.TestCase):
    def setUp(self):
        self.workspace = _load_workspace('live_workspace_context_test')
        self.orig_workspace_org_id = self.workspace.WORKSPACE_ORG_ID
        self.orig_runtime_host_identity_file = self.workspace.RUNTIME_HOST_IDENTITY_FILE
        self.orig_runtime_admission_file = self.workspace.RUNTIME_ADMISSION_FILE
        self.orig_federation_peers_file = self.workspace.FEDERATION_PEERS_FILE
        self.orig_federation_replay_file = self.workspace.FEDERATION_REPLAY_FILE
        self.orig_federation_signing_secret = self.workspace.FEDERATION_SIGNING_SECRET
        self.orig_get_founding_org = self.workspace._get_founding_org
        self.orig_load_orgs = self.workspace.load_orgs
        self.orig_load_workspace_credentials = self.workspace._load_workspace_credentials
        self.orig_load_host_identity = self.workspace.load_host_identity
        self.orig_load_admission_registry = self.workspace.load_admission_registry
        self.orig_runtime_host_state = self.workspace._runtime_host_state
        self.orig_federation_authority = self.workspace._federation_authority
        self.orig_log_event = self.workspace.log_event
        self.orig_list_warrants = self.workspace.list_warrants
        self.orig_get_warrant = self.workspace.get_warrant
        self.orig_issue_warrant = self.workspace.issue_warrant
        self.orig_review_warrant = self.workspace.review_warrant
        self.orig_commitment_summary = self.workspace.commitments.commitment_summary
        self.orig_list_commitments = self.workspace.commitments.list_commitments
        self.orig_validate_commitment_for_settlement = self.workspace.commitments.validate_commitment_for_settlement
        self.orig_record_settlement_ref = self.workspace.commitments.record_settlement_ref
        self.orig_settle_commitment = self.workspace.commitments.settle_commitment
        self.orig_case_summary = self.workspace.cases.case_summary
        self.orig_list_cases = self.workspace.cases.list_cases
        self.orig_blocking_commitment_ids = self.workspace.cases.blocking_commitment_ids
        self.orig_blocked_peer_host_ids = self.workspace.cases.blocked_peer_host_ids
        self.orig_maybe_block_commitment_settlement = self.workspace._maybe_block_commitment_settlement
        self.orig_ensure_case_for_delivery_failure = self.workspace.cases.ensure_case_for_delivery_failure
        self.orig_subscription_snapshot = self.workspace.service_state.subscription_snapshot
        self.orig_accounting_snapshot = self.workspace.service_state.accounting_snapshot
        self.orig_summarize_inbox_entries = self.workspace.summarize_inbox_entries
        self.orig_get_execution_job = self.workspace.get_execution_job
        self.orig_list_execution_jobs = self.workspace.list_execution_jobs
        self.orig_execution_job_summary = self.workspace.execution_job_summary
        self.orig_upsert_execution_job = self.workspace.upsert_execution_job

    def tearDown(self):
        self.workspace.WORKSPACE_ORG_ID = self.orig_workspace_org_id
        self.workspace.RUNTIME_HOST_IDENTITY_FILE = self.orig_runtime_host_identity_file
        self.workspace.RUNTIME_ADMISSION_FILE = self.orig_runtime_admission_file
        self.workspace.FEDERATION_PEERS_FILE = self.orig_federation_peers_file
        self.workspace.FEDERATION_REPLAY_FILE = self.orig_federation_replay_file
        self.workspace.FEDERATION_SIGNING_SECRET = self.orig_federation_signing_secret
        self.workspace._get_founding_org = self.orig_get_founding_org
        self.workspace.load_orgs = self.orig_load_orgs
        self.workspace._load_workspace_credentials = self.orig_load_workspace_credentials
        self.workspace.load_host_identity = self.orig_load_host_identity
        self.workspace.load_admission_registry = self.orig_load_admission_registry
        self.workspace._runtime_host_state = self.orig_runtime_host_state
        self.workspace._federation_authority = self.orig_federation_authority
        self.workspace.log_event = self.orig_log_event
        self.workspace.list_warrants = self.orig_list_warrants
        self.workspace.get_warrant = self.orig_get_warrant
        self.workspace.issue_warrant = self.orig_issue_warrant
        self.workspace.review_warrant = self.orig_review_warrant
        self.workspace.commitments.commitment_summary = self.orig_commitment_summary
        self.workspace.commitments.list_commitments = self.orig_list_commitments
        self.workspace.commitments.validate_commitment_for_settlement = self.orig_validate_commitment_for_settlement
        self.workspace.commitments.record_settlement_ref = self.orig_record_settlement_ref
        self.workspace.commitments.settle_commitment = self.orig_settle_commitment
        self.workspace.cases.case_summary = self.orig_case_summary
        self.workspace.cases.list_cases = self.orig_list_cases
        self.workspace.cases.blocking_commitment_ids = self.orig_blocking_commitment_ids
        self.workspace.cases.blocked_peer_host_ids = self.orig_blocked_peer_host_ids
        self.workspace._maybe_block_commitment_settlement = self.orig_maybe_block_commitment_settlement
        self.workspace.cases.ensure_case_for_delivery_failure = self.orig_ensure_case_for_delivery_failure
        self.workspace.service_state.subscription_snapshot = self.orig_subscription_snapshot
        self.workspace.service_state.accounting_snapshot = self.orig_accounting_snapshot
        self.workspace.summarize_inbox_entries = self.orig_summarize_inbox_entries
        self.workspace.get_execution_job = self.orig_get_execution_job
        self.workspace.list_execution_jobs = self.orig_list_execution_jobs
        self.workspace.execution_job_summary = self.orig_execution_job_summary
        self.workspace.upsert_execution_job = self.orig_upsert_execution_job

    def test_live_workspace_rejects_non_founding_configured_org(self):
        self.workspace._load_workspace_credentials = lambda: (None, None, None, None)
        self.workspace.WORKSPACE_ORG_ID = 'org_other'
        self.workspace._get_founding_org = lambda: ('org_founding', {'slug': 'meridian', 'name': 'Meridian'})
        with self.assertRaises(RuntimeError):
            self.workspace._resolve_workspace_context()

    def test_live_workspace_rejects_non_founding_credential_scope(self):
        self.workspace._load_workspace_credentials = lambda: ('owner', 'secret', 'org_other', None)
        self.workspace._get_founding_org = lambda: ('org_founding', {'slug': 'meridian', 'name': 'Meridian'})
        with self.assertRaises(RuntimeError):
            self.workspace._resolve_workspace_context()

    def test_request_override_must_match_bound_org(self):
        with self.assertRaises(ValueError):
            self.workspace._enforce_request_context(
                urlparse('/api/status?org_id=org_other'),
                _Headers(),
                'org_founding',
            )

        context = self.workspace._enforce_request_context(
            urlparse('/api/status?org_id=org_founding'),
            _Headers({'X-Meridian-Org-Id': 'org_founding'}),
            'org_founding',
        )
        self.assertEqual(context['requested_org_id'], 'org_founding')
        self.assertEqual(context['bound_org_id'], 'org_founding')

    def test_live_workspace_context_returns_institution_context(self):
        self.workspace._load_workspace_credentials = lambda: (None, None, None, None)
        self.workspace._get_founding_org = lambda: (
            'org_founding',
            {'id': 'org_founding', 'slug': 'meridian', 'name': 'Meridian', 'lifecycle_state': 'founding'},
        )
        ctx = self.workspace._resolve_workspace_context()
        self.assertEqual(ctx.org_id, 'org_founding')
        self.assertEqual(ctx.boundary.name, 'workspace')
        self.assertEqual(ctx.context_source, 'founding_default')

    def test_auth_context_reports_credential_binding(self):
        self.workspace._load_workspace_credentials = lambda: ('owner', 'secret', 'org_founding', None)
        auth = self.workspace._resolve_auth_context('org_founding')
        self.assertEqual(auth['mode'], 'credential_bound')
        self.assertEqual(auth['org_id'], 'org_founding')
        self.assertEqual(auth['actor_id'], 'workspace_user:owner')

    def test_auth_context_prefers_explicit_user_id(self):
        self.workspace._load_workspace_credentials = lambda: ('owner', 'secret', 'org_founding', 'user_meridian_owner')
        self.workspace.load_orgs = lambda: {
            'organizations': {
                'org_founding': {
                    'id': 'org_founding',
                    'slug': 'meridian',
                    'name': 'Meridian',
                    'owner_id': 'user_meridian_owner',
                    'members': [{'user_id': 'user_meridian_owner', 'role': 'owner'}],
                },
            }
        }
        auth = self.workspace._resolve_auth_context('org_founding')
        self.assertEqual(auth['actor_id'], 'user_meridian_owner')
        self.assertEqual(auth['actor_source'], 'credentials')
        self.assertEqual(auth['role'], 'owner')

    def test_auth_context_resolves_owner_alias_role(self):
        self.workspace._load_workspace_credentials = lambda: ('owner', 'secret', 'org_founding', None)
        self.workspace.load_orgs = lambda: {
            'organizations': {
                'org_founding': {
                    'id': 'org_founding',
                    'slug': 'meridian',
                    'name': 'Meridian',
                    'owner_id': 'user_son',
                    'members': [{'user_id': 'user_son', 'role': 'owner'}],
                },
            }
        }
        auth = self.workspace._resolve_auth_context('org_founding')
        self.assertEqual(auth['user_id'], 'user_son')
        self.assertEqual(auth['role'], 'owner')
        self.assertEqual(auth['actor_source'], 'owner_alias')

    def test_mutation_authorization_requires_admin_for_kill_switch(self):
        auth = {'enabled': True, 'role': 'member'}
        with self.assertRaises(PermissionError):
            self.workspace._enforce_mutation_authorization(auth, 'org_founding', '/api/authority/kill-switch')

    def test_mutation_authorization_allows_member_request(self):
        auth = {'enabled': True, 'role': 'member'}
        required = self.workspace._enforce_mutation_authorization(auth, 'org_founding', '/api/authority/request')
        self.assertEqual(required, 'member')

    def test_permission_snapshot_tracks_allowed_paths(self):
        auth = {'enabled': True, 'role': 'admin'}
        permissions = self.workspace._permission_snapshot(auth)['mutation_paths']
        self.assertTrue(permissions['/api/authority/kill-switch']['allowed'])
        self.assertTrue(permissions['/api/institution/charter']['allowed'])
        self.assertFalse(permissions['/api/treasury/contribute']['allowed'])
        self.assertTrue(permissions['/api/warrants/issue']['allowed'])
        self.assertTrue(permissions['/api/warrants/approve']['allowed'])
        self.assertTrue(permissions['/api/payouts/propose']['allowed'])
        self.assertTrue(permissions['/api/payouts/submit']['allowed'])
        self.assertTrue(permissions['/api/payouts/review']['allowed'])
        self.assertFalse(permissions['/api/payouts/approve']['allowed'])
        self.assertTrue(permissions['/api/treasury/settlement-adapters/preflight']['allowed'])
        self.assertTrue(permissions['/api/subscriptions/add']['allowed'])
        self.assertTrue(permissions['/api/subscriptions/convert']['allowed'])
        self.assertTrue(permissions['/api/subscriptions/verify-payment']['allowed'])
        self.assertTrue(permissions['/api/subscriptions/remove']['allowed'])
        self.assertTrue(permissions['/api/subscriptions/set-email']['allowed'])
        self.assertTrue(permissions['/api/subscriptions/record-delivery']['allowed'])
        self.assertFalse(permissions['/api/accounting/expense']['allowed'])
        self.assertEqual(permissions['/api/accounting/expense']['required_role'], 'owner')
        self.assertFalse(permissions['/api/accounting/reimburse']['allowed'])
        self.assertEqual(permissions['/api/accounting/reimburse']['required_role'], 'owner')
        self.assertFalse(permissions['/api/accounting/draw']['allowed'])
        self.assertEqual(permissions['/api/accounting/draw']['required_role'], 'owner')
        self.assertEqual(
            permissions['/api/treasury/settlement-adapters/preflight']['required_role'],
            'member',
        )
        self.assertEqual(permissions['/api/subscriptions/add']['required_role'], 'admin')
        self.assertEqual(permissions['/api/subscriptions/convert']['required_role'], 'admin')
        self.assertEqual(permissions['/api/subscriptions/verify-payment']['required_role'], 'admin')
        self.assertEqual(permissions['/api/subscriptions/remove']['required_role'], 'admin')
        self.assertEqual(permissions['/api/payouts/execute']['required_role'], 'owner')
        self.assertTrue(permissions['/api/cases/open']['allowed'])
        self.assertTrue(permissions['/api/cases/resolve']['allowed'])
        self.assertTrue(permissions['/api/federation/send']['allowed'])
        self.assertFalse(permissions['/api/federation/peers/refresh']['allowed'])
        self.assertEqual(permissions['/api/federation/peers/refresh']['required_role'], 'owner')

    def test_payout_snapshot_surfaces_settlement_adapters(self):
        self.workspace.payout_proposal_summary = lambda org_id=None: {'total': 0, 'executed': 0}
        self.workspace.list_payout_proposals = lambda org_id=None: []
        self.workspace.load_payout_proposals = lambda org_id=None: {'state_machine': {'states': []}}
        self.workspace.list_settlement_adapters = lambda org_id=None: [
            {'adapter_id': 'internal_ledger', 'payout_execution_enabled': True},
            {'adapter_id': 'base_usdc_x402', 'payout_execution_enabled': False},
        ]
        self.workspace.settlement_adapter_summary = lambda org_id=None, host_supported_adapters=None: {
            'default_payout_adapter': 'internal_ledger',
            'host_supported_adapters': list(host_supported_adapters or []),
        }
        snapshot = self.workspace._payout_snapshot(
            'org_founding',
            host_supported_adapters=['internal_ledger'],
        )
        self.assertEqual(snapshot['settlement_adapter_summary']['default_payout_adapter'], 'internal_ledger')
        self.assertEqual(snapshot['settlement_adapter_summary']['host_supported_adapters'], ['internal_ledger'])
        self.assertEqual(len(snapshot['settlement_adapters']), 2)

    def test_api_status_exposes_runtime_core(self):
        from runtime_host import default_host_identity
        self.workspace._load_workspace_credentials = lambda: ('owner', 'secret', 'org_founding', 'user_owner')
        self.workspace._get_founding_org = lambda: (
            'org_founding',
            {
                'id': 'org_founding',
                'slug': 'meridian',
                'name': 'Meridian',
                'owner_id': 'user_owner',
                'members': [{'user_id': 'user_owner', 'role': 'owner'}],
                'lifecycle_state': 'founding',
                'policy_defaults': {},
            },
        )
        self.workspace.load_registry = lambda: {'agents': {}}
        self.workspace._load_queue = lambda org_id: {
            'kill_switch': False,
            'pending_approvals': {},
            'delegations': {},
        }
        self.workspace.treasury_snapshot = lambda org_id: {}
        self.workspace._phase_mod.evaluate = lambda org_id: (0, {'name': 'Founder-Backed Build'})
        self.workspace._load_records = lambda org_id: {'violations': {}, 'appeals': {}}
        self.workspace.list_warrants = lambda org_id=None, **_kwargs: [
            {
                'warrant_id': 'war_live_demo',
                'court_review_state': 'approved',
                'execution_state': 'ready',
            }
        ]
        self.workspace.commitments.commitment_summary = lambda org_id=None: {
            'total': 1,
            'proposed': 0,
            'accepted': 1,
            'rejected': 0,
            'breached': 0,
            'settled': 0,
            'delivery_refs_total': 0,
        }
        self.workspace.commitments.list_commitments = lambda org_id=None: [
            {
                'commitment_id': 'com_live_demo',
                'status': 'accepted',
            }
        ]
        self.workspace.cases.case_summary = lambda org_id=None: {
            'total': 1,
            'open': 1,
            'stayed': 0,
            'resolved': 0,
        }
        self.workspace.cases.list_cases = lambda org_id=None: [
            {
                'case_id': 'case_live_demo',
                'status': 'open',
                'claim_type': 'breach_of_commitment',
            }
        ]
        self.workspace.service_state.subscription_snapshot = lambda org_id=None: {
            'bound_org_id': org_id,
            'mutation_enabled': True,
            'identity_model': 'session',
            'summary': {'subscriber_count': 1, 'external_target_count': 0},
        }
        self.workspace.service_state.accounting_snapshot = lambda org_id=None: {
            'bound_org_id': org_id,
            'mutation_enabled': True,
            'identity_model': 'session',
            'summary': {'capital_contributed_usd': 2.0, 'unreimbursed_expenses_usd': 0.0},
        }
        self.workspace.get_sprint_lead = lambda org_id: ('', 0)
        self.workspace.get_pending_approvals = lambda org_id=None: []
        self.workspace._ci_vertical_status = lambda reg, lead_id, org_id: {}
        self.workspace.get_agent_remediation = lambda economy_key, reg, org_id=None: None
        self.workspace.capsule_dir = lambda org_id: f'/tmp/capsules/{org_id}'
        self.workspace.load_host_identity = lambda *args, **kwargs: default_host_identity(
            host_id='host_live',
            label='Meridian Live Host',
            supported_boundaries=['workspace', 'cli', 'mcp_service'],
        )
        self.workspace.load_admission_registry = lambda *args, **kwargs: {
            'source': 'derived_bound_default',
            'host_id': 'host_live',
            'institutions': {'org_founding': {'status': 'admitted'}},
            'admitted_org_ids': ['org_founding'],
        }
        fake_commitments = types.SimpleNamespace(
            commitment_summary=lambda org_id=None: {
                'total': 1,
                'open': 0,
                'proposed': 0,
                'accepted': 1,
                'rejected': 0,
                'breached': 0,
                'settled': 0,
                'delivery_refs_total': 0,
            },
            list_commitments=lambda org_id=None: [{
                'commitment_id': 'com_live_demo',
                'status': 'accepted',
                'target_host_id': 'host_live',
                'target_institution_id': 'org_founding',
                'summary': 'Demo commitment',
                'delivery_refs': [],
            }],
        )
        fake_cases = types.SimpleNamespace(
            case_summary=lambda org_id=None: {
                'total': 1,
                'open': 1,
                'stayed': 0,
                'resolved': 0,
            },
            blocking_commitment_ids=lambda org_id=None: ['com_live_demo'],
            list_cases=lambda org_id=None: [{
                'case_id': 'case_live_demo',
                'status': 'open',
                'claim_type': 'breach_of_commitment',
                'linked_commitment_id': 'com_live_demo',
            }],
            blocked_peer_host_ids=lambda org_id=None: ['host_peer'],
        )
        ctx = self.workspace._resolve_workspace_context()
        with mock.patch.object(self.workspace, 'commitments', fake_commitments), \
             mock.patch.object(self.workspace, 'cases', fake_cases):
            status = self.workspace.api_status(institution_context=ctx)
        self.assertEqual(status['runtime_core']['institution_context']['org_id'], 'org_founding')
        self.assertFalse(status['runtime_core']['admission']['additional_institutions_allowed'])
        self.assertEqual(status['runtime_core']['admission']['management_mode'], 'founding_locked')
        self.assertFalse(status['runtime_core']['admission']['mutation_enabled'])
        self.assertTrue(status['runtime_core']['service_registry']['federation_gateway']['supports_institution_routing'])
        self.assertTrue(status['runtime_core']['service_registry']['federation_gateway']['requires_warrant'])
        self.assertEqual(
            status['runtime_core']['service_registry']['federation_gateway']['required_warrant_actions']['execution_request'],
            'federated_execution',
        )
        self.assertFalse(status['runtime_core']['service_registry']['mcp_service']['supports_institution_routing'])
        self.assertEqual(status['runtime_core']['service_registry']['subscriptions']['identity_model'], 'session')
        self.assertEqual(status['runtime_core']['service_registry']['accounting']['identity_model'], 'session')
        self.assertEqual(status['runtime_core']['host_identity']['host_id'], 'host_live')
        self.assertEqual(status['runtime_core']['admission']['admitted_org_ids'], ['org_founding'])
        self.assertEqual(status['runtime_core']['admission']['management_mode'], 'founding_locked')
        self.assertFalse(status['runtime_core']['admission']['mutation_enabled'])
        self.assertEqual(
            status['runtime_core']['admission']['mutation_disabled_reason'],
            'single_institution_deployment',
        )
        self.assertEqual(status['warrants']['total'], 1)
        self.assertEqual(status['warrants']['executable'], 1)
        self.assertIn('commitments', status)
        self.assertEqual(status['commitments']['total'], 1)
        self.assertEqual(status['commitments']['accepted'], 1)
        self.assertEqual(status['commitments']['management_mode'], 'founding_workspace_local')
        self.assertTrue(status['commitments']['mutation_enabled'])
        self.assertEqual(status['cases']['total'], 1)
        self.assertEqual(status['cases']['open'], 1)
        self.assertEqual(status['cases']['management_mode'], 'founding_workspace_local')
        self.assertEqual(status['cases']['blocking_commitment_ids'], ['com_live_demo'])
        self.assertEqual(status['cases']['blocked_peer_host_ids'], ['host_peer'])
        self.assertEqual(status['service_state']['subscriptions']['summary']['subscriber_count'], 1)
        self.assertTrue(status['service_state']['subscriptions']['mutation_enabled'])
        self.assertEqual(status['service_state']['subscriptions']['identity_model'], 'session')
        self.assertEqual(status['service_state']['accounting']['summary']['capital_contributed_usd'], 2.0)
        self.assertTrue(status['service_state']['accounting']['mutation_enabled'])
        self.assertIn('federation', status['runtime_core'])
        self.assertFalse(status['runtime_core']['federation']['enabled'])

    def test_admission_snapshot_reports_founding_lock(self):
        from runtime_host import default_host_identity

        snapshot = self.workspace._admission_snapshot(
            'org_founding',
            host_identity=default_host_identity(
                host_id='host_live',
                label='Meridian Live Host',
                role='institution_host',
            ),
            admission_registry={
                'source': 'derived_bound_default',
                'host_id': 'host_live',
                'institutions': {'org_founding': {'status': 'admitted'}},
                'admitted_org_ids': ['org_founding'],
            },
        )
        self.assertEqual(snapshot['management_mode'], 'founding_locked')
        self.assertFalse(snapshot['mutation_enabled'])
        self.assertEqual(snapshot['institutions']['org_founding']['status'], 'admitted')

    def test_mutate_admission_fails_closed_for_live_runtime(self):
        with self.assertRaises(PermissionError):
            self.workspace._mutate_admission('org_founding', 'admit', 'org_demo_peer')

    def test_federation_snapshot_reports_disabled_live_host(self):
        from runtime_host import default_host_identity
        host = default_host_identity(
            host_id='host_live',
            label='Meridian Live Host',
            federation_enabled=False,
            supported_boundaries=['workspace', 'cli', 'federation_gateway'],
        )
        snap = self.workspace._federation_snapshot(
            'org_founding',
            host_identity=host,
            admission_registry={'admitted_org_ids': ['org_founding']},
        )
        self.assertFalse(snap['enabled'])
        self.assertFalse(snap['send_enabled'])
        self.assertEqual(snap['disabled_reason'], 'host_federation_disabled')
        self.assertEqual(snap['management_mode'], 'founding_locked')
        self.assertFalse(snap['mutation_enabled'])

    def test_federation_snapshot_surfaces_inbox_summary(self):
        from runtime_host import default_host_identity
        self.workspace.summarize_inbox_entries = lambda org_id: {
            'org_id': org_id,
            'total': 1,
            'received': 1,
            'processed': 0,
            'message_type_counts': {'execution_request': 1},
            'state_counts': {'received': 1},
            'updatedAt': '2026-03-22T00:00:00Z',
        }
        host = default_host_identity(
            host_id='host_live',
            label='Meridian Live Host',
            federation_enabled=False,
            supported_boundaries=['workspace', 'cli', 'federation_gateway'],
        )
        snap = self.workspace._federation_snapshot(
            'org_founding',
            host_identity=host,
            admission_registry={'admitted_org_ids': ['org_founding']},
        )
        self.assertEqual(snap['inbox_summary']['total'], 1)
        self.assertEqual(
            snap['inbox_summary']['message_type_counts']['execution_request'],
            1,
        )

    def test_federation_execution_jobs_snapshot_surfaces_local_warrant(self):
        self.workspace.list_execution_jobs = lambda org_id, state=None: [
            {
                'job_id': 'fej_demo',
                'envelope_id': 'fed_exec_demo',
                'receipt_id': 'fedrcpt_demo',
                'state': 'pending_local_warrant',
                'local_warrant_id': 'war_local_demo',
                'message_type': 'execution_request',
                'received_at': '2026-03-22T00:00:00Z',
                'source_host_id': 'host_alpha',
                'source_institution_id': 'org_alpha',
                'target_host_id': 'host_live',
                'target_institution_id': 'org_founding',
                'boundary_name': 'federation_gateway',
                'identity_model': 'signed_host_service',
                'payload': {'task': 'demo'},
                'payload_hash': 'hash_demo',
            }
        ]
        self.workspace.execution_job_summary = lambda org_id: {
            'org_id': org_id,
            'total': 1,
            'pending_local_warrant': 1,
            'ready': 0,
            'executed': 0,
            'blocked': 0,
            'rejected': 0,
            'state_counts': {'pending_local_warrant': 1},
            'message_type_counts': {'execution_request': 1},
            'updatedAt': '2026-03-22T00:00:00Z',
        }
        self.workspace.get_warrant = lambda warrant_id, org_id=None: {
            'warrant_id': warrant_id,
            'court_review_state': 'pending_review',
            'execution_state': 'ready',
            'expires_at': '2026-03-22T01:00:00Z',
        }
        snapshot = self.workspace._federation_execution_jobs_snapshot('org_founding')
        self.assertEqual(snapshot['summary']['total'], 1)
        self.assertEqual(snapshot['summary']['pending_local_warrant'], 1)
        self.assertEqual(snapshot['jobs'][0]['envelope_id'], 'fed_exec_demo')
        self.assertEqual(snapshot['jobs'][0]['state'], 'pending_local_warrant')
        self.assertEqual(snapshot['jobs'][0]['local_warrant']['warrant_id'], 'war_local_demo')
        self.assertEqual(snapshot['jobs'][0]['local_warrant']['court_review_state'], 'pending_review')
        self.assertEqual(snapshot['jobs'][0]['local_warrant']['execution_state'], 'ready')

    def test_federation_receipt_is_bound_to_receiver_host_and_org(self):
        from federation import FederationEnvelopeClaims

        receipt = self.workspace._federation_receipt(
            'org_founding',
            'host_live',
            FederationEnvelopeClaims(
                envelope_id='fed_demo',
                message_type='execution_request',
                boundary_name='federation_gateway',
            ),
        )
        self.assertEqual(receipt['envelope_id'], 'fed_demo')
        self.assertEqual(receipt['receiver_host_id'], 'host_live')
        self.assertEqual(receipt['receiver_institution_id'], 'org_founding')
        self.assertEqual(receipt['identity_model'], 'signed_host_service')
        self.assertTrue(receipt['receipt_id'].startswith('fedrcpt_'))

    def test_process_received_execution_request_creates_local_warranted_job(self):
        from federation import FederationEnvelopeClaims

        audit_events = []
        self.workspace.get_execution_job = lambda envelope_id, org_id=None: None
        self.workspace.issue_warrant = lambda *args, **kwargs: {
            'warrant_id': 'war_local_demo',
            'court_review_state': 'pending_review',
            'execution_state': 'ready',
            'expires_at': '2026-03-22T01:00:00Z',
        }
        self.workspace.get_warrant = lambda warrant_id, org_id=None: {
            'warrant_id': warrant_id,
            'court_review_state': 'pending_review',
            'execution_state': 'ready',
            'expires_at': '2026-03-22T01:00:00Z',
        }
        self.workspace.upsert_execution_job = lambda org_id, job: dict(job, job_id='fej_demo')
        original_blocking_case = self.workspace._blocking_case_for_delivery
        self.workspace._blocking_case_for_delivery = lambda **kwargs: None
        self.workspace.log_event = lambda *args, **kwargs: audit_events.append({
            'args': args,
            'kwargs': kwargs,
        })
        original_inbox_entry = self.workspace._federation_inbox_entry
        self.workspace._federation_inbox_entry = lambda *args, **kwargs: {
            'envelope_id': 'fed_exec_demo',
            'state': kwargs.get('state', 'received'),
        }
        try:
            claims = FederationEnvelopeClaims(
                envelope_id='fed_exec_demo',
                source_host_id='host_peer',
                source_institution_id='org_peer',
                target_host_id='host_live',
                target_institution_id='org_founding',
                actor_type='service',
                actor_id='peer:host_peer',
                session_id='ses_peer',
                boundary_name='federation_gateway',
                identity_model='signed_host_service',
                message_type='execution_request',
                payload_hash='hash_live',
                warrant_id='war_sender',
                commitment_id='cmt_live',
            )
            processing = self.workspace._process_received_federation_message(
                'org_founding',
                claims,
                {'receipt_id': 'fedrcpt_live', 'accepted_at': '2026-03-22T00:00:00Z'},
                payload={'task': 'demo'},
            )
        finally:
            self.workspace._blocking_case_for_delivery = original_blocking_case
            self.workspace._federation_inbox_entry = original_inbox_entry

        self.assertTrue(processing['applied'])
        self.assertEqual(processing['state'], 'processed')
        self.assertEqual(processing['reason'], 'execution_job_created')
        self.assertEqual(processing['execution_job']['job_id'], 'fej_demo')
        self.assertEqual(processing['execution_job']['state'], 'pending_local_warrant')
        self.assertEqual(processing['receiver_warrant']['warrant_id'], 'war_local_demo')
        self.assertEqual(processing['inbox_entry']['state'], 'processed')
        self.assertEqual(audit_events[-1]['args'][2], 'federation_execution_job_created')

    def test_process_received_execution_request_blocks_on_case(self):
        from federation import FederationEnvelopeClaims

        self.workspace.get_execution_job = lambda envelope_id, org_id=None: None
        self.workspace.issue_warrant = lambda *args, **kwargs: self.fail('blocked execution should not issue warrant')
        original_blocking_case = self.workspace._blocking_case_for_delivery
        self.workspace._blocking_case_for_delivery = lambda **kwargs: {
            'case_id': 'case_live_demo',
            'claim_type': 'non_delivery',
            'status': 'open',
        }
        self.workspace.upsert_execution_job = lambda org_id, job: dict(job, job_id='fej_demo')
        self.workspace.log_event = lambda *args, **kwargs: None
        original_inbox_entry = self.workspace._federation_inbox_entry
        self.workspace._federation_inbox_entry = lambda *args, **kwargs: {
            'envelope_id': 'fed_exec_demo',
            'state': kwargs.get('state', 'received'),
        }
        try:
            claims = FederationEnvelopeClaims(
                envelope_id='fed_exec_demo',
                source_host_id='host_peer',
                source_institution_id='org_peer',
                target_host_id='host_live',
                target_institution_id='org_founding',
                actor_type='service',
                actor_id='peer:host_peer',
                session_id='ses_peer',
                boundary_name='federation_gateway',
                identity_model='signed_host_service',
                message_type='execution_request',
                payload_hash='hash_live',
                warrant_id='war_sender',
                commitment_id='cmt_live',
            )
            processing = self.workspace._process_received_federation_message(
                'org_founding',
                claims,
                {'receipt_id': 'fedrcpt_live', 'accepted_at': '2026-03-22T00:00:00Z'},
                payload={'task': 'demo'},
            )
        finally:
            self.workspace._blocking_case_for_delivery = original_blocking_case
            self.workspace._federation_inbox_entry = original_inbox_entry

        self.assertTrue(processing['applied'])
        self.assertEqual(processing['reason'], 'case_blocked')
        self.assertEqual(processing['execution_job']['state'], 'blocked')
        self.assertEqual(processing['case']['case_id'], 'case_live_demo')
        self.assertIsNone(processing['receiver_warrant'])

    def test_mutate_federation_peer_is_rejected_on_live(self):
        with self.assertRaises(PermissionError):
            self.workspace._mutate_federation_peer('org_founding', 'upsert', {
                'peer_host_id': 'host_beta',
                'shared_secret': 'beta-secret',
            })

    def test_mutate_federation_peer_refresh_is_rejected_on_live(self):
        with self.assertRaises(PermissionError):
            self.workspace._mutate_federation_peer('org_founding', 'refresh', {
                'peer_host_id': 'host_beta',
            })

    def test_maybe_suspend_peer_for_case_reports_founding_lock(self):
        result = self.workspace._maybe_suspend_peer_for_case(
            {
                'case_id': 'case_live_demo',
                'claim_type': 'misrouted_execution',
                'status': 'open',
                'target_host_id': 'host_peer',
            },
            'user_owner',
            org_id='org_founding',
            session_id='ses_demo',
        )
        self.assertFalse(result['applied'])
        self.assertEqual(result['peer_host_id'], 'host_peer')
        self.assertEqual(result['reason'], 'single_institution_deployment')

    def test_maybe_open_case_for_delivery_failure_reports_fail_closed_peer_state(self):
        from federation import FederationDeliveryError

        audit_events = []
        self.workspace.log_event = lambda *args, **kwargs: audit_events.append({
            'args': args,
            'kwargs': kwargs,
        })
        self.workspace.cases.ensure_case_for_delivery_failure = lambda claim_type, actor_id, **kwargs: ({
            'case_id': 'case_live_demo',
            'claim_type': claim_type,
            'status': 'open',
            'linked_commitment_id': kwargs.get('linked_commitment_id', ''),
            'target_host_id': kwargs.get('target_host_id', ''),
        }, True)
        error = FederationDeliveryError(
            "Peer host 'host_peer' returned receipt receiver_institution_id 'org_wrong', not 'org_peer'",
            peer_host_id='host_peer',
        )

        case_record, federation_peer = self.workspace._maybe_open_case_for_delivery_failure(
            error,
            'user_owner',
            org_id='org_founding',
            target_host_id='host_peer',
            target_institution_id='org_peer',
            commitment_id='cmt_demo',
            warrant_id='war_demo',
            session_id='ses_demo',
        )

        self.assertEqual(case_record['claim_type'], 'misrouted_execution')
        self.assertEqual(federation_peer['peer_host_id'], 'host_peer')
        self.assertFalse(federation_peer['applied'])
        self.assertEqual(federation_peer['reason'], 'single_institution_deployment')
        self.assertEqual(audit_events[0]['args'][2], 'case_opened')

    def test_maybe_stay_warrant_for_case_stays_ready_warrant(self):
        audit_events = []
        self.workspace.list_warrants = lambda org_id=None, **_kwargs: [
            {
                'warrant_id': 'war_live_demo',
                'court_review_state': 'approved',
                'execution_state': 'ready',
            }
        ]
        self.workspace.review_warrant = lambda warrant_id, decision, by, **_kwargs: {
            'warrant_id': warrant_id,
            'court_review_state': 'stayed',
            'execution_state': 'ready',
            'reviewed_by': by,
        }
        self.workspace.log_event = lambda *args, **kwargs: audit_events.append({
            'args': args,
            'kwargs': kwargs,
        })

        warrant = self.workspace._maybe_stay_warrant_for_case(
            {
                'case_id': 'case_live_demo',
                'claim_type': 'misrouted_execution',
                'linked_warrant_id': 'war_live_demo',
            },
            'user_owner',
            org_id='org_founding',
            session_id='ses_demo',
            note='Receipt contradiction',
        )

        self.assertTrue(warrant['applied'])
        self.assertEqual(warrant['warrant_id'], 'war_live_demo')
        self.assertEqual(warrant['court_review_state'], 'stayed')
        self.assertEqual(audit_events[0]['args'][2], 'warrant_stayed_for_case')
        self.assertEqual(audit_events[0]['kwargs']['details']['case_id'], 'case_live_demo')

    def test_maybe_block_commitment_settlement_returns_case_and_warrant(self):
        audit_events = []
        self.workspace.cases.blocking_commitment_case = lambda commitment_id, **_kwargs: {
            'case_id': 'case_live_demo',
            'claim_type': 'non_delivery',
            'status': 'open',
            'linked_commitment_id': commitment_id,
            'linked_warrant_id': 'war_live_demo',
        }
        self.workspace.list_warrants = lambda org_id=None, **_kwargs: [
            {
                'warrant_id': 'war_live_demo',
                'court_review_state': 'approved',
                'execution_state': 'ready',
            }
        ]
        self.workspace.review_warrant = lambda warrant_id, decision, by, **_kwargs: {
            'warrant_id': warrant_id,
            'court_review_state': 'stayed',
            'execution_state': 'ready',
            'reviewed_by': by,
        }
        self.workspace.log_event = lambda *args, **kwargs: audit_events.append({
            'args': args,
            'kwargs': kwargs,
        })

        case_record, warrant = self.workspace._maybe_block_commitment_settlement(
            'cmt_demo',
            'user_owner',
            org_id='org_founding',
            session_id='ses_demo',
            note='Do not settle while case is open',
        )

        self.assertEqual(case_record['case_id'], 'case_live_demo')
        self.assertTrue(warrant['applied'])
        self.assertEqual(warrant['warrant_id'], 'war_live_demo')
        self.assertEqual(audit_events[-1]['args'][2], 'commitment_settlement_blocked')
        self.assertEqual(audit_events[-1]['kwargs']['resource'], 'cmt_demo')

    def test_process_received_settlement_notice_marks_inbox_processed(self):
        from federation import FederationEnvelopeClaims

        audit_events = []
        self.workspace.commitments.validate_commitment_for_settlement = (
            lambda commitment_id, **_kwargs: {'commitment_id': commitment_id, 'state': 'accepted'}
        )
        self.workspace._maybe_block_commitment_settlement = lambda *args, **kwargs: (None, None)
        self.workspace.commitments.record_settlement_ref = lambda *args, **kwargs: None
        self.workspace.commitments.settle_commitment = lambda commitment_id, by, **_kwargs: {
            'commitment_id': commitment_id,
            'state': 'settled',
            'settled_by': by,
        }
        self.workspace.log_event = lambda *args, **kwargs: audit_events.append({
            'args': args,
            'kwargs': kwargs,
        })
        original_inbox_entry = self.workspace._federation_inbox_entry
        self.workspace._federation_inbox_entry = lambda *args, **kwargs: {
            'envelope_id': 'fed_live_settle',
            'state': kwargs.get('state', 'received'),
            'processed_at': '2026-03-22T00:00:00Z' if kwargs.get('state') == 'processed' else '',
        }
        try:
            claims = FederationEnvelopeClaims(
                envelope_id='fed_live_settle',
                source_host_id='host_peer',
                source_institution_id='org_peer',
                target_host_id='host_live',
                target_institution_id='org_founding',
                actor_type='service',
                actor_id='peer:host_peer',
                session_id='ses_peer',
                boundary_name='federation_gateway',
                identity_model='signed_host_service',
                message_type='settlement_notice',
                payload_hash='hash_live',
                warrant_id='war_live',
                commitment_id='cmt_live',
            )
            processing = self.workspace._process_received_federation_message(
                'org_founding',
                claims,
                {'receipt_id': 'fedrcpt_live', 'accepted_at': '2026-03-22T00:00:00Z'},
                payload={
                    'proposal_id': 'ppo_live',
                    'tx_ref': 'tx_live',
                    'settlement_adapter': 'internal_ledger',
                },
            )
        finally:
            self.workspace._federation_inbox_entry = original_inbox_entry

        self.assertTrue(processing['applied'])
        self.assertEqual(processing['state'], 'processed')
        self.assertEqual(processing['reason'], 'settlement_notice_applied')
        self.assertEqual(processing['commitment']['state'], 'settled')
        self.assertEqual(processing['settlement_ref']['tx_ref'], 'tx_live')
        self.assertEqual(processing['inbox_entry']['state'], 'processed')
        self.assertEqual(audit_events[-1]['args'][2], 'federation_settlement_notice_applied')

    def test_process_received_settlement_notice_respects_case_block(self):
        from federation import FederationEnvelopeClaims

        self.workspace.commitments.validate_commitment_for_settlement = (
            lambda commitment_id, **_kwargs: {'commitment_id': commitment_id, 'state': 'accepted'}
        )
        self.workspace._maybe_block_commitment_settlement = lambda *args, **kwargs: (
            {'case_id': 'case_live_demo', 'status': 'open', 'linked_commitment_id': 'cmt_live'},
            {'applied': True, 'warrant_id': 'war_live', 'court_review_state': 'stayed'},
        )
        self.workspace.commitments.record_settlement_ref = lambda *args, **kwargs: self.fail(
            'settlement ref should not be recorded'
        )
        self.workspace.commitments.settle_commitment = lambda *args, **kwargs: self.fail(
            'commitment should not settle'
        )
        self.workspace.log_event = lambda *args, **kwargs: None

        claims = FederationEnvelopeClaims(
            envelope_id='fed_live_blocked',
            source_host_id='host_peer',
            source_institution_id='org_peer',
            target_host_id='host_live',
            target_institution_id='org_founding',
            actor_type='service',
            actor_id='peer:host_peer',
            session_id='ses_peer',
            boundary_name='federation_gateway',
            identity_model='signed_host_service',
            message_type='settlement_notice',
            payload_hash='hash_live',
            warrant_id='war_live',
            commitment_id='cmt_live',
        )
        processing = self.workspace._process_received_federation_message(
            'org_founding',
            claims,
            {'receipt_id': 'fedrcpt_live', 'accepted_at': '2026-03-22T00:00:00Z'},
            payload={'tx_ref': 'tx_live'},
        )
        self.assertFalse(processing['applied'])
        self.assertEqual(processing['reason'], 'case_blocked')
        self.assertEqual(processing['state'], 'received')
        self.assertEqual(processing['case']['case_id'], 'case_live_demo')

    def test_deliver_federation_envelope_attaches_case_payload_on_preflight_block(self):
        from runtime_host import default_host_identity

        audit_events = []

        class FakeAuthority:
            def ensure_enabled(self):
                return True

        self.workspace._runtime_host_state = lambda _org_id: (
            default_host_identity(host_id='host_live', federation_enabled=True, peer_transport='https'),
            {'admitted_org_ids': ['org_founding']},
        )
        self.workspace._federation_authority = lambda _host: FakeAuthority()
        self.workspace.cases.blocking_peer_case = lambda target_host_id, **_kwargs: {
            'case_id': 'case_live_demo',
            'claim_type': 'misrouted_execution',
            'status': 'open',
            'target_host_id': target_host_id,
        }
        self.workspace.log_event = lambda *args, **kwargs: audit_events.append({
            'args': args,
            'kwargs': kwargs,
        })

        with self.assertRaises(PermissionError) as exc_info:
            self.workspace._deliver_federation_envelope(
                'org_founding',
                'host_peer',
                'org_peer',
                'settlement_notice',
                payload={'tx_ref': '0xabc'},
                actor_type='user',
                actor_id='user_owner',
                session_id='ses_demo',
            )

        self.assertEqual(exc_info.exception.case_record['case_id'], 'case_live_demo')
        self.assertEqual(exc_info.exception.federation_peer['peer_host_id'], 'host_peer')
        self.assertEqual(exc_info.exception.federation_peer['reason'], 'peer_not_registered')
        self.assertEqual(audit_events[0]['args'][2], 'federation_case_blocked')

    def test_federation_manifest_reports_founding_locked_runtime(self):
        from runtime_host import default_host_identity

        self.workspace._load_workspace_credentials = lambda: (None, None, None, None)
        self.workspace._get_founding_org = lambda: (
            'org_founding',
            {'id': 'org_founding', 'slug': 'meridian', 'name': 'Meridian', 'lifecycle_state': 'founding'},
        )
        manifest = self.workspace._federation_manifest(
            self.workspace._resolve_workspace_context(),
            host_identity=default_host_identity(
                host_id='host_live',
                label='Meridian Live Host',
                federation_enabled=False,
                supported_boundaries=['workspace', 'cli', 'federation_gateway'],
            ),
            admission_registry={
                'source': 'derived_bound_default',
                'host_id': 'host_live',
                'institutions': {'org_founding': {'status': 'admitted'}},
                'admitted_org_ids': ['org_founding'],
            },
        )
        self.assertEqual(manifest['host_identity']['host_id'], 'host_live')
        self.assertEqual(manifest['admission']['management_mode'], 'founding_locked')
        self.assertFalse(manifest['federation']['enabled'])
        self.assertTrue(manifest['service_registry']['federation_gateway']['requires_warrant'])

    def test_admission_snapshot_reports_founding_lock(self):
        from runtime_host import default_host_identity
        host = default_host_identity(
            host_id='host_live',
            label='Meridian Live Host',
            federation_enabled=False,
            supported_boundaries=['workspace', 'cli', 'federation_gateway'],
        )
        snap = self.workspace._admission_snapshot(
            'org_founding',
            host_identity=host,
            admission_registry={'source': 'derived_bound_default', 'admitted_org_ids': ['org_founding'], 'institutions': {'org_founding': {'status': 'admitted'}}},
        )
        self.assertEqual(snap['management_mode'], 'founding_locked')
        self.assertFalse(snap['mutation_enabled'])
        self.assertEqual(snap['mutation_disabled_reason'], 'single_institution_deployment')

    def test_mutate_admission_is_rejected_on_live(self):
        with self.assertRaises(PermissionError):
            self.workspace._mutate_admission('org_founding', 'admit', 'org_other')

    def test_accept_federation_request_fails_closed_when_disabled(self):
        from federation import FederationAuthority, FederationUnavailable
        from runtime_host import default_host_identity

        self.workspace.FEDERATION_SIGNING_SECRET = 'alpha-secret'
        self.workspace.load_host_identity = lambda *args, **kwargs: default_host_identity(
            host_id='host_live',
            label='Meridian Live Host',
            federation_enabled=False,
            peer_transport='none',
            supported_boundaries=['workspace', 'cli', 'federation_gateway'],
        )
        self.workspace.load_admission_registry = lambda *args, **kwargs: {
            'source': 'derived_bound_default',
            'host_id': 'host_live',
            'institutions': {'org_founding': {'status': 'admitted'}},
            'admitted_org_ids': ['org_founding'],
        }
        sender = FederationAuthority(
            default_host_identity(
                host_id='host_live',
                federation_enabled=True,
                peer_transport='https',
            ),
            signing_secret='alpha-secret',
        )
        envelope = sender.issue(
            'org_founding',
            'host_live',
            'org_founding',
            'execution_request',
            payload={'task': 'demo'},
        )
        with self.assertRaises(FederationUnavailable):
            self.workspace._accept_federation_request(
                'org_founding',
                envelope,
                payload={'task': 'demo'},
            )

    def test_deliver_federation_envelope_fails_closed_when_disabled(self):
        from federation import FederationUnavailable
        from runtime_host import default_host_identity

        self.workspace.load_host_identity = lambda *args, **kwargs: default_host_identity(
            host_id='host_live',
            label='Meridian Live Host',
            federation_enabled=False,
            peer_transport='none',
            supported_boundaries=['workspace', 'cli', 'federation_gateway'],
        )
        self.workspace.load_admission_registry = lambda *args, **kwargs: {
            'source': 'derived_bound_default',
            'host_id': 'host_live',
            'institutions': {'org_founding': {'status': 'admitted'}},
            'admitted_org_ids': ['org_founding'],
        }

        with self.assertRaises(FederationUnavailable):
            self.workspace._deliver_federation_envelope(
                'org_founding',
                'host_peer',
                'org_peer',
                'execution_request',
                payload={'task': 'demo'},
                actor_type='user',
                actor_id='user_owner',
                session_id='ses_demo',
            )


if __name__ == '__main__':
    unittest.main()
