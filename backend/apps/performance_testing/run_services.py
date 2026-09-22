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
    AGENT_VERSION, CONTROLLER_FRESH_SECONDS, ENGINE_VERSION, MAX_NODES_PER_RUN,
    PROTOCOL_VERSION,
)
from .models import (
    PerformanceControllerState, PerformanceNode, PerformancePlan, PerformanceRun,
    PerformanceRunNode,
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
        'contract': 3,
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


def _snapshot_for(run_id, plan, allocations, mode):
    target = plan.target
    if (
        plan.project_id != target.project_id
        or any(plan.project_id != item['node'].project_id for item in allocations)
    ):
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
        'schema_version': 3,
        'run_id': str(run_id),
        'engine_version': ENGINE_VERSION,
        'plan_name': plan.name,
        'base_url': target.base_url,
        'allowed_methods': deepcopy(target.allowed_methods),
        'mode': mode,
        'nodes': [
            {
                'node_id': str(item['node'].pk),
                'users': item['users'],
                'validation_key': item['validation_key'],
            }
            for item in allocations
        ],
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


def _matching_validation(plan, node, validation_key, *, lock=False):
    if node.enrollment_consumed_at is None:
        return None
    candidates = PerformanceRun.objects.filter(
        plan=plan,
        participants__node=node,
        participants__validation_key=validation_key,
        mode=PerformanceRun.Mode.VALIDATION,
        status=PerformanceRun.Status.COMPLETED,
        validation_key=validation_key,
        created_at__gte=node.enrollment_consumed_at,
    ).order_by('-finished_at', '-created_at')
    if lock:
        candidates = candidates.select_for_update()
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
            return run
    return None


def node_eligibility(plan, now=None):
    now = now or timezone.now()
    items = []
    nodes = PerformanceNode.objects.filter(
        project=plan.project, deleted_at__isnull=True,
    ).order_by('id')
    for node in nodes:
        status = node.status_at(now)
        compatible = bool(
            node.revoked_at is None
            and node.protocol_version == PROTOCOL_VERSION
            and node.agent_version == AGENT_VERSION
            and node.engine_version == ENGINE_VERSION
        )
        try:
            validation_key = validation_key_for(plan, node)
            validation_run = _matching_validation(plan, node, validation_key)
        except (TypeError, ValueError, OverflowError):
            validation_run = None
        reason_code = ''
        reason = ''
        if status != 'online':
            reason_code = f'node_{status}'
            reason = {'pending': '节点尚未注册。', 'offline': '节点当前离线。',
                      'revoked': '节点已吊销。'}.get(status, '节点不可用。')
        elif not compatible:
            reason_code = 'version_mismatch'
            reason = f'节点需升级至 Agent {AGENT_VERSION} / 协议 {PROTOCOL_VERSION}。'
        elif validation_run is None:
            reason_code = 'validation_required'
            reason = '当前计划尚未在此节点完成有效的单用户验证。'
        items.append({
            'node_id': str(node.pk),
            'node_name': node.name,
            'status': status,
            'compatible': compatible,
            'validation_valid': validation_run is not None,
            'validation_run_id': str(validation_run.pk) if validation_run else None,
            'reason_code': reason_code,
            'reason': reason,
        })
    return items


def _normalize_node_ids(node_ids, mode):
    if not isinstance(node_ids, (list, tuple)):
        raise RunValidationRejected('node_ids 必须是节点 UUID 数组。')
    if len(node_ids) != len(set(node_ids)):
        raise RunValidationRejected('node_ids 不能包含重复节点。')
    if mode == PerformanceRun.Mode.VALIDATION and len(node_ids) != 1:
        raise RunValidationRejected('单用户验证必须且只能选择一个节点。')
    if mode == PerformanceRun.Mode.LOAD and not 1 <= len(node_ids) <= MAX_NODES_PER_RUN:
        raise RunValidationRejected(f'正式压测必须选择 1 到 {MAX_NODES_PER_RUN} 个节点。')
    return tuple(sorted(node_ids, key=str))


@transaction.atomic
def create_run(project, plan_id, node_ids, request_id, created_by, mode='load'):
    normalized_node_ids = _normalize_node_ids(node_ids, mode)
    # Serialize idempotency decisions per project on databases with row locks.
    locked_project = Project.objects.select_for_update().get(pk=project.pk)
    existing = PerformanceRun.objects.prefetch_related('participants').filter(
        project=locked_project, request_id=request_id,
    ).first()
    if existing is not None:
        existing_node_ids = tuple(sorted(
            (item.node_id for item in existing.participants.all()), key=str,
        ))
        if (
            existing.plan_id == plan_id
            and existing_node_ids == normalized_node_ids
            and existing.mode == mode
        ):
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
    nodes = list(PerformanceNode.objects.select_for_update().filter(
        pk__in=normalized_node_ids, project=locked_project, deleted_at__isnull=True,
    ).order_by('id'))
    if len(nodes) != len(normalized_node_ids):
        found_ids = {node.pk for node in nodes}
        missing_ids = [node_id for node_id in normalized_node_ids if node_id not in found_ids]
        known = {
            node.pk: node
            for node in PerformanceNode.objects.filter(pk__in=missing_ids).only(
                'id', 'project_id', 'deleted_at',
            )
        }
        node_id = missing_ids[0]
        candidate = known.get(node_id)
        if candidate is None:
            raise RunValidationRejected(f'性能节点 {node_id} 不存在。')
        if candidate.project_id != locked_project.pk:
            raise RunValidationRejected(f'性能节点 {node_id} 不属于当前项目。')
        raise RunValidationRejected(f'性能节点 {node_id} 已删除。')
    now = timezone.now()
    if mode == PerformanceRun.Mode.LOAD and plan.users < len(nodes):
        raise RunValidationRejected('总虚拟用户数不能少于所选节点数。')
    for node in nodes:
        if node.revoked_at is not None:
            raise RunValidationRejected(f'节点 {node.name} 已吊销。')
        status_at = node.status_at(now)
        if status_at == 'pending':
            raise RunValidationRejected(f'节点 {node.name} 尚未注册。')
        if status_at == 'offline':
            raise RunValidationRejected(f'节点 {node.name} 当前离线。')
        if not _node_is_compatible(node, now):
            raise RunValidationRejected(
                f'节点 {node.name} 版本不兼容，必须升级至 Agent '
                f'{AGENT_VERSION} / 协议 {PROTOCOL_VERSION}。'
            )

    quotient, remainder = divmod(1 if mode == PerformanceRun.Mode.VALIDATION else plan.users, len(nodes))
    allocations = []
    for index, node in enumerate(nodes):
        try:
            current_validation_key = validation_key_for(plan, node)
        except (TypeError, ValueError, OverflowError) as exc:
            raise RunValidationRejected('计划或节点验证指纹无法生成。') from exc
        validation_run = None
        if mode == PerformanceRun.Mode.LOAD:
            validation_run = _matching_validation(
                plan, node, current_validation_key, lock=True,
            )
            if validation_run is None:
                raise RunValidationRejected(
                    f'节点 {node.name} 没有与当前计划、目标和节点能力匹配的成功验证。'
                )
        allocations.append({
            'node': node,
            'users': quotient + (1 if index < remainder else 0),
            'validation_key': current_validation_key,
            'validation_run': validation_run,
        })

    run = PerformanceRun(
        project=locked_project,
        plan=plan,
        created_by=created_by,
        request_id=request_id,
        mode=mode,
        validation_key=allocations[0]['validation_key'] if len(allocations) == 1 else '',
    )
    snapshot, digest = _snapshot_for(run.pk, plan, allocations, mode)
    run.snapshot = snapshot
    run.snapshot_sha256 = digest
    run.save(force_insert=True)
    PerformanceRunNode.objects.bulk_create([
        PerformanceRunNode(
            run=run,
            node=item['node'],
            node_name=item['node'].name,
            node_agent_version=item['node'].agent_version,
            node_protocol_version=item['node'].protocol_version,
            node_engine_version=item['node'].engine_version,
            assigned_users=item['users'],
            validation_key=item['validation_key'],
            validation_run=item['validation_run'],
        )
        for item in allocations
    ])
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
        if not locked.reason_code:
            locked.reason_code = 'cancelled'
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
        fields = ['status', 'stop_requested_at']
        if not locked.reason_code:
            locked.reason_code = 'cancelled'
            locked.reason = '用户请求停止运行。'
            fields.extend(('reason_code', 'reason'))
        fields = tuple(fields)
    locked.save(update_fields=fields)
    participant_status = (
        PerformanceRunNode.Status.STOPPED
        if locked.status == PerformanceRun.Status.CANCELLED
        else PerformanceRunNode.Status.STOPPING
    )
    locked.participants.exclude(status__in=(
        PerformanceRunNode.Status.STOPPED,
        PerformanceRunNode.Status.FAILED,
        PerformanceRunNode.Status.LOST,
    )).update(status=participant_status)
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
    participant = None
    if report is not None:
        reported_run = PerformanceRun.objects.select_for_update().filter(pk=report['run_id']).first()
        if reported_run is None:
            raise RunReportRejected('run_report 不属于当前节点或运行不存在。')
        participant = PerformanceRunNode.objects.select_for_update().filter(
            run=reported_run, node=node,
        ).first()
        if participant is None:
            raise RunReportRejected('run_report 不属于当前节点或运行不存在。')
        if report['sequence'] <= participant.node_report_seq:
            raise RunReportRejected('run_report.sequence 必须严格递增。')
        participant.node_report = _serialized_report(report)
        participant.node_report_seq = report['sequence']
        fields = ['node_report', 'node_report_seq']
        report_state = report['state']
        terminal_member_states = {
            PerformanceRunNode.Status.STOPPED,
            PerformanceRunNode.Status.FAILED,
            PerformanceRunNode.Status.LOST,
        }
        if participant.status not in terminal_member_states:
            if report_state == 'ready':
                if participant.status != PerformanceRunNode.Status.STOPPING:
                    participant.status = PerformanceRunNode.Status.READY
                    fields.append('status')
                if participant.ready_at is None:
                    participant.ready_at = now
                    fields.append('ready_at')
            elif report_state == 'running':
                if participant.status != PerformanceRunNode.Status.STOPPING:
                    participant.status = PerformanceRunNode.Status.RUNNING
                    fields.append('status')
                if participant.started_at is None:
                    participant.started_at = now
                    fields.append('started_at')
            elif report_state == 'stopped':
                participant.status = PerformanceRunNode.Status.STOPPED
                participant.stopped_at = participant.stopped_at or now
                fields.extend(('status', 'stopped_at'))
            elif report_state == 'failed':
                participant.status = PerformanceRunNode.Status.FAILED
                participant.stopped_at = participant.stopped_at or now
                fields.extend(('status', 'stopped_at'))
            elif report_state == 'preparing' and participant.status == PerformanceRunNode.Status.QUEUED:
                participant.status = PerformanceRunNode.Status.PREPARING
                fields.append('status')
        if report['reason_code'] and not participant.reason_code:
            participant.reason_code = report['reason_code']
            participant.reason = report['reason']
            fields.extend(('reason_code', 'reason'))
        participant.save(update_fields=tuple(dict.fromkeys(fields)))

    candidate = reported_run
    if candidate is None:
        candidate_run_id = PerformanceRunNode.objects.filter(
            node=node,
            run__status__in=(
                PerformanceRun.Status.PREPARING,
                PerformanceRun.Status.RUNNING,
                PerformanceRun.Status.STOPPING,
            ),
        ).exclude(node_command={}).order_by('run__created_at').values_list(
            'run_id', flat=True,
        ).first()
        if candidate_run_id is not None:
            # Re-read both rows under the canonical Run -> RunNode lock order.
            # A controller cleanup racing this heartbeat must finish first, so a
            # cleared/stale prepare command can never be renewed afterwards.
            candidate = PerformanceRun.objects.select_for_update().get(pk=candidate_run_id)
    if candidate is None:
        return {'type': 'idle'}
    if participant is None:
        participant = PerformanceRunNode.objects.select_for_update().filter(
            run=candidate, node=node,
        ).first()
    if participant is None:
        return {'type': 'idle'}
    if (
        participant.node_agent_version != node.agent_version
        or participant.node_protocol_version != node.protocol_version
        or participant.node_engine_version != node.engine_version
    ):
        reason = '节点执行身份或版本已变化，运行无法继续。'
        stopped = stop_runs_for_node_identity_change(
            node, 'node_identity_changed', reason, now,
        )
        authoritative = next((item for item in stopped if item.pk == candidate.pk), candidate)
        return _stop_command(authoritative, authoritative.reason or reason)
    if candidate.status in PerformanceRun.TERMINAL_STATUSES:
        return _stop_command(candidate, candidate.reason or f'运行已进入终态：{candidate.status}')

    execution = controller_execution_status(now)
    if not execution['available']:
        return _stop_command(candidate, execution['reason'] or '性能执行配置不可用。')
    if not execution['controller_online']:
        return _stop_command(candidate, '性能控制器租约已失效。')

    command = participant.node_command
    if (
        isinstance(command, dict)
        and command.get('type') in {'prepare', 'stop'}
        and command.get('run_id') == str(candidate.pk)
    ):
        if command.get('type') == 'prepare':
            participant.last_command_at = now
            participant.save(update_fields=('last_command_at',))
        return deepcopy(command)
    return {'type': 'idle'}


@transaction.atomic
def stop_runs_for_node_identity_change(node, reason_code, reason, now=None):
    now = now or timezone.now()
    run_ids = list(PerformanceRunNode.objects.filter(
        node=node, run__status__in=PerformanceRun.ACTIVE_STATUSES,
    ).order_by('run_id').values_list('run_id', flat=True))
    runs = []
    for run_id in run_ids:
        run = PerformanceRun.objects.select_for_update().get(pk=run_id)
        participants = list(PerformanceRunNode.objects.select_for_update().filter(
            run=run,
        ).order_by('node_id'))
        if run.status == PerformanceRun.Status.QUEUED:
            run.status = PerformanceRun.Status.INCOMPLETE
            run.finished_at = now
            state_field = 'finished_at'
        else:
            run.status = PerformanceRun.Status.STOPPING
            run.stop_requested_at = run.stop_requested_at or now
            state_field = 'stop_requested_at'
        fields = ['status', state_field]
        if not run.reason_code:
            run.reason_code = reason_code
            run.reason = reason
            fields.extend(('reason_code', 'reason'))
        stop_command = _stop_command(run, run.reason or reason)
        for item in participants:
            item.node_command = stop_command
            if item.node_id == node.pk:
                item.status = PerformanceRunNode.Status.LOST
                if not item.reason_code:
                    item.reason_code = reason_code
                    item.reason = reason
            elif item.status not in (
                PerformanceRunNode.Status.STOPPED,
                PerformanceRunNode.Status.FAILED,
                PerformanceRunNode.Status.LOST,
            ):
                item.status = PerformanceRunNode.Status.STOPPING
            item.save(update_fields=('node_command', 'status', 'reason_code', 'reason'))
        run.save(update_fields=fields)
        runs.append(run)
    return runs
