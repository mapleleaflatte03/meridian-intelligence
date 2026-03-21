#!/usr/bin/env python3
import importlib.util
import os
import sys
import unittest
from unittest.mock import patch


THIS_DIR = os.path.dirname(os.path.abspath(__file__))
if THIS_DIR not in sys.path:
    sys.path.insert(0, THIS_DIR)

READINESS_PATH = os.path.join(THIS_DIR, "readiness.py")
SPEC = importlib.util.spec_from_file_location("meridian_readiness", READINESS_PATH)
readiness = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(readiness)


class ReadinessVerdictTests(unittest.TestCase):
    def _fake_run(self, runtime_ok=True, preflight_ok=True):
        def fake_run(cmd, cwd=readiness.WORKSPACE, timeout=30):
            if cmd[:2] == ["openclaw", "health"]:
                return {
                    "ok": runtime_ok,
                    "returncode": 0 if runtime_ok else 1,
                    "stdout": "",
                    "stderr": "",
                }
            if cmd[:3] == ["openclaw", "agent", "--agent"]:
                return {
                    "ok": runtime_ok,
                    "returncode": 0 if runtime_ok else 1,
                    "stdout": "PONG" if runtime_ok else "",
                    "stderr": "",
                }
            if len(cmd) >= 2 and cmd[1] == readiness.CI_VERTICAL_PY:
                return {
                    "ok": preflight_ok,
                    "returncode": 0 if preflight_ok else 1,
                    "stdout": "PREFLIGHT: OK" if preflight_ok else "PREFLIGHT: BLOCKED -- policy gate",
                    "stderr": "",
                }
            raise AssertionError(f"Unexpected command: {cmd}")

        return fake_run

    def _collect(self, treasury, phase_num, phase_name, preflight_ok):
        with patch.object(readiness, "_run", side_effect=self._fake_run(preflight_ok=preflight_ok)), \
             patch.object(readiness, "treasury_snapshot", return_value=treasury), \
             patch.object(readiness._phase_mod, "evaluate", return_value=(phase_num, {
                 "name": phase_name,
                 "next_phase": phase_num + 1 if phase_num < 6 else None,
                 "next_phase_name": "Next Phase" if phase_num < 6 else None,
                 "next_unlock": "Do the next thing" if phase_num < 6 else None,
             })), \
             patch.object(readiness, "_latest_brief", return_value={"exists": False, "path": None, "date": None}), \
             patch.object(readiness, "_delivery_targets", return_value={
                 "ok": True,
                 "count": 0,
                 "internal_test_count": 2,
                 "targets": ["1053016694", "6114408283"],
                 "external_targets": [],
                 "stderr": "",
             }):
            return readiness.collect()

    def test_owner_blocked_treasury_takes_precedence_over_phase(self):
        report = self._collect(
            treasury={
                "balance_usd": 2.0,
                "reserve_floor_usd": 50.0,
                "runway_usd": -48.0,
                "shortfall_usd": 48.0,
                "above_reserve": False,
            },
            phase_num=0,
            phase_name="Founder-Backed Build",
            preflight_ok=False,
        )
        self.assertEqual(report["verdict"], "OWNER_BLOCKED_TREASURY")
        self.assertEqual(report["phase"]["number"], 0)
        self.assertEqual(report["treasury"]["customer_revenue_usd"], 0.0)
        self.assertEqual(report["treasury"]["support_received_usd"], 0.0)
        self.assertEqual(report["treasury"]["owner_capital_usd"], 0.0)

    def test_phase_blocked_automation_requires_phase_four_even_if_cash_is_clear(self):
        report = self._collect(
            treasury={
                "balance_usd": 75.0,
                "reserve_floor_usd": 50.0,
                "runway_usd": 25.0,
                "shortfall_usd": 0.0,
                "above_reserve": True,
            },
            phase_num=2,
            phase_name="Customer-Validated Pilot",
            preflight_ok=False,
        )
        self.assertEqual(report["verdict"], "PHASE_BLOCKED_AUTOMATION")
        self.assertEqual(report["phase"]["name"], "Customer-Validated Pilot")
        self.assertEqual(report["treasury"]["customer_revenue_usd"], 0.0)

    def test_constitution_blocked_preflight_only_after_automation_phase(self):
        report = self._collect(
            treasury={
                "balance_usd": 100.0,
                "reserve_floor_usd": 50.0,
                "runway_usd": 50.0,
                "shortfall_usd": 0.0,
                "above_reserve": True,
            },
            phase_num=4,
            phase_name="Treasury-Cleared Automation",
            preflight_ok=False,
        )
        self.assertEqual(report["verdict"], "CONSTITUTION_BLOCKED_PREFLIGHT")


if __name__ == "__main__":
    unittest.main()
