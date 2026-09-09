"""Disposable local website used to verify browser-to-requests independence.

No Swagger is supplied. Tokens and item IDs change for every login/create.
The server retains an operation ledger so tests can reject hidden replays.
"""
from __future__ import annotations

from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading
import time
from urllib.parse import parse_qs, urlsplit
import uuid


HTML = r'''<!doctype html><html lang="zh"><meta charset="utf-8"><title>接口探索验收</title>
<style>body{font-family:system-ui;margin:35px;max-width:900px}input,button{padding:10px;margin:8px}table{width:100%;border-collapse:collapse}td{padding:12px;border-bottom:1px solid #ddd}</style>
<h1>测试物品管理</h1><section id="login-form"><label>账号<input id="username"></label>
<label>密码<input id="password" type="password"></label><button id="login">登录</button></section>
<section id="items-panel" hidden><label>物品名称<input id="name"></label><button id="add">新增物品</button>
<label>查询名称<input id="query"></label><button id="search">查询</button><table><thead><tr><th>名称</th><th>操作</th></tr></thead><tbody id="items"></tbody></table>
<dialog id="edit-dialog"><label>新名称<input id="edit-name"></label><button id="save-edit">保存修改</button></dialog></section>
<p id="notice" role="status"></p><script>
let token='', currentId=null;
const el=id=>document.getElementById(id);
async function api(path,method='GET',body) {
 const r=await fetch('/api'+path,{method,headers:{'Content-Type':'application/json',...(token?{'Authorization':'Bearer '+token}:{})},...(body?{body:JSON.stringify(body)}:{})});
 const data=await r.json(); if(data.code!==200) throw new Error(data.message); return data.data;
}
async function load() {
 const result=await api('/items?name='+encodeURIComponent(el('query').value));
 el('items').replaceChildren();
 for(const item of result.items) {
  const row=document.createElement('tr');row.dataset.id=item.id;
  const cell=document.createElement('td');cell.textContent=item.name;row.append(cell);
  const actions=document.createElement('td');
  const edit=document.createElement('button');edit.textContent='编辑';edit.onclick=()=>{currentId=item.id;el('edit-name').value=item.name;el('edit-dialog').showModal()};actions.append(edit);
  const del=document.createElement('button');del.textContent='删除';del.onclick=async()=>{await api('/items/'+item.id,'DELETE');await load();el('notice').textContent='删除成功'};actions.append(del);row.append(actions);el('items').append(row);
 }
 if(!result.items.length)el('items').innerHTML='<tr><td>暂无数据</td></tr>';
}
el('login').onclick=async()=>{try{const d=await api('/login','POST',{username:el('username').value,password:el('password').value});token=d.token;el('login-form').hidden=true;el('items-panel').hidden=false;el('notice').textContent='登录成功';await load()}catch(e){el('notice').textContent=e.message}};
el('add').onclick=async()=>{await api('/items','POST',{name:el('name').value});await load();el('notice').textContent='新增成功'};
el('save-edit').onclick=async()=>{await api('/items/'+currentId,'PATCH',{name:el('edit-name').value});el('edit-dialog').close();await load();el('notice').textContent='修改成功'};
el('search').onclick=load;
</script></html>'''


class Fixture:
    def __init__(self):
        self.items = {}
        self.tokens = set()
        self.ledger = []
        self.serial = int(time.time() * 1000)
        self.lock = threading.Lock()


