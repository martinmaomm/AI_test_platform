"""LLM-backed v3 GoalPlan normalisation without action-word heuristics."""

from __future__ import annotations

import json
import re
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from ai_core.model_manager import get_llm_manager

from .generation_contracts import GoalPlan, ScenarioInputInsufficientError, parse_goal_plan_json
from .generation_security import redact_metadata, redact_text

_GENERIC_DESCRIPTION_PATTERN = re.compile(r'^(?:请)?(?:帮我)?(?:生成|编写|创建)?(?:一个)?(?:测试|用例|脚本)?[。！!？?\s]*$')

NORMALIZER_SYSTEM_PROMPT = """你是 WebUI 自动化测试目标整理器。将用户已明确表达的目标整理为严格 JSON。
不得编造页面字段、按钮、定位器、接口、登录流程或业务结果。页面入口、控件和路径属于后续真实探索，不要求用户预先提供。
每个 Goal 的 kind 只能是 setup/exercise/verify/cleanup，它只表示测试阶段，绝不代表页面按钮或 CRUD 意图。
side_effect 只能由用户目标语义判断；test_data 的 Goal 必须由一个 cleanup Goal 的 cleanup_for_goal_ids 引用。
verify Goal 必须提供 verification；有 cleanup_for_goal_ids 的 cleanup Goal 也必须提供 verification。verification 只能是 null，或 {"mode":"visible"}，或 {"mode":"contains_ref|not_contains_ref","input_ref":"当前 Goal 已声明 ref"}。
input_refs 的 name 必须是大写执行变量名。source 为 credential 时才提供 credential_slot，值只能是 username 或 password；其他 source 必须省略 credential_slot。
同名 input ref 在所有 Goal 中必须保持相同 source 和 credential_slot；不得使用系统保留变量名。
需要登录时 credentials_required=true，并同时声明 UI_TEST_USERNAME/username 和 UI_TEST_PASSWORD/password 两个 credential ref；不需要登录时不得声明 credential ref。
整个 GoalPlan 至少包含一个 verification contract，保证最终脚本有可验证结果。
除空白或“帮我生成测试”这类完全没有测试对象的输入外，不因缺少固定动作词或成功词而拒绝输入。
不要输出 Markdown、解释、代码、用户名、密码、Token 或完整 URL。
输出必须严格匹配：
{
  "schema_version":3,"title":"","objective":"","preconditions":[],
  "goals":[{"id":"G1","kind":"setup|exercise|verify|cleanup","objective":"","completion_criteria":"","input_refs":[{"name":"USER_NAME","source":"generated"}],"verification":null,"side_effect":"none|test_data|external|unknown","cleanup_for_goal_ids":[]}],
  "forbidden_actions":[],"credentials_required":false,"discovery_notes":[],"ambiguities":[],"risk_level":"low|medium|high"
}"""


class RequirementNormalizer:
    """One interpretation call and at most one JSON-only repair call."""

    def __init__(self, model_config_id: int):
        self.model_config_id = model_config_id
        self.model_manager = get_llm_manager(config_id=model_config_id)

    def normalize(self, description_safe: str, test_case_context: dict[str, Any] | None = None) -> GoalPlan:
        description = redact_text(str(description_safe or '')).strip()
        if not description or _GENERIC_DESCRIPTION_PATTERN.fullmatch(description):
            raise ScenarioInputInsufficientError('scenario_target_missing')
        raw_output = self.model_manager.invoke([
            SystemMessage(content=NORMALIZER_SYSTEM_PROMPT),
            HumanMessage(content=json.dumps({
                'description': description,
                'test_case_context': redact_metadata(test_case_context or {}),
            }, ensure_ascii=False)),
        ])
        return parse_goal_plan_json(raw_output, format_repair=self._repair_json_once)

    def _repair_json_once(self, invalid_output: str, validation_error: str) -> str:
        return self.model_manager.invoke([
            SystemMessage(content='你是严格 JSON 格式修复器。不得新增业务目标、页面元素、定位器或完成状态；只输出 JSON。'),
            HumanMessage(content=json.dumps({
                'instruction': '仅修复 v3 GoalPlan JSON 的格式、字段名或缺失的结构。',
                'validation_error': validation_error,
                'invalid_output': redact_text(invalid_output),
            }, ensure_ascii=False)),
        ])


def normalize_requirement(description_safe: str, *, model_config_id: int, test_case_context: dict[str, Any] | None = None) -> GoalPlan:
    return RequirementNormalizer(model_config_id).normalize(description_safe, test_case_context)
