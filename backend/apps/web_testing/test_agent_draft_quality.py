"""Offline checks for the script-first workflow, without a model or database."""

import ast

from django.test import SimpleTestCase

from .assertion_state import analyze_assertion_state, evaluation_status
from .draft_quality import _static_entry_goto, evaluate_draft
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
        script = SCRIPT + '\n    # PENDING_STEP: {"reason":"删除尚未探索"}\n'
        result = evaluate_draft(script)
        self.assertEqual(result['blockers'], [])
        self.assertEqual(result['completion'], 'partial')
        state = analyze_assertion_state(script)
        self.assertEqual(state['pending'][0]['kind'], 'step')
        status, _, _ = evaluation_status(script, operation_success=True, runtime_assertion_count=1)
        self.assertEqual(status, 'incomplete')

    def test_pending_quality_warning_distinguishes_steps_and_assertions(self):
        script = SCRIPT + '\n    # PENDING_STEP: {"reason":"补充操作"}\n    # PENDING_ASSERTION: {"reason":"补充断言"}\n'
        warning = next(item for item in evaluate_draft(script)['warnings'] if item['code'] == 'PENDING_WORK')
        self.assertIn('1 项步骤', warning['message'])
        self.assertIn('1 项断言', warning['message'])

    def test_pending_comment_in_string_does_not_change_status(self):
        script = SCRIPT + "\n    example = '# PENDING_STEP: 示例文字'\n"
        self.assertEqual(evaluate_draft(script)['completion'], 'complete')

    def test_actual_failure_wins_over_pending(self):
        script = SCRIPT + '\n    # PENDING_STEP: 未完成\n'
        self.assertEqual(evaluation_status(script, operation_success=False, runtime_assertion_count=1)[0], 'failed')

    def test_missing_navigation_cannot_be_saved_as_executable_case(self):
        report = evaluate_draft(SCRIPT.replace("    await page.goto('https://example.test/')\n", ''))
        self.assertIn('ENTRY_NAVIGATION_MISSING', [item['code'] for item in report['blockers']])

    def test_dangerous_imports_and_calls_are_blocked(self):
        for addition in ['    eval("1+1")', '    open("/tmp/out", "w")', '    await page.evaluate("1+1")']:
            self.assertTrue(evaluate_draft(SCRIPT + '\n' + addition)['blockers'])
        self.assertTrue(evaluate_draft('import subprocess\n' + SCRIPT)['blockers'])

    def test_allure_import_is_not_part_of_the_script_contract(self):
        report = evaluate_draft('import allure\n' + SCRIPT)
        self.assertIn('IMPORT_NOT_ALLOWED', [item['code'] for item in report['blockers']])

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
        self.assertIn('TARGET_URL_CHANGED', [item['code'] for item in changed['blockers']])

    def test_only_statically_ordered_entry_must_match_full_target_url(self):
        target = 'https://example.test/start?mode=test#/entry'
        script = SCRIPT.replace(
            "    await page.goto('https://example.test/')",
            "    await page.goto('https://example.test/start?mode=test#/entry')\n"
            "    await page.goto('https://example.test/legitimate-next-page')",
        )
        report = evaluate_draft(script, target_url=target)
        self.assertNotIn('TARGET_URL_CHANGED', [item['code'] for item in report['blockers']])

        changed = script.replace(target, 'https://example.test/start?mode=other#/entry', 1)
        report = evaluate_draft(changed, target_url=target)
        self.assertIn('TARGET_URL_CHANGED', [item['code'] for item in report['blockers']])

    def test_sequential_local_string_entry_and_alias_keep_exact_target(self):
        target = 'https://example.test/start?mode=test#/entry'
        for statements in (
            f"target_url = {target!r}\n    await page.goto(target_url)",
            f"target_url: str = {target!r}\n    await page.goto(url=target_url)",
            f"entry = {target!r}\n    target_url = entry\n    await page.goto(target_url)",
        ):
            with self.subTest(statements=statements):
                script = SCRIPT.replace("await page.goto('https://example.test/')", statements)
                report = evaluate_draft(script, target_url=target)
                self.assertEqual(report['blockers'], [])
                self.assertEqual(report['warnings'], [])
                tree = ast.parse(script)
                original = ast.dump(tree, include_attributes=True)
                status, call, resolved = _static_entry_goto(tree)
                self.assertEqual(status, 'resolved')
                self.assertEqual(resolved, target)
                self.assertIsInstance(call.args[0] if call.args else call.keywords[0].value, ast.Name)
                self.assertEqual(ast.dump(tree, include_attributes=True), original)

    def test_local_string_entry_preserves_url_path_query_fragment_and_origin_checks(self):
        target = 'https://example.test/start?mode=test#/entry'
        for url, code in (
            ('https://example.test/other?mode=test#/entry', 'TARGET_URL_CHANGED'),
            ('https://example.test/start?mode=other#/entry', 'TARGET_URL_CHANGED'),
            ('https://example.test/start?mode=test#/other', 'TARGET_URL_CHANGED'),
            ('https://outside.test/start?mode=test#/entry', 'NAVIGATION_OUTSIDE_TARGET'),
            ('http://example.test/start?mode=test#/entry', 'NAVIGATION_OUTSIDE_TARGET'),
            ('https://example.test:8443/start?mode=test#/entry', 'NAVIGATION_OUTSIDE_TARGET'),
            ('/start?mode=test#/entry', 'ABSOLUTE_URL_REQUIRED'),
            ('', 'ABSOLUTE_URL_REQUIRED'),
        ):
            with self.subTest(url=url):
                script = SCRIPT.replace(
                    "await page.goto('https://example.test/')",
                    f'target_url = {url!r}\n    await page.goto(target_url)',
                )
                report = evaluate_draft(script, target_url=target)
                self.assertIn(code, [item['code'] for item in report['blockers']])

    def test_unknown_or_reassigned_local_entry_is_still_unconfirmed(self):
        for prefix in (
            "target_url = variables['URL']",
            "target_url = 'https://example.test/'\n    target_url = variables['URL']",
            "target_url = 'https://example.test/'\n    target_url = 'https://example.test/'",
            "entry = variables['URL']\n    target_url = entry",
            "target_url = 'https://example.test/'\n    target_url += '/next'",
            "target_url = 'https://example.test/'\n    other, target_url = ('x', variables['URL'])",
            "target_url = 'https://example.test/'\n    print(target_url := variables['URL'])",
        ):
            with self.subTest(prefix=prefix):
                script = SCRIPT.replace(
                    "await page.goto('https://example.test/')",
                    prefix + '\n    await page.goto(target_url)',
                )
                report = evaluate_draft(script, target_url='https://example.test/')
                codes = [item['code'] for item in report['warnings']]
                self.assertIn('ENTRY_NAVIGATION_UNCONFIRMED', codes)
                self.assertIn('DYNAMIC_NAVIGATION_REVIEW', codes)

    def test_branch_loop_and_import_shadow_cannot_certify_local_entry(self):
        for prefix in (
            "if True:\n        target_url = 'https://example.test/'",
            "target_url = 'https://example.test/'\n    if variables:\n        target_url = variables['URL']",
            "target_url = 'https://example.test/'\n    for target_url in variables.values():\n        pass",
            "target_url = 'https://example.test/'\n    import time as target_url",
            "target_url = 'https://example.test/'\n    from os import getenv as target_url",
        ):
            with self.subTest(prefix=prefix):
                script = SCRIPT.replace(
                    "await page.goto('https://example.test/')",
                    prefix + '\n    await page.goto(target_url)',
                )
                report = evaluate_draft(script, target_url='https://example.test/')
                self.assertIn('ENTRY_NAVIGATION_UNCONFIRMED', [item['code'] for item in report['warnings']])

    def test_helper_local_constants_are_isolated_from_caller(self):
        target = 'https://example.test/expected'
        script = '''async def open_entry(page):
    target_url = 'https://example.test/other'
    await page.goto(target_url)

async def run(page):
    target_url = 'https://example.test/expected'
    await open_entry(page)
    assert True
'''
        report = evaluate_draft(script, target_url=target)
        self.assertIn('TARGET_URL_CHANGED', [item['code'] for item in report['blockers']])

        # A helper with no navigation must not overwrite its caller's binding.
        script = script.replace('    await page.goto(target_url)', '    return target_url').replace(
            '    assert True', '    await page.goto(target_url)\n    assert True',
        )
        report = evaluate_draft(script, target_url=target)
        self.assertEqual(report['blockers'], [])
        self.assertNotIn('ENTRY_NAVIGATION_UNCONFIRMED', [item['code'] for item in report['warnings']])

    def test_helper_arguments_and_global_constants_are_not_inferred_from_caller(self):
        for helper in (
            'async def open_entry(page, target_url):',
            'async def open_entry(page, ignored):',
        ):
            with self.subTest(helper=helper):
                script = f'''target_url = 'https://example.test/global'
{helper}
    await page.goto(target_url)

async def run(page):
    target_url = 'https://example.test/expected'
    await open_entry(page, target_url)
    assert True
'''
                report = evaluate_draft(script, target_url='https://example.test/expected')
                self.assertIn('ENTRY_NAVIGATION_UNCONFIRMED', [item['code'] for item in report['warnings']])

    def test_shadowed_aliased_and_recursive_helpers_remain_unconfirmed(self):
        helper = '''async def open_entry(page):
    target_url = 'https://example.test/'
    await page.goto(target_url)

'''
        for call in (
            'import time as open_entry\n    await open_entry(page)',
            'from time import time as open_entry\n    await open_entry(page)',
            'navigate = open_entry\n    await navigate(page)',
            'await run(page)',
        ):
            with self.subTest(call=call):
                script = helper + 'async def run(page):\n    ' + call + '\n    assert True\n'
                report = evaluate_draft(script, target_url='https://example.test/')
                self.assertIn('ENTRY_NAVIGATION_UNCONFIRMED', [item['code'] for item in report['warnings']])

    def test_constant_resolution_only_confirms_the_first_goto(self):
        script = SCRIPT.replace(
            "await page.goto('https://example.test/')",
            "target_url = 'https://example.test/'\n    await page.goto(target_url)\n"
            "    await page.goto(variables['NEXT_URL'])",
        )
        report = evaluate_draft(script, target_url='https://example.test/')
        self.assertEqual(report['blockers'], [])
        self.assertNotIn('ENTRY_NAVIGATION_UNCONFIRMED', [item['code'] for item in report['warnings']])
        warnings = [item for item in report['warnings'] if item['code'] == 'DYNAMIC_NAVIGATION_REVIEW']
        self.assertEqual(len(warnings), 1)
        self.assertIn("variables['NEXT_URL']", script.splitlines()[warnings[0]['line'] - 1])

    def test_source_earlier_unused_helper_goto_is_not_the_entry(self):
        target = 'https://example.test/start?mode=test#/entry'
        helper = '''async def unused_helper(page):
    await page.goto("https://example.test/not-the-entry")

'''
        script = helper + SCRIPT.replace('https://example.test/', target)
        report = evaluate_draft(script, target_url=target)
        self.assertNotIn('TARGET_URL_CHANGED', [item['code'] for item in report['blockers']])

    def test_simple_called_helper_entry_is_checked_but_complex_entry_is_unconfirmed(self):
        helper_script = '''async def open_entry(page):
    await page.goto("https://example.test/wrong")

async def run(page):
    await open_entry(page)
    assert True
'''
        report = evaluate_draft(helper_script, target_url='https://example.test/expected')
        self.assertIn('TARGET_URL_CHANGED', [item['code'] for item in report['blockers']])

        complex_script = helper_script.replace(
            '    await page.goto("https://example.test/wrong")',
            '    if True:\n        await page.goto("https://example.test/wrong")',
        )
        report = evaluate_draft(complex_script, target_url='https://example.test/expected')
        self.assertNotIn('TARGET_URL_CHANGED', [item['code'] for item in report['blockers']])
        self.assertIn('ENTRY_NAVIGATION_UNCONFIRMED', [item['code'] for item in report['warnings']])

    def test_uncertain_helper_execution_cannot_certify_a_later_literal_entry(self):
        helper = '''async def open_entry(page):
    await page.goto("https://example.test/other")

'''
        for prefix in (
            '    if True:\n        await open_entry(page)\n',
            '    open_entry(page)\n',
            '    navigate = open_entry\n    await navigate(page)\n',
        ):
            with self.subTest(prefix=prefix):
                script = helper + 'async def run(page):\n' + prefix + (
                    '    await page.goto("https://example.test/expected")\n'
                    '    assert True\n'
                )
                report = evaluate_draft(script, target_url='https://example.test/expected')
                self.assertNotIn('TARGET_URL_CHANGED', [item['code'] for item in report['blockers']])
                self.assertIn('ENTRY_NAVIGATION_UNCONFIRMED', [item['code'] for item in report['warnings']])

    def test_unreachable_goto_after_return_is_not_an_entry(self):
        report = evaluate_draft('''async def run(page):
    return
    await page.goto("https://example.test/expected")
    assert True
''', target_url='https://example.test/expected')
        self.assertIn('ENTRY_NAVIGATION_MISSING', [item['code'] for item in report['blockers']])

    def test_no_assertion_is_editable_but_not_complete(self):
        script = SCRIPT.replace("    await expect(page.locator('#result')).to_have_text(name)\n", '')
        report = evaluate_draft(script)
        self.assertEqual(report['blockers'], [])
        self.assertEqual(report['completion'], 'partial')

    def test_broken_syntax_preserves_actionable_issue(self):
        report = evaluate_draft('async def run(page, variables):\n    await page.goto(')
        self.assertEqual(report['status'], 'needs_review')
        self.assertEqual(report['blockers'][0]['code'], 'SCRIPT_CONTRACT_INVALID')
