"""Offline regression coverage for runner-owned assertion progress stdout."""

from __future__ import annotations

import io
import sys
import tempfile
import types
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from .assertion_state import read_runtime_assertion_count
from .script_contract import materialize_script


class _PassingExpectation:
    async def to_be_visible(self):
        return None


class _FailingExpectation:
    async def to_be_visible(self):
        raise AssertionError('expected fixture failure')


def _fake_playwright_modules(*, page_factory=_PassingExpectation, context_close_error=None,
                             browser_close_error=None, playwright_exit_error=None):
    package = types.ModuleType('playwright')
    async_api = types.ModuleType('playwright.async_api')

    class FakePage(page_factory):
        async def screenshot(self, **_kwargs):
            return None

    class FakeContext:
        async def new_page(self):
            return FakePage()

        async def close(self):
            if context_close_error:
                raise context_close_error

    class FakeBrowser:
        async def new_context(self, **_kwargs):
            return FakeContext()

        async def close(self):
            if browser_close_error:
                raise browser_close_error

    class FakeBrowserType:
        async def launch(self, **_kwargs):
            return FakeBrowser()

    class FakePlaywright:
        chromium = FakeBrowserType()

    class FakeAsyncPlaywright:
        async def __aenter__(self):
            return FakePlaywright()

        async def __aexit__(self, *_args):
            if playwright_exit_error:
                raise playwright_exit_error
            return False

    async_api.expect = lambda value: value
    async_api.async_playwright = lambda: FakeAsyncPlaywright()
    package.async_api = async_api
    return {'playwright': package, 'playwright.async_api': async_api}


