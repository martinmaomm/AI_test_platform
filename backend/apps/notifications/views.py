"""
消息渠道 ViewSet：CRUD + 测试连接
"""
import logging
import requests
from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from django.core.mail import get_connection, EmailMessage

from .models import NotificationChannel, NotificationReceiver, EmailConfig
from .serializers import (
    NotificationChannelSerializer,
    NotificationReceiverSerializer,
    EmailConfigSerializer,
)

logger = logging.getLogger(__name__)


class NotificationChannelViewSet(viewsets.ModelViewSet):
    """全局渠道 CRUD（面向管理员）"""
    serializer_class = NotificationChannelSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        qs = NotificationChannel.objects.all()
        is_active = self.request.query_params.get('is_active')
        if is_active is not None:
            qs = qs.filter(is_active=str(is_active).lower() in ('true', '1', 'yes'))
        return qs


class NotificationReceiverViewSet(viewsets.ModelViewSet):
    """项目级通知接收对象 CRUD；支持 test_connection 校验 Webhook URL。project_id 过滤。"""
    serializer_class = NotificationReceiverSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        qs = NotificationReceiver.objects.select_related('channel', 'project').all()
        project_id_raw = self.kwargs.get('project_id') or self.request.query_params.get('project_id')
        if project_id_raw not in (None, ''):
            try:
                project_id = int(project_id_raw)
                direct_ids = set(qs.filter(project_id=project_id).values_list('id', flat=True))
                try:
                    from scheduled_tasks.models import ScheduledTask
                    task_receiver_ids = set(
                        ScheduledTask.objects.filter(project_id=project_id)
                        .values_list('notice_targets', flat=True)
                    )
                    task_receiver_ids.discard(None)
                    all_ids = direct_ids | task_receiver_ids
                except Exception:
                    all_ids = direct_ids
                qs = qs.filter(id__in=all_ids) if all_ids else qs.filter(project_id=project_id)
            except (ValueError, TypeError):
                qs = qs.none()
        return qs

    def perform_create(self, serializer):
        instance = serializer.save()
        if not instance.created_by_id and self.request.user:
            pass  # 审计字段由模型 auto_now 处理，created_by 可为空

    @action(detail=False, methods=['post'], url_path='test_connection')
    def test_connection(self, request):
        """
        测试连接：接收 webhook_url（及可选 channel_type），
        向该 URL 发送一条简单 JSON 消息，根据响应状态码返回成功或失败。
        """
        webhook_url = (request.data.get('webhook_url') or '').strip()
        if not webhook_url:
            return Response(
                {'detail': '请提供 webhook_url'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        channel_type = request.data.get('channel_type') or 'dingtalk'

        if channel_type == 'dingtalk':
            payload = {'msgtype': 'text', 'text': {'content': 'AITS 消息渠道测试'}}
        else:
            payload = {'msgtype': 'text', 'text': {'content': 'AITS 消息渠道测试'}}

        try:
            resp = requests.post(
                webhook_url,
                json=payload,
                timeout=10,
                headers={'Content-Type': 'application/json'},
            )
            if resp.status_code == 200:
                return Response({'detail': '连接成功'}, status=status.HTTP_200_OK)
            return Response(
                {'detail': f'请求失败，状态码: {resp.status_code}'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        except requests.RequestException as e:
            logger.warning('test_connection request failed: %s', e)
            return Response(
                {'detail': f'连接失败: {str(e)}'},
                status=status.HTTP_400_BAD_REQUEST,
            )

    @action(detail=True, methods=['post'], url_path='test')
    def test_by_id(self, request, pk=None, project_id=None, **kwargs):
        """按渠道 ID 测试：从数据库读取真实 Webhook 发送，不依赖前端传明文，防抓包。"""
        try:
            return self._test_by_id_impl(request, pk)
        except Exception as e:
            logger.warning('test_by_id top-level error: %s', e, exc_info=True)
            return Response(
                {'success': False, 'message': f'测试失败: {str(e)}', 'detail': f'测试失败: {str(e)}'},
                status=status.HTTP_400_BAD_REQUEST,
            )

    def _test_by_id_impl(self, request, pk):
        try:
            receiver = self.get_object()
        except Exception as e:
            logger.warning('test_by_id get_object failed: %s', e)
            msg = f'获取接收对象失败: {str(e)}'
            return Response(
                {'success': False, 'message': msg, 'detail': msg},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # 通过外键获取 channel_code，适配重构后的数据模型
        try:
            channel_code = receiver.channel.channel_code if receiver.channel else 'dingtalk'
        except AttributeError as e:
            logger.warning('test_by_id channel_code access failed: %s', e)
            msg = '渠道配置异常，请检查该接收对象关联的渠道是否存在'
            return Response(
                {'success': False, 'message': msg, 'detail': msg},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # 邮件渠道：解析收件人并发送测试邮件
        if channel_code == 'email':
            return self._test_email_channel(receiver)

        webhook_url = (receiver.webhook_url or '').strip()
        if not webhook_url:
            msg = '该渠道未配置 Webhook 地址'
            return Response(
                {'success': False, 'message': msg, 'detail': msg},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if channel_code == 'dingtalk':
            payload = {'msgtype': 'text', 'text': {'content': 'AITS 消息渠道测试'}}
        elif channel_code == 'wechat_work':
            payload = {'msgtype': 'text', 'text': {'content': 'AITS 消息渠道测试'}}
        else:
            payload = {'msgtype': 'text', 'text': {'content': 'AITS 消息渠道测试'}}

        try:
            resp = requests.post(
                webhook_url,
                json=payload,
                timeout=10,
                headers={'Content-Type': 'application/json'},
            )
            if resp.status_code == 200:
                return Response({'success': True, 'message': '连接成功'}, status=status.HTTP_200_OK)
            msg = f'测试发送失败: 请求返回状态码 {resp.status_code}'
            return Response(
                {'success': False, 'message': msg, 'detail': msg},
                status=status.HTTP_400_BAD_REQUEST,
            )
        except requests.RequestException as e:
            logger.warning('test_by_id request failed: %s', e)
            msg = f'测试发送失败: {str(e)}'
            return Response(
                {'success': False, 'message': msg, 'detail': msg},
                status=status.HTTP_400_BAD_REQUEST,
            )
        except Exception as e:
            logger.warning('test_by_id unexpected error: %s', e, exc_info=True)
            msg = f'测试发送失败: {str(e)}'
            return Response(
                {'success': False, 'message': msg, 'detail': msg},
                status=status.HTTP_400_BAD_REQUEST,
            )

    def _test_email_channel(self, receiver):
        """邮件渠道测试：解析收件人，使用 EmailConfig 发送测试邮件。"""
        addr_str = (getattr(receiver, 'target_address', None) or '').strip()
        recipient_list = [
            email.strip()
            for email in addr_str.replace(';', ',').split(',')
            if email.strip()
        ]
        if not recipient_list:
            msg = '未提取到有效的收件人邮箱地址'
            return Response(
                {'success': False, 'message': msg, 'detail': msg},
                status=status.HTTP_400_BAD_REQUEST,
            )

        config = EmailConfig.objects.filter(is_active=True).order_by('-updated_at').first()
        if not config:
            msg = '未配置邮件服务，请先在系统设置中配置 SMTP'
            return Response(
                {'success': False, 'message': msg, 'detail': msg},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            conn = get_connection(
                backend='django.core.mail.backends.smtp.EmailBackend',
                host=config.smtp_server,
                port=config.port,
                username=config.sender_email,
                password=config.smtp_password,
                use_ssl=config.use_ssl,
                use_tls=not config.use_ssl,
                fail_silently=False,
            )
            msg = EmailMessage(
                subject='【AITS 智能测试系统】邮件通道测试',
                body='您好！这是一封来自 AITS 系统的测试邮件。如果您收到此邮件，说明您的邮件接收通道已配置成功！',
                from_email=config.sender_email,
                to=recipient_list,
                connection=conn,
            )
            msg.send(fail_silently=False)
            return Response(
                {'success': True, 'message': '测试邮件发送成功！'},
                status=status.HTTP_200_OK,
            )
        except Exception as e:
            logger.warning('test_email_channel send failed: %s', e, exc_info=True)
            return Response(
                {'success': False, 'message': f'邮件发送失败: {str(e)}', 'detail': str(e)},
                status=status.HTTP_400_BAD_REQUEST,
            )


class EmailConfigViewSet(viewsets.ModelViewSet):
    """邮件服务配置 CRUD"""
    queryset = EmailConfig.objects.all()
    serializer_class = EmailConfigSerializer
    permission_classes = [IsAuthenticated]

    @action(detail=True, methods=['post'], url_path='test')
    def test_connection(self, request, pk=None):
        """测试 SMTP 连接"""
        config = self.get_object()
        try:
            conn = get_connection(
                backend='django.core.mail.backends.smtp.EmailBackend',
                host=config.smtp_server,
                port=config.port,
                username=config.sender_email,
                password=config.smtp_password,
                use_ssl=config.use_ssl,
                use_tls=not config.use_ssl,
                fail_silently=False,
            )
            conn.open()
            conn.close()
            return Response({'detail': '连接成功'}, status=status.HTTP_200_OK)
        except Exception as e:
            logger.warning('EmailConfig test_connection failed: %s', e)
            return Response(
                {'detail': f'连接失败: {str(e)}'},
                status=status.HTTP_400_BAD_REQUEST,
            )
