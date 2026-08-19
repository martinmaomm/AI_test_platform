from django.urls import path
from . import views

app_name = 'knowledge'

urlpatterns = [
    # 知识库文件管理
    path('', views.KnowledgeBaseFileListView.as_view(), name='knowledge_file_list'),
    path('<int:pk>/', views.KnowledgeBaseFileDetailView.as_view(), name='knowledge_file_detail'),
    path('<int:file_id>/reprocess/', views.reprocess_file, name='reprocess_file'),
    path('<int:file_id>/start-processing/', views.start_file_processing, name='start_file_processing'),
    path('task-status/<str:task_id>/', views.get_task_status, name='get_task_status'),
]
