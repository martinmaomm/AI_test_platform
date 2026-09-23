"""Durable lifecycle for passive performance-run analysis tasks."""
from __future__ import annotations

import hashlib
import json
import logging
import time
import uuid
from datetime import timedelta

from celery.exceptions import SoftTimeLimitExceeded
from django.db import transaction
from django.http import Http404
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied

from ai_core.config_access import usable_llm_configurations
from projects.access import EXECUTE, REPORT, get_project_for_user

from .models import PerformanceAnalysis, PerformanceRun


logger = logging.getLogger(__name__)

ANALYSIS_QUEUE_TIMEOUT_SECONDS = 300
ANALYSIS_RUN_TIMEOUT_SECONDS = 180


class AnalysisConflict(Exception):
    pass


class AnalysisUnavailable(Exception):
    pass


class TaskStopped(Exception):
    """Stable cancellation signal understood by the shared streaming adapter."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


_SAFE_FAILURES = {
    'queue_unavailable': '分析任务暂时无法进入队列，请稍后重新分析。',
    'queue_timeout': '分析任务排队超时，请重新分析。',
    'analysis_timeout': '分析任务执行超时，请重新分析。',
    'permission_revoked': '项目权限已变化，分析任务已停止。',
    'model_unavailable': '所选模型已停用或不可用，请重新选择模型。',
    'run_unavailable': '压测运行已不可用于分析。',
    'INVALID_MODEL_OUTPUT': '模型返回的分析结构无效，请重新分析。',
    'model_error': '模型服务调用失败，请稍后重新分析。',
    'analysis_failed': '分析任务执行失败，请重新分析。',
}


def _request_hash(model_config_id: int, targets: dict) -> str:
    encoded = json.dumps(
        {'model_config_id': model_config_id, 'targets': targets},
        ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False,
    ).encode('utf-8')
    return hashlib.sha256(encoded).hexdigest()


def _model(model_config_id: int):
    return usable_llm_configurations().filter(pk=model_config_id).first()


def _model_info(config) -> dict:
    display = f'{config.get_provider_display()} - {config.model_name}'
    return {
        'config_id': config.pk,
        'name': display,
        'provider': config.provider,
        'model_name': config.model_name,
    }


def _recover_locked(run_id, now=None) -> int:
    now = now or timezone.now()
    queued = PerformanceAnalysis.objects.filter(
        run_id=run_id,
        status=PerformanceAnalysis.Status.QUEUED,
        queued_deadline_at__lte=now,
    ).update(
        status=PerformanceAnalysis.Status.FAILED,
        error_code='queue_timeout',
        error_message=_SAFE_FAILURES['queue_timeout'],
        finished_at=now,
    )
    running = PerformanceAnalysis.objects.filter(
        run_id=run_id,
        status=PerformanceAnalysis.Status.RUNNING,
        running_deadline_at__lte=now,
    ).update(
        status=PerformanceAnalysis.Status.FAILED,
        error_code='analysis_timeout',
        error_message=_SAFE_FAILURES['analysis_timeout'],
        finished_at=now,
    )
    return queued + running


def recover_expired_analyses(run_id) -> int:
    with transaction.atomic():
        run = PerformanceRun.objects.select_for_update().filter(pk=run_id).first()
        if run is None:
            return 0
        return _recover_locked(run.pk)


def _finish(analysis_id, expected_status, *, status, result=None, code='') -> bool:
    """Finish through a status CAS so an expired task cannot overwrite recovery."""
    run_id = PerformanceAnalysis.objects.filter(pk=analysis_id).values_list('run_id', flat=True).first()
    if run_id is None:
        return False
    with transaction.atomic():
        if not PerformanceRun.objects.select_for_update().filter(pk=run_id).exists():
            return False
        _recover_locked(run_id)
        values = {
            'status': status,
            'finished_at': timezone.now(),
            'error_code': code,
            'error_message': _SAFE_FAILURES.get(code, '') if code else '',
        }
        if result is not None:
            values['result'] = result
        return bool(PerformanceAnalysis.objects.filter(
            pk=analysis_id, status=expected_status,
        ).update(**values))


def enqueue_analysis(run_id, user, request_id, model_config_id, targets):
    """Create one analysis under the run lock and publish only after commit."""
    fingerprint = _request_hash(model_config_id, targets)
    with transaction.atomic():
        run = PerformanceRun.objects.select_for_update().filter(pk=run_id).first()
        if run is None:
            raise PerformanceRun.DoesNotExist
        _recover_locked(run.pk)

        existing = PerformanceAnalysis.objects.filter(
            run=run, request_id=request_id,
        ).first()
        if existing is not None:
            if existing.request_hash != fingerprint:
                raise AnalysisConflict('相同 request_id 对应的分析内容已变化，请使用新的请求编号。')
            return existing, False

        if run.mode != PerformanceRun.Mode.LOAD or run.status not in PerformanceRun.TERMINAL_STATUSES:
            raise AnalysisUnavailable('仅已结束的正式压测运行可以发起分析。')
        if PerformanceAnalysis.objects.filter(
            run=run, status__in=PerformanceAnalysis.ACTIVE_STATUSES,
        ).exists():
            raise AnalysisConflict('该压测运行已有排队中或执行中的分析。')

        config = _model(model_config_id)
        if config is None:
            raise AnalysisUnavailable('所选模型已停用或不可用。')

        now = timezone.now()
        celery_task_id = str(uuid.uuid4())
        analysis = PerformanceAnalysis.objects.create(
            run=run,
            created_by=user,
            request_id=request_id,
            request_hash=fingerprint,
            model_config_id=config.pk,
            model_info=_model_info(config),
            targets=targets,
            celery_task_id=celery_task_id,
            queued_deadline_at=now + timedelta(seconds=ANALYSIS_QUEUE_TIMEOUT_SECONDS),
        )

        def dispatch():
            try:
                from .tasks import run_performance_analysis_async
                run_performance_analysis_async.apply_async(
                    args=(str(analysis.pk),), task_id=celery_task_id,
                )
            except Exception as exc:
                logger.error(
                    'Performance analysis queue submission failed: analysis=%s error_type=%s',
                    analysis.pk, type(exc).__name__,
                )
                _finish(
                    analysis.pk, PerformanceAnalysis.Status.QUEUED,
                    status=PerformanceAnalysis.Status.FAILED, code='queue_unavailable',
                )

        transaction.on_commit(dispatch)
    analysis.refresh_from_db()
    return analysis, True


def _require_runtime_access(analysis):
    if analysis.created_by_id is None or not analysis.created_by.is_active:
        raise TaskStopped('permission_revoked')
    try:
        get_project_for_user(
            analysis.run.project_id, analysis.created_by, REPORT,
            expected_project_type='perf',
        )
        get_project_for_user(
            analysis.run.project_id, analysis.created_by, EXECUTE,
            expected_project_type='perf',
        )
    except (Http404, PermissionDenied) as exc:
        raise TaskStopped('permission_revoked') from exc
    config = _model(analysis.model_config_id)
    if config is None or (
        config.provider != analysis.model_info.get('provider')
        or config.model_name != analysis.model_info.get('model_name')
    ):
        raise TaskStopped('model_unavailable')
    if (
        analysis.run.mode != PerformanceRun.Mode.LOAD
        or analysis.run.status not in PerformanceRun.TERMINAL_STATUSES
    ):
        raise TaskStopped('run_unavailable')


class AnalysisContext:
    def __init__(self, analysis):
        self.analysis_id = analysis.pk
        self.deadline = time.monotonic() + ANALYSIS_RUN_TIMEOUT_SECONDS

    def remaining_seconds(self):
        return max(0.0, self.deadline - time.monotonic())

    def check_active(self):
        analysis = PerformanceAnalysis.objects.select_related(
            'created_by', 'run',
        ).filter(pk=self.analysis_id).first()
        if analysis is None or analysis.status != PerformanceAnalysis.Status.RUNNING:
            raise TaskStopped('analysis_timeout')
        if (
            self.remaining_seconds() <= 0
            or analysis.running_deadline_at is None
            or analysis.running_deadline_at <= timezone.now()
        ):
            raise TaskStopped('analysis_timeout')
        _require_runtime_access(analysis)
        return True


def _claim(analysis_id):
    run_id = PerformanceAnalysis.objects.filter(pk=analysis_id).values_list('run_id', flat=True).first()
    if run_id is None:
        return None
    with transaction.atomic():
        run = PerformanceRun.objects.select_for_update().filter(pk=run_id).first()
        if run is None:
            return None
        _recover_locked(run.pk)
        analysis = PerformanceAnalysis.objects.select_for_update().select_related(
            'created_by', 'run',
        ).filter(pk=analysis_id).first()
        if analysis is None or analysis.status != PerformanceAnalysis.Status.QUEUED:
            return None
        try:
            _require_runtime_access(analysis)
        except TaskStopped as exc:
            analysis.status = PerformanceAnalysis.Status.FAILED
            analysis.error_code = exc.code
            analysis.error_message = _SAFE_FAILURES[exc.code]
            analysis.finished_at = timezone.now()
            analysis.save(update_fields=('status', 'error_code', 'error_message', 'finished_at'))
            return None
        now = timezone.now()
        analysis.status = PerformanceAnalysis.Status.RUNNING
        analysis.started_at = now
        analysis.running_deadline_at = now + timedelta(seconds=ANALYSIS_RUN_TIMEOUT_SECONDS)
        analysis.error_code = ''
        analysis.error_message = ''
        analysis.save(update_fields=(
            'status', 'started_at', 'running_deadline_at', 'error_code', 'error_message',
        ))
        return analysis


def execute_analysis(analysis_id):
    analysis = _claim(analysis_id)
    if analysis is None:
        return {'analysis_id': str(analysis_id), 'status': 'skipped'}

    context = AnalysisContext(analysis)
    try:
        from project_knowledge.llm import KnowledgeLLMTimeout
        from .analysis_data import build_analysis_input
        from .analysis_engine import AnalysisOutputError, generate_analysis

        context.check_active()
        run = PerformanceRun.objects.prefetch_related('participants').get(pk=analysis.run_id)
        payload = build_analysis_input(run, analysis.targets)
        updated = PerformanceAnalysis.objects.filter(
            pk=analysis.pk, status=PerformanceAnalysis.Status.RUNNING,
        ).update(input_snapshot=payload)
        if not updated:
            raise TaskStopped('analysis_timeout')
        context.check_active()
        result = generate_analysis(
            analysis.model_config_id, payload,
            context.check_active, context.remaining_seconds,
        )
        context.check_active()
        completed = _finish(
            analysis.pk, PerformanceAnalysis.Status.RUNNING,
            status=PerformanceAnalysis.Status.COMPLETED, result=result,
        )
        return {
            'analysis_id': str(analysis.pk),
            'status': 'completed' if completed else 'skipped',
        }
    except TaskStopped as exc:
        code = exc.code if exc.code in _SAFE_FAILURES else 'analysis_failed'
    except KnowledgeLLMTimeout:
        code = 'analysis_timeout'
    except AnalysisOutputError:
        code = 'INVALID_MODEL_OUTPUT'
    except SoftTimeLimitExceeded:
        code = 'analysis_timeout'
    except Exception as exc:
        # Provider responses and exception messages may contain credentials or
        # user data. Persist and log only the exception class.
        logger.error(
            'Performance analysis failed: analysis=%s error_type=%s',
            analysis.pk, type(exc).__name__,
        )
        code = 'model_error'

    _finish(
        analysis.pk, PerformanceAnalysis.Status.RUNNING,
        status=PerformanceAnalysis.Status.FAILED, code=code,
    )
    return {'analysis_id': str(analysis.pk), 'status': 'failed', 'error_code': code}