class AssertionProgressLogTests(unittest.TestCase):
    def _execute(self, source, *, modules=None, test_name='test_assertion_progress'):
        modules = modules or _fake_playwright_modules()
        with tempfile.TemporaryDirectory() as directory, patch.dict(sys.modules, modules, clear=False):
            count_path = str(Path(directory) / 'runtime_assertions.json')
            namespace = {'__name__': 'assertion_progress_fixture'}
            exec(materialize_script(
                source,
                test_name,
                headed=False,
                runtime_assertion_count_path=count_path,
            ), namespace)
            output = io.StringIO()
            with redirect_stdout(output):
                try:
                    namespace[test_name]()
                except BaseException as error:
                    return output.getvalue().splitlines(), read_runtime_assertion_count(count_path), error
            return output.getvalue().splitlines(), read_runtime_assertion_count(count_path), None

    def test_chinese_labels_and_fallback_log_once_after_success_without_re_evaluation(self):
        source = '''from playwright.async_api import expect

calls = []

def only_once():
    calls.append('called')
    if len(calls) != 1:
        raise RuntimeError('assertion expression was evaluated again')
    return True

async def run(page):
    # 验证：筛选结果已刷新
    assert only_once()
    await expect(page).to_be_visible()
'''
        lines, count, error = self._execute(source)

        self.assertIsNone(error)
        self.assertEqual(count, 2)
        self.assertEqual(lines, [
            '验证 筛选结果已刷新 通过',
            '验证 第 14 行的页面断言 通过',
            '测试用例执行完毕',
        ])
        self.assertNotIn('None', '\n'.join(lines))

    def test_loop_and_unexecuted_branch_follow_actual_execution_count(self):
        source = '''async def run(page):
    for _ in range(2):
        # 验证：循环内结果有效
        assert page is not None
    if False:
        # 验证：不会执行的分支
        assert page is None
'''
        lines, count, error = self._execute(source)

        self.assertIsNone(error)
        self.assertEqual(count, 2)
        self.assertEqual(lines, [
            '验证 循环内结果有效 通过',
            '验证 循环内结果有效 通过',
            '测试用例执行完毕',
        ])

    def test_function_local_expect_import_uses_the_same_single_progress_proxy(self):
        source = '''async def run(page):
    from playwright.async_api import expect
    # 验证：函数内导入的页面结果
    await expect(page).to_be_visible()
'''
        lines, count, error = self._execute(source, test_name='test_local_expect_import')

        self.assertIsNone(error)
        self.assertEqual(count, 1)
        self.assertEqual(lines, [
            '验证 函数内导入的页面结果 通过',
            '测试用例执行完毕',
        ])

    def test_failure_logs_no_success_for_failed_assertion_and_no_completion(self):
        source = '''async def run(page):
    # 验证：前置条件正确
    assert page is not None
    # 验证：第二项条件正确
    assert page is not None
    # 验证：第三项失败条件
    assert page is None
'''
        lines, count, error = self._execute(source)

        self.assertIsInstance(error, AssertionError)
        self.assertEqual(count, 2)
        self.assertEqual(lines, [
            '验证 前置条件正确 通过',
            '验证 第二项条件正确 通过',
        ])

    def test_caught_assertion_or_expectation_failure_blocks_whole_case_completion(self):
        source = '''from playwright.async_api import expect

async def run(page):
    try:
        # 验证：可恢复断言
        assert page is None
    except AssertionError:
        pass
    # 验证：后续断言通过
    assert page is not None
    try:
        # 验证：可恢复页面断言
        await expect(page).to_be_visible()
    except AssertionError:
        pass
'''
        lines, count, error = self._execute(
            source,
            modules=_fake_playwright_modules(page_factory=_FailingExpectation),
        )

        self.assertIsNone(error)
        self.assertEqual(count, 1)
        self.assertEqual(lines, ['验证 后续断言通过 通过'])

    def test_caught_literal_assertion_or_expression_error_also_blocks_completion(self):
        source = '''def raises_while_evaluating():
    raise RuntimeError('fixture condition error')

async def run(page):
    try:
        assert False
    except AssertionError:
        pass
    try:
        # 验证：表达式求值
        assert raises_while_evaluating()
    except RuntimeError:
        pass
    # 验证：仍可继续的结果正确
    assert page is not None
'''
        lines, count, error = self._execute(source, test_name='test_caught_literal_or_expression_error')

        self.assertIsNone(error)
        self.assertEqual(count, 1)
        self.assertEqual(lines, ['验证 仍可继续的结果正确 通过'])

    def test_pending_marker_or_no_real_assertion_never_emits_completion(self):
        pending_source = '''async def run(page):
    # 验证：页面对象可用
    assert page is not None
    # AITS_PENDING_STEP: {"reason":"尚未确认清理结果"}
    # AITS_PENDING_ASSERTION: {"reason":"尚未确认完整结果"}
'''
        pending_lines, pending_count, pending_error = self._execute(pending_source, test_name='test_pending')
        no_assertion_lines, no_assertion_count, no_assertion_error = self._execute(
            'async def run(page):\n    return None\n', test_name='test_no_assertion',
        )

        self.assertIsNone(pending_error)
        self.assertEqual(pending_count, 1)
        self.assertEqual(pending_lines, ['验证 页面对象可用 通过'])
        self.assertIsNone(no_assertion_error)
        self.assertEqual(no_assertion_count, 0)
        self.assertEqual(no_assertion_lines, [])

    def test_normal_return_emits_completion_only_after_the_assertion(self):
        source = '''async def run(page):
    # 验证：返回前条件正确
    assert page is not None
    return None
'''
        lines, count, error = self._execute(source, test_name='test_normal_return')

        self.assertIsNone(error)
        self.assertEqual(count, 1)
        self.assertEqual(lines, ['验证 返回前条件正确 通过', '测试用例执行完毕'])

    def test_cleanup_failure_never_emits_completion_after_return(self):
        source = '''async def run(page):
    # 验证：正常返回前断言
    assert page is not None
    return None
'''
        for modules, expected_error in (
            (_fake_playwright_modules(context_close_error=RuntimeError('context close failure')), 'context close failure'),
            (_fake_playwright_modules(playwright_exit_error=RuntimeError('playwright exit failure')), 'playwright exit failure'),
        ):
            with self.subTest(expected_error=expected_error):
                lines, count, error = self._execute(
                    source, modules=modules, test_name='test_cleanup_failure',
                )
                self.assertIsInstance(error, RuntimeError)
                self.assertEqual(str(error), expected_error)
                self.assertEqual(count, 1)
                self.assertEqual(lines, ['验证 正常返回前断言 通过'])
