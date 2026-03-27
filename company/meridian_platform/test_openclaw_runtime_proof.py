#!/usr/bin/env python3
import importlib.util
import pathlib
import unittest
from unittest import mock


THIS_DIR = pathlib.Path(__file__).resolve().parent
MODULE_PATH = THIS_DIR / 'openclaw_runtime_proof.py'


def _load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


proof = _load_module('meridian_openclaw_runtime_proof_test', MODULE_PATH)


class OpenClawRuntimeProofTests(unittest.TestCase):
    def test_parse_openclaw_health_extracts_structured_runtime_state(self):
        output = """
Telegram: ok (@eggsama_bot) (1599ms)
Agents: main (default), atlas, sentinel, forge, quill, aegis, pulse
Heartbeat interval: 30m (main)
Session store (main): /root/.meridian/agents/main/sessions/sessions.json (25 entries)
- agent:main:main (3m ago)
- agent:main:cron:abc123 (85m ago)
"""
        parsed = proof.parse_openclaw_health(output)

        self.assertTrue(parsed['telegram']['ok'])
        self.assertEqual(parsed['telegram']['detail'], '@eggsama_bot')
        self.assertEqual(parsed['agent_count'], 7)
        self.assertEqual(parsed['heartbeat']['primary_agent'], 'main')
        self.assertEqual(parsed['session_total'], 25)
        self.assertEqual(parsed['session_stores'][0]['agent'], 'main')
        self.assertEqual(parsed['sessions'][0]['session_id'], 'main')
        self.assertEqual(parsed['agents'][0]['handle'], 'main')
        self.assertTrue(parsed['agents'][0]['is_default'])

    def test_map_governed_agents_uses_economy_key_then_name_fallback(self):
        agents = [
            {
                'id': 'agent_main',
                'org_id': 'org_1',
                'name': 'Leviathann',
                'economy_key': 'main',
                'runtime_binding': {'runtime_id': 'openclaw_compatible'},
            },
            {
                'id': 'agent_writer',
                'org_id': 'org_1',
                'name': 'Release Writer',
                'economy_key': '',
                'runtime_binding': {'runtime_id': 'openclaw_compatible'},
            },
        ]

        mapped = proof.map_governed_agents_to_openclaw_handles(agents)
        self.assertEqual(mapped[0]['openclaw_handle'], 'main')
        self.assertEqual(mapped[0]['handle_source'], 'economy_key')
        self.assertEqual(mapped[1]['openclaw_handle'], 'release_writer')
        self.assertEqual(mapped[1]['handle_source'], 'name')
        self.assertTrue(mapped[0]['has_openclaw_handle'])
        self.assertEqual(mapped[0]['runtime_binding']['runtime_id'], 'openclaw_compatible')

    def test_collect_openclaw_runtime_proof_combines_health_and_registry_truth(self):
        with mock.patch.object(proof, 'load_registry', return_value={
            'agents': {
                'agent_main': {
                    'id': 'agent_main',
                    'org_id': 'org_1',
                    'name': 'Leviathann',
                    'economy_key': 'main',
                    'runtime_binding': {'runtime_id': 'openclaw_compatible'},
                },
                'agent_atlas': {
                    'id': 'agent_atlas',
                    'org_id': 'org_1',
                    'name': 'Atlas',
                    'economy_key': 'atlas',
                    'runtime_binding': {'runtime_id': 'openclaw_compatible'},
                },
            }
        }):
            result = proof.collect_openclaw_runtime_proof(
                health_output="""
Telegram: ok (@eggsama_bot) (1599ms)
Agents: main (default), atlas, sentinel
Heartbeat interval: 30m (main)
Session store (main): /root/.meridian/agents/main/sessions/sessions.json (25 entries)
""",
            )

        self.assertEqual(result['proof_type'], 'live_single_host_openclaw_deployment')
        self.assertTrue(result['health']['health_ok'])
        self.assertEqual(result['governed_agents'][0]['openclaw_handle'], 'main')
        self.assertEqual(result['handle_overlap'], ['atlas', 'main'])
        self.assertEqual(result['handle_gap'], [])
        self.assertEqual(result['deployment_truth']['scope'], 'single_host')
        self.assertFalse(result['deployment_truth']['generic_runtime_claim'])
        self.assertEqual(result['runtime_id'], 'openclaw_compatible')
        self.assertFalse(result['pong_probe']['checked'])

    def test_public_receipt_filters_runtime_proof_for_public_route(self):
        receipt = proof.public_openclaw_runtime_receipt({
            'runtime_id': 'openclaw_compatible',
            'proof_type': 'live_single_host_openclaw_deployment',
            'checked_at': '2026-03-22T00:00:00Z',
            'deployment_truth': {'scope': 'single_host', 'generic_runtime_claim': False},
            'health': {
                'health_ok': True,
                'agent_count': 2,
                'session_total': 5,
                'agents': [{'handle': 'main'}, {'handle': 'atlas'}],
                'heartbeat': {'interval': '30m', 'primary_agent': 'main'},
                'telegram': {'ok': True},
            },
            'pong_probe': {'checked': True, 'ok': True, 'output': 'PONG'},
            'governed_agents': [
                {
                    'agent_id': 'agent_main',
                    'agent_name': 'Leviathann',
                    'org_id': 'org_1',
                    'role': 'manager',
                    'openclaw_handle': 'main',
                    'handle_source': 'economy_key',
                    'runtime_binding': {
                        'runtime_id': 'openclaw_compatible',
                        'runtime_registered': True,
                        'registration_status': 'registered',
                        'bound_org_id': 'org_1',
                    },
                }
            ],
            'handle_overlap': ['main'],
            'handle_gap': [],
        }, bound_org_id='org_1')

        self.assertEqual(receipt['bound_org_id'], 'org_1')
        self.assertEqual(receipt['health']['agent_handles'], ['main', 'atlas'])
        self.assertEqual(receipt['pong_probe']['output'], 'PONG')
        self.assertNotIn('session_stores', receipt['health'])
        self.assertEqual(receipt['governed_agents'][0]['runtime_binding']['runtime_id'], 'openclaw_compatible')


if __name__ == '__main__':
    unittest.main()
