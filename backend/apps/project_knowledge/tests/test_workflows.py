"""Offline contracts for knowledge workflows and their streaming adapter."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import django
from django.test import SimpleTestCase

django.setup()

from project_knowledge import llm, workflows


SOURCE_ID = '11111111-1111-1111-1111-111111111111'
RELATED_ID = '22222222-2222-2222-2222-222222222222'


class FakeContext:
    def __init__(self, replies, search_results=None, history=None):
        self.replies = list(replies)
        self.search_results = search_results or []
        self.history = history or []
        self.checks = 0
        self.checkpoints = []
        self.search_calls = []
        self.llm_messages = []

    def check_active(self):
        self.checks += 1

    def checkpoint(self, **kwargs):
        self.checkpoints.append(kwargs)

    def llm(self, messages, on_chunk=None):
        self.llm_messages.append(messages)
        reply = self.replies.pop(0)
        if on_chunk:
            on_chunk(reply)
        return reply

    def search(self, query, revision_ids=None):
        self.search_calls.append((query, revision_ids))
        return self.search_results


def source(source_id=SOURCE_ID, content='角色新增时必须填写名称'):
    return {
        'id': source_id, 'content': content, 'file_name': '需求.md', 'document_id': 7,
        'revision_id': 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa', 'heading': '角色管理', 'location': {'paragraph': 1},
    }


class GenerateCasesWorkflowTests(SimpleTestCase):
    def task(self):
        return SimpleNamespace(
            payload={'document_ids': [7], 'goal': '角色管理', 'module': '权限', 'text_only': True},
            snapshot=[{'document_id': 7, 'revision_id': 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa'}],
        )

    def test_processes_each_selected_chunk_and_drops_unprovided_citations(self):
        chunk = SimpleNamespace(
            id=SOURCE_ID, ordinal=1, content='角色新增时必须填写名称', heading='角色管理', location={'paragraph': 1},
            revision=SimpleNamespace(id='aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa', document=SimpleNamespace(id=7, name='需求.md')),
        )
        replies = [
            '{"test_points":[{"title":"名称必填","source_ids":["%s"]}]}' % SOURCE_ID,
            '{"cases":[{"title":"新增角色名称必填","steps":[{"action":"不填名称提交","expected":"提示必填"}],"sources":["%s","fake"],"test_point_ids":["tp-%s-1"]}]}' % (SOURCE_ID, SOURCE_ID),
        ]
        context = FakeContext(replies)
        with patch('project_knowledge.workflows._selected_chunks', return_value=[chunk]):
            result = workflows.generate_cases(self.task(), context)

        self.assertEqual(result['coverage']['processed'], 1)
        self.assertEqual(len(result['cases']), 1)
        self.assertEqual([item['id'] for item in result['cases'][0]['sources']], [SOURCE_ID])
        self.assertEqual(result['cases'][0]['review_status'], 'unreviewed')
        self.assertTrue(any('不存在' in warning for warning in result['warnings']))
        self.assertEqual(context.search_calls, [])
        self.assertGreaterEqual(len(context.checkpoints), 4)

    def test_invalid_case_batch_gets_one_repair_and_keeps_no_unreferenced_case(self):
        chunk = SimpleNamespace(
            id=SOURCE_ID, ordinal=1, content='规则', heading='', location={},
            revision=SimpleNamespace(id='aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa', document=SimpleNamespace(id=7, name='需求.md')),
        )
        context = FakeContext([
            '{"test_points":[{"title":"规则","source_ids":["%s"]}]}' % SOURCE_ID,
            'not json',
            '{"cases":[{"title":"可保存草稿","steps":[{"action":"操作","expected":"结果"}],"sources":["%s"],"test_point_ids":["tp-%s-1"]}]}' % (SOURCE_ID, SOURCE_ID),
        ])
        with patch('project_knowledge.workflows._selected_chunks', return_value=[chunk]):
            result = workflows.generate_cases(self.task(), context)

        self.assertEqual(len(result['cases']), 1)
        self.assertEqual(context.replies, [])

    def test_no_selected_chunks_is_explicit_partial_result(self):
        context = FakeContext([])
        with patch('project_knowledge.workflows._selected_chunks', return_value=[]):
            result = workflows.generate_cases(self.task(), context)

        self.assertTrue(result['partial'])
        self.assertEqual(result['coverage']['pending'], [])
        self.assertTrue(any('重新选择资料范围' in warning for warning in result['warnings']))

    def test_invalid_steps_and_overlong_fields_remain_editable_but_not_covered(self):
        chunk = SimpleNamespace(
            id=SOURCE_ID, ordinal=1, content='规则', heading='', location={},
            revision=SimpleNamespace(id='aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa', document=SimpleNamespace(id=7, name='需求.md')),
        )
        context = FakeContext([
            '{"test_points":[{"title":"规则","source_ids":null}]}',
            '{"cases":[{"title":"%s","module":"%s","test_type":"%s",'
            '"steps":[{"action":"有效操作","expected":"有效结果"},{"action":"缺少预期"}],'
            '"sources":["%s"],"test_point_ids":["tp-%s-1"]}]}' % ('标' * 256, '模' * 101, '类' * 51, SOURCE_ID, SOURCE_ID),
        ])
        with patch('project_knowledge.workflows._selected_chunks', return_value=[chunk]):
            result = workflows.generate_cases(self.task(), context)

        case = result['cases'][0]
        self.assertEqual((len(case['title']), len(case['module']), len(case['test_type'])), (255, 100, 50))
        self.assertTrue(any('步骤 2 缺少预期结果' in question for question in case['pending_questions']))
        self.assertEqual(result['coverage']['processed'], 0)
        self.assertEqual(result['coverage']['pending'], [SOURCE_ID])
        self.assertEqual(context.replies, [])  # valid portion must not trigger format repair

    def test_partial_test_point_coverage_keeps_valid_case_and_chunk_pending(self):
        chunk = SimpleNamespace(
            id=SOURCE_ID, ordinal=1, content='规则', heading='', location={},
            revision=SimpleNamespace(id='aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa', document=SimpleNamespace(id=7, name='需求.md')),
        )
        task = self.task()
        task.payload['text_only'] = False
        context = FakeContext([
            '{"test_points":[{"title":"名称必填","source_ids":["%s"]},{"title":"名称唯一","source_ids":["%s"]}]}' % (SOURCE_ID, SOURCE_ID),
            '{"cases":[{"title":"名称必填","steps":[{"action":"空名称提交","expected":"提示必填"}],"sources":["%s"],"test_point_ids":["tp-%s-1"]}]}' % (SOURCE_ID, SOURCE_ID),
        ])
        with patch('project_knowledge.workflows._selected_chunks', return_value=[chunk]):
            result = workflows.generate_cases(task, context)

        self.assertEqual(len(result['cases']), 1)
        self.assertEqual(result['coverage']['processed'], 0)
        self.assertEqual(result['coverage']['pending'], [SOURCE_ID])
        self.assertTrue(any('未被有效用例覆盖' in warning for warning in result['warnings']))
        self.assertTrue(any('名称唯一' in item for item in result['cases'][0]['pending_questions']))

    def test_interruption_before_third_chunk_keeps_remaining_chunks_pending(self):
        class TaskStopped(Exception):
            code = 'CANCELLED'

        def chunk(chunk_id, ordinal):
            return SimpleNamespace(
                id=chunk_id, ordinal=ordinal, content='规则', heading='', location={},
                revision=SimpleNamespace(id='aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa', document=SimpleNamespace(id=7, name='需求.md')),
            )

        chunks = [chunk(SOURCE_ID, 1), chunk(RELATED_ID, 2), chunk('33333333-3333-3333-3333-333333333333', 3)]

        class StoppingContext(FakeContext):
            def llm(self, messages, on_chunk=None):
                if not self.replies:
                    raise TaskStopped('stopped before third chunk')
                return super().llm(messages, on_chunk)

        replies = []
        for chunk_id in (SOURCE_ID, RELATED_ID):
            replies.extend([
                '{"test_points":[{"title":"规则","source_ids":["%s"]}]}' % chunk_id,
                '{"cases":[{"title":"用例-%s","steps":[{"action":"操作","expected":"结果"}],"sources":["%s"],"test_point_ids":["tp-%s-1"]}]}' % (chunk_id, chunk_id, chunk_id),
            ])
        context = StoppingContext(replies)
        with patch('project_knowledge.workflows._selected_chunks', return_value=chunks):
            with self.assertRaises(TaskStopped):
                workflows.generate_cases(self.task(), context)

        saved = context.checkpoints[-1]['result']
        self.assertEqual(saved['coverage']['processed'], 2)
        self.assertEqual(saved['coverage']['pending'], [chunks[2].id])


class AnswerQuestionWorkflowTests(SimpleTestCase):
    def task(self):
        return SimpleNamespace(
            id='current-task', payload={'question': '名称是否必填？', 'conversation_id': 'c1'},
            snapshot=[{'document_id': 7, 'revision_id': 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa'}],
        )

    def test_empty_retrieval_never_calls_model(self):
        context = FakeContext([])
        result = workflows.answer_question(self.task(), context)

        self.assertEqual(result['result_type'], 'insufficient')
        self.assertIn('未找到依据', result['answer'])
        self.assertEqual(context.replies, [])

    def test_answer_only_keeps_sources_given_to_model(self):
        context = FakeContext([
            '{"answer":"名称必填。","result_type":"supported","source_ids":["%s"],"warnings":[]}' % SOURCE_ID,
        ], [source()])
        result = workflows.answer_question(self.task(), context)

        self.assertEqual(result['result_type'], 'supported')
        self.assertEqual([item['id'] for item in result['sources']], [SOURCE_ID])
        self.assertEqual(result['warnings'], [])

    def test_bad_answer_json_is_partial_and_preserves_raw_draft(self):
        context = FakeContext(['not json'], [source()])
        result = workflows.answer_question(self.task(), context)

        self.assertEqual(result['result_type'], 'partial')
        self.assertEqual(result['raw_draft'], 'not json')

    def test_mixed_valid_and_invented_answer_sources_are_partial(self):
        context = FakeContext([
            '{"answer":"名称必填。","result_type":"supported","source_ids":["%s","invented"],"warnings":[]}' % SOURCE_ID,
        ], [source()])
        result = workflows.answer_question(self.task(), context)

        self.assertEqual(result['result_type'], 'partial')
        self.assertTrue(any('未正式核验' in warning for warning in result['warnings']))

    def test_malformed_answer_source_ids_does_not_raise(self):
        context = FakeContext([
            '{"answer":"名称必填。","result_type":"supported","source_ids":"not-a-list","warnings":null}',
        ], [source()])
        result = workflows.answer_question(self.task(), context)

        self.assertEqual(result['result_type'], 'insufficient')
        self.assertTrue(any('来源引用格式无效' in warning for warning in result['warnings']))

    def test_history_uses_recent_entries_first_then_restores_chronology(self):
        history = [
            {'task_id': 'new-task', 'role': 'assistant', 'content': '最新回答'},
            {'task_id': 'middle-task', 'role': 'user', 'content': '中间问题'},
            {'task_id': 'old-task', 'role': 'assistant', 'content': '很旧内容' * 40},
            {'task_id': 'current-task', 'role': 'assistant', 'content': ''},
            {'task_id': 'current-task', 'role': 'user', 'content': '名称是否必填？'},
        ]
        context = FakeContext([], history=history)
        with patch('project_knowledge.workflows.MAX_HISTORY_CHARS', 100):
            messages, warnings = workflows._conversation_history(self.task(), context, 'c1')

        self.assertEqual([item['content'] for item in messages], [
            '历史user（仅帮助理解追问，不能作为资料事实）：中间问题',
            '历史assistant（仅帮助理解追问，不能作为资料事实）：最新回答',
        ])
        self.assertTrue(warnings)


class StreamingAdapterTests(SimpleTestCase):
    def test_retries_only_before_any_output_and_passes_remaining_timeout(self):
        class TemporaryError(RuntimeError):
            pass

        class Model:
            def __init__(self):
                self.calls = 0
                self.timeouts = []

            def stream(self, messages, timeout):
                self.calls += 1
                self.timeouts.append(timeout)
                if self.calls == 1:
                    raise TemporaryError('HTTP 503 service unavailable')
                yield 'ok'

        model = Model()
        manager = SimpleNamespace(current_llm=model)
        chunks = []
        with patch('project_knowledge.llm.get_llm_manager', return_value=manager), patch('project_knowledge.llm.time.sleep'):
            response = llm.stream_call(9, [{'role': 'user', 'content': 'hi'}], chunks.append, lambda: None, lambda: 30)

        self.assertEqual(response, 'ok')
        self.assertEqual(chunks, ['ok'])
        self.assertEqual(model.calls, 2)
        self.assertLessEqual(model.timeouts[1], model.timeouts[0])

    def test_long_retry_after_is_cancellable_between_short_wait_slices(self):
        class TemporaryError(RuntimeError):
            headers = {'Retry-After': '900'}

        class TaskStopped(Exception):
            code = 'CANCELLED'

        class Model:
            calls = 0

            def stream(self, messages, timeout):
                self.calls += 1
                raise TemporaryError('HTTP 429 Too Many Requests')

        checks = 0

        def check_active():
            nonlocal checks
            checks += 1
            if checks >= 3:
                raise TaskStopped('cancelled while waiting')

        model = Model()
        with patch('project_knowledge.llm.get_llm_manager', return_value=SimpleNamespace(current_llm=model)), patch(
            'project_knowledge.llm.time.sleep',
        ) as sleep:
            with self.assertRaises(TaskStopped):
                llm.stream_call(9, [{'role': 'user', 'content': 'hi'}], None, check_active, lambda: 1200)

        sleep.assert_called_once_with(0.5)
        self.assertEqual(model.calls, 1)

    def test_single_request_budget_does_not_expand_saved_model_timeout(self):
        class Model:
            timeout = None

            def stream(self, messages, timeout):
                self.timeout = timeout
                yield 'ok'

        model = Model()
        manager = SimpleNamespace(current_llm=model, config={'extra_config': {'timeout': 3}})
        with patch('project_knowledge.llm.get_llm_manager', return_value=manager):
            self.assertEqual(
                llm.stream_call(9, [{'role': 'user', 'content': 'hi'}], None, lambda: None, lambda: 30),
                'ok',
            )

        self.assertEqual(model.timeout, 3)

    def test_chunk_after_single_call_deadline_is_rejected(self):
        class Model:
            def stream(self, messages, timeout):
                yield 'late'

        manager = SimpleNamespace(current_llm=Model(), config={'extra_config': {'timeout': 1}})
        with patch('project_knowledge.llm.get_llm_manager', return_value=manager), patch(
            'project_knowledge.llm.time.monotonic', side_effect=[0, 0, 1],
        ):
            with self.assertRaises(llm.KnowledgeLLMTimeout):
                llm.stream_call(9, [{'role': 'user', 'content': 'hi'}], None, lambda: None, lambda: 30)

    def test_empty_stream_checks_active_before_completion(self):
        class Model:
            def stream(self, messages, timeout):
                return iter(())

        checks = []
        with patch('project_knowledge.llm.get_llm_manager', return_value=SimpleNamespace(current_llm=Model())):
            self.assertEqual(
                llm.stream_call(9, [{'role': 'user', 'content': 'hi'}], None, lambda: checks.append(True), lambda: 30),
                '',
            )

        self.assertGreaterEqual(len(checks), 2)

    def test_partial_stream_failure_is_not_replayed(self):
        class Model:
            calls = 0

            def stream(self, messages, timeout):
                self.calls += 1
                yield 'partial'
                raise RuntimeError('HTTP 503 service unavailable')

        model = Model()
        with patch('project_knowledge.llm.get_llm_manager', return_value=SimpleNamespace(current_llm=model)):
            with self.assertRaises(llm.StreamInterruptedError):
                llm.stream_call(9, [{'role': 'user', 'content': 'hi'}], None, lambda: None, lambda: 30)

        self.assertEqual(model.calls, 1)

    def test_task_stopped_from_callback_is_not_retried_or_wrapped(self):
        class TaskStopped(Exception):
            code = 'CANCELLED'

        class Model:
            calls = 0

            def stream(self, messages, timeout):
                self.calls += 1
                yield 'first'
                yield 'second'

        model = Model()
        checks = iter((None, None, TaskStopped('stopped')))

        def check_active():
            value = next(checks)
            if isinstance(value, BaseException):
                raise value

        with patch('project_knowledge.llm.get_llm_manager', return_value=SimpleNamespace(current_llm=model)):
            with self.assertRaises(TaskStopped):
                llm.stream_call(9, [{'role': 'user', 'content': 'hi'}], None, check_active, lambda: 30)

        self.assertEqual(model.calls, 1)
