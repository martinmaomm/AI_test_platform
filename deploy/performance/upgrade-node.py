#!/usr/bin/env python3
"""Safely replace one standalone performance-node Docker container.

The old container is retained as a stopped backup.  This tool deliberately
supports only configurations that Docker can clone without delegating to a
higher-level manager (Compose, Swarm or Unraid).
"""

from __future__ import annotations

import argparse
import copy
import fcntl
import http.client
import io
import json
import os
import platform as host_platform
import re
import socket
import stat
import subprocess
import sys
import tarfile
import time
import urllib.parse
import uuid
from pathlib import Path
from typing import Any


OWNER_LABEL = "com.automation-platform.owner"
NODE_LABEL = "com.automation-platform.node-id"
PLATFORM_LABEL = "com.automation-platform.platform"
ROLE_LABEL = "com.automation-platform.role"
INSTALLER_OWNER = "performance-node-installer-v1"
DEFAULT_STATE_DIR = "/var/lib/performance-node"
LOCK_DIR = Path("/run/lock/automation-performance-nodes")
MANAGED_LABEL_PREFIXES = ("com.docker.compose.", "com.docker.swarm.", "net.unraid.docker.")
IMAGE_PATTERN = re.compile(r"^docker\.io/[a-z0-9][a-z0-9-]*/[a-z0-9][a-z0-9._-]*@sha256:[0-9a-f]{64}$")
VERSION_PATTERN = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")


class UpgradeError(RuntimeError):
    pass


def log(message: str) -> None:
    print(f"[性能节点升级] {message}")


def fail(message: str) -> "NoReturn":  # type: ignore[name-defined]
    raise UpgradeError(message)


def normalized_platform(value: str) -> str:
    try:
        parsed = urllib.parse.urlsplit(value)
        port = parsed.port
    except ValueError:
        fail("--platform URL 无效")
    if (
        parsed.scheme != "https"
        or not parsed.netloc
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or port == 0
        or any(char.isspace() or char == "\\" or ord(char) < 32 for char in value)
    ):
        fail("--platform 必须是不含用户信息、查询或片段的 HTTPS URL")
    return urllib.parse.urlunsplit(("https", parsed.netloc, parsed.path.rstrip("/"), "", ""))


def normalize_uuid(value: str) -> str:
    try:
        return str(uuid.UUID(value))
    except (ValueError, TypeError, AttributeError):
        fail("--node-id 必须是标准 UUID")


def verify_host() -> None:
    if host_platform.system() != "Linux":
        fail("升级工具只能在 Linux 节点宿主机运行")
    if os.geteuid() != 0:
        fail("必须以 root 身份运行（例如 sudo python3 upgrade-node.py ...）")


def acquire_lock(node_id: str) -> int:
    try:
        LOCK_DIR.mkdir(mode=0o700, parents=False, exist_ok=True)
        directory = LOCK_DIR.lstat()
    except OSError:
        fail("无法创建或检查节点锁目录")
    if not stat.S_ISDIR(directory.st_mode) or stat.S_ISLNK(directory.st_mode):
        fail("节点锁目录类型不安全")
    if directory.st_uid != os.geteuid() or stat.S_IMODE(directory.st_mode) != 0o700:
        fail("节点锁目录必须由 root 拥有且权限为 0700")
    lock_path = LOCK_DIR / f"{node_id}.lock"  # shared with install-node.sh
    flags = os.O_RDWR | os.O_CREAT | os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        existed = lock_path.exists()
        descriptor = os.open(lock_path, flags, 0o600)
        details = os.fstat(descriptor)
        if (
            not stat.S_ISREG(details.st_mode)
            or details.st_uid != os.geteuid()
            or (existed and stat.S_IMODE(details.st_mode) != 0o600)
        ):
            fail("节点锁文件类型、属主或权限不安全")
        os.fchmod(descriptor, 0o600)
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        try:
            os.close(descriptor)
        except (OSError, UnboundLocalError):
            pass
        fail("该节点已有安装或升级进程在运行，请稍后重试")
    except OSError:
        try:
            os.close(descriptor)
        except (OSError, UnboundLocalError):
            pass
        fail("无法安全锁定节点升级")
    return descriptor


