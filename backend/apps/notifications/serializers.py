"""
通知对象序列化器
"""
from rest_framework import serializers
from .models import NotificationChannel, NotificationReceiver, EmailConfig


def _should_ignore_address(value):
    """空字符串或脱敏串（含 ***）时不覆写数据库中的真实地址"""
    if value is None:
        return True
    s = (value if isinstance(value, str) else str(value)).strip()
    return s == '' or '***' in s


class NotificationChannelSerializer(serializers.ModelSerializer):
    """全局渠道 ModelSerializer（面向管理员）"""

    class Meta:
        model = NotificationChannel
        fields = (
            'id',
            'channel_code',
            'channel_name',
            'description',
            'is_active',
            'created_at',
            'updated_at',
        )
        read_only_fields = ('id', 'created_at', 'updated_at')
        extra_kwargs = {
            'description': {'required': False, 'allow_blank': True},
            'is_active': {'default': True},
        }

    def update(self, instance, validated_data):
        validated_data.pop('channel_code', None)  # 禁止修改渠道标识
        return super().update(instance, validated_data)


class NotificationReceiverSerializer(serializers.ModelSerializer):
    """项目级通知接收对象 ModelSerializer；更新时空/脱敏不覆写原值。支持 channel_type 兼容旧前端。"""

    channel_code = serializers.CharField(source='channel.channel_code', read_only=True)
    channel_name = serializers.CharField(source='channel.channel_name', read_only=True)
    channel_type = serializers.CharField(write_only=True, required=False, help_text='兼容：dingtalk/wechat_work/email，与 channel 二选一')

    class Meta:
        model = NotificationReceiver
        fields = (
            'id',
            'project',
            'channel',
            'channel_type',
            'channel_code',
            'channel_name',
            'name',
            'webhook_url',
            'target_address',
            'is_active',
            'created_at',
            'updated_at',
        )
        read_only_fields = ('id', 'created_at', 'updated_at')
        extra_kwargs = {
            'name': {'max_length': 100},
            'webhook_url': {'required': False, 'allow_blank': True},
            'target_address': {'required': False, 'allow_blank': True, 'max_length': 1000},
            'is_active': {'default': True},
        }

    def validate(self, attrs):
        channel = attrs.get('channel')
        channel_type = attrs.pop('channel_type', None)
        if not channel and channel_type:
            ch = NotificationChannel.objects.filter(channel_code=channel_type).first()
            if ch:
                attrs['channel'] = ch
        channel = attrs.get('channel') or (self.instance.channel if self.instance else None)
        if not channel:
            raise serializers.ValidationError({'channel': '请选择渠道类型（channel 或 channel_type）'})
        channel_code = getattr(channel, 'channel_code', 'dingtalk')
        if channel_code == 'email':
            addr = (attrs.get('target_address') or getattr(self.instance, 'target_address', None) or '').strip()
            if not addr or _should_ignore_address(attrs.get('target_address')):
                if not self.instance or not (getattr(self.instance, 'target_address', None) or '').strip():
                    raise serializers.ValidationError({'target_address': '邮件渠道请填写收件人邮箱，多个邮箱用英文逗号分隔'})
        else:
            url = (attrs.get('webhook_url') or getattr(self.instance, 'webhook_url', None) or '').strip()
            if not url or _should_ignore_address(attrs.get('webhook_url')):
                if not self.instance or not (getattr(self.instance, 'webhook_url', None) or '').strip():
                    raise serializers.ValidationError({'webhook_url': '请填写 Webhook 地址'})
        return attrs

    def update(self, instance, validated_data):
        if 'webhook_url' in validated_data and _should_ignore_address(validated_data['webhook_url']):
            validated_data.pop('webhook_url', None)
        if 'target_address' in validated_data and _should_ignore_address(validated_data['target_address']):
            validated_data.pop('target_address', None)
        return super().update(instance, validated_data)


def _should_ignore_password(value):
    """空字符串或脱敏串时不覆写数据库中的密码"""
    if value is None:
        return True
    s = (value if isinstance(value, str) else str(value)).strip()
    return s == '' or '***' in s


class EmailConfigSerializer(serializers.ModelSerializer):
    """邮件服务配置序列化器；smtp_password 返回脱敏，更新时空/*** 不覆写。"""

    class Meta:
        model = EmailConfig
        fields = (
            'id',
            'name',
            'smtp_server',
            'port',
            'sender_email',
            'smtp_password',
            'use_ssl',
            'is_active',
            'created_at',
            'updated_at',
        )
        read_only_fields = ('id', 'created_at', 'updated_at')
        extra_kwargs = {
            'smtp_password': {'required': False, 'allow_blank': True},
        }

    def to_representation(self, instance):
        data = super().to_representation(instance)
        if instance and getattr(instance, 'smtp_password', None):
            data['smtp_password'] = '***'
        return data

    def validate(self, attrs):
        if not self.instance and not (attrs.get('smtp_password') or '').strip():
            raise serializers.ValidationError({'smtp_password': '创建时请填写 SMTP 授权码'})
        return attrs

    def update(self, instance, validated_data):
        if 'smtp_password' in validated_data and _should_ignore_password(validated_data['smtp_password']):
            validated_data.pop('smtp_password', None)
        return super().update(instance, validated_data)
