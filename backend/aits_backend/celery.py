"""
Celery configuration for aits_backend project.
"""

import os
import sys

from celery import Celery

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


@app.task(bind=True, ignore_result=True)
def debug_task(self):
    print(f'Request: {self.request!r}')
