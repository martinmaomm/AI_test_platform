"""Offline regressions for safe third-party model-service error classification."""

import httpx
from django.test import SimpleTestCase
from openai import APIConnectionError, APIError, APIStatusError, APITimeoutError, OpenAI, OpenAIError
from unittest.mock import patch

from .model_service_errors import classify_model_service_error


def openai_status_error(status_code: int, *, body=None) -> APIStatusError:
    request = httpx.Request("POST", "https://provider.invalid/v1/chat/completions")
    response = httpx.Response(status_code, request=request)
    return APIStatusError("provider response", response=response, body=body)


class ResponseOnlyError(RuntimeError):
    def __init__(self, status_code: int):
        super().__init__("response wrapper")
        self.response = type("Response", (), {"status_code": status_code})()


class ModelServiceErrorTests(SimpleTestCase):
    stage = "script_generation"

    def test_openai_style_5xx_is_a_safe_model_unavailable_error(self):
        for status_code in (500, 502, 503, 504):
            with self.subTest(status_code=status_code):
                self.assertEqual(
                    classify_model_service_error(
                        openai_status_error(status_code), stage=self.stage,
                    ),
                    (
                        "MODEL_SERVICE_ERROR",
                        f"本次锁定的模型服务异常（HTTP {status_code}），请稍后重试。",
                    ) if status_code != 504 else (
                        "MODEL_GATEWAY_TIMEOUT",
                        "本次锁定的模型服务请求超时（HTTP 504），请稍后重试。",
                    ),
                )

    def test_response_status_code_is_supported_without_reading_message_text(self):
        self.assertEqual(
            classify_model_service_error(ResponseOnlyError(503), stage=self.stage),
            ("MODEL_SERVICE_ERROR", "本次锁定的模型服务异常（HTTP 503），请稍后重试。"),
        )

    def test_wrapped_status_error_uses_cause_without_reading_sensitive_body(self):
        provider_error = openai_status_error(
            504,
            body={"message": "do not expose", "api_key": "secret-value"},
        )
        wrapper = RuntimeError("generic wrapper")
        wrapper.__cause__ = provider_error

        result = classify_model_service_error(wrapper)

        self.assertEqual(result, ("MODEL_GATEWAY_TIMEOUT", "本次锁定的模型服务请求超时（HTTP 504），请稍后重试。"))
        self.assertNotIn("secret-value", result[1])
        self.assertNotIn("do not expose", result[1])

    def test_rate_limit_and_authentication_statuses_have_stable_messages(self):
        self.assertEqual(
            classify_model_service_error(openai_status_error(429), stage=self.stage),
            ("MODEL_RATE_LIMITED", "本次锁定的模型触发限流，请稍后重试。"),
        )
        self.assertEqual(
            classify_model_service_error(openai_status_error(401), stage=self.stage),
            ("MODEL_AUTHENTICATION_FAILED", "本次锁定的模型认证或权限校验失败，请检查模型配置后重试。"),
        )
        self.assertEqual(
            classify_model_service_error(openai_status_error(403), stage=self.stage),
            ("MODEL_AUTHENTICATION_FAILED", "本次锁定的模型认证或权限校验失败，请检查模型配置后重试。"),
        )

    def test_plain_message_with_500_is_not_treated_as_a_model_service_error(self):
        self.assertIsNone(
            classify_model_service_error(RuntimeError("page tool returned 500"), stage=self.stage)
        )

    def test_tool_http_error_is_not_classified_without_an_explicit_model_stage(self):
        tool_error = httpx.HTTPStatusError(
            "tool endpoint failed",
            request=httpx.Request("POST", "https://browser-tool.invalid/"),
            response=httpx.Response(
                500,
                request=httpx.Request("POST", "https://browser-tool.invalid/"),
            ),
        )

        self.assertIsNone(classify_model_service_error(tool_error))

    def test_sdk_status_is_classified_without_stage_but_generic_status_is_not(self):
        self.assertEqual(
            classify_model_service_error(openai_status_error(503)),
            ("MODEL_SERVICE_ERROR", "本次锁定的模型服务异常（HTTP 503），请稍后重试。"),
        )
        self.assertIsNone(classify_model_service_error(ResponseOnlyError(503)))

    def test_context_chain_is_supported_and_cycles_do_not_loop(self):
        provider_error = openai_status_error(502)
        wrapper = RuntimeError("outer")
        wrapper.__context__ = provider_error
        provider_error.__context__ = wrapper

        self.assertEqual(
            classify_model_service_error(wrapper),
            ("MODEL_SERVICE_ERROR", "本次锁定的模型服务异常（HTTP 502），请稍后重试。"),
        )

    def test_stateless_trusted_stream_and_transport_errors_have_safe_codes(self):
        request = httpx.Request("POST", "https://provider.invalid/v1/chat/completions")
        cases = (
            (
                APIError("stream failed", request=request, body={"type": "server_error"}),
                ("MODEL_SERVICE_ERROR", "本次锁定的模型服务流式响应异常，请稍后重试。"),
            ),
            (
                APIConnectionError(request=request),
                ("MODEL_SERVICE_ERROR", "本次锁定的模型服务连接异常，请稍后重试。"),
            ),
            (
                APITimeoutError(request=request),
                ("MODEL_GATEWAY_TIMEOUT", "本次锁定的模型服务请求超时，请稍后重试。"),
            ),
            (
                OpenAIError("模型服务暂时不可用，请稍后重试"),
                ("MODEL_SERVICE_ERROR", "本次锁定的模型服务暂时不可用，请稍后重试。"),
            ),
        )
        for error, expected in cases:
            with self.subTest(error=type(error).__name__):
                self.assertEqual(classify_model_service_error(error), expected)

    def test_stateless_authentication_configuration_and_runtime_errors_are_not_classified(self):
        request = httpx.Request("POST", "https://provider.invalid/v1/chat/completions")
        errors = (
            APIError("invalid credentials", request=request, body={"type": "authentication_error"}),
            APIError("invalid request", request=request, body={"code": "invalid_request"}),
            OpenAIError("The api_key client option must be set"),
            RuntimeError("model service temporarily unavailable"),
        )
        for error in errors:
            with self.subTest(error=type(error).__name__):
                self.assertIsNone(classify_model_service_error(error, stage=self.stage))

    def test_sdk_retries_one_chat_request_without_rerunning_the_surrounding_tool_step(self):
        requests = []
        tool_runs = []

        def handler(request):
            requests.append(request)
            if len(requests) == 1:
                return httpx.Response(500, request=request, json={"error": "temporary"})
            return httpx.Response(
                200,
                request=request,
                json={
                    "id": "chatcmpl-test",
                    "object": "chat.completion",
                    "created": 0,
                    "model": "mock-model",
                    "choices": [{
                        "index": 0,
                        "message": {"role": "assistant", "content": "ok", "refusal": None},
                        "finish_reason": "stop",
                        "logprobs": None,
                    }],
                    "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
                },
            )

        client = OpenAI(
            api_key="test-key",
            base_url="https://provider.invalid/v1",
            max_retries=3,
            http_client=httpx.Client(transport=httpx.MockTransport(handler)),
        )
        try:
            with patch("openai._base_client.time.sleep"):
                tool_runs.append("completed-before-model-call")
                completion = client.chat.completions.create(
                    model="mock-model",
                    messages=[{"role": "user", "content": "test"}],
                )
        finally:
            client.close()

        self.assertEqual(completion.choices[0].message.content, "ok")
        self.assertEqual(tool_runs, ["completed-before-model-call"])
        self.assertEqual(len(requests), 2)
        self.assertEqual(
            [request.headers["x-stainless-retry-count"] for request in requests],
            ["0", "1"],
        )
