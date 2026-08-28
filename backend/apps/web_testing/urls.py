"""
Web Testing URLs
统一管理Web UI自动化测试的URL配置
"""
from django.urls import path, include
from rest_framework.routers import DefaultRouter
from . import views

app_name = 'web_testing'

router = DefaultRouter()
router.register(r'pages', views.WebPageViewSet, basename='web-page')
router.register(r'elements', views.WebElementViewSet, basename='web-element')

urlpatterns = [

    # ============ WebUI测试脚本管理 ============
    path('scripts/', views.CreateWebUITestScriptView.as_view(), name='create-webui-test-script'),
    path('scripts/save/', views.SaveGeneratedWebUITestScriptView.as_view(), name='save-generated-webui-test-script'),
    path('scripts/stop/', views.StopWebUITestScriptView.as_view(), name='stop-webui-test-script'),
    path('scripts/from-testcase/', views.CreateWebUITestScriptFromTestCaseView.as_view(), name='create-webui-test-script-from-testcase'),
    path('scripts/from-testcase/stop/', views.StopWebUITestScriptFromTestCaseView.as_view(), name='stop-webui-test-script-from-testcase'),
    
    # ============ MidScene脚本管理 ============
    path('midscene/generate/', views.GenerateMidSceneScriptView.as_view(), name='generate-midscene-script'),
    path('midscene/scripts/', views.list_midscene_scripts, name='list-midscene-scripts'),
    path('midscene/scripts/<int:script_id>/', views.get_midscene_script, name='get-midscene-script'),
    
    # 统一的任务状态查询接口
    path('task-status/<str:task_id>/', views.TaskStatusView.as_view(), name='task-status'),
    
    # ============ Web UI测试用例生成 ============
    path('test-cases/generate/', views.GenerateWebUITestCasesView.as_view(), name='generate-webui-test-cases'),

    # ============ Web UI测试模块树管理 ============
    path('modules/', views.WebUITestModuleListCreateView.as_view(), name='webui-test-modules-list-create'),
    path('modules/<int:pk>/', views.WebUITestModuleRetrieveUpdateDestroyView.as_view(), name='webui-test-module-detail'),
    
    # ============ Web UI测试用例管理 ============
    path('test-cases/batch-delete/', views.WebUITestCaseBatchDeleteView.as_view(), name='test-cases-batch-delete'),
    path('test-cases/batch-update/', views.WebUITestCaseBatchUpdateView.as_view(), name='test-cases-batch-update'),
    path('test-cases/batch-generate-code/', views.WebUITestCaseBatchGenerateCodeView.as_view(), name='test-cases-batch-generate-code'),
    path('test-cases/', views.WebUITestCaseListCreateView.as_view(), name='webui-test-cases-list-create'),
    path('test-cases/<int:pk>/', views.WebUITestCaseRetrieveUpdateDestroyView.as_view(), name='webui-test-case-detail'),
    path('test-cases/<int:pk>/generate-code/', views.WebUITestCaseGenerateCodeView.as_view(), name='webui-test-case-generate-code'),
    path('test-cases/<int:pk>/save-script/', views.WebUITestCaseSaveScriptView.as_view(), name='webui-test-case-save-script'),
    path('test-cases/<int:pk>/execute/', views.ExecuteWebUITestCaseView.as_view(), name='execute-webui-test-case'),

    # ============ Web UI测试套件管理 ============
    path('test-suites/', views.WebUITestSuiteListCreateView.as_view(), name='webui-test-suites-list-create'),
    path('test-suites/<int:pk>/', views.WebUITestSuiteRetrieveUpdateDestroyView.as_view(), name='webui-test-suite-detail'),
    path('test-suites/<int:pk>/add-test-cases/', views.WebUITestSuiteAddTestCaseView.as_view(), name='webui-test-suite-add-test-cases'),
    path('test-suites/<int:pk>/remove-test-case/<int:test_case_id>/', views.WebUITestSuiteRemoveTestCaseView.as_view(), name='webui-test-suite-remove-test-case'),
    path('test-suites/<int:pk>/execute/', views.ExecuteWebUITestSuiteView.as_view(), name='execute-webui-test-suite'),
    
    # ============ 执行记录管理 ============
    path('executions/', views.TestExecutionListView.as_view(), name='test-executions-list'),
    path('executions/case/<int:pk>/', views.TestCaseExecutionDetailView.as_view(), name='test-case-execution-detail'),
    path('executions/suite/<int:pk>/', views.TestSuiteExecutionDetailView.as_view(), name='test-suite-execution-detail'),
    path('executions/<int:pk>/cases/', views.TestExecutionCasesView.as_view(), name='test-execution-cases'),
    path('executions/<int:pk>/delete/', views.TestExecutionDeleteView.as_view(), name='test-execution-delete'),
    
    # ============ 统计信息 ============
    path('execution-statistics/', views.get_webui_test_execution_statistics, name='get-webui-test-execution-statistics'),
    path('test-suite-statistics/', views.get_webui_test_suite_statistics, name='get-webui-test-suite-statistics'),

    # ============ POM 页面对象管理 ============
    path('dict/web-actions/', views.get_web_ui_actions, name='get_web_ui_actions'),
    path('extract-pom/', views.ExtractPomFromDocView.as_view(), name='extract_pom'),
    path('clear-assets/', views.clear_project_web_assets, name='clear_project_web_assets'),
    path('', include(router.urls)),
]
