from unittest.mock import Mock, patch

import requests

from django.test import SimpleTestCase, TestCase

from .delivery import (
    NotificationDeliveryError,
    parse_recipients,
    send_email,
    send_webhook,
    test_smtp_connection,
    validate_webhook_url,
)
from .models import EmailConfig


class NotificationWebhookDeliveryTests(SimpleTestCase):
    DINGTALK_URL = "https://oapi.dingtalk.com/robot/send?access_token=receiver-secret"
    WECHAT_URL = "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=receiver-secret"

    def test_validate_webhook_url_accepts_only_official_endpoints(self):
        self.assertEqual(
            validate_webhook_url("dingtalk", self.DINGTALK_URL), self.DINGTALK_URL
        )
        self.assertEqual(
            validate_webhook_url(
                "dingtalk",
                "https://oapi.dingtalk.com:443/robot/send?access_token=receiver-secret",
            ),
            "https://oapi.dingtalk.com:443/robot/send?access_token=receiver-secret",
        )
        self.assertEqual(
            validate_webhook_url("wechat_work", self.WECHAT_URL), self.WECHAT_URL
        )
        for channel, url in (
            ("dingtalk", "http://oapi.dingtalk.com/robot/send?access_token=value"),
            ("dingtalk", "https://attacker.example/robot/send?access_token=value"),
            (
                "dingtalk",
                "https://user@oapi.dingtalk.com/robot/send?access_token=value",
            ),
            (
                "dingtalk",
                "https://oapi.dingtalk.com/robot/send?access_token=value#fragment",
            ),
            ("dingtalk", "https://oapi.dingtalk.com/robot/send"),
            ("dingtalk", "https://oapi.dingtalk.com:444/robot/send?access_token=value"),
            ("email", self.DINGTALK_URL),
        ):
            with self.assertRaises(NotificationDeliveryError):
                validate_webhook_url(channel, url)

    @patch("notifications.delivery.requests.post")
    def test_send_webhook_requires_http_and_official_success_body(self, post):
        post.return_value = Mock(
            status_code=200, json=Mock(return_value={"errcode": 0})
        )

        send_webhook("dingtalk", self.DINGTALK_URL, {"msgtype": "text"})

        post.assert_called_once_with(
            self.DINGTALK_URL,
            json={"msgtype": "text"},
            timeout=10,
            allow_redirects=False,
            headers={"Content-Type": "application/json"},
        )
        post.return_value = Mock(
            status_code=200, json=Mock(return_value={"errcode": "0"})
        )
        send_webhook("dingtalk", self.DINGTALK_URL, {"msgtype": "text"})
        for response in (
            Mock(status_code=201, json=Mock(return_value={"errcode": 0})),
            Mock(status_code=200, json=Mock(return_value={"errcode": 1})),
            Mock(status_code=200, json=Mock(return_value={"errcode": True})),
            Mock(status_code=200, json=Mock(return_value={"errcode": 0.0})),
            Mock(status_code=200, json=Mock(return_value={"errcode": "0"})),
            Mock(status_code=200, json=Mock(side_effect=ValueError("bad body"))),
        ):
            post.return_value = response
            with self.assertRaises(NotificationDeliveryError) as error:
                send_webhook("wechat_work", self.WECHAT_URL, {"msgtype": "text"})
            self.assertNotIn("receiver-secret", str(error.exception))

    @patch("notifications.delivery.requests.post")
    def test_send_webhook_exposes_only_safe_status_or_errcode_context(self, post):
        post.return_value = Mock(
            status_code=503, json=Mock(return_value={"errcode": 0})
        )
        with self.assertRaisesRegex(NotificationDeliveryError, "状态码: 503"):
            send_webhook("dingtalk", self.DINGTALK_URL, {"msgtype": "text"})

        post.return_value = Mock(
            status_code=200,
            json=Mock(return_value={"errcode": 310000, "errmsg": "secret detail"}),
        )
        with self.assertRaisesRegex(
            NotificationDeliveryError, "关键词、签名或 IP 白名单"
        ) as error:
            send_webhook("dingtalk", self.DINGTALK_URL, {"msgtype": "text"})
        self.assertNotIn("secret detail", str(error.exception))

        post.side_effect = requests.Timeout("private timeout detail")
        with self.assertRaisesRegex(NotificationDeliveryError, "请求超时") as error:
            send_webhook("dingtalk", self.DINGTALK_URL, {"msgtype": "text"})
        self.assertNotIn("private timeout detail", str(error.exception))

    def test_parse_recipients_normalizes_supported_separators_and_rejects_bad_values(
        self,
    ):
        self.assertEqual(
            parse_recipients("one@example.test; two@example.test\nthree@example.test"),
            ["one@example.test", "two@example.test", "three@example.test"],
        )
        for value in ("", "one@example.test,", "not-an-email"):
            with self.assertRaises(NotificationDeliveryError):
                parse_recipients(value)


class NotificationEmailDeliveryTests(TestCase):
    def setUp(self):
        self.config = EmailConfig.objects.create(
            name="SMTP",
            smtp_server="smtp.example.test",
            port=465,
            sender_email="sender@example.test",
            smtp_password="test-only-password",
            use_ssl=True,
        )

    @patch("notifications.delivery.EmailMultiAlternatives")
    def test_send_email_uses_enabled_smtp_timeout_and_requires_one_send(
        self, email_message
    ):
        message = email_message.return_value
        message.send.return_value = 1

        send_email("subject", "body", ["one@example.test"], html_body="<p>body</p>")

        connection = email_message.call_args.kwargs["connection"]
        self.assertEqual(connection.timeout, 10)
        self.assertTrue(connection.use_ssl)
        self.assertFalse(connection.use_tls)
        message.attach_alternative.assert_called_once_with("<p>body</p>", "text/html")
        message.send.return_value = 0
        with self.assertRaises(NotificationDeliveryError):
            send_email("subject", "body", ["one@example.test"])

    @patch("notifications.delivery._get_smtp_connection")
    def test_test_smtp_connection_uses_shared_transport_rules(self, get_connection):
        connection = get_connection.return_value

        test_smtp_connection(self.config)

        connection.open.assert_called_once_with()
        connection.close.assert_called_once_with()
