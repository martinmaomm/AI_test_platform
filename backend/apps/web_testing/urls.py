"""Web UI automation API routes."""
from django.urls import path

from . import views
from . import script_assistant_views

app_name = 'web_testing'

urlpatterns = [
    path('script-assistants/', script_assistant_views.ScriptAssistantListCreateView.as_view(), name='script-assistants'),
    path('script-assistants/<uuid:session_id>/', script_assistant_views.ScriptAssistantDetailView.as_view(), name='script-assistant-detail'),
    path('script-assistants/<uuid:session_id>/messages/', script_assistant_views.ScriptAssistantMessageView.as_view(), name='script-assistant-messages'),
    path('script-assistants/<uuid:session_id>/reset/', script_assistant_views.ScriptAssistantResetView.as_view(), name='script-assistant-reset'),
    path('script-assistants/<uuid:session_id>/verify/', script_assistant_views.ScriptAssistantVerifyView.as_view(), name='script-assistant-verify'),
    path('script-assistants/<uuid:session_id>/cancel/', script_assistant_views.ScriptAssistantCancelView.as_view(), name='script-assistant-cancel'),
    path('script-assistants/<uuid:session_id>/apply/', script_assistant_views.ScriptAssistantApplyView.as_view(), name='script-assistant-apply'),
    # AI + Playwright MCP script generation.
    path('script-generation-settings/', views.WebUIScriptGenerationSettingsView.as_view(), name='script-generation-settings'),
    path('script-generations/', views.WebUIScriptGenerationCreateView.as_view(), name='script-generation-create'),
    path('script-generations/<uuid:generation_id>/', views.WebUIScriptGenerationDetailView.as_view(), name='script-generation-detail'),
    path('script-generations/<uuid:generation_id>/failure-screenshot/', views.WebUIScriptGenerationFailureScreenshotView.as_view(), name='script-generation-failure-screenshot'),
    path('script-generations/<uuid:generation_id>/cancel/', views.WebUIScriptGenerationCancelView.as_view(), name='script-generation-cancel'),
    path('script-generations/<uuid:generation_id>/resolve/', views.WebUIScriptGenerationResolveView.as_view(), name='script-generation-resolve'),
    path('script-generations/<uuid:generation_id>/retry-generation/', views.WebUIScriptGenerationRetryView.as_view(), name='script-generation-retry-generation'),
    path('script-generations/<uuid:generation_id>/resume-exploration/', views.WebUIScriptGenerationResumeExplorationView.as_view(), name='script-generation-resume-exploration'),
    path('script-generations/<uuid:generation_id>/draft/', views.WebUIScriptGenerationDraftView.as_view(), name='script-generation-draft'),
    path('script-generations/<uuid:generation_id>/debug/', views.WebUIScriptGenerationDebugView.as_view(), name='script-generation-debug'),
    path('script-generations/<uuid:generation_id>/repair/', views.WebUIScriptGenerationRepairView.as_view(), name='script-generation-repair'),
    path('script-generations/<uuid:generation_id>/repair/apply/', views.WebUIScriptGenerationRepairApplyView.as_view(), name='script-generation-repair-apply'),
    path('script-generations/<uuid:generation_id>/repair/discard/', views.WebUIScriptGenerationRepairDiscardView.as_view(), name='script-generation-repair-discard'),
    path('script-generations/<uuid:generation_id>/save/', views.WebUIScriptGenerationSaveView.as_view(), name='script-generation-save'),

    path('task-status/<str:task_id>/', views.TaskStatusView.as_view(), name='task-status'),

    # Business classification only.
    path('modules/', views.WebUITestModuleListCreateView.as_view(), name='modules-list-create'),
    path('modules/<int:pk>/', views.WebUITestModuleRetrieveUpdateDestroyView.as_view(), name='module-detail'),

    # Independent executable Python scripts.
    path('test-cases/batch-delete/', views.WebUITestCaseBatchDeleteView.as_view(), name='test-cases-batch-delete'),
    path('test-cases/batch-update/', views.WebUITestCaseBatchUpdateView.as_view(), name='test-cases-batch-update'),
    path('test-cases/', views.WebUITestCaseListCreateView.as_view(), name='test-cases-list-create'),
    path('test-cases/<int:pk>/', views.WebUITestCaseRetrieveUpdateDestroyView.as_view(), name='test-case-detail'),
    path('test-cases/<int:pk>/execute/', views.ExecuteWebUITestCaseView.as_view(), name='execute-test-case'),

    # Ordered suites of independent scripts.
    path('test-suites/', views.WebUITestSuiteListCreateView.as_view(), name='test-suites-list-create'),
    path('test-suites/<int:pk>/', views.WebUITestSuiteRetrieveUpdateDestroyView.as_view(), name='test-suite-detail'),
    path('test-suites/<int:pk>/add-test-cases/', views.WebUITestSuiteAddTestCaseView.as_view(), name='test-suite-add-cases'),
    path('test-suites/<int:pk>/remove-test-case/<int:test_case_id>/', views.WebUITestSuiteRemoveTestCaseView.as_view(), name='test-suite-remove-case'),
    path('test-suites/<int:pk>/reorder/', views.WebUITestSuiteReorderView.as_view(), name='test-suite-reorder'),
    path('test-suites/<int:pk>/execute/', views.ExecuteWebUITestSuiteView.as_view(), name='execute-test-suite'),

    # Execution history and screenshots.
    path('executions/', views.TestExecutionListView.as_view(), name='executions-list'),
    path('executions/case/<int:pk>/', views.TestCaseExecutionDetailView.as_view(), name='case-execution-detail'),
    path('executions/suite/<int:pk>/', views.TestSuiteExecutionDetailView.as_view(), name='suite-execution-detail'),
    path('executions/<int:pk>/report/', views.TestExecutionReportView.as_view(), name='execution-report'),
    path('executions/<int:pk>/cases/', views.TestExecutionCasesView.as_view(), name='execution-cases'),
    path('executions/<int:pk>/cases/<int:case_pk>/screenshot/', views.TestExecutionScreenshotView.as_view(), name='suite-case-screenshot'),
    path('executions/<int:pk>/screenshot/', views.TestExecutionScreenshotView.as_view(), name='execution-screenshot'),
    path('executions/<int:pk>/delete/', views.TestExecutionDeleteView.as_view(), name='execution-delete'),
    path('execution-statistics/', views.get_webui_test_execution_statistics, name='execution-statistics'),
    path('test-suite-statistics/', views.get_webui_test_suite_statistics, name='test-suite-statistics'),
]
