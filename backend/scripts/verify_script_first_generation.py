"""Opt-in real-model/MCP/Python smoke test on isolated localhost fixtures.

Uses existing model and MCP configuration read-only. Does not create platform
records, restart services, or access the user's business website. Artifacts are
kept under backend/temp/script-first-smoke. Requires explicit --live.

    python scripts/verify_script_first_generation.py --live --model-id 4 --user-id 1
"""

from __future__ import annotations

import argparse
import asyncio
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import sys
import threading
import uuid


PEOPLE_HTML = '''<!doctype html><html lang="en"><head><meta charset="utf-8"><title>Team desk</title></head>
<body><main><h1>Team desk</h1>
<form id="login"><h2>Sign in</h2><label>Username <input name="username" autocomplete="username"></label>
<label>Password <input type="password" name="password" autocomplete="current-password"></label><button>Log in</button><p id="login-error" role="alert"></p></form>
<section id="people" hidden><h2>People</h2><button id="new-record">New record</button>
<label>Search account <input id="query"></label><button id="search">Search</button><button id="reset">Reset</button>
<p role="status" id="notice"></p><table><thead><tr><th>Account</th><th>Display name</th><th>Actions</th></tr></thead><tbody id="rows"></tbody></table>
<p id="empty">No records</p>
<dialog id="editor"><form id="record-form"><h3 id="editor-title">New record</h3>
<label>Account <input name="account" required></label><label>Display name <input name="display_name" required></label>
<button type="submit">Save</button><button type="button" id="cancel">Cancel</button></form></dialog></section></main>
<script>
const login=document.querySelector('#login'), people=document.querySelector('#people'), editor=document.querySelector('#editor');
const form=document.querySelector('#record-form'), rows=document.querySelector('#rows'), query=document.querySelector('#query'), notice=document.querySelector('#notice');
let records=[], selected=null;
login.onsubmit=e=>{e.preventDefault(); const v=new FormData(login); if(v.get('username')==='fixture-user'&&v.get('password')==='fixture-pass'){login.hidden=true;people.hidden=false;}else{document.querySelector('#login-error').textContent='Invalid credentials';}};
function render(){rows.replaceChildren(); const matched=records.filter(r=>r.account.includes(query.value)); document.querySelector('#empty').hidden=matched.length!==0;
for(const record of matched){const tr=document.createElement('tr'); for(const field of ['account','display_name']){const td=document.createElement('td');td.textContent=record[field];tr.append(td);}
const td=document.createElement('td');const edit=document.createElement('button');edit.textContent='Edit';edit.onclick=()=>{selected=record;form.elements.account.value=record.account;form.elements.display_name.value=record.display_name;document.querySelector('#editor-title').textContent='Edit record';editor.showModal();};
const remove=document.createElement('button');remove.textContent='Remove';remove.onclick=()=>{records=records.filter(r=>r!==record);notice.textContent='Record removed';render();};td.append(edit,remove);tr.append(td);rows.append(tr);}}
document.querySelector('#new-record').onclick=()=>{selected=null;form.reset();document.querySelector('#editor-title').textContent='New record';editor.showModal();};
form.onsubmit=e=>{e.preventDefault();const data=Object.fromEntries(new FormData(form));if(selected){Object.assign(selected,data);notice.textContent='Record updated';}else{records.push(data);notice.textContent='Record created';}editor.close();render();};
document.querySelector('#cancel').onclick=()=>editor.close();document.querySelector('#search').onclick=render;document.querySelector('#reset').onclick=()=>{query.value='';render();};
</script></body></html>'''

PREFERENCES_HTML = '''<!doctype html><html lang="en"><head><meta charset="utf-8"><title>Reading preferences</title></head>
<body><main><h1>Reading preferences</h1><form id="preferences">
<label>Display language <select name="language"><option value="en">English</option><option value="fr">French</option></select></label>
<label><input name="updates" type="checkbox">Receive updates</label><button>Apply settings</button></form>
<section aria-label="Saved preferences"><h2>Current settings</h2><p id="language">English</p><p id="updates">Updates off</p><p role="status" id="status"></p></section></main>
<script>document.querySelector('#preferences').onsubmit=e=>{e.preventDefault();const form=e.target;document.querySelector('#language').textContent=form.elements.language.selectedOptions[0].text;document.querySelector('#updates').textContent=form.elements.updates.checked?'Updates on':'Updates off';document.querySelector('#status').textContent='Preferences saved';};</script></body></html>'''


class FixtureHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        document = PREFERENCES_HTML if self.path.split('?')[0] == '/preferences' else PEOPLE_HTML
        content = document.encode('utf-8')
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Content-Length', str(len(content)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(content)

    def log_message(self, *_args):
        pass


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live', action='store_true')
    parser.add_argument('--model-id', type=int, required=True)
    parser.add_argument('--user-id', type=int, required=True)
    parser.add_argument('--scenario', choices=['people', 'preferences', 'all'], default='all')
    parser.add_argument('--timeout', type=int, default=480)
    args = parser.parse_args()
    if not args.live:
        parser.error('--live is required: this makes billable model requests')

    backend = Path(__file__).resolve().parent.parent
    sys.path[:0] = [str(backend), str(backend / 'apps')]
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'aits_backend.settings')
    os.environ['MCP_USE_ANONYMIZED_TELEMETRY'] = 'false'
    import django
    django.setup()
    from ai_core.model_manager import get_llm_manager
    from web_testing.assertion_state import evaluation_status
    from web_testing.draft_quality import evaluate_draft
    from web_testing.generation_brief import build_generation_brief
    from web_testing.generation_preflight import resolve_active_playwright_mcp_config
    from web_testing.playwright_python_runner import ExecutionConfig, PlaywrightRunner
    from web_testing.script_exploration_agent import ScriptExplorationAgent

    selection = resolve_active_playwright_mcp_config(args.user_id)
    if not selection:
        raise RuntimeError('No active Playwright MCP configuration for this user')
    manager = get_llm_manager(config_id=args.model_id)
    server = ThreadingHTTPServer(('127.0.0.1', 0), FixtureHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f'http://127.0.0.1:{server.server_port}'
    output_dir = backend / 'temp' / 'script-first-smoke' / str(uuid.uuid4())
    output_dir.mkdir(parents=True)
    print(f'SMOKE_ARTIFACT_DIR={output_dir}', flush=True)
    scenarios = {
        'people': ('/', '登录后，在 People 页面新增一条人员记录，Account 和 Display name 用 time.time_ns() 生成唯一值；搜索确认新增记录存在；编辑该记录的 Display name 并验证更新；删除本轮新增的记录，搜索并验证不存在。只操作本轮数据，生成可重复执行的 Python 脚本，添加中文步骤注释。', {'username': 'fixture-user', 'password': 'fixture-pass'}),
        'preferences': ('/preferences', '把阅读偏好的显示语言改成 French，启用 Receive updates，保存后验证 Current settings 中的语言和更新状态。生成可重复执行的 Python 脚本，添加中文步骤注释。', None),
    }
    outcomes = []
    try:
        for name, (path, goal, credentials) in scenarios.items():
            if args.scenario not in {'all', name}:
                continue
            directory = output_dir / name
            directory.mkdir()
            checkpoint_count = 0

            def checkpoint(payload):
                nonlocal checkpoint_count
                checkpoint_count += 1
                script = str(payload.get('script_draft') or '')
                (directory / 'checkpoint.py').write_text(script, encoding='utf-8')
                (directory / 'checkpoint.json').write_text(json.dumps(payload.get('snapshot') or {}, ensure_ascii=False, indent=2), encoding='utf-8')
                stats = (payload.get('snapshot') or {}).get('tool_stats') or {}
                print(f'CHECKPOINT {name} #{checkpoint_count} chars={len(script)} tools={stats.get("total_tool_calls", 0)}', flush=True)

            agent = ScriptExplorationAgent(
                llm_model=manager.current_llm, mcp_config=selection[1],
                generation_id=str(uuid.uuid4()), cancel_check=lambda: False,
                exploration_timeout_seconds=args.timeout, checkpoint_callback=checkpoint,
            )
            description = f'目标网址：{base_url + path}\n{goal}'
            if credentials:
                description += f'\n测试登录账号 {credentials["username"]}，密码 {credentials["password"]}。'
            result = asyncio.run(agent.generate(
                brief=build_generation_brief(description, title=name).model_dump(),
                target_url=base_url + path,
            ))
            (directory / 'generated.py').write_text(result.script_draft, encoding='utf-8')
            (directory / 'snapshot.json').write_text(json.dumps(result.snapshot, ensure_ascii=False, indent=2), encoding='utf-8')
            quality = evaluate_draft(result.script_draft, target_url=base_url + path, snapshot=result.snapshot)
            (directory / 'quality.json').write_text(json.dumps(quality, ensure_ascii=False, indent=2), encoding='utf-8')
            outcome = {'scenario': name, 'error_code': result.error_code, 'completion': result.completion, 'checkpoints': checkpoint_count, 'script_chars': len(result.script_draft), 'stats': result.snapshot.get('tool_stats'), 'blockers': quality['blockers'], 'runs': []}
            if result.script_draft and not quality['blockers']:
                for run in range(2):
                    config = ExecutionConfig(timeout=60, generate_allure=False, failure_screenshot_path=str(directory / f'failure-{run + 1}.png'), environment_variables={'UI_TEST_USERNAME': 'fixture-user', 'UI_TEST_PASSWORD': 'fixture-pass'})
                    execution = PlaywrightRunner().run_single_test(f'smoke-{name}-{run}', result.script_draft, config)
                    status, _, count = evaluation_status(result.script_draft, operation_success=execution.success, runtime_assertion_count=execution.runtime_assertion_count)
                    outcome['runs'].append({'status': status, 'runtime_assertion_count': count})
                    (directory / f'run-{run + 1}.log').write_text(execution.stdout + '\n' + execution.stderr, encoding='utf-8')
                    print(f'REPLAY {name} #{run + 1}: {status}, assertions={count}', flush=True)
            outcomes.append(outcome)
            (output_dir / 'summary.json').write_text(json.dumps(outcomes, ensure_ascii=False, indent=2), encoding='utf-8')
            print(json.dumps(outcome, ensure_ascii=False), flush=True)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
    return 0 if outcomes and all(not item['error_code'] and len(item['runs']) == 2 and all(run['status'] == 'passed' for run in item['runs']) for item in outcomes) else 1


if __name__ == '__main__':
    raise SystemExit(main())
