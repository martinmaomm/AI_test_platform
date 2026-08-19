"""
Scheduled Tasks Views
定时任务中心API视图
"""
import logging
from rest_framework import generics, status, permissions
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.permissions import AllowAny
from django.shortcuts import get_object_or_404
from django.db.models import Q
from django.utils import timezone
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework.filters import SearchFilter, OrderingFilter

from common.api import response
from .models import ScheduledTask, TaskExecutionLog
from .serializers import (
    ScheduledTaskSerializer, ScheduledTaskCreateSerializer, TaskExecutionLogSerializer,
    TaskExecutionLogListSerializer, TaskRunSerializer, TaskStatusUpdateSerializer,
    SuiteChoiceSerializer
)
from .tasks import run_task_manually, calculate_next_run_time

logger = logging.getLogger(__name__)


class ScheduledTaskListCreateView(generics.ListCreateAPIView):
    """定时任务列表和创建视图"""
    
    permission_classes = [permissions.IsAuthenticated]
    filter_backends = [DjangoFilterBackend, SearchFilter, OrderingFilter]
    filterset_fields = ['suite_type', 'status', 'environment']
    search_fields = ['name', 'description']
    ordering_fields = ['created_at', 'updated_at', 'last_run_time', 'next_run_time']
    ordering = ['-created_at']
    
    def get_queryset(self):
        """获取用户有权限的任务"""
        project_id = self.kwargs['project_id']
        user = self.request.user
        qs = ScheduledTask.objects.filter(project_id=project_id)
        if not user.is_superuser:
            qs = qs.filter(Q(user=user) | Q(project__members__user=user)).distinct()
        return qs.prefetch_related('notice_targets')
    
    def get_serializer_class(self):
        """根据请求方法选择序列化器"""
        if self.request.method == 'POST':
            return ScheduledTaskCreateSerializer
        return ScheduledTaskSerializer
    
    def perform_create(self, serializer):
        """创建任务时设置用户和项目"""
        project_id = self.kwargs['project_id']
        serializer.save(
            user=self.request.user,
            project_id=project_id
        )


class ScheduledTaskDetailView(generics.RetrieveUpdateDestroyAPIView):
    """定时任务详情视图"""
    
    permission_classes = [permissions.IsAuthenticated]
    
    def get_queryset(self):
        """获取用户有权限的任务"""
        project_id = self.kwargs['project_id']
        user = self.request.user
        qs = ScheduledTask.objects.filter(project_id=project_id)
        if not user.is_superuser:
            qs = qs.filter(Q(user=user) | Q(project__members__user=user)).distinct()
        return qs.prefetch_related('notice_targets')
    
    def get_serializer_class(self):
        """根据请求方法选择序列化器"""
        if self.request.method in ['PUT', 'PATCH']:
            return ScheduledTaskCreateSerializer
        return ScheduledTaskSerializer


