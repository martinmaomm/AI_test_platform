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

from django.db import transaction
from django.utils import timezone

from .models import PerformanceControllerState, PerformanceRun
from .pki import create_run_certificates, private_write
from .runtime_settings import NODE_SOURCE, RuntimeSettings
from performance_node.locust_runtime import canonical_sha256, validate_snapshot

logger = logging.getLogger(__name__)
ACTIVE = ('preparing', 'running', 'stopping')
TERMINAL = ('completed', 'failed', 'cancelled', 'incomplete')
LEASE_SECONDS = 15
REAP_SECONDS = 25


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
            old = PerformanceRun.objects.filter(status__in=ACTIVE).order_by('created_at').first()
            if old:
                self.current = old.pk
                self.orphan = True
                self.reaping_since = time.monotonic()
                self.pending_result = ('incomplete', 'controller_restarted', '控制器重启，旧运行已停止，未自动重跑')
                old.status = 'stopping'
                old.node_command = {'type': 'stop', 'run_id': str(old.pk), 'reason': '控制器重启'}
                old.save(update_fields=['status', 'node_command'])
                state.current_run = old
                state.save(update_fields=['current_run'])
        return True

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
                raise RuntimeError('控制器运行租约已失效')
            yield state, PerformanceRun.objects.select_for_update().get(pk=run_id)

    def tick(self):
        if not self.renew():
            self.stop_process()
            raise RuntimeError('控制器租约已被其他实例接管')
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
            except Exception:
                logger.exception('性能运行准备失败，run_id=%s', run.pk)
                self.begin_reap('failed', 'prepare_failed', '准备压测进程或 TLS 失败，请检查控制器日志')
            return
        run = PerformanceRun.objects.select_related('node').get(pk=self.current)
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
        done = read_json(self.directory / 'complete.json')
        result = read_json(self.directory / 'supervisor-result.json')
        if done is not None:
            if done.get('complete'):
                cancelled = run.stop_requested_at is not None or done.get('reason') == 'cancelled'
                self.begin_reap('cancelled' if cancelled else 'completed', '',
                                '已手动停止' if cancelled else '压测执行完成')
            else:
                status = 'cancelled' if done.get('reason') == 'cancelled' and not run.started_at else 'incomplete'
                self.begin_reap(status, done.get('reason', 'missing_final_metrics'), '运行结束但最终统计不完整')
            return
        if result is not None or (self.process and self.process.poll() is not None):
            self.begin_reap('incomplete' if run.started_at else 'failed', 'engine_exited', '执行进程异常结束，未收到完整最终统计')
            return
        if run.node.revoked_at or not run.node.agent_token_digest or run.node.status_at() != 'online':
            identity_changed = run.reason_code == 'node_revoked'
            self.begin_reap('incomplete' if run.started_at else 'failed',
                            run.reason_code if identity_changed else 'node_unavailable',
                            run.reason if identity_changed else '节点已离线或身份已失效，运行已停止')
            return
        if run.node_report.get('state') == 'failed':
            self.begin_reap('incomplete' if run.started_at else 'failed', 'node_execution_failed', run.node_report.get('reason') or '节点准备或执行失败')
            return
        if run.status == 'stopping' or run.stop_requested_at:
            touch_lease(self.directory / 'start.stop')
            return
        if run.node_report.get('state') in ('ready', 'running') and metrics and metrics.get('admitted_workers') == 1:
            touch_lease(self.directory / 'start')

    def prepare(self, run):
        run.refresh_from_db()
        if run.node.status_at() != 'online' or run.node.revoked_at:
            raise ValueError('节点已离线')
        validate_snapshot(run.snapshot)
        if canonical_sha256(run.snapshot) != run.snapshot_sha256:
            raise ValueError('运行快照摘要不符')
        # Re-check target approval after queueing; never mutate the frozen snapshot.
        if run.plan_id:
            target = run.plan.target
            if target.base_url.rstrip('/') != run.snapshot['base_url'].rstrip('/') or not set(step['method'] for step in run.snapshot['steps']).issubset(target.allowed_methods):
                raise ValueError('目标批准范围已变化')
        self.directory = self.config.root / str(run.pk)
        self.directory.mkdir(mode=0o700, exist_ok=False)
        credentials = create_run_certificates(self.directory, self.config.server_name, run.pk, run.node_id)
        with socket.socket() as available:
            available.bind(('127.0.0.1', 0))
            raw_port = available.getsockname()[1]
        handshake = secrets.token_urlsafe(32)
        script = (NODE_SOURCE / 'performance_node/locust_runtime.py').read_text(encoding='utf-8')
        private_write(self.directory / 'engine.py', script)
        private_write(self.directory / 'run.json', json.dumps({'snapshot': run.snapshot, 'handshake_token': handshake}, ensure_ascii=False))
        bind = f'[{self.config.bind_host}]' if ':' in self.config.bind_host else self.config.bind_host
        private_write(self.directory / 'stunnel.conf',
                      'foreground = yes\npid =\nsyslog = no\ndebug = warning\n[rpc]\n'
                      f'accept = {bind}:{self.config.port}\nconnect = 127.0.0.1:{raw_port}\n'
                      f'cert = {self.directory / "server.pem"}\nkey = {self.directory / "server.key"}\n'
                      f'CAfile = {self.directory / "ca.pem"}\nverifyChain = yes\nrequireCert = yes\n'
                      'sslVersionMin = TLSv1.2\nTIMEOUTclose = 0\n')
        max_seconds = run.snapshot['duration_seconds'] + 75
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
        with (self.directory / 'supervisor.log').open('ab') as output:
            self.process = subprocess.Popen([sys.executable, '-m', 'performance_node.process_supervisor', str(self.directory / 'supervisor.json')],
                                            cwd=self.directory, env=child_environment(), stdin=subprocess.DEVNULL,
                                            stdout=output, stderr=output, start_new_session=True)
        command = {'type': 'prepare', 'run_id': str(run.pk), 'snapshot': run.snapshot, 'snapshot_sha256': run.snapshot_sha256,
                   'script_source': script, 'script_sha256': hashlib.sha256(script.encode()).hexdigest(), 'lease_seconds': LEASE_SECONDS,
                   'max_seconds': max_seconds, 'handshake_token': handshake,
                   'tls': {**credentials, 'host': self.config.public_host, 'port': self.config.port, 'server_name': self.config.server_name}}
        with self.owned_run(run.pk) as (_, locked):
            if locked.status not in ACTIVE:
                raise RuntimeError('运行已终止，禁止下发准备指令')
            if locked.reason_code == 'node_revoked':
                return  # Revocation wins over a concurrently finishing prepare.
            locked.node_command = command
            locked.save(update_fields=['node_command'])

    def record_metrics(self, run, metrics):
        if not isinstance(metrics, dict) or type(metrics.get('requests')) is not int:
            return
        with self.owned_run(run.pk) as (_, locked):
            if locked.status in ACTIVE:
                self._record_metrics(locked, metrics)

    def _record_metrics(self, run, metrics):
        history = list(run.metrics_samples or [])
        if time.monotonic() - self.last_sample >= 2 or metrics.get('complete'):
            history.append({'timestamp': timezone.now().isoformat(), 'metrics': metrics})
            history = history[-400:]
            self.last_sample = time.monotonic()
        updates = {'latest_metrics': metrics, 'metrics_samples': history}
        if metrics.get('started') and not run.started_at:
            updates['started_at'] = timezone.now()
        if metrics.get('started') and run.status == 'preparing':
            # Do not overwrite a concurrent user's stop request.
            PerformanceRun.objects.filter(pk=run.pk, status='preparing').update(status='running')
        PerformanceRun.objects.filter(pk=run.pk, status__in=ACTIVE).update(**updates)

    def begin_reap(self, status, code, reason):
        self.stop_process()
        self.pending_result = (status, code, str(reason)[:500])
        self.reaping_since = time.monotonic()
        with self.owned_run(self.current):
            PerformanceRun.objects.filter(pk=self.current, status__in=ACTIVE).update(
                status='stopping', node_command={'type': 'stop', 'run_id': str(self.current), 'reason': str(reason)[:500]})

    def reap(self, run):
        self.stop_process()
        if self.process and self.process.poll() is None:
            return  # Never release the global slot while our supervisor is alive.
        acknowledged = run.node_report.get('state') in ('stopped', 'failed')
        if not acknowledged and time.monotonic() - self.reaping_since < REAP_SECONDS:
            return
        status, code, reason = self.pending_result
        with self.owned_run(run.pk) as (state, _):
            PerformanceRun.objects.filter(pk=run.pk, status__in=ACTIVE).update(
                status=status, reason_code=code, reason=reason, finished_at=timezone.now(), node_command={})
            state.current_run = None
            state.save(update_fields=['current_run'])
        # Only delete secrets that this controller generated for this exact run.
        if self.directory and self.directory.name == str(run.pk):
            for name in ('client.key', 'server.key', 'run.json'):
                (self.directory / name).unlink(missing_ok=True)
        self.current = self.process = self.directory = self.reaping_since = self.pending_result = None
        self.orphan = False
        self.last_sample = 0

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
