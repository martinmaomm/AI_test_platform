"""
Scheduled Tasks & Web Testing 统一信号处理器
- ScheduledTask: 定时任务注册/注销
- WebElement: POM 单点维护，联动更新 WebPage.pom_code
"""
import logging
from django.db.models.signals import post_save, post_delete
from django.dispatch import receiver

from .models import ScheduledTask
from .tasks import register_periodic_task, unregister_periodic_task, update_periodic_task

logger = logging.getLogger(__name__)


# ============ WebElement 信号（POM 单点维护） ============

@receiver(post_save, sender='web_testing.WebElement')
def webelement_post_save(sender, instance, created, **kwargs):
    """
    WebElement 保存后：调用所属页面的 refresh_pom_code()，
    使定位器/名称变更时 pom_code 自动同步。
    """
    try:
        page = instance.page
        if page:
            page.refresh_pom_code()
            logger.debug(f"WebElement(id={instance.id}) 变更，已刷新 WebPage(id={page.id}) 的 pom_code")
    except Exception as e:
        logger.error(f"WebElement post_save 刷新 pom_code 失败: {e}", exc_info=True)


@receiver(post_delete, sender='web_testing.WebElement')
def webelement_post_delete(sender, instance, **kwargs):
    """
    WebElement 删除后：调用所属页面的 refresh_pom_code()，
    使 pom_code 与当前元素列表保持一致。
    """
    try:
        page = instance.page
        if page:
            page.refresh_pom_code()
            logger.debug(f"WebElement(id={instance.id}) 已删除，已刷新 WebPage(id={page.id}) 的 pom_code")
    except Exception as e:
        logger.error(f"WebElement post_delete 刷新 pom_code 失败: {e}", exc_info=True)


# ============ ScheduledTask 信号 ============


@receiver(post_save, sender=ScheduledTask)
def scheduled_task_post_save(sender, instance, created, **kwargs):
    """
    定时任务保存后的信号处理
    """
    try:
        if created:
            # 新创建的任务，注册到Celery Beat
            logger.info(f"新定时任务创建，开始注册: {instance.name}")
            register_periodic_task(instance)
        else:
            # 更新现有任务
            logger.info(f"定时任务更新，开始更新注册: {instance.name}")
            update_periodic_task(instance)

    except Exception as e:
        logger.error(f"处理定时任务保存信号时发生错误: {str(e)}", exc_info=True)


@receiver(post_delete, sender=ScheduledTask)
def scheduled_task_post_delete(sender, instance, **kwargs):
    """
    定时任务删除后的信号处理
    """
    try:
        logger.info(f"定时任务删除，开始注销: {instance.name}")
        unregister_periodic_task(instance)

    except Exception as e:
        logger.error(f"处理定时任务删除信号时发生错误: {str(e)}", exc_info=True)
