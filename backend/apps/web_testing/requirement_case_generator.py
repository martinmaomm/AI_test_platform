"""LLM adapter for generating and repairing structured WebUI requirement drafts."""

from __future__ import annotations

import json
import logging

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import ValidationError

from ai_core.model_manager import get_llm_manager
from common.parsers import extract_json_from_output, parse_json_robust

from .generation_security import redact_metadata, redact_text
from .requirement_case_contracts import GeneratedDraftBatch


logger = logging.getLogger(__name__)


class RequirementDraftGenerationError(RuntimeError):
    """Raised when the model cannot produce the required JSON contract."""


class RequirementCaseGenerator:
    """Generate strict drafts with at most one constrained repair call."""

    def __init__(self, model_config_id):
        self.model_config_id = model_config_id
        self.manager = get_llm_manager(config_id=model_config_id)

    @staticmethod
    def _system_prompt():
        return """你是 WebUI 测试用例设计专家。请只设计自然语言测试步骤，不探索网页、不生成代码、不执行测试。

必须遵守：
1. 只输出一个 JSON 对象，顶层字段只能是 test_cases。
2. 每条用例只能包含 title、description、priority、category、preconditions、steps、expected_result。
3. priority 只能是 high、medium、low。
4. category 只能是 functional、negative、boundary。
5. 每个步骤只能包含 step_id、action、target、value、description。
6. action 只能是 goto、click、fill、select、check、hover。
7. goto 不填写 target，必须填写相对路径 value；click/check/hover 必须有 target 且 value 为 null；fill/select 必须同时有 target 和 value。
8. step_id 必须从 1 开始连续。
9. 不得输出 module_id、数据库 ID、定位器、密码、Token、Cookie、API Key 或真实账号。
10. 涉及密码等敏感输入时，只能使用“有效密码”“无效密码”或“由环境变量提供”等语义占位内容。
11. 目标元素优先严格使用提供的页面资产名称；预期结果必须是用户可观察的页面状态。
12. 不得使用 Markdown 代码围栏或附加解释。"""

    @staticmethod
    def _payload(*, context, generation):
        return redact_metadata({
            'locked_module': context.get('module') or {},
            'page_assets': context.get('assets') or {},
            'knowledge_snippets': context.get('knowledge', {}).get('snippets') or [],
            'request': {
                'description': generation.request_text,
                'scope': generation.generation_scope,
                'case_categories': generation.case_categories,
                'target_case_count': generation.target_case_count,
            },
            'output_example': {
                'test_cases': [{
                    'title': '示例用例',
                    'description': '验证一个明确的业务场景。',
                    'priority': 'medium',
                    'category': 'functional',
                    'preconditions': ['已进入目标模块'],
                    'steps': [{
                        'step_id': 1,
                        'action': 'click',
                        'target': '示例按钮',
                        'value': None,
                        'description': '点击示例按钮',
                    }],
                    'expected_result': '页面显示可观察的成功结果。',
                }],
            },
        })

    def _invoke(self, system_prompt, payload):
        return self.manager.invoke([
            SystemMessage(content=system_prompt),
            HumanMessage(content=json.dumps(payload, ensure_ascii=False)),
        ])

    @staticmethod
    def _parse(raw_output):
        json_text = extract_json_from_output(str(raw_output or ''))
        if not json_text:
            raise RequirementDraftGenerationError('模型没有返回可解析的 JSON。')
        try:
            parsed = parse_json_robust(json_text)
            if isinstance(parsed, list):
                parsed = {'test_cases': parsed}
            batch = GeneratedDraftBatch.model_validate(parsed)
        except (ValueError, ValidationError, TypeError) as exc:
            raise RequirementDraftGenerationError('模型返回的用例结构不符合约定。') from exc
        return [item.model_dump(mode='json') for item in batch.test_cases]

    @staticmethod
    def _source_refs(draft, context):
        module = context.get('module') or {}
        refs = [f"module:{module.get('id')}"] if module.get('id') else []
        targets = {
            str(step.get('target') or '').strip().casefold()
            for step in draft.get('steps') or []
            if step.get('target')
        }
        paths = {
            str(step.get('value') or '').strip().rstrip('/') or '/'
            for step in draft.get('steps') or []
            if step.get('action') == 'goto' and step.get('value')
        }
        for page in context.get('assets', {}).get('pages') or []:
            page_targets = {
                str(element.get('name') or '').strip().casefold()
                for element in page.get('elements') or []
            }
            page_path = str(page.get('url_path') or '/').rstrip('/') or '/'
            if targets.intersection(page_targets) or page_path in paths:
                refs.append(f"page:{page.get('id')}")
        for source in (context.get('knowledge', {}).get('matched_sources') or [])[:3]:
            if source.get('id'):
                refs.append(f"knowledge:{source['id']}")
        return list(dict.fromkeys(refs))[:12]

    def _normalize(self, drafts, *, generation, context):
        normalized = []
        for index, draft in enumerate(drafts[:generation.target_case_count], start=1):
            draft = dict(draft)
            draft['draft_key'] = f'draft-{index:03d}'
            draft['module_id'] = generation.module_id
            draft['source_refs'] = self._source_refs(draft, context)
            normalized.append(draft)
        return normalized

    def generate(self, *, generation, context):
        payload = self._payload(context=context, generation=generation)
        raw_output = self._invoke(self._system_prompt(), payload)
        try:
            drafts = self._parse(raw_output)
            repaired = False
        except RequirementDraftGenerationError as first_error:
            repair_payload = {
                'instruction': '修复下面输出，使其严格符合既定 JSON 契约。不得增加说明。',
                'contract_error': str(first_error),
                'invalid_output': redact_text(str(raw_output or ''))[:12000],
                'original_request': payload,
            }
            repaired_output = self._invoke(self._system_prompt(), repair_payload)
            drafts = self._parse(repaired_output)
            repaired = True
        return self._normalize(drafts, generation=generation, context=context), repaired

    def repair(self, *, drafts, report, generation, context):
        payload = {
            'instruction': '只修复确定性校验器指出的 blocker，保持草稿数量和业务意图。严格输出 test_cases JSON。',
            'drafts': redact_metadata(drafts),
            'validation_blockers': redact_metadata(report.get('blockers') or []),
            'original_request': self._payload(context=context, generation=generation),
        }
        raw_output = self._invoke(self._system_prompt(), payload)
        parsed = self._parse(raw_output)
        return self._normalize(parsed, generation=generation, context=context)
