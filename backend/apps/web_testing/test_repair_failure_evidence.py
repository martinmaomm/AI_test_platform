"""Offline regressions for bounded, evidence-led AI repair routing."""

from django.test import SimpleTestCase

from .ai_assisted_debugging import (
    MAX_REPAIR_LOG_CHARS,
    failure_evidence,
    is_non_code_failure,
    requires_directed_mcp,
)


class RepairFailureEvidenceTests(SimpleTestCase):
    def test_tail_failure_after_large_prefix_still_directs_mcp(self):
        evidence = failure_evidence(log=(
            ('historical diagnostic output\n' * 700)
            + 'test_case.py::test_submit FAILED\n'
            + 'E   playwright._impl._errors.TimeoutError: Locator.click: Timeout 30000ms exceeded.\n'
            + 'E   Call log:\n'
            + 'E   - waiting for get_by_role("button", name="提交")\n'
        ))

        self.assertEqual(evidence['category'], 'action_timeout')
        self.assertTrue(requires_directed_mcp(evidence))
        self.assertLessEqual(len(evidence['log']), MAX_REPAIR_LOG_CHARS)
        self.assertIn('Locator.click: Timeout 30000ms exceeded.', evidence['technical_message'])

    def test_failure_excerpt_keeps_exception_and_tail_after_large_captured_output(self):
        evidence = failure_evidence(log=(
            'test_case.py::test_submit FAILED\n'
            'E   playwright._impl._errors.TimeoutError: Locator.click: Timeout 30000ms exceeded.\n'
            'E   Call log:\n'
            'E   - waiting for get_by_role("button", name="提交")\n'
            'Captured stdout call ------------------------------\n'
            + ('later captured output\n' * 900)
            + 'CAPTURED_OUTPUT_TAIL\n'
        ))

        self.assertEqual(evidence['category'], 'action_timeout')
        self.assertLessEqual(len(evidence['log']), MAX_REPAIR_LOG_CHARS)
        self.assertIn('Locator.click: Timeout 30000ms exceeded.', evidence['log'])
        self.assertIn('Call log:', evidence['log'])
        self.assertIn('CAPTURED_OUTPUT_TAIL', evidence['log'])

    def test_negative_login_assertion_stays_repairable(self):
        evidence = failure_evidence(log=(
            'test_case.py::test_negative_login FAILED\n'
            'E   AssertionError: expect(locator).to_be_visible() failed\n'
            'E   Actual value: <element hidden>\n'
            'E   Call log:\n'
            'E   - expect(get_by_text("账号或密码错误")).to_be_visible()\n'
        ))

        self.assertEqual(evidence['category'], 'assertion_failure')
        self.assertFalse(is_non_code_failure(evidence))
        self.assertTrue(requires_directed_mcp(evidence))

    def test_real_environment_authentication_and_cancellation_failures_stop_repair(self):
        cases = (
            'E   Error: page.goto: net::ERR_CONNECTION_REFUSED at http://example.test',
            'E   playwright._impl._errors.Error: BrowserType.launch: Executable doesn\'t exist',
            'E   AuthenticationError: invalid credentials',
            'E   asyncio.exceptions.CancelledError: task_cancelled',
        )
        for raw in cases:
            with self.subTest(raw=raw):
                evidence = failure_evidence(log=raw)
                self.assertTrue(is_non_code_failure(evidence))

    def test_explicit_network_exception_primaries_stop_repair(self):
        cases = (
            'E   requests.exceptions.ConnectionError: connection refused',
            'E   OSError: [Errno 51] Network is unreachable',
        )
        for raw in cases:
            with self.subTest(raw=raw):
                evidence = failure_evidence(log=raw)
                self.assertEqual(evidence['category'], 'network_error')
                self.assertTrue(is_non_code_failure(evidence))

    def test_source_or_business_text_with_non_code_markers_is_not_a_failure_signal(self):
        evidence = failure_evidence(fallback=(
            'await expect(page.get_by_text("账号或密码错误")).to_be_visible()\n'
            'message = "authentication failed"\n'
            'locator = page.get_by_text("invalid credentials")\n'
        ))

        self.assertEqual(evidence['category'], 'unknown')
        self.assertFalse(is_non_code_failure(evidence))

    def test_fallback_technical_failures_are_classified_without_promoting_business_copy(self):
        locator = failure_evidence(
            fallback='playwright._impl._errors.TimeoutError: Locator.click: Timeout 5000ms exceeded.',
        )
        browser = failure_evidence(
            fallback='BrowserType.launch: Executable doesn\'t exist at /tmp/chromium',
        )
        business_copy = failure_evidence(
            fallback='账号或密码错误，请检查后重试。',
        )

        self.assertEqual(locator['category'], 'action_timeout')
        self.assertTrue(requires_directed_mcp(locator))
        self.assertEqual(browser['category'], 'browser_unavailable')
        self.assertTrue(is_non_code_failure(browser))
        self.assertEqual(business_copy['category'], 'unknown')
        self.assertFalse(is_non_code_failure(business_copy))

    def test_all_model_output_is_bounded_and_runtime_values_are_redacted(self):
        secret = 'repair-runtime-secret'
        evidence = failure_evidence(
            stdout=('prefix ' + secret + '\n') * 900,
            stderr=(
                'E   playwright._impl._errors.TimeoutError: Locator.click: Timeout 1000ms exceeded.\n'
                f'E   Call log: {secret}\n'
            ),
            log=('tail ' + secret + '\n') * 900,
            runtime_variables=[{'name': 'token', 'value': secret, 'is_secret': True}],
        )

        for key in ('summary', 'technical_message', 'stdout', 'stderr', 'log'):
            self.assertNotIn(secret, evidence[key])
        for key in ('stdout', 'stderr', 'log'):
            self.assertLessEqual(len(evidence[key]), MAX_REPAIR_LOG_CHARS)
