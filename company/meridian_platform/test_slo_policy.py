#!/usr/bin/env python3
import datetime
import os
import sys
import unittest
from unittest import mock


THIS_DIR = os.path.dirname(os.path.abspath(__file__))
if THIS_DIR not in sys.path:
    sys.path.insert(0, THIS_DIR)

import slo_policy


class SloPolicyTests(unittest.TestCase):
    def test_evaluate_observability_returns_healthy_for_fresh_metrics(self):
        metrics = {
            'audit': {'latest_at': '2026-03-25T00:15:00Z'},
            'metering': {'latest_at': '2026-03-25T00:20:00Z', 'total_cost_usd': 1.25},
        }
        with mock.patch.object(slo_policy, '_now', return_value=datetime.datetime(2026, 3, 25, 0, 30, 0)):
            evaluation = slo_policy.evaluate_observability(metrics)

        self.assertEqual(evaluation['policy_name'], 'meridian_observability_slo_v1')
        self.assertEqual(evaluation['status'], 'healthy')
        self.assertEqual(evaluation['alert_count'], 0)
        self.assertEqual(evaluation['healthy_objective_count'], 3)

    def test_evaluate_observability_reports_warning_and_breach_thresholds(self):
        metrics = {
            'audit': {'latest_at': '2026-03-24T22:00:00Z'},
            'metering': {'latest_at': '2026-03-24T22:00:00Z', 'total_cost_usd': 120.0},
        }
        with mock.patch.object(slo_policy, '_now', return_value=datetime.datetime(2026, 3, 25, 0, 30, 0)):
            evaluation = slo_policy.evaluate_observability(metrics)

        self.assertEqual(evaluation['status'], 'breached')
        self.assertGreaterEqual(evaluation['alert_count'], 1)
        objective_names = {item['name'] for item in evaluation['objectives']}
        self.assertIn('monthly_metering_cost', objective_names)
        self.assertIn('monthly_metering_cost', {item['objective'] for item in evaluation['alerts']})


if __name__ == '__main__':
    unittest.main()
