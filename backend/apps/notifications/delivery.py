"""Safe SMTP delivery for the email-only notification subsystem."""

from __future__ import annotations

import re
import smtplib
import socket
import ssl

from django.core.exceptions import ValidationError as DjangoValidationError
from django.core.mail import EmailMultiAlternatives, get_connection
from django.core.validators import validate_email

from .models import EmailConfig


REQUEST_TIMEOUT_SECONDS = 10
SUPPORTED_CHANNEL_CODES = frozenset({"email"})
_SAFE_DELIVERY_MESSAGES = {
    "请至少填写一个收件人邮箱",
    "收件人邮箱列表格式不正确",
    "收件人邮箱格式不正确",
    "未配置启用的邮件服务",
    "SMTP 连接超时，请检查服务器地址或网络",
    "SMTP SSL/TLS 协商失败，请检查端口与加密方式",
    "SMTP 认证失败，请检查发件邮箱或授权码",
    "SMTP 服务器拒绝收件人",
    "SMTP 拒绝部分收件人，其他收件人可能已收信；请核对地址，不要直接重复发送",
    "SMTP 服务器拒绝邮件内容或发件人",
    "SMTP 连接失败",
    "SMTP 发送失败",
}


class NotificationDeliveryError(Exception):
    """A transport error whose message is safe for API responses and logs."""

    def __init__(self, safe_message: str):
        if safe_message not in _SAFE_DELIVERY_MESSAGES:
            safe_message = "SMTP 发送失败"
        self.safe_message = safe_message
        super().__init__(safe_message)


def parse_recipients(text: str) -> list[str]:
    """Parse supported separators and deduplicate addresses while preserving order."""
    if not isinstance(text, str) or not text.strip():
        raise NotificationDeliveryError("请至少填写一个收件人邮箱")
    recipients = [item.strip() for item in re.split(r"[,;\r\n]+", text)]
    if not recipients or any(not item for item in recipients):
        raise NotificationDeliveryError("收件人邮箱列表格式不正确")

    result: list[str] = []
    seen: set[str] = set()
    for recipient in recipients:
        try:
            validate_email(recipient)
        except DjangoValidationError as exc:
            raise NotificationDeliveryError("收件人邮箱格式不正确") from exc
        normalized = recipient.casefold()
        if normalized not in seen:
            seen.add(normalized)
            result.append(recipient)
    return result


def _get_smtp_connection(config: EmailConfig):
    """`use_ssl=False` deliberately means STARTTLS, never plaintext SMTP."""
    return get_connection(
        backend="django.core.mail.backends.smtp.EmailBackend",
        host=config.smtp_server,
        port=config.port,
        username=config.sender_email,
        password=config.smtp_password,
        use_ssl=config.use_ssl,
        use_tls=not config.use_ssl,
        timeout=REQUEST_TIMEOUT_SECONDS,
        fail_silently=False,
    )


def _safe_smtp_error(exc: Exception) -> NotificationDeliveryError:
    if isinstance(exc, (TimeoutError, socket.timeout)):
        return NotificationDeliveryError("SMTP 连接超时，请检查服务器地址或网络")
    if isinstance(exc, ssl.SSLError):
        return NotificationDeliveryError("SMTP SSL/TLS 协商失败，请检查端口与加密方式")
    if isinstance(exc, smtplib.SMTPAuthenticationError):
        return NotificationDeliveryError("SMTP 认证失败，请检查发件邮箱或授权码")
    if isinstance(exc, smtplib.SMTPRecipientsRefused):
        return NotificationDeliveryError("SMTP 服务器拒绝收件人")
    if isinstance(exc, (smtplib.SMTPSenderRefused, smtplib.SMTPDataError)):
        return NotificationDeliveryError("SMTP 服务器拒绝邮件内容或发件人")
    if isinstance(exc, (smtplib.SMTPConnectError, smtplib.SMTPServerDisconnected, OSError)):
        return NotificationDeliveryError("SMTP 连接失败")
    return NotificationDeliveryError("SMTP 发送失败")


def test_smtp_connection(config: EmailConfig) -> None:
    """Open and authenticate only; this test never sends a recipient message."""
    connection = None
    try:
        connection = _get_smtp_connection(config)
        connection.open()
    except NotificationDeliveryError:
        raise
    except Exception as exc:
        raise _safe_smtp_error(exc) from exc
    finally:
        if connection is not None:
            try:
                connection.close()
            except Exception:
                pass


def _active_email_config() -> EmailConfig:
    config = EmailConfig.objects.filter(is_active=True).order_by("-updated_at", "-pk").first()
    if config is None:
        raise NotificationDeliveryError("未配置启用的邮件服务")
    return config


def send_email(subject: str, body: str, recipients: list[str], html_body: str | None = None) -> None:
    """Send one message and reject any partial recipient refusal as a failure.

    Django's stock SMTP backend treats a ``sendmail`` partial-refusal mapping as
    a successful message.  Calling ``sendmail`` directly lets this notification
    path report that state instead of falsely claiming every recipient received it.
    """
    if not recipients:
        raise NotificationDeliveryError("请至少填写一个收件人邮箱")
    normalized_recipients = parse_recipients(",".join(str(recipient) for recipient in recipients))
    config = _active_email_config()
    connection = None
    try:
        connection = _get_smtp_connection(config)
        connection.open()
        message = EmailMultiAlternatives(
            subject=subject,
            body=body,
            from_email=config.sender_email,
            to=normalized_recipients,
            connection=connection,
        )
        if html_body is not None:
            message.attach_alternative(html_body, "text/html")
        smtp_client = getattr(connection, "connection", None)
        if smtp_client is not None and callable(getattr(smtp_client, "sendmail", None)):
            refused = smtp_client.sendmail(
                message.from_email,
                message.recipients(),
                message.message().as_bytes(linesep="\r\n"),
            )
            if refused:
                if len(refused) < len(message.recipients()):
                    raise NotificationDeliveryError(
                        "SMTP 拒绝部分收件人，其他收件人可能已收信；请核对地址，不要直接重复发送"
                    )
                raise smtplib.SMTPRecipientsRefused(refused)
        elif message.send(fail_silently=False) != 1:
            raise NotificationDeliveryError("SMTP 发送失败")
    except NotificationDeliveryError:
        raise
    except Exception as exc:
        raise _safe_smtp_error(exc) from exc
    finally:
        if connection is not None:
            try:
                connection.close()
            except Exception:
                pass
