import signal
from threading import Event

from django.core.management.base import BaseCommand, CommandError
from django.db import InterfaceError, OperationalError, close_old_connections, connections

from performance_testing.controller import ControllerLeaseLost, PerformanceController


RETRY_MAX_SECONDS = 30
DATABASE_TIMEOUT_SECONDS = 5
# Retry connection/transport failures, not invalid SQL or bad credentials.
CONNECTION_ERROR_CODES = {1040, 1042, 1053, 1158, 1159, 1160, 1161,
                          2002, 2003, 2006, 2013, 2055}


def connection_failure(exc):
    return isinstance(exc, InterfaceError) or (
        isinstance(exc, OperationalError) and exc.args
        and exc.args[0] in CONNECTION_ERROR_CODES
    )


def configure_database_timeouts():
    """Bound this command's DB operations so lost networking cannot hang it."""
    database = connections['default']
    if database.vendor != 'mysql':
        return
    options = dict(database.settings_dict.get('OPTIONS', {}))
    for key in ('connect_timeout', 'read_timeout', 'write_timeout'):
        value = options.get(key)
        options[key] = min(value, DATABASE_TIMEOUT_SECONDS) if (
            isinstance(value, (int, float)) and value > 0
        ) else DATABASE_TIMEOUT_SECONDS
    database.close()
    database.settings_dict['OPTIONS'] = options


class Command(BaseCommand):
    help = '运行独立性能执行控制器（全局顺序调度，不占用 Celery）'
    # Startup must reach the reconnect loop even when MySQL is unavailable.
    requires_system_checks = []

    def handle(self, *args, **options):
        stopping = Event()
        previous = {}
        for sig in (signal.SIGTERM, signal.SIGINT):
            previous[sig] = signal.signal(sig, lambda *_: stopping.set())
        try:
            configure_database_timeouts()
            self.run(stopping)
        except (ValueError, OSError) as exc:
            raise CommandError(str(exc)) from None
        finally:
            for sig, handler in previous.items():
                signal.signal(sig, handler)

    def run(self, stopping):
        controller = retiring = None
        recovering = False
        delay = 1
        try:
            while not stopping.is_set():
                # Never reclaim or start another run while our old supervisor
                # is alive. Its independent lease also expires during outage.
                if retiring is not None:
                    retiring.stop_process()
                    if retiring.process and retiring.process.poll() is None:
                        stopping.wait(1)
                        continue
                    retiring = None
                try:
                    close_old_connections()
                    if controller is None:
                        controller = PerformanceController()
                        if not controller.claim():
                            controller = None
                            if not recovering:
                                raise CommandError('已有性能执行控制器持有租约')
                            # A previous claim may have committed before its
                            # response was lost; never overwrite a live lease.
                            stopping.wait(delay)
                            delay = min(delay * 2, RETRY_MAX_SECONDS)
                            continue
                        if not recovering:
                            self.stdout.write('性能执行控制器已启动')
                    controller.tick()
                    if recovering:
                        self.stdout.write('性能控制器已恢复调度；中断的运行不会自动重跑')
                    recovering = False
                    delay = 1
                    stopping.wait(.5)
                except (InterfaceError, OperationalError, ControllerLeaseLost) as exc:
                    if not isinstance(exc, ControllerLeaseLost) and not connection_failure(exc):
                        raise
                    if not recovering:
                        reason = '调度租约失效' if isinstance(exc, ControllerLeaseLost) else '数据库连接中断'
                        # Never include raw database exceptions/connection credentials.
                        self.stderr.write(f'{reason}，已暂停调度；将自动重试，最长间隔 {RETRY_MAX_SECONDS} 秒')
                    recovering = True
                    retiring, controller = controller, None
                    if retiring is not None:
                        retiring.stop_process()
                    connections.close_all()
                    stopping.wait(delay)
                    delay = min(delay * 2, RETRY_MAX_SECONDS)
        finally:
            try:
                if controller is not None:
                    try:
                        controller.close()
                    except (InterfaceError, OperationalError, ControllerLeaseLost):
                        self.stderr.write('停止时数据库或租约不可用；本地执行进程已请求停止，剩余租约将自行过期')
                        controller.stop_process()
                if retiring is not None:
                    retiring.stop_process()
            finally:
                connections.close_all()