def handler_for(fixture):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def handle_request(self):
            parsed = urlsplit(self.path)
            raw = self.rfile.read(int(self.headers.get('Content-Length', 0)))
            try:
                body = json.loads(raw) if raw else {}
            except ValueError:
                return self.send_json(400, {'code': 400, 'message': 'invalid JSON'})
            if parsed.path in {'/', '/app'}:
                html = HTML.replace("fetch('/api'+path", "fetch(" + json.dumps(fixture.api_origin + '/api') + "+path")
                self.send_response(200)
                self.send_header('Content-Type', 'text/html; charset=utf-8')
                self.send_header('Content-Length', str(len(html.encode())))
                self.end_headers()
                self.wfile.write(html.encode())
                return
            if not parsed.path.startswith('/api/'):
                return self.send_json(404, {'code': 404})
            with fixture.lock:
                fixture.ledger.append({'method': self.command, 'path': parsed.path, 'name': body.get('name'),
                                       'query': parse_qs(parsed.query, keep_blank_values=True)})
                if parsed.path == '/api/login' and self.command == 'POST':
                    if body != {'username': 'fixture-user', 'password': 'fixture-password'}:
                        return self.send_json(200, {'code': 401, 'message': '登录信息不正确'})
                    token = uuid.uuid4().hex
                    fixture.tokens.add(token)
                    return self.send_json(200, {'code': 200, 'data': {'token': token}}, cookie=f'fixture_session={token}; HttpOnly; Path=/')
                authorization = self.headers.get('Authorization', '')
                cookie = self.headers.get('Cookie', '')
                if authorization.removeprefix('Bearer ') not in fixture.tokens and not any(f'fixture_session={token}' in cookie for token in fixture.tokens):
                    return self.send_json(200, {'code': 401, 'message': '尚未登录'})
                if parsed.path == '/api/items':
                    if self.command == 'GET':
                        name = parse_qs(parsed.query, keep_blank_values=True).get('name', [''])[0]
                        items = [value for value in fixture.items.values() if not name or value['name'] == name]
                        return self.send_json(200, {'code': 200, 'data': {'items': items, 'total': len(items)}})
                    if self.command == 'POST':
                        name = body.get('name')
                        if not isinstance(name, str) or any(value['name'] == name for value in fixture.items.values()):
                            return self.send_json(200, {'code': 409, 'message': '名称无效或重复'})
                        fixture.serial += 1
                        item = {'id': fixture.serial, 'name': name}
                        fixture.items[item['id']] = item
                        return self.send_json(200, {'code': 200, 'data': item})
                if parsed.path.startswith('/api/items/'):
                    try:
                        item_id = int(parsed.path.rsplit('/', 1)[-1])
                        item = fixture.items[item_id]
                    except (ValueError, KeyError):
                        return self.send_json(200, {'code': 404, 'message': '记录不存在'})
                    if self.command == 'PATCH':
                        item['name'] = body['name']
                        return self.send_json(200, {'code': 200, 'data': item})
                    if self.command == 'DELETE':
                        del fixture.items[item_id]
                        return self.send_json(200, {'code': 200, 'data': None})
                return self.send_json(404, {'code': 404})

        def send_json(self, status, value, *, cookie=None):
            payload = json.dumps(value, ensure_ascii=False).encode()
            self.send_response(status)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(payload)))
            self.send_header('Access-Control-Allow-Origin', fixture.origin)
            if cookie:
                self.send_header('Set-Cookie', cookie)
            self.end_headers()
            self.wfile.write(payload)

        def do_OPTIONS(self):
            self.send_response(204)
            self.send_header('Access-Control-Allow-Origin', fixture.origin)
            self.send_header('Access-Control-Allow-Headers', 'Content-Type, Authorization')
            self.send_header('Access-Control-Allow-Methods', 'GET, POST, PATCH, DELETE, OPTIONS')
            self.send_header('Content-Length', '0')
            self.end_headers()

        do_GET = do_POST = do_PATCH = do_DELETE = handle_request
    return Handler


@contextmanager
def running_fixture(*, split_api=False, api_hostname='127.0.0.1'):
    fixture = Fixture()
    server = ThreadingHTTPServer(('127.0.0.1', 0), handler_for(fixture))
    fixture.origin = f'http://127.0.0.1:{server.server_port}'
    api_server = ThreadingHTTPServer(('127.0.0.1', 0), handler_for(fixture)) if split_api else None
    fixture.api_origin = f'http://{api_hostname}:{api_server.server_port}' if api_server else fixture.origin
    api_thread = threading.Thread(target=api_server.serve_forever, daemon=True) if api_server else None
    if api_thread:
        api_thread.start()
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield fixture
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
        if api_server:
            api_server.shutdown()
            api_server.server_close()
            api_thread.join(timeout=3)
