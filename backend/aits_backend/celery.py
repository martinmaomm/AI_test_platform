"""
Celery configuration for aits_backend project.
"""

import os
import sys
import logging.config
from pathlib import Path

from celery import Celery
from celery.signals import setup_logging

# 添加 apps 目录到 Python 路径
# 项目根目录
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# 添加 apps 目录到 Python 路径
sys.path.insert(0, os.path.join(BASE_DIR, "apps"))

# Set the default Django settings module for the 'celery' program.
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'aits_backend.settings')

app = Celery('aits_backend')

# Using a string here means the worker doesn't have to serialize
# the configuration object to child processes.
app.config_from_object('django.conf:settings', namespace='CELERY')

# Load task modules from all registered Django apps.
app.autodiscover_tasks()


@setup_logging.connect
def configure_celery_logging(loglevel=None, logfile=None, **kwargs):
    """为Celery Worker配置控制台和可轮转的独立文件日志。"""
    from copy import deepcopy
    from django.conf import settings

    log_path = logfile or settings.CELERY_WORKER_LOG_FILE
    if log_path in {'-', 'stderr', 'stdout'}:
        log_path = settings.CELERY_WORKER_LOG_FILE

    log_path = Path(log_path)
    log_path.parent.mkdir(parents=True, exist_ok=True)

    logging_config = deepcopy(settings.LOGGING)
    logging_config['handlers']['celery_file'] = {
        'level': loglevel or 'INFO',
        'class': 'logging.handlers.RotatingFileHandler',
        'filename': str(log_path),
        'maxBytes': 20 * 1024 * 1024,
        'backupCount': 5,
        'encoding': 'utf-8',
        'formatter': 'verbose',
    }
    logging_config['root'] = {
        'handlers': ['console', 'celery_file'],
        'level': loglevel or 'INFO',
    }

    logging.config.dictConfig(logging_config)
    logging.getLogger(__name__).info(
        "Celery文件日志已启用: %s (最大20MB，保留5个备份)",
        log_path,
    )


@app.task(bind=True, ignore_result=True)
def debug_task(self):
    print(f'Request: {self.request!r}')
