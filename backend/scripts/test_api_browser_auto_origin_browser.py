"""Real Vue/Django + pinned MCP acceptance of origin approval and cancellation.

Only disposable SQLite and loopback fixtures are used. The deterministic model
drives real Chrome, not a simulated collector; no NAS/provider/Redis is called.
Run after building the frontend, with the backend virtual environment.

If npm registry metadata is not cached, TEST_MCP_NPM_PREFIX may point to an
existing npm project containing the pinned package. Also prepend that project's
node_modules/.bin to PATH so the platform output wrapper can resolve the binary.
This remains offline; it does not install or change the package version.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import socket
import tempfile
import threading
from types import SimpleNamespace
from unittest.mock import patch
from wsgiref.simple_server import make_server

from api_browser_discovery_fixture import running_fixture
from test_api_browser_discovery_browser import BACKEND, bootstrap, database
from test_api_browser_discovery_e2e import scripted_model
from test_platform_reports_browser import CHROME, loopback_only
from test_project_knowledge_browser import _QuietHandler, _static_or_django


def verify(origin, fixture, site, output):
    from api_testing.browser_discovery import task_trace_file
    from api_testing.models import BrowserDiscoveryTask
    from playwright.sync_api import expect, sync_playwright

    api_base = f"/api/v1/projects/{fixture['project_id']}/api-testing/browser-discoveries/"
    errors = []
    report = {}
    with sync_playwright() as pw:
        browser = pw.chromium.launch(executable_path=str(CHROME), headless=True)
        context = browser.new_context(viewport={"width": 1536, "height": 1200})
        context.route("**/*", lambda route: route.continue_() if route.request.url.startswith(origin + "/") else route.abort())
        context.add_init_script("localStorage.setItem('auth-store', JSON.stringify(" + json.dumps({
            "accessToken": fixture["token"], "refreshToken": None,
            "user": {"id": fixture["user_id"], "username": "browser-discovery-offline"},
        }) + ")); localStorage.removeItem('project-store');")
        page = context.new_page()
        page.on("pageerror", lambda error: errors.append(str(error)))

        def start():
            page.goto(origin + "/api-testing/workspace/browser")
            expect(page).to_have_url(re.compile(r"/workspace/browser$"))
            expect(page.locator(".discovery-toolbar")).to_be_visible(timeout=20000)
            page.get_by_test_id("api-browser-discovery-new").click()
            expect(page).to_have_url(re.compile(r"/workspace/browser\?view=new$"))
            form = page.get_by_test_id("api-browser-discovery-create-form")
            expect(form).to_be_visible(timeout=20000)
            expect(form.get_by_role("textbox", name="手动 API origin（可选）", exact=True)).to_be_hidden()
            form.get_by_role("textbox", name="完整页面 URL", exact=True).fill(site.origin + "/app")
            form.get_by_role("textbox", name="探索目标说明", exact=True).fill("登录后探索物品新增、修改、删除及查询，仅操作本轮数据。")
            form.locator(".el-form-item").filter(has_text="LLM 模型").locator(".el-select").click()
            page.get_by_role("option", name="本地模拟 · browser-fixture-model", exact=True).click()
            form.get_by_role("spinbutton").fill("120")
            form.get_by_text("我确认允许在上述授权测试范围内修改测试数据", exact=True).click()
            with page.expect_response(lambda response: response.request.method == "POST" and response.url.endswith(api_base)) as created:
                form.get_by_role("button", name="开始探索", exact=True).click()
            assert created.value.status == 202, created.value.text()
            task = database(lambda: BrowserDiscoveryTask.objects.latest("created_at"))
            expect(page).to_have_url(re.compile(rf"/workspace/browser\?discovery_id={re.escape(str(task.id))}$"))
            pending = page.locator(".origin-candidate").filter(has_text=site.api_origin)
            expect(pending).to_be_visible(timeout=60000)
            expect(pending).to_contain_text("POST /api/login")
            return task, pending

        try:
            task, pending = start()
            assert not site.ledger, "An unapproved login reached the API server"
            expect(pending).not_to_contain_text("fixture-password")
            pending.scroll_into_view_if_needed()
            page.screenshot(path=str(output / "pending-origin.png"), full_page=True)
            with page.expect_response(lambda response: response.request.method == "POST" and response.url.endswith(f"/{task.id}/origins/")) as approved:
                pending.get_by_role("button", name="允许", exact=True).click()
            assert approved.value.status == 200, approved.value.text()
            expect(page.locator(".task-detail-header .el-tag")).to_have_text(re.compile(r"^(已完成|部分完成|失败|已取消)$"), timeout=60000)
            task = database(lambda: BrowserDiscoveryTask.objects.get(pk=task.pk))
            assert task.status == "completed", (task.status, task.error_code, task.evidence_summary)
            assert task.api_origin == site.api_origin
            assert not site.items
            login_count = sum(row["path"] == "/api/login" for row in site.ledger)
            assert login_count == 1, f"Approval replayed login {login_count} times"
            for method in ("POST", "PATCH", "DELETE"):
                assert sum(row["method"] == method and row["path"].startswith("/api/items") for row in site.ledger) == 1
            trace = [json.loads(line) for line in task_trace_file(task).read_text().splitlines()]
            login = next(row for row in trace if row.get("event") == "request" and row.get("path") == "/api/login")
            assert any(row.get("event") == "request_body" and row.get("request_id") == login["request_id"] for row in trace)
            assert any(row.get("event") == "response" and row.get("request_id") == login["request_id"] and row.get("status") == 200 for row in trace)
            expect(page.locator(".record-row strong").filter(has_text="POST /api/login")).to_be_visible(timeout=10000)
            expect(page.get_by_text("没有可交接的已授权样本", exact=True)).to_have_count(0)
            expect(page.get_by_role("button", name="确认接口并生成场景", exact=True)).to_be_visible()
            page.screenshot(path=str(output / "approved-complete.png"), full_page=True)
            report.update(approved_login_requests=login_count, approved_status=task.status, remaining_items=len(site.items), completed_samples_refreshed=True)

            previous_count = len(site.ledger)
            cancelled, pending = start()
            assert len(site.ledger) == previous_count, "Second unapproved login reached server"
            page.get_by_role("button", name="取消探索", exact=True).click()
            expect(page.locator(".task-detail-header .el-tag")).to_have_text("已取消", timeout=30000)
            cancelled = database(lambda: BrowserDiscoveryTask.objects.get(pk=cancelled.pk))
            assert cancelled.status == "cancelled"
            assert len(site.ledger) == previous_count, "Cancellation released a held request to the network"
            page.screenshot(path=str(output / "cancelled-pending-origin.png"), full_page=True)
            report.update(cancelled_status=cancelled.status, cancelled_requests=0, page_errors=errors)
            assert not errors, errors
        except Exception:
            page.screenshot(path=str(output / "failure.png"), full_page=True)
            raise
        finally:
            context.close()
            browser.close()
    return report


def main():
    os.environ["ANONYMIZED_TELEMETRY"] = "false"
    os.environ["MCP_USE_ANONYMIZED_TELEMETRY"] = "false"
    output = BACKEND / "logs" / "api-browser-auto-origin-browser"
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="automation-api-auto-origin-") as temp, running_fixture(
        split_api=True, api_hostname="localhost",
    ) as site, patch.object(socket.socket, "connect", loopback_only(socket.socket.connect)), patch.object(
        socket.socket, "connect_ex", loopback_only(socket.socket.connect_ex),
    ):
        fixture = bootstrap(Path(temp))
        from django.conf import settings
        from django.core.wsgi import get_wsgi_application
        from ai_core.models import MCPConfiguration
        from api_testing.tasks import run_browser_discovery_async

        settings.API_BROWSER_DISCOVERY_ENABLED = True
        MCPConfiguration.objects.create(created_by_id=fixture["user_id"], name="playwright", raw_config=json.dumps({"mcpServers": {"playwright": {
            "command": "npx", "args": ["--offline", "-y",
                # An installed local prefix also works when npm's registry
                # metadata cache is absent; never download a replacement.
                *(["--prefix", os.environ["TEST_MCP_NPM_PREFIX"]] if os.environ.get("TEST_MCP_NPM_PREFIX") else []),
                "@executeautomation/playwright-mcp-server@1.0.12"],
            "env": {"CHROME_EXECUTABLE_PATH": str(CHROME)}, "timeout": 45,
        }}}))

        def background(args, task_id, **kwargs):
            def run():
                from django.db import connections
                try:
                    run_browser_discovery_async.apply(args=args, task_id=task_id)
                finally:
                    connections.close_all()
            worker = threading.Thread(target=run, daemon=True)
            fixture["workers"].append(worker)
            worker.start()
            return SimpleNamespace(id=task_id)

        with patch("ai_core.model_manager.get_llm_manager", side_effect=lambda *args, **kwargs: SimpleNamespace(current_llm=scripted_model(site.origin))), patch.object(
            run_browser_discovery_async, "apply_async", side_effect=background,
        ):
            server = make_server("127.0.0.1", 0, _static_or_django(get_wsgi_application()), handler_class=_QuietHandler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                report = verify(f"http://127.0.0.1:{server.server_port}", fixture, site, output)
            finally:
                from api_testing.models import BrowserDiscoveryTask
                BrowserDiscoveryTask.objects.filter(status__in=["queued", "running", "finalizing"]).update(cancellation_requested=True)
                for worker in fixture["workers"]:
                    worker.join(timeout=15)
                    assert not worker.is_alive(), "MCP worker did not release its browser"
                server.shutdown()
                thread.join(timeout=5)
                server.server_close()
        report["scope"] = "Real Vue/Django/Chrome/MCP; deterministic model; isolated loopback only"
        (output / "summary.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
