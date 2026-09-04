"""LLM-backed v4 complete-scenario normalisation."""

from __future__ import annotations

import json
import re
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from ai_core.model_manager import get_llm_manager

from .generation_contracts import (
    GenerationContractError,
    ScenarioInputInsufficientError,
    ScenarioPlan,
    parse_scenario_plan_json,
)
from .generation_security import URL_RE

_LOW_RANDOMNESS_TEMPERATURE = 0
_STRUCTURED_OUTPUT_CAPABILITY_TERMS = (
    'structured output', 'structured_output', 'response_format', 'json schema',
    'json_schema', 'function calling', 'tool calling', 'with_structured_output',
)
_UNSUPPORTED_CAPABILITY_TERMS = (
    'not support', 'unsupported', 'not implemented',
    'unexpected keyword argument',
)

_GENERIC_DESCRIPTION_PATTERN = re.compile(r'^(?:请)?(?:帮我)?(?:生成|编写|创建)?(?:一个)?(?:测试|用例|脚本)?[。！!？?\s]*$')
_EXPLICIT_READ_ONLY_PATTERN = re.compile(
    r'(?:只查看|仅查看|只读|禁止(?:任何)?写入|不得(?:进行)?写(?:入|操作)|不允许(?:进行)?写(?:入|操作)|read[ -]?only)',
    re.I,
)
_MODEL_TARGET_REFERENCE = '目标页面'
_CONTRACT_REPAIR_GUIDANCE = {
    'absolute_url_forbidden': '删除完整 URL，用“目标页面”或相对路径表达，不改变业务目标。',
    'schema_version_mismatch': '将 schema_version 设为整数 4。',
    'assertion_ref_shape': 'contains_ref/not_contains_ref 只声明 input_ref，literal 留空。',
    'assertion_literal_shape': 'contains_literal/not_contains_literal 只声明 literal，input_ref 留空。',
    'assertion_visible_shape': 'visible 断言的 input_ref 和 literal 都必须留空。',
    'assertion_deferred_shape': 'deferred 断言的 input_ref 和 literal 都必须留空，等待探索阶段确认。',
    'duplicate_input_ref': '删除重复 input_refs，保留唯一的变量声明。',
    'duplicate_assertion_id': '为每条断言分配唯一且连续的 A1、A2、A3 编号。',
    'duplicate_criterion_assertion': '每个 criterion_index 只保留一条 assertion_requirement。',
    'criterion_assertion_missing': '使 success_criteria 与 assertion_requirements 一一对应，criterion_index 从 0 连续递增。',
    'unknown_assertion_input_ref': '将断言引用的 input_ref 补充到 input_refs，或改为已声明的等价变量。',
    'credential_refs_incomplete': 'credentials_required=true 时同时声明 username/UI_TEST_USERNAME 和 password/UI_TEST_PASSWORD 两个 credential input ref。',
    'credential_flag_missing': '已声明 credential input ref 时，将 credentials_required 设为 true。',
    'cleanup_without_write_scope': '用户要求写入并清理时保留 cleanup_expected=true 且设 allow_test_data_writes=true；否则两者都为 false。',
    'main_assertion_missing': '至少保留一条 phase=main 的 assertion_requirement。',
    'cleanup_assertion_missing': (
        '先核对 scenario_input：只有用户明确要求测试后清理时才保留 cleanup_expected=true。'
        '此时复用现有“清理后目标不存在”的 success criterion，并将对应 '
        'assertion_requirement 设为 phase=cleanup；如果尚无该 criterion，根据已声明的测试数据 '
        'input_ref 补充 not_contains_ref，不得凭空添加 literal。'
        '如果用户未要求额外清理，将 cleanup_expected 改为 false，'
        '不要把场景本身的删除步骤误当成 cleanup。'
    ),
    'unexpected_cleanup_assertion': '只有用户要求写入后清理时保留 phase=cleanup 并设 cleanup_expected=true，否则改为 phase=main。',
    'cleanup_assertion_positive': '清理断言必须使用 not_contains_ref 或 not_contains_literal 验证目标已不存在。',
    'credential_slot_missing': 'credential input ref 必须按变量用途声明 credential_slot=username 或 password。',
    'unexpected_credential_slot': '非 credential 类型 input ref 的 credential_slot 必须留空。',
    'credential_ref_name_invalid': 'username 凭据只能命名 UI_TEST_USERNAME，password 凭据只能命名 UI_TEST_PASSWORD。',
    'credential_value_kind_invalid': 'credential 的 UI_TEST_USERNAME 使用 value_kind=text，UI_TEST_PASSWORD 使用 value_kind=password。',
    'list_field_required': '将指定字段改为 JSON 数组。',
    'input_refs_list_required': '将 input_refs 改为 JSON 数组。',
}

