from __future__ import annotations

import json
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import performance_node.locust_runtime as runtime


class FixtureHandler(BaseHTTPRequestHandler):
    requests: list[str] = []
    lock = threading.Lock()
    delay_seconds = 0.0

    def do_GET(self):
        with self.lock:
            self.requests.append(self.headers.get("X-Run-User", ""))
        time.sleep(self.delay_seconds)
        payload = b'{"ok":true}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, _format, *_args):
        pass


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


def snapshot(
    base_url: str, node_ids: list[str], *, duration: int = 1,
    users_per_node: int = 1, spawn_rate: float | None = None,
) -> dict:
    users = len(node_ids) * users_per_node
    return {
        "schema_version": 3,
        "run_id": str(uuid.uuid4()),
        "nodes": [
            {"node_id": node_id, "users": users_per_node, "validation_key": f"{index:x}" * 64}
            for index, node_id in enumerate(node_ids, start=1)
        ],
        "engine_version": "2.43.3",
        "plan_name": "two-node-fixture",
        "base_url": base_url,
        "allowed_methods": ["GET"],
        "mode": "load",
        "users": users,
        "spawn_rate": spawn_rate if spawn_rate is not None else users,
        "duration_seconds": duration,
        "wait_seconds": 0.1,
        "variables": {},
        "unique_variables": [{"name": "run_user", "prefix": "fixture-"}],
        "steps": [{
            "name": "health", "phase": "main", "method": "GET", "path": "/health",
            "query": {}, "headers": {"X-Run-User": "${run_user}"},
            "body_type": "none", "body": None, "extract": [],
            "assertions": [{"check": "status_code", "comparator": "eq", "expected": 200}],
        }],
    }


