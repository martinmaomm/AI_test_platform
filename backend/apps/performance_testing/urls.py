from django.urls import path

from .views import (
    NodeDetailView, NodeEnrollmentView, NodeInstallationView, NodeListCreateView, NodeRevokeView,
    PerformanceConfigView, PlanDetailView, PlanListCreateView,
    TargetDetailView, TargetListCreateView,
)
from .run_views import (
    PerformanceRunCreateView, PerformanceRunDetailView, PerformanceRunListView,
    PerformanceRunStopView,
)
from .discovery_views import (
    PerformanceDiscoveryCancelView,
    PerformanceDiscoveryCollectionView,
    PerformanceDiscoveryConfigView,
    PerformanceDiscoveryDetailView,
    PerformanceDiscoveryDraftView,
    PerformanceDiscoveryOriginView,
    PerformanceDiscoveryRecordsView,
    PerformanceDiscoveryRetryView,
)


app_name = 'performance_testing'

urlpatterns = [
    path('discovery/config/', PerformanceDiscoveryConfigView.as_view(), name='discovery-config'),
    path('discovery/tasks/', PerformanceDiscoveryCollectionView.as_view(), name='discovery-task-list'),
    path('discovery/tasks/<uuid:task_id>/', PerformanceDiscoveryDetailView.as_view(), name='discovery-task-detail'),
    path('discovery/tasks/<uuid:task_id>/records/', PerformanceDiscoveryRecordsView.as_view(), name='discovery-task-records'),
    path('discovery/tasks/<uuid:task_id>/cancel/', PerformanceDiscoveryCancelView.as_view(), name='discovery-task-cancel'),
    path('discovery/tasks/<uuid:task_id>/retry/', PerformanceDiscoveryRetryView.as_view(), name='discovery-task-retry'),
    path('discovery/tasks/<uuid:task_id>/origin/', PerformanceDiscoveryOriginView.as_view(), name='discovery-task-origin'),
    path('discovery/tasks/<uuid:task_id>/draft/', PerformanceDiscoveryDraftView.as_view(), name='discovery-task-draft'),
    path('config/', PerformanceConfigView.as_view(), name='config'),
    path('targets/', TargetListCreateView.as_view(), name='target-list'),
    path('targets/<int:pk>/', TargetDetailView.as_view(), name='target-detail'),
    path('plans/', PlanListCreateView.as_view(), name='plan-list'),
    path('plans/<int:pk>/runs/', PerformanceRunCreateView.as_view(), name='run-create'),
    path('plans/<int:pk>/', PlanDetailView.as_view(), name='plan-detail'),
    path('runs/', PerformanceRunListView.as_view(), name='run-list'),
    path('runs/<uuid:run_id>/stop/', PerformanceRunStopView.as_view(), name='run-stop'),
    path('runs/<uuid:run_id>/', PerformanceRunDetailView.as_view(), name='run-detail'),
    path('nodes/', NodeListCreateView.as_view(), name='node-list'),
    path('nodes/<uuid:node_id>/', NodeDetailView.as_view(), name='node-detail'),
    path('nodes/<uuid:node_id>/installation/', NodeInstallationView.as_view(), name='node-installation'),
    path('nodes/<uuid:node_id>/enrollment/', NodeEnrollmentView.as_view(), name='node-enrollment'),
    path('nodes/<uuid:node_id>/revoke/', NodeRevokeView.as_view(), name='node-revoke'),
]
