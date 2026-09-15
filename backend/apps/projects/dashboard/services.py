"""首页统计：按项目隔离，统计已结束执行中的实际用例结果。"""
from datetime import datetime, time, timedelta

from django.db.models import Count, F, Q
from django.utils import timezone


FINISHED = ('passed', 'failed', 'error', 'incomplete', 'stopped')
VERDICTS = ('passed', 'failed', 'error')


def _empty_summary():
    return dict(today_pass_rate=0.0, today_executions=0,
                ai_contribution_rate=0.0, total_cases=0)


def _empty_trend(start, end):
    return [dict(date=(start + timedelta(days=n)).isoformat(), executions=0,
                 passed=0, failed=0, pass_rate=0.0)
            for n in range((end - start).days + 1)]


def _models(project):
    if project.project_type == 'api':
        from api_testing.models import (
            APITestExecution, APITestCase, APITestCaseExecutionDetail, APITestSuiteCaseExecution,
        )
        return APITestExecution, APITestCase, APITestCaseExecutionDetail, APITestSuiteCaseExecution
    if project.project_type == 'web':
        from web_testing.models import (
            WebUITestExecution, WebUITestCase, WebUITestCaseExecutionDetail, WebUITestSuiteCaseExecution,
        )
        return WebUITestExecution, WebUITestCase, WebUITestCaseExecutionDetail, WebUITestSuiteCaseExecution
    return None


def _executions(project, start, end):
    # 用应用时区划分日期，半开区间也避免依赖 MySQL 的时区表。
    tz = timezone.get_current_timezone()
    lower = timezone.make_aware(datetime.combine(start, time.min), tz)
    upper = timezone.make_aware(datetime.combine(end + timedelta(days=1), time.min), tz)
    return _models(project)[0].objects.filter(
        project_id=project.pk, created_at__gte=lower, created_at__lt=upper,
        status__in=FINISHED,
    ).order_by()


def _details(project, executions):
    _, _, single, child = _models(project)
    return (
        (single.objects.filter(execution__in=executions.exclude(exec_type='suite')).order_by(), 'execution'),
        (child.objects.filter(suite_execution__execution__in=executions.filter(exec_type='suite')).order_by(),
         'suite_execution__execution'),
    )


def _rate(passed, failed):
    return round(100 * passed / (passed + failed), 2) if passed + failed else 0.0


def get_dashboard_summary(project):
    models = _models(project)
    if not models:
        return _empty_summary()
    today = timezone.localdate()
    executions = _executions(project, today, today)
    passed = failed = 0
    for details, _ in _details(project, executions):
        result = details.aggregate(
            passed=Count('pk', filter=Q(status='passed')),
            failed=Count('pk', filter=Q(status__in=('failed', 'error'))),
        )
        passed += result['passed']
        failed += result['failed']
    cases = models[1].objects.filter(project_id=project.pk)
    total = cases.count()
    # 来源在首次保存用例时固化，不随脚本编辑、AI 修复或工作区删除而变化。
    # 历史来源未知的用例属于总数，但不臆测为 AI 生成。
    ai_count = cases.filter(creation_source='ai').count()
    return dict(today_pass_rate=_rate(passed, failed), today_executions=executions.count(),
                ai_contribution_rate=round(100 * ai_count / total, 2) if total else 0.0,
                total_cases=total)


def get_dashboard_trend(project):
    end = timezone.localdate()
    start = end - timedelta(days=6)
    rows = _empty_trend(start, end)
    if not _models(project):
        return rows
    by_date = {row['date']: row for row in rows}
    executions = _executions(project, start, end)
    for timestamp in executions.values_list('created_at', flat=True).iterator():
        by_date[timezone.localdate(timestamp).isoformat()]['executions'] += 1
    for details, path in _details(project, executions):
        for status, timestamp in details.filter(status__in=VERDICTS).values_list(
            'status', f'{path}__created_at',
        ).iterator():
            row = by_date[timezone.localdate(timestamp).isoformat()]
            row['passed' if status == 'passed' else 'failed'] += 1
    for row in rows:
        row['pass_rate'] = _rate(row['passed'], row['failed'])
    return rows


def _usable_name(value):
    return value.strip() if isinstance(value, str) and value.strip().lower() not in ('', 'none', 'null') else ''


def _identifier(value):
    # 快照缺少或损坏 ID 时仅使用名称兜底，不让一条记录拖垮整页统计。
    if isinstance(value, bool):
        return None
    try:
        return int(value) if str(value).isdigit() and int(value) > 0 else None
    except (TypeError, ValueError):
        return None


def get_dashboard_top_failures(project):
    models = _models(project)
    if not models:
        return []
    end = timezone.localdate()
    executions = _executions(project, end - timedelta(days=6), end)
    cases = dict(models[1].objects.filter(project_id=project.pk).values_list('pk', 'title'))
    workspace_cases = {}
    if project.project_type == 'api':
        from api_testing.models import APIWorkspace
        workspace_cases = dict(APIWorkspace.objects.filter(
            project_id=project.pk, saved_case_id__in=cases,
        ).values_list('pk', 'saved_case_id'))
    grouped = {}
    for details, path in _details(project, executions):
        fields = ['pk', 'test_case_id', 'execution_name', 'parent_execution_id']
        annotations = {'execution_name': F(f'{path}__name'), 'parent_execution_id': F(f'{path}__pk')}
        if project.project_type == 'api' or path != 'execution':
            fields.append('name')
        if project.project_type == 'api' and path == 'execution':
            annotations.update(workspace_id=F('execution__input_snapshot__workspace_id'),
                               frozen_case_id=F('execution__input_snapshot__cases__0__case_id'))
            fields += ['workspace_id', 'frozen_case_id']
        for detail in details.filter(status__in=('failed', 'error')).annotate(**annotations).values(*fields).iterator():
            workspace_id = _identifier(detail.get('workspace_id'))
            case_id = detail['test_case_id'] or _identifier(detail.get('frozen_case_id')) or workspace_cases.get(workspace_id)
            snapshot_name = _usable_name(detail.get('name')) or _usable_name(detail['execution_name'])
            if case_id:
                key = ('case', case_id)
                name = _usable_name(cases.get(case_id)) or snapshot_name or f'用例#{case_id}'
            elif workspace_id:
                key = ('workspace', workspace_id)
                name = snapshot_name or f'未保存场景（工作区 {workspace_id}）'
            elif project.project_type == 'web' and path == 'execution':
                # UI 草稿没有稳定用例关联，执行名称又可能完全相同，不能臆测它们是同一用例。
                key = ('execution', detail['parent_execution_id'])
                name = f'{snapshot_name or "未保存用例"}（执行 #{detail["parent_execution_id"]}）'
            else:
                key = ('name', snapshot_name) if snapshot_name else (path, detail['pk'])
                name = snapshot_name or f'未命名用例（执行明细 {detail["pk"]}）'
            row = grouped.setdefault(key, dict(test_case_id=case_id if case_id in cases else None,
                                               test_case_name=name, fail_count=0))
            row['fail_count'] += 1
    return sorted(grouped.values(), key=lambda row: (-row['fail_count'], row['test_case_name']))[:5]
