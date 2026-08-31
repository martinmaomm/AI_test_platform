"""Offline regression coverage for repairs that must not weaken a test."""

from unittest import TestCase

from .script_repair_policy import validate_targeted_repair


SCRIPT = '''from playwright.async_api import expect

async def run(page):
    user_name = "generated_test_user"
    try:
        await page.goto('/users')
        await page.get_by_role('button', name='添加').click()
        await page.get_by_label('账号').fill(user_name)
        await expect(page.get_by_role('heading')).to_have_text('用户列表')
    finally:
        await page.get_by_role('button', name='关闭').click()
'''

SNAPSHOT = {
    'elements': [{
        'visible_name': '新增',
        'candidate_locators': ["page.get_by_role('button', name='新增', exact=True)", "button[data-action='add']"],
    }],
    'step_evidence': {'S1': {'status': 'confirmed', 'paths': ['/users'], 'element_names': ['新增']}},
}


class TargetedRepairPolicyTests(TestCase):
    def check(self, candidate, *, blocked, snapshot=SNAPSHOT):
        self.assertEqual(bool(validate_targeted_repair(SCRIPT, candidate, snapshot)), blocked)

    def test_comments_and_standard_import_are_allowed(self):
        self.check('import time\n' + SCRIPT.replace('    user_name', '    # 步骤：创建本轮测试数据\n    user_name'), blocked=False)

    def test_evidenced_locator_change_is_allowed(self):
        self.check(SCRIPT.replace("name='添加'", "name='新增', exact=True"), blocked=False)

    def test_css_evidenced_locator_change_is_allowed(self):
        self.check(SCRIPT.replace("page.get_by_role('button', name='添加')", 'page.locator("button[data-action=\'add\']")'), blocked=False)

    def test_unobserved_locator_is_rejected(self):
        self.check(SCRIPT.replace("name='添加'", "name='新增', exact=True"), blocked=True, snapshot={})

    def test_unresolved_element_is_not_evidence(self):
        snapshot = {**SNAPSHOT, 'step_evidence': {'S1': {'status': 'unresolved', 'paths': [], 'element_names': ['新增']}}}
        self.check(SCRIPT.replace("name='添加'", "name='新增', exact=True"), blocked=True, snapshot=snapshot)

    def test_changing_assertion_or_expected_value_is_rejected(self):
        self.check(SCRIPT.replace("to_have_text('用户列表')", 'to_be_visible()'), blocked=True)
        self.check(SCRIPT.replace("to_have_text('用户列表')", "to_have_text('错误页面')"), blocked=True)

    def test_removing_assertion_is_rejected(self):
        self.check(SCRIPT.replace("        await expect(page.get_by_role('heading')).to_have_text('用户列表')\n", ''), blocked=True)

    def test_swallowing_exceptions_is_rejected(self):
        self.check(SCRIPT.replace('    finally:', '    except Exception:\n        pass\n    finally:'), blocked=True)

    def test_changing_control_flow_or_data_is_rejected(self):
        self.check(SCRIPT.replace('    try:', '    if False:' ).replace('    finally:', '    else:'), blocked=True)
        self.check(SCRIPT.replace('generated_test_user', 'existing_business_user'), blocked=True)
        self.check(SCRIPT.replace("goto('/users')", "goto('/admin')"), blocked=True)

    def test_removing_cleanup_is_rejected(self):
        self.check(SCRIPT.replace("        await page.get_by_role('button', name='关闭').click()", '        pass'), blocked=True)

    def test_unsafe_import_or_import_alias_is_rejected(self):
        self.check('import subprocess\n' + SCRIPT, blocked=True)
        self.check('import time as expect\n' + SCRIPT, blocked=True)
        self.check(SCRIPT.replace('from playwright.async_api import expect', ''), blocked=True)

    def test_invalid_python_preserves_original(self):
        self.check('this is not valid Python !', blocked=True)
