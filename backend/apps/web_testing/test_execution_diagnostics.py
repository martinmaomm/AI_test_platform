import unittest

from .execution_diagnostics import diagnose_failure, friendly_failure_summary


class FailureDiagnosticTests(unittest.TestCase):
    def test_click_timeout_extracts_explicit_role_target_and_timeout(self):
        diagnostic = diagnose_failure(
            stdout=(
                'E   playwright._impl._errors.TimeoutError: '
                'Locator.click: Timeout 30000ms exceeded.\n'
                'E   locator resolved to <Locator get_by_role("button", name="登录")>'
            )
        )

        self.assertEqual(diagnostic.category, 'action_timeout')
        self.assertEqual(diagnostic.action, '点击')
        self.assertEqual(diagnostic.target, '按钮“登录”')
        self.assertEqual(diagnostic.timeout_ms, 30000)
        self.assertIn('点击元素超时', diagnostic.summary)
        self.assertIn('按钮“登录”', diagnostic.summary)
        self.assertIn('30 秒', diagnostic.summary)

    def test_fill_timeout_without_explicit_target_does_not_invent_target(self):
        diagnostic = diagnose_failure(
            stdout='E   TimeoutError: Locator.fill: Timeout 5000ms exceeded.'
        )

        self.assertEqual(diagnostic.action, '输入')
        self.assertIsNone(diagnostic.target)
        self.assertNotIn('按钮', diagnostic.summary)
        self.assertIn('输入元素超时', diagnostic.summary)

    def test_assertion_strict_mode_and_navigation_errors_are_classified(self):
        cases = [
            (
                'E   AssertionError: expect(locator).to_be_visible() failed\n'
                'E   Timeout 1000ms exceeded.',
                'assertion_failure',
                '页面校验未通过',
            ),
            (
                'E   strict mode violation: locator("#submit") resolved to 2 elements',
                'strict_mode',
                '定位元素不唯一',
            ),
            (
                'E   Error: page.goto: net::ERR_CONNECTION_REFUSED at http://example.test',
                'navigation_error',
                '页面访问失败',
            ),
        ]
        for raw, category, title in cases:
            with self.subTest(category=category):
                diagnostic = diagnose_failure(stdout=raw)
                self.assertEqual(diagnostic.category, category)
                self.assertIn(title, diagnostic.summary)

    def test_syntax_import_and_unknown_errors_have_safe_fallbacks(self):
        syntax = diagnose_failure(stdout='E   SyntaxError: invalid syntax (test_case.py, line 4)')
        imported = diagnose_failure(stderr='ModuleNotFoundError: No module named "pages.login"')
        unknown = diagnose_failure(stdout='some unrelated output')

        self.assertEqual(syntax.category, 'script_error')
        self.assertEqual(imported.category, 'script_error')
        self.assertEqual(unknown.category, 'unknown')
        self.assertIn('测试脚本', syntax.summary)
        self.assertIn('测试脚本', imported.summary)
        self.assertIn('技术日志', unknown.summary)

    def test_unknown_technical_fallback_is_not_exposed_as_user_summary(self):
        summary = friendly_failure_summary(
            fallback='playwright._impl._errors.TimeoutError: raw technical failure'
        )
        self.assertIn('测试执行失败', summary)
        self.assertNotIn('playwright._impl', summary)


if __name__ == '__main__':
    unittest.main()
