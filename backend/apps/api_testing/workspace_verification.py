"""Pure guards for the frozen API workspace generate-and-verify pipeline."""
from __future__ import annotations

import hashlib
import json
import re
from copy import deepcopy
from typing import Any
from urllib.parse import urlsplit

from .workspace_service import WorkspaceValidationError, normalize_draft

_VARIABLE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}|\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}|\$([A-Za-z_][A-Za-z0-9_]*)")


def draft_hash(draft: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(draft, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def require_target_url(value: Any) -> str:
    if not isinstance(value, str) or value != value.strip() or not value:
        raise WorkspaceValidationError('base_url 必须是用户确认的完整 HTTP(S) 目标地址。')
    parts = urlsplit(value)
    if parts.scheme.lower() not in {'http', 'https'} or not parts.netloc or not parts.hostname:
        raise WorkspaceValidationError('base_url 必须是用户确认的完整 HTTP(S) 目标地址。')
    return value.rstrip('/')


def _path_matches(endpoint_path: str, request_url: str) -> bool:
    """Accept one concrete or runtime-variable segment for each OpenAPI path parameter."""
    path = urlsplit(request_url).path or request_url.split('?', 1)[0]
    parts = re.split(r'(\{[^}/]+\})', endpoint_path.rstrip('/') or '/')
    pattern = ''.join('[^/]+' if re.fullmatch(r'\{[^}/]+\}', part) else re.escape(part) for part in parts)
    return bool(re.fullmatch(pattern, path.rstrip('/') or '/'))


def _variable_names(value: Any) -> set[str]:
    if isinstance(value, str):
        return {next(item for item in match.groups() if item is not None) for match in _VARIABLE.finditer(value)}
    if isinstance(value, dict):
        return set().union(*(_variable_names(item) for item in value.values())) if value else set()
    if isinstance(value, list):
        return set().union(*(_variable_names(item) for item in value)) if value else set()
    return set()


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def step_assertions(draft: dict[str, Any]) -> dict[str, set[str]]:
    """Attach assertions to endpoint occurrence without pinning path-variable values."""
    values: dict[str, set[str]] = {}
    occurrences: dict[str, int] = {}
    for step in draft.get('teststeps', []):
        request = step.get('request') if isinstance(step, dict) else {}
        request = request if isinstance(request, dict) else {}
        endpoint_id = step.get('endpoint_id') if isinstance(step, dict) else None
        method = str(request.get('method', '')).upper()
        identity_base = (f'endpoint:{endpoint_id}:method:{method}' if isinstance(endpoint_id, int)
                         else f"request:{method}:url:{request.get('url', '')}")
        occurrence = occurrences.get(identity_base, 0)
        occurrences[identity_base] = occurrence + 1
        identity = f'{identity_base}:occurrence:{occurrence}'
        values[identity] = {_canonical(item) for item in (step.get('validate') or []) if isinstance(item, (dict, list))}
    return values


def _referenced_variable_closure(value: Any, variables: dict[str, Any], seen: set[str] | None = None) -> set[str]:
    seen = set(seen or set())
    for name in _variable_names(value):
        if name in seen:
            continue
        seen.add(name)
        if name in variables:
            seen.update(_referenced_variable_closure(variables[name], variables, seen))
    return seen


def protected_expected_values(draft: dict[str, Any], variables_override: dict[str, Any] | None = None) -> dict[str, dict[str, str]]:
    """Changing ``config.variables.expected`` cannot weaken ``${expected}`` assertions."""
    variables = draft.get('config', {}).get('variables', {}) if isinstance(draft.get('config'), dict) else {}
    variables = variables if isinstance(variables, dict) else {}
    variables = {**variables, **(variables_override or {})}
    protected: dict[str, dict[str, str]] = {}
    for identity, step in zip(step_assertions(draft), draft.get('teststeps', [])):
        names = _referenced_variable_closure(step.get('validate') if isinstance(step, dict) else [], variables)
        protected[identity] = {name: _canonical(variables[name]) for name in names if name in variables}
    return protected


def assertions_preserved(baseline: dict[str, set[str]], protected: dict[str, dict[str, str]], candidate: dict[str, Any]) -> bool:
    candidate_assertions = step_assertions(candidate)
    if any(not required.issubset(candidate_assertions.get(identity, set())) for identity, required in baseline.items()):
        return False
    candidate_order = {identity: index for index, identity in enumerate(candidate_assertions)}
    baseline_order = [candidate_order[identity] for identity in baseline if identity in candidate_order]
    if baseline_order != sorted(baseline_order):
        return False
    candidate_variables = candidate.get('config', {}).get('variables', {})
    if not isinstance(candidate_variables, dict):
        return False
    protected_names = {name for expected in protected.values() for name in expected}
    if any(protected_names.intersection(step.get('extract', {})) for step in candidate.get('teststeps', []) if isinstance(step, dict)):
        return False
    return all(
        all(name in candidate_variables and _canonical(candidate_variables[name]) == value for name, value in expected.items())
        for expected in protected.values()
    )


def draft_changes(previous: dict[str, Any], current: dict[str, Any]) -> list[str]:
    """Human-readable structural diff that deliberately excludes request values and secrets."""
    if not isinstance(previous, dict) or not isinstance(previous.get('config'), dict) or not isinstance(previous.get('teststeps'), list):
        return ['已修正上一轮候选的非法 JSON 结构']
    before = previous['teststeps']
    after = current.get('teststeps', []) if isinstance(current, dict) and isinstance(current.get('teststeps'), list) else []
    changes: list[str] = []
    if len(before) != len(after):
        changes.append(f'测试步骤数量由 {len(before)} 调整为 {len(after)}')
    for index, (old, new) in enumerate(zip(before, after), start=1):
        if not isinstance(old, dict) or not isinstance(new, dict):
            changes.append(f'第 {index} 步已修正非法结构')
            continue
        old_request, new_request = old.get('request', {}), new.get('request', {})
        old_request = old_request if isinstance(old_request, dict) else {}
        new_request = new_request if isinstance(new_request, dict) else {}
        if (old.get('endpoint_id'), old_request.get('method'), old_request.get('url')) != (
            new.get('endpoint_id'), new_request.get('method'), new_request.get('url'),
        ):
            changes.append(f'第 {index} 步接口定位已调整')
        changed_request_fields = [
            field for field in ('headers', 'params', 'json', 'data', 'raw', 'allow_redirects')
            if _canonical(old_request.get(field)) != _canonical(new_request.get(field))
        ]
        if changed_request_fields:
            changes.append(f"第 {index} 步请求字段已调整：{'、'.join(changed_request_fields)}")
        old_extract, new_extract = old.get('extract', {}), new.get('extract', {})
        if set(old_extract) != set(new_extract):
            changes.append(f'第 {index} 步提取变量集合已调整')
        elif _canonical(old_extract) != _canonical(new_extract):
            changes.append(f'第 {index} 步提取路径已调整')
        if _canonical(old.get('validate', [])) != _canonical(new.get('validate', [])):
            changes.append(f'第 {index} 步断言结构已调整')
    old_variables_value = previous['config'].get('variables', {})
    new_config = current.get('config', {}) if isinstance(current, dict) else {}
    new_variables_value = new_config.get('variables', {}) if isinstance(new_config, dict) else {}
    if not isinstance(old_variables_value, dict) or not isinstance(new_variables_value, dict):
        return changes + ['已修正配置变量的非法结构']
    old_variables = set(old_variables_value)
    new_variables = set(new_variables_value)
    if old_variables != new_variables:
        changes.append('配置变量名称集合已调整')
    elif _canonical(old_variables_value) != _canonical(new_variables_value):
        changes.append('配置变量定义已调整')
    return changes or ['未变更请求结构；保留上一轮候选进行复测']


def _require_variables(value: Any, variables: dict[str, Any], *, allowed: set[str], label: str) -> None:
    for name in _variable_names(value):
        if name not in allowed:
            raise WorkspaceValidationError(f'{label} 使用了未提供的变量 {name}。')
        if name in variables and (variables[name] is None or variables[name] == ''):
            raise WorkspaceValidationError(f'{label} 使用了未提供的变量 {name}。')


def _static_variable_values(variables: dict[str, Any]) -> dict[str, Any]:
    """Use runtime alias semantics without materializing reusable unique values.

    Empty unused values are legal. A later extraction can supply a token that
    started as an empty editor placeholder; only actual pre-extraction use of
    an empty value is rejected by ``_require_variables``.
    """
    from .requests_runtime import CaseContractError, _resolve_variable_definitions
    try:
        return _resolve_variable_definitions({'timestamp_ns': 1, 'uuid4': 'runtime-placeholder', **variables})
    except CaseContractError as exc:
        raise WorkspaceValidationError(str(exc)) from exc


def _require_endpoint_input(step: dict[str, Any], endpoint: dict[str, Any], variables: dict[str, Any], *, allowed: set[str], index: int) -> None:
    request = step['request']
    parameters = endpoint.get('parameters') if isinstance(endpoint.get('parameters'), list) else []
    for parameter in parameters:
        if not isinstance(parameter, dict) or not parameter.get('required'):
            continue
        name, location = parameter.get('name'), parameter.get('in')
        if not isinstance(name, str):
            continue
        if location == 'path':
            path_without_runtime_variables = re.sub(
                rf'\$\{{{re.escape(name)}\}}|\{{\{{\s*{re.escape(name)}\s*\}}\}}', '', request['url'],
            )
            if f'{{{name}}}' in path_without_runtime_variables:
                raise WorkspaceValidationError(f'候选步骤 {index + 1} 的路径参数 {name} 必须是实际值或已提供变量。')
        elif location == 'query' and name not in request.get('params', {}):
            raise WorkspaceValidationError(f'候选步骤 {index + 1} 缺少必填 query 参数 {name}。')
        elif location == 'header' and not any(str(key).lower() == name.lower() for key in request.get('headers', {})):
            raise WorkspaceValidationError(f'候选步骤 {index + 1} 缺少必填 header 参数 {name}。')
    body = endpoint.get('request_body') if isinstance(endpoint.get('request_body'), dict) else {}
    body_key = next((key for key in ('json', 'data', 'raw') if request.get(key) is not None), None)
    swagger_body_required = any(
        parameter.get('in') == 'body' and parameter.get('required')
        for parameter in parameters if isinstance(parameter, dict)
    )
    if (body.get('required') or swagger_body_required) and body_key is None:
        raise WorkspaceValidationError(f'候选步骤 {index + 1} 缺少规范要求的请求体。')
    content = body.get('content') if isinstance(body.get('content'), dict) else {}
    if body_key and content:
        expected_types = set(content)
        content_type = next((str(value) for key, value in request.get('headers', {}).items() if str(key).lower() == 'content-type'), '')
        inferred = (
            'application/json' if body_key == 'json'
            else ('application/x-www-form-urlencoded' if body_key == 'data' and not content_type else content_type)
        )
        if inferred and not any(inferred.lower().split(';', 1)[0] == item.lower() for item in expected_types):
            raise WorkspaceValidationError(f'候选步骤 {index + 1} 的请求体媒体类型不在接口规范中。')
    _require_variables(request, variables, allowed=allowed, label=f'候选步骤 {index + 1}')


def _security_parameter(scheme: dict[str, Any]) -> tuple[str, str] | None:
    """Return a request location/name only when OpenAPI defines it unambiguously."""
    scheme_type = str(scheme.get('type') or '').lower()
    if scheme_type == 'apikey':
        location, name = scheme.get('in'), scheme.get('name')
        if location in {'header', 'query', 'cookie'} and isinstance(name, str) and name:
            return str(location), name
    # HTTP bearer/basic, Swagger basic and OAuth flows all use Authorization.
    if scheme_type in {'http', 'basic', 'oauth2', 'openidconnect'}:
        return 'header', 'Authorization'
    return None


def _request_has_security_parameter(request: dict[str, Any], location: str, name: str, *, session_cookie_available: bool) -> bool:
    if location == 'header':
        return any(str(key).lower() == name.lower() and value is not None and value != '' for key, value in request.get('headers', {}).items())
    if location == 'query':
        return name in request.get('params', {}) and request['params'][name] is not None and request['params'][name] != ''
    if location == 'cookie':
        cookie = next((str(value) for key, value in request.get('headers', {}).items() if str(key).lower() == 'cookie'), '')
        return session_cookie_available or any(part.strip().startswith(f'{name}=') for part in cookie.split(';'))
    return False


def _require_endpoint_security(request: dict[str, Any], endpoint: dict[str, Any], *, index: int,
                               session_cookie_available: bool) -> None:
    """Reject removal of a credential only when the selected OpenAPI scope proves it is required."""
    context = endpoint.get('document_context') if isinstance(endpoint.get('document_context'), dict) else {}
    requirements = context.get('security')
    schemes = context.get('security_schemes') if isinstance(context.get('security_schemes'), dict) else {}
    if not isinstance(requirements, list) or not requirements:
        return
    alternatives: list[list[tuple[str, str]]] = []
    for requirement in requirements:
        if not isinstance(requirement, dict) or not requirement:
            # An empty security requirement is an explicitly unauthenticated
            # alternative, so the document cannot prove a credential is needed.
            return
        fields: list[tuple[str, str]] = []
        for scheme_name in requirement:
            scheme = schemes.get(scheme_name)
            field = _security_parameter(scheme) if isinstance(scheme, dict) else None
            if field is None:
                # Missing or non-representable scheme data must remain a model
                # review concern, not a guessed hard-coded authentication rule.
                return
            fields.append(field)
        if fields:
            alternatives.append(fields)
    if alternatives and not any(
        all(_request_has_security_parameter(request, location, name, session_cookie_available=session_cookie_available) for location, name in fields)
        for fields in alternatives
    ):
        raise WorkspaceValidationError(f'候选步骤 {index + 1} 缺少 OpenAPI security 要求的鉴权参数。')


def prepare_candidate(value: Any, *, endpoints: list[dict[str, Any]], target_url: str,
                      variables: dict[str, Any], baseline: dict[str, set[str]] | None = None,
                      protected: dict[str, dict[str, str]] | None = None,
                      required_endpoint_ids: set[int] | None = None,
                      authenticated_target_ids: set[int] | None = None,
                      cookie_session_dependency_ids: set[int] | None = None) -> dict[str, Any]:
    draft = normalize_draft(value)
    selected = {item['id']: item for item in endpoints}
    if not draft['teststeps']:
        raise WorkspaceValidationError('候选草稿没有测试步骤。')
    merged_variables = {**deepcopy(draft['config'].get('variables') or {}), **deepcopy(variables)}
    scoped_values = _static_variable_values(merged_variables)
    config_headers = draft['config'].get('headers') if isinstance(draft['config'].get('headers'), dict) else {}
    available = set(merged_variables) | {'timestamp_ns', 'uuid4'}
    completed_dependency_ids: set[int] = set()
    for index, step in enumerate(draft['teststeps']):
        request = step['request']
        endpoint = selected.get(step.get('endpoint_id'))
        if endpoint is None:
            raise WorkspaceValidationError(f'候选步骤 {index + 1} 未引用本次规范端点。')
        if request['method'] != str(endpoint['method']).upper() or not _path_matches(str(endpoint['path']), request['url']):
            raise WorkspaceValidationError(f'候选步骤 {index + 1} 的方法或路径与规范端点不一致。')
        if urlsplit(request['url']).scheme or urlsplit(request['url']).netloc:
            raise WorkspaceValidationError(f'候选步骤 {index + 1} 不允许修改已确认目标地址。')
        if not step['validate']:
            raise WorkspaceValidationError(f'候选步骤 {index + 1} 至少需要一条可执行断言。')
        # Runtime overlays headers case-insensitively before substitution.  Do
        # the same here so a step-level ``authorization`` correctly replaces a
        # global ``Authorization`` instead of triggering a false static error.
        from .requests_runtime import _merge_headers
        effective_step = deepcopy(step)
        effective_headers = _merge_headers(config_headers, request.get('headers', {}))
        effective_step['request']['headers'] = effective_headers
        _require_variables(
            effective_headers, scoped_values, allowed=available,
            label=f'候选步骤 {index + 1} 的有效 headers（依赖登录变量请移到登录后的步骤）',
        )
        _require_endpoint_input(effective_step, endpoint, scoped_values, allowed=available, index=index)
        # A global OpenAPI security declaration cannot tell us whether this is
        # an intentionally unauthenticated negative case (or an endpoint whose
        # document forgot a security override).  Enforce it only for business
        # targets the scenario plan explicitly marked as authenticated.
        if step.get('endpoint_id') in (authenticated_target_ids or set()):
            _require_endpoint_security(
                effective_step['request'], endpoint, index=index,
                session_cookie_available=bool(completed_dependency_ids.intersection(cookie_session_dependency_ids or set())),
            )
        for extracted_name in step['extract']:
            scoped_values.pop(extracted_name, None)
        _require_variables(step['validate'], scoped_values, allowed=available | set(step['extract']), label=f'候选步骤 {index + 1} 断言')
        request['allow_redirects'] = False
        available.update(step['extract'])
        if isinstance(step.get('endpoint_id'), int):
            completed_dependency_ids.add(step['endpoint_id'])
    draft['config']['base_url'] = target_url
    draft['config']['variables'] = merged_variables
    if required_endpoint_ids:
        actual_ids = {step.get('endpoint_id') for step in draft['teststeps'] if isinstance(step.get('endpoint_id'), int)}
        missing = sorted(required_endpoint_ids - actual_ids)
        if missing:
            raise WorkspaceValidationError(f'候选草稿没有保留本场景业务目标端点：{missing}。')
    if baseline is not None and not assertions_preserved(baseline, protected or {}, draft):
        raise WorkspaceValidationError('修复候选删除、弱化、迁移了原步骤或断言，或改写了断言引用的预期变量。')
    return draft


def classify_result(result: dict[str, Any], draft: dict[str, Any]) -> tuple[str, str, str]:
    """Return public status, explanation, and one of repair/stop/review."""
    steps = result.get('step_datas') if isinstance(result, dict) else None
    complete = (isinstance(steps, list) and len(steps) == len(draft['teststeps']) and all(
        step.get('status') == 'passed'
        and ((step.get('validators') or {}).get('validate_extractor') or [])
        and all(item.get('passed') for item in ((step.get('validators') or {}).get('validate_extractor') or []))
        for step in steps
    ))
    if result.get('success') and complete:
        return 'passed', '所有步骤已运行且断言通过。', 'stop'
    error_type = result.get('error_type') if isinstance(result, dict) else ''
    if error_type in {'HardTimeout', 'Timeout'}:
        return 'needs_review', '请求超时，远端是否生效未知，未自动重试。', 'stop'
    for step in steps or []:
        if step.get('status') == 'passed':
            continue
        response = ((step.get('data') or {}).get('req_resps') or [{}])[0].get('response') or {}
        code = response.get('status_code')
        if code in {401, 403}:
            return 'needs_review', '目标返回权限错误，未盲目重放。', 'stop'
        if isinstance(code, int) and code >= 500:
            return 'needs_review', '目标返回 5xx，未盲目重放。', 'stop'
    if error_type in {'ExtractionFailure', 'CaseContractError', 'UnsupportedCaseFeature'}:
        return 'failed', '请求或提取结构错误，可在保护断言前提下修复。', 'repair'
    return 'needs_review', '运行结果需要人工审阅，未自动修改预期或重放。', 'review'
