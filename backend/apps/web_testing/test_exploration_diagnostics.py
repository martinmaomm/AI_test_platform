"""Offline protocol, failure classification and authenticated image regressions."""

import base64
import json
import tempfile
from pathlib import Path
from unittest.mock import Mock
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase, override_settings
from langchain_core.messages import ToolMessage
from rest_framework.test import APIRequestFactory, force_authenticate

from ai_core.mcp_exploration_runtime import project_messages
from projects.models import Project, ProjectMember
from .exploration_diagnostics import (
    MAX_OBSERVATION_JSON_CHARS, extract_page_context, failure_context,
    render_observation, render_observation_unavailable,
)
from .exploration_trace import ExplorationTraceRecorder, _tool_failed
from .models import WebUIScriptGeneration
from .script_exploration_agent import ScriptExplorationAgent
from .views import WebUIScriptGenerationFailureScreenshotView


def trailer(**overrides):
    data = {
        'page_url': 'https://fixture.example.test/#/catalog', 'page_title': 'Catalog',
        'breadcrumbs': ['Work', 'Catalog'], 'element_label': 'Display name',
        'container_label': 'Create entry', 'selector': 'input[name="f17"]',
        'visible': False, 'enabled': True, 'matched_count': 1,
        'tool_failed': True, 'captured_at': '2026-09-11T04:00:00.000Z',
        'reason_code': 'element_hidden', 'message': '目标输入框不可见，请先观察当前页面。',
        'screenshot_status': 'captured', 'screenshot_file': 'failure-fixture.png',
    }
    data.update(overrides)
    encoded = base64.urlsafe_b64encode(json.dumps(data, ensure_ascii=False).encode()).decode().rstrip('=')
    return 'PLATFORM_BROWSER_DIAGNOSTICS_V1:' + encoded


def browser_observation(fingerprint='a' * 64, *, scope='page', text=None, elements=None):
    return {
        'version': 1, 'page_url': 'https://fixture.example.test/#/catalog',
        'page_title': 'Catalog', 'scope': scope, 'fingerprint': fingerprint,
        'settled': True, 'truncated': False, 'notes': [],
        'elements': elements if elements is not None else [{
            'tag': 'input', 'role': 'textbox', 'name': 'Display name', 'id': 'f17',
            'html_name': 'backend_display_name', 'type': 'text', 'placeholder': 'Name', 'visible': True, 'enabled': True,
            'readonly': False,
            'mcp_selector': 'input[name="backend_display_name"]:visible',
            'mcp_selector_status': 'verified_current_page',
            'container': 'Create entry',
        }],
        'text': text if text is not None else ['Create entry'],
    }


