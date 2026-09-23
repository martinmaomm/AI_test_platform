"""Durable lifecycle for passive performance-run analysis tasks."""
from __future__ import annotations

import hashlib
import json
import logging
import time
import uuid
from datetime import timedelta

from celery.exceptions import SoftTimeLimitExceeded
from django.conf import settings
from django.db import transaction
from django.http import Http404
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied

from ai_core.config_access import usable_llm_configurations
from projects.access import EXECUTE, REPORT, get_project_for_user

from .models import PerformanceAnalysis, PerformanceRun


logger = logging.getLogger(__name__)

ANALYSIS_QUEUE_TIMEOUT_SECONDS = 300
DEFAULT_ANALYSIS_TIMEOUT_SECONDS = 600
MIN_ANALYSIS_TIMEOUT_SECONDS = 60
MAX_ANALYSIS_TIMEOUT_SECONDS = 1800
ACTIVITY_CHECK_INTERVAL_SECONDS = 2.0
PROGRESS_SAVE_INTERVAL_SECONDS = 2.0

PROGRESS_PHASES = {
    'queued', 'preparing', 'waiting_response', 'receiving', 'retrying',
    'validating', 'completed',
}
RETRY_REASONS = {
    'timeout', 'connection_error', 'rate_limited', 'upstream_error',
    'auth_error', 'stream_interrupted', 'unknown',
}
_PHASE_LABELS = {
    'queued': '排队', 'preparing': '准备分析', 'waiting_response': '等待模型响应',
    'receiving': '接收模型正文', 'retrying': '模型调用重试',
    'validating': '校验模型结果', 'completed': '完成',
}
_RETRY_REASON_LABELS = {
    'timeout': '模型响应超时', 'connection_error': '模型连接异常',
    'rate_limited': '模型服务限流', 'upstream_error': '模型上游异常',
    'auth_error': '模型认证异常', 'stream_interrupted': '模型流中断',
    'unknown': '未知模型异常',
}


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


def analysis_timeout_seconds() -> int:
    value = getattr(
        settings, 'PERFORMANCE_ANALYSIS_TIMEOUT_SECONDS',
        DEFAULT_ANALYSIS_TIMEOUT_SECONDS,
    )
    if isinstance(value, bool):
        return DEFAULT_ANALYSIS_TIMEOUT_SECONDS
    try:
        value = int(value)
    except (TypeError, ValueError):
        return DEFAULT_ANALYSIS_TIMEOUT_SECONDS
    if not MIN_ANALYSIS_TIMEOUT_SECONDS <= value <= MAX_ANALYSIS_TIMEOUT_SECONDS:
        return DEFAULT_ANALYSIS_TIMEOUT_SECONDS
    return value


def _record_timeout(analysis) -> int:
    value = analysis.timeout_seconds
    if isinstance(value, int) and MIN_ANALYSIS_TIMEOUT_SECONDS <= value <= MAX_ANALYSIS_TIMEOUT_SECONDS:
        return value
    return DEFAULT_ANALYSIS_TIMEOUT_SECONDS


def _iso(value) -> str:
    return value.isoformat()


def _initial_progress(now) -> dict:
    return {
        'phase': 'queued',
        'attempt': 0,
        'stream_chunks': 0,
        'received_chars': 0,
        'elapsed_seconds': 0.0,
        'first_text_at': None,
        'last_activity_at': _iso(now),
        'model_timeout_seconds': None,
        'retry_reason': '',
        'retry_delay_seconds': 0.0,
        'retry_count': 0,
    }


def _elapsed_seconds(started_at, now) -> float:
    if started_at is None:
        return 0.0
    return round(max(0.0, (now - started_at).total_seconds()), 3)


