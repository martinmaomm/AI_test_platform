"""
Scheduled Tasks Views
定时任务中心API视图
"""
import logging
from rest_framework import generics, status, permissions
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response
from rest_framework.views import APIView
from django.shortcuts import get_object_or_404
from django.http import Http404
from django.db.models import Q, Count
from django.db import transaction
from rest_framework.exceptions import APIException, ValidationError
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework.filters import SearchFilter, OrderingFilter

from common.api import response
from .models import ScheduledTask, TaskExecutionLog
from .serializers import (
    ScheduledTaskSerializer, ScheduledTaskCreateSerializer, TaskExecutionLogSerializer,
    TaskExecutionLogListSerializer, TaskRunSerializer, TaskStatusUpdateSerializer,
    SuiteChoiceSerializer
)
from .contracts import get_schedule_project, suite_model

logger = logging.getLogger(__name__)


class ScheduleProjectMixin:
    def get_schedule_project(self):
        capability = {'POST': 'edit', 'PUT': 'edit', 'PATCH': 'edit', 'DELETE': 'delete'}.get(self.request.method, 'read')
        return get_schedule_project(self.kwargs['project_id'], self.request.user, capability)

    def get_serializer_context(self):
        context = super().get_serializer_context()
        context['project'] = self.get_schedule_project()
        return context


class ScheduledTaskListCreateView(ScheduleProjectMixin, generics.ListCreateAPIView):
    """定时任务列表和创建视图"""
    
    permission_classes = [permissions.IsAuthenticated]
    filter_backends = [DjangoFilterBackend, SearchFilter, OrderingFilter]
    filterset_fields = ['status']
    search_fields = ['name', 'description']
    ordering_fields = ['created_at', 'updated_at', 'last_run_time', 'next_run_time']
    ordering = ['-created_at']
    
    def get_queryset(self):
        """获取用户有权限的任务"""
        return ScheduledTask.objects.filter(project=self.get_schedule_project()).prefetch_related('notice_targets')
    
    def get_serializer_class(self):
        """根据请求方法选择序列化器"""
        if self.request.method == 'POST':
            return ScheduledTaskCreateSerializer
        return ScheduledTaskSerializer
    
    def perform_create(self, serializer):
        """创建任务时设置用户和项目"""
        with transaction.atomic():
            serializer.save(user=self.request.user, project=self.get_schedule_project())


class ScheduledTaskDetailView(ScheduleProjectMixin, generics.RetrieveUpdateDestroyAPIView):
    """定时任务详情视图"""
    
    permission_classes = [permissions.IsAuthenticated]
    
    def get_queryset(self):
        """获取用户有权限的任务"""
        return ScheduledTask.objects.filter(project=self.get_schedule_project()).prefetch_related('notice_targets')
    
    def get_serializer_class(self):
        """根据请求方法选择序列化器"""
        if self.request.method in ['PUT', 'PATCH']:
            return ScheduledTaskCreateSerializer
        return ScheduledTaskSerializer

    def perform_update(self, serializer):
        with transaction.atomic():
            serializer.save()

    def perform_destroy(self, instance):
        with transaction.atomic():
            task = ScheduledTask.objects.select_for_update().get(pk=instance.pk)
            if task.execution_logs.filter(status__in=['pending', 'running']).exists():
                raise ValidationError('任务正在执行，结束后才能删除')
            task.delete()


