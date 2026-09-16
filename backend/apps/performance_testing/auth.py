from dataclasses import dataclass

from rest_framework.authentication import BaseAuthentication, get_authorization_header
from rest_framework.exceptions import AuthenticationFailed, NotAuthenticated

from .services import CredentialRejected, authenticate_agent_token


@dataclass(frozen=True)
class NodePrincipal:
    node_id: object

    @property
    def is_authenticated(self):
        return True


class NodeTokenAuthentication(BaseAuthentication):
    """Authenticate only ``Authorization: Node <uuid.secret>`` credentials."""

    def authenticate(self, request):
        parts = get_authorization_header(request).split()
        if not parts:
            raise NotAuthenticated('缺少节点身份凭证。')
        if len(parts) != 2 or parts[0].lower() != b'node':
            raise AuthenticationFailed('节点身份凭证无效。')
        try:
            raw = parts[1].decode('ascii')
            node, credential = authenticate_agent_token(raw)
        except (UnicodeDecodeError, CredentialRejected):
            raise AuthenticationFailed('节点身份凭证无效。')
        request.performance_node = node
        return NodePrincipal(node.pk), credential

    def authenticate_header(self, request):
        return 'Node'
