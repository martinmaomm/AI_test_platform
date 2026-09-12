"""A small, deterministic requests runtime for API test-case JSON.

This module intentionally has no Django or Automation Platform imports.  ``export_python``
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
    "eq", "ne", "contains", "not_contains", "gt", "ge", "lt", "le", "type", "length", "length_gt",
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


def _validate_length_threshold(value: Any) -> None:
    if type(value) is not int or value < 0:
        raise CaseContractError("长度大于（length_gt）断言的预期值必须是非负整数，例如 0（不加引号）")


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
    if comparator == "length_gt" and not (isinstance(args[1], str) and _FULL_VARIABLE.fullmatch(args[1])):
        _validate_length_threshold(args[1])
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
    # API business requests always skip server certificate validation.  Remove
    # legacy per-case switches rather than persisting a misleading override.
    config.pop("verify", None)
    config.pop("verify_ssl", None)
    variables = config.get("variables", {})
    if not isinstance(variables, Mapping):
        raise CaseContractError("config.variables 必须是 JSON 对象")
    for name in variables:
        if not isinstance(name, str) or not _VARIABLE_NAME.fullmatch(name):
            raise CaseContractError(f"config.variables 变量名无效：{name!r}")
    headers = config.get("headers", {})
    if not isinstance(headers, Mapping):
        raise CaseContractError("config.headers 必须是 JSON 对象")
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
        phase = step.get("phase", "main")
        if phase not in {"main", "cleanup"}:
            raise CaseContractError(f"{field}.phase 只能是 main 或 cleanup")
        if phase == "cleanup":
            requires = step.get("requires")
            if not isinstance(requires, list) or not requires:
                raise CaseContractError(f"{field}.requires 必须是非空变量名数组")
            invalid_requires = [
                name for name in requires
                if not isinstance(name, str) or not _VARIABLE_NAME.fullmatch(name)
            ]
            if invalid_requires:
                raise CaseContractError(f"{field}.requires 包含无效变量名：{invalid_requires[0]!r}")
            if len(set(requires)) != len(requires):
                raise CaseContractError(f"{field}.requires 不允许重复变量名")
        elif "requires" in step:
            raise CaseContractError(f"{field}.requires 仅允许 cleanup 步骤使用")
        supported_request_fields = {
            "method", "url", "headers", "params", "json", "data", "raw",
            "timeout", "allow_redirects", "cookies", "verify", "verify_ssl",
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
        if "allow_redirects" in request and not isinstance(request["allow_redirects"], bool):
            raise CaseContractError(f"{field}.request.allow_redirects 必须是布尔值")
        request.pop("verify", None)
        request.pop("verify_ssl", None)
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
            validate_selector(selector)
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
        for position, validator in enumerate(step["validate"]):
            validate_selector(_parse_validator(validator, f"{field}.validate[{position}]")["check"])
        step.pop("validators", None)
        if phase == "cleanup":
            step["phase"] = "cleanup"
            step["requires"] = list(requires)
            cleanup_location_variables = set().union(*(
                _variable_names(request.get(key))
                for key in ("url", "params", "json", "data", "raw")
                if key in request
            ))
            undeclared = cleanup_location_variables.difference(requires)
            if undeclared:
                raise CaseContractError(
                    f"{field} 清理定位载荷引用了未在 requires 声明的变量：{', '.join(sorted(undeclared))}"
                )
            if not cleanup_location_variables.intersection(requires):
                raise CaseContractError(
                    f"{field}.requires 至少一个变量必须实际用于 url/params/json/data/raw 清理定位载荷"
                )
        else:
            if "phase" in raw_step:
                step["phase"] = "main"
            else:
                step.pop("phase", None)
        normalised_steps.append(step)

    result = deepcopy(raw)
    result["version"] = raw.get("version", 1)
    result["config"] = config
    result["config"]["variables"] = deepcopy(dict(variables))
    result["config"]["headers"] = deepcopy(dict(headers))
    result["config"].setdefault("name", "")
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


def _validate_known_variables(case: Mapping[str, Any], variables: Mapping[str, Any], *,
                              headers: Mapping[str, Any] | None = None) -> set[tuple[int, str]]:
    """Reject statically unknown placeholders before any HTTP request is sent."""
    available = set(variables)
    definitions: dict[str, tuple[int, str] | None] = {name: None for name in available}
    extraction_sources: dict[tuple[int, str], set[tuple[int, str]]] = {}
    require_unique: set[tuple[int, str]] = set()
    config = case["config"]

    def definition_sources(definition: tuple[int, str], seen: set[tuple[int, str]] | None = None) -> set[tuple[int, str]]:
        seen = set() if seen is None else seen
        if definition in seen:
            return set()
        seen.add(definition)
        sources = extraction_sources.get(definition)
        if sources is None:
            return {definition}
        # Track both ends so a transfer cannot hide a filtered or unselected
        # list source. Explicit indexes are handled by the selector itself.
        return {definition}.union(*(definition_sources(source, seen) for source in sources))

    def source_definitions(name: str, seen: set[str] | None = None) -> set[tuple[int, str]]:
        """Follow config aliases until a current extraction definition takes over.

        Runtime resolves config templates before requests begin, so this is a
        deliberately conservative write guard: an unshadowed config alias
        still protects any extracted variable it names. An extraction of the
        alias name itself is a new definition version and stops that lookup.
        """
        seen = set() if seen is None else seen
        if name in seen:
            return set()
        seen.add(name)
        definition = definitions.get(name)
        if definition is not None:
            return definition_sources(definition)
        raw_definition = config.get("variables", {}).get(name)
        if raw_definition is None:
            return set()
        return set().union(*(source_definitions(item, seen) for item in _variable_names(raw_definition)))

    for value in (config.get("base_url"),):
        missing = _variable_names(value).difference(available)
        if missing:
            raise CaseContractError(f"未知变量：{', '.join(sorted(missing))}")
    indexed_steps = list(enumerate(case["teststeps"], start=1))
    execution_steps = [item for item in indexed_steps if item[1].get("phase", "main") == "main"]
    execution_steps.extend(item for item in indexed_steps if item[1].get("phase", "main") == "cleanup")
    for index, step in execution_steps:
        request = step["request"]
        for key in ("url", "params", "json", "data", "raw"):
            if key in request:
                missing = _variable_names(request[key]).difference(available)
                if missing:
                    raise CaseContractError(f"步骤 {index} 请求前发现未知变量：{', '.join(sorted(missing))}")
        effective_headers = _merge_headers(
            headers if headers is not None else config.get("headers") or {}, request.get("headers") or {},
        )
        missing_headers = _variable_names(effective_headers).difference(available)
        if missing_headers:
            raise CaseContractError(
                f"步骤 {index} 的有效 headers 使用了当前未定义的变量：{', '.join(sorted(missing_headers))}；"
                "依赖登录提取值的 header 请放到登录后的具体步骤。"
            )
        for name, selector in step["extract"].items():
            missing = _variable_names(selector).difference(available)
            if missing:
                raise CaseContractError(f"步骤 {index} 提取 {name} 时发现未知变量：{', '.join(sorted(missing))}")
        for position, raw_validator in enumerate(step["validate"]):
            validator = _parse_validator(raw_validator, f"teststeps[{index - 1}].validate[{position}]")
            missing = _variable_names(validator["check"]).difference(available | set(step["extract"]))
            if missing:
                raise CaseContractError(f"步骤 {index} 断言选择器中发现未知变量：{', '.join(sorted(missing))}")
            missing = _variable_names(validator["expect"]).difference(available | set(step["extract"]))
            if missing:
                raise CaseContractError(f"步骤 {index} 断言中发现未知变量：{', '.join(sorted(missing))}")
        if request["method"] not in {"GET", "HEAD", "OPTIONS"}:
            request_values = [request.get(key) for key in ("url", "params", "json", "data", "raw", "cookies") if key in request]
            request_values.append(effective_headers)
            for variable_name in set().union(*(_variable_names(value) for value in request_values)):
                require_unique.update(source_definitions(variable_name))
        pending_definitions = {}
        for name, selector in step["extract"].items():
            definition = (index, name)
            root, operations = _parse_selector(selector)
            # ``extract.old_id`` (including a following key/index path) copies
            # the current variable value. Track only that explicit first name;
            # do not infer arbitrary dependencies from a whole extract object.
            if root == "extract" and operations and operations[0][0] == "key":
                extraction_sources[definition] = source_definitions(operations[0][1])
            elif root == "extract" and not operations:
                extraction_sources[definition] = set().union(*(source_definitions(item) for item in definitions))
            pending_definitions[name] = definition
        definitions.update(pending_definitions)
        available.update(step["extract"])
    return require_unique


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
        "headers": _merge_headers(headers, option_headers),
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


def _merge_headers(global_headers: Mapping[str, Any], step_headers: Mapping[str, Any]) -> dict[str, Any]:
    """Overlay step headers using HTTP's case-insensitive field names."""
    merged = {}
    known = {}
    for source in (global_headers, step_headers):
        for key, value in source.items():
            replaced = known.get(str(key).lower())
            if replaced is not None:
                merged.pop(replaced, None)
            merged[key] = value
            known[str(key).lower()] = key
    return merged


