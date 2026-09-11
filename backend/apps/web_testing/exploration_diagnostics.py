"""Bounded browser-owned diagnostics, separate from model prose and page HTML."""

import base64
import json
import re
from urllib.parse import urlsplit, urlunsplit


DIAGNOSTICS_MARKER = re.compile(r'PLATFORM_BROWSER_DIAGNOSTICS_V1:([A-Za-z0-9_-]+)')
SCREENSHOT_NAME = re.compile(r'failure-[A-Za-z0-9_-]{1,100}\.png\Z')
_TEXT_FIELDS = {
    'page_title': 200, 'element_label': 200, 'container_label': 200,
    'selector': 1000, 'captured_at': 50, 'reason_code': 80,
    'message': 500, 'screenshot_message': 300,
}


def _text(value, limit):
    return re.sub(r'[\x00-\x1f\x7f]', ' ', value).strip()[:limit] if isinstance(value, str) else ''


def normalize_page_context(value):
    if not isinstance(value, dict) or not value:
        return {}
    result = {key: _text(value.get(key), limit) for key, limit in _TEXT_FIELDS.items()}
    result['page_url'] = ''
    try:
        url = urlsplit(_text(value.get('page_url'), 2048))
        if url.scheme in {'http', 'https'} and url.hostname:
            # Credentials in URL userinfo never belong in a diagnostic link.
            result['page_url'] = urlunsplit((url.scheme, url.netloc.rsplit('@', 1)[-1], url.path, url.query, url.fragment))
    except ValueError:
        pass
    breadcrumbs = value.get('breadcrumbs')
    result['breadcrumbs'] = [_text(item, 100) for item in breadcrumbs[:8] if isinstance(item, str)] if isinstance(breadcrumbs, list) else []
    for key in ('visible', 'enabled', 'tool_failed'):
        result[key] = value.get(key) if isinstance(value.get(key), bool) else None
    count = value.get('matched_count')
    result['matched_count'] = count if isinstance(count, int) and not isinstance(count, bool) and 0 <= count <= 100000 else None
    filename = value.get('screenshot_file')
    result['screenshot_file'] = filename if isinstance(filename, str) and SCREENSHOT_NAME.fullmatch(filename) else ''
    screenshot_status = value.get('screenshot_status')
    result['screenshot_status'] = screenshot_status if screenshot_status in ('captured', 'unavailable', 'not_requested') else 'not_requested'
    if result['screenshot_status'] == 'captured' and not result['screenshot_file']:
        result['screenshot_status'] = 'unavailable'
        result['screenshot_message'] = '截图文件信息无效，无法展示。'
    return result


def extract_page_context(text):
    """Decode the final platform trailer even through MCP's TextContent repr.

    Never eval tool text. The trailer uses URL-safe base64 solely as framing;
    it is removed from raw excerpts and model projections to avoid token bloat.
    """
    matches = list(DIAGNOSTICS_MARKER.finditer(text))
    context = {}
    if matches and len(matches[-1][1]) <= 24000:
        encoded = matches[-1][1]
        try:
            decoded = base64.b64decode(encoded + '=' * (-len(encoded) % 4), altchars=b'-_', validate=True)
            context = normalize_page_context(json.loads(decoded))
        except (ValueError, UnicodeDecodeError):
            pass
    return context, DIAGNOSTICS_MARKER.sub('', text)


def failure_context(events, error_code='', error_message=''):
    """Describe the terminal event, never reinterpret an earlier recovered error."""
    if not error_code:
        return {}
    event = events[-1] if events else {}
    context = normalize_page_context(event.get('page_context'))
    # Model/transport errors and total limits can happen after an unrelated tool
    # failure. Do not present that earlier screenshot as their failure scene.
    non_browser_stop = (
        str(error_code).upper().startswith('MODEL_')
        or error_code in {'model_service_error', 'rate_limit', 'transient', 'other',
                         'graph_recursion', 'exploration_timeout', 'TASK_CANCELLED', 'CHECKPOINT_FAILED'}
    )
    failed_event = event.get('status') in {'failed', 'blocked'} and not non_browser_stop
    message = context.get('message') if failed_event else ''
    reason = context.get('reason_code') if failed_event else ''
    if failed_event and not message:
        raw = event.get('raw_output') or event.get('result_excerpt') or ''
        if re.search(r'(?:element is not visible|resolved to hidden|not visible|hidden)', raw, re.I):
            reason, message = 'element_hidden', '目标元素不可见，可能位于已关闭的弹窗或隐藏区域；请先确认当前可见页面。'
        elif re.search(r'timeout|timed out', raw, re.I):
            reason, message = 'element_timeout', '等待目标元素或操作完成超时；请检查定位器和当前页面状态。'
    return {
        **context,
        'event_id': event.get('event_id', ''), 'sequence': event.get('sequence'),
        'action': event.get('action', ''), 'tool_name': event.get('tool_name', ''),
        'selector': context.get('selector') or _text((event.get('locator_input') or {}).get('selector'), 1000),
        'message': message or error_message or '探索已停止，请查看任务终止原因。',
        'reason_code': reason or error_code,
        'location_source': 'failure_event' if failed_event else 'last_observed',
        'screenshot_status': context.get('screenshot_status', 'not_requested') if failed_event else 'not_requested',
        'screenshot_file': context.get('screenshot_file', '') if failed_event else '',
        'screenshot_message': (
            context.get('screenshot_message', '') or ('本次未采集失败现场截图。' if not context.get('screenshot_file') else '')
        ) if failed_event else '本次终止未采集失败现场截图，页面信息仅供参考。',
    }
