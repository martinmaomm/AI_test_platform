"""Offline regressions for MCP exploration context and automatic observations."""

import asyncio
import base64
import json
from collections import Counter

from django.test import SimpleTestCase
from langchain.agents import create_agent
from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.tools import StructuredTool

from ai_core.mcp_exploration_runtime import (
    OBSERVATION_MARKER,
    ExplorationRuntimeMiddleware,
    message_chars,
    project_messages,
)
from ai_core.tests.test_mcp_agent_budget import ScriptedToolBatchModel
from ai_core.webui_playwright_agent import MCPBrowserToolGuard
from web_testing.exploration_trace import _tool_failed


def observation_wire(fingerprint='a' * 64, *, scope='page'):
    payload = {
        'page_url': 'https://fixture.example.test/#/catalog', 'page_title': 'Catalog',
        'tool_failed': False,
        'observation': {
            'version': 1, 'page_url': 'https://fixture.example.test/#/catalog',
            'page_title': 'Catalog', 'scope': scope, 'fingerprint': fingerprint,
            'settled': True, 'truncated': False, 'notes': ['read completed'],
            'elements': [{
                'tag': 'dialog', 'role': 'dialog', 'name': 'Editor', 'id': 'edit',
                'type': '', 'placeholder': '', 'visible': True, 'enabled': True,
                'container': 'page',
            }, {
                'tag': 'input', 'role': 'textbox', 'name': 'Late field', 'id': 'late',
                'type': 'text', 'placeholder': '', 'visible': True, 'enabled': True,
                'container': 'Editor',
            }],
            'text': ['Editor opened'],
        },
    }
    encoded = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip('=')
    return 'PLATFORM_BROWSER_DIAGNOSTICS_V1:' + encoded


def invalid_observation_wire():
    payload = {
        'tool_failed': False,
        'observation': {
            'version': 2, 'page_url': 'https://fixture.example.test/', 'page_title': 'Catalog',
            'scope': 'page', 'fingerprint': 'invalid', 'settled': True, 'truncated': False,
            'notes': [], 'elements': [], 'text': [],
        },
    }
    encoded = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip('=')
    return 'PLATFORM_BROWSER_DIAGNOSTICS_V1:' + encoded


class AsyncToolProbe:
    """In-memory tool recorder that detects concurrent executions."""

    def __init__(self, outputs):
        self.outputs = outputs
        self.events = []
        self.active = 0
        self.peak_active = 0

    async def run(self, name):
        self.events.append(("start", name))
        self.active += 1
        self.peak_active = max(self.peak_active, self.active)
        try:
            await asyncio.sleep(0.005)
            self.events.append(("end", name))
            return self.outputs[name]
        finally:
            self.active -= 1


def async_tool(name, probe):
    async def invoke():
        """Run one asynchronous in-memory fixture tool."""
        return await probe.run(name)

    return StructuredTool.from_function(
        coroutine=invoke,
        name=name,
        description="Offline asynchronous fixture tool.",
    )


class ToolCallbackCounter(BaseCallbackHandler):
    """Count tool starts emitted by the actual LangChain tool handler."""

    raise_error = True
    run_inline = True

    def __init__(self):
        self.starts = Counter()

    def on_tool_start(self, serialized, input_str, **kwargs):
        name = str((serialized or {}).get("name") or "")
        self.starts[name] += 1


def tool_group(index, content, *, name="fixture_write"):
    call_id = f"round-{index}"
    return [
        AIMessage(content="", tool_calls=[{
            "name": name,
            "args": {},
            "id": call_id,
            "type": "tool_call",
        }]),
        ToolMessage(content=content, tool_call_id=call_id, name=name),
    ]