_SELECTOR_ROOTS = {"body", "status_code", "headers", "extract"}
_SELECTOR_ALIASES = {"status": "status_code", "pathstatus": "status_code", "header": "headers"}


class _SelectorParser:
    """Parse the deliberately small response selector grammar without eval."""

    def __init__(self, selector: str):
        self.selector = selector
        self.text = selector.strip()
        self.position = 0

    def error(self, detail: str) -> CaseContractError:
        return CaseContractError(f"不支持的响应选择器 {self.selector!r}：{detail}")

    def skip_spaces(self) -> None:
        while self.position < len(self.text) and self.text[self.position].isspace():
            self.position += 1

    def expect(self, token: str) -> None:
        if not self.text.startswith(token, self.position):
            raise self.error(f"缺少 {token!r}")
        self.position += len(token)

    def bare_key(self, *, filter_field: bool = False) -> str:
        start = self.position
        stop = ".[]" + ("=)" if filter_field else "")
        while self.position < len(self.text) and self.text[self.position] not in stop:
            character = self.text[self.position]
            if character.isspace() or character in "?@()*'\"{}":
                break
            self.position += 1
        key = self.text[start:self.position]
        if not key:
            raise self.error("路径键不能为空；特殊键请使用带引号的 []")
        if any(character in key for character in "*?@$()"):
            raise self.error("不支持通配符、函数或动态路径")
        return key

    def quoted_string(self) -> str:
        quote = self.text[self.position]
        self.position += 1
        result: list[str] = []
        escapes = {"b": "\b", "f": "\f", "n": "\n", "r": "\r", "t": "\t", "\\": "\\", "\"": '"', "'": "'", "/": "/"}
        while self.position < len(self.text):
            character = self.text[self.position]
            self.position += 1
            if character == quote:
                return "".join(result)
            if character in "\r\n":
                raise self.error("字符串不能包含未转义换行")
            if character != "\\":
                result.append(character)
                continue
            if self.position >= len(self.text):
                raise self.error("字符串转义不完整")
            escaped = self.text[self.position]
            self.position += 1
            if escaped == "u":
                code = self.text[self.position:self.position + 4]
                if len(code) != 4 or any(item not in "0123456789abcdefABCDEF" for item in code):
                    raise self.error("Unicode 转义非法")
                self.position += 4
                result.append(chr(int(code, 16)))
            elif escaped in escapes:
                result.append(escapes[escaped])
            else:
                raise self.error("字符串转义非法")
        raise self.error("字符串缺少结束引号")

    def filter_field(self) -> tuple[str, ...]:
        self.expect("@")
        self.expect(".")
        keys = [self.bare_key(filter_field=True)]
        while self.position < len(self.text) and self.text[self.position] == ".":
            self.position += 1
            keys.append(self.bare_key(filter_field=True))
        return tuple(keys)

    def filter_literal(self) -> tuple[str, Any]:
        self.skip_spaces()
        if self.position >= len(self.text):
            raise self.error("筛选条件缺少等值右侧数据")
        if self.text[self.position] in "'\"":
            value: Any = self.quoted_string()
            variable = _FULL_VARIABLE.fullmatch(value)
            if variable:
                return "variable", next(name for name in variable.groups() if name is not None)
            return "literal", value
        start = self.position
        while self.position < len(self.text) and self.text[self.position] not in ") \t\r\n":
            self.position += 1
        token = self.text[start:self.position]
        variable = _FULL_VARIABLE.fullmatch(token)
        if variable:
            return "variable", next(name for name in variable.groups() if name is not None)
        try:
            value = json.loads(token)
        except json.JSONDecodeError as exc:
            raise self.error("筛选条件右侧只能是 JSON 标量或完整变量") from exc
        if isinstance(value, (list, dict)):
            raise self.error("筛选条件右侧只能是 JSON 标量")
        return "literal", value

    def bracket(self) -> tuple[str, Any]:
        self.expect("[")
        self.skip_spaces()
        if self.position >= len(self.text):
            raise self.error("缺少 ]")
        if self.text[self.position] in "'\"":
            key = self.quoted_string()
            self.skip_spaces()
            self.expect("]")
            return "key", key
        if self.text.startswith("?(", self.position):
            self.position += 2
            self.skip_spaces()
            field = self.filter_field()
            self.skip_spaces()
            self.expect("==")
            kind, value = self.filter_literal()
            self.skip_spaces()
            self.expect(")")
            self.skip_spaces()
            self.expect("]")
            return "filter", (field, kind, value)
        start = self.position
        while self.position < len(self.text) and self.text[self.position].isdigit():
            self.position += 1
        token = self.text[start:self.position]
        self.skip_spaces()
        if not token or not self.text.startswith("]", self.position):
            raise self.error("[] 只支持带引号的键、非负数字索引或单个等值筛选")
        self.position += 1
        return "index", int(token)

    def parse(self) -> tuple[str, tuple[tuple[str, Any], ...]]:
        if not self.text:
            raise self.error("不能为空")
        if self.text.startswith("$"):
            self.position = 1
            if self.position < len(self.text) and self.text[self.position] == ".":
                self.position += 1
        elif self.text.startswith("."):
            self.position = 1
        root_start = self.position
        self.bare_key()
        root_name = self.text[root_start:self.position]
        root = _SELECTOR_ALIASES.get(root_name, root_name)
        if root not in _SELECTOR_ROOTS:
            raise self.error("根节点仅支持 body、status_code、headers 或 extract")
        operations: list[tuple[str, Any]] = []
        while self.position < len(self.text):
            character = self.text[self.position]
            if character == ".":
                self.position += 1
                operations.append(("key", self.bare_key()))
            elif character == "[":
                operations.append(self.bracket())
            else:
                raise self.error("存在未解析残余")
        return root, tuple(operations)


