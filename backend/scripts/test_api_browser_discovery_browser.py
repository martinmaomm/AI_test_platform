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
import uuid
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
    from api_testing.models import APISpecification, APIEndpoint, APIWorkspace
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
    spec = APISpecification.objects.create(
        project=project, created_by=user, spec_name="验收 Swagger 文档", status="completed",
        spec_type="swagger", metadata={"swagger": "2.0", "paths": {}},
    )
    endpoint = APIEndpoint.objects.create(spec=spec, method="GET", path="/health", summary="文档健康检查")
    workspace = APIWorkspace.objects.create(
        project=project, owner=user, spec=spec, title="文档来源工作区", model_id=model.pk,
        endpoint_ids=[endpoint.pk],
    )
    return {
        "token": str(AccessToken.for_user(user)), "user_id": user.pk,
        "project_id": project.pk, "model_id": model.pk, "config": config,
        "document_workspace_id": workspace.pk, "document_endpoint_id": endpoint.pk,
        "cancel_started": threading.Event(), "cancel_release": threading.Event(), "workers": [],
    }


def trace_lines(path, api_origin, count, *, eligible=True, same_path=False, repeat_first=False, repeat_last=False):
    """Write only local synthetic collector output; task code still ingests it."""
    lines = []
    for sequence in range(1, count + 1):
        request_id = f"fixture-{sequence}"
        sample_id = max(1, sequence - 1) if repeat_first else sequence
        if repeat_last and sequence == count:
            sample_id = 1
        resource_type = "fetch" if eligible else "image"
        lines.append({
            "protocol_version": 1, "event": "request", "request_id": request_id,
            "request_sequence": sequence, "url": f"{api_origin}/items/{1 if same_path else sample_id}",
            "method": "GET", "resource_type": resource_type,
            "headers": {"Content-Type": "application/json", "Authorization": "must-not-escape"},
        })
        lines.append({
            "protocol_version": 1, "event": "response", "request_id": request_id,
            "status": 200, "headers": {"Content-Type": "application/json", "Set-Cookie": "must-not-escape"},
            **({"capture_status": "metadata_only"} if not eligible else {}),
            "body": {"id": sample_id, "token": "must-not-escape"},
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
            await asyncio.to_thread(fixture["cancel_release"].wait, timeout=45)
            return {"completed": False, "error_code": "cancelled", "summary": "取消前未保留证据", "tool_calls": 2, "model_calls": 1}
        if "无有效" in description:
            count, completed, eligible = 1, False, False
        elif "分页" in description:
            count, completed, eligible = 52, True, True
        elif "多个样本" in description:
            count, completed, eligible = 2, True, True
        else:
            count, completed, eligible = 3, False, True
        trace = Path(kwargs["trace_file"])
        trace.parent.mkdir(parents=True, exist_ok=True)
        trace_lines(
            trace, kwargs["api_origin"], count, eligible=eligible,
            same_path="多个样本" in description, repeat_first="部分失败" in description,
            repeat_last="分页" in description,
        )
        assert await sync_to_async(checkpoint, thread_sensitive=True)({"current_action": "正在整理已观察接口", "tool_calls": 4, "model_calls": 2})
        return {"completed": completed, "summary": "隔离 runner 已保留观察样本", "tool_calls": 4, "model_calls": 2}

    return run


def verify(origin, fixture, output):
    from playwright.sync_api import expect, sync_playwright
    from api_testing.models import APIEndpoint, APISpecification, APIWorkspace, APITestCase, APITestExecution, BrowserDiscoveryRecord, BrowserDiscoveryTask

    errors = []
    requests = []
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
        page.on("request", lambda request: requests.append((request.method, request.url)))

        def tab(source):
            return page.get_by_test_id(f"api-workspace-source-{source}")

        def switch_to(source):
            tab(source).click()
            expect(page).to_have_url(re.compile(rf"/workspace/{'documents' if source == 'documents' else 'browser'}(?:\?|$)"))
            expect(tab(source)).to_have_attribute("aria-selected", "true")
            if source == "documents":
                expect(page.get_by_test_id("api-workspace-select")).to_contain_text("文档来源工作区")
                expect(page.locator(".endpoint-field .el-checkbox.is-checked")).to_have_count(1)
            elif fixture["config"].API_BROWSER_DISCOVERY_ENABLED:
                expect(page.locator(".discovery-toolbar")).to_be_visible()
                expect(page.get_by_test_id("api-browser-discovery-create-form")).to_have_count(0)

        def task_row(slug):
            return page.locator(".browser-discovery-panel .el-table__row").filter(has_text=slug)

        def show_task_list():
            page.get_by_test_id("api-browser-discovery-list").click()
            expect(page).to_have_url(re.compile(r"/workspace/browser$"))
            expect(page.locator(".discovery-toolbar")).to_be_visible(timeout=15000)

        def open_task(slug):
            show_task_list()
            task_row(slug).get_by_role("button", name="查看", exact=True).click()
            expect(page).to_have_url(re.compile(r"/workspace/browser\?discovery_id=[0-9a-f-]{36}$"))

        def create_task(slug, description):
            page.get_by_test_id("api-browser-discovery-new").click()
            expect(page).to_have_url(re.compile(r"/workspace/browser\?view=new$"))
            d = page.get_by_test_id("api-browser-discovery-create-form")
            d.get_by_role("textbox", name="完整页面 URL", exact=True).fill(f"https://web.example.test/{slug}?source=browser#fixture")
            origin_input = d.get_by_role("textbox", name="手动 API origin（可选）", exact=True)
            if not origin_input.is_visible():
                d.get_by_text("高级设置", exact=True).click()
            origin_input.fill("https://api.example.test")
            d.get_by_role("textbox", name="探索目标说明", exact=True).fill(description)
            d.locator(".el-form-item").filter(has_text="LLM 模型").locator(".el-select").click()
            expect(page.get_by_role("option", name="本地模拟 · browser-fixture-model", exact=True)).to_be_visible()
            page.get_by_role("option", name="本地模拟 · browser-fixture-model", exact=True).click()
            d.get_by_role("spinbutton").fill("60")
            consent = d.get_by_role("checkbox", name="允许测试数据写入", exact=True)
            if not consent.is_checked():
                d.get_by_text("我确认允许在上述授权测试范围内修改测试数据", exact=True).click()
            expect(consent).to_be_checked()
            with page.expect_response(lambda item: item.request.method == "POST" and item.url.endswith(api_base)) as created:
                d.get_by_role("button", name="开始探索", exact=True).click()
            assert created.value.status == 202, created.value.text()
            task = created.value.json()["data"]
            expect(page).to_have_url(re.compile(rf"/workspace/browser\?discovery_id={re.escape(str(task['id']))}$"))
            return task

        try:
            page.goto(origin + "/api-testing/workspace")
            expect(page.get_by_role("tab", name="从接口文档生成", exact=True)).to_be_visible(timeout=20000)
            expect(page).to_have_url(re.compile(r"/workspace/documents\?workspace_id="))
            expect(tab("documents")).to_have_attribute("aria-selected", "true")
            expect(page.locator(".header-actions")).to_contain_text("文档来源工作区")
            spec_picker = page.locator(".context-panel .el-form-item").filter(has_text="API 规范").locator(".el-select")
            expect(spec_picker).to_contain_text("验收 Swagger 文档")
            expect(page.get_by_test_id("api-browser-discovery-panel")).to_have_count(0)
            assert not any("browser-discoveries" in url for _, url in requests), requests
            page.screenshot(path=str(output / "default-document-page.png"), full_page=True)
            switch_to("browser")
            expect(page.get_by_test_id("api-browser-discovery-create-form")).to_have_count(0)
            expect(page.locator(".discovery-toolbar")).to_have_count(0)
            page.screenshot(path=str(output / "disabled-feature.png"), full_page=True)

            fixture["config"].API_BROWSER_DISCOVERY_ENABLED = True
            from django.conf import settings
            settings.API_BROWSER_DISCOVERY_ENABLED = True
            requests.clear()
            page.reload()
            expect(page).to_have_url(re.compile(r"/workspace/browser$"))
            expect(page.locator(".discovery-toolbar")).to_be_visible(timeout=15000)
            expect(page.get_by_test_id("api-browser-discovery-create-form")).to_have_count(0)
            expect(page.locator(".context-panel")).to_have_count(0)
            expect(page.get_by_role("button", name="新建工作区", exact=True)).to_have_count(0)
            assert not any(re.search(r"/api-specs/(?:\?|$)", url) for _, url in requests), requests
            assert database(lambda: APIWorkspace.objects.count()) == 1, "browser entry created an empty workspace"
            page.get_by_test_id("api-browser-discovery-new").click()
            expect(page).to_have_url(re.compile(r"/workspace/browser\?view=new$"))
            d = page.get_by_test_id("api-browser-discovery-create-form")
            expect(d).to_be_visible(timeout=15000)
            d.locator(".el-form-item").filter(has_text="LLM 模型").locator(".el-select").click()
            expect(page.get_by_role("option", name="本地模拟 · browser-fixture-model", exact=True)).to_be_visible()
            expect(page.get_by_role("option", name="browser-disabled-model", exact=True)).to_have_count(0)
            page.keyboard.press("Escape")
            page.screenshot(path=str(output / "inline-browser-page.png"), full_page=True)
            url_input = d.get_by_role("textbox", name="完整页面 URL", exact=True)
            url_input.fill("https://web.example.test/unsent")
            tab("documents").click()
            unsent_confirm = page.locator(".el-message-box:visible")
            expect(unsent_confirm).to_be_visible()
            unsent_confirm.get_by_role("button", name="取消", exact=True).click()
            expect(url_input).to_have_value("https://web.example.test/unsent")
            url_input.fill("")

            cancel_task = create_task("cancel", "取消任务，观察运行进度")
            assert fixture["cancel_started"].wait(timeout=15), "simulated runner did not start"
            expect(page.get_by_text("正在模拟网页操作", exact=True).first).to_be_visible(timeout=15000)
            expect(page.get_by_role("button", name="删除", exact=True)).to_be_disabled()
            page.screenshot(path=str(output / "running-progress.png"), full_page=True)
            # Leaving the browser page must not send task cancellation or create a document workspace.
            switch_to("documents")
            assert not any(method == "POST" and "/cancel/" in url for method, url in requests)
            assert not database(lambda: BrowserDiscoveryTask.objects.get(target_url__contains="/cancel").cancellation_requested)
            requests.clear()
            # Observe more than one 1500 ms poll interval after leaving. An
            # inactive cached browser page must not keep requesting task data.
            page.wait_for_timeout(1800)
            assert not any("browser-discoveries" in url for _, url in requests), requests
            switch_to("browser")
            open_task("/cancel")
            page.get_by_role("button", name="取消探索", exact=True).click()
            fixture["cancel_release"].set()
            expect(page.get_by_text("已取消", exact=True).first).to_be_visible(timeout=15000)
            expect(page.get_by_text("取消前未保留证据", exact=True)).to_be_visible()
            expect(page.get_by_text("已请求取消，正在等待当前检查点安全收敛。", exact=True)).to_have_count(0)
            page.reload()
            expect(page).to_have_url(re.compile(rf"/workspace/browser\?discovery_id={re.escape(str(cancel_task['id']))}$"))

            partial_task = create_task("partial", "部分失败后仍可交接")
            expect(page).to_have_url(re.compile(rf"/workspace/browser\?discovery_id={re.escape(str(partial_task['id']))}$"))
            expect(page.get_by_text("部分完成", exact=True).first).to_be_visible(timeout=15000)
            # Terminal task details automatically load their saved samples.
            expect(page.get_by_text("GET /items/1", exact=True)).to_be_visible(timeout=15000)
            expect(page.get_by_text("已观察：请求/响应样本", exact=False)).to_be_visible()
            # Three captured requests contain only two publicly distinct samples.
            # The list must collapse the duplicate but keep its evidence at handoff.
            expect(page.locator(".sample-select .el-checkbox")).to_have_count(2)
            expect(page.locator(".record-sample .el-collapse-item__header")).to_have_count(2)
            duplicate_row = page.locator(".record-row").filter(has_text="GET /items/1")
            expect(duplicate_row).to_contain_text("采集 2 次")
            expect(duplicate_row).to_contain_text("合并 1")
            assert database(lambda: BrowserDiscoveryRecord.objects.filter(task__target_url__contains="/partial").count()) == 3
            page.screenshot(path=str(output / "partial-records.png"), full_page=True)

            create_task("multiple", "同组多个样本，核对后续响应")
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
            expect(page.get_by_text("部分完成", exact=True).first).to_be_visible(timeout=15000)
            expect(page.get_by_text("样本 #1（不可交接）", exact=True).first).to_be_visible(timeout=15000)
            expect(page.get_by_role("button", name="确认接口并生成场景", exact=True)).to_be_disabled()
            page.screenshot(path=str(output / "no-eligible-evidence.png"), full_page=True)

            create_task("page", "分页与五十接口组限制")
            expect(page.get_by_text("已完成", exact=True).first).to_be_visible(timeout=15000)
            samples = page.locator(".sample-select .el-checkbox:not(.is-disabled)")
            expect(samples).to_have_count(50, timeout=15000)
            for index in range(50):
                samples.nth(index).click()
            expect(page.get_by_role("button", name="加载更多样本", exact=True)).to_be_visible()
            page.get_by_role("button", name="加载更多样本", exact=True).click()
            sample_51 = page.locator(".sample-select .el-checkbox").filter(has_text="样本 #51")
            expect(sample_51).to_be_visible(timeout=15000)
            expect(samples).to_have_count(51)
            first_page_sample = page.locator(".record-row").filter(
                has=page.get_by_text("GET /items/1", exact=True),
            )
            expect(first_page_sample).to_contain_text("采集 2 次")
            expect(first_page_sample.locator(".sample-select .el-checkbox")).to_have_class(re.compile(r"is-checked"))
            expect(page.locator(".sample-select .el-checkbox").filter(has_text="样本 #52")).to_have_count(0)
            sample_51.click()
            expect(page.get_by_text("最多可选择 50 组接口", exact=False)).to_be_visible()
            expect(page.get_by_role("button", name="确认接口并生成场景", exact=True)).to_be_disabled()
            page.screenshot(path=str(output / "pagination-fifty-limit.png"), full_page=True)

            open_task("/partial")
            selected_samples = page.locator(".sample-select .el-checkbox")
            expect(selected_samples).to_have_count(2)
            selected_samples.nth(0).click()
            selected_samples.nth(1).click()
            with page.expect_response(lambda item: item.request.method == "POST" and item.url.endswith("/handoff/")) as handoff:
                page.get_by_role("button", name="确认接口并生成场景", exact=True).click()
            assert handoff.value.status == 201, handoff.value.text()
            handed_ids = handoff.value.request.post_data_json["record_ids"]
            expected_ids = database(lambda: list(BrowserDiscoveryRecord.objects.filter(
                task__target_url__contains="/partial",
            ).order_by("id").values_list("id", flat=True)))
            assert sorted(handed_ids) == expected_ids, "merged selection lost raw evidence IDs"
            source = handoff.value.json()["data"]["workspace"]
            assert source["source_type"] == "browser_capture" and source["source_task_id"] and source["source_name"]
            expect(page).to_have_url(re.compile(r"/workspace/browser\?workspace_id="))
            confirmation = page.get_by_role("dialog", name="生成并验证确认", exact=True)
            expect(confirmation).to_be_visible(timeout=15000)
            expect(confirmation.get_by_role("button", name="确认并开始验证", exact=True)).to_be_enabled()
            confirmation.get_by_role("button", name="取消", exact=True).click()
            expect(confirmation).to_be_hidden()
            expect(page.get_by_test_id("api-workspace-source-readonly")).to_contain_text("网页探索发现")
            expect(page.get_by_test_id("api-browser-discovery-panel")).to_have_count(0)
            expect(page.locator(".context-panel .el-form-item").filter(has_text="API 规范").locator(".el-select")).to_have_count(0)
            expect(page.locator(".endpoint-field .el-checkbox.is-checked")).to_have_count(2)
            expect(page.get_by_role("textbox", name="描述测试目标", exact=True)).to_have_value(re.compile("需求历史"))
            assert not any(re.search(r"/api-specs/(?:\?|$)", url) for _, url in requests if "/workspaces" not in url), requests
            page.get_by_test_id("api-workspace-source-readonly").scroll_into_view_if_needed()
            page.screenshot(path=str(output / "handoff-workspace.png"), full_page=True)

            handoff_workspace = database(lambda: APIWorkspace.objects.get(title__startswith="网页探索工作区"))
            spec = database(lambda: APISpecification.objects.get(pk=handoff_workspace.spec_id))
            assert spec.spec_type == "browser_capture" and spec.description
            assert sorted(spec.metadata["browser_capture"]["selected_record_ids"]) == expected_ids
            assert len(spec.metadata["browser_capture"]["observed_samples"]) == 3
            assert handoff_workspace.draft['config']['base_url'] == "https://api.example.test"
            assert sorted(handoff_workspace.endpoint_ids) == sorted(database(lambda: list(APIEndpoint.objects.filter(spec=spec).values_list("id", flat=True))))
            assert database(lambda: BrowserDiscoveryTask.objects.get(task_id=handoff_workspace.generation["source"]["task_id"])).description == "部分失败后仍可交接"
            assert database(lambda: APIWorkspace.objects.get(pk=handoff_workspace.pk)).status == "idle"
            assert database(lambda: APITestCase.objects.count()) == 0
            assert database(lambda: APITestExecution.objects.count()) == 0

            # The source list is isolated, and unsent text survives cancelling a page switch.
            page.get_by_test_id("api-workspace-select").click()
            expect(page.get_by_role("option", name="文档来源工作区", exact=True)).to_have_count(0)
            page.keyboard.press("Escape")
            prompt = page.get_by_role("textbox", name="描述测试目标", exact=True)
            prompt.fill("未提交的隔离验收描述")
            tab("documents").click()
            confirm = page.locator(".el-message-box:visible")
            expect(confirm).to_be_visible()
            page.screenshot(path=str(output / "dirty-draft-confirmation.png"), full_page=True)
            confirm.get_by_role("button", name="取消", exact=True).click()
            expect(tab("browser")).to_have_attribute("aria-selected", "true")
            expect(prompt).to_have_value("未提交的隔离验收描述")
            prompt.fill("")
            switch_to("documents")
            expect(page.locator(".header-actions")).to_contain_text("文档来源工作区")
            expect(page.get_by_test_id("api-browser-discovery-panel")).to_have_count(0)
            page.get_by_test_id("api-workspace-select").click()
            expect(page.get_by_role("option", name=re.compile("网页探索工作区"))).to_have_count(0)
            page.keyboard.press("Escape")
            doc_picker = page.locator(".context-panel .el-form-item").filter(has_text="API 规范").locator(".el-select")
            doc_picker.click()
            expect(page.get_by_role("option", name="验收 Swagger 文档", exact=True)).to_be_visible()
            expect(page.get_by_role("option", name=re.compile("网页探索发现"))).to_have_count(0)
            page.keyboard.press("Escape")
            page.screenshot(path=str(output / "document-source-isolation.png"), full_page=True)

            # Provenance survives generation metadata replacement and all entry routes.
            database(lambda: APIWorkspace.objects.filter(pk=handoff_workspace.pk).update(generation={}))
            requests.clear()
            page.goto(origin + f"/api-testing/workspace?workspace_id={handoff_workspace.pk}")
            expect(page).to_have_url(re.compile(rf"/workspace/browser\?workspace_id={handoff_workspace.pk}$"), timeout=15000)
            expect(page.get_by_test_id("api-workspace-source-readonly")).to_contain_text(spec.spec_name)
            expect(page.locator(".endpoint-field .el-checkbox.is-checked")).to_have_count(2)
            page.reload()
            expect(tab("browser")).to_have_attribute("aria-selected", "true")
            expect(page.get_by_test_id("api-workspace-source-readonly")).to_contain_text(spec.spec_name)
            expect(page.get_by_test_id("api-browser-discovery-new")).to_be_enabled()
            expect(page.get_by_test_id("api-browser-discovery-panel")).to_have_count(0)
            page.get_by_test_id("api-workspace-source-readonly").scroll_into_view_if_needed()
            page.screenshot(path=str(output / "browser-refresh-stable-source.png"), full_page=True)

            endpoint_id = handoff_workspace.endpoint_ids[0]
            page.goto(origin + f"/api-testing/workspace?endpoint_id={endpoint_id}")
            expect(page).to_have_url(re.compile(r"/workspace/browser\?workspace_id="), timeout=15000)
            expect(page.get_by_test_id("api-workspace-source-readonly")).to_contain_text(spec.spec_name)
            # A saved multi-endpoint scenario has no case.endpoint FK. Its actual
            # step references must retain browser provenance when reopened.
            case_script = {
                "version": 1, "config": {"name": "浏览器场景深链", "base_url": "https://api.example.test"},
                "teststeps": [
                    {"name": f"检查接口 {index}", "endpoint_id": endpoint,
                     "request": {"method": "GET", "url": f"/items/{index}"},
                     "validate": [{"eq": ["status_code", 200]}]}
                    for index, endpoint in enumerate(handoff_workspace.endpoint_ids, 1)
                ],
            }
            saved_case = database(lambda: APITestCase.objects.create(
                project_id=fixture["project_id"], created_by_id=fixture["user_id"],
                title="浏览器场景深链", test_case_type="scenario", endpoint=None,
                script_content=json.dumps(case_script),
            ))
            page.goto(origin + f"/api-testing/workspace?case_id={saved_case.pk}")
            expect(page).to_have_url(re.compile(r"/workspace/browser\?workspace_id="), timeout=15000)
            expect(page.get_by_test_id("api-workspace-source-readonly")).to_contain_text(spec.spec_name)
            expect(page.locator(".endpoint-field .el-checkbox.is-checked")).to_have_count(2)

            # A published source keeps its provenance. The standalone workspace
            # exposes an explicit return link instead of mixing task rows into
            # the editor, and the referenced source remains undeletable.
            page.get_by_test_id("api-browser-source-task").click()
            expect(page).to_have_url(re.compile(rf"/workspace/browser\?discovery_id={re.escape(str(partial_task['id']))}$"))
            expect(page.get_by_role("button", name="删除", exact=True)).to_be_disabled()

            def failed_fixture(slug):
                from django.utils import timezone

                task = BrowserDiscoveryTask.objects.create(
                    project_id=fixture["project_id"], owner_id=fixture["user_id"],
                    model_id=fixture["model_id"], task_id=str(uuid.uuid4()),
                    target_url=f"https://web.example.test/{slug}",
                    description="独立失败删除验收", api_origin="https://api.example.test",
                    status=BrowserDiscoveryTask.Status.FAILED, finished_at=timezone.now(),
                    error_code="fixture_failure", error_message="隔离模拟失败",
                )
                BrowserDiscoveryRecord.objects.create(
                    task=task, sequence=1, method="GET", path=f"/{slug}",
                    origin="https://api.example.test", resource_type="fetch",
                    status_code=200, is_eligible=True,
                )
                return str(task.pk)

            failed_id = database(lambda: failed_fixture("delete-failed"))
            other_id = database(lambda: failed_fixture("delete-other"))
            show_task_list()
            page.locator(".discovery-toolbar").get_by_role("button", name="刷新", exact=True).click()
            expect(task_row("/delete-failed")).to_be_visible(timeout=15000)
            open_task("/delete-failed")
            detail = page.locator(".browser-discovery-panel .task-detail")
            expect(detail).to_contain_text(failed_id)
            page.get_by_role("button", name="查看已授权样本", exact=True).click()
            expect(detail).to_contain_text("GET /delete-failed")

            # Cancelling confirmation does not send a DELETE or change selection.
            before_delete_count = sum(method == "DELETE" for method, _ in requests)
            detail.get_by_role("button", name="删除", exact=True).click()
            delete_dialog = page.locator(".el-message-box:visible")
            expect(delete_dialog).to_be_visible()
            expect(delete_dialog).to_be_in_viewport()
            expect(delete_dialog).to_have_css("opacity", "1")
            page.screenshot(path=str(output / "delete-failed-confirmation.png"), full_page=True, animations="disabled")
            delete_dialog.get_by_role("button", name="取消", exact=True).click()
            assert sum(method == "DELETE" for method, _ in requests) == before_delete_count
            assert database(lambda: BrowserDiscoveryTask.objects.filter(pk=failed_id).exists())
            expect(detail).to_contain_text(failed_id)

            # If the rendered row is stale, the server still owns the decision.
            # A conflict must keep the task and show an actionable error.
            database(lambda: BrowserDiscoveryTask.objects.filter(pk=failed_id).update(status="running"))
            detail.get_by_role("button", name="删除", exact=True).click()
            with page.expect_response(lambda item: item.request.method == "DELETE" and item.url.endswith(f"{api_base}{failed_id}/")) as refused:
                delete_dialog.get_by_role("button", name=re.compile("删除")).click()
            assert refused.value.status == 409, refused.value.text()
            expect(page.locator(".el-message--error").last).to_be_visible()
            expect(detail).to_be_visible()
            expect(detail).to_contain_text(failed_id)
            assert database(lambda: BrowserDiscoveryRecord.objects.filter(task_id=failed_id).exists())
            database(lambda: BrowserDiscoveryTask.objects.filter(pk=failed_id).update(status="failed"))
            show_task_list()
            page.locator(".discovery-toolbar").get_by_role("button", name="刷新", exact=True).click()
            expect(task_row("/delete-failed").get_by_role("button", name="删除", exact=True)).to_be_enabled()

            # List/detail are intentionally separate pages. Deleting another
            # row returns to the list; reopening the selected task proves that
            # its detail and samples neither leak nor get replaced.
            task_row("/delete-other").get_by_role("button", name="删除", exact=True).click()
            with page.expect_response(lambda item: item.request.method == "DELETE" and item.url.endswith(f"{api_base}{other_id}/")) as deleted:
                delete_dialog.get_by_role("button", name=re.compile("删除")).click()
            assert deleted.value.status == 200, deleted.value.text()
            expect(task_row("/delete-other")).to_have_count(0)
            open_task("/delete-failed")
            detail = page.locator(".browser-discovery-panel .task-detail")
            expect(detail).to_contain_text(failed_id)
            expect(detail).to_contain_text("GET /delete-failed")
            assert not database(lambda: BrowserDiscoveryRecord.objects.filter(task_id=other_id).exists())

            # Deleting the selected failure clears the detail without a manual
            # refresh and leaves already-created cases/workspaces/specs intact.
            held_samples = []

            def hold_samples(route):
                # Save a real pre-deletion response, but deliver it only after
                # DELETE succeeds. A late read must not resurrect old samples.
                held_samples.append((route, route.fetch()))

            sample_route = re.compile(rf"{re.escape(api_base)}{re.escape(failed_id)}/records/.*")
            page.route(sample_route, hold_samples)
            with page.expect_request(lambda item: f"{api_base}{failed_id}/records/" in item.url):
                page.get_by_role("button", name="查看已授权样本", exact=True).click()
            # request events precede route handlers; pump Playwright's event
            # loop until the real response has been captured for delayed delivery.
            for _ in range(100):
                if held_samples:
                    break
                page.wait_for_timeout(50)
            assert len(held_samples) == 1
            detail.get_by_role("button", name="删除", exact=True).click()
            with page.expect_response(lambda item: item.request.method == "DELETE" and item.url.endswith(f"{api_base}{failed_id}/")) as deleted:
                delete_dialog.get_by_role("button", name=re.compile("删除")).click()
            assert deleted.value.status == 200, deleted.value.text()
            expect(page).to_have_url(re.compile(r"/workspace/browser$"))
            expect(task_row("/delete-failed")).to_have_count(0)
            expect(detail).to_have_count(0)
            for held_route, saved_response in held_samples:
                held_route.fulfill(response=saved_response)
            page.unroute(sample_route, hold_samples)
            page.wait_for_timeout(200)
            expect(detail).to_have_count(0)
            assert not database(lambda: BrowserDiscoveryTask.objects.filter(pk=failed_id).exists())
            assert not database(lambda: BrowserDiscoveryRecord.objects.filter(task_id=failed_id).exists())
            assert database(lambda: APISpecification.objects.filter(pk=spec.pk).exists())
            assert database(lambda: APIWorkspace.objects.filter(pk=handoff_workspace.pk).exists())
            page.reload()
            show_task_list()
            expect(task_row("/partial")).to_be_visible(timeout=15000)
            expect(task_row("/delete-failed")).to_have_count(0)
            expect(task_row("/delete-other")).to_have_count(0)
            page.screenshot(path=str(output / "deleted-failures-preserved-workspace.png"), full_page=True)
            page.goto(origin + "/api-testing/workspace")
            expect(page).to_have_url(re.compile(r"/workspace/documents\?workspace_id="), timeout=15000)
            expect(page.locator(".header-actions")).to_contain_text("文档来源工作区")
            assert database(lambda: APITestCase.objects.count()) == 1  # Only the explicit fixture above.
            assert database(lambda: APITestExecution.objects.count()) == 0
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
