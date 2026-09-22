"""Single global execution controller. No Locust or gevent in this process."""
from __future__ import annotations

from datetime import timedelta
from contextlib import contextmanager
import hashlib
import json
import logging
import os
from pathlib import Path
import secrets
import signal
import socket
import subprocess
import sys
import time
import uuid

from django.db import InterfaceError, OperationalError, transaction
from django.utils import timezone

from .models import PerformanceControllerState, PerformanceRun
from .constants import MAX_FAILURE_SAMPLES, MAX_FAILURE_VALUE_BYTES
from .pki import create_run_certificates, private_write
from .runtime_settings import NODE_SOURCE, RuntimeSettings
from performance_node.locust_runtime import (
    canonical_sha256, normalize_validation_steps, validate_snapshot,
)

logger = logging.getLogger(__name__)
ACTIVE = ('preparing', 'running', 'stopping')
TERMINAL = ('completed', 'failed', 'cancelled', 'incomplete')
LEASE_SECONDS = 15
REAP_SECONDS = 30
SHUTDOWN_SECONDS = 30
# A delivered renewal may arrive after the DB transaction commits. Heartbeat
# transport is bounded; leave time for delivery, lease expiry and SIGKILL.
LEASE_RECLAIM_SECONDS = LEASE_SECONDS + 15 + 5 + 5


class ControllerLeaseLost(RuntimeError):
    """The controller must stop its processes before attempting a new claim."""


def _bounded_evidence_value(value):
    try:
        encoded = json.dumps(value, ensure_ascii=False, separators=(',', ':'), allow_nan=False)
    except (TypeError, ValueError):
        encoded = json.dumps(str(value), ensure_ascii=False)
    raw = encoded.encode('utf-8')
    if len(raw) <= MAX_FAILURE_VALUE_BYTES:
        return value
    return raw[:MAX_FAILURE_VALUE_BYTES].decode('utf-8', errors='ignore') + '…'


def bounded_validation_steps(value, snapshot=None):
    return normalize_validation_steps(value)


def bounded_metrics(metrics, snapshot=None, mode=None):
    """Defend the reporting database even if a local engine file is malformed."""
    result = {key: value for key, value in metrics.items() if key != 'nodes'}
    samples = result.get('failure_samples')
    normalized = []
    required = {
        'step_index', 'step_name', 'phase', 'check', 'comparator', 'expected',
        'actual', 'error_type', 'message',
    }
    if isinstance(samples, list):
        for sample in samples[:MAX_FAILURE_SAMPLES]:
            if not isinstance(sample, dict) or set(sample) not in (required, required | {'node_id'}):
                continue
            normalized.append({
                'step_index': sample['step_index'] if type(sample['step_index']) is int else 0,
                'step_name': str(sample['step_name'])[:200],
                'phase': sample['phase'] if sample['phase'] in ('setup', 'main') else 'main',
                'check': str(sample['check'])[:512],
                'comparator': str(sample['comparator'])[:32],
                'expected': _bounded_evidence_value(sample['expected']),
                'actual': _bounded_evidence_value(sample['actual']),
                'error_type': str(sample['error_type'])[:64],
                'message': str(sample['message'])[:500],
            })
            if sample.get('node_id') in {item['node_id'] for item in (snapshot or {}).get('nodes', [])}:
                normalized[-1]['node_id'] = sample['node_id']
    result['failure_samples'] = normalized
    details = result.pop('validation_steps', None)
    if mode != 'load' and isinstance(details, list):
        result['validation_steps'] = bounded_validation_steps(details, snapshot)
    return result


def read_json(path):
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 2 * 1024 * 1024:
        return None
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None


def touch_lease(path):
    if not path.exists():
        private_write(path, 'lease')
    else:
        os.utime(path, None)


def child_environment():
    result = {key: os.environ[key] for key in ('PATH', 'LANG', 'LC_ALL', 'SYSTEMROOT') if key in os.environ}
    result.update(PYTHONPATH=str(NODE_SOURCE), PYTHONUNBUFFERED='1', PYTHONDONTWRITEBYTECODE='1',
                  LOCUST_WORKER_LOG_REPORT_INTERVAL='-1')
    return result


