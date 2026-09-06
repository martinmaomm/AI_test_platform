from django.urls import path

from . import views


app_name = 'project_knowledge'

urlpatterns = [
    path('options/', views.KnowledgeOptionsView.as_view(), name='options'),
    path('documents/', views.DocumentListView.as_view(), name='document-list'),
    path('documents/<int:document_id>/', views.DocumentDetailView.as_view(), name='document-detail'),
    path('documents/<int:document_id>/prepare/', views.DocumentPrepareView.as_view(), name='document-prepare'),
    path('documents/<int:document_id>/sections/', views.DocumentSectionsView.as_view(), name='document-sections'),
    path('sources/<uuid:chunk_id>/', views.SourceDetailView.as_view(), name='source-detail'),
    path('tasks/', views.TaskListView.as_view(), name='task-list'),
    path('tasks/<uuid:task_id>/', views.TaskDetailView.as_view(), name='task-detail'),
    path('tasks/<uuid:task_id>/cancel/', views.TaskCancelView.as_view(), name='task-cancel'),
    path('tasks/<uuid:task_id>/retry-cleanup/', views.CleanupTaskRetryView.as_view(), name='task-retry-cleanup'),
    path('case-generations/', views.CaseGenerationView.as_view(), name='case-generation'),
    path('case-generations/<uuid:task_id>/save/', views.CaseGenerationSaveView.as_view(), name='case-generation-save'),
    path('manual-cases/', views.ManualCaseListView.as_view(), name='manual-case-list'),
    path('manual-cases/export/', views.ManualCaseExportView.as_view(), name='manual-case-export'),
    path('manual-cases/<uuid:case_id>/', views.ManualCaseDetailView.as_view(), name='manual-case-detail'),
    path('conversations/', views.ConversationListView.as_view(), name='conversation-list'),
    path('conversations/<uuid:conversation_id>/messages/', views.MessageListView.as_view(), name='message-list'),
]
