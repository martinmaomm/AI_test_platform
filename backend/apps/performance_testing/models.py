import uuid
from datetime import timedelta

from django.db import models
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
    labels = models.JSONField(default=dict, blank=True)

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
