from django.urls import path

from .workspace_views import (
    APIWorkspaceCollectionView,
    APIWorkspaceDebugView,
    APIWorkspaceDetailView,
    APIWorkspaceMessagesView,
    APIWorkspacePythonView,
    APIWorkspaceSaveView,
)


urlpatterns = [
    path('workspaces/', APIWorkspaceCollectionView.as_view(), name='api-workspace-list'),
    path('workspaces/<int:workspace_id>/', APIWorkspaceDetailView.as_view(), name='api-workspace-detail'),
    path('workspaces/<int:workspace_id>/messages/', APIWorkspaceMessagesView.as_view(), name='api-workspace-messages'),
    path('workspaces/<int:workspace_id>/debug/', APIWorkspaceDebugView.as_view(), name='api-workspace-debug'),
    path('workspaces/<int:workspace_id>/save/', APIWorkspaceSaveView.as_view(), name='api-workspace-save'),
    path('workspaces/<int:workspace_id>/python/', APIWorkspacePythonView.as_view(), name='api-workspace-python'),
]