class PerformanceController:
    def __init__(self, configuration=None):
        self.config = configuration or RuntimeSettings.load()
        self.owner = str(uuid.uuid4())
        self.current = None
        self.process = None
        self.directory = None
        self.reaping_since = None
        self.pending_result = None
        self.shutting_down = False
        self.orphan = False
        self.last_sample = 0
        self.stopping_since = None

    def claim(self):
        if not self.config.enabled:
            raise ValueError('PERFORMANCE_EXECUTION_ENABLED 未启用')
        if self.config.root.is_symlink():
            raise ValueError('执行目录不能是符号链接')
        self.config.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        if self.config.root.stat().st_mode & 0o077:
            raise ValueError('执行目录必须为专用 0700 目录')
        PerformanceControllerState.objects.get_or_create(pk=1)
        with transaction.atomic():
            state = PerformanceControllerState.objects.select_for_update().get(pk=1)
            now = timezone.now()
            if state.owner_id and state.lease_until and state.lease_until > now:
                return False
            state.owner_id = self.owner
            state.heartbeat_at = now
            state.lease_until = now + timedelta(seconds=LEASE_SECONDS)
            state.save()
            old = PerformanceRun.objects.select_for_update().filter(status__in=ACTIVE).order_by('created_at').first()
            if old:
                self.current = old.pk
                self.orphan = True
                self.reaping_since = time.monotonic()
                status = 'incomplete' if old.started_at else 'failed'
                self.pending_result = self._terminal_result(old, status, 'controller_restarted',
                                                           '控制器重启，旧运行已停止，未自动重跑')
                old.status = 'stopping'
                old.reason_code, old.reason = self.pending_result[1:]
                old.latest_metrics = {**old.latest_metrics, 'complete': False}
                old.save(update_fields=['status', 'reason_code', 'reason', 'latest_metrics'])
                self._stop_members(old, '控制器重启')
                state.current_run = old
                state.save(update_fields=['current_run'])
        return True

    @staticmethod
    def _terminal_result(run, status, code, reason):
        # All callers hold the parent Run lock. First persisted cause wins;
        # clocks on different nodes are not used to order termination events.
        if run.reason_code:
            if run.reason_code == 'cancelled':
                status = 'cancelled'
            elif status in ('completed', 'cancelled'):
                status = 'incomplete' if run.started_at else 'failed'
            return status, run.reason_code, run.reason
        return status, code, str(reason)[:500]

    @staticmethod
    def _stop_members(run, reason):
        for member in run.participants.select_for_update().order_by('node_id'):
            member.node_command = {'type': 'stop', 'run_id': str(run.pk), 'reason': str(reason)[:500]}
            if member.status not in ('stopped', 'failed', 'lost'):
                member.status = 'stopping'
            member.save(update_fields=['node_command', 'status'])

    def renew(self):
        now = timezone.now()
        return bool(PerformanceControllerState.objects.filter(pk=1, owner_id=self.owner, lease_until__gt=now).update(
            heartbeat_at=now, lease_until=now + timedelta(seconds=LEASE_SECONDS)))

    @contextmanager
    def owned_run(self, run_id):
        """Fence every state write, including writes after slow subprocess work."""
        with transaction.atomic():
            state = PerformanceControllerState.objects.select_for_update().get(pk=1)
            if (state.owner_id != self.owner or not state.lease_until
                    or state.lease_until <= timezone.now() or state.current_run_id != run_id):
                raise ControllerLeaseLost('控制器运行租约已失效')
            yield state, PerformanceRun.objects.select_for_update().get(pk=run_id)

    def tick(self):
        if not self.renew():
            self.stop_process()
            raise ControllerLeaseLost('控制器租约已过期或已被其他实例接管')
        if self.current is None:
            if self.shutting_down:
                return
            with transaction.atomic():
                state = PerformanceControllerState.objects.select_for_update().get(pk=1, owner_id=self.owner)
                if PerformanceRun.objects.filter(status__in=ACTIVE).exists():
                    raise RuntimeError('检测到未回收的活动运行，请重启控制器核对')
                run = PerformanceRun.objects.select_for_update().filter(status='queued').order_by('created_at').first()
                if not run:
                    return
                run.status = 'preparing'
                run.save(update_fields=['status'])
                state.current_run = run
                state.save(update_fields=['current_run'])
                self.current = run.pk
            try:
                self.prepare(run)
            except (InterfaceError, OperationalError, ControllerLeaseLost):
                # Connection/ownership loss requires retiring this controller,
                # not another database write through begin_reap().
                raise
            except Exception:
                logger.exception('性能运行准备失败，run_id=%s', run.pk)
                self.begin_reap('failed', 'prepare_failed', '准备压测进程或 TLS 失败，请检查控制器日志')
            return
        run = PerformanceRun.objects.get(pk=self.current)
        if self.reaping_since is not None:
            self.reap(run)
            return
        if run.status in TERMINAL:
            self.begin_reap(run.status, run.reason_code, run.reason or '运行已终止')
            return
        touch_lease(self.directory / 'lease')
        metrics = read_json(self.directory / 'metrics.json')
        if metrics:
            self.record_metrics(run, metrics)
            run.refresh_from_db()
        done = read_json(self.directory / 'complete.json')
        result = read_json(self.directory / 'supervisor-result.json')
        exited = result is not None or (self.process and self.process.poll() is not None)
        if done is None and exited:
            # complete.json is atomically written before exit. It can appear
            # between our first file read and process.poll(); recover that tail.
            self.stop_process()
            done = read_json(self.directory / 'complete.json')
        if done is not None:
            if isinstance(done.get('metrics'), dict):
                self.record_metrics(run, done['metrics'])
                run.refresh_from_db()
            if done.get('complete'):
                cancelled = run.stop_requested_at is not None or done.get('reason') == 'cancelled'
                self.begin_reap('cancelled' if cancelled else 'completed', '',
                                '已手动停止' if cancelled else '压测执行完成')
            else:
                status = ('cancelled' if done.get('reason') == 'cancelled' or run.reason_code == 'cancelled'
                          else 'incomplete' if run.started_at else 'failed')
                self.begin_reap(status, done.get('reason', 'missing_final_metrics'), '运行结束但最终统计不完整')
            return
        if exited:
            self.begin_reap('incomplete' if run.started_at else 'failed', 'engine_exited', '执行进程异常结束，未收到完整最终统计')
            return
        members = list(run.participants.select_related('node').order_by('node_id'))
        for member in members:
            node = member.node
            if node.revoked_at or not node.agent_token_digest or node.status_at() != 'online':
                self.begin_reap('incomplete' if run.started_at else 'failed',
                                'node_unavailable', f'节点 {member.node_name or node.name} 已离线或身份已失效')
                return
            if member.node_report.get('state') == 'failed':
                self.begin_reap('incomplete' if run.started_at else 'failed', 'node_execution_failed',
                                f'节点 {member.node_name or node.name}：{member.node_report.get("reason") or "准备或执行失败"}')
                return
        if run.status == 'stopping' or run.stop_requested_at:
            touch_lease(self.directory / 'start.stop')
            if self.stopping_since is None:
                self.stopping_since = time.monotonic()
            elif time.monotonic() - self.stopping_since > SHUTDOWN_SECONDS + 5:
                self.begin_reap('cancelled' if run.reason_code == 'cancelled' else 'incomplete',
                                'shutdown_timeout', '收尾超时，未收到全部最终统计')
            return
        expected = {str(member.node_id) for member in members}
        admitted = set(metrics.get('admitted_node_ids', [])) if metrics else set()
        if expected and admitted == expected and all(
                member.node_report.get('state') in ('ready', 'running') for member in members):
            with self.owned_run(run.pk) as (_, locked):
                # A heartbeat can report failure between the outer read and
                # acquiring Run. Re-read here, in the same ordering as reports.
                current_members = list(locked.participants.select_related('node').order_by('node_id'))
                frozen_ids = {item['node_id'] for item in locked.snapshot.get('nodes', [])}
                current_ids = {str(member.node_id) for member in current_members}
                ready = bool(current_ids and current_ids == frozen_ids == admitted and all(
                    member.node_report.get('state') in ('ready', 'running')
                    and member.node.status_at() == 'online' and not member.node.revoked_at
                    and member.node.agent_token_digest
                    for member in current_members))
                if locked.status == 'preparing' and not locked.stop_requested_at and not locked.reason_code and ready:
                    touch_lease(self.directory / 'start')

    def prepare(self, run):
        run.refresh_from_db()
        from .run_services import _node_is_compatible
        members = list(run.participants.select_related('node').order_by('node_id'))
        if not members or any(not _node_is_compatible(member.node, timezone.now()) for member in members):
            raise ValueError('参与节点已离线、身份失效或需要升级')
        validate_snapshot(run.snapshot)
        if canonical_sha256(run.snapshot) != run.snapshot_sha256:
            raise ValueError('运行快照摘要不符')
        frozen = {item['node_id']: item for item in run.snapshot['nodes']}
        if set(frozen) != {str(member.node_id) for member in members}:
            raise ValueError('参与节点集合与快照不符')
        for member in members:
            if (member.assigned_users != frozen[str(member.node_id)]['users']
                    or member.validation_key != frozen[str(member.node_id)]['validation_key']
                    or member.node_agent_version != member.node.agent_version
                    or member.node_protocol_version != member.node.protocol_version
                    or member.node_engine_version != member.node.engine_version):
                raise ValueError('参与节点能力或分配已变化，请重新验证并创建运行')
        # Re-check target approval after queueing; never mutate the frozen snapshot.
        if run.plan_id:
            target = run.plan.target
            if target.base_url.rstrip('/') != run.snapshot['base_url'].rstrip('/') or not set(step['method'] for step in run.snapshot['steps']).issubset(target.allowed_methods):
                raise ValueError('目标批准范围已变化')
        self.directory = self.config.root / str(run.pk)
        self.directory.mkdir(mode=0o700, exist_ok=False)
        credentials = create_run_certificates(self.directory, self.config.server_name, run.pk, frozen)
        with socket.socket() as available:
            available.bind(('127.0.0.1', 0))
            raw_port = available.getsockname()[1]
        handshakes = {node_id: secrets.token_urlsafe(32) for node_id in frozen}
        script = (NODE_SOURCE / 'performance_node/locust_runtime.py').read_text(encoding='utf-8')
        private_write(self.directory / 'engine.py', script)
        private_write(self.directory / 'run.json', json.dumps({'snapshot': run.snapshot, 'handshake_tokens': handshakes}, ensure_ascii=False))
        bind = f'[{self.config.bind_host}]' if ':' in self.config.bind_host else self.config.bind_host
        private_write(self.directory / 'stunnel.conf',
                      'foreground = yes\npid =\nsyslog = no\ndebug = warning\n[rpc]\n'
                      f'accept = {bind}:{self.config.port}\nconnect = 127.0.0.1:{raw_port}\n'
                      f'cert = {self.directory / "server.pem"}\nkey = {self.directory / "server.key"}\n'
                      f'CAfile = {self.directory / "ca.pem"}\nverifyChain = yes\nrequireCert = yes\n'
                      'sslVersionMin = TLSv1.2\nTIMEOUTclose = 0\n')
        max_seconds = run.snapshot['duration_seconds'] + 120
        engine_argv = [sys.executable, str(self.directory / 'engine.py'), '--role', 'master', '--config', str(self.directory / 'run.json'),
                       '--master-host', '127.0.0.1', '--master-port', str(raw_port), '--start-file', str(self.directory / 'start'),
                       '--metrics-file', str(self.directory / 'metrics.json'), '--complete-file', str(self.directory / 'complete.json')]
        supervisor = {'processes': [
            {'name': 'tunnel', 'argv': [self.config.stunnel, str(self.directory / 'stunnel.conf')], 'cwd': str(self.directory), 'log_path': str(self.directory / 'tunnel.log')},
            {'name': 'engine', 'argv': engine_argv, 'cwd': str(self.directory), 'log_path': str(self.directory / 'engine.log')},
        ], 'lease_file': str(self.directory / 'lease'), 'lease_seconds': LEASE_SECONDS,
            'max_seconds': max_seconds, 'result_file': str(self.directory / 'supervisor-result.json')}
        private_write(self.directory / 'supervisor.json', json.dumps(supervisor))
        touch_lease(self.directory / 'lease')
        with self.owned_run(run.pk) as (_, locked):
            if locked.status != 'preparing' or locked.stop_requested_at or locked.reason_code:
                raise RuntimeError('运行已终止，禁止下发准备指令')
            # Revalidate after certificate generation, while the parent lock
            # serializes revocation/stop. Do not lock Nodes in reverse order.
            for member in locked.participants.select_related('node').order_by('node_id'):
                if not _node_is_compatible(member.node, timezone.now()):
                    raise ValueError('准备期间节点资格发生变化')
            with (self.directory / 'supervisor.log').open('ab') as output:
                self.process = subprocess.Popen([sys.executable, '-m', 'performance_node.process_supervisor', str(self.directory / 'supervisor.json')],
                                                cwd=self.directory, env=child_environment(), stdin=subprocess.DEVNULL,
                                                stdout=output, stderr=output, start_new_session=True)
            for member in locked.participants.select_for_update().order_by('node_id'):
                node_id = str(member.node_id)
                member.node_command = {
                    'type': 'prepare', 'run_id': str(run.pk), 'node_id': node_id,
                    'snapshot': run.snapshot, 'snapshot_sha256': run.snapshot_sha256,
                    'script_source': script, 'script_sha256': hashlib.sha256(script.encode()).hexdigest(),
                    'lease_seconds': LEASE_SECONDS, 'max_seconds': max_seconds,
                    'handshake_token': handshakes[node_id],
                    'tls': {**credentials[node_id], 'host': self.config.public_host,
                            'port': self.config.port, 'server_name': self.config.server_name},
                }
                member.status = 'preparing'
                member.save(update_fields=['node_command', 'status'])

    def record_metrics(self, run, metrics):
        if not isinstance(metrics, dict) or type(metrics.get('requests')) is not int:
            return
        node_metrics = metrics.get('nodes', {})
        metrics = bounded_metrics(metrics, run.snapshot, run.mode)
        with self.owned_run(run.pk) as (_, locked):
            if locked.status in ACTIVE:
                self._record_metrics(locked, metrics, node_metrics)

    def _record_metrics(self, run, metrics, node_metrics=None):
        history = list(run.metrics_samples or [])
        sample_due = time.monotonic() - self.last_sample >= 2 or metrics.get('complete')
        now = timezone.now()
        if sample_due:
            history.append({'timestamp': timezone.now().isoformat(),
                            'metrics': {key: value for key, value in metrics.items() if key != 'validation_steps'}})
            history = history[-400:]
            self.last_sample = time.monotonic()
        updates = {'latest_metrics': metrics, 'metrics_samples': history}
        if metrics.get('started') and not run.started_at:
            updates['started_at'] = now
        if metrics.get('started') and run.status == 'preparing':
            # Do not overwrite a concurrent user's stop request.
            PerformanceRun.objects.filter(pk=run.pk, status='preparing').update(status='running')
        PerformanceRun.objects.filter(pk=run.pk, status__in=ACTIVE).update(**updates)
        if not isinstance(node_metrics, dict):
            return
        # Each member owns its own bounded series. Do not multiply the global
        # failure-sample budget by keeping a second independent collection.
        for member in run.participants.select_for_update().order_by('node_id'):
            data = node_metrics.get(str(member.node_id))
            if not isinstance(data, dict) or type(data.get('requests')) is not int:
                continue
            data = bounded_metrics(data, run.snapshot, 'load')
            data['failure_samples'] = [sample for sample in metrics['failure_samples']
                                       if sample.get('node_id') == str(member.node_id)]
            member.latest_metrics = data
            if sample_due:
                member.metrics_samples = [*(member.metrics_samples or []),
                    {'timestamp': now.isoformat(), 'metrics': data}][-400:]
            fields = ['latest_metrics', 'metrics_samples']
            if metrics.get('started') and member.started_at is None:
                member.started_at = now
                fields.append('started_at')
            if data.get('status') in ('lost', 'failed') and member.status not in ('stopped', 'failed', 'lost'):
                member.status = data['status']
                fields.append('status')
            if data.get('reason_code') and not member.reason_code:
                member.reason_code = str(data['reason_code'])[:64]
                member.reason = str(data.get('reason') or '')[:500]
                fields.extend(['reason_code', 'reason'])
            member.save(update_fields=fields)

    def begin_reap(self, status, code, reason):
        # Called only after Master has finalized, or on emergency termination.
        # User stop takes the start.stop path first and keeps renewing leases.
        self.stop_process()
        with self.owned_run(self.current) as (_, run):
            self.pending_result = self._terminal_result(run, status, code, reason)
            run.status = 'stopping'
            run.reason_code, run.reason = self.pending_result[1:]
            if status in ('failed', 'incomplete') and run.latest_metrics:
                run.latest_metrics = {**run.latest_metrics, 'complete': False}
            run.save(update_fields=['status', 'reason_code', 'reason', 'latest_metrics'])
            self._stop_members(run, reason)
        self.reaping_since = time.monotonic()

    def reap(self, run):
        self.stop_process()
        if self.process and self.process.poll() is None:
            return  # Never release the global slot while our supervisor is alive.
        # For a predecessor we cannot wait() on its process. Its unrenewed
        # independent lease plus forced-shutdown bound is the proof instead.
        if self.orphan and time.monotonic() - self.reaping_since < LEASE_RECLAIM_SECONDS:
            return
        status, code, reason = self.pending_result
        with self.owned_run(run.pk) as (state, locked):
            now = timezone.now()
            members = list(locked.participants.select_for_update().order_by('node_id'))
            for member in members:
                acknowledged = member.node_report.get('state') in ('stopped', 'failed')
                expired = member.last_command_at is None or (
                    now - member.last_command_at).total_seconds() >= LEASE_RECLAIM_SECONDS
                if not acknowledged and not expired:
                    return
            status, code, reason = self._terminal_result(locked, status, code, reason)
            for member in members:
                member.node_command = {}
                if member.node_report.get('state') in ('stopped', 'failed'):
                    if member.status not in ('failed', 'lost'):
                        member.status = member.node_report['state']
                    member.stopped_at = member.stopped_at or now
                elif member.last_command_at:
                    member.status = 'lost'
                    member.reason_code = member.reason_code or 'lease_expired'
                    member.reason = member.reason or '未收到停止确认，已等待命令租约及强制回收期限'
                else:
                    member.status = 'stopped'
                    member.stopped_at = member.stopped_at or now
                member.save(update_fields=['node_command', 'status', 'stopped_at', 'reason_code', 'reason'])
            PerformanceRun.objects.filter(pk=run.pk, status__in=ACTIVE).update(
                status=status, reason_code=code, reason=reason, finished_at=now)
            state.current_run = None
            state.save(update_fields=['current_run'])
        # Only delete secrets that this controller generated for this exact run.
        if self.directory and self.directory.name == str(run.pk):
            for name in ('server.key', 'run.json', *(f'client-{member.node_id}.key' for member in members)):
                (self.directory / name).unlink(missing_ok=True)
        self.current = self.process = self.directory = self.reaping_since = self.pending_result = None
        self.orphan = False
        self.last_sample = 0
        self.stopping_since = None

    def stop_process(self):
        if self.process and self.process.poll() is None:
            self.process.send_signal(signal.SIGTERM)
            try:
                self.process.wait(timeout=12)
            except subprocess.TimeoutExpired:
                # Its independent lease also expires; do not start a new run early.
                logger.error('监督进程未及时退出，等待租约回收，pid=%s', self.process.pid)

    def close(self):
        self.shutting_down = True
        try:
            if self.current and self.reaping_since is None:
                self.begin_reap('incomplete', 'controller_stopped', '控制器停止，运行已中止')
            deadline = time.monotonic() + REAP_SECONDS + 2
            while self.current and time.monotonic() < deadline:
                self.tick()
                time.sleep(.2)
        finally:
            self.stop_process()
            PerformanceControllerState.objects.filter(pk=1, owner_id=self.owner).update(
                owner_id='', lease_until=timezone.now())
