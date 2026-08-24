"""AI 场景生成使用的 API 响应契约整理工具。"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any, Dict


def compact_responses(
    responses: Any,
    definitions: Mapping[str, Any] | None = None,
) -> Dict[str, Any]:
    """保留响应描述、响应头、Schema 和示例，去掉无关的空字段。

    当前 APIEndpoint.responses 主要是解析后的 OpenAPI 结构，
    同时兼容部分旧数据中直接保存的 Swagger response 结构。
    """

    responses = _as_mapping(responses)
    if not responses:
        return {}

    compacted: Dict[str, Any] = {}
    for status_code, response in responses.items():
        response = _as_mapping(response)
        if not response:
            continue

        item: Dict[str, Any] = {}
        if response.get("description"):
            item["description"] = response["description"]
        if response.get("headers"):
            item["headers"] = response["headers"]

        content = response.get("content")
        if isinstance(content, Mapping) and content:
            compact_content = {}
            for content_type, media in content.items():
                media = _as_mapping(media)
                if not media:
                    continue
                compact_media = _compact_media(media, definitions)
                if compact_media:
                    compact_content[str(content_type)] = compact_media
            if compact_content:
                item["content"] = compact_content
        elif response.get("schema") or "example" in response or response.get("examples"):
            # 兼容 Swagger 2.0 response 尚未经过统一解析的旧数据。
            item["content"] = {
                "application/json": _compact_media(response, definitions)
            }

        if item:
            compacted[str(status_code)] = item

    return compacted


def build_generation_endpoint(
    endpoint: Mapping[str, Any],
    definitions: Mapping[str, Any] | None = None,
) -> Dict[str, Any]:
    """构建传给模型的单个接口上下文。"""

    return {
        "id": endpoint.get("id"),
        "path": endpoint.get("path", ""),
        "method": str(endpoint.get("method", "")).upper(),
        "summary": endpoint.get("summary", ""),
        "description": endpoint.get("description", ""),
        "parameters": endpoint.get("parameters") or [],
        "request_body": endpoint.get("request_body") or {},
        "responses": compact_responses(endpoint.get("responses") or {}, definitions),
    }


def _compact_media(
    media: Mapping[str, Any],
    definitions: Mapping[str, Any] | None = None,
) -> Dict[str, Any]:
    result: Dict[str, Any] = {}
    if media.get("schema"):
        result["schema"] = _resolve_refs(media["schema"], definitions or {})

    for key in ("example", "examples"):
        value = media.get(key)
        if key == "example" and key in media and value is not None:
            result[key] = value
        elif key == "examples" and value:
            result[key] = value
    return result


def _as_mapping(value: Any) -> Mapping[str, Any]:
    if isinstance(value, Mapping):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, Mapping) else {}
        except (TypeError, ValueError, json.JSONDecodeError):
            return {}
    return {}


def _resolve_refs(
    value: Any,
    definitions: Mapping[str, Any],
    resolving: frozenset[str] = frozenset(),
) -> Any:
    """解析 Swagger definitions 中的 $ref，同时避免循环引用。"""

    if isinstance(value, Mapping):
        ref = value.get("$ref")
        if isinstance(ref, str) and ref.startswith("#/definitions/"):
            definition_name = ref.split("/", 2)[-1]
            definition = definitions.get(definition_name)
            if isinstance(definition, Mapping) and ref not in resolving:
                resolved = _resolve_refs(definition, definitions, resolving | {ref})
                if isinstance(resolved, Mapping):
                    extras = {
                        key: _resolve_refs(child, definitions, resolving)
                        for key, child in value.items()
                        if key != "$ref"
                    }
                    return {**resolved, **extras}

        return {
            key: _resolve_refs(child, definitions, resolving)
            for key, child in value.items()
        }

    if isinstance(value, list):
        return [_resolve_refs(item, definitions, resolving) for item in value]

    return value
