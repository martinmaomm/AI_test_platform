"""Email notification UI/API acceptance using disposable SQLite and mail outbox.

Build frontend first. Python/browser sockets may only reach loopback.
SMTP protocol/TLS acceptance is covered separately by notifications.test_smtp_protocol.
No real NAS database, mailbox, Redis, LLM or target website is contacted.
"""
from __future__ import annotations

import json
from pathlib import Path
import smtplib
import socket
import tempfile
import threading
from unittest.mock import patch
from wsgiref.simple_server import make_server

from test_platform_reports_browser import BACKEND, CHROME, bootstrap, loopback_only
from test_project_knowledge_browser import _QuietHandler, _static_or_django


def verify_ui(origin, fixture, output, connection_factory):
    from django.core import mail
    from playwright.sync_api import expect, sync_playwright

    errors = []
    created_ids = {}
    with sync_playwright() as pw:
        browser = pw.chromium.launch(executable_path=str(CHROME), headless=True)
        context = browser.new_context(viewport={"width": 1500, "height": 1080})
        context.set_default_timeout(10000)
        context.route("**/*", lambda route: route.continue_()
                      if route.request.url.startswith(origin + "/") else route.abort())
        context.add_init_script(
            "localStorage.setItem('auth-store', JSON.stringify(" + json.dumps({
                "accessToken": fixture["token"], "refreshToken": None,
                "user": {"id": fixture["user_id"], "username": "reports-offline", "is_staff": True},
            }) + "));"
        )
        page = context.new_page()
        page.on("pageerror", lambda error: errors.append(str(error)))

        def dialog():
            return page.locator(".el-dialog:visible").last

        def response_for(method, path):
            return page.expect_response(lambda r: r.request.method == method and r.url.endswith(path))

        def set_project(project_id, name, kind):
            page.evaluate("(project) => localStorage.setItem('project-store', JSON.stringify({currentProject: project}))",
                          {"id": project_id, "name": name, "project_type": kind})

        def message_error(text):
            expect(page.locator(".el-message--error").last).to_contain_text(text)
            expect(page.locator(".el-message--error").last).to_have_css("opacity", "1")

        def save_receiver(name, addresses, expected=201):
            page.get_by_role("button", name="新建邮件接收组", exact=True).click()
            d = dialog()
            expect(d.locator(".el-select")).to_have_count(0)
            expect(d).not_to_contain_text("Webhook")
            d.get_by_placeholder("如：测试结果接收组").fill(name)
            d.get_by_placeholder("如 qa@example.com；多个邮箱可用逗号、分号或换行分隔").fill(addresses)
            with response_for("POST", receiver_path) as result:
                d.get_by_role("button", name="创建", exact=True).click()
            assert result.value.status == expected, result.value.text()
            if expected == 201:
                expect(d).not_to_be_visible()
                return result.value.json()["id"]
            expect(d).to_be_visible()
            message_error("邮箱")
            d.get_by_role("button", name="取消", exact=True).click()

        def send_test(row, expected=200):
            row.get_by_role("button", name="发送测试邮件", exact=True).click()
            confirm = page.locator(".el-message-box")
            expect(confirm).to_contain_text("发送一封测试邮件")
            with page.expect_response(lambda r: r.request.method == "POST" and r.url.endswith("/test/")) as result:
                confirm.get_by_role("button", name="确认发送", exact=True).click()
            assert result.value.status == expected, result.value.text()
            expect(confirm).not_to_be_visible()
            return result.value

        try:
            page.goto(origin + "/settings")
            expect(page.get_by_text("邮件服务配置", exact=True)).to_be_visible()
            expect(page.get_by_text("消息通道配置", exact=True)).to_have_count(0)
            page.get_by_text("邮件服务配置", exact=True).click()
            expect(page.get_by_text("暂无配置，点击「新建配置」添加", exact=True)).to_be_visible()
            page.get_by_role("button", name="新建配置", exact=True).click()
            d = dialog()
            d.get_by_role("button", name="创建", exact=True).click()
            expect(d.get_by_text("请输入 SMTP 授权码", exact=True)).to_be_visible()
            d.get_by_placeholder("如：公司邮件服务器").fill("隔离邮件服务")
            d.get_by_placeholder("如 smtp.qq.com、smtp.163.com").fill("https://invalid.test/path")
            d.get_by_placeholder("发件人邮箱地址", exact=True).fill("sender@example.test")
            d.get_by_placeholder("请输入 SMTP 授权码（非登录密码）").fill("isolated-smtp-secret")
            expect(d.get_by_role("radio", name="SSL/TLS（常用 465）", exact=True)).to_be_checked()
            with response_for("POST", "/api/v1/notifications/email-configs/") as invalid:
                d.get_by_role("button", name="创建", exact=True).click()
            assert invalid.value.status == 400, invalid.value.text()
            message_error("SMTP")
            d.get_by_placeholder("如 smtp.qq.com、smtp.163.com").fill("smtp.example.test")
            with response_for("POST", "/api/v1/notifications/email-configs/") as created:
                d.get_by_role("button", name="创建", exact=True).click()
            assert created.value.status == 201, created.value.text()
            config_data = created.value.json()
            assert "smtp_password" not in config_data
            assert config_data["has_password"] is True
            assert config_data["is_effective"] is True
            created_ids["config_id"] = config_data["id"]
            expect(d).not_to_be_visible()
            smtp_row = page.get_by_role("row").filter(has_text="隔离邮件服务")
            expect(smtp_row).to_contain_text("当前用于发送")
            expect(smtp_row).to_contain_text("已配置")
            assert connection_factory.call_count == 0, "Saving must not connect or send"
            smtp_row.get_by_role("button", name="编辑", exact=True).click()
            d = dialog()
            expect(d.get_by_placeholder("已配置，如需修改请填入新授权码；不修改请留空")).to_have_value("")
            d.get_by_placeholder("如：公司邮件服务器").fill("隔离邮件服务更新")
            d.get_by_text("STARTTLS（常用 587）", exact=True).click()
            expect(d.get_by_role("radio", name="STARTTLS（常用 587）", exact=True)).to_be_checked()
            d.get_by_role("spinbutton").fill("587")
            with response_for("PUT", f'/api/v1/notifications/email-configs/{created_ids["config_id"]}/') as updated:
                d.get_by_role("button", name="更新", exact=True).click()
            assert updated.value.status == 200, updated.value.text()
            assert updated.value.json()["use_ssl"] is False
            assert updated.value.json()["has_password"] is True
            expect(d).not_to_be_visible()
            smtp_row = page.get_by_role("row").filter(has_text="隔离邮件服务更新")
            with response_for("POST", f'/api/v1/notifications/email-configs/{created_ids["config_id"]}/test/') as connected:
                smtp_row.get_by_role("button", name="测试连接", exact=True).click()
            assert connected.value.status == 200, connected.value.text()
            expect(page.locator(".el-message--success").last).to_contain_text("尚未发送测试邮件")
            expect(page.locator(".el-message--success").last).to_have_css("opacity", "1")
            expect(page.locator(".el-message--error")).to_have_count(0)
            assert len(mail.outbox) == 0
            page.screenshot(path=str(output / "email-smtp-settings.png"), full_page=True)

            set_project(fixture["project_id"], "UI 邮件隔离项目", "web")
            page.goto(origin + "/web-testing/notification-receivers")
            receiver_path = f'/api/v1/projects/{fixture["project_id"]}/notification-receivers/'
            created_ids["web_receiver_id"] = save_receiver("UI 邮件接收组", "qa@example.test;ops@example.test\nqa@example.test")
            row = page.get_by_role("row").filter(has_text="UI 邮件接收组")
            expect(row).to_contain_text("qa@example.test")
            expect(row).to_contain_text("ops@example.test")
            row.get_by_role("button", name="编辑", exact=True).click()
            d = dialog()
            expect(d.get_by_placeholder("如 qa@example.com；多个邮箱可用逗号、分号或换行分隔")).to_have_value("qa@example.test,ops@example.test")
            d.get_by_placeholder("如：测试结果接收组").fill("UI 邮件更新组")
            with response_for("PATCH", receiver_path + str(created_ids["web_receiver_id"]) + "/") as updated:
                d.get_by_role("button", name="更新", exact=True).click()
            assert updated.value.status == 200, updated.value.text()
            expect(d).not_to_be_visible()
            row = page.get_by_role("row").filter(has_text="UI 邮件更新组")
            before = len(mail.outbox)
            send_test(row)
            expect(page.locator(".el-message--success").last).to_contain_text("SMTP 已接受")
            expect(page.locator(".el-message--success").last).to_have_css("opacity", "1")
            assert len(mail.outbox) == before + 1
            assert mail.outbox[-1].to == ["qa@example.test", "ops@example.test"]
            page.screenshot(path=str(output / "email-ui-receivers.png"), full_page=True)
            expect(page.locator(".el-message--success")).to_have_count(0, timeout=10000)

            connection_factory.side_effect = smtplib.SMTPAuthenticationError(535, b"fixture-secret-must-not-leak")
            refused = send_test(row, 400)
            message_error("SMTP 认证失败")
            assert "fixture-secret-must-not-leak" not in refused.text()
            assert len(mail.outbox) == before + 1
            page.screenshot(path=str(output / "email-auth-failure.png"), full_page=True)
            connection_factory.side_effect = None

            save_receiver("不能保存的收件组", "not-an-email", expected=400)
            expect(page.get_by_role("row").filter(has_text="不能保存的收件组")).to_have_count(0)
            row.get_by_role("button", name="编辑", exact=True).click()
            d = dialog()
            d.locator(".el-switch").click()
            with page.expect_response(lambda r: r.request.method == "PATCH" and receiver_path in r.url):
                d.get_by_role("button", name="更新", exact=True).click()
            expect(d).not_to_be_visible()
            expect(row.get_by_role("button", name="发送测试邮件")).to_be_disabled()
            row.get_by_role("button", name="编辑", exact=True).click()
            d = dialog()
            d.locator(".el-switch").click()
            with page.expect_response(lambda r: r.request.method == "PATCH" and receiver_path in r.url):
                d.get_by_role("button", name="更新", exact=True).click()
            expect(d).not_to_be_visible()

            page.goto(origin + "/web-testing/scheduled-tasks")
            page.get_by_role("button", name="创建任务", exact=True).click()
            d = dialog()
            d.get_by_placeholder("请输入任务名称").fill("邮件计划隔离验收")
            d.locator(".el-form-item").filter(has_text="测试套件").first.locator(".el-select").click()
            page.get_by_role("option", name="邮件验收套件", exact=False).click()
            d.get_by_placeholder("请输入任务名称").click()
            d.get_by_placeholder("如: 0 9 * * 1-5 (工作日9点执行)").fill("0 9 * * *")
            d.get_by_text("暂停", exact=True).click()
            expect(d.get_by_role("radio", name="暂停", exact=True)).to_be_checked()
            d.locator(".el-form-item").filter(has_text="邮件接收组").first.locator(".el-select").click()
            expect(page.get_by_role("option", name="已停用邮件组", exact=True)).to_have_count(0)
            expect(page.get_by_role("option", name="旧钉钉组", exact=True)).to_have_count(0)
            page.get_by_role("option", name="UI 邮件更新组", exact=True).click()
            d.get_by_placeholder("请输入任务名称").click()
            page.screenshot(path=str(output / "email-scheduled-task.png"), full_page=True)
            with response_for("POST", f'/api/v1/projects/{fixture["project_id"]}/scheduled-tasks/tasks/') as scheduled:
                d.get_by_role("button", name="创建", exact=True).click()
            assert scheduled.value.status == 201, scheduled.value.text()
            assert scheduled.value.json()["name"] == "邮件计划隔离验收"
            expect(d).not_to_be_visible()
            expect(page.get_by_role("row").filter(has_text="邮件计划隔离验收")).to_contain_text("qa")

            set_project(fixture["api_project_id"], "API 邮件隔离项目", "api")
            page.goto(origin + "/api-testing/notification-receivers")
            expect(page.get_by_role("row").filter(has_text="UI 邮件更新组")).to_have_count(0)
            receiver_path = f'/api/v1/projects/{fixture["api_project_id"]}/notification-receivers/'
            created_ids["api_receiver_id"] = save_receiver("API 邮件接收组", "api-qa@example.test")
            row = page.get_by_role("row").filter(has_text="API 邮件接收组")
            send_test(row)
            expect(page.locator(".el-message--success").last).to_contain_text("SMTP 已接受")
            expect(page.locator(".el-message--success").last).to_have_css("opacity", "1")
            assert mail.outbox[-1].to == ["api-qa@example.test"]
            page.screenshot(path=str(output / "email-api-receivers.png"), full_page=True)
            row.get_by_role("button", name="删除", exact=True).click()
            with response_for("DELETE", receiver_path + str(created_ids["api_receiver_id"]) + "/") as deleted:
                page.locator(".el-message-box").get_by_role("button", name="确定删除").click()
            assert deleted.value.status == 204
            expect(page.get_by_role("row").filter(has_text="API 邮件接收组")).to_have_count(0)

            page.goto(origin + "/settings/channel-config")
            expect(page.get_by_role("heading", name="邮件服务配置")).to_be_visible()
            assert "/settings/email-config" in page.url
            # Delete a second, inactive SMTP configuration through the UI while
            # retaining the active one for the completion-notification test.
            page.get_by_role("button", name="新建配置", exact=True).click()
            d = dialog()
            d.get_by_placeholder("如：公司邮件服务器").fill("待删除的邮件配置")
            d.get_by_placeholder("如 smtp.qq.com、smtp.163.com").fill("smtp.example.test")
            d.get_by_placeholder("发件人邮箱地址", exact=True).fill("unused@example.test")
            d.get_by_placeholder("请输入 SMTP 授权码（非登录密码）").fill("unused-fixture-secret")
            d.locator(".el-switch").click()
            with response_for("POST", "/api/v1/notifications/email-configs/") as unused:
                d.get_by_role("button", name="创建", exact=True).click()
            assert unused.value.status == 201, unused.value.text()
            assert unused.value.json()["is_effective"] is False
            expect(d).not_to_be_visible()
            page.get_by_role("row").filter(has_text="待删除的邮件配置").get_by_role("button", name="删除", exact=True).click()
            with response_for("DELETE", f'/api/v1/notifications/email-configs/{unused.value.json()["id"]}/') as deleted_config:
                page.locator(".el-message-box").get_by_role("button", name="确定删除").click()
            assert deleted_config.value.status == 204
            expect(page.get_by_role("row").filter(has_text="待删除的邮件配置")).to_have_count(0)
            expect(page.get_by_role("row").filter(has_text="隔离邮件服务更新")).to_contain_text("当前用于发送")
            assert not errors, errors
        except Exception:
            page.screenshot(path=str(output / "failure.png"), full_page=True, animations="disabled")
            raise
        finally:
            context.close()
            browser.close()
    return created_ids


