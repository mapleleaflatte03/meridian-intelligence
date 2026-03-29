#!/usr/bin/env python3
import importlib.util
import json
import pathlib
import unittest
from unittest import mock


THIS_DIR = pathlib.Path(__file__).resolve().parent
MODULE_PATH = THIS_DIR / 'loom_runtime_proof.py'


def _load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


proof = _load_module('loom_runtime_proof_test', MODULE_PATH)


class LoomRuntimeProofTests(unittest.TestCase):
    def test_parse_loom_health_extracts_structured_runtime_state(self):
        output = json.dumps({
            'status': 'healthy',
            'checks': [
                {'level': 'OK', 'label': 'agent_runtime', 'detail': 'profiles=7 agents=leviathann,atlas,quill memory_ready=7/7 session_ready=7/7'},
                {'level': 'OK', 'label': 'channel_runtime', 'detail': 'total=2 enabled=2 ingress=0 delivery_path=/tmp inbox_path=/tmp channels=web_api,telegram'},
                {'level': 'OK', 'label': 'session_provenance', 'detail': 'total=3 sessions=telegram:founder,web_api:owner,web_api:demo'},
            ],
        })
        parsed = proof.parse_loom_health(output)

        self.assertTrue(parsed['health_ok'])
        self.assertEqual(parsed['status'], 'healthy')
        self.assertTrue(parsed['telegram']['ok'])
        self.assertEqual(parsed['agent_count'], 3)
        self.assertEqual(parsed['agents'][0]['handle'], 'leviathann')
        self.assertEqual(parsed['session_total'], 3)
        self.assertEqual(parsed['session_runtime']['active_count'], 3)
        self.assertEqual(parsed['session_runtime']['archived_count'], 0)
        self.assertEqual(parsed['channel_runtime']['channel_ids'], ['web_api', 'telegram'])

    def test_map_governed_agents_uses_runtime_binding_truth(self):
        agents = [
            {
                'id': 'agent_main',
                'org_id': 'org_1',
                'name': 'Leviathann',
                'economy_key': 'main',
                'runtime_binding': {'runtime_id': 'loom_native'},
            },
            {
                'id': 'agent_writer',
                'org_id': 'org_1',
                'name': 'Release Writer',
                'economy_key': '',
                'runtime_binding': {'runtime_id': 'loom_native'},
            },
        ]

        mapped = proof.map_governed_agents_to_loom_handles(agents)
        self.assertEqual(mapped[0]['loom_handle'], 'main')
        self.assertEqual(mapped[0]['handle_source'], 'economy_key')
        self.assertEqual(mapped[0]['economy_key'], 'main')
        self.assertEqual(mapped[1]['loom_handle'], 'release_writer')
        self.assertEqual(mapped[1]['handle_source'], 'name')
        self.assertTrue(mapped[0]['has_loom_handle'])
        self.assertEqual(mapped[0]['runtime_binding']['runtime_id'], 'loom_native')

    def test_map_governed_agents_prefers_runtime_handle_alias_when_present(self):
        agents = [
            {
                'id': 'agent_main',
                'org_id': 'org_1',
                'name': 'Leviathann',
                'economy_key': 'main',
                'runtime_binding': {'runtime_id': 'loom_native'},
            }
        ]

        mapped = proof.map_governed_agents_to_loom_handles(agents, runtime_handles=['leviathann', 'atlas'])
        self.assertEqual(mapped[0]['loom_handle'], 'leviathann')
        self.assertEqual(mapped[0]['economy_key'], 'main')
        self.assertEqual(mapped[0]['handle_source'], 'runtime_match')
        self.assertEqual(mapped[0]['handle_candidates'], ['main', 'leviathann', 'agent_main'])

    def test_collect_loom_runtime_proof_combines_health_and_registry_truth(self):
        service_status = json.dumps({
            'running': True,
            'service_status': 'running',
            'health': 'healthy',
            'transport': 'socket+http',
        })
        memory_status = json.dumps({
            'agent_count': 1,
            'total_entries': 2,
            'total_bytes': 128,
            'agents': [{'agent_id': 'atlas', 'entry_count': 2, 'categories': ['general'], 'total_bytes': 128}],
        })
        context_status = json.dumps({
            'layer_count': 12,
            'section_count': 6,
            'mutable_count': 4,
            'sections': ['agents', 'heartbeat', 'memory', 'soul', 'tools', 'user'],
        })

        def fake_run(command, timeout):
            if 'service' in command:
                return {
                    'command': list(command),
                    'ok': True,
                    'returncode': 0,
                    'stdout': service_status,
                    'stderr': '',
                }
            if 'memory' in command:
                return {
                    'command': list(command),
                    'ok': True,
                    'returncode': 0,
                    'stdout': memory_status,
                    'stderr': '',
                }
            if 'context' in command:
                return {
                    'command': list(command),
                    'ok': True,
                    'returncode': 0,
                    'stdout': context_status,
                    'stderr': '',
                }
            raise AssertionError(f'unexpected command: {command}')

        with mock.patch.object(proof, 'load_registry', return_value={
            'agents': {
                'agent_main': {
                    'id': 'agent_main',
                    'org_id': 'org_1',
                    'name': 'Leviathann',
                    'economy_key': 'main',
                    'runtime_binding': {'runtime_id': 'loom_native'},
                },
                'agent_atlas': {
                    'id': 'agent_atlas',
                    'org_id': 'org_1',
                    'name': 'Atlas',
                    'economy_key': 'atlas',
                    'runtime_binding': {'runtime_id': 'loom_native'},
                },
            }
        }), mock.patch.object(proof, '_run_command', side_effect=fake_run):
            result = proof.collect_loom_runtime_proof(
                health_output=json.dumps({
                    'status': 'healthy',
                    'checks': [
                        {'level': 'OK', 'label': 'agent_runtime', 'detail': 'profiles=7 agents=leviathann,atlas,sentinel memory_ready=7/7 session_ready=7/7'},
                        {'level': 'OK', 'label': 'channel_runtime', 'detail': 'total=2 enabled=2 ingress=0 active_deliveries=4 archived_deliveries=1 delivery_path=/tmp inbox_path=/tmp channels=web_api,telegram'},
                        {'level': 'OK', 'label': 'session_provenance', 'detail': 'total=3 active=2 archived=1 sessions=web_api:owner,telegram:founder'},
                    ],
                }),
                include_service_probe=True,
                service_probe_command=['loom', 'service', 'status', '--format', 'json'],
            )

        self.assertEqual(result['proof_type'], 'live_single_host_loom_deployment')
        self.assertTrue(result['health']['health_ok'])
        self.assertEqual(result['governed_agents'][0]['loom_handle'], 'leviathann')
        self.assertEqual(result['governed_agents'][0]['economy_key'], 'main')
        self.assertEqual(result['handle_overlap'], ['atlas', 'leviathann'])
        self.assertEqual(result['handle_gap'], [])
        self.assertEqual(result['deployment_truth']['scope'], 'single_host')
        self.assertFalse(result['deployment_truth']['generic_runtime_claim'])
        self.assertEqual(result['runtime_id'], 'loom_native')
        self.assertTrue(result['memory_context']['checked'])
        self.assertTrue(result['memory_context']['memory_ok'])
        self.assertTrue(result['memory_context']['context_ok'])
        self.assertEqual(result['memory_context']['memory']['total_entries'], 2)
        self.assertEqual(result['memory_context']['context']['section_count'], 6)
        self.assertEqual(result['health']['session_runtime']['active_count'], 2)
        self.assertEqual(result['health']['session_runtime']['archived_count'], 1)
        self.assertEqual(result['health']['channel_runtime']['active_delivery_count'], 4)
        self.assertEqual(result['health']['channel_runtime']['archived_delivery_count'], 1)

    def test_public_receipt_filters_runtime_proof_for_public_route(self):
        receipt = proof.public_loom_runtime_receipt({
            'runtime_id': 'loom_native',
            'proof_type': 'live_single_host_loom_deployment',
            'checked_at': '2026-03-22T00:00:00Z',
            'deployment_truth': {'scope': 'single_host', 'generic_runtime_claim': False},
            'health': {
                'status': 'healthy',
                'health_ok': True,
                'telegram': {'ok': True},
                'agent_count': 2,
                'agents': [{'handle': 'leviathann'}, {'handle': 'atlas'}],
                'heartbeat': {'interval': None, 'primary_agent': None},
                'session_total': 0,
                'session_runtime': {'total_count': 6, 'active_count': 4, 'archived_count': 2},
                'channel_runtime': {'total_count': 2, 'enabled_count': 2, 'ingress_count': 16, 'active_delivery_count': 13, 'archived_delivery_count': 16},
            },
            'service_probe': {'checked': True, 'ok': True, 'output': 'service running', 'service_status': 'running', 'health': 'healthy', 'transport': 'socket+http'},
            'memory_context': {
                'checked': True,
                'memory_ok': True,
                'context_ok': True,
                'memory': {'total_entries': 2},
                'context': {'section_count': 6},
            },
            'governed_agents': [
                {
                    'agent_id': 'agent_main',
                    'agent_name': 'Leviathann',
                    'org_id': 'org_1',
                    'role': 'manager',
                    'loom_handle': 'leviathann',
                    'economy_key': 'main',
                    'handle_candidates': ['main', 'leviathann', 'agent_main'],
                    'handle_source': 'runtime_match',
                    'runtime_binding': {
                        'runtime_id': 'loom_native',
                        'runtime_registered': True,
                        'registration_status': 'registered',
                        'bound_org_id': 'org_1',
                    },
                }
            ],
            'handle_overlap': ['leviathann'],
            'handle_gap': [],
        }, bound_org_id='org_1')

        self.assertEqual(receipt['runtime_id'], 'loom_native')
        self.assertTrue(receipt['runtime_health']['health_ok'])
        self.assertTrue(receipt['runtime_health']['service_probe_ok'])
        self.assertEqual(receipt['service_probe']['status'], 'running')
        self.assertEqual(receipt['runtime_surfaces']['session_provenance']['active_count'], 4)
        self.assertEqual(receipt['runtime_surfaces']['channel_runtime']['archived_delivery_count'], 16)
        self.assertTrue(receipt['memory_context']['checked'])
        self.assertTrue(receipt['memory_context']['memory_ok'])
        self.assertTrue(receipt['memory_context']['context_ok'])
        self.assertEqual(receipt['health']['agent_handles'], ['leviathann', 'atlas'])
        self.assertEqual(receipt['governed_agents'][0]['runtime_binding']['runtime_id'], 'loom_native')
        self.assertEqual(receipt['governed_agents'][0]['economy_key'], 'main')
        self.assertEqual(receipt['governed_agents'][0]['loom_handle'], 'leviathann')


if __name__ == '__main__':
    unittest.main()