def run_cli(arguments: list[str], *, capture: bool = True) -> str:
    try:
        result = subprocess.run(
            arguments,
            check=True,
            text=True,
            stdout=subprocess.PIPE if capture else subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            timeout=300,
        )
    except (OSError, subprocess.SubprocessError):
        fail("Docker 命令执行失败；目标容器尚未变更，请检查本机 Docker 后重试")
    return result.stdout.strip() if capture else ""


def docker_socket() -> str:
    if os.environ.get("DOCKER_HOST", "").strip() and not os.environ["DOCKER_HOST"].startswith("unix://"):
        fail("拒绝远程 DOCKER_HOST；只能升级本机 Unix socket Docker")
    raw = run_cli(["docker", "context", "inspect"])
    try:
        contexts = json.loads(raw)
        endpoint = contexts[0]["Endpoints"]["docker"]["Host"]
    except (KeyError, IndexError, TypeError, json.JSONDecodeError):
        fail("无法读取当前 Docker context")
    if not isinstance(endpoint, str) or not endpoint.startswith("unix://"):
        fail("拒绝远程 Docker context；只能升级本机 Unix socket Docker")
    path = endpoint[len("unix://"):]
    if not path.startswith("/"):
        fail("Docker Unix socket 路径无效")
    docker_host = os.environ.get("DOCKER_HOST", "").strip()
    if docker_host.startswith("unix://") and docker_host[len("unix://"):] != path:
        fail("DOCKER_HOST 与当前 Docker context 指向不同 daemon，拒绝升级")
    return path


class UnixHTTPConnection(http.client.HTTPConnection):
    def __init__(self, socket_path: str, timeout: float = 30) -> None:
        super().__init__("localhost", timeout=timeout)
        self.socket_path = socket_path

    def connect(self) -> None:
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        self.sock.connect(self.socket_path)


