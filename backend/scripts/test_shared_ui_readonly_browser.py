"""Read-only shared UI smoke test on disposable SQLite and loopback only.

This deliberately checks the common project, model, legacy knowledge-base and
mail configuration pages without clicking any mutation or connection action.
Every browser API request must be GET/HEAD; Python sockets and browser traffic
outside the local WSGI origin are blocked.
"""
from __future__ import annotations

import json
from pathlib import Path
import socket
import tempfile
import threading
from unittest.mock import patch
from wsgiref.simple_server import make_server

from test_platform_reports_browser import BACKEND, CHROME, bootstrap, loopback_only
from test_project_knowledge_browser import _QuietHandler, _static_or_django


def add_readonly_fixtures(fixture):
    """Pre-seed only temporary rows; UI verification itself remains read-only."""
    from ai_core.models import LLMConfiguration, ModelType
    from django.contrib.auth import get_user_model
    from django.core.files.uploadedfile import SimpleUploadedFile
    from notifications.models import EmailConfig
    from projects.knowledge.models import KnowledgeBaseFile
    from projects.models import Project, ProjectMember, UploadedFile

    user = get_user_model().objects.get(pk=fixture["user_id"])
    user.is_staff = True
    user.save(update_fields=["is_staff"])
    project = Project.objects.get(pk=fixture["project_id"])
    project.name = "共享页面隔离项目"
    project.save(update_fields=["name"])
    # The legacy knowledge list intentionally authorizes via membership rather
    # than creator/owner.  Seed the same read permission a real viewer needs.
    ProjectMember.objects.create(
        project=project, user=user, role="owner", can_edit=True,
        can_delete=True, can_execute_tests=True, can_view_reports=True,
    )
    LLMConfiguration.objects.create(
        created_by=user,
        model_type=ModelType.LLM,
        provider="openai",
        provider_name="隔离模型提供商",
        model_name="shared-ui-offline-model",
        api_key="fixture-only",
        base_url="https://provider.example.test/v1",
        is_active=True,
    )
    uploaded = UploadedFile.objects.create(
        original_name="shared-ui-existing-knowledge.md",
        file=SimpleUploadedFile("shared-ui-existing-knowledge.md", b"# fixture\n"),
        file_size=10,
        file_hash="a" * 64,
        file_type=UploadedFile.FileType.MD,
        upload_status=UploadedFile.UploadStatus.UPLOADED,
        uploaded_by=user,
        project=project,
    )
    KnowledgeBaseFile.objects.create(
        project=project,
        uploaded_file=uploaded,
        uploaded_by=user,
        status=KnowledgeBaseFile.RAGIngestionStatus.COMPLETED,
    )
    EmailConfig.objects.create(
        name="共享邮件隔离配置",
        smtp_server="smtp.example.test",
        port=465,
        sender_email="fixture@example.test",
        smtp_password="fixture-only",
        use_ssl=True,
        is_active=True,
    )
    fixture["project"] = {"id": project.id, "name": project.name, "project_type": project.project_type}
    return fixture


def verify_browser(origin, fixture, output):
    from playwright.sync_api import expect, sync_playwright

    errors, api_mutations = [], []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(executable_path=str(CHROME), headless=True)
        context = browser.new_context(viewport={"width": 1440, "height": 1050})
        context.route("**/*", lambda route: route.continue_() if route.request.url.startswith(origin + "/") else route.abort())
        context.add_init_script(
            "localStorage.setItem('auth-store', JSON.stringify(" + json.dumps({
                "accessToken": fixture["token"], "refreshToken": None,
                "user": {"id": fixture["user_id"], "username": "reports-offline", "is_staff": True},
            }) + "));localStorage.setItem('project-store', JSON.stringify(" + json.dumps({"currentProject": fixture["project"]}) + "));"
        )
        page = context.new_page()
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.on("request", lambda request: api_mutations.append((request.method, request.url))
                if "/api/" in request.url and request.method not in {"GET", "HEAD"} else None)
        try:
            page.goto(origin + "/project/project-list")
            expect(page.get_by_role("heading", name="项目管理列表")).to_be_visible(timeout=20000)
            expect(page.get_by_text("共享页面隔离项目", exact=True)).to_be_visible()
            page.screenshot(path=str(output / "project-list-readonly.png"), full_page=True, animations="disabled")

            page.goto(origin + "/ai-config/llm")
            expect(page.get_by_role("heading", name="LLM模型配置")).to_be_visible(timeout=15000)
            expect(page.get_by_text("shared-ui-offline-model", exact=True)).to_be_visible()
            expect(page.get_by_text("隔离模型提供商", exact=True)).to_be_visible()
            page.screenshot(path=str(output / "llm-config-readonly.png"), full_page=True, animations="disabled")

            page.goto(origin + "/project/knowledge-base")
            expect(page.get_by_role("heading", name="知识库管理")).to_be_visible(timeout=15000)
            expect(page.get_by_text("shared-ui-existing-knowledge.md", exact=True)).to_be_visible()
            page.screenshot(path=str(output / "knowledge-readonly.png"), full_page=True, animations="disabled")

            page.goto(origin + "/settings/email-config")
            expect(page.get_by_role("heading", name="邮件服务配置")).to_be_visible(timeout=15000)
            row = page.get_by_role("row").filter(has_text="共享邮件隔离配置")
            expect(row).to_contain_text("已配置")
            expect(row).to_contain_text("当前用于发送")
            page.screenshot(path=str(output / "email-config-readonly.png"), full_page=True, animations="disabled")
            assert not errors, errors
            assert not api_mutations, f"Read-only smoke made API mutations: {api_mutations}"
        except Exception:
            page.screenshot(path=str(output / "failure.png"), full_page=True, animations="disabled")
            raise
        finally:
            context.close()
            browser.close()


def main():
    output = BACKEND / "logs" / "shared-ui-readonly-browser"
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="automation-shared-ui-readonly-") as temp, patch.object(
        socket.socket, "connect", loopback_only(socket.socket.connect)
    ), patch.object(socket.socket, "connect_ex", loopback_only(socket.socket.connect_ex)):
        fixture = add_readonly_fixtures(bootstrap(Path(temp)))
        from django.core.wsgi import get_wsgi_application

        server = make_server("127.0.0.1", 0, _static_or_django(get_wsgi_application()), handler_class=_QuietHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            verify_browser(f"http://127.0.0.1:{server.server_port}", fixture, output)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)
    print("PASS: isolated read-only shared project/model/knowledge/email UI smoke")


if __name__ == "__main__":
    main()
