from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import NotificationChannelViewSet, NotificationReceiverViewSet, EmailConfigViewSet

router = DefaultRouter()
router.register(r'channels', NotificationChannelViewSet, basename='notification-channel')
router.register(r'receivers', NotificationReceiverViewSet, basename='notification-receiver')
router.register(r'email-configs', EmailConfigViewSet, basename='email-config')

urlpatterns = [
    path('', include(router.urls)),
]
