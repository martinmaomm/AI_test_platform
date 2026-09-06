"""Execution API shared by visual cases and suites."""
import json

from django.db import transaction
from django.shortcuts import get_object_or_404
from rest_framework import permissions
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.views import APIView

from common.api import response
from projects.models import Environment, Project
from .execution_snapshots import capture_case_snapshot, capture_suite_snapshot
from .models import APITestCase, APITestExecution, APITestSuite


def execution_project(user, project_id):
    project = get_object_or_404(Project, pk=project_id)
    if not (user.is_superuser or project.created_by_id == user.pk or project.owner_id == user.pk
            or project.members.filter(user=user, can_execute_tests=True).exists()):
        raise PermissionDenied('没有权限执行此项目的测试')
    return project


def selected_environment(project, data):
    env_id = data.get('environment_id')
    return get_object_or_404(Environment, pk=env_id, project=project, is_active=True) if env_id else None


class ExecuteAPITestCaseView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, project_id, pk):
        project = execution_project(request.user, project_id)
        case = get_object_or_404(APITestCase, project=project, pk=pk)
        environment = selected_environment(project, request.data)
        variables = request.data.get('variables', {})
        if not isinstance(variables, dict):
            raise ValidationError('变量必须是对象')
        # Unsaved editor debug freezes the submitted draft, never overwrites the saved case.
        if request.data.get('script_content'):
            case.script_content = request.data['script_content']
        try:
            with transaction.atomic():
                execution = APITestExecution.objects.create(
                    exec_type='scenario' if case.test_case_type == 'scenario' else 'case',
                    name=case.title, description=case.description, executor=request.user,
                    project=project, environment=environment,
                )
                capture_case_snapshot(execution, case, environment, variables)
                if request.data.get('base_url'):
                    execution.input_snapshot['cases'][0]['options']['base_url'] = request.data['base_url']
                    execution.save(update_fields=['input_snapshot'])
        except (ValueError, TypeError) as exc:
            raise ValidationError(str(exc)) from exc
        if request.data.get('sync') is True:
            from .execution_service import run_execution
            run_execution(execution.pk)
            execution.refresh_from_db()
            result = json.loads(execution.case_execution_detail.httprunner_result or '{}')
            result['execution_id'] = execution.pk
            return response(kind='success', data=result, message='测试执行完成')
        from .tasks import execute_api_test_case_async
        return dispatch_execution(execution, execute_api_test_case_async)


class ExecuteAPITestSuiteView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, project_id, pk):
        project = execution_project(request.user, project_id)
        suite = get_object_or_404(APITestSuite, project=project, pk=pk)
        environment = selected_environment(project, request.data)
        try:
            with transaction.atomic():
                execution = APITestExecution.objects.create(
                    exec_type='suite', name=suite.name, description=suite.description,
                    executor=request.user, project=project, environment=environment,
                )
                capture_suite_snapshot(execution, suite, environment, request.data.get('variables'))
        except (ValueError, TypeError) as exc:
            raise ValidationError(str(exc)) from exc
        from .tasks import execute_api_test_suite_async
        return dispatch_execution(execution, execute_api_test_suite_async)


def dispatch_execution(execution, task):
    try:
        queued = task.delay(execution.pk)
    except Exception:
        # A broker outage must not leave a permanently pending platform record.
        from django.utils import timezone
        execution.status, execution.progress = 'error', 100
        execution.error_message = '执行任务派发失败，请检查 Celery 和 Redis 服务后重试'
        execution.end_time = timezone.now()
        execution.save(update_fields=['status', 'progress', 'error_message', 'end_time'])
        if execution.exec_type == 'suite':
            detail = execution.suite_execution_detail
            detail.case_executions.update(status='error', error_message=execution.error_message)
            detail.failed_cases, detail.end_time = detail.total_cases, execution.end_time
            detail.save(update_fields=['failed_cases', 'end_time'])
        else:
            detail = execution.case_execution_detail
            detail.status, detail.error_message = 'error', execution.error_message
            detail.save(update_fields=['status', 'error_message'])
        return response(kind='error', message=execution.error_message)
    APITestExecution.objects.filter(pk=execution.pk).update(task_id=queued.id)
    return response(kind='success', data={
        'execution_id': execution.pk, 'task_id': queued.id,
        'exec_type': execution.exec_type, 'execution_name': execution.name,
        'test_suite_name': execution.name, 'total_cases': len(execution.input_snapshot['cases']),
        'environment_name': execution.environment.name if execution.environment else '用例配置',
    }, message='测试执行已开始')
