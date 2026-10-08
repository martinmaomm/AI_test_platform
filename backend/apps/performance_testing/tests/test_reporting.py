from html.parser import HTMLParser
import uuid
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from performance_testing.models import PerformanceAnalysis, PerformanceRun
from projects.models import Project, ProjectMember
from users.models import User


class ReportParser(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.tags = []
        self.attributes = []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        self.tags.append(tag)
        self.attributes.extend(attrs)


class PerformanceReportTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='report-owner', email='report-owner@example.test')
        self.reporter = User.objects.create_user(username='report-viewer', email='report-viewer@example.test')
        self.executor = User.objects.create_user(username='report-executor', email='report-executor@example.test')
        self.outsider = User.objects.create_user(username='report-outsider', email='report-outsider@example.test')
        self.project = Project.objects.create(name='报告', project_type='perf', created_by=self.user)
        self.other_project = Project.objects.create(name='其他', project_type='perf', created_by=self.user)
        for user, execute, report in ((self.user, True, True), (self.reporter, False, True), (self.executor, True, False)):
            ProjectMember.objects.create(project=self.project, user=user, role='editor', can_execute_tests=execute, can_view_reports=report)
        self.run = self.create_run()
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def create_run(self, **changes):
        values = dict(project=self.project, created_by=self.user, request_id=uuid.uuid4(), mode='load',
                      status='completed', snapshot={'plan_name': '商品搜索测试', 'users': 100, 'duration_seconds': 60},
                      snapshot_sha256='0' * 64, latest_metrics={'requests': 120, 'failures': 2,
                      'elapsed_seconds': 60, 'p95': 200, 'p99': 300, 'avg_response_time': 123, 'users': 0,
                      'entries': [{'method': 'GET', 'name': '商品搜索', 'requests': 120, 'failures': 2, 'p95': 200}]},
                      metrics_samples=[{'metrics': {'requests': 10, 'elapsed_seconds': 5, 'p95': 100, 'users': 5}},
                                       {'metrics': {'requests': 120, 'elapsed_seconds': 60, 'p95': 200, 'users': 100}}])
        values.update(changes)
        return PerformanceRun.objects.create(**values)

    def path(self, run=None):
        return f'/api/v1/projects/{self.project.pk}/performance/runs/{(run or self.run).pk}/report.html'

    def test_readonly_reporter_downloads_without_dispatching_ai_or_mutating_records(self):
        self.client.force_authenticate(self.reporter)
        before = list(PerformanceRun.objects.values())
        with patch('performance_testing.tasks.run_performance_analysis_async.apply_async') as dispatch:
            response = self.client.get(self.path())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'text/html; charset=utf-8')
        self.assertEqual(response['Content-Disposition'], f'attachment; filename="performance-{self.run.pk}.html"')
        self.assertEqual(response['Cache-Control'], 'no-store')
        self.assertEqual(response['X-Content-Type-Options'], 'nosniff')
        self.assertEqual(list(PerformanceRun.objects.values()), before)
        self.assertFalse(PerformanceAnalysis.objects.exists())
        dispatch.assert_not_called()
        html = response.content.decode()
        for text in ('商品搜索测试', '未配置验收标准', '尚无已完成的 AI 分析', '无需登录平台'):
            self.assertIn(text, html)
        self.assertEqual(html.count('<svg '), 3)

    def test_permissions_and_project_isolation(self):
        self.client.force_authenticate(None)
        self.assertIn(self.client.get(self.path()).status_code, (401, 403))
        for user, expected in ((self.executor, 403), (self.outsider, 404)):
            self.client.force_authenticate(user)
            self.assertEqual(self.client.get(self.path()).status_code, expected)
        self.client.force_authenticate(self.user)
        foreign = self.create_run(project=self.other_project)
        self.assertEqual(self.client.get(self.path(foreign)).status_code, 404)

    def test_only_terminal_formal_runs_and_get_are_supported(self):
        for status in PerformanceRun.ACTIVE_STATUSES:
            self.assertEqual(self.client.get(self.path(self.create_run(status=status))).status_code, 409)
        self.assertEqual(self.client.get(self.path(self.create_run(mode='validation'))).status_code, 409)
        for status in PerformanceRun.TERMINAL_STATUSES:
            self.assertEqual(self.client.get(self.path(self.create_run(status=status))).status_code, 200)
        self.assertEqual(self.client.post(self.path(), {}, format='json').status_code, 405)

    def test_self_contained_escaped_allowlist_omits_raw_secrets_and_non_summary_analyses(self):
        secret = 'SENSITIVE_FIXTURE_DO_NOT_EXPORT'
        attack = '</style><script>alert(1)</script><img src="https://example.test/steal">'
        self.run.snapshot.update({'plan_name': attack, 'headers': {'Cookie': secret}, 'variables': {'token': secret},
                                  'steps': [{'name': secret, 'url': 'https://example.test/?token=' + secret}]})
        self.run.latest_metrics.update({'logs': secret, 'response': secret, 'failure_samples': [
            {'error_type': 'assertion_failed', 'message': secret, 'expected': secret, 'actual': secret}]})
        self.run.latest_metrics['entries'][0]['name'] = secret
        self.run.reason = secret
        self.run.save()
        common = dict(run=self.run, created_by=self.user, request_hash='0' * 64, model_config_id=1,
                      queued_deadline_at=timezone.now(), model_info={'api_key': secret}, input_snapshot={'secret': secret})
        saved = PerformanceAnalysis.objects.create(**common, request_id=uuid.uuid4(), status='completed',
            result={'summary': attack, 'findings': [{'title': attack, 'detail': attack,
                    'recommendation': attack, 'kind': 'hypothesis'}], 'limitations': [attack],
                    'extra': secret, 'evidence': [{'value': secret}]})
        PerformanceAnalysis.objects.create(**common, request_id=uuid.uuid4(), status='completed',
            analysis_type='load_comparison', result={'summary': secret})
        PerformanceAnalysis.objects.create(**common, request_id=uuid.uuid4(), status='failed', result={'summary': secret})
        html = self.client.get(self.path()).content.decode()
        self.assertNotIn(secret, html)
        self.assertIn('&lt;script&gt;alert(1)&lt;/script&gt;', html)
        self.assertIn('待验证推测', html)
        self.assertIn('失败定位与排查建议', html)
        parser = ReportParser(html)
        self.assertFalse(set(parser.tags) & {'script', 'img', 'iframe', 'object', 'embed', 'link', 'form', 'a'})
        self.assertFalse(any(key.startswith('on') or key in ('src', 'href', 'srcset') for key, _ in parser.attributes))
        self.assertIn("connect-src 'none'", html)
        self.assertIn("script-src 'none'", html)
        self.assertEqual(PerformanceAnalysis.objects.get(pk=saved.pk).result['summary'], attack)

    def test_frozen_acceptance_and_missing_metrics_remain_explicit(self):
        self.run.acceptance_targets = {'p95_ms': 100, 'rps_min': 1}
        self.run.latest_metrics = {}
        self.run.metrics_samples = []
        self.run.save()
        html = self.client.get(self.path()).content.decode()
        self.assertIn('数据不足', html)
        self.assertIn('≤ 100 ms', html)
        self.assertIn('≥ 1 次/秒', html)
        self.assertEqual(html.count('暂无有效采样'), 3)
        self.assertNotIn('nan', html.lower())
        self.assertNotIn('<svg ', html)
