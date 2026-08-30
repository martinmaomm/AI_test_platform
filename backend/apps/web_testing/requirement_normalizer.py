"""LLM-backed, strictly structured WebUI scenario normalisation."""

from __future__ import annotations

import json
import logging
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from ai_core.model_manager import get_llm_manager

from .generation_contracts import GenerationContractError, ScenarioSpec, parse_scenario_spec_json
from .generation_security import redact_metadata, redact_text

logger = logging.getLogger(__name__)


NORMALIZER_SYSTEM_PROMPT = """你是 WebUI 自动化测试场景整理器。只将用户已明确表达的目标整理成 JSON，
不得编造接口字段、页面元素、业务结果或登录流程。若缺少成功标准、目标元素或业务字段，写入 ambiguities。
不要输出 Markdown、解释、代码、用户名、密码、Token 或完整 URL。
输出必须严格匹配以下 JSON 结构：
{
  "title":"", "objective":"", "preconditions":[],
  "steps":[{"id":"S1","name":"","intent":"navigate|read|create|update|delete|assert|cleanup","target_hint":"","input_refs":[],"mutates_data":false,"expected":""}],
  "assertions":[{"id":"A1","name":"","target_hint":"","expected":"","step_id":"S1"}],
  "cleanup":[{"id":"C1","name":"","target_hint":"","condition":"","step_id":"S1"}],
  "forbidden_actions":[], "credentials_required":false, "ambiguities":[], "risk_level":"low|medium|high"
}"""


class RequirementNormalizer:
    """One LLM call plus at most one JSON-only format repair."""

    def __init__(self, model_config_id: int):
        self.model_config_id = model_config_id
        self.model_manager = get_llm_manager(config_id=model_config_id)

    def normalize(self, description_safe: str, test_case_context: dict[str, Any] | None = None) -> ScenarioSpec:
        safe_context = redact_metadata(test_case_context or {})
        user_payload = {
            'description': redact_text(description_safe),
            'test_case_context': safe_context,
        }
        raw_output = self.model_manager.invoke([
            SystemMessage(content=NORMALIZER_SYSTEM_PROMPT),
            HumanMessage(content=json.dumps(user_payload, ensure_ascii=False)),
        ])
        return parse_scenario_spec_json(raw_output, format_repair=self._repair_json_once)

    def _repair_json_once(self, invalid_output: str, validation_error: str) -> str:
        """Ask only for schema-conforming reformatting, never a new interpretation."""
        prompt = {
            'instruction': '仅修复以下 JSON 的格式或缺失结构，不新增任何业务事实。只输出 JSON。',
            'validation_error': validation_error,
            'invalid_output': redact_text(invalid_output),
        }
        return self.model_manager.invoke([
            SystemMessage(content='你是严格 JSON 格式修复器，不补充业务含义。'),
            HumanMessage(content=json.dumps(prompt, ensure_ascii=False)),
        ])


def normalize_requirement(
    description_safe: str,
    model_config_id: int,
    test_case_context: dict[str, Any] | None = None,
) -> ScenarioSpec:
    """Convenience entry point used by the Celery orchestrator."""
    return RequirementNormalizer(model_config_id).normalize(description_safe, test_case_context)
