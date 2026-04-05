#!/usr/bin/env python3
import importlib.util
import sys
import unittest
from pathlib import Path
from unittest import mock

WORKSPACE = Path("/home/ubuntu/.meridian/workspace")
if str(WORKSPACE) not in sys.path:
    sys.path.insert(0, str(WORKSPACE))

spec = importlib.util.spec_from_file_location("meridian_gateway_test_brain_router", WORKSPACE / "meridian_gateway.py")
meridian_gateway = importlib.util.module_from_spec(spec)
spec.loader.exec_module(meridian_gateway)


class GatewayBrainRouterIntegrationTests(unittest.TestCase):
    def test_run_codex_exec_delegates_to_brain_router(self):
        expected = {
            "ok": True,
            "output_text": "router answer",
            "provider_profile": "manager_primary",
            "transport_kind": "http_json",
            "auth_mode": "bearer_pool",
            "model": "reasoner-small",
        }
        with mock.patch.object(
            meridian_gateway.brain_router,
            "execute_manager",
            return_value=expected,
        ) as exec_mock:
            result = meridian_gateway._run_codex_exec(
                system_prompt="sys",
                user_prompt="usr",
                model="reasoner-small",
                timeout=5,
            )
        self.assertEqual(result, expected)
        exec_mock.assert_called_once()

    def test_manager_exec_metadata_delegates_to_brain_router(self):
        expected = {
            "provider_profile": "manager_primary",
            "model": "reasoner-small",
            "transport_kind": "http_json",
            "auth_mode": "bearer_pool",
        }
        with mock.patch.object(
            meridian_gateway.brain_router,
            "manager_exec_metadata",
            return_value=expected,
        ) as meta_mock:
            result = meridian_gateway._manager_exec_metadata("reasoner-small")
        self.assertEqual(result, expected)
        meta_mock.assert_called_once()

    def test_loom_manager_defaults_prefers_brain_router_model(self):
        with mock.patch.object(
            meridian_gateway.brain_router,
            "manager_exec_metadata",
            return_value={
                "provider_profile": "manager_primary",
                "model": "reasoner-small",
                "transport_kind": "http_json",
                "auth_mode": "bearer_pool",
            },
        ):
            defaults = meridian_gateway._loom_manager_defaults()
        self.assertEqual(defaults["provider_profile"], "manager_primary")
        self.assertEqual(defaults["model"], "reasoner-small")


if __name__ == "__main__":
    unittest.main()
