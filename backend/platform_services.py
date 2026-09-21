#!/usr/bin/env python3
"""AI test platform service supervisor and its small local CLI.

The manager is intentionally local-only: one process owns all state writes and
accepts a fixed command vocabulary over a private Unix socket.  State contains
process identity metadata and command fingerprints, never environment values or
complete command lines.
"""

from __future__ import annotations

import argparse
import collections
import dataclasses
import errno
import fcntl
import functools
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import socket
import subprocess
import sys
import time
import traceback
from typing import Any, Callable, Iterable, Mapping, Sequence

import psutil


BACKEND_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = BACKEND_DIR.parent
DEFAULT_STATE_DIR = BACKEND_DIR / "temp" / "platform-services"
DEFAULT_LOG_DIR = BACKEND_DIR / "logs" / "services"
CORE_SERVICES = ("backend", "celery", "controller", "caddy")
STOP_ORDER = ("controller", "celery", "caddy", "backend")
STATE_VERSION = 2
MAX_MESSAGE_BYTES = 64 * 1024
LOG_ROTATE_BYTES = 20 * 1024 * 1024
LOG_BACKUP_COUNT = 5


class ServiceError(RuntimeError):
    """A user-visible service management error."""


@dataclasses.dataclass(frozen=True)
class RuntimePaths:
    state_dir: Path = DEFAULT_STATE_DIR
    log_dir: Path = DEFAULT_LOG_DIR

    @property
    def state_file(self) -> Path:
        return self.state_dir / "state.json"

    @property
    def lock_file(self) -> Path:
        return self.state_dir / "manager.lock"

    @property
    def socket_file(self) -> Path:
        return self.state_dir / "manager.sock"

    @property
    def manager_log(self) -> Path:
        return self.log_dir / "platform-manager.log"

    def service_log(self, name: str) -> Path:
        return self.log_dir / f"{name}.log"


@dataclasses.dataclass(frozen=True)
class EngineOptions:
    poll_interval: float = 0.25
    restart_window: float = 600.0
    restart_limit: int = 5
    minimum_restart_delay: float = 2.0
    controller_restart_delay: float = 20.0
    max_restart_delay: float = 300.0


