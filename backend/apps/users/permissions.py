"""Platform roles are separate from membership of a particular project."""
from rest_framework.permissions import BasePermission


def is_platform_admin(user):
    return bool(
        user and user.is_authenticated and user.is_active
        and (user.is_staff or user.is_superuser)
    )


class IsPlatformAdmin(BasePermission):
    message = '仅管理员可以执行此操作。'

    def has_permission(self, request, view):
        return is_platform_admin(request.user)
