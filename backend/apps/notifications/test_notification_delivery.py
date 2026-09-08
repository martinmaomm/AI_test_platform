"""Offline SMTP delivery contracts; no real provider is contacted."""

import smtplib
import socket
import ssl
from unittest.mock import Mock, patch

from django.core import mail
from django.core.mail import get_connection
from django.test import SimpleTestCase, TestCase

from .delivery import (
    NotificationDeliveryError,
    parse_recipients,
    send_email,
    test_smtp_connection,
)
from .models import EmailConfig


class RecipientParsingTests(SimpleTestCase):
    def test_supported_separators_are_normalized_and_deduplicated(self):
        self.assertEqual(
            parse_recipients("one@example.test; two@example.test\nONE@example.test"),
            ["one@example.test", "two@example.test"],
        )

    def test_empty_bad_or_empty_item_is_rejected(self):
        for value in ("", "one@example.test,", "not-an-email"):
            with self.subTest(value=value), self.assertRaises(NotificationDeliveryError):
                parse_recipients(value)


class EmailDeliveryTests(TestCase):
    def setUp(self):
        self.config = EmailConfig.objects.create(
            name="SMTP",
            smtp_server="smtp.example.test",
            port=465,
            sender_email="sender@example.test",
            smtp_password="test-only-password",
            use_ssl=True,
        )

    def _connection(self, *, refused=None, open_side_effect=None):
        client = Mock()
        client.sendmail.return_value = {} if refused is None else refused
        connection = Mock(connection=client)
        connection.open.side_effect = open_side_effect
        return connection, client

    @patch("notifications.delivery._get_smtp_connection")
    def test_send_email_builds_one_mime_message_for_all_recipients(self, get_connection):
        connection, client = self._connection()
        get_connection.return_value = connection

        send_email(
            "mail-subject",
            "纯文本内容",
            ["one@example.test", "two@example.test", "ONE@example.test"],
            html_body="<p>HTML 内容</p>",
        )

        connection.open.assert_called_once_with()
        connection.close.assert_called_once_with()
        sender, recipients, raw_message = client.sendmail.call_args.args
        self.assertEqual(sender, "sender@example.test")
        self.assertEqual(recipients, ["one@example.test", "two@example.test"])
        self.assertIn(b"Subject: mail-subject", raw_message)
        self.assertIn(b"multipart/alternative", raw_message)

    @patch("notifications.delivery._get_smtp_connection")
    def test_partial_recipient_refusal_is_not_reported_as_success(self, get_connection):
        connection, client = self._connection(
            refused={"two@example.test": (550, b"not accepted")}
        )
        get_connection.return_value = connection

        with self.assertRaisesRegex(NotificationDeliveryError, "部分收件人") as caught:
            send_email("subject", "body", ["one@example.test", "two@example.test"])
        self.assertIn("不要直接重复发送", caught.exception.safe_message)
        connection.close.assert_called_once_with()
        client.sendmail.assert_called_once()

    @patch("notifications.delivery._get_smtp_connection")
    def test_non_smtp_test_backend_keeps_django_message_and_outbox_contract(self, get_smtp_connection):
        get_smtp_connection.return_value = get_connection("django.core.mail.backends.locmem.EmailBackend")

        send_email("mail-subject", "body", ["one@example.test"])

        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["one@example.test"])

    @patch("notifications.delivery._get_smtp_connection")
    def test_smtp_security_errors_are_safe_and_do_not_leak_credentials(self, get_connection):
        cases = (
            (smtplib.SMTPAuthenticationError(535, b"password=private"), "认证失败"),
            (ssl.SSLError("private TLS detail"), "SSL/TLS"),
            (socket.timeout("private timeout detail"), "超时"),
        )
        for error, expected in cases:
            connection, _ = self._connection(open_side_effect=error)
            get_connection.return_value = connection
            with self.subTest(error=type(error).__name__), self.assertRaises(NotificationDeliveryError) as caught:
                test_smtp_connection(self.config)
            self.assertIn(expected, caught.exception.safe_message)
            self.assertNotIn("private", caught.exception.safe_message)
            self.assertNotIn("password", caught.exception.safe_message)

    @patch("notifications.delivery._get_smtp_connection")
    def test_connection_test_opens_and_authenticates_without_sending_email(self, get_connection):
        connection, client = self._connection()
        get_connection.return_value = connection

        test_smtp_connection(self.config)

        connection.open.assert_called_once_with()
        connection.close.assert_called_once_with()
        client.sendmail.assert_not_called()

    @patch("notifications.delivery.get_connection")
    def test_starttls_is_used_when_ssl_is_disabled(self, get_connection):
        from .delivery import _get_smtp_connection

        self.config.use_ssl = False
        _get_smtp_connection(self.config)

        self.assertFalse(get_connection.call_args.kwargs["use_ssl"])
        self.assertTrue(get_connection.call_args.kwargs["use_tls"])
