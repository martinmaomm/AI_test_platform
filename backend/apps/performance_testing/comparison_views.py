from django.db.models import Case, IntegerField, Value, When
from django.shortcuts import get_object_or_404
from rest_framework import serializers
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView

from projects.access import REPORT, get_project_for_user

from .analysis_views import _error, _ok
from .comparison_data import build_comparison_data, run_metadata
from .models import PerformanceRun


TERMINAL = ('completed', 'failed', 'cancelled', 'incomplete')


def _current(request, project_id, run_id):
    project = get_project_for_user(project_id, request.user, REPORT, expected_project_type='perf')
    return get_object_or_404(
        PerformanceRun.objects.select_related('plan').prefetch_related('participants'),
        project=project, pk=run_id,
    )


def _eligible(run):
    return run.mode == PerformanceRun.Mode.LOAD and run.status in TERMINAL


class PerformanceComparisonCandidatesView(APIView):
    permission_classes = (IsAuthenticated,)

    def get(self, request, project_id, run_id):
        run = _current(request, project_id, run_id)
        if not _eligible(run):
            return _error('comparison_unavailable', '历史对比仅支持已结束的正式压测。', 409)
        preferred = {'plan_id': run.plan_id} if run.plan_id is not None else {'pk': run.pk}
        candidates = PerformanceRun.objects.filter(
            project_id=run.project_id, mode=PerformanceRun.Mode.LOAD, status__in=TERMINAL,
        ).exclude(pk=run.pk).select_related('plan').annotate(
            same_plan_order=Case(When(**preferred, then=Value(0)), default=Value(1), output_field=IntegerField()),
            history_order=Case(When(created_at__lte=run.created_at, then=Value(0)), default=Value(1), output_field=IntegerField()),
        ).order_by('same_plan_order', 'history_order', '-created_at', '-id')[:100]
        return _ok({'items': [dict(run_metadata(item), same_plan=run.plan_id is not None and item.plan_id == run.plan_id)
                              for item in candidates]})


class PerformanceComparisonView(APIView):
    permission_classes = (IsAuthenticated,)

    def get(self, request, project_id, run_id):
        run = _current(request, project_id, run_id)
        if not _eligible(run):
            return _error('comparison_unavailable', '历史对比仅支持已结束的正式压测。', 409)
        if set(request.query_params) != {'baseline_run_id'} or len(request.query_params.getlist('baseline_run_id')) != 1:
            raise serializers.ValidationError({'baseline_run_id': '请提供一个基准运行 ID。'})
        baseline_id = serializers.UUIDField().run_validation(request.query_params.get('baseline_run_id'))
        if baseline_id == run.pk:
            return _error('comparison_unavailable', '请选择另一次运行作为基准。', 409)
        baseline = get_object_or_404(
            PerformanceRun.objects.select_related('plan').prefetch_related('participants'),
            pk=baseline_id, project_id=run.project_id,
        )
        if not _eligible(baseline):
            return _error('comparison_unavailable', '基准必须是已结束的正式压测。', 409)
        return _ok(build_comparison_data(run, baseline))
