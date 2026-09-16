"""Persistent, fail-closed execution lifecycle for protocol-v2 node commands."""

from __future__ import annotations

import fcntl
import hashlib
import importlib
import ipaddress
import json
import logging
import os
import re
import shutil
import signal
import socket
import ssl
import stat
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import psutil

from .config import NodeConfig
from .errors import AgentStopped, ProtocolError, StateError
from .process_supervisor import minimal_child_environment


logger = logging.getLogger(__name__)
_EXECUTION_FILENAME = "execution.json"
_LOCK_FILENAME = "execution.lock"
_RUNS_DIRECTORY = "runs"
_HEX_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_PREPARE_FIELDS = {
    "type", "run_id", "snapshot", "snapshot_sha256", "script_source",
    "script_sha256", "lease_seconds", "max_seconds", "tls", "handshake_token",
}
_TLS_FIELDS = {"host", "port", "server_name", "ca_pem", "cert_pem", "key_pem"}


@dataclass(frozen=True)
class RuntimeContract:
    validate_snapshot: Callable[[dict[str, Any]], Any]
    canonical_sha256: Callable[[object], str]
    source_path: Path


@dataclass(frozen=True)
class PreparedCommand:
    run_id: str
    snapshot: dict[str, Any]
    snapshot_sha256: str
    script_source: str
    script_sha256: str
    lease_seconds: int
    max_seconds: int
    tls: dict[str, Any]
    handshake_token: str
    fingerprint: str


def load_runtime_contract() -> RuntimeContract:
    try:
        module = importlib.import_module("performance_node.locust_runtime")
    except (ImportError, OSError) as exc:
        raise ProtocolError("固定执行模板尚未安装") from exc
    validate = getattr(module, "validate_snapshot", None)
    canonical = getattr(module, "canonical_sha256", None)
    source_name = getattr(module, "__file__", None)
    if not callable(validate) or not callable(canonical) or not source_name:
        raise ProtocolError("固定执行模板接口不完整")
    source_path = Path(source_name)
    if not source_path.is_file():
        raise ProtocolError("固定执行模板文件不存在")
    return RuntimeContract(validate, canonical, source_path)


def _uuid(value: object, field: str) -> str:
    if not isinstance(value, str):
        raise ProtocolError(f"命令 {field} 不符合协议")
    try:
        return str(uuid.UUID(value))
    except ValueError as exc:
        raise ProtocolError(f"命令 {field} 不符合协议") from exc


def _positive_int(value: object, field: str, maximum: int | None = None) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ProtocolError(f"命令 {field} 不符合协议")
    if maximum is not None and value > maximum:
        raise ProtocolError(f"命令 {field} 超出上限")
    return value


def _sha(value: object, field: str) -> str:
    if not isinstance(value, str) or not _HEX_SHA256.fullmatch(value):
        raise ProtocolError(f"命令 {field} 不符合协议")
    return value


def _safe_text(value: object, field: str, maximum: int) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value.encode("utf-8")) > maximum
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        raise ProtocolError(f"命令 {field} 不符合协议")
    return value


def _atomic_write(path: Path, payload: bytes, mode: int = 0o600) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    descriptor: int | None = None
    temporary_name: str | None = None
    try:
        descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}-", dir=path.parent)
        os.fchmod(descriptor, mode)
        with os.fdopen(descriptor, "wb") as handle:
            descriptor = None
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, path)
        temporary_name = None
    except OSError as exc:
        raise StateError("无法安全保存执行状态") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if temporary_name is not None:
            try:
                os.unlink(temporary_name)
            except FileNotFoundError:
                pass