class TaskRunView(APIView):
    """手动执行任务视图"""
    
    permission_classes = [permissions.IsAuthenticated]
    
    def post(self, request, project_id, pk):
        """手动执行定时任务"""
        get_schedule_project(project_id, request.user, 'execute')
        try:
            task = get_object_or_404(
                ScheduledTask.objects.filter(project_id=project_id),
                pk=pk
            )
            
            # 验证任务状态
            serializer = TaskRunSerializer(data={}, context={'task': task})
            if not serializer.is_valid():
                return response(
                    kind="validation_error",
                    errors=serializer.errors,
                    message="任务状态验证失败",
                    status_code=status.HTTP_400_BAD_REQUEST
                )
            
            from .scheduling import reserve_scheduled_run
            reservation = reserve_scheduled_run(task.id, manual=True)
            if not reservation.started:
                return response(kind='error', message=reservation.error or '无法启动任务',
                                status_code=409 if reservation.already_running else 400)
            return response(
                kind="success",
                data={
                    'task_id': task.id,
                    'execution_id': reservation.execution_log_id,
                    'task_name': task.name
                },
                message="任务已进入顺序执行队列"
            )
            
        except (APIException, Http404):
            raise
        except Exception as e:
            logger.error(f"手动执行任务时发生错误: {str(e)}", exc_info=True)
            return response(
                kind="error",
                message=f"执行任务时发生错误: {str(e)}",
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


class TaskStatusUpdateView(APIView):
    """任务状态更新视图"""
    
    permission_classes = [permissions.IsAuthenticated]
    
    def patch(self, request, project_id, pk):
        """更新任务状态"""
        get_schedule_project(project_id, request.user, 'edit')
        try:
            task = get_object_or_404(
                ScheduledTask.objects.filter(project_id=project_id),
                pk=pk
            )
            
            serializer = TaskStatusUpdateSerializer(data=request.data)
            if not serializer.is_valid():
                return response(
                    kind="validation_error",
                    errors=serializer.errors,
                    message="任务状态验证失败",
                    status_code=status.HTTP_400_BAD_REQUEST
                )
            
            desired = serializer.validated_data['status']
            if desired == 'active':
                get_schedule_project(project_id, request.user, 'execute')
                check = ScheduledTaskCreateSerializer(task, data={'status': desired}, partial=True, context={'project': task.project})
                check.is_valid(raise_exception=True)
            task.status = desired
            task.save(update_fields=['status', 'updated_at'])
            
            return response(
                kind="success",
                data=ScheduledTaskSerializer(task).data,
                message=f"任务状态已更新为: {task.get_status_display()}"
            )
            
        except (APIException, Http404):
            raise
        except Exception as e:
            logger.error(f"更新任务状态时发生错误: {str(e)}", exc_info=True)
            return response(
                kind="error",
                message=f"更新任务状态时发生错误: {str(e)}",
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


class TaskExecutionLogListView(generics.ListAPIView):
    """任务执行日志列表视图"""
    
    permission_classes = [permissions.IsAuthenticated]
    filter_backends = [DjangoFilterBackend, SearchFilter, OrderingFilter]
    filterset_fields = ['status']
    ordering_fields = ['start_time', 'end_time']
    ordering = ['-start_time']
    
    def get_queryset(self):
        """获取用户有权限的任务的执行日志"""
        project_id = self.kwargs['project_id']
        get_schedule_project(project_id, self.request.user, 'report')
        queryset = TaskExecutionLog.objects.select_related('task').filter(task__project_id=project_id)
        
        # 处理搜索
        search = self.request.GET.get('search')
        if search:
            queryset = queryset.filter(task__name__icontains=search)
        
        return queryset
    
    def get_serializer_class(self):
        return TaskExecutionLogListSerializer
    
    def list(self, request, *args, **kwargs):
        """重写list方法以支持分页和自定义响应格式"""
        queryset = self.filter_queryset(self.get_queryset())
        page = int(request.query_params.get("page", 1))
        page_size = int(request.query_params.get("page_size", 20))
        # 直接使用封装的分页函数
        return response(
            kind="paginated_queryset",
            data=queryset,
            page=page,
            page_size=page_size,
            serializer_class=self.get_serializer_class(),
            message="获取任务执行日志列表成功"
        )


class TaskExecutionLogDetailView(generics.RetrieveDestroyAPIView):
    """任务执行日志详情视图（支持查看与删除）"""
    
    permission_classes = [permissions.IsAuthenticated]
    
    def get_queryset(self):
        """获取用户有权限的任务的执行日志"""
        project_id = self.kwargs['project_id']
        capability = 'delete' if self.request.method == 'DELETE' else 'report'
        get_schedule_project(project_id, self.request.user, capability)
        return TaskExecutionLog.objects.filter(task__project_id=project_id)

    def perform_destroy(self, instance):
        if instance.status in {'pending', 'running'}:
            raise ValidationError('执行尚未结束，不能删除执行记录')
        instance.delete()
    
    def get_serializer_class(self):
        return TaskExecutionLogSerializer


class ReportExecutionLogPublicView(generics.RetrieveAPIView):
    """登录后的平台报告详情；保留类名以兼容既有全局 URL。"""
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = TaskExecutionLogSerializer

    def get_queryset(self):
        user = self.request.user
        queryset = TaskExecutionLog.objects.select_related('task', 'task__project')
        if user.is_superuser:
            return queryset
        return queryset.filter(
            Q(task__project__owner=user)
            | Q(task__project__created_by=user)
            | Q(task__project__members__user=user, task__project__members__can_view_reports=True)
        ).distinct()


class TaskExecutionLogsByTaskView(generics.ListAPIView):
    """获取指定任务的执行日志"""
    
    permission_classes = [permissions.IsAuthenticated]
    filter_backends = [DjangoFilterBackend, OrderingFilter]
    filterset_fields = ['status']
    ordering_fields = ['start_time', 'end_time']
    ordering = ['-start_time']
    
    def get_queryset(self):
        """获取指定任务的执行日志"""
        project_id = self.kwargs['project_id']
        task_id = self.kwargs['task_id']
        get_schedule_project(project_id, self.request.user, 'report')
        task = get_object_or_404(ScheduledTask.objects.filter(project_id=project_id), pk=task_id)
        
        return TaskExecutionLog.objects.filter(task=task)
    
    def get_serializer_class(self):
        return TaskExecutionLogListSerializer


class SuiteChoicesView(APIView):
    """获取测试套件选择列表"""
    
    permission_classes = [permissions.IsAuthenticated]
    
    def get(self, request, project_id):
        """获取指定类型的测试套件列表"""
        project = get_schedule_project(project_id, request.user)
        queryset = suite_model(project).objects.filter(project=project).annotate(case_count=Count('test_cases'))
        suites = []
        for suite in queryset:
            reason = '套件未启用' if suite.status != 'active' else '套件没有测试用例' if not suite.case_count else ''
            suites.append({
                'id': suite.id, 'name': suite.name, 'description': suite.description,
                'total_cases': suite.case_count, 'status': suite.status,
                'selectable': not reason, 'unavailable_reason': reason,
            })
        return response(kind='success', data=SuiteChoiceSerializer(suites, many=True).data,
                        message='获取当前项目测试套件成功')


@api_view(['GET'])
@permission_classes([permissions.IsAuthenticated])
def task_statistics(request, project_id):
    """获取定时任务统计信息"""
    get_schedule_project(project_id, request.user, 'report')
    try:
        tasks = ScheduledTask.objects.filter(project_id=project_id)
        
        # 统计信息
        total_tasks = tasks.count()
        active_tasks = tasks.filter(status='active').count()
        paused_tasks = tasks.filter(status='paused').count()
        
        # 按类型统计
        web_tasks = tasks.filter(suite_type='web').count()
        api_tasks = tasks.filter(suite_type='api').count()
        
        # 执行统计
        total_executions = TaskExecutionLog.objects.filter(
            task__in=tasks
        ).count()
        
        success_executions = TaskExecutionLog.objects.filter(
            task__in=tasks,
            status='success'
        ).count()
        
        failed_executions = TaskExecutionLog.objects.filter(
            task__in=tasks,
            status='failed'
        ).count()
        
        success_rate = round((success_executions / total_executions * 100), 2) if total_executions > 0 else 0
        
        statistics = {
            'total_tasks': total_tasks,
            'active_tasks': active_tasks,
            'paused_tasks': paused_tasks,
            'web_tasks': web_tasks,
            'api_tasks': api_tasks,
            'total_executions': total_executions,
            'success_executions': success_executions,
            'failed_executions': failed_executions,
            'success_rate': success_rate
        }
        
        return response(
            kind="success",
            data=statistics,
            message="获取统计信息成功"
        )
        
    except Exception as e:
        logger.error(f"获取统计信息时发生错误: {str(e)}", exc_info=True)
        return response(
            kind="error",
            message=f"获取统计信息时发生错误: {str(e)}",
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR
        )
