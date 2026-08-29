from django.contrib.auth import get_user_model
from django.test import TestCase
from types import SimpleNamespace
from rest_framework.test import APIRequestFactory, force_authenticate
from rest_framework.exceptions import PermissionDenied
from django.http import Http404
import json
from unittest.mock import Mock, patch

from projects.models import Environment, Project, ProjectMember

from .models import (
    WebPage,
    WebUITestCase,
    WebUITestCaseExecutionDetail,
    WebUITestModule,
    WebUITestExecution,
    WebUITestSuite,
    WebUITestSuiteExecutionDetail,
)
from .project_access import READ, get_project_for_user
from .script_contract import ScriptContractError, store_script_content
from .script_extraction import extract_playwright_metadata
from .views import (
    TestExecutionListView,
    TestCaseExecutionDetailView,
    TestExecutionDeleteView,
    ExecuteWebUITestCaseView,
    ExecuteWebUITestSuiteView,
    WebUITestCaseListCreateView,
    WebUITestSuiteAddTestCaseView,
    WebPageViewSet,
    get_webui_test_execution_statistics,
    get_webui_test_suite_statistics,
)
from .tasks import (
    _execute_webui_script_generation_from_testcase,
    _execute_webui_test_suite_logic,
)


class WebUIScriptMetadataTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username='webui-metadata-user',
            email='webui-metadata@example.com',
            password='test-password',
        )
        self.project = Project.objects.create(
            name='WebUI metadata project',
            project_type='web',
            owner=self.user,
            created_by=self.user,
        )

    def make_case(self):
        return WebUITestCase.objects.create(
            title='metadata case',
            description='metadata case description',
            expected_result='passed',
            user=self.user,
            project=self.project,
        )

    def test_new_case_defaults_to_no_script(self):
        case = self.make_case()

        self.assertEqual(case.script_source, 'manual')
        self.assertEqual(case.script_status, 'none')
        self.assertEqual(case.script_framework, 'playwright_python_async')
        self.assertEqual(case.script_version, 0)
        self.assertEqual(case.script_validation_error, '')
        self.assertEqual(case.generation_metadata, {})

    def test_store_script_updates_source_status_and_version(self):
        case = self.make_case()
        script = 'async def run(page):\n    await page.goto("/")\n'

        store_script_content(case, script, source='step_generator')
        case.refresh_from_db()
        self.assertEqual(case.script_source, 'step_generator')
        self.assertEqual(case.script_status, 'ready')
        self.assertEqual(case.script_version, 1)
        self.assertEqual(case.test_script_content, script.strip())

        store_script_content(case, script, source='manual')
        case.refresh_from_db()
        self.assertEqual(case.script_source, 'manual')
        self.assertEqual(case.script_version, 2)

        store_script_content(case, None, source='manual')
        case.refresh_from_db()
        self.assertIsNone(case.test_script_content)
        self.assertEqual(case.script_status, 'none')
        self.assertEqual(case.script_version, 3)

    def test_mcp_script_populates_structured_metadata_and_redacts_credentials(self):
        case = self.make_case()
        case.description = '登录账号 admin 123456，验证密码字段'
        case.save(update_fields=['description'])
        script = """from playwright.async_api import expect

async def run(page):
    await page.goto('/login?token=supersecret')
    await page.get_by_label('Password').fill('supersecret')
    await page.get_by_role('button', name='Login').click()
    await expect(page.get_by_label('Password')).to_have_value('supersecret')
"""

        store_script_content(
            case,
            script,
            source='mcp_exploration',
            generation_metadata={'nested': {'password': 'supersecret'}},
        )
        case.refresh_from_db()

        serialized = json.dumps(
            {
                'steps': case.steps,
                'expected_result': case.expected_result,
                'metadata': case.generation_metadata,
            },
            ensure_ascii=False,
        )
        self.assertNotIn('supersecret', serialized)
        self.assertNotIn('123456', serialized)
        self.assertEqual(
            [step['action'] for step in case.steps],
            ['goto', 'fill', 'click'],
        )
        self.assertEqual(case.generation_metadata['extraction_version'], 'webui-playwright-ast-v1')
        self.assertIn('目标元素应有指定值', case.expected_result)


class WebUIProjectAccessTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.owner = User.objects.create_user(
            username='webui-project-owner', email='webui-project-owner@example.com', password='test-password'
        )
        self.member = User.objects.create_user(
            username='webui-project-member', email='webui-project-member@example.com', password='test-password'
        )
        self.other = User.objects.create_user(
            username='webui-project-other', email='webui-project-other@example.com', password='test-password'
        )
        self.project = Project.objects.create(
            name='Collaborative WebUI project',
            project_type='web',
            owner=self.owner,
            created_by=self.owner,
        )
        ProjectMember.objects.create(
            project=self.project,
            user=self.member,
            role='editor',
            can_edit=True,
            can_delete=False,
            can_execute_tests=False,
            can_view_reports=True,
        )

    def test_member_can_access_resource_created_by_another_user(self):
        WebUITestCase.objects.create(
            title='owner case',
            description='owner case',
            expected_result='owner case passes',
            user=self.owner,
            project=self.project,
        )
        request = APIRequestFactory().get('/test-cases/')
        force_authenticate(request, user=self.member)

        result = WebUITestCaseListCreateView.as_view()(request, project_id=self.project.id)

        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.data['results'][0]['title'], 'owner case')

    def test_member_capability_is_enforced_and_nonmember_is_hidden(self):
        self.assertEqual(
            get_project_for_user(self.project.id, self.member, READ), self.project
        )
        with self.assertRaises(PermissionDenied):
            get_project_for_user(self.project.id, self.member, 'execute')
        with self.assertRaises(Http404):
            get_project_for_user(self.project.id, self.other, READ)

    def test_case_create_rejects_payload_project_different_from_url(self):
        other_project = Project.objects.create(
            name='Another WebUI project',
            project_type='web',
            owner=self.owner,
            created_by=self.owner,
        )
        request = APIRequestFactory().post(
            '/test-cases/',
            {
                'title': 'mismatched case',
                'description': 'mismatched case',
                'expected_result': 'must be rejected',
                'project': other_project.id,
            },
            format='json',
        )
        force_authenticate(request, user=self.member)

        result = WebUITestCaseListCreateView.as_view()(
            request, project_id=self.project.id
        )

        self.assertEqual(result.status_code, 400)
        self.assertFalse(
            WebUITestCase.objects.filter(title='mismatched case').exists()
        )

    def test_pom_page_create_uses_url_project_when_payload_omits_project(self):
        module = WebUITestModule.objects.create(
            name='member module', project=self.project
        )
        request = APIRequestFactory().post(
            '/pages/', {'name': 'member page', 'module_id': module.id}, format='json'
        )
        force_authenticate(request, user=self.member)

        result = WebPageViewSet.as_view({'post': 'create'})(
            request, project_id=self.project.id
        )

        self.assertEqual(result.status_code, 201)
        self.assertTrue(
            WebPage.objects.filter(name='member page', project=self.project).exists()
        )
        page = WebPage.objects.get(name='member page', project=self.project)
        self.assertEqual(page.module_id, module.id)

        update_request = APIRequestFactory().put(
            f'/pages/{page.id}/',
            {'name': 'updated member page', 'module_id': module.id},
            format='json',
        )
        force_authenticate(update_request, user=self.member)

        update_result = WebPageViewSet.as_view({'put': 'update'})(
            update_request, project_id=self.project.id, pk=page.id
        )

        self.assertEqual(update_result.status_code, 200)
        page.refresh_from_db()
        self.assertEqual(page.name, 'updated member page')
        self.assertEqual(page.module_id, module.id)


class WebUISuiteProjectIsolationTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username='webui-suite-owner', email='webui-suite-owner@example.com', password='test-password'
        )
        self.project_a = Project.objects.create(
            name='Suite project A', project_type='web', owner=self.user, created_by=self.user
        )
        self.project_b = Project.objects.create(
            name='Suite project B', project_type='web', owner=self.user, created_by=self.user
        )
        self.suite = WebUITestSuite.objects.create(
            name='project A suite', user=self.user, project=self.project_a
        )
        self.case_b = WebUITestCase.objects.create(
            title='project B case', description='case B', expected_result='pass',
            user=self.user, project=self.project_b,
        )

    def test_suite_cannot_add_cross_project_case(self):
        request = APIRequestFactory().post(
            '/test-suites/add-test-cases/',
            {'test_case_ids': [self.case_b.id]},
            format='json',
        )
        force_authenticate(request, user=self.user)

        result = WebUITestSuiteAddTestCaseView.as_view()(
            request, project_id=self.project_a.id, pk=self.suite.id
        )

        self.assertEqual(result.status_code, 400)
        self.assertFalse(self.suite.test_cases.filter(pk=self.case_b.id).exists())


class WebUIScriptMetadataValidationTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username='webui-validation-user',
            email='webui-validation@example.com',
            password='test-password',
        )
        self.project = Project.objects.create(
            name='WebUI validation project',
            project_type='web',
            owner=self.user,
            created_by=self.user,
        )

    def make_case(self):
        return WebUITestCase.objects.create(
            title='validation case',
            description='validation case description',
            expected_result='passed',
            user=self.user,
            project=self.project,
        )

    def test_invalid_script_does_not_change_existing_metadata(self):
        case = self.make_case()
        store_script_content(
            case,
            'async def run(page):\n    pass\n',
            source='manual',
        )
        with self.assertRaises(ScriptContractError):
            store_script_content(case, 'async def main():\n    pass\n', source='manual')

        case.refresh_from_db()
        self.assertEqual(case.script_status, 'ready')
        self.assertEqual(case.script_version, 1)

    def test_create_and_update_serializers_use_storage_service(self):
        from .serializers import WebUITestCaseCreateSerializer, WebUITestCaseDetailSerializer

        script = 'async def run(page):\n    await page.goto("/")\n'
        create_serializer = WebUITestCaseCreateSerializer(
            data={
                'title': 'serializer case',
                'description': 'serializer case description',
                'expected_result': 'passed',
                'project': self.project.id,
                'script_source': 'step_generator',
                'test_script_content': script,
            },
            context={'request': SimpleNamespace(user=self.user)},
        )
        self.assertTrue(create_serializer.is_valid(), create_serializer.errors)
        case = create_serializer.save()
        self.assertEqual(case.script_source, 'step_generator')
        self.assertEqual(case.script_status, 'ready')
        self.assertEqual(case.script_version, 1)

        update_serializer = WebUITestCaseDetailSerializer(
            case,
            data={'test_script_content': script},
            partial=True,
        )
        self.assertTrue(update_serializer.is_valid(), update_serializer.errors)
        case = update_serializer.save()
        self.assertEqual(case.script_source, 'manual')
        self.assertEqual(case.script_version, 2)


class WebUIExtractionTests(TestCase):
    def test_ast_extraction_is_ordered_and_redacts_assertion_values(self):
        script = """from playwright.async_api import expect

async def run(page):
    await page.goto('/login')
    await page.get_by_label('Password').fill('supersecret')
    await page.get_by_role('button', name='Login').click()
    await expect(page.get_by_label('Password')).to_have_value('supersecret')
"""

        metadata = extract_playwright_metadata(script, '登录账号 admin 123456')

        self.assertEqual(
            [step['action'] for step in metadata['extracted_steps']],
            ['goto', 'fill', 'click'],
        )
        self.assertEqual(metadata['extracted_steps'][1]['value'], '<redacted>')
        self.assertEqual(metadata['assertion_candidates'][0]['expected'], '目标元素应有指定值：<redacted>')
        self.assertNotIn('supersecret', json.dumps(metadata, ensure_ascii=False))
        self.assertNotIn('123456', json.dumps(metadata, ensure_ascii=False))


class WebUIReportPermissionTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.owner = User.objects.create_user(
            username='webui-report-owner',
            email='webui-report-owner@example.com',
            password='test-password',
        )
        self.member = User.objects.create_user(
            username='webui-report-member',
            email='webui-report-member@example.com',
            password='test-password',
        )
        self.no_report_member = User.objects.create_user(
            username='webui-no-report-member',
            email='webui-no-report-member@example.com',
            password='test-password',
        )
        self.project = Project.objects.create(
            name='WebUI report project',
            project_type='web',
            owner=self.owner,
            created_by=self.owner,
        )
        ProjectMember.objects.create(
            project=self.project,
            user=self.member,
            role='viewer',
            can_view_reports=True,
            can_delete=True,
        )
        ProjectMember.objects.create(
            project=self.project,
            user=self.no_report_member,
            role='viewer',
            can_view_reports=False,
        )
        self.case = WebUITestCase.objects.create(
            title='owner report case',
            description='owner report case',
            expected_result='passed',
            user=self.owner,
            project=self.project,
        )
        self.execution = WebUITestExecution.objects.create(
            exec_type='case',
            name='owner execution',
            status='passed',
            executor=self.owner,
            project=self.project,
        )
        WebUITestCaseExecutionDetail.objects.create(
            execution=self.execution,
            test_case=self.case,
            status='passed',
        )
        self.suite = WebUITestSuite.objects.create(
            name='owner suite', user=self.owner, project=self.project
        )
        self.suite_execution = WebUITestExecution.objects.create(
            exec_type='suite',
            name='owner suite execution',
            status='passed',
            executor=self.owner,
            project=self.project,
        )

    def request(self, method, user, path='/executions/'):
        request = getattr(APIRequestFactory(), method.lower())(path)
        force_authenticate(request, user=user)
        return request

    def test_report_member_can_list_view_and_count_owner_executions(self):
        list_result = TestExecutionListView.as_view()(
            self.request('GET', self.member), project_id=self.project.id
        )
        self.assertEqual(list_result.status_code, 200)
        names = [item['name'] for item in list_result.data['data']['items']]
        self.assertCountEqual(names, ['owner execution', 'owner suite execution'])

        detail_result = TestCaseExecutionDetailView.as_view()(
            self.request('GET', self.member),
            project_id=self.project.id,
            pk=self.execution.id,
        )
        self.assertEqual(detail_result.status_code, 200)
        self.assertEqual(detail_result.data['data']['project_id'], self.project.id)

        execution_stats = get_webui_test_execution_statistics(
            self.request('GET', self.member, '/execution-statistics/'), self.project.id
        )
        self.assertEqual(execution_stats.data['data']['total'], 2)

        suite_stats = get_webui_test_suite_statistics(
            self.request('GET', self.member, '/test-suite-statistics/'), self.project.id
        )
        self.assertEqual(suite_stats.data['data']['total_executions'], 2)
        self.assertEqual(suite_stats.data['data']['total_suite_executions'], 1)

    def test_member_without_report_capability_gets_forbidden(self):
        result = TestExecutionListView.as_view()(
            self.request('GET', self.no_report_member), project_id=self.project.id
        )
        self.assertEqual(result.status_code, 403)

    def test_delete_member_can_delete_owner_execution_in_project(self):
        result = TestExecutionDeleteView.as_view()(
            self.request('DELETE', self.member, '/executions/delete/'),
            project_id=self.project.id,
            pk=self.execution.id,
        )
        self.assertEqual(result.status_code, 200)
        self.assertFalse(WebUITestExecution.objects.filter(pk=self.execution.id).exists())


class WebUIGenerationTaskProjectAccessTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.owner = User.objects.create_user(
            username='webui-generation-owner',
            email='webui-generation-owner@example.com',
            password='test-password',
        )
        self.member = User.objects.create_user(
            username='webui-generation-member',
            email='webui-generation-member@example.com',
            password='test-password',
        )
        self.project = Project.objects.create(
            name='WebUI generation project',
            project_type='web',
            owner=self.owner,
            created_by=self.owner,
        )
        ProjectMember.objects.create(
            project=self.project,
            user=self.member,
            role='editor',
            can_edit=True,
        )
        self.case = WebUITestCase.objects.create(
            title='owner generation case',
            description='generate a script',
            expected_result='the script is generated',
            user=self.owner,
            project=self.project,
        )
        self.task_instance = SimpleNamespace(
            request=SimpleNamespace(id='generation-task-id')
        )

    def test_edit_member_can_generate_script_for_owner_case(self):
        agent_instance = SimpleNamespace(
            run=Mock(),
        )
        with patch('web_testing.tasks.update_task_progress'), patch(
            'web_testing.tasks.update_task_success',
            side_effect=lambda task, message, result: result,
        ), patch(
            'ai_core.webui_playwright_agent.WebUIPlaywrightAgent',
            return_value=agent_instance,
        ) as agent_class, patch(
            'web_testing.tasks.asyncio.run',
            return_value={'success': True, 'test_script': 'async def run(page):\n    pass\n'},
        ):
            result = _execute_webui_script_generation_from_testcase(
                self.task_instance,
                self.case.id,
                self.member.id,
                self.project.id,
            )

        self.assertTrue(result['success'])
        agent_class.assert_called_once_with(user_id=self.member.id)

    def test_revoked_edit_access_fails_before_agent_work(self):
        ProjectMember.objects.filter(
            project=self.project, user=self.member
        ).update(can_edit=False)

        with patch('web_testing.tasks.update_task_progress'), patch(
            'ai_core.webui_playwright_agent.WebUIPlaywrightAgent'
        ) as agent_class:
            result = _execute_webui_script_generation_from_testcase(
                self.task_instance,
                self.case.id,
                self.member.id,
                self.project.id,
            )

        self.assertFalse(result['success'])
        agent_class.assert_not_called()

    def test_agent_save_node_allows_member_to_save_owner_case(self):
        from ai_core.webui_playwright_agent import WebUIPlaywrightAgent

        agent = WebUIPlaywrightAgent.__new__(WebUIPlaywrightAgent)
        agent._send_node_start_notification = Mock()
        agent._send_websocket_message = Mock()
        agent._send_task_completed_notification = Mock()

        result = agent._save_script_node({
            'test_script': 'async def run(page):\n    await page.goto("/")\n',
            'user_id': self.member.id,
            'project_id': self.project.id,
            'test_case_id': self.case.id,
            'script_name': 'member script',
        })

        self.assertEqual(result['current_step'], 'saved')
        self.case.refresh_from_db()
        self.assertEqual(self.case.script_status, 'ready')


class WebUIExecutionContractTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username='webui-contract-user',
            email='webui-contract@example.com',
            password='test-password',
        )
        self.project = Project.objects.create(
            name='WebUI contract project',
            project_type='web',
            owner=self.user,
            created_by=self.user,
        )
        self.case = WebUITestCase.objects.create(
            title='contract case',
            description='contract case',
            expected_result='passed',
            test_script_content='async def run(page):\n    await page.goto("/")\n',
            script_status='ready',
            user=self.user,
            project=self.project,
        )
        self.suite = WebUITestSuite.objects.create(
            name='contract suite', user=self.user, project=self.project
        )
        self.suite.test_cases.add(self.case)
        self.web_environment = Environment.objects.create(
            project=self.project,
            name='web environment',
            category=Environment.EnvironmentCategory.WEB,
            config={'base_url': 'https://web.example.test'},
        )
        self.api_environment = Environment.objects.create(
            project=self.project,
            name='wrong API environment',
            category=Environment.EnvironmentCategory.API,
            config={'base_url': 'https://api.example.test'},
        )

    def request(self, path, payload):
        request = APIRequestFactory().post(path, payload, format='json')
        force_authenticate(request, user=self.user)
        return request

    def test_case_rejects_non_web_environment(self):
        result = ExecuteWebUITestCaseView.as_view()(
            self.request('/execute/', {'environment_id': self.api_environment.id}),
            project_id=self.project.id,
            pk=self.case.id,
        )

        self.assertFalse(result.data['success'])
        self.assertFalse(WebUITestExecution.objects.exists())

    def test_suite_requires_web_environment(self):
        result = ExecuteWebUITestSuiteView.as_view()(
            self.request('/execute-suite/', {}),
            project_id=self.project.id,
            pk=self.suite.id,
        )

        self.assertFalse(result.data['success'])
        self.assertIn('WebUI', result.data['message'])
        self.assertFalse(WebUITestExecution.objects.exists())

    @patch('web_testing.tasks.execute_webui_test_case_task.delay')
    def test_case_ignores_client_browser_and_uses_fixed_chrome_engine(self, delay):
        delay.return_value = SimpleNamespace(id='webui-execution-task')
        result = ExecuteWebUITestCaseView.as_view()(
            self.request(
                '/execute/',
                {
                    'environment_id': self.web_environment.id,
                    'options': {
                        'browser': 'firefox',
                        'headed': False,
                        'timeout': 60,
                        'html_report': True,
                    },
                },
            ),
            project_id=self.project.id,
            pk=self.case.id,
        )

        self.assertTrue(result.data['success'])
        execution = WebUITestExecution.objects.get()
        self.assertEqual(execution.browser, 'chromium')
        passed_options = delay.call_args.args[1]
        self.assertEqual(passed_options, {'headed': False, 'timeout': 60})

    @patch('web_testing.views.execute_webui_test_suite_task.delay')
    def test_suite_ignores_client_browser_and_uses_fixed_chrome_engine(self, delay):
        delay.return_value = SimpleNamespace(id='webui-suite-task')
        result = ExecuteWebUITestSuiteView.as_view()(
            self.request(
                '/execute-suite/',
                {
                    'environment_id': self.web_environment.id,
                    'options': {
                        'browser': 'webkit',
                        'headed': True,
                        'timeout': 90,
                        'html_report': False,
                    },
                },
            ),
            project_id=self.project.id,
            pk=self.suite.id,
        )

        self.assertTrue(result.data['success'])
        execution = WebUITestExecution.objects.get()
        self.assertEqual(execution.browser, 'chromium')
        passed_options = delay.call_args.args[2]
        self.assertEqual(
            passed_options,
            {'headed': True, 'timeout': 90, 'suite_name': self.suite.name},
        )

    @patch('web_testing.tasks.update_task_progress')
    @patch('web_testing.tasks._run_test_suite_script')
    def test_suite_runner_failure_remains_failed_and_persists_real_error(
        self, run_suite, _update_progress
    ):
        run_suite.return_value = {
            'success': False,
            'error': 'RuntimeError: visible suite error',
            'result': {
                'stdout': 'pytest output',
                'stderr': 'pytest error output',
                'test_files': [],
                'case_results': [],
                'allure_report': '',
            },
        }
        execution = WebUITestExecution.objects.create(
            exec_type='suite',
            name=self.suite.name,
            executor=self.user,
            project=self.project,
            environment=self.web_environment,
        )
        WebUITestSuiteExecutionDetail.objects.create(
            execution=execution,
            test_suite=self.suite,
            total_cases=1,
        )
        task_instance = SimpleNamespace(
            request=SimpleNamespace(id='webui-suite-failure-task')
        )

        result = _execute_webui_test_suite_logic(
            task_instance,
            execution.id,
            self.user.id,
            {'headed': False, 'timeout': 60},
        )

        execution.refresh_from_db()
        self.assertFalse(result['success'])
        self.assertEqual(execution.status, 'failed')
        self.assertEqual(
            execution.error_message, 'RuntimeError: visible suite error'
        )
        self.assertEqual(result['error'], execution.error_message)


class WebUIExecutionProjectIsolationTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username='webui-execution-user',
            email='webui-execution@example.com',
            password='test-password',
        )
        self.project_a = Project.objects.create(
            name='WebUI project A', project_type='web', owner=self.user, created_by=self.user
        )
        self.project_b = Project.objects.create(
            name='WebUI project B', project_type='web', owner=self.user, created_by=self.user
        )
        WebUITestExecution.objects.create(
            exec_type='case', name='A passed', status='passed', executor=self.user, project=self.project_a
        )
        WebUITestExecution.objects.create(
            exec_type='case', name='A failed', status='failed', executor=self.user, project=self.project_a
        )
        WebUITestExecution.objects.create(
            exec_type='case', name='B passed', status='passed', executor=self.user, project=self.project_b
        )

    def test_execution_queryset_is_project_scoped(self):
        queryset = WebUITestExecution.objects.filter(
            executor=self.user, project_id=self.project_a.id
        )

        self.assertCountEqual(queryset.values_list('name', flat=True), ['A passed', 'A failed'])

    def test_execution_list_view_does_not_return_other_project(self):
        factory = APIRequestFactory()
        request = factory.get('/executions/')
        force_authenticate(request, user=self.user)

        result = TestExecutionListView.as_view()(request, project_id=self.project_a.id)

        self.assertEqual(result.status_code, 200)
        names = [item['name'] for item in result.data['data']['items']]
        self.assertCountEqual(names, ['A passed', 'A failed'])

    def test_execution_statistics_is_project_scoped(self):
        factory = APIRequestFactory()
        request = factory.get('/execution-statistics/')
        force_authenticate(request, user=self.user)

        result = get_webui_test_execution_statistics(request, self.project_a.id)

        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.data['data']['total'], 2)
        self.assertEqual(result.data['data']['passed'], 1)
        self.assertEqual(result.data['data']['failed'], 1)