class ExecutionStateStore:
    def __init__(self, state_dir: Path):
        self.path = state_dir / _EXECUTION_FILENAME

    def load(self) -> dict[str, Any]:
        try:
            details = self.path.lstat()
        except FileNotFoundError:
            return {"version": 1, "active": None, "report": None, "completed_runs": {}}
        if stat.S_ISLNK(details.st_mode) or not stat.S_ISREG(details.st_mode) or details.st_mode & 0o077:
            raise StateError("执行状态文件类型或权限不安全")
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            raise StateError("执行状态文件已损坏；已停止以防止重复执行") from exc
        if (
            not isinstance(data, dict)
            or set(data) != {"version", "active", "report", "completed_runs"}
            or data["version"] != 1
            or isinstance(data["version"], bool)
            or not isinstance(data["completed_runs"], dict)
            or any(not isinstance(key, str) or not isinstance(value, dict) for key, value in data["completed_runs"].items())
            or (data["active"] is not None and not isinstance(data["active"], dict))
            or (data["report"] is not None and not isinstance(data["report"], dict))
        ):
            raise StateError("执行状态文件已损坏；已停止以防止重复执行")
        return data

    def save(self, data: dict[str, Any]) -> None:
        _atomic_write(
            self.path,
            json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8"),
        )


