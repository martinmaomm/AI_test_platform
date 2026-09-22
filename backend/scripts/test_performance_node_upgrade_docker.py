#!/usr/bin/env python3
"""Disposable real-Docker smoke test for upgrade-node.py.

It creates only UUID-named local fixtures, never contacts a platform, and
removes resources only after verifying this test's ownership label.
"""

from __future__ import annotations

import argparse
import contextlib
import importlib.util
import io
import json
import subprocess
import tempfile
import uuid
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "deploy" / "performance" / "upgrade-node.py"
SPEC = importlib.util.spec_from_file_location("performance_node_upgrader_smoke", SCRIPT)
assert SPEC and SPEC.loader
upgrader = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(upgrader)

SMOKE_LABEL = "com.automation-platform.upgrade-smoke"


def docker(*arguments: str, check: bool = True) -> str:
    result = subprocess.run(
        ["docker", *arguments], check=check, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    return result.stdout.strip()


def owned(kind: str, name: str, run_id: str) -> bool:
    if kind == "container":
        value = docker("container", "inspect", "--format", "{{index .Config.Labels \"" + SMOKE_LABEL + "\"}}", name, check=False)
    else:
        value = docker("volume", "inspect", "--format", "{{index .Labels \"" + SMOKE_LABEL + "\"}}", name, check=False)
    return value == run_id


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--target-image",
        default="docker.io/martinmao9/performance-node@sha256:6561c059c5d15c8e52bc44ad82f718b21671fc4a1b735c8ed9b23c1d518c36ce",
    )
    parser.add_argument("--agent-version", default="0.4.0")
    parser.add_argument("--source-mode", choices=("run", "start"), default="run")
    parser.add_argument("--inject-start-failure", action="store_true")
    args = parser.parse_args()

    architecture = docker("info", "--format", "{{.Architecture}}")
    suffix = "arm64" if architecture in {"arm64", "aarch64"} else "amd64"
    old_image = f"automation-platform-performance-node:0.3.2-{suffix}"
    docker("image", "inspect", old_image)
    docker("image", "inspect", args.target_image)

    run_id = uuid.uuid4().hex
    node_id = str(uuid.uuid4())
    container = f"performance-node-upgrade-smoke-{run_id[:12]}"
    volume = container + "-identity"
    platform_url = "https://127.0.0.1:9"
    backup = ""
    docker("volume", "create", "--label", f"{SMOKE_LABEL}={run_id}", volume)
    try:
        state = {
            "identity.json": {
                "version": 1, "node_id": node_id, "agent_token": node_id + ".synthetic-smoke-secret",
            },
            "execution.json": {"version": 1, "active": None, "report": None, "completed_runs": {}},
        }
        if args.source_mode == "start":
            state["installation.json"] = {
                "version": 1, "platform_url": platform_url, "node_id": node_id, "ca_sha256": None,
            }
        writer = (
            "import json,os,pathlib; p=pathlib.Path('/state'); p.mkdir(exist_ok=True); "
            "data=json.loads(os.environ['SMOKE_STATE']); "
            "[(p.joinpath(k).write_text(json.dumps(v),encoding='utf-8'),"
            "os.chmod(p/k,0o600),os.chown(p/k,10001,10001)) for k,v in data.items()]; "
            "os.chown(p,10001,10001); os.chmod(p,0o700)"
        )
        docker(
            "run", "--rm", "--user", "0", "--network", "none",
            "-e", "SMOKE_STATE=" + json.dumps(state, separators=(",", ":")),
            "-v", f"{volume}:/state", "--entrypoint", "python", args.target_image, "-c", writer,
        )
        command = ["run"] if args.source_mode == "run" else [
            "start", "--server", platform_url, "--node-id", node_id,
            "--token", node_id + ".already-consumed-start-token",
        ]
        docker(
            "create", "--name", container, "--network", "bridge", "--restart", "unless-stopped",
            "--read-only", "--init", "--tmpfs", "/tmp:rw,noexec,nosuid,size=16m",
            "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
            "--cpus", "0.5", "--memory", "128m",
            "--label", f"{SMOKE_LABEL}={run_id}",
            "--label", f"{upgrader.NODE_LABEL}={node_id}",
            "--label", f"{upgrader.PLATFORM_LABEL}={platform_url}",
            "-e", f"PERFORMANCE_PLATFORM_URL={platform_url}",
            "-e", "PERFORMANCE_NODE_STATE_DIR=/var/lib/performance-node",
            "-v", f"{volume}:/var/lib/performance-node", old_image, *command,
        )
        docker("start", container)

        with tempfile.TemporaryDirectory(prefix="performance-node-upgrade-lock-") as lock_dir:
            upgrader.LOCK_DIR = Path(lock_dir)
            upgrader.verify_host = lambda: None
            invocation = argparse.Namespace(
                node_id=node_id, platform=platform_url, image=args.target_image,
                agent_version=args.agent_version, container=container,
            )
            output = io.StringIO()
            with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
                if args.inject_start_failure:
                    api = upgrader.DockerAPI(upgrader.docker_socket())
                    old_id = json.loads(docker("container", "inspect", container))[0]["Id"]
                    real_start = api.start

                    def start_with_failure(container_id):
                        if container_id != old_id:
                            raise upgrader.UpgradeError("synthetic new-container start failure")
                        real_start(container_id)

                    api.start = start_with_failure
                    try:
                        upgrader.upgrade(invocation, api)
                    except upgrader.UpgradeError as exc:
                        assert "synthetic new-container start failure" in str(exc)
                    else:
                        raise AssertionError("injected start failure did not fail")
                else:
                    upgrader.upgrade(invocation)
                    upgrader.upgrade(invocation)  # exact image is an idempotent no-op
            transcript = output.getvalue()

        current = json.loads(docker("container", "inspect", container))[0]
        if args.inject_start_failure:
            assert current["State"]["Running"] is True
            assert current["HostConfig"]["RestartPolicy"]["Name"] == "unless-stopped"
            assert current["Image"] == docker("image", "inspect", "--format", "{{.Id}}", old_image)
            names = docker(
                "container", "ls", "-a", "--filter", f"label={SMOKE_LABEL}={run_id}",
                "--format", "{{.Names}}",
            ).splitlines()
            assert names == [container]
            assert "already-consumed-start-token" not in transcript
            print("真实 Docker 回滚 smoke 通过：新版启动失败后仅清理新容器并恢复原节点")
            return 0
        names = docker(
            "container", "ls", "-a", "--filter", f"label={SMOKE_LABEL}={run_id}",
            "--format", "{{.Names}}",
        ).splitlines()
        backups = [name for name in names if name != container]
        assert len(backups) == 1
        backup = backups[0]
        mounts = [mount for mount in current["Mounts"] if mount["Destination"] == "/var/lib/performance-node"]
        assert len(mounts) == 1 and mounts[0].get("Name") == volume
        assert current["State"]["Running"] is True
        assert current["HostConfig"]["RestartPolicy"]["Name"] == "unless-stopped"
        assert current["HostConfig"]["ReadonlyRootfs"] is True
        assert current["HostConfig"]["CapDrop"] == ["ALL"]
        assert current["HostConfig"]["NanoCpus"] == 500000000
        assert current["HostConfig"]["Memory"] == 134217728
        assert current["HostConfig"]["SecurityOpt"] == ["no-new-privileges"]
        assert "/tmp" in current["HostConfig"]["Tmpfs"]
        assert current["Config"]["Labels"]["org.opencontainers.image.version"] == args.agent_version
        assert "synthetic-smoke-secret" not in transcript
        assert "already-consumed-start-token" not in transcript
        assert "无需升级" in transcript

        old = json.loads(docker("container", "inspect", backup))[0]
        assert old["State"]["Running"] is False
        assert old["HostConfig"]["RestartPolicy"]["Name"] == "no"
        print(
            f"真实 Docker {args.source_mode} 升级 smoke 通过："
            "身份卷保留、旧容器停用备份、新容器启动、重复运行 no-op"
        )
        return 0
    finally:
        for name in (container, backup):
            if name and owned("container", name, run_id):
                docker("container", "rm", "-f", name)
        if owned("volume", volume, run_id):
            docker("volume", "rm", volume)


if __name__ == "__main__":
    raise SystemExit(main())
