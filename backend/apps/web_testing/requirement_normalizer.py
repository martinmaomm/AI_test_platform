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
from .generation_security import redact_metadata, redact_text

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

NORMALIZER_SYSTEM_PROMPT = """你是 WebUI 自动化测试目标整理器。将用户已明确表达的完整测试场景整理为严格 JSON。
不得编造页面字段、按钮、定位器、接口、登录流程或业务结果。页面入口、控件和路径属于后续真实探索，不要求用户预先提供。
instructions 是同一个智能体在同一浏览器会话里连续完成的业务流程清单，不得拆成独立子任务、状态边界或独立执行单元。
success_criteria 是用户可验证结果。assertion_requirements 必须与 success_criteria 一一对应，criterion_index 从 0 开始，assertion_id 使用 A1/A2；phase 只能为 main 或 cleanup，kind 只能为 visible、contains_ref、not_contains_ref、contains_literal、not_contains_literal。ref 类只引用 input_refs；literal 类只能使用用户描述或测试用例上下文中原样出现的非敏感短文本，不得猜页面提示语。cleanup_expected=true 时必须增加一条能由清理动作后页面观察确认的 cleanup success criterion/requirement；不能把点击清理控件当成清理完成。
input_refs 的 name 必须是大写执行变量名，source 只能为 generated/runtime/credential。
credential source 必须提供 credential_slot=username 或 password；需要登录时 credentials_required=true，并同时声明 UI_TEST_USERNAME 和 UI_TEST_PASSWORD。不得写入任何凭据值。
allow_test_data_writes 必须根据用户完整场景的语义判断：只要完成目标可能改变被测系统持久状态，且用户未明确限定为只读，就必须为 true。不得使用有限动作词表或描述关键词扫描代替语义判断；显式只读约束优先。cleanup_expected 仅在这类写入后要求恢复测试数据时为 true。
forbidden_actions 描述用户禁止或平台必须阻止的高风险行为。除空白或“帮我生成测试”这类完全没有测试对象的输入外，不因缺少固定动作词或成功词而拒绝输入。
不要输出 Markdown、解释、代码、用户名、密码、Token 或完整 URL。
输出必须严格匹配：
{
  "schema_version":4,"title":"","objective":"","instructions":[""],
  "success_criteria":[""],
  "assertion_requirements":[{"assertion_id":"A1","criterion_index":0,"phase":"main","kind":"visible","input_ref":"","literal":""}],
  "input_refs":[{"name":"USER_NAME","source":"generated","credential_slot":""}],
  "preconditions":[],"forbidden_actions":[],"credentials_required":false,
  "allow_test_data_writes":false,"cleanup_expected":false,"discovery_notes":[],"risk_level":"low|medium|high"
}"""


class RequirementNormalizer:
    def __init__(self, model_config_id: int):
        self.model_manager = get_llm_manager(config_id=model_config_id)

    def normalize(self, description_safe: str, test_case_context: dict[str, Any] | None = None) -> ScenarioPlan:
        description = redact_text(str(description_safe or '')).strip()
        if not description or _GENERIC_DESCRIPTION_PATTERN.fullmatch(description):
            raise ScenarioInputInsufficientError('scenario_target_missing')
        messages = [
            SystemMessage(content=NORMALIZER_SYSTEM_PROMPT),
            HumanMessage(content=json.dumps({
                'description': description,
                'test_case_context': redact_metadata(test_case_context or {}),
            }, ensure_ascii=False)),
        ]
        try:
            raw_output = self._invoke_structured_plan(messages)
        except _StructuredOutputUnsupported:
            raw_output = self._invoke_json_prompt(messages)
        plan = parse_scenario_plan_json(raw_output, format_repair=self._repair_json_once)
        plan = _apply_explicit_read_only_override(plan, description)
        _validate_assertion_literals(plan, description, test_case_context or {})
        return plan

    def _invoke_structured_plan(self, messages: list[SystemMessage | HumanMessage]) -> Any:
        """Use native LangChain structured output, but only fall back on capability gaps."""
        model = getattr(self.model_manager, 'current_llm', None)
        with_structured_output = getattr(model, 'with_structured_output', None)
        if not callable(with_structured_output):
            raise _StructuredOutputUnsupported('with_structured_output unavailable')
        try:
            structured_model = with_structured_output(ScenarioPlan, include_raw=True)
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
    ) -> str:
        return self.model_manager.invoke([
            SystemMessage(content='你是严格 JSON 格式修复器。不得新增业务目标、页面元素、定位器或完成状态；只输出 JSON。'),
            HumanMessage(content=json.dumps({
                'instruction': '仅针对 validation_diagnostics 指出的 v4 ScenarioPlan JSON 路径修复格式、字段名或缺失结构。',
                'validation_diagnostics': list(diagnostics),
                'invalid_output': redact_text(invalid_output),
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


def _validate_assertion_literals(plan: ScenarioPlan, description: str, test_case_context: dict[str, Any]) -> None:
    source = f'{description}\n{json.dumps(redact_metadata(test_case_context), ensure_ascii=False)}'
    invented = [
        item.assertion_id for item in plan.assertion_requirements
        if item.literal and item.literal not in source
    ]
    if invented:
        raise GenerationContractError('assertion_literal_not_user_owned')
