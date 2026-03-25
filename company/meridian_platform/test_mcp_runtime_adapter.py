#!/usr/bin/env python3
import importlib.util
import json
import os
import sys
import unittest
from unittest import mock

WORKSPACE = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..'))
COMPANY_DIR = os.path.join(WORKSPACE, 'company')

for path in (COMPANY_DIR, os.path.join(COMPANY_DIR, 'meridian_platform')):
    if path not in sys.path:
        sys.path.insert(0, path)


def _load_module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


mcp_server = _load_module(os.path.join(COMPANY_DIR, 'mcp_server.py'), 'company_mcp_runtime_adapter_test')


class McpRuntimeAdapterTests(unittest.TestCase):
    def test_research_defaults_to_openclaw(self):
        stdout = json.dumps({'response': 'openclaw research result'})
        completed = mock.Mock(returncode=0, stdout=stdout, stderr='')
        with mock.patch.dict(os.environ, {}, clear=False):
            with mock.patch.object(mcp_server.subprocess, 'run', return_value=completed) as run_mock:
                result = mcp_server.do_on_demand_research('OpenAI pricing', 'quick')

        self.assertEqual(result['runtime'], 'openclaw')
        self.assertEqual(result['research'], 'openclaw research result')
        self.assertEqual(result['agent'], 'Atlas')
        command = run_mock.call_args.args[0]
        self.assertEqual(command[:4], ['openclaw', 'agent', '--agent', 'atlas'])

    def test_research_uses_loom_when_enabled(self):
        submit = mock.Mock(returncode=0, stdout=json.dumps({'job_id': 'job-123'}), stderr='')
        inspect = mock.Mock(returncode=0, stdout=json.dumps({'job_status': 'completed'}), stderr='')
        worker_result = {
            'summary': 'fallback summary',
            'skill_output': {'research': 'loom research result'},
        }
        env = {
            'MERIDIAN_INTELLIGENCE_RESEARCH_RUNTIME': 'loom',
            'MERIDIAN_LOOM_RESEARCH_CAPABILITY': 'company.research.v0',
            'MERIDIAN_LOOM_SERVICE_TOKEN': 'loom-local-token',
        }
        with mock.patch.dict(os.environ, env, clear=False):
            with mock.patch.object(mcp_server.subprocess, 'run', side_effect=[submit, inspect]) as run_mock:
                with mock.patch.object(mcp_server, '_load_json_file', return_value=worker_result):
                    with mock.patch.object(mcp_server.time, 'sleep', return_value=None):
                        result = mcp_server.do_on_demand_research('Anthropic context window', 'deep')

        self.assertEqual(result['runtime'], 'loom')
        self.assertEqual(result['capability_name'], 'company.research.v0')
        self.assertEqual(result['job_id'], 'job-123')
        self.assertEqual(result['research'], 'loom research result')
        submit_cmd = run_mock.call_args_list[0].args[0]
        inspect_cmd = run_mock.call_args_list[1].args[0]
        self.assertEqual(submit_cmd[1:3], ['service', 'submit'])
        self.assertEqual(inspect_cmd[1:3], ['job', 'inspect'])

    def test_research_loom_url_payload_extracts_normalized_text(self):
        submit = mock.Mock(returncode=0, stdout=json.dumps({'job_id': 'job-url'}), stderr='')
        inspect = mock.Mock(returncode=0, stdout=json.dumps({'job_status': 'completed'}), stderr='')
        worker_result = {
            'summary': 'safe-web-research',
            'skill_output': {
                'results': [
                    {'url': 'https://example.com/', 'normalized_text': 'example domain text'}
                ]
            },
        }
        env = {
            'MERIDIAN_INTELLIGENCE_RESEARCH_RUNTIME': 'loom',
            'MERIDIAN_LOOM_RESEARCH_CAPABILITY': 'clawskill.safe-web-research.v0',
            'MERIDIAN_LOOM_SERVICE_TOKEN': 'loom-local-token',
        }
        with mock.patch.dict(os.environ, env, clear=False):
            with mock.patch.object(mcp_server.subprocess, 'run', side_effect=[submit, inspect]) as run_mock:
                with mock.patch.object(mcp_server, '_load_json_file', return_value=worker_result):
                    with mock.patch.object(mcp_server.time, 'sleep', return_value=None):
                        result = mcp_server.do_on_demand_research('https://example.com', 'quick')

        self.assertEqual(result['runtime'], 'loom')
        self.assertEqual(result['research'], 'example domain text')
        submit_cmd = run_mock.call_args_list[0].args[0]
        payload = json.loads(submit_cmd[submit_cmd.index('--payload-json') + 1])
        self.assertEqual(payload['url'], 'https://example.com')
        self.assertEqual(payload['urls'], ['https://example.com'])


    def test_on_demand_research_route_override_openclaw_beats_global_loom(self):
        stdout = json.dumps({'response': 'openclaw route result'})
        completed = mock.Mock(returncode=0, stdout=stdout, stderr='')
        env = {
            'MERIDIAN_INTELLIGENCE_RESEARCH_RUNTIME': 'loom',
            'MERIDIAN_INTELLIGENCE_ON_DEMAND_RESEARCH_RUNTIME': 'openclaw',
        }
        with mock.patch.dict(os.environ, env, clear=False):
            with mock.patch.object(mcp_server.subprocess, 'run', return_value=completed):
                result = mcp_server.do_on_demand_research_route('OpenAI pricing', 'quick')

        self.assertEqual(result['runtime'], 'openclaw')
        self.assertEqual(result['route_cutover']['requested_runtime'], 'openclaw')
        self.assertEqual(result['route_cutover']['selected_runtime'], 'openclaw')
        self.assertFalse(result['route_cutover']['fallback_enabled'])

    def test_on_demand_research_route_uses_loom_with_cutover_metadata(self):
        service_status = mock.Mock(
            returncode=0,
            stdout=json.dumps({
                'running': True,
                'service_status': 'running',
                'health': 'healthy',
                'transport': 'socket+http',
                'service_target': '127.0.0.1:18910',
            }),
            stderr='',
        )
        capability_show = mock.Mock(
            returncode=0,
            stdout=json.dumps({
                'enabled': True,
                'verification_status': 'verified',
                'promotion_state': 'promoted',
                'worker_kind': 'python',
                'worker_entry': 'workers/python/imported-clawskill-safe-web-research-v0.py',
                'payload_mode': 'json',
                'adapter_kind': 'url_report_v0',
                'runtime_lane': 'python_host_process/imported_workspace_skill',
                'env_contract': 'host python3',
            }),
            stderr='',
        )
        submit = mock.Mock(
            returncode=0,
            stdout=json.dumps({
                'job_id': 'job-route',
                'transport': 'socket+http',
                'service_target': '127.0.0.1:18910',
                'queue_path': '/tmp/queue.json',
                'ingress_request_path': '/tmp/request.json',
                'ingress_receipt_path': '/tmp/receipt.json',
                'policy_class': 'standard',
            }),
            stderr='',
        )
        inspect = mock.Mock(
            returncode=0,
            stdout=json.dumps({
                'job_status': 'completed',
                'job_stage': 'ok',
                'runtime_outcome': 'worker_executed',
                'worker_status': 'completed',
                'job_path': '/tmp/jobs/job-route/job.json',
                'event_path': '/tmp/jobs/job-route/event.json',
                'audit_log_path': '/tmp/jobs/job-route/audit.jsonl',
                'parity_report_path': '/tmp/jobs/job-route/parity.json',
            }),
            stderr='',
        )
        worker_result = {
            'summary': 'safe-web-research',
            'skill_output': {'results': [{'normalized_text': 'loom route result'}]},
        }
        env = {
            'MERIDIAN_INTELLIGENCE_ON_DEMAND_RESEARCH_RUNTIME': 'loom',
            'MERIDIAN_LOOM_RESEARCH_CAPABILITY': 'clawskill.safe-web-research.v0',
            'MERIDIAN_LOOM_SERVICE_TOKEN': 'loom-local-token',
        }
        with mock.patch.dict(os.environ, env, clear=False):
            with mock.patch.object(mcp_server.subprocess, 'run', side_effect=[service_status, capability_show, submit, inspect]):
                with mock.patch.object(mcp_server, '_load_json_file', return_value=worker_result):
                    with mock.patch.object(mcp_server.time, 'sleep', return_value=None):
                        result = mcp_server.do_on_demand_research_route('https://example.com', 'quick')

        self.assertEqual(result['runtime'], 'loom')
        self.assertEqual(result['research'], 'loom route result')
        self.assertEqual(result['route_cutover']['requested_runtime'], 'loom')
        self.assertEqual(result['route_cutover']['selected_runtime'], 'loom')
        self.assertTrue(result['route_cutover']['loom_preflight']['ok'])
        self.assertEqual(result['route_cutover']['loom']['job_id'], 'job-route')
        self.assertEqual(result['route_cutover']['loom']['result_path_hint'], '/tmp/jobs/job-route/result.json')

    def test_on_demand_research_route_loom_preflight_failure_falls_back_when_enabled(self):
        service_status = mock.Mock(
            returncode=0,
            stdout=json.dumps({
                'running': True,
                'service_status': 'running',
                'health': 'healthy',
                'transport': 'socket+http',
            }),
            stderr='',
        )
        capability_show = mock.Mock(returncode=1, stdout='', stderr='missing capability')
        openclaw = mock.Mock(returncode=0, stdout=json.dumps({'response': 'fallback openclaw result'}), stderr='')
        env = {
            'MERIDIAN_INTELLIGENCE_ON_DEMAND_RESEARCH_RUNTIME': 'loom',
            'MERIDIAN_INTELLIGENCE_ON_DEMAND_RESEARCH_ALLOW_FALLBACK': '1',
            'MERIDIAN_LOOM_RESEARCH_CAPABILITY': 'missing.capability.v0',
        }
        with mock.patch.dict(os.environ, env, clear=False):
            with mock.patch.object(mcp_server.subprocess, 'run', side_effect=[service_status, capability_show, openclaw]):
                result = mcp_server.do_on_demand_research_route('OpenAI pricing', 'quick')

        self.assertEqual(result['runtime'], 'openclaw')
        self.assertEqual(result['research'], 'fallback openclaw result')
        self.assertEqual(result['route_cutover']['requested_runtime'], 'loom')
        self.assertEqual(result['route_cutover']['selected_runtime'], 'openclaw')
        self.assertTrue(result['route_cutover']['fallback_enabled'])
        self.assertTrue(result['route_cutover']['fallback']['used'])
        self.assertEqual(result['route_cutover']['fallback']['state'], 'preflight_failed')
        self.assertIn('missing capability', result['route_cutover']['fallback']['reason'])

    def test_qa_defaults_to_openclaw_when_only_research_runtime_is_loom(self):
        stdout = json.dumps({'response': 'openclaw qa result'})
        completed = mock.Mock(returncode=0, stdout=stdout, stderr='')
        env = {'MERIDIAN_INTELLIGENCE_RESEARCH_RUNTIME': 'loom'}
        with mock.patch.dict(os.environ, env, clear=False):
            with mock.patch.object(mcp_server.subprocess, 'run', return_value=completed) as run_mock:
                result = mcp_server.do_qa_verify('text to verify', 'factual')

        self.assertEqual(result['runtime'], 'openclaw')
        self.assertEqual(result['verification'], 'openclaw qa result')
        command = run_mock.call_args.args[0]
        self.assertEqual(command[:4], ['openclaw', 'agent', '--agent', 'aegis'])

    def test_qa_uses_loom_when_enabled(self):
        submit = mock.Mock(returncode=0, stdout=json.dumps({'job_id': 'job-qa'}), stderr='')
        inspect = mock.Mock(returncode=0, stdout=json.dumps({'job_status': 'completed'}), stderr='')
        worker_result = {
            'summary': 'PASS',
            'skill_output': {'verification': 'PASS with confidence 92'},
        }
        env = {
            'MERIDIAN_INTELLIGENCE_QA_RUNTIME': 'loom',
            'MERIDIAN_LOOM_QA_CAPABILITY': 'company.qa.v0',
            'MERIDIAN_LOOM_SERVICE_TOKEN': 'loom-local-token',
        }
        with mock.patch.dict(os.environ, env, clear=False):
            with mock.patch.object(mcp_server.subprocess, 'run', side_effect=[submit, inspect]):
                with mock.patch.object(mcp_server, '_load_json_file', return_value=worker_result):
                    with mock.patch.object(mcp_server.time, 'sleep', return_value=None):
                        result = mcp_server.do_qa_verify('text to verify', 'factual')

        self.assertEqual(result['runtime'], 'loom')
        self.assertEqual(result['capability_name'], 'company.qa.v0')
        self.assertEqual(result['job_id'], 'job-qa')
        self.assertEqual(result['verification'], 'PASS with confidence 92')


if __name__ == '__main__':
    unittest.main()
