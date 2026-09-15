"""Dashboard regression tests; run with the isolated SQLite bootstrap."""
from datetime import datetime, timedelta, timezone as dt_timezone
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from api_testing.models import (
    APISpecification, APIEndpoint, APIWorkspace, APITestCase, APITestExecution, APITestCaseExecutionDetail,
    APITestSuiteExecutionDetail, APITestSuiteCaseExecution,
)
from web_testing.models import (
    WebUITestCase, WebUITestExecution, WebUITestCaseExecutionDetail,
    WebUITestSuiteExecutionDetail, WebUITestSuiteCaseExecution,
)
from projects.models import Project, ProjectMember
from projects.dashboard.services import (
    get_dashboard_summary, get_dashboard_trend, get_dashboard_top_failures,
)


class DashboardTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username='dashboard', email='dashboard@example.test')
        self.api = Project.objects.create(name='API', project_type='api', created_by=self.user)
        self.web = Project.objects.create(name='UI', project_type='web', created_by=self.user)
        # UTC is still the previous day: catches timezone.now().date() regression.
        self.now = datetime(2026, 9, 14, 16, 30, tzinfo=dt_timezone.utc)
        self.tz = timezone.override('Asia/Shanghai')
        self.tz.__enter__()
        self.addCleanup(self.tz.__exit__, None, None, None)
        self.clock = patch('django.utils.timezone.now', return_value=self.now)
        self.clock.start()
        self.addCleanup(self.clock.stop)

    def execution(self, project=None, status='failed', name='执行名称', when=None, suite=False, snapshot=None):
        project = project or self.api
        model = APITestExecution if project.project_type == 'api' else WebUITestExecution
        kwargs = dict(project=project, executor=self.user, name=name, status=status,
                      exec_type='suite' if suite else 'case')
        if model is APITestExecution:
            kwargs['input_snapshot'] = snapshot or {}
        execution = model.objects.create(**kwargs)
        model.objects.filter(pk=execution.pk).update(created_at=when or self.now)
        return execution

    def single(self, project=None, status='failed', name='场景名称', case=None, **kwargs):
        project = project or self.api
        execution = self.execution(project, status=kwargs.pop('parent_status', status), name=name, **kwargs)
        model = APITestCaseExecutionDetail if project.project_type == 'api' else WebUITestCaseExecutionDetail
        data = dict(execution=execution, test_case=case, status=status)
        if project.project_type == 'api':
            data['name'] = name
        return model.objects.create(**data)

    def test_null_case_failures_use_distinct_workspace_and_snapshot_names(self):
        self.single(name='注册流程', snapshot={'workspace_id': 101})
        self.single(name='注册流程', snapshot={'workspace_id': 101})
        self.single(name='退出流程', snapshot={'workspace_id': 102})
        self.single(name='独立调试')
        rows = get_dashboard_top_failures(self.api)
        self.assertEqual([(r['test_case_name'], r['fail_count']) for r in rows],
                         [('注册流程', 2), ('独立调试', 1), ('退出流程', 1)])
        self.assertTrue(all(r['test_case_id'] is None for r in rows))

    def test_workspace_failures_merge_with_saved_case_and_suite(self):
        case = APITestCase.objects.create(project=self.api, title='保存后的名称', test_case_type='scenario', created_by=self.user)
        workspace = APIWorkspace.objects.create(project=self.api, owner=self.user, saved_case=case)
        self.single(name='保存前名称', snapshot={'workspace_id': workspace.pk})
        self.single(case=case)
        self.single(snapshot={'cases': [{'case_id': case.pk}]})
        suite = APITestSuiteExecutionDetail.objects.create(execution=self.execution(suite=True))
        APITestSuiteCaseExecution.objects.create(suite_execution=suite, test_case=case, name='旧名字', status='error')
        self.assertEqual(get_dashboard_top_failures(self.api), [
            dict(test_case_id=case.pk, test_case_name='保存后的名称', fail_count=4),
        ])

    def test_missing_names_never_produce_none_and_malformed_snapshot_is_safe(self):
        self.single(name='None', snapshot={'workspace_id': {}, 'cases': [{'case_id': []}]})
        self.single(name='', snapshot={'workspace_id': 200})
        rows = get_dashboard_top_failures(self.api)
        self.assertEqual(len(rows), 2)
        self.assertTrue(all(r['test_case_name'] and 'None' not in r['test_case_name'] for r in rows))

    def test_web_draft_execution_is_counted_without_case_fk(self):
        self.single(self.web, status='passed', name='未保存的成功草稿')
        self.single(self.web, name='未保存的失败草稿')
        summary = get_dashboard_summary(self.web)
        self.assertEqual(summary['today_executions'], 2)
        self.assertEqual(summary['today_pass_rate'], 50)
        self.assertEqual(summary['total_cases'], 0)
        self.assertIn('未保存的失败草稿', get_dashboard_top_failures(self.web)[0]['test_case_name'])

    def test_web_drafts_without_identity_are_not_merged_just_because_names_match(self):
        self.single(self.web, name='生成草稿调试')
        self.single(self.web, name='生成草稿调试')
        rows = get_dashboard_top_failures(self.web)
        self.assertEqual(len(rows), 2)
        self.assertEqual([r['fail_count'] for r in rows], [1, 1])
        self.assertNotEqual(rows[0]['test_case_name'], rows[1]['test_case_name'])

    def test_summary_trend_use_child_verdicts_not_suite_counters(self):
        for project, suite_model, child_model in [
            (self.api, APITestSuiteExecutionDetail, APITestSuiteCaseExecution),
            (self.web, WebUITestSuiteExecutionDetail, WebUITestSuiteCaseExecution),
        ]:
            with self.subTest(project=project.project_type):
                self.single(project, status='passed')
                self.single(project, status='skipped', parent_status='stopped')
                suite = suite_model.objects.create(execution=self.execution(project, suite=True),
                                                   total_cases=99, passed_cases=98, failed_cases=1)
                for i, status in enumerate(('passed', 'failed', 'error', 'skipped', 'pending', 'incomplete')):
                    child_model.objects.create(suite_execution=suite, name=f'子用例{i}', status=status)
                summary = get_dashboard_summary(project)
                trend = get_dashboard_trend(project)
                self.assertEqual(summary['today_executions'], 3)
                self.assertEqual(summary['today_pass_rate'], 50)
                self.assertEqual(trend[-1], dict(date='2026-09-15', executions=3, passed=2, failed=2, pass_rate=50))
                self.assertEqual(sum(r['fail_count'] for r in get_dashboard_top_failures(project)), 2)

    def test_boundaries_timezone_running_and_other_project_are_excluded_consistently(self):
        start = datetime(2026, 9, 8, 16, tzinfo=dt_timezone.utc)
        self.single(name='第七天', when=start)
        self.single(name='超出七天', when=start - timedelta(microseconds=1))
        self.single(name='未来', when=datetime(2026, 9, 15, 16, tzinfo=dt_timezone.utc))
        self.single(name='尚未结束', parent_status='running')
        other = Project.objects.create(name='别人的 API', project_type='api', created_by=self.user)
        self.single(other, name='不能混入')
        self.single(name='今日成功', status='passed')
        self.assertEqual(get_dashboard_summary(self.api)['today_executions'], 1)
        trend = get_dashboard_trend(self.api)
        self.assertEqual(len(trend), 7)
        self.assertEqual(trend[0]['failed'], 1)
        self.assertEqual(trend[-1]['passed'], 1)
        self.assertEqual([r['test_case_name'] for r in get_dashboard_top_failures(self.api)], ['第七天'])

    def test_ai_ratio_uses_fixed_creation_source_not_case_type_or_current_script_source(self):
        manual = APITestCase.objects.create(project=self.api, title='手写场景', test_case_type='scenario', created_by=self.user)
        spec = APISpecification.objects.create(project=self.api, spec_name='测试规范', created_by=self.user)
        endpoint = APIEndpoint.objects.create(spec=spec, path='/health', method='GET')
        generated = APITestCase.objects.create(project=self.api, title='AI 端点', test_case_type='endpoint',
                                               endpoint=endpoint, created_by=self.user, creation_source='ai')
        APIWorkspace.objects.create(project=self.api, owner=self.user, saved_case=manual, model_id=1,
                                    generation={'status': 'passed', 'adopted_revision': None})
        for generation in ({'adopted_revision': 2}, {'adopted_revision': 3}, {}, {'adopted_revision': None}):
            APIWorkspace.objects.create(project=self.api, owner=self.user, saved_case=generated, generation=generation)
        self.assertEqual(get_dashboard_summary(self.api)['ai_contribution_rate'], 50)
        # Current script source can change without affecting the initial source.
        for initial, current in (('manual', 'mcp_exploration'), ('ai', 'manual'),
                                 ('ai', 'manual'), ('ai', 'mcp_exploration')):
            WebUITestCase.objects.create(project=self.web, title=initial, script_source=current,
                                         creation_source=initial, user=self.user)
        self.assertEqual(get_dashboard_summary(self.web)['ai_contribution_rate'], 75)

    def test_api_ratio_survives_workspace_regeneration_and_deletion(self):
        case = APITestCase.objects.create(project=self.api, title='AI 场景', test_case_type='scenario',
                                          created_by=self.user, creation_source='ai')
        workspace = APIWorkspace.objects.create(project=self.api, owner=self.user, saved_case=case,
                                                generation={'adopted_revision': 1})
        self.assertEqual(get_dashboard_summary(self.api)['ai_contribution_rate'], 100)
        workspace.generation = {}
        workspace.save(update_fields=['generation'])
        self.assertEqual(get_dashboard_summary(self.api)['ai_contribution_rate'], 100)
        workspace.delete()
        self.assertEqual(get_dashboard_summary(self.api)['ai_contribution_rate'], 100)

    def test_unknown_source_is_in_denominator_but_not_assumed_ai(self):
        for project, model, owner_kwargs in (
            (self.api, APITestCase, {'created_by': self.user, 'test_case_type': 'scenario'}),
            (self.web, WebUITestCase, {'user': self.user}),
        ):
            with self.subTest(project=project.project_type):
                for source in ('ai', 'manual', 'unknown'):
                    model.objects.create(project=project, title=source, creation_source=source, **owner_kwargs)
                summary = get_dashboard_summary(project)
                self.assertEqual(summary['total_cases'], 3)
                self.assertEqual(summary['ai_contribution_rate'], 33.33)

    def test_unadopted_or_manual_workspace_does_not_reclassify_saved_case(self):
        case = APITestCase.objects.create(project=self.api, title='手工场景', test_case_type='scenario',
                                          created_by=self.user, creation_source='manual')
        APIWorkspace.objects.create(project=self.api, owner=self.user, saved_case=case,
                                    generation={'adopted_revision': 1})
        self.assertEqual(get_dashboard_summary(self.api)['ai_contribution_rate'], 0)

    def test_endpoints_keep_permissions_and_return_503_on_statistics_error(self):
        client = APIClient()
        for kind in ('summary', 'trend', 'top-failures'):
            url = f'/api/v1/projects/{self.api.pk}/dashboard/{kind}/'
            self.assertEqual(client.get(url).status_code, 401)
        client.force_authenticate(self.user)
        url = f'/api/v1/projects/{self.api.pk}/dashboard/summary/'
        self.assertEqual(client.get(url).status_code, 404)
        member = ProjectMember.objects.create(project=self.api, user=self.user, can_view_reports=False)
        self.assertEqual(client.get(url).status_code, 403)
        member.can_view_reports = True
        member.save()
        self.assertEqual(client.get(url).status_code, 200)
        for kind, service in (('summary', 'summary'), ('trend', 'trend'), ('top-failures', 'top_failures')):
            with patch(f'projects.dashboard.views.get_dashboard_{service}', side_effect=RuntimeError('test error')), \
                    self.assertLogs('projects.dashboard.views', level='ERROR'):
                response = client.get(f'/api/v1/projects/{self.api.pk}/dashboard/{kind}/')
                self.assertEqual(response.status_code, 503)
                self.assertIn('暂时无法加载', response.data['message'])
                self.assertNotIn('today_executions', response.data)
        self.user.is_staff = True
        self.user.save()
        self.assertEqual(client.get(f'/api/v1/projects/{self.web.pk}/dashboard/summary/').status_code, 200)

    def test_empty_or_unsupported_projects(self):
        for project in (self.api, self.web, Project.objects.create(project_type='perf', created_by=self.user)):
            self.assertEqual(get_dashboard_summary(project)['total_cases'], 0)
            self.assertEqual(get_dashboard_top_failures(project), [])
            self.assertEqual(len(get_dashboard_trend(project)), 7)

    def test_top_five_is_ordered_and_bounded(self):
        for n in range(1, 8):
            for _ in range(n):
                self.single(name=f'场景{n}', snapshot={'workspace_id': n})
        self.assertEqual([r['fail_count'] for r in get_dashboard_top_failures(self.api)], [7, 6, 5, 4, 3])
