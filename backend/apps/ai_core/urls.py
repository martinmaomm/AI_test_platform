"""
AI核心功能URL配置
包括LLM管理、RAG功能等
"""
from django.urls import path
from . import views

app_name = 'ai_core'

urlpatterns = [
    # ============ RAG配置管理 ============
    path('rag-configs/', views.RAGConfigurationViewSet.as_view(), name='rag_configs'),
    path('rag-configs/<int:config_id>/', views.RAGConfigurationDetailView.as_view(), name='rag_config_detail'),
    path('rag-configs/<int:config_id>/<str:action>/', views.RAGConfigurationActionView.as_view(), name='rag_config_action'),
    path('rag-configs/test_connection/', views.RAGTestConnectionView.as_view(), name='rag_test_connection'),
    
    # ============ LLM配置管理 ============
    path('llm-configs/', views.LLMConfigurationViewSet.as_view(), name='llm_configs'),
    path('llm-configs/<int:config_id>/', views.LLMConfigurationDetailView.as_view(), name='llm_config_detail'),
    path('llm-configs/<int:config_id>/<str:action>/', views.LLMConfigurationActionView.as_view(), name='llm_config_action'),
    path('llm-configs/test_connection/', views.LLMTestConnectionView.as_view(), name='llm_test_connection'),
    path('llm-usage-logs/statistics/', views.LLMUsageStatisticsView.as_view(), name='llm_usage_statistics'),
    
    # ============ 视觉模型配置管理 ============
    path('vision-configs/test-connection/', views.VisionTestConnectionView.as_view(), name='vision_test_connection'),
    
    # ============ MCP配置管理 ============
    path('mcp-configs/', views.MCPConfigurationViewSet.as_view(), name='mcp_configs'),
    path('mcp-configs/<int:config_id>/', views.MCPConfigurationDetailView.as_view(), name='mcp_config_detail'),
    path('mcp-configs/<int:config_id>/<str:action>/', views.MCPConfigurationActionView.as_view(), name='mcp_config_action'),
    path('mcp-configs/test-connection/', views.MCPTestConnectionView.as_view(), name='mcp_test_connection'),
]
