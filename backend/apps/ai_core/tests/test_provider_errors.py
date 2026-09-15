"""Offline contracts for safe model-provider failure classification."""
from __future__ import annotations

import httpx
from django.test import SimpleTestCase
from openai import (
    APIConnectionError, APIError, APIStatusError, APITimeoutError,
    AuthenticationError, InternalServerError, NotFoundError, RateLimitError,
)

from ai_core.provider_errors import classify_provider_error, public_model_failure


class ProviderErrorClassificationTests(SimpleTestCase):
    def request(self):
        return httpx.Request('POST', 'https://models.example.test/v1/chat/completions')

    def response(self, status):
        return httpx.Response(status, request=self.request(), json={'error': {'code': 'safe-fixture'}})

    def test_openai_stream_http_200_overload_is_safe_and_retryable(self):
        error = APIError(
            'Our servers are currently overloaded. Please try again later.',
            request=self.request(), body=None,
        )
        self.assertEqual(classify_provider_error(error), {
            'code': 'MODEL_OVERLOADED',
            'message': '模型服务当前负载较高，请稍后重试。',
            'retryable': True,
        })

    def test_status_failures_are_classified_without_returning_body(self):
        cases = [
            (RateLimitError('rate limited', response=self.response(429), body={'secret': 'nope'}), 'MODEL_RATE_LIMITED', True, 429),
            (InternalServerError('upstream unavailable', response=self.response(503), body={'secret': 'nope'}), 'MODEL_UNAVAILABLE', True, 503),
            (APIStatusError('gateway timeout', response=self.response(504), body={'secret': 'nope'}), 'MODEL_TIMEOUT', True, 504),
            (AuthenticationError('bad key', response=self.response(401), body={'secret': 'nope'}), 'MODEL_AUTH_ERROR', False, 401),
            (NotFoundError('unknown model', response=self.response(404), body={'secret': 'nope'}), 'MODEL_CONFIG_ERROR', False, 404),
            (APIStatusError('unsupported model parameter', response=self.response(400), body={'secret': 'nope'}), 'MODEL_CONFIG_ERROR', False, 400),
            (APIStatusError('invalid model parameter', response=self.response(422), body={'secret': 'nope'}), 'MODEL_CONFIG_ERROR', False, 422),
        ]
        for error, code, retryable, status in cases:
            with self.subTest(status=status):
                result = classify_provider_error(error)
                self.assertEqual(result['code'], code)
                self.assertEqual(result['retryable'], retryable)
                self.assertEqual(result['status_code'], status)
                self.assertNotIn('secret', str(result))

    def test_model_context_handles_generic_transport_only_when_explicit(self):
        error = RuntimeError('provider request failed 503')
        error.status_code = 503
        self.assertIsNone(classify_provider_error(error))
        self.assertEqual(classify_provider_error(error, model_context=True)['code'], 'MODEL_UNAVAILABLE')
        self.assertEqual(
            classify_provider_error(TimeoutError('socket timed out'), model_context=True)['code'],
            'MODEL_TIMEOUT',
        )

    def test_openai_transport_classes_do_not_depend_on_message_text(self):
        self.assertEqual(
            classify_provider_error(APIConnectionError(message='Connection error.', request=self.request()))['code'],
            'MODEL_UNAVAILABLE',
        )
        self.assertEqual(
            classify_provider_error(APITimeoutError(request=self.request()))['code'],
            'MODEL_TIMEOUT',
        )

    def test_quota_is_non_retryable_and_auth_wins_over_overload_body(self):
        quota = RateLimitError('rate limited', response=self.response(429), body={'error': {'code': 'insufficient_quota'}})
        self.assertEqual(classify_provider_error(quota)['code'], 'MODEL_QUOTA_EXHAUSTED')
        self.assertFalse(classify_provider_error(quota)['retryable'])
        quota_message = RateLimitError(
            'You exceeded your current quota, please check your plan and billing details.',
            response=self.response(429), body=None,
        )
        self.assertEqual(classify_provider_error(quota_message)['code'], 'MODEL_QUOTA_EXHAUSTED')
        self.assertFalse(classify_provider_error(quota_message)['retryable'])
        auth = AuthenticationError('forbidden', response=self.response(403), body={'error': {'code': 'server_overloaded'}})
        self.assertEqual(classify_provider_error(auth)['code'], 'MODEL_AUTH_ERROR')

    def test_wrapped_stream_error_and_unknown_mcp_error_do_not_conflate(self):
        wrapped = RuntimeError('流式LLM调用失败: Our servers are currently overloaded. Please try again later.')
        self.assertEqual(classify_provider_error(wrapped)['code'], 'MODEL_OVERLOADED')
        self.assertIsNone(classify_provider_error(RuntimeError('playwright click timeout: secret page input')))

    def test_public_task_projection_has_fixed_message_and_stage(self):
        value = public_model_failure({'code': 'MODEL_RATE_LIMITED', 'message': 'raw provider response'}, stage='exploring')
        self.assertEqual(value, {
            'code': 'MODEL_RATE_LIMITED',
            'message': '模型服务当前请求过于频繁，请稍后重试。',
            'retryable': True,
            'stage': 'exploring',
        })
        self.assertEqual(public_model_failure({'code': 'MODEL_TIMEOUT'}, stage='repairing')['stage'], 'repairing')
