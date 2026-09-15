"""Offline checks for action capabilities without page-label classification."""

from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from django.test import SimpleTestCase

from .exploration_policy import ExplorationPolicy
from .exploration_trace import ExplorationTraceRecorder
from .generation_preflight import run_safety_preflight
from .script_exploration_agent import EXPLORATION_SCRIPT_CONSTRAINTS, ScriptExplorationToolGuard


class ExplorationActionScopeTests(SimpleTestCase):
    def guard(self, *, read_only=False):
        return ScriptExplorationToolGuard(
            policy=ExplorationPolicy('offline-scope', 'scenario_namespace', read_only, not read_only, False),
            trace_recorder=ExplorationTraceRecorder('/'),
            target_url='https://example.test/', progress_notifier=lambda: None, stop_check=lambda: '',
        )

    def test_page_names_do_not_trigger_preflight_business_risk_inference(self):
        for description in (
            '打开已发布记录列表并查询', '查看支付配置页面，不提交任何修改',
            '检查上传历史和下载记录', 'Inspect payment and approval history',
        ):
            with self.subTest(description=description), patch(
                'web_testing.generation_preflight.usable_llm_configurations',
            ) as models, patch(
                'web_testing.generation_preflight.resolve_active_playwright_mcp_config',
                return_value=(2, {'mcpServers': {}}),
            ):
                models.return_value.filter.return_value.exists.return_value = True
                result = run_safety_preflight(
                    SimpleNamespace(model_info={'config_id': 1}, user_id=3, description_safe=description),
                    {'allow_test_data_writes': False},
                )
            self.assertEqual(result.outcome, 'continue')
            self.assertIn('不授予测试数据写入权限', result.warnings[0])

    def test_selectors_and_labels_do_not_determine_the_business_action(self):
        for read_only in (False, True):
            for selector in (
                '#payment-settings', 'text=已发布记录', 'text=下载记录',
                '[data-testid="upload-history"]', '#approval-center', '#submit-history',
            ):
                with self.subTest(read_only=read_only, selector=selector):
                    guard = self.guard(read_only=read_only)
                    run_id = uuid4()
                    guard.on_tool_start({'name': 'playwright_click'}, '', inputs={'selector': selector}, run_id=run_id)
                    guard.on_tool_end('Clicked successfully', run_id=run_id)
                    self.assertIsNone(guard.terminal_error)
                    self.assertEqual(guard.get_stats()['tool_counts']['playwright_click'], 1)

    def test_read_only_keyboard_guard_uses_key_not_selector_text(self):
        guard = self.guard(read_only=True)
        run_id = uuid4()
        guard.on_tool_start(
            {'name': 'playwright_press_key'}, '',
            inputs={'selector': '#payment-center-submit', 'key': 'Tab'}, run_id=run_id,
        )
        guard.on_tool_end('Pressed Tab', run_id=run_id)
        for inputs, encoded in (({'key': 'Enter'}, ''), (None, '{"key":"Return"}')):
            with self.subTest(inputs=inputs), self.assertRaises(Exception) as caught:
                self.guard(read_only=True).on_tool_start(
                    {'name': 'playwright_press_key'}, encoded, inputs=inputs, run_id=uuid4(),
                )
            self.assertEqual(caught.exception.error_kind, 'read_only_violation')

    def test_concrete_disallowed_capabilities_are_still_blocked(self):
        for tool in ('playwright_upload_file', 'playwright_evaluate', 'playwright_close'):
            with self.subTest(tool=tool), self.assertRaises(Exception) as caught:
                self.guard().on_tool_start({'name': tool}, '', inputs={}, run_id=uuid4())
            self.assertEqual(caught.exception.error_kind, 'read_only_violation')
        with self.assertRaises(Exception) as outside:
            self.guard().on_tool_start(
                {'name': 'playwright_navigate'}, '',
                inputs={'url': 'https://outside.test/'}, run_id=uuid4(),
            )
        self.assertEqual(outside.exception.error_kind, 'external_domain_blocked')

    def test_unknown_write_result_and_repeated_operations_still_stop(self):
        guard = self.guard()
        run_id = uuid4()
        guard.on_tool_start({'name': 'playwright_click'}, '', inputs={'selector': '#payment-history'}, run_id=run_id)
        with self.assertRaises(Exception) as unknown:
            guard.on_tool_end('Error: connection closed after dispatch', run_id=run_id)
        self.assertEqual(unknown.exception.error_kind, 'write_result_unknown')

        guard = self.guard()
        for _ in range(2):
            run_id = uuid4()
            guard.on_tool_start({'name': 'playwright_click'}, '', inputs={'selector': '#publish-history'}, run_id=run_id)
            guard.on_tool_end('Clicked successfully', run_id=run_id)
        with self.assertRaises(Exception) as repeated:
            guard.on_tool_start({'name': 'playwright_click'}, '', inputs={'selector': '#publish-history'}, run_id=uuid4())
        self.assertEqual(repeated.exception.error_kind, 'repeated_interaction')

    def test_agent_still_requires_explicit_scope_and_real_page_evidence(self):
        self.assertIn('页面或定位器的名称不等于操作授权', EXPLORATION_SCRIPT_CONSTRAINTS)
        self.assertIn('未授权高风险操作', EXPLORATION_SCRIPT_CONSTRAINTS)
        self.assertIn('无法确认授权范围的动作不要执行', EXPLORATION_SCRIPT_CONSTRAINTS)
