from django.urls import path
from . import views
from .views import (
    TaskStatusView,
    TestStatisticsView, EndpointTestCasesView, EndpointTestCasesOrderView,
    APIModuleListView, APIModuleOrderView, APIEndpointOrderView,
    DebugScenarioStepsView
)
from rest_framework import routers
from .workspace_urls import urlpatterns as workspace_urlpatterns
from .browser_discovery_views import (
    BrowserDiscoveryCancelView,
    BrowserDiscoveryCollectionView,
    BrowserDiscoveryConfigView,
    BrowserDiscoveryDetailView,
    BrowserDiscoveryHandoffView,
    BrowserDiscoveryOriginsView,
    BrowserDiscoveryRecordsView,
)

app_name = 'api_testing'

urlpatterns = [
    # Browser-captured API discovery. Raw JSONL never goes through MEDIA or a
    # public download route; records is a bounded redacted projection.
    path('browser-discoveries/config/', BrowserDiscoveryConfigView.as_view(), name='browser-discovery-config'),
    path('browser-discoveries/', BrowserDiscoveryCollectionView.as_view(), name='browser-discovery-list'),
    path('browser-discoveries/<uuid:task_id>/', BrowserDiscoveryDetailView.as_view(), name='browser-discovery-detail'),
    path('browser-discoveries/<uuid:task_id>/records/', BrowserDiscoveryRecordsView.as_view(), name='browser-discovery-records'),
    path('browser-discoveries/<uuid:task_id>/origins/', BrowserDiscoveryOriginsView.as_view(), name='browser-discovery-origins'),
    path('browser-discoveries/<uuid:task_id>/cancel/', BrowserDiscoveryCancelView.as_view(), name='browser-discovery-cancel'),
    path('browser-discoveries/<uuid:task_id>/handoff/', BrowserDiscoveryHandoffView.as_view(), name='browser-discovery-handoff'),
    # 模块管理（端点测试用例页面的模块排序）
    path('modules/', views.APIModuleListView.as_view(), name='api_module_list'),
    path('modules/order/', views.APIModuleOrderView.as_view(), name='api_module_order'),
    # API规范管理
    path('api-specs/', views.APISpecificationListView.as_view(), name='api_spec_list'),
    path('api-specs/<int:pk>/', views.APISpecificationRetrieveUpdateDestroyView.as_view(), name='api_spec_detail'),
    
    # API端点管理
    path('api-specs/<int:spec_id>/endpoints/', views.APIEndpointListView.as_view(), name='api_endpoint_list'),
    path('api-specs/<int:spec_id>/endpoints/<int:pk>/', views.APIEndpointDetailView.as_view(), name='api_endpoint_detail'),
    # 批量更新端点排序（同一 spec 下端点拖拽）
    path('api-specs/<int:spec_id>/endpoints/order/', views.APIEndpointOrderView.as_view(), name='api_endpoint_order'),
    # 获取端点的测试用例
    path('api-specs/<int:spec_id>/endpoints/<int:endpoint_id>/test-cases/', views.EndpointTestCasesView.as_view(), name='get_endpoint_test_cases'),
    # 批量更新端点测试用例排序（拖拽后持久化）
    path('api-specs/<int:spec_id>/endpoints/<int:endpoint_id>/test-cases/order/', views.EndpointTestCasesOrderView.as_view(), name='update_endpoint_test_cases_order'),

    # 统一的任务状态查询接口
    path('task-status/<str:task_id>/', TaskStatusView.as_view(), name='task-status'),

    # API测试用例管理 
    path('test-cases/', views.APITestCaseListCreateView.as_view(), name='api-test-cases-list-create'),
    path('test-cases/order/', views.ScenarioTestCasesOrderView.as_view(), name='api-test-cases-order'),
    path('test-cases/batch-delete/', views.APITestCaseBatchDeleteView.as_view(), name='api-test-cases-batch-delete'),
    path('test-cases/<int:pk>/', views.APITestCaseRetrieveUpdateDestroyView.as_view(), name='api-test-case-detail'),
    path('test-cases/<int:pk>/execute/', views.ExecuteAPITestCaseView.as_view(), name='execute-api-test-case'),
    
    # ============ API测试套件管理 ============
    path('test-suites/', views.APITestSuiteListCreateView.as_view(), name='api-test-suites-list-create'),
    path('test-suites/<int:pk>/', views.APITestSuiteRetrieveUpdateDestroyView.as_view(), name='api-test-suite-detail'),
    path('test-suites/<int:pk>/add-test-cases/', views.APITestSuiteAddTestCaseView.as_view(), name='api-test-suite-add-test-cases'),
    path('test-suites/<int:pk>/remove-test-case/<int:test_case_id>/', views.APITestSuiteRemoveTestCaseView.as_view(), name='api-test-suite-remove-test-case'),
    path('test-suites/<int:pk>/execute/', views.ExecuteAPITestSuiteView.as_view(), name='execute-api-test-suite'),
    
    # ============ 执行记录管理 ============
    path('executions/', views.APITestExecutionListView.as_view(), name='api-test-executions-list'),
    path('executions/case/<int:pk>/', views.APITestCaseExecutionDetailView.as_view(), name='api-test-case-execution-detail'),
    path('executions/suite/<int:pk>/', views.APITestSuiteExecutionDetailView.as_view(), name='api-test-suite-execution-detail'),
    path('executions/<int:pk>/cases/', views.APITestExecutionCasesView.as_view(), name='api-test-execution-cases'),
    path('executions/<int:pk>/report/', views.APITestExecutionReportView.as_view(), name='api-test-execution-report'),
    path('executions/<int:pk>/delete/', views.APITestExecutionDeleteView.as_view(), name='api-test-execution-delete'),
    
    # 测试统计
    path('statistics/', views.TestStatisticsView.as_view(), name='test_statistics'),

    # 场景调试（截取前 N 步同步执行）
    path('debug-scenario-steps/', DebugScenarioStepsView.as_view(), name='debug-scenario-steps'),
    
]

# Kept in a dedicated module so the durable workspace does not alter legacy views.
urlpatterns += workspace_urlpatterns
