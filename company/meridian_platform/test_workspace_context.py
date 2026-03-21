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
        self.orig_get_founding_org = self.workspace._get_founding_org
        self.orig_load_orgs = self.workspace.load_orgs
        self.orig_load_workspace_credentials = self.workspace._load_workspace_credentials

    def tearDown(self):
        self.workspace.WORKSPACE_ORG_ID = self.orig_workspace_org_id
        self.workspace._get_founding_org = self.orig_get_founding_org
        self.workspace.load_orgs = self.orig_load_orgs
        self.workspace._load_workspace_credentials = self.orig_load_workspace_credentials

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


if __name__ == '__main__':
    unittest.main()