NORMALIZER_SYSTEM_PROMPT = """你是 WebUI 自动化测试目标整理器。将用户已明确表达的完整测试场景整理为严格 JSON。
不得编造页面字段、按钮、定位器、接口、登录流程或业务结果。页面入口、控件和路径属于后续真实探索，不要求用户预先提供。
instructions 是同一个智能体在同一浏览器会话里连续完成的业务流程清单，不得拆成独立子任务、状态边界或独立执行单元。
success_criteria 是用户可验证结果。assertion_requirements 必须与 success_criteria 一一对应，criterion_index 从 0 开始，assertion_id 使用 A1/A2；phase 只能为 main 或 cleanup，kind 只能为 visible、contains_ref、not_contains_ref、contains_literal、not_contains_literal、deferred。ref 类只引用 input_refs；literal 类只能使用用户描述或测试用例上下文中原样出现的短文本，不得猜页面提示语。用户没有给出可固定的页面验证细节时，使用 deferred，input_ref 和 literal 均留空，交由后续真实探索确认或标记待补充；不要为了满足合约猜测页面提示语。cleanup_expected=true 时必须增加一条能由清理动作后页面观察确认的 cleanup success criterion/requirement；不能把点击清理控件当成清理完成。
input_refs 的 name 必须是大写执行变量名，source 只能为 generated/runtime/credential，且每个 input ref 都必须声明 value_kind=text、email、password 或 integer。仅依据用户已明确的值类型选择；未知时用 text，不得依据网站字段名或业务词表猜测。generated 表示脚本运行时自动生成该类型合法的新值，runtime 表示运行前提供该类型的值。
credential source 必须提供 credential_slot=username 或 password；需要登录时 credentials_required=true，并同时声明 UI_TEST_USERNAME 和 UI_TEST_PASSWORD。UI_TEST_USERNAME 的 value_kind 必须为 text，UI_TEST_PASSWORD 的 value_kind 必须为 password。用户提供的测试环境凭据可原样保留在场景语义中；如声明 credential input ref，脚本仍通过对应运行变量读取其值。
allow_test_data_writes 必须根据用户完整场景的语义判断：只要完成目标可能改变被测系统持久状态，且用户未明确限定为只读，就必须为 true。不得使用有限动作词表或描述关键词扫描代替语义判断；显式只读约束优先。cleanup_expected 仅在这类写入后要求恢复测试数据时为 true。
forbidden_actions 描述用户禁止或平台必须阻止的高风险行为。除空白或“帮我生成测试”这类完全没有测试对象的输入外，不因缺少固定动作词或成功词而拒绝输入。
不要输出 Markdown、解释、代码或完整 URL。
输出必须严格匹配：
{
  "schema_version":4,"title":"","objective":"","instructions":[""],
  "success_criteria":[""],
  "assertion_requirements":[{"assertion_id":"A1","criterion_index":0,"phase":"main","kind":"deferred","input_ref":"","literal":""}],
  "input_refs":[{"name":"TEST_INPUT","source":"generated","value_kind":"text","credential_slot":""}],
  "preconditions":[],"forbidden_actions":[],"credentials_required":false,
  "allow_test_data_writes":false,"cleanup_expected":false,"discovery_notes":[],"risk_level":"low|medium|high"
}"""


