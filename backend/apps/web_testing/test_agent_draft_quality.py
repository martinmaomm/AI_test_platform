"""Offline checks for the script-first workflow, without a model or database."""

from django.test import SimpleTestCase

from .assertion_state import analyze_assertion_state, evaluation_status
from .draft_quality import evaluate_draft
from .generation_brief import build_generation_brief


SCRIPT = '''"""场景：编辑展示名称。目标：保存后展示新名称。"""
import time
from playwright.async_api import expect

async def run(page, variables):
    # 打开目标页面
    await page.goto('https://example.test/')
    # 使用本轮唯一数据填表
    name = variables.get('NAME') or f'user_{time.time_ns()}'
    await page.get_by_label('Display name').fill(name)
    # 根据真实页面操作并验证
    await page.get_by_role('button', name='Save').click()
    await expect(page.locator('#result')).to_have_text(name)
'''


class GenerationBriefTests(SimpleTestCase):
    def test_preserves_goal_without_predeclared_assertions(self):
        text = '打开页面并探索客户查询，页面断言由探索确认。'
        brief = build_generation_brief(text)
        self.assertEqual(brief.original_user_target, text)
        self.assertEqual(brief.model_dump()['schema_version'], 5)
        self.assertNotIn('assertion_requirements', brief.model_dump())
        self.assertNotIn('credentials_required', brief.model_dump())

    def test_no_site_specific_action_vocabulary(self):
        for text in ['探索工作流归档与恢复', 'Explore the telescope calibration panel', '搜索并查看商品']:
            self.assertEqual(build_generation_brief(text).objective, text)

    def test_explicit_read_only_wins(self):
        self.assertFalse(build_generation_brief('只读探索，不要提交数据').allow_test_data_writes)
        self.assertTrue(build_generation_brief('探索测试流程并创建临时记录').allow_test_data_writes)


class AgentDraftQualityTests(SimpleTestCase):
    def test_script_without_finalization_or_plan_is_accepted(self):
        result = evaluate_draft(SCRIPT, snapshot={'schema_version': 5, 'events': [{'action': 'navigate'}]})
        self.assertEqual(result['blockers'], [])
        self.assertEqual(result['completion'], 'complete')

    def test_constants_and_helper_logic_do_not_require_variable_declarations(self):
        script = SCRIPT.replace("name = variables.get('NAME') or f'user_{time.time_ns()}'", "name = '固定枚举值'")
        self.assertEqual(evaluate_draft(script)['blockers'], [])

    def test_top_comment_is_also_a_valid_scene_description(self):
        script = SCRIPT.replace('"""场景：编辑展示名称。目标：保存后展示新名称。"""', '# 场景：编辑展示名称。目标：保存后展示新名称。')
        self.assertNotIn('SCENARIO_DESCRIPTION_MISSING', [item['code'] for item in evaluate_draft(script)['warnings']])

    def test_pending_step_prevents_false_pass_with_successful_assertion(self):
        script = SCRIPT + '\n    # AITS_PENDING_STEP: {"reason":"删除尚未探索"}\n'
        result = evaluate_draft(script)
        self.assertEqual(result['blockers'], [])
        self.assertEqual(result['completion'], 'partial')
        state = analyze_assertion_state(script)
        self.assertEqual(state['pending'][0]['kind'], 'step')
        status, _, _ = evaluation_status(script, operation_success=True, runtime_assertion_count=1)
        self.assertEqual(status, 'incomplete')

    def test_pending_comment_in_string_does_not_change_status(self):
        script = SCRIPT + "\n    example = '# AITS_PENDING_STEP: 示例文字'\n"
        self.assertEqual(evaluate_draft(script)['completion'], 'complete')

    def test_actual_failure_wins_over_pending(self):
        script = SCRIPT + '\n    # AITS_PENDING_STEP: 未完成\n'
        self.assertEqual(evaluation_status(script, operation_success=False, runtime_assertion_count=1)[0], 'failed')

    def test_missing_navigation_cannot_be_saved_as_executable_case(self):
        report = evaluate_draft(SCRIPT.replace("    await page.goto('https://example.test/')\n", ''))
        self.assertIn('ENTRY_NAVIGATION_MISSING', [item['code'] for item in report['blockers']])

    def test_dangerous_imports_and_calls_are_blocked(self):
        for addition in ['    eval("1+1")', '    open("/tmp/out", "w")', '    await page.evaluate("1+1")']:
            self.assertTrue(evaluate_draft(SCRIPT + '\n' + addition)['blockers'])
        self.assertTrue(evaluate_draft('import subprocess\n' + SCRIPT)['blockers'])

    def test_known_os_alias_cannot_execute_command(self):
        script = 'from os import system as execute\n' + SCRIPT + '\n    execute("echo hello")\n'
        self.assertIn('UNSAFE_OS_OPERATION', [item['code'] for item in evaluate_draft(script)['blockers']])

    def test_environment_configuration_read_is_allowed(self):
        script = SCRIPT.replace('import time', 'import time\nimport os').replace("variables.get('NAME')", "os.getenv('UI_NAME')")
        self.assertEqual(evaluate_draft(script)['blockers'], [])

    def test_undefined_global_is_blocked(self):
        report = evaluate_draft(SCRIPT.replace('name=name', 'name=missing_name').replace('to_have_text(name)', 'to_have_text(missing_name)'))
        self.assertIn('UNDEFINED_NAME', [item['code'] for item in report['blockers']])

    def test_external_navigation_and_top_level_execution_are_blocked(self):
        report = evaluate_draft(SCRIPT.replace("page.goto('https://example.test/')", "page.goto('https://outside.test/')"), target_url='https://example.test/')
        self.assertIn('NAVIGATION_OUTSIDE_TARGET', [item['code'] for item in report['blockers']])
        self.assertIn('TOP_LEVEL_EXECUTION', [item['code'] for item in evaluate_draft(SCRIPT + '\nprint("import work")')['blockers']])

    def test_relative_navigation_is_not_a_complete_address(self):
        for navigation in ("page.goto('/')", "page.goto(url='/catalog')"):
            report = evaluate_draft(SCRIPT.replace("page.goto('https://example.test/')", navigation))
            self.assertTrue(report['blockers'])

    def test_exact_entry_including_query_and_hash_is_preserved(self):
        target = 'https://example.test/ui?mode=test#/catalog'
        script = SCRIPT.replace('https://example.test/', target)
        report = evaluate_draft(script, target_url=target)
        self.assertEqual(report['blockers'], [])
        self.assertNotIn('TARGET_URL_CHANGED', [item['code'] for item in report['warnings']])
        changed = evaluate_draft(script, target_url='https://example.test/other')
        self.assertIn('TARGET_URL_CHANGED', [item['code'] for item in changed['warnings']])

    def test_no_assertion_is_editable_but_not_complete(self):
        script = SCRIPT.replace("    await expect(page.locator('#result')).to_have_text(name)\n", '')
        report = evaluate_draft(script)
        self.assertEqual(report['blockers'], [])
        self.assertEqual(report['completion'], 'partial')

    def test_broken_syntax_preserves_actionable_issue(self):
        report = evaluate_draft('async def run(page, variables):\n    await page.goto(')
        self.assertEqual(report['status'], 'needs_review')
        self.assertEqual(report['blockers'][0]['code'], 'SCRIPT_CONTRACT_INVALID')
