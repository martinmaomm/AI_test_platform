"""Bounded browser-owned diagnostics, separate from model prose and page HTML."""

import base64
import json
import re
from urllib.parse import urlsplit, urlunsplit


DIAGNOSTICS_MARKER = re.compile(r'PLATFORM_BROWSER_DIAGNOSTICS_V1:([A-Za-z0-9_-]+)')
SCREENSHOT_NAME = re.compile(r'failure-[A-Za-z0-9_-]{1,100}\.png\Z')
# This is the wire-text limit, not a decoded-byte estimate.  It keeps the
# consumer aligned with the Node protocol and still admits multilingual JSON.
MAX_DIAGNOSTICS_ENCODED_CHARS = 100000
MAX_DIAGNOSTICS_DECODED_BYTES = (MAX_DIAGNOSTICS_ENCODED_CHARS // 4) * 3
MAX_OBSERVATION_JSON_CHARS = 16000
_FINGERPRINT = re.compile(r'[a-f0-9]{64}\Z')
_TEXT_FIELDS = {
    'page_title': 200, 'element_label': 200, 'container_label': 200,
    'selector': 1000, 'captured_at': 50, 'reason_code': 80,
    'message': 500, 'screenshot_message': 300,
}


def _observation_json_size(value):
    return len(json.dumps(value, ensure_ascii=False, separators=(',', ':'), default=str))


def _text(value, limit):
    return re.sub(r'[\x00-\x1f\x7f]', ' ', value).strip()[:limit] if isinstance(value, str) else ''


def _was_text_truncated(value, limit):
    return isinstance(value, str) and len(value.strip()) > limit


def normalize_observation(value):
    """Validate the browser-owned semantic observation without trusting prose.

    The Node producer is responsible for the primary bound.  The consumer
    applies a second bound so a malformed or stale producer cannot inflate
    diagnostic storage or model context.
    """
    if not isinstance(value, dict):
        return {}
    if (
        not isinstance(value.get('version'), int)
        or isinstance(value.get('version'), bool)
        or value.get('version') != 1
        or not isinstance(value.get('page_url'), str)
    ):
        return {}
    fingerprint = value.get('fingerprint')
    if not isinstance(fingerprint, str) or not _FINGERPRINT.fullmatch(fingerprint):
        return {}
    scope = _text(value.get('scope'), 1000)
    if not scope or not isinstance(value.get('page_title'), str):
        return {}
    if not isinstance(value.get('settled'), bool) or not isinstance(value.get('truncated'), bool):
        return {}
    notes = value.get('notes')
    elements = value.get('elements')
    text = value.get('text')
    if not isinstance(notes, list) or not isinstance(elements, list) or not isinstance(text, list):
        return {}
    raw_page_url = _text(value.get('page_url'), 2048)
    page_url = ''
    try:
        parsed = urlsplit(raw_page_url)
        if parsed.scheme in {'http', 'https'} and parsed.hostname:
            page_url = urlunsplit((parsed.scheme, parsed.netloc.rsplit('@', 1)[-1], parsed.path, parsed.query, parsed.fragment))
    except ValueError:
        return {}
    # Blank fixtures are a real browser state.  Do not permit any other
    # non-network scheme into model-visible diagnostics.
    if raw_page_url == 'about:blank':
        page_url = raw_page_url
    if not page_url:
        return {}
    normalized_elements = []
    element_limits = {
        'tag': 80, 'role': 100, 'name': 300, 'id': 200, 'type': 100,
        'placeholder': 300, 'container': 500,
    }
    consumer_truncated = (
        len(notes) > 24 or len(elements) > 48 or len(text) > 80
        or _was_text_truncated(value.get('page_url'), 2048)
        or _was_text_truncated(value.get('page_title'), 300)
        or _was_text_truncated(value.get('scope'), 1000)
    )
    for item in elements[:48]:
        if not isinstance(item, dict) or item.get('visible') is not True:
            consumer_truncated = True
            continue
        enabled = item.get('enabled')
        if enabled is not None and not isinstance(enabled, bool):
            consumer_truncated = True
            continue
        if any(_was_text_truncated(item.get(key), limit) for key, limit in element_limits.items()):
            consumer_truncated = True
        normalized_elements.append({
            **{key: _text(item.get(key), limit) for key, limit in element_limits.items()},
            'visible': True,
            'enabled': enabled,
        })
    result = {
        'version': 1,
        'page_url': page_url,
        'page_title': _text(value.get('page_title'), 300),
        'scope': scope,
        'fingerprint': fingerprint,
        'settled': value['settled'],
        'truncated': value['truncated'] or consumer_truncated,
        'notes': [_text(item, 300) for item in notes[:24] if isinstance(item, str)],
        'elements': normalized_elements,
        'text': [_text(item, 500) for item in text[:80] if isinstance(item, str)],
    }
    if any(_was_text_truncated(item, 300) for item in notes[:24] if isinstance(item, str)):
        result['truncated'] = True
    if any(_was_text_truncated(item, 500) for item in text[:80] if isinstance(item, str)):
        result['truncated'] = True
    # Drop least useful text first, then controls/notes.  Core state fields
    # remain available even when an oversized producer violates its contract.
    while _observation_json_size(result) > MAX_OBSERVATION_JSON_CHARS:
        result['truncated'] = True
        if result['text']:
            result['text'].pop()
        elif result['elements']:
            result['elements'].pop()
        elif result['notes']:
            result['notes'].pop()
        else:
            return {}
    return result


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
    observation = normalize_observation(value.get('observation'))
    if observation:
        result['observation'] = observation
    elif 'observation' in value:
        # The producer opted into structured evidence, but it was not safe to
        # validate.  Callers must not silently promote the raw reader excerpt.
        result['observation_error'] = 'invalid'
    return result


def extract_page_context(text):
    """Decode the final platform trailer even through MCP's TextContent repr.

    Never eval tool text. The trailer uses URL-safe base64 solely as framing;
    it is removed from raw excerpts and model projections to avoid token bloat.
    """
    if not isinstance(text, str):
        return {}, ''
    matches = list(DIAGNOSTICS_MARKER.finditer(text))
    context = {}
    if matches and len(matches[-1][1]) <= MAX_DIAGNOSTICS_ENCODED_CHARS:
        encoded = matches[-1][1]
        try:
            decoded = base64.b64decode(encoded + '=' * (-len(encoded) % 4), altchars=b'-_', validate=True)
            if len(decoded) <= MAX_DIAGNOSTICS_DECODED_BYTES:
                context = normalize_page_context(json.loads(decoded))
        except (ValueError, UnicodeDecodeError, json.JSONDecodeError):
            pass
    return context, DIAGNOSTICS_MARKER.sub('', text)


def render_target_diagnostics(context):
    """Render explicit locator evidence even without a structured snapshot."""
    if not isinstance(context, dict):
        return ''
    if context.get('element_label') or context.get('selector') or context.get('visible') is not None:
        details = []
        if context.get('element_label'):
            details.append('目标=' + context['element_label'])
        if context.get('selector'):
            details.append('定位=' + context['selector'])
        if context.get('visible') is not None:
            details.append('可见=' + ('是' if context['visible'] else '否'))
        if context.get('enabled') is not None:
            details.append('可用=' + ('是' if context['enabled'] else '否'))
        if context.get('matched_count') is not None:
            details.append('匹配数=' + str(context['matched_count']))
        if context.get('reason_code'):
            details.append('原因=' + context['reason_code'])
        if context.get('message'):
            details.append('说明=' + context['message'])
        return '目标诊断：' + '；'.join(details)
    return ''


def render_observation_unavailable(context):
    if isinstance(context, dict) and context.get('observation_error'):
        return '[平台页面观察不可用：结构化 observation 无效；原始页面内容不能作为已验证页面证据。]'
    return ''


def _whole_lines(lines, limit):
    selected = []
    size = 0
    for line in lines:
        extra = len(line) + (1 if selected else 0)
        if size + extra > limit:
            continue
        selected.append(line)
        size += extra
    if len(selected) == len(lines):
        return '\n'.join(selected)
    marker = '[页面观察摘要已截断。]'
    if len(marker) > limit:
        marker = '[截断]' if len('[截断]') <= limit else ''
    # Reserve a whole-line marker even at a near-exact budget.  Removing the
    # latest complete record is safer than silently implying a full snapshot.
    while marker and selected and size + len(marker) + 1 > limit:
        removed = selected.pop()
        size -= len(removed) + (1 if selected else 0)
    if marker and size + len(marker) + (1 if selected else 0) <= limit:
        selected.append(marker)
    return '\n'.join(selected)


def render_observation(context, limit=8000):
    """Render a bounded semantic model projection, never raw trailer bytes."""
    observation = context.get('observation') if isinstance(context, dict) else None
    if not isinstance(observation, dict):
        return ''
    mandatory = [
        '[平台页面观察]',
        f"范围：{observation['scope']}；地址：{observation['page_url']}；标题：{observation['page_title']}",
        f"页面稳定：{'是' if observation['settled'] else '否'}；观察截断：{'是' if observation['truncated'] else '否'}",
    ]
    if target := render_target_diagnostics(context):
        mandatory.append(target)
    mandatory.extend('说明：' + note for note in observation['notes'])

    def priority(item):
        tag = item.get('tag', '').lower()
        role = item.get('role', '').lower()
        if tag == 'dialog' or role in {'dialog', 'alertdialog'}:
            return 0
        if tag == 'form':
            return 1
        if tag in {'input', 'textarea', 'select', 'button'} or role in {'button', 'textbox', 'combobox', 'checkbox', 'radio'}:
            return 2
        return 3

    records = []
    for item in sorted(observation['elements'], key=priority):
        details = [f"<{item['tag'] or 'element'}>"]
        for key, label in (('role', 'role'), ('name', 'name'), ('id', 'id'), ('type', 'type'), ('placeholder', 'placeholder'), ('container', 'container')):
            if item.get(key):
                details.append(f'{label}={item[key]}')
        details.append('enabled=' + ('unknown' if item['enabled'] is None else str(item['enabled']).lower()))
        records.append('控件：' + '；'.join(details))
    for item in observation['text']:
        records.append('可见文本：' + item)
    return _whole_lines([*mandatory, *records], limit)


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
