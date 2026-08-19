"""
项目级通知接收对象 URL（嵌套在 /api/v1/projects/<project_id>/notification-receivers/ 下）
"""
from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import NotificationReceiverViewSet

router = DefaultRouter()
router.register(r'', NotificationReceiverViewSet, basename='project-notification-receiver')

urlpatterns = [
    path('', include(router.urls)),
]
