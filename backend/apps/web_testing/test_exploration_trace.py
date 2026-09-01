from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory

from django.test import SimpleTestCase

from .exploration_trace import (
    _MAX_GENERATOR_EVIDENCE_CHARS,
    ExplorationEvent,
    ExplorationTrace,
    ExplorationTraceRecorder,
    assess_trace_coverage,
    successful_trace_evidence,
    trace_has_minimum_page_state,
)
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

    def test_generator_evidence_compresses_repeated_page_text_within_a_hard_bound(self):
        trace = ExplorationTrace(
            start_path='/',
            observed_paths=['/users'],
            events=[
                ExplorationEvent(
                    sequence=1, tool_name='playwright_navigate', category='navigate',
                    status='succeeded', relative_path='/users', output_excerpt='用户列表页面',
                ),
                ExplorationEvent(
                    sequence=2, tool_name='playwright_click', category='interact',
                    status='succeeded', relative_path='/users',
                    locator={'selector': '[data-testid="user-search"]'},
                    input_summary='placeholder=用户名', output_excerpt='已打开查询条件',
                ),
                *[
                    ExplorationEvent(
                        sequence=index, tool_name='playwright_get_visible_text', category='observe',
                        status='succeeded', relative_path='/users',
                        output_excerpt='重复的整页列表文本 ' + ('用户列表 ' * 200),
                    )
                    for index in range(3, 42)
                ],
            ],
            coverage={'S1': {'status': 'confirmed', 'event_sequences': [2, 3], 'reason': '已确认查询控件'}},
        )

        evidence = successful_trace_evidence(trace)
        serialized = json.dumps(evidence, ensure_ascii=False, separators=(',', ':'))

        self.assertLessEqual(len(serialized), _MAX_GENERATOR_EVIDENCE_CHARS)
        self.assertIn('/users', serialized)
        self.assertTrue(any(
            event['locator'].get('selector') == '[data-testid="user-search"]'
            for event in evidence['events']
        ))
        self.assertEqual(evidence['coverage']['S1']['status'], 'confirmed')
        self.assertEqual(evidence['coverage']['S1']['event_sequences'], [2])
        self.assertLess(len(evidence['events']), len(trace.events))
        sequences = [event['sequence'] for event in evidence['events']]
        self.assertEqual(sequences, sorted(sequences))

    def test_generator_evidence_folds_only_the_same_page_state(self):
        trace = ExplorationTrace(
            start_path='/users', observed_paths=['/users'],
            events=[
                ExplorationEvent(
                    sequence=1, tool_name='playwright_get_visible_text', category='observe',
                    status='succeeded', relative_path='/users', output_excerpt='用户列表 Alice',
                ),
                ExplorationEvent(
                    sequence=2, tool_name='playwright_get_visible_text', category='observe',
                    status='succeeded', relative_path='/users', output_excerpt='用户列表 Alice',
                ),
                ExplorationEvent(
                    sequence=3, tool_name='playwright_get_visible_text', category='observe',
                    status='succeeded', relative_path='/users', output_excerpt='用户列表 Bob',
                ),
                ExplorationEvent(
                    sequence=4, tool_name='playwright_snapshot', category='observe',
                    status='succeeded', relative_path='/users', output_excerpt='快照文本版本一',
                    state_fingerprint='stable-page-state',
                ),
                ExplorationEvent(
                    sequence=5, tool_name='playwright_snapshot', category='observe',
                    status='succeeded', relative_path='/users', output_excerpt='快照文本版本二',
                    state_fingerprint='stable-page-state',
                ),
            ],
        )

        evidence = successful_trace_evidence(trace)

        self.assertEqual([event['sequence'] for event in evidence['events']], [1, 3, 4])
        self.assertEqual(
            [event['output_excerpt'] for event in evidence['events']],
            ['用户列表 Alice', '用户列表 Bob', '快照文本版本一'],
        )

    def test_generator_evidence_hard_bound_downgrades_unattached_coverage(self):
        events = [
            ExplorationEvent(
                sequence=index, tool_name='playwright_get_visible_text', category='observe',
                status='succeeded', relative_path=f'/users/{index}',
                locator={'selector': f'[data-testid="user-{index}"]'},
                output_excerpt=f'用户详情 {index} ' + ('页面字段 ' * 120),
            )
            for index in range(1, 121)
        ]
        trace = ExplorationTrace(
            start_path='/' + ('very-long-start/' * 500),
            observed_paths=[f'/users/{index}' for index in range(1, 81)],
            events=events,
            coverage={
                f'S{index}': {
                    'status': 'confirmed', 'event_sequences': [index],
                    'reason': '已确认页面字段与定位器 ' + ('覆盖说明 ' * 100),
                }
                for index in range(1, 121)
            },
            cleanup={
                'status': 'residual', 'attempted': True,
                'residuals': [('残留记录 ' * 200) + str(index) for index in range(100)],
                'reason': '清理结果说明 ' * 300,
            },
            warnings=[('探索警告 ' * 200) + str(index) for index in range(50)],
        )

        evidence = successful_trace_evidence(trace)
        serialized = json.dumps(evidence, ensure_ascii=False, separators=(',', ':'))
        retained_sequences = {event['sequence'] for event in evidence['events']}

        self.assertLessEqual(len(serialized), _MAX_GENERATOR_EVIDENCE_CHARS)
        self.assertIn(1, retained_sequences)
        self.assertEqual(evidence['coverage']['S1']['status'], 'confirmed')
        self.assertEqual(evidence['coverage']['S1']['event_sequences'], [1])
        self.assertTrue(any(
            item['status'] == 'missing' and item['event_sequences'] == []
            for item in evidence['coverage'].values()
        ))
        for item in evidence['coverage'].values():
            self.assertTrue(set(item['event_sequences']).issubset(retained_sequences))
            if item['status'] in {'confirmed', 'partially_confirmed'}:
                self.assertTrue(item['event_sequences'])
        self.assertEqual(
            [event['sequence'] for event in evidence['events']],
            sorted(retained_sequences),
        )

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
