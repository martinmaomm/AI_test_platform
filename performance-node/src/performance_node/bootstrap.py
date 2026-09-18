"""One-command secure enrollment followed by the existing runtime loop."""

from __future__ import annotations

import hashlib
import logging
import threading
from collections.abc import Callable

from .ca import resolve_ca
from .client import PerformanceNodeClient
from .config import NodeConfig, ca_sha256, enrollment_token, node_id
from .errors import AgentStopped, NodeError, RequestRejected, StateError
from .runner import install_stop_handlers, run_forever
from .state import EnrollmentAttempt, InstallationBinding, StateStore

logger = logging.getLogger(__name__)


def _same_binding(existing: InstallationBinding, requested: InstallationBinding) -> bool:
    return existing == requested


def _enroll_once(
    client: PerformanceNodeClient,
    store: StateStore,
    expected_node_id: str,
    token: str,
) -> None:
    token_sha256 = hashlib.sha256(token.encode("utf-8")).hexdigest()
    previous = store.load_attempt()
    if previous is not None and previous.token_sha256 == token_sha256:
        raise AgentStopped(
            "该一次性凭证已有注册尝试且不会自动重发；请在平台重新生成凭证并使用新 token"
        )

    store.save_attempt(EnrollmentAttempt(token_sha256, "in_progress"))
    try:
        client.enroll(
            token,
            expected_node_id=expected_node_id,
            retry_transient=False,
        )
    except RequestRejected as exc:
        store.save_attempt(EnrollmentAttempt(token_sha256, "rejected"))
        raise AgentStopped(
            "注册被平台明确拒绝；已停止且不会用同一凭证重试，请重新生成一次性凭证"
        ) from exc
    except NodeError as exc:
        store.save_attempt(EnrollmentAttempt(token_sha256, "ambiguous"))
        raise AgentStopped(
            "注册结果不确定；为避免重复消费凭证已停止且不会用同一凭证重试，"
            "请重新生成一次性凭证"
        ) from exc

    try:
        store.clear_attempt()
    except StateError:
        # identity.json is the source of truth. A stale marker is harmless once
        # the atomic identity write has completed.
        logger.warning("节点身份已保存，但注册尝试标记未能清除；后续将安全复用身份")


def start_agent(
    server: str,
    requested_node_id: str,
    one_time_token: str,
    requested_ca_sha256: str | None,
    *,
    runtime: Callable[[PerformanceNodeClient, threading.Event], int] = run_forever,
    handler_installer: Callable[[threading.Event], None] = install_stop_handlers,
) -> int:
    """Provision once and hold an exclusive state lock until runtime exits."""
    normalized_node_id = node_id(requested_node_id)
    normalized_token = enrollment_token(one_time_token)
    normalized_ca_sha256 = ca_sha256(requested_ca_sha256)
    initial_config = NodeConfig.from_start(server)
    requested_binding = InstallationBinding(
        initial_config.platform_url,
        normalized_node_id,
        normalized_ca_sha256,
    )
    store = StateStore(initial_config.state_dir)

    with store.start_lock():
        if store.binding_exists():
            if not _same_binding(store.load_binding(), requested_binding):
                raise AgentStopped("安装参数与状态卷绑定不一致；已停止以防止跨平台或跨节点接管")
            verify = resolve_ca(store, initial_config.platform_url, normalized_ca_sha256)
        else:
            if store.exists():
                raise AgentStopped("现有节点身份缺少安装绑定；不能通过 start 自动接管")
            # A typo in a CA fingerprint must not permanently bind a fresh
            # volume. Validate/pin the CA before publishing the binding, while
            # still ensuring this happens before any enrollment token is sent.
            verify = resolve_ca(store, initial_config.platform_url, normalized_ca_sha256)
            store.save_binding(requested_binding)
        if normalized_ca_sha256 is not None:
            logger.info("平台 CA 证书和 SHA256 指纹已校验")

        config = NodeConfig(
            platform_url=initial_config.platform_url,
            state_dir=initial_config.state_dir,
            ca_bundle=verify,
            stunnel_binary=initial_config.stunnel_binary,
        )
        client = PerformanceNodeClient(config)
        if store.exists():
            identity = store.load()
            if identity.node_id != normalized_node_id:
                raise AgentStopped("持久化节点身份与 --node-id 不一致")
            logger.info("复用已有节点身份")
        else:
            _enroll_once(client, store, normalized_node_id, normalized_token)
            logger.info("平台 TLS 证书已校验")
            logger.info("首次注册成功，节点身份已安全保存")

        del normalized_token, one_time_token
        stop_event = threading.Event()
        handler_installer(stop_event)
        logger.info("启动节点心跳")
        return runtime(client, stop_event)
