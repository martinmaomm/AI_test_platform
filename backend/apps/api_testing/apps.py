from django.apps import AppConfig


class ApiTestingConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'api_testing'
    
    def ready(self):
        """应用启动时自动发现任务"""
        try:
            import api_testing.tasks  # noqa
        except ImportError:
            pass
