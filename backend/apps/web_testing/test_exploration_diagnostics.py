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
from .exploration_diagnostics import extract_page_context, failure_context
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