class ExplorationDiagnosticsTests(SimpleTestCase):
    def event(self, output=None):
        recorder = ExplorationTraceRecorder('/')
        recorder.on_tool_start({'name': 'playwright_fill'}, '', run_id='r1', inputs={'selector': '[name=f17]', 'value': 'test'})
        recorder.on_tool_end(output or ('Operation failed: not visible\n' + trailer()), run_id='r1')
        return recorder.evidence_snapshot()['events'][0]

    def test_mcp_repr_trailer_has_real_url_and_labels_not_last_root(self):
        output = f"[TextContent(type='text', text='Operation failed: not visible'), TextContent(type='text', text='{trailer()}')]"
        event = self.event(output)
        self.assertEqual(event['status'], 'failed')
        self.assertEqual(event['relative_path'], '/#/catalog')
        self.assertEqual(event['page_context']['element_label'], 'Display name')
        self.assertNotIn('PLATFORM_BROWSER_DIAGNOSTICS', event['result_excerpt'])
        self.assertNotIn('PLATFORM_BROWSER_DIAGNOSTICS', event['raw_output'])
        failure = failure_context([event], 'interaction_failure')
        self.assertEqual(failure['event_id'], 'E000001')
        self.assertEqual(failure['page_title'], 'Catalog')
        self.assertEqual(failure['screenshot_file'], 'failure-fixture.png')

    def test_metadata_preserved_even_when_is_error_lost_by_adapter(self):
        self.assertTrue(_tool_failed(trailer(), tool_name='playwright_fill'))
        self.assertFalse(_tool_failed('normal page\n' + trailer(tool_failed=False), tool_name='playwright_get_visible_html'))

    def test_success_observation_saves_actual_page_context(self):
        recorder = ExplorationTraceRecorder('/')
        recorder.on_tool_start({'name': 'playwright_get_visible_html'}, '', run_id='r1', inputs={})
        recorder.on_tool_end('HTML content: <h1>Page</h1>\n' + trailer(tool_failed=False), run_id='r1')
        state = recorder.evidence_snapshot()['page_states'][0]
        self.assertEqual(state['relative_path'], '/#/catalog')
        self.assertEqual(state['page_context']['page_title'], 'Catalog')

    def test_validated_observation_is_decoded_from_textcontent_repr_and_bounds_oversize(self):
        raw = f"[TextContent(type='text', text='{trailer(tool_failed=False, observation=browser_observation(text=['x' * 500] * 80))}') ]"
        context, without = extract_page_context(raw)
        observation = context['observation']
        self.assertEqual(observation['fingerprint'], 'a' * 64)
        self.assertTrue(observation['truncated'])
        self.assertLessEqual(len(json.dumps(observation, ensure_ascii=False, separators=(',', ':'))), MAX_OBSERVATION_JSON_CHARS)
        self.assertNotIn('PLATFORM_BROWSER_DIAGNOSTICS', without)

    def test_invalid_observation_keeps_valid_legacy_diagnostics(self):
        context, _ = extract_page_context(trailer(
            tool_failed=False, observation=browser_observation(fingerprint='not-a-fingerprint'),
        ))
        self.assertEqual(context['page_title'], 'Catalog')
        self.assertNotIn('observation', context)
        self.assertEqual(context['observation_error'], 'invalid')
        self.assertIn('不可用', render_observation_unavailable(context))

    def test_html_name_and_native_readonly_are_validated_and_rendered_distinctly(self):
        observation = browser_observation(elements=[{
            'tag': 'input', 'role': 'combobox', 'name': 'Visible field label',
            'html_name': 'stored_field', 'id': 'field-1', 'type': 'text',
            'placeholder': '', 'visible': True, 'enabled': True, 'readonly': True,
            'container': 'Named dialog',
        }, {
            'tag': 'div', 'role': 'combobox', 'name': 'Custom control label',
            'html_name': '', 'id': 'custom-1', 'type': '', 'placeholder': '',
            'visible': True, 'enabled': True, 'readonly': None, 'container': 'Named dialog',
        }])
        context, _ = extract_page_context(trailer(tool_failed=False, observation=observation))
        controls = context['observation']['elements']
        self.assertEqual(controls[0]['html_name'], 'stored_field')
        self.assertIs(controls[0]['readonly'], True)
        self.assertIsNone(controls[1]['readonly'])
        rendered = render_observation(context)
        self.assertIn('语义名称近似值 (not HTML name)=Visible field label', rendered)
        self.assertIn('HTML name=stored_field', rendered)
        self.assertIn('readonly=true', rendered)
        self.assertIn('readonly=unknown', rendered)

        observation['elements'][0]['readonly'] = 'true'
        invalid_context, _ = extract_page_context(trailer(tool_failed=False, observation=observation))
        self.assertEqual(invalid_context['observation']['elements'], [controls[1]])
        self.assertTrue(invalid_context['observation']['truncated'])

    def test_selector_hint_is_literal_optional_and_conflicting_verified_claim_is_removed(self):
        without_hint = browser_observation(elements=[{
            'tag': 'input', 'role': '', 'name': 'Optional hint', 'html_name': '',
            'id': '', 'type': 'text', 'placeholder': '', 'visible': True,
            'enabled': True, 'readonly': False, 'container': '',
        }])
        context, _ = extract_page_context(trailer(tool_failed=False, observation=without_hint))
        control = context['observation']['elements'][0]
        self.assertFalse(context['observation']['truncated'])
        self.assertEqual(control['mcp_selector'], '')
        self.assertEqual(control['mcp_selector_status'], 'not_provided')

        literal = 'input[name="two  spaces \\"quoted\\""]:visible'
        with_hint = browser_observation(elements=[{
            **without_hint['elements'][0], 'mcp_selector': literal,
            'mcp_selector_status': 'verified_current_page',
        }])
        context, _ = extract_page_context(trailer(tool_failed=False, observation=with_hint))
        self.assertEqual(context['observation']['elements'][0]['mcp_selector'], literal)
        self.assertIn(literal, render_observation(context))

        conflicting = browser_observation(elements=[{
            **without_hint['elements'][0], 'mcp_selector': '',
            'mcp_selector_status': 'verified_current_page',
        }])
        context, _ = extract_page_context(trailer(tool_failed=False, observation=conflicting))
        self.assertTrue(context['observation']['truncated'])
        self.assertEqual(context['observation']['elements'][0]['mcp_selector'], '')
        self.assertEqual(context['observation']['elements'][0]['mcp_selector_status'], 'verification_error')

    def test_native_select_options_keep_exact_values_and_are_bounded(self):
        options = [
            {'value': 'en', 'label': 'English', 'disabled': False, 'selected': False},
            {'value': ' fr  "quoted" ', 'label': 'French Canada', 'disabled': False, 'selected': True},
        ]
        observation = browser_observation(elements=[{
            'tag': 'select', 'role': '', 'name': 'Language', 'html_name': '',
            'id': '', 'type': '', 'placeholder': '', 'visible': True,
            'enabled': True, 'readonly': None, 'container': 'Preferences',
            'mcp_selector': 'select[aria-label="Language"]:visible',
            'mcp_selector_status': 'verified_current_page',
            'select_value': ' fr  "quoted" ', 'options': options,
            'options_truncated': False,
        }])
        context, _ = extract_page_context(trailer(tool_failed=False, observation=observation))
        control = context['observation']['elements'][0]
        self.assertEqual(control['select_value'], ' fr  "quoted" ')
        self.assertEqual(control['options'], options)
        rendered = render_observation(context)
        self.assertIn('当前 select value=" fr  \\"quoted\\" "', rendered)
        self.assertIn('原生 select options=', rendered)

        observation['elements'][0]['options'] = options * 21
        context, _ = extract_page_context(trailer(tool_failed=False, observation=observation))
        self.assertTrue(context['observation']['truncated'])
        self.assertTrue(context['observation']['elements'][0]['options_truncated'])
        self.assertEqual(len(context['observation']['elements'][0]['options']), 40)

    def test_malformed_tag_cannot_crash_optional_select_branch(self):
        observation = browser_observation(elements=[{
            'tag': 123, 'visible': True, 'enabled': True, 'readonly': None,
        }])
        context, _ = extract_page_context(trailer(tool_failed=False, observation=observation))
        self.assertEqual(context['observation']['elements'][0]['tag'], '')
        self.assertEqual(context['observation']['elements'][0]['mcp_selector_status'], 'not_provided')

    def test_projection_reserves_a_complete_truncation_marker_at_near_exact_budget(self):
        context, _ = extract_page_context(trailer(
            tool_failed=False, observation=browser_observation(text=['late text ' * 30]),
        ))
        source = json.dumps(context, ensure_ascii=False, sort_keys=True)
        without_text = {**context, 'observation': {**context['observation'], 'text': []}}
        budget = len(render_observation(without_text)) + 3
        rendered = render_observation(context, limit=budget)
        self.assertLessEqual(len(rendered), budget)
        self.assertTrue(rendered.endswith('[页面观察摘要已截断。]'))
        complete_lines = render_observation(context).splitlines()
        for line in rendered.splitlines()[:-1]:
            self.assertIn(line, complete_lines)
        self.assertEqual(json.dumps(context, ensure_ascii=False, sort_keys=True), source)

    def test_projection_reserves_visible_text_among_many_long_selectors(self):
        template = browser_observation()['elements'][0]
        elements = [{
            **template, 'name': f'Control {index}', 'id': f'control-{index}',
            'mcp_selector': f'input[data-key="{index}  ' + 'segment  ' * 55 + '"]:visible',
        } for index in range(10)]
        texts = [(f'Visible line {index}: ' + 'content ' * 20).rstrip() for index in range(16)]
        context, _ = extract_page_context(trailer(
            tool_failed=False, observation=browser_observation(elements=elements, text=texts),
        ))
        self.assertEqual(context['observation']['text'], texts)
        original = json.dumps(context, ensure_ascii=False, sort_keys=True)
        rendered = render_observation(context)
        self.assertLessEqual(len(rendered), 8000)
        self.assertIn('[页面观察摘要已截断。]', rendered)
        self.assertIn(elements[0]['mcp_selector'], rendered)
        self.assertGreaterEqual(sum(line.startswith('控件：') for line in rendered.splitlines()), 3)
        for item in texts:
            self.assertIn('可见文本：' + item, rendered)
        self.assertEqual(json.dumps(context, ensure_ascii=False, sort_keys=True), original)

        message = ToolMessage(
            content=trailer(tool_failed=False, observation=context['observation']),
            name='playwright_get_visible_html', tool_call_id='balanced-read',
        )
        projected, _ = project_messages([message])
        self.assertIn('可见文本：' + texts[-1], projected[0].content)
        self.assertIn(elements[0]['mcp_selector'], projected[0].content)
        self.assertLessEqual(len(projected[0].content), 8000)

    def test_projection_preserves_browser_control_order_and_exact_selector_lines(self):
        template = browser_observation()['elements'][0]
        elements = [{
            **template, 'tag': tag, 'role': role, 'id': f'control-{index}',
            'mcp_selector': f'  {tag}[data-key="{index}  two  spaces"]:visible  ',
        } for index, (tag, role) in enumerate([
            ('a', 'link'), ('input', 'textbox'), ('div', 'combobox'), ('button', 'button'),
        ])]
        context, _ = extract_page_context(trailer(
            tool_failed=False, observation=browser_observation(elements=elements, text=['visible ' * 40] * 20),
        ))
        selector_label = '已核对当前页面的MCP selector，语义名称不等于HTML属性='
        for limit in (0, 1, 3, 4, 13, 64, 128, 256, 511, 1024, 3000, 8000, 16000):
            with self.subTest(limit=limit):
                rendered = render_observation(context, limit=limit)
                self.assertLessEqual(len(rendered), limit)
                selectors = [
                    line.split(selector_label, 1)[1]
                    for line in rendered.splitlines() if line.startswith('控件：')
                ]
                expected = [item['mcp_selector'] for item in elements if item['mcp_selector'] in selectors]
                self.assertEqual(selectors, expected)
                if limit >= 3000:
                    self.assertEqual(selectors, [item['mcp_selector'] for item in elements])
                    self.assertIn('可见文本：', rendered)
                if 4 <= limit <= 3000:
                    self.assertIn('截断', rendered)

    def test_projection_long_notes_cannot_consume_all_control_and_text_space(self):
        observation = browser_observation(text=['Visible detail ' * 20] * 20)
        observation['notes'] = ['note ' * 50] * 24
        context, _ = extract_page_context(trailer(tool_failed=False, observation=observation))
        rendered = render_observation(context, limit=2000)
        self.assertLessEqual(len(rendered), 2000)
        self.assertIn('控件：', rendered)
        self.assertIn('可见文本：Visible detail', rendered)
        self.assertIn('截断', rendered)

    def test_projection_reclaims_budget_when_only_one_content_kind_exists(self):
        template = browser_observation()['elements'][0]
        for elements, texts, prefix in (
            ([template] * 20, [], '控件：'),
            ([], ['Visible detail ' * 20] * 20, '可见文本：'),
        ):
            with self.subTest(prefix=prefix):
                context, _ = extract_page_context(trailer(
                    tool_failed=False, observation=browser_observation(elements=elements, text=texts),
                ))
                rendered = render_observation(context, limit=2000)
                self.assertLessEqual(len(rendered), 2000)
                content = '\n'.join(line for line in rendered.splitlines() if line.startswith(prefix))
                self.assertGreater(len(content), 1000)
                self.assertIn('截断', rendered)

    def test_boolean_observation_version_is_invalid(self):
        context, _ = extract_page_context(trailer(
            tool_failed=False, observation={**browser_observation(), 'version': True},
        ))
        self.assertEqual(context['observation_error'], 'invalid')

    def test_about_blank_and_consumer_truncation_are_explicit(self):
        observation = browser_observation(
            text=['t' * 600] * 81,
            elements=[{
                'tag': 'input', 'role': 'textbox', 'name': 'n' * 310, 'id': 'i',
                'type': 'text', 'placeholder': '', 'visible': True, 'enabled': True,
                'container': 'page',
            }] * 49,
        )
        observation['page_url'] = 'about:blank'
        context, _ = extract_page_context(trailer(tool_failed=False, observation=observation))
        normalized = context['observation']
        self.assertEqual(normalized['page_url'], 'about:blank')
        self.assertTrue(normalized['truncated'])
        self.assertLessEqual(len(normalized['elements']), 48)
        self.assertLessEqual(len(normalized['text']), 80)

    def test_invalid_observation_does_not_create_a_proven_page_state(self):
        output = 'Traceback\nError executing tool\n' + trailer(
            tool_failed=False, observation=browser_observation(fingerprint='invalid'),
        )
        self.assertFalse(_tool_failed(output, tool_name='playwright_get_visible_html'))
        recorder = ExplorationTraceRecorder('/')
        recorder.on_tool_start({'name': 'playwright_get_visible_html'}, '', run_id='invalid-read', inputs={})
        recorder.on_tool_end(output, run_id='invalid-read')
        self.assertEqual(recorder.evidence_snapshot()['page_states'], [])

    def test_successful_structured_read_ignores_error_like_page_copy_and_reuses_browser_state(self):
        first = 'Traceback\nError executing tool\n' + trailer(
            tool_failed=False, observation=browser_observation(scope='form#entry'),
        )
        second = '<main>Error executing tool</main>' + trailer(
            tool_failed=False, observation=browser_observation(scope='page'),
        )
        self.assertFalse(_tool_failed(first, tool_name='playwright_get_visible_html'))
        recorder = ExplorationTraceRecorder('/')
        for index, output in enumerate((first, second), 1):
            recorder.on_tool_start({'name': 'playwright_get_visible_html'}, '', run_id=f'read-{index}', inputs={})
            recorder.on_tool_end(output, run_id=f'read-{index}')
        states = recorder.evidence_snapshot()['page_states']
        self.assertEqual(len(states), 1)
        self.assertEqual(states[0]['fingerprint'], 'a' * 16)

    def test_trailer_is_removed_from_model_projection_not_durable_source(self):
        text = 'Operation failed. 平台现场：输入框不可见。\n' + trailer()
        source = ToolMessage(content=text, name='playwright_fill', tool_call_id='call-1')
        projected, _ = project_messages([source])
        self.assertNotIn('PLATFORM_BROWSER_DIAGNOSTICS', projected[0].content)
        self.assertIn('输入框不可见', projected[0].content)
        self.assertIn('PLATFORM_BROWSER_DIAGNOSTICS', source.content)

    def test_bad_or_missing_diagnostics_never_invent_a_failure_page(self):
        for raw in ('no metadata', 'PLATFORM_BROWSER_DIAGNOSTICS_V1:invalid', 'PLATFORM_BROWSER_DIAGNOSTICS_V1:' + 'a' * 24001):
            with self.subTest(raw=raw[:40]):
                self.assertEqual(extract_page_context(raw)[0], {})
        event = self.event('Operation failed: locator resolved to hidden input')
        result = failure_context([event], 'interaction_failure')
        self.assertNotIn('page_url', result)
        self.assertEqual(result['screenshot_status'], 'not_requested')
        self.assertEqual(result['reason_code'], 'element_hidden')

    def test_later_success_does_not_misattribute_a_recovered_failure(self):
        failed = self.event()
        success = {**failed, 'event_id': 'E000002', 'status': 'succeeded', 'page_context': {}}
        self.assertEqual(failure_context([failed], ''), {})
        result = failure_context([failed, success], 'model_service_error', '模型连接中断')
        self.assertEqual(result['event_id'], 'E000002')
        self.assertEqual(result['message'], '模型连接中断')
        self.assertEqual(result['location_source'], 'last_observed')
        self.assertEqual(result['screenshot_status'], 'not_requested')

    def test_bounded_fields_and_unsafe_screenshot_path_rejected(self):
        metadata, _ = extract_page_context(trailer(
            page_url='https://user:secret@fixture.example.test/#/catalog',
            page_title='X' * 300, screenshot_file='../../elsewhere.png',
        ))
        self.assertNotIn('secret', metadata['page_url'])
        self.assertEqual(len(metadata['page_title']), 200)
        self.assertEqual(metadata['screenshot_status'], 'unavailable')
        self.assertEqual(metadata['screenshot_file'], '')

    def test_terminal_snapshot_preserves_diagnostics(self):
        agent = ScriptExplorationAgent(Mock(), {}, str(uuid4()), lambda: False, 10)
        agent._saved_trace_data = {'events': [self.event()]}
        agent._termination_reason = 'interaction_failure'
        self.assertEqual(agent._snapshot()['failure_context']['element_label'], 'Display name')

    def test_model_timeout_does_not_claim_the_previous_failed_tool_as_its_cause(self):
        result = failure_context([self.event()], 'transient', '模型连接中断')
        self.assertEqual(result['location_source'], 'last_observed')
        self.assertEqual(result['message'], '模型连接中断')
        self.assertEqual(result['screenshot_status'], 'not_requested')

    def test_repair_runtime_values_stay_templated_in_new_diagnostics(self):
        recorder = ExplorationTraceRecorder('/')
        recorder.configure_runtime({'TOKEN': 'runtime-private-fixture'}, {'TOKEN': 'runtime'})
        recorder.on_tool_start({'name': 'playwright_fill'}, '', run_id='repair', inputs={'selector': 'input'})
        recorder.on_tool_end('Operation failed\n' + trailer(
            page_url='https://fixture.test/?token=runtime-private-fixture',
            element_label='runtime-private-fixture',
        ), run_id='repair')
        context = recorder.evidence_snapshot()['events'][0]['page_context']
        self.assertEqual(context['page_url'], 'https://fixture.test/?token={{TOKEN}}')
        self.assertEqual(context['element_label'], '{{TOKEN}}')
        self.assertEqual(context['screenshot_file'], 'failure-fixture.png')


