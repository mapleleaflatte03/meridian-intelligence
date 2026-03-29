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
        self.assertEqual(meridian_gateway._parse_telegram_command('/atlas OpenAI pricing'), {'mode': 'atlas', 'arg': 'OpenAI pricing'})
        self.assertEqual(meridian_gateway._parse_telegram_command('/aegis factual::hello'), {'mode': 'aegis', 'arg': 'factual::hello'})
        self.assertEqual(meridian_gateway._parse_telegram_command('plain text'), {'mode': 'team', 'arg': 'plain text'})

    def test_run_team_route_uses_specialists_and_returns_manager_answer(self):
        runtime = mock.Mock()
        runtime.run_goal.return_value = 'direct answer'

        with mock.patch.object(meridian_gateway, '_team_route_plan', return_value={
            'mode': 'team',
            'topic': 'pricing',
            'depth': 'standard',
            'criteria': 'factual',
            'reason': 'needs coordination',
        }):
            with mock.patch.object(meridian_gateway.mcp_server, 'do_on_demand_research_route', return_value={
                'research': 'atlas research',
                'job_id': 'job-r',
            }) as research_mock:
                with mock.patch.object(meridian_gateway.mcp_server, 'do_qa_verify_route', return_value={
                    'verification': 'aegis verification',
                    'job_id': 'job-v',
                }) as verify_mock:
                    with mock.patch.object(meridian_gateway, '_manager_synthesis', return_value='manager answer'):
                        answer, meta = meridian_gateway._run_team_route('Please research pricing', 'telegram:123', runtime)

        self.assertEqual(answer, 'manager answer')
        self.assertEqual(meta['mode'], 'team')
        self.assertEqual(meta['job_id'], 'job-v')
        self.assertEqual(len(meta['steps']), 2)
        self.assertEqual(research_mock.call_args.kwargs['session_id'], 'telegram:123')
        self.assertEqual(research_mock.call_args.kwargs['agent_id'], meridian_gateway.TEAM_RESEARCH_AGENT_ID)
        self.assertEqual(verify_mock.call_args.kwargs['session_id'], 'telegram:123')
        self.assertEqual(verify_mock.call_args.kwargs['agent_id'], meridian_gateway.TEAM_VERIFY_AGENT_ID)
        runtime.run_goal.assert_not_called()

    def test_run_team_route_direct_mode_uses_runtime(self):
        runtime = mock.Mock()
        runtime.run_goal.return_value = 'direct answer'

        with mock.patch.object(meridian_gateway, '_team_route_plan', return_value={'mode': 'direct', 'reason': 'greeting'}):
            answer, meta = meridian_gateway._run_team_route('hi', 'telegram:123', runtime)

        self.assertEqual(answer, 'direct answer')
        self.assertEqual(meta['mode'], 'direct')
        runtime.run_goal.assert_called_once_with('hi')


if __name__ == '__main__':
    unittest.main()
