#!/usr/bin/env python3
import json
import os
import shutil
import tempfile
import unittest

import capsule
import treasury


class TreasuryCapsuleTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp(prefix='treasury-capsule-')
        self._capsules_dir = os.path.join(self._tmp, 'capsules')
        self._legacy_ledger = os.path.join(self._tmp, 'ledger.json')
        self._legacy_revenue = os.path.join(self._tmp, 'revenue.json')
        self.org_id = 'org_live'

        with open(self._legacy_ledger, 'w') as f:
            json.dump({
                'treasury': {
                    'cash_usd': 7.5,
                    'reserve_floor_usd': 5.0,
                    'total_revenue_usd': 3.0,
                    'support_received_usd': 1.25,
                    'owner_capital_contributed_usd': 2.0,
                    'owner_draws_usd': 0.0,
                }
            }, f, indent=2)
        with open(self._legacy_revenue, 'w') as f:
            json.dump({
                'clients': {'cli_a': {'id': 'cli_a', 'name': 'Client A'}},
                'orders': {
                    'ord_a': {
                        'id': 'ord_a',
                        'client': 'cli_a',
                        'status': 'paid',
                        'product': 'brief',
                    }
                },
                'receivables_usd': 0.0,
            }, f, indent=2)

        self._orig_capsules_dir = capsule.CAPSULES_DIR
        self._orig_legacy_ledger = capsule.LEGACY_LEDGER_FILE
        self._orig_legacy_revenue = capsule.LEGACY_REVENUE_FILE
        self._orig_default_org = capsule.default_org_id

        capsule.CAPSULES_DIR = self._capsules_dir
        capsule.LEGACY_LEDGER_FILE = self._legacy_ledger
        capsule.LEGACY_REVENUE_FILE = self._legacy_revenue
        capsule.default_org_id = lambda: self.org_id

        self._orig_treasury_default = treasury._default_org_id
        self._orig_treasury_ensure = treasury.ensure_treasury_aliases
        self._orig_treasury_ledger_path = treasury.capsule_ledger_path
        self._orig_treasury_revenue_path = treasury.capsule_revenue_path

        treasury._default_org_id = lambda: self.org_id
        treasury.ensure_treasury_aliases = capsule.ensure_treasury_aliases
        treasury.capsule_ledger_path = capsule.ledger_path
        treasury.capsule_revenue_path = capsule.revenue_path

    def tearDown(self):
        capsule.CAPSULES_DIR = self._orig_capsules_dir
        capsule.LEGACY_LEDGER_FILE = self._orig_legacy_ledger
        capsule.LEGACY_REVENUE_FILE = self._orig_legacy_revenue
        capsule.default_org_id = self._orig_default_org

        treasury._default_org_id = self._orig_treasury_default
        treasury.ensure_treasury_aliases = self._orig_treasury_ensure
        treasury.capsule_ledger_path = self._orig_treasury_ledger_path
        treasury.capsule_revenue_path = self._orig_treasury_revenue_path

        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_aliases_are_symlinks_to_legacy_state(self):
        aliases = capsule.ensure_treasury_aliases(self.org_id)
        self.assertTrue(os.path.islink(aliases['ledger']))
        self.assertTrue(os.path.islink(aliases['revenue']))
        self.assertEqual(os.path.realpath(aliases['ledger']), self._legacy_ledger)
        self.assertEqual(os.path.realpath(aliases['revenue']), self._legacy_revenue)

    def test_matching_regular_file_is_replaced_with_symlink(self):
        org_dir = os.path.join(self._capsules_dir, self.org_id)
        os.makedirs(org_dir, exist_ok=True)
        ledger_alias = os.path.join(org_dir, 'ledger.json')
        with open(self._legacy_ledger) as src, open(ledger_alias, 'w') as dst:
            dst.write(src.read())
        capsule.ensure_treasury_aliases(self.org_id)
        self.assertTrue(os.path.islink(ledger_alias))
        self.assertEqual(os.path.realpath(ledger_alias), self._legacy_ledger)

    def test_treasury_reads_through_capsule_aliases(self):
        capsule.ensure_treasury_aliases(self.org_id)
        self.assertEqual(treasury.get_balance(self.org_id), 7.5)
        snap = treasury.treasury_snapshot(self.org_id)
        self.assertEqual(snap['support_received_usd'], 1.25)
        self.assertEqual(snap['owner_capital_usd'], 2.0)
        self.assertEqual(snap['clients'], 1)
        self.assertEqual(snap['paid_orders'], 1)


if __name__ == '__main__':
    unittest.main()
