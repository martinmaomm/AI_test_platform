"""
Dashboard 统计服务层：按项目类型动态路由到对应模型
统一返回结构，前端无需修改。
"""
from datetime import timedelta
from django.db.models import Count, Q, Sum
from django.db.models.functions import TruncDate
from django.utils import timezone


def _empty_summary():
    return {
        'today_pass_rate': 0.0,
        'today_executions': 0,
        'ai_contribution_rate': 0.0,
        'total_cases': 0,
    }


def _empty_trend(start_date, end_date):
    result = []
    dt = start_date
    while dt <= end_date:
        result.append({
            'date': dt.isoformat(),
            'executions': 0,
            'passed': 0,
            'failed': 0,
            'pass_rate': 0.0,
        })
        dt += timedelta(days=1)
    return result


def _empty_top_failures():
    return []


# ==================== API 测试 ====================

def _api_summary(project_id):
    from api_testing.models import APITestExecution, APITestCase, APITestCaseExecutionDetail
    from api_testing.models import APITestSuiteExecutionDetail

    today = timezone.now().date()
    today_execs = APITestExecution.objects.filter(
        project_id=project_id,
        created_at__date=today
    ).exclude(status__in=('pending', 'running'))

    today_executions = today_execs.count()
    total_steps = 0
    passed_steps = 0
    for exec in today_execs.select_related('case_execution_detail', 'suite_execution_detail'):
        if exec.exec_type == 'case':
            try:
                detail = exec.case_execution_detail
                total_steps += 1
                if detail.status == 'passed':
                    passed_steps += 1
            except APITestCaseExecutionDetail.DoesNotExist:
                pass
        elif exec.exec_type == 'suite':
            try:
                detail = exec.suite_execution_detail
                total_steps += detail.total_cases
                passed_steps += detail.passed_cases
            except APITestSuiteExecutionDetail.DoesNotExist:
                pass

    today_pass_rate = round((passed_steps / total_steps * 100), 2) if total_steps > 0 else 0.0
    total_cases = APITestCase.objects.filter(project_id=project_id).count()
    ai_cases = APITestCase.objects.filter(
        project_id=project_id,
        test_case_type='scenario'
    ).count()
    ai_contribution_rate = round((ai_cases / total_cases * 100), 2) if total_cases > 0 else 0.0

    return {
        'today_pass_rate': today_pass_rate,
        'today_executions': today_executions,
        'ai_contribution_rate': ai_contribution_rate,
        'total_cases': total_cases,
    }


def _api_trend(project_id, start_date, end_date):
    from api_testing.models import APITestExecution, APITestCaseExecutionDetail
    from api_testing.models import APITestSuiteExecutionDetail

    exec_counts = (
        APITestExecution.objects.filter(
            project_id=project_id,
            created_at__date__gte=start_date,
            created_at__date__lte=end_date
        )
        .exclude(status__in=('pending', 'running'))
        .annotate(date=TruncDate('created_at'))
        .values('date')
        .annotate(executions=Count('id'))
        .order_by('date')
    )
    case_stats = (
        APITestCaseExecutionDetail.objects.filter(
            execution__project_id=project_id,
            execution__created_at__date__gte=start_date,
            execution__created_at__date__lte=end_date
        )
        .exclude(execution__status__in=('pending', 'running'))
        .annotate(date=TruncDate('execution__created_at'))
        .values('date')
        .annotate(
            passed=Count('id', filter=Q(status='passed')),
            failed=Count('id', filter=Q(status__in=('failed', 'error')))
        )
        .order_by('date')
    )
    suite_stats = (
        APITestSuiteExecutionDetail.objects.filter(
            execution__project_id=project_id,
            execution__created_at__date__gte=start_date,
            execution__created_at__date__lte=end_date
        )
        .exclude(execution__status__in=('pending', 'running'))
        .annotate(date=TruncDate('execution__created_at'))
        .values('date')
        .annotate(passed=Sum('passed_cases'), failed=Sum('failed_cases'))
        .order_by('date')
    )
    return _merge_trend_results(exec_counts, case_stats, suite_stats, start_date, end_date)


