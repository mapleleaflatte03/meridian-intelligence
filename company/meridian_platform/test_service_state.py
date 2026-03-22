#!/usr/bin/env python3
import json
import os
import shutil
import tempfile
import unittest

import service_state


class ServiceStateTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp(prefix='service-state-')
        self._subs = os.path.join(self._tmp, 'subscriptions.json')
        self._owner = os.path.join(self._tmp, 'owner_ledger.json')
        self._ledger = os.path.join(self._tmp, 'ledger.json')
        self._orig_subscriptions_path = service_state.subscriptions_path
        self._orig_owner_ledger_path = service_state.owner_ledger_path
        self._orig_ledger_path = service_state.ledger_path
        self._orig_ensure_subscription_aliases = service_state.ensure_subscription_aliases
        self._orig_ensure_accounting_aliases = service_state.ensure_accounting_aliases
        self._orig_legacy_subscriptions = service_state.LEGACY_SUBSCRIPTIONS_FILE
        self._orig_legacy_owner = service_state.LEGACY_OWNER_LEDGER_FILE
        self._orig_workspace = service_state.WORKSPACE
        self._orig_subscription_summary = service_state.subscription_service.subscription_summary

        service_state.subscriptions_path = lambda org_id=None: self._subs
        service_state.owner_ledger_path = lambda org_id=None: self._owner
        service_state.ledger_path = lambda org_id=None: self._ledger
        service_state.ensure_subscription_aliases = lambda org_id=None: {
            'subscriptions': self._subs,
            'subscriptions_backup': self._subs + '.bak',
            'subscriptions_lock': self._subs + '.lock',
            'canonical_source': 'capsule_file',
            'canonical_service_module': 'company.meridian_platform.subscription_service',
            'compatibility_mode': 'legacy_symlink',
            'compatibility_module': 'company.subscriptions',
            'canonical_paths': {
                'subscriptions': self._subs,
                'subscriptions_backup': self._subs + '.bak',
                'subscriptions_lock': self._subs + '.lock',
            },
            'legacy_paths': {
                'subscriptions': os.path.join(self._tmp, 'company', 'subscriptions.json'),
                'subscriptions_backup': os.path.join(self._tmp, 'company', 'subscriptions.json.bak'),
                'subscriptions_lock': os.path.join(self._tmp, 'company', '.subscriptions.lock'),
            },
        }
        service_state.ensure_accounting_aliases = lambda org_id=None: {
            'owner_ledger': self._owner,
            'canonical_source': 'capsule_file',
            'canonical_service_module': 'company.meridian_platform.accounting_service',
            'compatibility_mode': 'legacy_symlink',
            'compatibility_module': 'company.accounting',
            'canonical_paths': {
                'owner_ledger': self._owner,
            },
            'legacy_paths': {
                'owner_ledger': os.path.join(self._tmp, 'company', 'owner_ledger.json'),
            },
        }
        service_state.LEGACY_SUBSCRIPTIONS_FILE = os.path.join(self._tmp, 'company', 'subscriptions.json')
        service_state.LEGACY_OWNER_LEDGER_FILE = os.path.join(self._tmp, 'company', 'owner_ledger.json')
        service_state.WORKSPACE = self._tmp

    def tearDown(self):
        service_state.subscriptions_path = self._orig_subscriptions_path
        service_state.owner_ledger_path = self._orig_owner_ledger_path
        service_state.ledger_path = self._orig_ledger_path
        service_state.ensure_subscription_aliases = self._orig_ensure_subscription_aliases
        service_state.ensure_accounting_aliases = self._orig_ensure_accounting_aliases
        service_state.LEGACY_SUBSCRIPTIONS_FILE = self._orig_legacy_subscriptions
        service_state.LEGACY_OWNER_LEDGER_FILE = self._orig_legacy_owner
        service_state.WORKSPACE = self._orig_workspace
        service_state.subscription_service.subscription_summary = self._orig_subscription_summary
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_subscription_snapshot_reports_canonical_summary(self):
        with open(self._subs, 'w') as f:
            json.dump({
                'subscribers': {
                    '100': [
                        {'status': 'active', 'plan': 'trial', 'expires_at': ''},
                        {'status': 'cancelled', 'plan': 'premium-brief-monthly', 'expires_at': ''},
                    ],
                    '200': [
                        {
                            'status': 'active',
                            'plan': 'premium-brief-monthly',
                            'payment_verified': True,
                            'expires_at': '',
                        },
                    ],
                },
                'delivery_log': [{'id': 'd1'}, {'id': 'd2'}],
                '_meta': {
                    'service_scope': 'founding_meridian_service',
                    'bound_org_id': 'org_demo',
                    'internal_test_ids': ['100'],
                },
            }, f, indent=2)

        service_state.subscription_service.subscription_summary = lambda org_id=None: {
            'subscriber_count': 2,
            'subscription_count': 3,
            'active_subscription_count': 2,
            'verified_paid_subscription_count': 1,
            'delivery_log_count': 2,
            'internal_test_id_count': 1,
            'external_target_count': 1,
        }
        snap = service_state.subscription_snapshot('org_demo')
        self.assertEqual(snap['bound_org_id'], 'org_demo')
        self.assertEqual(snap['storage_model'], 'capsule_canonical_with_legacy_symlink')
        self.assertEqual(snap['management_mode'], 'institution_owned_service')
        self.assertTrue(snap['mutation_enabled'])
        self.assertEqual(snap['identity_model'], 'session')
        self.assertEqual(snap['meta']['service_scope'], 'institution_owned_subscription_service')
        self.assertEqual(snap['meta']['boundary_name'], 'subscriptions')
        self.assertEqual(snap['canonical_source'], 'service_module')
        self.assertEqual(
            snap['canonical_service_module'],
            'company.meridian_platform.subscription_service',
        )
        self.assertEqual(snap['canonical_path'], 'subscriptions.json')
        self.assertEqual(snap['legacy_path_role'], 'compatibility_symlink')
        self.assertEqual(snap['legacy_path'], 'company/subscriptions.json')
        self.assertEqual(snap['compatibility_module'], 'company.subscriptions')
        self.assertEqual(snap['compatibility_mode'], 'legacy_shim')
        self.assertEqual(snap['alias_registry']['canonical_source'], 'capsule_file')
        self.assertEqual(
            snap['alias_registry']['canonical_paths']['subscriptions'],
            'subscriptions.json',
        )
        self.assertEqual(
            snap['alias_registry']['legacy_paths']['subscriptions'],
            'company/subscriptions.json',
        )
        self.assertEqual(snap['alias_registry']['compatibility_mode'], 'legacy_symlink')
        self.assertIn('/api/subscriptions/add', snap['mutation_paths'])
        self.assertIn('/api/subscriptions/convert', snap['mutation_paths'])
        self.assertIn('/api/subscriptions/verify-payment', snap['mutation_paths'])
        self.assertIn('/api/subscriptions/remove', snap['mutation_paths'])
        self.assertEqual(snap['summary']['subscriber_count'], 2)
        self.assertEqual(snap['summary']['subscription_count'], 3)
        self.assertEqual(snap['summary']['active_subscription_count'], 2)
        self.assertEqual(snap['summary']['verified_paid_subscription_count'], 1)
        self.assertEqual(snap['summary']['delivery_log_count'], 2)
        self.assertEqual(snap['summary']['internal_test_id_count'], 1)
        self.assertEqual(snap['summary']['external_target_count'], 1)

    def test_accounting_snapshot_reports_owner_summary(self):
        with open(self._owner, 'w') as f:
            json.dump({
                'owner': 'Son Nguyen The',
                'capital_contributed_usd': 2.0,
                'expenses_paid_usd': 1.25,
                'reimbursements_received_usd': 0.5,
                'draws_taken_usd': 0.25,
                'entries': [{'type': 'capital_contribution'}],
                '_meta': {
                    'service_scope': 'founding_meridian_service',
                    'bound_org_id': 'org_demo',
                },
            }, f, indent=2)

        snap = service_state.accounting_snapshot('org_demo')
        self.assertEqual(snap['bound_org_id'], 'org_demo')
        self.assertEqual(snap['storage_model'], 'capsule_owned_owner_ledger')
        self.assertEqual(snap['management_mode'], 'institution_owned_service')
        self.assertTrue(snap['mutation_enabled'])
        self.assertEqual(snap['identity_model'], 'session')
        self.assertEqual(snap['meta']['service_scope'], 'institution_owned_service')
        self.assertEqual(snap['canonical_source'], 'service_module')
        self.assertEqual(
            snap['canonical_service_module'],
            'company.meridian_platform.accounting_service',
        )
        self.assertEqual(snap['canonical_path'], 'owner_ledger.json')
        self.assertEqual(snap['legacy_path_role'], 'compatibility_symlink')
        self.assertEqual(snap['legacy_path'], 'company/owner_ledger.json')
        self.assertEqual(snap['compatibility_module'], 'company.accounting')
        self.assertEqual(snap['compatibility_mode'], 'legacy_shim')
        self.assertEqual(snap['alias_registry']['canonical_source'], 'capsule_file')
        self.assertEqual(
            snap['alias_registry']['canonical_paths']['owner_ledger'],
            'owner_ledger.json',
        )
        self.assertEqual(
            snap['alias_registry']['legacy_paths']['owner_ledger'],
            'company/owner_ledger.json',
        )
        self.assertEqual(snap['alias_registry']['compatibility_mode'], 'legacy_symlink')
        self.assertIn('/api/accounting/expense', snap['mutation_paths'])
        self.assertEqual(snap['summary']['capital_contributed_usd'], 2.0)
        self.assertEqual(snap['summary']['expenses_paid_usd'], 1.25)
        self.assertEqual(snap['summary']['reimbursements_received_usd'], 0.5)
        self.assertEqual(snap['summary']['draws_taken_usd'], 0.25)
        self.assertEqual(snap['summary']['unreimbursed_expenses_usd'], 0.75)
        self.assertEqual(snap['summary']['entry_count'], 1)

    def test_accounting_snapshot_backfills_owner_capital_from_treasury(self):
        with open(self._owner, 'w') as f:
            json.dump({
                'capital_contributed_usd': 0.0,
                'expenses_paid_usd': 0.0,
                'reimbursements_received_usd': 0.0,
                'draws_taken_usd': 0.0,
                'entries': [],
                '_meta': {'bound_org_id': 'org_demo'},
            }, f, indent=2)
        with open(self._ledger, 'w') as f:
            json.dump({
                'treasury': {
                    'owner_capital_contributed_usd': 2.0,
                }
            }, f, indent=2)

        snap = service_state.accounting_snapshot('org_demo')

        self.assertEqual(snap['summary']['capital_contributed_usd'], 2.0)
        self.assertEqual(snap['management_mode'], 'institution_owned_service')
        self.assertTrue(snap['meta']['capital_sync_backfilled'])
        self.assertEqual(snap['meta']['capital_sync_source'], 'treasury_ledger')

        with open(self._owner) as f:
            saved = json.load(f)
        self.assertEqual(saved['capital_contributed_usd'], 2.0)
        self.assertEqual(saved['entries'][-1]['type'], 'capital_contribution_backfill')


if __name__ == '__main__':
    unittest.main()
