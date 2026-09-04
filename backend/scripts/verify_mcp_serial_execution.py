"""Real local MCP/Chromium regression; a scripted model makes no API requests.

Requires the cached pinned Node MCP package and its Chromium installation.
Run from backend: .venv/bin/python scripts/verify_mcp_serial_execution.py
Only loopback fixture pages are accessed. No deployment database is used.
"""

import argparse
import asyncio
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import sys
from threading import Thread
from uuid import uuid4


HTML = b'''<!doctype html><html><body>
<form><label>Account<input name="username"></label>
<label>Password<input name="password" type="password"></label>
<button type="submit">Sign in</button></form><p id="result">NOT_SUBMITTED</p>
<script>document.querySelector('form').onsubmit=e=>{
e.preventDefault(); const data=new FormData(e.target);
document.querySelector('#result').textContent=
data.get('username')==='fixture-user'&&data.get('password')==='fixture-password'
?'AUTH_OK':'AUTH_REJECTED';};</script></body></html>'''


class FixtureHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Content-Length', str(len(HTML)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(HTML)

    def log_message(self, *_args):
        pass


async def verify(url, rounds, backend, run_id):
    from langchain_core.callbacks import BaseCallbackHandler
    from langchain_core.language_models.chat_models import BaseChatModel
    from langchain_core.messages import AIMessage, ToolMessage
    from langchain_core.outputs import ChatGeneration, ChatResult
    from langchain_core.tools import tool
    from mcp_use import MCPClient
    from ai_core.mcp_agent_budget import BudgetedMCPAgent
    from web_testing.generation_preflight import prepare_playwright_mcp_output_config

    class ScriptedModel(BaseChatModel):
        calls: int = 0

        @property
        def _llm_type(self):
            return 'local-serial-regression'

        def bind_tools(self, tools, **kwargs):
            return self

        def _generate(self, messages, stop=None, run_manager=None, **kwargs):
            if self.calls:
                observed = next(m for m in reversed(messages) if isinstance(m, ToolMessage)
                                and m.name == 'playwright_get_visible_text')
                assert 'AUTH_OK' in str(observed.content) and 'AUTH_REJECTED' not in str(observed.content), observed.content
            self.calls += 1
            batch = [
                ('playwright_navigate', {'url': url, 'headless': True}),
                ('playwright_fill', {'selector': '[name=username]', 'value': 'fixture-user'}),
                ('playwright_fill', {'selector': '[name=password]', 'value': 'fixture-password'}),
                ('playwright_click', {'selector': 'button[type=submit]'}),
                ('playwright_get_visible_text', {}),
                ('aits_serial_checkpoint', {}),
            ]
            message = AIMessage(content='SERIAL_OK') if self.calls > rounds else AIMessage(
                content='', tool_calls=[
                    {'name': name, 'args': args, 'id': f'{self.calls}-{i}', 'type': 'tool_call'}
                    for i, (name, args) in enumerate(batch)
                ],
            )
            return ChatResult(generations=[ChatGeneration(message=message)])

        async def _agenerate(self, messages, stop=None, run_manager=None, **kwargs):
            return self._generate(messages, stop, run_manager, **kwargs)

    class Calls(BaseCallbackHandler):
        raise_error = True
        run_inline = True

        def __init__(self):
            self.active = set()
            self.peak = 0
            self.order = []

        def on_tool_start(self, serialized, input_str, *, run_id, **kwargs):
            self.active.add(run_id)
            self.peak = max(self.peak, len(self.active))
            self.order.append(serialized['name'])

        def on_tool_end(self, output, *, run_id, **kwargs):
            self.active.discard(run_id)

        def on_tool_error(self, error, *, run_id, **kwargs):
            self.active.discard(run_id)

    checkpoints = []

    @tool('aits_serial_checkpoint')
    async def checkpoint() -> str:
        """Record the local draft checkpoint after observing the login result."""
        checkpoints.append(len(checkpoints) + 1)
        return 'CHECKPOINT_OK'

    config = prepare_playwright_mcp_output_config({'mcpServers': {'playwright': {
        'command': 'npx',
        'args': ['--offline', '-y', '@executeautomation/playwright-mcp-server@1.0.12'],
        'env': {'PLAYWRIGHT_BROWSERS_PATH': os.environ.get('PLAYWRIGHT_BROWSERS_PATH', str(backend / '.playwright-browsers'))},
    }}}, run_id, base_dir=str(backend))
    client = MCPClient.from_dict(config)
    calls, model = Calls(), ScriptedModel()
    try:
        await client.create_all_sessions()
        agent = BudgetedMCPAgent(llm=model, client=client, max_steps=rounds + 3, callbacks=[calls])
        await agent.initialize()
        await agent.register_local_tools([checkpoint])
        result = await agent.run('Exercise one ordered batch per fixture login.', manage_connector=False)
        expected = ['playwright_navigate', 'playwright_fill', 'playwright_fill',
                    'playwright_click', 'playwright_get_visible_text', 'aits_serial_checkpoint'] * rounds
        assert result == 'SERIAL_OK', result
        assert calls.peak == 1 and not calls.active, (calls.peak, calls.active)
        assert calls.order == expected, calls.order
        assert len(checkpoints) == rounds
        return {'rounds_passed': rounds, 'model_calls': model.calls,
                'tool_calls': len(calls.order), 'peak_in_flight': calls.peak,
                'order_verified': True, 'checkpoints': len(checkpoints)}
    finally:
        await client.close_all_sessions()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--rounds', type=int, default=20, choices=range(1, 51))
    args = parser.parse_args()
    backend = Path(__file__).resolve().parent.parent
    sys.path[:0] = [str(backend), str(backend / 'apps')]
    os.environ['DJANGO_SETTINGS_MODULE'] = 'aits_backend.settings'
    os.environ['ANONYMIZED_TELEMETRY'] = 'false'
    os.environ['MCP_USE_ANONYMIZED_TELEMETRY'] = 'false'
    import django
    django.setup()
    run_id = str(uuid4())
    server = ThreadingHTTPServer(('127.0.0.1', 0), FixtureHandler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        result = asyncio.run(asyncio.wait_for(verify(
            f'http://127.0.0.1:{server.server_port}/', args.rounds, backend, run_id,
        ), timeout=90))
        print(json.dumps({**result, 'mcp_log': str(backend / 'logs' / 'playwright-mcp' / f'{run_id}.log')}, indent=2))
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


if __name__ == '__main__':
    main()
