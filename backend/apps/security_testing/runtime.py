"""Durable queue and immutable execution snapshots for security tasks."""
import hashlib
import json
from copy import deepcopy
from datetime import timedelta

from celery.exceptions import SoftTimeLimitExceeded
from django.db import transaction
from django.http import Http404
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied

from ai_core.config_access import usable_llm_configurations
from projects.access import EDIT, EXECUTE, REPORT, get_project_for_user
from projects.models import Project

from .credentials import decrypt_identities
from .models import SecurityPlan, SecurityTask

QUEUE_SECONDS = 300
EXECUTION_SECONDS = 600
FAILURES = {
    'queue_unavailable': '任务队列暂时不可用，请重新提交。',
    'queue_timeout': '任务排队超时，请重新提交。',
    'task_timeout': '任务超过总时限，已停止。',
    'permission_revoked': '项目权限已变化，任务已停止。',
    'model_unavailable': '所选模型不可用，请重新选择。',
    'invalid_model_output': '模型返回的规划结构无效，请重新生成。',
    'task_failed': '任务执行失败，请检查配置后重试。',
    'cancelled': '任务已由用户停止。',
}


class TaskConflict(ValueError):
    pass


class TaskStopped(Exception):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


def _authorize(project_id, user, kind):
    required = (EDIT, EXECUTE) if kind == SecurityTask.Kind.PLANNING else (EXECUTE, REPORT)
    for capability in required:
        get_project_for_user(project_id, user, capability, expected_project_type='security')


def finish(task_id, status, code='', result=None):
    now = timezone.now()
    values = {'status': status, 'error_code': code, 'error_message': FAILURES.get(code, ''),
              'finished_at': now, 'heartbeat_at': now, 'updated_at': now,
              'identities_ciphertext': ''}
    if result is not None:
        values['result'] = result
    if status == 'completed':
        current = SecurityTask.objects.filter(pk=task_id).values_list('progress', flat=True).first() or {}
        values['progress'] = {**current, 'phase': 'completed', 'completed': current.get('total', 0)}
    return SecurityTask.objects.filter(pk=task_id, status__in=('queued', 'running')).update(**values)


def recover_expired(project_id):
    now = timezone.now()
    for status, code in [('queued', 'queue_timeout'), ('running', 'task_timeout')]:
        SecurityTask.objects.filter(project_id=project_id, status=status, deadline_at__lte=now).update(
            status='failed', error_code=code, error_message=FAILURES[code],
            finished_at=now, updated_at=now, identities_ciphertext='',
        )


def _dispatch(task_id):
    from .tasks import run_security_task
    try:
        run_security_task.apply_async(args=(str(task_id),), expires=QUEUE_SECONDS,
                                     soft_time_limit=EXECUTION_SECONDS + 5,
                                     time_limit=EXECUTION_SECONDS + 15)
    except Exception:
        finish(task_id, 'failed', 'queue_unavailable')


def enqueue(plan_id, user, kind, request_id, model_config_id=None, case_ids=None):
    plan = SecurityPlan.objects.only('project_id').get(pk=plan_id)
    _authorize(plan.project_id, user, kind)
    payload = {'plan_id': str(plan_id), 'kind': kind, 'model_config_id': model_config_id,
               'case_ids': sorted(case_ids or [])}
    request_hash = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
    with transaction.atomic():
        Project.objects.select_for_update().get(pk=plan.project_id)
        plan = SecurityPlan.objects.select_for_update().get(pk=plan_id)
        _authorize(plan.project_id, user, kind)
        previous = SecurityTask.objects.filter(project=plan.project, request_id=request_id).first()
        if previous:
            if previous.request_hash != request_hash or previous.created_by_id != user.pk:
                raise TaskConflict('此请求编号已用于不同任务，请刷新后重试。')
            return previous, False
        if model_config_id is not None and not usable_llm_configurations().filter(pk=model_config_id).exists():
            raise TaskConflict(FAILURES['model_unavailable'])
        selected = []
        if kind == SecurityTask.Kind.EXECUTION:
            allowed = {item['id']: item for item in plan.cases}
            if not case_ids or any(ident not in allowed for ident in case_ids):
                raise TaskConflict('所选检查不属于当前计划，请重新生成并选择。')
            selected = [deepcopy(item) for item in plan.cases if item['id'] in case_ids]
        task = SecurityTask.objects.create(
            project=plan.project, plan=plan, created_by=user, kind=kind,
            request_id=request_id, request_hash=request_hash,
            snapshot={'config': deepcopy(plan.config), 'cases': selected,
                      'plan_name': plan.name, 'plan_revision': plan.revision},
            identities_ciphertext=plan.identities_ciphertext if kind == 'execution' else '',
            model_config_id=model_config_id, deadline_at=timezone.now() + timedelta(seconds=QUEUE_SECONDS),
            progress={'phase': 'queued', 'completed': 0, 'total': len(selected), 'requests_sent': 0},
        )
        transaction.on_commit(lambda: _dispatch(task.pk))
    task.refresh_from_db()
    return task, True


