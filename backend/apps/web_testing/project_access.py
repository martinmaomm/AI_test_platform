"""Project-scoped access checks for Web UI APIs."""

from __future__ import annotations

from functools import wraps
from typing import Optional

from django.http import Http404
from rest_framework.exceptions import PermissionDenied, ValidationError

from projects.models import Project, ProjectMember


READ = 'read'
EDIT = 'edit'
DELETE = 'delete'
EXECUTE = 'execute'
REPORT = 'report'


def get_project_for_user(project_id: int, user, capability: str = READ) -> Project:
    """Return a project only when the user has the requested capability.

    A non-member receives 404 so project existence is not disclosed. A member
    without the requested capability receives the normal DRF 403 response.
    Owners and creators bypass member capability flags.
    """

    project = Project.objects.filter(pk=project_id).first()
    if project is None:
        raise Http404('项目不存在')

    if project.owner_id == user.id or project.created_by_id == user.id:
        return project

    member = ProjectMember.objects.filter(project_id=project_id, user_id=user.id).first()
    if member is None:
        raise Http404('项目不存在或无权限访问')

    required_flag = {
        EDIT: 'can_edit',
        DELETE: 'can_delete',
        EXECUTE: 'can_execute_tests',
        REPORT: 'can_view_reports',
    }.get(capability)
    if required_flag and not getattr(member, required_flag, False):
        raise PermissionDenied('没有执行此项目操作的权限')
    return project


def project_access_required(capability: str = READ):
    """Guard an APIView method before its broad legacy exception handlers run."""

    def decorator(view_method):
        @wraps(view_method)
        def wrapped(*args, **kwargs):
            # Supports both function-based views and APIView methods.
            if args and hasattr(args[0], 'user'):
                request = args[0]
            elif len(args) >= 2:
                request = args[1]
            else:
                raise TypeError('项目权限校验缺少 request 参数')
            project_id = kwargs.get('project_id')
            if project_id is None:
                values = args[1:] if args and hasattr(args[0], 'user') else args[2:]
                if values:
                    project_id = values[0]
            get_project_for_user(project_id, request.user, capability)
            return view_method(*args, **kwargs)

        return wrapped

    return decorator


def payload_project_mismatch(data, project_id: int, field: str = 'project') -> bool:
    """Whether a payload tries to override the authoritative URL project."""

    if not hasattr(data, 'get'):
        return False
    value = data.get(field)
    if value in (None, ''):
        return False
    try:
        return int(value) != int(project_id)
    except (TypeError, ValueError):
        return True


def validate_related_project(model, value, project_id, field: str):
    """Reject a related resource that is not owned by the URL project."""

    if value in (None, ''):
        return
    object_id = getattr(value, 'pk', value)
    try:
        matches_project = model.objects.filter(pk=object_id, project_id=project_id).exists()
    except (TypeError, ValueError):
        matches_project = False
    if not matches_project:
        raise ValidationError({field: '关联对象必须属于 URL 中的项目'})
