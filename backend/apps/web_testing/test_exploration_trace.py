from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory

from django.test import SimpleTestCase

from .exploration_trace import ExplorationTraceRecorder, assess_trace_coverage, successful_trace_evidence, trace_has_minimum_page_state
from .generation_contracts import ScenarioSpec


class ExplorationTraceRecorderTests(SimpleTestCase):
    def scenario(self):
        return ScenarioSpec.model_validate({
            'title': '查看用户', 'objective': '查看用户列表', 'preconditions': [],
            'steps': [{'id': 'S1', 'name': '进入用户列表', 'intent': 'navigate', 'target_hint': '用户列表', 'expected': '列表可见'}],
            'assertions': [{'id': 'A1', 'name': '列表可见', 'target_hint': '用户列表', 'expected': '可见', 'step_id': 'S1'}],
        })

    def test_callbacks_record_sanitized_deduplicated_trace_without_final_text(self):
        recorder = ExplorationTraceRecorder('/users')
        recorder.on_tool_start({'name': 'playwright_fill'}, '', run_id='fill', inputs={'selector': '#password', 'value': 'secret-value'})
        recorder.on_tool_end({'ok': True}, run_id='fill')
        recorder.on_tool_start({'name': 'playwright_get_visible_text'}, '', run_id='read-1', inputs={'path': '/users'})
        recorder.on_tool_end('用户列表\nAlice', run_id='read-1')
        recorder.on_tool_start({'name': 'playwright_get_visible_text'}, '', run_id='read-2', inputs={'path': '/users'})
        recorder.on_tool_end('用户列表\nAlice', run_id='read-2')
        recorder.on_tool_start({'name': 'playwright_click'}, '', run_id='bad', inputs={'selector': '#missing'})
        recorder.on_tool_error(RuntimeError('invalid selector'), run_id='bad')

        trace = assess_trace_coverage(self.scenario(), recorder.build(tool_stats={'total_tool_calls': 4}))
        dumped = trace.model_dump_json()
        self.assertEqual(trace.schema_version, 2)
        self.assertEqual(len(trace.events), 3)
        self.assertTrue(trace_has_minimum_page_state(trace))
        self.assertEqual(trace.coverage['S1']['status'], 'confirmed')
        self.assertNotIn('secret-value', dumped)
        self.assertEqual(trace.failed_interactions, [4])

    def test_generator_evidence_excludes_failed_locators_and_absolute_urls(self):
        recorder = ExplorationTraceRecorder('/')
        recorder.on_tool_start({'name': 'playwright_navigate'}, '', run_id='ok', inputs={'url': 'https://example.test/users'})
        recorder.on_tool_end('用户列表', run_id='ok')
        recorder.on_tool_start({'name': 'playwright_click'}, '', run_id='bad', inputs={'selector': '#not-found'})
        recorder.on_tool_error(RuntimeError('invalid selector'), run_id='bad')
        evidence = successful_trace_evidence(recorder.build(tool_stats={}))
        self.assertEqual(len(evidence['events']), 1)
        self.assertNotIn('#not-found', str(evidence))
        self.assertNotIn('https://', str(evidence))

    def test_fill_text_and_echoed_credentials_never_reach_database_or_jsonl_trace(self):
        with TemporaryDirectory() as directory:
            trace_file = Path(directory) / 'generation.trace.jsonl'
            recorder = ExplorationTraceRecorder(
                '/login', sensitive_values=('admin@example.test', 'real-password'),
                trace_file=trace_file,
            )
            recorder.on_tool_start(
                {'name': 'playwright_fill'}, '', run_id='username',
                inputs={'selector': '#username', 'text': 'admin@example.test'},
            )
            recorder.on_tool_end(
                'filled admin@example.test without exposing real-password', run_id='username',
            )
            trace = recorder.build(tool_stats={'total_tool_calls': 1})
            combined = trace.model_dump_json() + trace_file.read_text(encoding='utf-8')

        self.assertNotIn('admin@example.test', combined)
        self.assertNotIn('real-password', combined)
        self.assertIn('<runtime_test_data>', combined)
