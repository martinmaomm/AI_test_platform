"""Offline regression tests for the saved-script assistant worker seam."""

import asyncio
import time
from contextlib import ExitStack
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from projects.models import Project
from .models import (
    WebUIScriptAssistant,
    WebUITestCase,
    WebUITestCaseExecutionDetail,
    WebUITestExecution,
)
from .script_assistant import (
    ScriptAssistantConflict,
    _execute_candidate,
    _outcome,
    _run_repair_rounds,
    _stream_text,
    _validate_candidate,
    directed_mcp_inspect,
    candidate_hash,
    run_script_assistant_operation,
)


SCRIPT = """from playwright.async_api import expect
async def run(page):
    await page.goto("https://example.test")
    await expect(page.locator("#status")).to_have_text("ready")
"""


class ScriptAssistantCoreTests(TestCase):
    def setUp(self):
        user = get_user_model().objects.create_user(
            username="assistant-core", password="pw"
        )
        project = Project.objects.create(
            name="assistant core", project_type="web", owner=user, created_by=user
        )
        case = WebUITestCase.objects.create(
            title="状态检查",
            description="状态",
            user=user,
            project=project,
            test_script_content=SCRIPT,
            script_status="ready",
        )
        self.session = WebUIScriptAssistant.objects.create(
            project=project,
            user=user,
            test_case=case,
            mode="repair",
            model_config_id=1,
            status="running",
            operation="repair",
            revision=1,
            task_id="assistant-core-task",
            deadline_at=timezone.now() + timedelta(minutes=2),
            source_script=SCRIPT,
            source_script_version=case.script_version,
            source_variables=[],
            source_options={},
        )

    def test_runner_success_is_not_a_pass_without_evaluation_contract(self):
        outcome = _outcome({"success": True, "result": {}})
        self.assertEqual(outcome["status"], "error")
        self.assertFalse(outcome["operation_success"])

    def test_no_mcp_trace_is_schema_valid_and_changed_locator_is_blocked(self):
        candidate = SCRIPT.replace("#status", "#new-status")
        blockers = _validate_candidate(self.session, candidate, [])
        self.assertTrue(blockers)
        self.assertNotIn("GenerationContractError", str(blockers))

    def test_runner_exception_settles_fresh_execution_and_detail(self):
        with patch(
            "web_testing.tasks._run_test_script",
            side_effect=RuntimeError("browser missing"),
        ):
            attempt = _execute_candidate(self.session, SCRIPT, [], 1)
        execution = WebUITestExecution.objects.get(pk=attempt["execution_id"])
        self.assertEqual(attempt["status"], "error")
        self.assertEqual(execution.status, "error")
        self.assertIsNotNone(execution.end_time)
        self.assertEqual(execution.case_execution_detail.status, "error")

    def test_successful_candidate_has_no_execution_error(self):
        runner_result = {
            "operation_success": True,
            "evaluation_status": "passed",
            "runtime_assertion_count": 1,
            "result": {
                "operation_success": True,
                "evaluation_status": "passed",
                "runtime_assertion_count": 1,
                "stdout": "ok",
                "stderr": "",
            },
        }
        with patch("web_testing.tasks._run_test_script", return_value=runner_result):
            attempt = _execute_candidate(self.session, SCRIPT, [], 1)
        execution = WebUITestExecution.objects.get(pk=attempt["execution_id"])
        self.assertEqual(attempt["status"], "passed")
        self.assertEqual(execution.error_message, "")
        self.assertEqual(execution.case_execution_detail.error_message, None)

    def test_non_code_second_failure_stops_before_another_model_round(self):
        first = {
            "status": "failed",
            "execution_id": 91,
            "summary": "页面访问失败",
            "has_screenshot": False,
            "runtime_assertion_count": 0,
            "diagnostics": [],
            "evidence": {"category": "network_error", "summary": "页面访问失败"},
        }
        with patch(
            "web_testing.script_assistant.LLMConfiguration.objects.get",
            return_value=SimpleNamespace(id=1),
        ), patch(
            "web_testing.script_assistant._execute_candidate", return_value=first
        ), patch(
            "web_testing.script_assistant._stream_text"
        ) as stream:
            result = _run_repair_rounds(
                self.session, 1, "assistant-core-task", SCRIPT, [], None
            )
        self.assertEqual(result["reason"], "non_code_failure")
        stream.assert_not_called()

    def test_repair_runs_at_most_two_candidates_in_order(self):
        failed = {
            "status": "failed",
            "execution_id": 101,
            "summary": "断言失败",
            "has_screenshot": False,
            "runtime_assertion_count": 1,
            "diagnostics": [],
            "evidence": {"category": "assertion_failure", "summary": "断言失败"},
        }
        passed = {
            **failed,
            "status": "passed",
            "execution_id": 102,
            "summary": "",
            "evidence": {"category": "assertion_failure", "summary": ""},
        }
        with patch(
            "web_testing.script_assistant.LLMConfiguration.objects.get",
            return_value=SimpleNamespace(id=1),
        ), patch(
            "web_testing.script_assistant._execute_candidate",
            side_effect=[failed, passed],
        ) as execute, patch(
            "web_testing.script_assistant._stream_text", return_value=SCRIPT
        ) as stream:
            result = _run_repair_rounds(
                self.session, 1, "assistant-core-task", SCRIPT, [], None
            )
        self.assertTrue(result["success"])
        self.assertEqual(execute.call_count, 2)
        stream.assert_called_once()
        self.session.refresh_from_db()
        self.assertIn("第 2 轮候选已实际验证通过。", self.session.summary)

    def test_silent_stream_honors_deadline_without_late_write(self):
        class LLM:
            def stream(self, _messages):
                time.sleep(0.5)
                yield SimpleNamespace(content="late")

        manager = SimpleNamespace(
            current_llm=LLM(),
            _ensure_initialized=lambda: None,
            _extract_stream_chunk_content=lambda chunk: chunk.content,
        )
        self.session.deadline_at = timezone.now() + timedelta(milliseconds=80)
        self.session.save(update_fields=["deadline_at"])
        with patch(
            "web_testing.script_assistant.get_llm_manager", return_value=manager
        ):
            with self.assertRaises(ScriptAssistantConflict):
                _stream_text(
                    1,
                    "test",
                    session_id=str(self.session.id),
                    revision=1,
                    task_id="assistant-core-task",
                )

    def _mcp_manager(self):
        return SimpleNamespace(current_llm=object(), _ensure_initialized=lambda: None)

    def _mcp_patches(self, client, agent, raw_config=None, remaining=10):
        raw_config = raw_config or {
            "mcpServers": {
                "playwright": {"command": "npx", "args": ["playwright-mcp-server"]},
                "filesystem": {"command": "unsafe-filesystem"},
                "database": {"command": "unsafe-database"},
            },
        }
        liveness = (
            {"side_effect": remaining}
            if isinstance(remaining, (list, tuple))
            else {"return_value": remaining}
        )
        return (
            patch(
                "web_testing.generation_preflight.resolve_active_playwright_mcp_config",
                return_value=(7, raw_config),
            ),
            patch(
                "web_testing.generation_preflight.prepare_playwright_mcp_output_config",
                return_value=raw_config,
            ),
            patch(
                "web_testing.script_assistant.get_llm_manager",
                return_value=self._mcp_manager(),
            ),
            patch(
                "mcp_use.MCPClient.from_dict",
                side_effect=lambda payload: setattr(client, "payload", payload)
                or client,
            ),
            patch("ai_core.mcp_agent_budget.BudgetedMCPAgent", agent),
            patch("web_testing.script_assistant._remaining", **liveness),
            patch("web_testing.script_assistant._still_current", return_value=True),
        )

    def test_directed_mcp_uses_only_playwright_and_callback_locator_evidence(self):
        class Client:
            closed = False

            async def create_all_sessions(self):
                pass

            async def close_all_sessions(self):
                self.closed = True

        class Agent:
            created = None

            def __init__(self, **kwargs):
                type(self).created = kwargs
                self.guard = kwargs["callbacks"][0]

            async def initialize(self):
                pass

            async def run(self, _prompt, **_kwargs):
                self.guard.on_tool_start(
                    {"name": "playwright_click"},
                    "",
                    run_id="locator",
                    inputs={"selector": "#new-status"},
                )
                self.guard.on_tool_end("ok", run_id="locator")
                return "模型臆测 #invented-selector；该文本不是 callback 证据。"

        client = Client()
        with ExitStack() as stack:
            for item in self._mcp_patches(client, Agent):
                stack.enter_context(item)
            text, trace = directed_mcp_inspect(
                self.session, "assistant-core-task", {"category": "action_timeout"}, []
            )
        self.assertTrue(client.closed)
        self.assertEqual(Agent.created["client"] is client, True)
        self.assertEqual(set(client.payload["mcpServers"]), {"playwright"})
        self.assertIn("#invented-selector", text)
        self.assertEqual(len(trace["events"]), 1)
        self.assertEqual(trace["events"][0]["locator_input"]["selector"], "#new-status")
        self.session._directed_mcp_snapshot = trace
        # A regular action and an expect matcher both accept only the locator
        # observed by the real successful callback above.
        action_source = """async def run(page):
    await page.locator("#status").click()
"""
        self.session.source_script = action_source
        self.assertEqual(
            _validate_candidate(
                self.session, action_source.replace("#status", "#new-status"), []
            ),
            [],
        )
        self.assertTrue(
            _validate_candidate(
                self.session,
                action_source.replace("#status", "#invented-selector"),
                [],
            )
        )
        self.session.source_script = SCRIPT
        self.assertEqual(
            _validate_candidate(
                self.session, SCRIPT.replace("#status", "#new-status"), []
            ),
            [],
        )
        invented_blockers = _validate_candidate(
            self.session,
            SCRIPT.replace("#status", "#invented-selector"),
            [],
        )
        self.assertTrue(invented_blockers)
        self.assertTrue(
            any(item["code"] == "REPAIR_SCOPE_CHANGED" for item in invented_blockers)
        )
        expected_changed = _validate_candidate(
            self.session,
            SCRIPT.replace("#status", "#new-status").replace('"ready"', '"changed"'),
            [],
        )
        self.assertTrue(
            any(
                item["code"] == "ASSERTION_SEMANTICS_CHANGED"
                for item in expected_changed
            )
        )

    def test_missing_mcp_returns_empty_evidence_without_running_a_client(self):
        with patch(
            "web_testing.generation_preflight.resolve_active_playwright_mcp_config",
            return_value=None,
        ), patch("mcp_use.MCPClient.from_dict") as client:
            text, trace = directed_mcp_inspect(
                self.session, "assistant-core-task", {"category": "action_timeout"}, []
            )
        self.assertEqual(trace, {})
        self.assertIn("未配置可用 MCP", text)
        client.assert_not_called()

    def test_deadline_cancels_agent_run_and_closes_mcp_client(self):
        class Client:
            closed = False

            async def create_all_sessions(self):
                pass

            async def close_all_sessions(self):
                self.closed = True

        class Agent:
            initialized = False

            def __init__(self, **kwargs):
                pass

            async def initialize(self):
                type(self).initialized = True

            async def run(self, _prompt, **_kwargs):
                await asyncio.sleep(0.5)

        client = Client()
        self.session.deadline_at = timezone.now() + timedelta(milliseconds=60)
        self.session.save(update_fields=["deadline_at"])
        with ExitStack() as stack:
            for item in self._mcp_patches(client, Agent, remaining=[10, 10, 0]):
                stack.enter_context(item)
            with self.assertRaises(ScriptAssistantConflict):
                directed_mcp_inspect(
                    self.session,
                    "assistant-core-task",
                    {"category": "action_timeout"},
                    [],
                )
        self.assertTrue(Agent.initialized)
        self.assertTrue(client.closed)

    def _queue_current_session(self, *, mode="edit", task_id="assistant-terminal-task"):
        self.session.mode = mode
        self.session.status = "queued"
        self.session.operation = mode
        self.session.revision += 1
        self.session.task_id = task_id
        self.session.deadline_at = timezone.now() + timedelta(minutes=2)
        self.session.pending_script = SCRIPT
        self.session.pending_message = "调整脚本"
        self.session.save()
        return self.session.revision, task_id

    def test_oversized_stream_becomes_current_session_failure(self):
        revision, task_id = self._queue_current_session()

        class LLM:
            def stream(self, _messages):
                yield SimpleNamespace(content="x" * 200001, response_metadata={})

        manager = SimpleNamespace(
            current_llm=LLM(),
            _ensure_initialized=lambda: None,
            _extract_stream_chunk_content=lambda chunk: chunk.content,
        )
        with patch(
            "web_testing.script_assistant.LLMConfiguration.objects.get",
            return_value=SimpleNamespace(id=1),
        ), patch("web_testing.script_assistant.get_llm_manager", return_value=manager):
            result = run_script_assistant_operation(
                str(self.session.id), revision, task_id
            )
        self.session.refresh_from_db()
        self.assertFalse(result["success"])
        self.assertEqual(self.session.status, "failed")
        self.assertIn("超过候选脚本大小限制", self.session.message)

    def test_missing_repair_snapshot_becomes_current_session_failure(self):
        source_execution = WebUITestExecution.objects.create(
            exec_type="case",
            name="旧失败执行",
            executor=self.session.user,
            project=self.session.project,
            status="failed",
        )
        self.session.execution = source_execution
        self.session.save(update_fields=["execution"])
        revision, task_id = self._queue_current_session(mode="repair")
        with patch(
            "web_testing.script_assistant.LLMConfiguration.objects.get",
            return_value=SimpleNamespace(id=1),
        ):
            result = run_script_assistant_operation(
                str(self.session.id), revision, task_id
            )
        self.session.refresh_from_db()
        self.assertFalse(result["success"])
        self.assertEqual(self.session.status, "failed")
        self.assertIn("执行详情缺失", self.session.message)

    def test_explicit_stream_finish_reason_rejects_partial_candidate(self):
        class LLM:
            def stream(self, _messages):
                yield SimpleNamespace(
                    content=SCRIPT, response_metadata={"finish_reason": "length"}
                )

        manager = SimpleNamespace(
            current_llm=LLM(),
            _ensure_initialized=lambda: None,
            _extract_stream_chunk_content=lambda chunk: chunk.content,
        )
        with patch(
            "web_testing.script_assistant.get_llm_manager", return_value=manager
        ):
            with self.assertRaisesRegex(ScriptAssistantConflict, "被截断或内容过滤"):
                _stream_text(
                    1,
                    "test",
                    session_id=str(self.session.id),
                    revision=1,
                    task_id="assistant-core-task",
                )

    def test_second_round_stream_failure_preserves_first_round_execution(self):
        source_execution = WebUITestExecution.objects.create(
            exec_type="case",
            name="来源失败执行",
            executor=self.session.user,
            project=self.session.project,
            status="failed",
        )
        WebUITestCaseExecutionDetail.objects.create(
            execution=source_execution,
            test_case=self.session.test_case,
            status="failed",
            log="Locator.click: Timeout 1000ms exceeded.",
        )
        self.session.execution = source_execution
        self.session.save(update_fields=["execution"])
        revision, task_id = self._queue_current_session(mode="repair")
        first = {
            "status": "failed",
            "execution_id": 701,
            "summary": "页面校验未通过",
            "has_screenshot": True,
            "runtime_assertion_count": 1,
            "diagnostics": [],
            "evidence": {"category": "assertion_failure", "summary": "页面校验未通过"},
        }
        with patch(
            "web_testing.script_assistant.LLMConfiguration.objects.get",
            return_value=SimpleNamespace(id=1),
        ), patch(
            "web_testing.script_assistant.directed_mcp_inspect",
            return_value=("无额外定位器证据", {}),
        ), patch(
            "web_testing.script_assistant._stream_text",
            side_effect=[
                SCRIPT,
                ScriptAssistantConflict(
                    "模型响应被截断或内容过滤，未生成完整候选脚本。"
                ),
            ],
        ), patch(
            "web_testing.script_assistant._execute_candidate", return_value=first
        ):
            result = run_script_assistant_operation(
                str(self.session.id), revision, task_id
            )
        self.session.refresh_from_db()
        self.assertFalse(result["success"])
        self.assertEqual(self.session.status, "failed")
        self.assertEqual(self.session.candidate_hash, candidate_hash(SCRIPT))
        self.assertEqual(self.session.attempts[0]["execution_id"], 701)
        self.assertEqual(self.session.verification["execution_id"], 701)
