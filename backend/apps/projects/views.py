from rest_framework import generics, permissions, status, viewsets
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.decorators import api_view, permission_classes, action
from rest_framework.response import Response
from django.shortcuts import get_object_or_404
from django.db import transaction
from django.db.models import Q
from django.contrib.auth import get_user_model
from django.core.paginator import Paginator
from django.core.files.storage import default_storage
import logging

from users.models import UserPreference
from users.permissions import IsPlatformAdmin, is_platform_admin
from .access import REPORT, get_project_for_user, projects_for_user
from .models import Project, ProjectMember
from .serializers import (
    ProjectSerializer, ProjectCreateSerializer, ProjectMemberSerializer,
    ProjectMemberCreateSerializer, ProjectDetailSerializer, ProjectUpdateSerializer
)
from common.api import response

logger = logging.getLogger(__name__)
User = get_user_model()


def _lock_admin_and_target(request, target_user_id):
    users = {user.pk: user for user in User.objects.select_for_update().filter(
        pk__in={request.user.pk, target_user_id},
    ).order_by('pk')}
    actor = users.get(request.user.pk)
    if not is_platform_admin(actor):
        raise PermissionDenied('您的管理员权限已变更，请刷新后重试。')
    target = users.get(target_user_id)
    if target is None:
        raise ValidationError({'user': '用户不存在'})
    if is_platform_admin(target):
        raise ValidationError({'user': '管理员可以管理全部项目，无需加入项目成员。'})
    return target


class ProjectViewSet(viewsets.ModelViewSet):
    """项目管理ViewSet"""
    permission_classes = [permissions.IsAuthenticated]

    def get_permissions(self):
        permission_types = [permissions.IsAuthenticated]
        if self.action in {'create', 'update', 'partial_update', 'destroy'}:
            permission_types.append(IsPlatformAdmin)
        return [permission() for permission in permission_types]
    
    def get_queryset(self):
        queryset = projects_for_user(self.request.user)

        # 按项目类型过滤
        project_type = self.request.query_params.get('project_type', '')
        if project_type:
            if project_type not in dict(Project.PROJECT_TYPE_CHOICES):
                raise ValidationError({'project_type': '不支持的项目类型'})
            queryset = queryset.filter(project_type=project_type)

        # 搜索功能
        search_query = self.request.query_params.get('search', '')
        if search_query:
            queryset = queryset.filter(
                Q(name__icontains=search_query) |
                Q(description__icontains=search_query)
            )

        return queryset.order_by('-updated_at')

    def get_serializer_class(self):
        if self.action == 'create':
            return ProjectCreateSerializer
        elif self.action in ['update', 'partial_update']:
            return ProjectUpdateSerializer
        elif self.action == 'retrieve':
            return ProjectDetailSerializer
        return ProjectSerializer

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user)

    def perform_update(self, serializer):
        """Project metadata is managed by platform administrators only."""
        serializer.save()

    def list(self, request, *args, **kwargs):
        queryset = self.get_queryset()
        page = int(request.query_params.get("page", 1))
        page_size = int(request.query_params.get("page_size", 20))
        # 直接使用封装的分页函数
        return response(
            kind="paginated_queryset",
            data=queryset,
            page=page,
            page_size=page_size,
            serializer_class=self.get_serializer_class(),
            message="获取项目列表成功"
        )

    def perform_destroy(self, instance):
        """删除项目时，同时删除所有相关的物理文件和数据库记录"""
        try:
            # 获取项目下的所有上传文件
            uploaded_files = instance.uploaded_files.all()

            # 删除物理文件
            for uploaded_file in uploaded_files:
                if uploaded_file.file:
                    try:
                        file_path = uploaded_file.file.name
                        if default_storage.exists(file_path):
                            default_storage.delete(file_path)
                            logger.info(f"已删除项目文件: {file_path}")
                        else:
                            logger.warning(f"项目文件不存在: {file_path}")
                    except Exception as e:
                        logger.warning(f"删除项目文件失败: {uploaded_file.file.name if uploaded_file.file else 'Unknown'}, 错误: {e}")

            # 删除项目记录（这会自动删除所有相关的CASCADE记录）
            super().perform_destroy(instance)
            logger.info(f"已删除项目: {instance.name} (ID: {instance.id})")

        except Exception as e:
            logger.error(f"删除项目失败: {instance.id}, 错误: {e}")
            raise

    @action(detail=False, methods=['get'])
    def user_projects(self, request):
        """获取当前用户的项目列表"""
        projects = projects_for_user(request.user)
        serializer = ProjectSerializer(projects, many=True)
        return response(
            kind="success",
            data=serializer.data,
            message="获取用户项目列表成功"
        )

    @action(detail=True, methods=['post'])
    def leave(self, request, pk=None):
        """离开项目"""
        project = self.get_object()
        member = get_object_or_404(ProjectMember, project=project, user=request.user)

        member.delete()
        return response(
            kind="success",
            data={"message": "已成功离开项目"},
            message="已成功离开项目"
        )

    @action(detail=True, methods=['get'])
    def statistics(self, request, pk=None):
        """获取项目统计信息"""
        project = self.get_object()

        get_project_for_user(project.pk, request.user, REPORT)

        # 统计信息
        total_members = project.members.count()
        total_environments = project.environments.count()
        total_knowledge_files = project.knowledge_files.count()

        return response(
            kind="success",
            data={
                "project_id": project.id,
                "project_name": project.name,
                "total_members": total_members,
                "total_environments": total_environments,
                "total_knowledge_files": total_knowledge_files,
                "created_at": project.created_at,
                "updated_at": project.updated_at
            },
            message="获取项目统计信息成功"
        )


