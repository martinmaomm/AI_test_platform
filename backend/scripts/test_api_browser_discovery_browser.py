"""Real Vue + Django browser acceptance for API browser discovery.

This test uses a disposable SQLite database, an in-process WSGI server and a
simulated local broker/runner. It never starts MCP, contacts a model, NAS, or
a target website. Run after the frontend build and browser-discovery migration
are present:

    python backend/scripts/test_api_browser_discovery_browser.py
"""
from __future__ import annotations

import json
import os
import re
import asyncio
from pathlib import Path
import socket
import sys
import tempfile
import threading
from types import SimpleNamespace
from unittest.mock import patch
from wsgiref.simple_server import make_server

from test_project_knowledge_browser import _NoMigrations, _QuietHandler, _static_or_django
from test_platform_reports_browser import CHROME, loopback_only


BACKEND = Path(__file__).resolve().parent.parent
KEY = "api-browser-discovery-isolated-browser-key"


def database(call):
    """Keep ORM calls outside Playwright's sync event-loop owner."""
    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(call).result()


def bootstrap(root):
    sys.path[:0] = [str(BACKEND), str(BACKEND / "apps")]
    os.environ["DJANGO_SETTINGS_MODULE"] = "config.settings"
    os.environ["OFFLINE_TEST_NETWORK"] = "blocked"
    from config import settings as config

    config.DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": str(root / "test.sqlite3")}}
    config.SECRET_KEY = KEY
    config.SIMPLE_JWT = {**config.SIMPLE_JWT, "SIGNING_KEY": KEY}
    config.MIGRATION_MODULES = _NoMigrations()
    config.CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
    config.CELERY_BROKER_URL = "memory://"
    config.CELERY_RESULT_BACKEND = "cache+memory://"
    config.PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
    config.ALLOWED_HOSTS = ["127.0.0.1", "localhost", "testserver"]
    config.MEDIA_ROOT = str(root / "media")
    config.LOGGING = {"version": 1, "disable_existing_loggers": True}
    config.API_BROWSER_DISCOVERY_ENABLED = False
    import django

    django.setup()
    from django.contrib.auth import get_user_model
    from django.core.management import call_command
    from projects.models import Project
    from ai_core.models import LLMConfiguration, ModelType
    from rest_framework_simplejwt.tokens import AccessToken

    call_command("migrate", run_syncdb=True, verbosity=0)
    user = get_user_model().objects.create_user(username="browser-discovery-offline", password="fixture-only")
    project = Project.objects.create(name="浏览器探索隔离验收", project_type="api", created_by=user)
    model = LLMConfiguration.objects.create(
        created_by=user, provider="openai", provider_name="本地模拟", model_type=ModelType.LLM, is_active=True,
        model_name="browser-fixture-model", api_key="fixture-only", base_url="https://never-called.invalid",
    )
    LLMConfiguration.objects.create(
        created_by=user, provider="openai", model_type=ModelType.LLM, model_name="browser-disabled-model", is_active=False,
    )
    return {
        "token": str(AccessToken.for_user(user)), "user_id": user.pk,
        "project_id": project.pk, "model_id": model.pk, "config": config,
        "cancel_started": threading.Event(), "cancel_release": threading.Event(), "workers": [],
    }


def trace_lines(path, api_origin, count, *, eligible=True, same_path=False):
    """Write only local synthetic collector output; task code still ingests it."""
    lines = []
    for sequence in range(1, count + 1):
        request_id = f"fixture-{sequence}"
        resource_type = "fetch" if eligible else "image"
        lines.append({
            "protocol_version": 1, "event": "request", "request_id": request_id,
            "request_sequence": sequence, "url": f"{api_origin}/items/{1 if same_path else sequence}",
            "method": "GET", "resource_type": resource_type,
            "headers": {"Content-Type": "application/json", "Authorization": "must-not-escape"},
        })
        lines.append({
            "protocol_version": 1, "event": "response", "request_id": request_id,
            "status": 200, "headers": {"Content-Type": "application/json", "Set-Cookie": "must-not-escape"},
            **({"capture_status": "metadata_only"} if not eligible else {}),
            "body": {"id": sequence, "token": "must-not-escape"},
        })
    path.write_text("\n".join(json.dumps(item) for item in lines) + "\n", encoding="utf-8")