class GenerationFailureScreenshotTests(TestCase):
    def setUp(self):
        self.owner = get_user_model().objects.create_user(username='image-owner', email='image-owner@example.test')
        self.member = get_user_model().objects.create_user(username='image-member', email='image-member@example.test')
        self.outsider = get_user_model().objects.create_user(username='image-outside', email='image-outside@example.test')
        self.project = Project.objects.create(name='Image project', project_type='web', owner=self.owner, created_by=self.owner)
        ProjectMember.objects.create(project=self.project, user=self.member, role='viewer')
        self.generation = WebUIScriptGeneration.objects.create(
            project=self.project, user=self.owner, status='needs_review',
            exploration_snapshot={'failure_context': failure_context([ExplorationDiagnosticsTests().event()], 'interaction_failure')},
        )
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.settings_override = override_settings(BASE_DIR=self.directory.name)
        self.settings_override.enable()
        self.addCleanup(self.settings_override.disable)
        self.root = Path(self.directory.name) / 'temp' / 'playwright-mcp' / str(self.generation.id) / 'screenshots'
        self.root.mkdir(parents=True)
        self.image = self.root / 'failure-fixture.png'
        self.content = b'\x89PNG\r\n\x1a\n' + b'fixture'
        self.image.write_bytes(self.content)

    def request(self, user=None, params=None, project_id=None):
        request = APIRequestFactory().get('/failure-screenshot/', params if params is not None else {'event_id': 'E000001'})
        if user is not False:
            force_authenticate(request, user=user or self.owner)
        return WebUIScriptGenerationFailureScreenshotView.as_view()(
            request, project_id=project_id or self.project.id, generation_id=self.generation.id,
        )

    def test_owner_receives_png_without_public_cache(self):
        response = self.request()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Cache-Control'], 'private, no-store')
        self.assertEqual(b''.join(response.streaming_content), self.content)
        response.close()

    def test_auth_ownership_project_and_event_are_checked(self):
        self.assertEqual(self.request(user=False).status_code, 401)
        self.assertEqual(self.request(user=self.member).status_code, 403)
        self.assertEqual(self.request(user=self.outsider).status_code, 404)
        self.assertEqual(self.request(project_id=9999).status_code, 404)
        for params in ({}, {'event_id':'E000002'}, {'event_id':'E000001','captured_at':'another-attempt'}):
            self.assertEqual(self.request(params=params).status_code, 404)

    def test_missing_invalid_symlink_and_traversal_files_rejected(self):
        self.image.unlink()
        self.assertEqual(self.request().status_code, 404)
        self.image.write_text('not PNG', encoding='utf-8')
        self.assertEqual(self.request().status_code, 404)
        self.image.unlink()
        outside = Path(self.directory.name) / 'outside.png'
        outside.write_bytes(self.content)
        self.image.symlink_to(outside)
        self.assertEqual(self.request().status_code, 404)
        for filename in ('../../outside.png', str(outside)):
            self.generation.exploration_snapshot['failure_context']['screenshot_file'] = filename
            self.generation.save()
            self.assertEqual(self.request().status_code, 404)
