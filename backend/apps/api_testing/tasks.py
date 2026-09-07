"""API requests execution task entry points."""
from celery import shared_task
from common.task import update_task_progress
from .workspace_tasks import debug_api_workspace, generate_and_verify_api_workspace  # noqa: F401

@shared_task(bind=True, name='api_testing.tasks.execute_api_test_case_async')
def execute_api_test_case_async(self, execution_id, test_case_id=None, environment_id=None):
    return _execute_api_test_case(self, execution_id, test_case_id, environment_id)


def _execute_api_test_case(task_instance, execution_id, test_case_id=None, environment_id=None):
    from .execution_service import run_execution
    return run_execution(execution_id, progress=lambda value, message: update_task_progress(task_instance, value, message))


@shared_task(bind=True, name='api_testing.tasks.execute_api_test_suite_async')
def execute_api_test_suite_async(self, execution_id, test_suite_id=None, environment_id=None, task_execution_log_id=None):
    return _execute_api_test_suite(self, execution_id, test_suite_id, environment_id, task_execution_log_id)


def _execute_api_test_suite(task_instance, execution_id, test_suite_id=None, environment_id=None, task_execution_log_id=None):
    from .execution_service import run_execution
    return run_execution(
        execution_id, suite=True, scheduled_log_id=task_execution_log_id,
        progress=lambda value, message: update_task_progress(task_instance, value, message),
    )
