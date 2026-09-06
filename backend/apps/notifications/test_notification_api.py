from unittest.mock import Mock, patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from projects.models import Project, ProjectMember

from .delivery import NotificationDeliveryError
from .models import EmailConfig, NotificationChannel, NotificationReceiver
from .views import (
    EmailConfigViewSet,
    NotificationChannelViewSet,
    NotificationReceiverViewSet,
)


class NotificationApiContractTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.owner = user_model.objects.create_user(
            "notification-owner", "notification-owner@example.test", "password"
        )
        self.member = user_model.objects.create_user(
            "notification-member", "notification-member@example.test", "password"
        )
        self.read_only_member = user_model.objects.create_user(
            "notification-read-only", "notification-read-only@example.test", "password"
        )
        self.outsider = user_model.objects.create_user(
            "notification-outsider", "notification-outsider@example.test", "password"
        )
        self.admin = user_model.objects.create_user(
            "notification-admin",
            "notification-admin@example.test",
            "password",
            is_staff=True,
        )
        self.project = Project.objects.create(
            name="Notification project",
            project_type="web",
            owner=self.owner,
            created_by=self.owner,
        )
        self.other_project = Project.objects.create(
            name="Other project",
            project_type="api",
            owner=self.outsider,
            created_by=self.outsider,
        )
        ProjectMember.objects.create(
            project=self.project,
            user=self.member,
            role="editor",
            can_edit=True,
            can_delete=True,
            can_execute_tests=True,
        )
        ProjectMember.objects.create(
            project=self.project,
            user=self.read_only_member,
            role="viewer",
            can_edit=False,
            can_delete=False,
            can_execute_tests=False,
            can_view_reports=False,
        )
        self.dingtalk = NotificationChannel.objects.create(
            channel_code="dingtalk", channel_name="钉钉"
        )
        self.email = NotificationChannel.objects.create(
            channel_code="email", channel_name="邮件"
        )
        self.disabled = NotificationChannel.objects.create(
            channel_code="wechat_work", channel_name="企微", is_active=False
        )
        self.legacy = NotificationChannel.objects.create(
            channel_code="legacy_webhook", channel_name="旧渠道"
        )
        self.receiver = NotificationReceiver.objects.create(
            project=self.project,
            channel=self.dingtalk,
            name="Current project",
            webhook_url="https://oapi.dingtalk.com/robot/send?access_token=current-secret",
        )
        self.other_receiver = NotificationReceiver.objects.create(
            project=self.other_project,
            channel=self.dingtalk,
            name="Other project",
            webhook_url="https://oapi.dingtalk.com/robot/send?access_token=other-secret",
        )
        self.factory = APIRequestFactory()

    def request(self, user, method, path, payload=None):
        request = getattr(self.factory, method.lower())(
            path, payload or {}, format="json"
        )
        force_authenticate(request, user=user)
        return request

    def receiver_view(self, method, *, detail=False):
        actions = {
            "get": "retrieve" if detail else "list",
            "post": "create",
            "patch": "partial_update",
            "delete": "destroy",
        }
        return NotificationReceiverViewSet.as_view(
            {method.lower(): actions[method.lower()]}
        )

    def test_project_receiver_list_never_mixes_other_project_relations(self):
        response = self.receiver_view("GET")(
            self.request(self.owner, "GET", "/receivers/"),
            project_id=self.project.pk,
        )

        self.assertEqual(response.status_code, 200, response.data)
        receiver_ids = [item["id"] for item in response.data["results"]]
        self.assertEqual(receiver_ids, [self.receiver.pk])
        self.assertEqual(response.data["results"][0]["webhook_url"], "***")
        response = self.receiver_view("GET")(
            self.request(self.owner, "GET", "/notifications/receivers/"),
        )
        self.assertEqual(response.status_code, 400)

    def test_create_uses_url_project_and_rejects_mismatched_body_project(self):
        payload = {
            "project": self.other_project.pk,
            "channel": self.dingtalk.pk,
            "name": "Mismatch",
            "webhook_url": "https://oapi.dingtalk.com/robot/send?access_token=new-secret",
        }
        response = self.receiver_view("POST")(
            self.request(self.member, "POST", "/receivers/", payload),
            project_id=self.project.pk,
        )
        self.assertEqual(response.status_code, 400, response.data)
        self.assertFalse(NotificationReceiver.objects.filter(name="Mismatch").exists())

        payload["project"] = self.project.pk
        response = self.receiver_view("POST")(
            self.request(self.member, "POST", "/receivers/", payload),
            project_id=self.project.pk,
        )
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(
            NotificationReceiver.objects.get(name="Mismatch").project_id,
            self.project.pk,
        )

    def test_rejects_conflicting_project_fields_before_serializer_validation(self):
        response = self.receiver_view("POST")(
            self.request(
                self.member,
                "POST",
                "/receivers/",
                {
                    "project": self.project.pk,
                    "project_id": self.other_project.pk,
                    "channel": self.dingtalk.pk,
                    "name": "Conflict",
                    "webhook_url": "https://oapi.dingtalk.com/robot/send?access_token=conflict-secret",
                },
            ),
            project_id=self.project.pk,
        )

        self.assertEqual(response.status_code, 400, response.data)
        self.assertFalse(NotificationReceiver.objects.filter(name="Conflict").exists())

    def test_disabled_channel_rejects_new_transport_but_existing_receiver_can_be_renamed(
        self,
    ):
        disabled_receiver = NotificationReceiver.objects.create(
            project=self.project,
            channel=self.disabled,
            name="Legacy",
            webhook_url="https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=legacy-secret",
        )
        response = self.receiver_view("PATCH", detail=True)(
            self.request(
                self.member,
                "PATCH",
                "/receivers/",
                {
                    "project": self.project.pk,
                    "channel": self.disabled.pk,
                    "name": "Renamed legacy",
                    "webhook_url": "",
                },
            ),
            project_id=self.project.pk,
            pk=disabled_receiver.pk,
        )
        self.assertEqual(response.status_code, 200, response.data)
        disabled_receiver.refresh_from_db()
        self.assertEqual(disabled_receiver.name, "Renamed legacy")

        response = self.receiver_view("POST")(
            self.request(
                self.member,
                "POST",
                "/receivers/",
                {
                    "project": self.project.pk,
                    "channel": self.disabled.pk,
                    "name": "Blocked",
                    "webhook_url": "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=new-secret",
                },
            ),
            project_id=self.project.pk,
        )
        self.assertEqual(response.status_code, 400, response.data)

    def test_unknown_channel_rejects_write_but_authorized_delete_can_clean_it(self):
        legacy_receiver = NotificationReceiver.objects.create(
            project=self.project,
            channel=self.legacy,
            name="Legacy unknown",
            webhook_url="https://legacy.example.test/hook",
        )
        response = self.receiver_view("PATCH", detail=True)(
            self.request(
                self.member,
                "PATCH",
                "/receivers/",
                {
                    "project": self.project.pk,
                    "channel": self.legacy.pk,
                    "name": "Renamed legacy unknown",
                },
            ),
            project_id=self.project.pk,
            pk=legacy_receiver.pk,
        )
        self.assertEqual(response.status_code, 400, response.data)

        response = self.receiver_view("DELETE", detail=True)(
            self.request(self.member, "DELETE", "/receivers/"),
            project_id=self.project.pk,
            pk=legacy_receiver.pk,
        )
        self.assertEqual(response.status_code, 204, response.data)

    def test_switching_channel_requires_a_new_valid_target(self):
        response = self.receiver_view("PATCH", detail=True)(
            self.request(
                self.member,
                "PATCH",
                "/receivers/",
                {
                    "project": self.project.pk,
                    "channel": self.email.pk,
                },
            ),
            project_id=self.project.pk,
            pk=self.receiver.pk,
        )
        self.assertEqual(response.status_code, 400, response.data)

        response = self.receiver_view("PATCH", detail=True)(
            self.request(
                self.member,
                "PATCH",
                "/receivers/",
                {
                    "project": self.project.pk,
                    "channel": self.email.pk,
                    "target_address": "one@example.test;two@example.test",
                },
            ),
            project_id=self.project.pk,
            pk=self.receiver.pk,
        )
        self.assertEqual(response.status_code, 200, response.data)
        self.receiver.refresh_from_db()
        self.assertEqual(self.receiver.channel_id, self.email.pk)
        self.assertEqual(
            self.receiver.target_address, "one@example.test,two@example.test"
        )

        response = self.receiver_view("PATCH", detail=True)(
            self.request(
                self.member,
                "PATCH",
                "/receivers/",
                {
                    "project": self.project.pk,
                    "channel": self.dingtalk.pk,
                },
            ),
            project_id=self.project.pk,
            pk=self.receiver.pk,
        )
        self.assertEqual(response.status_code, 400, response.data)

        response = self.receiver_view("PATCH", detail=True)(
            self.request(
                self.member,
                "PATCH",
                "/receivers/",
                {
                    "project": self.project.pk,
                    "channel": self.dingtalk.pk,
                    "webhook_url": "https://oapi.dingtalk.com/robot/send?access_token=replacement-secret",
                },
            ),
            project_id=self.project.pk,
            pk=self.receiver.pk,
        )
        self.assertEqual(response.status_code, 200, response.data)

    def test_test_connection_rejects_non_string_inputs(self):
        view = NotificationReceiverViewSet.as_view({"post": "test_connection"})
        for payload in (
            {
                "project": self.project.pk,
                "channel_type": "dingtalk",
                "webhook_url": ["not-a-url"],
            },
            {
                "project": self.project.pk,
                "channel_type": ["dingtalk"],
                "webhook_url": "https://oapi.dingtalk.com/robot/send?access_token=test-secret",
            },
        ):
            response = view(
                self.request(
                    self.member, "POST", "/receivers/test_connection/", payload
                ),
                project_id=self.project.pk,
            )
            self.assertEqual(response.status_code, 400, response.data)

    def test_outsider_get_write_and_test_receive_not_found(self):
        list_response = self.receiver_view("GET")(
            self.request(self.outsider, "GET", "/receivers/"),
            project_id=self.project.pk,
        )
        create_response = self.receiver_view("POST")(
            self.request(
                self.outsider,
                "POST",
                "/receivers/",
                {
                    "channel": self.dingtalk.pk,
                    "name": "Outsider",
                    "webhook_url": "https://oapi.dingtalk.com/robot/send?access_token=outsider-secret",
                },
            ),
            project_id=self.project.pk,
        )
        test_response = NotificationReceiverViewSet.as_view({"post": "test_by_id"})(
            self.request(self.outsider, "POST", "/receivers/test/"),
            project_id=self.project.pk,
            pk=self.receiver.pk,
        )
        self.assertEqual(list_response.status_code, 404)
        self.assertEqual(create_response.status_code, 404)
        self.assertEqual(test_response.status_code, 404)

    def test_read_only_member_cannot_update_or_execute_a_receiver_test(self):
        update_response = self.receiver_view("PATCH", detail=True)(
            self.request(
                self.read_only_member, "PATCH", "/receivers/", {"name": "Denied"}
            ),
            project_id=self.project.pk,
            pk=self.receiver.pk,
        )
        test_response = NotificationReceiverViewSet.as_view({"post": "test_by_id"})(
            self.request(self.read_only_member, "POST", "/receivers/test/"),
            project_id=self.project.pk,
            pk=self.receiver.pk,
        )
        self.assertEqual(update_response.status_code, 403)
        self.assertEqual(test_response.status_code, 403)

    def test_nested_url_only_create_and_test_by_id_success(self):
        create_response = self.receiver_view("POST")(
            self.request(
                self.member,
                "POST",
                "/receivers/",
                {
                    "channel": self.dingtalk.pk,
                    "name": "URL project only",
                    "webhook_url": "https://oapi.dingtalk.com/robot/send?access_token=url-only-secret",
                },
            ),
            project_id=self.project.pk,
        )
        self.assertEqual(create_response.status_code, 201, create_response.data)
        self.assertEqual(create_response.data["project"], self.project.pk)

        view = NotificationReceiverViewSet.as_view({"post": "test_by_id"})
        with patch("notifications.views.send_webhook") as sender:
            test_response = view(
                self.request(self.member, "POST", "/receivers/test/"),
                project_id=self.project.pk,
                pk=self.receiver.pk,
            )
        self.assertEqual(test_response.status_code, 200, test_response.data)
        sender.assert_called_once()

    @patch("notifications.delivery.requests.post")
    def test_http_success_with_business_error_returns_safe_api_failure(self, post):
        post.return_value = Mock(
            status_code=200,
            json=Mock(return_value={"errcode": 1, "errmsg": "do not expose"}),
        )
        view = NotificationReceiverViewSet.as_view({"post": "test_connection"})
        response = view(
            self.request(
                self.member,
                "POST",
                "/receivers/test_connection/",
                {
                    "project": self.project.pk,
                    "channel_type": "dingtalk",
                    "webhook_url": "https://oapi.dingtalk.com/robot/send?access_token=api-test-secret",
                },
            ),
            project_id=self.project.pk,
        )
        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn("错误码: 1", str(response.data))
        self.assertNotIn("do not expose", str(response.data))

    def test_receiver_test_rejects_disabled_config_without_a_network_request(self):
        receiver = NotificationReceiver.objects.create(
            project=self.project,
            channel=self.disabled,
            name="Disabled",
            webhook_url="https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=disabled-secret",
        )
        view = NotificationReceiverViewSet.as_view({"post": "test_by_id"})
        with patch("notifications.views.send_webhook") as sender:
            response = view(
                self.request(self.member, "POST", "/receivers/test/"),
                project_id=self.project.pk,
                pk=receiver.pk,
            )
        self.assertEqual(response.status_code, 400, response.data)
        sender.assert_not_called()

        raw_view = NotificationReceiverViewSet.as_view({"post": "test_connection"})
        with patch("notifications.views.send_webhook") as sender:
            response = raw_view(
                self.request(
                    self.member,
                    "POST",
                    "/receivers/test_connection/",
                    {
                        "channel_type": "wechat_work",
                        "webhook_url": "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=disabled-secret",
                    },
                ),
                project_id=self.project.pk,
            )
        self.assertEqual(response.status_code, 400, response.data)
        sender.assert_not_called()

    def test_smtp_config_rejects_invalid_port_and_masked_new_password(self):
        view = EmailConfigViewSet.as_view({"post": "create"})
        payload = {
            "name": "Invalid SMTP",
            "smtp_server": "smtp.example.test",
            "port": 465,
            "sender_email": "sender@example.test",
            "smtp_password": "test-only",
        }
        for changes in ({"port": 0}, {"port": 65536}, {"smtp_password": "***"}):
            response = view(
                self.request(
                    self.admin, "POST", "/email-configs/", {**payload, **changes}
                )
            )
            self.assertEqual(response.status_code, 400, response.data)
        self.assertFalse(EmailConfig.objects.filter(name="Invalid SMTP").exists())

    def test_channel_writes_are_admin_only_and_channel_deletion_is_guarded(self):
        create_view = NotificationChannelViewSet.as_view({"post": "create"})
        response = create_view(
            self.request(
                self.owner,
                "POST",
                "/channels/",
                {
                    "channel_code": "wechat_work",
                    "channel_name": "企微",
                },
            )
        )
        self.assertEqual(response.status_code, 403)

        patch_view = NotificationChannelViewSet.as_view({"patch": "partial_update"})
        response = patch_view(
            self.request(self.admin, "PATCH", "/channels/", {"channel_code": "email"}),
            pk=self.dingtalk.pk,
        )
        self.assertEqual(response.status_code, 400, response.data)
        self.dingtalk.refresh_from_db()
        self.assertEqual(self.dingtalk.channel_code, "dingtalk")

        delete_view = NotificationChannelViewSet.as_view({"delete": "destroy"})
        response = delete_view(
            self.request(self.admin, "DELETE", "/channels/"), pk=self.dingtalk.pk
        )
        self.assertEqual(response.status_code, 400, response.data)
        self.assertTrue(
            NotificationChannel.objects.filter(pk=self.dingtalk.pk).exists()
        )

    def test_test_connection_error_response_never_echoes_transport_details(self):
        view = NotificationReceiverViewSet.as_view({"post": "test_connection"})
        with patch(
            "notifications.views.send_webhook",
            side_effect=NotificationDeliveryError("token=raw-secret invalid"),
        ):
            response = view(
                self.request(
                    self.member,
                    "POST",
                    "/receivers/test_connection/",
                    {
                        "project": self.project.pk,
                        "channel_type": "dingtalk",
                        "webhook_url": "https://oapi.dingtalk.com/robot/send?access_token=raw-secret",
                    },
                ),
                project_id=self.project.pk,
            )
        self.assertEqual(response.status_code, 400, response.data)
        self.assertNotIn("raw-secret", str(response.data))
        self.assertNotIn("token", str(response.data).lower())
        self.assertNotIn("invalid", str(response.data).lower())

    def test_email_config_is_admin_only(self):
        config = EmailConfig.objects.create(
            name="SMTP",
            smtp_server="smtp.example.test",
            sender_email="sender@example.test",
            smtp_password="test-password",
        )
        view = EmailConfigViewSet.as_view({"get": "retrieve"})
        response = view(
            self.request(self.owner, "GET", "/email-configs/"), pk=config.pk
        )
        self.assertEqual(response.status_code, 403)
