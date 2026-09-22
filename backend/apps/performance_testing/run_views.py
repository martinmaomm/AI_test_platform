from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from projects.access import EXECUTE, READ, REPORT, get_project_for_user

from .models import PerformancePlan, PerformanceRun
from .parsers import LimitedJSONParser
from .run_services import (
    RunConflict, RunUnavailable, RunValidationRejected, create_run, node_eligibility,
    request_stop,
)
from .serializers import (
    PerformanceRunCreateSerializer, PerformanceRunDetailSerializer, PerformanceRunListSerializer,
)


def _ok(data, code=status.HTTP_200_OK):
    return Response({'success': True, 'data': data}, status=code)


def _error(code, message, http_status):
    return Response({
        'success': False,
        'error': {'code': code, 'message': message},
    }, status=http_status)


def _project(request, project_id, capability):
    return get_project_for_user(
        project_id, request.user, capability, expected_project_type='perf',
    )


class RunAPIView(APIView):
    permission_classes = (IsAuthenticated,)
    parser_classes = (LimitedJSONParser,)


class PerformanceRunListView(RunAPIView):
    def get(self, request, project_id):
        project = _project(request, project_id, READ)
        runs = PerformanceRun.objects.filter(project=project).select_related(
            'plan',
        ).prefetch_related('participants__node').order_by('-created_at', '-id')[:100]
        return _ok({'items': PerformanceRunListSerializer(runs, many=True).data})


class PerformanceRunCreateView(RunAPIView):
    def post(self, request, project_id, pk):
        project = _project(request, project_id, EXECUTE)
        serializer = PerformanceRunCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        try:
            run, created = create_run(
                project, pk, data['node_ids'], data['request_id'], request.user,
                data['mode'],
            )
        except RunConflict as exc:
            return _error('idempotency_conflict', str(exc), status.HTTP_409_CONFLICT)
        except RunUnavailable as exc:
            return _error('execution_unavailable', str(exc), status.HTTP_409_CONFLICT)
        except RunValidationRejected as exc:
            return _error('run_validation_failed', str(exc), status.HTTP_400_BAD_REQUEST)
        run = PerformanceRun.objects.select_related('plan').prefetch_related(
            'participants__node',
        ).get(pk=run.pk)
        return _ok(
            PerformanceRunListSerializer(run).data,
            status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )


class PerformanceRunDetailView(RunAPIView):
    def get(self, request, project_id, run_id):
        project = _project(request, project_id, REPORT)
        run = get_object_or_404(
            PerformanceRun.objects.select_related('plan').prefetch_related('participants__node'),
            pk=run_id, project=project,
        )
        return _ok(PerformanceRunDetailSerializer(run).data)


class PerformanceRunStopView(RunAPIView):
    def post(self, request, project_id, run_id):
        project = _project(request, project_id, EXECUTE)
        run = get_object_or_404(PerformanceRun, pk=run_id, project=project)
        run = request_stop(run)
        run = PerformanceRun.objects.select_related('plan').prefetch_related(
            'participants__node',
        ).get(pk=run.pk)
        return _ok(PerformanceRunListSerializer(run).data)


class PerformanceNodeEligibilityView(RunAPIView):
    def get(self, request, project_id, pk):
        project = _project(request, project_id, READ)
        plan = get_object_or_404(
            PerformancePlan.objects.select_related('target'), pk=pk, project=project,
        )
        return _ok({'items': node_eligibility(plan)})
