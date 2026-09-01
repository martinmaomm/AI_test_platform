"""LLM-backed, strictly structured WebUI scenario normalisation."""

from __future__ import annotations

import json
import logging
import re
from copy import deepcopy
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from ai_core.model_manager import get_llm_manager

from .generation_contracts import (
    ScenarioInputInsufficientError,
    ScenarioSpec,
    parse_scenario_spec_json,
)
from .generation_security import redact_metadata, redact_text

logger = logging.getLogger(__name__)


_ACTION_PATTERN = re.compile(
    r'新增|创建|添加|编辑|修改|更新|删除|移除|查询|查看|进入|打开|保存|提交|验证|确认|检查',
)
_OUTCOME_PATTERN = re.compile(
    r'验证|确认|检查|出现|存在|可见|不存在|消失|成功|失败|生效|结果|状态|应当|应该|期望',
)
_GENERIC_DESCRIPTION_PATTERN = re.compile(r'^(?:请)?(?:帮我)?(?:生成|编写|创建)?(?:一个)?(?:测试|用例|脚本)?[。！!？?\s]*$')
_EXPLICIT_SUCCESS_PATTERNS = (
    ('create', re.compile(r'(?:新增|创建|添加).{0,80}(?:出现|存在|可见|验证|确认|检查)')),
    ('update', re.compile(r'(?:编辑|修改|更新).{0,80}(?:验证|确认|检查|生效|成功|更新)')),
    ('delete', re.compile(r'(?:删除|移除).{0,80}(?:验证|确认|检查|不存在|消失)')),
)
_ASSERTION_EXPECTED = {
    'create': '本轮新建的唯一测试数据出现。',
    'update': '本轮新建测试数据的编辑结果已更新。',
    'delete': '本轮新建测试数据已不存在。',
}
_ASSERTION_NAME = {
    'create': '验证新增结果',
    'update': '验证编辑结果',
    'delete': '验证删除结果',
}
_DEFAULT_CLEANUP_CONDITION = '仅清理本轮新建且带唯一标记的测试数据；结束或失败时尝试删除，失败时报告残留。'


NORMALIZER_SYSTEM_PROMPT = """你是 WebUI 自动化测试场景整理器。只将用户已明确表达的目标整理成 JSON，
不得编造接口字段、页面元素、业务结果或登录流程。
页面可通过目标范围内的实际探索获得的信息，例如表单字段、按钮文案、输入提示、菜单层级、相对路径、列表状态和可见元素，
必须写入 discovery_targets，不能写入 ambiguities，也不能要求用户提前提供。
只有页面无法观察且会改变业务含义或安全边界、必须由用户决策的信息，才写入 ambiguities。
用户已经说明通过列表出现、内容更新或记录不存在来验证结果时，不得再追问 Toast 文案等可选成功标志。
普通增删改查目标允许在探索中实际提交和观察结果，不要自行添加“探索只读”的 forbidden_actions。
用户明确写了“只查看/不提交/探索阶段只读”时必须保留在 forbidden_actions，不能为了完成目标忽略它。
默认只能修改、删除本轮新建且带唯一标记的测试数据，不修改已有数据；用户明确指定已有数据时不得扩大到其他记录。
对于本轮新建数据，cleanup 默认写明“结束或失败时尝试清理本轮数据；失败时报告残留”，不要求用户补写这项平台默认策略。
不要输出 Markdown、解释、代码、用户名、密码、Token 或完整 URL。
输出必须严格匹配以下 JSON 结构：
{
  "title":"", "objective":"", "preconditions":[],
  "steps":[{"id":"S1","name":"","intent":"navigate|read|create|update|delete|assert|cleanup","target_hint":"","input_refs":[],"mutates_data":false,"expected":""}],
  "assertions":[{"id":"A1","name":"","target_hint":"","expected":"","step_id":"S1"}],
  "cleanup":[{"id":"C1","name":"","target_hint":"","condition":"","step_id":"S1"}],
  "forbidden_actions":[], "credentials_required":false, "discovery_targets":[],
  "ambiguities":[], "risk_level":"low|medium|high"
}"""


