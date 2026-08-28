from django.contrib.auth import get_user_model
from django.test import TestCase
from types import SimpleNamespace
from rest_framework.test import APIRequestFactory, force_authenticate

from projects.models import Project

from .models import WebUITestCase, WebUITestExecution
from .script_contract import ScriptContractError, store_script_content
from .views import TestExecutionListView, get_webui_test_execution_statistics


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
