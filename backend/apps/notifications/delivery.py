"""Safe, reusable transports for notification channels."""

from __future__ import annotations

import re
from urllib.parse import parse_qs, urlsplit

import requests
from django.core.exceptions import ValidationError as DjangoValidationError
from django.core.mail import EmailMultiAlternatives, get_connection
from django.core.validators import validate_email

from .models import EmailConfig


REQUEST_TIMEOUT_SECONDS = 10
SUPPORTED_CHANNEL_CODES = frozenset({"dingtalk", "wechat_work", "email"})
_WEBHOOK_ENDPOINTS = {
    "dingtalk": ("oapi.dingtalk.com", "/robot/send", "access_token"),
    "wechat_work": ("qyapi.weixin.qq.com", "/cgi-bin/webhook/send", "key"),
}
_SAFE_DELIVERY_MESSAGES = {
    "不支持的 Webhook 渠道",
    "请填写 Webhook 地址",
    "Webhook 地址格式不正确",
    "Webhook 地址不是该渠道的官方地址",
    "Webhook 地址缺少渠道凭证",
    "Webhook 请求超时，请检查网络或渠道服务",
    "Webhook 网络请求失败",
    "Webhook 服务返回了无效响应",
    "Webhook 服务未确认发送成功",
    "钉钉渠道验证失败，请检查关键词、签名或 IP 白名单",
    "请至少填写一个收件人邮箱",
    "收件人邮箱列表格式不正确",
    "收件人邮箱格式不正确",
    "SMTP 连接失败",
    "未配置启用的邮件服务",
    "邮件服务未确认发送成功",
    "邮件发送失败",
}
_SAFE_STATUS_MESSAGE = re.compile(r"^Webhook 服务响应状态码: [1-5][0-9]{2}$")
_SAFE_ERRCODE_MESSAGE = re.compile(r"^Webhook 服务返回错误码: -?[0-9]+$")


class NotificationDeliveryError(Exception):
    """A safe-to-return notification transport error."""

    def __init__(self, safe_message: str):
        if safe_message not in _SAFE_DELIVERY_MESSAGES and not (
            _SAFE_STATUS_MESSAGE.fullmatch(safe_message)
            or _SAFE_ERRCODE_MESSAGE.fullmatch(safe_message)
        ):
            safe_message = "通知发送失败，请检查渠道配置"
        self.safe_message = safe_message
        super().__init__(safe_message)


def validate_webhook_url(channel_code: str, webhook_url: str) -> str:
    """Validate an official robot endpoint without exposing its credentials."""
    if channel_code not in _WEBHOOK_ENDPOINTS:
        raise NotificationDeliveryError("不支持的 Webhook 渠道")
    if not isinstance(webhook_url, str) or not webhook_url.strip():
        raise NotificationDeliveryError("请填写 Webhook 地址")

    url = webhook_url.strip()
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except ValueError as exc:
        raise NotificationDeliveryError("Webhook 地址格式不正确") from exc

    expected_host, expected_path, required_query = _WEBHOOK_ENDPOINTS[channel_code]
    if (
        parsed.scheme != "https"
        or parsed.hostname != expected_host
        or parsed.path != expected_path
        or parsed.fragment
        or parsed.username is not None
        or parsed.password is not None
        or port not in (None, 443)
    ):
        raise NotificationDeliveryError("Webhook 地址不是该渠道的官方地址")

    query = parse_qs(parsed.query, keep_blank_values=True)
    if not query.get(required_query) or not query[required_query][0].strip():
        raise NotificationDeliveryError("Webhook 地址缺少渠道凭证")
    return url


def send_webhook(channel_code: str, webhook_url: str, payload: dict) -> None:
    """Send one webhook message and require an explicit official success body."""
    url = validate_webhook_url(channel_code, webhook_url)
    try:
        response = requests.post(
            url,
            json=payload,
            timeout=REQUEST_TIMEOUT_SECONDS,
            allow_redirects=False,
            headers={"Content-Type": "application/json"},
        )
    except requests.Timeout as exc:
        raise NotificationDeliveryError(
            "Webhook 请求超时，请检查网络或渠道服务"
        ) from exc
    except requests.RequestException as exc:
        raise NotificationDeliveryError("Webhook 网络请求失败") from exc

    if response.status_code != 200:
        raise NotificationDeliveryError(
            f"Webhook 服务响应状态码: {response.status_code}"
        )
    try:
        response_data = response.json()
    except ValueError as exc:
        raise NotificationDeliveryError("Webhook 服务返回了无效响应") from exc
    errcode = response_data.get("errcode") if isinstance(response_data, dict) else None
    is_success = (type(errcode) is int and errcode == 0) or (
        channel_code == "dingtalk" and type(errcode) is str and errcode == "0"
    )
    if not is_success:
        if channel_code == "dingtalk" and type(errcode) is int and errcode == 310000:
            raise NotificationDeliveryError(
                "钉钉渠道验证失败，请检查关键词、签名或 IP 白名单"
            )
        if type(errcode) is int:
            raise NotificationDeliveryError(f"Webhook 服务返回错误码: {errcode}")
        raise NotificationDeliveryError("Webhook 服务未确认发送成功")


def parse_recipients(text: str) -> list[str]:
    """Parse comma, semicolon, or newline-separated email addresses strictly."""
    if not isinstance(text, str) or not text.strip():
        raise NotificationDeliveryError("请至少填写一个收件人邮箱")
    recipients = [item.strip() for item in re.split(r"[,;\r\n]+", text)]
    if not recipients or any(not item for item in recipients):
        raise NotificationDeliveryError("收件人邮箱列表格式不正确")
    for recipient in recipients:
        try:
            validate_email(recipient)
        except DjangoValidationError as exc:
            raise NotificationDeliveryError("收件人邮箱格式不正确") from exc
    return recipients


def _get_smtp_connection(config: EmailConfig):
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


def test_smtp_connection(config: EmailConfig) -> None:
    """Test one selected SMTP configuration using the shared timeout/TLS rules."""
    try:
        connection = _get_smtp_connection(config)
        connection.open()
        connection.close()
    except Exception as exc:
        raise NotificationDeliveryError("SMTP 连接失败") from exc


def send_email(
    subject: str, body: str, recipients: list[str], html_body: str | None = None
) -> None:
    """Send exactly one email through the newest enabled SMTP configuration."""
    if not recipients:
        raise NotificationDeliveryError("请至少填写一个收件人邮箱")
    config = EmailConfig.objects.filter(is_active=True).order_by("-updated_at").first()
    if config is None:
        raise NotificationDeliveryError("未配置启用的邮件服务")

    try:
        message = EmailMultiAlternatives(
            subject=subject,
            body=body,
            from_email=config.sender_email,
            to=recipients,
            connection=_get_smtp_connection(config),
        )
        if html_body is not None:
            message.attach_alternative(html_body, "text/html")
        if message.send(fail_silently=False) != 1:
            raise NotificationDeliveryError("邮件服务未确认发送成功")
    except NotificationDeliveryError:
        raise
    except Exception as exc:
        raise NotificationDeliveryError("邮件发送失败") from exc
