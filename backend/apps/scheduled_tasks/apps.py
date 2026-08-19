"""
Scheduled Tasks App Configuration
"""
from django.apps import AppConfig


class ScheduledTasksConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'scheduled_tasks'
    verbose_name = '定时任务中心'
    
    def ready(self):
        """应用准备就绪时注册信号"""
        import scheduled_tasks.signals
