"""Notification UI/API integration with disposable SQLite and mocked delivery.

Build frontend first. External browser and Python sockets are denied; no real
Webhook, SMTP, NAS, Redis or model is used.
"""

from __future__ import annotations

import json
from pathlib import Path
import socket
import tempfile
import threading
from unittest.mock import Mock, patch
from wsgiref.simple_server import make_server

from test_platform_reports_browser import BACKEND, CHROME, bootstrap, loopback_only
from test_project_knowledge_browser import _QuietHandler, _static_or_django


WEBHOOK = "https://oapi.dingtalk.com/robot/send?access_token=isolated-fixture-only"


def verify_ui(origin, fixture, output, post):
    from playwright.sync_api import expect, sync_playwright

    errors = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(executable_path=str(CHROME), headless=True)
        context = browser.new_context(viewport={"width": 1500, "height": 1000})
        context.route(
            "**/*",
            lambda route: (
                route.continue_()
                if route.request.url.startswith(origin + "/")
                else route.abort()
            ),
        )
        context.add_init_script(
            "localStorage.setItem('auth-store', JSON.stringify("
            + json.dumps(
                {
                    "accessToken": fixture["token"],
                    "refreshToken": None,
                    "user": {"id": fixture["user_id"], "username": "reports-offline"},
                }
            )
            + "));localStorage.setItem('project-store', JSON.stringify("
            + json.dumps({"currentProject": fixture["project"]})
            + "));"
        )
        page = context.new_page()
        page.on("pageerror", lambda error: errors.append(str(error)))
        base = f'/api/v1/projects/{fixture["project_id"]}/notification-receivers/'
        try:
            page.goto(origin + "/web-testing/notification-receivers")
            page.get_by_role("button", name="新建消息接收对象", exact=True).click()
            dialog = page.get_by_role("dialog")
            dialog.locator(".el-select").click()
            page.get_by_role("option", name="钉钉", exact=True).click()
            dialog.get_by_placeholder("如：研发组钉钉群").fill("通知隔离验收")
            dialog.get_by_placeholder("请输入 Webhook 地址").fill(WEBHOOK)
            with page.expect_response(
                lambda r: r.request.method == "POST" and r.url.endswith(base)
            ) as created:
                dialog.get_by_role("button", name="创建", exact=True).click()
            assert created.value.status == 201, created.value.text()
            expect(dialog).not_to_be_visible()
            row = page.get_by_role("row").filter(has_text="通知隔离验收")
            expect(row).to_be_visible()
            assert post.call_count == 0, "Saving must not send notifications"

            row.get_by_role("button", name="编辑", exact=True).click()
            dialog.get_by_placeholder("如：研发组钉钉群").fill("通知重命名验收")
            with page.expect_response(
                lambda r: r.request.method == "PATCH" and base in r.url
            ) as updated:
                dialog.get_by_role("button", name="更新", exact=True).click()
            assert updated.value.status == 200, updated.value.text()
            expect(dialog).not_to_be_visible()
            row = page.get_by_role("row").filter(has_text="通知重命名验收")
            expect(row).to_be_visible()

            post.return_value = Mock(status_code=200)
            post.return_value.json.return_value = {"errcode": 0, "errmsg": "ok"}
            with page.expect_response(
                lambda r: r.request.method == "POST" and r.url.endswith("/test/")
            ) as test_ok:
                row.get_by_role("button", name="测试", exact=True).click()
            assert test_ok.value.status == 200, test_ok.value.text()
            success_message = page.locator(".el-message--success").filter(
                has_text="测试消息已发送"
            )
            expect(success_message).to_be_visible()
            expect(success_message).to_have_css("opacity", "1")
            assert post.call_count == 1
            assert post.call_args.kwargs["allow_redirects"] is False
            page.screenshot(
                path=str(output / "notification-success.png"),
                full_page=True,
                animations="allow",
            )

            post.return_value.json.return_value = {
                "errcode": 310000,
                "errmsg": "fixture-secret-must-not-leak",
            }
            with page.expect_response(
                lambda r: r.request.method == "POST" and r.url.endswith("/test/")
            ) as rejected:
                row.get_by_role("button", name="测试", exact=True).click()
            assert rejected.value.status == 400, rejected.value.text()
            rejected_message = page.locator(".el-message--error").last
            expect(rejected_message).to_contain_text(
                "钉钉渠道验证失败，请检查关键词、签名或 IP 白名单"
            )
            expect(rejected_message).to_be_visible()
            expect(rejected_message).to_have_css("opacity", "1")
            assert "fixture-secret-must-not-leak" not in rejected.value.text()
            assert post.call_count == 2
            assert "/login" not in page.url
            page.screenshot(
                path=str(output / "notification-rejected.png"),
                full_page=True,
                animations="allow",
            )

            page.get_by_role("button", name="新建消息接收对象", exact=True).click()
            dialog.get_by_placeholder("如：研发组钉钉群").fill("不应保存的通知")
            dialog.get_by_placeholder("请输入 Webhook 地址").fill(
                "https://example.test/not-a-notification"
            )
            with page.expect_response(
                lambda r: r.request.method == "POST" and r.url.endswith(base)
            ) as invalid:
                dialog.get_by_role("button", name="创建", exact=True).click()
            assert invalid.value.status == 400, invalid.value.text()
            expect(dialog).to_be_visible()
            validation_message = page.locator(".el-message--error").last
            expect(validation_message).to_contain_text("官方地址")
            expect(validation_message).to_be_visible()
            expect(validation_message).to_have_css("opacity", "1")
            expect(
                page.get_by_role("row").filter(has_text="不应保存的通知")
            ).to_have_count(0)
            assert post.call_count == 2
            page.screenshot(
                path=str(output / "notification-validation-error.png"),
                full_page=True,
                animations="allow",
            )
            dialog.get_by_role("button", name="取消", exact=True).click()
            page.goto(origin + "/settings/channel-config")
            page.get_by_role("button", name="编辑", exact=True).click()
            channel_dialog = page.get_by_role("dialog")
            channel_dialog.get_by_placeholder("如：钉钉、企业微信、邮件").fill(
                "钉钉验收通道"
            )
            with page.expect_response(
                lambda r: r.request.method == "PATCH"
                and "/notifications/channels/" in r.url
            ) as channel_updated:
                channel_dialog.get_by_role("button", name="更新", exact=True).click()
            assert channel_updated.value.status == 200, channel_updated.value.text()
            expect(channel_dialog).not_to_be_visible()
            expect(page.get_by_text("钉钉验收通道", exact=True)).to_be_visible()
            assert not errors, errors
        except Exception:
            page.screenshot(
                path=str(output / "failure.png"), full_page=True, animations="disabled"
            )
            raise
        finally:
            context.close()
            browser.close()


