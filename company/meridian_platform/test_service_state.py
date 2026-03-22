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
        self._orig_subscriptions_path = service_state.subscriptions_path
        self._orig_owner_ledger_path = service_state.owner_ledger_path
        self._orig_legacy_subscriptions = service_state.LEGACY_SUBSCRIPTIONS_FILE
        self._orig_legacy_owner = service_state.LEGACY_OWNER_LEDGER_FILE
        self._orig_workspace = service_state.WORKSPACE

        service_state.subscriptions_path = lambda org_id=None: self._subs
        service_state.owner_ledger_path = lambda org_id=None: self._owner
        service_state.LEGACY_SUBSCRIPTIONS_FILE = os.path.join(self._tmp, 'company', 'subscriptions.json')
        service_state.LEGACY_OWNER_LEDGER_FILE = os.path.join(self._tmp, 'company', 'owner_ledger.json')
        service_state.WORKSPACE = self._tmp

    def tearDown(self):
        service_state.subscriptions_path = self._orig_subscriptions_path
        service_state.owner_ledger_path = self._orig_owner_ledger_path
        service_state.LEGACY_SUBSCRIPTIONS_FILE = self._orig_legacy_subscriptions
        service_state.LEGACY_OWNER_LEDGER_FILE = self._orig_legacy_owner
        service_state.WORKSPACE = self._orig_workspace
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

        snap = service_state.subscription_snapshot('org_demo')
        self.assertEqual(snap['bound_org_id'], 'org_demo')
        self.assertEqual(snap['storage_model'], 'capsule_canonical_with_legacy_symlink')
        self.assertEqual(snap['summary']['subscriber_count'], 2)
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
        self.assertEqual(snap['storage_model'], 'capsule_canonical_with_legacy_symlink')
        self.assertEqual(snap['summary']['capital_contributed_usd'], 2.0)
        self.assertEqual(snap['summary']['expenses_paid_usd'], 1.25)
        self.assertEqual(snap['summary']['reimbursements_received_usd'], 0.5)
        self.assertEqual(snap['summary']['draws_taken_usd'], 0.25)
        self.assertEqual(snap['summary']['unreimbursed_expenses_usd'], 0.75)
        self.assertEqual(snap['summary']['entry_count'], 1)


if __name__ == '__main__':
    unittest.main()
