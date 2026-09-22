from rest_framework.authentication import get_authorization_header
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from .auth import NodeTokenAuthentication
from .constants import HEARTBEAT_INTERVAL_SECONDS, NODE_OFFLINE_AFTER_SECONDS
from .constants import AGENT_VERSION, ENGINE_VERSION, PROTOCOL_VERSION
from .parsers import LimitedJSONParser
from .serializers import EnrollmentSerializer, HeartbeatSerializer
from .run_services import RunReportRejected, execution_configuration
from .services import (
    CredentialRejected, VersionMismatch, agent_enrollment_response,
    consume_enrollment, handle_agent_heartbeat,
)


def _version_conflict():
    return Response({
        'success': False,
        'error': {'code': 'version_mismatch', 'message': '节点版本不兼容。'},
    }, status=409)


def _ok(data):
    return Response({'success': True, 'data': data})


def _unauthorized(message):
    return Response({
        'success': False,
        'error': {'code': 'authentication_failed', 'message': message},
    }, status=401)


class AgentEnrollView(APIView):
    authentication_classes = ()
    permission_classes = (AllowAny,)
    parser_classes = (LimitedJSONParser,)

    def post(self, request):
        # Enrollment is authenticated only by the one-time body credential.
        # A platform JWT must never become a machine identity.
        if get_authorization_header(request):
            return _unauthorized('注册接口不接受用户身份凭证。')
        serializer = EnrollmentSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        try:
            node, agent_token = consume_enrollment(data['enrollment_token'], data)
        except VersionMismatch:
            return _version_conflict()
        except CredentialRejected:
            return _unauthorized('注册凭证无效、已过期或已使用。')
        return _ok(agent_enrollment_response(node, agent_token))


class AgentHeartbeatView(APIView):
    authentication_classes = (NodeTokenAuthentication,)
    permission_classes = (AllowAny,)
    parser_classes = (LimitedJSONParser,)

    def post(self, request):
        serializer = HeartbeatSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        version_data = {
            'protocol_version': data['protocol_version'],
            'agent_version': data['agent_version'],
            'engine_version': data['engine_version'],
        }
        try:
            server_time, command = handle_agent_heartbeat(
                request.performance_node, request.auth, version_data, data['resources'],
                data['run_report'],
            )
        except VersionMismatch:
            return _version_conflict()
        except CredentialRejected:
            return _unauthorized('节点身份凭证已失效。')
        except RunReportRejected as exc:
            return Response({
                'success': False,
                'error': {'code': 'run_report_rejected', 'message': str(exc)},
            }, status=409)
        configuration = execution_configuration()
        execution_compatible = bool(
            data['protocol_version'] == PROTOCOL_VERSION
            and data['agent_version'] == AGENT_VERSION
            and data['engine_version'] == ENGINE_VERSION
        )
        return _ok({
            'node_id': str(request.performance_node.pk),
            'server_time': server_time,
            'heartbeat_interval_seconds': HEARTBEAT_INTERVAL_SECONDS,
            'lease_seconds': NODE_OFFLINE_AFTER_SECONDS,
            # Echo a supported legacy management protocol so an old Agent can
            # keep heartbeating and surface the upgrade prompt. It remains
            # execution-disabled and can never receive a protocol-3 prepare.
            'protocol_version': data['protocol_version'],
            'command': command,
            'execution_enabled': bool(configuration['available'] and execution_compatible),
        })