def _failure_message(code: str, progress: dict | None) -> str:
    message = _SAFE_FAILURES.get(code, _SAFE_FAILURES['analysis_failed'])
    if not isinstance(progress, dict) or not progress:
        return message
    phase = progress.get('phase')
    reason = progress.get('retry_reason')
    details = []
    if phase in _PHASE_LABELS:
        details.append(f'失败阶段：{_PHASE_LABELS[phase]}。')
    if reason in _RETRY_REASON_LABELS:
        details.append(f'最近原因：{_RETRY_REASON_LABELS[reason]}。')
    return message + ''.join(details)


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
    expired = [
        *PerformanceAnalysis.objects.filter(
            run_id=run_id,
            status=PerformanceAnalysis.Status.QUEUED,
            queued_deadline_at__lte=now,
        ),
        *PerformanceAnalysis.objects.filter(
            run_id=run_id,
            status=PerformanceAnalysis.Status.RUNNING,
            running_deadline_at__lte=now,
        ),
    ]
    recovered = 0
    for analysis in expired:
        code = (
            'queue_timeout'
            if analysis.status == PerformanceAnalysis.Status.QUEUED
            else 'analysis_timeout'
        )
        progress = analysis.progress if isinstance(analysis.progress, dict) else {}
        if progress:
            progress = {
                **progress,
                'elapsed_seconds': _elapsed_seconds(analysis.started_at, now),
            }
        updated = PerformanceAnalysis.objects.filter(
            pk=analysis.pk, status=analysis.status,
        ).update(
            status=PerformanceAnalysis.Status.FAILED,
            progress=progress,
            error_code=code,
            error_message=_failure_message(code, progress),
            finished_at=now,
        )
        if updated:
            recovered += 1
            logger.info(
                'Performance analysis finished: analysis_id=%s phase=%s '
                'attempt=%s retry_reason=%s elapsed_seconds=%s error_code=%s',
                analysis.pk, progress.get('phase', ''), progress.get('attempt', 0),
                progress.get('retry_reason', ''), progress.get('elapsed_seconds', 0), code,
            )
    return recovered


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
        analysis = PerformanceAnalysis.objects.filter(
            pk=analysis_id, status=expected_status,
        ).first()
        if analysis is None:
            return False
        values = {
            'status': status,
            'finished_at': timezone.now(),
            'error_code': code,
            'error_message': _failure_message(code, analysis.progress) if code else '',
        }
        if result is not None:
            values['result'] = result
        updated = bool(PerformanceAnalysis.objects.filter(
            pk=analysis_id, status=expected_status,
        ).update(**values))
        if updated:
            progress = analysis.progress if isinstance(analysis.progress, dict) else {}
            logger.info(
                'Performance analysis finished: analysis_id=%s phase=%s '
                'attempt=%s retry_reason=%s elapsed_seconds=%s error_code=%s',
                analysis.pk, progress.get('phase', ''), progress.get('attempt', 0),
                progress.get('retry_reason', ''), progress.get('elapsed_seconds', 0), code,
            )
        return updated


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
        timeout_seconds = analysis_timeout_seconds()
        analysis = PerformanceAnalysis.objects.create(
            run=run,
            created_by=user,
            request_id=request_id,
            request_hash=fingerprint,
            model_config_id=config.pk,
            model_info=_model_info(config),
            targets=targets,
            timeout_seconds=timeout_seconds,
            progress=_initial_progress(now),
            celery_task_id=celery_task_id,
            queued_deadline_at=now + timedelta(seconds=ANALYSIS_QUEUE_TIMEOUT_SECONDS),
        )

        def dispatch():
            try:
                from .tasks import run_performance_analysis_async
                run_performance_analysis_async.apply_async(
                    args=(str(analysis.pk),), task_id=celery_task_id,
                    soft_time_limit=timeout_seconds + 5,
                    time_limit=timeout_seconds + 15,
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
        remaining = max(
            0.0,
            (analysis.running_deadline_at - timezone.now()).total_seconds(),
        )
        self.deadline = time.monotonic() + remaining
        self.started_at = analysis.started_at
        self.progress = (
            dict(analysis.progress) if isinstance(analysis.progress, dict) else {}
        )
        self._last_db_check = None
        self._last_progress_save = time.monotonic()
        self._last_retry_marker = (
            self.progress.get('attempt'), self.progress.get('retry_reason'),
        )

    def remaining_seconds(self):
        return max(0.0, self.deadline - time.monotonic())

    def check_active(self, force=False):
        current = time.monotonic()
        if self.remaining_seconds() <= 0:
            raise TaskStopped('analysis_timeout')
        if (
            not force
            and self._last_db_check is not None
            and current - self._last_db_check < ACTIVITY_CHECK_INTERVAL_SECONDS
        ):
            return True
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
        self._last_db_check = current
        return True

    @staticmethod
    def _counter(value):
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            return None
        return value

    @staticmethod
    def _seconds(value):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        value = float(value)
        if value < 0 or value != value or value == float('inf'):
            return None
        return round(value, 3)

    def on_event(self, event, force=False):
        if not isinstance(event, dict):
            return
        now = timezone.now()
        current = time.monotonic()
        previous_phase = self.progress.get('phase')
        previous_attempt = self.progress.get('attempt')
        previous_model_timeout = self.progress.get('model_timeout_seconds')
        changed = False
        retry_event = False
        model_timeout_changed = False

        phase = event.get('phase')
        if phase in PROGRESS_PHASES:
            self.progress['phase'] = phase
            changed = changed or phase != previous_phase

        attempt = self._counter(event.get('attempt'))
        if attempt is not None:
            attempt = max(attempt, self._counter(previous_attempt) or 0)
            self.progress['attempt'] = attempt
            changed = changed or attempt != previous_attempt

        for key in ('stream_chunks', 'received_chars'):
            value = self._counter(event.get(key))
            if value is not None:
                value = max(value, self._counter(self.progress.get(key)) or 0)
                changed = changed or value != self.progress.get(key)
                self.progress[key] = value

        model_timeout = self._seconds(event.get('model_timeout_seconds'))
        if model_timeout is not None:
            model_timeout_changed = model_timeout != previous_model_timeout
            changed = changed or model_timeout_changed
            self.progress['model_timeout_seconds'] = model_timeout

        reason = event.get('retry_reason')
        if reason in RETRY_REASONS:
            changed = changed or reason != self.progress.get('retry_reason')
            self.progress['retry_reason'] = reason
            retry_event = True
        delay = self._seconds(event.get('retry_delay_seconds'))
        if delay is not None:
            changed = changed or delay != self.progress.get('retry_delay_seconds')
            self.progress['retry_delay_seconds'] = delay
            retry_event = True

        marker = (self.progress.get('attempt'), self.progress.get('retry_reason'))
        if retry_event and marker != self._last_retry_marker:
            self.progress['retry_count'] = (
                self._counter(self.progress.get('retry_count')) or 0
            ) + 1
            self._last_retry_marker = marker
            changed = True

        if (self._counter(self.progress.get('received_chars')) or 0) > 0:
            if not self.progress.get('first_text_at'):
                self.progress['first_text_at'] = _iso(now)
                changed = True

        if changed:
            self.progress['last_activity_at'] = _iso(now)
        self.progress['elapsed_seconds'] = _elapsed_seconds(self.started_at, now)

        immediate = (
            force or retry_event
            or self.progress.get('phase') != previous_phase
            or self.progress.get('attempt') != previous_attempt
            or model_timeout_changed
        )
        if not immediate and current - self._last_progress_save < PROGRESS_SAVE_INTERVAL_SECONDS:
            return
        if not changed and not force:
            return
        updated = PerformanceAnalysis.objects.filter(
            pk=self.analysis_id, status=PerformanceAnalysis.Status.RUNNING,
        ).update(progress=self.progress)
        if not updated:
            raise TaskStopped('analysis_timeout')
        self._last_progress_save = current
        if immediate:
            logger.info(
                'Performance analysis progress: analysis_id=%s phase=%s '
                'attempt=%s retry_reason=%s elapsed_seconds=%s',
                self.analysis_id, self.progress.get('phase', ''),
                self.progress.get('attempt', 0), self.progress.get('retry_reason', ''),
                self.progress.get('elapsed_seconds', 0),
            )

    def flush(self):
        now = timezone.now()
        self.progress['elapsed_seconds'] = _elapsed_seconds(self.started_at, now)
        updated = PerformanceAnalysis.objects.filter(
            pk=self.analysis_id, status=PerformanceAnalysis.Status.RUNNING,
        ).update(progress=self.progress)
        if updated:
            self._last_progress_save = time.monotonic()


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
        progress = analysis.progress if isinstance(analysis.progress, dict) else {}
        progress = {
            **progress,
            'phase': 'preparing',
            'elapsed_seconds': 0.0,
            'last_activity_at': _iso(now),
        }
        analysis.status = PerformanceAnalysis.Status.RUNNING
        analysis.started_at = now
        analysis.running_deadline_at = now + timedelta(seconds=_record_timeout(analysis))
        analysis.progress = progress
        analysis.error_code = ''
        analysis.error_message = ''
        analysis.save(update_fields=(
            'status', 'started_at', 'running_deadline_at', 'progress',
            'error_code', 'error_message',
        ))
        logger.info(
            'Performance analysis progress: analysis_id=%s phase=preparing '
            'attempt=%s retry_reason=%s elapsed_seconds=0',
            analysis.pk, progress.get('attempt', 0), progress.get('retry_reason', ''),
        )
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

        context.check_active(force=True)
        context.on_event({'phase': 'preparing'}, force=True)
        run = PerformanceRun.objects.prefetch_related('participants').get(pk=analysis.run_id)
        payload = build_analysis_input(run, analysis.targets)
        updated = PerformanceAnalysis.objects.filter(
            pk=analysis.pk, status=PerformanceAnalysis.Status.RUNNING,
        ).update(input_snapshot=payload)
        if not updated:
            raise TaskStopped('analysis_timeout')
        context.check_active(force=True)
        result = generate_analysis(
            analysis.model_config_id, payload,
            context.check_active, context.remaining_seconds,
            on_event=context.on_event,
        )
        context.check_active(force=True)
        context.on_event({'phase': 'completed'}, force=True)
        context.check_active(force=True)
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
        transport_code = getattr(exc, 'code', None)
        if (
            exc.__class__.__name__ == 'AnalysisTransportError'
            and transport_code in _SAFE_FAILURES
        ):
            code = transport_code
        else:
            # Provider responses and exception messages may contain credentials
            # or user data. Persist and log only the exception class.
            logger.error(
                'Performance analysis failed: analysis_id=%s phase=%s attempt=%s '
                'retry_reason=%s elapsed_seconds=%s error_type=%s',
                analysis.pk, context.progress.get('phase', ''),
                context.progress.get('attempt', 0),
                context.progress.get('retry_reason', ''),
                context.progress.get('elapsed_seconds', 0), type(exc).__name__,
            )
            code = 'model_error'

    context.flush()
    _finish(
        analysis.pk, PerformanceAnalysis.Status.RUNNING,
        status=PerformanceAnalysis.Status.FAILED, code=code,
    )
    return {'analysis_id': str(analysis.pk), 'status': 'failed', 'error_code': code}
