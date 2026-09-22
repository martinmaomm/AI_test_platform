"""CLI entry point for enrollment and the protocol-v3 execution agent."""

from __future__ import annotations

import argparse
import logging
import sys
import threading

from .bootstrap import start_agent
from .client import PerformanceNodeClient
from .config import NodeConfig, enrollment_token_from_env
from .errors import NodeError
from .runner import install_stop_handlers, run_forever


class SafeArgumentParser(argparse.ArgumentParser):
    """Never echo rejected argv because start argv contains a one-time token."""

    def error(self, _message: str) -> None:
        self.print_usage(sys.stderr)
        self.exit(2, f"{self.prog}: 参数无效；请使用 --help 查看用法\n")


def _parser() -> argparse.ArgumentParser:
    parser = SafeArgumentParser(prog="python -m performance_node")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("enroll", help="使用环境变量完成旧版注册")
    commands.add_parser("run", help="使用已保存身份运行节点")
    start = commands.add_parser("start", help="注册或复用身份后直接运行节点")
    start.add_argument("--server", required=True, metavar="HTTPS_URL")
    start.add_argument("--node-id", required=True, metavar="UUID")
    start.add_argument("--token", required=True, metavar="ONE_TIME_TOKEN")
    start.add_argument("--ca-sha256", metavar="HEX64")
    return parser


def _consume_start_token(arguments: argparse.Namespace) -> str:
    token = arguments.token
    arguments.token = None
    return token


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    arguments = _parser().parse_args(argv)
    try:
        if arguments.command == "start":
            return start_agent(
                arguments.server,
                arguments.node_id,
                _consume_start_token(arguments),
                arguments.ca_sha256,
            )
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