def main():
    output = BACKEND / "logs" / "notifications-browser"
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix="aits-notifications-"
    ) as root, patch.object(
        socket.socket, "connect", loopback_only(socket.socket.connect)
    ), patch.object(
        socket.socket, "connect_ex", loopback_only(socket.socket.connect_ex)
    ):
        fixture = bootstrap(Path(root))
        from django.contrib.auth import get_user_model
        from django.core.wsgi import get_wsgi_application
        from notifications.models import NotificationChannel, NotificationReceiver
        from projects.models import Project

        user = get_user_model().objects.get(pk=fixture["user_id"])
        user.is_staff = True
        user.save(update_fields=["is_staff"])
        project = Project.objects.get(pk=fixture["project_id"])
        fixture["project"] = {
            "id": project.id,
            "name": project.name,
            "project_type": "web",
        }
        NotificationChannel.objects.create(channel_code="dingtalk", channel_name="钉钉")
        server = make_server(
            "127.0.0.1",
            0,
            _static_or_django(get_wsgi_application()),
            handler_class=_QuietHandler,
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        with patch("notifications.delivery.requests.post") as post:
            thread.start()
            try:
                verify_ui(
                    f"http://127.0.0.1:{server.server_port}", fixture, output, post
                )
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)
        rows = NotificationReceiver.objects.filter(project=project)
        assert rows.count() == 1
        assert rows.get().name == "通知重命名验收"
        assert rows.get().webhook_url == WEBHOOK
    print("PASS: isolated notification UI/API integration (no external delivery)")


if __name__ == "__main__":
    main()
