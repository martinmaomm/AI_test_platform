#!/usr/bin/env python3
"""Focused tests for the standalone performance-node Docker upgrader."""

from __future__ import annotations

import argparse
import contextlib
import importlib.util
import io
import itertools
import json
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "deploy" / "performance" / "upgrade-node.py"
SPEC = importlib.util.spec_from_file_location("performance_node_upgrader", SCRIPT)
assert SPEC and SPEC.loader
upgrader = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(upgrader)

NODE_ID = "11111111-1111-4111-8111-111111111111"
PLATFORM = "https://platform.example.test"
IMAGE_ID = "sha256:" + "4" * 64
IMAGE = "docker.io/example/performance-node@sha256:" + "5" * 64
SECRET = NODE_ID + ".do-not-print-this-token"


def arguments(**changes):
    values = {
        "node_id": NODE_ID,
        "platform": PLATFORM,
        "image": IMAGE,
        "agent_version": "0.4.0",
        "container": None,
    }
    values.update(changes)
    return argparse.Namespace(**values)


def container_details(*, command=None, labels=None, network_mode="bridge", running=True, image="sha256:old"):
    labels = {
        upgrader.NODE_LABEL: NODE_ID,
        upgrader.PLATFORM_LABEL: PLATFORM,
        upgrader.OWNER_LABEL: upgrader.INSTALLER_OWNER,
        **(labels or {}),
    }
    host = {
        "Binds": None,
        "ContainerIDFile": "",
        "LogConfig": {"Type": "json-file", "Config": {"max-size": "10m"}},
        "NetworkMode": network_mode,
        "PortBindings": {"9000/tcp": [{"HostIp": "127.0.0.1", "HostPort": "19000"}]},
        "RestartPolicy": {"Name": "unless-stopped", "MaximumRetryCount": 0},
        "AutoRemove": False,
        "VolumeDriver": "",
        "VolumesFrom": None,
        "CapAdd": None,
        "CapDrop": ["ALL"],
        "CgroupnsMode": "private",
        "Dns": ["1.1.1.1"],
        "DnsOptions": [],
        "DnsSearch": [],
        "ExtraHosts": ["service.test:127.0.0.1"],
        "GroupAdd": [],
        "IpcMode": "private",
        "Cgroup": "",
        "Links": None,
        "OomScoreAdj": 0,
        "PidMode": "",
        "Privileged": False,
        "PublishAllPorts": False,
        "ReadonlyRootfs": True,
        "SecurityOpt": ["no-new-privileges:true"],
        "UTSMode": "",
        "UsernsMode": "",
        "ShmSize": 67108864,
        "Runtime": "runc",
        "ConsoleSize": [0, 0],
        "Isolation": "",
        "CpuShares": 512,
        "Memory": 536870912,
        "NanoCpus": 1000000000,
        "MaskedPaths": ["/proc/acpi"],
        "ReadonlyPaths": ["/proc/bus"],
    }
    config = {
        "Hostname": "old-node",
        "Domainname": "",
        "User": "nodeagent",
        "AttachStdin": False,
        "AttachStdout": True,
        "AttachStderr": True,
        "ExposedPorts": {"9000/tcp": {}},
        "Tty": False,
        "OpenStdin": False,
        "StdinOnce": False,
        "Env": [
            "PERFORMANCE_NODE_STATE_DIR=/var/lib/performance-node",
            "PERFORMANCE_PLATFORM_URL=" + PLATFORM,
            "CUSTOM=value",
        ],
        "Cmd": command or ["run"],
        "Image": "old-image",
        "Volumes": {"/var/lib/performance-node": {}},
        "WorkingDir": "/app",
        "Entrypoint": ["python", "-m", "performance_node"],
        "Labels": labels,
        "StopTimeout": 25,
    }
    return {
        "Id": "oldcontainer1234567890",
        "Name": "/performance-node",
        "Image": image,
        "Config": config,
        "HostConfig": host,
        "Mounts": [{
            "Type": "volume", "Name": "identity", "Source": "/var/lib/docker/volumes/identity/_data",
            "Destination": "/var/lib/performance-node", "Driver": "local", "Mode": "z", "RW": True,
            "Propagation": "",
        }],
        "State": {"Status": "running" if running else "exited", "Running": running},
        "NetworkSettings": {"Networks": {"node-network": {
            "IPAMConfig": None, "Links": None, "Aliases": ["performance-node"],
            "NetworkID": "network", "EndpointID": "endpoint", "Gateway": "172.20.0.1",
            "IPAddress": "172.20.0.2", "IPPrefixLen": 16, "IPv6Gateway": "",
            "GlobalIPv6Address": "", "GlobalIPv6PrefixLen": 0, "MacAddress": "",
            "DriverOpts": None,
        }}},
    }