def _command_fingerprint(command: dict[str, Any]) -> str:
    try:
        encoded = json.dumps(
            command, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ProtocolError("prepare 命令不是有效 JSON 数据") from exc
    return hashlib.sha256(encoded).hexdigest()


def _validate_host(value: object, field: str) -> str:
    host = _safe_text(value, field, 253)
    if any(character in host for character in "/\\[]:"):
        try:
            return str(ipaddress.ip_address(host))
        except ValueError as exc:
            raise ProtocolError(f"命令 {field} 不符合协议") from exc
    try:
        encoded = host.encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise ProtocolError(f"命令 {field} 不符合协议") from exc
    if len(encoded) > 253 or any(not label or len(label) > 63 for label in encoded.rstrip(".").split(".")):
        raise ProtocolError(f"命令 {field} 不符合协议")
    if not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?", encoded):
        raise ProtocolError(f"命令 {field} 不符合协议")
    return encoded


def validate_prepare_command(command: dict[str, Any], node_id: str) -> PreparedCommand:
    if set(command) != _PREPARE_FIELDS or command.get("type") != "prepare":
        raise ProtocolError("prepare 命令字段不符合协议")
    run_id = _uuid(command["run_id"], "run_id")
    snapshot = command["snapshot"]
    if not isinstance(snapshot, dict):
        raise ProtocolError("命令 snapshot 不符合协议")
    if _uuid(snapshot.get("run_id"), "snapshot.run_id") != run_id:
        raise ProtocolError("命令 run_id 与快照不一致")
    if _uuid(snapshot.get("node_id"), "snapshot.node_id") != node_id:
        raise ProtocolError("命令快照不属于当前节点")

    contract = load_runtime_contract()
    try:
        contract.validate_snapshot(snapshot)
        actual_snapshot_sha = contract.canonical_sha256(snapshot)
    except Exception as exc:
        raise ProtocolError("命令 snapshot 未通过固定模板校验") from exc
    snapshot_sha = _sha(command["snapshot_sha256"], "snapshot_sha256")
    if actual_snapshot_sha != snapshot_sha:
        raise ProtocolError("命令 snapshot 摘要不匹配")

    script_source = command["script_source"]
    if not isinstance(script_source, str) or not script_source or len(script_source.encode("utf-8")) > 262_144:
        raise ProtocolError("命令 script_source 不符合协议")
    script_sha = _sha(command["script_sha256"], "script_sha256")
    source_bytes = script_source.encode("utf-8")
    try:
        local_bytes = contract.source_path.read_bytes()
    except OSError as exc:
        raise ProtocolError("无法读取本地固定执行模板") from exc
    if hashlib.sha256(source_bytes).hexdigest() != script_sha:
        raise ProtocolError("命令脚本摘要不匹配")
    if hashlib.sha256(local_bytes).hexdigest() != script_sha or local_bytes != source_bytes:
        raise ProtocolError("平台固定脚本与本机版本不一致；请升级节点")

    lease_seconds = _positive_int(command["lease_seconds"], "lease_seconds", 60)
    max_seconds = _positive_int(command["max_seconds"], "max_seconds", 675)
    duration = _positive_int(snapshot.get("duration_seconds"), "snapshot.duration_seconds", 600)
    if max_seconds > duration + 75:
        raise ProtocolError("命令 max_seconds 超出本地运行边界")

    tls = command["tls"]
    if not isinstance(tls, dict) or set(tls) != _TLS_FIELDS:
        raise ProtocolError("命令 tls 字段不符合协议")
    normalized_tls = {
        "host": _validate_host(tls["host"], "tls.host"),
        "port": _positive_int(tls["port"], "tls.port", 65535),
        "server_name": _validate_host(tls["server_name"], "tls.server_name"),
    }
    for field in ("ca_pem", "cert_pem", "key_pem"):
        value = tls[field]
        if not isinstance(value, str) or not value or len(value.encode("utf-8")) > 65_536:
            raise ProtocolError(f"命令 tls.{field} 不符合协议")
        normalized_tls[field] = value
    handshake_token = _safe_text(command["handshake_token"], "handshake_token", 512)
    if len(handshake_token) < 32 or not re.fullmatch(r"[A-Za-z0-9_-]+", handshake_token):
        raise ProtocolError("命令 handshake_token 不符合协议")
    return PreparedCommand(
        run_id=run_id,
        snapshot=snapshot,
        snapshot_sha256=snapshot_sha,
        script_source=script_source,
        script_sha256=script_sha,
        lease_seconds=lease_seconds,
        max_seconds=max_seconds,
        tls=normalized_tls,
        handshake_token=handshake_token,
        fingerprint=_command_fingerprint(command),
    )


def _allocate_loopback_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _stunnel_target(host: str, port: int) -> str:
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return f"{host}:{port}"
    return f"[{address}]:{port}" if address.version == 6 else f"{address}:{port}"


class RunExecutor:
    """Own one node-local run and make duplicate/restart behavior persistent."""

    def __init__(self, config: NodeConfig, node_id: str):
        self.config = config
        self.node_id = _uuid(node_id, "node_id")
        self._validate_state_directory()
        self.store = ExecutionStateStore(config.state_dir)
        self._lock_handle = self._acquire_lock()
        try:
            self.state = self.store.load()
            self._recover_after_restart()
        except Exception:
            self.close()
            raise

    def _validate_state_directory(self) -> None:
        try:
            details = self.config.state_dir.lstat()
        except OSError as exc:
            raise StateError("执行状态目录不存在或不可读取") from exc
        if (
            stat.S_ISLNK(details.st_mode)
            or not stat.S_ISDIR(details.st_mode)
            or details.st_mode & 0o077
        ):
            raise StateError("执行状态目录必须是专用的 0700 目录")

    def _acquire_lock(self):
        path = self.config.state_dir / _LOCK_FILENAME
        descriptor = os.open(path, os.O_RDWR | os.O_CREAT | os.O_CLOEXEC, 0o600)
        os.fchmod(descriptor, 0o600)
        handle = os.fdopen(descriptor, "a+b", buffering=0)
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            handle.close()
            raise AgentStopped("另一个节点进程正在管理执行状态") from exc
        return handle

    def close(self) -> None:
        if self._lock_handle is not None:
            self._lock_handle.close()
            self._lock_handle = None

    def _save(self) -> None:
        self.store.save(self.state)

    def _completed(self, run_id: str) -> bool:
        return run_id in self.state["completed_runs"]

    def _remember_completed(self, run_id: str, report: dict[str, Any]) -> None:
        self.state["completed_runs"][run_id] = dict(report)

    @staticmethod
    def _verified_process(pid: object, created_at: object) -> psutil.Process | None:
        if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0 or not isinstance(created_at, (int, float)):
            return None
        try:
            process = psutil.Process(pid)
            if abs(process.create_time() - float(created_at)) > 0.01:
                return None
            return process
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            return None

    def _stop_supervisor(self, active: dict[str, Any]) -> None:
        process = self._verified_process(active.get("supervisor_pid"), active.get("supervisor_created_at"))
        if process is None:
            return
        try:
            process.send_signal(signal.SIGTERM)
            process.wait(timeout=7)
        except psutil.TimeoutExpired:
            try:
                process.kill()
                process.wait(timeout=2)
            except (psutil.NoSuchProcess, psutil.TimeoutExpired):
                pass
        except psutil.NoSuchProcess:
            pass

    def _recover_after_restart(self) -> None:
        active = self.state.get("active")
        if active is None:
            return
        run_id = active.get("run_id")
        if not isinstance(run_id, str):
            raise StateError("执行状态文件已损坏；无法安全回收旧任务")
        self._stop_supervisor(active)
        self._finish(run_id, "failed", "agent_restarted", "节点进程重启，旧任务已回收且不会重放")

    def _finish(self, run_id: str, state: str, reason_code: str, reason: str) -> None:
        previous = self.state.get("active") or self.state.get("report") or {}
        sequence = previous.get("sequence", 0)
        if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence < 0:
            sequence = 0
        self.state["active"] = None
        report = {
            "run_id": run_id,
            "state": state,
            "reason_code": reason_code,
            "reason": reason,
            "sequence": sequence,
        }
        self.state["report"] = report
        self._remember_completed(run_id, report)
        self._remove_run_secrets(previous, run_id)
        self._save()

    def _remove_run_secrets(self, previous: dict[str, Any], run_id: str) -> None:
        lease_name = previous.get("lease_file")
        if not isinstance(lease_name, str):
            return
        run_dir = Path(lease_name).parent
        expected = self.config.state_dir / _RUNS_DIRECTORY / run_id
        # Never broaden cleanup based on persisted paths. This exact lexical
        # ownership check limits deletion to the current UUID run directory.
        if run_dir != expected or expected.name != run_id:
            logger.error("运行秘密清理被拒绝：run_id=%s", run_id)
            return
        for name in ("client-key.pem", "runtime.json"):
            try:
                (run_dir / name).unlink(missing_ok=True)
            except OSError:
                logger.warning("运行秘密清理失败：run_id=%s file=%s", run_id, name)

    def poll(self) -> None:
        active = self.state.get("active")
        if active is None:
            return
        result_path = Path(active["result_file"])
        process = self._verified_process(active.get("supervisor_pid"), active.get("supervisor_created_at"))
        if not result_path.exists() and process is not None and process.is_running():
            return
        reason = "child_failed"
        try:
            result = json.loads(result_path.read_text(encoding="utf-8"))
            if isinstance(result, dict) and result.get("reason") in {
                "finished", "lease_expired", "timeout", "child_failed", "stopped",
            }:
                reason = result["reason"]
        except (OSError, ValueError, json.JSONDecodeError):
            pass
        mapping = {
            "finished": ("stopped", "finished", "执行进程正常结束"),
            "stopped": ("stopped", "stopped", "执行进程已停止"),
            "lease_expired": ("failed", "lease_expired", "执行租约过期，进程已回收"),
            "timeout": ("failed", "timeout", "执行超过本地时限，进程已回收"),
            "child_failed": ("failed", "child_failed", "执行子进程异常退出"),
        }
        report_state, code, message = mapping[reason]
        self._finish(active["run_id"], report_state, code, message)

    def report_for_heartbeat(self) -> dict[str, Any] | None:
        self.poll()
        source = self.state.get("active") or self.state.get("report")
        if source is None:
            return None
        sequence = source.get("sequence", 0) + 1
        source["sequence"] = sequence
        if self.state.get("active") is source:
            self.state["active"] = source
        else:
            self.state["report"] = source
            self.state["completed_runs"][source["run_id"]] = dict(source)
        self._save()
        return {
            "run_id": source["run_id"],
            "sequence": sequence,
            "state": source["state"],
            "reason_code": source.get("reason_code", ""),
            "reason": source.get("reason", ""),
        }

    def handle_command(self, command: dict[str, Any]) -> None:
        command_type = command.get("type")
        if command_type == "idle":
            if set(command) != {"type"}:
                raise ProtocolError("idle 命令字段不符合协议")
            if self.state.get("active") is not None:
                self.stop_active("server_idle", "平台已取消本地指派", report_state="stopped")
            elif self.state.get("report") is not None:
                self.state["report"] = None
                self._save()
            return
        if command_type == "stop":
            if set(command) != {"type", "run_id", "reason"}:
                raise ProtocolError("stop 命令字段不符合协议")
            run_id = _uuid(command["run_id"], "run_id")
            _safe_text(command["reason"], "reason", 1024)
            active = self.state.get("active")
            if active is not None and active.get("run_id") != run_id:
                self.stop_active("run_mismatch", "停止命令与本地活动任务不匹配", report_state="failed")
                raise AgentStopped("停止命令与本地活动任务不匹配")
            if active is not None:
                self.stop_active("server_stop", "平台要求停止执行", report_state="stopped")
            elif (self.state.get("report") or {}).get("run_id") == run_id:
                # This stop is the server's acknowledgement of the terminal
                # report sent in the current heartbeat. Keep the completed-run
                # record for no-replay, but release the report slot so a new run
                # can be selected on the next heartbeat.
                self.state["report"] = None
                self._save()
            return
        if command_type != "prepare":
            if self.state.get("active") is not None:
                self.stop_active("unknown_command", "平台命令无法识别", report_state="failed")
            raise AgentStopped("收到未知执行命令，节点已停止")
        prepared = validate_prepare_command(command, self.node_id)
        self._handle_prepare(prepared)

    def _handle_prepare(self, command: PreparedCommand) -> None:
        self.poll()
        active = self.state.get("active")
        if active is not None:
            if active["run_id"] != command.run_id:
                self.stop_active("overlapping_run", "收到不同任务时本地仍有活动任务", report_state="failed")
                raise AgentStopped("拒绝重叠执行任务")
            if active["command_fingerprint"] != command.fingerprint:
                self.stop_active("command_changed", "重复任务的冻结命令发生变化", report_state="failed")
                raise AgentStopped("重复 prepare 命令内容不一致")
            self._renew_lease(Path(active["lease_file"]))
            return
        if self._completed(command.run_id):
            self.state["report"] = dict(self.state["completed_runs"][command.run_id])
            self._save()
            return
        self._start(command)

    def _stunnel_binary(self) -> str:
        configured = self.config.stunnel_binary
        # This value is trusted local configuration, never a remote command
        # field. Resolve it once using the Agent's PATH, then pass only the
        # absolute executable into the minimal child environment.
        resolved = configured if configured and os.path.isabs(configured) else shutil.which(configured or "")
        if not resolved or not os.path.isfile(resolved) or not os.access(resolved, os.X_OK):
            raise ProtocolError("本机未安装可执行的 stunnel")
        return os.path.abspath(resolved)

    @staticmethod
    def _renew_lease(path: Path) -> None:
        try:
            path.touch(exist_ok=True)
            os.chmod(path, 0o600)
        except OSError as exc:
            raise StateError("无法续写本地执行租约") from exc

    def _start(self, command: PreparedCommand) -> None:
        runs_dir = self.config.state_dir / _RUNS_DIRECTORY
        try:
            runs_dir.mkdir(mode=0o700, exist_ok=True)
            if runs_dir.is_symlink() or runs_dir.stat().st_mode & 0o077:
                raise StateError("任务根目录权限不安全")
        except OSError as exc:
            raise StateError("无法创建安全的任务根目录") from exc
        run_dir = runs_dir / command.run_id
        try:
            run_dir.mkdir(mode=0o700, parents=True, exist_ok=False)
        except FileExistsError:
            report = {
                "run_id": command.run_id, "state": "failed", "reason_code": "local_run_exists",
                "reason": "本地已存在同任务目录，为防重跑已拒绝执行", "sequence": 0,
            }
            self.state["report"] = report
            self._remember_completed(command.run_id, report)
            self._save()
            return
        except OSError as exc:
            raise StateError("无法创建安全的任务目录") from exc

        active = {
            "run_id": command.run_id,
            "state": "preparing",
            "reason_code": "",
            "reason": "",
            "sequence": 0,
            "command_fingerprint": command.fingerprint,
            "lease_file": str(run_dir / "lease"),
            "result_file": str(run_dir / "result.json"),
            "supervisor_pid": None,
            "supervisor_created_at": None,
        }
        self.state["active"] = active
        self.state["report"] = None
        self._save()  # Persist no-replay ownership before creating any child.
        stage = "runtime_material"
        spawned_process: subprocess.Popen[bytes] | None = None
        try:
            self._write_runtime_files(run_dir, command)
            stage = "supervisor_config"
            supervisor_config = self._write_supervisor_config(run_dir, command)
            self._renew_lease(Path(active["lease_file"]))
            stage = "supervisor_spawn"
            log_descriptor = os.open(
                run_dir / "supervisor.log",
                os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_CLOEXEC,
                0o600,
            )
            with os.fdopen(log_descriptor, "ab", buffering=0) as log:
                process = subprocess.Popen(
                    [sys.executable, str(Path(__file__).with_name("process_supervisor.py")), str(supervisor_config)],
                    cwd=run_dir,
                    stdin=subprocess.DEVNULL,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    env=minimal_child_environment(),
                    start_new_session=True,
                    close_fds=True,
                )
            spawned_process = process
            stage = "supervisor_identity"
            active["supervisor_pid"] = process.pid
            active["supervisor_created_at"] = psutil.Process(process.pid).create_time()
            # Reap the supervisor itself even while the main thread is blocked
            # in HTTPS. Its own lease watchdog already reaps engine/tunnel.
            threading.Thread(
                target=process.wait,
                name=f"performance-supervisor-wait-{command.run_id}",
                daemon=True,
            ).start()
            self._save()
            if not self._wait_until_children_alive(process):
                self.poll()
                if self.state.get("active") is not None:
                    self.stop_active("startup_failed", "执行子进程未能稳定启动", report_state="failed")
                return
            active["state"] = "ready"
            self._save()
        except (OSError, ValueError, ssl.SSLError, subprocess.SubprocessError, psutil.Error, ProtocolError, StateError) as exc:
            if (
                spawned_process is not None
                and spawned_process.poll() is None
                and active.get("supervisor_created_at") is None
            ):
                # The child is unquestionably ours through this live Popen
                # handle, even if psutil identity capture failed before state
                # persistence made PID-based recovery safe.
                spawned_process.send_signal(signal.SIGTERM)
                try:
                    spawned_process.wait(timeout=7)
                except subprocess.TimeoutExpired:
                    spawned_process.kill()
                    spawned_process.wait(timeout=2)
            codes = {
                "runtime_material": ("prepare_material_failed", "本地运行材料或 TLS 凭证准备失败"),
                "supervisor_config": ("prepare_supervisor_config_failed", "本地监督配置准备失败"),
                "supervisor_spawn": ("prepare_supervisor_start_failed", "本地监督进程启动失败"),
                "supervisor_identity": ("prepare_supervisor_identity_failed", "本地监督进程身份记录失败"),
            }
            code, reason = codes[stage]
            logger.error(
                "执行准备失败：stage=%s exception_type=%s",
                stage,
                type(exc).__name__,
            )
            self.stop_active(code, reason, report_state="failed")

    @staticmethod
    def _wait_until_children_alive(process: subprocess.Popen[bytes]) -> bool:
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            if process.poll() is not None:
                return False
            try:
                children = psutil.Process(process.pid).children(recursive=False)
                if len([child for child in children if child.is_running()]) >= 2:
                    return True
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                return False
            time.sleep(0.05)
        return False

    def _write_runtime_files(self, run_dir: Path, command: PreparedCommand) -> None:
        paths = {
            "ca": run_dir / "ca.pem",
            "cert": run_dir / "client-cert.pem",
            "key": run_dir / "client-key.pem",
            "script": run_dir / "locust_runtime.py",
            "runtime": run_dir / "runtime.json",
        }
        _atomic_write(paths["ca"], command.tls["ca_pem"].encode("utf-8"))
        _atomic_write(paths["cert"], command.tls["cert_pem"].encode("utf-8"))
        _atomic_write(paths["key"], command.tls["key_pem"].encode("utf-8"))
        _atomic_write(paths["script"], command.script_source.encode("utf-8"))
        _atomic_write(paths["runtime"], json.dumps(
            {"snapshot": command.snapshot, "handshake_token": command.handshake_token},
            sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False,
        ).encode("utf-8"))
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.verify_mode = ssl.CERT_REQUIRED
        context.check_hostname = True
        context.load_verify_locations(cafile=str(paths["ca"]))
        context.load_cert_chain(certfile=str(paths["cert"]), keyfile=str(paths["key"]))

    def _write_supervisor_config(self, run_dir: Path, command: PreparedCommand) -> Path:
        local_port = _allocate_loopback_port()
        server_name = command.tls["server_name"]
        try:
            ipaddress.ip_address(server_name)
            identity_check = f"checkIP = {server_name}"
        except ValueError:
            identity_check = f"checkHost = {server_name}"
        stunnel_config = "\n".join((
            "foreground = yes",
            "pid =",
            "syslog = no",
            "debug = warning",
            "client = yes",
            "verifyChain = yes",
            "sslVersionMin = TLSv1.2",
            "[performance-master]",
            f"accept = 127.0.0.1:{local_port}",
            f"connect = {_stunnel_target(command.tls['host'], command.tls['port'])}",
            f"CAfile = {run_dir / 'ca.pem'}",
            f"cert = {run_dir / 'client-cert.pem'}",
            f"key = {run_dir / 'client-key.pem'}",
            identity_check,
            f"sni = {server_name}",
            "TIMEOUTconnect = 5",
            "TIMEOUTclose = 0",
            "",
        ))
        stunnel_path = run_dir / "stunnel.conf"
        _atomic_write(stunnel_path, stunnel_config.encode("utf-8"))
        supervisor_path = run_dir / "supervisor.json"
        payload = {
            "processes": [
                {
                    "name": "tunnel",
                    "argv": [self._stunnel_binary(), str(stunnel_path)],
                    "cwd": str(run_dir),
                    "log_path": str(run_dir / "tunnel.log"),
                },
                {
                    "name": "engine",
                    "argv": [
                        sys.executable, str(run_dir / "locust_runtime.py"),
                        "--role", "worker", "--config", str(run_dir / "runtime.json"),
                        "--master-host", "127.0.0.1", "--master-port", str(local_port),
                    ],
                    "cwd": str(run_dir),
                    "log_path": str(run_dir / "engine.log"),
                },
            ],
            "lease_file": str(run_dir / "lease"),
            "lease_seconds": command.lease_seconds,
            "max_seconds": command.max_seconds,
            "result_file": str(run_dir / "result.json"),
        }
        _atomic_write(
            supervisor_path,
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8"),
        )
        return supervisor_path

    def stop_active(self, reason_code: str, reason: str, *, report_state: str = "stopped") -> None:
        active = self.state.get("active")
        if active is None:
            return
        self._stop_supervisor(active)
        self._finish(active["run_id"], report_state, reason_code, reason)

    def shutdown(self) -> None:
        try:
            self.stop_active("agent_stopped", "节点进程退出，执行已回收", report_state="stopped")
        finally:
            self.close()
