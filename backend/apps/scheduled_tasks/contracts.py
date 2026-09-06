"""Project-scoped inputs shared by scheduled-task APIs and execution checks."""
from django.http import Http404
from rest_framework.exceptions import ValidationError

from projects.models import Project
from web_testing.project_access import get_project_for_user


def get_schedule_project(project_id, user, capability='read'):
    if user.is_superuser:
        project = Project.objects.filter(pk=project_id).first()
        if project is None:
            raise Http404('项目不存在')
    else:
        project = get_project_for_user(project_id, user, capability, expected_project_type=None)
    if project.project_type not in {'web', 'api'}:
        raise ValidationError('当前项目不支持计划任务，仅支持 UI 和 API 项目')
    return project


def suite_model(project):
    if project.project_type == 'web':
        from web_testing.models import WebUITestSuite
        return WebUITestSuite
    if project.project_type == 'api':
        from api_testing.models import APITestSuite
        return APITestSuite
    raise ValidationError('当前项目不支持计划任务')


def validate_suites(project, ids):
    if not isinstance(ids, list) or not ids:
        raise ValidationError({'suite_ids': '至少选择一个测试套件'})
    if any(type(value) is not int or value <= 0 for value in ids):
        raise ValidationError({'suite_ids': '套件 ID 必须为正整数'})
    if len(ids) != len(set(ids)):
        raise ValidationError({'suite_ids': '不能重复选择同一个套件'})
    rows = {suite.pk: suite for suite in suite_model(project).objects.filter(
        project=project, pk__in=ids,
    ).prefetch_related('test_cases')}
    missing = set(ids) - rows.keys()
    if missing:
        raise ValidationError({'suite_ids': '部分套件已删除或不属于当前项目，请重新选择'})
    for suite in rows.values():
        if suite.status != 'active':
            raise ValidationError({'suite_ids': f'套件「{suite.name}」未启用'})
        if not suite.test_cases.exists():
            raise ValidationError({'suite_ids': f'套件「{suite.name}」没有测试用例'})
    return [rows[pk] for pk in ids]