def _parse_selector(selector: str) -> tuple[str, tuple[tuple[str, Any], ...]]:
    if not isinstance(selector, str):
        raise CaseContractError("响应选择器必须是字符串")
    return _SelectorParser(selector).parse()


def validate_selector(selector: str) -> None:
    """Reject unsupported selector syntax before a case is executed."""
    _parse_selector(selector)


def selector_has_filter(selector: str) -> bool:
    """Return whether a valid selector contains an exact-match list filter."""
    return any(kind == "filter" for kind, _ in _parse_selector(selector)[1])


def _filter_value(item: Any, field: tuple[str, ...]) -> Any:
    current = item
    for key in field:
        if not isinstance(current, Mapping) or key not in current:
            raise KeyError(key)
        current = current[key]
    return current


def _selector_equals(actual: Any, expected: Any) -> bool:
    # JSON booleans are not numbers for selector equality, unlike Python's True == 1.
    return type(actual) is type(expected) and actual == expected


def _select(selector: str, context: Mapping[str, Any], *, variables: Mapping[str, Any] | None = None,
            require_unique: bool = False) -> Any:
    """Select a response value using only parsed keys, indexes and one equality filter.

    A filter returns its list unchanged for validators (so ``length=0`` can
    assert absence). Extraction callers opt into ``require_unique`` whenever a
    filter exists or a value later drives a write request. It guards filter
    matches and unselected terminal lists, not explicitly indexed positions.
    """
    root, operations = _parse_selector(selector)
    if root not in context:
        raise CaseContractError(f"不支持的响应选择器：{selector}")
    bindings = variables if variables is not None else context.get("extract", {})
    if not isinstance(bindings, Mapping):
        raise CaseContractError("响应选择器变量上下文必须是 JSON 对象")
    current: Any = context[root]
    for position, (kind, payload) in enumerate(operations):
        if kind == "key":
            if not isinstance(current, Mapping):
                if isinstance(current, list):
                    if not payload.isdecimal():
                        raise CaseContractError("数组不支持隐式字段投影；请明确使用 [0].字段")
                    try:
                        current = current[int(payload)]
                    except IndexError as exc:
                        raise ExtractionFailure(f"选择器索引 [{payload}] 越界，当前列表长度为 {len(current)}") from exc
                    continue
                raise KeyError(payload)
            if root == "headers" and position == 0:
                current = _header_value(current, payload)
            elif payload in current:
                current = current[payload]
            else:
                raise KeyError(payload)
        elif kind == "index":
            if not isinstance(current, list):
                raise KeyError(payload)
            try:
                current = current[payload]
            except IndexError as exc:
                raise ExtractionFailure(f"选择器索引 [{payload}] 越界，当前列表长度为 {len(current)}") from exc
        else:
            if not isinstance(current, list):
                raise KeyError("filter")
            field, value_kind, value = payload
            if value_kind == "variable":
                if value not in bindings:
                    raise CaseContractError(f"未知变量：{value}")
                expected = bindings[value]
            else:
                expected = value
            current = [item for item in current if _filter_matches(item, field, expected)]
            if require_unique and len(current) != 1:
                raise ExtractionFailure(f"选择器筛选结果必须唯一匹配，实际为 {len(current)} 条")
    if require_unique and isinstance(current, list) and len(current) != 1:
        raise ExtractionFailure(f"选择器结果必须唯一匹配，实际为 {len(current)} 条")
    return current


