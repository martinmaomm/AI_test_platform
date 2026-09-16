"""Idle-only heartbeat loop with a testable graceful-stop boundary."""

from __future__ import annotations

import logging
import signal
import threading
from collections.abc import Callable

from .client import PerformanceNodeClient
from .errors import AgentStopped, NodeError, RetryExhausted

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
) -> int:
    """Return 0 after a requested stop, 2 after a fail-closed protocol stop."""
    stop_event = stop_event or threading.Event()
    wait = wait or stop_event.wait
    identity = client.store.load()
    interval = 5
    while not stop_event.is_set():
        try:
            result = client.heartbeat(identity)
            interval = result.interval_seconds
        except AgentStopped as exc:
            logger.error("节点已停止：%s", exc)
            return 2
        except RetryExhausted as exc:
            logger.warning("心跳暂时失败：%s", exc)
            interval = min(interval, 5)
        except NodeError as exc:
            logger.error("协议错误，节点已停止：%s", exc)
            return 2
        if wait(interval):
            break
    return 0
