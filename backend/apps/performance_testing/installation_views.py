"""Public, secret-free endpoints used by the performance-node bootstrap."""

from django.http import HttpResponse
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import csrf_exempt
from rest_framework.permissions import AllowAny
from rest_framework.views import APIView

from .installation import (
    ReleaseConfigurationError, ca_certificate_bytes, installer_script_bytes,
    upgrade_script_bytes,
)


def _plain_response(content, *, status):
    response = HttpResponse(content, status=status, content_type='text/plain; charset=utf-8')
    response['Cache-Control'] = 'no-store'
    response['X-Content-Type-Options'] = 'nosniff'
    return response


class InstallScriptView(APIView):
    authentication_classes = ()
    permission_classes = (AllowAny,)

    def get(self, request):
        if request.META.get('QUERY_STRING'):
            return _plain_response('安装脚本地址不接受查询参数。\n', status=400)
        try:
            content = installer_script_bytes()
        except ReleaseConfigurationError as exc:
            return _plain_response(f'性能节点安装脚本暂不可用：{exc}\n', status=503)
        response = HttpResponse(content, content_type='text/x-shellscript')
        response['Cache-Control'] = 'no-store'
        response['X-Content-Type-Options'] = 'nosniff'
        response['Content-Disposition'] = 'inline; filename="install-node.sh"'
        return response


class UpgradeScriptView(View):
    """Public static code only; target instructions remain admin-protected."""

    http_method_names = ('get', 'head')

    def get(self, request):
        if request.META.get('QUERY_STRING'):
            return _plain_response('升级脚本地址不接受查询参数。\n', status=400)
        try:
            content = upgrade_script_bytes()
        except ReleaseConfigurationError:
            return _plain_response('性能节点升级脚本暂不可用。\n', status=503)
        response = HttpResponse(content, content_type='text/x-python; charset=utf-8')
        response['Cache-Control'] = 'no-store'
        response['X-Content-Type-Options'] = 'nosniff'
        response['Content-Disposition'] = 'inline; filename="upgrade-node.py"'
        return response

    head = get


@method_decorator(csrf_exempt, name='dispatch')
class InstallCACertificateView(View):
    """Serve the public PEM without DRF renderer/content negotiation."""

    http_method_names = ('get', 'head')

    def get(self, request):
        if request.META.get('QUERY_STRING'):
            return _plain_response('CA 证书地址不接受查询参数。\n', status=400)
        try:
            content = ca_certificate_bytes()
        except ReleaseConfigurationError as exc:
            return _plain_response(f'性能节点 CA 证书暂不可用：{exc}\n', status=503)
        if content is None:
            return _plain_response('未配置性能节点专用 CA 证书。\n', status=404)
        response = HttpResponse(content, content_type='application/x-pem-file')
        response['Cache-Control'] = 'no-store'
        response['X-Content-Type-Options'] = 'nosniff'
        response['Content-Disposition'] = 'inline; filename="ca.pem"'
        return response

    head = get
