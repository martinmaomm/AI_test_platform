"""Offline unit coverage for the opt-in live-generation acceptance CLI."""
from __future__ import annotations

import importlib.util
import io
import json
from pathlib import Path
from unittest.mock import Mock, patch
from contextlib import redirect_stderr

from django.test import SimpleTestCase


SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "verify_webui_generation_live.py"
SPEC = importlib.util.spec_from_file_location("verify_webui_generation_live", SCRIPT)
live = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(live)


def generation(*, status="ready", verification=None, script="assert 1", workspace_revision=4, generation_revision=91):
    return {
        "status": status,
        "current_stage": "completed",
        "progress": 100,
        # This is the lifecycle revision, deliberately distinct from the draft workspace revision.
        "revision": generation_revision,
        "script_draft": script,
        "quality_report": {"status": "ready", "completion": "complete"},
        "exploration_snapshot": {"artifact": {"remaining_steps": []}},
        "workspace": {
            "revision": workspace_revision,
            "verification": verification or {"status": "unverified"},
            "repair": {"status": "idle"},
        },
    }


class _Headers:
    def __init__(self, content_type="application/json"):
        self.content_type = content_type

    def get_content_type(self):
        return self.content_type


class _Response:
    status = 200

    def __init__(self, body, content_type="application/json"):
        self.body = body
        self.headers = _Headers(content_type)

    def read(self):
        return self.body

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


