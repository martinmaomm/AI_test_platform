from celery import shared_task


@shared_task(name='security_testing.tasks.run_security_task')
def run_security_task(task_id):
    from .runtime import execute_task
    return execute_task(task_id)
