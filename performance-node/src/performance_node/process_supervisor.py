"""Supervise a trusted local engine/tunnel pair behind a file lease.

The supervisor is deliberately independent from the agent heartbeat process.  A
blocked or crashed HTTP client therefore cannot leave load-generation children
running after their lease expires.
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import stat
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any


POLL_SECONDS = 0.1
TUNNEL_STARTUP_SECONDS = 0.5
SHUTDOWN_SECONDS = 5.0
MAX_SECONDS_LIMIT = 675
_PROCESS_NAMES = {"engine", "tunnel"}


class SupervisorConfigError(ValueError):
    """A safe, local-only supervisor configuration error."""


@dataclass(frozen=True)
class ProcessSpec:
    name: str
    argv: tuple[str, ...]
    cwd: Path
    log_path: Path


@dataclass(frozen=True)
class SupervisorConfig:
    processes: tuple[ProcessSpec, ...]
    lease_file: Path
    lease_seconds: int
    max_seconds: int
    result_file: Path


def _positive_int(value: object, field: str, *, maximum: int | None = None) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise SupervisorConfigError(f"{field} must be a positive integer")
    if maximum is not None and value > maximum:
        raise SupervisorConfigError(f"{field} exceeds the supported limit")
    return value


def _path(value: object, field: str) -> Path:
    if not isinstance(value, str) or not value or "\x00" in value:
        raise SupervisorConfigError(f"{field} must be a non-empty path")
    return Path(value)


def load_config(path: Path) -> SupervisorConfig:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise SupervisorConfigError("unable to read supervisor config") from exc
    if not isinstance(raw, dict) or set(raw) != {
        "processes", "lease_file", "lease_seconds", "max_seconds", "result_file",
    }:
        raise SupervisorConfigError("supervisor config fields are invalid")
    processes_raw = raw["processes"]
    if not isinstance(processes_raw, list) or not processes_raw or len(processes_raw) > 2:
        raise SupervisorConfigError("processes must contain one engine and an optional tunnel")

    specs: list[ProcessSpec] = []
    names: set[str] = set()
    for index, item in enumerate(processes_raw):
        if not isinstance(item, dict) or set(item) != {"name", "argv", "cwd", "log_path"}:
            raise SupervisorConfigError(f"processes[{index}] fields are invalid")
        name = item["name"]
        argv = item["argv"]
        if name not in _PROCESS_NAMES or name in names:
            raise SupervisorConfigError(f"processes[{index}].name is invalid")
        if (
            not isinstance(argv, list)
            or not argv
            or any(not isinstance(value, str) or not value or "\x00" in value for value in argv)
        ):
            raise SupervisorConfigError(f"processes[{index}].argv is invalid")
        cwd = _path(item["cwd"], f"processes[{index}].cwd")
        if not cwd.is_dir():
            raise SupervisorConfigError(f"processes[{index}].cwd is not a directory")
        specs.append(ProcessSpec(name, tuple(argv), cwd, _path(item["log_path"], f"processes[{index}].log_path")))
        names.add(name)
    if "engine" not in names:
        raise SupervisorConfigError("an engine process is required")

    return SupervisorConfig(
        processes=tuple(specs),
        lease_file=_path(raw["lease_file"], "lease_file"),
        lease_seconds=_positive_int(raw["lease_seconds"], "lease_seconds"),
        max_seconds=_positive_int(raw["max_seconds"], "max_seconds", maximum=MAX_SECONDS_LIMIT),
        result_file=_path(raw["result_file"], "result_file"),
    )


def minimal_child_environment() -> dict[str, str]:
    """Return an allowlisted environment with no agent credentials or proxies."""
    environment = {
        "PATH": "/usr/local/bin:/usr/bin:/bin",
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "PYTHONUNBUFFERED": "1",
    }
    # Preserve a caller-selected temporary directory only when it is an absolute
    # path.  This is useful for read-only containers and carries no credential.
    temporary = os.environ.get("TMPDIR")
    if temporary and os.path.isabs(temporary):
        environment["TMPDIR"] = temporary
    python_path = os.environ.get("PYTHONPATH")
    if python_path:
        environment["PYTHONPATH"] = python_path
    for name in ("PYTHONDONTWRITEBYTECODE", "LOCUST_WORKER_LOG_REPORT_INTERVAL"):
        value = os.environ.get(name)
        if value:
            environment[name] = value
    return environment


def _lease_expired(path: Path, lease_seconds: int) -> bool:
    try:
        details = path.stat()
    except OSError:
        return True
    if not stat.S_ISREG(details.st_mode):
        return True
    return time.time() - details.st_mtime > lease_seconds


def _open_log(path: Path):
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_CLOEXEC, 0o600)
    os.fchmod(descriptor, 0o600)
    return os.fdopen(descriptor, "ab", buffering=0)


def _write_result(path: Path, reason: str, exit_codes: dict[str, int | None]) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    payload = json.dumps(
        {"reason": reason, "exit_codes": exit_codes},
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    descriptor: int | None = None
    temporary_name: str | None = None
    try:
        descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}-", dir=path.parent)
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            descriptor = None
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, path)
        temporary_name = None
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if temporary_name is not None:
            try:
                os.unlink(temporary_name)
            except FileNotFoundError:
                pass


class ProcessSupervisor:
    def __init__(self, config: SupervisorConfig):
        self.config = config
        self.stop_event = threading.Event()
        self.children: dict[str, subprocess.Popen[bytes]] = {}
        self._logs: list[Any] = []

    def request_stop(self, _signum: int | None = None, _frame: object = None) -> None:
        self.stop_event.set()

    def _start(self, spec: ProcessSpec) -> subprocess.Popen[bytes]:
        log = _open_log(spec.log_path)
        self._logs.append(log)
        try:
            child = subprocess.Popen(
                spec.argv,
                cwd=spec.cwd,
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=subprocess.STDOUT,
                env=minimal_child_environment(),
                start_new_session=True,
                close_fds=True,
            )
        except Exception:
            log.close()
            self._logs.remove(log)
            raise
        self.children[spec.name] = child
        return child

    def _start_children(self) -> str | None:
        specs = {spec.name: spec for spec in self.config.processes}
        tunnel = specs.get("tunnel")
        if tunnel is not None:
            child = self._start(tunnel)
            deadline = time.monotonic() + TUNNEL_STARTUP_SECONDS
            while time.monotonic() < deadline:
                if child.poll() is not None:
                    return "child_failed"
                if self.stop_event.wait(POLL_SECONDS):
                    return "stopped"
                if _lease_expired(self.config.lease_file, self.config.lease_seconds):
                    return "lease_expired"
        self._start(specs["engine"])
        return None

    def _watch(self, started: float) -> str:
        while True:
            tunnel = self.children.get("tunnel")
            engine = self.children.get("engine")
            # A tunnel is infrastructure: any unsolicited exit invalidates the
            # run, even if engine completion is observed in the same poll.
            if tunnel is not None and tunnel.poll() is not None:
                return "child_failed"
            if engine is not None and engine.poll() is not None:
                return "finished" if engine.returncode == 0 else "child_failed"
            if self.stop_event.is_set():
                return "stopped"
            if _lease_expired(self.config.lease_file, self.config.lease_seconds):
                return "lease_expired"
            if time.monotonic() - started >= self.config.max_seconds:
                return "timeout"
            self.stop_event.wait(POLL_SECONDS)

    def _terminate_all(self) -> None:
        for child in self.children.values():
            if child.poll() is None:
                try:
                    os.killpg(child.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
        deadline = time.monotonic() + SHUTDOWN_SECONDS
        while time.monotonic() < deadline and any(child.poll() is None for child in self.children.values()):
            time.sleep(POLL_SECONDS)
        for child in self.children.values():
            if child.poll() is None:
                try:
                    os.killpg(child.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
        for child in self.children.values():
            try:
                child.wait(timeout=1)
            except subprocess.TimeoutExpired:
                pass

    def run(self) -> str:
        started = time.monotonic()
        reason = "child_failed"
        try:
            if _lease_expired(self.config.lease_file, self.config.lease_seconds):
                reason = "lease_expired"
            else:
                startup_reason = self._start_children()
                reason = startup_reason or self._watch(started)
        except (OSError, subprocess.SubprocessError):
            reason = "child_failed"
        finally:
            self._terminate_all()
            exit_codes = {
                spec.name: self.children[spec.name].poll() if spec.name in self.children else None
                for spec in self.config.processes
            }
            _write_result(self.config.result_file, reason, exit_codes)
            for log in self._logs:
                log.close()
        return reason


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m performance_node.process_supervisor")
    parser.add_argument("config_path")
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        config = load_config(Path(arguments.config_path))
    except SupervisorConfigError as exc:
        print(f"invalid supervisor config: {exc}", file=sys.stderr)
        return 2
    supervisor = ProcessSupervisor(config)
    signal.signal(signal.SIGTERM, supervisor.request_stop)
    signal.signal(signal.SIGINT, supervisor.request_stop)
    reason = supervisor.run()
    return 0 if reason == "finished" else 1


if __name__ == "__main__":
    raise SystemExit(main())
