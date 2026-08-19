from django.urls import path, include
from rest_framework.routers import DefaultRouter
from . import views

app_name = 'projects'

# 创建路由器并注册项目ViewSet
router = DefaultRouter()
router.register(r'', views.ProjectViewSet, basename='project')

urlpatterns = [
    # 项目成员管理（更具体的路径放在前面）
    path('<int:project_id>/members/', views.ProjectMemberListView.as_view(), name='project_member_list'),
    path('<int:project_id>/members/<int:pk>/', views.ProjectMemberDetailView.as_view(), name='project_member_detail'),
    # 知识库
    path('<int:project_id>/knowledge-base/', include('projects.knowledge.urls')),
    # 环境管理
    path('<int:project_id>/environments/', include('projects.environments.urls')),
    # Dashboard 统计
    path('<int:project_id>/dashboard/', include('projects.dashboard.urls')),
    
    # 项目ViewSet路由（放在最后，作为默认路由）
    path('', include(router.urls)),
]
