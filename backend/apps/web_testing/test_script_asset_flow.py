"""Regression tests for the independent WebUI script-asset workflow."""

from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase

from projects.models import Project

from .execution_variables import merge_execution_variables, store_runtime_variables
from .models import (
    WebUITestCase,
    WebUITestCaseExecutionDetail,
    WebUITestExecution,
    WebUITestModule,
    WebUITestSuite,
    WebUITestSuiteCase,
    WebUITestSuiteExecutionDetail,
)
from .serializers import WebUITestCaseDetailSerializer
from .tasks import _execute_webui_test_suite_logic


User = get_user_model()


class ScriptAssetFlowTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='script-asset-user',
            password='test-password',
        )
        self.project = Project.objects.create(
            name='Script asset project',
            project_type='web',
            owner=self.user,
            created_by=self.user,
        )
    def make_case(self, title, script, variables=None):
        return WebUITestCase.objects.create(
            title=title,
            description=f'{title} description',
            user=self.user,
            project=self.project,
            module=WebUITestModule.ensure_default(self.project.id),
            variables=variables or [],
            test_script_content=script,
            script_status='ready',
        )

    def test_default_module_is_stable_and_partial_case_update_preserves_module(self):
        default_module = WebUITestModule.ensure_default(self.project.id)
        self.assertEqual(WebUITestModule.ensure_default(self.project.id).id, default_module.id)
        custom_module = WebUITestModule.objects.create(
            project=self.project,
            name='权限管理',
            order=1,
        )
        test_case = self.make_case('查询用户', 'async def run(page):\n    return None\n')
        test_case.module = custom_module
        test_case.save(update_fields=['module'])

        serializer = WebUITestCaseDetailSerializer(
            test_case,
            data={'title': '查询用户（更新）'},
            partial=True,
        )
        self.assertTrue(serializer.is_valid(), serializer.errors)
        serializer.save()
        test_case.refresh_from_db()

        self.assertEqual(test_case.module_id, custom_module.id)
        self.assertEqual(
            WebUITestModule.objects.filter(project=self.project, is_default=True).count(),
            1,
        )

    def test_variable_layers_merge_from_case_to_suite_to_runtime(self):
        merged = merge_execution_variables(
            [
                {'name': 'SHARED_VALUE', 'value': 'case'},
                {'name': 'REQUIRED_VALUE', 'value': '', 'required': True},
                {'name': 'CASE_ONLY', 'value': 'case-only'},
            ],
            [
                {'name': 'SHARED_VALUE', 'value': 'suite'},
                {'name': 'SUITE_ONLY', 'value': 'suite-only'},
            ],
            [
                {'name': 'SHARED_VALUE', 'value': 'runtime'},
                {'name': 'REQUIRED_VALUE', 'value': 'provided-at-runtime'},
            ],
        )

        self.assertEqual(merged['SHARED_VALUE'], 'runtime')
        self.assertEqual(merged['REQUIRED_VALUE'], 'provided-at-runtime')
        self.assertEqual(merged['CASE_ONLY'], 'case-only')
        self.assertEqual(merged['SUITE_ONLY'], 'suite-only')

    def test_deleting_assets_preserves_execution_history_snapshots(self):
        test_case = self.make_case('待删除用例', 'async def run(page):\n    return None\n')
        case_execution = WebUITestExecution.objects.create(
            exec_type='case',
            name=test_case.title,
            description=test_case.description,
            executor=self.user,
            project=self.project,
        )
        case_detail = WebUITestCaseExecutionDetail.objects.create(
            execution=case_execution,
            test_case=test_case,
        )
        suite = WebUITestSuite.objects.create(
            name='待删除套件',
            project=self.project,
            user=self.user,
        )
        suite_execution = WebUITestExecution.objects.create(
            exec_type='suite',
            name=suite.name,
            executor=self.user,
            project=self.project,
        )
        suite_detail = WebUITestSuiteExecutionDetail.objects.create(
            execution=suite_execution,
            test_suite=suite,
        )

        test_case.delete()
        suite.delete()
        case_detail.refresh_from_db()
        suite_detail.refresh_from_db()

        self.assertIsNone(case_detail.test_case_id)
        self.assertIsNone(suite_detail.test_suite_id)
        self.assertTrue(WebUITestExecution.objects.filter(pk=case_execution.pk).exists())
        self.assertTrue(WebUITestExecution.objects.filter(pk=suite_execution.pk).exists())

    def test_suite_runs_in_membership_order_and_continues_after_failure(self):
        first_script = 'async def run(page):\n    return "first"\n'
        second_script = (
            'from playwright.async_api import expect\n'
            'async def run(page):\n'
            '    await expect(page).to_have_title("Second")\n'
            '    return "second"\n'
        )
        first_case = self.make_case(
            '第一个失败用例',
            first_script,
            variables=[
                {'name': 'SHARED_VALUE', 'value': 'case'},
                {'name': 'REQUIRED_VALUE', 'value': '', 'required': True},
            ],
        )
        second_case = self.make_case('第二个通过用例', second_script)
        suite = WebUITestSuite.objects.create(
            name='独立脚本套件',
            project=self.project,
            user=self.user,
            variables=[
                {'name': 'SHARED_VALUE', 'value': 'suite'},
                {'name': 'SUITE_ONLY', 'value': 'suite-only'},
            ],
        )
        WebUITestSuiteCase.objects.create(suite=suite, test_case=second_case, order=20)
        WebUITestSuiteCase.objects.create(suite=suite, test_case=first_case, order=10)
        execution = WebUITestExecution.objects.create(
            exec_type='suite',
            name=suite.name,
            executor=self.user,
            project=self.project,
        )
        detail = WebUITestSuiteExecutionDetail.objects.create(
            execution=execution,
            test_suite=suite,
            total_cases=2,
        )
        store_runtime_variables(execution.id, [
            {'name': 'SHARED_VALUE', 'value': 'runtime'},
            {'name': 'REQUIRED_VALUE', 'value': 'provided-at-runtime'},
        ])
        task = SimpleNamespace(request=SimpleNamespace(id='suite-task-id'))
        failed_result = {
            'success': False,
            'error': 'first failed',
            'result': {
                'stdout': '',
                'stderr': 'playwright._impl._errors.TimeoutError: Locator.click timeout',
            },
        }
        passed_result = {
            'success': True,
            'runtime_assertion_count': 1,
            'error': '',
            'result': {'stdout': 'second passed', 'stderr': '', 'return_code': 0},
        }

        def run_in_order(script_content, *_args, **_kwargs):
            if script_content == first_script.strip():
                self.assertFalse(detail.case_executions.filter(test_case=second_case).exists())
                return failed_result
            self.assertEqual(script_content, second_script.strip())
            first_result = detail.case_executions.get(test_case=first_case)
            self.assertEqual(first_result.status, 'failed')
            return passed_result

        with patch(
            'web_testing.tasks._run_test_script',
            side_effect=run_in_order,
        ) as run_script, patch('web_testing.tasks.update_task_progress'):
            result = _execute_webui_test_suite_logic(
                task,
                execution.id,
                self.user.id,
                {'headed': False, 'timeout': 60},
            )

        self.assertEqual(run_script.call_count, 2)
        self.assertEqual(run_script.call_args_list[0].args[0], first_script.strip())
        self.assertEqual(run_script.call_args_list[1].args[0], second_script.strip())
        self.assertEqual(
            run_script.call_args_list[0].kwargs['environment_variables']['SHARED_VALUE'],
            'runtime',
        )
        self.assertEqual(
            run_script.call_args_list[0].kwargs['environment_variables']['REQUIRED_VALUE'],
            'provided-at-runtime',
        )
        self.assertEqual(result['failed_cases'], 1)
        self.assertEqual(result['passed_cases'], 1)
        self.assertFalse(result['success'])

        detail.refresh_from_db()
        execution.refresh_from_db()
        case_results = list(detail.case_executions.order_by('id').values_list('status', flat=True))
        self.assertEqual(case_results, ['failed', 'passed'])
        self.assertEqual(execution.status, 'failed')
