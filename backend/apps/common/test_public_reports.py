"""Public report reads must not grant access to management or execution APIs."""
import tempfile
from pathlib import Path
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from api_testing.models import (
    APITestExecution, APITestCaseExecutionDetail, APITestSuiteExecutionDetail,
    APITestSuiteCaseExecution,
)
from projects.models import Project
from scheduled_tasks.models import ScheduledTask, TaskExecutionLog
from web_testing.models import (
    WebUITestExecution, WebUITestCaseExecutionDetail, WebUITestSuiteExecutionDetail,
    WebUITestSuiteCaseExecution,
)


class PublicReportBoundaryTests(TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix='public-report-contract-')
        self.addCleanup(temp.cleanup)
        override = override_settings(MEDIA_ROOT=temp.name)
        override.enable()
        self.addCleanup(override.disable)
        self.media = Path(temp.name)
        self.owner = get_user_model().objects.create_user(username='report-owner', email='report-owner@example.test', password='fixture-only')
        self.outsider = get_user_model().objects.create_user(username='report-outsider', email='report-outsider@example.test', password='fixture-only')
        self.web = Project.objects.create(name='Web reports', project_type='web', created_by=self.owner)
        self.api = Project.objects.create(name='API reports', project_type='api', created_by=self.owner)
        self.case = WebUITestExecution.objects.create(project=self.web, executor=self.owner, exec_type='case', name='WEB-CASE', status='passed')
        self.case_detail = WebUITestCaseExecutionDetail.objects.create(execution=self.case, status='passed', log='WEB-CASE-LOG', screenshot_path=self.screenshot(self.case.id))
        self.suite = WebUITestExecution.objects.create(project=self.web, executor=self.owner, exec_type='suite', name='WEB-SUITE', status='failed')
        detail = WebUITestSuiteExecutionDetail.objects.create(execution=self.suite, total_cases=2)
        # Insertion order differs from execution order, so ordering is tested.
        self.pending = WebUITestSuiteCaseExecution.objects.create(suite_execution=detail, name='Pending child', execution_order=2, status='pending', script_content='private source', variables=[{'name': 'secret', 'value': 'not-report-data'}])
        self.child = WebUITestSuiteCaseExecution.objects.create(suite_execution=detail, name='First child', execution_order=1, status='passed', log='CHILD-LOG', screenshot_path=self.screenshot(self.suite.id))
        self.api_case = APITestExecution.objects.create(project=self.api, executor=self.owner, exec_type='case', name='API-CASE', status='passed')
        APITestCaseExecutionDetail.objects.create(execution=self.api_case, name='API-CASE', status='passed', log='API-CASE-LOG', httprunner_result='{"success":true}')
        self.api_suite = APITestExecution.objects.create(project=self.api, executor=self.owner, exec_type='suite', name='API-SUITE', status='passed')
        api_detail = APITestSuiteExecutionDetail.objects.create(execution=self.api_suite, test_suite_name='API-SUITE', total_cases=1)
        APITestSuiteCaseExecution.objects.create(suite_execution=api_detail, name='API child', status='passed', log='API-CHILD-LOG', httprunner_result='{"success":true}')
        with patch('scheduled_tasks.signals.register_periodic_task'):
            task = ScheduledTask.objects.create(project=self.api, user=self.owner, name='Public schedule', suite_type='api', suite_ids=[999999], cron_expression='0 9 * * *', status='paused')
        self.log = TaskExecutionLog.objects.create(task=task, start_time=timezone.now(), status='success', linked_executions=[{'kind':'api', 'project_id':self.api.id, 'execution_id':self.api_suite.id}])
        self.client = APIClient()

    def screenshot(self, execution_id):
        relative = f'webui_failure_screenshots/execution_{execution_id}/single_case.png'
        path = self.media / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'PNG report artifact')
        return relative

    def report_urls(self):
        return [
            f'/api/v1/projects/{self.web.id}/web-testing/executions/{item.id}/report/'
            for item in (self.case, self.suite)
        ] + [
            f'/api/v1/projects/{self.api.id}/api-testing/executions/{item.id}/report/'
            for item in (self.api_case, self.api_suite)
        ] + [f'/api/v1/reports/{alias}/{self.log.id}/' for alias in ('detail', 'execution')]

    def test_reports_are_public_with_no_login_foreign_login_or_invalid_token(self):
        for actor in ('anonymous', 'outsider', 'stale-token'):
            self.client.force_authenticate(self.outsider if actor == 'outsider' else None)
            self.client.credentials(HTTP_AUTHORIZATION='Bearer expired-or-invalid' if actor == 'stale-token' else '')
            for url in self.report_urls():
                with self.subTest(actor=actor, url=url):
                    reply = self.client.get(url)
                    self.assertEqual(reply.status_code, 200, reply.data)
            shot = self.client.get(f'/api/v1/projects/{self.web.id}/web-testing/executions/{self.case.id}/screenshot/')
            self.assertEqual(shot.status_code, 200)
            self.assertEqual(b''.join(shot.streaming_content), b'PNG report artifact')
            shot.close()

    def test_web_suite_report_contains_ordered_readonly_results(self):
        reply = self.client.get(self.report_urls()[1])
        children = reply.data['data']['case_executions']
        self.assertEqual([item['id'] for item in children], [self.child.id, self.pending.id])
        self.assertEqual(children[0]['log'], 'CHILD-LOG')
        self.assertEqual(children[1]['status_display'], '未执行（套件已结束）')
        for child in children:
            self.assertNotIn('script_content', child)
            self.assertNotIn('variables', child)
            self.assertNotIn('repair_availability', child)
        self.assertNotIn('repair_availability', self.client.get(self.report_urls()[0]).data['data'])
        shot = self.client.get(f'/api/v1/projects/{self.web.id}/web-testing/executions/{self.suite.id}/cases/{self.child.id}/screenshot/')
        self.assertEqual(shot.status_code, 200)
        shot.close()

    def test_invalid_report_ids_and_cross_project_screenshot_paths_are_404(self):
        for url in (
            f'/api/v1/projects/{self.api.id}/web-testing/executions/{self.case.id}/report/',
            f'/api/v1/projects/{self.web.id}/api-testing/executions/{self.api_case.id}/report/',
            '/api/v1/reports/detail/999999/',
            f'/api/v1/projects/{self.api.id}/web-testing/executions/{self.case.id}/screenshot/',
            f'/api/v1/projects/{self.web.id}/web-testing/executions/{self.case.id}/cases/{self.child.id}/screenshot/',
        ):
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 404)
        for invalid_path in ('../../outside.png', self.screenshot(self.suite.id)):
            self.case_detail.screenshot_path = invalid_path
            self.case_detail.save(update_fields=['screenshot_path'])
            self.assertEqual(self.client.get(f'/api/v1/projects/{self.web.id}/web-testing/executions/{self.case.id}/screenshot/').status_code, 404)

    def test_report_reads_cannot_mutate_or_open_management_endpoints(self):
        for url in self.report_urls():
            for method in ('post', 'patch', 'delete'):
                with self.subTest(url=url, method=method):
                    self.assertEqual(getattr(self.client, method)(url, {}, format='json').status_code, 405)
        for suffix in ('executions/', f'executions/case/{self.api_case.id}/', f'executions/{self.api_suite.id}/cases/'):
            url = f'/api/v1/projects/{self.api.id}/api-testing/{suffix}'
            self.assertEqual(self.client.get(url).status_code, 401)
        for suffix in ('executions/', f'executions/{self.suite.id}/cases/', 'script-assistants/'):
            url = f'/api/v1/projects/{self.web.id}/web-testing/{suffix}'
            self.assertEqual(self.client.get(url).status_code, 401)
        url = f'/api/v1/projects/{self.api.id}/api-testing/executions/{self.api_case.id}/delete/'
        self.assertEqual(self.client.delete(url).status_code, 401)
        self.assertTrue(APITestExecution.objects.filter(pk=self.api_case.id).exists())
