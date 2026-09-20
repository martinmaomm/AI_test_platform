from __future__ import annotations

import importlib.util
from copy import deepcopy
from datetime import timedelta
import hashlib
import json
from pathlib import Path

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from projects.models import Project

from .constants import (
    AGENT_VERSION, CONTROLLER_FRESH_SECONDS, ENGINE_VERSION, PROTOCOL_VERSION,
)
from .models import (
    PerformanceControllerState, PerformanceNode, PerformancePlan, PerformanceRun,
)


class RunConflict(Exception):
    pass


class RunUnavailable(Exception):
    pass


class RunValidationRejected(Exception):
    pass


class RunReportRejected(Exception):
    pass


def execution_configuration():
    """Load the controller-owned environment contract without an import cycle."""
    try:
        from .runtime_settings import execution_configuration as load_configuration
    except ModuleNotFoundError as exc:
        if exc.name not in {
            'performance_testing.runtime_settings',
            f'{__package__}.runtime_settings',
        }:
            raise
        return {
            'enabled': False,
            'available': False,
            'reason': '性能执行运行时尚未安装。',
        }
    configuration = load_configuration()
    return {
        'enabled': bool(configuration.get('enabled')),
        'available': bool(configuration.get('available')),
        'reason': str(configuration.get('reason') or ''),
    }


def _controller_is_fresh(state, now):
    return bool(
        state
        and state.owner_id
        and state.heartbeat_at
        and state.lease_until
        and state.heartbeat_at >= now - timedelta(seconds=CONTROLLER_FRESH_SECONDS)
        and state.lease_until > now
    )


def controller_execution_status(now=None):
    now = now or timezone.now()
    state = PerformanceControllerState.objects.filter(pk=1).first()
    configuration = execution_configuration()
    return {
        'state': state,
        'controller_online': _controller_is_fresh(state, now),
        'enabled': configuration['enabled'],
        'available': configuration['available'],
        'reason': configuration['reason'],
    }


def _load_snapshot_contract():
    try:
        from performance_node.locust_runtime import canonical_sha256, validate_snapshot
        return canonical_sha256, validate_snapshot
    except ModuleNotFoundError as exc:
        if exc.name not in {'performance_node', 'performance_node.locust_runtime'}:
            raise

    source = Path(settings.BASE_DIR).parent / 'performance-node' / 'src' / 'performance_node' / 'locust_runtime.py'
    if not source.is_file():
        raise RunUnavailable('固定执行模板不可用。')
    spec = importlib.util.spec_from_file_location('_performance_locust_runtime_contract', source)
    if spec is None or spec.loader is None:
        raise RunUnavailable('固定执行模板不可用。')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.canonical_sha256, module.validate_snapshot


def validation_key_for(plan, node):
    """Fingerprint request/data and the exact node capability used to validate it."""
    target = plan.target
    payload = {
        'contract': 2,
        'plan_id': plan.pk,
        'target_id': target.pk,
        'base_url': target.base_url,
        'allowed_methods': deepcopy(target.allowed_methods),
        'variables': deepcopy(plan.variables),
        'unique_variables': deepcopy(plan.unique_variables),
        'steps': deepcopy(plan.steps),
        'node': {
            'id': str(node.pk),
            'protocol_version': node.protocol_version,
            'agent_version': node.agent_version,
            'engine_version': node.engine_version,
        },
    }
    encoded = json.dumps(
        payload, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False,
    ).encode('utf-8')
    return hashlib.sha256(encoded).hexdigest()


