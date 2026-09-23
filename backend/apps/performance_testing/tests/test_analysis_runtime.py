from datetime import timedelta
import uuid
from unittest.mock import patch

from celery.exceptions import SoftTimeLimitExceeded
from django.test import TestCase
from django.utils import timezone

from ai_core.models import LLMConfiguration
from performance_testing.analysis_engine import AnalysisOutputError
from performance_testing.analysis_runtime import (
    _finish, execute_analysis, recover_expired_analyses,
)
from performance_testing.models import PerformanceAnalysis, PerformanceRun
from project_knowledge.llm import KnowledgeLLMTimeout
from projects.models import Project, ProjectMember
from users.models import User


class PerformanceAnalysisRuntimeTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='analysis-worker', email='analysis-worker@example.test',
        )
        self.project = Project.objects.create(
            name='analysis-worker-project', project_type='perf', created_by=self.user,
        )
        self.membership = ProjectMember.objects.create(
            project=self.project, user=self.user, role='editor',
            can_execute_tests=True, can_view_reports=True,
        )
        self.model = LLMConfiguration.objects.create(
            model_type='llm', provider='offline', provider_name='Offline',
            model_name='analysis-runtime', api_key='fixture-key',
            created_by=self.user, is_active=True,
        )
        self.run = PerformanceRun.objects.create(
            project=self.project, created_by=self.user,
            request_id=uuid.uuid4(), mode=PerformanceRun.Mode.LOAD,
            status=PerformanceRun.Status.COMPLETED,
            snapshot={'users': 5, 'duration_seconds': 30},
            snapshot_sha256='0' * 64,
            latest_metrics={
                'requests': 10, 'failures': 0, 'rps': 2,
                'p95': 100, 'complete': True,
            },
        )

    def _analysis(self, **kwargs):
        defaults = {
            'run': self.run,
            'created_by': self.user,
            'request_id': uuid.uuid4(),
            'request_hash': uuid.uuid4().hex * 2,
            'model_config_id': self.model.pk,
            'model_info': {
                'config_id': self.model.pk,
                'name': 'Offline - analysis-runtime',
                'provider': 'offline',
                'model_name': 'analysis-runtime',
            },
            'targets': {'p95_ms': 200},
            'queued_deadline_at': timezone.now() + timedelta(minutes=5),
        }
        defaults.update(kwargs)
        return PerformanceAnalysis.objects.create(**defaults)

    @staticmethod
    def _result(payload):
        return {
            'summary': 'fixture summary',
            'findings': [],
            'limitations': payload['limitations'],
            'assessment': payload['assessment'],
            'evidence': payload['evidence'],
        }

    def test_success_persists_snapshot_and_result_and_redelivery_is_idempotent(self):
        analysis = self._analysis()

        def generate(model_config_id, payload, check_active, remaining_seconds):
            self.assertEqual(model_config_id, self.model.pk)
            self.assertTrue(callable(check_active))
            self.assertTrue(callable(remaining_seconds))
            check_active()
            self.assertGreater(remaining_seconds(), 0)
            return self._result(payload)

        with patch(
            'performance_testing.analysis_engine.generate_analysis', side_effect=generate,
        ) as mocked:
            first = execute_analysis(str(analysis.pk))
            second = execute_analysis(str(analysis.pk))
        self.assertEqual(first['status'], 'completed')
        self.assertEqual(second['status'], 'skipped')
        mocked.assert_called_once()
        analysis.refresh_from_db()
        self.assertEqual(analysis.status, PerformanceAnalysis.Status.COMPLETED)
        self.assertEqual(analysis.result['summary'], 'fixture summary')
        self.assertEqual(analysis.input_snapshot['schema_version'], 1)
        self.assertEqual(analysis.error_code, '')
        self.assertIsNotNone(analysis.started_at)
        self.assertIsNotNone(analysis.finished_at)

    def test_worker_rechecks_permission_and_model_before_claim(self):
        permission = self._analysis()
        self.membership.can_execute_tests = False
        self.membership.save(update_fields=('can_execute_tests',))
        with patch('performance_testing.analysis_engine.generate_analysis') as generate:
            execute_analysis(str(permission.pk))
        generate.assert_not_called()
        permission.refresh_from_db()
        self.assertEqual((permission.status, permission.error_code), ('failed', 'permission_revoked'))

        self.membership.can_execute_tests = True
        self.membership.save(update_fields=('can_execute_tests',))
        changed_model = self._analysis()
        self.model.model_name = 'changed-after-queue'
        self.model.save(update_fields=('model_name',))
        execute_analysis(str(changed_model.pk))
        changed_model.refresh_from_db()
        self.assertEqual((changed_model.status, changed_model.error_code), ('failed', 'model_unavailable'))

    def test_permission_is_rechecked_after_model_returns(self):
        analysis = self._analysis()

        def revoke_then_return(model_config_id, payload, check_active, remaining_seconds):
            ProjectMember.objects.filter(pk=self.membership.pk).update(can_view_reports=False)
            return self._result(payload)

        with patch(
            'performance_testing.analysis_engine.generate_analysis',
            side_effect=revoke_then_return,
        ):
            response = execute_analysis(str(analysis.pk))
        self.assertEqual(response['error_code'], 'permission_revoked')
        analysis.refresh_from_db()
        self.assertEqual((analysis.status, analysis.error_code), ('failed', 'permission_revoked'))
        self.assertIsNone(analysis.result)

    def test_known_and_unknown_failures_store_only_safe_messages(self):
        cases = (
            (KnowledgeLLMTimeout('provider detail'), 'analysis_timeout'),
            (AnalysisOutputError('raw invalid output'), 'INVALID_MODEL_OUTPUT'),
            (SoftTimeLimitExceeded('hard detail'), 'analysis_timeout'),
            (RuntimeError('https://user:secret@provider.invalid'), 'model_error'),
        )
        for exception, expected_code in cases:
            analysis = self._analysis()
            with patch(
                'performance_testing.analysis_engine.generate_analysis',
                side_effect=exception,
            ), self.assertLogs(
                'performance_testing.analysis_runtime', level='ERROR',
            ) if expected_code == 'model_error' else _NoopContext():
                response = execute_analysis(str(analysis.pk))
            self.assertEqual(response['error_code'], expected_code)
            analysis.refresh_from_db()
            self.assertEqual((analysis.status, analysis.error_code), ('failed', expected_code))
            self.assertNotIn('secret', analysis.error_message)
            self.assertNotIn('provider detail', analysis.error_message)
            self.assertNotIn('raw invalid output', analysis.error_message)
            self.assertNotIn('hard detail', analysis.error_message)

    def test_expiry_and_terminal_cas_prevent_late_result_overwrite(self):
        now = timezone.now()
        analysis = self._analysis(
            status=PerformanceAnalysis.Status.RUNNING,
            started_at=now - timedelta(minutes=4),
            running_deadline_at=now - timedelta(seconds=1),
        )
        self.assertEqual(recover_expired_analyses(self.run.pk), 1)
        analysis.refresh_from_db()
        self.assertEqual((analysis.status, analysis.error_code), ('failed', 'analysis_timeout'))
        updated = _finish(
            analysis.pk, PerformanceAnalysis.Status.RUNNING,
            status=PerformanceAnalysis.Status.COMPLETED,
            result={'summary': 'late'},
        )
        self.assertFalse(updated)
        analysis.refresh_from_db()
        self.assertEqual(analysis.status, PerformanceAnalysis.Status.FAILED)
        self.assertIsNone(analysis.result)

    def test_expired_queue_delivery_is_skipped(self):
        analysis = self._analysis(
            queued_deadline_at=timezone.now() - timedelta(seconds=1),
        )
        with patch('performance_testing.analysis_engine.generate_analysis') as generate:
            response = execute_analysis(str(analysis.pk))
        self.assertEqual(response['status'], 'skipped')
        generate.assert_not_called()
        analysis.refresh_from_db()
        self.assertEqual((analysis.status, analysis.error_code), ('failed', 'queue_timeout'))


class _NoopContext:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False
