from rest_framework import generics, permissions, status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response
from django.shortcuts import get_object_or_404
from django.db.models import Q
import json
import time

from ..models import Project, Environment
from ..serializers import EnvironmentSerializer, EnvironmentCreateSerializer
from common.api import response


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
        
        # 支持按类型过滤
        queryset = Environment.objects.filter(project=project)
        category = self.request.query_params.get('category')
        if category:
            queryset = queryset.filter(category=category)
        
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
        
        serializer.save(project=project)

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
        return Environment.objects.filter(project=project)

    def update(self, request, *args, **kwargs):
        return super().update(request, *args, **kwargs)