def _filter_matches(item: Any, field: tuple[str, ...], expected: Any) -> bool:
    try:
        return _selector_equals(_filter_value(item, field), expected)
    except KeyError:
        return False


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
    if comparator == "length_gt":
        _validate_length_threshold(expected)
        try:
            return len(actual) > expected
        except TypeError as exc:
            raise CaseContractError("长度大于（length_gt）断言的检查值没有长度；请填写列表、字符串或对象的路径") from exc
    raise UnsupportedCaseFeature(f"不支持的比较器：{comparator}")


def _validator_record(validator: Mapping[str, Any], context: Mapping[str, Any], variables: Mapping[str, Any]) -> dict[str, Any]:
    comparator = validator["comparator"]
    check = validator["check"]
    raw_expected = deepcopy(validator["expect"])
    try:
        expected = _substitute(raw_expected, variables)
        actual = _select(check, context, variables=variables)
        passed = _compare(comparator, actual, expected)
        message = "断言通过" if passed else f"断言失败：{check} {comparator} {expected!r}，实际为 {actual!r}"
        if comparator == "length_gt":
            message = f"{'断言通过' if passed else '断言失败'}：{check} 的长度应大于 {expected}，实际长度为 {len(actual)}"
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
    phase: str = "main",
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
    result = {
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
    if phase == "cleanup":
        result["phase"] = "cleanup"
    return result


def _cleanup_status(steps: list[dict[str, Any]], cleanup_declared: bool) -> str:
    cleanup_steps = [step for step in steps if step.get("phase") == "cleanup"]
    if not cleanup_declared:
        return "not_required"
    if not cleanup_steps or all(step.get("status") == "skipped" for step in cleanup_steps):
        return "not_run"
    if any(step.get("status") in {"failed", "error", "unknown"} for step in cleanup_steps):
        return "failed"
    if any(step.get("status") == "skipped" for step in cleanup_steps):
        return "incomplete"
    return "completed"


def _replay_safety(*, success: bool, side_effects: bool, cleanup_status: str) -> dict[str, Any]:
    if success:
        safe_to_retry = True
        reason = "执行成功，无需失败重跑"
    elif side_effects:
        safe_to_retry = False
        reason = "已派发可能产生副作用的请求，无法严格证明回滚完整，禁止自动重跑"
    else:
        safe_to_retry = True
        reason = "未派发可能产生副作用的请求，可安全重跑"
    return {
        "safe_to_retry": safe_to_retry,
        "reason": reason,
        "side_effects": side_effects,
        "cleanup_status": cleanup_status,
    }


def _report(*, script_id: str, name: str, started_wall: datetime, started_monotonic: float,
            steps: list[dict[str, Any]], error: str = "", error_type: str = "",
            side_effects: bool = False, cleanup_declared: bool = False) -> dict[str, Any]:
    duration = round(time.monotonic() - started_monotonic, 6)
    succeeded = sum(1 for step in steps if step["status"] == "passed")
    failed = sum(1 for step in steps if step["status"] == "failed")
    errored = sum(1 for step in steps if step["status"] == "error")
    skipped = sum(1 for step in steps if step["status"] == "skipped")
    success = not error and failed == 0 and errored == 0 and skipped == 0
    status = "passed" if success else ("failed" if error_type in {"ValidationFailure", "ExtractionFailure"} else "error")
    log = "\n".join(step["log"] for step in steps if step.get("log"))
    cleanup_status = _cleanup_status(steps, cleanup_declared)
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
        "replay_safety": _replay_safety(
            success=success, side_effects=side_effects, cleanup_status=cleanup_status,
        ),
    }


