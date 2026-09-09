"""Browser observations extend generation guidance without changing Swagger."""
import json

from django.test import SimpleTestCase

from .workspace_tasks import _generation_messages, _planner_messages


class BrowserDiscoveryPromptTests(SimpleTestCase):
    def messages(self, endpoints, mode='generate'):
        return _generation_messages(
            conversation=[{'role': 'user', 'content': '探索本轮物品新增、编辑、删除。'}],
            draft={}, endpoints=endpoints, mode=mode, failure_evidence={'error': 'fixture'},
        )

    def test_observation_rules_preserve_samples_without_inventing_schema(self):
        endpoint = {'id': 1, 'method': 'POST', 'path': '/items', 'document_context': {
            'browser_capture': {'observed_samples': [{'sequence': 1}, {'sequence': 8}]},
        }}
        system, user = self.messages([endpoint])
        self.assertIn('不是完整 OpenAPI schema', system.content)
        self.assertIn('slots.sources', system.content)
        self.assertIn('全新会话', system.content)
        self.assertIn('<redacted>', system.content)
        self.assertIn('name/value 数组', system.content)
        self.assertEqual(json.loads(user.content)['selected_endpoints'], [endpoint])

    def test_swagger_and_empty_scope_do_not_receive_capture_path_rules(self):
        for endpoints in ([], [{'id': 1, 'document_context': {'security': []}}], [{'id': 1, 'document_context': None}]):
            with self.subTest(endpoints=endpoints):
                self.assertNotIn('path_template.slots', self.messages(endpoints)[0].content)

    def test_repair_keeps_assertion_protection_with_capture_guidance(self):
        messages = self.messages([{'document_context': {'browser_capture': {}}}], mode='repair')
        self.assertIn('不得删除、放宽、跳过或伪造已有断言', messages[0].content)
        self.assertIn('slots.sources', messages[0].content)
        self.assertEqual(json.loads(messages[1].content)['failure_evidence'], {'error': 'fixture'})

    def test_planning_uses_observed_auth_without_inventing_requiredness(self):
        messages = _planner_messages(conversation=[], endpoints=[{'document_context': {'browser_capture': {}}}])
        self.assertIn('不证明接口所有情况都必须认证', messages[0].content)
        self.assertIn('每个场景均需重新登录', messages[0].content)
        swagger = _planner_messages(conversation=[], endpoints=[{'document_context': {}}])
        self.assertNotIn('observed_request.auth_hints', swagger[0].content)