class RequirementNormalizer:
    """One LLM call plus at most one JSON-only format repair."""

    def __init__(self, model_config_id: int):
        self.model_config_id = model_config_id
        self.model_manager = get_llm_manager(config_id=model_config_id)

    def normalize(self, description_safe: str, test_case_context: dict[str, Any] | None = None) -> ScenarioSpec:
        self._raise_if_description_is_clearly_insufficient(description_safe)
        safe_context = redact_metadata(test_case_context or {})
        user_payload = {
            'description': redact_text(description_safe),
            'test_case_context': safe_context,
        }
        raw_output = self.model_manager.invoke([
            SystemMessage(content=NORMALIZER_SYSTEM_PROMPT),
            HumanMessage(content=json.dumps(user_payload, ensure_ascii=False)),
        ])
        return parse_scenario_spec_json(
            raw_output,
            format_repair=self._repair_json_once,
            payload_transform=lambda payload: self._apply_explicit_scenario_defaults(payload, description_safe),
        )

    @staticmethod
    def _raise_if_description_is_clearly_insufficient(description_safe: str) -> None:
        description = redact_text(description_safe).strip()
        if not description or _GENERIC_DESCRIPTION_PATTERN.fullmatch(description):
            raise ScenarioInputInsufficientError('scenario_target_missing')
        if not _ACTION_PATTERN.search(description):
            raise ScenarioInputInsufficientError('scenario_steps_missing')
        if not _OUTCOME_PATTERN.search(description):
            raise ScenarioInputInsufficientError('scenario_outcome_missing')

    @staticmethod
    def _apply_explicit_scenario_defaults(payload: Any, description_safe: str) -> Any:
        """Fill only assertion and cleanup guarantees stated by policy or user text."""
        if not isinstance(payload, dict):
            return payload
        normalized = deepcopy(payload)
        steps = normalized.get('steps')
        if not isinstance(steps, list):
            return normalized

        mutation_steps = [
            step for step in steps
            if isinstance(step, dict) and (step.get('mutates_data') or step.get('intent') in {'create', 'update', 'delete'})
        ]
        assertions = normalized.get('assertions')
        if assertions is None:
            assertions = []
        if isinstance(assertions, list):
            existing_step_ids = {
                item.get('step_id') for item in assertions if isinstance(item, dict)
            }
            used_ids = {
                item.get('id') for item in assertions if isinstance(item, dict)
            }
            for intent, pattern in _EXPLICIT_SUCCESS_PATTERNS:
                if not pattern.search(description_safe):
                    continue
                step = next((item for item in mutation_steps if item.get('intent') == intent), None)
                if step is None:
                    continue
                step_id = step.get('id')
                target_hint = step.get('target_hint')
                if not isinstance(step_id, str) or not isinstance(target_hint, str) or not target_hint.strip():
                    continue
                if step_id in existing_step_ids:
                    continue
                next_index = 1
                while f'A{next_index}' in used_ids:
                    next_index += 1
                assertion_id = f'A{next_index}'
                assertions.append({
                    'id': assertion_id,
                    'name': _ASSERTION_NAME[intent],
                    'target_hint': target_hint,
                    'expected': _ASSERTION_EXPECTED[intent],
                    'step_id': step_id,
                })
                existing_step_ids.add(step_id)
                used_ids.add(assertion_id)
            normalized['assertions'] = assertions

        if mutation_steps and not normalized.get('cleanup'):
            create_step = next((item for item in mutation_steps if item.get('intent') == 'create'), None)
            if create_step is not None:
                step_id = create_step.get('id')
                target_hint = create_step.get('target_hint')
                if isinstance(step_id, str) and isinstance(target_hint, str) and target_hint.strip():
                    normalized['cleanup'] = [{
                        'id': 'C1',
                        'name': '清理本轮新建测试数据',
                        'target_hint': target_hint,
                        'condition': _DEFAULT_CLEANUP_CONDITION,
                        'step_id': step_id,
                    }]
        return normalized

    def _repair_json_once(self, invalid_output: str, validation_error: str) -> str:
        """Ask only for schema-conforming reformatting, never a new interpretation."""
        prompt = {
            'instruction': (
                '仅修复以下 JSON 的格式或缺失结构，不新增业务操作、页面元素、定位器或业务字段。'
                '可根据用户已经明确的成功标准补全 assertions，并为本轮新建的唯一测试数据补全默认 cleanup。只输出 JSON。'
            ),
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
