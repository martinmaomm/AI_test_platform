"""Protocol-v3 heartbeat and persistent execution loop."""

from __future__ import annotations

import logging
import signal
import threading
from collections.abc import Callable

from .client import PerformanceNodeClient
from .errors import AgentStopped, NodeError, RetryExhausted
from .executor import RunExecutor

logger = logging.getLogger(__name__)


def install_stop_handlers(stop_event: threading.Event) -> None:
    def request_stop(_signum: int, _frame: object) -> None:
        stop_event.set()

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)


def run_forever(
    client: PerformanceNodeClient,
    stop_event: threading.Event | None = None,
    wait: Callable[[float], bool] | None = None,
    executor: RunExecutor | None = None,
) -> int:
    """Return 0 after a requested stop, 2 after a fail-closed protocol stop."""
    stop_event = stop_event or threading.Event()
    wait = wait or stop_event.wait
    identity = client.store.load()
    executor = executor or RunExecutor(client.config, identity.node_id)
    interval = 5
    first_heartbeat_succeeded = False
    try:
        while not stop_event.is_set():
            try:
                report = executor.report_for_heartbeat()
                result = client.heartbeat(identity, report)
                interval = result.interval_seconds
                if not first_heartbeat_succeeded:
                    logger.info("平台 TLS 证书已校验，节点首次心跳成功")
                    first_heartbeat_succeeded = True
                if stop_event.is_set():
                    break
                executor.handle_command(result.command)
            except AgentStopped as exc:
                executor.stop_active("agent_stopped", "节点身份或命令被拒绝", report_state="failed")
                logger.error("节点已停止：%s", exc)
                return 2
            except RetryExhausted as exc:
                # Do not renew the independent execution lease on a failed
                # heartbeat. The supervisor will stop even if HTTP remains stuck.
                logger.warning("心跳暂时失败：%s", exc)
                interval = min(interval, 5)
            except NodeError as exc:
                executor.stop_active("protocol_error", "节点协议校验失败", report_state="failed")
                logger.error("协议错误，节点已停止：%s", exc)
                return 2
            if wait(interval):
                break
        executor.shutdown()
        return 0
    finally:
        executor.close()