def _secure_directory(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.is_symlink() or not path.is_dir():
        raise ServiceError(f"状态路径不是安全目录：{path}")
    os.chmod(path, 0o700)


def _secure_log_directory(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.is_symlink() or not path.is_dir():
        raise ServiceError(f"日志路径不是安全目录：{path}")
    os.chmod(path, 0o700)


def _atomic_write_json(path: Path, value: Mapping[str, Any]) -> None:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            output.write(payload)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
        os.chmod(path, 0o600)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _read_json(path: Path) -> dict[str, Any]:
    try:
        if path.is_symlink():
            return {}
        with path.open("r", encoding="utf-8") as source:
            value = json.load(source)
    except (FileNotFoundError, OSError, ValueError, TypeError):
        return {}
    return value if isinstance(value, dict) else {}


def _safe_detail(value: object, limit: int = 300) -> str:
    text = " ".join(str(value).replace("\x00", "").split())
    return text[:limit]


def _resolve_executable(value: str, cwd: str) -> str:
    candidate = Path(value)
    if candidate.is_absolute() or "/" in value:
        if not candidate.is_absolute():
            candidate = Path(cwd) / candidate
        return str(candidate.resolve(strict=False))
    found = shutil.which(value)
    return str(Path(found).resolve(strict=False)) if found else value


def _resolve_argument_path(value: str, cwd: str) -> str:
    candidate = Path(value)
    if candidate.is_absolute():
        return str(candidate.resolve(strict=False))
    return str((Path(cwd) / candidate).resolve(strict=False))


def canonical_command(command: Sequence[str], cwd: str) -> tuple[str, ...]:
    """Normalize known equivalent launch forms without loosening argument checks."""
    if not command:
        return ()
    executable = _resolve_executable(str(command[0]), cwd)
    basename = Path(executable).name.lower()
    arguments = [str(item) for item in command[1:]]
    if basename.startswith("python"):
        if len(arguments) >= 2 and arguments[0] == "-m":
            return ("python-module", arguments[1], *arguments[2:])
        if arguments and arguments[0] == "-c":
            return ("python-inline", *arguments)
        if arguments and not arguments[0].startswith("-"):
            return ("python-script", _resolve_argument_path(arguments[0], cwd), *arguments[1:])
        return ("python", executable, *arguments)
    if basename == "celery":
        return ("python-module", "celery", *arguments)
    return ("executable", executable, *arguments)


def command_fingerprint(command: Sequence[str], cwd: str) -> str:
    normalized = json.dumps(canonical_command(command, cwd), ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _external_command_matches(name: str, spec: Mapping[str, Any], actual_cwd: str, actual_command: Sequence[str]) -> bool:
    expected_cwd = str(Path(str(spec["cwd"])).resolve())
    commands_match = canonical_command(actual_command, actual_cwd) == canonical_command(spec["command"], expected_cwd)
    # A manually launched Caddy commonly uses the repository root while the
    # managed spec uses backend/. Its absolute Caddyfile and complete argv are
    # the ownership-neutral conflict identity; managed ownership still requires
    # the captured cwd below.
    return commands_match and (actual_cwd == expected_cwd or name == "caddy")


@functools.lru_cache(maxsize=1)
def _boot_identity() -> str:
    """Distinguish persisted process identities across machine reboots."""
    if sys.platform == "darwin":
        value = subprocess.check_output(
            ["/usr/sbin/sysctl", "-n", "kern.bootsessionuuid"],
            text=True, timeout=2, stderr=subprocess.DEVNULL,
        ).strip()
    elif sys.platform.startswith("linux"):
        value = Path("/proc/sys/kernel/random/boot_id").read_text(encoding="ascii").strip()
    else:
        raise ServiceError("稳定进程身份核验仅支持 macOS / Linux")
    if not value:
        raise ServiceError("无法读取本次系统启动标识，已拒绝纳管")
    return value


def _process_identity(process: psutil.Process) -> dict[str, Any]:
    # psutil 7.2.2 (pinned in requirements.txt) uses the unadjusted kernel
    # start time in _get_ident() on macOS/Linux, including for its own PID-reuse
    # checks. Public create_time() is wall-clock adjusted and may differ between
    # Python processes after NTP updates. Keep this private API in one adapter;
    # a dependency change must not reinterpret existing persisted identities.
    pid, started = process._get_ident()
    return {
        "pid": pid,
        "start_time": started,
        "boot_id": _boot_identity(),
        "source": f"psutil-{psutil.__version__}",
    }


def _process_values(process: psutil.Process) -> tuple[dict[str, Any], str, list[str]] | None:
    try:
        values = _process_identity(process), str(Path(process.cwd()).resolve()), process.cmdline()
        return values if process.is_running() else None
    except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess, OSError):
        return None


def _record_matches_process(record: Mapping[str, Any], spec: Mapping[str, Any]) -> bool:
    pid = record.get("pid")
    identity = record.get("process_identity")
    if not isinstance(pid, int) or not isinstance(identity, dict):
        return False
    try:
        process = psutil.Process(pid)
    except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
        return False
    values = _process_values(process)
    if not values:
        return False
    actual_identity, actual_cwd, actual_command = values
    expected_cwd = str(Path(str(spec["cwd"])).resolve())
    actual_fingerprint = command_fingerprint(actual_command, actual_cwd)
    observed_fingerprint = record.get("observed_command_fingerprint") or record.get("command_fingerprint")
    observed_cwd = str(record.get("observed_cwd") or expected_cwd)
    return (
        actual_identity == identity
        and actual_cwd == observed_cwd == expected_cwd
        and canonical_command(actual_command, actual_cwd)
        == canonical_command([str(item) for item in spec["command"]], expected_cwd)
        and actual_fingerprint == observed_fingerprint
        and record.get("command_fingerprint")
        == command_fingerprint([str(item) for item in spec["command"]], expected_cwd)
    )


def _new_record(name: str, spec: Mapping[str, Any]) -> dict[str, Any]:
    cwd = str(Path(str(spec["cwd"])).resolve())
    return {
        "name": name,
        "desired": False,
        "pid": None,
        "process_identity": None,
        "cwd": cwd,
        "command_fingerprint": command_fingerprint(spec["command"], cwd),
        "observed_command_fingerprint": None,
        "observed_cwd": None,
        "started_at": None,
        "state": "stopped",
        "detail": "",
        "restart_attempts": [],
        "next_restart_at": None,
        "restart_blocked": False,
    }


class SupervisorEngine:
    """Single-writer service lifecycle engine, injectable for isolated tests."""

    def __init__(
        self,
        specs: Mapping[str, Mapping[str, Any]],
        paths: RuntimePaths = RuntimePaths(),
        options: EngineOptions = EngineOptions(),
        preflight: Callable[[str, Mapping[str, Any]], str | None] | None = None,
        spec_loader: Callable[[], Mapping[str, Mapping[str, Any]]] | None = None,
        manager_identity: Mapping[str, Any] | None = None,
    ) -> None:
        self.specs = {name: dict(spec) for name, spec in specs.items()}
        self.paths = paths
        self.options = options
        self.preflight = preflight or (lambda _name, _spec: None)
        self.spec_loader = spec_loader
        self.manager_identity = dict(manager_identity or {})
        self.process_handles: dict[str, subprocess.Popen[bytes]] = {}
        self.retired_handles: list[subprocess.Popen[bytes]] = []
        self.log_handles: dict[str, Any] = {}
        _secure_directory(paths.state_dir)
        _secure_log_directory(paths.log_dir)
        previous = _read_json(paths.state_file)
        old_records = previous.get("services", {}) if previous.get("version") == STATE_VERSION else {}
        self.records: dict[str, dict[str, Any]] = {}
        for name, spec in self.specs.items():
            fresh = _new_record(name, spec)
            old = old_records.get(name) if isinstance(old_records, dict) else None
            if isinstance(old, dict):
                for key in (
                    "desired", "pid", "process_identity", "started_at", "state", "detail",
                    "restart_attempts", "next_restart_at", "restart_blocked",
                    "observed_command_fingerprint", "observed_cwd",
                ):
                    if key in old:
                        fresh[key] = old[key]
            # Current configuration always owns cwd and the fingerprint.
            fresh["cwd"] = str(Path(str(spec["cwd"])).resolve())
            fresh["command_fingerprint"] = command_fingerprint(spec["command"], fresh["cwd"])
            self.records[name] = fresh
        self._adopt_or_reject_previous_processes()
        self.save()

    def reload_specs(self) -> None:
        """Refresh child environments without changing live-process identity rules."""
        if self.spec_loader is None:
            return
        refreshed = self.spec_loader()
        if set(refreshed) != set(self.specs):
            raise ServiceError("服务配置集合在管理器运行期间发生变化，请先停止平台服务")
        for name, raw_spec in refreshed.items():
            # A running child remains tied to the exact argv/cwd used to launch it.
            # Its new environment becomes active only after an explicit restart.
            if self._running(name):
                continue
            spec = dict(raw_spec)
            self.specs[name] = spec
            record = self.records[name]
            record["cwd"] = str(Path(str(spec["cwd"])).resolve())
            record["command_fingerprint"] = command_fingerprint(spec["command"], record["cwd"])

    def _adopt_or_reject_previous_processes(self) -> None:
        for name, record in self.records.items():
            if record.get("pid") is None:
                continue
            if _record_matches_process(record, self.specs[name]):
                record["state"] = "running" if record.get("desired") else "stopping"
                record["detail"] = "已核验并接管原管理器子进程"
            else:
                record["pid"] = None
                record["process_identity"] = None
                if record.get("desired"):
                    record["state"] = "waiting_restart"
                    record["detail"] = "旧进程身份失效，等待受控重启"
                    record["next_restart_at"] = time.time() + self._restart_delay(name, record)
                else:
                    record["state"] = "stopped"
                    record["detail"] = ""

    def save(self) -> None:
        state = {
            "version": STATE_VERSION,
            "manager": self.manager_identity,
            "updated_at": time.time(),
            "services": self.records,
        }
        _atomic_write_json(self.paths.state_file, state)

    def _prune_attempts(self, record: dict[str, Any], now: float | None = None) -> list[float]:
        cutoff = (now if now is not None else time.time()) - self.options.restart_window
        attempts = [float(value) for value in record.get("restart_attempts", []) if float(value) >= cutoff]
        record["restart_attempts"] = attempts
        return attempts

    def _restart_delay(self, name: str, record: Mapping[str, Any]) -> float:
        configured = max(0.0, float(self.specs[name].get("restart_delay", 0.0)))
        floor = self.options.controller_restart_delay if name == "controller" else self.options.minimum_restart_delay
        base = max(configured, floor)
        exponent = min(len(record.get("restart_attempts", [])), 8)
        return min(base * (2**exponent), self.options.max_restart_delay)

    def _running(self, name: str) -> bool:
        return _record_matches_process(self.records[name], self.specs[name])

    def _clear_process(self, name: str) -> None:
        process = self.process_handles.pop(name, None)
        if process is not None:
            try:
                process.wait(timeout=0.2)
            except (subprocess.TimeoutExpired, OSError):
                self.retired_handles.append(process)
        handle = self.log_handles.pop(name, None)
        if handle is not None:
            try:
                handle.close()
            except OSError:
                pass
        record = self.records[name]
        record["pid"] = None
        record["process_identity"] = None
        record["observed_command_fingerprint"] = None
        record["observed_cwd"] = None

    def _equivalent_external_processes(self, name: str) -> list[int]:
        spec = self.specs[name]
        owned = {
            int(record["pid"])
            for record_name, record in self.records.items()
            if record.get("pid") is not None and _record_matches_process(record, self.specs[record_name])
        }
        matches: list[int] = []
        for process in psutil.process_iter(["pid"]):
            if process.pid in owned or process.pid == os.getpid():
                continue
            values = _process_values(process)
            if not values:
                continue
            _created, actual_cwd, actual_command = values
            if _external_command_matches(name, spec, actual_cwd, actual_command):
                matches.append(process.pid)
        return matches

    @staticmethod
    def _rotate_log(path: Path) -> None:
        try:
            if not path.exists() or path.stat().st_size < LOG_ROTATE_BYTES:
                return
            oldest = path.with_name(f"{path.name}.{LOG_BACKUP_COUNT}")
            try:
                oldest.unlink()
            except FileNotFoundError:
                pass
            for index in range(LOG_BACKUP_COUNT - 1, 0, -1):
                source = path.with_name(f"{path.name}.{index}")
                if source.exists():
                    source.replace(path.with_name(f"{path.name}.{index + 1}"))
            path.replace(path.with_name(f"{path.name}.1"))
        except OSError:
            # Logging must not make service startup unavailable.
            return

    @staticmethod
    def _capture_identity(pid: int, spec: Mapping[str, Any], timeout: float = 1.0) -> tuple[dict[str, Any], str, str]:
        deadline = time.monotonic() + timeout
        last: tuple[dict[str, Any], str, list[str]] | None = None
        while time.monotonic() < deadline:
            try:
                last = _process_values(psutil.Process(pid))
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                last = None
            if last:
                identity, actual_cwd, actual_command = last
                expected_cwd = str(Path(str(spec["cwd"])).resolve())
                if (
                    actual_cwd == expected_cwd
                    and canonical_command(actual_command, actual_cwd) == canonical_command(spec["command"], expected_cwd)
                ):
                    return identity, actual_cwd, command_fingerprint(actual_command, actual_cwd)
            time.sleep(0.01)
        raise ServiceError("启动后无法核验实际进程身份，已拒绝纳管")

    @staticmethod
    def _addresses_overlap(expected_host: str, actual_host: str) -> bool:
        wildcards = {"", "0.0.0.0", "::", "*"}
        if expected_host in wildcards or actual_host in wildcards:
            return True
        loopbacks = {"localhost", "127.0.0.1", "::1"}
        return expected_host == actual_host or (expected_host in loopbacks and actual_host in loopbacks)

    def _port_conflicts(self, name: str) -> list[tuple[str, int, int | None]]:
        spec = self.specs[name]
        ports = spec.get("ports", [])
        if not ports:
            return []
        owned = {
            int(record["pid"])
            for record_name, record in self.records.items()
            if record.get("pid") is not None and _record_matches_process(record, self.specs[record_name])
        }
        conflicts: list[tuple[str, int, int | None]] = []
        connections_available = True
        try:
            connections = psutil.net_connections(kind="inet")
        except (psutil.AccessDenied, psutil.Error, OSError):
            connections = []
            connections_available = False
        for declared in ports:
            expected_host = str(declared.get("host", "0.0.0.0"))
            expected_port = int(declared["port"])
            for connection in connections:
                if connection.status != psutil.CONN_LISTEN or not connection.laddr:
                    continue
                actual_host = str(connection.laddr.ip)
                actual_port = int(connection.laddr.port)
                if actual_port != expected_port or not self._addresses_overlap(expected_host, actual_host):
                    continue
                if connection.pid not in owned:
                    conflicts.append((actual_host, actual_port, connection.pid))
                    break
            if not connections_available and not conflicts:
                families = [socket.AF_INET6] if ":" in expected_host else [socket.AF_INET]
                bind_host = {"localhost": "127.0.0.1", "*": "0.0.0.0"}.get(expected_host, expected_host)
                for family in families:
                    probe = socket.socket(family, socket.SOCK_STREAM)
                    try:
                        # Match normal server restart semantics: recently closed
                        # connections in TIME_WAIT do not mean a listener exists.
                        # Do not use SO_REUSEPORT, which could hide a live server.
                        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                        probe.bind((bind_host, expected_port))
                    except OSError as exc:
                        if exc.errno in {errno.EADDRINUSE, errno.EACCES}:
                            conflicts.append((expected_host, expected_port, None))
                        else:
                            conflicts.append((expected_host, expected_port, None))
                        break
                    finally:
                        probe.close()
        return conflicts

    def _validate_start(self, name: str) -> str | None:
        spec = self.specs[name]
        if not bool(spec.get("enabled")):
            reason = _safe_detail(spec.get("disabled_reason") or "该服务未启用")
            return f"{name} 已禁用：{reason}"
        try:
            preflight_error = self.preflight(name, spec)
        except Exception as exc:  # Configuration checks must not crash the manager.
            return f"{name} 启动前检查失败：{_safe_detail(exc)}"
        if preflight_error:
            return f"{name} 启动前检查未通过：{_safe_detail(preflight_error)}"
        external = self._equivalent_external_processes(name)
        if external:
            joined = ", ".join(str(pid) for pid in external)
            return f"检测到未纳管的 {name} 手动实例（PID {joined}），请先正常停止后再纳管；不会接管或终止该进程。"
        conflicts = self._port_conflicts(name)
        if conflicts:
            host, port, pid = conflicts[0]
            owner = f"PID {pid}" if pid is not None else "未知进程"
            return f"{name} 所需端口 {host}:{port} 已被{owner}占用，请先确认并正常停止冲突服务；不会自动终止。"
        return None

    def _launch(self, name: str, automatic: bool = False) -> tuple[bool, str]:
        # The manager itself intentionally keeps only its shell environment.
        # Reload here so every real child launch receives the current .env snapshot.
        record = self.records[name]
        now = time.time()
        if automatic:
            attempts = self._prune_attempts(record, now)
            if len(attempts) >= self.options.restart_limit:
                record["state"] = "abnormal"
                record["restart_blocked"] = True
                record["next_restart_at"] = None
                record["detail"] = f"{int(self.options.restart_window // 60)}分钟内重启已达{self.options.restart_limit}次，等待手动重启"
                self.save()
                return False, record["detail"]
            attempts.append(now)
            record["restart_attempts"] = attempts
        try:
            self.reload_specs()
        except Exception as exc:
            record["state"] = "abnormal" if automatic else "stopped"
            record["detail"] = f"重新读取服务配置失败：{_safe_detail(exc)}"
            if automatic:
                if len(record["restart_attempts"]) >= self.options.restart_limit:
                    record["restart_blocked"] = True
                    record["next_restart_at"] = None
                else:
                    record["next_restart_at"] = now + self._restart_delay(name, record)
            self.save()
            return False, record["detail"]
        error = self._validate_start(name)
        if error:
            record["state"] = "abnormal" if automatic else "stopped"
            record["detail"] = error
            if automatic:
                if len(record["restart_attempts"]) >= self.options.restart_limit:
                    record["restart_blocked"] = True
                    record["next_restart_at"] = None
                else:
                    record["next_restart_at"] = now + self._restart_delay(name, record)
            self.save()
            return False, error
        spec = self.specs[name]
        log_path = self.paths.service_log(name)
        self._rotate_log(log_path)
        log_handle = log_path.open("ab", buffering=0)
        os.chmod(log_path, 0o600)
        try:
            process = subprocess.Popen(
                [str(item) for item in spec["command"]],
                cwd=str(spec["cwd"]),
                env={str(key): str(value) for key, value in spec["env"].items()},
                stdin=subprocess.DEVNULL,
                stdout=log_handle,
                stderr=subprocess.STDOUT,
                start_new_session=True,
                close_fds=True,
            )
            identity, observed_cwd, observed_fingerprint = self._capture_identity(process.pid, spec)
        except Exception as exc:
            if "process" in locals() and process.poll() is None:
                try:
                    process.terminate()
                    process.wait(timeout=0.5)
                except OSError:
                    pass
                except subprocess.TimeoutExpired:
                    self.retired_handles.append(process)
            log_handle.close()
            record["state"] = "abnormal" if automatic else "stopped"
            record["detail"] = f"启动失败：{_safe_detail(exc)}"
            if automatic:
                record["next_restart_at"] = now + self._restart_delay(name, record)
            self.save()
            return False, record["detail"]
        self.process_handles[name] = process
        self.log_handles[name] = log_handle
        record.update(
            {
                "desired": True,
                "pid": process.pid,
                "process_identity": identity,
                "observed_cwd": observed_cwd,
                "observed_command_fingerprint": observed_fingerprint,
                "started_at": now,
                "state": "running",
                "detail": "自动重启成功" if automatic else "已启动",
                "next_restart_at": None,
                "restart_blocked": False,
            }
        )
        self.save()
        return True, record["detail"]

    def start(self, names: Iterable[str], manual: bool = True) -> dict[str, dict[str, Any]]:
        results: dict[str, dict[str, Any]] = {}
        for name in names:
            if name not in self.specs:
                results[name] = {"ok": False, "detail": f"未知服务：{name}"}
                continue
            record = self.records[name]
            if self._running(name):
                if not record.get("desired"):
                    results[name] = {"ok": False, "detail": f"{name} 正在停止，待进程退出后再启动"}
                    continue
                record["state"] = "running"
                results[name] = {"ok": True, "detail": "已在运行", "pid": record["pid"]}
                continue
            if record.get("pid") is not None:
                self._clear_process(name)
            if manual:
                record["restart_attempts"] = []
                record["next_restart_at"] = None
                record["restart_blocked"] = False
            record["desired"] = True
            ok, detail = self._launch(name, automatic=not manual)
            if not ok and manual:
                record["desired"] = False
                self.save()
            results[name] = {"ok": ok, "detail": detail, "pid": record.get("pid")}
        self.save()
        return results

    def _signal_stop(self, name: str) -> tuple[bool, str]:
        record = self.records[name]
        record["desired"] = False
        record["next_restart_at"] = None
        if not self._running(name):
            if record.get("pid") is not None:
                self._clear_process(name)
            record["state"] = "stopped"
            record["detail"] = "未运行"
            return True, record["detail"]
        pid = int(record["pid"])
        # Re-validate immediately before signalling. Never fall back to name-based killing.
        if not _record_matches_process(record, self.specs[name]):
            record["state"] = "stale"
            record["detail"] = "进程身份已变化，拒绝发送停止信号"
            self._clear_process(name)
            return False, record["detail"]
        try:
            psutil.Process(pid).terminate()
        except psutil.NoSuchProcess:
            self._clear_process(name)
            record["state"] = "stopped"
            record["detail"] = "已停止"
            return True, record["detail"]
        except (psutil.AccessDenied, OSError) as exc:
            record["state"] = "stop_failed"
            record["detail"] = f"无法发送 SIGTERM：{_safe_detail(exc)}"
            return False, record["detail"]
        record["state"] = "stopping"
        record["detail"] = "已发送 SIGTERM，等待退出"
        return True, record["detail"]

    def stop(self, names: Iterable[str]) -> dict[str, dict[str, Any]]:
        selected = list(names)
        results: dict[str, dict[str, Any]] = {}
        for name in selected:
            if name not in self.specs:
                results[name] = {"ok": False, "detail": f"未知服务：{name}"}
                continue
            ok, detail = self._signal_stop(name)
            results[name] = {"ok": ok, "detail": detail}
            if self.records[name]["state"] == "stopping":
                deadline = time.monotonic() + max(0.0, float(self.specs[name].get("stop_timeout", 10.0)))
                while self._running(name) and time.monotonic() < deadline:
                    time.sleep(min(0.05, self.options.poll_interval))
                if not self._running(name):
                    self._clear_process(name)
                    self.records[name]["state"] = "stopped"
                    self.records[name]["detail"] = "已停止"
                    results[name] = {"ok": True, "detail": "已停止"}
                else:
                    self.records[name]["state"] = "stopping"
                    self.records[name]["detail"] = "停止超时，进程仍在退出；未强制终止"
                    results[name] = {
                        "ok": False,
                        "detail": self.records[name]["detail"],
                        "pid": self.records[name]["pid"],
                    }
            self.save()
        self.save()
        return results

    def restart(self, names: Iterable[str]) -> dict[str, dict[str, Any]]:
        selected = list(dict.fromkeys(names))
        results: dict[str, dict[str, Any]] = {}
        unknown = [name for name in selected if name not in self.specs]
        for name in unknown:
            results[name] = {"ok": False, "detail": f"未知服务：{name}"}
        selected = [name for name in selected if name in self.specs]
        if not selected:
            return results

        stop_order = [name for name in STOP_ORDER if name in selected]
        stopped = self.stop(stop_order)
        results.update(stopped)
        if any(not stopped[name].get("ok") or self._running(name) for name in stop_order):
            for name in stop_order:
                if stopped[name].get("ok"):
                    results[name] = {"ok": False, "detail": "已停止；其他服务尚未完成停止，本轮未重新启动"}
            self.save()
            return results

        for name in selected:
            self.records[name]["restart_attempts"] = []
            self.records[name]["restart_blocked"] = False
            self.records[name]["next_restart_at"] = None
        start_order = [name for name in CORE_SERVICES if name in selected]
        start_failed = False
        for name in start_order:
            if start_failed:
                results[name] = {"ok": False, "detail": "前置服务启动失败，本服务保持停止"}
                continue
            started = self.start([name], manual=True)[name]
            if started.get("ok"):
                started["detail"] = "已重启"
            else:
                start_failed = True
            results[name] = started
        self.save()
        return results

    def tick(self) -> None:
        remaining: list[subprocess.Popen[bytes]] = []
        for process in self.retired_handles:
            if process.poll() is None:
                remaining.append(process)
            else:
                try:
                    process.wait(timeout=0)
                except (subprocess.TimeoutExpired, OSError):
                    remaining.append(process)
        self.retired_handles = remaining
        now = time.time()
        changed = False
        for name, record in self.records.items():
            running = self._running(name)
            if running:
                intended_state = "running" if record.get("desired") else "stopping"
                if record.get("state") not in {intended_state, "stop_failed"}:
                    record["state"] = intended_state
                    changed = True
                self._prune_attempts(record, now)
                continue
            if record.get("pid") is not None:
                self._clear_process(name)
                changed = True
            if not record.get("desired"):
                if record.get("state") != "stopped":
                    record["state"] = "stopped"
                    record["detail"] = "已停止"
                    changed = True
                continue
            attempts = self._prune_attempts(record, now)
            if record.get("restart_blocked") or len(attempts) >= self.options.restart_limit:
                record["state"] = "abnormal"
                record["restart_blocked"] = True
                record["next_restart_at"] = None
                record["detail"] = f"{int(self.options.restart_window // 60)}分钟内重启已达{self.options.restart_limit}次，等待手动重启"
                changed = True
                continue
            if record.get("next_restart_at") is None:
                record["next_restart_at"] = now + self._restart_delay(name, record)
                record["state"] = "waiting_restart"
                record["detail"] = "进程异常退出，等待自动重启"
                changed = True
            if float(record["next_restart_at"]) <= now:
                self._launch(name, automatic=True)
                changed = True
        if changed:
            self.save()

    def status(self, names: Iterable[str] | None = None) -> dict[str, Any]:
        selected = list(names) if names is not None else list(self.specs)
        services: dict[str, Any] = {}
        now = time.time()
        for name in selected:
            if name not in self.specs:
                services[name] = {"state": "unknown", "running": False, "detail": f"未知服务：{name}"}
                continue
            record = self.records[name]
            running = self._running(name)
            attempts = self._prune_attempts(record, now)
            state = record.get("state", "stopped")
            if running:
                state = "running" if record.get("desired") else "stopping"
            elif state == "running":
                state = "waiting_restart" if record.get("desired") else "stopped"
            external = [] if running else self._equivalent_external_processes(name)
            port_conflicts = [] if running or external else self._port_conflicts(name)
            if external or port_conflicts:
                state = "unmanaged"
                if external:
                    record_detail = f"检测到未纳管手动实例（PID {', '.join(str(pid) for pid in external)}）"
                else:
                    host, port, pid = port_conflicts[0]
                    owner = f"PID {pid}" if pid is not None else "未知进程"
                    record_detail = f"检测到端口 {host}:{port} 被{owner}占用，实例未纳管"
            else:
                record_detail = _safe_detail(record.get("detail", ""))
            services[name] = {
                "label": str(self.specs[name].get("label", name)),
                "state": state,
                "running": running,
                "pid": record.get("pid") if running else (external[0] if len(external) == 1 else None),
                "desired": bool(record.get("desired")),
                "restart_attempts": len(attempts),
                "restart_limit": self.options.restart_limit,
                "detail": record_detail,
                "log_path": str(self.paths.service_log(name)),
                "enabled": bool(self.specs[name].get("enabled")),
                "disabled_reason": _safe_detail(self.specs[name].get("disabled_reason", "")),
            }
        return {"services": services}

    def close(self, stop_services: bool = False) -> None:
        if stop_services:
            running = [name for name in self.specs if self._running(name)]
            if running:
                self.stop(running)
        for handle in list(self.log_handles.values()):
            try:
                handle.close()
            except OSError:
                pass
        self.log_handles.clear()
        for process in list(self.process_handles.values()) + self.retired_handles:
            if process.poll() is not None:
                try:
                    process.wait(timeout=0)
                except (subprocess.TimeoutExpired, OSError):
                    pass
        self.process_handles = {name: process for name, process in self.process_handles.items() if process.poll() is None}
        self.retired_handles = [process for process in self.retired_handles if process.poll() is None]


class ManagerServer:
    def __init__(self, specs: Mapping[str, Mapping[str, Any]], preflight: Callable[..., str | None], paths: RuntimePaths) -> None:
        self.paths = paths
        self.stopping = False
        self.shutdown_when_idle = False
        _secure_directory(paths.state_dir)
        _secure_log_directory(paths.log_dir)
        self.lock_handle = paths.lock_file.open("a+b")
        os.chmod(paths.lock_file, 0o600)
        try:
            fcntl.flock(self.lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            self.lock_handle.close()
            raise ServiceError("已有平台服务管理器正在运行") from exc
        identity = {
            "pid": os.getpid(),
            "process_identity": _process_identity(psutil.Process()),
            "cwd": str(PROJECT_ROOT),
            "script": str(Path(__file__).resolve()),
        }
        self.engine = SupervisorEngine(
            specs,
            paths=paths,
            preflight=preflight,
            spec_loader=lambda: _load_configuration()[0],
            manager_identity=identity,
        )
        try:
            paths.socket_file.unlink()
        except FileNotFoundError:
            pass
        self.server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.server.bind(str(paths.socket_file))
        os.chmod(paths.socket_file, 0o600)
        self.server.listen(8)
        self.server.settimeout(self.engine.options.poll_interval)

    def request_stop(self, *_args: object) -> None:
        self.stopping = True

    def _dispatch(self, request: Mapping[str, Any]) -> dict[str, Any]:
        action = request.get("action")
        names = request.get("services", [])
        if not isinstance(names, list) or any(not isinstance(name, str) for name in names):
            return {"ok": False, "error": "服务参数格式错误"}
        if action not in {"start", "stop", "restart", "status"}:
            return {"ok": False, "error": "不支持的管理动作"}
        if any(name not in self.engine.specs for name in names):
            return {"ok": False, "error": "包含未知服务"}
        if action == "status":
            return {"ok": True, **self.engine.status(names or None)}
        operation = getattr(self.engine, action)(names)
        if action == "stop" and all(not record.get("desired") for record in self.engine.records.values()):
            self.shutdown_when_idle = True
        return {"ok": all(item.get("ok") for item in operation.values()), "results": operation}

    def _serve_one(self, connection: socket.socket, read_timeout: float = 30.0) -> None:
        connection.settimeout(read_timeout)
        chunks: list[bytes] = []
        size = 0
        try:
            while True:
                block = connection.recv(min(4096, MAX_MESSAGE_BYTES - size + 1))
                if not block:
                    break
                chunks.append(block)
                size += len(block)
                if size > MAX_MESSAGE_BYTES:
                    try:
                        connection.sendall(b'{"ok":false,"error":"request too large"}\n')
                    except (BrokenPipeError, ConnectionResetError, socket.timeout, OSError):
                        pass
                    return
                if b"\n" in block:
                    break
        except (BrokenPipeError, ConnectionResetError, socket.timeout, OSError):
            return
        if not chunks:
            return
        try:
            request = json.loads(b"".join(chunks).split(b"\n", 1)[0].decode("utf-8"))
            response = self._dispatch(request) if isinstance(request, dict) else {"ok": False, "error": "请求格式错误"}
        except (ValueError, UnicodeError) as exc:
            response = {"ok": False, "error": f"请求格式错误：{_safe_detail(exc)}"}
        except Exception as exc:
            response = {"ok": False, "error": f"管理动作失败：{_safe_detail(exc)}"}
        try:
            connection.sendall(json.dumps(response, ensure_ascii=False).encode("utf-8") + b"\n")
        except (BrokenPipeError, ConnectionResetError, socket.timeout, OSError):
            # The operation may already be complete and persisted. A vanished
            # CLI client must never take the long-lived manager down.
            return

    def run(self) -> int:
        signal.signal(signal.SIGTERM, self.request_stop)
        signal.signal(signal.SIGINT, self.request_stop)
        try:
            while not self.stopping:
                self.engine.tick()
                if self.shutdown_when_idle and not any(record.get("pid") for record in self.engine.records.values()):
                    break
                try:
                    connection, _ = self.server.accept()
                except socket.timeout:
                    continue
                with connection:
                    try:
                        self._serve_one(connection)
                    except (BrokenPipeError, ConnectionResetError, socket.timeout, OSError):
                        continue
        finally:
            # SIGTERM/SIGINT is a controlled manager shutdown; only verified owned
            # children are asked to stop. SIGKILL naturally cannot run this path.
            if self.stopping:
                running = [name for name in STOP_ORDER if name in self.engine.specs and self.engine._running(name)]
                if running:
                    self.engine.stop(running)
                self.engine.close(stop_services=False)
            else:
                self.engine.close(stop_services=False)
            self.server.close()
            try:
                self.paths.socket_file.unlink()
            except FileNotFoundError:
                pass
            fcntl.flock(self.lock_handle.fileno(), fcntl.LOCK_UN)
            self.lock_handle.close()
        return 0


def _load_configuration() -> tuple[dict[str, dict[str, Any]], Callable[..., str | None]]:
    from platform_service_config import load_services, preflight_service

    services = load_services()
    if not isinstance(services, dict):
        raise ServiceError("服务配置返回值无效")
    if set(services) != set(CORE_SERVICES):
        raise ServiceError("本版本只允许 backend、celery、controller、caddy 四个服务")
    required = {"label", "command", "cwd", "env", "enabled", "disabled_reason", "stop_timeout", "restart_delay", "ports"}
    normalized: dict[str, dict[str, Any]] = {}
    for name, raw_spec in services.items():
        if not isinstance(name, str) or not isinstance(raw_spec, dict):
            raise ServiceError("服务配置项格式无效")
        missing = required.difference(raw_spec)
        if missing:
            raise ServiceError(f"服务 {name} 缺少配置字段：{', '.join(sorted(missing))}")
        normalized[name] = dict(raw_spec)
    return normalized, preflight_service


def _manager_identity_valid(state: Mapping[str, Any]) -> bool:
    manager = state.get("manager")
    if not isinstance(manager, dict):
        return False
    pid = manager.get("pid")
    identity = manager.get("process_identity")
    if state.get("version") != STATE_VERSION or not isinstance(pid, int) or not isinstance(identity, dict):
        return False
    try:
        process = psutil.Process(pid)
        values = _process_values(process)
    except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
        return False
    if not values:
        return False
    actual_identity, actual_cwd, command = values
    expected = canonical_command([sys.executable, str(Path(__file__).resolve()), "--manager"], str(PROJECT_ROOT))
    return (
        actual_identity == identity
        and actual_cwd == str(PROJECT_ROOT)
        and canonical_command(command, actual_cwd) == expected
    )


def _send_request(paths: RuntimePaths, request: Mapping[str, Any], timeout: float = 60.0) -> dict[str, Any]:
    client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    client.settimeout(timeout)
    try:
        client.connect(str(paths.socket_file))
        client.sendall(json.dumps(request, ensure_ascii=False).encode("utf-8") + b"\n")
        chunks: list[bytes] = []
        size = 0
        while True:
            block = client.recv(min(4096, MAX_MESSAGE_BYTES - size + 1))
            if not block:
                break
            chunks.append(block)
            size += len(block)
            if size > MAX_MESSAGE_BYTES:
                raise ServiceError("管理器响应过大")
            if b"\n" in block:
                break
        response = json.loads(b"".join(chunks).split(b"\n", 1)[0].decode("utf-8"))
        if not isinstance(response, dict):
            raise ValueError("response is not an object")
        return response
    except socket.timeout:
        raise ServiceError("等待管理器响应超时；服务可能仍在按顺序收尾，请运行 platform status 查看进程实况") from None
    except (OSError, ValueError, UnicodeError) as exc:
        raise ServiceError(f"无法与平台服务管理器通信：{_safe_detail(exc)}") from None
    finally:
        client.close()


def _ensure_manager(paths: RuntimePaths) -> None:
    state = _read_json(paths.state_file)
    if _manager_identity_valid(state):
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            try:
                if _send_request(paths, {"action": "status", "services": []}, timeout=0.5).get("ok"):
                    return
            except ServiceError:
                time.sleep(0.1)
        raise ServiceError("平台服务管理器进程存在但通信失败；为避免重复管理器，已拒绝再次启动")
    _secure_directory(paths.state_dir)
    _secure_log_directory(paths.log_dir)
    manager_log = paths.manager_log.open("ab", buffering=0)
    os.chmod(paths.manager_log, 0o600)
    try:
        subprocess.Popen(
            [sys.executable, str(Path(__file__).resolve()), "--manager"],
            cwd=str(PROJECT_ROOT),
            env=os.environ.copy(),
            stdin=subprocess.DEVNULL,
            stdout=manager_log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            close_fds=True,
        )
    finally:
        manager_log.close()
    deadline = time.monotonic() + 8.0
    last_error = "管理器未就绪"
    while time.monotonic() < deadline:
        try:
            response = _send_request(paths, {"action": "status", "services": []}, timeout=1.0)
            if response.get("ok"):
                return
            last_error = _safe_detail(response.get("error", last_error))
        except ServiceError as exc:
            last_error = str(exc)
        time.sleep(0.1)
    raise ServiceError(f"平台服务管理器启动失败：{last_error}；请查看 {paths.manager_log}")


def _default_names(specs: Mapping[str, Any], action: str, state: Mapping[str, Any] | None = None) -> list[str]:
    available_core = [name for name in CORE_SERVICES if name in specs]
    if action in {"start", "restart"}:
        return [name for name in available_core if bool(specs[name].get("enabled"))]
    if action == "stop":
        records = (state or {}).get("services", {})
        return [
            name for name in STOP_ORDER if name in specs
            if bool(specs[name].get("enabled")) or (isinstance(records, dict) and bool(records.get(name, {}).get("pid")))
        ]
    return available_core


def _resolve_names(raw: Sequence[str], specs: Mapping[str, Any], action: str, paths: RuntimePaths) -> list[str]:
    names = list(raw) if raw else _default_names(specs, action, _read_json(paths.state_file))
    unknown = [name for name in names if name not in specs]
    if unknown:
        raise ServiceError(f"未知服务：{', '.join(unknown)}")
    if not names:
        raise ServiceError("没有可操作的服务")
    return list(dict.fromkeys(names))


def _offline_status(specs: Mapping[str, Mapping[str, Any]], paths: RuntimePaths, names: Sequence[str]) -> dict[str, Any]:
    previous = _read_json(paths.state_file)
    records = previous.get("services", {}) if isinstance(previous.get("services"), dict) else {}
    services: dict[str, Any] = {}
    for name in names:
        spec = specs[name]
        record = records.get(name, {}) if isinstance(records, dict) else {}
        running = isinstance(record, dict) and _record_matches_process(record, spec)
        external: list[int] = []
        if not running:
            for process in psutil.process_iter(["pid"]):
                values = _process_values(process)
                if not values:
                    continue
                _created, actual_cwd, actual_command = values
                if _external_command_matches(name, spec, actual_cwd, actual_command):
                    external.append(process.pid)
        unmanaged = bool(external)
        services[name] = {
            "label": str(spec.get("label", name)),
            "state": "orphaned" if running else ("unmanaged" if unmanaged else ("disabled" if not spec.get("enabled") else "stopped")),
            "running": running,
            "pid": record.get("pid") if running else (external[0] if len(external) == 1 else None),
            "desired": bool(record.get("desired")) if isinstance(record, dict) else False,
            "restart_attempts": len(record.get("restart_attempts", [])) if isinstance(record, dict) else 0,
            "restart_limit": EngineOptions().restart_limit,
            "detail": (
                "管理器未运行，但发现经状态身份核验的旧子进程"
                if running
                else (f"检测到未纳管手动实例（PID {', '.join(str(pid) for pid in external)}）" if unmanaged else "管理器未运行")
            ),
            "log_path": str(paths.service_log(name)),
            "enabled": bool(spec.get("enabled")),
            "disabled_reason": _safe_detail(spec.get("disabled_reason", "")),
        }
    return {"manager": {"running": False}, "services": services}


def _run_probe(names: Sequence[str]) -> dict[str, dict[str, Any]]:
    probe = BACKEND_DIR / "scripts" / "check_platform_services.py"
    unknown = {name: {"healthy": None, "detail": "健康探针不可用"} for name in names}
    if not probe.is_file():
        return unknown
    try:
        completed = subprocess.run(
            [sys.executable, str(probe), "--json"],
            cwd=str(BACKEND_DIR),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=15.0,
            check=False,
            text=True,
            encoding="utf-8",
        )
        payload = json.loads(completed.stdout)
        if not isinstance(payload, dict):
            raise ValueError("probe output is not an object")
    except (OSError, subprocess.TimeoutExpired, ValueError, UnicodeError):
        return {name: {"healthy": None, "detail": "健康探针失败，状态未知"} for name in names}
    result: dict[str, dict[str, Any]] = {}
    for name in names:
        item = payload.get(name)
        if not isinstance(item, dict) or not isinstance(item.get("healthy"), bool):
            result[name] = {"healthy": None, "detail": "健康探针未返回有效结果，状态未知"}
        else:
            result[name] = {"healthy": bool(item["healthy"]), "detail": _safe_detail(item.get("detail", ""))}
    return result


STATE_LABELS = {
    "running": "运行中",
    "stopped": "已停止",
    "stopping": "停止中",
    "waiting_restart": "等待重启",
    "abnormal": "异常",
    "stop_failed": "停止失败",
    "stale": "身份失效",
    "orphaned": "管理器遗留",
    "disabled": "已禁用",
    "unmanaged": "未纳管",
    "unknown": "未知",
}


def _print_operation(response: Mapping[str, Any]) -> None:
    if response.get("error"):
        print(f"错误：{response['error']}", file=sys.stderr)
        return
    for name, item in response.get("results", {}).items():
        prefix = "成功" if item.get("ok") else "失败"
        pid = f"（PID {item['pid']}）" if item.get("pid") else ""
        print(f"{name}: {prefix}，{item.get('detail', '')}{pid}")


def _print_status(payload: Mapping[str, Any]) -> None:
    manager = payload.get("manager", {})
    print(f"管理器：{'运行中' if manager.get('running') else '未运行'}")
    print("服务\t运行状态\tPID\t健康状态\t重试\t日志")
    for name, item in payload.get("services", {}).items():
        health = item.get("healthy")
        health_text = "健康" if health is True else ("不健康" if health is False else "未知")
        retry = f"{item.get('restart_attempts', 0)}/{item.get('restart_limit', 5)}"
        state = STATE_LABELS.get(str(item.get("state")), str(item.get("state", "未知")))
        print(f"{name}\t{state}\t{item.get('pid') or '-'}\t{health_text}\t{retry}\t{item.get('log_path', '')}")
        details = [value for value in (item.get("detail"), item.get("health_detail")) if value]
        if details:
            print(f"  {'；'.join(str(value) for value in details)}")


def _tail(path: Path, lines: int) -> list[str]:
    if not path.exists():
        raise ServiceError(f"日志文件不存在：{path}")
    with path.open("r", encoding="utf-8", errors="replace") as source:
        return list(collections.deque(source, maxlen=lines))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="platform", description="统一管理 AI 测试平台本机服务")
    subparsers = parser.add_subparsers(dest="action", required=True)
    for action in ("start", "stop", "restart"):
        command = subparsers.add_parser(action)
        command.add_argument("services", nargs="*", metavar="SERVICE")
    status = subparsers.add_parser("status")
    status.add_argument("services", nargs="*", metavar="SERVICE")
    status.add_argument("--json", action="store_true", dest="as_json")
    logs = subparsers.add_parser("logs")
    logs.add_argument("service", metavar="SERVICE")
    logs.add_argument("--lines", type=int, default=100)
    return parser


def cli(argv: Sequence[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    specs, _preflight = _load_configuration()
    paths = RuntimePaths()
    if arguments.action == "logs":
        if arguments.service not in specs:
            raise ServiceError(f"未知服务：{arguments.service}")
        if arguments.lines < 1 or arguments.lines > 10000:
            raise ServiceError("--lines 必须在 1 到 10000 之间")
        sys.stdout.writelines(_tail(paths.service_log(arguments.service), arguments.lines))
        return 0
    names = _resolve_names(arguments.services, specs, arguments.action, paths)
    if arguments.action == "status":
        state = _read_json(paths.state_file)
        if _manager_identity_valid(state):
            try:
                payload = _send_request(paths, {"action": "status", "services": names})
                payload["manager"] = {"running": True, "pid": state.get("manager", {}).get("pid")}
            except ServiceError:
                payload = _offline_status(specs, paths, names)
                payload["manager"]["detail"] = "管理器进程存在但通信失败"
        else:
            payload = _offline_status(specs, paths, names)
        probes = _run_probe(names)
        for name, item in payload["services"].items():
            item.update({"healthy": probes[name]["healthy"], "health_detail": probes[name]["detail"]})
        if arguments.as_json:
            print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
        else:
            _print_status(payload)
        return 0
    _ensure_manager(paths)
    timeout = 60.0
    if arguments.action in {"stop", "restart"}:
        timeout = max(60.0, sum(float(specs[name].get("stop_timeout", 10.0)) for name in names) + 30.0)
        print(f"正在按顺序{('停止' if arguments.action == 'stop' else '重启')}服务；Celery warm stop 可能需要等待，超时不会强制终止。")
    response = _send_request(paths, {"action": arguments.action, "services": names}, timeout=timeout)
    _print_operation(response)
    return 0 if response.get("ok") else 1


def manager_main() -> int:
    specs, preflight = _load_configuration()
    server = ManagerServer(specs, preflight, RuntimePaths())
    return server.run()


def main() -> int:
    try:
        if sys.argv[1:] == ["--manager"]:
            return manager_main()
        return cli()
    except ServiceError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("已取消", file=sys.stderr)
        return 130
    except Exception as exc:
        if sys.argv[1:] == ["--manager"]:
            traceback.print_exc()
        else:
            print(f"错误：平台服务管理失败：{_safe_detail(exc)}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