class ProjectMessagesTests(SimpleTestCase):
    def test_keeps_users_latest_page_and_recent_complete_groups_without_orphans(self):
        latest_page = '<main data-k="p2">' + ("y" * 120) + "</main>"
        messages = [HumanMessage(content="u-1"), *tool_group(1, "x" * 1200)]
        messages.extend([HumanMessage(content="u-2")])
        messages.extend(tool_group(2, latest_page, name="playwright_get_visible_html"))
        messages.extend(tool_group(3, "z" * 80))
        messages.extend(tool_group(4, "q" * 80))
        original_dumps = [message.model_dump() for message in messages]

        projected, removed = project_messages(messages, budget=300, page_limit=300)

        self.assertEqual([message.model_dump() for message in messages], original_dumps)
        self.assertGreater(removed, 0)
        self.assertLess(message_chars(projected), message_chars(messages))
        self.assertEqual(
            [message.content for message in projected if isinstance(message, HumanMessage)],
            ["u-1", "u-2"],
        )
        self.assertTrue(any(
            isinstance(message, ToolMessage) and latest_page in message.content
            for message in projected
        ))
        retained_call_ids = {
            call["id"]
            for message in projected
            if isinstance(message, AIMessage)
            for call in message.tool_calls
        }
        self.assertTrue(all(
            message.tool_call_id in retained_call_ids
            for message in projected
            if isinstance(message, ToolMessage)
        ))
        self.assertTrue({"round-3", "round-4"}.issubset(retained_call_ids))

    def test_structured_observation_precedes_huge_legacy_prefix_without_raw_metadata(self):
        source = ToolMessage(
            content=('legacy html ' + ('x' * 12000) + '\n' + observation_wire()),
            tool_call_id='read-1', name='playwright_get_visible_html',
        )
        original = source.model_dump()
        projected, _ = project_messages([source], page_limit=1200)
        content = projected[0].content
        self.assertEqual(source.model_dump(), original)
        self.assertIn('[平台页面观察]', content)
        self.assertIn('Late field', content)
        self.assertLess(content.index('<dialog>'), content.index('<input>'))
        self.assertNotIn('PLATFORM_BROWSER_DIAGNOSTICS', content)
        self.assertNotIn('x' * 100, content)

    def test_invalid_declared_observation_is_not_projected_as_page_evidence(self):
        source = ToolMessage(
            content='Error executing tool\n' + ('legacy html ' * 2000) + invalid_observation_wire(),
            tool_call_id='read-invalid', name='playwright_get_visible_html',
        )
        projected, _ = project_messages([source], page_limit=1200)
        self.assertIn('观察不可用', projected[0].content)
        self.assertNotIn('legacy html', projected[0].content)
        self.assertNotIn('PLATFORM_BROWSER_DIAGNOSTICS', projected[0].content)