def _api_top_failures(project_id, start_date, end_date):
    from api_testing.models import APITestCaseExecutionDetail, APITestSuiteCaseExecution, APITestCase

    case_failures = (
        APITestCaseExecutionDetail.objects.filter(
            execution__project_id=project_id,
            execution__created_at__date__gte=start_date,
            execution__created_at__date__lte=end_date,
            status__in=('failed', 'error')
        )
        .values('test_case_id')
        .annotate(fail_count=Count('id'))
    )
    suite_failures = (
        APITestSuiteCaseExecution.objects.filter(
            suite_execution__execution__project_id=project_id,
            suite_execution__execution__created_at__date__gte=start_date,
            suite_execution__execution__created_at__date__lte=end_date,
            status__in=('failed', 'error')
        )
        .values('test_case_id')
        .annotate(fail_count=Count('id'))
    )
    case_id_to_count = {}
    for row in case_failures:
        case_id_to_count[row['test_case_id']] = case_id_to_count.get(row['test_case_id'], 0) + row['fail_count']
    for row in suite_failures:
        case_id_to_count[row['test_case_id']] = case_id_to_count.get(row['test_case_id'], 0) + row['fail_count']

    sorted_cases = sorted(case_id_to_count.items(), key=lambda x: x[1], reverse=True)[:5]
    case_ids = [c[0] for c in sorted_cases]
    cases_by_id = {c.id: c for c in APITestCase.objects.filter(id__in=case_ids).only('id', 'title')}

    return [
        {
            'test_case_id': cid,
            'test_case_name': getattr(cases_by_id.get(cid), 'title', None) or f'用例#{cid}',
            'fail_count': cnt,
        }
        for cid, cnt in sorted_cases
    ]


# ==================== Web 测试 ====================
# WebUITestExecution 无 project FK，通过 case_execution_detail__test_case__project 或
# suite_execution_detail__test_suite__project 过滤

def _web_summary(project_id):
    from web_testing.models import (
        WebUITestExecution, WebUITestCase,
        WebUITestCaseExecutionDetail, WebUITestSuiteExecutionDetail
    )

    today = timezone.now().date()
    base_q = Q(
        case_execution_detail__test_case__project_id=project_id
    ) | Q(
        suite_execution_detail__test_suite__project_id=project_id
    )
    today_execs = WebUITestExecution.objects.filter(base_q).filter(
        created_at__date=today
    ).exclude(status__in=('pending', 'running')).distinct()

    today_executions = today_execs.count()
    total_steps = 0
    passed_steps = 0
    for exec in today_execs.select_related('case_execution_detail', 'suite_execution_detail'):
        if exec.exec_type == 'case':
            try:
                detail = exec.case_execution_detail
                total_steps += 1
                if detail.status == 'passed':
                    passed_steps += 1
            except WebUITestCaseExecutionDetail.DoesNotExist:
                pass
        elif exec.exec_type == 'suite':
            try:
                detail = exec.suite_execution_detail
                total_steps += detail.total_cases
                passed_steps += detail.passed_cases
            except WebUITestSuiteExecutionDetail.DoesNotExist:
                pass

    today_pass_rate = round((passed_steps / total_steps * 100), 2) if total_steps > 0 else 0.0
    total_cases = WebUITestCase.objects.filter(project_id=project_id).count()
    return {
        'today_pass_rate': today_pass_rate,
        'today_executions': today_executions,
        'ai_contribution_rate': 0.0,
        'total_cases': total_cases,
    }


def _web_trend(project_id, start_date, end_date):
    from web_testing.models import (
        WebUITestExecution, WebUITestCaseExecutionDetail,
        WebUITestSuiteExecutionDetail
    )

    base_q = Q(
        case_execution_detail__test_case__project_id=project_id
    ) | Q(
        suite_execution_detail__test_suite__project_id=project_id
    )
    exec_qs = WebUITestExecution.objects.filter(base_q).filter(
        created_at__date__gte=start_date,
        created_at__date__lte=end_date
    ).exclude(status__in=('pending', 'running')).distinct()

    exec_counts = (
        exec_qs.annotate(date=TruncDate('created_at'))
        .values('date')
        .annotate(executions=Count('id'))
        .order_by('date')
    )
    case_stats = (
        WebUITestCaseExecutionDetail.objects.filter(
            test_case__project_id=project_id,
            execution__created_at__date__gte=start_date,
            execution__created_at__date__lte=end_date
        )
        .exclude(execution__status__in=('pending', 'running'))
        .annotate(date=TruncDate('execution__created_at'))
        .values('date')
        .annotate(
            passed=Count('id', filter=Q(status='passed')),
            failed=Count('id', filter=Q(status__in=('failed', 'error')))
        )
        .order_by('date')
    )
    suite_stats = (
        WebUITestSuiteExecutionDetail.objects.filter(
            test_suite__project_id=project_id,
            execution__created_at__date__gte=start_date,
            execution__created_at__date__lte=end_date
        )
        .exclude(execution__status__in=('pending', 'running'))
        .annotate(date=TruncDate('execution__created_at'))
        .values('date')
        .annotate(passed=Sum('passed_cases'), failed=Sum('failed_cases'))
        .order_by('date')
    )
    return _merge_trend_results(exec_counts, case_stats, suite_stats, start_date, end_date)