def simulated_runner(fixture):
    async def run(**kwargs):
        from asgiref.sync import sync_to_async

        description = kwargs["description"]
        checkpoint = kwargs["checkpoint"]
        if "取消" in description:
            assert await sync_to_async(checkpoint, thread_sensitive=True)({"current_action": "正在模拟网页操作", "tool_calls": 2, "model_calls": 1})
            fixture["cancel_started"].set()
            await asyncio.to_thread(fixture["cancel_release"].wait, timeout=20)
            return {"completed": False, "error_code": "cancelled", "summary": "取消前未保留证据", "tool_calls": 2, "model_calls": 1}
        if "无有效" in description:
            count, completed, eligible = 1, False, False
        elif "分页" in description:
            count, completed, eligible = 51, True, True
        elif "多个样本" in description:
            count, completed, eligible = 2, True, True
        else:
            count, completed, eligible = 2, False, True
        trace = Path(kwargs["trace_file"])
        trace.parent.mkdir(parents=True, exist_ok=True)
        trace_lines(trace, kwargs["api_origin"], count, eligible=eligible, same_path="多个样本" in description)
        assert await sync_to_async(checkpoint, thread_sensitive=True)({"current_action": "正在整理已观察接口", "tool_calls": 4, "model_calls": 2})
        return {"completed": completed, "summary": "隔离 runner 已保留观察样本", "tool_calls": 4, "model_calls": 2}

    return run