def build_error_report(script_id: str, error: str, error_type: str = "CaseContractError", *, name: str = "") -> dict[str, Any]:
    """Build the stable report used when execution cannot be started."""
    started_wall = datetime.now(timezone.utc)
    started_monotonic = time.monotonic()
    return _report(
        script_id=script_id, name=name, started_wall=started_wall,
        started_monotonic=started_monotonic, steps=[], error=error, error_type=error_type,
    )


def interrupted_report(script_id: str, case: Mapping[str, Any], error_type: str, message: str,
                       *, partial_report: Mapping[str, Any] | None = None,
                       side_effects: bool = False) -> dict[str, Any]:
    """Build a terminal report from worker evidence without guessing outcomes."""
    started_wall = datetime.now(timezone.utc)
    started_monotonic = time.monotonic()
    cleanup_declared = any(step.get("phase", "main") == "cleanup" for step in case["teststeps"])
    if isinstance(partial_report, Mapping):
        raw_steps = partial_report.get("step_datas", [])
        steps = deepcopy(raw_steps) if isinstance(raw_steps, list) else []
        previous_safety = partial_report.get("replay_safety", {})
        side_effects = side_effects or bool(
            previous_safety.get("side_effects") if isinstance(previous_safety, Mapping) else False
        )
    else:
        steps = [
            _empty_step(
                step["name"], status="skipped", error="执行被中止，未发送请求",
                log=f"步骤 {index} skipped：执行被中止",
                phase=step.get("phase", "main"),
            )
            for index, step in enumerate(case["teststeps"], start=1)
        ]
    report = _report(
        script_id=script_id, name=str(case["config"].get("name") or script_id),
        started_wall=started_wall, started_monotonic=started_monotonic, steps=steps,
        error=message, error_type=error_type, side_effects=side_effects,
        cleanup_declared=cleanup_declared,
    )
    if isinstance(partial_report, Mapping):
        if isinstance(partial_report.get("time"), Mapping):
            report["time"]["start_at"] = partial_report["time"].get("start_at", report["time"]["start_at"])
        report["log"] = "\n".join(filter(None, [report.get("log", ""), message]))
    return report


