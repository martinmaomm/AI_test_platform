from django.http import HttpResponse
from django.shortcuts import get_object_or_404

from projects.access import REPORT

from .models import PerformanceRun
from .reporting import render_report
from .run_views import RunAPIView, _error, _project


class PerformanceRunReportView(RunAPIView):
    def get(self, request, project_id, run_id):
        project = _project(request, project_id, REPORT)
        run = get_object_or_404(
            PerformanceRun.objects.select_related('plan').prefetch_related('participants'),
            pk=run_id, project=project,
        )
        if run.mode != PerformanceRun.Mode.LOAD or not run.is_terminal:
            return _error('report_unavailable', 'HTML 报告仅支持已结束的正式压测。', 409)
        response = HttpResponse(render_report(run), content_type='text/html; charset=utf-8')
        response['Content-Disposition'] = f'attachment; filename="performance-{run.pk}.html"'
        response['Cache-Control'] = 'no-store'
        response['X-Content-Type-Options'] = 'nosniff'
        return response
