#!/usr/bin/env python3
import importlib.util
import sys
import tempfile
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
    def test_skill_registry_reads_frontmatter_description(self):
        registry = meridian_gateway.SkillRegistry(meridian_gateway.SKILLS_DIR)
        items = registry.load()
        skill = next(item for item in items if item['name'] == 'mvp-sprint-scope')
        self.assertNotEqual(skill['description'], '---')
        self.assertIn('MVP', skill['description'])

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

    def test_short_prompt_skill_route_uses_existing_skill(self):
        plan = meridian_gateway._team_route_plan('mvp scope', 'telegram:123')
        self.assertEqual(plan['mode'], 'team')
        self.assertEqual(plan['reason'], 'skill_routed_request')
        self.assertIn('ATLAS', plan['workers'])
        self.assertIn('mvp-sprint-scope', [item['name'] for item in plan['skills']])

    def test_short_prompt_skill_route_adds_verified_facts_for_status_flows(self):
        plan = meridian_gateway._team_route_plan('ops snapshot', 'telegram:123')
        self.assertEqual(plan['reason'], 'skill_routed_request')
        self.assertIsInstance(plan.get('verified_facts'), dict)
        self.assertIn('runtime_id', plan['verified_facts'])

    def test_skill_registry_can_create_autonomous_skill(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            registry = meridian_gateway.SkillRegistry(Path(tmpdir))
            created = registry.create_autonomous_skill('founder update', session_key='telegram:proof', manager_brief='founder update')
            self.assertIsNotNone(created)
            self.assertTrue((Path(tmpdir) / 'founder-update' / 'SKILL.md').exists())
            self.assertEqual(created['name'], 'founder-update')

    def test_skill_registry_refines_autonomous_skill_with_new_variation(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            registry = meridian_gateway.SkillRegistry(Path(tmpdir))
            created = registry.create_autonomous_skill(
                'founder update',
                session_key='telegram:proof',
                manager_brief='founder update',
            )
            self.assertIsNotNone(created)
            refined = registry.create_autonomous_skill(
                'founder update brief for the team',
                session_key='telegram:proof',
                manager_brief='founder update brief for the team',
            )
            self.assertIsNotNone(refined)
            self.assertIn(refined.get('autonomy_status'), {'refined', 'reused', 'created'})
            content = (Path(tmpdir) / refined['name'] / 'SKILL.md').read_text(encoding='utf-8')
            self.assertIn('## Learned Variations', content)

    def test_skill_registry_reuses_autonomous_skill_for_exact_same_request(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            registry = meridian_gateway.SkillRegistry(Path(tmpdir))
            created = registry.create_autonomous_skill(
                'protocol hồi sinh deal nguội',
                session_key='telegram:proof',
                manager_brief='protocol hồi sinh deal nguội',
            )
            self.assertIsNotNone(created)
            reused = registry.create_autonomous_skill(
                'protocol hồi sinh deal nguội',
                session_key='telegram:proof',
                manager_brief='protocol hồi sinh deal nguội',
            )
            self.assertIsNotNone(reused)
            self.assertEqual(reused.get('name'), created.get('name'))
            self.assertEqual(reused.get('autonomy_status'), 'reused')

    def test_actionable_end_user_request_creates_skill_routed_team_plan(self):
        prompt = 'bạn có thể gửi mail cho tôi về trạng thái cập nhật mới nhất của Meridian thông qua mail của chính tôi là nguyensimon186@gmail.com.'
        with tempfile.TemporaryDirectory() as tmpdir:
            original_registry = meridian_gateway.TEAM_SKILLS
            registry = meridian_gateway.SkillRegistry(Path(tmpdir))
            registry.load()
            meridian_gateway.TEAM_SKILLS = registry
            try:
                plan = meridian_gateway._team_route_plan(prompt, 'telegram:5322393870')
            finally:
                meridian_gateway.TEAM_SKILLS = original_registry
            self.assertEqual(plan['mode'], 'team')
            self.assertEqual(plan['reason'], 'skill_routed_request')
            self.assertTrue(plan['skills'])
            self.assertTrue(any(('email' in item['name'] or 'mail' in item['name']) for item in plan['skills']))
            self.assertIn('QUILL', plan['workers'])
            self.assertIn('prioritize the user-facing artifact', plan['manager_brief'])

    def test_follow_up_after_demo_materializes_new_skill_instead_of_council_match(self):
        prompt = 'soạn follow up cho khách sau demo hôm qua'
        self.assertTrue(meridian_gateway._autonomy_skill_candidate(prompt))
        with tempfile.TemporaryDirectory() as tmpdir:
            original_registry = meridian_gateway.TEAM_SKILLS
            registry = meridian_gateway.SkillRegistry(Path(tmpdir))
            registry.load()
            meridian_gateway.TEAM_SKILLS = registry
            try:
                bundle = meridian_gateway._skill_bundle_for_request(
                    prompt,
                    'web_api:test-follow-up',
                    manager_brief=prompt,
                    allow_create=True,
                )
            finally:
                meridian_gateway.TEAM_SKILLS = original_registry
            self.assertIsNotNone(bundle['created_skill'])
            created_name = str(bundle['created_skill']['name'])
            self.assertNotEqual(created_name, 'council-meeting')
            self.assertTrue(created_name.startswith('follow-') or 'follow' in created_name)
            self.assertTrue(any(item['name'] == created_name for item in bundle['matches']))

    def test_protocol_request_materializes_new_skill_instead_of_reusing_council_skill(self):
        prompt = (
            'hãy tạo cho tôi một protocol hồi sinh deal nguội trong 9 phút: gồm 2 giả thuyết đảo ngược, '
            '4 câu hỏi loại bỏ ngụy biện, 1 tin nhắn kéo khách quay lại bàn đàm phán, và 1 tiêu chí dừng rõ ràng.'
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            council_dir = root / 'council-meeting'
            council_dir.mkdir(parents=True, exist_ok=True)
            (council_dir / 'SKILL.md').write_text(
                """---
name: council-meeting
description: "Use when Meridian needs a board-style council discussion about customer readiness, open-source intent, strategic clarity, or whether the current product is truly buyable."
category: "strategy"
---

# Council Meeting

Use when the user asks for:
- council meeting
- board review
- why would a customer buy this
""",
                encoding='utf-8',
            )
            original_registry = meridian_gateway.TEAM_SKILLS
            registry = meridian_gateway.SkillRegistry(root)
            registry.load()
            meridian_gateway.TEAM_SKILLS = registry
            try:
                bundle = meridian_gateway._skill_bundle_for_request(
                    prompt,
                    'telegram:5322393870',
                    manager_brief=prompt,
                    allow_create=True,
                )
            finally:
                meridian_gateway.TEAM_SKILLS = original_registry
            self.assertIsNotNone(bundle['created_skill'])
            self.assertNotEqual(bundle['created_skill']['name'], 'council-meeting')
            self.assertIn('protocol', bundle['created_skill']['name'])
            self.assertIn('QUILL', bundle['workers'])
            self.assertIn('FORGE', bundle['workers'])
            self.assertTrue(any(item['name'] == bundle['created_skill']['name'] for item in bundle['matches']))

    def test_protocol_request_reuses_existing_autonomous_protocol_skill(self):
        prompt = (
            'hãy tạo cho tôi một protocol hồi sinh deal nguội trong 9 phút: gồm 2 giả thuyết đảo ngược, '
            '4 câu hỏi loại bỏ ngụy biện, 1 tin nhắn kéo khách quay lại bàn đàm phán, và 1 tiêu chí dừng rõ ràng.'
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            original_registry = meridian_gateway.TEAM_SKILLS
            registry = meridian_gateway.SkillRegistry(Path(tmpdir))
            registry.load()
            meridian_gateway.TEAM_SKILLS = registry
            try:
                first = meridian_gateway._skill_bundle_for_request(
                    prompt,
                    'telegram:5322393870',
                    manager_brief=prompt,
                    allow_create=True,
                )
                second = meridian_gateway._skill_bundle_for_request(
                    prompt,
                    'telegram:5322393870',
                    manager_brief=prompt,
                    allow_create=True,
                )
            finally:
                meridian_gateway.TEAM_SKILLS = original_registry
            self.assertIsNotNone(first['created_skill'])
            self.assertIsNone(second['created_skill'])
            self.assertIsNone(second['refined_skill'])
            self.assertTrue(second['matches'])
            self.assertEqual(second['matches'][0]['name'], first['created_skill']['name'])

    def test_research_customer_prompt_creates_specific_skill_instead_of_refining_follow_up_skill(self):
        prompt = 'research khách hàng cho sản phẩm competitor intelligence'
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            follow_dir = root / 'follow-demo-soan'
            follow_dir.mkdir(parents=True, exist_ok=True)
            (follow_dir / 'SKILL.md').write_text(
                """---
name: follow-demo-soan
description: "Use when a request like 'soạn follow up cho khách sau demo hôm qua' needs a reusable Meridian workflow instead of an ad hoc reply."
metadata:
  created_by: meridian_skill_autonomy
  session_key: "web_api:test"
  category: "communication"
---

# Follow Demo Soan

Use this skill when the user gives a short prompt such as:
- soạn follow up cho khách sau demo hôm qua
""",
                encoding='utf-8',
            )
            original_registry = meridian_gateway.TEAM_SKILLS
            registry = meridian_gateway.SkillRegistry(root)
            registry.load()
            meridian_gateway.TEAM_SKILLS = registry
            try:
                bundle = meridian_gateway._skill_bundle_for_request(
                    prompt,
                    'web_api:test-research-customer',
                    manager_brief=prompt,
                    allow_create=True,
                )
            finally:
                meridian_gateway.TEAM_SKILLS = original_registry
            self.assertIsNotNone(bundle['created_skill'])
            self.assertNotEqual(bundle['created_skill']['name'], 'follow-demo-soan')
            self.assertIn('research', bundle['created_skill']['name'])

    def test_atlas_placeholder_citations_are_sanitized_to_customer_research_starter(self):
        plan = {
            'manager_brief': 'Research customer demand for competitor intelligence.',
            'topic': 'research khách hàng cho sản phẩm competitor intelligence',
            'criteria': 'factual',
            'skills': [
                {
                    'name': 'research-khach-hang',
                    'description': 'Customer research starter pack',
                    'workers': ['ATLAS', 'AEGIS'],
                    'category': 'research',
                }
            ],
        }
        result = {
            'research': "[{'finding':'Fake claim','citations':[{'url':'https://example.com/fake'}]}]",
            'job_id': 'job-atlas',
            'error': '',
        }
        with mock.patch.object(meridian_gateway, 'append_session_event'):
            with mock.patch.object(meridian_gateway.mcp_server, 'do_on_demand_research_route', return_value=result):
                receipt = meridian_gateway._run_specialist_step(
                    'ATLAS',
                    'research khách hàng cho sản phẩm competitor intelligence',
                    'web_api:test-research-customer',
                    plan,
                )
        self.assertEqual(receipt['status'], 'ok')
        self.assertIn('giả thuyết cần kiểm chứng', receipt['result'])
        self.assertIn('placeholder_citations_detected_in_research_output', receipt['warnings'])
        self.assertIn('customer_research_starter_salvaged_after_unverified_research', receipt['warnings'])

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

    def test_specialist_receipt_surfaces_skills_used(self):
        plan = {
            'manager_brief': 'Create a host snapshot.',
            'topic': 'ops snapshot',
            'criteria': 'consistency',
            'skills': [
                {
                    'name': 'ops-snapshot',
                    'description': 'Use when Leviathann needs a fast local health snapshot.',
                    'body_excerpt': '1. Check local health.\n2. Summarize actionable issues.',
                    'workers': ['FORGE', 'PULSE'],
                }
            ],
        }
        loom_result = {
            'ok': True,
            'job_id': 'job-forge',
            'worker_result': {
                'host_response_json': {
                    'output_text': '```json\n{"result":"host snapshot ready","confidence":"high","citations":[],"warnings":[]}\n```'
                }
            },
        }
        with mock.patch.object(meridian_gateway, 'append_session_event'):
            with mock.patch.object(meridian_gateway.mcp_server, '_shared_run_loom_capability', return_value=loom_result):
                receipt = meridian_gateway._run_specialist_step('FORGE', 'ops snapshot', 'telegram:5322393870', plan)
        self.assertEqual(receipt['skills_used'], ['ops-snapshot'])

    def test_quality_marks_recoverable_action_flow_as_partial(self):
        steps = [
            {
                'agent_id': 'agent_quill',
                'task_kind': 'write',
                'status': 'ok',
                'result': "{'type':'meeting-plan','status':'draft','time':'sáng mai'}",
                'warnings': ['Meeting details are minimal and may need further clarification from the host.'],
            },
            {
                'agent_id': 'agent_forge',
                'task_kind': 'execute',
                'status': 'ok',
                'result': 'Unable to book meeting due to lack of information. 1. Confirm exact time. 2. Check availability.',
                'warnings': ['Loom job timed out (120s limit)'],
            },
            {
                'agent_id': 'agent_aegis',
                'task_kind': 'qa_gate',
                'status': 'ok',
                'result': 'FAIL',
                'warnings': ['Request lacks concrete details (time, participants, purpose)'],
            },
        ]
        status, reasons = meridian_gateway._assess_skill_quality_outcome(steps)
        self.assertEqual(status, 'partial')
        self.assertIn('QA gate returned FAIL.', reasons)

    def test_quality_marks_unusable_timed_out_flow_as_failure(self):
        steps = [
            {
                'agent_id': 'agent_atlas',
                'task_kind': 'research',
                'status': 'error',
                'result': '',
                'warnings': ['Loom job timed out (150s limit)'],
            },
            {
                'agent_id': 'agent_aegis',
                'task_kind': 'qa_gate',
                'status': 'error',
                'result': 'Loom job timed out (90s limit)',
                'warnings': ['Loom job timed out (90s limit)'],
            },
        ]
        status, reasons = meridian_gateway._assess_skill_quality_outcome(steps)
        self.assertEqual(status, 'failure')
        self.assertTrue(any('agent_atlas status=error' in reason for reason in reasons))

    def test_short_skill_routed_requests_do_not_pull_noisy_history_into_specialists(self):
        plan = {'reason': 'skill_routed_request'}
        with mock.patch.object(meridian_gateway, 'imported_history_context', return_value='noisy prior context'):
            context = meridian_gateway._specialist_history_context(
                'gửi mail cho tôi nội dung chào khách',
                'telegram:proof',
                plan,
            )
        self.assertEqual(context, '')

    def test_mail_skill_addendum_forbids_product_scope_output(self):
        addendum = meridian_gateway._skill_specific_execution_addendum(
            'gửi mail cho tôi nội dung chào khách',
            [{'name': 'mail-gui'}],
        )
        self.assertIn('send-ready email or message draft', addendum)
        self.assertIn('Do not return product goals, scope, acceptance criteria', addendum)

    def test_communication_profile_prefers_quill_and_aegis_only(self):
        self.assertEqual(meridian_gateway.AUTONOMY_WORKER_PROFILES['communication'], ['QUILL', 'AEGIS'])

    def test_salvage_user_artifact_rewrites_mail_scope_drift(self):
        salvaged = meridian_gateway._salvage_user_artifact(
            'gửi mail cho tôi nội dung chào khách và hỏi lịch hẹn ngày mai',
            ['mail-gui'],
        )
        self.assertIn('Tiêu đề', salvaged)
        self.assertIn('[Tên khách]', salvaged)

    def test_meeting_output_with_internal_attendees_needs_salvage(self):
        raw = "{'subject': 'Meeting Invitation', 'to': 'FORGE, AEGIS', 'attendees': ['Atlas', 'Quill', 'Forge']}"
        self.assertTrue(meridian_gateway._meeting_output_needs_salvage(raw))

    def test_web_request_session_prefers_explicit_session_id(self):
        resolved = meridian_gateway._resolve_web_request_session(
            {'goal': 'book meeting', 'session_id': 'Team Demo 01'},
            {},
            'book meeting',
        )
        self.assertEqual(resolved['session_id'], 'team-demo-01')
        self.assertEqual(resolved['session_key'], 'web_api:team-demo-01')
        self.assertFalse(resolved['generated'])

    def test_web_request_session_generates_isolated_id_when_missing(self):
        resolved_a = meridian_gateway._resolve_web_request_session({'goal': 'book meeting'}, {}, 'book meeting')
        resolved_b = meridian_gateway._resolve_web_request_session({'goal': 'founder update'}, {}, 'founder update')
        self.assertTrue(str(resolved_a['session_id']).startswith('ws-'))
        self.assertTrue(str(resolved_b['session_id']).startswith('ws-'))
        self.assertNotEqual(resolved_a['session_id'], resolved_b['session_id'])
        self.assertNotEqual(resolved_a['session_key'], resolved_b['session_key'])
        self.assertTrue(resolved_a['generated'])
        self.assertTrue(resolved_b['generated'])

    def test_effective_web_session_key_ignores_legacy_shared_ingress_key(self):
        session_key = meridian_gateway._effective_web_session_key(
            'ws-demo1234',
            {'session_key': f'web_api:{meridian_gateway.LOOM_ORG_ID}'},
        )
        self.assertEqual(session_key, 'web_api:ws-demo1234')

    def test_effective_web_session_key_keeps_specific_ingress_key(self):
        session_key = meridian_gateway._effective_web_session_key(
            'ws-demo1234',
            {'session_key': 'web_api:thread-abc'},
        )
        self.assertEqual(session_key, 'web_api:thread-abc')

    def test_book_meeting_without_execution_details_downshifts_to_quill_and_aegis(self):
        workers = meridian_gateway._refine_skill_routed_workers(
            'book meeting với khách hàng tiềm năng vào sáng mai',
            [{'name': 'book-meeting'}],
            ['QUILL', 'FORGE', 'AEGIS'],
        )
        self.assertEqual(workers, ['QUILL', 'AEGIS'])

    def test_book_meeting_with_execution_details_keeps_forge(self):
        workers = meridian_gateway._refine_skill_routed_workers(
            'book meeting với demo@acme.com lúc 09:30 trên Zoom',
            [{'name': 'book-meeting'}],
            ['QUILL', 'FORGE', 'AEGIS'],
        )
        self.assertEqual(workers, ['QUILL', 'FORGE', 'AEGIS'])

    def test_communication_skills_use_fast_specialist_timeouts(self):
        self.assertEqual(
            meridian_gateway._specialist_timeout_for_request('AEGIS', 'gửi mail cho khách', ['mail-gui']),
            25,
        )
        self.assertEqual(
            meridian_gateway._specialist_timeout_for_request('QUILL', 'book meeting', ['book-meeting']),
            30,
        )

    def test_communication_skills_prefer_direct_provider_first(self):
        self.assertTrue(meridian_gateway._prefer_direct_provider_first('QUILL', ['mail-gui']))
        self.assertTrue(meridian_gateway._prefer_direct_provider_first('AEGIS', ['book-meeting']))
        self.assertTrue(meridian_gateway._prefer_direct_provider_first('QUILL', ['follow-demo-soan']))
        self.assertFalse(meridian_gateway._prefer_direct_provider_first('FORGE', ['book-meeting']))
        self.assertTrue(meridian_gateway._prefer_direct_provider_first('QUILL', ['safe-web-research']))

    def test_safe_web_requests_are_detected_from_public_url(self):
        self.assertTrue(
            meridian_gateway._request_prefers_safe_web_research(
                'đọc source này giúp tôi https://openai.com/index/introducing-gpt-5/'
            )
        )

    def test_skill_bundle_prefers_safe_web_research_for_url_prompt(self):
        with mock.patch.object(
            meridian_gateway.TEAM_SKILLS,
            'search',
            return_value=[
                {'name': 'ai-intelligence', 'description': 'generic research', 'score': 13},
                {'name': 'safe-web-research', 'description': 'safe url fetch', 'score': 12},
            ],
        ):
            bundle = meridian_gateway._skill_bundle_for_request(
                'check link này giúp tôi https://example.com',
                'web_api:test-safe-web',
                manager_brief='check link này giúp tôi https://example.com',
                allow_create=True,
            )
        self.assertEqual([item['name'] for item in bundle['matches']], ['safe-web-research'])

    def test_salvaged_competitor_scan_names_follow_up_targets_and_narrower_query(self):
        artifact = meridian_gateway._salvage_competitor_scan_artifact('scan đối thủ openai tuần này')
        self.assertIn('Official-source', artifact)
        self.assertIn('Narrower next query', artifact)
        self.assertIn('OPENAI', artifact)

    def test_competitor_scan_artifact_needs_salvage_when_follow_up_targets_missing(self):
        artifact = """**Status**\nNo verified findings.\n\n**Verified findings**\nNone.\n\n**Unknowns**\nStill unknown.\n\n**Next move**\nTry again later."""
        self.assertTrue(meridian_gateway._competitor_scan_artifact_needs_salvage(artifact))
        self.assertFalse(
            meridian_gateway._competitor_scan_artifact_needs_salvage(
                meridian_gateway._salvage_competitor_scan_artifact('scan đối thủ openai tuần này')
            )
        )

    def test_scan_doi_thu_quality_uses_final_artifact_when_worker_qa_is_recoverable(self):
        steps = [
            {
                'agent_id': 'agent_atlas',
                'task_kind': 'research',
                'status': 'ok',
                'result': "{'Status':'Search completed','Verified findings':[],'Unknowns':'unverified competitor moves'}",
                'warnings': [],
            },
            {
                'agent_id': 'agent_aegis',
                'task_kind': 'qa_gate',
                'status': 'ok',
                'result': 'FAIL',
                'warnings': [
                    'Missing required follow-up targets in unknowns section',
                    'No narrower next query specified for bounded scan',
                    'Verified findings remain empty without explicit source limitations documented',
                ],
            },
        ]
        artifact = meridian_gateway._salvage_competitor_scan_artifact('scan đối thủ openai tuần này')
        status, reasons = meridian_gateway._assess_skill_quality_outcome(
            steps,
            ['scan-doi-thu'],
            final_artifact=artifact,
        )
        self.assertEqual(status, 'success')
        self.assertEqual(reasons, [])

    def test_ops_snapshot_warnings_are_informational_for_quality(self):
        steps = [
            {
                'agent_id': 'agent_forge',
                'task_kind': 'execute',
                'status': 'ok',
                'result': 'Operational Meridian snapshot: runtime `loom_native` for `org_48b05c21` is up.',
                'warnings': [
                    'payout_execution_gate: Phase 0 (Founder-Backed Build) does not allow contributor payouts yet',
                    'disk pressure and scheduled-job status were not independently verified in this snapshot',
                ],
            },
            {
                'agent_id': 'agent_pulse',
                'task_kind': 'compress',
                'status': 'ok',
                'result': 'Compressed Meridian snapshot: `loom_native` on `org_48b05c21`, preflight `CLEAR`.',
                'warnings': [],
            },
        ]
        status, reasons = meridian_gateway._assess_skill_quality_outcome(steps, ['ops-snapshot'])
        self.assertEqual(status, 'success')
        self.assertEqual(reasons, [])

    def test_scope_document_is_not_treated_as_usable_artifact(self):
        step = {
            'agent_id': 'agent_quill',
            'task_kind': 'write',
            'status': 'ok',
            'result': '**Product Goal:** Make a new feature page. **Acceptance Criteria:** ...',
            'warnings': [],
        }
        self.assertFalse(meridian_gateway._step_has_usable_artifact(step))

    def test_qa_fail_with_only_informational_warnings_is_recoverable(self):
        step = {
            'agent_id': 'agent_aegis',
            'task_kind': 'qa_gate',
            'status': 'ok',
            'result': 'FAIL',
            'warnings': ['bounded llm host call completed against https://example.com via provider profile aegis_specialist (openai_compatible)'],
        }
        self.assertTrue(meridian_gateway._qa_fail_is_recoverable(step))

    def test_follow_up_skill_addendum_forbids_scope_output(self):
        addendum = meridian_gateway._skill_specific_execution_addendum(
            'soạn follow up cho khách sau demo hôm qua',
            [{'name': 'follow-demo-soan'}],
        )
        self.assertIn('customer follow-up message or email', addendum)
        self.assertIn('Do not return product goals, feature scope', addendum)

    def test_salvage_user_artifact_rewrites_follow_up_scope_drift(self):
        salvaged = meridian_gateway._salvage_user_artifact(
            'soạn follow up cho khách sau demo hôm qua',
            ['follow-demo-soan'],
        )
        self.assertIn('Cảm ơn anh/chị đã dành thời gian tham gia buổi demo hôm qua', salvaged)

    def test_protocol_request_keeps_manager_protocol_answer_over_mail_follow_worker_artifact(self):
        request = (
            'hãy tạo cho tôi một protocol cứu deal chết trong 7 phút: gồm 3 giả thuyết, '
            '5 câu hỏi bóc tách, 1 tin nhắn follow-up gửi khách, và 1 tiêu chí dừng rõ ràng.'
        )
        manager_answer = (
            '**Protocol cứu deal chết trong 7 phút**\n\n'
            '**3 giả thuyết**\n'
            '1. Deal kẹt ở ưu tiên nội bộ.\n'
            '2. Deal kẹt ở rủi ro quyết định.\n'
            '3. Deal kẹt ở timing hoặc ngân sách.\n\n'
            '**5 câu hỏi bóc tách**\n'
            '1. Điều gì đang chặn quyết định?\n'
            '2. Ưu tiên nào đang đứng trước deal này?\n'
            '3. Ai còn chưa đồng ý?\n'
            '4. Rủi ro lớn nhất là gì?\n'
            '5. Điều gì cần đổi để deal chạy lại?\n\n'
            '**1 tin nhắn follow-up gửi khách**\n'
            'Anh/chị cho em hỏi đâu là điểm lớn nhất đang chặn quyết định để bên em xử lý ngay.\n\n'
            '**1 tiêu chí dừng rõ ràng**\n'
            'Nếu không có người chịu trách nhiệm và không có mốc thời gian rõ trong 7 ngày thì đóng deal.'
        )
        steps = [
            {
                'status': 'ok',
                'task_kind': 'write',
                'result': '**Tiêu đề:** Chào anh/chị\\n\\n**Nội dung:** Xin lịch hẹn ngày mai.',
                'warnings': [],
            }
        ]
        repaired, warnings = meridian_gateway._repair_manager_answer(
            request,
            manager_answer,
            steps,
            ['follow-demo-soan', 'mail-gui'],
        )
        self.assertEqual(repaired, manager_answer)
        self.assertEqual(warnings, [])

    def test_protocol_request_salvage_prefers_protocol_template_over_mail_template(self):
        request = (
            'hãy tạo cho tôi một protocol cứu deal chết trong 7 phút: gồm 3 giả thuyết, '
            '5 câu hỏi bóc tách, 1 tin nhắn follow-up gửi khách, và 1 tiêu chí dừng rõ ràng.'
        )
        salvaged = meridian_gateway._salvage_user_artifact(request, ['follow-demo-soan', 'mail-gui'])
        self.assertIn('giả thuyết', salvaged.lower())
        self.assertIn('tiêu chí dừng', salvaged.lower())
        self.assertNotIn('**tiêu đề:**', salvaged.lower())

    def test_protocol_request_repairs_from_worker_payload_dict(self):
        request = (
            'hãy tạo cho tôi một protocol kéo deal im lặng quay lại trong 11 phút: gồm 3 giả thuyết, '
            '4 câu hỏi phá ngụy biện, 1 tin nhắn follow-up kéo khách trả lời, và 1 tiêu chí dừng rõ ràng.'
        )
        steps = [
            {
                'status': 'ok',
                'task_kind': 'write',
                'result': "{'protocol': {'hypotheses': ['H1', 'H2'], 'debiasing_questions': ['Q1', 'Q2'], 'follow_up_message': 'Ping khách ngay.', 'stop_rule': 'Dừng nếu không có owner.'}}",
                'warnings': [],
            }
        ]
        repaired, warnings = meridian_gateway._repair_manager_answer(
            request,
            'LLM endpoint returned HTTP 400:',
            steps,
            ['protocol-deal-hoi'],
        )
        self.assertIn('giả thuyết', repaired.lower())
        self.assertIn('câu hỏi', repaired.lower())
        self.assertIn('tiêu chí dừng', repaired.lower())
        self.assertIn('manager_response_repaired_from_worker_artifact', warnings)


if __name__ == '__main__':
    unittest.main()
