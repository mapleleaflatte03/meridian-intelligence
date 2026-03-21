#!/usr/bin/env python3
import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import accounting
import agent_registry
import authority
import capsule
import court
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
                'epoch': {'number': 8, 'auth_decay_per_epoch': 5},
                'agents': {
                    'atlas': {
                        'name': 'Atlas',
                        'status': 'active',
                        'reputation_units': 91,
                        'authority_units': 42,
                        'last_scored_at': '2026-03-21T00:00:00Z',
                        'probation': False,
                        'zero_authority': False,
                        'lead_ban': False,
                        'remediation_only': False,
                    },
                    'sentinel': {
                        'name': 'Sentinel',
                        'status': 'active',
                        'reputation_units': 50,
                        'authority_units': 25,
                        'last_scored_at': '2026-03-21T00:00:00Z',
                        'probation': False,
                        'zero_authority': False,
                        'lead_ban': False,
                        'remediation_only': False,
                    },
                },
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
        self._orig_accounting_ensure = accounting.ensure_treasury_aliases
        self._orig_accounting_ledger_path = accounting.capsule_ledger_path
        self._orig_accounting_owner = accounting.OWNER_LEDGER
        self._orig_accounting_transactions = accounting.TRANSACTIONS
        self._orig_registry_ensure = agent_registry.ensure_treasury_aliases
        self._orig_registry_ledger_path = agent_registry.capsule_ledger_path
        self._orig_registry_file = agent_registry.REGISTRY_FILE
        self._orig_authority_default = authority._default_org_id
        self._orig_authority_ensure = authority.ensure_treasury_aliases
        self._orig_authority_ledger_path = authority.capsule_ledger_path
        self._orig_court_default = court._default_org_id
        self._orig_court_ensure = court.ensure_treasury_aliases
        self._orig_court_ledger_path = court.capsule_ledger_path

        treasury._default_org_id = lambda: self.org_id
        treasury.ensure_treasury_aliases = capsule.ensure_treasury_aliases
        treasury.capsule_ledger_path = capsule.ledger_path
        treasury.capsule_revenue_path = capsule.revenue_path

        accounting.ensure_treasury_aliases = capsule.ensure_treasury_aliases
        accounting.capsule_ledger_path = capsule.ledger_path
        accounting.OWNER_LEDGER = os.path.join(self._tmp, 'owner_ledger.json')
        accounting.TRANSACTIONS = os.path.join(self._tmp, 'transactions.jsonl')

        agent_registry.ensure_treasury_aliases = capsule.ensure_treasury_aliases
        agent_registry.capsule_ledger_path = capsule.ledger_path
        agent_registry.REGISTRY_FILE = os.path.join(self._tmp, 'agent_registry.json')
        with open(agent_registry.REGISTRY_FILE, 'w') as f:
            json.dump({
                'agents': {
                    'agent_atlas_123456': {
                        'id': 'agent_atlas_123456',
                        'org_id': self.org_id,
                        'name': 'Atlas',
                        'role': 'analyst',
                        'purpose': 'Research',
                        'model_policy': {},
                        'scopes': ['research'],
                        'budget': {'max_per_run_usd': 1.0, 'max_per_day_usd': 5.0, 'max_per_month_usd': 100.0},
                        'approval_required': False,
                        'rollout_state': 'active',
                        'sla': {},
                        'reputation_units': 1,
                        'authority_units': 1,
                        'sponsor_id': None,
                        'risk_state': 'nominal',
                        'lifecycle_state': 'active',
                        'economy_key': 'atlas',
                        'incident_count': 0,
                        'escalation_path': [],
                        'status': 'active',
                        'created_at': '2026-03-21T00:00:00Z',
                        'last_active_at': '2026-03-21T00:00:00Z',
                    }
                },
                'updatedAt': '2026-03-21T00:00:00Z',
            }, f, indent=2)

        authority._default_org_id = lambda: self.org_id
        authority.ensure_treasury_aliases = capsule.ensure_treasury_aliases
        authority.capsule_ledger_path = capsule.ledger_path

        court._default_org_id = lambda: self.org_id
        court.ensure_treasury_aliases = capsule.ensure_treasury_aliases
        court.capsule_ledger_path = capsule.ledger_path

    def tearDown(self):
        capsule.CAPSULES_DIR = self._orig_capsules_dir
        capsule.LEGACY_LEDGER_FILE = self._orig_legacy_ledger
        capsule.LEGACY_REVENUE_FILE = self._orig_legacy_revenue
        capsule.default_org_id = self._orig_default_org

        treasury._default_org_id = self._orig_treasury_default
        treasury.ensure_treasury_aliases = self._orig_treasury_ensure
        treasury.capsule_ledger_path = self._orig_treasury_ledger_path
        treasury.capsule_revenue_path = self._orig_treasury_revenue_path

        accounting.ensure_treasury_aliases = self._orig_accounting_ensure
        accounting.capsule_ledger_path = self._orig_accounting_ledger_path
        accounting.OWNER_LEDGER = self._orig_accounting_owner
        accounting.TRANSACTIONS = self._orig_accounting_transactions

        agent_registry.ensure_treasury_aliases = self._orig_registry_ensure
        agent_registry.capsule_ledger_path = self._orig_registry_ledger_path
        agent_registry.REGISTRY_FILE = self._orig_registry_file

        authority._default_org_id = self._orig_authority_default
        authority.ensure_treasury_aliases = self._orig_authority_ensure
        authority.capsule_ledger_path = self._orig_authority_ledger_path

        court._default_org_id = self._orig_court_default
        court.ensure_treasury_aliases = self._orig_court_ensure
        court.capsule_ledger_path = self._orig_court_ledger_path

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

    def test_accounting_writes_through_capsule_alias(self):
        capsule.ensure_treasury_aliases(self.org_id)
        result = accounting.contribute_capital(1.0, 'test deposit', actor='owner')
        self.assertEqual(result['cash_after_usd'], 8.5)
        with open(self._legacy_ledger) as f:
            ledger = json.load(f)
        self.assertEqual(ledger['treasury']['cash_usd'], 8.5)
        self.assertEqual(ledger['treasury']['owner_capital_contributed_usd'], 3.0)

    def test_agent_registry_sync_reads_capsule_alias(self):
        capsule.ensure_treasury_aliases(self.org_id)
        agent_registry.sync_from_economy()
        with open(agent_registry.REGISTRY_FILE) as f:
            registry = json.load(f)
        atlas = registry['agents']['agent_atlas_123456']
        self.assertEqual(atlas['reputation_units'], 91)
        self.assertEqual(atlas['authority_units'], 42)

    def test_authority_and_court_use_capsule_ledger_alias(self):
        capsule.ensure_treasury_aliases(self.org_id)
        lead_id, lead_auth = authority.get_sprint_lead(self.org_id)
        self.assertEqual(lead_id, 'atlas')
        self.assertEqual(lead_auth, 42)

        violation_id = court.file_violation(
            agent_id='atlas',
            org_id=self.org_id,
            violation_type='false_confidence',
            severity=5,
            evidence='test evidence',
        )
        self.assertTrue(violation_id.startswith('vio_'))
        with open(self._legacy_ledger) as f:
            ledger = json.load(f)
        self.assertTrue(ledger['agents']['atlas']['zero_authority'])


if __name__ == '__main__':
    unittest.main()
