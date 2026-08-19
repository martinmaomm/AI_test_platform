from django.apps import AppConfig


class AiCoreConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'ai_core'
    
    def ready(self):
        """应用启动时自动发现任务"""
        try:
            import ai_core.tasks  # noqa
        except ImportError:
            pass
