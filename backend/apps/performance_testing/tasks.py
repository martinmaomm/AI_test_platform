from celery import shared_task
from celery.exceptions import SoftTimeLimitExceeded

from .analysis_runtime import _finish
from .models import PerformanceAnalysis


@shared_task(
    bind=True,
    name='performance_testing.tasks.run_performance_analysis_async',
    soft_time_limit=185,
    time_limit=195,
)
def run_performance_analysis_async(self, analysis_id):
    from .analysis_runtime import execute_analysis

    try:
        return execute_analysis(analysis_id)
    except SoftTimeLimitExceeded:
        _finish(
            analysis_id, PerformanceAnalysis.Status.RUNNING,
            status=PerformanceAnalysis.Status.FAILED, code='analysis_timeout',
        )
        return {
            'analysis_id': str(analysis_id),
            'status': 'failed',
            'error_code': 'analysis_timeout',
        }