class RequirementNormalizer:
    def __init__(self, model_config_id: int):
        self.model_manager = get_llm_manager(config_id=model_config_id)

    def normalize(self, description_safe: str, test_case_context: dict[str, Any] | None = None) -> ScenarioPlan:
        description = str(description_safe or '').strip()
        if not description or _GENERIC_DESCRIPTION_PATTERN.fullmatch(description):
            raise ScenarioInputInsufficientError('scenario_target_missing')
        model_input = _prepare_model_value({
            'description': description,
            'test_case_context': test_case_context or {},
        })
        messages = [
            SystemMessage(content=NORMALIZER_SYSTEM_PROMPT),
            HumanMessage(content=json.dumps(model_input, ensure_ascii=False)),
        ]
        try:
            raw_output = self._invoke_structured_plan(messages)
        except _StructuredOutputUnsupported:
            raw_output = self._invoke_json_prompt(messages)
        plan = parse_scenario_plan_json(
            raw_output,
            format_repair=lambda invalid_output, diagnostics: self._repair_json_once(
                invalid_output,
                diagnostics,
                scenario_input=model_input,
            ),
        )
        plan = _apply_explicit_read_only_override(plan, description)
        return _apply_user_assertion_ownership(plan, description, test_case_context or {})

    def _invoke_structured_plan(self, messages: list[SystemMessage | HumanMessage]) -> Any:
        """Use native LangChain structured output, but only fall back on capability gaps."""
        model = getattr(self.model_manager, 'current_llm', None)
        with_structured_output = getattr(model, 'with_structured_output', None)
        if not callable(with_structured_output):
            raise _StructuredOutputUnsupported('with_structured_output unavailable')
        try:
            # Pass a plain JSON Schema so provider/LangChain streaming adapters
            # cannot run Pydantic validation before the raw response reaches us.
            # ScenarioPlan validation and the single repair attempt stay owned
            # by parse_scenario_plan_json below.
            structured_model = with_structured_output(
                ScenarioPlan.model_json_schema(),
                include_raw=True,
            )
            structured_output = structured_model.invoke(
                messages, temperature=_LOW_RANDOMNESS_TEMPERATURE,
            )
            if isinstance(structured_output, dict) and any(
                key in structured_output
                for key in ('raw', 'parsed', 'parsing_error')
            ):
                parsed_output = structured_output.get('parsed')
                if parsed_output is not None:
                    return parsed_output
                raw_output = _extract_raw_output(structured_output.get('raw'))
                if raw_output is not None:
                    return raw_output
                raise GenerationContractError(
                    'model_output_invalid',
                    diagnostics=({
                        'path': '<structured_output>',
                        'type': (
                            'structured_parse_error'
                            if structured_output.get('parsing_error') is not None
                            else 'structured_output_empty'
                        ),
                        'stage': 'contract_parsing',
                    },),
                )
            return structured_output
        except Exception as exc:
            if _is_transport_failure(exc):
                raise
            if _is_structured_output_unsupported(exc):
                raise _StructuredOutputUnsupported(str(exc)) from exc
            raw_output = getattr(exc, 'llm_output', None)
            if raw_output is not None:
                return raw_output
            raise

    def _invoke_json_prompt(self, messages: list[SystemMessage | HumanMessage]) -> str:
        """Narrow fallback for providers that explicitly reject native structure."""
        return self.model_manager.invoke(
            messages, temperature=_LOW_RANDOMNESS_TEMPERATURE,
        )

    def _repair_json_once(
        self,
        invalid_output: str,
        diagnostics: tuple[dict[str, str], ...],
        *,
        scenario_input: dict[str, Any] | None = None,
    ) -> str:
        guidance = [
            {
                'path': item.get('path', '<contract>'),
                'type': item.get('type', 'validation_error'),
                'rule': _CONTRACT_REPAIR_GUIDANCE[item['type']],
            }
            for item in diagnostics
            if item.get('type') in _CONTRACT_REPAIR_GUIDANCE
        ]
        return self.model_manager.invoke([
            SystemMessage(content=(
                '你是严格 JSON 合约修复器。必须逐条执行 validation_guidance，'
                '不得通过删除用户要求的清理、凭据或写入语义来规避校验。'
                '不得新增业务目标、页面元素、定位器或完成状态；只输出 JSON。'
            )),
            HumanMessage(content=json.dumps({
                'instruction': '仅修复 validation_diagnostics 指出的 v4 ScenarioPlan 结构、字段及它们的关联关系。',
                'validation_diagnostics': list(diagnostics),
                'validation_guidance': guidance,
                'json_schema': ScenarioPlan.model_json_schema(),
                'scenario_input': scenario_input or {},
                'invalid_output': _prepare_repair_output(invalid_output),
            }, ensure_ascii=False)),
        ], temperature=_LOW_RANDOMNESS_TEMPERATURE)


class _StructuredOutputUnsupported(RuntimeError):
    """Internal marker: a provider explicitly lacks structured output support."""


def _is_structured_output_unsupported(exc: Exception) -> bool:
    """Keep fallback narrow so transport failures never create a second request."""
    if isinstance(exc, NotImplementedError):
        return True
    message = str(exc).lower()
    has_capability_term = any(term in message for term in _STRUCTURED_OUTPUT_CAPABILITY_TERMS)
    has_unsupported_term = any(term in message for term in _UNSUPPORTED_CAPABILITY_TERMS)
    return has_capability_term and has_unsupported_term


