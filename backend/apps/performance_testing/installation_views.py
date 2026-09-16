"""Public, secret-free endpoints used by the performance-node bootstrap."""

from django.http import HttpResponse
from rest_framework.permissions import AllowAny
from rest_framework.views import APIView

from .installation import ReleaseConfigurationError, installer_script_bytes


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
