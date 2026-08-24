"""HttpRunner 场景脚本的确定性预检查。

该模块只负责检查脚本内部和已提供响应契约中可以静态确认的问题，不发起网络请求，也不修改输入脚本。
后续可以在此基础上增加真实响应校验。
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any, Dict, List, Set
from urllib.parse import urlsplit


class ScenarioPreflightValidator:
    """检查场景脚本中的变量引用、响应路径和断言结构。"""

    _VARIABLE_PATTERN = re.compile(r"\$\{([^{}]+)\}")
    _VARIABLE_NAME_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
    _PATH_TAIL_PATTERN = re.compile(
        r"^(?:(?:\.[A-Za-z_][A-Za-z0-9_-]*)|(?:\[(?:\d+|\*|'[^']+'|\"[^\"]+\")\]))*$"
    )
    _FUNCTION_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_.]*\([^{}]*\)$")
    _RESPONSE_ROOTS = {"status_code", "body", "headers", "cookies"}
    _SUPPORTED_COMPARATORS = {
        "eq",
        "ne",
        "gt",
        "ge",
        "lt",
        "le",
        "contains",
        "startswith",
        "endswith",
        "regex",
        "type_match",
    }

    def validate(
        self,
        scenario: Mapping[str, Any],
        api_specifications: Sequence[Mapping[str, Any]] | None = None,
    ) -> Dict[str, Any]:
        """返回结构化检查报告，不修改 ``scenario``。"""

        errors: List[Dict[str, Any]] = []
        warnings: List[Dict[str, Any]] = []
        warning_keys: Set[str] = set()
        endpoint_contracts = self._build_endpoint_contract_index(api_specifications)

        if not isinstance(scenario, Mapping):
            self._add_issue(
                errors,
                kind="invalid_script",
                field="script",
                message="场景脚本必须是 JSON 对象",
                suggestion="检查 AI 返回的脚本格式",
            )
            return self._build_report(errors, warnings, 0)

        config = scenario.get("config", {})
        if not isinstance(config, Mapping):
            self._add_issue(
                errors,
                kind="invalid_config",
                field="config",
                message="config 必须是 JSON 对象",
                suggestion="将 config 调整为包含 name、base_url、variables 的对象",
            )
            config = {}

        global_variables = self._validate_variable_mapping(
            config.get("variables", {}),
            field="config.variables",
            step=0,
            errors=errors,
        )
        self._scan_values(
            config.get("variables", {}),
            available=global_variables,
            field="config.variables",
            step=0,
            errors=errors,
        )

        teststeps = scenario.get("teststeps")
        if not isinstance(teststeps, list):
            self._add_issue(
                errors,
                kind="invalid_teststeps",
                field="teststeps",
                message="teststeps 必须是数组",
                suggestion="检查 AI 是否返回了正确的 HttpRunner 场景结构",
            )
            return self._build_report(errors, warnings, 0)

        if not teststeps:
            self._add_issue(
                errors,
                kind="empty_teststeps",
                field="teststeps",
                message="场景至少需要包含一个测试步骤",
                suggestion="重新生成场景或手工添加测试步骤",
            )

        defined_variables = set(global_variables)
        extracted_variables: Set[str] = set()

        for index, step_data in enumerate(teststeps):
            step_number = index + 1
            field_prefix = f"teststeps[{index}]"

            if not isinstance(step_data, Mapping):
                self._add_issue(
                    errors,
                    kind="invalid_step",
                    step=step_number,
                    field=field_prefix,
                    message="测试步骤必须是 JSON 对象",
                    suggestion="检查该步骤的 name、request、extract 和 validate 字段",
                )
                continue

            step_variables = self._validate_variable_mapping(
                step_data.get("variables", {}),
                field=f"{field_prefix}.variables",
                step=step_number,
                errors=errors,
            )
            available_for_step = defined_variables | step_variables

            # 步骤变量在当前步骤执行前可用，但不允许循环引用自己。
            self._scan_values(
                step_data.get("variables", {}),
                available=available_for_step,
                field=f"{field_prefix}.variables",
                step=step_number,
                errors=errors,
                local_variable_names=step_variables,
            )

            request = step_data.get("request")
            response_context = self._get_response_context(request, endpoint_contracts)
            if not isinstance(request, Mapping):
                self._add_issue(
                    errors,
                    kind="invalid_request",
                    step=step_number,
                    field=f"{field_prefix}.request",
                    message="request 必须是 JSON 对象",
                    suggestion="检查请求方法和 URL 是否存在",
                )
            else:
                self._scan_values(
                    request,
                    available=available_for_step,
                    field=f"{field_prefix}.request",
                    step=step_number,
                    errors=errors,
                )

            extract = step_data.get("extract", {})
            if not isinstance(extract, Mapping):
                self._add_issue(
                    errors,
                    kind="invalid_extract",
                    step=step_number,
                    field=f"{field_prefix}.extract",
                    message="extract 必须是“变量名: 响应路径”的对象",
                    suggestion="例如：{\"user_id\": \"body.data.id\"}",
                )
                extract = {}

            current_extracts: Set[str] = set()
            for variable_name, response_path in extract.items():
                variable_name = str(variable_name)
                variable_field = f"{field_prefix}.extract.{variable_name}"

                if not self._VARIABLE_NAME_PATTERN.fullmatch(variable_name):
                    self._add_issue(
                        errors,
                        kind="invalid_variable_name",
                        step=step_number,
                        field=variable_field,
                        variable=variable_name,
                        message=f"提取变量名 {variable_name!r} 不符合变量命名规则",
                        suggestion="使用字母、数字和下划线，且不要以数字开头",
                    )

                if variable_name in current_extracts:
                    self._add_issue(
                        errors,
                        kind="duplicate_extract_variable",
                        step=step_number,
                        field=variable_field,
                        variable=variable_name,
                        message=f"当前步骤重复提取变量 {variable_name}",
                        suggestion="保留一个提取定义，避免后一个结果覆盖前一个结果",
                    )
                current_extracts.add(variable_name)

                self._validate_response_path(
                    response_path,
                    step=step_number,
                    field=variable_field,
                    path_kind="提取",
                    response_context=response_context,
                    errors=errors,
                    warnings=warnings,
                    warning_keys=warning_keys,
                )

                if variable_name in defined_variables or variable_name in step_variables:
                    self._add_issue(
                        warnings,
                        kind="variable_overwritten",
                        step=step_number,
                        field=variable_field,
                        variable=variable_name,
                        message=f"提取变量 {variable_name} 会覆盖已有变量",
                        suggestion="确认这是有意刷新变量，而不是变量名写错",
                    )

            validators = step_data.get("validate", step_data.get("validators", []))
            if validators is None:
                validators = []
            if not isinstance(validators, list):
                self._add_issue(
                    errors,
                    kind="invalid_validate",
                    step=step_number,
                    field=f"{field_prefix}.validate",
                    message="validate 必须是断言对象数组",
                    suggestion="例如：[{\"eq\": [\"status_code\", 200]}]",
                )
            else:
                self._validate_assertions(
                    validators,
                    step=step_number,
                    field=f"{field_prefix}.validate",
                    available=available_for_step,
                    errors=errors,
                    warnings=warnings,
                    warning_keys=warning_keys,
                    response_context=response_context,
                )

            # 提取变量从当前步骤完成后才进入后续步骤的可用集合。
            for variable_name in current_extracts:
                defined_variables.add(variable_name)
                extracted_variables.add(variable_name)

        report = self._build_report(errors, warnings, len(teststeps))
        report["summary"]["defined_variable_count"] = len(defined_variables)
        report["summary"]["extracted_variable_count"] = len(extracted_variables)
        return report

    def _build_endpoint_contract_index(
        self,
        api_specifications: Sequence[Mapping[str, Any]] | None,
    ) -> Dict[tuple[str, str], Any]:
        """按 method + path 建立响应契约索引。"""
        index: Dict[tuple[str, str], Any] = {}
        for specification in api_specifications or []:
            if not isinstance(specification, Mapping):
                continue
            for endpoint in specification.get("endpoints", []) or []:
                if not isinstance(endpoint, Mapping):
                    continue
                method = str(endpoint.get("method", "")).upper()
                path = self._normalize_url_path(endpoint.get("path"))
                responses = endpoint.get("responses")
                if method and path and isinstance(responses, Mapping) and responses:
                    index[(method, path)] = responses
        return index

    def _get_response_context(
        self,
        request: Any,
        endpoint_contracts: Dict[tuple[str, str], Any],
    ) -> Dict[str, Any] | None:
        if not isinstance(request, Mapping):
            return None

        method = str(request.get("method", "")).upper()
        path = self._normalize_url_path(request.get("url"))
        responses = endpoint_contracts.get((method, path))
        if not isinstance(responses, Mapping):
            return None

        known_paths: Set[str] = set()
        known_roots: Set[str] = set()
        for response in responses.values():
            if not isinstance(response, Mapping):
                continue

            known_paths.add("status_code")
            known_roots.add("status_code")

            headers = response.get("headers")
            if isinstance(headers, Mapping) and headers:
                known_roots.add("headers")
                for header_name in headers:
                    known_paths.add(f"headers.{header_name}")

            content = response.get("content")
            if not isinstance(content, Mapping) and (
                response.get("schema")
                or "example" in response
                or response.get("examples")
            ):
                content = {"application/json": response}

            if not isinstance(content, Mapping):
                continue

            for media in content.values():
                if not isinstance(media, Mapping):
                    continue
                example = media.get("example")
                if example is not None:
                    known_roots.add("body")
                    self._collect_example_paths(example, "body", known_paths)

                examples = media.get("examples")
                if isinstance(examples, Mapping):
                    for named_example in examples.values():
                        if isinstance(named_example, Mapping) and "value" in named_example:
                            named_example = named_example["value"]
                        if isinstance(named_example, Mapping) and "example" in named_example:
                            named_example = named_example["example"]
                        if named_example is not None:
                            known_roots.add("body")
                            self._collect_example_paths(named_example, "body", known_paths)

                schema = media.get("schema")
                if schema:
                    known_roots.add("body")
                    self._collect_schema_paths(schema, "body", known_paths)

        return {"known_paths": known_paths, "known_roots": known_roots}

    @staticmethod
    def _normalize_url_path(value: Any) -> str:
        raw_path = str(value or "").strip()
        if not raw_path:
            return ""

        parsed = urlsplit(raw_path)
        path = parsed.path if parsed.scheme or parsed.netloc else raw_path.split("?", 1)[0]
        path = re.sub(r"\$\{[^{}]+\}", "{param}", path)
        path = re.sub(r"\{[^{}]+\}", "{param}", path)
        path = re.sub(r"/{2,}", "/", path)
        if not path.startswith("/"):
            path = f"/{path}"
        return path.rstrip("/") or "/"

    def _collect_example_paths(self, value: Any, prefix: str, paths: Set[str]) -> None:
        paths.add(prefix)
        if isinstance(value, Mapping):
            for key, child in value.items():
                key = str(key)
                if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_-]*", key):
                    child_path = f"{prefix}.{key}"
                else:
                    child_path = f"{prefix}['{key}']"
                self._collect_example_paths(child, child_path, paths)
        elif isinstance(value, list):
            for index, child in enumerate(value):
                self._collect_example_paths(child, f"{prefix}[{index}]", paths)

    def _collect_schema_paths(self, schema: Any, prefix: str, paths: Set[str]) -> None:
        if not isinstance(schema, Mapping):
            return
        paths.add(prefix)

        properties = schema.get("properties")
        if isinstance(properties, Mapping):
            for key, child_schema in properties.items():
                key = str(key)
                if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_-]*", key):
                    child_path = f"{prefix}.{key}"
                else:
                    child_path = f"{prefix}['{key}']"
                self._collect_schema_paths(child_schema, child_path, paths)

        items = schema.get("items")
        if items:
            self._collect_schema_paths(items, f"{prefix}[0]", paths)

        for branch_key in ("allOf", "anyOf", "oneOf"):
            branches = schema.get(branch_key)
            if isinstance(branches, list):
                for branch in branches:
                    self._collect_schema_paths(branch, prefix, paths)

    def _validate_variable_mapping(
        self,
        value: Any,
        *,
        field: str,
        step: int,
        errors: List[Dict[str, Any]],
    ) -> Set[str]:
        if value is None:
            return set()
        if not isinstance(value, Mapping):
            self._add_issue(
                errors,
                kind="invalid_variables",
                step=step,
                field=field,
                message=f"{field} 必须是对象",
                suggestion="将变量定义写成 {\"name\": \"value\"} 格式",
            )
            return set()

        names: Set[str] = set()
        for raw_name in value.keys():
            name = str(raw_name)
            if not self._VARIABLE_NAME_PATTERN.fullmatch(name):
                self._add_issue(
                    errors,
                    kind="invalid_variable_name",
                    step=step,
                    field=f"{field}.{name}",
                    variable=name,
                    message=f"变量名 {name!r} 不符合变量命名规则",
                    suggestion="使用字母、数字和下划线，且不要以数字开头",
                )
            names.add(name)
        return names

    def _scan_values(
        self,
        value: Any,
        *,
        available: Set[str],
        field: str,
        step: int,
        errors: List[Dict[str, Any]],
        local_variable_names: Set[str] | None = None,
    ) -> None:
        if isinstance(value, str):
            for match in self._VARIABLE_PATTERN.finditer(value):
                expression = match.group(1).strip()
                if not expression:
                    self._add_issue(
                        errors,
                        kind="invalid_variable_reference",
                        step=step,
                        field=field,
                        message="发现空的变量引用 ${}",
                        suggestion="填写变量名或删除该占位符",
                    )
                    continue

                # HttpRunner 内置函数，例如 ${get_timestamp()}，不是场景变量。
                if self._FUNCTION_PATTERN.fullmatch(expression):
                    continue

                if local_variable_names and expression in local_variable_names:
                    self._add_issue(
                        errors,
                        kind="circular_variable",
                        step=step,
                        field=field,
                        variable=expression,
                        message=f"变量 {expression} 在定义时引用了自己",
                        suggestion="改为引用前序步骤变量或移除该占位符",
                    )
                    continue

                if expression not in available:
                    self._add_issue(
                        errors,
                        kind="undefined_variable",
                        step=step,
                        field=field,
                        variable=expression,
                        message=f"变量 {expression} 未在当前步骤之前定义",
                        suggestion="在前序步骤 extract 中提取该变量，或加入 config.variables",
                    )
            return

        if isinstance(value, Mapping):
            for key, child in value.items():
                self._scan_values(
                    child,
                    available=available,
                    field=f"{field}.{key}",
                    step=step,
                    errors=errors,
                    local_variable_names=local_variable_names,
                )
            return

        if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
            for index, child in enumerate(value):
                self._scan_values(
                    child,
                    available=available,
                    field=f"{field}[{index}]",
                    step=step,
                    errors=errors,
                    local_variable_names=local_variable_names,
                )

    def _validate_assertions(
        self,
        validators: List[Any],
        *,
        step: int,
        field: str,
        available: Set[str],
        errors: List[Dict[str, Any]],
        warnings: List[Dict[str, Any]],
        warning_keys: Set[str],
        response_context: Dict[str, Any] | None,
    ) -> None:
        for index, assertion in enumerate(validators):
            assertion_field = f"{field}[{index}]"
            if not isinstance(assertion, Mapping) or len(assertion) != 1:
                self._add_issue(
                    errors,
                    kind="invalid_assertion",
                    step=step,
                    field=assertion_field,
                    message="每条断言必须是只包含一个比较器的对象",
                    suggestion="例如：{\"eq\": [\"body.code\", 200]}",
                )
                continue

            comparator, operands = next(iter(assertion.items()))
            if not isinstance(comparator, str) or not comparator.strip():
                self._add_issue(
                    errors,
                    kind="invalid_comparator",
                    step=step,
                    field=assertion_field,
                    message="断言比较器不能为空",
                    suggestion="使用 eq、ne、gt、lt 或 contains 等比较器",
                )
                continue

            if comparator not in self._SUPPORTED_COMPARATORS:
                self._add_issue(
                    warnings,
                    kind="unknown_comparator",
                    step=step,
                    field=assertion_field,
                    comparator=comparator,
                    message=f"暂未对比较器 {comparator} 做专门校验",
                    suggestion="确认当前 HttpRunner 版本支持该比较器",
                )

            if not isinstance(operands, list) or len(operands) < 2:
                self._add_issue(
                    errors,
                    kind="invalid_assertion_operands",
                    step=step,
                    field=assertion_field,
                    message="断言值必须是 [响应路径, 期望值] 数组",
                    suggestion="例如：{\"eq\": [\"body.code\", 200]}",
                )
                continue

            target = operands[0]
            self._validate_response_path(
                target,
                step=step,
                field=f"{assertion_field}[0]",
                path_kind="断言",
                errors=errors,
                warnings=warnings,
                warning_keys=warning_keys,
                response_context=response_context,
            )
            self._scan_values(
                operands[1:],
                available=available,
                field=f"{assertion_field}[1]",
                step=step,
                errors=errors,
            )

    def _validate_response_path(
        self,
        path: Any,
        *,
        step: int,
        field: str,
        path_kind: str,
        errors: List[Dict[str, Any]],
        warnings: List[Dict[str, Any]],
        warning_keys: Set[str],
        response_context: Dict[str, Any] | None,
    ) -> None:
        if not isinstance(path, str) or not path.strip():
            self._add_issue(
                errors,
                kind="invalid_response_path",
                step=step,
                field=field,
                message=f"{path_kind}路径必须是非空字符串",
                suggestion="使用 status_code、body.data.id、headers.Content-Type 等路径",
            )
            return

        normalized_path = path.strip()
        root_match = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)", normalized_path)
        root = root_match.group(1) if root_match else ""
        if root not in self._RESPONSE_ROOTS:
            self._add_issue(
                errors,
                kind="invalid_response_path",
                step=step,
                field=field,
                path=normalized_path,
                message=f"{path_kind}路径 {normalized_path!r} 的响应根节点不受支持",
                suggestion="路径必须以 status_code、body、headers 或 cookies 开头",
            )
            return

        tail = normalized_path[len(root) :]
        if root == "status_code" and tail:
            self._add_issue(
                errors,
                kind="invalid_response_path",
                step=step,
                field=field,
                path=normalized_path,
                message="status_code 不支持继续访问子字段",
                suggestion="直接使用 status_code 作为响应路径",
            )
            return

        if tail and not self._PATH_TAIL_PATTERN.fullmatch(tail):
            self._add_issue(
                errors,
                kind="invalid_response_path",
                step=step,
                field=field,
                path=normalized_path,
                message=f"{path_kind}路径 {normalized_path!r} 的格式不合法",
                suggestion="使用点号字段或数组下标，例如 body.data.id、body.items[0].id",
            )
            return

        if response_context and root in response_context.get("known_roots", set()):
            known_paths = response_context.get("known_paths", set())
            if normalized_path not in known_paths:
                # Swagger/OpenAPI 文档可能不完整或与实际响应存在差异。
                # 这类静态契约不一致不能直接阻断场景保存，交给真实调试响应确认。
                self._add_issue(
                    warnings,
                    kind="response_path_not_in_contract",
                    step=step,
                    field=field,
                    path=normalized_path,
                    message=f"{path_kind}路径 {normalized_path!r} 不在当前接口的响应契约中",
                    suggestion="优先改用当前接口 response example 或 schema 中真实存在的字段；若文档不完整，请通过调试响应确认",
                )
            return

        # 没有响应契约时只能确认格式，字段是否存在需要调试真实响应。
        if root != "status_code":
            warning_key = f"{step}:{field}:{normalized_path}"
            if warning_key not in warning_keys:
                warning_keys.add(warning_key)
                self._add_issue(
                    warnings,
                    kind="response_path_unverified",
                    step=step,
                    field=field,
                    path=normalized_path,
                    message=f"{path_kind}路径格式正确，但尚未结合真实响应确认字段是否存在",
                    suggestion="调试到该步骤后，根据实际响应再次检查该路径",
                )

    @staticmethod
    def _add_issue(target: List[Dict[str, Any]], **issue: Any) -> None:
        target.append(issue)

    @staticmethod
    def _build_report(
        errors: List[Dict[str, Any]],
        warnings: List[Dict[str, Any]],
        step_count: int,
    ) -> Dict[str, Any]:
        return {
            "valid": not errors,
            "errors": errors,
            "warnings": warnings,
            "summary": {
                "error_count": len(errors),
                "warning_count": len(warnings),
                "step_count": step_count,
            },
        }
