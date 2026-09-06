"""
消息渠道 ViewSet：CRUD + 测试连接
"""

import logging
from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAdminUser, IsAuthenticated
from rest_framework.exceptions import ValidationError
from django.http import Http404

from .models import NotificationChannel, NotificationReceiver, EmailConfig
from .serializers import (
    NotificationChannelSerializer,
    NotificationReceiverSerializer,
    EmailConfigSerializer,
)
from .delivery import (
    NotificationDeliveryError,
    SUPPORTED_CHANNEL_CODES,
    parse_recipients,
    send_email,
    send_webhook,
    test_smtp_connection,
)
from projects.models import Project
from web_testing.project_access import DELETE, EDIT, EXECUTE, READ, get_project_for_user

logger = logging.getLogger(__name__)


class NotificationChannelViewSet(viewsets.ModelViewSet):
    """全局渠道 CRUD（面向管理员）"""

    serializer_class = NotificationChannelSerializer
    permission_classes = [IsAuthenticated]

    def get_permissions(self):
        permission_class = (
            IsAuthenticated if self.action in {"list", "retrieve"} else IsAdminUser
        )
        return [permission_class()]

    def get_queryset(self):
        qs = NotificationChannel.objects.all()
        is_active = self.request.query_params.get("is_active")
        if is_active is not None:
            qs = qs.filter(is_active=str(is_active).lower() in ("true", "1", "yes"))
        return qs

    def perform_destroy(self, instance):
        if instance.receivers.exists():
            raise ValidationError(
                {"detail": "该渠道仍有关联的通知对象，请先停用渠道或处理接收对象"}
            )
        super().perform_destroy(instance)


