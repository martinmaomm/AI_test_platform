import uuid
from unittest.mock import patch

from django.test import TestCase
from rest_framework.test import APIClient

from performance_testing.models import PerformanceAnalysis, PerformanceRun, PerformancePlan, PerformanceTarget
from projects.models import Project, ProjectMember
from users.models import User


class ComparisonAPITests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='compare-owner', email='compare-owner@example.test')
        self.reporter = User.objects.create_user(username='compare-reporter', email='compare-reporter@example.test')
        self.executor = User.objects.create_user(username='compare-executor', email='compare-executor@example.test')
        self.outsider = User.objects.create_user(username='compare-outsider', email='compare-outsider@example.test')
        self.project = Project.objects.create(name='Compare', project_type='perf', created_by=self.user)
        self.other_project = Project.objects.create(name='Other', project_type='perf', created_by=self.user)
        for user, execute, report in ((self.user, True, True), (self.reporter, False, True), (self.executor, True, False)):
            ProjectMember.objects.create(project=self.project, user=user, role='editor', can_execute_tests=execute, can_view_reports=report)
        self.current = self.create_run()
        self.baseline = self.create_run()
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def create_run(self, **changes):
        values = dict(project=self.project, created_by=self.user, request_id=uuid.uuid4(), mode='load',
                      status='completed', snapshot={'plan_name': '历史快照名称'}, snapshot_sha256='0' * 64,
                      latest_metrics={'requests': 10, 'failures': 0})
        values.update(changes)
        return PerformanceRun.objects.create(**values)

    def path(self, run=None, candidates=False):
        return f'/api/v1/projects/{self.project.pk}/performance/runs/{(run or self.current).pk}/' + ('comparison-candidates/' if candidates else 'comparison/')

    def test_read_only_reporter_can_compare_without_creating_or_dispatching_analysis(self):
        self.client.force_authenticate(self.reporter)
        before = list(PerformanceRun.objects.values())
        with patch('performance_testing.tasks.run_performance_analysis_async.apply_async') as queued:
            response = self.client.get(self.path(), {'baseline_run_id': self.baseline.pk})
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['data']['baseline']['id'], str(self.baseline.pk))
        self.assertEqual(response.data['data']['comparability']['status'], 'insufficient_data')
        self.assertEqual(list(PerformanceRun.objects.values()), before)
        self.assertFalse(PerformanceAnalysis.objects.exists())
        queued.assert_not_called()

    def test_permissions_and_project_isolation(self):
        for user, code in ((self.executor, 403), (self.outsider, 404)):
            self.client.force_authenticate(user)
            self.assertEqual(self.client.get(self.path(), {'baseline_run_id': self.baseline.pk}).status_code, code)
            self.assertEqual(self.client.get(self.path(candidates=True)).status_code, code)
        self.client.force_authenticate(self.user)
        other = self.create_run(project=self.other_project)
        self.assertEqual(self.client.get(self.path(), {'baseline_run_id': other.pk}).status_code, 404)

    def test_terminal_load_distinct_and_strict_query(self):
        self.assertEqual(self.client.get(self.path(), {'baseline_run_id': self.current.pk}).status_code, 409)
        for changes in ({'mode': 'validation'}, {'status': 'running'}):
            invalid = self.create_run(**changes)
            self.assertEqual(self.client.get(self.path(), {'baseline_run_id': invalid.pk}).status_code, 409)
            self.assertEqual(self.client.get(self.path(invalid), {'baseline_run_id': self.baseline.pk}).status_code, 409)
            self.assertEqual(self.client.get(self.path(invalid, True)).status_code, 409)
        for query in ({}, {'baseline_run_id': 'invalid'}, {'baseline_run_id': self.baseline.pk, 'surprise': 'x'},
                      {'baseline_run_id': [str(self.baseline.pk), str(self.current.pk)]}):
            self.assertEqual(self.client.get(self.path(), query).status_code, 400)

    def test_candidates_only_same_project_other_terminal_load_without_raw_data(self):
        for changes in ({'project': self.other_project}, {'mode': 'validation'}, {'status': 'running'}):
            self.create_run(**changes)
        response = self.client.get(self.path(candidates=True))
        self.assertEqual(response.status_code, 200, response.data)
        rows = response.data['data']['items']
        self.assertEqual([row['id'] for row in rows], [str(self.baseline.pk)])
        self.assertEqual(set(rows[0]), {'id', 'plan_name', 'status', 'created_at', 'same_plan'})

    def test_candidates_prefer_same_plan_history_and_bound_results(self):
        target = PerformanceTarget.objects.create(project=self.project, name='target', base_url='http://example.test')
        plan = PerformancePlan.objects.create(project=self.project, target=target, name='plan')
        historical = self.create_run(plan=plan)
        current = self.create_run(plan=plan)
        later = self.create_run(plan=plan)
        for _ in range(101):
            self.create_run()
        response = self.client.get(self.path(current, True))
        self.assertEqual(response.status_code, 200, response.data)
        items = response.data['data']['items']
        self.assertEqual(len(items), 100)
        self.assertEqual([item['id'] for item in items[:2]], [str(historical.pk), str(later.pk)])
        self.assertTrue(all(item['same_plan'] for item in items[:2]))
        self.assertFalse(items[2]['same_plan'])
