from django.apps import AppConfig


class WebTestingConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'web_testing'
    verbose_name = 'Web Testing'
    
    def ready(self):
        """应用准备就绪时的初始化 - 统一从 scheduled_tasks 加载信号"""
        import scheduled_tasks.signals  # noqa: F401