def verify(origin, fixture, output):
    from playwright.sync_api import expect, sync_playwright
    from api_testing.models import APIEndpoint, APISpecification, APIWorkspace, APITestCase, APITestExecution, BrowserDiscoveryTask

    errors = []
    api_base = f"/api/v1/projects/{fixture['project_id']}/api-testing/browser-discoveries/"

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

        def dialog():
            return page.locator(".el-dialog:visible").last

        def task_row(slug):
            return page.locator(".browser-discovery-panel .el-table__row").filter(has_text=slug)

        def create_task(slug, description):
            page.get_by_role("button", name="从网页探索生成", exact=True).click()
            d = dialog()
            d.get_by_role("textbox", name="完整页面 URL", exact=True).fill(f"https://web.example.test/{slug}?source=browser#fixture")
            d.get_by_role("textbox", name="API origin（可未知）", exact=True).fill("https://api.example.test")
            d.get_by_role("textbox", name="探索目标说明", exact=True).fill(description)
            d.locator(".el-form-item").filter(has_text="LLM 模型").locator(".el-select").click()
            expect(page.get_by_role("option", name="本地模拟 · browser-fixture-model", exact=True)).to_be_visible()
            d.get_by_role("spinbutton").fill("60")
            d.get_by_text("我确认允许在上述授权测试范围内修改测试数据", exact=True).click()
            with page.expect_response(lambda item: item.request.method == "POST" and item.url.endswith(api_base)) as created:
                d.get_by_role("button", name="开始探索", exact=True).click()
            assert created.value.status == 202, created.value.text()
            expect(d).to_be_hidden()

        try:
            page.goto(origin + "/api-testing/workspace")
            expect(page.get_by_role("heading", name="API 对话工作区")).to_be_visible(timeout=20000)
            expect(page.get_by_role("button", name="从接口文档生成", exact=True)).to_be_visible()
            expect(page.get_by_role("button", name="从网页探索生成", exact=True)).to_have_count(0)
            page.screenshot(path=str(output / "disabled-feature.png"), full_page=True)

            fixture["config"].API_BROWSER_DISCOVERY_ENABLED = True
            from django.conf import settings
            settings.API_BROWSER_DISCOVERY_ENABLED = True
            page.reload()
            expect(page.get_by_role("button", name="从网页探索生成", exact=True)).to_be_visible(timeout=15000)
            page.get_by_role("button", name="从网页探索生成", exact=True).click()
            d = dialog()
            d.locator(".el-form-item").filter(has_text="LLM 模型").locator(".el-select").click()
            expect(page.get_by_role("option", name="本地模拟 · browser-fixture-model", exact=True)).to_be_visible()
            expect(page.get_by_role("option", name="browser-disabled-model", exact=True)).to_have_count(0)
            page.keyboard.press("Escape")
            d.get_by_role("button", name="取消", exact=True).click()

            create_task("cancel", "取消任务，观察运行进度")
            assert fixture["cancel_started"].wait(timeout=15), "simulated runner did not start"
            expect(page.get_by_text("正在模拟网页操作", exact=True).first).to_be_visible(timeout=15000)
            page.screenshot(path=str(output / "running-progress.png"), full_page=True)
            page.get_by_role("button", name="取消探索", exact=True).click()
            fixture["cancel_release"].set()
            expect(page.get_by_text("已取消", exact=True).first).to_be_visible(timeout=15000)
            expect(page.get_by_text("取消前未保留证据", exact=True)).to_be_visible()
            expect(page.get_by_text("已请求取消，正在等待当前检查点安全收敛。", exact=True)).to_have_count(0)
            page.reload()
            expect(task_row("/cancel")).to_be_visible(timeout=15000)

            create_task("partial", "部分失败后仍可交接")
            expect(task_row("/partial")).to_be_visible(timeout=15000)
            task_row("/partial").get_by_role("button", name="查看", exact=True).click()
            expect(page.get_by_text("部分完成", exact=True).first).to_be_visible(timeout=15000)
            page.get_by_role("button", name="查看已授权样本", exact=True).click()
            expect(page.get_by_text("GET /items/1", exact=True)).to_be_visible(timeout=15000)
            expect(page.get_by_text("已观察：请求/响应样本", exact=False)).to_be_visible()
            page.screenshot(path=str(output / "partial-records.png"), full_page=True)

            create_task("multiple", "同组多个样本，核对后续响应")
            task_row("/multiple").get_by_role("button", name="查看", exact=True).click()
            page.get_by_role("button", name="查看已授权样本", exact=True).click()
            sample_headers = page.locator(".record-sample .el-collapse-item__header")
            expect(sample_headers).to_have_count(2, timeout=15000)
            expect(sample_headers.nth(0)).to_contain_text("样本 #1")
            expect(sample_headers.nth(1)).to_contain_text("样本 #2")
            sample_headers.nth(1).click()
            expect(page.locator(".record-sample pre").nth(1)).to_contain_text('"id": 2')
            expect(page.locator(".record-sample pre").nth(1)).to_be_visible()
            page.wait_for_function("""() => {
                const wrap = document.querySelectorAll('.record-sample .el-collapse-item__wrap')[1];
                return wrap && wrap.getBoundingClientRect().height >= Math.min(200, wrap.scrollHeight);
            }""")
            page.locator(".record-sample pre").nth(1).scroll_into_view_if_needed()
            page.screenshot(path=str(output / "multiple-samples.png"), full_page=True)

            create_task("no-eligible", "无有效证据")
            task_row("/no-eligible").get_by_role("button", name="查看", exact=True).click()
            expect(page.get_by_text("部分完成", exact=True).first).to_be_visible(timeout=15000)
            page.get_by_role("button", name="查看已授权样本", exact=True).click()
            expect(page.get_by_text("样本 #1（不可交接）", exact=True).first).to_be_visible(timeout=15000)
            expect(page.get_by_role("button", name="创建来源并进入工作区", exact=True)).to_be_disabled()
            page.screenshot(path=str(output / "no-eligible-evidence.png"), full_page=True)

            create_task("page", "分页与五十接口组限制")
            task_row("/page").get_by_role("button", name="查看", exact=True).click()
            expect(page.get_by_text("已完成", exact=True).first).to_be_visible(timeout=15000)
            page.get_by_role("button", name="查看已授权样本", exact=True).click()
            samples = page.locator(".sample-select .el-checkbox:not(.is-disabled)")
            expect(samples).to_have_count(50, timeout=15000)
            for index in range(50):
                samples.nth(index).click()
            expect(page.get_by_role("button", name="加载更多样本", exact=True)).to_be_visible()
            page.get_by_role("button", name="加载更多样本", exact=True).click()
            sample_51 = page.locator(".sample-select .el-checkbox").filter(has_text="样本 #51")
            expect(sample_51).to_be_visible(timeout=15000)
            sample_51.click()
            expect(page.get_by_text("最多可选择 50 组接口", exact=False)).to_be_visible()
            expect(page.get_by_role("button", name="创建来源并进入工作区", exact=True)).to_be_disabled()
            page.screenshot(path=str(output / "pagination-fifty-limit.png"), full_page=True)

            task_row("/partial").get_by_role("button", name="查看", exact=True).click()
            page.get_by_role("button", name="查看已授权样本", exact=True).click()
            selected_samples = page.locator(".sample-select .el-checkbox")
            expect(selected_samples).to_have_count(2)
            selected_samples.nth(0).click()
            selected_samples.nth(1).click()
            page.get_by_role("combobox", name="模型", exact=True).click()
            page.get_by_role("option", name="本地模拟 · browser-fixture-model", exact=True).click()
            expect(page.get_by_role("button", name="保存工作区设置", exact=True)).to_be_enabled()
            page.get_by_role("button", name="创建来源并进入工作区", exact=True).click()
            confirm = page.locator(".el-message-box")
            expect(confirm).to_contain_text("转入网页探索工作区")
            page.screenshot(path=str(output / "dirty-draft-confirmation.png"), full_page=True)
            with page.expect_response(lambda item: item.request.method == "POST" and item.url.endswith("/handoff/")) as handoff:
                confirm.get_by_role("button", name="继续", exact=True).click()
            assert handoff.value.status == 201, handoff.value.text()
            expect(confirm).to_be_hidden()
            expect(page.locator(".el-message-box:visible")).to_have_count(0)
            expect(page.get_by_text("已创建网页探索来源并加载其 API 工作区", exact=False)).to_be_visible(timeout=15000)
            spec_picker = page.locator(".context-panel .el-form-item").filter(has_text="API 规范").locator(".el-select")
            expect(spec_picker).to_contain_text("网页探索发现")
            expect(page.locator(".endpoint-field .el-checkbox.is-checked")).to_have_count(2)
            expect(page.get_by_role("textbox", name="描述测试目标", exact=True)).to_have_value(re.compile("需求历史"))
            page.screenshot(path=str(output / "handoff-workspace.png"), full_page=True)

            handoff_workspace = database(lambda: APIWorkspace.objects.get(title__startswith="网页探索工作区"))
            spec = database(lambda: APISpecification.objects.get(pk=handoff_workspace.spec_id))
            assert spec.spec_type == "browser_capture" and spec.description
            assert handoff_workspace.draft['config']['base_url'] == "https://api.example.test"
            assert sorted(handoff_workspace.endpoint_ids) == sorted(database(lambda: list(APIEndpoint.objects.filter(spec=spec).values_list("id", flat=True))))
            assert database(lambda: BrowserDiscoveryTask.objects.get(task_id=handoff_workspace.generation["source"]["task_id"])).description == "部分失败后仍可交接"
            assert database(lambda: APIWorkspace.objects.get(pk=handoff_workspace.pk)).status == "idle"
            assert database(lambda: APITestCase.objects.count()) == 0
            assert database(lambda: APITestExecution.objects.count()) == 0

            spec_picker.hover()
            clear_spec = spec_picker.locator(".el-select__clear")
            expect(clear_spec).to_be_visible()
            clear_spec.click()
            expect(spec_picker).to_contain_text("选择 API 规范")
            expect(page.locator(".endpoint-field .el-checkbox.is-checked")).to_have_count(0)
            page.screenshot(path=str(output / "cleared-spec-empty.png"), full_page=True)
            assert not errors, errors
        except Exception:
            page.screenshot(path=str(output / "failure.png"), full_page=True)
            raise
        finally:
            context.close()
            browser.close()


