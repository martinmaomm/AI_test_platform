"""Offline reliability tests for requests checkpoints, cancellation and cleanup."""

from datetime import timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
import subprocess
import sys
import threading
import time
import unittest
from unittest.mock import patch

from api_testing.requests_runner import requests_runner
from api_testing.requests_runtime import CaseContractError, export_python, normalize_case, run_case


class FakeResponse:
    def __init__(self, status_code=200, body=None, url="http://127.0.0.1/"):
        self.status_code, self._body, self.url = status_code, body, url
        self.headers = {"content-type": "application/json"}
        self.elapsed = timedelta(milliseconds=1)
        self.text = json.dumps(body)

    def json(self):
        return self._body


class FakeSession:
    def __init__(self, responses):
        self.responses, self.requests, self.closed = list(responses), [], False

    def request(self, **kwargs):
        self.requests.append(kwargs)
        return self.responses.pop(0)

    def close(self):
        self.closed = True


class LoopbackHandler(BaseHTTPRequestHandler):
    def _send(self, status, body):
        encoded = json.dumps(body).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        try:
            self.wfile.write(encoded)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_GET(self):
        self.server.paths.append(("GET", self.path))
        if self.path == "/slow":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", "100")
            self.end_headers()
            try:
                for _index in range(20):
                    self.wfile.write(b" ")
                    self.wfile.flush()
                    time.sleep(0.05)
            except (BrokenPipeError, ConnectionResetError):
                pass
            return
        self._send(200, {"ok": True})

    def do_POST(self):
        self.server.paths.append(("POST", self.path))
        self._send(201, {"id": 42})

    def do_DELETE(self):
        self.server.paths.append(("DELETE", self.path))
        self._send(204, {})

    def log_message(self, _format, *args):
        return


class RequestsCheckpointTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), LoopbackHandler)
        cls.server.paths = []
        cls.server_thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.server_thread.start()
        cls.previous_loopback_port = os.environ.get("OFFLINE_ALLOWED_LOOPBACK_PORT")
        os.environ["OFFLINE_ALLOWED_LOOPBACK_PORT"] = str(cls.server.server_port)
        cls.base_url = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.server_thread.join(timeout=2)
        if cls.previous_loopback_port is None:
            os.environ.pop("OFFLINE_ALLOWED_LOOPBACK_PORT", None)
        else:
            os.environ["OFFLINE_ALLOWED_LOOPBACK_PORT"] = cls.previous_loopback_port
        super().tearDownClass()

    def setUp(self):
        self.server.paths.clear()

    def test_normalize_rejects_invalid_phase_and_requires(self):
        invalid_cases = [
            ({"teststeps": [{"phase": "later", "request": {"url": "/x"}}]}, "phase"),
            ({"teststeps": [{"phase": "cleanup", "request": {"url": "/x"}}]}, "requires"),
            ({"teststeps": [{"phase": "cleanup", "requires": [], "request": {"url": "/x"}}]}, "requires"),
            ({"teststeps": [{"phase": "cleanup", "requires": ["bad-name"], "request": {"url": "/x"}}]}, "变量名"),
            ({"teststeps": [{"phase": "main", "requires": ["id"], "request": {"url": "/x"}}]}, "cleanup"),
            ({
                "config": {"variables": {"old_id": 1}},
                "teststeps": [{
                    "phase": "cleanup", "requires": ["new_id"],
                    "request": {"method": "DELETE", "url": "/items/${old_id}"},
                }],
            }, "未在 requires"),
            ({
                "teststeps": [{
                    "phase": "cleanup", "requires": ["created_id"],
                    "request": {"method": "DELETE", "url": "/items/old-static-id"},
                }],
            }, "至少一个变量"),
            ({
                "teststeps": [{
                    "phase": "cleanup", "requires": ["created_id"],
                    "request": {
                        "method": "DELETE", "url": "/items/old-static-id",
                        "headers": {"Authorization": "Bearer ${created_id}"},
                    },
                }],
            }, "至少一个变量"),
        ]
        for case, expected in invalid_cases:
            with self.subTest(expected=expected), self.assertRaises(CaseContractError) as caught:
                normalize_case(case)
            self.assertIn(expected, str(caught.exception))
        explicit_main = normalize_case({
            "teststeps": [{"phase": "main", "request": {"url": "/x"}}],
        })
        self.assertEqual(explicit_main["teststeps"][0]["phase"], "main")

    def test_main_failure_runs_cleanup_in_same_session_and_skips_other_main(self):
        case = {
            "config": {"base_url": "https://api.example.test"},
            "teststeps": [
                {
                    "name": "create then fail", "request": {"method": "POST", "url": "/items"},
                    "extract": {"created_id": "body.id"},
                    "validate": [{"eq": ["status_code", 200]}],
                },
                {"name": "not sent", "request": {"url": "/later"}},
                {
                    "name": "cleanup", "phase": "cleanup", "requires": ["created_id"],
                    "request": {"method": "DELETE", "url": "/items/${created_id}"},
                    "validate": [{"eq": ["status_code", 204]}],
                },
            ],
        }
        session = FakeSession([FakeResponse(201, {"id": 42}), FakeResponse(204, {})])
        with patch("api_testing.requests_runtime.requests.Session", return_value=session):
            result = run_case("cleanup", case)
        self.assertEqual([request["method"] for request in session.requests], ["POST", "DELETE"])
        self.assertEqual([step["status"] for step in result["step_datas"]], ["failed", "skipped", "passed"])
        self.assertEqual(result["error_type"], "ValidationFailure")
        self.assertEqual(result["replay_safety"]["cleanup_status"], "completed")
        self.assertFalse(result["replay_safety"]["safe_to_retry"])
        self.assertTrue(session.closed)

    def test_config_or_runtime_id_does_not_authorize_cleanup(self):
        case = {
            "config": {"base_url": "https://api.example.test", "variables": {"old_id": 99}},
            "teststeps": [
                {"name": "read", "request": {"url": "/items"}},
                {
                    "name": "cleanup", "phase": "cleanup", "requires": ["old_id"],
                    "request": {"method": "DELETE", "url": "/items/${old_id}"},
                },
            ],
        }
        session = FakeSession([FakeResponse(200, [])])
        with patch("api_testing.requests_runtime.requests.Session", return_value=session):
            result = run_case("old-id", case, options={"variables": {"old_id": 100}})
        self.assertEqual(len(session.requests), 1)
        self.assertEqual(result["step_datas"][1]["status"], "skipped")
        self.assertEqual(result["error_type"], "CleanupUnavailable")
        self.assertEqual(result["replay_safety"]["cleanup_status"], "not_run")
        self.assertFalse(result["replay_safety"]["side_effects"])

    def test_cleanup_failure_does_not_replace_primary_failure(self):
        case = {
            "config": {"base_url": "https://api.example.test"},
            "teststeps": [
                {
                    "request": {"method": "POST", "url": "/items"},
                    "extract": {"created_id": "body.id"},
                    "validate": [{"eq": ["status_code", 200]}],
                },
                {
                    "phase": "cleanup", "requires": ["created_id"],
                    "request": {"method": "DELETE", "url": "/items/${created_id}"},
                    "validate": [{"eq": ["status_code", 204]}],
                },
            ],
        }
        session = FakeSession([FakeResponse(201, {"id": 42}), FakeResponse(500, {})])
        with patch("api_testing.requests_runtime.requests.Session", return_value=session):
            result = run_case("primary", case)
        self.assertEqual(result["error_type"], "ValidationFailure")
        self.assertIn("status_code", result["error"])
        self.assertEqual(result["step_datas"][1]["status"], "failed")
        self.assertEqual(result["replay_safety"]["cleanup_status"], "failed")

    def test_cleanup_runtime_and_export_are_consistent(self):
        case = {
            "config": {"base_url": "https://api.example.test"},
            "teststeps": [
                {
                    "request": {"method": "POST", "url": "/items"},
                    "extract": {"created_id": "body.id"},
                    "validate": [{"eq": ["status_code", 201]}],
                },
                {
                    "phase": "cleanup", "requires": ["created_id"],
                    "request": {"method": "DELETE", "url": "/items/${created_id}"},
                    "validate": [{"eq": ["status_code", 204]}],
                },
            ],
        }
        namespace = {"__name__": "exported_cleanup_case"}
        exec(compile(export_python(case), "exported_cleanup.py", "exec"), namespace)
        native_session = FakeSession([FakeResponse(201, {"id": 42}), FakeResponse(204, {})])
        with patch("api_testing.requests_runtime.requests.Session", return_value=native_session):
            native = run_case("same", case)
        exported_session = FakeSession([FakeResponse(201, {"id": 42}), FakeResponse(204, {})])
        with patch.object(namespace["requests"], "Session", return_value=exported_session):
            exported = namespace["run_case"]("same", namespace["CASE"])
        self.assertTrue(native["success"])
        self.assertEqual(native["step_datas"], exported["step_datas"])
        self.assertEqual(native["replay_safety"], exported["replay_safety"])
        self.assertEqual(native["replay_safety"]["cleanup_status"], "completed")

    def test_parent_receives_full_progress_reports_and_final_shape(self):
        progress = []
        case = {"config": {"base_url": self.base_url}, "teststeps": [
            {"name": "first", "request": {"url": "/ok"}},
            {"name": "second", "request": {"url": "/ok"}},
        ]}
        result = requests_runner(
            "progress", json.dumps(case), options={"total_timeout": 2},
            hard_timeout_seconds=2, on_progress=progress.append,
        )
        self.assertTrue(result["success"], result)
        self.assertNotIn("details", result)
        self.assertNotIn("checkpoint", result)
        self.assertGreaterEqual(len(progress), 6)
        self.assertTrue(all(item["success"] is False for item in progress))
        self.assertTrue(all(item["details"][0]["step_datas"] == item["step_datas"] for item in progress))
        self.assertEqual(progress[0]["checkpoint"], {
            "completed_steps": 0, "total_steps": 2, "current_step": 1,
        })
        first_done = next(item for item in progress if item["checkpoint"]["completed_steps"] == 1)
        self.assertEqual(first_done["checkpoint"], {
            "completed_steps": 1, "total_steps": 2, "current_step": None,
        })
        self.assertEqual([step["status"] for step in result["step_datas"]], ["passed", "passed"])
        self.assertNotIn("unknown", result["stat"]["teststeps"])
        self.assertEqual(result["replay_safety"]["cleanup_status"], "not_required")

    def test_hard_timeout_preserves_completed_step_and_marks_in_flight_unknown(self):
        case = {
            "config": {"base_url": self.base_url},
            "teststeps": [
                {
                    "name": "first", "request": {"method": "POST", "url": "/create"},
                    "extract": {"created_id": "body.id"},
                },
                {"name": "in flight", "request": {"url": "/slow"}},
                {"name": "never", "request": {"url": "/never"}},
                {
                    "name": "cleanup", "phase": "cleanup", "requires": ["created_id"],
                    "request": {"method": "DELETE", "url": "/items/${created_id}"},
                },
            ],
        }
        started = time.monotonic()
        result = requests_runner(
            "timeout", json.dumps(case), options={"total_timeout": 2}, hard_timeout_seconds=0.35,
        )
        elapsed = time.monotonic() - started
        self.assertLess(elapsed, 1.0)
        self.assertEqual(result["error_type"], "HardTimeout")
        self.assertEqual(
            [step["status"] for step in result["step_datas"]],
            ["passed", "unknown", "skipped", "skipped"],
        )
        self.assertEqual(self.server.paths[:2], [("POST", "/create"), ("GET", "/slow")])
        self.assertNotIn(("DELETE", "/items/42"), self.server.paths)
        self.assertEqual(result["replay_safety"]["cleanup_status"], "not_run")
        self.assertFalse(result["replay_safety"]["safe_to_retry"])

    def test_cancel_after_first_checkpoint_completion_sends_no_later_request(self):
        cancel = threading.Event()

        def progress(report):
            if [step["status"] for step in report["step_datas"]] == ["passed", "skipped"]:
                cancel.set()

        case = {
            "config": {"base_url": self.base_url},
            "teststeps": [
                {"name": "first", "request": {"url": "/ok"}},
                {"name": "must not send", "request": {"url": "/never"}},
            ],
        }
        result = requests_runner(
            "cancel", json.dumps(case), options={"total_timeout": 2},
            on_progress=progress, should_cancel=cancel.is_set,
        )
        self.assertEqual(result["error_type"], "Cancelled")
        self.assertEqual([step["status"] for step in result["step_datas"]], ["passed", "skipped"])
        self.assertEqual(self.server.paths, [("GET", "/ok")])

    def test_progress_persistence_error_stops_before_request_and_is_retry_safe(self):
        def fail_progress(_report):
            raise RuntimeError("database unavailable")

        case = {"config": {"base_url": self.base_url},
                "teststeps": [{"request": {"url": "/must-not-send"}}]}
        result = requests_runner(
            "evidence", json.dumps(case), hard_timeout_seconds=2,
            on_progress=fail_progress,
        )
        self.assertEqual(result["error_type"], "EvidencePersistenceError")
        self.assertIn("database unavailable", result["error"])
        self.assertEqual(result["step_datas"][0]["status"], "unknown")
        self.assertEqual(self.server.paths, [])
        self.assertTrue(result["replay_safety"]["safe_to_retry"])
        self.assertFalse(result["replay_safety"]["side_effects"])

    def test_progress_failure_after_write_dispatch_is_conservatively_unsafe(self):
        calls = 0

        def fail_on_dispatch(_report):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise RuntimeError("evidence write failed")

        case = {"config": {"base_url": self.base_url},
                "teststeps": [{"request": {"method": "POST", "url": "/must-not-send"}}]}
        result = requests_runner(
            "evidence-write", json.dumps(case), hard_timeout_seconds=2,
            on_progress=fail_on_dispatch,
        )
        self.assertEqual(result["error_type"], "EvidencePersistenceError")
        self.assertEqual(self.server.paths, [])
        self.assertTrue(result["replay_safety"]["side_effects"])
        self.assertFalse(result["replay_safety"]["safe_to_retry"])

    def test_progress_callback_consuming_deadline_receives_no_ack(self):
        def slow_progress(_report):
            time.sleep(0.35)

        case = {"config": {"base_url": self.base_url},
                "teststeps": [{"request": {"url": "/must-not-send"}}]}
        result = requests_runner(
            "slow-evidence", json.dumps(case), hard_timeout_seconds=0.3,
            on_progress=slow_progress,
        )
        self.assertEqual(result["error_type"], "HardTimeout")
        self.assertEqual(self.server.paths, [])

    def test_malformed_protocol_kills_and_reaps_worker_without_continuing(self):
        created = []
        real_popen = subprocess.Popen

        def malformed_worker(_command, **kwargs):
            command = [
                sys.executable, "-u", "-c",
                "import sys,time; sys.stdin.readline(); print('{bad', flush=True); time.sleep(5)",
            ]
            process = real_popen(command, **kwargs)
            created.append(process)
            return process

        case = {"config": {"base_url": self.base_url},
                "teststeps": [{"request": {"url": "/must-not-send"}}]}
        started = time.monotonic()
        with patch("api_testing.requests_runner.subprocess.Popen", side_effect=malformed_worker):
            result = requests_runner("malformed", json.dumps(case), hard_timeout_seconds=2)
        self.assertLess(time.monotonic() - started, 1)
        self.assertEqual(result["error_type"], "WorkerProtocolError")
        self.assertEqual(self.server.paths, [])
        self.assertIsNotNone(created[0].poll())

    def test_large_stderr_is_drained_without_deadlock(self):
        real_popen = subprocess.Popen
        worker_result = {
            "success": True, "status": "passed", "step_datas": [],
            "replay_safety": {
                "safe_to_retry": True, "reason": "test",
                "side_effects": False, "cleanup_status": "not_required",
            },
        }

        def noisy_worker(_command, **kwargs):
            source = (
                "import json,os,sys; sys.stdin.readline(); "
                "os.write(2, b'x' * 200000); "
                f"print(json.dumps({{'type':'result','report':{worker_result!r}}}), flush=True)"
            )
            return real_popen([sys.executable, "-u", "-c", source], **kwargs)

        case = {"config": {"base_url": self.base_url},
                "teststeps": [{"request": {"url": "/must-not-send"}}]}
        with patch("api_testing.requests_runner.subprocess.Popen", side_effect=noisy_worker):
            result = requests_runner("stderr", json.dumps(case), hard_timeout_seconds=2)
        self.assertTrue(result["success"])
        self.assertEqual(self.server.paths, [])

    def test_worker_process_is_reaped_after_success(self):
        created = []
        real_popen = subprocess.Popen

        def tracking_popen(*args, **kwargs):
            process = real_popen(*args, **kwargs)
            created.append(process)
            return process

        case = {"config": {"base_url": self.base_url},
                "teststeps": [{"request": {"url": "/ok"}}]}
        with patch("api_testing.requests_runner.subprocess.Popen", side_effect=tracking_popen):
            result = requests_runner("reap", json.dumps(case), hard_timeout_seconds=2)
        self.assertTrue(result["success"], result)
        self.assertEqual(len(created), 1)
        self.assertIsNotNone(created[0].poll())


if __name__ == "__main__":
    unittest.main()