class DockerAPI:
    def __init__(self, socket_path: str) -> None:
        self.socket_path = socket_path
        self.prefix = ""
        version = self.request("GET", "/version")
        if not isinstance(version, dict) or not isinstance(version.get("ApiVersion"), str):
            fail("Docker daemon 版本响应无效")
        major_minor = version["ApiVersion"].split(".")
        if len(major_minor) != 2 or not all(part.isdigit() for part in major_minor):
            fail("Docker daemon API 版本无效")
        self.prefix = f"/v{version['ApiVersion']}"

    def request(
        self,
        method: str,
        path: str,
        body: object | None = None,
        *,
        expected: tuple[int, ...] = (200,),
        raw: bool = False,
    ) -> Any:
        payload = None if body is None else json.dumps(body, separators=(",", ":")).encode()
        headers = {"Content-Type": "application/json"} if payload is not None else {}
        connection = UnixHTTPConnection(self.socket_path)
        try:
            connection.request(method, self.prefix + path, payload, headers)
            response = connection.getresponse()
            data = response.read(2 * 1024 * 1024 + 1)
        except (OSError, http.client.HTTPException):
            fail("无法连接本机 Docker daemon")
        finally:
            connection.close()
        if len(data) > 2 * 1024 * 1024:
            fail("Docker daemon 响应过大，拒绝继续")
        if response.status not in expected:
            fail(f"Docker 操作失败（HTTP {response.status}）；请检查容器状态后重试")
        if raw:
            return data
        if not data:
            return None
        try:
            return json.loads(data)
        except json.JSONDecodeError:
            fail("Docker daemon 返回了无效 JSON")

    def containers(self) -> list[dict[str, Any]]:
        value = self.request("GET", "/containers/json?all=1")
        if not isinstance(value, list):
            fail("Docker 容器列表响应无效")
        return value

    def inspect(self, container_id: str) -> dict[str, Any]:
        value = self.request("GET", f"/containers/{container_id}/json")
        if not isinstance(value, dict):
            fail("Docker 容器详情响应无效")
        return value

    def network(self, network_id: str) -> dict[str, Any]:
        value = self.request("GET", f"/networks/{network_id}")
        if not isinstance(value, dict):
            fail("Docker 网络详情响应无效")
        return value

    def file_json(self, container_id: str, path: str, *, required: bool = True) -> dict[str, Any] | None:
        encoded = urllib.parse.quote(path, safe="")
        try:
            archive = self.request("GET", f"/containers/{container_id}/archive?path={encoded}", raw=True)
        except UpgradeError as exc:
            if not required and "HTTP 404" in str(exc):
                return None
            raise
        try:
            with tarfile.open(fileobj=io.BytesIO(archive), mode="r:*") as bundle:
                members = [member for member in bundle.getmembers() if member.isfile()]
                if (
                    len(members) != 1
                    or members[0].size > 1024 * 1024
                    or members[0].mode & 0o077
                ):
                    fail("节点状态文件归档无效")
                source = bundle.extractfile(members[0])
                if source is None:
                    fail("节点状态文件无法读取")
                value = json.loads(source.read().decode("utf-8"))
        except (tarfile.TarError, UnicodeError, json.JSONDecodeError, OSError):
            fail("节点状态文件已损坏，拒绝升级")
        if not isinstance(value, dict):
            fail("节点状态文件格式无效，拒绝升级")
        return value

    def update_restart(self, container_id: str, policy: dict[str, Any]) -> None:
        self.request("POST", f"/containers/{container_id}/update", {"RestartPolicy": policy})

    def stop(self, container_id: str, seconds: int) -> None:
        self.request("POST", f"/containers/{container_id}/stop?t={seconds}", expected=(204, 304))

    def start(self, container_id: str) -> None:
        self.request("POST", f"/containers/{container_id}/start", expected=(204, 304))

    def rename(self, container_id: str, name: str) -> None:
        self.request("POST", f"/containers/{container_id}/rename?name={urllib.parse.quote(name, safe='')}", expected=(204,))

    def create(self, name: str, payload: dict[str, Any]) -> str:
        result = self.request("POST", f"/containers/create?name={urllib.parse.quote(name, safe='')}", payload, expected=(201,))
        if not isinstance(result, dict) or not isinstance(result.get("Id"), str):
            fail("Docker 未返回新容器 ID")
        return result["Id"]

    def remove(self, container_id: str) -> None:
        self.request("DELETE", f"/containers/{container_id}?v=0&force=0", expected=(204,))


def env_value(config: dict[str, Any], key: str) -> str | None:
    for item in config.get("Env") or []:
        if isinstance(item, str) and item.startswith(key + "="):
            return item.split("=", 1)[1]
    return None


