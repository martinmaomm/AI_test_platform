from celery import shared_task


@shared_task(name='project_knowledge.execute_task')
def execute_knowledge_task(task_id):
    from .runtime import execute_task
    return execute_task(task_id)
