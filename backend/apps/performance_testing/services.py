import hashlib
import hmac
import secrets
import uuid
from dataclasses import dataclass
from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from projects.models import Project

from .constants import (
    AGENT_VERSION, ENGINE_VERSION, ENROLLMENT_TTL_SECONDS,
    HEARTBEAT_INTERVAL_SECONDS, NODE_OFFLINE_AFTER_SECONDS, PROTOCOL_VERSION,
    SUPPORTED_AGENT_VERSIONS, SUPPORTED_PROTOCOL_VERSIONS,
)
from .models import PerformanceNode, PerformanceRun
from .run_services import execution_configuration, stop_runs_for_node_identity_change


_DUMMY_DIGEST = '0' * 64


class CredentialRejected(Exception):
    pass


class VersionMismatch(Exception):
    pass


class EnrollmentRejected(Exception):
    pass


class NodeHasActiveRuns(Exception):
    def __init__(self, count):
        self.count = count
        super().__init__(f'节点存在 {count} 个活跃运行。')


@dataclass(frozen=True)
class NodeCredential:
    node_id: uuid.UUID
    digest: str


def _digest(secret):
    return hashlib.sha256(secret.encode('utf-8')).hexdigest()


def _matches(stored_digest, secret):
    candidate = _digest(secret)
    return hmac.compare_digest(stored_digest or _DUMMY_DIGEST, candidate)


def _new_credential(node_id):
    # token_urlsafe(32) is generated from 32 random bytes (256 bits).
    secret = secrets.token_urlsafe(32)
    return f'{node_id}.{secret}', _digest(secret)


def _parse_credential(raw):
    if not isinstance(raw, str) or len(raw) > 256:
        raise CredentialRejected
    node_part, separator, secret = raw.partition('.')
    if not separator or not secret:
        raise CredentialRejected
    try:
        node_id = uuid.UUID(node_part)
    except (TypeError, ValueError, AttributeError):
        raise CredentialRejected
    return node_id, secret


def ensure_supported_versions(data):
    if (
        data.get('protocol_version') not in SUPPORTED_PROTOCOL_VERSIONS
        or data.get('agent_version') not in SUPPORTED_AGENT_VERSIONS
        or data.get('engine_version') != ENGINE_VERSION
    ):
        raise VersionMismatch


@transaction.atomic
def issue_enrollment(node):
    locked = PerformanceNode.objects.select_for_update().get(pk=node.pk)
    if locked.deleted_at is not None:
        raise EnrollmentRejected('节点已删除，不能重新签发安装凭证。')
    if locked.revoked_at is not None:
        raise EnrollmentRejected('节点已吊销，不能重新签发安装凭证。')
    if locked.enrollment_consumed_at is not None or locked.agent_token_digest:
        raise EnrollmentRejected('节点已注册，不能重新签发安装凭证。')
    token, digest = _new_credential(locked.pk)
    now = timezone.now()
    locked.enrollment_token_digest = digest
    locked.enrollment_expires_at = now + timedelta(seconds=ENROLLMENT_TTL_SECONDS)
    locked.save(update_fields=(
        'enrollment_token_digest', 'enrollment_expires_at', 'updated_at',
    ))
    return locked, token


@transaction.atomic
def create_node_with_enrollment(project, validated_data):
    node = PerformanceNode.objects.create(project=project, **validated_data)
    return issue_enrollment(node)


@transaction.atomic
def consume_enrollment(raw_token, version_data):
    ensure_supported_versions(version_data)
    node_id, secret = _parse_credential(raw_token)
    node = PerformanceNode.objects.select_for_update().filter(
        pk=node_id, deleted_at__isnull=True,
    ).first()
    digest_matches = _matches(getattr(node, 'enrollment_token_digest', ''), secret)
    if node is None or not digest_matches:
        raise CredentialRejected

    now = timezone.now()
    if (
        node.revoked_at is not None
        or node.enrollment_consumed_at is not None
        or node.enrollment_expires_at is None
        or node.enrollment_expires_at <= now
    ):
        raise CredentialRejected

    agent_token, agent_digest = _new_credential(node.pk)
    # The digest/unused/expiry predicates make one-time consumption safe on
    # SQLite too, where select_for_update() does not provide a row lock.
    updated = PerformanceNode.objects.filter(
        pk=node.pk,
        enrollment_token_digest=node.enrollment_token_digest,
        enrollment_consumed_at__isnull=True,
        enrollment_expires_at__gt=now,
        revoked_at__isnull=True,
        deleted_at__isnull=True,
    ).update(
        enrollment_token_digest='',
        enrollment_expires_at=None,
        enrollment_consumed_at=now,
        agent_token_digest=agent_digest,
        last_seen_at=None,
        agent_version=version_data['agent_version'],
        engine_version=version_data['engine_version'],
        protocol_version=version_data['protocol_version'],
        resources={},
        updated_at=now,
    )
    if updated != 1:
        raise CredentialRejected
    node.refresh_from_db()
    return node, agent_token


