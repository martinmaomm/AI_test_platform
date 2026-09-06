"""Freeze API execution inputs at dispatch; workers never read mutable case content."""
from copy import deepcopy

from django.db import transaction

from .models import APITestCaseExecutionDetail, APITestSuiteCaseExecution, APITestSuiteExecutionDetail


def environment_options(environment):
    if environment is None:
        return {}
    if not environment.is_active or not environment.is_api_environment:
        raise ValueError('请选择已启用的 API 环境')
    return deepcopy(environment.get_api_config() or {})


def freeze_case(case, options, overrides=None):
    from .case_contract import normalize_case
    document = normalize_case(case.script_content)
    config = document.get('config', {})
    options = deepcopy(options)
    options['variables'] = {
        **options.get('variables', {}), **config.get('variables', {}), **(overrides or {}),
    }
    return {
        'case_id': case.id, 'name': case.title, 'script': document,
        'revision': case.updated_at.isoformat(), 'options': options,
    }


@transaction.atomic
def capture_case_snapshot(execution, case, environment=None, variables=None):
    if execution.project_id != case.project_id or (environment and environment.project_id != case.project_id):
        raise ValueError('用例和执行环境必须属于当前项目')
    frozen = freeze_case(case, environment_options(environment), variables)
    detail, _ = APITestCaseExecutionDetail.objects.get_or_create(
        execution=execution, defaults={'test_case': case, 'name': case.title},
    )
    frozen['detail_id'] = detail.id
    execution.input_snapshot = {'version': 1, 'kind': 'case', 'cases': [frozen]}
    execution.save(update_fields=['input_snapshot'])
    return execution.input_snapshot


@transaction.atomic
def capture_suite_snapshot(execution, suite, environment=None, variables=None):
    if execution.project_id != suite.project_id or (environment and environment.project_id != suite.project_id):
        raise ValueError('套件和执行环境必须属于当前项目')
    options = environment_options(environment)
    if not isinstance(suite.variables, dict) or (variables is not None and not isinstance(variables, dict)):
        raise ValueError('套件变量及本次覆盖变量必须是对象')
    overrides = {**suite.variables, **(variables or {})}
    order = {str(pk): index for index, pk in enumerate(suite.test_case_order or [])}
    cases = sorted(suite.test_cases.all(), key=lambda case: (order.get(str(case.pk), 10**9), case.pk))
    if not cases:
        raise ValueError('套件没有测试用例')
    if any(case.project_id != suite.project_id for case in cases):
        raise ValueError('套件中存在其他项目的用例')
    frozen = [freeze_case(case, options, overrides) for case in cases]
    detail, _ = APITestSuiteExecutionDetail.objects.get_or_create(
        execution=execution, defaults={
            'test_suite': suite, 'test_suite_name': suite.name, 'total_cases': len(cases),
        },
    )
    for case, item in zip(cases, frozen):
        case_detail, _ = APITestSuiteCaseExecution.objects.get_or_create(
            suite_execution=detail, test_case=case,
            defaults={'name': case.title, 'status': 'pending'},
        )
        item['detail_id'] = case_detail.pk
    execution.input_snapshot = {'version': 1, 'kind': 'suite', 'name': suite.name, 'cases': frozen}
    execution.save(update_fields=['input_snapshot'])
    return execution.input_snapshot