class FakeAPI:
    def __init__(self, details=None):
        self.old = deepcopy(details or container_details())
        self.new = None
        self.created_payload = None
        self.created_name = None
        self.operations = []
        self.files = {
            "/var/lib/performance-node/identity.json": {
                "version": 1, "node_id": NODE_ID, "agent_token": SECRET,
            },
            "/var/lib/performance-node/installation.json": {
                "version": 1, "platform_url": PLATFORM, "node_id": NODE_ID, "ca_sha256": None,
            },
            "/var/lib/performance-node/execution.json": {
                "version": 1, "active": None, "report": None, "completed_runs": {},
            },
        }
        if self.old["Config"]["Cmd"] == ["run"]:
            self.files.pop("/var/lib/performance-node/installation.json")
        self.fail_at = None
        self.early_exit = False

    def containers(self):
        values = [{
            "Id": self.old["Id"], "Names": [self.old["Name"]],
            "Labels": deepcopy(self.old["Config"]["Labels"]),
        }]
        if self.new:
            values.append({"Id": self.new["Id"], "Names": [self.new["Name"]], "Labels": {}})
        return values

    def inspect(self, container_id):
        if self.new and container_id == self.new["Id"]:
            if self.early_exit:
                self.new["State"] = {"Status": "exited", "Running": False}
            return deepcopy(self.new)
        return deepcopy(self.old)

    def file_json(self, container_id, path, *, required=True):
        value = self.files.get(path)
        if value is None and required:
            raise upgrader.UpgradeError("HTTP 404")
        return deepcopy(value)

    def network(self, network_id):
        return {"Id": network_id, "Driver": "bridge", "Scope": "local", "Labels": {}}

    def _maybe_fail(self, operation):
        self.operations.append(operation)
        if self.fail_at == operation:
            raise upgrader.UpgradeError("simulated failure")

    def update_restart(self, container_id, policy):
        is_new = self.new and container_id == self.new["Id"]
        operation = (
            "disable-new-restart" if is_new and policy.get("Name") == "no"
            else "restore-new-restart" if is_new
            else "disable-restart" if policy.get("Name") == "no"
            else "restore-restart"
        )
        self._maybe_fail(operation)
        if is_new:
            self.new.setdefault("HostConfig", {})["RestartPolicy"] = deepcopy(policy)
        else:
            self.old["HostConfig"]["RestartPolicy"] = deepcopy(policy)

    def stop(self, container_id, seconds):
        self._maybe_fail("stop-new" if self.new and container_id == self.new["Id"] else "stop-old")
        target = self.new if self.new and container_id == self.new["Id"] else self.old
        target["State"] = {"Status": "exited", "Running": False}

    def start(self, container_id):
        operation = "start-new" if self.new and container_id == self.new["Id"] else "start-old"
        self._maybe_fail(operation)
        target = self.new if self.new and container_id == self.new["Id"] else self.old
        target["State"] = {"Status": "running", "Running": True}

    def rename(self, container_id, name):
        operation = "rename-back" if name == "performance-node" else "rename-old"
        self._maybe_fail(operation)
        self.old["Name"] = "/" + name

    def create(self, name, payload):
        self._maybe_fail("create-new")
        self.created_payload = deepcopy(payload)
        self.created_name = name
        self.new = {
            "Id": "newcontainer1234567890", "Name": "/" + name,
            "State": {"Status": "created", "Running": False},
            "HostConfig": deepcopy(payload["HostConfig"]),
        }
        return self.new["Id"]

    def remove(self, container_id):
        self._maybe_fail("remove-new")
        self.new = None


class UpgradeTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        clock = itertools.count(start=0, step=10)
        patches = [
            mock.patch.object(upgrader, "LOCK_DIR", Path(self.temporary.name)),
            mock.patch.object(upgrader, "verify_host", return_value=None),
            mock.patch.object(upgrader, "image_version", return_value="0.4.0"),
            mock.patch.object(upgrader, "image_details", return_value=(IMAGE_ID, {
                "org.opencontainers.image.version": "0.4.0",
            })),
            mock.patch.object(upgrader.time, "sleep", return_value=None),
            mock.patch.object(upgrader.time, "monotonic", side_effect=lambda: next(clock)),
        ]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)

    def execute(self, api, args=None):
        output = io.StringIO()
        errors = io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
            upgrader.upgrade(args or arguments(), api)
        return output.getvalue() + errors.getvalue()

    def test_upgrades_legacy_run_and_preserves_host_config_and_mounts(self):
        api = FakeAPI(container_details(command=["run"]))
        original_host = deepcopy(api.old["HostConfig"])
        output = self.execute(api)
        self.assertEqual(api.created_name, "performance-node")
        self.assertEqual(api.created_payload["Cmd"], ["run"])
        self.assertEqual(
            {key: value for key, value in api.created_payload["HostConfig"].items() if key != "RestartPolicy"},
            {**{key: value for key, value in original_host.items() if key != "RestartPolicy"},
             "Mounts": [{
                 "Type": "volume", "Source": "identity", "Target": "/var/lib/performance-node",
                 "ReadOnly": False,
             }]},
        )
        self.assertEqual(api.created_payload["HostConfig"]["RestartPolicy"]["Name"], "no")
        self.assertEqual(api.new["HostConfig"]["RestartPolicy"], original_host["RestartPolicy"])
        self.assertEqual(api.created_payload["Volumes"], {"/var/lib/performance-node": {}})
        self.assertEqual(api.created_payload["Image"], IMAGE)
        self.assertEqual(api.old["HostConfig"]["RestartPolicy"]["Name"], "no")
        self.assertIn("平台页面核对新版 heartbeat", output)

    def test_preserves_start_command_without_printing_consumed_token(self):
        command = ["start", "--server", PLATFORM, "--node-id", NODE_ID, "--token", "consumed-secret"]
        api = FakeAPI(container_details(command=command))
        output = self.execute(api)
        self.assertEqual(api.created_payload["Cmd"], command)
        self.assertNotIn("consumed-secret", output)
        self.assertNotIn(SECRET, output)

    def test_anonymous_volume_declarations_are_pinned_to_inspected_volume(self):
        shapes = (
            {"Mounts": [{
                "Type": "volume", "Source": "", "Target": "/var/lib/performance-node",
                "ReadOnly": False,
            }], "Binds": None},
            {"Mounts": None, "Binds": ["/var/lib/performance-node"]},
        )
        for shape in shapes:
            details = container_details()
            details["HostConfig"].update(deepcopy(shape))
            api = FakeAPI(details)
            with self.subTest(shape=shape):
                self.execute(api)
                mounts = api.created_payload["HostConfig"]["Mounts"]
                identity = [item for item in mounts if item["Target"] == "/var/lib/performance-node"]
                self.assertEqual(len(identity), 1)
                self.assertEqual(identity[0]["Source"], "identity")

    def test_rejects_identity_or_platform_mismatch_before_stop(self):
        for field, value in (("identity", "22222222-2222-4222-8222-222222222222"), ("environment", "https://wrong.test")):
            api = FakeAPI()
            if field == "identity":
                api.files["/var/lib/performance-node/identity.json"]["node_id"] = value
            else:
                api.old["Config"]["Env"] = [
                    "PERFORMANCE_NODE_STATE_DIR=/var/lib/performance-node",
                    "PERFORMANCE_PLATFORM_URL=" + value,
                ]
            with self.subTest(field=field), self.assertRaises(upgrader.UpgradeError):
                self.execute(api)
            self.assertNotIn("stop-old", api.operations)

    def test_rejects_active_work_and_missing_persistent_mount(self):
        api = FakeAPI()
        api.files["/var/lib/performance-node/execution.json"]["active"] = {"run_id": "busy"}
        with self.assertRaisesRegex(upgrader.UpgradeError, "活动压测"):
            self.execute(api)
        self.assertEqual(api.operations, [])

        api = FakeAPI()
        api.files["/var/lib/performance-node/execution.json"]["report"] = {"run_id": "pending"}
        with self.assertRaisesRegex(upgrader.UpgradeError, "待平台确认"):
            self.execute(api)
        self.assertEqual(api.operations, [])

        details = container_details()
        details["Mounts"] = []
        api = FakeAPI(details)
        with self.assertRaisesRegex(upgrader.UpgradeError, "持久化身份"):
            self.execute(api, arguments(container="performance-node"))
        self.assertEqual(api.operations, [])

    def test_rejects_managed_and_complex_networks_before_pull(self):
        cases = []
        managed = container_details(labels={"com.docker.compose.project": "demo"})
        cases.append(managed)
        host = container_details(network_mode="host")
        cases.append(host)
        multiple = container_details()
        multiple["NetworkSettings"]["Networks"]["second"] = {}
        cases.append(multiple)
        static = container_details()
        static["NetworkSettings"]["Networks"]["node-network"]["IPAMConfig"] = {"IPv4Address": "172.20.0.9"}
        cases.append(static)
        for details in cases:
            api = FakeAPI(details)
            with self.subTest(details=details["HostConfig"]["NetworkMode"]), self.assertRaises(upgrader.UpgradeError):
                self.execute(api)
            self.assertEqual(api.operations, [])

    def test_rejects_auto_remove_before_any_mutation(self):
        details = container_details()
        details["HostConfig"]["AutoRemove"] = True
        api = FakeAPI(details)
        with self.assertRaisesRegex(upgrader.UpgradeError, "自动删除"):
            self.execute(api)
        self.assertEqual(api.operations, [])

    def test_pull_or_version_failure_does_not_stop_old_container(self):
        api = FakeAPI()
        with mock.patch.object(upgrader, "image_version", side_effect=upgrader.UpgradeError("pull failed")):
            with self.assertRaisesRegex(upgrader.UpgradeError, "pull failed"):
                self.execute(api)
        self.assertEqual(api.operations, [])

    def test_create_failure_rolls_back_name_restart_and_running_state(self):
        api = FakeAPI()
        api.fail_at = "create-new"
        with self.assertRaises(upgrader.UpgradeError):
            self.execute(api)
        self.assertEqual(api.old["Name"], "/performance-node")
        self.assertTrue(api.old["State"]["Running"])
        self.assertEqual(api.old["HostConfig"]["RestartPolicy"]["Name"], "unless-stopped")
        self.assertIn("start-old", api.operations)

    def test_stop_failure_restores_restart_policy(self):
        api = FakeAPI()
        api.fail_at = "stop-old"
        with self.assertRaises(upgrader.UpgradeError):
            self.execute(api)
        self.assertEqual(api.old["Name"], "/performance-node")
        self.assertEqual(api.old["HostConfig"]["RestartPolicy"]["Name"], "unless-stopped")

    def test_start_failure_removes_new_and_restores_old(self):
        api = FakeAPI()
        api.fail_at = "start-new"
        with self.assertRaises(upgrader.UpgradeError):
            self.execute(api)
        self.assertIsNone(api.new)
        self.assertEqual(api.old["Name"], "/performance-node")
        self.assertTrue(api.old["State"]["Running"])

    def test_early_exit_removes_only_new_container_and_rolls_back(self):
        api = FakeAPI()
        api.early_exit = True
        with self.assertRaisesRegex(upgrader.UpgradeError, "提前退出|持续运行"):
            self.execute(api)
        self.assertIsNone(api.new)
        self.assertEqual(api.old["Name"], "/performance-node")
        self.assertTrue(api.old["State"]["Running"])

    def test_failed_new_cleanup_keeps_old_backup_stopped(self):
        api = FakeAPI()
        api.early_exit = True
        api.fail_at = "remove-new"
        with self.assertRaisesRegex(upgrader.UpgradeError, "不会启动"):
            self.execute(api)
        self.assertIn("-backup-", api.old["Name"])
        self.assertFalse(api.old["State"]["Running"])
        self.assertEqual(api.old["HostConfig"]["RestartPolicy"]["Name"], "no")
        self.assertNotIn("rename-back", api.operations)
        self.assertNotIn("start-old", api.operations)

    def test_failed_backup_rename_never_restarts_old_container(self):
        api = FakeAPI()
        api.early_exit = True
        api.fail_at = "rename-back"
        with self.assertRaisesRegex(upgrader.UpgradeError, "不会恢复"):
            self.execute(api)
        self.assertIn("-backup-", api.old["Name"])
        self.assertFalse(api.old["State"]["Running"])
        self.assertEqual(api.old["HostConfig"]["RestartPolicy"]["Name"], "no")
        self.assertNotIn("restore-restart", api.operations)
        self.assertNotIn("start-old", api.operations)

    def test_offline_old_container_starts_new_container(self):
        api = FakeAPI(container_details(running=False))
        self.execute(api)
        self.assertNotIn("stop-old", api.operations)
        self.assertIn("start-new", api.operations)
        self.assertTrue(api.new["State"]["Running"])

    def test_current_exact_image_is_noop(self):
        details = container_details(image=IMAGE_ID)
        api = FakeAPI(details)
        output = self.execute(api)
        self.assertIn("无需升级", output)
        self.assertEqual(api.operations, [])

    def test_current_exact_image_is_noop_even_with_active_state(self):
        details = container_details(image=IMAGE_ID)
        api = FakeAPI(details)
        api.files["/var/lib/performance-node/execution.json"]["active"] = {"run_id": "still-running"}
        output = self.execute(api)
        self.assertIn("无需升级", output)
        self.assertEqual(api.operations, [])

    def test_ambiguous_match_and_backup_name_conflict_stop_before_mutation(self):
        api = FakeAPI()
        original = api.containers
        api.containers = lambda: original() + [{
            "Id": "other", "Names": ["/other"], "Labels": {upgrader.NODE_LABEL: NODE_ID},
        }]
        with self.assertRaisesRegex(upgrader.UpgradeError, "唯一定位"):
            self.execute(api)
        self.assertEqual(api.operations, [])

        api = FakeAPI()
        original = api.containers
        backup = "performance-node-backup-" + api.old["Id"][:12]
        api.containers = lambda: original() + [{"Id": "other", "Names": ["/" + backup], "Labels": {}}]
        with self.assertRaisesRegex(upgrader.UpgradeError, "备份容器名"):
            self.execute(api, arguments(container="performance-node"))
        self.assertEqual(api.operations, [])

    def test_concurrent_lock_is_rejected(self):
        api = FakeAPI()
        with mock.patch.object(upgrader.fcntl, "flock", side_effect=BlockingIOError):
            with self.assertRaisesRegex(upgrader.UpgradeError, "升级进程"):
                self.execute(api)
        self.assertEqual(api.operations, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
