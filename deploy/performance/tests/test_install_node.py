from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
import uuid
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
INSTALLER = REPO_ROOT / "deploy" / "performance" / "install-node.sh"
IMAGE_REF = "automation-platform-performance-node:0.2.0-amd64"
IMAGE_ID = "sha256:e8d384d6bb0c3a7fc0a9585e3c5dac926bc27cd65550267906e1de43d5a2e1c6"
IMAGE_CONFIG_ID = "sha256:" + "c1" * 32
REGISTRY_REF = "docker.io/automationplatform/performance-node@sha256:" + "b7" * 32
REGISTRY_DIGEST = REGISTRY_REF.rsplit("@", 1)[1]
PLATFORM = "https://load.example.test:18443"
ARCHIVE_URL = PLATFORM + "/downloads/performance-node-amd64.tar.gz"


FAKE_COMMAND = r'''
import hashlib
import json
import os
import pathlib
import re
import shutil
import stat
import sys

name = pathlib.Path(sys.argv[0]).name
args = sys.argv[1:]
state_path = pathlib.Path(os.environ["FAKE_STATE"])

def load_state():
    if state_path.exists():
        return json.loads(state_path.read_text())
    return {"networks": {}, "volumes": {}, "containers": {}, "image_loaded": False,
            "registry_image_loaded": False,
            "identity": "missing", "enroll_calls": 0, "commands": []}

def save_state(value):
    state_path.write_text(json.dumps(value, sort_keys=True))

def fail(message=""):
    if message:
        print(message, file=sys.stderr)
    raise SystemExit(1)

if name == "id":
    print("0")
elif name == "uname":
    print("Linux" if args == ["-s"] else "x86_64")
elif name == "flock":
    raise SystemExit(0)
elif name == "chmod":
    values = [value for value in args if value != "--"]
    pathlib.Path(values[-1]).chmod(int(values[-2], 8))
elif name == "stat":
    try:
        index = args.index("-c")
        fmt = args[index + 1]
        path = pathlib.Path(args[-1])
        details = path.stat()
        if fmt == "%a":
            print(format(stat.S_IMODE(details.st_mode), "o"))
        elif fmt == "%u":
            print("0")
        elif fmt == "%s":
            print(details.st_size)
        elif fmt == "%d:%i":
            print(f"{details.st_dev}:{details.st_ino}")
        else:
            fail("unsupported stat format")
    except Exception as exc:
        fail(str(exc))
elif name == "install":
    source = pathlib.Path(args[-2])
    destination = pathlib.Path(args[-1])
    shutil.copyfile(source, destination)
    destination.chmod(0o600)
elif name == "sha256sum":
    if args:
        path = pathlib.Path(args[-1])
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        print(f"{digest}  {path}")
    else:
        digest = hashlib.sha256(sys.stdin.buffer.read()).hexdigest()
        print(f"{digest}  -")
elif name == "curl":
    state = load_state()
    state.setdefault("curl_commands", []).append(args)
    save_state(state)
    mode = os.environ.get("FAKE_CURL_MODE", "success")
    output = pathlib.Path(args[args.index("--output") + 1])
    archive = pathlib.Path(os.environ["FAKE_ARCHIVE"]).read_bytes()
    current = output.read_bytes() if output.exists() else b""
    if mode == "offline":
        if not current:
            output.write_bytes(archive[: max(1, len(archive) // 2)])
        print("curl: network unavailable", file=sys.stderr)
        raise SystemExit(7)
    if mode == "bad_hash":
        output.write_bytes(b"X" * len(archive))
    else:
        output.write_bytes(current + archive[len(current):])
elif name == "docker":
    state = load_state()
    state["commands"].append(args)
    save_state(state)

    def labels_from(values):
        labels = {}
        for position, value in enumerate(values):
            if value == "--label":
                key, item = values[position + 1].split("=", 1)
                labels[key] = item
        return labels

    def inspect(resource_type):
        collection = state[{"network": "networks", "volume": "volumes",
                            "container": "containers"}[resource_type]]
        target = args[-1]
        if target not in collection:
            raise SystemExit(1)
        resource = collection[target]
        if "--format" not in args:
            print("[]")
            return
        template = args[args.index("--format") + 1]
        if resource_type == "container":
            label_match = re.search(r'index \.Config\.Labels "([^"]+)"', template)
            if re.search(r'index \.Labels ', template):
                fail("container labels exist only under .Config.Labels")
        else:
            label_match = re.search(r'index \.Labels "([^"]+)"', template)
            if re.search(r'index \.Config\.Labels ', template):
                fail("network and volume labels exist under .Labels")
        if label_match:
            print(resource.get("labels", {}).get(label_match.group(1), "<no value>"))
        elif template == "{{.Config.Image}}":
            print(resource["image_ref"])
        elif template == "{{.Image}}":
            print(resource["image_id"])
        elif template == "{{.State.Status}}":
            print(resource["status"])
        else:
            fail("unsupported inspect template")

    if args[:2] == ["context", "show"]:
        if os.environ.get("FAKE_CONTEXT_SHOW_UNSUPPORTED") == "1":
            fail("docker context: 'show' is not a docker command")
        print("default")
    elif args[:2] == ["context", "inspect"]:
        template = args[args.index("--format") + 1]
        current_context = os.environ.get("DOCKER_CONTEXT") or os.environ.get("FAKE_CURRENT_CONTEXT", "default")
        if template == "{{.Name}}":
            if os.environ.get("FAKE_CONTEXT_INSPECT_FAILURE") == "name":
                fail("cannot inspect current context")
            print(current_context)
        else:
            if os.environ.get("FAKE_CONTEXT_INSPECT_FAILURE") == "endpoint":
                fail("cannot inspect context endpoint")
            if args[2] != current_context:
                fail("installer inspected a different context")
            print(os.environ.get("FAKE_DOCKER_ENDPOINT", "unix:///var/run/docker.sock"))
    elif args[0] == "info":
        if os.environ.get("FAKE_DOCKER_UNAVAILABLE") == "1":
            raise SystemExit(1)
        print("linux/amd64")
    elif args[:2] == ["network", "inspect"]:
        inspect("network")
    elif args[:2] == ["volume", "inspect"]:
        inspect("volume")
    elif args[:2] == ["container", "inspect"]:
        inspect("container")
    elif args[:2] == ["network", "create"]:
        state["networks"][args[-1]] = {"labels": labels_from(args)}
        save_state(state)
        print(args[-1])
    elif args[:2] == ["volume", "create"]:
        state["volumes"][args[-1]] = {"labels": labels_from(args)}
        save_state(state)
        print(args[-1])
    elif args[:2] == ["image", "inspect"]:
        reference = args[-1]
        is_registry = reference == os.environ["FAKE_REGISTRY_REF"]
        available = (
            state.get("registry_image_loaded") or os.environ.get("FAKE_PRELOAD_REGISTRY_IMAGE") == "1"
        ) if is_registry else (
            state.get("image_loaded") or os.environ.get("FAKE_PRELOAD_IMAGE") == "1"
        )
        if not available:
            raise SystemExit(1)
        if "--format" not in args:
            print("[]")
        else:
            template = args[args.index("--format") + 1]
            if template == "{{.Id}}":
                if is_registry:
                    print(os.environ.get("FAKE_REGISTRY_IMAGE_ID", os.environ["FAKE_IMAGE_CONFIG_ID"]))
                else:
                    print(state.get("tagged_image_id", os.environ["FAKE_IMAGE_ID"]))
            elif template == "{{.Os}}/{{.Architecture}}":
                if is_registry:
                    print(os.environ.get("FAKE_REGISTRY_IMAGE_PLATFORM", "linux/amd64"))
                else:
                    print(os.environ.get("FAKE_IMAGE_PLATFORM", "linux/amd64"))
            else:
                fail("unsupported image inspect template")
    elif args[0] == "pull":
        if os.environ.get("FAKE_DOCKER_PULL_FAIL") == "1":
            print("pull failed", file=sys.stderr)
            raise SystemExit(1)
        if args != ["pull", "--platform", "linux/amd64", os.environ["FAKE_REGISTRY_REF"]]:
            fail("unexpected docker pull arguments: " + repr(args))
        state["registry_image_loaded"] = True
        save_state(state)
        print("pulled fixed digest")
    elif args[:2] == ["image", "tag"]:
        if args[2:] != [os.environ["FAKE_REGISTRY_REF"], os.environ["FAKE_IMAGE_REF"]]:
            fail("unexpected docker image tag arguments: " + repr(args))
        state["image_loaded"] = True
        state["tagged_image_id"] = os.environ.get(
            "FAKE_REGISTRY_IMAGE_ID", os.environ["FAKE_IMAGE_CONFIG_ID"],
        )
        save_state(state)
    elif args[0] == "load":
        state["image_loaded"] = True
        save_state(state)
        print("Loaded image")
    elif args[0] == "run":
        container_name = args[args.index("--name") + 1]
        if container_name.endswith("-identity-check"):
            result = {"valid": 0, "missing": 3, "invalid": 4}[state["identity"]]
            raise SystemExit(result)
        if container_name.endswith("-enroll"):
            state["enroll_calls"] += 1
            behavior = os.environ.get("FAKE_ENROLL", "success")
            if behavior in {"success", "write_identity_fail"}:
                state["identity"] = "valid"
            save_state(state)
            if behavior == "success":
                print("节点注册成功，长期身份已安全保存")
                raise SystemExit(0)
            raise SystemExit(42)
        if os.environ.get("FAKE_RUNTIME_FAIL") == "1":
            raise SystemExit(42)
        state["containers"][container_name] = {
            "labels": labels_from(args), "image_ref": os.environ["FAKE_IMAGE_REF"],
            "image_id": state.get("tagged_image_id", os.environ["FAKE_IMAGE_ID"]),
            "status": "running",
        }
        save_state(state)
        print("container-id")
    elif args[0] == "start":
        state["containers"][args[-1]]["status"] = "running"
        save_state(state)
        print(args[-1])
    else:
        fail("unsupported docker command: " + repr(args))
else:
    fail("unsupported fake command: " + name)
'''


