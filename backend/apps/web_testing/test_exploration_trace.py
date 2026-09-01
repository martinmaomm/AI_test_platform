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
    structured_trace_evidence,
    trace_has_minimum_page_state,
)
from .generation_contracts import ScenarioSpec


class ExplorationTraceRecorderTests(SimpleTestCase):
    def scenario(self):
        return ScenarioSpec.model_validate({
            'title': '查看用户', 'objective': '查看用户列表', 'preconditions': [],
            'steps': [{'id': 'S1', 'name': '查看用户列表', 'intent': 'read', 'target_hint': '用户列表', 'expected': '列表可见'}],
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

    def test_successful_callbacks_build_persistable_evidence_and_classify_fragile_selectors(self):
        recorder = ExplorationTraceRecorder('/users', runtime_namespace='aits-explore-run-123456789abc')
        for run_id, selector in (
            ('stable', '[data-testid="user-list"]'),
            ('dynamic', '#el-id-123'),
            ('visible-input', 'input:visible'),
            ('nth', 'table > tbody > tr:nth-child(2) > td > button'),
            ('fallback', '[data-testid="user-list"], .user-list'),
            ('siblings', 'td + td + td + td'),
        ):
            recorder.on_tool_start(
                {'name': 'playwright_click'}, '', run_id=run_id,
                inputs={'selector': selector},
            )
            recorder.on_tool_end('用户列表操作成功', run_id=run_id)
        recorder.on_tool_start(
            {'name': 'playwright_click'}, '', run_id='failed',
            inputs={'selector': '[data-testid="missing"]'},
        )
        recorder.on_tool_error(RuntimeError('invalid selector'), run_id='failed')

        trace = assess_trace_coverage(self.scenario(), recorder.build(tool_stats={'total_tool_calls': 5}))
        by_value = {item.locator_value: item for item in trace.element_evidence}

        self.assertEqual(trace.schema_version, 2)
        self.assertEqual(by_value['[data-testid="user-list"]'].stability, 'stable')
        self.assertEqual(by_value['#el-id-123'].stability, 'rejected')
        self.assertEqual(by_value['input:visible'].stability, 'fragile')
        self.assertEqual(by_value['table > tbody > tr:nth-child(2) > td > button'].stability, 'fragile')
        self.assertEqual(by_value['[data-testid="user-list"], .user-list'].stability, 'fragile')
        self.assertEqual(by_value['td + td + td + td'].stability, 'fragile')
        self.assertNotIn('[data-testid="missing"]', by_value)
        self.assertTrue(all(item.evidence_id.startswith('E') for item in trace.element_evidence))

    def test_intent_aware_coverage_links_crud_interactions_and_following_observes(self):
        scenario = ScenarioSpec.model_validate({
            'title': '用户 CRUD', 'objective': '验证用户变更', 'preconditions': [],
            'steps': [
                {'id': 'S1', 'name': '新增用户', 'intent': 'create', 'target_hint': '用户列表', 'expected': '用户创建成功'},
                {'id': 'S2', 'name': '编辑用户', 'intent': 'update', 'target_hint': '用户列表', 'expected': '用户更新成功'},
                {'id': 'S3', 'name': '删除用户', 'intent': 'delete', 'target_hint': '用户列表', 'expected': '用户删除成功'},
            ],
            'assertions': [
                {'id': 'A1', 'name': '创建成功', 'target_hint': '用户', 'expected': '成功', 'step_id': 'S1'},
                {'id': 'A2', 'name': '更新成功', 'target_hint': '用户', 'expected': '成功', 'step_id': 'S2'},
                {'id': 'A3', 'name': '删除成功', 'target_hint': '用户', 'expected': '成功', 'step_id': 'S3'},
            ],
            'cleanup': [
                {'id': 'C1', 'name': '清理用户', 'target_hint': '用户', 'condition': '删除本轮用户', 'step_id': 'S1'},
            ],
        })
        events = [
            ExplorationEvent(sequence=1, tool_name='playwright_click', category='interact', status='succeeded', relative_path='/users', locator={'text': '添加'}),
            ExplorationEvent(sequence=2, tool_name='playwright_get_visible_text', category='observe', status='succeeded', relative_path='/users', output_excerpt='新增用户表单已打开'),
            ExplorationEvent(sequence=3, tool_name='playwright_click', category='interact', status='succeeded', relative_path='/users', locator={'selector': 'button:has-text(编辑)'}),
            ExplorationEvent(sequence=4, tool_name='playwright_get_visible_text', category='observe', status='succeeded', relative_path='/users', output_excerpt='编辑用户表单已打开'),
            ExplorationEvent(sequence=5, tool_name='playwright_click', category='interact', status='succeeded', relative_path='/users', locator={'selector': 'button:has-text(删除)'}),
            ExplorationEvent(sequence=6, tool_name='playwright_get_visible_text', category='observe', status='succeeded', relative_path='/users', output_excerpt='删除用户确认弹窗已打开'),
        ]

        trace = assess_trace_coverage(scenario, ExplorationTrace(start_path='/users', events=events))

        self.assertEqual(trace.coverage['S1']['event_sequences'], [1, 2])
        self.assertEqual(trace.coverage['S2']['event_sequences'], [3, 4])
        self.assertEqual(trace.coverage['S3']['event_sequences'], [5, 6])
        evidence_by_sequence = {item.source_sequence: item for item in trace.element_evidence}
        self.assertEqual(evidence_by_sequence[1].scenario_step_ids, ['S1'])
        self.assertEqual(evidence_by_sequence[3].scenario_step_ids, ['S2'])
        self.assertEqual(evidence_by_sequence[5].scenario_step_ids, ['S3'])

    def test_crud_coverage_does_not_accept_visible_text_without_matching_interaction(self):
        scenario = ScenarioSpec.model_validate({
            'title': '新增用户', 'objective': '新增用户', 'preconditions': [],
            'steps': [{'id': 'S1', 'name': '新增用户', 'intent': 'create', 'target_hint': '用户列表', 'expected': '用户创建成功'}],
            'assertions': [{'id': 'A1', 'name': '创建成功', 'target_hint': '用户', 'expected': '成功', 'step_id': 'S1'}],
            'cleanup': [{'id': 'C1', 'name': '清理用户', 'target_hint': '用户', 'condition': '删除本轮用户', 'step_id': 'S1'}],
        })
        trace = assess_trace_coverage(scenario, ExplorationTrace(
            start_path='/users',
            events=[ExplorationEvent(
                sequence=1, tool_name='playwright_get_visible_text', category='observe',
                status='succeeded', relative_path='/users', output_excerpt='添加用户按钮和用户列表可见',
            )],
        ))

        self.assertEqual(trace.coverage['S1']['status'], 'missing')
        self.assertFalse(trace.element_evidence)

    def test_recorder_retains_delete_intent_after_long_locator_is_truncated(self):
        scenario = ScenarioSpec.model_validate({
            'title': '删除用户', 'objective': '删除用户', 'preconditions': [],
            'steps': [{'id': 'S1', 'name': '删除用户', 'intent': 'delete', 'target_hint': '用户', 'expected': '用户已删除'}],
            'assertions': [{'id': 'A1', 'name': '删除成功', 'target_hint': '用户', 'expected': '已删除', 'step_id': 'S1'}],
            'cleanup': [{'id': 'C1', 'name': '确认删除用户', 'target_hint': '用户', 'condition': '删除已完成', 'step_id': 'S1'}],
        })
        raw_selector = 'button:has-text(用户)' + ('x' * 200) + '删除'
        recorder = ExplorationTraceRecorder('/users')
        recorder.on_tool_start(
            {'name': 'playwright_click'}, '', run_id='delete',
            inputs={'selector': raw_selector},
        )
        recorder.on_tool_end('删除确认框已打开', run_id='delete')

        trace = assess_trace_coverage(scenario, recorder.build(tool_stats={'total_tool_calls': 1}))

        event = trace.events[0]
        self.assertEqual(event.operation_intent, 'delete')
        self.assertNotIn('删除', event.locator['selector'])
        self.assertEqual(len(event.locator['selector']), 180)
        self.assertEqual(trace.coverage['S1']['event_sequences'], [1])
        self.assertEqual(trace.element_evidence[0].source_sequence, 1)

    def test_failed_or_blocked_crud_tools_do_not_form_usable_evidence(self):
        scenario = ScenarioSpec.model_validate({
            'title': '删除用户', 'objective': '删除用户', 'preconditions': [],
            'steps': [{'id': 'S1', 'name': '删除用户', 'intent': 'delete', 'target_hint': '用户', 'expected': '用户已删除'}],
            'assertions': [{'id': 'A1', 'name': '删除成功', 'target_hint': '用户', 'expected': '已删除', 'step_id': 'S1'}],
            'cleanup': [{'id': 'C1', 'name': '确认删除用户', 'target_hint': '用户', 'condition': '删除已完成', 'step_id': 'S1'}],
        })
        recorder = ExplorationTraceRecorder('/users')
        recorder.on_tool_start(
            {'name': 'playwright_click'}, '', run_id='failed',
            inputs={'selector': 'button:has-text(删除用户)'},
        )
        recorder.on_tool_error(RuntimeError('invalid selector'), run_id='failed')
        recorder.mark_blocked(
            {'name': 'playwright_click'}, '', run_id='blocked',
            inputs={'selector': 'button:has-text(删除用户)'},
        )

        trace = assess_trace_coverage(scenario, recorder.build(tool_stats={'total_tool_calls': 2}))

        self.assertEqual([event.operation_intent for event in trace.events], ['', ''])
        self.assertEqual(trace.coverage['S1']['status'], 'missing')
        self.assertFalse(trace.element_evidence)

    def test_assert_for_create_uses_only_the_observe_after_matching_create(self):
        scenario = ScenarioSpec.model_validate({
            'title': '验证新增用户', 'objective': '验证新增结果', 'preconditions': [],
            'steps': [
                {'id': 'S1', 'name': '新增用户', 'intent': 'create', 'target_hint': '用户列表', 'expected': '用户创建成功'},
                {'id': 'S2', 'name': '验证新增结果', 'intent': 'assert', 'target_hint': '用户列表', 'expected': '新增用户显示'},
            ],
            'assertions': [
                {'id': 'A1', 'name': '创建成功', 'target_hint': '用户', 'expected': '成功', 'step_id': 'S1'},
                {'id': 'A2', 'name': '新增结果可见', 'target_hint': '用户', 'expected': '可见', 'step_id': 'S2'},
            ],
            'cleanup': [{'id': 'C1', 'name': '清理用户', 'target_hint': '用户', 'condition': '删除本轮用户', 'step_id': 'S1'}],
        })
        trace = assess_trace_coverage(scenario, ExplorationTrace(start_path='/users', events=[
            ExplorationEvent(sequence=1, tool_name='playwright_get_visible_text', category='observe', status='succeeded', relative_path='/users', output_excerpt='用户列表'),
            ExplorationEvent(sequence=2, tool_name='playwright_click', category='interact', status='succeeded', relative_path='/users', locator={'text': '添加'}),
            ExplorationEvent(sequence=3, tool_name='playwright_get_visible_text', category='observe', status='succeeded', relative_path='/users', output_excerpt='用户列表中显示本轮新增用户'),
        ]))

        self.assertEqual(trace.coverage['S2']['event_sequences'], [2, 3])
        self.assertNotIn(1, trace.coverage['S2']['event_sequences'])

    def test_create_targets_do_not_share_an_unrelated_add_interaction(self):
        scenario = ScenarioSpec.model_validate({
            'title': '创建不同目标', 'objective': '创建用户和角色', 'preconditions': [],
            'steps': [
                {'id': 'S1', 'name': '新增用户', 'intent': 'create', 'target_hint': '用户列表', 'expected': '用户创建成功'},
                {'id': 'S2', 'name': '新增角色', 'intent': 'create', 'target_hint': '角色列表', 'expected': '角色创建成功'},
            ],
            'assertions': [
                {'id': 'A1', 'name': '用户创建成功', 'target_hint': '用户', 'expected': '成功', 'step_id': 'S1'},
                {'id': 'A2', 'name': '角色创建成功', 'target_hint': '角色', 'expected': '成功', 'step_id': 'S2'},
            ],
            'cleanup': [{'id': 'C1', 'name': '清理用户', 'target_hint': '用户', 'condition': '删除本轮用户', 'step_id': 'S1'}],
        })
        trace = assess_trace_coverage(scenario, ExplorationTrace(start_path='/users', events=[
            ExplorationEvent(sequence=1, tool_name='playwright_click', category='interact', status='succeeded', relative_path='/users', locator={'text': '添加'}),
            ExplorationEvent(sequence=2, tool_name='playwright_get_visible_text', category='observe', status='succeeded', relative_path='/users', output_excerpt='新增用户表单已打开'),
        ]))

        self.assertEqual(trace.coverage['S1']['status'], 'confirmed')
        self.assertEqual(trace.coverage['S2']['status'], 'missing')

    def test_navigate_can_be_confirmed_by_the_following_login_observe_and_cleanup_by_delete(self):
        scenario = ScenarioSpec.model_validate({
            'title': '登录并清理', 'objective': '进入登录页后删除本轮数据', 'preconditions': [],
            'steps': [
                {'id': 'S1', 'name': '进入登录页面', 'intent': 'navigate', 'target_hint': '登录', 'expected': '登录表单显示'},
                {'id': 'S2', 'name': '清理测试用户', 'intent': 'cleanup', 'target_hint': '用户', 'expected': '删除本轮用户'},
            ],
            'assertions': [
                {'id': 'A1', 'name': '登录页可见', 'target_hint': '登录', 'expected': '可见', 'step_id': 'S1'},
                {'id': 'A2', 'name': '用户已清理', 'target_hint': '用户', 'expected': '删除', 'step_id': 'S2'},
            ],
        })
        trace = assess_trace_coverage(scenario, ExplorationTrace(start_path='/', events=[
            ExplorationEvent(sequence=1, tool_name='playwright_navigate', category='navigate', status='succeeded', relative_path='/login'),
            ExplorationEvent(sequence=2, tool_name='playwright_get_visible_text', category='observe', status='succeeded', relative_path='/login', output_excerpt='登录'),
            ExplorationEvent(sequence=3, tool_name='playwright_click', category='interact', status='succeeded', relative_path='/users', locator={'text': '删除用户'}),
        ]))

        self.assertEqual(trace.coverage['S1']['event_sequences'], [1, 2])
        self.assertEqual(trace.coverage['S2']['event_sequences'], [3])

    def test_login_navigation_requires_the_post_submit_destination(self):
        scenario = ScenarioSpec.model_validate({
            'title': '登录系统', 'objective': '登录后进入首页', 'preconditions': [],
            'steps': [{
                'id': 'S1', 'name': '登录系统', 'intent': 'navigate',
                'target_hint': '系统登录页面', 'expected': '成功登录并进入系统首页',
            }],
            'assertions': [{
                'id': 'A1', 'name': '首页可见', 'target_hint': '首页',
                'expected': '首页可见', 'step_id': 'S1',
            }],
        })
        before_login = ExplorationTrace(start_path='/', events=[
            ExplorationEvent(
                sequence=1, tool_name='playwright_navigate', category='navigate',
                status='succeeded', relative_path='/',
            ),
            ExplorationEvent(
                sequence=2, tool_name='playwright_get_visible_text', category='observe',
                status='succeeded', relative_path='/', output_excerpt='登录',
            ),
        ])
        after_login = before_login.model_copy(update={'events': [
            *before_login.events,
            ExplorationEvent(
                sequence=3, tool_name='playwright_click', category='interact',
                status='succeeded', relative_path='/',
                locator={'selector': 'button:has-text("登录")'},
            ),
            ExplorationEvent(
                sequence=4, tool_name='playwright_get_visible_text', category='observe',
                status='succeeded', relative_path='/', output_excerpt='首页 用户列表',
            ),
        ]})

        self.assertEqual(
            assess_trace_coverage(scenario, before_login).coverage['S1']['status'],
            'missing',
        )
        self.assertEqual(
            assess_trace_coverage(scenario, after_login).coverage['S1']['event_sequences'],
            [3, 4],
        )

    def test_generic_read_words_do_not_confirm_an_unrelated_page(self):
        scenario = ScenarioSpec.model_validate({
            'title': '读取用户列表', 'objective': '探索用户页面结构', 'preconditions': [],
            'steps': [{
                'id': 'S1', 'name': '探索页面元素', 'intent': 'read',
                'target_hint': '用户列表页面', 'expected': '获取页面结构和字段信息',
            }],
            'assertions': [{
                'id': 'A1', 'name': '用户列表可见', 'target_hint': '用户列表',
                'expected': '用户列表可见', 'step_id': 'S1',
            }],
        })
        trace = ExplorationTrace(start_path='/', events=[ExplorationEvent(
            sequence=1, tool_name='playwright_get_visible_text', category='observe',
            status='succeeded', relative_path='/', output_excerpt='登录 获取体验账号',
        )])

        self.assertEqual(
            assess_trace_coverage(scenario, trace).coverage['S1']['status'],
            'missing',
        )

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

    def test_structured_generator_input_is_bounded_and_excludes_raw_events(self):
        trace = ExplorationTrace(
            start_path='/',
            events=[
                ExplorationEvent(
                    sequence=index, tool_name='playwright_click', category='interact',
                    status='succeeded', relative_path='/users',
                    locator={'selector': f'[data-testid="user-{index}"]'},
                    output_excerpt='用户列表页面 ' * 100,
                )
                for index in range(1, 121)
            ],
            coverage={
                f'S{index}': {'status': 'confirmed', 'event_sequences': [index], 'reason': '已确认' * 100}
                for index in range(1, 121)
            },
        )

        evidence = structured_trace_evidence(trace)

        self.assertLessEqual(
            len(json.dumps(evidence, ensure_ascii=False, separators=(',', ':'))),
            _MAX_GENERATOR_EVIDENCE_CHARS,
        )
        self.assertNotIn('events', evidence)
        self.assertTrue(evidence['element_evidence'])

    def test_structured_generator_input_prioritizes_late_step_locator_over_unrelated_events(self):
        trace = ExplorationTrace(
            start_path='/users',
            events=[
                ExplorationEvent(
                    sequence=index, tool_name='playwright_click', category='interact',
                    status='succeeded', relative_path='/users',
                    locator={'selector': f'[data-testid="noise-{index}"]'},
                    output_excerpt='无关页面动作 ' * 20,
                )
                for index in range(1, 120)
            ] + [ExplorationEvent(
                sequence=120, tool_name='playwright_click', category='interact',
                status='succeeded', relative_path='/users',
                locator={'selector': '[data-testid="delete-user"]'},
                output_excerpt='删除用户',
            )],
            coverage={
                'S1': {'status': 'confirmed', 'event_sequences': [120], 'reason': '删除用户已确认'},
            },
        )

        evidence = structured_trace_evidence(trace)

        self.assertLessEqual(
            len(json.dumps(evidence, ensure_ascii=False, separators=(',', ':'))),
            _MAX_GENERATOR_EVIDENCE_CHARS,
        )
        self.assertIn('E000120', evidence['step_evidence']['S1']['evidence_ids'])
        self.assertTrue(any(
            item['evidence_id'] == 'E000120'
            for item in evidence['element_evidence']
        ))

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
