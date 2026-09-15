"""Email-only notification API tests, isolated from SMTP providers."""

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from projects.models import Project, ProjectMember

from .delivery import NotificationDeliveryError
from .models import EmailConfig, NotificationChannel, NotificationReceiver
from .views import EmailConfigViewSet, NotificationChannelViewSet, NotificationReceiverViewSet


class NotificationApiContractTests(TestCase):
    def setUp(self):
        users = get_user_model()
        self.owner = users.objects.create_user("notification-owner", "owner@example.test", "password")
        self.editor = users.objects.create_user("notification-editor", "editor@example.test", "password")
        self.viewer = users.objects.create_user("notification-viewer", "viewer@example.test", "password")
        self.outsider = users.objects.create_user("notification-outsider", "outsider@example.test", "password")
        self.admin = users.objects.create_user("notification-admin", "admin@example.test", "password", is_staff=True)
        self.project = Project.objects.create(name="Notification project", project_type="web", owner=self.owner, created_by=self.owner)
        self.other_project = Project.objects.create(name="Other project", project_type="api", owner=self.outsider, created_by=self.outsider)
        ProjectMember.objects.create(
            project=self.project, user=self.owner, role="editor", can_edit=True,
            can_delete=True, can_execute_tests=True, can_view_reports=True,
        )
        ProjectMember.objects.create(
            project=self.other_project, user=self.outsider, role="editor", can_edit=True,
            can_delete=True, can_execute_tests=True, can_view_reports=True,
        )
        ProjectMember.objects.create(project=self.project, user=self.editor, role="editor", can_edit=True, can_delete=True, can_execute_tests=True)
        ProjectMember.objects.create(project=self.project, user=self.viewer, role="viewer", can_edit=False, can_delete=False, can_execute_tests=False)
        self.email = NotificationChannel.objects.create(channel_code="email", channel_name="邮件")
        self.legacy = NotificationChannel.objects.create(channel_code="dingtalk", channel_name="钉钉")
        self.legacy_receiver = NotificationReceiver.objects.create(
            project=self.project,
            channel=self.legacy,
            name="Legacy webhook",
            webhook_url="https://legacy.invalid/secret",
        )
        self.factory = APIRequestFactory()

    def request(self, user, method, path, payload=None):
        request = getattr(self.factory, method.lower())(path, payload or {}, format="json")
        force_authenticate(request, user=user)
        return request

    def receiver_view(self, method, *, detail=False):
        action = {"get": "retrieve" if detail else "list", "post": "create", "patch": "partial_update", "delete": "destroy"}[method.lower()]
        return NotificationReceiverViewSet.as_view({method.lower(): action})

    def create_receiver(self, payload=None):
        return self.receiver_view("POST")(
            self.request(
                self.editor,
                "POST",
                "/receivers/",
                payload or {"name": "Release notices", "target_address": "one@example.test"},
            ),
            project_id=self.project.pk,
        )

    def test_create_without_channel_binds_builtin_email_and_hides_webhook_fields(self):
        response = self.create_receiver(
            {"name": "Release notices", "target_address": "one@example.test; ONE@example.test"}
        )

        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data["channel_code"], "email")
        self.assertEqual(response.data["channel_name"], "邮件")
        self.assertTrue(response.data["channel_is_active"])
        self.assertNotIn("channel", response.data)
        self.assertNotIn("webhook_url", response.data)
        receiver = NotificationReceiver.objects.get(pk=response.data["id"])
        self.assertEqual(receiver.channel_id, self.email.pk)
        self.assertEqual(receiver.target_address, "one@example.test")

    def test_explicit_old_channel_or_webhook_input_is_rejected(self):
        cases = (
            {"name": "Old type", "target_address": "one@example.test", "channel_type": "dingtalk"},
            {"name": "Old channel", "target_address": "one@example.test", "channel": self.legacy.pk},
            {"name": "Old webhook", "target_address": "one@example.test", "webhook_url": "https://legacy.invalid"},
        )
        for payload in cases:
            with self.subTest(payload=payload):
                response = self.create_receiver(payload)
                self.assertEqual(response.status_code, 400, response.data)
        self.assertFalse(NotificationReceiver.objects.filter(name__startswith="Old").exists())

    def test_receiver_list_and_channel_list_only_expose_email(self):
        email_receiver = NotificationReceiver.objects.create(
            project=self.project, channel=self.email, name="Email", target_address="one@example.test"
        )
        response = self.receiver_view("GET")(
            self.request(self.owner, "GET", "/receivers/"), project_id=self.project.pk
        )
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual([item["id"] for item in response.data["results"]], [email_receiver.pk])

        channel_view = NotificationChannelViewSet.as_view({"get": "list"})
        channel_response = channel_view(self.request(self.owner, "GET", "/channels/"))
        self.assertEqual(channel_response.status_code, 200, channel_response.data)
        self.assertEqual([item["channel_code"] for item in channel_response.data["results"]], ["email"])
        self.assertEqual(
            channel_view(self.request(self.admin, "POST", "/channels/", {"channel_code": "email"})).status_code,
            405,
        )

    def test_disabled_email_channel_blocks_new_receiver_and_email_test(self):
        existing = NotificationReceiver.objects.create(
            project=self.project, channel=self.email, name="Existing", target_address="one@example.test"
        )
        self.email.is_active = False
        self.email.save(update_fields=["is_active"])

        response = self.create_receiver()
        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn("停用", str(response.data))

        test_view = NotificationReceiverViewSet.as_view({"post": "test_by_id"})
        with patch("notifications.views.send_email") as send_email:
            test_response = test_view(
                self.request(self.editor, "POST", "/receivers/test/"), project_id=self.project.pk, pk=existing.pk
            )
        self.assertEqual(test_response.status_code, 400, test_response.data)
        send_email.assert_not_called()

    def test_disabled_receiver_cannot_send_a_test_email(self):
        receiver = NotificationReceiver.objects.create(
            project=self.project,
            channel=self.email,
            name="Disabled receiver",
            target_address="one@example.test",
            is_active=False,
        )
        view = NotificationReceiverViewSet.as_view({"post": "test_by_id"})
        with patch("notifications.views.send_email") as send_email:
            response = view(
                self.request(self.editor, "POST", "/receivers/test/"),
                project_id=self.project.pk,
                pk=receiver.pk,
            )
        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn("停用", str(response.data))
        send_email.assert_not_called()

    def test_receiver_test_requires_execute_permission_and_distinguishes_mail_acceptance(self):
        receiver = NotificationReceiver.objects.create(
            project=self.project, channel=self.email, name="Existing", target_address="one@example.test"
        )
        view = NotificationReceiverViewSet.as_view({"post": "test_by_id"})
        denied = view(self.request(self.viewer, "POST", "/receivers/test/"), project_id=self.project.pk, pk=receiver.pk)
        self.assertEqual(denied.status_code, 403)

        with patch("notifications.views.send_email") as send_email:
            accepted = view(self.request(self.editor, "POST", "/receivers/test/"), project_id=self.project.pk, pk=receiver.pk)
        self.assertEqual(accepted.status_code, 200, accepted.data)
        self.assertIn("SMTP 服务器已接受", accepted.data["message"])
        self.assertIn("收件箱", accepted.data["message"])
        send_email.assert_called_once()

    def test_no_active_smtp_config_returns_safe_receiver_test_failure(self):
        receiver = NotificationReceiver.objects.create(
            project=self.project, channel=self.email, name="No SMTP", target_address="one@example.test"
        )
        view = NotificationReceiverViewSet.as_view({"post": "test_by_id"})
        response = view(self.request(self.editor, "POST", "/receivers/test/"), project_id=self.project.pk, pk=receiver.pk)
        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn("未配置启用的邮件服务", str(response.data))

    def test_email_config_create_update_masks_secret_and_effective_order_is_stable(self):
        create = EmailConfigViewSet.as_view({"post": "create"})
        payload = {
            "name": "Primary SMTP",
            "smtp_server": "smtp.example.test",
            "port": 465,
            "sender_email": "sender@example.test",
            "smtp_password": "test-only-password",
            "use_ssl": True,
        }
        first = create(self.request(self.admin, "POST", "/email-configs/", payload))
        self.assertEqual(first.status_code, 201, first.data)
        self.assertNotIn("smtp_password", first.data)
        self.assertTrue(first.data["has_password"])
        self.assertTrue(first.data["is_effective"])

        update = EmailConfigViewSet.as_view({"patch": "partial_update"})
        updated = update(self.request(self.admin, "PATCH", "/email-configs/", {"smtp_password": "***", "name": "Updated SMTP"}), pk=first.data["id"])
        self.assertEqual(updated.status_code, 200, updated.data)
        config = EmailConfig.objects.get(pk=first.data["id"])
        self.assertEqual(config.smtp_password, "test-only-password")

        second = EmailConfig.objects.create(**{**payload, "name": "Newer SMTP", "smtp_password": "another-test-password"})
        list_view = EmailConfigViewSet.as_view({"get": "list"})
        listed = list_view(self.request(self.admin, "GET", "/email-configs/"))
        self.assertEqual(listed.status_code, 200, listed.data)
        self.assertEqual(listed.data["results"][0]["id"], second.pk)
        self.assertTrue(listed.data["results"][0]["is_effective"])
        self.assertFalse(next(row for row in listed.data["results"] if row["id"] == config.pk)["is_effective"])

    def test_email_config_rejects_bad_host_port_or_missing_password(self):
        create = EmailConfigViewSet.as_view({"post": "create"})
        payload = {
            "name": "Invalid SMTP",
            "smtp_server": "smtp.example.test",
            "port": 465,
            "sender_email": "sender@example.test",
            "smtp_password": "test-only-password",
        }
        for changes in ({"smtp_server": "https://smtp.example.test"}, {"smtp_server": "bad host"}, {"port": 0}, {"smtp_password": ""}):
            with self.subTest(changes=changes):
                response = create(self.request(self.admin, "POST", "/email-configs/", {**payload, **changes}))
                self.assertEqual(response.status_code, 400, response.data)

    def test_email_config_connection_test_is_admin_only_and_never_claims_delivery(self):
        config = EmailConfig.objects.create(
            name="SMTP", smtp_server="smtp.example.test", sender_email="sender@example.test", smtp_password="test-password"
        )
        view = EmailConfigViewSet.as_view({"post": "test_connection"})
        denied = view(self.request(self.owner, "POST", "/email-configs/test/"), pk=config.pk)
        self.assertEqual(denied.status_code, 403)

        with patch("notifications.views.test_smtp_connection") as connection_test:
            response = view(self.request(self.admin, "POST", "/email-configs/test/"), pk=config.pk)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertIn("连接与认证成功", response.data["detail"])
        self.assertIn("未发送", response.data["detail"])
        connection_test.assert_called_once_with(config)

    def test_receiver_requires_project_edit_access(self):
        response = self.receiver_view("POST")(
            self.request(self.outsider, "POST", "/receivers/", {"name": "Denied", "target_address": "one@example.test"}),
            project_id=self.project.pk,
        )
        self.assertEqual(response.status_code, 404)

    def test_receiver_patch_delete_and_global_detail_keep_project_permissions(self):
        receiver = NotificationReceiver.objects.create(
            project=self.project, channel=self.email, name="Editable", target_address="one@example.test"
        )
        patch_view = self.receiver_view("PATCH", detail=True)
        denied = patch_view(
            self.request(self.viewer, "PATCH", "/receivers/", {"name": "Denied"}),
            project_id=self.project.pk,
            pk=receiver.pk,
        )
        self.assertEqual(denied.status_code, 403)

        mismatched = patch_view(
            self.request(self.editor, "PATCH", "/receivers/?project_id=999", {"name": "Mismatch"}),
            project_id=self.project.pk,
            pk=receiver.pk,
        )
        self.assertEqual(mismatched.status_code, 400, mismatched.data)

        updated = patch_view(
            self.request(self.editor, "PATCH", "/receivers/", {"name": "Updated"}),
            project_id=self.project.pk,
            pk=receiver.pk,
        )
        self.assertEqual(updated.status_code, 200, updated.data)
        receiver.refresh_from_db()
        self.assertEqual(receiver.name, "Updated")

        detail_view = NotificationReceiverViewSet.as_view({"get": "retrieve"})
        global_detail = detail_view(self.request(self.editor, "GET", "/receivers/detail/"), pk=receiver.pk)
        self.assertEqual(global_detail.status_code, 200, global_detail.data)
        self.assertEqual(global_detail.data["project"], self.project.pk)

        delete_view = self.receiver_view("DELETE", detail=True)
        denied_delete = delete_view(
            self.request(self.viewer, "DELETE", "/receivers/"), project_id=self.project.pk, pk=receiver.pk
        )
        self.assertEqual(denied_delete.status_code, 403)
        deleted = delete_view(
            self.request(self.editor, "DELETE", "/receivers/"), project_id=self.project.pk, pk=receiver.pk
        )
        self.assertEqual(deleted.status_code, 204)
        self.assertFalse(NotificationReceiver.objects.filter(pk=receiver.pk).exists())

    def test_receiver_rejects_project_body_or_query_mismatch(self):
        body_mismatch = self.create_receiver(
            {"project": self.other_project.pk, "name": "Wrong project", "target_address": "one@example.test"}
        )
        self.assertEqual(body_mismatch.status_code, 400, body_mismatch.data)
        query_view = self.receiver_view("POST")
        query_mismatch = query_view(
            self.request(self.editor, "POST", f"/receivers/?project_id={self.other_project.pk}", {"name": "Wrong query", "target_address": "one@example.test"}),
            project_id=self.project.pk,
        )
        self.assertEqual(query_mismatch.status_code, 400, query_mismatch.data)

    def test_smtp_crud_is_admin_only_and_password_length_is_bounded(self):
        create = EmailConfigViewSet.as_view({"post": "create"})
        payload = {
            "name": "SMTP CRUD",
            "smtp_server": "smtp.example.test",
            "sender_email": "sender@example.test",
            "smtp_password": "test-only-password",
        }
        denied = create(self.request(self.owner, "POST", "/email-configs/", payload))
        self.assertEqual(denied.status_code, 403)
        oversized = create(self.request(self.admin, "POST", "/email-configs/", {**payload, "smtp_password": "x" * 256}))
        self.assertEqual(oversized.status_code, 400, oversized.data)
        created = create(self.request(self.admin, "POST", "/email-configs/", payload))
        self.assertEqual(created.status_code, 201, created.data)

        delete_view = EmailConfigViewSet.as_view({"delete": "destroy"})
        self.assertEqual(delete_view(self.request(self.owner, "DELETE", "/email-configs/"), pk=created.data["id"]).status_code, 403)
        self.assertEqual(delete_view(self.request(self.admin, "DELETE", "/email-configs/"), pk=created.data["id"]).status_code, 204)

    def test_disabled_smtp_config_cannot_be_used_for_receiver_delivery(self):
        EmailConfig.objects.create(
            name="Disabled SMTP",
            smtp_server="smtp.example.test",
            sender_email="sender@example.test",
            smtp_password="test-password",
            is_active=False,
        )
        receiver = NotificationReceiver.objects.create(
            project=self.project, channel=self.email, name="Disabled SMTP target", target_address="one@example.test"
        )
        view = NotificationReceiverViewSet.as_view({"post": "test_by_id"})
        response = view(self.request(self.editor, "POST", "/receivers/test/"), project_id=self.project.pk, pk=receiver.pk)
        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn("未配置启用的邮件服务", str(response.data))
