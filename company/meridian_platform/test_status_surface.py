#!/usr/bin/env python3
import importlib.util
import json
import os
import sqlite3
import sys
import tempfile
import unittest
from unittest import mock


THIS_DIR = os.path.dirname(os.path.abspath(__file__))
if THIS_DIR not in sys.path:
    sys.path.insert(0, THIS_DIR)

STATUS_PATH = os.path.join(THIS_DIR, 'status_surface.py')
SPEC = importlib.util.spec_from_file_location('meridian_status_surface', STATUS_PATH)
status_surface = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(status_surface)

import audit
import metering
import observability_store


class StatusSurfaceTests(unittest.TestCase):
    def test_persistence_snapshot_exposes_concrete_file_backed_seams(self):
        snapshot = status_surface.persistence_snapshot('org_founding')
        self.assertEqual(snapshot['backend'], 'file-backed-jsonl')
        self.assertEqual(snapshot['db']['status'], 'absent')
        seam_names = {os.path.basename(seam['path']) for seam in snapshot['seams']}
        seam_owners = {seam['owner'] for seam in snapshot['seams']}
        self.assertIn('audit_log.jsonl', seam_names)
        self.assertIn('metering.jsonl', seam_names)
        self.assertIn('accounting_service.py', seam_owners)

    def test_observability_snapshot_summarizes_file_backed_metrics(self):
        with tempfile.TemporaryDirectory() as tmp:
            audit_path = os.path.join(tmp, 'audit_log.jsonl')
            metering_path = os.path.join(tmp, 'metering.jsonl')
            with open(audit_path, 'w') as f:
                f.write(json.dumps({
                    'org_id': 'org_founding',
                    'agent_id': 'agent_main',
                    'action': 'status_check',
                    'outcome': 'success',
                    'timestamp': '2026-03-25T00:00:00Z',
                }) + '\n')
            with open(metering_path, 'w') as f:
                f.write(json.dumps({
                    'org_id': 'org_founding',
                    'agent_id': 'agent_main',
                    'metric': 'mcp_tool_call',
                    'quantity': 1,
                    'unit': 'calls',
                    'cost_usd': 1.25,
                    'timestamp': '2026-03-25T00:01:00Z',
                }) + '\n')
            with mock.patch.object(audit, 'AUDIT_FILE', audit_path),                  mock.patch.object(metering, 'METERING_FILE', metering_path):
                snapshot = status_surface.observability_snapshot('org_founding')

        self.assertEqual(snapshot['backend'], 'file-backed-jsonl')
        self.assertEqual(snapshot['metrics']['audit']['total_events'], 1)
        self.assertEqual(snapshot['metrics']['metering']['total_cost_usd'], 1.25)
        self.assertEqual(snapshot['slo']['status'], 'not_formalized')
        self.assertEqual(snapshot['metrics']['metering']['latest_metric'], 'mcp_tool_call')

    def test_sqlite_observability_mirror_supports_queries_and_export(self):
        with tempfile.TemporaryDirectory() as tmp:
            audit_path = os.path.join(tmp, 'audit_log.jsonl')
            metering_path = os.path.join(tmp, 'metering.jsonl')
            with mock.patch.object(audit, 'AUDIT_FILE', audit_path), \
                 mock.patch.object(metering, 'METERING_FILE', metering_path):
                audit.log_event(
                    'org_founding',
                    'agent_main',
                    'status_check',
                    resource='workspace',
                    outcome='success',
                    details={'surface': 'status'},
                )
                metering.record(
                    'org_founding',
                    'agent_main',
                    'mcp_tool_call',
                    quantity=2,
                    unit='calls',
                    cost_usd=1.25,
                    run_id='run_1',
                    details={'surface': 'status'},
                )

            db_path = observability_store.db_path_for_log(audit_path)
            self.assertTrue(os.path.exists(db_path))
            with sqlite3.connect(db_path) as conn:
                audit_count = conn.execute('SELECT COUNT(*) FROM audit_events').fetchone()[0]
                meter_count = conn.execute('SELECT COUNT(*) FROM metering_events').fetchone()[0]
            self.assertEqual(audit_count, 1)
            self.assertEqual(meter_count, 1)

            with mock.patch.object(audit, 'AUDIT_FILE', audit_path), \
                 mock.patch.object(metering, 'METERING_FILE', metering_path):
                snapshot = status_surface.observability_snapshot('org_founding')
                metrics_text = status_surface.observability_metrics_text('org_founding')

        self.assertEqual(snapshot['backend'], 'sqlite+jsonl')
        self.assertEqual(snapshot['db']['status'], 'present')
        self.assertEqual(snapshot['export']['route'], '/metrics')
        self.assertIn('meridian_audit_events_total{org_id="org_founding"} 1', metrics_text)
        self.assertIn('meridian_metering_cost_usd_total{org_id="org_founding"} 1.2500', metrics_text)


if __name__ == '__main__':
    unittest.main()
