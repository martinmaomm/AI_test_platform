"""Small, admin-only account and project-assignment management surface."""
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.views import APIView

from common.api import response
from projects.models import Project, ProjectMember
from .models import User
from .permissions import IsPlatformAdmin, is_platform_admin
from .serializers import ManagedUserSerializer, UserProjectAssignmentSerializer


def _locked_actor(request):
    actor = get_object_or_404(User.objects.select_for_update(), pk=request.user.pk)
    if not is_platform_admin(actor):
        raise PermissionDenied('您的管理员权限已变更，请刷新后重试。')
    return actor


def _locked_edit_target(request, pk):
    # A fixed order avoids crossing actor/target locks when two admins edit.
    users = {user.pk: user for user in User.objects.select_for_update().filter(
        pk__in={request.user.pk, pk},
    ).order_by('pk')}
    actor = users.get(request.user.pk)
    if not is_platform_admin(actor):
        raise PermissionDenied('您的管理员权限已变更，请刷新后重试。')
    target = users.get(pk)
    if target is None:
        from django.http import Http404
        raise Http404('用户不存在')
    if target.is_superuser:
        raise PermissionDenied('超级管理员账号受保护，请通过个人资料修改自身资料。')
    if target.pk == actor.pk:
        raise PermissionDenied('不能通过用户管理修改自己，请使用个人资料。')
    if target.is_staff and not actor.is_superuser:
        raise PermissionDenied('只有超级管理员可以管理其他管理员。')
    return actor, target


class UserManagementListView(APIView):
    permission_classes = [IsPlatformAdmin]

    def get(self, request):
        try:
            page = max(1, int(request.query_params.get('page', 1)))
            page_size = min(100, max(1, int(request.query_params.get('page_size', 20))))
        except (TypeError, ValueError):
            raise ValidationError('分页参数必须是数字。')
        users = User.objects.order_by('-is_superuser', '-is_staff', 'id')
        search = request.query_params.get('search', '').strip()
        if search:
            users = users.filter(Q(username__icontains=search) | Q(email__icontains=search))
        return response(kind='paginated_queryset', data=users, page=page, page_size=page_size,
                        serializer_class=ManagedUserSerializer, message='获取用户列表成功')

    def post(self, request):
        try:
            with transaction.atomic():
                actor = _locked_actor(request)
                serializer = ManagedUserSerializer(data=request.data, context={'request': request, 'actor': actor})
                serializer.is_valid(raise_exception=True)
                serializer.save()
                return response(kind='created', data=serializer.data, status_code=201, message='用户创建成功')
        except IntegrityError:
            raise ValidationError('用户名或邮箱已存在，请刷新后重试。')


class UserManagementDetailView(APIView):
    permission_classes = [IsPlatformAdmin]

    def get(self, request, pk):
        return response(kind='success', data=ManagedUserSerializer(get_object_or_404(User, pk=pk)).data)

    def patch(self, request, pk):
        try:
            with transaction.atomic():
                actor, target = _locked_edit_target(request, pk)
                serializer = ManagedUserSerializer(target, data=request.data, partial=True,
                                                   context={'request': request, 'actor': actor})
                serializer.is_valid(raise_exception=True)
                serializer.save()
                return response(kind='success', data=serializer.data, message='用户信息已更新')
        except IntegrityError:
            raise ValidationError('用户名或邮箱已存在，请刷新后重试。')


class UserProjectAssignmentView(APIView):
    permission_classes = [IsPlatformAdmin]

    def get(self, request, pk):
        user = get_object_or_404(User, pk=pk)
        ids = ProjectMember.objects.filter(user=user).order_by('project_id').values_list('project_id', flat=True)
        return response(kind='success', data={'project_ids': list(ids)})

    def put(self, request, pk):
        with transaction.atomic():
            _, target = _locked_edit_target(request, pk)
            if target.is_staff:
                raise ValidationError('管理员可以管理全部项目，无需单独分配。')
            serializer = UserProjectAssignmentSerializer(data=request.data)
            serializer.is_valid(raise_exception=True)
            ids = serializer.validated_data['project_ids']
            locked_ids = list(Project.objects.select_for_update().filter(pk__in=ids).order_by('pk').values_list('pk', flat=True))
            if locked_ids != ids:
                raise ValidationError('所选项目已变更，请刷新后重试。')
            ProjectMember.objects.filter(user=target).exclude(project_id__in=ids).delete()
            for project_id in ids:
                ProjectMember.objects.update_or_create(project_id=project_id, user=target, defaults={
                    'role': 'editor', 'can_edit': True, 'can_delete': True,
                    'can_execute_tests': True, 'can_view_reports': True,
                })
            return response(kind='success', data={'project_ids': ids}, message='项目分配已更新')
