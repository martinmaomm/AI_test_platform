"""CLI entry point: only `enroll` and idle-only `run` are supported."""

from __future__ import annotations

import argparse
import logging
import sys
import threading

from .client import PerformanceNodeClient
from .config import NodeConfig, enrollment_token_from_env
from .errors import NodeError
from .runner import install_stop_handlers, run_forever


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m performance_node")
    parser.add_argument("command", choices=("enroll", "run"), help="enroll 或 run")
    return parser


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    arguments = _parser().parse_args(argv)
    try:
        client = PerformanceNodeClient(NodeConfig.from_env())
        if arguments.command == "enroll":
            client.enroll(enrollment_token_from_env())
            logging.info("节点注册成功，长期身份已安全保存")
            return 0
        stop_event = threading.Event()
        install_stop_handlers(stop_event)
        return run_forever(client, stop_event)
    except NodeError as exc:
        logging.error("节点操作失败：%s", exc)
        return 2


if __name__ == "__main__":
    sys.exit(main())
