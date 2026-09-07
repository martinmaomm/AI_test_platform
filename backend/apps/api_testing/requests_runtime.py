"""A small, deterministic requests runtime for API test-case JSON.

This module intentionally has no Django or AITS imports.  ``export_python``
embeds this exact module source in generated files, so exported tests and the
server execute the same normalisation, substitution, extraction and assertion
code.  It accepts only structured JSON test cases; it never evaluates hooks,
Python snippets, or arbitrary expressions.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import inspect
import json
import pprint
import re
import sys
import time
import uuid
from collections.abc import Mapping
from typing import Any
from urllib.parse import urljoin, urlsplit

import requests


MAX_REQUEST_TIMEOUT_SECONDS = 120.0
MAX_TOTAL_TIMEOUT_SECONDS = 600.0
DEFAULT_CONNECT_TIMEOUT_SECONDS = 10.0
DEFAULT_READ_TIMEOUT_SECONDS = 30.0
MAX_VARIABLE_RESOLUTION_DEPTH = 32
_VARIABLE_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_FULL_VARIABLE = re.compile(r"^(?:\$\{([A-Za-z_][A-Za-z0-9_]*)\}|\{\{([A-Za-z_][A-Za-z0-9_]*)\}\}|\$([A-Za-z_][A-Za-z0-9_]*))$")
_EMBEDDED_VARIABLE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}|\{\{([A-Za-z_][A-Za-z0-9_]*)\}\}|\$(?![.{])([A-Za-z_][A-Za-z0-9_]*)")
_SUPPORTED_COMPARATORS = {
    "eq", "ne", "contains", "not_contains", "gt", "ge", "lt", "le", "type", "length",
}
_TYPE_NAMES = {
    "null": type(None), "none": type(None), "bool": bool, "boolean": bool,
    "int": int, "integer": int, "float": float, "number": (int, float),
    "str": str, "string": str, "list": list, "array": list, "dict": dict,
    "object": dict,
}


class CaseContractError(ValueError):
    """Raised when JSON does not conform to the supported case contract."""


class UnsupportedCaseFeature(CaseContractError):
    """Raised for an explicit feature which the requests runtime cannot run."""


class ExtractionFailure(ValueError):
    """A response extraction assertion failure, distinct from case syntax."""


def _as_mapping(value: Any, field: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise CaseContractError(f"{field} 必须是 JSON 对象")
    return deepcopy(dict(value))


def _case_value(value: Any) -> dict[str, Any]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError as exc:
            raise CaseContractError(f"用例必须是合法 JSON：{exc.msg}") from exc
    return _as_mapping(value, "用例")


def _canonical_validator(value: Any, field: str) -> dict[str, Any]:
    if isinstance(value, Mapping):
        raw = dict(value)
        if {"check", "assert"}.issubset(raw):
            comparator = raw["assert"]
            args = [raw["check"]]
            if "expect" in raw:
                args.append(raw["expect"])
        elif len(raw) == 1:
            comparator, args = next(iter(raw.items()))
            if not isinstance(args, list):
                raise CaseContractError(f"{field} 的比较器参数必须是数组")
        else:
            raise CaseContractError(f"{field} 必须是 {{比较器: [检查项, 预期值]}} 格式")
    elif isinstance(value, list) and len(value) in (2, 3):
        # The old visual editor also persisted [check, comparator, expect].
        if len(value) == 3:
            args, comparator = [value[0], value[2]], value[1]
        else:
            args, comparator = [value[0]], value[1]
    else:
        raise CaseContractError(f"{field} 不是可识别的断言")

    comparator = str(comparator).lower()
    if comparator not in _SUPPORTED_COMPARATORS:
        names = ", ".join(sorted(_SUPPORTED_COMPARATORS))
        raise UnsupportedCaseFeature(f"{field} 使用了不支持的比较器 {comparator!r}；仅支持 {names}")
    if len(args) != 2:
        raise CaseContractError(f"{field} 的 {comparator} 必须包含检查项和预期值")
    if not isinstance(args[0], str) or not args[0].strip():
        raise CaseContractError(f"{field} 的检查项必须是非空字符串")
    if comparator == "type" and (not isinstance(args[1], str) or args[1].lower() not in _TYPE_NAMES):
        names = ", ".join(sorted(_TYPE_NAMES))
        raise CaseContractError(f"{field} 的 type 预期值必须是支持的类型名：{names}")
    # Keep the editor's established {eq: [check, expect]} JSON as canonical.
    # The runner parses this controlled form below; UI callers never need to
    # understand the internal comparator/check/expect representation.
    return {comparator: [args[0], deepcopy(args[1])]}


def _parse_validator(value: Any, field: str) -> dict[str, Any]:
    canonical = _canonical_validator(value, field)
    comparator, args = next(iter(canonical.items()))
    return {"comparator": comparator, "check": args[0], "expect": args[1]}


def normalize_case(value: Any) -> dict[str, Any]:
    """Return a copied, JSON-only case in the B-plan canonical shape.

    Normalisation is deliberately strict about executable legacy-engine features.
    Unknown metadata is retained for editor forward compatibility, while hooks,
    request scripts and arbitrary function fields fail loudly instead of being
    silently ignored.
    """
    raw = _case_value(value)
    if type(raw.get('version', 1)) is not int or raw.get('version', 1) != 1:
        raise CaseContractError('仅支持 version=1 的 API 用例契约')
    unsupported = {"setup_hooks", "teardown_hooks", "setup", "teardown", "functions"}
    found = sorted(unsupported.intersection(raw))
    if found:
        raise UnsupportedCaseFeature(f"requests 运行器不支持任意代码字段：{', '.join(found)}")

    config = _as_mapping(raw.get("config", {}), "config")
    for key in unsupported:
        if key in config:
            raise UnsupportedCaseFeature(f"requests 运行器不支持 config.{key}")
    variables = config.get("variables", {})
    if not isinstance(variables, Mapping):
        raise CaseContractError("config.variables 必须是 JSON 对象")
    for name in variables:
        if not isinstance(name, str) or not _VARIABLE_NAME.fullmatch(name):
            raise CaseContractError(f"config.variables 变量名无效：{name!r}")
    headers = config.get("headers", {})
    if not isinstance(headers, Mapping):
        raise CaseContractError("config.headers 必须是 JSON 对象")
    if "verify" in config and not isinstance(config["verify"], bool):
        raise CaseContractError("config.verify 必须是布尔值")
    if "base_url" in config and config["base_url"] is not None and not isinstance(config["base_url"], str):
        raise CaseContractError("config.base_url 必须是字符串")

    steps = raw.get("teststeps", [])
    if not isinstance(steps, list):
        raise CaseContractError("teststeps 必须是数组")
    normalised_steps: list[dict[str, Any]] = []
    for index, raw_step in enumerate(steps, start=1):
        field = f"teststeps[{index - 1}]"
        step = _as_mapping(raw_step, field)
        found = sorted(unsupported.intersection(step))
        if found:
            raise UnsupportedCaseFeature(f"{field} 不支持任意代码字段：{', '.join(found)}")
        request = step.get("request")
        if not isinstance(request, Mapping):
            raise CaseContractError(f"{field}.request 必须是 JSON 对象")
        request = deepcopy(dict(request))
        supported_request_fields = {
            "method", "url", "headers", "params", "json", "data", "raw",
            "timeout", "allow_redirects", "cookies", "verify",
        }
        unknown_request_fields = sorted(set(request).difference(supported_request_fields))
        if unknown_request_fields:
            raise UnsupportedCaseFeature(
                f"{field}.request 含不支持字段：{', '.join(unknown_request_fields)}"
            )
        request_unsupported = {"hooks", "setup_hooks", "teardown_hooks", "function"}.intersection(request)
        if request_unsupported:
            raise UnsupportedCaseFeature(f"{field}.request 不支持任意代码字段：{', '.join(sorted(request_unsupported))}")
        method = request.get("method", "GET")
        if not isinstance(method, str) or not method.strip():
            raise CaseContractError(f"{field}.request.method 必须是非空字符串")
        url = request.get("url")
        if not isinstance(url, str) or not url.strip():
            raise CaseContractError(f"{field}.request.url 必须是非空字符串")
        for mapping_key in ("headers", "params"):
            if mapping_key in request and not isinstance(request[mapping_key], Mapping):
                raise CaseContractError(f"{field}.request.{mapping_key} 必须是 JSON 对象")
        if "cookies" in request and request["cookies"] is not None and not isinstance(request["cookies"], Mapping):
            raise CaseContractError(f"{field}.request.cookies 必须是 JSON 对象")
        for boolean_key in ("allow_redirects", "verify"):
            if boolean_key in request and not isinstance(request[boolean_key], bool):
                raise CaseContractError(f"{field}.request.{boolean_key} 必须是布尔值")
        if "timeout" in request and request["timeout"] is not None:
            _positive_timeout(request["timeout"], f"{field}.request.timeout", MAX_REQUEST_TIMEOUT_SECONDS)
        body_keys = [key for key in ("json", "data", "raw") if key in request and request[key] is not None]
        if len(body_keys) > 1:
            raise CaseContractError(f"{field}.request 的 json、data、raw 只能选择一个")
        if "raw" in body_keys and not isinstance(request["raw"], (str, bytes)):
            raise CaseContractError(f"{field}.request.raw 必须是字符串")
        extract = step.get("extract", {})
        if not isinstance(extract, Mapping):
            raise CaseContractError(f"{field}.extract 必须是 JSON 对象")
        for name, selector in extract.items():
            if not isinstance(name, str) or not _VARIABLE_NAME.fullmatch(name):
                raise CaseContractError(f"{field}.extract 变量名无效：{name!r}")
            if not isinstance(selector, str) or not selector.strip():
                raise CaseContractError(f"{field}.extract.{name} 必须是非空选择器")
        validates = step.get("validate", step.get("validators", []))
        if validates is None:
            validates = []
        if not isinstance(validates, list):
            raise CaseContractError(f"{field}.validate 必须是数组")
        step["name"] = str(step.get("name") or f"步骤 {index}")
        request["method"] = method.upper()
        step["request"] = request
        step["extract"] = deepcopy(dict(extract))
        step["validate"] = [_canonical_validator(item, f"{field}.validate[{position}]") for position, item in enumerate(validates)]
        step.pop("validators", None)
        normalised_steps.append(step)

    result = deepcopy(raw)
    result["version"] = raw.get("version", 1)
    result["config"] = config
    result["config"]["variables"] = deepcopy(dict(variables))
    result["config"]["headers"] = deepcopy(dict(headers))
    result["config"].setdefault("name", "")
    result["config"].setdefault("verify", True)
    result["teststeps"] = normalised_steps
    return result


def _variable_names(value: Any) -> set[str]:
    names: set[str] = set()
    if isinstance(value, str):
        for match in _EMBEDDED_VARIABLE.finditer(value):
            names.add(next(name for name in match.groups() if name is not None))
    elif isinstance(value, Mapping):
        for item in value.values():
            names.update(_variable_names(item))
    elif isinstance(value, list):
        for item in value:
            names.update(_variable_names(item))
    return names


def _validate_known_variables(case: Mapping[str, Any], variables: Mapping[str, Any]) -> None:
    """Reject statically unknown placeholders before any HTTP request is sent."""
    available = set(variables)
    config = case["config"]
    for value in (config.get("base_url"), config.get("headers")):
        missing = _variable_names(value).difference(available)
        if missing:
            raise CaseContractError(f"未知变量：{', '.join(sorted(missing))}")
    for index, step in enumerate(case["teststeps"], start=1):
        request = step["request"]
        for key in ("url", "headers", "params", "json", "data", "raw"):
            if key in request:
                missing = _variable_names(request[key]).difference(available)
                if missing:
                    raise CaseContractError(f"步骤 {index} 请求前发现未知变量：{', '.join(sorted(missing))}")
        for position, raw_validator in enumerate(step["validate"]):
            validator = _parse_validator(raw_validator, f"teststeps[{index - 1}].validate[{position}]")
            missing = _variable_names(validator["expect"]).difference(available | set(step["extract"]))
            if missing:
                raise CaseContractError(f"步骤 {index} 断言中发现未知变量：{', '.join(sorted(missing))}")
        available.update(step["extract"])


def _builtin_variables() -> dict[str, Any]:
    """Return the two intentionally small, non-evaluated runtime defaults."""
    return {"timestamp_ns": time.time_ns(), "uuid4": str(uuid.uuid4())}


def _resolve_variable_definitions(definitions: Mapping[str, Any]) -> dict[str, Any]:
    """Resolve variable-to-variable templates with bounded cycle detection.

    A full placeholder returns the referenced value unchanged, preserving JSON
    types. Embedded placeholders are string interpolation by definition. This
    is a fixed lookup/substitution operation, never an expression evaluator.
    """
    resolved: dict[str, Any] = {}
    resolving: list[str] = []

    def resolve(name: str) -> Any:
        if name in resolved:
            return deepcopy(resolved[name])
        if name not in definitions:
            raise CaseContractError(f"未知变量：{name}")
        if name in resolving:
            cycle = " -> ".join([*resolving, name])
            raise CaseContractError(f"变量模板存在循环引用：{cycle}")
        if len(resolving) >= MAX_VARIABLE_RESOLUTION_DEPTH:
            raise CaseContractError(f"变量模板解析超过最大深度 {MAX_VARIABLE_RESOLUTION_DEPTH}")
        resolving.append(name)
        try:
            value = resolve_value(definitions[name])
            resolved[name] = deepcopy(value)
            return deepcopy(value)
        finally:
            resolving.pop()

    def resolve_value(value: Any) -> Any:
        if isinstance(value, str):
            full = _FULL_VARIABLE.fullmatch(value)
            if full:
                return resolve(next(name for name in full.groups() if name is not None))

            def replace(match: re.Match[str]) -> str:
                return str(resolve(next(name for name in match.groups() if name is not None)))

            return _EMBEDDED_VARIABLE.sub(replace, value)
        if isinstance(value, Mapping):
            return {key: resolve_value(item) for key, item in value.items()}
        if isinstance(value, list):
            return [resolve_value(item) for item in value]
        return deepcopy(value)

    return {name: resolve(name) for name in definitions}


def _substitute(value: Any, variables: Mapping[str, Any]) -> Any:
    if isinstance(value, str):
        full = _FULL_VARIABLE.fullmatch(value)
        if full:
            name = next(name for name in full.groups() if name is not None)
            if name not in variables:
                raise CaseContractError(f"未知变量：{name}")
            return deepcopy(variables[name])

        def replace(match: re.Match[str]) -> str:
            name = next(name for name in match.groups() if name is not None)
            if name not in variables:
                raise CaseContractError(f"未知变量：{name}")
            return str(variables[name])

        return _EMBEDDED_VARIABLE.sub(replace, value)
    if isinstance(value, Mapping):
        return {key: _substitute(item, variables) for key, item in value.items()}
    if isinstance(value, list):
        return [_substitute(item, variables) for item in value]
    return deepcopy(value)


def _positive_timeout(value: Any, name: str, maximum: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise CaseContractError(f"{name} 必须是秒数") from exc
    if not 0 < parsed <= maximum:
        raise CaseContractError(f"{name} 必须在 0 到 {maximum:g} 秒之间")
    return parsed


def _runtime_options(case: Mapping[str, Any], options: Mapping[str, Any] | None) -> dict[str, Any]:
    options = dict(options or {})
    config = case["config"]
    default_timeout = options.get("timeout", config.get("timeout", DEFAULT_READ_TIMEOUT_SECONDS))
    read = _positive_timeout(options.get("read_timeout", default_timeout), "read_timeout", MAX_REQUEST_TIMEOUT_SECONDS)
    connect = _positive_timeout(options.get("connect_timeout", default_timeout), "connect_timeout", MAX_REQUEST_TIMEOUT_SECONDS)
    total_default = min(MAX_TOTAL_TIMEOUT_SECONDS, max(read, read * max(1, len(case["teststeps"]))))
    total = _positive_timeout(options.get("total_timeout", config.get("total_timeout", total_default)), "total_timeout", MAX_TOTAL_TIMEOUT_SECONDS)
    # Environment.get_api_config() exposes verify_ssl; explicit platform
    # ``verify`` remains the newer, higher-priority spelling.
    verify = options.get("verify", options.get("verify_ssl", config.get("verify", True)))
    if not isinstance(verify, bool):
        raise CaseContractError("verify 必须是布尔值")
    headers = config.get("headers", {})
    option_headers = options.get("headers", {})
    if not isinstance(option_headers, Mapping):
        raise CaseContractError("options.headers 必须是 JSON 对象")
    allowed_origin = options.get("allowed_origin")
    if allowed_origin is not None:
        if not isinstance(allowed_origin, str):
            raise CaseContractError("options.allowed_origin 必须是完整 HTTP(S) URL")
        _http_origin(allowed_origin, "options.allowed_origin")
    return {
        "connect_timeout": connect,
        "read_timeout": read,
        "total_timeout": total,
        "verify": verify,
        "headers": {**headers, **dict(option_headers)},
        "allowed_origin": allowed_origin,
    }


def _http_origin(value: str, label: str) -> tuple[str, str, int]:
    parts = urlsplit(value)
    if parts.scheme.lower() not in {"http", "https"} or not parts.hostname:
        raise CaseContractError(f"{label} 必须是完整 HTTP(S) URL")
    try:
        port = parts.port or (443 if parts.scheme.lower() == "https" else 80)
    except ValueError as exc:
        raise CaseContractError(f"{label} 端口非法") from exc
    return parts.scheme.lower(), parts.hostname.lower(), port


def _assert_allowed_origin(url: str, allowed_origin: str | None) -> None:
    if allowed_origin is not None and _http_origin(url, "请求 URL") != _http_origin(allowed_origin, "options.allowed_origin"):
        raise CaseContractError("请求 URL 超出本轮确认目标地址的 origin")


def _absolute_http_url(value: Any, base_url: Any) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip() or any(char in value for char in "\r\n"):
        raise CaseContractError("请求 URL 不能为空、不能含首尾空白或换行")
    request_parts = urlsplit(value)
    if base_url is not None and not request_parts.scheme and not request_parts.netloc:
        if not isinstance(base_url, str) or not base_url.strip() or base_url != base_url.strip():
            raise CaseContractError("base_url 必须是合法的 HTTP(S) URL")
        base_parts = urlsplit(base_url)
        if base_parts.scheme.lower() not in {"http", "https"} or not base_parts.netloc or not base_parts.hostname:
            raise CaseContractError("base_url 必须是完整的 HTTP(S) URL")
        # A configured base_url is an API prefix, not a file URL. Preserve its
        # path for both editor conventions: "users" and "/users".
        prefix = base_url.rstrip("/") + "/"
        value = urljoin(prefix, value.lstrip("/"))
    parsed = urlsplit(value)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc or not parsed.hostname:
        raise CaseContractError("请求 URL 必须是完整的 HTTP(S) URL，或提供合法 base_url 的相对路径")
    return value


def _header_value(headers: Mapping[str, Any], name: str) -> Any:
    for key, value in headers.items():
        if str(key).lower() == name.lower():
            return value
    raise KeyError(name)


def _http_headers(value: Mapping[str, Any]) -> dict[str, str]:
    """Convert the HTTP-only header boundary without coercing JSON variables."""
    result: dict[str, str] = {}
    for raw_key, raw_value in value.items():
        key, item = str(raw_key), str(raw_value)
        if not key or any(char in key for char in "\r\n:") or any(char in item for char in "\r\n"):
            raise CaseContractError("HTTP Header 名称或值不能包含换行，名称不能含冒号")
        result[key] = item
    return result


def _path_tokens(path: str) -> list[str]:
    # Supports dot paths and basic JSONPath-like ["key"] / [0] segments.
    return [token for token in re.findall(r"(?:^|\.)([^.\[\]]+)|\[\s*['\"]?([^\]'\"]+)['\"]?\s*\]", path) for token in token if token != ""]


def _select(selector: str, context: Mapping[str, Any]) -> Any:
    value = selector.strip()
    if value.startswith("$."):
        value = value[2:]
    elif value.startswith("$"):
        value = value[1:]
    value = value.lstrip(".")
    aliases = {"status": "status_code", "pathstatus": "status_code", "header": "headers"}
    first, dot, rest = value.partition(".")
    first = aliases.get(first, first)
    if first not in context:
        raise CaseContractError(f"不支持的响应选择器：{selector}")
    current: Any = context[first]
    if not dot:
        return current
    for token in _path_tokens(rest):
        if isinstance(current, Mapping):
            if first == "headers" and current is context["headers"]:
                current = _header_value(current, token)
            elif token in current:
                current = current[token]
            else:
                raise KeyError(token)
        elif isinstance(current, list):
            try:
                current = current[int(token)]
            except (ValueError, IndexError) as exc:
                raise KeyError(token) from exc
        else:
            raise KeyError(token)
    return current


def _response_body(response: Any) -> Any:
    try:
        return response.json()
    except (ValueError, json.JSONDecodeError, AttributeError):
        text = getattr(response, "text", None)
        if isinstance(text, str):
            return text
        content = getattr(response, "content", b"")
        if isinstance(content, bytes):
            return content.decode("utf-8", errors="replace")
        return content


def _elapsed_ms(response: Any, fallback_started: float) -> float:
    elapsed = getattr(response, "elapsed", None)
    if elapsed is not None and hasattr(elapsed, "total_seconds"):
        return round(float(elapsed.total_seconds()) * 1000, 3)
    return round((time.monotonic() - fallback_started) * 1000, 3)


def _contains(actual: Any, expected: Any) -> bool:
    if isinstance(actual, Mapping) and isinstance(expected, Mapping):
        return all(key in actual and actual[key] == value for key, value in expected.items())
    try:
        return expected in actual
    except TypeError:
        return False


def _compare(comparator: str, actual: Any, expected: Any) -> bool:
    if comparator == "eq":
        return actual == expected
    if comparator == "ne":
        return actual != expected
    if comparator == "contains":
        return _contains(actual, expected)
    if comparator == "not_contains":
        return not _contains(actual, expected)
    if comparator in {"gt", "ge", "lt", "le"}:
        return {"gt": actual > expected, "ge": actual >= expected, "lt": actual < expected, "le": actual <= expected}[comparator]
    if comparator == "type":
        if not isinstance(expected, str) or expected.lower() not in _TYPE_NAMES:
            raise CaseContractError("type 断言的预期值必须是支持的类型名")
        expected_type = _TYPE_NAMES[expected.lower()]
        # bool is an int subclass but must not satisfy numeric editor assertions.
        return isinstance(actual, expected_type) and not (expected.lower() in {"int", "integer", "float", "number"} and isinstance(actual, bool))
    if comparator == "length":
        try:
            return len(actual) == expected
        except TypeError as exc:
            raise CaseContractError("length 断言的检查值没有长度") from exc
    raise UnsupportedCaseFeature(f"不支持的比较器：{comparator}")


def _validator_record(validator: Mapping[str, Any], context: Mapping[str, Any], variables: Mapping[str, Any]) -> dict[str, Any]:
    comparator = validator["comparator"]
    check = validator["check"]
    raw_expected = deepcopy(validator["expect"])
    try:
        expected = _substitute(raw_expected, variables)
        actual = _select(check, context)
        passed = _compare(comparator, actual, expected)
        message = "断言通过" if passed else f"断言失败：{check} {comparator} {expected!r}，实际为 {actual!r}"
    except (CaseContractError, KeyError, TypeError, ValueError) as exc:
        actual, expected, passed, message = None, raw_expected, False, f"断言无法执行：{exc}"
    return {
        "comparator": comparator,
        "check": check,
        "check_value": actual,
        "expect": expected,
        "expect_value": expected,
        "check_result": "pass" if passed else "fail",
        "passed": passed,
        "message": message,
    }


def _empty_step(
    name: str, *, status: str, error: str = "", log: str = "",
    request_view: Mapping[str, Any] | None = None, elapsed_ms: float = 0,
) -> dict[str, Any]:
    validators = {"validate_extractor": []}
    req_resps: list[dict[str, Any]] = []
    if request_view is not None:
        req_resps.append({
            "request": deepcopy(dict(request_view)),
            "response": {
                "status_code": None, "headers": {}, "body": None, "content": None,
                "url": request_view.get("url", ""), "elapsed_ms": elapsed_ms, "error": error,
            },
        })
    return {
        "name": name,
        "success": False,
        "status": status,
        "data": {"req_resps": req_resps, "validators": validators, "stat": {"elapsed_ms": elapsed_ms}},
        "validators": validators,
        "export_vars": {},
        "attachment": {},
        "error": error,
        "log": log,
    }


def _report(*, script_id: str, name: str, started_wall: datetime, started_monotonic: float, steps: list[dict[str, Any]], error: str = "", error_type: str = "") -> dict[str, Any]:
    duration = round(time.monotonic() - started_monotonic, 6)
    succeeded = sum(1 for step in steps if step["status"] == "passed")
    failed = sum(1 for step in steps if step["status"] == "failed")
    errored = sum(1 for step in steps if step["status"] == "error")
    skipped = sum(1 for step in steps if step["status"] == "skipped")
    success = not error and failed == 0 and errored == 0 and skipped == 0
    status = "passed" if success else ("failed" if error_type in {"ValidationFailure", "ExtractionFailure"} else "error")
    log = "\n".join(step["log"] for step in steps if step.get("log"))
    return {
        "success": success,
        "status": status,
        "script_id": str(script_id),
        "name": name,
        "stat": {
            "testcases": {"total": 1, "success": 1 if success else 0, "fail": 0 if success else 1},
            "teststeps": {"total": len(steps), "successes": succeeded, "failures": failed, "errors": errored, "skipped": skipped},
        },
        "time": {"start_at": started_wall.isoformat(), "duration": duration},
        "step_datas": steps,
        "error": error,
        "error_type": error_type,
        "log": log,
    }


def build_error_report(script_id: str, error: str, error_type: str = "CaseContractError", *, name: str = "") -> dict[str, Any]:
    """Build the stable report used when execution cannot be started."""
    started_wall = datetime.now(timezone.utc)
    started_monotonic = time.monotonic()
    return _report(
        script_id=script_id, name=name, started_wall=started_wall,
        started_monotonic=started_monotonic, steps=[], error=error, error_type=error_type,
    )


def hard_timeout_report(script_id: str, case: Mapping[str, Any], total_timeout: float) -> dict[str, Any]:
    """Report a killed worker without inventing any per-step execution state.

    The current worker protocol has no checkpoint stream. A process kill cannot
    establish which requests reached a remote service, so callers receive no
    fabricated ``step_datas``. A future protocol may append verified completed
    checkpoints, one in-flight error and then skipped steps, but only after it
    actually transports that evidence to the parent process.
    """
    started_wall = datetime.now(timezone.utc)
    started_monotonic = time.monotonic()
    message = (
        f"平台硬总超时（{total_timeout:g} 秒）；已终止并回收本地 worker。"
        "逐步结果未完整返回，远端请求是否生效未知；如用例包含写操作，请检查测试数据。"
    )
    return _report(
        script_id=script_id, name=str(case["config"].get("name") or script_id),
        started_wall=started_wall, started_monotonic=started_monotonic, steps=[],
        error=message, error_type="HardTimeout",
    )


def resolve_total_timeout(case: Mapping[str, Any], options: Mapping[str, Any] | None = None) -> float:
    """Validate runtime timeouts and return the bounded platform deadline."""
    if options is not None and not isinstance(options, Mapping):
        raise CaseContractError("options 必须是 JSON 对象")
    return _runtime_options(case, options)["total_timeout"]


def run_case(script_id: str, script_content: Any, base_url: str | None = None, options: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Execute one normalised case with one isolated ``requests.Session``.

    A failure never raises into callers: it becomes a stable report. The
    session is scoped to this call, which shares cookies among steps but never
    leaks cookies or extracted variables to another case. ``total_timeout`` is
    cooperative here; the platform wrapper supplies the hard wall-clock limit.
    """
    started_wall = datetime.now(timezone.utc)
    started_monotonic = time.monotonic()
    steps: list[dict[str, Any]] = []
    try:
        if options is not None and not isinstance(options, Mapping):
            raise CaseContractError("options 必须是 JSON 对象")
        option_mapping = dict(options or {})
        case = normalize_case(script_content)
        if not case["teststeps"]:
            raise CaseContractError("用例至少需要一个测试步骤才能执行")
        config = case["config"]
        # Builtins are defaults only. Config and frozen execution options can
        # deliberately override either value for deterministic reruns.
        variables = _builtin_variables()
        variables.update(deepcopy(dict(config["variables"])))
        raw_option_variables = option_mapping.get("variables", {})
        if not isinstance(raw_option_variables, Mapping):
            raise CaseContractError("options.variables 必须是 JSON 对象")
        option_variables = dict(raw_option_variables)
        variables.update(deepcopy(option_variables))
        variables = _resolve_variable_definitions(variables)
        _validate_known_variables(case, variables)
        runtime = _runtime_options(case, option_mapping)
        resolved_base_url = _substitute(base_url if base_url is not None else config.get("base_url"), variables)
    except (CaseContractError, UnsupportedCaseFeature) as exc:
        return _report(script_id=script_id, name="", started_wall=started_wall, started_monotonic=started_monotonic, steps=steps, error=str(exc), error_type=type(exc).__name__)

    session = requests.Session()
    deadline = started_monotonic + runtime["total_timeout"]
    terminal_error = ""
    terminal_error_type = ""
    try:
        for index, step in enumerate(case["teststeps"], start=1):
            if terminal_error:
                steps.append(_empty_step(step["name"], status="skipped", error="前序步骤失败，未发送请求", log=f"步骤 {index} skipped：前序步骤失败"))
                continue
            attempted_request: dict[str, Any] | None = None
            request_started: float | None = None
            try:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("用例总超时，未发送请求")
                request = _substitute(step["request"], variables)
                url = _absolute_http_url(request.pop("url"), resolved_base_url)
                _assert_allowed_origin(url, runtime["allowed_origin"])
                headers = _http_headers({**runtime["headers"], **dict(request.pop("headers", {}))})
                params = request.pop("params", None)
                raw_request_timeout = request.pop("timeout", None)
                if raw_request_timeout is None:
                    connect_timeout, read_timeout = runtime["connect_timeout"], runtime["read_timeout"]
                else:
                    request_timeout = _positive_timeout(
                        raw_request_timeout, f"步骤 {index}.request.timeout", MAX_REQUEST_TIMEOUT_SECONDS,
                    )
                    connect_timeout = read_timeout = request_timeout
                request_verify = request.pop("verify", runtime["verify"])
                request_redirects = request.pop("allow_redirects", True)
                request_cookies = request.pop("cookies", None)
                if not isinstance(request_verify, bool) or not isinstance(request_redirects, bool):
                    raise CaseContractError(f"步骤 {index} 的 verify/allow_redirects 必须是布尔值")
                if request_cookies is not None and not isinstance(request_cookies, Mapping):
                    raise CaseContractError(f"步骤 {index} 的 cookies 必须是 JSON 对象")
                body_keys = [key for key in ("json", "data", "raw") if key in request and request[key] is not None]
                request_kwargs: dict[str, Any] = {
                    "method": request.pop("method"), "url": url, "headers": headers,
                    "params": params, "verify": request_verify, "allow_redirects": request_redirects,
                    "timeout": (min(connect_timeout, remaining / 2), min(read_timeout, remaining / 2)),
                }
                if request_cookies is not None:
                    request_kwargs["cookies"] = dict(request_cookies)
                if body_keys:
                    key = body_keys[0]
                    request_kwargs["data" if key == "raw" else key] = request.pop(key)
                if request:
                    raise UnsupportedCaseFeature(f"步骤 {index} request 含不支持字段：{', '.join(sorted(request))}")
                request_body = request_kwargs.get("json", request_kwargs.get("data"))
                attempted_request = {
                    "method": request_kwargs["method"], "url": url, "headers": headers,
                    "params": params, "body": request_body,
                }
                request_started = time.monotonic()
                response = session.request(**request_kwargs)
                body = _response_body(response)
                response_headers = dict(getattr(response, "headers", {}) or {})
                status_code = getattr(response, "status_code", None)
                response_context = {
                    "body": body, "status_code": status_code, "headers": response_headers,
                    "extract": variables,
                }
                exported: dict[str, Any] = {}
                extraction_results: list[dict[str, Any]] = []
                extraction_errors: list[str] = []
                for name, selector in step["extract"].items():
                    try:
                        exported[name] = deepcopy(_select(selector, response_context))
                        extraction_results.append({"name": name, "selector": selector, "passed": True})
                    except (CaseContractError, KeyError, TypeError, ValueError) as exc:
                        message = f"步骤 {index} 提取变量 {name} 失败：{exc}"
                        extraction_errors.append(message)
                        extraction_results.append({"name": name, "selector": selector, "passed": False, "error": str(exc)})
                # A partially extracted response is useful evidence, but never a
                # source of variables for later requests after extraction failed.
                if not extraction_errors:
                    variables.update(exported)
                    response_context["extract"] = variables
                records = [
                    _validator_record(
                        _parse_validator(raw_validator, f"步骤 {index} 断言"), response_context, variables,
                    )
                    for raw_validator in step["validate"]
                ]
                passed = all(record["passed"] for record in records)
                elapsed = _elapsed_ms(response, request_started)
                response_view = {
                    "status_code": status_code, "headers": response_headers, "body": body,
                    "content": getattr(response, "text", None), "url": getattr(response, "url", url), "elapsed_ms": elapsed,
                }
                validators = {"validate_extractor": records}
                passed = passed and not extraction_errors
                step_error = (extraction_errors[0] if extraction_errors else "") or (
                    "" if passed else next(record["message"] for record in records if not record["passed"])
                )
                steps.append({
                    "name": step["name"], "success": passed, "status": "passed" if passed else "failed",
                    "data": {"req_resps": [{"request": attempted_request, "response": response_view}], "validators": validators, "stat": {"elapsed_ms": elapsed, "response_time_ms": elapsed}},
                    "validators": validators, "export_vars": exported, "extraction_results": extraction_results,
                    "attachment": {}, "error": step_error,
                    "log": f"步骤 {index} {'成功' if passed else '失败'}：{request_kwargs['method']} {url}",
                })
                if not passed:
                    terminal_error = step_error
                    terminal_error_type = "ExtractionFailure" if extraction_errors else "ValidationFailure"
            except TimeoutError as exc:
                terminal_error, terminal_error_type = str(exc), "Timeout"
                elapsed = round((time.monotonic() - request_started) * 1000, 3) if request_started is not None else 0
                steps.append(_empty_step(step["name"], status="error", error=terminal_error, log=f"步骤 {index} 超时：{terminal_error}", request_view=attempted_request, elapsed_ms=elapsed))
            except (CaseContractError, UnsupportedCaseFeature) as exc:
                terminal_error, terminal_error_type = str(exc), type(exc).__name__
                elapsed = round((time.monotonic() - request_started) * 1000, 3) if request_started is not None else 0
                steps.append(_empty_step(step["name"], status="error", error=terminal_error, log=f"步骤 {index} 执行错误：{terminal_error}", request_view=attempted_request, elapsed_ms=elapsed))
            except requests.RequestException as exc:
                terminal_error, terminal_error_type = str(exc), type(exc).__name__
                elapsed = round((time.monotonic() - request_started) * 1000, 3) if request_started is not None else 0
                steps.append(_empty_step(step["name"], status="error", error=terminal_error, log=f"步骤 {index} 请求错误：{terminal_error}", request_view=attempted_request, elapsed_ms=elapsed))
            except Exception as exc:  # requests adapters may raise implementation-specific errors.
                terminal_error, terminal_error_type = str(exc), type(exc).__name__
                elapsed = round((time.monotonic() - request_started) * 1000, 3) if request_started is not None else 0
                steps.append(_empty_step(step["name"], status="error", error=terminal_error, log=f"步骤 {index} 执行错误：{terminal_error}", request_view=attempted_request, elapsed_ms=elapsed))
    finally:
        session.close()
    return _report(script_id=script_id, name=str(config.get("name") or script_id), started_wall=started_wall, started_monotonic=started_monotonic, steps=steps, error=terminal_error, error_type=terminal_error_type)