class ExecutionContext:
    def __init__(self, task):
        self.task = task

    def remaining_seconds(self):
        return max(0.0, (self.task.deadline_at - timezone.now()).total_seconds())

    def check_active(self):
        current = SecurityTask.objects.filter(pk=self.task.pk).values('status', 'cancel_requested').first()
        if not current or current['status'] != 'running' or current['cancel_requested']:
            raise TaskStopped('cancelled')
        if self.remaining_seconds() <= 0:
            raise TaskStopped('task_timeout')
        from users.models import User
        user = User.objects.filter(pk=self.task.created_by_id).first()
        try:
            _authorize(self.task.project_id, user, self.task.kind)
        except (PermissionDenied, Http404):
            raise TaskStopped('permission_revoked') from None
        if self.task.model_config_id and not usable_llm_configurations().filter(pk=self.task.model_config_id).exists():
            raise TaskStopped('model_unavailable')
        SecurityTask.objects.filter(pk=self.task.pk, status='running').update(heartbeat_at=timezone.now())

    def on_progress(self, value):
        self.check_active()
        progress = {}
        for key in ('completed', 'total', 'requests_sent'):
            item = value.get(key)
            if type(item) is int and 0 <= item <= 10000:
                progress[key] = item
        phase = value.get('phase')
        if phase in ('queued', 'planning', 'running', 'checking', 'completed', 'preparing', 'request'):
            progress['phase'] = phase
        values = {'progress': progress, 'heartbeat_at': timezone.now(), 'updated_at': timezone.now()}
        partial = value.get('partial_result')
        if (self.task.kind == 'execution' and isinstance(partial, dict)
                and set(partial) == {'items', 'summary', 'limitations'}
                and isinstance(partial['items'], list) and len(partial['items']) <= 80
                and isinstance(partial['summary'], dict) and isinstance(partial['limitations'], list)):
            # Only the in-process runner produces this callback. Both progress
            # and its redacted evidence commit together, including request count.
            values['result'] = deepcopy(partial)
        SecurityTask.objects.filter(pk=self.task.pk, status='running', cancel_requested=False).update(**values)


def execute_task(task_id):
    now = timezone.now()
    task = SecurityTask.objects.filter(pk=task_id).first()
    if not task:
        return {'status': 'missing'}
    recover_expired(task.project_id)
    # Atomic claim means repeated delivery can never issue a second set of requests.
    claimed = SecurityTask.objects.filter(pk=task_id, status='queued', cancel_requested=False).update(
        status='running', started_at=now, heartbeat_at=now, updated_at=now,
        deadline_at=now + timedelta(seconds=EXECUTION_SECONDS),
    )
    if not claimed:
        return {'status': 'not_claimed'}
    task.refresh_from_db()
    context = ExecutionContext(task)
    try:
        context.check_active()
        if task.kind == SecurityTask.Kind.PLANNING:
            from .planning import generate
            context.on_progress({'phase': 'planning', 'completed': 0, 'total': 1, 'requests_sent': 0})
            cases = generate(task.snapshot['config'], task.model_config_id, context.check_active, context.remaining_seconds)
            context.check_active()
            with transaction.atomic():
                plan = SecurityPlan.objects.select_for_update().get(pk=task.plan_id)
                current = SecurityTask.objects.select_for_update().get(pk=task.pk)
                context.check_active()
                applied = plan.revision == task.snapshot['plan_revision']
                if applied:
                    plan.cases = cases
                    plan.save(update_fields=['cases', 'updated_at'])
                finish(current.pk, 'completed', result={
                    'cases': cases, 'plan_applied': applied, 'plan_revision': task.snapshot['plan_revision'],
                    'source': 'ai' if task.model_config_id else 'rules',
                    'limitations': [] if applied else ['计划已修改，本次规划未覆盖当前计划，请重新生成。'],
                })
        else:
            from .runner import execute
            identities = decrypt_identities(task.identities_ciphertext)
            result = execute(task.snapshot, identities, context.check_active, context.on_progress)
            context.check_active()
            finish(task.pk, 'completed', result=result)
    except TaskStopped as exc:
        finish(task.pk, 'cancelled' if exc.code == 'cancelled' else 'failed', exc.code)
    except SoftTimeLimitExceeded:
        finish(task.pk, 'failed', 'task_timeout')
    except Exception as exc:
        from .planning import PlanningOutputError
        from .planning_process import PlanningTransportError
        from .runner import RuntimeDeadlineExceeded
        code = 'invalid_model_output' if isinstance(exc, PlanningOutputError) else 'task_failed'
        if isinstance(exc, RuntimeDeadlineExceeded):
            code = 'task_timeout'
        if isinstance(exc, PlanningTransportError) and exc.code in ('task_timeout', 'invalid_model_output'):
            code = exc.code
        finish(task.pk, 'failed', code)
    return {'task_id': str(task.pk), 'status': SecurityTask.objects.values_list('status', flat=True).get(pk=task.pk)}


def stop_task(task_id):
    now = timezone.now()
    SecurityTask.objects.filter(pk=task_id, status__in=('queued', 'running')).update(
        status='cancelled', cancel_requested=True, error_code='cancelled',
        error_message=FAILURES['cancelled'], finished_at=now, updated_at=now,
        identities_ciphertext='',
    )
