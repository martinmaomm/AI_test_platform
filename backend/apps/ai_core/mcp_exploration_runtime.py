"""Model-only context projection and serial action/observation pairing.

Never rewrites the graph's durable messages or the browser callbacks. Automatic
reads go through the same tool handler as ordinary reads (including budgets,
cancellation and trace callbacks). No business labels or site-specific rules.
"""

import asyncio
import json
import re
from dataclasses import replace
from uuid import uuid4

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage


OBSERVATION_MARKER = '[平台自动页面观察'
READ_TOOLS = frozenset({'playwright_get_visible_html', 'playwright_get_visible_text', 'playwright_snapshot'})
# Tool capabilities, not a dictionary of business intents/button names.
OBSERVED_ACTIONS = frozenset({
    'playwright_navigate', 'playwright_click', 'playwright_click_and_switch_tab',
    'playwright_press_key', 'playwright_go_back', 'playwright_go_forward',
})


def as_text(value):
    return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)


def bounded_text(value, limit=8000):
    text = as_text(value)
    if len(text) <= limit:
        return text
    return text[:limit] + '\n[内容已截断；缺失部分不能视为不存在，请按需读取原页面或草稿。]'


def compact_page(value, limit=8000):
    text = as_text(value)
    # Remove non-visible bulk, without changing element attributes or labels.
    text = re.sub(r'<(script|style)\b[^>]*>.*?</\1\s*>', '[省略非可见代码]', text, flags=re.I | re.S)
    text = re.sub(r'data:image/[^;,\s]+;base64,[a-z0-9+/=]+', '[省略图片数据]', text, flags=re.I)
    return bounded_text(text, limit)


def compact_saved_evidence(snapshot):
    """Small historical reference, not a replacement for the saved trace."""
    source = snapshot if isinstance(snapshot, dict) else {}
    def bound(value):
        if isinstance(value, str):
            return bounded_text(value, 2000)
        if isinstance(value, list):
            return [bound(item) for item in value[:30]]
        if isinstance(value, dict):
            return {key: bound(item) for key, item in value.items()}
        return value
    return {
        'note': '历史证据摘要，可能省略或截断；不是当前浏览器状态，不据此推断未观察的结果。',
        'events': bound((source.get('events') or [])[-12:]),
        'page_states': bound((source.get('page_states') or [])[-2:]),
        'locator_evidence': bound((source.get('locator_evidence') or [])[-30:]),
        'trace_path': source.get('trace_path', ''),
    }


def message_chars(messages):
    return sum(len(as_text(message.model_dump())) for message in messages)


def project_messages(messages, *, budget=48000, page_limit=8000):
    """Drop whole old tool exchanges, preserving the latest page and two rounds.

    User instructions and mandatory recent groups may exceed the soft budget.
    Tool-call arguments are never shortened into invalid calls and no orphan
    ToolMessage is created by pruning. The source objects remain unchanged.
    """
    projected = []
    for message in messages:
        if isinstance(message, ToolMessage) and (
            message.name in READ_TOOLS or OBSERVATION_MARKER in as_text(message.content)
        ):
            message = message.model_copy(update={'content': compact_page(message.content, page_limit)})
        projected.append(message)

    groups = []
    index = 0
    while index < len(projected):
        message = projected[index]
        if isinstance(message, AIMessage) and message.tool_calls:
            ids = {call['id'] for call in message.tool_calls}
            end = index + 1
            while end < len(projected) and isinstance(projected[end], ToolMessage):
                end += 1
            replies = projected[index + 1:end]
            if {item.tool_call_id for item in replies} == ids:
                groups.append((index, end))
            index = end
        else:
            index += 1
    latest_page = next((
        group for group in reversed(groups)
        if any(isinstance(item, ToolMessage) and (
            item.name in READ_TOOLS or OBSERVATION_MARKER in as_text(item.content)
        ) for item in projected[group[0]:group[1]])
    ), None)
    protected = set(groups[-2:]) | ({latest_page} if latest_page else set())
    removed = set()
    size = message_chars(projected)
    for group in groups:
        if size <= budget:
            break
        if group in protected:
            continue
        size -= message_chars(projected[group[0]:group[1]])
        removed.update(range(*group))
    return [item for index, item in enumerate(projected) if index not in removed], len(removed)


class ExplorationRuntimeMiddleware(AgentMiddleware):
    def __init__(self, tools, *, checkpoint, failed, stats, auto_observe=True, context_chars=48000):
        self.checkpoint = checkpoint
        self.failed = failed
        self.stats = stats
        self.auto_observe = auto_observe
        self.context_chars = context_chars
        self._lock = asyncio.Lock()
        available = {tool.name: tool for tool in tools}
        self.reader = None
        for name in ('playwright_get_visible_html', 'playwright_get_visible_text'):
            tool = available.get(name)
            if tool is None:
                continue
            schema = tool.tool_call_schema
            schema = schema if isinstance(schema, dict) else schema.model_json_schema()
            if not schema.get('required'):
                self.reader = tool
                break

    @staticmethod
    def _next_is_observation(request):
        messages = request.state.get('messages', [])
        if not messages or not isinstance(messages[-1], AIMessage):
            return False
        calls = messages[-1].tool_calls
        for index, call in enumerate(calls[:-1]):
            if call['id'] == request.tool_call['id']:
                return calls[index + 1]['name'] in READ_TOOLS
        return False

    async def awrap_tool_call(self, request, handler):
        # A whole action+observation is serial, including local draft tools.
        async with self._lock:
            result = await handler(request)
            name = request.tool_call['name']
            if not (self.auto_observe and self.reader and name in OBSERVED_ACTIONS):
                return result
            if not isinstance(result, ToolMessage) or self.failed(result, tool_name=name):
                return result
            if self._next_is_observation(request):
                self.stats['automatic_observations_skipped'] += 1
                return result
            self.stats['automatic_observations'] += 1
            try:
                observed = await handler(replace(request, tool=self.reader, tool_call={
                    'name': self.reader.name, 'args': {},
                    'id': 'auto-observe-' + uuid4().hex, 'type': 'tool_call',
                }))
                if self.failed(observed, tool_name=self.reader.name):
                    raise ValueError(bounded_text(getattr(observed, 'content', str(observed)), 1000))
                observation = compact_page(getattr(observed, 'content', str(observed)))
            except Exception as exc:
                # The action already executed: never replay it as a read retry.
                self.stats['automatic_observation_failures'] += 1
                observation = '观察失败；动作已经执行，不要重放动作。按需单独读取页面：' + bounded_text(str(exc), 1000)
            return result.model_copy(update={'content': (
                as_text(result.content) + '\n' + OBSERVATION_MARKER
                + '；动作成功不等于业务验证通过]\n' + observation
            )})

    async def awrap_model_call(self, request, handler):
        projected, removed = project_messages(request.messages, budget=self.context_chars)
        summary = HumanMessage(content=(
            '[平台当前草稿检查点，不是新的测试指令]\n'
            + as_text(self.checkpoint())
            + '\n早期工具轮次可能被省略；按此进度继续，勿重放已完成流程。'
            '需要准确代码时使用 read_script_draft，勿猜测被省略的内容。'
        ))
        messages = [*projected, summary]
        self.stats['context_requests'] += 1
        self.stats['context_source_chars'] += message_chars(request.messages)
        self.stats['context_sent_chars'] += message_chars(messages)
        self.stats['context_pruned_messages'] += removed
        return await handler(request.override(messages=messages))
