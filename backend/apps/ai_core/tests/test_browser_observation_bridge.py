"""Actual Node MCP -> adapter wire representation -> Python projection/guard.

Opt in with local PLAYWRIGHT_MCP_TEST_PACKAGE_ROOT and
PLAYWRIGHT_CHROME_EXECUTABLE_PATH; no download or external website is used.
Run with scripts/test_webui_generation_offline.py, not NAS-backed tests.
"""

import asyncio
from collections import Counter
import json
import os
from pathlib import Path
import shutil
import subprocess
import unittest
from uuid import uuid4

from django.conf import settings
from django.test import SimpleTestCase
from langchain.agents import create_agent
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from mcp.types import TextContent

from ai_core.mcp_exploration_runtime import ExplorationRuntimeMiddleware, project_messages
from ai_core.tests.test_mcp_agent_budget import ScriptedToolBatchModel
from ai_core.tests.test_mcp_exploration_runtime import AsyncToolProbe, async_tool
from ai_core.webui_playwright_agent import MCPBrowserToolGuard, MCPToolGuardError
from web_testing.exploration_diagnostics import extract_page_context
from web_testing.exploration_trace import _tool_failed


def adapter_text(envelope):
    # The installed mcp-use adapter stringifies Content objects, not the envelope.
    return str([TextContent(**item) for item in envelope['content'] if item['type'] == 'text'])


class CapturingFixtureModel(ScriptedToolBatchModel):
    """Deterministic local model stub, recording actual projected model input."""

    observed_messages: list = []

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        self.observed_messages.append(messages)
        return super()._generate(messages, stop=stop, run_manager=run_manager, **kwargs)


