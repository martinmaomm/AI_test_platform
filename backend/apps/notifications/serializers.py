"""Email-only notification API serializers."""

from __future__ import annotations

import ipaddress
import re

from rest_framework import serializers

from .delivery import NotificationDeliveryError, SUPPORTED_CHANNEL_CODES, parse_recipients
from .models import EmailConfig, NotificationChannel, NotificationReceiver, get_builtin_email_channel


def _is_valid_smtp_host(value: str) -> bool:
    """Accept a hostname or IP literal, but never a URL or host:port string."""
    if not isinstance(value, str):
        return False
    host = value.strip()
    if not host or host != value or len(host) > 253 or "://" in host:
        return False
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        pass
    if host.endswith("."):
        host = host[:-1]
    if not host or len(host) > 253:
        return False
    label = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?$")
    return all(label.fullmatch(part) for part in host.split("."))


def _should_keep_password(value) -> bool:
    return value is None or (isinstance(value, str) and (not value.strip() or value == "***"))


class NotificationChannelSerializer(serializers.ModelSerializer):
    """Read-only metadata for the built-in email channel."""

    class Meta:
        model = NotificationChannel
        fields = (
            "id",
            "channel_code",
            "channel_name",
            "description",
            "is_active",
            "created_at",
            "updated_at",
        )
        read_only_fields = fields


class NotificationReceiverSerializer(serializers.ModelSerializer):
    """Project receiver serializer that always binds to the built-in email channel."""

    channel = serializers.PrimaryKeyRelatedField(
        queryset=NotificationChannel.objects.all(), required=False, write_only=True
    )
    channel_type = serializers.CharField(required=False, write_only=True)
    webhook_url = serializers.CharField(required=False, write_only=True, allow_blank=True)
    channel_code = serializers.CharField(source="channel.channel_code", read_only=True)
    channel_name = serializers.CharField(source="channel.channel_name", read_only=True)
    channel_is_active = serializers.BooleanField(source="channel.is_active", read_only=True)

    class Meta:
        model = NotificationReceiver
        fields = (
            "id",
            "project",
            "channel",
            "channel_type",
            "channel_code",
            "channel_name",
            "channel_is_active",
            "name",
            "webhook_url",
            "target_address",
            "is_active",
            "created_at",
            "updated_at",
        )
        read_only_fields = ("id", "created_at", "updated_at")
        extra_kwargs = {
            "project": {"required": False},
            "target_address": {"required": False, "allow_blank": False, "max_length": 1000},
            "is_active": {"default": True},
        }

    def validate(self, attrs):
        request_project = self.context.get("project")
        requested_project = attrs.get("project")
        if request_project is not None:
            if requested_project is not None and requested_project.pk != request_project.pk:
                raise serializers.ValidationError({"project": "请求体 project 必须与 URL 中的项目一致"})
            if self.instance is not None and self.instance.project_id != request_project.pk:
                raise serializers.ValidationError({"project": "通知对象必须属于当前项目"})
            attrs["project"] = request_project
        elif self.instance is not None and requested_project is not None and requested_project.pk != self.instance.project_id:
            raise serializers.ValidationError({"project": "不允许变更通知对象所属项目"})

        if "webhook_url" in attrs:
            raise serializers.ValidationError({"webhook_url": "通知仅支持邮件，不接受 Webhook 配置"})

        supplied_channel = attrs.pop("channel", None)
        supplied_type = attrs.pop("channel_type", None)
        if supplied_type is not None and supplied_type != "email":
            raise serializers.ValidationError({"channel_type": "通知仅支持 email 渠道"})
        if supplied_channel is not None and supplied_channel.channel_code not in SUPPORTED_CHANNEL_CODES:
            raise serializers.ValidationError({"channel": "通知仅支持内置 email 渠道"})

        email_channel = get_builtin_email_channel()
        if supplied_channel is not None and supplied_channel.pk != email_channel.pk:
            raise serializers.ValidationError({"channel": "通知仅支持内置 email 渠道"})
        is_new_or_switch = self.instance is None or self.instance.channel_id != email_channel.pk
        if is_new_or_switch and not email_channel.is_active:
            raise serializers.ValidationError({"channel": "邮件通知渠道已停用，不能新建或切换接收组"})
        attrs["channel"] = email_channel

        if "target_address" in attrs:
            try:
                attrs["target_address"] = ",".join(parse_recipients(attrs["target_address"]))
            except NotificationDeliveryError as exc:
                raise serializers.ValidationError({"target_address": exc.safe_message}) from exc
        elif self.instance is None:
            raise serializers.ValidationError({"target_address": "请至少填写一个收件人邮箱"})
        return attrs


class EmailConfigSerializer(serializers.ModelSerializer):
    """SMTP configuration without exposing its authorization code."""

    smtp_password = serializers.CharField(
        required=False,
        allow_blank=True,
        write_only=True,
        max_length=255,
    )
    has_password = serializers.SerializerMethodField(read_only=True)
    is_effective = serializers.SerializerMethodField(read_only=True)

    class Meta:
        model = EmailConfig
        fields = (
            "id",
            "name",
            "smtp_server",
            "port",
            "sender_email",
            "smtp_password",
            "has_password",
            "use_ssl",
            "is_active",
            "is_effective",
            "created_at",
            "updated_at",
        )
        read_only_fields = ("id", "is_effective", "created_at", "updated_at")

    def get_is_effective(self, instance) -> bool:
        if not instance.is_active:
            return False
        cache_key = "_effective_email_config_id"
        if cache_key not in self.context:
            self.context[cache_key] = (
                EmailConfig.objects.filter(is_active=True)
                .order_by("-updated_at", "-pk")
                .values_list("pk", flat=True)
                .first()
            )
        effective_id = self.context[cache_key]
        return instance.pk == effective_id

    def get_has_password(self, instance) -> bool:
        return bool(instance.smtp_password)

    def validate_smtp_server(self, value: str) -> str:
        if not _is_valid_smtp_host(value):
            raise serializers.ValidationError("SMTP 服务器地址格式不正确")
        return value.strip()

    def validate_port(self, value: int) -> int:
        if not 1 <= value <= 65535:
            raise serializers.ValidationError("SMTP 端口必须在 1 到 65535 之间")
        return value

    def validate(self, attrs):
        if self.instance is None and _should_keep_password(attrs.get("smtp_password")):
            raise serializers.ValidationError({"smtp_password": "创建时请填写 SMTP 授权码"})
        return attrs

    def update(self, instance, validated_data):
        if "smtp_password" in validated_data and _should_keep_password(validated_data["smtp_password"]):
            validated_data.pop("smtp_password", None)
        return super().update(instance, validated_data)
