#!/usr/bin/env python3
import importlib.util
import os
import unittest
from unittest import mock

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..'))
SCRIPT_PATH = os.path.join(ROOT, 'run_live_demo_phase6.py')


def _load_script():
    spec = importlib.util.spec_from_file_location('run_live_demo_phase6_test_module', SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


live_demo_phase6 = _load_script()


class LiveDemoPhase6Tests(unittest.TestCase):
    def test_run_demo_binds_local_payment_evidence_and_reports_blocked_runtime(self):
        with mock.patch.object(live_demo_phase6.subscription_service, '_loom_delivery_capability', return_value=''), \
             mock.patch.object(live_demo_phase6.subscription_service, '_loom_bin', return_value='/tmp/missing-loom-binary'):
            result = live_demo_phase6.run_demo(disable_outbound_dispatch=True, timeout=1)

        payment_entry = result['payment_entry']
        capture_result = result['capture_result']
        runtime = result['runtime']

        self.assertEqual(payment_entry['type'], 'customer_payment')
        self.assertIn('No blockchain settlement', payment_entry['note'])
        self.assertTrue(capture_result['subscription']['payment_verified'])
        self.assertEqual(capture_result['subscription']['payment_evidence']['payment_ref'], payment_entry['payment_ref'])
        self.assertEqual(capture_result['delivery_run']['state'], 'blocked')
        self.assertFalse(capture_result['delivery_run']['delivered'])
        self.assertEqual(capture_result['delivery_artifact'], {})
        self.assertEqual(runtime['capability_name'], '')
        self.assertFalse(runtime['preflight']['ok'])
        self.assertIn('No Loom delivery capability is configured', runtime['preflight']['errors'])
        blockchain = live_demo_phase6.delivery_blockchain_artifact(result)
        self.assertEqual(blockchain['artifact'], '')
        self.assertEqual(blockchain['artifact_type'], '')
        self.assertFalse(os.path.exists(result['demo_state_dir']))

    def test_delivery_blockchain_artifact_prefers_runtime_settlement_refs(self):
        result = {
            'capture_result': {
                'delivery_run': {
                    'execution_refs': {
                        'settlement_adapter': 'segregated_hot_wallet',
                        'proof_type': 'onchain_receipt',
                        'proof': {'signed_raw_hex': '0xfeedbeef'},
                    },
                },
            },
        }

        artifact = live_demo_phase6.delivery_blockchain_artifact(result)
        self.assertEqual(artifact['artifact_type'], 'signed_raw_hex')
        self.assertEqual(artifact['artifact'], '0xfeedbeef')
        self.assertEqual(artifact['artifact_source'], 'delivery_run.execution_refs.proof')
        self.assertEqual(artifact['settlement_adapter'], 'segregated_hot_wallet')
        self.assertEqual(artifact['proof_type'], 'onchain_receipt')


if __name__ == '__main__':
    unittest.main()
