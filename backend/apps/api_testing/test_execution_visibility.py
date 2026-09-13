from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from projects.models import Project, ProjectMember

from .models import APITestCaseExecutionDetail, APITestExecution


class APITestExecutionVisibilityTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.owner = user_model.objects.create_user(
            username='execution-owner', email='execution-owner@example.test', password='local-only',
        )
        self.reporter = user_model.objects.create_user(
            username='execution-reporter', email='execution-reporter@example.test', password='local-only',
        )
        self.denied = user_model.objects.create_user(
            username='execution-denied', email='execution-denied@example.test', password='local-only',
        )
        self.outsider = user_model.objects.create_user(
            username='execution-outsider', email='execution-outsider@example.test', password='local-only',
        )
        self.project = Project.objects.create(
            name='Execution visibility', project_type='api', owner=self.owner, created_by=self.owner,
        )
        self.other_project = Project.objects.create(
            name='Other execution visibility', project_type='api', owner=self.owner, created_by=self.owner,
        )
        ProjectMember.objects.create(
            project=self.project, user=self.reporter, role='viewer', can_view_reports=True,
        )
        ProjectMember.objects.create(
            project=self.project, user=self.denied, role='viewer', can_view_reports=False,
        )
        self.client = APIClient()

    def execution(self, *, project=None, executor=None, source=None, input_snapshot=None, exec_type='case', status='running'):
        snapshot = input_snapshot if input_snapshot is not None else ({'source': source} if source else {})
        return APITestExecution.objects.create(
            project=project or self.project,
            executor=executor or self.owner,
            exec_type=exec_type,
            name=f'{source or "formal"}-{exec_type}-{status}',
            status=status,
            input_snapshot=snapshot,
        )

    def list(self, user, **params):
        self.client.force_authenticate(user)
        return self.client.get(
            f'/api/v1/projects/{self.project.pk}/api-testing/executions/', params,
        )

    def items(self, response):
        self.assertEqual(response.status_code, 200, response.data)
        return response.data['data']['items']

    def test_workspace_record_without_environment_or_saved_case_is_listed_and_readable(self):
        execution = self.execution(source='workspace_debug')
        APITestCaseExecutionDetail.objects.create(
            execution=execution, test_case=None, name=execution.name, status='running', httprunner_result='{}',
        )

        items = self.items(self.list(self.owner))
        row = next(item for item in items if item['id'] == execution.id)
        self.assertIsNone(row['environment'])
        self.assertEqual(row['execution_source'], 'workspace_debug')
        self.assertEqual(row['execution_source_display'], '工作区调试')
        self.assertNotIn('input_snapshot', row)

        detail = self.client.get(
            f'/api/v1/projects/{self.project.pk}/api-testing/executions/case/{execution.pk}/',
        )
        self.assertEqual(detail.status_code, 200, detail.data)
        self.assertIsNone(detail.data['data']['test_case'])
        report = self.client.get(
            f'/api/v1/projects/{self.project.pk}/api-testing/executions/{execution.pk}/report/',
        )
        self.assertEqual(report.status_code, 200, report.data)
        self.assertEqual(report.data['data']['execution_source'], 'workspace_debug')

    def test_list_uses_report_permission_and_project_boundary(self):
        visible = self.execution(source='workspace_generation')
        denied_execution = self.execution(executor=self.denied, source='workspace_debug')
        other_project_execution = self.execution(project=self.other_project, source='workspace_debug')

        reporter_ids = {item['id'] for item in self.items(self.list(self.reporter))}
        self.assertIn(visible.id, reporter_ids)
        self.assertIn(denied_execution.id, reporter_ids)
        self.assertNotIn(other_project_execution.id, reporter_ids)

        denied_ids = {item['id'] for item in self.items(self.list(self.denied))}
        self.assertNotIn(visible.id, denied_ids)
        self.assertNotIn(denied_execution.id, denied_ids)

    def test_non_member_executor_cannot_list_or_read_execution_details(self):
        execution = self.execution(executor=self.outsider, source='workspace_debug')
        APITestCaseExecutionDetail.objects.create(
            execution=execution, test_case=None, name=execution.name, status='running', httprunner_result='{}',
        )

        outsider_ids = {item['id'] for item in self.items(self.list(self.outsider))}
        self.assertNotIn(execution.id, outsider_ids)

        detail = self.client.get(
            f'/api/v1/projects/{self.project.pk}/api-testing/executions/case/{execution.pk}/',
        )
        self.assertEqual(detail.status_code, 400, detail.data)
        self.assertFalse(detail.data['success'])
        report = self.client.get(
            f'/api/v1/projects/{self.project.pk}/api-testing/executions/{execution.pk}/report/',
        )
        self.assertEqual(report.status_code, 404, report.data)
        self.assertFalse(report.data['success'])

    def test_source_filter_preserves_execution_type_and_status_filters(self):
        matching = self.execution(source='workspace_debug', exec_type='case', status='running')
        self.execution(source='workspace_generation', exec_type='case', status='running')
        self.execution(source='workspace_debug', exec_type='scenario', status='passed')
        formal = self.execution(source=None, exec_type='suite', status='failed')
        formal_null = self.execution(input_snapshot={'source': None})
        formal_unknown = self.execution(input_snapshot={'source': 'legacy_source'})
        formal_empty = self.execution(input_snapshot={})

        filtered_ids = {
            item['id'] for item in self.items(self.list(
                self.owner,
                execution_source='workspace_debug',
                exec_type='case',
                status='running',
            ))
        }
        self.assertEqual(filtered_ids, {matching.id})

        formal_items = self.items(self.list(self.owner, execution_source='formal'))
        formal_rows = {item['id']: item for item in formal_items}
        self.assertEqual(set(formal_rows), {
            formal.id, formal_null.id, formal_unknown.id, formal_empty.id,
        })
        for row in formal_rows.values():
            self.assertEqual(row['execution_source'], 'formal')
            self.assertEqual(row['execution_source_display'], '正式执行')

    def test_statistics_include_workspace_records_and_use_report_scope(self):
        self.execution(source='workspace_debug', status='passed')
        self.execution(source='workspace_generation', status='failed')
        self.execution(status='running')
        self.execution(project=self.other_project, status='passed')
        url = f'/api/v1/projects/{self.project.pk}/api-testing/statistics/'
        for user in (self.owner, self.reporter):
            self.client.force_authenticate(user)
            reply = self.client.get(url)
            self.assertEqual(reply.status_code, 200)
            self.assertEqual(reply.data['data'], {
                'total': 3, 'pending': 0, 'running': 1, 'passed': 1,
                'failed': 1, 'error': 0, 'stopped': 0, 'success_rate': 33.3,
            })
        for user in (self.denied, self.outsider):
            self.client.force_authenticate(user)
            reply = self.client.get(url)
            self.assertEqual(reply.status_code, 403)
            self.assertFalse(reply.data['success'])