class NotificationReceiverViewSet(viewsets.ModelViewSet):
    """项目级通知接收对象 CRUD；支持 test_connection 校验 Webhook URL。project_id 过滤。"""

    serializer_class = NotificationReceiverSerializer
    permission_classes = [IsAuthenticated]

    def _capability(self):
        if self.action in {"test_connection", "test_by_id"}:
            return EXECUTE
        return {
            "create": EDIT,
            "update": EDIT,
            "partial_update": EDIT,
            "destroy": DELETE,
        }.get(self.action, READ)

    def _project_for_user(self, project_id, capability=None):
        try:
            project_id = int(project_id)
        except (TypeError, ValueError) as exc:
            raise ValidationError({"project_id": "project_id 必须为有效整数"}) from exc
        if self.request.user.is_superuser:
            project = Project.objects.filter(pk=project_id).first()
            if project is None:
                raise Http404("项目不存在")
            return project
        return get_project_for_user(
            project_id,
            self.request.user,
            capability or self._capability(),
            expected_project_type=None,
        )

    def _request_project_id(self, *, required=False):
        url_project_id = self.kwargs.get("project_id")
        body_project_id = (
            self.request.data.get("project_id")
            if hasattr(self.request.data, "get")
            else None
        )
        body_project = (
            self.request.data.get("project")
            if hasattr(self.request.data, "get")
            else None
        )
        if (
            body_project_id not in (None, "")
            and body_project not in (None, "")
            and str(body_project_id) != str(body_project)
        ):
            raise ValidationError(
                {"project_id": "请求体 project_id 与 project 必须一致"}
            )
        if body_project_id in (None, ""):
            body_project_id = body_project
        query_project_id = self.request.query_params.get("project_id")
        if url_project_id not in (None, ""):
            if body_project_id not in (None, "") and str(body_project_id) != str(
                url_project_id
            ):
                raise ValidationError(
                    {"project": "请求体 project 必须与 URL 中的项目一致"}
                )
            if query_project_id not in (None, "") and str(query_project_id) != str(
                url_project_id
            ):
                raise ValidationError(
                    {"project_id": "query project_id 必须与 URL 中的项目一致"}
                )
            return url_project_id
        if (
            body_project_id not in (None, "")
            and query_project_id not in (None, "")
            and str(body_project_id) != str(query_project_id)
        ):
            raise ValidationError(
                {"project_id": "请求体和 query 中的 project_id 必须一致"}
            )
        return (
            body_project_id
            if body_project_id not in (None, "")
            else query_project_id if query_project_id not in (None, "") else None
        )

    def _request_project(self, *, required=False, capability=None):
        project_id = self._request_project_id(required=required)
        if project_id in (None, ""):
            if required:
                raise ValidationError({"project_id": "请提供 project_id"})
            return None
        return self._project_for_user(project_id, capability)

    def get_serializer_context(self):
        context = super().get_serializer_context()
        if self.action in {"create", "update", "partial_update"}:
            context["project"] = self._request_project(required=True)
        return context

    def get_queryset(self):
        qs = NotificationReceiver.objects.select_related("channel", "project").all()
        project = self._request_project()
        if project is not None:
            return qs.filter(project_id=project.pk)
        if self.kwargs.get("pk") is None:
            raise ValidationError({"project_id": "请提供 project_id"})
        receiver = qs.filter(pk=self.kwargs["pk"]).first()
        if receiver is None:
            return qs.none()
        self._project_for_user(receiver.project_id)
        return qs.filter(pk=receiver.pk)

    def perform_create(self, serializer):
        serializer.save()

    @action(detail=False, methods=["post"], url_path="test_connection")
    def test_connection(self, request, project_id=None, **kwargs):
        """
        测试连接：接收 webhook_url（及可选 channel_type），
        向该 URL 发送一条简单 JSON 消息，根据响应状态码返回成功或失败。
        """
        self._request_project(required=True, capability=EXECUTE)
        webhook_url_value = (
            request.data.get("webhook_url") if hasattr(request.data, "get") else None
        )
        if not isinstance(webhook_url_value, str):
            return Response(
                {"detail": "请提供有效的 Webhook 地址"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        webhook_url = webhook_url_value.strip()
        if not webhook_url:
            return Response(
                {"detail": "请提供 webhook_url"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        channel_type = request.data.get("channel_type") or "dingtalk"
        if not isinstance(channel_type, str):
            return Response(
                {"detail": "请提供有效的渠道类型"}, status=status.HTTP_400_BAD_REQUEST
            )
        if channel_type not in SUPPORTED_CHANNEL_CODES - {"email"}:
            return Response(
                {"detail": "仅支持 dingtalk 或 wechat_work Webhook 测试"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if not NotificationChannel.objects.filter(
            channel_code=channel_type, is_active=True
        ).exists():
            return Response(
                {"detail": "该通知渠道不存在或已禁用"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        payload = {"msgtype": "text", "text": {"content": "AITS 消息渠道测试"}}

        try:
            send_webhook(channel_type, webhook_url, payload)
            return Response({"detail": "连接成功"}, status=status.HTTP_200_OK)
        except NotificationDeliveryError as exc:
            logger.warning("test_connection failed: %s", exc.safe_message)
            return Response(
                {"detail": exc.safe_message}, status=status.HTTP_400_BAD_REQUEST
            )

    @action(detail=True, methods=["post"], url_path="test")
    def test_by_id(self, request, pk=None, project_id=None, **kwargs):
        """按渠道 ID 测试：从数据库读取真实 Webhook 发送，不依赖前端传明文，防抓包。"""
        return self._test_by_id_impl(request, pk)

    def _test_by_id_impl(self, request, pk):
        receiver = self.get_object()

        # 通过外键获取 channel_code，适配重构后的数据模型
        channel_code = receiver.channel.channel_code if receiver.channel else ""
        if (
            not receiver.is_active
            or not receiver.channel
            or not receiver.channel.is_active
        ):
            return Response(
                {
                    "success": False,
                    "message": "通知对象或渠道已禁用",
                    "detail": "通知对象或渠道已禁用",
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        if channel_code not in SUPPORTED_CHANNEL_CODES:
            return Response(
                {
                    "success": False,
                    "message": "不支持的通知渠道",
                    "detail": "不支持的通知渠道",
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        # 邮件渠道：解析收件人并发送测试邮件
        if channel_code == "email":
            return self._test_email_channel(receiver)

        webhook_url = (receiver.webhook_url or "").strip()
        payload = {"msgtype": "text", "text": {"content": "AITS 消息渠道测试"}}

        try:
            send_webhook(channel_code, webhook_url, payload)
            return Response(
                {"success": True, "message": "连接成功"}, status=status.HTTP_200_OK
            )
        except NotificationDeliveryError as exc:
            logger.warning(
                "test_by_id failed: receiver=%s error=%s", receiver.pk, exc.safe_message
            )
            return Response(
                {
                    "success": False,
                    "message": exc.safe_message,
                    "detail": exc.safe_message,
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

    def _test_email_channel(self, receiver):
        """邮件渠道测试：解析收件人，使用 EmailConfig 发送测试邮件。"""
        try:
            recipient_list = parse_recipients(
                getattr(receiver, "target_address", "") or ""
            )
            send_email(
                subject="【AITS 智能测试系统】邮件通道测试",
                body="您好！这是一封来自 AITS 系统的测试邮件。如果您收到此邮件，说明您的邮件接收通道已配置成功！",
                recipients=recipient_list,
            )
            return Response(
                {"success": True, "message": "测试邮件发送成功！"},
                status=status.HTTP_200_OK,
            )
        except NotificationDeliveryError as exc:
            logger.warning(
                "test_email_channel failed: receiver=%s error=%s",
                receiver.pk,
                exc.safe_message,
            )
            return Response(
                {
                    "success": False,
                    "message": exc.safe_message,
                    "detail": exc.safe_message,
                },
                status=status.HTTP_400_BAD_REQUEST,
            )


class EmailConfigViewSet(viewsets.ModelViewSet):
    """邮件服务配置 CRUD"""

    queryset = EmailConfig.objects.all()
    serializer_class = EmailConfigSerializer
    permission_classes = [IsAdminUser]

    @action(detail=True, methods=["post"], url_path="test")
    def test_connection(self, request, pk=None):
        """测试 SMTP 连接"""
        config = self.get_object()
        try:
            test_smtp_connection(config)
            return Response({"detail": "连接成功"}, status=status.HTTP_200_OK)
        except NotificationDeliveryError as exc:
            logger.warning(
                "EmailConfig test_connection failed: config=%s error=%s",
                config.pk,
                exc.safe_message,
            )
            return Response(
                {"detail": exc.safe_message},
                status=status.HTTP_400_BAD_REQUEST,
            )
