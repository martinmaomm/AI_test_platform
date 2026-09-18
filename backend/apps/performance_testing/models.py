import uuid
from datetime import timedelta

from django.db import models
from django.conf import settings
from django.utils import timezone

from projects.models import Project

from .constants import NODE_OFFLINE_AFTER_SECONDS


def default_allowed_methods():
    return ['GET']


class PerformanceTarget(models.Model):
    project = models.ForeignKey(
        Project, on_delete=models.CASCADE, related_name='performance_targets',
    )
    name = models.CharField(max_length=200)
    base_url = models.CharField(max_length=2048)
    allowed_methods = models.JSONField(default=default_allowed_methods)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'performance_targets'
        ordering = ('-created_at', '-id')


class PerformancePlan(models.Model):
    project = models.ForeignKey(
        Project, on_delete=models.CASCADE, related_name='performance_plans',
    )
    target = models.ForeignKey(
        PerformanceTarget, on_delete=models.RESTRICT, related_name='plans',
    )
    name = models.CharField(max_length=200)
    description = models.TextField(blank=True, default='')
    users = models.PositiveIntegerField(default=1)
    spawn_rate = models.FloatField(default=1)
    duration_seconds = models.PositiveIntegerField(default=30)
    wait_seconds = models.FloatField(default=1)
    variables = models.JSONField(default=dict)
    unique_variables = models.JSONField(default=list)
    steps = models.JSONField(default=list)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'performance_plans'
        ordering = ('-created_at', '-id')
        constraints = [
            models.CheckConstraint(
                check=models.Q(users__gte=1) & models.Q(users__lte=100),
                name='perf_plan_users_1_100',
            ),
            models.CheckConstraint(
                check=models.Q(spawn_rate__gt=0) & models.Q(spawn_rate__lte=100),
                name='perf_plan_spawn_rate_0_100',
            ),
            models.CheckConstraint(
                check=models.Q(duration_seconds__gte=1) & models.Q(duration_seconds__lte=600),
                name='perf_plan_duration_1_600',
            ),
            models.CheckConstraint(
                check=models.Q(wait_seconds__gte=0.1) & models.Q(wait_seconds__lte=60),
                name='perf_plan_wait_01_60',
            ),
        ]


class PerformanceNode(models.Model):
    class NetworkMode(models.TextChoices):
        LAN = 'lan', 'LAN'
        PUBLIC = 'public', 'Public'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey(
        Project, on_delete=models.CASCADE, related_name='performance_nodes',
    )
    name = models.CharField(max_length=200)
    network_mode = models.CharField(max_length=10, choices=NetworkMode.choices)

    enrollment_token_digest = models.CharField(max_length=64, blank=True, default='', editable=False)
    enrollment_expires_at = models.DateTimeField(null=True, blank=True, editable=False)
    enrollment_consumed_at = models.DateTimeField(null=True, blank=True, editable=False)
    agent_token_digest = models.CharField(max_length=64, blank=True, default='', editable=False)
    revoked_at = models.DateTimeField(null=True, blank=True, editable=False)

    last_seen_at = models.DateTimeField(null=True, blank=True, editable=False)
    agent_version = models.CharField(max_length=32, blank=True, default='', editable=False)
    engine_version = models.CharField(max_length=32, blank=True, default='', editable=False)
    protocol_version = models.PositiveIntegerField(null=True, blank=True, editable=False)
    resources = models.JSONField(default=dict, blank=True, editable=False)
    deleted_at = models.DateTimeField(null=True, blank=True, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'performance_nodes'
        ordering = ('-created_at', '-id')

    def status_at(self, current_time=None):
        if self.revoked_at is not None:
            return 'revoked'
        if self.last_seen_at is None:
            return 'pending'
        current_time = current_time or timezone.now()
        cutoff = current_time - timedelta(seconds=NODE_OFFLINE_AFTER_SECONDS)
        return 'online' if self.last_seen_at >= cutoff else 'offline'


class PerformanceRun(models.Model):
    class Mode(models.TextChoices):
        VALIDATION = 'validation', 'Validation'
        LOAD = 'load', 'Load'

    class Status(models.TextChoices):
        QUEUED = 'queued', 'Queued'
        PREPARING = 'preparing', 'Preparing'
        RUNNING = 'running', 'Running'
        STOPPING = 'stopping', 'Stopping'
        COMPLETED = 'completed', 'Completed'
        FAILED = 'failed', 'Failed'
        CANCELLED = 'cancelled', 'Cancelled'
        INCOMPLETE = 'incomplete', 'Incomplete'

    ACTIVE_STATUSES = (
        Status.QUEUED, Status.PREPARING, Status.RUNNING, Status.STOPPING,
    )
    TERMINAL_STATUSES = (
        Status.COMPLETED, Status.FAILED, Status.CANCELLED, Status.INCOMPLETE,
    )

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey(
        Project, on_delete=models.CASCADE, related_name='performance_runs',
    )
    plan = models.ForeignKey(
        PerformancePlan, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='runs',
    )
    node = models.ForeignKey(
        PerformanceNode, on_delete=models.RESTRICT, related_name='runs',
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='created_performance_runs',
    )
    request_id = models.UUIDField()
    mode = models.CharField(max_length=16, choices=Mode.choices, default=Mode.LOAD)
    validation_key = models.CharField(max_length=64, blank=True, default='', db_index=True)
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.QUEUED,
    )
    snapshot = models.JSONField(default=dict)
    snapshot_sha256 = models.CharField(max_length=64)
    node_command = models.JSONField(default=dict, blank=True, editable=False)
    node_report = models.JSONField(default=dict, blank=True, editable=False)
    node_report_seq = models.PositiveBigIntegerField(default=0, editable=False)
    latest_metrics = models.JSONField(default=dict, blank=True)
    metrics_samples = models.JSONField(default=list, blank=True)
    reason_code = models.CharField(max_length=64, blank=True, default='')
    reason = models.TextField(blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    stop_requested_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = 'performance_runs'
        ordering = ('-created_at', '-id')
        constraints = [
            models.UniqueConstraint(
                fields=('project', 'request_id'), name='perf_run_project_request_unique',
            ),
        ]
        indexes = [
            models.Index(fields=('status', 'created_at'), name='perf_run_status_created'),
        ]

    @property
    def is_terminal(self):
        return self.status in self.TERMINAL_STATUSES


class PerformanceControllerState(models.Model):
    id = models.PositiveSmallIntegerField(primary_key=True, default=1, editable=False)
    owner_id = models.CharField(max_length=200, blank=True, default='')
    heartbeat_at = models.DateTimeField(null=True, blank=True)
    lease_until = models.DateTimeField(null=True, blank=True)
    current_run = models.ForeignKey(
        PerformanceRun, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='+',
    )

    class Meta:
        db_table = 'performance_controller_state'
        constraints = [
            models.CheckConstraint(
                check=models.Q(id=1), name='perf_controller_singleton_id',
            ),
        ]

    def save(self, *args, **kwargs):
        if self.pk not in (None, 1):
            raise ValueError('PerformanceControllerState 只能使用主键 1。')
        self.pk = 1
        return super().save(*args, **kwargs)
