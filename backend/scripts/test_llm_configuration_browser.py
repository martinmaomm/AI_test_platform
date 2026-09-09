"""LLM configuration acceptance with real Vue/Django and a simulated SDK transport.

Build frontend first. Uses disposable SQLite, fixture-only credentials and no
external connections; the provider's real availability is not tested here.
Run: backend/.venv/bin/python backend/scripts/test_llm_configuration_browser.py
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import socket
import sys
import tempfile
import threading
from unittest.mock import patch
from wsgiref.simple_server import make_server

from test_platform_reports_browser import CHROME, loopback_only
from test_project_knowledge_browser import _NoMigrations, _QuietHandler, _static_or_django

BACKEND = Path(__file__).resolve().parent.parent


def bootstrap(root):
    sys.path[:0] = [str(BACKEND), str(BACKEND / "apps")]
    os.environ["DJANGO_SETTINGS_MODULE"] = "config.settings"
    os.environ["ANONYMIZED_TELEMETRY"] = "false"
    os.environ["MCP_USE_ANONYMIZED_TELEMETRY"] = "false"
    os.environ["OFFLINE_TEST_NETWORK"] = "blocked"
    from config import settings as config

    config.DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": str(root / "llm.sqlite3")}}
    config.SECRET_KEY = "llm-configuration-browser-isolated-test-key"
    config.SIMPLE_JWT = {**config.SIMPLE_JWT, "SIGNING_KEY": config.SECRET_KEY}
    config.MIGRATION_MODULES = _NoMigrations()
    config.CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
    config.CELERY_BROKER_URL = "memory://"
    config.CELERY_RESULT_BACKEND = "cache+memory://"
    config.PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
    config.ALLOWED_HOSTS = ["127.0.0.1", "localhost", "testserver"]
    config.MEDIA_ROOT = str(root / "media")
    config.LOGGING = {"version": 1, "disable_existing_loggers": True}
    import django
    django.setup()
    from django.contrib.auth import get_user_model
    from django.core.management import call_command
    from rest_framework_simplejwt.tokens import AccessToken
    from ai_core.models import LLMConfiguration

    call_command("migrate", run_syncdb=True, verbosity=0)
    user = get_user_model().objects.create_user(username="llm-browser-offline", password="fixture-only")
    model = LLMConfiguration.objects.create(
        created_by=user, provider="openai", provider_name="本地模拟供应商",
        model_type="llm", model_name="fixture-chat-model", api_key="fixture-only",
        base_url="https://provider.example.test/v1/chat/completions", is_active=True,
    )
    return {"id": model.pk, "user_id": user.pk, "token": str(AccessToken.for_user(user))}


def database(call):
    with ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(call).result()


def verify(origin, fixture, upstream, output):
    from playwright.sync_api import expect, sync_playwright
    from ai_core.models import LLMConfiguration

    errors = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(executable_path=str(CHROME), headless=True)
        context = browser.new_context(viewport={"width": 1440, "height": 1050})
        context.route("**/*", lambda route: route.continue_() if route.request.url.startswith(origin + "/") else route.abort())
        context.add_init_script("localStorage.setItem('auth-store', JSON.stringify(" + json.dumps({
            "accessToken": fixture["token"], "refreshToken": None,
            "user": {"id": fixture["user_id"], "username": "llm-browser-offline"},
        }) + "));")
        page = context.new_page()
        page.on("pageerror", lambda error: errors.append(str(error)))
        try:
            page.goto(origin + "/ai-config/llm")
            expect(page.get_by_role("heading", name="LLM模型配置")).to_be_visible(timeout=20000)
            row = page.locator(".el-table__row").filter(has_text="fixture-chat-model")
            expect(row).to_contain_text("接口类型：OpenAI兼容接口")
            expect(row).to_contain_text("本地模拟供应商")

            # Exercise the complete view -> model manager -> actual SDK chain.
            # Existing full endpoint must be corrected in-memory before saving.
            with page.expect_response(lambda r: r.url.endswith("/llm-configs/test_connection/")) as result:
                row.get_by_role("button", name="测试连接", exact=True).click()
            assert result.value.status == 200, result.value.text()
            success_notice = page.locator(".el-notification").filter(has_text="连接测试成功")
            expect(success_notice).to_be_visible()
            success_notice.locator(".el-notification__closeBtn").click()
            expect(success_notice).not_to_be_visible()
            assert upstream["requests"][-1] == ("/v1/chat/completions", True)

            row.get_by_role("button", name="编辑", exact=True).click()
            dialog = page.get_by_role("dialog", name="编辑AI模型配置")
            expect(dialog.get_by_label("API密钥", exact=True)).to_have_value("")
            expect(dialog.get_by_label("API密钥", exact=True)).to_have_attribute("placeholder", "留空则保留已有密钥")
            dialog.get_by_label("模型提供商", exact=True).fill("改名后的模拟供应商")
            with page.expect_response(lambda r: r.request.method == "PUT" and r.url.endswith(f"/llm-configs/{fixture['id']}/")) as updated:
                dialog.get_by_role("button", name="保存", exact=True).click()
            assert updated.value.status == 200, updated.value.text()
            expect(dialog).not_to_be_visible()
            expect(row).to_contain_text("改名后的模拟供应商")
            expect(row).to_contain_text("接口类型：OpenAI兼容接口")
            saved = database(lambda: LLMConfiguration.objects.get(pk=fixture["id"]))
            assert saved.api_key == "fixture-only"
            assert saved.base_url == "https://provider.example.test/v1"

            page.get_by_role("button", name="添加AI模型配置", exact=True).click()
            dialog = page.get_by_role("dialog", name="添加AI模型配置")
            expect(dialog.get_by_label("API密钥", exact=True)).to_have_attribute("placeholder", "请输入API密钥")
            interface = dialog.locator(".el-form-item").filter(has=page.get_by_text("模型接口类型", exact=True))
            expect(interface).to_contain_text("OpenAI兼容接口")
            interface.locator(".el-select").click()
            expect(page.get_by_role("option")).to_have_count(3)
            expect(page.get_by_role("option")).to_have_text(["OpenAI兼容接口", "DeepSeek原生接口", "Ollama本地接口"])
            page.get_by_role("option", name="OpenAI兼容接口", exact=True).click()
            expect(dialog).to_contain_text("/chat/completions")
            dialog.get_by_label("API地址", exact=True).fill("https://second.example.test/v1/chat/completions")
            dialog.get_by_label("模型名称", exact=True).fill("fixture-second-model")
            dialog.get_by_label("API密钥", exact=True).fill("fixture-second-key")
            with page.expect_response(lambda r: r.request.method == "POST" and r.url.endswith("/llm-configs/")) as created:
                dialog.get_by_role("button", name="保存", exact=True).click()
            assert created.value.status == 200, created.value.text()
            assert created.value.json()["data"]["base_url"] == "https://second.example.test/v1"
            expect(dialog).not_to_be_visible()
            page.screenshot(path=str(output / "interface-and-provider.png"), full_page=True)

            upstream["blocked"] = True
            with page.expect_response(lambda r: r.url.endswith("/llm-configs/test_connection/")) as failed:
                row.get_by_role("button", name="测试连接", exact=True).click()
            assert failed.value.status == 400, failed.value.text()
            notice = page.locator(".el-notification").filter(has_text="Cloudflare")
            expect(notice).to_be_visible()
            expect(notice).to_have_css("transform", "none")
            expect(notice).to_be_in_viewport()
            expect(notice).to_contain_text("403")
            assert "<html>" not in notice.inner_text()
            page.screenshot(path=str(output / "readable-gateway-error.png"), full_page=True)
            assert not errors, errors
        except Exception:
            page.screenshot(path=str(output / "failure.png"), full_page=True)
            raise
        finally:
            browser.close()


def main():
    output = BACKEND / "logs" / "llm-configuration-browser"
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="automation-llm-browser-") as temp, patch.object(
        socket.socket, "connect", loopback_only(socket.socket.connect),
    ), patch.object(socket.socket, "connect_ex", loopback_only(socket.socket.connect_ex)):
        fixture = bootstrap(Path(temp))
        import httpx
        from django.core.wsgi import get_wsgi_application
        from ai_core import model_manager

        upstream = {"blocked": False, "requests": []}

        def transport(request):
            body = json.loads(request.content)
            upstream["requests"].append((request.url.path, body.get("stream")))
            if upstream["blocked"]:
                return httpx.Response(403, headers={"content-type": "text/html"}, text="<html><title>Attention Required! | Cloudflare</title></html>")
            chunk = {"id": "fixture-chat", "object": "chat.completion.chunk", "model": "fixture-chat-model", "choices": [
                {"index": 0, "delta": {"role": "assistant", "content": "你好，隔离测试通过。"}, "finish_reason": "stop"},
            ]}
            return httpx.Response(200, headers={"content-type": "text/event-stream"}, text="data: " + json.dumps(chunk) + "\n\ndata: [DONE]\n\n")

        original_init = model_manager.init_chat_model
        with httpx.Client(transport=httpx.MockTransport(transport)) as client:
            def initialize(**kwargs):
                return original_init(**kwargs, http_client=client)

            with patch.object(model_manager, "init_chat_model", side_effect=initialize):
                server = make_server("127.0.0.1", 0, _static_or_django(get_wsgi_application()), handler_class=_QuietHandler)
                thread = threading.Thread(target=server.serve_forever, daemon=True)
                thread.start()
                try:
                    verify(f"http://127.0.0.1:{server.server_port}", fixture, upstream, output)
                finally:
                    server.shutdown()
                    thread.join(timeout=5)
                    server.server_close()
    print(f"PASS: isolated Vue/Django/SDK LLM configuration acceptance; evidence: {output}")


if __name__ == "__main__":
    main()