def export_python(value: Any) -> str:
    """Create a readable standalone Python or pytest executable.

    The generated file intentionally shows a read-only case summary and Python
    literal before the embedded runtime. It is still one-way: the source case
    remains the editor's canonical JSON and generated Python is never parsed
    back into AITS.
    """
    case = normalize_case(value)
    runtime_source = inspect.getsource(sys.modules[__name__]).rstrip()
    future_marker = chr(10) + "from __future__ import annotations" + chr(10)
    marker_index = runtime_source.find(future_marker)
    if marker_index < 0:  # Defensive: generated source must retain valid future-import placement.
        raise RuntimeError("无法定位运行核心的 future import")
    preamble = runtime_source[:marker_index + len(future_marker)]
    runtime_body = runtime_source[marker_index + len(future_marker):].lstrip(chr(10))
    scenario = json.dumps(str(case["config"].get("name") or "未命名场景"), ensure_ascii=False)
    summary_lines = [f"# 场景：{scenario}", "# 步骤摘要（只读；请在 AITS 编辑器修改后重新导出）："]
    for index, step in enumerate(case["teststeps"], start=1):
        request = step["request"]
        name = json.dumps(str(step["name"]), ensure_ascii=False)
        method = json.dumps(str(request["method"]), ensure_ascii=False)
        path = json.dumps(str(request["url"]), ensure_ascii=False)
        summary_lines.append(f"# {index}. 名称={name} 方法={method} 路径={path}")
    case_literal = pprint.pformat(case, width=100, sort_dicts=False)
    return f'''{preamble}

{chr(10).join(summary_lines)}
# 下方 CASE 是排版后的只读导出；不支持从 Python 反向同步到 AITS。
CASE = {case_literal}


{runtime_body}


def test_generated_api_case():
    result = run_case("exported-case", CASE)
    assert result["success"], result["error"] or result["log"]


if __name__ == "__main__":
    _result = run_case("exported-case", CASE)
    print(json.dumps(_result, ensure_ascii=False, indent=2, default=str))
    raise SystemExit(0 if _result["success"] else 1)
'''


__all__ = [
    "CaseContractError", "UnsupportedCaseFeature", "normalize_case", "run_case", "export_python",
    "build_error_report", "hard_timeout_report", "resolve_total_timeout",
]