def validate_identity(
    api: DockerAPI,
    container_id: str,
    state_dir: str,
    node_id: str,
    platform_url: str,
    config: dict[str, Any],
) -> None:
    identity = api.file_json(container_id, state_dir.rstrip("/") + "/identity.json")
    binding = api.file_json(
        container_id, state_dir.rstrip("/") + "/installation.json", required=False,
    )
    assert identity is not None
    if set(identity) != {"version", "node_id", "agent_token"} or identity.get("version") != 1:
        fail("节点身份格式无效，拒绝升级")
    token = identity.get("agent_token")
    try:
        identity_node = str(uuid.UUID(identity.get("node_id")))
        prefix, separator, secret = token.partition(".") if isinstance(token, str) else ("", "", "")
        token_node = str(uuid.UUID(prefix))
    except (ValueError, TypeError, AttributeError):
        fail("节点身份格式无效，拒绝升级")
    if (
        identity_node != node_id
        or token_node != node_id
        or not separator
        or not secret
        or any(char.isspace() or ord(char) < 32 for char in token)
    ):
        fail("节点身份与 --node-id 不一致，拒绝升级")
    command = config.get("Cmd") or []
    entrypoint = config.get("Entrypoint") or []
    if entrypoint != ["python", "-m", "performance_node"]:
        fail("原容器不是受支持的 performance-node 启动程序，拒绝接管")
    if binding is not None:
        if set(binding) != {"version", "platform_url", "node_id", "ca_sha256"}:
            fail("节点安装绑定格式无效，拒绝升级")
        try:
            binding_node = str(uuid.UUID(binding.get("node_id")))
        except (ValueError, TypeError, AttributeError):
            fail("节点安装绑定格式无效，拒绝升级")
        if binding.get("version") != 1 or binding_node != node_id or binding.get("platform_url") != platform_url:
            fail("节点安装绑定与本次 node/platform 不一致，拒绝升级")
        if not isinstance(command, list) or not command or command[0] != "start":
            fail("带 installation.json 的节点启动命令不受支持，拒绝升级")
        options: dict[str, str] = {}
        index = 1
        while index < len(command):
            option = command[index]
            if option not in {"--server", "--node-id", "--token", "--ca-sha256"} or index + 1 >= len(command):
                fail("节点 start 启动参数无法安全解析，拒绝升级")
            if option in options:
                fail("节点 start 启动参数包含重复项，拒绝升级")
            options[option] = command[index + 1]
            index += 2
        try:
            command_node = str(uuid.UUID(options.get("--node-id")))
        except (ValueError, TypeError, AttributeError):
            fail("节点 start 启动参数与身份不一致，拒绝升级")
        if (
            command_node != node_id
            or normalized_platform(options.get("--server", "")) != platform_url
            or not options.get("--token")
            or any(char.isspace() or ord(char) < 32 for char in options.get("--token", ""))
            or options.get("--ca-sha256") != binding.get("ca_sha256")
        ):
            fail("节点 start 启动参数与安装绑定不一致，拒绝升级")
    elif command == ["run"]:
        # Legacy install-node.sh predates installation.json.  Its immutable
        # platform binding is the runtime environment plus the persisted node
        # identity; both are required rather than inferred from a label alone.
        configured_platform = env_value(config, "PERFORMANCE_PLATFORM_URL")
        if configured_platform is None or normalized_platform(configured_platform) != platform_url:
            fail("旧节点缺少可信的平台环境绑定，拒绝升级")
    else:
        fail("节点缺少 installation.json 安装绑定，拒绝自动接管")


def validate_idle(api: DockerAPI, container_id: str, state_dir: str) -> None:
    execution = api.file_json(container_id, state_dir.rstrip("/") + "/execution.json", required=False)
    if execution is None:
        return
    if execution.get("version") != 1 or "active" not in execution or "report" not in execution:
        fail("节点执行状态无法确认，拒绝升级")
    if execution.get("active") is not None:
        fail("节点仍有活动压测任务，请先在平台停止任务再升级")
    if execution.get("report") is not None:
        fail("节点仍有待平台确认的压测报告，请等待执行记录稳定后再升级")


def persistent_state_mount(details: dict[str, Any], state_dir: str) -> dict[str, Any]:
    mounts = [mount for mount in details.get("Mounts") or [] if mount.get("Destination") == state_dir]
    if len(mounts) != 1 or mounts[0].get("Type") not in {"volume", "bind"}:
        fail("节点没有唯一的持久化身份卷或目录挂载，拒绝升级")
    source = mounts[0].get("Source")
    if not isinstance(source, str) or not source:
        fail("身份挂载来源无效，拒绝升级")
    return mounts[0]


