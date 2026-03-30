#!/usr/bin/env python3
import importlib.util
import sys
import unittest
from pathlib import Path
from unittest import mock

WORKSPACE = Path('/home/ubuntu/.meridian/workspace')
if str(WORKSPACE) not in sys.path:
    sys.path.insert(0, str(WORKSPACE))

spec = importlib.util.spec_from_file_location('meridian_gateway_test', WORKSPACE / 'meridian_gateway.py')
meridian_gateway = importlib.util.module_from_spec(spec)
spec.loader.exec_module(meridian_gateway)


class GatewayTeamRouteTests(unittest.TestCase):
    def test_parse_telegram_command_modes(self):
        self.assertEqual(meridian_gateway._parse_telegram_command('/help'), {'mode': 'help', 'arg': ''})
        self.assertEqual(meridian_gateway._parse_telegram_command('/atlas OpenAI pricing'), {'mode': 'team', 'arg': 'OpenAI pricing'})
        self.assertEqual(meridian_gateway._parse_telegram_command('/aegis factual::hello'), {'mode': 'team', 'arg': 'factual::hello'})
        self.assertEqual(meridian_gateway._parse_telegram_command('plain text'), {'mode': 'team', 'arg': 'plain text'})

    def test_run_team_route_uses_specialists_and_returns_manager_answer(self):
        runtime = mock.Mock()
        runtime.run_goal.return_value = 'direct answer'

        with mock.patch.object(meridian_gateway, '_team_route_plan', return_value={
            'mode': 'team',
            'topic': 'pricing',
            'depth': 'standard',
            'criteria': 'factual',
            'workers': ['ATLAS', 'AEGIS'],
            'reason': 'needs coordination',
        }):
            with mock.patch.object(meridian_gateway, '_run_specialist_step', side_effect=[
                {'agent_id': 'agent_atlas', 'request_id': 'job-r', 'result': 'atlas research'},
                {'agent_id': 'agent_aegis', 'request_id': 'job-v', 'result': 'aegis verification'},
            ]) as specialist_mock:
                with mock.patch.object(meridian_gateway, '_manager_synthesis', return_value='manager answer'):
                    answer, meta = meridian_gateway._run_team_route('Please research pricing', 'telegram:123', runtime)

        self.assertEqual(answer, 'manager answer')
        self.assertEqual(meta['mode'], 'team')
        self.assertEqual(meta['job_id'], 'job-v')
        self.assertEqual(len(meta['steps']), 2)
        self.assertEqual(specialist_mock.call_args_list[0].args[0], 'ATLAS')
        self.assertEqual(specialist_mock.call_args_list[1].args[0], 'AEGIS')
        runtime.run_goal.assert_not_called()

    def test_run_team_route_direct_mode_uses_manager(self):
        runtime = mock.Mock()
        runtime.run_goal.return_value = 'direct answer'

        with mock.patch.object(meridian_gateway, '_team_route_plan', return_value={'mode': 'direct', 'reason': 'greeting'}):
            with mock.patch.object(meridian_gateway, '_manager_direct_response', return_value='manager answer'):
                answer, meta = meridian_gateway._run_team_route('hi', 'telegram:123', runtime)

        self.assertEqual(answer, 'manager answer')
        self.assertEqual(meta['mode'], 'direct')
        runtime.run_goal.assert_not_called()

    def test_planner_fallback_adds_quill_for_writer_request(self):
        with mock.patch.object(meridian_gateway, '_run_codex_exec', return_value={'ok': False, 'output_text': ''}):
            plan = meridian_gateway._team_route_plan(
                'Write a short Meridian founder answer explaining why users should talk to Leviathann instead of direct specialists.',
                'web_api:org_48b05c21',
            )
        self.assertEqual(plan['mode'], 'team')
        self.assertEqual(plan['workers'], ['QUILL', 'AEGIS'])

    def test_complex_governance_request_does_not_collapse_to_internal_status(self):
        prompt = (
            'Leviathann, handle this as an operator crisis workflow. '
            'I need a truthful response that explains the current Meridian governance posture, '
            'states what happens if Sentinel is sanction-restricted while QA is still required, '
            'and produces an internal remediation plan for Telegram delivery and founder-facing messaging.'
        )
        plan = meridian_gateway._team_route_plan(prompt, 'telegram:5322393870')
        self.assertEqual(plan['mode'], 'team')
        self.assertEqual(plan['reason'], 'meridian_operator_workflow')
        self.assertIn('FORGE', plan['workers'])
        self.assertIn('AEGIS', plan['workers'])
        self.assertIn('QUILL', plan['workers'])

    def test_forge_receipt_backfills_from_runtime_result_when_worker_result_missing(self):
        plan = {
            'manager_brief': 'Draft the operational remediation sequence.',
            'topic': 'operator crisis',
            'criteria': 'consistency',
        }
        loom_result = {'ok': True, 'job_id': 'job-forge', 'worker_result': {}}
        backfill = {
            'host_response_json': {
                'output_text': '```json\n{"result":"forge sequence","confidence":0.8,"citations":[],"warnings":["host warning"]}\n```'
            }
        }
        with mock.patch.object(meridian_gateway, 'append_session_event'):
            with mock.patch.object(meridian_gateway.mcp_server, '_shared_run_loom_capability', return_value=loom_result):
                with mock.patch.object(meridian_gateway, '_load_runtime_job_result', return_value=backfill):
                    receipt = meridian_gateway._run_specialist_step('FORGE', 'Need remediation plan', 'telegram:5322393870', plan)
        self.assertEqual(receipt['status'], 'ok')
        self.assertEqual(receipt['result'], 'forge sequence')
        self.assertEqual(receipt['warnings'], ['host warning'])


if __name__ == '__main__':
    unittest.main()
