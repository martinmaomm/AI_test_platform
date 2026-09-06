"""
Scheduled Tasks Celery异步任务
定时任务执行逻辑和动态注册函数
"""
import json
import logging
from datetime import datetime
from celery import shared_task
from django_celery_beat.models import PeriodicTask, CrontabSchedule

from .models import ScheduledTask
from common.task import build_error_result

logger = logging.getLogger(__name__)


# ============ 定时任务执行函数 ============

@shared_task(bind=True, name='scheduled_tasks.run_scheduled_task')
def run_scheduled_task(self, task_id: int, manual: bool = False):
    """Reserve one run; the serial orchestrator dispatches only its first suite."""
    from .scheduling import reserve_scheduled_run

    reservation = reserve_scheduled_run(task_id, manual=manual)
    if reservation.started:
        return {
            'success': True,
            'message': '定时任务执行已启动',
            'execution_id': reservation.execution_log_id,
        }
    return build_error_result(self.request.id, reservation.error or '定时任务未启动')


# ============ 定时任务动态注册函数 ============

def register_periodic_task(task: ScheduledTask) -> bool:
    """
    注册定时任务到Celery Beat
    
    Args:
        task: 定时任务对象
    
    Returns:
        bool: 注册是否成功
    """
    from .cron import parse_cron, schedule_timezone

    if task.status != 'active':
        unregister_periodic_task(task)
        return True
    schedule, _ = CrontabSchedule.objects.get_or_create(
        **parse_cron(task.cron_expression),
        timezone=schedule_timezone(),
    )
    PeriodicTask.objects.update_or_create(
        name=f"scheduled_task_{task.id}",
        defaults={
            'crontab': schedule,
            'task': 'scheduled_tasks.run_scheduled_task',
            'args': json.dumps([task.id]),
            'enabled': True,
            'description': f"定时任务: {task.name}",
        },
    )
    logger.info("定时任务注册成功: %s (ID: %s)", task.name, task.id)
    return True


def unregister_periodic_task(task: ScheduledTask) -> bool:
    """
    从 Celery Beat 中移除定时任务。
    """
    deleted, _ = PeriodicTask.objects.filter(name=f"scheduled_task_{task.id}").delete()
    logger.info("定时任务已移除: %s (ID: %s), deleted=%s", task.name, task.id, deleted)
    return True


def update_periodic_task(task: ScheduledTask) -> bool:
    """
    更新Celery Beat中的定时任务
    
    Args:
        task: 定时任务对象
    
    Returns:
        bool: 更新是否成功
    """
    if task.status == 'active':
        return register_periodic_task(task)
    return unregister_periodic_task(task)


def calculate_next_run_time(cron_expression: str) -> datetime:
    """
    计算下次执行时间
    
    Args:
        cron_expression: Cron表达式
    
    Returns:
        Optional[datetime]: 下次执行时间
    """
    from .cron import next_run_time
    return next_run_time(cron_expression)


# Import the task at module load so Celery autodiscovery registers the serial
# dispatcher even before the first scheduled run imports ``scheduling``.
from .scheduling import dispatch_scheduled_suite  # noqa: E402,F401