def main():
    output = BACKEND / "logs" / "api-browser-discovery-browser"
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="automation-api-browser-discovery-") as temp, patch.object(
        socket.socket, "connect", loopback_only(socket.socket.connect),
    ), patch.object(socket.socket, "connect_ex", loopback_only(socket.socket.connect_ex)):
        fixture = bootstrap(Path(temp))
        from django.core.wsgi import get_wsgi_application
        from api_testing.tasks import run_browser_discovery_async

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

        with patch("api_testing.browser_discovery.resolve_browser_discovery_mcp_config", return_value={"mcpServers": {}}), patch(
            "ai_core.model_manager.get_llm_manager", return_value=SimpleNamespace(current_llm=object()),
        ), patch("api_testing.browser_discovery_agent.run_browser_discovery", side_effect=simulated_runner(fixture)), patch.object(
            run_browser_discovery_async, "apply_async", side_effect=background,
        ):
            server = make_server("127.0.0.1", 0, _static_or_django(get_wsgi_application()), handler_class=_QuietHandler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                verify(f"http://127.0.0.1:{server.server_port}", fixture, output)
            finally:
                fixture["cancel_release"].set()
                for worker in fixture["workers"]:
                    worker.join(timeout=10)
                    assert not worker.is_alive(), "isolated browser-discovery runner did not stop"
                server.shutdown()
                thread.join(timeout=5)
                server.server_close()
    print(f"PASS: API browser-discovery Vue/Django isolated browser acceptance; evidence: {output}")


if __name__ == "__main__":
    main()