def _is_transport_failure(exc: Exception) -> bool:
    status_code = getattr(exc, 'status_code', None)
    response = getattr(exc, 'response', None)
    if status_code is None:
        status_code = getattr(response, 'status_code', None)
    if isinstance(status_code, int) and (status_code == 429 or 500 <= status_code <= 599):
        return True
    return bool(re.search(r'\b(?:429|5\d{2}|timeout)\b|timed?\s*out|connection\s+(?:closed|error|reset)', str(exc), re.I))


def _extract_raw_output(raw_output: Any) -> Any | None:
    if raw_output is None:
        return None
    candidate = (
        raw_output.get('content')
        if isinstance(raw_output, dict) and 'content' in raw_output
        else getattr(raw_output, 'content', raw_output)
    )
    if isinstance(candidate, str):
        return candidate if candidate.strip() else None
    if isinstance(candidate, (dict, list, tuple)):
        return candidate if candidate else None
    return candidate if isinstance(candidate, (int, float, bool)) else None


def _prepare_model_value(value: Any) -> Any:
    """Remove transport-only values that must never become ScenarioPlan text."""
    if isinstance(value, dict):
        return {
            _prepare_model_text(str(key)): _prepare_model_value(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_prepare_model_value(item) for item in value]
    if isinstance(value, tuple):
        return [_prepare_model_value(item) for item in value]
    if isinstance(value, str):
        return _prepare_model_text(value)
    return value


def _prepare_model_text(value: str) -> str:
    return URL_RE.sub(_MODEL_TARGET_REFERENCE, value)


def _prepare_repair_output(invalid_output: str) -> str:
    """Normalize model target URLs without breaking the repair JSON syntax."""
    try:
        payload = json.loads(str(invalid_output))
    except (TypeError, ValueError):
        return _prepare_model_text(str(invalid_output))
    return json.dumps(_prepare_model_value(payload), ensure_ascii=False)


def normalize_requirement(description_safe: str, *, model_config_id: int, test_case_context: dict[str, Any] | None = None) -> ScenarioPlan:
    return RequirementNormalizer(model_config_id).normalize(description_safe, test_case_context)


def _apply_explicit_read_only_override(plan: ScenarioPlan, description: str) -> ScenarioPlan:
    explicit_read_only = bool(_EXPLICIT_READ_ONLY_PATTERN.search(description))
    forbidden_read_only = any(_EXPLICIT_READ_ONLY_PATTERN.search(item) for item in plan.forbidden_actions)
    if explicit_read_only or forbidden_read_only:
        cleanup_indexes = {
            item.criterion_index for item in plan.assertion_requirements
            if item.phase == 'cleanup'
        }
        retained_indexes = [
            index for index in range(len(plan.success_criteria))
            if index not in cleanup_indexes
        ]
        index_map = {
            prior: current for current, prior in enumerate(retained_indexes)
        }
        updates = {
            'allow_test_data_writes': False,
            'cleanup_expected': False,
            'success_criteria': [
                plan.success_criteria[index] for index in retained_indexes
            ],
            'assertion_requirements': [
                {
                    **item.model_dump(mode='json'),
                    'criterion_index': index_map[item.criterion_index],
                }
                for item in plan.assertion_requirements
                if item.phase == 'main'
            ],
        }
    else:
        return plan
    return ScenarioPlan.model_validate({**plan.model_dump(mode='json'), **updates})


def _apply_user_assertion_ownership(
    plan: ScenarioPlan, description: str, test_case_context: dict[str, Any],
) -> ScenarioPlan:
    """Keep user-owned literals fixed, but never fail on a model-invented one.

    A generated literal has no user semantic authority.  Reclassifying only
    that requirement as ``deferred`` preserves the success criterion and lets
    the explorer either prove a real result or emit a pending marker.
    """
    source = f'{description}\n{json.dumps(test_case_context, ensure_ascii=False)}'
    requirements = [
        {
            **item.model_dump(mode='json'),
            'kind': 'deferred',
            'input_ref': '',
            'literal': '',
        }
        if item.literal and item.literal not in source else item.model_dump(mode='json')
        for item in plan.assertion_requirements
    ]
    if (
        requirements == [item.model_dump(mode='json') for item in plan.assertion_requirements]
        and plan.original_user_target == _prepare_model_text(description)
    ):
        return plan
    return ScenarioPlan.model_validate({
        **plan.model_dump(mode='json'),
        # ScenarioPlan rejects absolute URLs.  Keep the user-owned semantics
        # in the same transport-safe form the explorer already receives.
        'original_user_target': _prepare_model_text(description),
        'assertion_requirements': requirements,
    })
