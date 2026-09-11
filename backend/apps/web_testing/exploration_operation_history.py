"""Pure, bounded projection of successful trace actions for exploration memory."""

from copy import deepcopy
import json
from typing import Any


_NOTE = (
    '成功工具动作历史，按原顺序保留重复动作；不代表业务通过，也不是当前页面现场。'
    'selector 和 input_value 沿用 trace 原值，不推断意图或生成断言。'
    'omitted_fields 标明因字符预算整字段省略的输入值；selector 不截断，放不下则整条省略。'
    'omitted_count 仅计因预算省略的成功非 observe 动作；truncated 表示动作或字段有省略。'
)


def _json_chars(value: Any) -> int:
    return len(json.dumps(value, ensure_ascii=False, separators=(',', ':'), allow_nan=False))


def compact_operation_history(
    events: list[dict[str, Any]], *, max_chars: int = 12000,
) -> dict[str, Any]:
    """Keep earliest fitting successful non-observe events without rewriting values.

    Only event_id, tool_name and locator_input.selector/input_value are copied.
    No observation address is projected, so none can be mistaken for a navigate
    input. The caller supplies current trace JSON with runtime values already
    redacted; this function performs no additional data or keyword redaction.

    The complete compact JSON (ensure_ascii=False) fits max_chars, including
    metadata and escaping. Prefer the full action; if it cannot fit, omit its
    entire input_value and mark that field. If it still cannot fit (notably a
    long selector), omit the whole action and consider the next one. Failed,
    blocked and observe events do not contribute to omitted_count.

    Raise ValueError for a non-integer budget or one too small for the fixed
    note and empty-actions envelope. Source data and retained literals are
    never mutated, sliced, normalized or deduplicated.
    """
    if type(max_chars) is not int or max_chars <= 0:
        raise ValueError('max_chars must be a positive integer')

    eligible = [
        event for event in events
        if isinstance(event, dict)
        and event.get('status') == 'succeeded'
        and event.get('action') not in (None, '', 'observe')
    ]
    result = {
        'note': _NOTE, 'actions': [],
        'omitted_count': len(eligible), 'truncated': bool(eligible),
    }
    minimum = _json_chars(result)
    if minimum > max_chars:
        raise ValueError(f'max_chars must be at least {minimum} for the history metadata')

    def with_action(action: dict[str, Any]) -> dict[str, Any]:
        actions = [*result['actions'], action]
        omitted_count = len(eligible) - len(actions)
        return {
            'note': _NOTE, 'actions': actions, 'omitted_count': omitted_count,
            'truncated': bool(omitted_count or any('omitted_fields' in item for item in actions)),
        }

    for event in eligible:
        locator = event.get('locator_input')
        locator = locator if isinstance(locator, dict) else {}
        action = {
            'event_id': event['event_id'],
            'tool_name': event['tool_name'],
            'locator_input': {
                key: deepcopy(locator[key]) for key in ('selector', 'input_value') if key in locator
            },
        }
        candidate = with_action(action)
        if _json_chars(candidate) <= max_chars:
            result = candidate
            continue
        if 'input_value' in action['locator_input']:
            del action['locator_input']['input_value']
            action['omitted_fields'] = {'locator_input.input_value': 'max_chars'}
            candidate = with_action(action)
            if _json_chars(candidate) <= max_chars:
                result = candidate
    return result
