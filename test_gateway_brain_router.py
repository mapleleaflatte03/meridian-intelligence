#!/usr/bin/env python3
import importlib.util
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock


def _resolve_workspace() -> Path:
    env_workspace = os.getenv("MERIDIAN_WORKSPACE_PATH")
    if env_workspace:
        return Path(env_workspace)
    return Path(__file__).resolve().parent


WORKSPACE = _resolve_workspace()
if str(WORKSPACE) not in sys.path:
    sys.path.insert(0, str(WORKSPACE))


def _install_mcp_server_stub() -> None:
    if "company.mcp_server" not in sys.modules:
        stub = types.ModuleType("company.mcp_server")

        def _unsupported(*_args, **_kwargs):
            raise RuntimeError("company.mcp_server stub invoked unexpectedly")

        stub.do_on_demand_research_route = _unsupported
        stub.do_qa_verify_route = _unsupported
        stub._specialist_direct_provider_fallback = _unsupported
        stub._shared_run_loom_capability = _unsupported
        stub._loom_runtime_context = _unsupported
        sys.modules["company.mcp_server"] = stub

    if "accounting" not in sys.modules:
        accounting = types.ModuleType("accounting")
        accounting.append_tx = lambda *_args, **_kwargs: None
        accounting.load_ledger = lambda *_args, **_kwargs: {"treasury": {"cash_usd": 0.0}}
        accounting.save_ledger = lambda *_args, **_kwargs: None
        sys.modules["accounting"] = accounting

    if "audit" not in sys.modules:
        audit = types.ModuleType("audit")
        audit.log_event = lambda *_args, **_kwargs: None
        sys.modules["audit"] = audit

    if "capsule" not in sys.modules:
        capsule = types.ModuleType("capsule")
        base = Path(tempfile.gettempdir()) / "meridian-intelligence-tests"
        base.mkdir(parents=True, exist_ok=True)
        ledger_path = base / "ledger.json"
        if not ledger_path.exists():
            ledger_path.write_text('{"treasury":{"cash_usd":0.0}}\n', encoding="utf-8")
        capsule.ensure_treasury_aliases = lambda *_args, **_kwargs: {"ledger": str(ledger_path)}
        capsule.ledger_path = lambda *_args, **_kwargs: str(ledger_path)
        sys.modules["capsule"] = capsule

    if "court" not in sys.modules:
        court = types.ModuleType("court")
        court.file_violation = lambda *_args, **_kwargs: {}
        court.get_restrictions = lambda *_args, **_kwargs: {}
        sys.modules["court"] = court

    if "warrants" not in sys.modules:
        warrants = types.ModuleType("warrants")
        warrants.issue_warrant = lambda *_args, **_kwargs: {}
        warrants.mark_warrant_executed = lambda *_args, **_kwargs: {}
        warrants.review_warrant = lambda *_args, **_kwargs: {}
        warrants.validate_warrant_for_execution = lambda *_args, **_kwargs: {"valid": True}
        sys.modules["warrants"] = warrants

    if "loom_runtime_client" not in sys.modules:
        runtime_client = types.ModuleType("loom_runtime_client")
        runtime_client.estimate_capability_cost_usd = lambda *_args, **_kwargs: 0.0
        runtime_client.format_estimated_cost_usd = lambda *_args, **_kwargs: "$0.00"
        sys.modules["loom_runtime_client"] = runtime_client

    if "loom_runtime_discovery" not in sys.modules:
        runtime_discovery = types.ModuleType("loom_runtime_discovery")
        runtime_discovery.preferred_loom_bin = lambda *_args, **_kwargs: "/usr/bin/loom"
        runtime_discovery.preferred_loom_root = lambda *_args, **_kwargs: str(
            Path(tempfile.gettempdir()) / "meridian-intelligence-tests"
        )
        runtime_discovery.runtime_value = (
            lambda _key, default=None, *_args, **_kwargs: default
        )
        sys.modules["loom_runtime_discovery"] = runtime_discovery

    if "session_history" not in sys.modules:
        session_history = types.ModuleType("session_history")
        session_history.append_session_event = lambda *_args, **_kwargs: None
        session_history.load_session_events = lambda *_args, **_kwargs: []
        sys.modules["session_history"] = session_history

    if "subscription_service" not in sys.modules:
        subscription_service = types.ModuleType("subscription_service")
        subscription_service.public_checkout_offer = lambda *_args, **_kwargs: {}
        subscription_service.subscription_summary = lambda *_args, **_kwargs: {}
        sys.modules["subscription_service"] = subscription_service

    if "team_topology" not in sys.modules:
        team_topology = types.ModuleType("team_topology")
        team_topology.SPECIALIST_KEYS = ("ATLAS", "SENTINEL", "FORGE", "QUILL", "AEGIS", "PULSE")

        class _Agent:
            def __init__(self, env_key, registry_id, handle, name, profile_name, model, task_kind):
                self.env_key = env_key
                self.registry_id = registry_id
                self.handle = handle
                self.name = name
                self.profile_name = profile_name
                self.model = model
                self.task_kind = task_kind

        class _Topology:
            def __init__(self):
                self.org_id = "org_test"
                self.manager = _Agent(
                    "MANAGER",
                    "agent_manager",
                    "main",
                    "Leviathann",
                    "manager_primary",
                    "reasoner-small",
                    "manage",
                )
                self.specialists = (
                    _Agent("ATLAS", "agent_atlas", "atlas", "Atlas", "atlas_specialist", "reasoner-small", "research"),
                    _Agent("SENTINEL", "agent_sentinel", "sentinel", "Sentinel", "sentinel_specialist", "reasoner-small", "verify"),
                    _Agent("FORGE", "agent_forge", "forge", "Forge", "forge_specialist", "reasoner-small", "execute"),
                    _Agent("QUILL", "agent_quill", "quill", "Quill", "quill_specialist", "reasoner-small", "write"),
                    _Agent("AEGIS", "agent_aegis", "aegis", "Aegis", "aegis_specialist", "reasoner-small", "qa_gate"),
                    _Agent("PULSE", "agent_pulse", "pulse", "Pulse", "pulse_specialist", "reasoner-small", "compress"),
                )

            def specialist_by_id(self, agent_id):
                token = str(agent_id or "").strip().lower()
                for agent in self.specialists:
                    if token in {agent.registry_id.lower(), agent.handle.lower(), agent.name.lower()}:
                        return agent
                return None

        team_topology.load_team_topology = lambda *_args, **_kwargs: _Topology()
        team_topology.sync_loom_team_profiles = lambda *_args, **_kwargs: {"ok": True}
        team_topology.default_imported_history_dir = (
            lambda *_args, **_kwargs: Path(tempfile.gettempdir()) / "meridian-intelligence-tests" / "imported"
        )
        sys.modules["team_topology"] = team_topology


_install_mcp_server_stub()

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
