"""Deterministic validation for WebUI requirement-generated drafts."""

from __future__ import annotations

import copy
import json
import re
from collections import Counter
from typing import Any

from .constants import WEBUI_STEPS_MAPPING
from .generation_security import (
    REDACTED_VALUE,
    SENSITIVE_KEY_RE,
    find_suspected_credentials,
    redact_metadata,
)


VALID_PRIORITIES = {'high', 'medium', 'low'}
VALID_CATEGORIES = {'functional', 'negative', 'boundary'}
GENERIC_EXPECTATIONS = {
    '操作成功', '操作正常', '页面正常', '符合预期', '显示正常',
    '成功', '正常', '通过',
}
SAFE_SENSITIVE_PLACEHOLDER_TERMS = {
    '有效', '无效', '测试', '动态', '随机', '环境变量', '占位', '脱敏', 'redacted',
    'variable', 'placeholder', '${', '{{', '<',
}
SENSITIVE_TARGET_RE = re.compile(r'(?:密码|口令|令牌|密钥|会话|cookie)', re.IGNORECASE)
DRAFT_FIELDS = {
    'draft_key', 'module_id', 'source_refs', 'title', 'description', 'priority',
    'category', 'preconditions', 'steps', 'expected_result',
}
STEP_FIELDS = {'step_id', 'action', 'target', 'value', 'description'}


def _issue(level, code, message, *, draft_key='', field='', step_id=None):
    issue = {
        'level': level,
        'code': code,
        'message': message,
        'draft_key': draft_key,
    }
    if field:
        issue['field'] = field
    if step_id is not None:
        issue['step_id'] = step_id
    return issue


def _text(value):
    return str(value or '').strip()


def _context_assets(context):
    pages = context.get('assets', {}).get('pages', []) if isinstance(context, dict) else []
    element_names = set()
    page_paths = set()
    for page in pages:
        path = _text(page.get('url_path'))
        if path:
            page_paths.add(path.rstrip('/') or '/')
        for element in page.get('elements') or []:
            name = _text(element.get('name'))
            if name:
                element_names.add(name.casefold())
    return element_names, page_paths


def _contains_sensitive_value(draft):
    serialized = json.dumps(draft, ensure_ascii=False)
    if find_suspected_credentials(serialized):
        return True
    for step in draft.get('steps') or []:
        target = _text(step.get('target'))
        value = _text(step.get('value'))
        if value and _is_sensitive_target(target) and not _is_safe_sensitive_placeholder(value):
            return True
    return False


def _is_safe_sensitive_placeholder(value):
    normalized = _text(value).casefold()
    return any(term.casefold() in normalized for term in SAFE_SENSITIVE_PLACEHOLDER_TERMS)


def _is_sensitive_target(value):
    return bool(SENSITIVE_KEY_RE.search(_text(value)) or SENSITIVE_TARGET_RE.search(_text(value)))


def sanitize_requirement_drafts(drafts):
    """Redact suspected secrets before any draft is persisted."""

    originals = copy.deepcopy(drafts or [])
    sanitized = redact_metadata(copy.deepcopy(originals))
    for original_draft, sanitized_draft in zip(originals, sanitized):
        if not isinstance(original_draft, dict) or not isinstance(sanitized_draft, dict):
            continue
        original_steps = original_draft.get('steps') or []
        sanitized_steps = sanitized_draft.get('steps') or []
        for original_step, sanitized_step in zip(original_steps, sanitized_steps):
            if not isinstance(original_step, dict) or not isinstance(sanitized_step, dict):
                continue
            target = _text(original_step.get('target'))
            value = _text(original_step.get('value'))
            unsafe_sensitive_value = (
                _is_sensitive_target(target)
                or find_suspected_credentials(value)
            )
            if value and unsafe_sensitive_value and not _is_safe_sensitive_placeholder(value):
                sanitized_step['value'] = REDACTED_VALUE
    return sanitized