class ProjectMemberListView(generics.ListCreateAPIView):
    """项目成员列表和添加视图"""
    serializer_class = ProjectMemberSerializer
    permission_classes = [permissions.IsAuthenticated, IsPlatformAdmin]

    def get_queryset(self):
        project_id = self.kwargs.get('project_id')
        project = get_object_or_404(Project, id=project_id)
        return ProjectMember.objects.filter(project=project)

    def get_serializer_class(self):
        if self.request.method == 'POST':
            return ProjectMemberCreateSerializer
        return ProjectMemberSerializer

    def get_serializer_context(self):
        context = super().get_serializer_context()
        context['project'] = get_object_or_404(Project, id=self.kwargs.get('project_id'))
        return context

    def perform_create(self, serializer):
        with transaction.atomic():
            target = _lock_admin_and_target(self.request, serializer.validated_data['user'].pk)
            project = get_object_or_404(
                Project.objects.select_for_update(), pk=self.kwargs.get('project_id'),
            )
            if ProjectMember.objects.filter(project=project, user=target).exists():
                raise ValidationError({'user': '该用户已经是项目成员'})
            serializer.save(project=project, user=target)


class ProjectMemberDetailView(generics.RetrieveUpdateDestroyAPIView):
    """项目成员详情视图"""
    serializer_class = ProjectMemberSerializer
    permission_classes = [permissions.IsAuthenticated, IsPlatformAdmin]

    def get_queryset(self):
        project_id = self.kwargs.get('project_id')
        return ProjectMember.objects.filter(project_id=project_id)

    def perform_update(self, serializer):
        with transaction.atomic():
            target = _lock_admin_and_target(self.request, serializer.instance.user_id)
            project = get_object_or_404(
                Project.objects.select_for_update(), pk=self.kwargs.get('project_id'),
            )
            serializer.instance = get_object_or_404(
                ProjectMember.objects.select_for_update(),
                pk=serializer.instance.pk, project=project, user=target,
            )
            serializer.save()

    def perform_destroy(self, instance):
        with transaction.atomic():
            target = _lock_admin_and_target(self.request, instance.user_id)
            project = get_object_or_404(
                Project.objects.select_for_update(), pk=self.kwargs.get('project_id'),
            )
            member = get_object_or_404(
                ProjectMember.objects.select_for_update(),
                pk=instance.pk, project=project, user=target,
            )
            member.delete()
