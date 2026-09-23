import importlib.util
import json
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch


class AnalysisStreamProgressTests(TestCase):
    def adapter(self, stream, timeout=300):
        manager = SimpleNamespace(config={'extra_config': {'timeout': timeout}},
                                  current_llm=SimpleNamespace(stream=stream))
        stub = SimpleNamespace(DEFAULT_LLM_TIMEOUT=300, get_llm_manager=lambda **kw: manager)
        path = Path(__file__).resolve().parents[2] / 'project_knowledge' / 'llm.py'
        spec = importlib.util.spec_from_file_location('_analysis_progress_test', path)
        module = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, {'ai_core.model_manager': stub}):
            spec.loader.exec_module(module)
        return module

    def test_metadata_chunks_do_not_count_as_body_and_600_allows_past_180(self):
        clock = [0.0]

        def stream(*args, **kwargs):
            self.assertEqual(kwargs['timeout'], 300)
            clock[0] = 181
            yield {'content': '', 'reasoning_content': 'private reasoning'}
            clock[0] = 183
            yield '结果'

        adapter = self.adapter(stream)
        events = []
        with patch.object(adapter.time, 'monotonic', side_effect=lambda: clock[0]):
            result = adapter.stream_call(1, [], None, lambda: None, lambda: 600-clock[0], events.append)
        self.assertEqual(result, '结果')
        waiting = [e for e in events if e['phase'] == 'waiting_response' and e['stream_chunks']]
        self.assertEqual(waiting[0]['received_chars'], 0)
        self.assertEqual(events[-1]['received_chars'], 2)
        self.assertEqual(events[-1]['stream_chunks'], 2)
        self.assertNotIn('private', json.dumps(events))

    def test_retry_reason_is_safe_and_shared_budget_not_reset(self):
        clock = [0.0]
        calls = []

        def stream(*args, **kwargs):
            calls.append(kwargs['timeout'])
            if len(calls) == 1:
                clock[0] = 250
                raise RuntimeError('connection reset with secret api key')
            yield 'ok'

        adapter = self.adapter(stream, timeout=600)
        events = []
        with patch.object(adapter.time, 'monotonic', side_effect=lambda: clock[0]), patch.object(
                adapter.time, 'sleep', side_effect=lambda seconds: clock.__setitem__(0, clock[0]+seconds)):
            result = adapter.stream_call(1, [], None, lambda: None, lambda: 600-clock[0], events.append)
        self.assertEqual(result, 'ok')
        self.assertEqual(calls, [600, 349])
        retries = [e for e in events if e['phase'] == 'retrying']
        self.assertEqual(retries[0]['retry_reason'], 'connection_error')
        self.assertEqual(events[-1]['attempt'], 2)
        self.assertNotIn('secret', json.dumps(events))

    def test_partial_stream_failure_never_replayed(self):
        calls = []

        def stream(*args, **kwargs):
            calls.append(True)
            yield 'partial'
            raise RuntimeError('connection reset')

        adapter = self.adapter(stream)
        events = []
        with self.assertRaises(adapter.StreamInterruptedError):
            adapter.stream_call(1, [], None, lambda: None, 600, events.append)
        self.assertEqual(len(calls), 1)
        self.assertFalse(any(e['phase'] == 'retrying' for e in events))

    def test_progress_observer_error_is_not_retried(self):
        adapter = self.adapter(lambda *args, **kwargs: iter(['body']))
        def event(_):
            raise PermissionError('connection permission changed')
        with self.assertRaises(PermissionError):
            adapter.stream_call(1, [], None, lambda: None, 600, event)
