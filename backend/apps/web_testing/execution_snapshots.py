"""Durable WebUI suite execution snapshots.

Snapshots are captured before a task is queued.  Runners must never rebuild
them from mutable source suites or cases.
"""
from __future__ import annotations

import copy

from .constants import normalize_webui_execution_options
from .execution_variables import runtime_variable_names
from .models import WebUITestSuiteCaseExecution, WebUITestSuiteExecutionDetail


def capture_suite_snapshot(execution, suite, *, execution_options=None, runtime_variables=None):
    """Create one complete, ordered suite snapshot for a new execution.

    The caller owns the surrounding transaction and must call this before any
    Celery dispatch.  A missing or partial snapshot is a dispatch bug, not a
    condition the worker is allowed to repair from current source data.
    """
    if execution.exec_type != 'suite':
        raise ValueError('仅套件执行可以创建套件快照')
    if execution.project_id != suite.project_id:
        raise ValueError('执行记录与测试套件不属于同一项目')
    if WebUITestSuiteExecutionDetail.objects.filter(execution=execution).exists():
        raise ValueError('套件执行快照已存在')

    memberships = list(
        suite.case_memberships.select_related('test_case__module').order_by('order', 'id')
    )
    if not memberships:
        raise ValueError('测试套件中没有测试用例')

    detail = WebUITestSuiteExecutionDetail.objects.create(
        execution=execution,
        test_suite=suite,
        total_cases=len(memberships),
        suite_variables=copy.deepcopy(suite.variables or []),
        runtime_variable_names=runtime_variable_names(runtime_variables),
        # All callers (including scheduled execution) freeze the normalized
        # defaults here, never defer option resolution to a future worker.
        execution_options=copy.deepcopy(normalize_webui_execution_options(execution_options)),
    )
    WebUITestSuiteCaseExecution.objects.bulk_create([
        WebUITestSuiteCaseExecution(
            suite_execution=detail,
            test_case=membership.test_case,
            name=membership.test_case.title,
            description=membership.test_case.description,
            module_name=membership.test_case.module.name if membership.test_case.module else '',
            execution_order=index,
            script_content=membership.test_case.test_script_content or '',
            variables=copy.deepcopy(membership.test_case.variables or []),
            source_script_version=membership.test_case.script_version,
            source_edit_version=membership.test_case.edit_version,
            status='pending',
        )
        for index, membership in enumerate(memberships, start=1)
    ])
    return detail
