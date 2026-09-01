"""Regression coverage for the global LangChain streaming transport policy."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock, patch

from django.test import SimpleTestCase
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage
from langchain_core.outputs import ChatGenerationChunk
from pydantic import BaseModel

from ai_core.midscene_script_agent import MidSceneAgent
from ai_core.model_manager import ModelManager


class StructuredReply(BaseModel):
    answer: str


class StreamingContractModel(BaseChatModel):
    """A model that fails if LangChain chooses generate instead of stream."""

    streaming: bool = True
    emit_tool_call: bool = False
    sync_stream_calls: int = 0
    async_stream_calls: int = 0
    generate_calls: int = 0
    tools_bound: bool = False

    @property
    def _llm_type(self) -> str:
        return 'streaming_contract_test'

    def bind_tools(self, tools: Any, **kwargs: Any):
        self.tools_bound = True
        return self

    def _generate(self, *args: Any, **kwargs: Any):
        self.generate_calls += 1
        raise AssertionError('generate must not run when streaming=True')

    async def _agenerate(self, *args: Any, **kwargs: Any):
        raise AssertionError('agenerate must not run when streaming=True')

    def _stream(self, messages: Any, stop: Any = None, run_manager: Any = None, **kwargs: Any):
        self.sync_stream_calls += 1
        if self.emit_tool_call:
            yield ChatGenerationChunk(message=AIMessageChunk(
                content='',
                tool_call_chunks=[{
                    'name': 'StructuredReply',
                    'args': '{"answer":"streamed"}',
                    'id': 'structured-call',
                    'index': 0,
                }],
            ))
            return
        yield ChatGenerationChunk(message=AIMessageChunk(content='sync '))
        yield ChatGenerationChunk(message=AIMessageChunk(content='stream'))

    async def _astream(self, messages: Any, stop: Any = None, run_manager: Any = None, **kwargs: Any):
        self.async_stream_calls += 1
        yield ChatGenerationChunk(message=AIMessageChunk(content='async '))
        yield ChatGenerationChunk(message=AIMessageChunk(content='stream'))


class ModelManagerInitializationStreamingTests(SimpleTestCase):
    def _manager(self, *, model_type: str, provider: str) -> ModelManager:
        manager = ModelManager.__new__(ModelManager)
        manager.model_type = model_type
        manager.config = {
            'provider': provider,
            'model': 'streaming-test-model',
            'api_key': 'test-key',
            'base_url': 'https://example.test/v1',
            'extra_config': {'streaming': False, 'temperature': 0.2},
        }
        return manager

    def test_initialization_forces_streaming_for_openai_compatible_and_deepseek(self):
        cases = (
            ('llm', 'openai', 'openai'),
            ('vision', 'deepseek', 'deepseek'),
        )
        for model_type, provider, expected_provider in cases:
            with self.subTest(model_type=model_type, provider=provider), patch(
                'ai_core.model_manager.init_chat_model', return_value=Mock()
            ) as init_chat_model:
                manager = self._manager(model_type=model_type, provider=provider)
                manager._initialize_model()

                self.assertEqual(init_chat_model.call_args.kwargs['model_provider'], expected_provider)
                self.assertTrue(init_chat_model.call_args.kwargs['streaming'])
                self.assertEqual(init_chat_model.call_args.kwargs['temperature'], 0.2)

    def test_connection_uses_the_streaming_enabled_invoke_contract(self):
        model = StreamingContractModel(streaming=True)
        manager = ModelManager.__new__(ModelManager)
        manager.model_type = 'llm'
        manager._initialized = True
        manager.current_llm = model
        manager.llm_type = 'test'
        manager.config = {'provider': 'test', 'model': 'streaming-test-model'}

        result = manager.test_connection()

        self.assertTrue(result['success'])
        self.assertEqual(result['response_content'], 'sync stream')
        self.assertEqual(model.sync_stream_calls, 1)
        self.assertEqual(model.generate_calls, 0)


class LangChainStreamingContractTests(SimpleTestCase):
    def test_invoke_and_ainvoke_aggregate_streams_when_streaming_is_enabled(self):
        model = StreamingContractModel(streaming=True)

        self.assertEqual(model.invoke([HumanMessage(content='hello')]).content, 'sync stream')
        self.assertEqual(asyncio.run(model.ainvoke([HumanMessage(content='hello')])).content, 'async stream')
        self.assertEqual(model.sync_stream_calls, 1)
        self.assertEqual(model.async_stream_calls, 1)
        self.assertEqual(model.generate_calls, 0)

    def test_structured_output_uses_the_same_streaming_invoke_contract(self):
        model = StreamingContractModel(streaming=True, emit_tool_call=True)

        reply = model.with_structured_output(StructuredReply).invoke('return a structured reply')

        self.assertEqual(reply, StructuredReply(answer='streamed'))
        self.assertTrue(model.tools_bound)
        self.assertEqual(model.sync_stream_calls, 1)
        self.assertEqual(model.generate_calls, 0)


class ModelManagerStreamInvokeTests(SimpleTestCase):
    @staticmethod
    def _manager(llm: Any) -> ModelManager:
        manager = ModelManager.__new__(ModelManager)
        manager.model_type = 'llm'
        manager._initialized = True
        manager.current_llm = llm
        return manager

    def test_stream_invoke_concatenates_text_and_content_blocks(self):
        class ChunkingLLM:
            def stream(self, messages, **kwargs):
                yield 'hello '
                yield SimpleNamespace(content=[
                    {'type': 'text', 'text': 'from '},
                    {'type': 'text', 'text': 'blocks'},
                ])
                yield SimpleNamespace(content={'content': '!'})

        chunks: list[str] = []
        response = self._manager(ChunkingLLM()).stream_invoke(
            [HumanMessage(content='hello')], callback=chunks.append,
        )

        self.assertEqual(response, 'hello from blocks!')
        self.assertEqual(chunks, ['hello ', 'from blocks', '!'])

    def test_stream_failure_preserves_cause_and_never_replays_invoke(self):
        class StreamFailure(RuntimeError):
            pass

        class FailingLLM:
            def __init__(self):
                self.invoke = Mock(return_value=AIMessage(content='must not be used'))
                self.error = StreamFailure('provider stream interrupted')

            def stream(self, messages, **kwargs):
                yield 'partial '
                raise self.error

        llm = FailingLLM()
        received: list[str] = []
        with self.assertRaises(RuntimeError) as raised:
            self._manager(llm).stream_invoke([HumanMessage(content='hello')], callback=received.append)

        self.assertEqual(received, ['partial '])
        self.assertIs(raised.exception.__cause__, llm.error)
        llm.invoke.assert_not_called()

    def test_stream_invoke_only_falls_back_when_stream_is_not_callable(self):
        llm = SimpleNamespace(stream=None, invoke=Mock(return_value=AIMessage(content='invoke result')))

        response = self._manager(llm).stream_invoke([HumanMessage(content='hello')])

        self.assertEqual(response, 'invoke result')
        llm.invoke.assert_called_once()

    def test_websocket_stream_failure_does_not_replay_invoke(self):
        class StreamFailure(RuntimeError):
            pass

        class FailingLLM:
            def __init__(self):
                self.invoke = Mock(return_value=AIMessage(content='must not be used'))
                self.error = StreamFailure('provider stream interrupted')

            def stream(self, messages, **kwargs):
                yield 'partial '
                raise self.error

        llm = FailingLLM()
        received: list[tuple[str, str]] = []
        with self.assertRaises(RuntimeError) as raised:
            self._manager(llm).stream_invoke_with_websocket(
                [HumanMessage(content='hello')],
                step_name='generation',
                streaming_callback=lambda step, content: received.append((step, content)),
            )

        self.assertEqual(received, [('generation', 'partial ')])
        self.assertIs(raised.exception.__cause__, llm.error)
        llm.invoke.assert_not_called()


class MidSceneStreamingFailureTests(SimpleTestCase):
    def test_partial_stream_failure_does_not_start_a_second_model_request(self):
        agent = MidSceneAgent.__new__(MidSceneAgent)
        agent.enable_streaming = True
        agent.streaming_callback = None
        agent._send_websocket_message = Mock()
        agent.generate_midscene_script = Mock(
            side_effect=RuntimeError('provider stream interrupted')
        )
        agent._call_vision_model = Mock(return_value='must not be used')

        with self.assertRaisesRegex(RuntimeError, 'provider stream interrupted'):
            agent._stream_vision_response('generate script', 'script_generator')

        agent.generate_midscene_script.assert_called_once()
        agent._call_vision_model.assert_not_called()
