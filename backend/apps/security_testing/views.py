from django.db import transaction
from django.db.models.deletion import ProtectedError
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from rest_framework import serializers
from rest_framework.permissions import IsAuthenticated
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response
from rest_framework.views import APIView

from performance_testing.parsers import LimitedJSONParser
from projects.access import READ, EDIT, EXECUTE, REPORT, get_project_for_user

from .credentials import encrypt_identities
from .models import SecurityPlan, SecurityTask
from .runtime import TaskConflict, enqueue, recover_expired, stop_task
from .serializers import (PlanSerializer, PlanWriteSerializer, PlanningCreateSerializer,
                          ExecutionCreateSerializer, TaskListSerializer, TaskDetailSerializer)


def ok(data, status=200):
    return Response({'success': True, 'data': data}, status=status)


def error(code, message, status=409):
    return Response({'success': False, 'error': {'code': code, 'message': message}}, status=status)


def project_for(request, project_id, capability):
    return get_project_for_user(project_id, request.user, capability, expected_project_type='security')


class SecurityAPIView(APIView):
    permission_classes = (IsAuthenticated,)
    parser_classes = (LimitedJSONParser,)


class PlanListView(SecurityAPIView):
    def get(self, request, project_id):
        project = project_for(request, project_id, READ)
        return ok({'items': PlanSerializer(SecurityPlan.objects.filter(project=project)[:200], many=True).data})

    def post(self, request, project_id):
        project = project_for(request, project_id, EDIT)
        serializer = PlanWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        values = dict(serializer.validated_data)
        identities = values.pop('identities', {})
        plan = SecurityPlan.objects.create(project=project, created_by=request.user,
                                           identities_ciphertext=encrypt_identities(identities), **values)
        return ok(PlanSerializer(plan).data, 201)


class PlanDetailView(SecurityAPIView):
    def get(self, request, project_id, plan_id):
        project = project_for(request, project_id, READ)
        return ok(PlanSerializer(get_object_or_404(SecurityPlan, pk=plan_id, project=project)).data)

    def patch(self, request, project_id, plan_id):
        project = project_for(request, project_id, EDIT)
        serializer = PlanWriteSerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        with transaction.atomic():
            plan = get_object_or_404(SecurityPlan.objects.select_for_update(), pk=plan_id, project=project)
            values = dict(serializer.validated_data)
            if 'identities' in values:
                plan.identities_ciphertext = encrypt_identities(values.pop('identities'))
            for key, value in values.items():
                setattr(plan, key, value)
            plan.revision += 1
            plan.cases = []
            plan.save()
        return ok(PlanSerializer(plan).data)

    def delete(self, request, project_id, plan_id):
        project = project_for(request, project_id, EDIT)
        plan = get_object_or_404(SecurityPlan, pk=plan_id, project=project)
        try:
            plan.delete()
        except ProtectedError:
            return error('plan_in_use', '已有任务引用此计划，不能删除。')
        return ok(None)


class PlanGenerateView(SecurityAPIView):
    kind = 'planning'
    serializer_class = PlanningCreateSerializer

    def post(self, request, project_id, plan_id):
        project = project_for(request, project_id, EDIT if self.kind == 'planning' else REPORT)
        project_for(request, project_id, EXECUTE)
        plan = get_object_or_404(SecurityPlan, pk=plan_id, project=project)
        serializer = self.serializer_class(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            task, created = enqueue(plan.pk, request.user, self.kind, **serializer.validated_data)
        except TaskConflict as exc:
            return error('task_conflict', str(exc))
        except SecurityPlan.DoesNotExist:
            return error('plan_unavailable', '计划已被删除，请刷新后重试。', 404)
        if task.error_code == 'queue_unavailable':
            return error(task.error_code, task.error_message, 503)
        return ok(TaskDetailSerializer(task).data, 202 if created else 200)


class PlanRunView(PlanGenerateView):
    kind = 'execution'
    serializer_class = ExecutionCreateSerializer


class TaskListView(SecurityAPIView):
    def get(self, request, project_id):
        project = project_for(request, project_id, READ)
        tasks = SecurityTask.objects.filter(project=project)
        try:
            project_for(request, project_id, REPORT)
        except PermissionDenied:
            project_for(request, project_id, EDIT)
            project_for(request, project_id, EXECUTE)
            tasks = tasks.filter(kind='planning', created_by=request.user)
        recover_expired(project.pk)
        return ok({'items': TaskListSerializer(tasks[:100], many=True).data})


class TaskDetailView(SecurityAPIView):
    def get(self, request, project_id, task_id):
        project = project_for(request, project_id, READ)
        recover_expired(project.pk)
        task = get_object_or_404(SecurityTask, pk=task_id, project=project)
        try:
            project_for(request, project_id, REPORT)
        except PermissionDenied:
            if task.kind != 'planning' or task.created_by_id != request.user.pk:
                raise
            project_for(request, project_id, EDIT)
            project_for(request, project_id, EXECUTE)
        return ok(TaskDetailSerializer(task).data)


class TaskStopView(SecurityAPIView):
    def post(self, request, project_id, task_id):
        project = project_for(request, project_id, EXECUTE)
        task = get_object_or_404(SecurityTask, pk=task_id, project=project)
        stop_task(task.pk)
        task.refresh_from_db()
        return ok(TaskListSerializer(task).data)


class TaskReportView(SecurityAPIView):
    def get(self, request, project_id, task_id):
        project = project_for(request, project_id, REPORT)
        recover_expired(project.pk)
        task = get_object_or_404(SecurityTask, pk=task_id, project=project)
        if task.kind != 'execution' or task.status not in SecurityTask.TERMINAL_STATUSES:
            return error('report_unavailable', '执行结束后可导出报告。')
        from .reporting import render_report
        response = HttpResponse(render_report(task), content_type='text/html; charset=utf-8')
        response['Content-Disposition'] = f'attachment; filename="security-{task.pk}.html"'
        response['Cache-Control'] = 'no-store'
        response['X-Content-Type-Options'] = 'nosniff'
        return response


class SourcesView(SecurityAPIView):
    def get(self, request, project_id):
        project_for(request, project_id, READ)
        from .sources import source_projects, source_endpoints
        source_id = request.query_params.get('source_project_id')
        if source_id is None:
            return ok({'projects': source_projects(request.user)})
        try:
            source_id = int(source_id)
            if source_id <= 0:
                raise ValueError
        except (TypeError, ValueError):
            raise serializers.ValidationError({'source_project_id': '请选择来源项目。'}) from None
        return ok(source_endpoints(source_id, request.user))
