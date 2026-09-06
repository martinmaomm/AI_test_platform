"""Scheduled task registration signals."""
import logging
from django.db.models.signals import post_save, post_delete
from django.dispatch import receiver

from .models import ScheduledTask
from .tasks import register_periodic_task, unregister_periodic_task, update_periodic_task

logger = logging.getLogger(__name__)

# ============ ScheduledTask 信号 ============


@receiver(post_save, sender=ScheduledTask)
def scheduled_task_post_save(sender, instance, created, **kwargs):
    """
    定时任务保存后的信号处理
    """
    if created:
        logger.info("新定时任务创建，开始注册: %s", instance.name)
        register_periodic_task(instance)
    else:
        logger.info("定时任务更新，开始更新注册: %s", instance.name)
        update_periodic_task(instance)


@receiver(post_delete, sender=ScheduledTask)
def scheduled_task_post_delete(sender, instance, **kwargs):
    """
    定时任务删除后的信号处理
    """
    logger.info("定时任务删除，开始注销: %s", instance.name)
    unregister_periodic_task(instance)
