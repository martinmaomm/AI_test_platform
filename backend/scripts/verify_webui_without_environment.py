"""Local Chromium smoke test for absolute-URL scripts; no DB, Redis or LLM.

Run from backend with the project venv. Only ephemeral loopback pages are used.
Artifacts are kept under backend/temp/environment-free-smoke/ for inspection.
"""

import json
import os
from pathlib import Path
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from uuid import uuid4


class FixtureHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.server.requests_seen.append(self.path)
        content = b'''<!doctype html><html><body>
        <h1>Direct URL fixture</h1><label>Name<input id="name"></label>
        <button onclick="document.querySelector('#result').textContent=document.querySelector('#name').value">Save</button>
        <p id="result"></p></body></html>'''
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Content-Length', str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def log_message(self, *_args):
        pass


def script_for(url):
    return f'''"""场景：无环境配置时直接打开完整网址并验证保存。"""
from playwright.async_api import expect

async def run(page, variables):
    # 使用描述中完整的路径、查询参数和 hash 进入页面
    await page.goto({url!r})
    await expect(page).to_have_url({url!r})
    # 使用可选运行变量填写并验证
    await page.get_by_label('Name').fill(variables.get('NAME', 'local-fixture'))
    await page.get_by_role('button', name='Save', exact=True).click()
    await expect(page.locator('#result')).to_have_text(variables.get('NAME', 'local-fixture'))
'''


def main():
    backend = Path(__file__).resolve().parent.parent
    sys.path[:0] = [str(backend), str(backend / 'apps')]
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'aits_backend.settings')
    from web_testing.assertion_state import evaluation_status
    from web_testing.playwright_python_runner import ExecutionConfig, PlaywrightRunner

    artifacts = backend / 'temp' / 'environment-free-smoke' / str(uuid4())
    artifacts.mkdir(parents=True)
    servers = [ThreadingHTTPServer(('127.0.0.1', 0), FixtureHandler) for _ in range(2)]
    for server in servers:
        server.requests_seen = []
        Thread(target=server.serve_forever, daemon=True).start()
    urls = [f'http://127.0.0.1:{server.server_port}/deep/path?mode=direct#/users' for server in servers]
    runner = PlaywrightRunner()
    summary = {}

    def record(name, result):
        (artifacts / f'{name}.log').write_text(result.stdout + '\n' + result.stderr, encoding='utf-8')
        summary[name] = {'success': result.success, 'assertions': result.runtime_assertion_count,
                         'return_code': result.return_code, 'cases': result.case_results}

    try:
        # A stale process environment must not override a script's own URL.
        os.environ['PLAYWRIGHT_BASE_URL'] = 'http://127.0.0.1:1/should-never-open'
        source = script_for(urls[0])
        single = runner.run_single_test('no-environment', source, ExecutionConfig(
            headed=False, timeout=45, environment_variables={'NAME': 'runtime-value'},
        ))
        record('single', single)
        assert single.success and single.runtime_assertion_count == 2, summary
        assert evaluation_status(source, operation_success=True, runtime_assertion_count=2)[0] == 'passed'

        suite = runner.run_suite_test('two-independent-urls', [
            {'test_case_id': i + 1, 'script_content': script_for(url), 'test_case_title': f'独立站点 {i + 1}'}
            for i, url in enumerate(urls)
        ], ExecutionConfig(headed=False, timeout=60))
        record('suite', suite)
        assert suite.success, summary
        assert all('/deep/path?mode=direct' in server.requests_seen for server in servers), 'URL path/query was lost'

        screenshot = artifacts / 'expected-failure.png'
        failed = runner.run_single_test('expected-failure', source + "\n    await expect(page.locator('#result')).to_have_text('wrong', timeout=500)\n", ExecutionConfig(
            headed=False, timeout=45, failure_screenshot_path=str(screenshot),
        ))
        record('expected_failure', failed)
        assert not failed.success and screenshot.is_file(), 'Failure screenshot was not preserved'
        summary['observed_requests'] = [server.requests_seen for server in servers]
        summary['failure_screenshot'] = str(screenshot)
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        print(f'ARTIFACTS={artifacts}')
    finally:
        (artifacts / 'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
        for server in servers:
            server.shutdown()
            server.server_close()


if __name__ == '__main__':
    main()