class TaskRunView(APIView):
    """手动执行任务视图"""
    
    permission_classes = [permissions.IsAuthenticated]
    
    def post(self, request, project_id, pk):
        """手动执行定时任务"""
        try:
            task = get_object_or_404(
                ScheduledTask.objects.filter(
                    project_id=project_id
                ).filter(
                    Q(user=request.user) | Q(project__members__user=request.user)
                ).distinct(),
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
            
            # 先创建执行日志，便于前端轮询与跳转报告
            execution_log = TaskExecutionLog.objects.create(
                task=task,
                start_time=timezone.now(),
                status='running'
            )
            result = run_task_manually.delay(task.id, execution_log.id)
            return response(
                kind="success",
                data={
                    'task_id': task.id,
                    'execution_id': execution_log.id,
                    'celery_task_id': result.id,
                    'task_name': task.name
                },
                message="任务执行已启动"
            )
            
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
        try:
            task = get_object_or_404(
                ScheduledTask.objects.filter(
                    project_id=project_id
                ).filter(
                    Q(user=request.user) | Q(project__members__user=request.user)
                ).distinct(),
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
            
            # 更新任务状态
            task.status = serializer.validated_data['status']
            task.save()
            
            # 计算下次执行时间
            if task.status == 'active':
                next_run_time = calculate_next_run_time(task.cron_expression)
                if next_run_time:
                    task.next_run_time = next_run_time
                    task.save(update_fields=['next_run_time'])
            
            return response(
                kind="success",
                data=ScheduledTaskSerializer(task).data,
                message=f"任务状态已更新为: {task.get_status_display()}"
            )
            
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
        user = self.request.user
        queryset = TaskExecutionLog.objects.select_related('task')
        
        if user.is_superuser:
            queryset = queryset.filter(task__project_id=project_id)
        else:
            queryset = queryset.filter(
                task__project_id=project_id
            ).filter(
                task__in=ScheduledTask.objects.filter(
                    Q(user=user) | Q(project__members__user=user)
                ).distinct()
            )
        
        # 处理 suite_type 筛选
        suite_type = self.request.GET.get('suite_type')
        if suite_type in ['web', 'api', 'app']:
            queryset = queryset.filter(task__suite_type=suite_type)
        
        # 处理搜索
        search = self.request.GET.get('search')
        if search:
            queryset = queryset.filter(task__name__icontains=search)
        
        return queryset
    
    def get_serializer_class(self):
        return TaskExecutionLogListSerializer
    
    def list(self, request, *args, **kwargs):
        """重写list方法以支持分页和自定义响应格式"""
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
            message="获取任务执行日志列表成功"
        )


class TaskExecutionLogDetailView(generics.RetrieveDestroyAPIView):
    """任务执行日志详情视图（支持查看与删除）"""
    
    permission_classes = [permissions.IsAuthenticated]
    
    def get_queryset(self):
        """获取用户有权限的任务的执行日志"""
        project_id = self.kwargs['project_id']
        user = self.request.user
        if user.is_superuser:
            return TaskExecutionLog.objects.filter(task__project_id=project_id)
        return TaskExecutionLog.objects.filter(
            task__project_id=project_id
        ).filter(
            task__in=ScheduledTask.objects.filter(
                Q(user=user) | Q(project__members__user=user)
            ).distinct()
        )
    
    def get_serializer_class(self):
        return TaskExecutionLogSerializer


class ReportExecutionLogPublicView(generics.RetrieveAPIView):
    """报告页免密查看执行日志详情（AllowAny，用于企微/钉钉链接打开）"""
    permission_classes = [AllowAny]
    queryset = TaskExecutionLog.objects.all()
    serializer_class = TaskExecutionLogSerializer


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
        user = self.request.user
        
        # 验证用户是否有权限访问该任务
        if user.is_superuser:
            task = get_object_or_404(
                ScheduledTask.objects.filter(project_id=project_id),
                pk=task_id
            )
        else:
            task = get_object_or_404(
                ScheduledTask.objects.filter(
                    project_id=project_id
                ).filter(
                    Q(user=user) | Q(project__members__user=user)
                ).distinct(),
                pk=task_id
            )
        
        return TaskExecutionLog.objects.filter(task=task)
    
    def get_serializer_class(self):
        return TaskExecutionLogListSerializer


class SuiteChoicesView(APIView):
    """获取测试套件选择列表"""
    
    permission_classes = [permissions.IsAuthenticated]
    
    def get(self, request, project_id):
        """获取指定类型的测试套件列表"""
        suite_type = request.query_params.get('suite_type')
        
        if not suite_type:
            return response(
                kind="error",
                message="缺少必要参数: suite_type",
                status_code=status.HTTP_400_BAD_REQUEST
            )
        
        try:
            suites = []
            
            if suite_type == 'web':
                from web_testing.models import WebUITestSuite
                queryset = WebUITestSuite.objects.filter(project_id=project_id)
                suites = [
                    {
                        'id': suite.id,
                        'name': suite.name,
                        'description': suite.description,
                        'total_cases': suite.test_cases.count()
                    }
                    for suite in queryset
                ]
                
            elif suite_type == 'api':
                from api_testing.models import APITestSuite
                queryset = APITestSuite.objects.filter(project_id=project_id)
                suites = [
                    {
                        'id': suite.id,
                        'name': suite.name,
                        'description': suite.description,
                        'total_cases': suite.test_cases.count()
                    }
                    for suite in queryset
                ]
                
            elif suite_type == 'app':
                # App测试套件模型待实现
                suites = []
            
            serializer = SuiteChoiceSerializer(suites, many=True)
            
            return response(
                kind="success",
                data=serializer.data,
                message="获取测试套件列表成功"
            )
            
        except Exception as e:
            logger.error(f"获取测试套件列表时发生错误: {str(e)}", exc_info=True)
            return response(
                kind="error",
                message=f"获取测试套件列表时发生错误: {str(e)}",
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


@api_view(['GET'])
@permission_classes([permissions.IsAuthenticated])
def task_statistics(request, project_id):
    """获取定时任务统计信息"""
    try:
        user = request.user
        
        # 获取用户有权限的任务
        if user.is_superuser:
            tasks = ScheduledTask.objects.filter(project_id=project_id)
        else:
            tasks = ScheduledTask.objects.filter(
                project_id=project_id
            ).filter(
                Q(user=user) | Q(project__members__user=user)
            ).distinct()
        
        # 统计信息
        total_tasks = tasks.count()
        active_tasks = tasks.filter(status='active').count()
        paused_tasks = tasks.filter(status='paused').count()
        
        # 按类型统计
        web_tasks = tasks.filter(suite_type='web').count()
        api_tasks = tasks.filter(suite_type='api').count()
        app_tasks = tasks.filter(suite_type='app').count()
        
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
            'app_tasks': app_tasks,
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
