from types import SimpleNamespace

from django.test import SimpleTestCase

from common import task
from common.task import celery_task_utils


class FakeTask:
    def __init__(self):
        self.request = SimpleNamespace(id='offline-task-id')
        self.states = []

    def update_state(self, *, state, meta):
        self.states.append({'state': state, 'meta': meta})


class CeleryTaskUtilsTests(SimpleTestCase):
    def test_progress_wrapper_completes_without_websocket_dependencies(self):
        fake_task = FakeTask()
        expected = {'status': 'completed', 'payload': {'source': 'offline'}}

        result = celery_task_utils.execute_async_task_with_progress(
            fake_task,
            '离线任务',
            lambda task: expected,
        )

        self.assertEqual(result, expected)
        self.assertEqual([item['state'] for item in fake_task.states], ['PROGRESS', 'PROGRESS', 'SUCCESS'])
        self.assertEqual(fake_task.states[-1]['meta']['result'], expected)

    def test_progress_wrapper_preserves_failed_result_and_records_failure(self):
        fake_task = FakeTask()
        expected = {'status': 'failed', 'error': 'expected offline failure'}

        result = celery_task_utils.execute_async_task_with_progress(
            fake_task,
            '离线任务',
            lambda task: expected,
        )

        self.assertEqual(result, expected)
        self.assertEqual([item['state'] for item in fake_task.states], ['PROGRESS', 'FAILURE'])
        self.assertEqual(fake_task.states[-1]['meta']['error'], expected['error'])

    def test_progress_wrapper_returns_normalized_error_after_executor_exception(self):
        fake_task = FakeTask()

        def raise_executor_error(task):
            raise ValueError('expected offline exception')

        result = celery_task_utils.execute_async_task_with_progress(
            fake_task,
            '离线任务',
            raise_executor_error,
        )

        self.assertEqual(result['status'], 'failed')
        self.assertIn('expected offline exception', result['error'])
        self.assertEqual([item['state'] for item in fake_task.states], ['PROGRESS', 'FAILURE'])
        self.assertEqual(fake_task.states[-1]['meta']['exc_type'], 'ValueError')

    def test_task_package_does_not_export_retired_websocket_wrapper(self):
        retired_names = (
            'execute_async_task_with_websocket',
            '_extract_user_id_from_task',
            '_send_websocket_task_completed',
            '_send_websocket_task_failed',
        )

        for name in retired_names:
            self.assertFalse(hasattr(celery_task_utils, name))
            self.assertFalse(hasattr(task, name))
            self.assertNotIn(name, task.__all__)
