"""
通知对象序列化器
"""

from rest_framework import serializers
from .models import NotificationChannel, NotificationReceiver, EmailConfig
from .delivery import (
    NotificationDeliveryError,
    SUPPORTED_CHANNEL_CODES,
    parse_recipients,
    validate_webhook_url,
)


def _should_ignore_address(value):
    """空字符串或脱敏串（含 ***）时不覆写数据库中的真实地址"""
    if value is None:
        return True
    s = (value if isinstance(value, str) else str(value)).strip()
    return s == "" or "***" in s


def _changes_existing_transport(instance, attrs):
    """Whether an existing receiver update actually changes a delivery target."""
    for field in ("webhook_url", "target_address"):
        if field not in attrs or _should_ignore_address(attrs[field]):
            continue
        current = getattr(instance, field, "") or ""
        if field == "target_address":
            try:
                if parse_recipients(attrs[field]) == parse_recipients(current):
                    continue
            except NotificationDeliveryError:
                pass
        elif attrs[field] == current:
            continue
        return True
    return False


class NotificationChannelSerializer(serializers.ModelSerializer):
    """全局渠道 ModelSerializer（面向管理员）"""

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
        read_only_fields = ("id", "created_at", "updated_at")
        extra_kwargs = {
            "description": {"required": False, "allow_blank": True},
            "is_active": {"default": True},
        }

    def update(self, instance, validated_data):
        if (
            "channel_code" in validated_data
            and validated_data["channel_code"] != instance.channel_code
        ):
            raise serializers.ValidationError({"channel_code": "不允许修改渠道标识"})
        return super().update(instance, validated_data)

    def validate_channel_code(self, value):
        if value not in SUPPORTED_CHANNEL_CODES:
            raise serializers.ValidationError(
                "仅支持 dingtalk、wechat_work 和 email 渠道"
            )
        return value


