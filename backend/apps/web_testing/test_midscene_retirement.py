"""Regression coverage for retiring the legacy MidScene runtime chain."""

from celery import current_app
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import SimpleTestCase, TestCase
from django.urls import Resolver404, resolve


class MidSceneRetirementTests(SimpleTestCase):
    def test_legacy_midscene_http_routes_no_longer_resolve(self):
        base = '/api/v1/projects/7/web-testing/'
        for suffix in ('midscene/generate/', 'midscene/scripts/', 'midscene/scripts/12/'):
            with self.subTest(path=suffix), self.assertRaises(Resolver404):
                resolve(base + suffix)

    def test_celery_registry_removes_midscene_and_keeps_execution_and_cancellation_tasks(self):
        import api_testing.tasks  # Ensure retained API tasks are registered in this process.
        import web_testing.tasks  # Ensure retained WebUI tasks are registered in this process.

        registered = current_app.tasks
        self.assertNotIn('web_testing.generate_midscene_script', registered)
        self.assertTrue({
            'web_testing.execute_webui_test_case',
            'web_testing.execute_webui_test_suite',
            'web_testing.cancel_task',
            'api_testing.tasks.execute_api_test_case_async',
            'api_testing.tasks.execute_api_test_suite_async',
        }.issubset(registered))

    def test_websocket_registry_removes_midscene_and_keeps_webui_channels(self):
        from api_testing.routing import websocket_urlpatterns
        from common.websocket.websocket_core import WebSocketConfig
        from common.websocket.websocket_handlers import WebSocketConsumerFactory

        patterns = {str(route.pattern) for route in websocket_urlpatterns}
        self.assertNotIn('ws/midscene_script_generation-streaming/$', patterns)
        self.assertNotIn('midscene_script_generation', WebSocketConfig.get_all_consumer_types())
        self.assertNotIn('midscene_script_generation', WebSocketConsumerFactory.get_all_consumer_types())
        self.assertTrue({
            'ws/webui_test_generation-streaming/$',
            'ws/webui_auto_test-streaming/$',
        }.issubset(patterns))


class RetainedWebUICancellationTests(TestCase):
    def test_cancellation_still_updates_the_webui_execution_only(self):
        from projects.models import Project
        from .models import MidSceneScript, WebUITestExecution
        from .tasks import cancel_task

        user = get_user_model().objects.create_user(
            username='cancellation-fixture', email='cancel@example.test',
        )
        project = Project.objects.create(name='WebUI', project_type='web', created_by=user)
        execution = WebUITestExecution.objects.create(
            exec_type='case', name='cancelled execution', project=project,
            executor=user, task_id='retirement-cancel-fixture', status='running',
        )
        other = WebUITestExecution.objects.create(
            exec_type='case', name='unrelated execution', project=project,
            executor=user, task_id='retirement-unrelated-fixture', status='running',
        )
        with patch('web_testing.tasks.AsyncResult') as result, patch.object(
            MidSceneScript.objects, 'filter', side_effect=AssertionError('retired table queried'),
        ):
            outcome = cancel_task.run(execution.task_id)
        self.assertTrue(outcome['success'], outcome)
        result.assert_called_once_with(execution.task_id)
        result.return_value.revoke.assert_called_once_with(terminate=True)
        self.assertTrue(cache.get(f'celery:cancel:{execution.task_id}'))
        execution.refresh_from_db()
        other.refresh_from_db()
        self.assertEqual(execution.status, 'stopped')
        self.assertIsNotNone(execution.end_time)
        self.assertEqual(other.status, 'running')
