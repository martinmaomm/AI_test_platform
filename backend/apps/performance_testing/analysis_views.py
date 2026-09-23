from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from projects.access import EXECUTE, REPORT, get_project_for_user

from .analysis_runtime import (
    AnalysisConflict, AnalysisUnavailable, enqueue_analysis, recover_expired_analyses,
)
from .analysis_serializers import (
    PerformanceAnalysisCreateSerializer, PerformanceAnalysisSerializer,
)
from .models import PerformanceAnalysis, PerformanceRun
from .parsers import LimitedJSONParser


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


def _run(project, run_id):
    return get_object_or_404(PerformanceRun, pk=run_id, project=project)


class PerformanceAnalysisAPIView(APIView):
    permission_classes = (IsAuthenticated,)
    parser_classes = (LimitedJSONParser,)


class PerformanceAnalysisListCreateView(PerformanceAnalysisAPIView):
    def get(self, request, project_id, run_id):
        project = _project(request, project_id, REPORT)
        run = _run(project, run_id)
        recover_expired_analyses(run.pk)
        items = PerformanceAnalysis.objects.filter(run=run).order_by(
            '-created_at', '-id',
        )[:20]
        return _ok({'items': PerformanceAnalysisSerializer(items, many=True).data})

    def post(self, request, project_id, run_id):
        project = _project(request, project_id, REPORT)
        _project(request, project_id, EXECUTE)
        run = _run(project, run_id)
        serializer = PerformanceAnalysisCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        values = serializer.validated_data
        try:
            analysis, created = enqueue_analysis(
                run.pk, request.user, values['request_id'],
                values['model_config_id'], values['targets'],
            )
        except AnalysisConflict as exc:
            return _error('analysis_conflict', str(exc), status.HTTP_409_CONFLICT)
        except AnalysisUnavailable as exc:
            return _error('analysis_unavailable', str(exc), status.HTTP_409_CONFLICT)
        except PerformanceRun.DoesNotExist:
            return _error('not_found', '压测运行不存在。', status.HTTP_404_NOT_FOUND)

        if created and analysis.error_code == 'queue_unavailable':
            return _error(
                'queue_unavailable', analysis.error_message,
                status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        return _ok(
            PerformanceAnalysisSerializer(analysis).data,
            status.HTTP_202_ACCEPTED if created else status.HTTP_200_OK,
        )


class PerformanceAnalysisDetailView(PerformanceAnalysisAPIView):
    def get(self, request, project_id, run_id, analysis_id):
        project = _project(request, project_id, REPORT)
        run = _run(project, run_id)
        recover_expired_analyses(run.pk)
        analysis = get_object_or_404(
            PerformanceAnalysis, pk=analysis_id, run=run,
        )
        return _ok(PerformanceAnalysisSerializer(analysis).data)
