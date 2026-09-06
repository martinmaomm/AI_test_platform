"""Project-scoped access checks for the project knowledge APIs."""

from django.http import Http404
from rest_framework.exceptions import PermissionDenied

from projects.models import Project, ProjectMember


def can_edit(project: Project, user) -> bool:
    """Whether ``user`` can change project knowledge owned by ``project``."""
    if not getattr(user, 'is_authenticated', False):
        return False
    if project.owner_id == user.id or project.created_by_id == user.id:
        return True
    return ProjectMember.objects.filter(
        project=project, user=user, can_edit=True,
    ).exists()


def require_project(project_id, user, edit: bool = False) -> Project:
    """Return an accessible project without disclosing unrelated projects.

    Creators and owners are intentionally allowed even when they have no
    ``ProjectMember`` row.  A user who is not a project member receives 404;
    an existing read-only member receives 403 for a requested edit operation.
    """
    project = Project.objects.filter(pk=project_id).first()
    if project is None:
        raise Http404('项目不存在')

    if not getattr(user, 'is_authenticated', False):
        raise Http404('项目不存在')
    if project.owner_id == user.id or project.created_by_id == user.id:
        return project

    member = ProjectMember.objects.filter(project=project, user=user).first()
    if member is None:
        raise Http404('项目不存在或无访问权限')
    if edit and not member.can_edit:
        raise PermissionDenied('没有修改此项目知识库的权限')
    if not edit and not member.can_view_reports:
        raise PermissionDenied('没有查看此项目知识库的权限')
    return project
