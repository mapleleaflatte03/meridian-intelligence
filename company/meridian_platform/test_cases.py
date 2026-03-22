#!/usr/bin/env python3
import importlib.util
import os
import tempfile
import unittest


PLATFORM_DIR = os.path.dirname(os.path.abspath(__file__))
CASES_PY = os.path.join(PLATFORM_DIR, 'cases.py')


def _load_module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class CaseModuleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cases = _load_module(CASES_PY, 'cases_test_module')
        self.org_id = 'org_founding'
        self.orig_capsule_path = self.cases.capsule_path
        self.cases.capsule_path = lambda org_id, filename: os.path.join(self.tmp.name, filename)

    def tearDown(self):
        self.cases.capsule_path = self.orig_capsule_path
        self.tmp.cleanup()

    def test_open_stay_and_resolve_case(self):
        record = self.cases.open_case(
            self.org_id,
            'misrouted_execution',
            'user_owner',
            target_host_id='host_peer',
            target_institution_id='org_peer',
            note='Wrong target received',
        )
        self.assertEqual(record['status'], 'open')
        self.assertEqual(record['institution_id'], self.org_id)
        stayed = self.cases.stay_case(record['case_id'], 'user_owner', org_id=self.org_id, note='Freeze')
        self.assertEqual(stayed['status'], 'stayed')
        resolved = self.cases.resolve_case(record['case_id'], 'user_owner', org_id=self.org_id, note='Closed')
        self.assertEqual(resolved['status'], 'resolved')
        self.assertEqual(self.cases.case_summary(self.org_id)['resolved'], 1)

    def test_breach_helper_dedupes_case(self):
        commitment = {
            'commitment_id': 'com_demo',
            'target_host_id': 'host_peer',
            'target_institution_id': 'org_peer',
        }
        first, created_first = self.cases.ensure_case_for_commitment_breach(
            commitment,
            'user_owner',
            org_id=self.org_id,
        )
        second, created_second = self.cases.ensure_case_for_commitment_breach(
            commitment,
            'user_owner',
            org_id=self.org_id,
        )
        self.assertTrue(created_first)
        self.assertFalse(created_second)
        self.assertEqual(first['case_id'], second['case_id'])


if __name__ == '__main__':
    unittest.main()
