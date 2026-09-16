"""Safe extraction and best-effort persistence for successful login records."""
import ipaddress
import logging

from django.conf import settings
from django.db import transaction

from .models import LoginRecord


logger = logging.getLogger(__name__)

_MAX_FORWARDED_FOR_LENGTH = 2048
_MAX_FORWARDED_FOR_HOPS = 32


def _normalized_ip(value):
    if not isinstance(value, str):
        return None
    value = value.strip()
    if not value or len(value) > 45 or '%' in value:
        return None
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        return None
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
        return address.ipv4_mapped
    return address


def _normalized_network(value):
    if not isinstance(value, str):
        return None
    try:
        network = ipaddress.ip_network(value.strip(), strict=True)
    except ValueError:
        return None
    if (
        isinstance(network, ipaddress.IPv6Network)
        and network.prefixlen >= 96
        and network.network_address.ipv4_mapped is not None
        and network.broadcast_address.ipv4_mapped is not None
    ):
        network = ipaddress.IPv4Network(
            (network.network_address.ipv4_mapped, network.prefixlen - 96),
            strict=True,
        )
    if network.prefixlen == 0:
        return None
    return network


def _trusted_networks():
    configured = getattr(settings, 'LOGIN_RECORD_TRUSTED_PROXIES', ())
    if isinstance(configured, str):
        configured = configured.split(',')
    networks = []
    for value in configured:
        network = _normalized_network(value)
        if network is not None:
            networks.append(network)
    return tuple(networks)


def _is_trusted(address, networks):
    return any(address.version == network.version and address in network for network in networks)


def get_login_ip(request):
    """Return a validated client IP, trusting XFF only from configured TCP peers."""
    peer = _normalized_ip(request.META.get('REMOTE_ADDR'))
    if peer is None:
        return None

    networks = _trusted_networks()
    if not _is_trusted(peer, networks):
        return str(peer)

    forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
    if not isinstance(forwarded_for, str) or not forwarded_for:
        return str(peer)
    if len(forwarded_for) > _MAX_FORWARDED_FOR_LENGTH:
        return str(peer)

    raw_hops = forwarded_for.split(',')
    if not 1 <= len(raw_hops) <= _MAX_FORWARDED_FOR_HOPS:
        return str(peer)
    hops = [_normalized_ip(value) for value in raw_hops]
    if any(address is None for address in hops):
        return str(peer)

    for address in reversed(hops):
        if not _is_trusted(address, networks):
            return str(address)
    return str(peer)


def record_successful_login(*, user, request):
    """Persist one minimal record without ever failing an authenticated login."""
    user_agent = request.META.get('HTTP_USER_AGENT', '') or ''
    if not isinstance(user_agent, str):
        user_agent = str(user_agent)
    try:
        # The nested atomic block supplies a savepoint when the login request is
        # already inside a transaction, so a failed insert does not poison it.
        with transaction.atomic():
            LoginRecord.objects.create(
                user=user,
                ip_address=get_login_ip(request),
                user_agent=user_agent[:512],
            )
    except Exception:
        # Never log the exception, SQL, headers, request data, or submitted secret.
        logger.error('Failed to persist successful login record for user_id=%s', user.pk)