class MultiNodeRuntimeTests(unittest.TestCase):
    def setUp(self):
        FixtureHandler.requests = []
        FixtureHandler.delay_seconds = 0.0
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), FixtureHandler)
        self.server_thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.server_thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.server_thread.join(timeout=2)

    def launch(self, root: Path, frozen: dict, tokens: dict[str, str], *, bad_node: str | None = None):
        port = free_port()
        start_file = root / "start"
        metrics_file = root / "metrics.json"
        complete_file = root / "complete.json"
        master_config = root / "master.json"
        master_config.write_text(json.dumps({"snapshot": frozen, "handshake_tokens": tokens}), encoding="utf-8")
        master = subprocess.Popen([
            sys.executable, runtime.__file__, "--role", "master", "--config", str(master_config),
            "--master-port", str(port), "--start-file", str(start_file),
            "--metrics-file", str(metrics_file), "--complete-file", str(complete_file),
        ], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        workers = []
        time.sleep(0.3)
        for index, node_id in enumerate(tokens):
            worker_config = root / f"worker-{index}.json"
            token = "wrong-" + "x" * 32 if node_id == bad_node else tokens[node_id]
            worker_config.write_text(json.dumps({"snapshot": frozen, "handshake_token": token}), encoding="utf-8")
            workers.append(subprocess.Popen([
                sys.executable, runtime.__file__, "--role", "worker", "--config", str(worker_config),
                "--node-id", node_id, "--master-port", str(port),
            ], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True))
        start_file.touch()
        return master, workers, complete_file

    @staticmethod
    def finish(processes: list[subprocess.Popen], timeout: float = 20) -> list[tuple[int, str, str]]:
        results = []
        for process in processes:
            stdout, stderr = process.communicate(timeout=timeout)
            results.append((process.returncode, stdout, stderr))
        return results

    def test_two_workers_share_total_users_and_return_complete_per_node_metrics(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            node_ids = sorted([str(uuid.uuid4()), str(uuid.uuid4())])
            frozen = snapshot(f"http://127.0.0.1:{self.server.server_port}", node_ids)
            tokens = {node_id: f"token-{index}-" + "x" * 32 for index, node_id in enumerate(node_ids)}
            master, workers, complete_file = self.launch(root, frozen, tokens)
            results = self.finish([master, *workers])
            self.assertEqual([item[0] for item in results], [0, 0, 0], results)
            report = json.loads(complete_file.read_text(encoding="utf-8"))
            self.assertTrue(report["complete"])
            self.assertEqual(report["reason"], "completed")
            metrics = report["metrics"]
            self.assertEqual(metrics["admitted_node_ids"], node_ids)
            self.assertEqual(metrics["requests"], sum(item["requests"] for item in metrics["nodes"].values()))
            self.assertTrue(all(item["assigned_users"] == 1 for item in metrics["nodes"].values()))
            self.assertTrue(all(item["complete"] for item in metrics["nodes"].values()))
            observed_nodes = {value.split("-")[2] for value in FixtureHandler.requests if value}
            self.assertEqual(observed_nodes, {node_id[:8] for node_id in node_ids})

    def test_wrong_member_token_never_crosses_start_barrier(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            node_ids = sorted([str(uuid.uuid4()), str(uuid.uuid4())])
            frozen = snapshot(f"http://127.0.0.1:{self.server.server_port}", node_ids)
            tokens = {node_id: f"token-{index}-" + "x" * 32 for index, node_id in enumerate(node_ids)}
            master, workers, complete_file = self.launch(root, frozen, tokens, bad_node=node_ids[1])
            results = self.finish([master, *workers])
            self.assertEqual(results[0][0], 2, results)
            report = json.loads(complete_file.read_text(encoding="utf-8"))
            self.assertFalse(report["metrics"]["started"])
            self.assertEqual(report["reason"], "worker_identity_invalid")
            self.assertEqual(FixtureHandler.requests, [])

    def test_slow_ramp_accepts_bounded_intermediate_assignments(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            node_ids = sorted([str(uuid.uuid4()), str(uuid.uuid4())])
            frozen = snapshot(
                f"http://127.0.0.1:{self.server.server_port}", node_ids,
                duration=5, users_per_node=2, spawn_rate=1,
            )
            tokens = {node_id: f"token-{index}-" + "x" * 32 for index, node_id in enumerate(node_ids)}
            master, workers, complete_file = self.launch(root, frozen, tokens)
            results = self.finish([master, *workers], timeout=20)
            self.assertEqual([item[0] for item in results], [0, 0, 0], results)
            report = json.loads(complete_file.read_text(encoding="utf-8"))
            self.assertTrue(report["complete"])
            self.assertTrue(all(
                item["assigned_users"] == 2 and item["complete"]
                for item in report["metrics"]["nodes"].values()
            ))

    def test_duration_can_end_mid_ramp_without_late_spawn_or_incomplete_final_report(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            node_ids = sorted([str(uuid.uuid4()), str(uuid.uuid4())])
            frozen = snapshot(
                f"http://127.0.0.1:{self.server.server_port}", node_ids,
                duration=1, users_per_node=2, spawn_rate=0.5,
            )
            tokens = {node_id: f"token-{index}-" + "x" * 32 for index, node_id in enumerate(node_ids)}
            master, workers, complete_file = self.launch(root, frozen, tokens)
            results = self.finish([master, *workers], timeout=20)
            self.assertEqual([item[0] for item in results], [0, 0, 0], results)
            report = json.loads(complete_file.read_text(encoding="utf-8"))
            self.assertEqual(report["reason"], "completed")
            self.assertTrue(report["complete"])
            self.assertTrue(all(item["complete"] for item in report["metrics"]["nodes"].values()))

    def test_stopping_report_is_not_frozen_before_inflight_request_finishes(self):
        FixtureHandler.delay_seconds = 4.0
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            node_ids = [str(uuid.uuid4())]
            frozen = snapshot(
                f"http://127.0.0.1:{self.server.server_port}", node_ids, duration=1,
            )
            tokens = {node_ids[0]: "token-" + "x" * 32}
            master, workers, complete_file = self.launch(root, frozen, tokens)
            results = self.finish([master, *workers], timeout=20)
            self.assertEqual([item[0] for item in results], [0, 0], results)
            report = json.loads(complete_file.read_text(encoding="utf-8"))
            node = report["metrics"]["nodes"][node_ids[0]]
            self.assertTrue(report["complete"])
            self.assertTrue(node["complete"])
            self.assertEqual(node["users"], 0)
            self.assertGreaterEqual(node["report_seq"], 2)

    def test_stop_file_cancel_keeps_complete_final_statistics(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            node_ids = sorted([str(uuid.uuid4()), str(uuid.uuid4())])
            frozen = snapshot(
                f"http://127.0.0.1:{self.server.server_port}", node_ids, duration=8,
            )
            tokens = {node_id: f"token-{index}-" + "x" * 32 for index, node_id in enumerate(node_ids)}
            master, workers, complete_file = self.launch(root, frozen, tokens)
            deadline = time.monotonic() + 6
            while len(FixtureHandler.requests) < 4 and time.monotonic() < deadline:
                time.sleep(0.05)
            self.assertGreaterEqual(len(FixtureHandler.requests), 4)
            (root / "start.stop").touch()
            results = self.finish([master, *workers], timeout=20)
            report = json.loads(complete_file.read_text(encoding="utf-8"))
            self.assertEqual([item[0] for item in results], [0, 0, 0], (results, report))
            self.assertEqual(report["reason"], "cancelled")
            self.assertTrue(report["complete"], report)
            self.assertTrue(all(item["complete"] for item in report["metrics"]["nodes"].values()), report)

    def test_worker_exit_stops_the_whole_run_without_reassigning_users(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            node_ids = sorted([str(uuid.uuid4()), str(uuid.uuid4())])
            frozen = snapshot(
                f"http://127.0.0.1:{self.server.server_port}", node_ids, duration=8,
            )
            tokens = {node_id: f"token-{index}-" + "x" * 32 for index, node_id in enumerate(node_ids)}
            master, workers, complete_file = self.launch(root, frozen, tokens)
            deadline = time.monotonic() + 6
            while time.monotonic() < deadline:
                observed_nodes = {value.split("-")[2] for value in FixtureHandler.requests if value}
                if observed_nodes == {node_id[:8] for node_id in node_ids}:
                    break
                time.sleep(0.05)
            else:
                self.fail("两个 Worker 未在故障注入前开始请求")
            workers[1].kill()
            killed_stdout, killed_stderr = workers[1].communicate(timeout=5)
            master_result = self.finish([master], timeout=40)[0]
            survivor_result = self.finish([workers[0]], timeout=10)[0]
            self.assertEqual(master_result[0], 2, (master_result, survivor_result, killed_stdout, killed_stderr))
            self.assertEqual(survivor_result[0], 0, survivor_result)
            report = json.loads(complete_file.read_text(encoding="utf-8"))
            self.assertEqual(report["reason"], "worker_lost")
            self.assertFalse(report["complete"])
            self.assertEqual(report["metrics"]["nodes"][node_ids[1]]["status"], "lost")
            self.assertIsNone(report["metrics"]["nodes"][node_ids[1]]["users"])
            self.assertEqual(report["metrics"]["nodes"][node_ids[0]]["assigned_users"], 1)


if __name__ == "__main__":
    unittest.main()
