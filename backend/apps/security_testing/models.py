import uuid

from django.conf import settings
from django.db import models


class SecurityPlan(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey('projects.Project', on_delete=models.CASCADE, related_name='security_plans')
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    name = models.CharField(max_length=200)
    config = models.JSONField(default=dict)
    identities_ciphertext = models.TextField(blank=True, default='')
    cases = models.JSONField(default=list, blank=True)
    revision = models.PositiveIntegerField(default=1)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-updated_at', '-id']


class SecurityTask(models.Model):
    class Kind(models.TextChoices):
        PLANNING = 'planning', '规划'
        EXECUTION = 'execution', '执行'

    class Status(models.TextChoices):
        QUEUED = 'queued', '排队中'
        RUNNING = 'running', '运行中'
        COMPLETED = 'completed', '已完成'
        FAILED = 'failed', '失败'
        CANCELLED = 'cancelled', '已停止'

    TERMINAL_STATUSES = (Status.COMPLETED, Status.FAILED, Status.CANCELLED)
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey('projects.Project', on_delete=models.CASCADE, related_name='security_tasks')
    plan = models.ForeignKey(SecurityPlan, on_delete=models.PROTECT, related_name='tasks')
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    kind = models.CharField(max_length=16, choices=Kind.choices)
    request_id = models.UUIDField()
    request_hash = models.CharField(max_length=64)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.QUEUED, db_index=True)
    snapshot = models.JSONField(default=dict)
    identities_ciphertext = models.TextField(blank=True, default='')
    model_config_id = models.PositiveBigIntegerField(null=True, blank=True)
    result = models.JSONField(default=dict, blank=True)
    progress = models.JSONField(default=dict, blank=True)
    cancel_requested = models.BooleanField(default=False)
    error_code = models.CharField(max_length=64, blank=True)
    error_message = models.CharField(max_length=500, blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    heartbeat_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    deadline_at = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at', '-id']
        constraints = [models.UniqueConstraint(fields=['project', 'request_id'], name='security_task_project_request')]