class AutomaticObservationGraphTests(SimpleTestCase):
    def make_agent(self, tool_names, outputs, *, batches):
        probe = AsyncToolProbe(outputs)
        stats = Counter()
        middleware = ExplorationRuntimeMiddleware(
            [async_tool(name, probe) for name in tool_names],
            checkpoint=lambda: {"revision": 2},
            failed=_tool_failed,
            stats=stats,
        )
        agent = create_agent(
            model=ScriptedToolBatchModel(tool_batches=batches),
            tools=[async_tool(name, probe) for name in tool_names],
            middleware=[middleware],
        )
        return agent, probe, stats

    def run_agent(self, agent, *callbacks):
        return asyncio.run(agent.ainvoke(
            {"messages": [HumanMessage(content="phase-a")]},
            config={"callbacks": list(callbacks)},
        ))

    def action_message(self, result, name):
        return next(
            message for message in result["messages"]
            if isinstance(message, ToolMessage) and message.name == name
        )

    def test_automatic_observation_uses_the_real_handler_and_is_serial(self):
        action = "playwright_navigate"
        reader = "playwright_get_visible_text"
        later_action = "playwright_fill"
        agent, probe, stats = self.make_agent(
            [action, reader, later_action],
            {
                action: "action-ok",
                reader: '<main data-k="r1">read-ok</main>',
                later_action: "later-ok",
            },
            batches=[[action, later_action]],
        )
        callback = ToolCallbackCounter()

        result = self.run_agent(agent, callback)

        self.assertEqual(probe.events, [
            ("start", action), ("end", action),
            ("start", reader), ("end", reader),
            ("start", later_action), ("end", later_action),
        ])
        self.assertEqual(probe.peak_active, 1)
        self.assertEqual(callback.starts, Counter({action: 1, reader: 1, later_action: 1}))
        self.assertEqual(stats["automatic_observations"], 1)
        self.assertEqual(stats["automatic_observation_failures"], 0)
        self.assertIn(OBSERVATION_MARKER, self.action_message(result, action).content)

    def test_automatic_observation_retains_wire_tail_until_model_projection(self):
        action = 'playwright_navigate'
        reader = 'playwright_get_visible_text'
        agent, probe, stats = self.make_agent(
            [action, reader],
            {action: 'action-ok', reader: ('legacy ' + ('x' * 12000) + '\n' + observation_wire())},
            batches=[[action]],
        )
        callback = ToolCallbackCounter()
        result = self.run_agent(agent, callback)
        action_message = self.action_message(result, action)
        projected, _ = project_messages(result['messages'], page_limit=1200)
        projected_action = next(item for item in projected if isinstance(item, ToolMessage) and item.name == action)

        self.assertEqual(probe.events, [('start', action), ('end', action), ('start', reader), ('end', reader)])
        self.assertEqual(callback.starts, Counter({action: 1, reader: 1}))
        self.assertEqual(stats['automatic_observations'], 1)
        self.assertIn('PLATFORM_BROWSER_DIAGNOSTICS', action_message.content)
        self.assertIn('[平台页面观察]', projected_action.content)
        self.assertNotIn('PLATFORM_BROWSER_DIAGNOSTICS', projected_action.content)

    def test_explicit_following_read_skips_automatic_observation(self):
        action = "playwright_navigate"
        reader = "playwright_get_visible_text"
        agent, probe, stats = self.make_agent(
            [action, reader],
            {action: "action-ok", reader: '<main data-k="r4">read-ok</main>'},
            batches=[[action, reader]],
        )
        callback = ToolCallbackCounter()

        result = self.run_agent(agent, callback)

        self.assertEqual(probe.events, [
            ("start", action), ("end", action),
            ("start", reader), ("end", reader),
        ])
        self.assertEqual(callback.starts, Counter({action: 1, reader: 1}))
        self.assertEqual(stats["automatic_observations"], 0)
        self.assertEqual(stats["automatic_observations_skipped"], 1)
        self.assertNotIn(OBSERVATION_MARKER, self.action_message(result, action).content)

    def test_fill_does_not_trigger_an_automatic_observation(self):
        action = "playwright_fill"
        reader = "playwright_get_visible_text"
        agent, probe, stats = self.make_agent(
            [action, reader],
            {action: "action-ok", reader: '<main data-k="r2">read-ok</main>'},
            batches=[[action]],
        )
        callback = ToolCallbackCounter()

        result = self.run_agent(agent, callback)

        self.assertEqual(probe.events, [("start", action), ("end", action)])
        self.assertEqual(callback.starts, Counter({action: 1}))
        self.assertEqual(stats["automatic_observations"], 0)
        self.assertNotIn(OBSERVATION_MARKER, self.action_message(result, action).content)

    def test_failed_action_does_not_trigger_an_automatic_observation(self):
        action = "playwright_navigate"
        reader = "playwright_get_visible_text"
        agent, probe, stats = self.make_agent(
            [action, reader],
            {action: "Error executing tool: fixture", reader: '<main data-k="r3">read-ok</main>'},
            batches=[[action]],
        )
        callback = ToolCallbackCounter()

        self.run_agent(agent, callback)

        self.assertEqual(probe.events, [("start", action), ("end", action)])
        self.assertEqual(callback.starts, Counter({action: 1}))
        self.assertEqual(stats["automatic_observations"], 0)

    def test_failed_observation_does_not_replay_the_completed_action(self):
        action = "playwright_navigate"
        reader = "playwright_get_visible_text"
        agent, probe, stats = self.make_agent(
            [action, reader],
            {action: "action-ok", reader: "Error executing tool: fixture"},
            batches=[[action]],
        )
        callback = ToolCallbackCounter()

        result = self.run_agent(agent, callback)

        self.assertEqual(probe.events, [
            ("start", action), ("end", action),
            ("start", reader), ("end", reader),
        ])
        self.assertEqual(callback.starts, Counter({action: 1, reader: 1}))
        self.assertEqual(stats["automatic_observations"], 1)
        self.assertEqual(stats["automatic_observation_failures"], 1)
        self.assertIn("不要重放动作", self.action_message(result, action).content)

    def test_raw_mapping_tool_call_schema_selects_a_no_argument_reader(self):
        async def read():
            """Return in-memory page evidence without arguments."""
            return '<main data-k="r6">read-ok</main>'

        reader = StructuredTool.from_function(
            coroutine=read,
            name="playwright_get_visible_text",
            description="Offline no-argument reader.",
            args_schema={"type": "object", "properties": {}},
        )

        middleware = ExplorationRuntimeMiddleware(
            [reader],
            checkpoint=lambda: {"revision": 2},
            failed=_tool_failed,
            stats=Counter(),
        )

        self.assertIsInstance(reader.tool_call_schema, dict)
        self.assertIs(middleware.reader, reader)

    def test_automatic_read_uses_the_browser_guard_callback_limit(self):
        action = "playwright_navigate"
        reader = "playwright_get_visible_text"
        agent, probe, stats = self.make_agent(
            [action, reader],
            {action: "action-ok", reader: '<main data-k="r5">read-ok</main>'},
            batches=[[action]],
        )
        callback = ToolCallbackCounter()
        guard = MCPBrowserToolGuard(max_tool_calls=1)

        result = self.run_agent(agent, callback, guard)

        self.assertEqual(probe.events, [("start", action), ("end", action)])
        self.assertEqual(callback.starts, Counter({action: 1, reader: 1}))
        self.assertEqual(guard.get_stats()["total_tool_calls"], 1)
        self.assertEqual(guard.get_stats()["blocked_tool_calls"], 1)
        self.assertEqual(stats["automatic_observations"], 1)
        self.assertEqual(stats["automatic_observation_failures"], 1)
        self.assertIn("不要重放动作", self.action_message(result, action).content)