def hard_timeout_report(script_id: str, case: Mapping[str, Any], total_timeout: float,
                        *, partial_report: Mapping[str, Any] | None = None,
                        side_effects: bool = False) -> dict[str, Any]:
    """Report a killed worker while preserving completed and in-flight checkpoints."""
    message = (
        f"平台硬总超时（{total_timeout:g} 秒）；已终止并回收本地 worker。"
        "当前请求结果及副作用状态未知；未执行步骤已标记 skipped，请检查测试数据与清理状态。"
    )
    return interrupted_report(
        script_id, case, "HardTimeout", message,
        partial_report=partial_report, side_effects=side_effects,
    )


def resolve_total_timeout(case: Mapping[str, Any], options: Mapping[str, Any] | None = None) -> float:
    """Validate runtime timeouts and return the bounded platform deadline."""
    if options is not None and not isinstance(options, Mapping):
        raise CaseContractError("options 必须是 JSON 对象")
    return _runtime_options(case, options)["total_timeout"]


def run_case(script_id: str, script_content: Any, base_url: str | None = None,
             options: Mapping[str, Any] | None = None, on_checkpoint: Any = None) -> dict[str, Any]:
    """Execute one normalised case with one isolated ``requests.Session``.

    A failure never raises into callers: it becomes a stable report. The
    session is scoped to this call, which shares cookies among steps but never
    leaks cookies or extracted variables to another case. ``total_timeout`` is
    cooperative here; the platform wrapper supplies the hard wall-clock limit.
    """
    started_wall = datetime.now(timezone.utc)
    started_monotonic = time.monotonic()
    completed_steps: dict[int, dict[str, Any]] = {}
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
        runtime = _runtime_options(case, option_mapping)
        unique_extracts = _validate_known_variables(case, variables, headers=runtime["headers"])
        resolved_base_url = _substitute(base_url if base_url is not None else config.get("base_url"), variables)
    except (CaseContractError, UnsupportedCaseFeature) as exc:
        return _report(script_id=script_id, name="", started_wall=started_wall,
                       started_monotonic=started_monotonic, steps=[], error=str(exc),
                       error_type=type(exc).__name__)

    session = requests.Session()
    deadline = started_monotonic + runtime["total_timeout"]
    primary_error = ""
    primary_error_type = ""
    cleanup_error = ""
    cleanup_error_type = ""
    side_effects = False
    successful_main_extracts: set[str] = set()
    indexed_steps = list(enumerate(case["teststeps"], start=1))
    main_steps = [item for item in indexed_steps if item[1].get("phase", "main") == "main"]
    cleanup_steps = [item for item in indexed_steps if item[1].get("phase", "main") == "cleanup"]
    execution_steps = main_steps + cleanup_steps
    cleanup_declared = bool(cleanup_steps)

    def current_report(current_index: int | None = None) -> dict[str, Any]:
        report_steps: list[dict[str, Any]] = []
        for index, step in execution_steps:
            if index in completed_steps:
                report_steps.append(deepcopy(completed_steps[index]))
            elif index == current_index:
                report_steps.append(_empty_step(
                    step["name"], status="unknown",
                    error="请求执行中，最终结果及副作用状态未知",
                    log=f"步骤 {index} unknown：等待 worker 完成 checkpoint",
                    phase=step.get("phase", "main"),
                ))
            else:
                report_steps.append(_empty_step(
                    step["name"], status="skipped", error="尚未执行",
                    log=f"步骤 {index} skipped：尚未执行",
                    phase=step.get("phase", "main"),
                ))
        error = primary_error or cleanup_error or ("执行尚未完成" if current_index is not None else "")
        error_type = primary_error_type or cleanup_error_type
        report = _report(
            script_id=script_id, name=str(config.get("name") or script_id),
            started_wall=started_wall, started_monotonic=started_monotonic,
            steps=report_steps, error=error, error_type=error_type,
            side_effects=side_effects, cleanup_declared=cleanup_declared,
        )
        report["checkpoint"] = {
            "completed_steps": len(completed_steps),
            "total_steps": len(execution_steps),
            "current_step": current_index,
        }
        return report

    def emit(event: str, index: int, *, current: bool = False) -> None:
        if on_checkpoint is None:
            return
        on_checkpoint({
            "event": event,
            "step_index": index,
            "report": current_report(index if current else None),
        })

    def complete_skipped(index: int, step: Mapping[str, Any], message: str) -> None:
        completed_steps[index] = _empty_step(
            step["name"], status="skipped", error=message,
            log=f"步骤 {index} skipped：{message}", phase=step.get("phase", "main"),
        )
        emit("step_completed", index)

    def execute_step(index: int, step: Mapping[str, Any]) -> tuple[str, str]:
        nonlocal side_effects
        phase = step.get("phase", "main")
        emit("step_started", index, current=True)
        attempted_request: dict[str, Any] | None = None
        request_started: float | None = None
        try:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("用例总超时，未发送请求")
            raw_request = deepcopy(step["request"])
            raw_headers = _merge_headers(runtime["headers"], raw_request.pop("headers", {}))
            request = _substitute(raw_request, variables)
            url = _absolute_http_url(request.pop("url"), resolved_base_url)
            _assert_allowed_origin(url, runtime["allowed_origin"])
            headers = _http_headers(_substitute(raw_headers, variables))
            params = request.pop("params", None)
            raw_request_timeout = request.pop("timeout", None)
            if raw_request_timeout is None:
                connect_timeout, read_timeout = runtime["connect_timeout"], runtime["read_timeout"]
            else:
                request_timeout = _positive_timeout(
                    raw_request_timeout, f"步骤 {index}.request.timeout", MAX_REQUEST_TIMEOUT_SECONDS,
                )
                connect_timeout = read_timeout = request_timeout
            request_redirects = request.pop("allow_redirects", True)
            request_cookies = request.pop("cookies", None)
            if not isinstance(request_redirects, bool):
                raise CaseContractError(f"步骤 {index} 的 allow_redirects 必须是布尔值")
            if request_cookies is not None and not isinstance(request_cookies, Mapping):
                raise CaseContractError(f"步骤 {index} 的 cookies 必须是 JSON 对象")
            body_keys = [key for key in ("json", "data", "raw") if key in request and request[key] is not None]
            request_kwargs: dict[str, Any] = {
                "method": request.pop("method"), "url": url, "headers": headers,
                "params": params, "verify": False, "allow_redirects": request_redirects,
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
            if request_kwargs["method"] not in {"GET", "HEAD", "OPTIONS"}:
                side_effects = True
            emit("request_dispatched", index, current=True)
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
                    exported[name] = deepcopy(_select(
                        selector, response_context, variables=variables,
                        require_unique=((index, name) in unique_extracts or selector_has_filter(selector)),
                    ))
                    extraction_results.append({"name": name, "selector": selector, "passed": True})
                except (CaseContractError, KeyError, TypeError, ValueError) as exc:
                    message = f"步骤 {index} 提取变量 {name} 失败：{exc}"
                    extraction_errors.append(message)
                    extraction_results.append({"name": name, "selector": selector, "passed": False, "error": str(exc)})
            if not extraction_errors:
                variables.update(exported)
                response_context["extract"] = variables
                if phase == "main":
                    successful_main_extracts.update(exported)
            records = [
                _validator_record(
                    _parse_validator(raw_validator, f"步骤 {index} 断言"), response_context, variables,
                )
                for raw_validator in step["validate"]
            ]
            passed = all(record["passed"] for record in records) and not extraction_errors
            elapsed = _elapsed_ms(response, request_started)
            response_view = {
                "status_code": status_code, "headers": response_headers, "body": body,
                "content": getattr(response, "text", None), "url": getattr(response, "url", url),
                "elapsed_ms": elapsed,
            }
            validators = {"validate_extractor": records}
            step_error = (extraction_errors[0] if extraction_errors else "") or (
                "" if passed else next(record["message"] for record in records if not record["passed"])
            )
            result_step = {
                "name": step["name"], "success": passed, "status": "passed" if passed else "failed",
                "data": {"req_resps": [{"request": attempted_request, "response": response_view}],
                         "validators": validators,
                         "stat": {"elapsed_ms": elapsed, "response_time_ms": elapsed}},
                "validators": validators, "export_vars": exported,
                "extraction_results": extraction_results, "attachment": {}, "error": step_error,
                "log": f"步骤 {index} {'成功' if passed else '失败'}：{request_kwargs['method']} {url}",
            }
            if phase == "cleanup":
                result_step["phase"] = "cleanup"
            completed_steps[index] = result_step
            if passed:
                return "", ""
            return step_error, "ExtractionFailure" if extraction_errors else "ValidationFailure"
        except TimeoutError as exc:
            step_error, step_error_type = str(exc), "Timeout"
            log_kind = "超时"
        except (CaseContractError, UnsupportedCaseFeature) as exc:
            step_error, step_error_type = str(exc), type(exc).__name__
            log_kind = "执行错误"
        except requests.RequestException as exc:
            step_error, step_error_type = str(exc), type(exc).__name__
            log_kind = "请求错误"
        except Exception as exc:  # requests adapters may raise implementation-specific errors.
            step_error, step_error_type = str(exc), type(exc).__name__
            log_kind = "执行错误"
        elapsed = round((time.monotonic() - request_started) * 1000, 3) if request_started is not None else 0
        completed_steps[index] = _empty_step(
            step["name"], status="error", error=step_error,
            log=f"步骤 {index} {log_kind}：{step_error}", request_view=attempted_request,
            elapsed_ms=elapsed, phase=phase,
        )
        return step_error, step_error_type

    try:
        for index, step in main_steps:
            if primary_error:
                complete_skipped(index, step, "前序 main 步骤失败，未发送请求")
                continue
            primary_error, primary_error_type = execute_step(index, step)
            emit("step_completed", index)

        for index, step in cleanup_steps:
            if deadline - time.monotonic() <= 0:
                message = "cleanup 未执行：用例总期限已耗尽，未发送请求"
                complete_skipped(index, step, message)
                if not primary_error and not cleanup_error:
                    cleanup_error, cleanup_error_type = message, "CleanupUnavailable"
                continue
            missing_requires = sorted(set(step["requires"]).difference(successful_main_extracts))
            if missing_requires:
                message = (
                    "cleanup 未执行：requires 变量必须来自本轮成功 main extract，缺少 "
                    + ", ".join(missing_requires)
                )
                complete_skipped(index, step, message)
                if not primary_error and not cleanup_error:
                    cleanup_error, cleanup_error_type = message, "CleanupUnavailable"
                continue
            step_error, step_error_type = execute_step(index, step)
            emit("step_completed", index)
            if step_error and not cleanup_error:
                cleanup_error, cleanup_error_type = step_error, step_error_type
    finally:
        session.close()
    ordered_results = [completed_steps[index] for index, _step in execution_steps]
    terminal_error = primary_error or cleanup_error
    terminal_error_type = primary_error_type or cleanup_error_type
    return _report(
        script_id=script_id, name=str(config.get("name") or script_id),
        started_wall=started_wall, started_monotonic=started_monotonic, steps=ordered_results,
        error=terminal_error, error_type=terminal_error_type, side_effects=side_effects,
        cleanup_declared=cleanup_declared,
    )


def export_python(value: Any) -> str:
    """Create a readable standalone Python or pytest executable.

    The generated file intentionally shows a read-only case summary and Python
    literal before the embedded runtime. It is still one-way: the source case
    remains the editor's canonical JSON and generated Python is never parsed
    back into Automation Platform.
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
    summary_lines = [f"# 场景：{scenario}", "# 步骤摘要（只读；请在自动化测试平台编辑器修改后重新导出）："]
    for index, step in enumerate(case["teststeps"], start=1):
        request = step["request"]
        name = json.dumps(str(step["name"]), ensure_ascii=False)
        method = json.dumps(str(request["method"]), ensure_ascii=False)
        path = json.dumps(str(request["url"]), ensure_ascii=False)
        summary_lines.append(f"# {index}. 名称={name} 方法={method} 路径={path}")
    case_literal = pprint.pformat(case, width=100, sort_dicts=False)
    return f'''{preamble}

{chr(10).join(summary_lines)}
# 下方 CASE 是排版后的只读导出；不支持从 Python 反向同步到自动化测试平台。
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
    "build_error_report", "hard_timeout_report", "interrupted_report", "resolve_total_timeout",
    "validate_selector", "selector_has_filter",
]
