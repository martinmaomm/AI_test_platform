import signal
import time

from django.core.management.base import BaseCommand, CommandError
from django.db import close_old_connections

from performance_testing.controller import PerformanceController


class Command(BaseCommand):
    help = '运行独立性能执行控制器（全局顺序调度，不占用 Celery）'

    def handle(self, *args, **options):
        try:
            controller = PerformanceController()
            claimed = controller.claim()
        except (ValueError, OSError) as exc:
            raise CommandError(str(exc)) from None
        if not claimed:
            raise CommandError('已有性能执行控制器持有租约')
        stopping = [False]
        signal.signal(signal.SIGTERM, lambda *_: stopping.__setitem__(0, True))
        signal.signal(signal.SIGINT, lambda *_: stopping.__setitem__(0, True))
        self.stdout.write('性能执行控制器已启动')
        try:
            while not stopping[0]:
                controller.tick()
                close_old_connections()
                time.sleep(.5)
        finally:
            controller.close()