def validate_standalone(api: DockerAPI, details: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    config = details.get("Config") or {}
    labels = config.get("Labels") or {}
    if any(any(str(key).startswith(prefix) for prefix in MANAGED_LABEL_PREFIXES) for key in labels):
        fail("该容器由 Compose、Swarm 或 Unraid 管理，请使用原管理工具升级")
    host_config = details.get("HostConfig") or {}
    if host_config.get("AutoRemove") is True:
        fail("原容器启用了自动删除（--rm），停止后无法保留备份，拒绝自动升级")
    network_mode = str(host_config.get("NetworkMode") or "")
    if network_mode == "host" or network_mode.startswith("container:"):
        fail("host/container 网络模式不支持自动克隆，请使用原管理工具升级")
    if config.get("MacAddress"):
        fail("节点使用固定 MAC，无法安全自动升级，请使用原管理工具升级")
    networks = (details.get("NetworkSettings") or {}).get("Networks") or {}
    if len(networks) != 1:
        fail("节点使用零个或多个网络，无法安全自动升级")
    name, endpoint = next(iter(networks.items()))
    if not isinstance(endpoint, dict):
        fail("节点网络配置无效")
    ipam = endpoint.get("IPAMConfig") or {}
    if any(ipam.get(key) for key in ("IPv4Address", "IPv6Address", "LinkLocalIPs")):
        fail("节点使用静态 IP，无法安全自动升级，请使用原管理工具升级")
    network_id = endpoint.get("NetworkID")
    if not isinstance(network_id, str) or not network_id:
        fail("节点网络 ID 无效")
    network = api.network(network_id)
    if network.get("Driver") != "bridge" or network.get("Scope") not in {None, "local"}:
        fail("节点不是本机 bridge 网络，无法安全自动升级，请使用原管理工具升级")
    network_labels = network.get("Labels") or {}
    if any(str(key).startswith(MANAGED_LABEL_PREFIXES) for key in network_labels):
        fail("节点网络由 Compose、Swarm 或 Unraid 管理，请使用原管理工具升级")
    return name, endpoint


CONFIG_KEYS = (
    "Hostname", "Domainname", "User", "AttachStdin", "AttachStdout", "AttachStderr",
    "ExposedPorts", "Tty", "OpenStdin", "StdinOnce", "Env", "Cmd", "Healthcheck",
    "ArgsEscaped", "Volumes", "WorkingDir", "Entrypoint", "NetworkDisabled", "MacAddress",
    "OnBuild", "Labels", "StopSignal", "StopTimeout", "Shell",
)


def create_payload(
    details: dict[str, Any],
    image: str,
    image_labels: dict[str, str],
    network_name: str,
    endpoint: dict[str, Any],
) -> dict[str, Any]:
    config = details["Config"]
    payload = {key: config[key] for key in CONFIG_KEYS if key in config and config[key] is not None}
    # Preserve the original command verbatim. In particular, a start command may
    # contain an already-consumed one-time token which must never be logged.
    payload["Image"] = image
    labels = copy.deepcopy(payload.get("Labels") or {})
    for key in list(labels):
        if str(key).startswith("org.opencontainers.image."):
            labels.pop(key)
    labels.update({key: value for key, value in image_labels.items() if key.startswith("org.opencontainers.image.")})
    payload["Labels"] = labels
    host_config = copy.deepcopy(details["HostConfig"])
    actual_mounts = {
        mount.get("Destination"): mount
        for mount in details.get("Mounts") or []
        if isinstance(mount, dict) and mount.get("Destination")
    }
    configured_mounts = copy.deepcopy(host_config.get("Mounts") or [])
    configured_targets: set[str] = set()
    for mount in configured_mounts:
        if not isinstance(mount, dict):
            fail("原容器 HostConfig.Mounts 格式无效")
        target = mount.get("Target") or mount.get("Destination")
        if not isinstance(target, str) or not target:
            fail("原容器 HostConfig.Mounts 缺少目标路径")
        configured_targets.add(target)
        if mount.get("Type") in {"volume", "bind"} and not mount.get("Source"):
            actual = actual_mounts.get(target) or {}
            source = actual.get("Name") if mount.get("Type") == "volume" else actual.get("Source")
            if actual.get("Type") != mount.get("Type") or not isinstance(source, str) or not source:
                fail("原容器匿名挂载无法绑定到实际持久来源，拒绝升级")
            mount["Source"] = source
    normalized_binds: list[str] = []
    for item in host_config.get("Binds") or []:
        if isinstance(item, str):
            parts = item.rsplit(":", 2)
            if len(parts) == 1:
                target = parts[0]
                actual = actual_mounts.get(target) or {}
                source = actual.get("Name")
                if actual.get("Type") != "volume" or not isinstance(source, str) or not source:
                    fail("原容器匿名卷声明无法绑定到实际卷，拒绝升级")
                if target not in configured_targets:
                    configured_mounts.append({
                        "Type": "volume", "Source": source, "Target": target,
                        "ReadOnly": not bool(actual.get("RW")),
                    })
                    configured_targets.add(target)
                continue
            configured_targets.add(parts[-2] if len(parts) == 3 else parts[-1])
            normalized_binds.append(item)
        else:
            fail("原容器 HostConfig.Binds 格式无效")
    missing_mounts = []
    for mount in details.get("Mounts") or []:
        destination = mount.get("Destination")
        if mount.get("Type") not in {"volume", "bind"} or destination in configured_targets:
            continue
        source = mount.get("Name") if mount.get("Type") == "volume" else mount.get("Source")
        if not isinstance(source, str) or not source or not isinstance(destination, str) or not destination:
            fail("原容器挂载配置无法完整克隆")
        cloned: dict[str, Any] = {
            "Type": mount["Type"], "Source": source, "Target": destination,
            "ReadOnly": not bool(mount.get("RW")),
        }
        if mount.get("Type") == "bind" and mount.get("Propagation"):
            cloned["BindOptions"] = {"Propagation": mount["Propagation"]}
        missing_mounts.append(cloned)
    if missing_mounts:
        configured_mounts.extend(missing_mounts)
    if configured_mounts:
        host_config["Mounts"] = configured_mounts
    if host_config.get("Binds") is not None:
        host_config["Binds"] = normalized_binds or None
    # A newly-created replacement stays non-restarting until the five-second
    # startup proof completes.  The original policy is restored on success.
    host_config["RestartPolicy"] = {"Name": "no", "MaximumRetryCount": 0}
    payload["HostConfig"] = host_config
    network_config: dict[str, Any] = {}
    for key in ("IPAMConfig", "Links", "DriverOpts"):
        if endpoint.get(key) is not None:
            network_config[key] = endpoint[key]
    automatic_aliases = {
        str(details.get("Name") or "").lstrip("/"),
        str(details.get("Id") or ""),
        str(details.get("Id") or "")[:12],
    }
    aliases = [alias for alias in endpoint.get("Aliases") or [] if alias not in automatic_aliases]
    if aliases:
        network_config["Aliases"] = aliases
    payload["NetworkingConfig"] = {"EndpointsConfig": {network_name: network_config}}
    return payload


def find_container(api: DockerAPI, node_id: str, requested_name: str | None) -> dict[str, Any]:
    candidates: list[dict[str, Any]] = []
    for summary in api.containers():
        names = [str(name).lstrip("/") for name in summary.get("Names") or []]
        labels = summary.get("Labels") or {}
        if requested_name:
            if requested_name in names:
                candidates.append(summary)
            continue
        container_id = summary.get("Id")
        if not isinstance(container_id, str) or not container_id:
            continue
        details = api.inspect(container_id)
        config = details.get("Config") or {}
        state_dir = env_value(config, "PERFORMANCE_NODE_STATE_DIR") or DEFAULT_STATE_DIR
        has_state_mount = any(
            mount.get("Destination") == state_dir and mount.get("Type") in {"volume", "bind"}
            for mount in details.get("Mounts") or []
        )
        if not has_state_mount:
            continue
        identity = api.file_json(container_id, state_dir.rstrip("/") + "/identity.json", required=False)
        identity_node = None
        if isinstance(identity, dict):
            try:
                identity_node = str(uuid.UUID(identity.get("node_id")))
            except (ValueError, TypeError, AttributeError):
                identity_node = None
        is_retained_backup = any(
            name.endswith("-backup-" + container_id[:12])
            and not bool((details.get("State") or {}).get("Running"))
            and ((details.get("HostConfig") or {}).get("RestartPolicy") or {}).get("Name") == "no"
            for name in names
        )
        if is_retained_backup:
            continue
        if identity_node == node_id:
            candidates.append(summary)
        elif labels.get(NODE_LABEL) == node_id:
            fail("节点标签匹配，但持久身份缺失或不一致；不会接管该容器")
    if len(candidates) != 1:
        if requested_name:
            fail("未找到唯一的指定容器；不会自动选择或接管其他容器")
        fail("无法按节点 ID 唯一定位原容器，请通过 --container 指定容器名")
    return candidates[0]


def image_version(image: str) -> str:
    run_cli(["docker", "image", "pull", image], capture=False)
    # Query the package itself rather than trusting a mutable label.
    return run_cli([
        "docker", "run", "--rm", "--network", "none", "--entrypoint", "python", image,
        "-c", "import performance_node; print(performance_node.__version__)",
    ])


def image_details(image: str) -> tuple[str, dict[str, str]]:
    raw = run_cli(["docker", "image", "inspect", image])
    try:
        values = json.loads(raw)
        details = values[0]
        image_id = details["Id"]
        labels = (details.get("Config") or {}).get("Labels") or {}
    except (json.JSONDecodeError, IndexError, TypeError, KeyError):
        fail("无法读取固定镜像详情")
    if not isinstance(image_id, str) or not image_id.startswith("sha256:") or not isinstance(labels, dict):
        fail("固定镜像详情无效")
    if not all(isinstance(key, str) and isinstance(value, str) for key, value in labels.items()):
        fail("固定镜像标签无效")
    return image_id, labels


def rollback(
    api: DockerAPI,
    *,
    old_id: str,
    old_name: str,
    backup_name: str,
    old_was_running: bool,
    restart_policy: dict[str, Any],
    new_id: str | None,
    renamed: bool,
    restart_changed: bool,
) -> None:
    if new_id:
        try:
            state = api.inspect(new_id).get("State") or {}
            if state.get("Running"):
                api.stop(new_id, 5)
            state = api.inspect(new_id).get("State") or {}
            if state.get("Running"):
                fail("新版容器无法确认已停止")
            api.remove(new_id)
        except UpgradeError:
            fail(
                f"新版容器未能安全停止并清理；旧容器保留为 {backup_name} 且不会启动，"
                "以免两个 Agent 争用同一身份。请人工检查，切勿删除身份卷"
            )
    if renamed:
        try:
            api.rename(old_id, old_name)
        except UpgradeError:
            fail(
                f"旧容器仍安全停用并保留为 {backup_name}，但原名称已无法确认可用；"
                "不会恢复 restart policy 或启动旧容器，请人工检查同名冲突"
            )
    if restart_changed:
        try:
            api.update_restart(old_id, restart_policy)
        except UpgradeError:
            fail("旧容器保持停用，但 restart policy 自动恢复失败；请人工检查后再启动")
    if old_was_running:
        try:
            state = api.inspect(old_id).get("State") or {}
            if not state.get("Running"):
                api.start(old_id)
        except UpgradeError:
            fail("旧容器名称和 restart policy 已恢复，但运行状态自动恢复失败；请人工检查")


def upgrade(args: argparse.Namespace, api: DockerAPI | None = None) -> None:
    node_id = normalize_uuid(args.node_id)
    platform_url = normalized_platform(args.platform)
    if not IMAGE_PATTERN.fullmatch(args.image):
        fail("--image 必须是 docker.io 固定 sha256 摘要引用")
    if not VERSION_PATTERN.fullmatch(args.agent_version):
        fail("--agent-version 格式无效")
    if args.container and (not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", args.container)):
        fail("--container 名称无效")
    verify_host()
    descriptor = acquire_lock(node_id)

    try:
        engine = api or DockerAPI(docker_socket())
        summary = find_container(engine, node_id, args.container)
        old_id = summary.get("Id")
        if not isinstance(old_id, str) or not old_id:
            fail("原容器 ID 无效")
        details = engine.inspect(old_id)
        # Lock the exact ID after discovery; never continue if the name was replaced.
        if details.get("Id") != old_id:
            fail("原容器身份在检查期间发生变化，请重试")
        old_name = str(details.get("Name") or "").lstrip("/")
        if not old_name:
            fail("原容器名称无效")
        labels = (details.get("Config") or {}).get("Labels") or {}
        if labels.get(NODE_LABEL) and labels.get(NODE_LABEL) != node_id:
            fail("原容器节点标签不匹配，拒绝升级")
        if labels.get(PLATFORM_LABEL) and normalized_platform(labels.get(PLATFORM_LABEL)) != platform_url:
            fail("原容器平台标签不匹配，拒绝升级")
        network_name, endpoint = validate_standalone(engine, details)
        config = details.get("Config") or {}
        state_dir = env_value(config, "PERFORMANCE_NODE_STATE_DIR") or DEFAULT_STATE_DIR
        persistent_state_mount(details, state_dir)
        validate_identity(engine, old_id, state_dir, node_id, platform_url, config)

        log("已核对原容器和持久身份，正在拉取并校验目标镜像…")
        version = image_version(args.image)
        if version != args.agent_version:
            fail("固定镜像内的实际 Agent 版本与页面要求不一致；原容器尚未停止")
        target_image_id, target_image_labels = image_details(args.image)
        if details.get("Image") == target_image_id:
            log(f"节点容器 {old_name} 已是 {args.agent_version}，无需升级")
            return
        validate_idle(engine, old_id, state_dir)

        all_names = {str(name).lstrip("/") for item in engine.containers() for name in item.get("Names") or []}
        backup_name = f"{old_name}-backup-{old_id[:12]}"
        if backup_name in all_names:
            fail(f"备份容器名 {backup_name} 已存在；原容器尚未停止，请人工确认")
        old_state = details.get("State") or {}
        old_status = old_state.get("Status")
        if old_status not in {"running", "exited", "created"}:
            fail(f"原容器状态为 {old_status}，无法安全自动升级")
        old_was_running = bool(old_state.get("Running"))
        restart_policy = (details.get("HostConfig") or {}).get("RestartPolicy") or {"Name": "no", "MaximumRetryCount": 0}
        payload = create_payload(details, args.image, target_image_labels, network_name, endpoint)

        log("镜像和容器配置校验通过，正在替换容器…")
        renamed = False
        restart_changed = False
        new_id: str | None = None
        try:
            # Disable the retained backup first so a host reboot cannot make two
            # containers compete for the same long-lived identity.
            engine.update_restart(old_id, {"Name": "no", "MaximumRetryCount": 0})
            restart_changed = True
            if old_was_running:
                engine.stop(old_id, int(config.get("StopTimeout") or 25))
            # Close the command-arrival race between the first idle check and
            # stopping the heartbeat process.  A newly accepted command/report
            # makes this attempt roll back instead of replacing the container.
            validate_idle(engine, old_id, state_dir)
            engine.rename(old_id, backup_name)
            renamed = True
            new_id = engine.create(old_name, payload)
            engine.start(new_id)
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                new_state = (engine.inspect(new_id).get("State") or {})
                if not new_state.get("Running"):
                    fail("新版容器启动后提前退出")
                time.sleep(0.25)
            if not (engine.inspect(new_id).get("State") or {}).get("Running"):
                fail("新版容器未能持续运行")
            engine.update_restart(new_id, restart_policy)
        except BaseException:
            rollback(
                engine, old_id=old_id, old_name=old_name, backup_name=backup_name,
                old_was_running=old_was_running, restart_policy=restart_policy,
                new_id=new_id, renamed=renamed, restart_changed=restart_changed,
            )
            raise

        log(f"节点容器 {old_name} 已使用 {args.agent_version} 镜像启动")
        log(f"旧容器已保留为 {backup_name}，restart policy 已设为 no；不会自动删除或删除卷")
        if labels.get(OWNER_LABEL) == INSTALLER_OWNER:
            log("该节点已脱离旧 install-node.sh 的固定配置记录，后续请继续使用平台提供的升级命令")
        log("容器已启动；请回到平台页面核对新版 heartbeat 后再执行压测")
    finally:
        os.close(descriptor)


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="安全升级一个现有 performance-node Docker 容器")
    result.add_argument("--node-id", required=True)
    result.add_argument("--platform", required=True)
    result.add_argument("--image", required=True)
    result.add_argument("--agent-version", required=True)
    result.add_argument("--container")
    return result


def main() -> int:
    try:
        upgrade(parser().parse_args())
        return 0
    except UpgradeError as exc:
        print(f"[性能节点升级] 错误：{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