def validate_requirement_drafts(drafts, *, generation, context):
    """Return per-draft blockers/warnings and an aggregate summary."""

    drafts = drafts if isinstance(drafts, list) else []
    context = context if isinstance(context, dict) else {}
    element_names, page_paths = _context_assets(context or {})
    selected_categories = set(generation.case_categories or [])
    items = []
    global_blockers = []
    global_warnings = []
    seen_keys = set()
    signatures = Counter()

    if len(drafts) > generation.target_case_count:
        global_blockers.append(_issue(
            'blocker',
            'DRAFT_COUNT_EXCEEDED',
            f'草稿数量不能超过本次配置的 {generation.target_case_count} 条。',
        ))
    if not drafts:
        global_blockers.append(_issue('blocker', 'DRAFTS_EMPTY', '至少需要保留一条测试用例草稿。'))

    for index, draft in enumerate(drafts, start=1):
        blockers = []
        warnings = []
        draft = draft if isinstance(draft, dict) else {}
        raw_draft_key = _text(draft.get('draft_key'))
        draft_key = raw_draft_key or f'draft-{index:03d}'

        if not raw_draft_key:
            blockers.append(_issue(
                'blocker', 'DRAFT_KEY_MISSING', '草稿标识不能为空，请重新生成该草稿。',
                draft_key=draft_key, field='draft_key',
            ))
        elif len(raw_draft_key) > 80:
            blockers.append(_issue(
                'blocker', 'DRAFT_KEY_TOO_LONG', '草稿标识不能超过 80 个字符。',
                draft_key=draft_key, field='draft_key',
            ))
        unknown_fields = sorted(set(draft) - DRAFT_FIELDS)
        if unknown_fields:
            blockers.append(_issue(
                'blocker', 'DRAFT_FIELDS_UNKNOWN',
                f'草稿包含系统不支持的字段：{", ".join(unknown_fields)}。',
                draft_key=draft_key,
            ))

        if draft_key in seen_keys:
            blockers.append(_issue(
                'blocker', 'DRAFT_KEY_DUPLICATE', '草稿标识重复，请重新生成或删除重复项。',
                draft_key=draft_key, field='draft_key',
            ))
        seen_keys.add(draft_key)

        raw_module_id = draft.get('module_id')
        try:
            module_matches = int(raw_module_id) == int(generation.module_id)
        except (TypeError, ValueError):
            module_matches = False
        if not module_matches:
            blockers.append(_issue(
                'blocker', 'MODULE_MISMATCH', '草稿模块与本次锁定的业务模块不一致。',
                draft_key=draft_key, field='module_id',
            ))

        for field, label in (
            ('title', '用例名称'),
            ('description', '用例描述'),
            ('expected_result', '预期结果'),
        ):
            if not _text(draft.get(field)):
                blockers.append(_issue(
                    'blocker', 'REQUIRED_TEXT_MISSING', f'{label}不能为空。',
                    draft_key=draft_key, field=field,
                ))

        for field, limit, label in (
            ('title', 200, '用例名称'),
            ('description', 1000, '用例描述'),
            ('expected_result', 1000, '预期结果'),
        ):
            if len(_text(draft.get(field))) > limit:
                blockers.append(_issue(
                    'blocker', 'TEXT_TOO_LONG', f'{label}不能超过 {limit} 个字符。',
                    draft_key=draft_key, field=field,
                ))

        preconditions = draft.get('preconditions')
        if not isinstance(preconditions, list):
            blockers.append(_issue(
                'blocker', 'PRECONDITIONS_INVALID', '前置条件必须是列表。',
                draft_key=draft_key, field='preconditions',
            ))
        elif len(preconditions) > 20 or any(len(_text(item)) > 500 for item in preconditions):
            blockers.append(_issue(
                'blocker', 'PRECONDITIONS_TOO_LONG', '前置条件最多 20 项，单项不能超过 500 个字符。',
                draft_key=draft_key, field='preconditions',
            ))

        if draft.get('priority') not in VALID_PRIORITIES:
            blockers.append(_issue(
                'blocker', 'PRIORITY_INVALID', '优先级必须是 high、medium 或 low。',
                draft_key=draft_key, field='priority',
            ))
        if draft.get('category') not in VALID_CATEGORIES:
            blockers.append(_issue(
                'blocker', 'CATEGORY_INVALID', '用例类型必须是功能、异常或边界测试。',
                draft_key=draft_key, field='category',
            ))
        elif selected_categories and draft.get('category') not in selected_categories:
            warnings.append(_issue(
                'warning', 'CATEGORY_NOT_SELECTED', '该草稿类型不在本次选择的用例类型中。',
                draft_key=draft_key, field='category',
            ))

        steps = draft.get('steps') if isinstance(draft.get('steps'), list) else []
        if not steps:
            blockers.append(_issue(
                'blocker', 'STEPS_EMPTY', '测试步骤至少需要一项。',
                draft_key=draft_key, field='steps',
            ))
        elif len(steps) > 30:
            blockers.append(_issue(
                'blocker', 'STEPS_TOO_MANY', '测试步骤不能超过 30 项。',
                draft_key=draft_key, field='steps',
            ))
        step_ids = [step.get('step_id') for step in steps if isinstance(step, dict)]
        if step_ids != list(range(1, len(steps) + 1)):
            blockers.append(_issue(
                'blocker', 'STEP_ID_SEQUENCE_INVALID', '步骤编号必须从 1 开始且连续。',
                draft_key=draft_key, field='steps',
            ))

        signature = []
        for step_index, step in enumerate(steps, start=1):
            step = step if isinstance(step, dict) else {}
            step_id = step.get('step_id', step_index)
            action = _text(step.get('action'))
            target = _text(step.get('target'))
            value = _text(step.get('value'))
            description = _text(step.get('description'))
            config = WEBUI_STEPS_MAPPING.get(action)

            unknown_step_fields = sorted(set(step) - STEP_FIELDS)
            if unknown_step_fields:
                blockers.append(_issue(
                    'blocker', 'STEP_FIELDS_UNKNOWN',
                    f'步骤 {step_id} 包含系统不支持的字段：{", ".join(unknown_step_fields)}。',
                    draft_key=draft_key, field='steps', step_id=step_id,
                ))
            for field, value_text, limit in (
                ('target', target, 200),
                ('value', value, 1000),
                ('description', description, 500),
            ):
                if len(value_text) > limit:
                    blockers.append(_issue(
                        'blocker', 'STEP_TEXT_TOO_LONG',
                        f'步骤 {step_id} 的 {field} 不能超过 {limit} 个字符。',
                        draft_key=draft_key, field=field, step_id=step_id,
                    ))

            if not config:
                blockers.append(_issue(
                    'blocker', 'ACTION_INVALID', f'步骤 {step_id} 使用了系统不支持的动作。',
                    draft_key=draft_key, field='action', step_id=step_id,
                ))
                continue
            if not description:
                blockers.append(_issue(
                    'blocker', 'STEP_DESCRIPTION_MISSING', f'步骤 {step_id} 缺少操作说明。',
                    draft_key=draft_key, field='description', step_id=step_id,
                ))
            if config['needTarget'] and not target:
                blockers.append(_issue(
                    'blocker', 'STEP_TARGET_MISSING', f'步骤 {step_id} 必须指定目标元素。',
                    draft_key=draft_key, field='target', step_id=step_id,
                ))
            if not config['needTarget'] and target:
                blockers.append(_issue(
                    'blocker', 'STEP_TARGET_FORBIDDEN', f'步骤 {step_id} 的动作不应填写目标元素。',
                    draft_key=draft_key, field='target', step_id=step_id,
                ))
            if config['needValue'] and not value:
                blockers.append(_issue(
                    'blocker', 'STEP_VALUE_MISSING', f'步骤 {step_id} 必须填写输入值。',
                    draft_key=draft_key, field='value', step_id=step_id,
                ))
            if not config['needValue'] and value:
                blockers.append(_issue(
                    'blocker', 'STEP_VALUE_FORBIDDEN', f'步骤 {step_id} 的动作不应填写输入值。',
                    draft_key=draft_key, field='value', step_id=step_id,
                ))

            if config['needTarget'] and target and target.casefold() not in element_names:
                warnings.append(_issue(
                    'warning', 'TARGET_NOT_IN_ASSETS', f'步骤 {step_id} 的目标元素未在当前模块资产中精确匹配。',
                    draft_key=draft_key, field='target', step_id=step_id,
                ))
            if action == 'goto' and value:
                normalized_path = value.rstrip('/') or '/'
                if value.startswith('/') and normalized_path not in page_paths:
                    warnings.append(_issue(
                        'warning', 'PAGE_PATH_NOT_IN_ASSETS', f'步骤 {step_id} 的页面路径未在当前模块资产中匹配。',
                        draft_key=draft_key, field='value', step_id=step_id,
                    ))
            signature.append((action, target.casefold(), value.casefold()))

        if _contains_sensitive_value(draft):
            blockers.append(_issue(
                'blocker', 'SENSITIVE_VALUE_DETECTED', '草稿中疑似包含密码、Token、Cookie 或密钥，已禁止导入。',
                draft_key=draft_key,
            ))

        expected_result = _text(draft.get('expected_result'))
        if len(expected_result) < 8 or expected_result in GENERIC_EXPECTATIONS:
            warnings.append(_issue(
                'warning', 'EXPECTED_RESULT_TOO_GENERIC', '预期结果过于笼统，建议补充可观察的页面结果。',
                draft_key=draft_key, field='expected_result',
            ))

        signature_tuple = tuple(signature)
        if signature_tuple:
            signatures[signature_tuple] += 1
        items.append({
            'draft_key': draft_key,
            'valid': not blockers,
            'blockers': blockers,
            'warnings': warnings,
        })

    duplicate_signatures = {signature for signature, count in signatures.items() if count > 1}
    if duplicate_signatures:
        for item, draft in zip(items, drafts):
            draft = draft if isinstance(draft, dict) else {}
            signature = tuple(
                (
                    _text(step.get('action')),
                    _text(step.get('target')).casefold(),
                    _text(step.get('value')).casefold(),
                )
                for step in (draft.get('steps') or [])
                if isinstance(step, dict)
            )
            if signature in duplicate_signatures:
                item['warnings'].append(_issue(
                    'warning', 'HIGHLY_SIMILAR_STEPS', '该用例与本批次其他用例的步骤高度重复。',
                    draft_key=item['draft_key'],
                ))

    actual_categories = {
        draft.get('category') for draft in drafts
        if isinstance(draft, dict) and draft.get('category') in VALID_CATEGORIES
    }
    for missing_category in sorted(selected_categories - actual_categories):
        global_warnings.append(_issue(
            'warning', 'SELECTED_CATEGORY_MISSING',
            f'本批草稿没有覆盖已选择的 {missing_category} 类型。',
        ))
    if not context.get('knowledge', {}).get('matched_sources'):
        global_warnings.append(_issue(
            'warning', 'KNOWLEDGE_NOT_MATCHED', '本次没有命中可用的知识资料，结果主要依据模块和页面资产。',
        ))

    blockers = global_blockers + [issue for item in items for issue in item['blockers']]
    warnings = global_warnings + [issue for item in items for issue in item['warnings']]
    importable_count = sum(1 for item in items if not item['blockers'])
    return {
        'valid': not blockers,
        'items': items,
        'blockers': blockers,
        'warnings': warnings,
        'summary': {
            'draft_count': len(drafts),
            'importable_count': importable_count,
            'blocked_count': len(items) - importable_count,
            'blocker_count': len(blockers),
            'warning_count': len(warnings),
        },
    }
