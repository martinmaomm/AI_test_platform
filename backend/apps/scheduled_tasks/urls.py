"""
Scheduled Tasks URLs
定时任务中心URL配置
"""
from django.urls import path, include
from rest_framework.routers import DefaultRouter
from . import views

# 创建路由器
router = DefaultRouter()

# 注册视图集
router.register(r'tasks', views.ScheduledTaskListCreateView, basename='scheduled-tasks')
router.register(r'task-detail', views.ScheduledTaskDetailView, basename='scheduled-task-detail')

# URL模式
urlpatterns = [
    # 定时任务管理
    path('tasks/', views.ScheduledTaskListCreateView.as_view(), name='task-list-create'),
    path('tasks/<int:pk>/', views.ScheduledTaskDetailView.as_view(), name='task-detail'),
    path('tasks/<int:pk>/run/', views.TaskRunView.as_view(), name='task-run'),
    path('tasks/<int:pk>/status/', views.TaskStatusUpdateView.as_view(), name='task-status-update'),
    
    # 执行日志
    path('execution-logs/', views.TaskExecutionLogListView.as_view(), name='execution-log-list'),
    path('execution-logs/<int:pk>/', views.TaskExecutionLogDetailView.as_view(), name='execution-log-detail'),
    path('tasks/<int:task_id>/execution-logs/', views.TaskExecutionLogsByTaskView.as_view(), name='task-execution-logs'),
    
    # 辅助接口
    path('suite-choices/', views.SuiteChoicesView.as_view(), name='suite-choices'),
    path('statistics/', views.task_statistics, name='task-statistics'),
]