def _snapshot_for(run_id, plan, node, mode):
    target = plan.target
    if plan.project_id != target.project_id or plan.project_id != node.project_id:
        raise RunValidationRejected('计划、目标与节点不属于同一项目。')
    # Re-run the management contract for legacy/directly-written rows, then add
    # the stricter fixed-runtime validation below.  Passing the old serializer
    # alone is intentionally insufficient for execution safety.
    from .serializers import PerformancePlanSerializer, PerformanceTargetSerializer
    target_validator = PerformanceTargetSerializer(data={
        'name': target.name,
        'base_url': target.base_url,
        'allowed_methods': deepcopy(target.allowed_methods),
    })
    plan_validator = PerformancePlanSerializer(data={
        'name': plan.name,
        'description': plan.description,
        'target_id': target.pk,
        'users': plan.users,
        'spawn_rate': plan.spawn_rate,
        'duration_seconds': plan.duration_seconds,
        'wait_seconds': plan.wait_seconds,
        'variables': deepcopy(plan.variables),
        'unique_variables': deepcopy(plan.unique_variables),
        'steps': deepcopy(plan.steps),
    }, context={'project': plan.project})
    if not target_validator.is_valid() or not plan_validator.is_valid():
        raise RunValidationRejected('计划或目标不再满足完整管理契约。')
    snapshot = {
        'schema_version': 2,
        'run_id': str(run_id),
        'node_id': str(node.pk),
        'engine_version': ENGINE_VERSION,
        'plan_name': plan.name,
        'base_url': target.base_url,
        'allowed_methods': deepcopy(target.allowed_methods),
        'mode': mode,
        'validation_key': validation_key_for(plan, node),
        'users': 1 if mode == PerformanceRun.Mode.VALIDATION else plan.users,
        'spawn_rate': 1 if mode == PerformanceRun.Mode.VALIDATION else plan.spawn_rate,
        # Validation is exactly one round; this is only its bounded wall-clock
        # budget so a short load duration cannot truncate setup/login.
        'duration_seconds': 120 if mode == PerformanceRun.Mode.VALIDATION else plan.duration_seconds,
        'wait_seconds': plan.wait_seconds,
        'variables': deepcopy(plan.variables),
        'unique_variables': deepcopy(plan.unique_variables),
        'steps': deepcopy(plan.steps),
    }
    canonical_sha256, validate_snapshot = _load_snapshot_contract()
    try:
        validate_snapshot(snapshot)
        digest = canonical_sha256(snapshot)
    except (TypeError, ValueError, OverflowError) as exc:
        raise RunValidationRejected(str(exc) or '计划不符合执行期安全约束。')
    return snapshot, digest


def _node_is_compatible(node, now):
    return bool(
        node.deleted_at is None
        and node.revoked_at is None
        and node.status_at(now) == 'online'
        and node.protocol_version == PROTOCOL_VERSION
        and node.agent_version == AGENT_VERSION
        and node.engine_version == ENGINE_VERSION
    )


def _has_matching_validation(plan, node, validation_key):
    candidates = PerformanceRun.objects.select_for_update().filter(
        plan=plan,
        node=node,
        mode=PerformanceRun.Mode.VALIDATION,
        status=PerformanceRun.Status.COMPLETED,
        validation_key=validation_key,
    ).order_by('-finished_at', '-created_at')
    expected_main = sum(
        isinstance(step, dict) and step.get('phase') == 'main'
        for step in (plan.steps or [])
    )
    for run in candidates:
        metrics = run.latest_metrics or {}
        if (
            metrics.get('complete') is True
            and metrics.get('validation_complete') is True
            and metrics.get('validation_passed') is True
            and type(metrics.get('requests')) is int
            and metrics['requests'] > 0
            and metrics.get('main_steps_completed') == expected_main
            and metrics.get('main_steps_total') == expected_main
        ):
            return True
    return False


@transaction.atomic
def create_run(project, plan_id, node_id, request_id, created_by, mode='load'):
    # Serialize idempotency decisions per project on databases with row locks.
    locked_project = Project.objects.select_for_update().get(pk=project.pk)
    existing = PerformanceRun.objects.select_related('node').filter(
        project=locked_project, request_id=request_id,
    ).first()
    if existing is not None:
        if existing.plan_id == plan_id and existing.node_id == node_id and existing.mode == mode:
            return existing, False
        raise RunConflict('同一 request_id 已用于不同运行参数。')

    status = controller_execution_status()
    if not status['available']:
        raise RunUnavailable(status['reason'] or '性能执行配置不可用。')
    if not status['controller_online']:
        raise RunUnavailable('性能控制器未在线。')

    plan = PerformancePlan.objects.select_for_update().select_related('target').filter(
        pk=plan_id, project=locked_project,
    ).first()
    if plan is None:
        raise RunValidationRejected('性能计划不存在。')
    node = PerformanceNode.objects.select_for_update().filter(
        pk=node_id, project=locked_project, deleted_at__isnull=True,
    ).first()
    if node is None:
        raise RunValidationRejected('性能节点不存在。')
    now = timezone.now()
    if not _node_is_compatible(node, now):
        raise RunValidationRejected(f'节点必须在线、未吊销且已升级至 Agent {AGENT_VERSION}，与当前执行模板保持一致。')

    try:
        current_validation_key = validation_key_for(plan, node)
    except (TypeError, ValueError, OverflowError) as exc:
        raise RunValidationRejected('计划或节点验证指纹无法生成。') from exc
    if mode == PerformanceRun.Mode.LOAD and not _has_matching_validation(
        plan, node, current_validation_key,
    ):
        raise RunValidationRejected('所选节点没有与当前计划、目标和节点能力匹配的成功验证。')

    run = PerformanceRun(
        project=locked_project,
        plan=plan,
        node=node,
        created_by=created_by,
        request_id=request_id,
        mode=mode,
        validation_key=current_validation_key,
    )
    snapshot, digest = _snapshot_for(run.pk, plan, node, mode)
    run.snapshot = snapshot
    run.snapshot_sha256 = digest
    run.save(force_insert=True)
    return run, True


