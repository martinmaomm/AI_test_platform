"""Focused AST regression coverage for the AI repair assertion gate."""

from django.test import SimpleTestCase

from .script_repair_policy import validate_assertion_preservation


BASELINE = '''async def run(page):
    target = page.locator("#old")
    expected = "paid"
    await expect(target).to_have_text(expected, timeout=1000)
'''

MODULE_EXPECTED_BASELINE = '''expected = "paid"
async def run(page):
    await expect(page.locator("#status")).to_have_text(expected)
'''


class AssertionPreservationTests(SimpleTestCase):
    def assertBlocked(self, candidate, code):
        blockers = validate_assertion_preservation(BASELINE, candidate)
        self.assertTrue(blockers)
        self.assertIn(code, {item['code'] for item in blockers})

    def test_allows_locator_variable_timeout_comment_and_new_assertion(self):
        candidate = '''async def run(page):
    # 已根据页面证据修正定位器
    target = page.locator("#new")
    expected = "paid"
    await expect(target).to_have_text(expected, timeout=5000)
    await expect(page.locator("main")).to_be_visible()
'''
        self.assertEqual(validate_assertion_preservation(BASELINE, candidate), [])

    def test_blocks_matcher_change_even_when_assertion_count_is_unchanged(self):
        candidate = BASELINE.replace('.to_have_text(expected, timeout=1000)', '.to_be_visible(timeout=1000)')
        self.assertBlocked(candidate, 'ASSERTION_SEMANTICS_CHANGED')

    def test_blocks_expected_value_assignment_bypass(self):
        candidate = BASELINE.replace('expected = "paid"', 'expected = "pending"')
        self.assertBlocked(candidate, 'ASSERTION_EXPECTED_VALUE_CHANGED')

    def test_blocks_module_level_expected_value_assignment_bypass(self):
        candidate = MODULE_EXPECTED_BASELINE.replace('expected = "paid"', 'expected = "pending"')
        blockers = validate_assertion_preservation(MODULE_EXPECTED_BASELINE, candidate)
        self.assertIn('ASSERTION_EXPECTED_VALUE_CHANGED', {item['code'] for item in blockers})

    def test_blocks_return_before_original_assertion(self):
        candidate = BASELINE.replace(
            '    await expect(target).to_have_text(expected, timeout=1000)',
            '    return\n    await expect(target).to_have_text(expected, timeout=1000)',
        )
        self.assertBlocked(candidate, 'ASSERTION_EXECUTION_CHANGED')

    def test_blocks_conditional_return_before_original_assertion(self):
        candidate = BASELINE.replace(
            '    await expect(target).to_have_text(expected, timeout=1000)',
            '    if page.locator("#skip").is_visible():\n        return\n    await expect(target).to_have_text(expected, timeout=1000)',
        )
        self.assertBlocked(candidate, 'ASSERTION_EXECUTION_CHANGED')

    def test_blocks_if_false_around_original_assertion(self):
        candidate = BASELINE.replace(
            '    await expect(target).to_have_text(expected, timeout=1000)',
            '    if False:\n        await expect(target).to_have_text(expected, timeout=1000)',
        )
        self.assertBlocked(candidate, 'ASSERTION_EXECUTION_CHANGED')

    def test_blocks_try_except_that_swallows_original_assertion(self):
        candidate = BASELINE.replace(
            '    await expect(target).to_have_text(expected, timeout=1000)',
            '    try:\n        await expect(target).to_have_text(expected, timeout=1000)\n    except AssertionError:\n        pass',
        )
        self.assertBlocked(candidate, 'ASSERTION_EXECUTION_CHANGED')

    def test_blocks_existing_assertion_handler_changed_from_raise_to_pass(self):
        baseline = '''async def run(page):
    try:
        await expect(page.locator("#status")).to_have_text("paid")
    except AssertionError:
        raise
'''
        candidate = baseline.replace('    except AssertionError:\n        raise', '    except AssertionError:\n        pass')
        blockers = validate_assertion_preservation(baseline, candidate)
        self.assertIn('ASSERTION_EXECUTION_CHANGED', {item['code'] for item in blockers})

    def test_compares_existing_try_finally_without_error(self):
        source = '''async def run(page):
    try:
        await expect(page.locator("#status")).to_have_text("paid")
    finally:
        await page.locator("#close").click()
'''
        self.assertEqual(validate_assertion_preservation(source, source), [])

    def test_duplicate_assertions_prefer_matching_control_and_keep_order(self):
        baseline = '''async def run(page):
    expected = "paid"
    await expect(page.locator("#one")).to_have_text(expected)
    await expect(page.locator("#two")).to_have_text(expected)
'''
        candidate = '''async def run(page):
    expected = "paid"
    if page.locator("#optional").is_visible():
        await expect(page.locator("#extra")).to_have_text(expected)
    await expect(page.locator("#one-new")).to_have_text(expected)
    await expect(page.locator("#two-new")).to_have_text(expected)
'''
        self.assertEqual(validate_assertion_preservation(baseline, candidate), [])

    def test_allows_locator_and_wait_repair_inside_allure_step(self):
        baseline = '''async def run(page):
    with allure.step("新增"):
        target = page.locator("#old")
        await page.wait_for_load_state("networkidle", timeout=1000)
        await expect(target).to_have_text("paid", timeout=1000)
'''
        candidate = '''async def run(page):
    with allure.step("新增"):
        target = page.locator("#new")
        await page.wait_for_load_state("networkidle", timeout=3000)
        await expect(target).to_have_text("paid", timeout=5000)
        await expect(page.locator("main")).to_be_visible()
'''
        self.assertEqual(validate_assertion_preservation(baseline, candidate), [])

    def test_same_matcher_assertions_cannot_be_reordered(self):
        baseline = '''async def run(page):
    first = "paid"
    second = "settled"
    await expect(page.locator("#one")).to_have_text(first)
    await expect(page.locator("#two")).to_have_text(second)
'''
        candidate = '''async def run(page):
    first = "paid"
    second = "settled"
    await expect(page.locator("#two-new")).to_have_text(second)
    await expect(page.locator("#one-new")).to_have_text(first)
'''
        blockers = validate_assertion_preservation(baseline, candidate)
        self.assertIn('ASSERTION_SEMANTICS_CHANGED', {item['code'] for item in blockers})

    def test_allows_a_leading_new_assertion_with_the_same_matcher(self):
        baseline = '''async def run(page):
    expected = "paid"
    await expect(page.locator("#status")).to_have_text(expected)
'''
        candidate = '''async def run(page):
    expected = "paid"
    await expect(page.locator("#heading")).to_have_text("账户")
    await expect(page.locator("#status-new")).to_have_text(expected)
'''
        self.assertEqual(validate_assertion_preservation(baseline, candidate), [])