def _web_top_failures(project_id, start_date, end_date):
    from web_testing.models import WebUITestCaseExecutionDetail, WebUITestSuiteCaseExecution, WebUITestCase

    case_failures = (
        WebUITestCaseExecutionDetail.objects.filter(
            test_case__project_id=project_id,
            execution__created_at__date__gte=start_date,
            execution__created_at__date__lte=end_date,
            status__in=('failed', 'error')
        )
        .values('test_case_id')
        .annotate(fail_count=Count('id'))
    )
    suite_failures = (
        WebUITestSuiteCaseExecution.objects.filter(
            test_case__project_id=project_id,
            suite_execution__execution__created_at__date__gte=start_date,
            suite_execution__execution__created_at__date__lte=end_date,
            status__in=('failed', 'error')
        )
        .values('test_case_id')
        .annotate(fail_count=Count('id'))
    )
    case_id_to_count = {}
    for row in case_failures:
        case_id_to_count[row['test_case_id']] = case_id_to_count.get(row['test_case_id'], 0) + row['fail_count']
    for row in suite_failures:
        case_id_to_count[row['test_case_id']] = case_id_to_count.get(row['test_case_id'], 0) + row['fail_count']

    sorted_cases = sorted(case_id_to_count.items(), key=lambda x: x[1], reverse=True)[:5]
    case_ids = [c[0] for c in sorted_cases]
    cases_by_id = {c.id: c for c in WebUITestCase.objects.filter(id__in=case_ids).only('id', 'title')}

    return [
        {
            'test_case_id': cid,
            'test_case_name': getattr(cases_by_id.get(cid), 'title', None) or f'用例#{cid}',
            'fail_count': cnt,
        }
        for cid, cnt in sorted_cases
    ]


# ==================== 通用合并逻辑 ====================

def _merge_trend_results(exec_counts, case_stats, suite_stats, start_date, end_date):
    by_date = {}
    dt = start_date
    while dt <= end_date:
        by_date[dt] = {
            'date': dt.isoformat(),
            'executions': 0,
            'passed': 0,
            'failed': 0,
            'pass_rate': 0.0,
        }
        dt += timedelta(days=1)

    for row in exec_counts:
        d = row['date']
        if d in by_date:
            by_date[d]['executions'] = row['executions']

    for row in case_stats:
        d = row['date']
        if d in by_date:
            by_date[d]['passed'] += row['passed'] or 0
            by_date[d]['failed'] += row['failed'] or 0

    for row in suite_stats:
        d = row['date']
        if d in by_date:
            by_date[d]['passed'] += row['passed'] or 0
            by_date[d]['failed'] += row['failed'] or 0

    result = []
    for d in sorted(by_date.keys()):
        row = by_date[d]
        total = row['passed'] + row['failed']
        row['pass_rate'] = round((row['passed'] / total * 100), 2) if total > 0 else 0.0
        result.append(row)
    return result


# ==================== 工厂入口 ====================

def get_dashboard_summary(project):
    """根据项目类型返回 summary 统计"""
    project_type = (project.project_type or 'api').lower()
    project_id = project.id
    try:
        if project_type == 'api':
            return _api_summary(project_id)
        if project_type == 'web':
            return _web_summary(project_id)
        if project_type in ('app', 'perf'):
            return _empty_summary()
        return _api_summary(project_id)
    except Exception:
        return _empty_summary()


def get_dashboard_trend(project):
    """根据项目类型返回 trend 趋势数据"""
    project_type = (project.project_type or 'api').lower()
    project_id = project.id
    end_date = timezone.now().date()
    start_date = end_date - timedelta(days=6)
    try:
        if project_type == 'api':
            return _api_trend(project_id, start_date, end_date)
        if project_type == 'web':
            return _web_trend(project_id, start_date, end_date)
        if project_type in ('app', 'perf'):
            return _empty_trend(start_date, end_date)
        return _api_trend(project_id, start_date, end_date)
    except Exception:
        return _empty_trend(start_date, end_date)


def get_dashboard_top_failures(project):
    """根据项目类型返回 top-failures 数据"""
    project_type = (project.project_type or 'api').lower()
    project_id = project.id
    end_date = timezone.now().date()
    start_date = end_date - timedelta(days=6)
    try:
        if project_type == 'api':
            return _api_top_failures(project_id, start_date, end_date)
        if project_type == 'web':
            return _web_top_failures(project_id, start_date, end_date)
        if project_type in ('app', 'perf'):
            return _empty_top_failures()
        return _api_top_failures(project_id, start_date, end_date)
    except Exception:
        return _empty_top_failures()