class BrowserObservationBridgeTests(SimpleTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        package_root = os.environ.get('PLAYWRIGHT_MCP_TEST_PACKAGE_ROOT')
        browser_path = os.environ.get('PLAYWRIGHT_CHROME_EXECUTABLE_PATH')
        if not package_root or not browser_path or not shutil.which('node'):
            raise unittest.SkipTest('Configure local MCP/Chromium paths to run real browser bridge tests')
        fixture = Path(settings.BASE_DIR) / 'scripts/tests/fixtures/browser_observation_wire.mjs'
        completed = subprocess.run(
            ['node', str(fixture)], capture_output=True, text=True, timeout=45, check=False,
        )
        if completed.returncode:
            raise RuntimeError('Local browser wire fixture failed: ' + completed.stderr[-4000:])
        line = next(line for line in completed.stdout.splitlines() if line.startswith('BROWSER_WIRE_FIXTURE:'))
        cls.envelopes = json.loads(line.split(':', 1)[1])
        cls.outputs = {key: adapter_text(value) for key, value in cls.envelopes.items()}

    def test_visible_surface_survives_real_wire_and_model_projection(self):
        context, _ = extract_page_context(self.outputs['html'])
        observation = context['observation']
        self.assertEqual(observation['version'], 1)
        names = [item['name'] for item in observation['elements']]
        self.assertIn('LateFieldAlpha', names)
        locked = next(item for item in observation['elements'] if item['name'] == 'LockedFieldBeta')
        self.assertIs(locked['enabled'], False)
        inherited = next(item for item in observation['elements'] if item['name'] == 'InheritedDisabledGamma')
        self.assertIs(inherited['enabled'], False)
        for name in ('InertField', 'AriaDisabledField'):
            self.assertIs(next(item for item in observation['elements'] if item['name'] == name)['enabled'], False)
        self.assertNotIn('HIDDEN_ANCESTOR_FIELD', json.dumps(observation))
        self.assertNotIn('HIDDEN_BUTTON_COPY', json.dumps(observation))

        messages = [
            HumanMessage(content='Inspect the current page'),
            AIMessage(content='', tool_calls=[{
                'id': 'read-one', 'type': 'tool_call', 'name': 'playwright_get_visible_html', 'args': {},
            }]),
            ToolMessage(content=self.outputs['html'], name='playwright_get_visible_html', tool_call_id='read-one'),
        ]
        original = messages[-1].content
        projected, _ = project_messages(messages)
        result = projected[-1].content
        self.assertIn('LateFieldAlpha', result)
        self.assertIn('LockedFieldBeta', result)
        self.assertNotIn('HIDDEN_ANCESTOR_TITLE', result)
        self.assertNotIn('PLATFORM_BROWSER_DIAGNOSTICS_V1:', result)
        self.assertLess(len(result), 12000)
        self.assertEqual(messages[-1].content, original)
        self.assertTrue(self.envelopes['hidden']['isError'])

    def test_scoped_read_changes_evidence_not_global_fingerprint(self):
        field = extract_page_context(self.outputs['field'])[0]['observation']
        full_page = extract_page_context(self.outputs['html'])[0]['observation']
        self.assertEqual(field['fingerprint'], full_page['fingerprint'])
        self.assertEqual(field['scope'], '#alpha')
        self.assertEqual([item['name'] for item in field['elements']], ['LateFieldAlpha'])
        self.assertIs(full_page['truncated'], True)
        self.assertIs(field['truncated'], False)
        self.assertIn('global_state_partial', field['notes'])
        self.assertNotIn('Navigation', json.dumps(field))

    def test_selector_literal_spacing_is_not_rewritten_by_display_formatting(self):
        self.assertFalse(self.envelopes['spaced']['isError'])
        observation = extract_page_context(self.outputs['spaced'])[0]['observation']
        self.assertEqual(observation['scope'], 'input[name="two  spaces"]')
        self.assertEqual([item['name'] for item in observation['elements']], ['ExactSelectorSpacing'])

    def test_unresolved_scope_is_explicit_and_retains_failure_screenshot(self):
        for key in ('hidden', 'ambiguous', 'missing'):
            with self.subTest(scope=key):
                self.assertTrue(self.envelopes[key]['isError'])
                context, _ = extract_page_context(self.outputs[key])
                self.assertIs(context['tool_failed'], True)
                self.assertEqual(context['observation']['elements'], [])
                self.assertEqual(context['screenshot_status'], 'captured')
                self.assertTrue(context['screenshot_file'].startswith('failure-'))

    def test_same_page_across_scopes_and_formats_does_not_reset_repeat_guard(self):
        fingerprints = [extract_page_context(self.outputs[key])[0]['observation']['fingerprint']
                        for key in ('html', 'text', 'scoped')]
        self.assertEqual(len(set(fingerprints)), 1)
        guard = MCPBrowserToolGuard(30)

        def observe(key, tool_name):
            run_id = uuid4()
            guard.on_tool_start({'name': tool_name}, '', run_id=run_id, inputs={})
            guard.on_tool_end(self.outputs[key], run_id=run_id, name=tool_name)

        observe('html', 'playwright_get_visible_html')
        initial_version = guard._page_state_version
        for key, tool in (('text', 'playwright_get_visible_text'), ('scoped', 'playwright_get_visible_html')):
            run_id = uuid4()
            guard.on_tool_start({'name': 'playwright_click'}, '', run_id=run_id, inputs={'selector': '#commit'})
            guard.on_tool_end('Clicked', run_id=run_id, name='playwright_click')
            observe(key, tool)
        self.assertEqual(guard._page_state_version, initial_version)
        with self.assertRaises(MCPToolGuardError):
            guard.on_tool_start({'name': 'playwright_click'}, '', run_id=uuid4(), inputs={'selector': '#commit'})

    def test_real_modal_close_advances_page_state(self):
        guard = MCPBrowserToolGuard(10)
        guard._record_observed_state('playwright_get_visible_html', self.outputs['html'], failed=False)
        before = guard._page_state_version
        guard._record_observed_state('playwright_get_visible_text', self.outputs['changed'], failed=False)
        self.assertGreater(guard._page_state_version, before)

    def test_real_wire_survives_automatic_observation_and_agent_model_projection(self):
        action, reader = 'playwright_click', 'playwright_get_visible_html'
        probe = AsyncToolProbe({action: 'Clicked', reader: self.outputs['html']})
        tools = [async_tool(name, probe) for name in (action, reader)]
        stats = Counter()
        middleware = ExplorationRuntimeMiddleware(
            tools, checkpoint=lambda: {'revision': 1}, failed=_tool_failed, stats=stats,
        )
        model = CapturingFixtureModel(tool_batches=[[action]])
        agent = create_agent(model=model, tools=tools, middleware=[middleware])
        guard = MCPBrowserToolGuard(10)
        asyncio.run(agent.ainvoke(
            {'messages': [HumanMessage(content='Inspect the current surface')]},
            config={'callbacks': [guard]},
        ))
        self.assertEqual(probe.peak_active, 1)
        self.assertEqual(stats['automatic_observations'], 1)
        self.assertEqual(stats['automatic_observation_failures'], 0)
        self.assertEqual(guard.get_stats()['total_tool_calls'], 2)
        model_input = '\n'.join(str(message.content) for message in model.observed_messages[-1])
        self.assertIn('LateFieldAlpha', model_input)
        self.assertIn('LockedFieldBeta', model_input)
        self.assertNotIn('HIDDEN_ANCESTOR_FIELD', model_input)
        self.assertNotIn('PLATFORM_BROWSER_DIAGNOSTICS_V1:', model_input)