def main():
    output = BACKEND / "logs" / "notifications-browser"
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="automation-notifications-") as root, patch.object(
        socket.socket, "connect", loopback_only(socket.socket.connect)
    ), patch.object(socket.socket, "connect_ex", loopback_only(socket.socket.connect_ex)):
        fixture = bootstrap(Path(root))
        from django.contrib.auth import get_user_model
        from django.core import mail
        from django.core.wsgi import get_wsgi_application
        from django.test import override_settings
        from notifications.models import EmailConfig, NotificationChannel, NotificationReceiver, get_builtin_email_channel
        from notifications.services import trigger_notification
        from projects.models import Project
        from scheduled_tasks.models import ScheduledTask, TaskExecutionLog
        from web_testing.models import WebUITestCase, WebUITestSuite, WebUITestSuiteCase
        user = get_user_model().objects.get(pk=fixture["user_id"])
        user.is_staff = True
        user.save(update_fields=["is_staff"])
        project = Project.objects.get(pk=fixture["project_id"])
        email_channel = get_builtin_email_channel()
        NotificationReceiver.objects.create(project=project, channel=email_channel, name="已停用邮件组", target_address="inactive@example.test", is_active=False)
        retired = NotificationChannel.objects.create(channel_code="dingtalk", channel_name="钉钉", is_active=False)
        NotificationReceiver.objects.create(project=project, channel=retired, name="旧钉钉组", webhook_url="https://example.test/retired", is_active=False)
        case = WebUITestCase.objects.create(title="邮件测试用例", project=project, user=user,
                                            test_script_content="async def test_example(page):\n    pass", script_status="ready")
        suite = WebUITestSuite.objects.create(project=project, user=user, name="邮件验收套件")
        WebUITestSuiteCase.objects.create(suite=suite, test_case=case, order=1)
        connection = mail.get_connection(backend="django.core.mail.backends.locmem.EmailBackend")
        mail.outbox.clear()
        server = make_server("127.0.0.1", 0, _static_or_django(get_wsgi_application()), handler_class=_QuietHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        with patch("notifications.delivery._get_smtp_connection", return_value=connection) as connection_factory:
            thread.start()
            try:
                ids = verify_ui(f"http://127.0.0.1:{server.server_port}", fixture, output, connection_factory)
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)
            config = EmailConfig.objects.get(pk=ids["config_id"])
            assert config.smtp_password == "isolated-smtp-secret", "Blank edit must preserve authorization code"
            assert config.port == 587 and config.use_ssl is False
            receiver = NotificationReceiver.objects.get(pk=ids["web_receiver_id"])
            assert receiver.target_address == "qa@example.test,ops@example.test" and receiver.is_active
            assert not NotificationReceiver.objects.filter(pk=ids["api_receiver_id"]).exists()
            task = ScheduledTask.objects.get(project=project, name="邮件计划隔离验收")
            assert list(task.notice_targets.values_list("id", flat=True)) == [receiver.pk]
            # Exercise final notification against actual report fixtures, not target websites.
            log = TaskExecutionLog.objects.get(pk=fixture["log_id"])
            log.task = task
            log.save(update_fields=["task"])
            with override_settings(FRONTEND_BASE_URL="http://127.0.0.1:5173"):
                count = len(mail.outbox)
                assert trigger_notification(task.pk, log, {}) is True
                assert len(mail.outbox) == count + 1
                assert mail.outbox[-1].alternatives
                assert f"/reports/detail/{log.pk}" in mail.outbox[-1].body
                assert "qa@example.test" in mail.outbox[-1].to
            config.delete()
            assert not EmailConfig.objects.exists()
    print("PASS: email SMTP forms + UI/API receivers + scheduled selection/report delivery; isolated outbox only")


if __name__ == "__main__":
    main()
