"""Shared project membership and capability checks."""

from __future__ import annotations

from typing import Optional

from django.http import Http404
from rest_framework.exceptions import PermissionDenied

from users.permissions import is_platform_admin

from .models import Project, ProjectMember


READ = 'read'
EDIT = 'edit'
DELETE = 'delete'
EXECUTE = 'execute'
REPORT = 'report'


def projects_for_user(user):
    """Return every project for admins and member projects for normal users."""
    if is_platform_admin(user):
        return Project.objects.all()
    if not getattr(user, 'is_authenticated', False) or not getattr(user, 'is_active', False):
        return Project.objects.none()
    return Project.objects.filter(members__user=user).distinct()


def get_project_for_user(
    project_id: int,
    user,
    capability: str = READ,
    expected_project_type: Optional[str] = None,
) -> Project:
    """Resolve a project using admin status or an active membership relation.

    ``owner`` and ``created_by`` are audit/legacy pointers and intentionally do
    not grant access. Removing the membership therefore revokes existing-token
    access on the next request.
    """
    project = Project.objects.filter(pk=project_id).first()
    if project is None or (expected_project_type and project.project_type != expected_project_type):
        raise Http404('项目不存在')

    if is_platform_admin(user):
        return project

    if not getattr(user, 'is_authenticated', False) or not getattr(user, 'is_active', False):
        raise Http404('项目不存在或无权限访问')

    member = ProjectMember.objects.filter(project_id=project_id, user_id=getattr(user, 'id', None)).first()
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
