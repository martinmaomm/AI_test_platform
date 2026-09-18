"""Opt-in real model/MCP discovery of a disposable localhost website.

Reads the selected platform model/MCP configuration, never writes business DB.
No pressure, NAS, real website or real test-account credentials are involved.
The selected provider receives a small synthetic exploration prompt.
"""
from __future__ import annotations

import argparse
import asyncio
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import logging
import os
from pathlib import Path
import sys
import tempfile
import threading
import uuid
from urllib.parse import urlsplit

BACKEND = Path(__file__).resolve().parents[1]
HTML = b'''<!doctype html><html lang="zh"><meta charset="utf-8"><title>Local test fixture</title>
<h1>Local fixture</h1><section id="login"><label>Account <input id="account"></label>
<label>Password <input id="password" type="password"></label><button onclick="login()">Login</button>
<p id="error"></p></section><section id="main" hidden><h2>Items</h2>
<label>Keyword <input id="keyword"></label><button onclick="search()">Search</button>
<table><thead><tr><th>ID</th><th>Name</th></tr></thead><tbody id="rows"></tbody></table></section>
<script>
let token='';
async function login(){const r=await fetch('/auth/session',{method:'POST',headers:{'Content-Type':'application/json'},
body:JSON.stringify({account:document.getElementById('account').value,password:document.getElementById('password').value})});
const v=await r.json();if(!r.ok){document.getElementById('error').textContent='Login failed';return;}
token=v.data.token;document.getElementById('login').hidden=true;document.getElementById('main').hidden=false;await search();}
async function search(){const r=await fetch('/items?keyword='+encodeURIComponent(document.getElementById('keyword').value),
{headers:{'Authorization':'Bearer '+token}});const v=await r.json();document.getElementById('rows').replaceChildren();
for(const item of v.data||[]){const tr=document.createElement('tr');for(const val of [item.id,item.name]){
const td=document.createElement('td');td.textContent=val;tr.appendChild(td);}document.getElementById('rows').appendChild(tr);}}
</script></html>'''


class Fixture(BaseHTTPRequestHandler):
    def send(self, status, content, content_type='application/json'):
        if isinstance(content, dict):
            content = json.dumps(content).encode()
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def do_GET(self):
        path = urlsplit(self.path).path
        if path == '/':
            return self.send(200, HTML, 'text/html; charset=utf-8')
        if path != '/items':
            return self.send(404, {'message': 'not found'})
        self.server.observed.append({'method': 'GET', 'path': path})
        if self.headers.get('Authorization') != 'Bearer ' + self.server.token:
            return self.send(401, {'code': 401, 'data': []})
        return self.send(200, {'code': 200, 'data': [{'id': 1, 'name': 'fixture-item'}]})

    def do_POST(self):
        if self.path != '/auth/session':
            return self.send(404, {'message': 'not found'})
        self.server.observed.append({'method': 'POST', 'path': self.path})
        size = min(int(self.headers.get('Content-Length', '0')), 4096)
        body = json.loads(self.rfile.read(size))
        if body != {'account': 'fixture-user', 'password': 'fixture-password'}:
            return self.send(401, {'code': 401})
        return self.send(200, {'code': 200, 'data': {'token': self.server.token}})

    def log_message(self, *_):
        pass


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--confirm-local-exploration', action='store_true')
    parser.add_argument('--model-id', type=int, required=True)
    options = parser.parse_args()
    if not options.confirm_local_exploration:
        parser.error('--confirm-local-exploration is required')
    sys.path[:0] = [str(BACKEND), str(BACKEND / 'apps')]
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
    os.environ['MCP_USE_ANONYMIZED_TELEMETRY'] = 'false'
    import django
    django.setup()
    from ai_core.config_access import usable_llm_configurations
    from ai_core.model_manager import get_llm_manager
    from api_testing.browser_discovery import resolve_browser_discovery_mcp_config
    from api_testing.browser_discovery_agent import run_browser_discovery
    model = usable_llm_configurations().get(pk=options.model_id)
    configuration = resolve_browser_discovery_mcp_config(model.created_by_id)
    llm = get_llm_manager(model.pk).current_llm
    if llm is None:
        raise RuntimeError('所选模型不可用')
    logging.getLogger('mcp_use').setLevel(logging.WARNING)
    logging.getLogger('httpx').setLevel(logging.WARNING)
    root = BACKEND / 'temp' / 'performance-discovery-acceptance'
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    output = Path(tempfile.mkdtemp(prefix='live-', dir=root))
    server = ThreadingHTTPServer(('127.0.0.1', 0), Fixture)
    server.token = 'fixture-' + uuid.uuid4().hex
    server.observed = []
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    origin = f'http://127.0.0.1:{server.server_port}'
    last_count = [-1]

    def checkpoint(payload):
        count = payload.get('tool_calls', 0)
        if count != last_count[0]:
            print(json.dumps({'tool_calls': count, 'phase': payload.get('phase'),
                              'elapsed_seconds': payload.get('elapsed_seconds')}, ensure_ascii=False), flush=True)
            last_count[0] = count
        return True

    try:
        result = asyncio.run(run_browser_discovery(
            llm_model=llm, mcp_config=configuration, task_id=str(uuid.uuid4()), target_url=origin + '/',
            description='只探索这个本机临时夹具。用 Account fixture-user、Password fixture-password 登录，'
                        '进入 Items 列表，在 Keyword 填入 fixture-item 后点击 Search，确认列表有结果后结束。不要访问其他站点。',
            api_origin=origin, trace_file=str(output / 'network.jsonl'),
            timeout_seconds=300, max_steps=20, max_tool_calls=16,
            capture_limits={'max_requests': 30, 'max_body_bytes': 8192, 'max_total_body_bytes': 65536},
            checkpoint=checkpoint,
        ))
        trace = output / 'network.jsonl'
        lines = trace.read_text().splitlines() if trace.exists() else []
        observations = {(item['method'], item['path']) for item in server.observed}
        evidence_text = '\n'.join(lines)
        passed = bool(result.get('completed') and ('POST', '/auth/session') in observations
                      and ('GET', '/items') in observations and '/auth/session' in evidence_text and '/items' in evidence_text)
        print(json.dumps({'passed': passed, 'model_id': model.pk, 'completed': result.get('completed'),
                          'error_code': result.get('error_code'), 'observed_requests': server.observed,
                          'evidence_lines': len(lines), 'trace_path': str(trace),
                          'scope': 'real model + MCP; localhost synthetic site; no load or business DB writes'}, ensure_ascii=False), flush=True)
        return 0 if passed else 1
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


if __name__ == '__main__':
    raise SystemExit(main())
