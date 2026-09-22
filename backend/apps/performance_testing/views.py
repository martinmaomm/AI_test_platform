from django.db import transaction
from django.db.models import Count, Q
from django.db.models.deletion import RestrictedError
from django.http import Http404
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import status
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from projects.access import DELETE, EDIT, READ, get_project_for_user
from projects.models import Project
from users.permissions import is_platform_admin

from .constants import platform_config
from .installation import ReleaseConfigurationError, installation_metadata
from .models import PerformanceNode, PerformancePlan, PerformanceRun, PerformanceTarget
from .parsers import LimitedJSONParser
from .serializers import (
    NodeReinstallSerializer, NodeRevokeSerializer, PerformanceNodeSerializer, PerformancePlanSerializer,
    PerformanceTargetSerializer,
)
from .services import (
    EnrollmentRejected, NodeHasActiveRuns, NodeReinstallRejected,
    create_node_with_enrollment, enrollment_response, issue_enrollment,
    reinstall_node, revoke_node,
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


def _admin_project(request, project_id, capability=READ):
    project = _project(request, project_id, capability)
    if not is_platform_admin(request.user):
        raise PermissionDenied('仅平台管理员可以管理性能节点或目标。')
    return project


def _visible_nodes(project):
    return PerformanceNode.objects.filter(
        project=project, deleted_at__isnull=True,
    ).annotate(
        active_run_count=Count(
            'run_participations__run',
            filter=Q(run_participations__run__status__in=PerformanceRun.ACTIVE_STATUSES),
            distinct=True,
        ),
    )


def _serialized_node(project, node_id):
    node = get_object_or_404(_visible_nodes(project), pk=node_id)
    return PerformanceNodeSerializer(
        node, context={'current_time': timezone.now()},
    ).data


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
        payload = platform_config(
            controller_online=execution['controller_online'],
            execution_available=execution['available'],
            unavailable_reason=execution['reason'],
        )
        payload['installation'] = installation_metadata()
        return _ok(payload)


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
        rows = _visible_nodes(project)
        context = {'current_time': timezone.now()}
        return _ok({'items': PerformanceNodeSerializer(rows, many=True, context=context).data})

    def post(self, request, project_id):
        project = _admin_project(request, project_id)
        serializer = PerformanceNodeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        node, token = create_node_with_enrollment(project, serializer.validated_data)
        node = get_object_or_404(_visible_nodes(project), pk=node.pk)
        payload = enrollment_response(node, token, PerformanceNodeSerializer)
        payload['installation'] = installation_metadata(node=node, enrollment_token=token)
        return _ok(payload, status.HTTP_201_CREATED)


class NodeDetailView(ManagementAPIView):
    def get(self, request, project_id, node_id):
        project = _project(request, project_id, READ)
        return _ok(_serialized_node(project, node_id))

    def patch(self, request, project_id, node_id):
        project = _admin_project(request, project_id)
        with transaction.atomic():
            node = get_object_or_404(
                PerformanceNode.objects.select_for_update(),
                pk=node_id, project=project, deleted_at__isnull=True,
            )
            if node.revoked_at is not None:
                return _conflict('已吊销节点不能编辑。')
            serializer = PerformanceNodeSerializer(node, data=request.data, partial=True)
            serializer.is_valid(raise_exception=True)
            node = serializer.save()
        return _ok(_serialized_node(project, node.pk))

    def delete(self, request, project_id, node_id):
        project = _admin_project(request, project_id, DELETE)
        with transaction.atomic():
            # Match create_run's project -> node lock order. New runs cannot pass
            # their node check between the active-run check and soft deletion.
            locked_project = Project.objects.select_for_update().get(pk=project.pk)
            node = get_object_or_404(
                PerformanceNode.objects.select_for_update(),
                pk=node_id, project=locked_project, deleted_at__isnull=True,
            )
            if node.revoked_at is None:
                return _conflict('节点必须先吊销才能删除。')
            active_run_count = PerformanceRun.objects.filter(
                participants__node=node, status__in=PerformanceRun.ACTIVE_STATUSES,
            ).count()
            if active_run_count:
                return _conflict(f'节点仍有 {active_run_count} 个未结束运行，不能删除。')
            node.deleted_at = timezone.now()
            node.save(update_fields=('deleted_at', 'updated_at'))
        return _ok({'id': node.pk})


class NodeEnrollmentView(ManagementAPIView):
    def post(self, request, project_id, node_id):
        project = _admin_project(request, project_id)
        node = get_object_or_404(_visible_nodes(project), pk=node_id)
        try:
            node, token = issue_enrollment(node)
        except EnrollmentRejected as exc:
            return _conflict(str(exc))
        node = get_object_or_404(_visible_nodes(project), pk=node.pk)
        payload = enrollment_response(node, token, PerformanceNodeSerializer)
        payload['installation'] = installation_metadata(node=node, enrollment_token=token)
        return _ok(payload)


class NodeInstallationView(ManagementAPIView):
    def get(self, request, project_id, node_id):
        project = _admin_project(request, project_id)
        node = get_object_or_404(_visible_nodes(project), pk=node_id)
        if node.revoked_at is not None:
            return _conflict('已吊销节点不能安装。')
        return _ok({
            'node': PerformanceNodeSerializer(
                node, context={'current_time': timezone.now()},
            ).data,
            'installation': installation_metadata(node=node),
        })


class NodeReinstallView(ManagementAPIView):
    def post(self, request, project_id, node_id):
        project = _admin_project(request, project_id)
        serializer = NodeReinstallSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            with transaction.atomic():
                node, token, installation = reinstall_node(project, node_id)
                node = get_object_or_404(_visible_nodes(project), pk=node.pk)
                payload = enrollment_response(node, token, PerformanceNodeSerializer)
                payload['installation'] = installation
        except PerformanceNode.DoesNotExist as exc:
            raise Http404('性能节点不存在。') from exc
        except NodeHasActiveRuns as exc:
            return Response({
                'success': False,
                'error': {
                    'code': 'node_has_active_runs',
                    'message': f'节点存在 {exc.count} 个未结束运行，不能重新安装。',
                    'count': exc.count,
                },
            }, status=status.HTTP_409_CONFLICT)
        except (NodeReinstallRejected, ReleaseConfigurationError) as exc:
            return _conflict(str(exc))
        return _ok(payload)


class NodeRevokeView(ManagementAPIView):
    def post(self, request, project_id, node_id):
        project = _admin_project(request, project_id)
        serializer = NodeRevokeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        node = get_object_or_404(_visible_nodes(project), pk=node_id)
        try:
            node = revoke_node(node, confirm_stop=serializer.validated_data['confirm_stop'])
        except PerformanceNode.DoesNotExist as exc:
            raise Http404('性能节点不存在。') from exc
        except NodeHasActiveRuns as exc:
            return Response({
                'success': False,
                'error': {
                    'code': 'node_has_active_runs',
                    'message': f'节点存在 {exc.count} 个活跃运行，请确认停止后再吊销。',
                    'count': exc.count,
                },
            }, status=status.HTTP_409_CONFLICT)
        return _ok(_serialized_node(project, node.pk))
