"""Project-scoped access checks for the project knowledge APIs."""

from django.http import Http404
from rest_framework.exceptions import PermissionDenied
from projects.access import EDIT, READ, get_project_for_user
from projects.models import Project
from users.permissions import is_platform_admin


def can_edit(project: Project, user) -> bool:
    """Whether ``user`` can change project knowledge owned by ``project``."""
    if not getattr(user, 'is_authenticated', False):
        return False
    if is_platform_admin(user):
        return True
    try:
        get_project_for_user(project.pk, user, EDIT)
    except (Http404, PermissionDenied):
        return False
    return True


def require_project(project_id, user, edit: bool = False) -> Project:
    """Return a project for platform admins or current project members."""
    return get_project_for_user(project_id, user, EDIT if edit else READ)