class InstallerHarness:
    def __init__(self, root: Path, *, with_docker: bool = True):
        self.root = root
        self.bin_dir = root / "bin"
        self.bin_dir.mkdir()
        self.state_path = root / "docker-state.json"
        self.archive = root / "image.tar.gz"
        self.archive.write_bytes((b"fixed-image-archive\n" * 64) + b"end")
        self.archive_sha = hashlib.sha256(self.archive.read_bytes()).hexdigest()
        self.archive_size = self.archive.stat().st_size
        dispatcher = self.bin_dir / "fake-command"
        dispatcher.write_text(f"#!{sys.executable}\n" + FAKE_COMMAND)
        dispatcher.chmod(0o755)
        commands = ["id", "uname", "flock", "chmod", "stat", "install", "sha256sum", "curl"]
        if with_docker:
            commands.append("docker")
        for command in commands:
            (self.bin_dir / command).symlink_to(dispatcher.name)

        source = INSTALLER.read_text()
        source = source.replace(
            'readonly BASE_DIR="/opt/automation-performance-nodes"',
            f'readonly BASE_DIR="{root / "nodes"}"',
        ).replace(
            'readonly LOCK_DIR="/run/lock/automation-performance-nodes"',
            f'readonly LOCK_DIR="{root / "locks"}"',
        )
        self.installer = root / "install-node.sh"
        self.installer.write_text(source)
        self.installer.chmod(0o755)
        self.node_id = str(uuid.uuid4())
        self.token = root / "token"
        self.ca = root / "ca.crt"
        self.write_token("TOP-SECRET-ENROLLMENT-TOKEN")
        self.ca.write_text("test-ca-public-certificate")
        self.ca.chmod(0o600)

    def write_token(self, value: str = "TOP-SECRET-ENROLLMENT-TOKEN") -> None:
        self.token.write_text(value)
        self.token.chmod(0o600)

    @property
    def env(self) -> dict[str, str]:
        return {
            **os.environ,
            "PATH": f"{self.bin_dir}:/usr/bin:/bin",
            "DOCKER_CONTEXT": "",
            "DOCKER_HOST": "",
            "FAKE_STATE": str(self.state_path),
            "FAKE_ARCHIVE": str(self.archive),
            "FAKE_IMAGE_REF": IMAGE_REF,
            "FAKE_IMAGE_ID": IMAGE_ID,
            "FAKE_IMAGE_CONFIG_ID": IMAGE_CONFIG_ID,
            "FAKE_REGISTRY_REF": REGISTRY_REF,
        }

    def command(self, *, archive_sha: str | None = None,
                registry_ref: str | None = None) -> list[str]:
        command = [
            "/bin/bash", str(self.installer),
            "--platform", PLATFORM,
            "--node-id", self.node_id,
            "--token-file", str(self.token),
            "--ca-file", str(self.ca),
            "--image-ref", IMAGE_REF,
            "--image-id", IMAGE_ID,
            "--image-config-id", IMAGE_CONFIG_ID,
            "--archive-url", ARCHIVE_URL,
            "--archive-sha256", archive_sha or self.archive_sha,
            "--archive-size", str(self.archive_size),
        ]
        if registry_ref is not None:
            command.extend(("--registry-ref", registry_ref))
        return command

    def run(self, *, extra_env: dict[str, str] | None = None,
            archive_sha: str | None = None,
            registry_ref: str | None = None) -> subprocess.CompletedProcess[str]:
        environment = self.env
        if extra_env:
            environment.update(extra_env)
        return subprocess.run(
            self.command(archive_sha=archive_sha, registry_ref=registry_ref), env=environment,
            text=True, capture_output=True, timeout=20,
        )

    def state(self) -> dict:
        return json.loads(self.state_path.read_text())

    @property
    def prefix(self) -> str:
        return f"automation-performance-node-{self.node_id}"


class InstallNodeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def harness(self, *, with_docker: bool = True) -> InstallerHarness:
        return InstallerHarness(self.root, with_docker=with_docker)

    def test_new_install_accepts_index_id_and_uses_isolated_resources(self) -> None:
        harness = self.harness()
        secret = harness.token.read_text()
        completed = harness.run()

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertFalse(harness.token.exists())
        self.assertNotIn(secret, completed.stdout + completed.stderr)
        state = harness.state()
        self.assertEqual(state["identity"], "valid")
        self.assertEqual(state["enroll_calls"], 1)
        self.assertIn(harness.prefix + "-network", state["networks"])
        self.assertIn(harness.prefix + "-identity", state["volumes"])
        self.assertEqual(state["containers"][harness.prefix]["status"], "running")
        curl_command = state["curl_commands"][0]
        self.assertEqual(curl_command[curl_command.index("--speed-limit") + 1], "1024")
        self.assertEqual(curl_command[curl_command.index("--speed-time") + 1], "120")
        flattened = "\n".join(" ".join(command) for command in state["commands"])
        self.assertNotIn(secret, flattened)
        self.assertNotIn("docker.sock", flattened)
        self.assertNotRegex(flattened, r"(^| )-p( |$)|--publish")
        runtime = state["containers"][harness.prefix]
        self.assertEqual(runtime["labels"]["com.automation-platform.node-id"], harness.node_id)
        fixed_config = (self.root / "nodes" / harness.node_id / "installation.conf").read_text()
        self.assertIn(f"image_id={IMAGE_ID}\n", fixed_config)
        self.assertIn(f"image_config_id={IMAGE_CONFIG_ID}\n", fixed_config)
        self.assertIn("容器已启动不等于平台在线", completed.stdout)

    def test_config_id_is_accepted_for_loaded_image_and_runtime_across_rerun(self) -> None:
        harness = self.harness()
        first = harness.run(extra_env={"FAKE_IMAGE_ID": IMAGE_CONFIG_ID})

        self.assertEqual(first.returncode, 0, first.stderr)
        state = harness.state()
        self.assertEqual(state["containers"][harness.prefix]["image_id"], IMAGE_CONFIG_ID)
        self.assertEqual(state["enroll_calls"], 1)

        harness.write_token("CROSS-VERSION-UNUSED-TOKEN")
        second = harness.run(extra_env={"FAKE_IMAGE_ID": IMAGE_ID})
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual(harness.state()["enroll_calls"], 1)
        self.assertIn("已经在运行", second.stdout)

    def test_unpinned_image_id_is_rejected(self) -> None:
        harness = self.harness()
        other_id = "sha256:" + "f3" * 32
        completed = harness.run(extra_env={
            "FAKE_PRELOAD_IMAGE": "1",
            "FAKE_IMAGE_ID": other_id,
        })

        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("与固定 index ID", completed.stderr)
        self.assertIn("config ID", completed.stderr)
        self.assertEqual(harness.state()["enroll_calls"], 0)
        self.assertTrue(harness.token.exists())

    def test_image_config_id_is_a_required_cli_argument(self) -> None:
        harness = self.harness()
        command = harness.command()
        position = command.index("--image-config-id")
        del command[position:position + 2]
        completed = subprocess.run(
            command, env=harness.env, text=True, capture_output=True, timeout=20,
        )

        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("缺少必需参数", completed.stderr)
        self.assertIn("--image-config-id sha256:ID", completed.stderr)

    def test_rerun_reuses_verified_archive_and_skips_enrollment(self) -> None:
        harness = self.harness()
        first = harness.run()
        self.assertEqual(first.returncode, 0, first.stderr)
        harness.write_token("SECOND-UNUSED-TOKEN")
        second = harness.run(extra_env={"FAKE_CURL_MODE": "offline"})

        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual(harness.state()["enroll_calls"], 1)
        self.assertFalse(harness.token.exists())
        self.assertIn("跳过归档下载与 docker load", second.stdout)
        self.assertIn("跳过 enroll", second.stdout)
        self.assertIn("已经在运行", second.stdout)

    def test_preloaded_exact_local_image_skips_archive_download(self) -> None:
        harness = self.harness()
        completed = harness.run(extra_env={
            "FAKE_PRELOAD_IMAGE": "1",
            "FAKE_CURL_MODE": "offline",
        })

        self.assertEqual(completed.returncode, 0, completed.stderr)
        state = harness.state()
        self.assertNotIn("curl_commands", state)
        self.assertFalse(any(command and command[0] == "load" for command in state["commands"]))
        self.assertIn("跳过归档下载与 docker load", completed.stdout)
        archive = self.root / "nodes" / harness.node_id / f"image-{harness.archive_sha}.tar.gz"
        self.assertFalse(archive.exists())

    def test_classic_registry_install_accepts_config_id_distinct_from_manifest_digest(self) -> None:
        harness = self.harness()
        completed = harness.run(
            registry_ref=REGISTRY_REF,
            extra_env={"FAKE_CURL_MODE": "offline"},
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        state = harness.state()
        self.assertEqual(state["enroll_calls"], 1)
        self.assertFalse(harness.token.exists())
        self.assertNotIn("curl_commands", state)
        self.assertIn(
            ["pull", "--platform", "linux/amd64", REGISTRY_REF],
            state["commands"],
        )
        self.assertIn(["image", "tag", REGISTRY_REF, IMAGE_REF], state["commands"])
        self.assertNotEqual(IMAGE_CONFIG_ID, REGISTRY_DIGEST)
        self.assertEqual(state["tagged_image_id"], IMAGE_CONFIG_ID)
        fixed_config = (self.root / "nodes" / harness.node_id / "installation.conf").read_text()
        self.assertNotIn("registry_ref", fixed_config)
        self.assertIn(f"archive_url={ARCHIVE_URL}\n", fixed_config)

    def test_containerd_registry_install_accepts_child_digest_distinct_from_index_id(self) -> None:
        harness = self.harness()
        self.assertNotEqual(IMAGE_ID, REGISTRY_DIGEST)
        completed = harness.run(
            registry_ref=REGISTRY_REF,
            extra_env={"FAKE_REGISTRY_IMAGE_ID": REGISTRY_DIGEST},
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        state = harness.state()
        self.assertEqual(state["enroll_calls"], 1)
        self.assertEqual(state["tagged_image_id"], REGISTRY_DIGEST)
        self.assertEqual(state["containers"][harness.prefix]["image_id"], REGISTRY_DIGEST)

    def test_registry_pull_failure_does_not_fall_back_or_enroll(self) -> None:
        harness = self.harness()
        completed = harness.run(
            registry_ref=REGISTRY_REF,
            extra_env={
                "FAKE_DOCKER_PULL_FAIL": "1",
                "FAKE_CURL_MODE": "offline",
            },
        )

        self.assertNotEqual(completed.returncode, 0)
        state = harness.state()
        self.assertEqual(state["enroll_calls"], 0)
        self.assertFalse(state["image_loaded"])
        self.assertNotIn("curl_commands", state)
        self.assertFalse(any(command and command[0] == "load" for command in state["commands"]))
        self.assertTrue(harness.token.exists())
        self.assertIn("未回退平台归档", completed.stderr)

    def test_registry_image_with_wrong_id_or_platform_never_enrolls_or_tags(self) -> None:
        cases = (
            ({"FAKE_REGISTRY_IMAGE_ID": "sha256:" + "f4" * 32}, "固定 index ID"),
            ({"FAKE_REGISTRY_IMAGE_PLATFORM": "linux/arm64"}, "架构"),
        )
        for environment, expected_message in cases:
            with self.subTest(environment=environment), tempfile.TemporaryDirectory() as folder:
                harness = InstallerHarness(Path(folder))
                completed = harness.run(registry_ref=REGISTRY_REF, extra_env=environment)

                self.assertNotEqual(completed.returncode, 0)
                state = harness.state()
                self.assertEqual(state["enroll_calls"], 0)
                self.assertFalse(state["image_loaded"])
                self.assertFalse(any(command[:2] == ["image", "tag"] for command in state["commands"]))
                self.assertTrue(harness.token.exists())
                self.assertIn(expected_message, completed.stderr)

    def test_registry_never_overwrites_an_existing_wrong_local_tag(self) -> None:
        harness = self.harness()
        wrong_id = "sha256:" + "f5" * 32
        completed = harness.run(
            registry_ref=REGISTRY_REF,
            extra_env={"FAKE_PRELOAD_IMAGE": "1", "FAKE_IMAGE_ID": wrong_id},
        )

        self.assertNotEqual(completed.returncode, 0)
        state = harness.state()
        self.assertEqual(state["enroll_calls"], 0)
        self.assertFalse(any(command and command[0] == "pull" for command in state["commands"]))
        self.assertFalse(any(command[:2] == ["image", "tag"] for command in state["commands"]))
        self.assertIn("不会覆盖或升级", completed.stderr)

    def test_registry_rerun_reuses_local_tag_and_does_not_register_twice(self) -> None:
        harness = self.harness()
        first = harness.run(registry_ref=REGISTRY_REF)
        self.assertEqual(first.returncode, 0, first.stderr)
        first_pull_count = sum(
            command and command[0] == "pull" for command in harness.state()["commands"]
        )
        harness.write_token("SECOND-UNUSED-TOKEN")

        second = harness.run(
            registry_ref=REGISTRY_REF,
            extra_env={"FAKE_DOCKER_PULL_FAIL": "1", "FAKE_CURL_MODE": "offline"},
        )
        self.assertEqual(second.returncode, 0, second.stderr)
        state = harness.state()
        self.assertEqual(state["enroll_calls"], 1)
        self.assertEqual(
            sum(command and command[0] == "pull" for command in state["commands"]),
            first_pull_count,
        )
        self.assertFalse(harness.token.exists())
        self.assertIn("跳过 enroll", second.stdout)

    def test_existing_archive_install_can_resume_with_registry_transport(self) -> None:
        harness = self.harness()
        first = harness.run()
        self.assertEqual(first.returncode, 0, first.stderr)
        original_config = (
            self.root / "nodes" / harness.node_id / "installation.conf"
        ).read_text()
        harness.write_token("TRANSPORT-CHANGE-MUST-NOT-BE-CONSUMED")

        second = harness.run(
            registry_ref=REGISTRY_REF,
            extra_env={"FAKE_DOCKER_PULL_FAIL": "1", "FAKE_CURL_MODE": "offline"},
        )
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual(harness.state()["enroll_calls"], 1)
        self.assertEqual(
            (self.root / "nodes" / harness.node_id / "installation.conf").read_text(),
            original_config,
        )

    def test_registry_ref_rejects_unpinned_digest_and_external_host(self) -> None:
        invalid_references = (
            "docker.io/automationplatform/performance-node:latest",
            "ghcr.io/automationplatform/performance-node@" + IMAGE_ID,
            "docker.io/automationplatform/performance-node@sha256:" + "A" * 64,
            "docker.io/automationplatform/performance-node@sha256:" + "e8" * 31,
        )
        for registry_ref in invalid_references:
            with self.subTest(registry_ref=registry_ref), tempfile.TemporaryDirectory() as folder:
                harness = InstallerHarness(Path(folder))
                completed = harness.run(registry_ref=registry_ref)
                self.assertNotEqual(completed.returncode, 0)
                self.assertTrue(harness.token.exists())
                self.assertRegex(completed.stderr, r"--registry-ref|摘要")

    def test_stopped_owned_runtime_is_started_without_reenroll(self) -> None:
        harness = self.harness()
        self.assertEqual(harness.run().returncode, 0)
        state = harness.state()
        state["containers"][harness.prefix]["status"] = "exited"
        harness.state_path.write_text(json.dumps(state))
        harness.write_token("NEW-UNUSED-TOKEN")

        completed = harness.run()
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(harness.state()["enroll_calls"], 1)
        self.assertEqual(harness.state()["containers"][harness.prefix]["status"], "running")
        self.assertIn("正在安全启动", completed.stdout)

    def test_foreign_resource_conflict_is_not_modified(self) -> None:
        harness = self.harness()
        self.assertEqual(harness.run().returncode, 0)
        state = harness.state()
        network = state["networks"][harness.prefix + "-network"]
        network["labels"]["com.automation-platform.owner"] = "someone-else"
        untouched = {"labels": {"owner": "other-node"}}
        state["networks"]["unrelated-network"] = untouched.copy()
        harness.state_path.write_text(json.dumps(state))
        harness.write_token("UNUSED-TOKEN")

        completed = harness.run()
        self.assertNotEqual(completed.returncode, 0)
        after = harness.state()
        self.assertEqual(after["networks"]["unrelated-network"], untouched)
        self.assertEqual(after["enroll_calls"], 1)
        self.assertTrue(harness.token.exists())
        self.assertIn("不属于本安装器", completed.stderr)

    def test_bad_archive_hash_never_loads_image(self) -> None:
        harness = self.harness()
        completed = harness.run(extra_env={"FAKE_CURL_MODE": "bad_hash"})

        self.assertNotEqual(completed.returncode, 0)
        state = harness.state()
        self.assertFalse(state["image_loaded"])
        self.assertEqual(state["enroll_calls"], 0)
        self.assertTrue(harness.token.exists())
        self.assertIn("SHA256 校验失败", completed.stderr)

    def test_missing_docker_reports_official_guidance_without_installing(self) -> None:
        harness = self.harness(with_docker=False)
        completed = harness.run()

        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("未找到 Docker", completed.stderr)
        self.assertIn("https://docs.docker.com/engine/install/", completed.stderr)
        self.assertTrue(harness.token.exists())

    def test_unavailable_docker_daemon_reports_guidance(self) -> None:
        harness = self.harness()
        completed = harness.run(extra_env={"FAKE_DOCKER_UNAVAILABLE": "1"})

        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("daemon 不可用或当前 root 无访问权限", completed.stderr)
        self.assertIn("不会安装或升级 Docker", completed.stderr)
        self.assertTrue(harness.token.exists())

    def test_remote_docker_context_is_rejected_before_resource_creation(self) -> None:
        harness = self.harness()
        completed = harness.run(extra_env={"FAKE_DOCKER_ENDPOINT": "ssh://remote.example"})

        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("拒绝远程 Docker context", completed.stderr)
        state = harness.state()
        self.assertFalse(state["networks"])
        self.assertFalse(state["volumes"])
        self.assertTrue(harness.token.exists())

    def test_legacy_docker_without_context_show_installs_using_current_context(self) -> None:
        harness = self.harness()
        completed = harness.run(extra_env={
            "FAKE_CONTEXT_SHOW_UNSUPPORTED": "1",
            "FAKE_CURRENT_CONTEXT": "local-node",
            "FAKE_DOCKER_ENDPOINT": "unix:///run/docker-local.sock",
        })
        self.assertEqual(completed.returncode, 0, completed.stderr)
        state = harness.state()
        self.assertEqual(state["enroll_calls"], 1)
        self.assertEqual(state["containers"][harness.prefix]["status"], "running")
        self.assertNotIn(["context", "show"], state["commands"])
        self.assertTrue(any(args[:3] == ["context", "inspect", "local-node"] for args in state["commands"]))

    def test_legacy_docker_still_rejects_remote_context_and_host_overrides(self) -> None:
        for environment in (
            {"DOCKER_CONTEXT": "remote-node", "FAKE_DOCKER_ENDPOINT": "ssh://remote.example"},
            {"DOCKER_HOST": "tcp://remote.example:2375"},
        ):
            with self.subTest(environment=environment), tempfile.TemporaryDirectory() as folder:
                harness = InstallerHarness(Path(folder))
                completed = harness.run(extra_env={"FAKE_CONTEXT_SHOW_UNSUPPORTED": "1", **environment})
                self.assertNotEqual(completed.returncode, 0)
                self.assertIn("拒绝远程", completed.stderr)
                state = harness.state()
                self.assertFalse(state["networks"])
                self.assertFalse(state["volumes"])
                self.assertFalse(state["containers"])
                self.assertNotIn("curl_commands", state)
                self.assertTrue(harness.token.exists())

    def test_context_inspection_failure_does_not_fall_back_to_default(self) -> None:
        for failure in ("name", "endpoint"):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as folder:
                harness = InstallerHarness(Path(folder))
                completed = harness.run(extra_env={"FAKE_CONTEXT_INSPECT_FAILURE": failure})
                self.assertNotEqual(completed.returncode, 0)
                self.assertIn("Docker context", completed.stderr)
                state = harness.state()
                self.assertFalse(state["networks"])
                self.assertFalse(state["volumes"])
                self.assertFalse(state["containers"])
                self.assertNotIn("curl_commands", state)
                self.assertTrue(harness.token.exists())

    def test_offline_partial_is_preserved_and_resumed(self) -> None:
        harness = self.harness()
        first = harness.run(extra_env={"FAKE_CURL_MODE": "offline"})
        self.assertNotEqual(first.returncode, 0)
        partial = self.root / "nodes" / harness.node_id / f"image-{harness.archive_sha}.tar.gz.part"
        self.assertTrue(partial.exists())
        partial_size = partial.stat().st_size
        self.assertGreater(partial_size, 0)
        self.assertLess(partial_size, harness.archive_size)
        self.assertEqual(harness.state()["enroll_calls"], 0)

        second = harness.run()
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertFalse(partial.exists())
        final = partial.with_suffix("")
        self.assertEqual(hashlib.sha256(final.read_bytes()).hexdigest(), harness.archive_sha)
        self.assertEqual(harness.state()["enroll_calls"], 1)

    def test_response_loss_with_persisted_identity_does_not_reenroll(self) -> None:
        harness = self.harness()
        completed = harness.run(extra_env={"FAKE_ENROLL": "write_identity_fail"})

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(harness.state()["identity"], "valid")
        self.assertEqual(harness.state()["enroll_calls"], 1)
        self.assertFalse(harness.token.exists())
        self.assertIn("身份已安全落盘", completed.stderr)

        harness.write_token("MUST-NOT-BE-CONSUMED")
        rerun = harness.run(extra_env={"FAKE_ENROLL": "fail"})
        self.assertEqual(rerun.returncode, 0, rerun.stderr)
        self.assertEqual(harness.state()["enroll_calls"], 1)

    def test_runtime_failure_preserves_identity_for_recovery(self) -> None:
        harness = self.harness()
        first = harness.run(extra_env={"FAKE_RUNTIME_FAIL": "1"})
        self.assertNotEqual(first.returncode, 0)
        self.assertEqual(harness.state()["identity"], "valid")
        self.assertEqual(harness.state()["enroll_calls"], 1)
        self.assertFalse(harness.token.exists())

        harness.write_token("RECOVERY-TOKEN-MUST-NOT-BE-USED")
        second = harness.run()
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual(harness.state()["enroll_calls"], 1)
        self.assertEqual(harness.state()["containers"][harness.prefix]["status"], "running")

    def test_failed_enrollment_without_identity_keeps_token_and_reports_no_success(self) -> None:
        harness = self.harness()
        secret = harness.token.read_text()
        completed = harness.run(extra_env={"FAKE_ENROLL": "fail"})

        self.assertNotEqual(completed.returncode, 0)
        self.assertEqual(harness.state()["identity"], "missing")
        self.assertEqual(harness.state()["enroll_calls"], 1)
        self.assertTrue(harness.token.exists())
        self.assertNotIn(secret, completed.stdout + completed.stderr)
        self.assertIn("未报告成功", completed.stderr)


if __name__ == "__main__":
    unittest.main()
