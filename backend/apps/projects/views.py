from rest_framework import generics, permissions, status, viewsets
from rest_framework.decorators import api_view, permission_classes, action
from rest_framework.response import Response
from django.shortcuts import get_object_or_404
from django.db.models import Q
from django.core.paginator import Paginator
from django.core.files.storage import default_storage
import logging

from users.models import UserPreference
from .models import Project, ProjectMember
from .serializers import (
    ProjectSerializer, ProjectCreateSerializer, ProjectMemberSerializer,
    ProjectMemberCreateSerializer, ProjectDetailSerializer
)
from common.api import response

logger = logging.getLogger(__name__)


class ProjectViewSet(viewsets.ModelViewSet):
    """项目管理ViewSet"""
    permission_classes = [permissions.IsAuthenticated]
    
    def get_queryset(self):
        user = self.request.user
        queryset = Project.objects.filter(
            Q(members__user=user) | Q(created_by=user) | Q(owner=user)
        ).distinct()

        # 按项目类型过滤
        project_type = self.request.query_params.get('project_type', '')
        if project_type and project_type in ['api', 'web', 'app', 'perf']:
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
        elif self.action in ['retrieve', 'update', 'partial_update']:
            return ProjectDetailSerializer
        return ProjectSerializer

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user)

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
        user = request.user
        projects = Project.objects.filter(
            Q(members__user=user) | Q(created_by=user) | Q(owner=user)
        ).distinct()
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

        # 项目所有者不能离开项目
        if project.owner == request.user:
            return response(
                kind="error",
                message="项目所有者不能离开项目"
            )

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

        # 检查用户权限
        if not project.members.filter(user=request.user, can_view_reports=True).exists():
            return response(
                kind="error",
                message="您没有权限查看此项目的统计信息"
            )

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
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        project_id = self.kwargs.get('project_id')
        project = get_object_or_404(Project, id=project_id)
        # 检查用户是否有权限查看成员
        if not project.members.filter(user=self.request.user, can_view_reports=True).exists():
            return ProjectMember.objects.none()
        return ProjectMember.objects.filter(project=project)

    def get_serializer_class(self):
        if self.request.method == 'POST':
            return ProjectMemberCreateSerializer
        return ProjectMemberSerializer

    def get_serializer_context(self):
        context = super().get_serializer_context()
        context['project'] = get_object_or_404(Project, id=self.kwargs.get('project_id'))
        return context


class ProjectMemberDetailView(generics.RetrieveUpdateDestroyAPIView):
    """项目成员详情视图"""
    serializer_class = ProjectMemberSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        project_id = self.kwargs.get('project_id')
        project = get_object_or_404(Project, id=project_id)
        # 检查用户是否有权限管理成员
        if not project.members.filter(user=self.request.user, can_edit=True).exists():
            return ProjectMember.objects.none()
        return ProjectMember.objects.filter(project=project)