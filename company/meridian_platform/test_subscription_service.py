#!/usr/bin/env python3
import json
import os
import shutil
import tempfile
import unittest

import subscription_service


class SubscriptionServiceTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp(prefix='subscription-service-')
        self._subs = os.path.join(self._tmp, 'subscriptions.json')
        self._backup = os.path.join(self._tmp, 'subscriptions.json.bak')
        self._lock = os.path.join(self._tmp, '.subscriptions.lock')
        self._tx = os.path.join(self._tmp, 'transactions.jsonl')
        self.org_id = 'org_demo'

        with open(self._subs, 'w') as f:
            json.dump(subscription_service._default_subscriptions(self.org_id), f, indent=2)
        with open(self._backup, 'w') as f:
            json.dump(subscription_service._default_subscriptions(self.org_id), f, indent=2)
        with open(self._lock, 'w') as f:
            f.write('')
        with open(self._tx, 'w') as f:
            f.write('')

        self._orig_primary = subscription_service.subscriptions_path
        self._orig_backup = subscription_service.subscriptions_backup_path
        self._orig_lock = subscription_service.subscriptions_lock_path
        self._orig_ensure_aliases = subscription_service.ensure_subscription_aliases
        self._orig_load_transactions = subscription_service._revenue_mod.load_transactions

        subscription_service.subscriptions_path = lambda org_id=None: self._subs
        subscription_service.subscriptions_backup_path = lambda org_id=None: self._backup
        subscription_service.subscriptions_lock_path = lambda org_id=None: self._lock
        subscription_service.ensure_subscription_aliases = lambda org_id=None: None

        def _load_transactions():
            with open(self._tx) as f:
                return [
                    json.loads(line)
                    for line in f
                    if line.strip()
                ]

        subscription_service._revenue_mod.load_transactions = _load_transactions

    def tearDown(self):
        subscription_service.subscriptions_path = self._orig_primary
        subscription_service.subscriptions_backup_path = self._orig_backup
        subscription_service.subscriptions_lock_path = self._orig_lock
        subscription_service.ensure_subscription_aliases = self._orig_ensure_aliases
        subscription_service._revenue_mod.load_transactions = self._orig_load_transactions
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _append_tx(self, entry):
        with open(self._tx, 'a') as f:
            f.write(json.dumps(entry) + '\n')

    def test_default_and_normalized_meta_include_storage_and_identity(self):
        default_payload = subscription_service._default_subscriptions(self.org_id)
        self.assertEqual(default_payload['_meta']['service_scope'], 'institution_owned_subscription_service')
        self.assertEqual(default_payload['_meta']['boundary_name'], 'subscriptions')
        self.assertEqual(default_payload['_meta']['identity_model'], 'session')
        self.assertEqual(default_payload['_meta']['storage_model'], 'capsule_canonical_with_legacy_symlink')
        self.assertEqual(default_payload['_meta']['bound_org_id'], self.org_id)

        normalized = subscription_service._normalize_subscriptions(
            {
                'subscribers': {},
                '_meta': {'bound_org_id': 'org_existing'},
            },
            self.org_id,
        )
        self.assertEqual(normalized['_meta']['service_scope'], 'institution_owned_subscription_service')
        self.assertEqual(normalized['_meta']['boundary_name'], 'subscriptions')
        self.assertEqual(normalized['_meta']['identity_model'], 'session')
        self.assertEqual(normalized['_meta']['storage_model'], 'capsule_canonical_with_legacy_symlink')
        self.assertEqual(normalized['_meta']['bound_org_id'], self.org_id)

    def test_create_trial_subscription_returns_active_record(self):
        result = subscription_service.create_subscription(
            '100',
            plan='trial',
            org_id=self.org_id,
            actor='user:owner',
        )
        subscription = result['subscription']
        self.assertEqual(result['telegram_id'], '100')
        self.assertEqual(subscription['plan'], 'trial')
        self.assertTrue(subscription['payment_verified'])
        self.assertEqual(subscription['created_by'], 'user:owner')
        self.assertEqual(
            subscription_service.load_subscriptions(self.org_id)['_meta']['service_scope'],
            'institution_owned_subscription_service',
        )

    def test_convert_trial_subscription_binds_payment_evidence(self):
        subscription_service.create_subscription('200', plan='trial', org_id=self.org_id)
        self._append_tx({
            'type': 'customer_payment',
            'order_id': 'ord-convert',
            'amount': 2.99,
            'client': 'cust-convert',
            'product': 'pilot-weekly',
            'payment_key': 'ref:ref-convert',
            'payment_ref': 'ref-convert',
            'tx_hash': '0xconvert',
            'ts': subscription_service.now_ts(),
        })

        result = subscription_service.convert_trial_subscription(
            '200',
            'premium-brief-weekly',
            payment_ref='ref-convert',
            confirm_payment=True,
            org_id=self.org_id,
            actor='user:admin',
        )
        subscription = result['subscription']
        self.assertEqual(subscription['plan'], 'premium-brief-weekly')
        self.assertTrue(subscription['payment_verified'])
        self.assertEqual(subscription['payment_evidence']['order_id'], 'ord-convert')
        self.assertTrue(subscription['converted_from_trial'])
        payload = subscription_service.load_subscriptions(self.org_id)
        self.assertEqual(payload['subscribers']['200'][0]['status'], 'converted')
        self.assertEqual(payload['subscribers']['200'][1]['status'], 'active')
        self.assertTrue(payload['subscribers']['200'][1]['converted_from_trial'])

    def test_convert_trial_subscription_requires_active_trial(self):
        subscription_service.create_subscription('205', plan='trial', org_id=self.org_id)
        payload = subscription_service.load_subscriptions(self.org_id)
        payload['subscribers']['205'][0]['status'] = 'cancelled'
        subscription_service.save_subscriptions(payload, self.org_id)

        with self.assertRaisesRegex(LookupError, 'No active trial found'):
            subscription_service.convert_trial_subscription(
                '205',
                'premium-brief-weekly',
                payment_ref='ref-missing',
                confirm_payment=False,
                org_id=self.org_id,
                actor='user:admin',
            )

    def test_check_subscription_and_summary_report_counts(self):
        subscription_service.create_subscription('300', plan='trial', org_id=self.org_id)
        self._append_tx({
            'type': 'customer_payment',
            'order_id': 'ord-summary',
            'amount': 2.99,
            'client': 'cust-summary',
            'product': 'pilot-weekly',
            'payment_key': 'ref:ref-summary',
            'payment_ref': 'ref-summary',
            'tx_hash': '0xsummary',
            'ts': subscription_service.now_ts(),
        })
        subscription_service.create_subscription(
            '301',
            plan='premium-brief-weekly',
            payment_ref='ref-summary',
            confirm_payment=True,
            org_id=self.org_id,
        )

        status = subscription_service.check_subscription('300', org_id=self.org_id)
        summary = subscription_service.subscription_summary(self.org_id)
        self.assertTrue(status['found'])
        self.assertTrue(status['active'])
        self.assertTrue(status['eligible_for_delivery'])
        self.assertEqual(status['subscription_count'], 1)
        self.assertEqual(summary['subscriber_count'], 2)
        self.assertEqual(summary['subscription_count'], 2)
        self.assertEqual(summary['active_subscription_count'], 2)
        self.assertEqual(summary['verified_paid_subscription_count'], 2)
        self.assertEqual(summary['external_target_count'], 2)

    def test_verify_subscription_payment_binds_existing_paid_record(self):
        created = subscription_service.create_subscription(
            '300',
            plan='premium-brief-weekly',
            payment_ref='ref-verify',
            org_id=self.org_id,
        )
        self._append_tx({
            'type': 'customer_payment',
            'order_id': 'ord-verify',
            'amount': 2.99,
            'client': 'cust-verify',
            'product': 'pilot-weekly',
            'payment_key': 'ref:ref-verify',
            'payment_ref': 'ref-verify',
            'tx_hash': '0xverify',
            'ts': subscription_service.now_ts(),
        })

        result = subscription_service.verify_subscription_payment(
            '300',
            subscription_id=created['subscription']['id'],
            org_id=self.org_id,
            actor='user:admin',
        )
        subscription = result['subscription']
        self.assertTrue(subscription['payment_verified'])
        self.assertEqual(subscription['payment_evidence']['tx_hash'], '0xverify')
        self.assertEqual(subscription['payment_verified_by'], 'user:admin')

    def test_active_delivery_targets_requires_verified_payment_evidence(self):
        subscription_service.create_subscription(
            '400',
            plan='premium-brief-weekly',
            payment_ref='ref-target',
            org_id=self.org_id,
        )
        self.assertEqual(subscription_service.active_delivery_targets(self.org_id), [])

        self._append_tx({
            'type': 'customer_payment',
            'order_id': 'ord-target',
            'amount': 2.99,
            'client': 'cust-target',
            'product': 'pilot-weekly',
            'payment_key': 'ref:ref-target',
            'payment_ref': 'ref-target',
            'tx_hash': '0xtarget',
            'ts': subscription_service.now_ts(),
        })
        subscription_service.verify_subscription_payment(
            '400',
            payment_ref='ref-target',
            org_id=self.org_id,
            actor='user:admin',
        )
        self.assertEqual(subscription_service.active_delivery_targets(self.org_id), ['400'])


if __name__ == '__main__':
    unittest.main()
