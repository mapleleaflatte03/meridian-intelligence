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
        self.assertEqual(plan['workers'], ['ATLAS', 'AEGIS', 'QUILL'])


if __name__ == '__main__':
    unittest.main()