class VerifyWebUIGenerationLiveTests(SimpleTestCase):
    def test_error_codes_keep_lowercase_runtime_reasons_not_free_text(self):
        self.assertEqual(live.safe_error_code('tool_budget'), 'tool_budget')
        self.assertEqual(live.safe_error_code('MODEL_OUTPUT_INVALID'), 'MODEL_OUTPUT_INVALID')
        self.assertEqual(live.safe_error_code('failed at https://example.test/?token=secret'), '')

    def test_default_mode_never_initializes_django_or_http(self):
        generation_id = "894a3fd5-0802-43f1-a28d-87aa57bb875b"
        with patch.object(live, "configure_django", side_effect=AssertionError("must not access DB")), patch.object(
            live, "loopback_open", side_effect=AssertionError("must not call HTTP")
        ), patch.object(
            live, "write_summary", side_effect=AssertionError("must not write artifacts")
        ):
            self.assertEqual(live.main(["--generation-id", generation_id]), 0)

    def test_loopback_validation_rejects_non_loopback_and_credentials(self):
        self.assertEqual(live.validate_loopback_base_url("http://localhost:8000"), "http://localhost:8000")
        for value in ("https://example.test", "http://user:pass@127.0.0.1:8000", "http://127.0.0.1:8000/api"):
            with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                # argparse's type contract is exercised exactly as the CLI invokes it.
                live.build_parser().parse_args(["--generation-id", "894a3fd5-0802-43f1-a28d-87aa57bb875b", "--base-url", value])

    def test_live_resolution_rejects_a_non_loopback_result(self):
        with patch.object(live.socket, "getaddrinfo", return_value=[(None, None, None, None, ("203.0.113.10", 0))]):
            with self.assertRaisesRegex(live.LiveAcceptanceError, "BASE_URL_NOT_LOOPBACK"):
                live.ensure_resolved_loopback("http://localhost:8000")

    def test_terminal_poll_reports_progress_without_sleeping_after_ready(self):
        fetch = Mock(return_value=generation())
        progress = Mock()
        result = live.wait_for_terminal_generation(fetch, timeout_seconds=10, poll_seconds=2, sleep=Mock(), monotonic=lambda: 0, progress=progress)
        self.assertEqual(result["status"], "ready")
        progress.assert_called_once_with({"event": "generation", "status": "ready", "stage": "completed", "progress": 100})

    def test_debug_completion_requires_stable_hash_and_runtime_assertions(self):
        digest = live.script_digest("assert 1")
        completed = generation(verification={
            "status": "passed", "execution_id": 23, "locked_revision": 4,
            "script_hash": digest, "runtime_assertion_count": 2,
        })
        self.assertEqual(live.validate_debug_completion(completed, execution_id=23, revision=4, digest=digest)["runtime_assertion_count"], 2)
        completed["workspace"]["verification"]["runtime_assertion_count"] = 0
        with self.assertRaisesRegex(live.LiveAcceptanceError, "RUNTIME_ASSERTIONS_MISSING"):
            live.validate_debug_completion(completed, execution_id=23, revision=4, digest=digest)

    def test_debug_payload_uses_workspace_revision_not_lifecycle_revision(self):
        data = generation(workspace_revision=4, generation_revision=91)
        revision, _digest = live.ensure_eligible_draft(data)
        self.assertEqual(revision, 4)

    def test_eligible_draft_rejects_partial_quality_or_remaining_artifact_steps(self):
        partial = generation()
        partial["quality_report"]["completion"] = "partial"
        with self.assertRaisesRegex(live.LiveAcceptanceError, "DRAFT_QUALITY_NOT_ELIGIBLE"):
            live.ensure_eligible_draft(partial)
        remaining = generation()
        remaining["exploration_snapshot"]["artifact"]["remaining_steps"] = ["unverified-step"]
        with self.assertRaisesRegex(live.LiveAcceptanceError, "ARTIFACT_REMAINING_STEPS"):
            live.ensure_eligible_draft(remaining)

    def test_terminal_evidence_keeps_only_safe_selected_fields(self):
        evidence = live.terminal_evidence({
            "status": "needs_review", "error_code": "MODEL_CALL_LIMIT_EXCEEDED",
            "error_message": "must never enter the artifact", "script_draft": "must never enter the artifact",
            "model_info": {"provider_name": "fixture-provider", "model_name": "fixture-model", "config_id": 5, "api_key": "hidden"},
            "tool_stats": {"model_calls": 60, "total_tool_calls": 120, "failed_tool_calls": 7, "last_operation": "hidden"},
        })
        self.assertEqual(evidence, {
            "generation_status": "needs_review", "generation_error_code": "MODEL_CALL_LIMIT_EXCEEDED",
            "model_info": {"provider_name": "fixture-provider", "model_name": "fixture-model", "config_id": 5},
            "calls": {"model_calls": 60, "browser_calls": 120, "failed_calls": 7},
        })

    def test_token_provider_refreshes_before_the_ten_minute_expiry(self):
        now = Mock(side_effect=[0, 0, 481, 481])
        mint = Mock(side_effect=["first-token", "refreshed-token"])
        provider = live.ShortLivedTokenProvider(object(), mint=mint, now=now, refresh_after_seconds=480)
        self.assertEqual(provider(), "first-token")
        self.assertEqual(provider(), "first-token")
        self.assertEqual(provider(), "refreshed-token")
        self.assertEqual(mint.call_count, 2)

    def test_failed_debug_run_retains_execution_id_and_safe_code(self):
        run = {"run": 1, "execution_id": 23, "status": "started"}
        live.mark_debug_run_failed(run, live.LiveAcceptanceError("EXECUTION_SCREENSHOT_MISSING"))
        self.assertEqual(run, {
            "run": 1, "execution_id": 23, "status": "failed",
            "failure_code": "EXECUTION_SCREENSHOT_MISSING",
        })

    def test_execution_artifacts_require_log_and_png(self):
        self.assertTrue(live.validate_execution_artifacts({"status": "passed", "log": "runner log"}, b"\x89PNG\r\n\x1a\nbody")["screenshot_present"])
        with self.assertRaisesRegex(live.LiveAcceptanceError, "EXECUTION_SCREENSHOT_MISSING"):
            live.validate_execution_artifacts({"status": "passed", "log": "runner log"}, b"not-png")

    def test_http_mock_sends_jwt_only_to_the_validated_origin(self):
        opened = []

        def opener(request, timeout):
            opened.append((request.full_url, request.get_header("Authorization"), request.data, timeout))
            return _Response(json.dumps({"success": True, "data": {}}).encode())

        client = live.JsonHttpClient("http://127.0.0.1:8000", lambda: "short-lived-token", opener=opener)
        self.assertEqual(client.post_json("/api/check/", {"confirm_execution": True}), {"success": True, "data": {}})
        self.assertEqual(opened, [("http://127.0.0.1:8000/api/check/", "Bearer short-lived-token", b'{"confirm_execution":true}', 30)])
