#!/usr/bin/env python3
import importlib.util
import os
import unittest
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
        self.assertTrue(permissions['/api/federation/send']['allowed'])
        self.assertFalse(permissions['/api/federation/peers/refresh']['allowed'])
        self.assertEqual(permissions['/api/federation/peers/refresh']['required_role'], 'owner')

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
        ctx = self.workspace._resolve_workspace_context()
        status = self.workspace.api_status(institution_context=ctx)
        self.assertEqual(status['runtime_core']['institution_context']['org_id'], 'org_founding')
        self.assertFalse(status['runtime_core']['admission']['additional_institutions_allowed'])
        self.assertEqual(status['runtime_core']['admission']['management_mode'], 'founding_locked')
        self.assertFalse(status['runtime_core']['admission']['mutation_enabled'])
        self.assertTrue(status['runtime_core']['service_registry']['federation_gateway']['supports_institution_routing'])
        self.assertFalse(status['runtime_core']['service_registry']['mcp_service']['supports_institution_routing'])
        self.assertEqual(status['runtime_core']['host_identity']['host_id'], 'host_live')
        self.assertEqual(status['runtime_core']['admission']['admitted_org_ids'], ['org_founding'])
        self.assertEqual(status['runtime_core']['admission']['management_mode'], 'founding_locked')
        self.assertFalse(status['runtime_core']['admission']['mutation_enabled'])
        self.assertEqual(
            status['runtime_core']['admission']['mutation_disabled_reason'],
            'single_institution_deployment',
        )
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
