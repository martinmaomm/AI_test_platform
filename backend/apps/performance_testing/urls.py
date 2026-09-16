from django.urls import path

from .views import (
    NodeDetailView, NodeEnrollmentView, NodeListCreateView, NodeRevokeView,
    PerformanceConfigView, PlanDetailView, PlanListCreateView,
    TargetDetailView, TargetListCreateView,
)


app_name = 'performance_testing'

urlpatterns = [
    path('config/', PerformanceConfigView.as_view(), name='config'),
    path('targets/', TargetListCreateView.as_view(), name='target-list'),
    path('targets/<int:pk>/', TargetDetailView.as_view(), name='target-detail'),
    path('plans/', PlanListCreateView.as_view(), name='plan-list'),
    path('plans/<int:pk>/', PlanDetailView.as_view(), name='plan-detail'),
    path('nodes/', NodeListCreateView.as_view(), name='node-list'),
    path('nodes/<uuid:node_id>/', NodeDetailView.as_view(), name='node-detail'),
    path('nodes/<uuid:node_id>/enrollment/', NodeEnrollmentView.as_view(), name='node-enrollment'),
    path('nodes/<uuid:node_id>/revoke/', NodeRevokeView.as_view(), name='node-revoke'),
]
