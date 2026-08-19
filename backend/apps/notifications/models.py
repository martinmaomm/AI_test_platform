"""
消息通知体系：全局渠道 + 项目接收对象
"""
from django.db import models


class NotificationChannel(models.Model):
    """
    全局渠道配置（钉钉/企微/邮件等）
    表名：notification_channel
    存放渠道元数据与接入说明，供前端展示与接收对象关联
    """
    channel_code = models.CharField(
        max_length=32,
        unique=True,
        db_index=True,
        verbose_name='渠道编码',
        help_text='如 dingtalk, wechat_work, email',
    )
    channel_name = models.CharField(max_length=64, verbose_name='渠道名称')
    description = models.TextField(
        blank=True,
        default='',
        verbose_name='配置引导说明',
        help_text='接入步骤、Webhook 获取方式等',
    )
    is_active = models.BooleanField(default=True, verbose_name='是否启用')
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='创建时间')
    updated_at = models.DateTimeField(auto_now=True, verbose_name='更新时间')

    class Meta:
        app_label = 'notifications'
        db_table = 'notification_channel'
        ordering = ['channel_code']
        verbose_name = '全局渠道'
        verbose_name_plural = '全局渠道'

    def __str__(self):
        return f"{self.channel_name} ({self.channel_code})"


class NotificationReceiver(models.Model):
    """
    项目级通知接收对象（Webhook 群组、邮件组等）
    表名：notification_receiver
    必须归属项目，关联全局渠道
    """
    project = models.ForeignKey(
        'projects.Project',
        on_delete=models.CASCADE,
        related_name='notification_receivers',
        verbose_name='所属项目',
    )
    channel = models.ForeignKey(
        NotificationChannel,
        on_delete=models.CASCADE,
        related_name='receivers',
        verbose_name='渠道类型',
    )
    name = models.CharField(max_length=100, verbose_name='通知组名称')
    webhook_url = models.URLField(
        max_length=2000,
        blank=True,
        default='',
        verbose_name='Webhook 地址',
    )
    target_address = models.CharField(
        max_length=1000,
        blank=True,
        default='',
        verbose_name='收件人邮箱',
        help_text='邮件渠道时使用，多个邮箱用英文逗号分隔',
    )
    is_active = models.BooleanField(default=True, verbose_name='是否启用')
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='创建时间')
    updated_at = models.DateTimeField(auto_now=True, verbose_name='更新时间')

    class Meta:
        app_label = 'notifications'
        db_table = 'notification_receiver'
        ordering = ['-created_at']
        verbose_name = '通知接收对象'
        verbose_name_plural = '通知接收对象'

    def __str__(self):
        return f"{self.channel.channel_name} - {self.name}"

    @property
    def channel_type(self):
        """兼容旧接口：返回 channel_code"""
        return self.channel.channel_code if self.channel else ''


class EmailConfig(models.Model):
    """邮件服务配置：SMTP 动态配置，供通知邮件发送使用"""

    name = models.CharField(max_length=100, verbose_name='配置名称', help_text='如：公司邮件服务器')
    smtp_server = models.CharField(max_length=255, verbose_name='SMTP 服务器')
    port = models.PositiveIntegerField(default=465, verbose_name='端口')
    sender_email = models.EmailField(max_length=255, verbose_name='发件邮箱')
    smtp_password = models.CharField(max_length=255, verbose_name='SMTP 授权码')
    use_ssl = models.BooleanField(default=True, verbose_name='使用 SSL')
    is_active = models.BooleanField(default=True, verbose_name='是否启用')
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='创建时间')
    updated_at = models.DateTimeField(auto_now=True, verbose_name='更新时间')

    class Meta:
        app_label = 'notifications'
        db_table = 'notifications_email_config'
        ordering = ['-created_at']
        verbose_name = '邮件服务配置'
        verbose_name_plural = '邮件服务配置'

    def __str__(self):
        return f"{self.name} ({self.sender_email})"