class NotificationReceiverSerializer(serializers.ModelSerializer):
    """项目级通知接收对象 ModelSerializer；更新时空/脱敏不覆写原值。支持 channel_type 兼容旧前端。"""

    channel_code = serializers.CharField(source="channel.channel_code", read_only=True)
    channel_name = serializers.CharField(source="channel.channel_name", read_only=True)
    channel_type = serializers.ChoiceField(
        choices=sorted(SUPPORTED_CHANNEL_CODES),
        write_only=True,
        required=False,
        help_text="兼容：dingtalk/wechat_work/email，与 channel 二选一",
    )

    class Meta:
        model = NotificationReceiver
        fields = (
            "id",
            "project",
            "channel",
            "channel_type",
            "channel_code",
            "channel_name",
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
            "name": {"max_length": 100},
            "webhook_url": {"required": False, "allow_blank": True},
            "target_address": {
                "required": False,
                "allow_blank": True,
                "max_length": 1000,
            },
            "is_active": {"default": True},
        }

    def validate(self, attrs):
        request_project = self.context.get("project")
        requested_project = attrs.get("project")
        if request_project is not None:
            if (
                requested_project is not None
                and requested_project.pk != request_project.pk
            ):
                raise serializers.ValidationError(
                    {"project": "请求体 project 必须与 URL 中的项目一致"}
                )
            if (
                self.instance is not None
                and self.instance.project_id != request_project.pk
            ):
                raise serializers.ValidationError(
                    {"project": "通知对象必须属于当前项目"}
                )
            attrs["project"] = request_project
        elif (
            self.instance is not None
            and requested_project is not None
            and requested_project.pk != self.instance.project_id
        ):
            raise serializers.ValidationError({"project": "不允许变更通知对象所属项目"})

        channel = attrs.get("channel")
        channel_type = attrs.pop("channel_type", None)
        if channel_type:
            type_channel = NotificationChannel.objects.filter(
                channel_code=channel_type
            ).first()
            if type_channel is None:
                raise serializers.ValidationError({"channel_type": "指定渠道不存在"})
            if channel is not None and channel.pk != type_channel.pk:
                raise serializers.ValidationError(
                    {"channel": "channel 与 channel_type 必须一致"}
                )
            attrs["channel"] = type_channel
        channel = attrs.get("channel") or (
            self.instance.channel if self.instance else None
        )
        if not channel:
            raise serializers.ValidationError(
                {"channel": "请选择渠道类型（channel 或 channel_type）"}
            )
        channel_code = getattr(channel, "channel_code", "")
        changing_channel = (
            self.instance is None or channel.pk != self.instance.channel_id
        )
        if channel_code not in SUPPORTED_CHANNEL_CODES:
            raise serializers.ValidationError(
                {"channel": "仅支持 dingtalk、wechat_work 和 email 渠道"}
            )
        is_disabled = not channel.is_active
        if is_disabled and changing_channel:
            raise serializers.ValidationError(
                {"channel": "该通知渠道不可用于新建或切换接收对象"}
            )
        if channel_code == "email":
            supplied_address = attrs.get("target_address")
            if supplied_address is not None and not _should_ignore_address(
                supplied_address
            ):
                try:
                    attrs["target_address"] = ",".join(
                        parse_recipients(supplied_address)
                    )
                except NotificationDeliveryError as exc:
                    raise serializers.ValidationError(
                        {"target_address": str(exc)}
                    ) from exc
            elif (
                changing_channel
                or not self.instance
                or not (getattr(self.instance, "target_address", None) or "").strip()
            ):
                raise serializers.ValidationError(
                    {
                        "target_address": "邮件渠道请填写收件人邮箱，多个邮箱可用逗号、分号或换行分隔"
                    }
                )
        else:
            supplied_url = attrs.get("webhook_url")
            if supplied_url is not None and not _should_ignore_address(supplied_url):
                try:
                    attrs["webhook_url"] = validate_webhook_url(
                        channel_code, supplied_url
                    )
                except NotificationDeliveryError as exc:
                    raise serializers.ValidationError(
                        {"webhook_url": str(exc)}
                    ) from exc
            elif (
                changing_channel
                or not self.instance
                or not (getattr(self.instance, "webhook_url", None) or "").strip()
            ):
                raise serializers.ValidationError(
                    {"webhook_url": "请填写 Webhook 地址"}
                )
        if is_disabled and _changes_existing_transport(self.instance, attrs):
            raise serializers.ValidationError({"channel": "该通知渠道不可修改传输配置"})
        return attrs

    def update(self, instance, validated_data):
        if "webhook_url" in validated_data and _should_ignore_address(
            validated_data["webhook_url"]
        ):
            validated_data.pop("webhook_url", None)
        if "target_address" in validated_data and _should_ignore_address(
            validated_data["target_address"]
        ):
            validated_data.pop("target_address", None)
        return super().update(instance, validated_data)

    def to_representation(self, instance):
        data = super().to_representation(instance)
        if getattr(instance, "webhook_url", None):
            data["webhook_url"] = "***"
        return data


def _should_ignore_password(value):
    """空字符串或脱敏串时不覆写数据库中的密码"""
    if value is None:
        return True
    s = (value if isinstance(value, str) else str(value)).strip()
    return s == "" or "***" in s


class EmailConfigSerializer(serializers.ModelSerializer):
    """邮件服务配置序列化器；smtp_password 返回脱敏，更新时空/*** 不覆写。"""

    class Meta:
        model = EmailConfig
        fields = (
            "id",
            "name",
            "smtp_server",
            "port",
            "sender_email",
            "smtp_password",
            "use_ssl",
            "is_active",
            "created_at",
            "updated_at",
        )
        read_only_fields = ("id", "created_at", "updated_at")
        extra_kwargs = {
            "smtp_password": {"required": False, "allow_blank": True},
        }

    def to_representation(self, instance):
        data = super().to_representation(instance)
        if instance and getattr(instance, "smtp_password", None):
            data["smtp_password"] = "***"
        return data

    def validate(self, attrs):
        if not self.instance and _should_ignore_password(attrs.get("smtp_password")):
            raise serializers.ValidationError(
                {"smtp_password": "创建时请填写 SMTP 授权码"}
            )
        return attrs

    def validate_port(self, value):
        if not 1 <= value <= 65535:
            raise serializers.ValidationError("SMTP 端口必须在 1 到 65535 之间")
        return value

    def update(self, instance, validated_data):
        if "smtp_password" in validated_data and _should_ignore_password(
            validated_data["smtp_password"]
        ):
            validated_data.pop("smtp_password", None)
        return super().update(instance, validated_data)
