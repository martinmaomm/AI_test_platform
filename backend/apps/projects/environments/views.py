from rest_framework import generics, permissions, status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from django.shortcuts import get_object_or_404
from django.db.models import Q
import json
import time

from ..models import Project, Environment
from ..serializers import EnvironmentSerializer, EnvironmentCreateSerializer
from common.api import response


SUPPORTED_PROJECT_ENVIRONMENT_TYPES = {'api', 'web', 'app'}


def get_project_environment_category(project):
    """环境类型由项目类型决定，不允许在同一项目中混用。"""

    if project.project_type not in SUPPORTED_PROJECT_ENVIRONMENT_TYPES:
        return None
    return project.project_type


class EnvironmentListView(generics.ListCreateAPIView):
    """环境列表和创建视图"""
    serializer_class = EnvironmentSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        user = self.request.user
        # 使用URL路径参数获取项目ID
        project_id = self.kwargs.get('project_id')
        if not project_id:
            return Environment.objects.none()
        
        project = get_object_or_404(Project, id=project_id)
        # 检查用户是否有权限管理环境
        if not project.members.filter(user=user, can_edit=True).exists():
            return Environment.objects.none()
        
        category = get_project_environment_category(project)
        if category is None:
            return Environment.objects.none()
        requested_category = self.request.query_params.get('category')
        if requested_category and requested_category != category:
            return Environment.objects.none()
        queryset = Environment.objects.filter(project=project, category=category)
        
        return queryset

    def get_serializer_class(self):
        if self.request.method == 'POST':
            return EnvironmentCreateSerializer
        return EnvironmentSerializer

    def perform_create(self, serializer):
        # 使用URL路径参数获取项目ID
        project_id = self.kwargs.get('project_id')
        if not project_id:
            raise ValueError("未提供项目ID")
        
        project = get_object_or_404(Project, id=project_id)
        # 检查用户是否有权限管理环境
        if not project.members.filter(user=self.request.user, can_edit=True).exists():
            raise ValueError("没有权限管理环境")
        
        expected_category = get_project_environment_category(project)
        if expected_category is None:
            raise ValidationError({'category': '当前项目类型暂不支持环境配置'})
        if serializer.validated_data.get('category') != expected_category:
            raise ValidationError({'category': '环境类型必须与项目类型一致'})
        serializer.save(project=project, category=expected_category)

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
            message="获取环境列表成功"
        )


class EnvironmentDetailView(generics.RetrieveUpdateDestroyAPIView):
    """环境详情视图"""
    serializer_class = EnvironmentSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        user = self.request.user
        # 使用URL路径参数获取项目ID
        project_id = self.kwargs.get('project_id')
        if not project_id:
            return Environment.objects.none()
        
        project = get_object_or_404(Project, id=project_id)
        # 检查用户是否有权限管理环境
        if not project.members.filter(user=user, can_edit=True).exists():
            return Environment.objects.none()
        category = get_project_environment_category(project)
        if category is None:
            return Environment.objects.none()
        return Environment.objects.filter(project=project, category=category)

    def update(self, request, *args, **kwargs):
        return super().update(request, *args, **kwargs)

    def perform_update(self, serializer):
        project_id = self.kwargs.get('project_id')
        project = get_object_or_404(Project, id=project_id)
        expected_category = get_project_environment_category(project)
        if expected_category is None:
            raise ValidationError({'category': '当前项目类型暂不支持环境配置'})
        requested_category = serializer.validated_data.get('category', serializer.instance.category)
        if requested_category != expected_category:
            raise ValidationError({'category': '环境类型必须与项目类型一致'})
        serializer.save(category=expected_category)