@transaction.atomic
def request_stop(run):
    locked = PerformanceRun.objects.select_for_update().get(pk=run.pk)
    if locked.status in PerformanceRun.TERMINAL_STATUSES:
        return locked
    now = timezone.now()
    if locked.status == PerformanceRun.Status.QUEUED:
        locked.status = PerformanceRun.Status.CANCELLED
        locked.finished_at = now
        locked.stop_requested_at = now
        locked.reason_code = 'cancelled_by_user'
        locked.reason = '用户在运行开始前取消。'
        fields = (
            'status', 'finished_at', 'stop_requested_at', 'reason_code', 'reason',
        )
    else:
        # Keep the current command unchanged.  A fresh controller first asks the
        # Master for final metrics, then replaces it with an explicit stop command.
        locked.status = PerformanceRun.Status.STOPPING
        if locked.stop_requested_at is None:
            locked.stop_requested_at = now
        fields = ('status', 'stop_requested_at')
    locked.save(update_fields=fields)
    return locked


def _serialized_report(report):
    return {
        'run_id': str(report['run_id']),
        'sequence': report['sequence'],
        'state': report['state'],
        'reason_code': report['reason_code'],
        'reason': report['reason'],
    }


def _stop_command(run, reason):
    return {'type': 'stop', 'run_id': str(run.pk), 'reason': reason}


@transaction.atomic
def record_run_report_and_command(node, report, now=None):
    """Persist an Agent report and choose only that node's current command.

    Reports are evidence for the controller.  They never mark a run completed or
    otherwise drive the server-side run state machine directly.
    """
    now = now or timezone.now()
    reported_run = None
    if report is not None:
        reported_run = PerformanceRun.objects.select_for_update().filter(
            pk=report['run_id'], node=node,
        ).first()
        if reported_run is None:
            raise RunReportRejected('run_report 不属于当前节点或运行不存在。')
        if report['sequence'] <= reported_run.node_report_seq:
            raise RunReportRejected('run_report.sequence 必须严格递增。')
        reported_run.node_report = _serialized_report(report)
        reported_run.node_report_seq = report['sequence']
        reported_run.save(update_fields=('node_report', 'node_report_seq'))

    candidate = reported_run
    if candidate is None:
        candidate = PerformanceRun.objects.filter(
            node=node,
            status__in=(
                PerformanceRun.Status.PREPARING,
                PerformanceRun.Status.RUNNING,
                PerformanceRun.Status.STOPPING,
            ),
        ).exclude(node_command={}).order_by('created_at').first()
    if candidate is None:
        return {'type': 'idle'}
    if candidate.status in PerformanceRun.TERMINAL_STATUSES:
        return _stop_command(candidate, candidate.reason or f'运行已进入终态：{candidate.status}')

    execution = controller_execution_status(now)
    if not execution['available']:
        return _stop_command(candidate, execution['reason'] or '性能执行配置不可用。')
    if not execution['controller_online']:
        return _stop_command(candidate, '性能控制器租约已失效。')

    command = candidate.node_command
    if (
        isinstance(command, dict)
        and command.get('type') in {'prepare', 'stop'}
        and command.get('run_id') == str(candidate.pk)
    ):
        # A stopping run deliberately keeps its current prepare command until the
        # controller has collected final Master metrics and stores type=stop.
        return deepcopy(command)
    return {'type': 'idle'}


@transaction.atomic
def stop_runs_for_node_identity_change(node, reason_code, reason, now=None):
    now = now or timezone.now()
    runs = list(PerformanceRun.objects.select_for_update().filter(
        node=node, status__in=PerformanceRun.ACTIVE_STATUSES,
    ))
    for run in runs:
        if run.status == PerformanceRun.Status.QUEUED:
            run.status = PerformanceRun.Status.INCOMPLETE
            run.finished_at = now
            state_field = 'finished_at'
        else:
            run.status = PerformanceRun.Status.STOPPING
            run.stop_requested_at = run.stop_requested_at or now
            state_field = 'stop_requested_at'
        run.reason_code = reason_code
        run.reason = reason
        run.node_command = _stop_command(run, reason)
        fields = ['status', state_field, 'reason_code', 'reason', 'node_command']
        run.save(update_fields=fields)
    return runs
