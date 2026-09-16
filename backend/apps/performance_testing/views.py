from django.db import transaction
from django.db.models.deletion import RestrictedError
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import status
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from projects.access import DELETE, EDIT, READ, get_project_for_user
from users.permissions import is_platform_admin

from .constants import platform_config
from .models import PerformanceNode, PerformancePlan, PerformanceTarget
from .parsers import LimitedJSONParser
from .serializers import (
    PerformanceNodeSerializer, PerformancePlanSerializer, PerformanceTargetSerializer,
)
from .services import (
    create_node_with_enrollment, enrollment_response, issue_enrollment, revoke_node,
)
from .run_services import controller_execution_status


def _ok(data, code=status.HTTP_200_OK):
    return Response({'success': True, 'data': data}, status=code)


def _conflict(message):
    return Response({
        'success': False,
        'error': {'code': 'conflict', 'message': message},
    }, status=status.HTTP_409_CONFLICT)


def _project(request, project_id, capability=READ):
    return get_project_for_user(
        project_id, request.user, capability, expected_project_type='perf',
    )


def _admin_project(request, project_id):
    project = _project(request, project_id, READ)
    if not is_platform_admin(request.user):
        raise PermissionDenied('仅平台管理员可以管理性能节点或目标。')
    return project


def _lock_target(project, raw_target_id):
    if isinstance(raw_target_id, bool) or not isinstance(raw_target_id, int):
        return None
    return PerformanceTarget.objects.select_for_update().filter(
        project=project, pk=raw_target_id,
    ).first()


class ManagementAPIView(APIView):
    permission_classes = (IsAuthenticated,)
    parser_classes = (LimitedJSONParser,)


class PerformanceConfigView(ManagementAPIView):
    def get(self, request, project_id):
        _project(request, project_id, READ)
        execution = controller_execution_status()
        return _ok(platform_config(
            controller_online=execution['controller_online'],
            execution_available=execution['available'],
            unavailable_reason=execution['reason'],
        ))


class TargetListCreateView(ManagementAPIView):
    def get(self, request, project_id):
        project = _project(request, project_id, READ)
        rows = PerformanceTarget.objects.filter(project=project)
        return _ok({'items': PerformanceTargetSerializer(rows, many=True).data})

    def post(self, request, project_id):
        project = _admin_project(request, project_id)
        serializer = PerformanceTargetSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        target = serializer.save(project=project)
        return _ok(PerformanceTargetSerializer(target).data, status.HTTP_201_CREATED)


class TargetDetailView(ManagementAPIView):
    def get(self, request, project_id, pk):
        project = _project(request, project_id, READ)
        target = get_object_or_404(PerformanceTarget, pk=pk, project=project)
        return _ok(PerformanceTargetSerializer(target).data)

    def patch(self, request, project_id, pk):
        project = _admin_project(request, project_id)
        with transaction.atomic():
            target = get_object_or_404(
                PerformanceTarget.objects.select_for_update(), pk=pk, project=project,
            )
            serializer = PerformanceTargetSerializer(target, data=request.data, partial=True)
            serializer.is_valid(raise_exception=True)
            target = serializer.save()
        return _ok(PerformanceTargetSerializer(target).data)

    def delete(self, request, project_id, pk):
        project = _admin_project(request, project_id)
        try:
            with transaction.atomic():
                target = get_object_or_404(
                    PerformanceTarget.objects.select_for_update(), pk=pk, project=project,
                )
                target_id = target.pk
                target.delete()
        except RestrictedError:
            return _conflict('目标已被计划引用，不能删除。')
        return _ok({'id': target_id})


class PlanListCreateView(ManagementAPIView):
    def get(self, request, project_id):
        project = _project(request, project_id, READ)
        rows = PerformancePlan.objects.filter(project=project).select_related('target')
        return _ok({'items': PerformancePlanSerializer(rows, many=True, context={'project': project}).data})

    def post(self, request, project_id):
        project = _project(request, project_id, EDIT)
        with transaction.atomic():
            _lock_target(project, request.data.get('target_id'))
            serializer = PerformancePlanSerializer(data=request.data, context={'project': project})
            serializer.is_valid(raise_exception=True)
            plan = serializer.save(project=project)
        return _ok(
            PerformancePlanSerializer(plan, context={'project': project}).data,
            status.HTTP_201_CREATED,
        )


class PlanDetailView(ManagementAPIView):
    def _get(self, project, pk):
        return get_object_or_404(
            PerformancePlan.objects.select_related('target'), pk=pk, project=project,
        )

    def get(self, request, project_id, pk):
        project = _project(request, project_id, READ)
        return _ok(PerformancePlanSerializer(self._get(project, pk), context={'project': project}).data)

    def patch(self, request, project_id, pk):
        project = _project(request, project_id, EDIT)
        with transaction.atomic():
            plan = get_object_or_404(
                PerformancePlan.objects.select_for_update().select_related('target'),
                pk=pk, project=project,
            )
            _lock_target(project, request.data.get('target_id', plan.target_id))
            serializer = PerformancePlanSerializer(
                plan, data=request.data, partial=True, context={'project': project},
            )
            serializer.is_valid(raise_exception=True)
            plan = serializer.save()
        return _ok(PerformancePlanSerializer(plan, context={'project': project}).data)

    def delete(self, request, project_id, pk):
        project = _project(request, project_id, DELETE)
        plan = self._get(project, pk)
        plan_id = plan.pk
        plan.delete()
        return _ok({'id': plan_id})


class NodeListCreateView(ManagementAPIView):
    def get(self, request, project_id):
        project = _project(request, project_id, READ)
        rows = PerformanceNode.objects.filter(project=project)
        context = {'current_time': timezone.now()}
        return _ok({'items': PerformanceNodeSerializer(rows, many=True, context=context).data})

    def post(self, request, project_id):
        project = _admin_project(request, project_id)
        serializer = PerformanceNodeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        node, token = create_node_with_enrollment(project, serializer.validated_data)
        payload = enrollment_response(node, token, PerformanceNodeSerializer)
        return _ok(payload, status.HTTP_201_CREATED)


class NodeDetailView(ManagementAPIView):
    def get(self, request, project_id, node_id):
        project = _project(request, project_id, READ)
        node = get_object_or_404(PerformanceNode, pk=node_id, project=project)
        return _ok(PerformanceNodeSerializer(node, context={'current_time': timezone.now()}).data)

    def patch(self, request, project_id, node_id):
        project = _admin_project(request, project_id)
        node = get_object_or_404(PerformanceNode, pk=node_id, project=project)
        serializer = PerformanceNodeSerializer(node, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        node = serializer.save()
        return _ok(PerformanceNodeSerializer(node, context={'current_time': timezone.now()}).data)


class NodeEnrollmentView(ManagementAPIView):
    def post(self, request, project_id, node_id):
        project = _admin_project(request, project_id)
        node = get_object_or_404(PerformanceNode, pk=node_id, project=project)
        node, token = issue_enrollment(node)
        return _ok(enrollment_response(node, token, PerformanceNodeSerializer))


class NodeRevokeView(ManagementAPIView):
    def post(self, request, project_id, node_id):
        project = _admin_project(request, project_id)
        node = get_object_or_404(PerformanceNode, pk=node_id, project=project)
        node = revoke_node(node)
        return _ok(PerformanceNodeSerializer(node, context={'current_time': timezone.now()}).data)
