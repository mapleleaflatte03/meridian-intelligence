#!/usr/bin/env python3
import importlib.util
import os
import tempfile
import types
import unittest
from unittest import mock


PLATFORM_DIR = os.path.dirname(os.path.abspath(__file__))
COMMITMENTS_PY = os.path.join(PLATFORM_DIR, 'commitments.py')
WORKSPACE_PY = os.path.join(PLATFORM_DIR, 'workspace.py')


def _load_module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class CommitmentModuleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.commitments = _load_module(COMMITMENTS_PY, 'commitments_test_module')
        self.org_id = 'org_founding'
        self.orig_capsule_path = self.commitments.capsule_path
        self.commitments.capsule_path = lambda org_id, filename: os.path.join(self.tmp.name, filename)

    def tearDown(self):
        self.commitments.capsule_path = self.orig_capsule_path
        self.tmp.cleanup()

    def test_commitment_lifecycle_and_delivery_refs(self):
        record = self.commitments.propose_commitment(
            'host_peer',
            'org_peer',
            'Deliver the approved brief',
            commitment_id='com_demo',
            proposed_by='user_owner',
            note='Founding pilot agreement',
            org_id=self.org_id,
        )
        self.assertEqual(record['status'], 'proposed')
        self.assertEqual(record['institution_id'], self.org_id)
        self.assertEqual(record['target_institution_id'], 'org_peer')

        record = self.commitments.accept_commitment('com_demo', 'user_owner', org_id=self.org_id)
        self.assertEqual(record['status'], 'accepted')
        self.assertEqual(record['accepted_by'], 'user_owner')

        validated = self.commitments.validate_commitment_for_delivery(
            'com_demo',
            target_host_id='host_peer',
            target_institution_id='org_peer',
            org_id=self.org_id,
        )
        self.assertEqual(validated['commitment_id'], 'com_demo')

        record = self.commitments.record_delivery_ref(
            'com_demo',
            {
                'receipt_id': 'fedrcpt_123',
                'envelope_id': 'fed_123',
                'message_type': 'execution_request',
            },
            org_id=self.org_id,
        )
        self.assertEqual(len(record['delivery_refs']), 1)
        self.assertEqual(record['delivery_refs'][0]['receipt_id'], 'fedrcpt_123')
        self.assertIn('recorded_at', record['delivery_refs'][0])

        record = self.commitments.settle_commitment('com_demo', 'user_owner', org_id=self.org_id)
        self.assertEqual(record['status'], 'settled')
        summary = self.commitments.commitment_summary(self.org_id)
        self.assertEqual(summary['total'], 1)
        self.assertEqual(summary['settled'], 1)
        self.assertEqual(summary['delivery_refs_total'], 1)

    def test_commitment_delivery_validation_rejects_target_mismatch(self):
        self.commitments.propose_commitment(
            'host_peer',
            'org_peer',
            'Deliver the approved brief',
            commitment_id='com_mismatch',
            proposed_by='user_owner',
            org_id=self.org_id,
        )
        self.commitments.accept_commitment('com_mismatch', 'user_owner', org_id=self.org_id)

        with self.assertRaises(ValueError):
            self.commitments.validate_commitment_for_delivery(
                'com_mismatch',
                target_host_id='host_other',
                target_institution_id='org_peer',
                org_id=self.org_id,
            )


class WorkspaceCommitmentTests(unittest.TestCase):
    def setUp(self):
        self.workspace = _load_module(WORKSPACE_PY, 'workspace_commitment_test_module')

    def test_federation_send_records_commitment_delivery_ref(self):
        host_identity = types.SimpleNamespace(host_id='host_live')
        delivery = {
            'peer': {'transport': 'https'},
            'claims': {'envelope_id': 'fed_abc'},
            'receipt': {
                'receipt_id': 'fedrcpt_abc',
                'receiver_host_id': 'host_peer',
                'receiver_institution_id': 'org_peer',
            },
            'response': {},
        }
        fake_authority = types.SimpleNamespace(
            ensure_enabled=lambda: None,
            deliver=mock.Mock(return_value=delivery),
            snapshot=mock.Mock(return_value={'enabled': True}),
        )

        commitment_validate = mock.Mock(return_value={
            'commitment_id': 'com_demo',
            'status': 'accepted',
            'target_host_id': 'host_peer',
            'target_institution_id': 'org_peer',
        })
        commitment_record = mock.Mock(return_value={
            'commitment_id': 'com_demo',
            'status': 'accepted',
            'delivery_refs': [],
        })
        fake_commitments = types.SimpleNamespace(
            validate_commitment_for_delivery=commitment_validate,
            record_delivery_ref=commitment_record,
        )

        with mock.patch.object(self.workspace, '_runtime_host_state', return_value=(host_identity, {'admitted_org_ids': ['org_founding']})), \
             mock.patch.object(self.workspace, '_federation_authority', return_value=fake_authority), \
             mock.patch.object(self.workspace, 'log_event'), \
             mock.patch.object(self.workspace, 'commitments', fake_commitments):
            delivery_result, federation_state = self.workspace._deliver_federation_envelope(
                'org_founding',
                'host_peer',
                'org_peer',
                'notice',
                payload={'task': 'demo'},
                actor_type='user',
                actor_id='user_owner',
                session_id='ses_demo',
                commitment_id='com_demo',
            )

        self.assertEqual(federation_state['enabled'], True)
        self.assertEqual(delivery_result['claims']['envelope_id'], 'fed_abc')
        commitment_validate.assert_called_once_with(
            'com_demo',
            target_host_id='host_peer',
            target_institution_id='org_peer',
            org_id='org_founding',
        )
        commitment_record.assert_called_once()
        recorded_ref = commitment_record.call_args.args[1]
        self.assertEqual(recorded_ref['receipt_id'], 'fedrcpt_abc')
        self.assertEqual(recorded_ref['target_host_id'], 'host_peer')
        self.assertEqual(recorded_ref['target_institution_id'], 'org_peer')


if __name__ == '__main__':
    unittest.main()
