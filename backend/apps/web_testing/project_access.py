"""Project-scoped access checks for Web UI APIs."""

from __future__ import annotations

from functools import wraps
from typing import Optional

from rest_framework.exceptions import ValidationError

from projects.access import (
    DELETE, EDIT, EXECUTE, READ, REPORT,
    get_project_for_user as get_scoped_project_for_user,
)
from projects.models import Project


def get_project_for_user(
    project_id: int,
    user,
    capability: str = READ,
    expected_project_type: Optional[str] = 'web',
) -> Project:
    return get_scoped_project_for_user(
        project_id, user, capability, expected_project_type=expected_project_type,
    )


def project_access_required(
    capability: str = READ,
    expected_project_type: Optional[str] = 'web',
):
    """Guard an APIView method and hide mismatched project types as 404."""

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
            get_project_for_user(
                project_id,
                request.user,
                capability,
                expected_project_type=expected_project_type,
            )
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
