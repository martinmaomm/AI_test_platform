from django.db.models.deletion import RestrictedError
from django.db.models.signals import pre_delete
from django.dispatch import receiver

from projects.models import Project

from .models import PerformanceRun


@receiver(pre_delete, sender=Project, dispatch_uid='performance_runs_restrict_active_project_delete')
def restrict_project_delete_with_active_runs(sender, instance, **kwargs):
    active_runs = instance.performance_runs.filter(
        status__in=PerformanceRun.ACTIVE_STATUSES,
    )
    if active_runs.exists():
        raise RestrictedError('项目存在未结束的性能运行，不能删除。', active_runs)
