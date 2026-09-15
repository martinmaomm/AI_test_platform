import json
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from ai_core.models import LLMConfiguration, MCPConfiguration, ModelType
from ai_core.webui_playwright_agent import WebUIPlaywrightAgent
from projects.models import Project, ProjectMember
from web_testing.generation_preflight import (
    resolve_active_playwright_mcp_config,
    run_safety_preflight,
)
from web_testing.models import WebUITestCase
from web_testing.script_assistant import case_edit_version
from web_testing.script_assistant_views import ScriptAssistantListCreateView, _model
from web_testing.serializers import WebUIScriptGenerationCreateSerializer


SCRIPT = """from playwright.async_api import expect

async def run(page):
    await page.goto('https://app.example.test/')
    await expect(page).to_have_title('Home')
"""


class SharedAIConfigurationRuntimeTests(TestCase):
    def setUp(self):
        users = get_user_model()
        self.admin = users.objects.create_user(
            'shared-config-admin', email='shared-config-admin@example.test', is_staff=True,
        )
        self.member = users.objects.create_user(
            'shared-config-member', email='shared-config-member@example.test',
        )
        self.llm = LLMConfiguration.objects.create(
            model_type=ModelType.LLM,
            provider='openai',
            model_name='shared-runtime-model',
            is_active=True,
            created_by=self.admin,
        )
        self.mcp = MCPConfiguration.objects.create(
            raw_config=json.dumps({
                'mcpServers': {
                    'playwright': {
                        'command': 'npx',
                        'args': ['@executeautomation/playwright-mcp-server@1.0.0'],
                    },
                },
            }),
            is_active=True,
            created_by=self.admin,
        )
        self.project = Project.objects.create(
            name='shared-config-project',
            project_type='web',
            owner=self.member,
            created_by=self.member,
        )
        ProjectMember.objects.create(
            project=self.project,
            user=self.member,
            role='editor',
            can_edit=True,
            can_execute_tests=True,
        )

    def test_member_can_select_admin_created_llm_for_project_workflow(self):
        self.assertEqual(_model(object(), self.llm.id), self.llm)

    def test_member_task_can_resolve_global_playwright_mcp(self):
        selected = resolve_active_playwright_mcp_config(self.member.id)
        self.assertIsNotNone(selected)
        config_id, config = selected
        self.assertEqual(config_id, self.mcp.id)
        self.assertIn('playwright', config['mcpServers'])

    def test_singleton_enable_state_controls_resolution_for_both_users(self):
        for user in (self.admin, self.member):
            self.assertEqual(resolve_active_playwright_mcp_config(user.id)[0], self.mcp.id)
        self.mcp.is_active = False
        self.mcp.save(update_fields=['is_active'])
        self.assertIsNone(resolve_active_playwright_mcp_config(self.member.id))

    def test_tool_catalog_status_does_not_change_runtime_config_selection(self):
        self.mcp.tools_status = 'error'
        self.mcp.tools_error = '清单检测失败不代表正在运行的任务不可用'
        self.mcp.save(update_fields=['tools_status', 'tools_error'])
        self.assertEqual(resolve_active_playwright_mcp_config(self.member.id)[0], self.mcp.id)

    def test_member_exploration_runtime_loads_admin_created_mcp(self):
        agent = WebUIPlaywrightAgent.__new__(WebUIPlaywrightAgent)
        agent._send_node_start_notification = lambda *_args: None
        agent._send_websocket_message = lambda *_args: None

        result = agent._load_mcp_config_node({'user_id': self.member.id})

        self.assertEqual(result['current_step'], 'config_loaded')
        self.assertEqual(
            result['mcp_config']['mcpServers']['playwright']['command'],
            'npx',
        )

    def test_member_generation_validation_accepts_admin_created_model(self):
        serializer = WebUIScriptGenerationCreateSerializer(
            data={
                'description': '目标网址：https://app.example.test/\n检查首页。',
                'model_config_id': self.llm.id,
            },
            context={
                'project': self.project,
                'request': SimpleNamespace(user=self.member),
            },
        )
        self.assertTrue(serializer.is_valid(), serializer.errors)
        self.assertEqual(serializer.validated_data['model_config'], self.llm)

    def test_member_generation_preflight_uses_global_llm_and_mcp(self):
        result = run_safety_preflight(
            SimpleNamespace(
                model_info={'config_id': self.llm.id},
                user_id=self.member.id,
            ),
            {'allow_test_data_writes': False},
        )
        self.assertEqual(result.outcome, 'continue')
        self.assertEqual(result.mcp_config_id, self.mcp.id)

    def test_shared_model_does_not_bypass_project_membership(self):
        case = WebUITestCase.objects.create(
            title='shared-model-case',
            description='edit with shared model',
            project=self.project,
            user=self.member,
            test_script_content=SCRIPT,
            script_status='ready',
            script_version=1,
        )
        payload = {
            'mode': 'edit',
            'model_config_id': self.llm.id,
            'test_case_id': case.id,
            'expected_edit_version': case_edit_version(case),
            'script_content': SCRIPT,
            'description': case.description,
            'message': '调整标题断言',
        }

        request = APIRequestFactory().post('/assistant/', payload, format='json')
        force_authenticate(request, user=self.member)
        with patch('web_testing.script_assistant_views._dispatch'):
            accepted = ScriptAssistantListCreateView.as_view()(
                request, project_id=self.project.id,
            )
        self.assertEqual(accepted.status_code, 202, accepted.data)

        outsider = get_user_model().objects.create_user(
            'shared-config-outsider', email='shared-config-outsider@example.test',
        )
        request = APIRequestFactory().post('/assistant/', payload, format='json')
        force_authenticate(request, user=outsider)
        denied = ScriptAssistantListCreateView.as_view()(
            request, project_id=self.project.id,
        )
        self.assertEqual(denied.status_code, 404, denied.data)
