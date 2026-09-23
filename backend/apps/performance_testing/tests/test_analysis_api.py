from datetime import timedelta
import math
import uuid
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from ai_core.models import LLMConfiguration
from performance_testing.analysis_serializers import PerformanceAnalysisCreateSerializer
from performance_testing.models import PerformanceAnalysis, PerformanceRun
from projects.models import Project, ProjectMember
from users.models import User


class PerformanceAnalysisAPITests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(
            username='analysis-user', email='analysis-user@example.test',
        )
        self.reporter = User.objects.create_user(
            username='analysis-reporter', email='analysis-reporter@example.test',
        )
        self.executor = User.objects.create_user(
            username='analysis-executor', email='analysis-executor@example.test',
        )
        self.outsider = User.objects.create_user(
            username='analysis-outsider', email='analysis-outsider@example.test',
        )
        self.project = Project.objects.create(
            name='analysis', project_type='perf', created_by=self.user,
        )
        self.other_project = Project.objects.create(
            name='other-analysis', project_type='perf', created_by=self.user,
        )
        ProjectMember.objects.create(
            project=self.project, user=self.user, role='editor',
            can_execute_tests=True, can_view_reports=True,
        )
        ProjectMember.objects.create(
            project=self.project, user=self.reporter, role='viewer',
            can_execute_tests=False, can_view_reports=True,
        )
        ProjectMember.objects.create(
            project=self.project, user=self.executor, role='editor',
            can_execute_tests=True, can_view_reports=False,
        )
        self.model = LLMConfiguration.objects.create(
            model_type='llm', provider='offline', provider_name='离线模型',
            model_name='analysis-fixture', api_key='fixture-secret',
            created_by=self.user, is_active=True,
        )
        self.run = self._run()
        self.client.force_authenticate(self.user)

    def _run(self, project=None, **kwargs):
        defaults = {
            'project': project or self.project,
            'created_by': self.user,
            'request_id': uuid.uuid4(),
            'mode': PerformanceRun.Mode.LOAD,
            'status': PerformanceRun.Status.COMPLETED,
            'snapshot': {},
            'snapshot_sha256': '0' * 64,
            'latest_metrics': {'requests': 10, 'failures': 0, 'complete': True},
        }
        defaults.update(kwargs)
        return PerformanceRun.objects.create(**defaults)

    def _path(self, run=None, suffix=''):
        run = run or self.run
        return (
            f'/api/v1/projects/{run.project_id}/performance/runs/{run.pk}/analyses/'
            f'{suffix}'
        )

    def _payload(self, request_id=None, **overrides):
        payload = {
            'request_id': str(request_id or uuid.uuid4()),
            'model_config_id': self.model.pk,
            'targets': {'p95_ms': 500, 'error_rate_percent': 1, 'rps_min': 10},
        }
        payload.update(overrides)
        return payload

    def test_create_list_and_detail_expose_only_safe_contract(self):
        payload = self._payload()
        with patch(
            'performance_testing.tasks.run_performance_analysis_async.apply_async',
        ) as queued, self.captureOnCommitCallbacks(execute=True):
            created = self.client.post(self._path(), payload, format='json')
        self.assertEqual(created.status_code, 202, created.data)
        data = created.data['data']
        self.assertEqual(data['run_id'], str(self.run.pk))
        self.assertEqual(data['status'], 'queued')
        self.assertEqual(data['model_info'], {
            'config_id': self.model.pk,
            'name': '离线模型 - analysis-fixture',
            'provider': 'offline',
            'model_name': 'analysis-fixture',
        })
        self.assertNotIn('api_key', str(data))
        self.assertNotIn('base_url', str(data))
        queued.assert_called_once()
        self.assertEqual(queued.call_args.kwargs['args'], (data['id'],))

        listed = self.client.get(self._path())
        self.assertEqual(listed.status_code, 200, listed.data)
        self.assertEqual([item['id'] for item in listed.data['data']['items']], [data['id']])
        detail = self.client.get(self._path(suffix=f'{data["id"]}/'))
        self.assertEqual(detail.status_code, 200, detail.data)
        self.assertEqual(detail.data['data'], data)

    def test_create_requires_report_and_execute_but_read_only_requires_report(self):
        self.client.force_authenticate(self.reporter)
        self.assertEqual(self.client.get(self._path()).status_code, 200)
        self.assertEqual(
            self.client.post(self._path(), self._payload(), format='json').status_code, 403,
        )

        self.client.force_authenticate(self.executor)
        self.assertEqual(self.client.get(self._path()).status_code, 403)
        self.assertEqual(
            self.client.post(self._path(), self._payload(), format='json').status_code, 403,
        )

        self.client.force_authenticate(self.outsider)
        self.assertEqual(self.client.get(self._path()).status_code, 404)

    def test_only_terminal_load_runs_accept_analysis(self):
        active = self._run(status=PerformanceRun.Status.RUNNING)
        validation = self._run(mode=PerformanceRun.Mode.VALIDATION)
        with patch('performance_testing.tasks.run_performance_analysis_async.apply_async'):
            for run in (active, validation):
                response = self.client.post(self._path(run), self._payload(), format='json')
                self.assertEqual(response.status_code, 409, response.data)
        self.assertFalse(PerformanceAnalysis.objects.filter(run__in=(active, validation)).exists())

    def test_strict_request_validation_rejects_unknown_boolean_and_nonfinite_numbers(self):
        invalid_payloads = (
            self._payload(extra=True),
            self._payload(model_config_id=True),
            self._payload(model_config_id=10 ** 100),
            self._payload(targets={'p95_ms': True}),
            self._payload(targets={'error_rate_percent': -1}),
            self._payload(targets={'error_rate_percent': 101}),
            self._payload(targets={'p95_ms': 0}),
            self._payload(targets={'rps_min': 0}),
            self._payload(targets={'p95_ms': 1, 'unexpected': 2}),
        )
        for payload in invalid_payloads:
            response = self.client.post(self._path(), payload, format='json')
            self.assertEqual(response.status_code, 400, (payload, response.data))

        for value in (math.nan, math.inf, -math.inf, 10 ** 10000):
            serializer = PerformanceAnalysisCreateSerializer(data=self._payload(
                targets={'p95_ms': value},
            ))
            self.assertFalse(serializer.is_valid(), value)

    def test_request_id_is_idempotent_and_active_work_is_exclusive(self):
        request_id = uuid.uuid4()
        payload = self._payload(request_id)
        with patch(
            'performance_testing.tasks.run_performance_analysis_async.apply_async',
        ) as queued, self.captureOnCommitCallbacks(execute=True):
            first = self.client.post(self._path(), payload, format='json')
        self.assertEqual(first.status_code, 202, first.data)
        queued.assert_called_once()

        repeated = self.client.post(self._path(), payload, format='json')
        self.assertEqual(repeated.status_code, 200, repeated.data)
        self.assertEqual(repeated.data['data']['id'], first.data['data']['id'])

        changed = self.client.post(self._path(), {
            **payload, 'targets': {'p95_ms': 501},
        }, format='json')
        self.assertEqual(changed.status_code, 409, changed.data)

        other = self.client.post(self._path(), self._payload(), format='json')
        self.assertEqual(other.status_code, 409, other.data)

        PerformanceAnalysis.objects.filter(pk=first.data['data']['id']).update(
            status=PerformanceAnalysis.Status.FAILED,
            finished_at=timezone.now(),
        )
        with patch(
            'performance_testing.tasks.run_performance_analysis_async.apply_async',
        ), self.captureOnCommitCallbacks(execute=True):
            retry = self.client.post(self._path(), self._payload(), format='json')
        self.assertEqual(retry.status_code, 202, retry.data)

    def test_queue_failure_is_sanitized_and_record_remains_readable(self):
        with patch(
            'performance_testing.tasks.run_performance_analysis_async.apply_async',
            side_effect=RuntimeError('redis://user:secret@broker'),
        ), self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(self._path(), self._payload(), format='json')
        # TestCase owns an outer transaction, so its captured on_commit callback
        # runs after the response is built. In production autocommit the view
        # refreshes the failed row and responds 503 instead.
        self.assertEqual(response.status_code, 202, response.data)
        analysis = PerformanceAnalysis.objects.get(run=self.run)
        self.assertEqual(analysis.status, PerformanceAnalysis.Status.FAILED)
        self.assertEqual(analysis.error_code, 'queue_unavailable')
        self.assertNotIn('secret', analysis.error_message)
        listed = self.client.get(self._path())
        self.assertEqual(listed.data['data']['items'][0]['id'], str(analysis.pk))

    def test_reads_recover_expired_work_and_keep_cross_project_ids_hidden(self):
        now = timezone.now()
        queued = PerformanceAnalysis.objects.create(
            run=self.run, created_by=self.user, request_id=uuid.uuid4(),
            request_hash='a' * 64, model_config_id=self.model.pk,
            model_info={}, targets={},
            queued_deadline_at=now - timedelta(seconds=1),
        )
        running = PerformanceAnalysis.objects.create(
            run=self.run, created_by=self.user, request_id=uuid.uuid4(),
            request_hash='b' * 64, model_config_id=self.model.pk,
            model_info={}, targets={}, status=PerformanceAnalysis.Status.RUNNING,
            queued_deadline_at=now + timedelta(seconds=30),
            running_deadline_at=now - timedelta(seconds=1), started_at=now - timedelta(minutes=4),
        )
        response = self.client.get(self._path())
        self.assertEqual(response.status_code, 200, response.data)
        queued.refresh_from_db()
        running.refresh_from_db()
        self.assertEqual((queued.status, queued.error_code), ('failed', 'queue_timeout'))
        self.assertEqual((running.status, running.error_code), ('failed', 'analysis_timeout'))

        other_run = self._run(project=self.other_project)
        other = PerformanceAnalysis.objects.create(
            run=other_run, created_by=self.user, request_id=uuid.uuid4(),
            request_hash='c' * 64, model_config_id=self.model.pk,
            model_info={}, targets={}, queued_deadline_at=now + timedelta(minutes=5),
        )
        self.assertEqual(self.client.get(self._path(suffix=f'{other.pk}/')).status_code, 404)
