"""
URL configuration for aits_backend project.
"""
from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from rest_framework.routers import DefaultRouter
from django.conf.urls.static import static
from scheduled_tasks import views as views_scheduled_tasks
from rest_framework_simplejwt.views import (
    TokenObtainPairView,
    TokenRefreshView,
    TokenVerifyView,
)

# 创建路由器
router = DefaultRouter()

urlpatterns = [
    path('', include(router.urls)),
    path('admin/', admin.site.urls),
    path('api/v1/users/', include('users.urls')),
    # JWT认证路由
    path('api/v1/auth/token/', TokenObtainPairView.as_view(), name='token_obtain_pair'),
    path('api/v1/auth/token/refresh/', TokenRefreshView.as_view(), name='token_refresh'),
    path('api/v1/auth/token/verify/', TokenVerifyView.as_view(), name='token_verify'),
    
    # API路由
    # 项目模块（内部继续分发子路由）
    path('api/v1/projects/', include('projects.urls')),
    
    path('api/v1/projects/<int:project_id>/api-testing/', include('api_testing.urls')),
    path('api/v1/ai-core/', include('ai_core.urls')),
    path('api/v1/projects/<int:project_id>/web-testing/', include('web_testing.urls')),
    path('api/v1/projects/<int:project_id>/scheduled-tasks/', include('scheduled_tasks.urls')),
    path('api/v1/projects/<int:project_id>/notification-receivers/', include('notifications.project_urls')),
    path('api/v1/reports/execution/<int:pk>/', views_scheduled_tasks.ReportExecutionLogPublicView.as_view(), name='report-execution-detail'),
    path('api/v1/reports/detail/<int:pk>/', views_scheduled_tasks.ReportExecutionLogPublicView.as_view(), name='report-detail'),
    path('api/v1/notifications/', include('notifications.urls')),
    path('api/v1/util/', include('common.urls'))
]

# 托管媒体文件（Allure 报告等，需可读权限供 iframe 加载）
urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
# 开发环境下额外静态与报告目录
if settings.DEBUG:
    urlpatterns += static(settings.STATIC_URL, document_root=settings.STATIC_ROOT)
    urlpatterns += static('playwright-reports/', document_root=settings.PLAYWRIGHT_REPORTS_ROOT)
    urlpatterns += static('httprunner-reports/', document_root=settings.HTTPRUNNER_REPORTS_ROOT)
