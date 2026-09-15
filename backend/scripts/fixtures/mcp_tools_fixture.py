"""Offline stdio MCP server for tool-list browser acceptance; never executes tools."""
import json
import sys


for line in sys.stdin:
    request = json.loads(line)
    if 'id' not in request:
        continue
    method = request.get('method')
    if method == 'initialize':
        result = {
            'protocolVersion': request.get('params', {}).get('protocolVersion', '2024-11-05'),
            'capabilities': {'tools': {}},
            'serverInfo': {'name': 'offline-tools-fixture', 'version': '1.0'},
        }
    elif method == 'tools/list':
        result = {'tools': [] if '--empty' in sys.argv else [
            {'name': name, 'description': f'仅供清单测试：{name}',
             'inputSchema': {'type': 'object', 'properties': {}}}
            for name in ('fixture_read_page', 'fixture_take_screenshot')
        ]}
    elif method == 'ping':
        result = {}
    else:
        # A probe must only list tools, never invoke actions.
        print(json.dumps({'jsonrpc': '2.0', 'id': request['id'], 'error': {
            'code': -32601, 'message': 'Fixture forbids tool execution',
        }}), flush=True)
        continue
    print(json.dumps({'jsonrpc': '2.0', 'id': request['id'], 'result': result}), flush=True)
