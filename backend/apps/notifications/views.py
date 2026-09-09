"""Email-only notification API views."""

import logging

from django.http import Http404
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import IsAdminUser, IsAuthenticated
from rest_framework.response import Response

from projects.models import Project
from web_testing.project_access import DELETE, EDIT, EXECUTE, READ, get_project_for_user

from .delivery import NotificationDeliveryError, parse_recipients, send_email, test_smtp_connection
from .models import EmailConfig, NotificationChannel, NotificationReceiver
from .serializers import EmailConfigSerializer, NotificationChannelSerializer, NotificationReceiverSerializer


logger = logging.getLogger(__name__)


class NotificationChannelViewSet(viewsets.ReadOnlyModelViewSet):
    """Expose the built-in email channel state; global channel CRUD is retired."""

    serializer_class = NotificationChannelSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return NotificationChannel.objects.filter(channel_code="email").order_by("channel_code")


class NotificationReceiverViewSet(viewsets.ModelViewSet):
    """Project-scoped email receiver CRUD and actual SMTP acceptance test."""

    serializer_class = NotificationReceiverSerializer
    permission_classes = [IsAuthenticated]

    def _capability(self):
        if self.action == "test_by_id":
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
        body_project_id = self.request.data.get("project_id") if hasattr(self.request.data, "get") else None
        body_project = self.request.data.get("project") if hasattr(self.request.data, "get") else None
        if body_project_id not in (None, "") and body_project not in (None, "") and str(body_project_id) != str(body_project):
            raise ValidationError({"project_id": "请求体 project_id 与 project 必须一致"})
        if body_project_id in (None, ""):
            body_project_id = body_project
        query_project_id = self.request.query_params.get("project_id")
        if url_project_id not in (None, ""):
            if body_project_id not in (None, "") and str(body_project_id) != str(url_project_id):
                raise ValidationError({"project": "请求体 project 必须与 URL 中的项目一致"})
            if query_project_id not in (None, "") and str(query_project_id) != str(url_project_id):
                raise ValidationError({"project_id": "query project_id 必须与 URL 中的项目一致"})
            return url_project_id
        if body_project_id not in (None, "") and query_project_id not in (None, "") and str(body_project_id) != str(query_project_id):
            raise ValidationError({"project_id": "请求体和 query 中的 project_id 必须一致"})
        return body_project_id if body_project_id not in (None, "") else query_project_id

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
        queryset = NotificationReceiver.objects.select_related("channel", "project").filter(channel__channel_code="email")
        project = self._request_project()
        if project is not None:
            return queryset.filter(project_id=project.pk)
        if self.kwargs.get("pk") is None:
            raise ValidationError({"project_id": "请提供 project_id"})
        receiver = queryset.filter(pk=self.kwargs["pk"]).first()
        if receiver is None:
            return queryset.none()
        self._project_for_user(receiver.project_id)
        return queryset.filter(pk=receiver.pk)

    @action(detail=True, methods=["post"], url_path="test")
    def test_by_id(self, request, pk=None, project_id=None, **kwargs):
        receiver = self.get_object()
        channel = receiver.channel
        if not receiver.is_active or not channel.is_active:
            return Response(
                {"success": False, "message": "邮件接收组或邮件渠道已停用", "detail": "邮件接收组或邮件渠道已停用"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            recipients = parse_recipients(receiver.target_address or "")
            send_email(
                subject="【自动化测试平台】邮件接收组测试",
                body="这是一封自动化测试平台测试邮件。SMTP 服务器已接受后，仍请在收件箱确认实际送达。",
                recipients=recipients,
            )
            return Response(
                {"success": True, "message": "SMTP 服务器已接受测试邮件，请在收件箱确认送达。"},
                status=status.HTTP_200_OK,
            )
        except NotificationDeliveryError as exc:
            logger.warning("email receiver test failed: receiver=%s reason=%s", receiver.pk, exc.safe_message)
            return Response(
                {"success": False, "message": exc.safe_message, "detail": exc.safe_message},
                status=status.HTTP_400_BAD_REQUEST,
            )


class EmailConfigViewSet(viewsets.ModelViewSet):
    """Admin-only SMTP configuration CRUD and connection/authentication test."""

    serializer_class = EmailConfigSerializer
    permission_classes = [IsAdminUser]

    def get_queryset(self):
        return EmailConfig.objects.all().order_by("-updated_at", "-pk")

    @action(detail=True, methods=["post"], url_path="test")
    def test_connection(self, request, pk=None):
        config = self.get_object()
        try:
            test_smtp_connection(config)
            return Response(
                {"detail": "SMTP 连接与认证成功，未发送测试邮件。"},
                status=status.HTTP_200_OK,
            )
        except NotificationDeliveryError as exc:
            logger.warning("SMTP connection test failed: config=%s reason=%s", config.pk, exc.safe_message)
            return Response({"detail": exc.safe_message}, status=status.HTTP_400_BAD_REQUEST)