def authenticate_agent_token(raw_token):
    node_id, secret = _parse_credential(raw_token)
    node = PerformanceNode.objects.filter(pk=node_id, deleted_at__isnull=True).first()
    digest_matches = _matches(getattr(node, 'agent_token_digest', ''), secret)
    if (
        node is None
        or node.revoked_at is not None
        or not node.agent_token_digest
        or not digest_matches
    ):
        raise CredentialRejected
    return node, NodeCredential(node_id=node.pk, digest=node.agent_token_digest)


def record_heartbeat(node, credential, version_data, resources):
    ensure_supported_versions(version_data)
    now = timezone.now()
    # Bind the update to the digest authenticated for this request. A revoke or
    # soft deletion racing after authentication therefore cannot renew last_seen_at.
    updated = PerformanceNode.objects.filter(
        pk=node.pk,
        agent_token_digest=credential.digest,
        revoked_at__isnull=True,
        deleted_at__isnull=True,
    ).update(
        last_seen_at=now,
        agent_version=version_data['agent_version'],
        engine_version=version_data['engine_version'],
        protocol_version=version_data['protocol_version'],
        resources=resources,
        updated_at=now,
    )
    if updated != 1:
        raise CredentialRejected
    return now


@transaction.atomic
def handle_agent_heartbeat(node, credential, version_data, resources, run_report):
    """Keep credential revalidation, heartbeat and command dispatch atomic."""
    ensure_supported_versions(version_data)
    locked = PerformanceNode.objects.select_for_update().filter(
        pk=node.pk, deleted_at__isnull=True,
    ).first()
    if (
        locked is None
        or locked.revoked_at is not None
        or not locked.agent_token_digest
        or not hmac.compare_digest(locked.agent_token_digest, credential.digest)
    ):
        raise CredentialRejected
    now = timezone.now()
    locked.last_seen_at = now
    locked.agent_version = version_data['agent_version']
    locked.engine_version = version_data['engine_version']
    locked.protocol_version = version_data['protocol_version']
    locked.resources = resources
    locked.save(update_fields=(
        'last_seen_at', 'agent_version', 'engine_version', 'protocol_version',
        'resources', 'updated_at',
    ))
    from .run_services import record_run_report_and_command
    command = record_run_report_and_command(locked, run_report, now)
    return now, command


@transaction.atomic
def revoke_node(node, confirm_stop=False):
    # Match create_run's project -> node lock order so checking active runs and
    # revoking the identity cannot race a newly-created run.
    Project.objects.select_for_update().get(pk=node.project_id)
    locked = PerformanceNode.objects.select_for_update().filter(
        pk=node.pk, project_id=node.project_id, deleted_at__isnull=True,
    ).first()
    if locked is None:
        raise PerformanceNode.DoesNotExist
    active_run_count = PerformanceRun.objects.filter(
        participants__node=locked, status__in=PerformanceRun.ACTIVE_STATUSES,
    ).count()
    if active_run_count and not confirm_stop:
        raise NodeHasActiveRuns(active_run_count)
    now = timezone.now()
    if active_run_count:
        stop_runs_for_node_identity_change(
            locked, 'node_revoked', '节点已吊销，运行无法确认完整结束。', now,
        )
    if locked.revoked_at is None:
        locked.revoked_at = now
    locked.enrollment_token_digest = ''
    locked.enrollment_expires_at = None
    locked.agent_token_digest = ''
    locked.last_seen_at = None
    locked.save(update_fields=(
        'revoked_at', 'enrollment_token_digest', 'enrollment_expires_at',
        'agent_token_digest', 'last_seen_at', 'updated_at',
    ))
    return locked


def enrollment_response(node, token, serializer_class):
    return {
        'node': serializer_class(node, context={'current_time': timezone.now()}).data,
        'enrollment_token': token,
        'expires_at': node.enrollment_expires_at,
    }


def agent_enrollment_response(node, agent_token):
    configuration = execution_configuration()
    execution_compatible = bool(
        node.protocol_version == PROTOCOL_VERSION
        and node.agent_version == AGENT_VERSION
        and node.engine_version == ENGINE_VERSION
    )
    return {
        'node_id': str(node.pk),
        'agent_token': agent_token,
        'heartbeat_interval_seconds': HEARTBEAT_INTERVAL_SECONDS,
        'lease_seconds': NODE_OFFLINE_AFTER_SECONDS,
        'protocol_version': node.protocol_version,
        'execution_enabled': bool(configuration['available'] and execution_compatible),
    }
