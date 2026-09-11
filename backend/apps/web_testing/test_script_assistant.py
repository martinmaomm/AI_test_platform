"""Offline API and execution-snapshot contract coverage.

The asynchronous core is patched only at the API dispatch seam.
"""

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from ai_core.models import LLMConfiguration, ModelType
from projects.models import Project, ProjectMember
from .constants import normalize_webui_execution_options
from .execution_snapshots import capture_suite_snapshot
from .models import (
    WebUIScriptAssistant,
    WebUITestCase,
    WebUITestCaseExecutionDetail,
    WebUITestExecution,
    WebUITestSuite,
)
from .script_assistant import _persist, candidate_hash, case_edit_version
from .script_assistant_views import (
    ScriptAssistantApplyView,
    ScriptAssistantCancelView,
    ScriptAssistantDetailView,
    ScriptAssistantListCreateView,
    ScriptAssistantMessageView,
    ScriptAssistantResetView,
    ScriptAssistantVerifyView,
)
from .views import ExecuteWebUITestCaseView, WebUITestCaseRetrieveUpdateDestroyView


SCRIPT = """from playwright.async_api import expect

async def run(page):
    await page.goto("https://example.test/")
    await expect(page).to_have_title("Home")
"""


class ScriptAssistantApiTests(TestCase):
    def setUp(self):
        self.factory = APIRequestFactory()
        self.user = get_user_model().objects.create_user(username="assistant-owner")
        self.project = Project.objects.create(
            name="assistant", project_type="web", owner=self.user, created_by=self.user
        )
        self.model = LLMConfiguration.objects.create(
            model_type=ModelType.LLM,
            provider="openai",
            provider_name="Fixture provider",
            is_active=True,
            api_key="key",
            base_url="https://llm.example.test",
            model_name="fixture",
            created_by=self.user,
        )
        self.case = WebUITestCase.objects.create(
            title="case",
            description="repair",
            project=self.project,
            user=self.user,
            test_script_content=SCRIPT,
            script_status="ready",
            script_version=1,
            variables=[{"name": "CASE_VALUE", "value": "case", "required": False}],
        )

    def _post(self, view, payload, *, session_id=None, user=None, project_id=None):
        request = self.factory.post("/assistant/", payload, format="json")
        force_authenticate(request, user=user or self.user)
        kwargs = {"project_id": project_id or self.project.id}
        if session_id is not None:
            kwargs["session_id"] = session_id
        return view.as_view()(request, **kwargs)

    def _get(self, view, query="", *, session_id=None, user=None, project_id=None):
        request = self.factory.get(f"/assistant/?{query}")
        force_authenticate(request, user=user or self.user)
        kwargs = {"project_id": project_id or self.project.id}
        if session_id is not None:
            kwargs["session_id"] = session_id
        return view.as_view()(request, **kwargs)

    def _failed_case_execution(self, *, runtime_names=None, options=None):
        execution = WebUITestExecution.objects.create(
            exec_type="case",
            name="failed",
            executor=self.user,
            project=self.project,
            status="failed",
        )
        WebUITestCaseExecutionDetail.objects.create(
            execution=execution,
            test_case=self.case,
            status="failed",
            source_script=SCRIPT,
            source_script_version=self.case.script_version,
            source_edit_version=case_edit_version(self.case),
            source_variables=self.case.variables,
            runtime_variable_names=runtime_names or [],
            execution_options=options
            or normalize_webui_execution_options({"headed": False}),
        )
        return execution

    def _repair_payload(self, execution, **extra):
        return {
            "mode": "repair",
            "model_config_id": self.model.id,
            "execution_id": execution.id,
            "confirm_execution": True,
            "runtime_variables": [],
            **extra,
        }

    def _create_edit(self):
        payload = {
            "mode": "edit",
            "model_config_id": self.model.id,
            "test_case_id": self.case.id,
            "expected_edit_version": case_edit_version(self.case),
            "script_content": SCRIPT + " \n",
            "description": "current editor",
            "variables": self.case.variables,
            "message": "add a wait",
        }
        with patch("web_testing.script_assistant_views._dispatch") as dispatch:
            response = self._post(ScriptAssistantListCreateView, payload)
        self.assertEqual(response.status_code, 202, response.data)
        dispatch.assert_called_once()
        return WebUIScriptAssistant.objects.get(pk=response.data["data"]["id"])

    def test_edit_create_locks_active_model_and_preserves_editor_whitespace(self):
        session = self._create_edit()
        self.assertEqual(session.status, "queued")
        self.assertEqual(session.model_info["provider_name"], "Fixture provider")
        self.assertTrue(session.task_id)
        self.assertIsNotNone(session.deadline_at)
        self.assertEqual(session.source_script, SCRIPT + " \n")
        self.assertEqual(session.source_edit_version, case_edit_version(self.case))

    def test_reset_clears_conversation_durably_without_modifying_case_or_execution(
        self,
    ):
        session = self._create_edit()
        execution = self._failed_case_execution()
        session.status = "candidate_ready"
        session.candidate_script = SCRIPT
        session.candidate_hash = candidate_hash(SCRIPT)
        session.candidate_diff = "old diff"
        session.summary = "old summary"
        session.blockers = [{"message": "old blocker"}]
        session.quality_report = {"old": True}
        session.attempts = [{"execution_id": execution.id}]
        session.verification = {"execution_id": execution.id, "status": "failed"}
        session.pending_use_candidate = True
        session.save()
        old_revision, old_task = session.revision, session.task_id
        before_case = case_edit_version(self.case)
        with patch("web_testing.script_assistant_views._dispatch") as dispatch:
            result = self._post(
                ScriptAssistantResetView,
                {"expected_revision": session.revision},
                session_id=session.id,
            )
        self.assertEqual(result.status_code, 200, result.data)
        dispatch.assert_not_called()
        session.refresh_from_db()
        self.assertEqual(session.status, "idle")
        self.assertEqual(session.revision, old_revision + 1)
        for field in (
            "source_script",
            "source_edit_version",
            "candidate_script",
            "candidate_hash",
            "candidate_diff",
            "summary",
            "blockers",
            "quality_report",
            "messages",
            "attempts",
            "verification",
            "source_variables",
            "source_options",
            "pending_script",
            "pending_description",
            "pending_variables",
            "pending_message",
            "pending_use_candidate",
            "task_id",
            "deadline_at",
            "cancel_requested_at",
        ):
            self.assertFalse(getattr(session, field), field)
        self.assertEqual(session.model_config_id, self.model.id)
        self.assertFalse(
            _persist(session.id, old_revision, old_task, candidate_script="late")
        )
        restored = self._get(
            ScriptAssistantListCreateView, f"mode=edit&test_case_id={self.case.id}"
        ).data["data"]
        self.assertEqual(len(restored), 1)
        self.assertEqual(restored[0]["id"], str(session.id))
        self.assertEqual(restored[0]["messages"], [])
        self.case.refresh_from_db()
        self.assertEqual(case_edit_version(self.case), before_case)
        execution.refresh_from_db()
        self.assertEqual(execution.status, "failed")
        self.assertEqual(WebUITestExecution.objects.count(), 1)

    def test_reset_rejects_running_and_stale_requests_without_clearing(self):
        session = self._create_edit()
        original_messages = session.messages
        for status in ("queued", "running", "candidate_ready"):
            with self.subTest(status=status):
                session.status = status
                session.save(update_fields=["status"])
                result = self._post(
                    ScriptAssistantResetView,
                    {
                        "expected_revision": session.revision
                        + (status == "candidate_ready")
                    },
                    session_id=session.id,
                )
                self.assertEqual(result.status_code, 409, result.data)
                session.refresh_from_db()
                self.assertEqual(session.messages, original_messages)

    def test_reset_does_not_accept_repair_or_other_user_sessions(self):
        session = self._create_edit()
        other = get_user_model().objects.create_user(
            username="reset-other", email="reset-other@example.test"
        )
        ProjectMember.objects.create(
            project=self.project, user=other, role="editor", can_edit=True
        )
        result = self._post(
            ScriptAssistantResetView,
            {"expected_revision": session.revision},
            session_id=session.id,
            user=other,
        )
        self.assertEqual(result.status_code, 404)
        session.mode, session.status = "repair", "candidate_ready"
        session.save(update_fields=["mode", "status"])
        result = self._post(
            ScriptAssistantResetView,
            {"expected_revision": session.revision},
            session_id=session.id,
        )
        self.assertEqual(result.status_code, 409)
        session.refresh_from_db()
        self.assertTrue(session.messages)

    def test_edit_has_single_conversation_and_new_message_after_reset_uses_same_id(
        self,
    ):
        session = self._create_edit()
        payload = {
            "mode": "edit",
            "model_config_id": self.model.id,
            "test_case_id": self.case.id,
            "expected_edit_version": case_edit_version(self.case),
            "script_content": SCRIPT,
            "message": "new",
            "variables": [],
        }
        with patch("web_testing.script_assistant_views._dispatch") as dispatch:
            duplicate = self._post(ScriptAssistantListCreateView, payload)
        self.assertEqual(duplicate.status_code, 409, duplicate.data)
        dispatch.assert_not_called()
        session.status = "cancelled"
        session.save(update_fields=["status"])
        result = self._post(
            ScriptAssistantResetView,
            {"expected_revision": session.revision},
            session_id=session.id,
        )
        self.assertEqual(result.status_code, 200, result.data)
        session.refresh_from_db()
        with patch("web_testing.script_assistant_views._dispatch"):
            sent = self._post(
                ScriptAssistantMessageView,
                {
                    **payload,
                    "expected_revision": session.revision,
                    "use_candidate": False,
                },
                session_id=session.id,
            )
        self.assertEqual(sent.status_code, 202, sent.data)
        session.refresh_from_db()
        self.assertEqual([entry["content"] for entry in session.messages], ["new"])
        self.assertEqual(session.source_script, SCRIPT)
        self.assertEqual(session.source_edit_version, case_edit_version(self.case))
        self.assertEqual(WebUIScriptAssistant.objects.filter(mode="edit").count(), 1)

    def test_case_edit_list_returns_only_current_conversation(self):
        old = self._create_edit()
        current = WebUIScriptAssistant.objects.create(
            project=self.project,
            user=self.user,
            test_case=self.case,
            mode="edit",
            model_config_id=self.model.id,
            status="idle",
            messages=[],
        )
        result = self._get(
            ScriptAssistantListCreateView, f"mode=edit&test_case_id={self.case.id}"
        )
        self.assertEqual(result.status_code, 200, result.data)
        self.assertEqual([row["id"] for row in result.data["data"]], [str(current.id)])
        # No destructive migration of older records, and repair lists retain
        # their existing behavior. The case editor exposes just one dialogue.
        self.assertTrue(WebUIScriptAssistant.objects.filter(pk=old.id).exists())

    def test_next_message_switches_model_atomically_not_during_running_task(self):
        session = self._create_edit()
        replacement = LLMConfiguration.objects.create(
            model_type=ModelType.LLM,
            provider="openai",
            provider_name="Next provider",
            model_name="next-model",
            api_key="offline-only",
            is_active=True,
            created_by=self.user,
        )
        payload = {
            "expected_revision": session.revision,
            "model_config_id": replacement.id,
            "expected_edit_version": case_edit_version(self.case),
            "script_content": SCRIPT,
            "message": "use new model",
            "use_candidate": False,
        }
        with patch("web_testing.script_assistant_views._dispatch") as dispatch:
            busy = self._post(
                ScriptAssistantMessageView, payload, session_id=session.id
            )
            self.assertEqual(busy.status_code, 409, busy.data)
            dispatch.assert_not_called()
        session.refresh_from_db()
        self.assertEqual(session.model_config_id, self.model.id)
        session.status = "candidate_ready"
        session.save(update_fields=["status"])
        with patch("web_testing.script_assistant_views._dispatch") as dispatch:
            result = self._post(
                ScriptAssistantMessageView, payload, session_id=session.id
            )
        self.assertEqual(result.status_code, 202, result.data)
        dispatch.assert_called_once()
        session.refresh_from_db()
        self.assertEqual(session.model_config_id, replacement.id)
        self.assertEqual(session.model_info["provider_name"], "Next provider")
        self.assertEqual(session.messages[-1]["content"], "use new model")

    def test_message_rejects_disabled_foreign_or_missing_model_without_mutation(self):
        session = self._create_edit()
        session.status = "candidate_ready"
        session.save(update_fields=["status"])
        before = (session.revision, session.model_config_id, session.messages)
        other = get_user_model().objects.create_user(
            username="model-other", email="model-other@example.test"
        )
        foreign = LLMConfiguration.objects.create(
            model_type=ModelType.LLM,
            provider="openai",
            model_name="foreign",
            api_key="offline-only",
            is_active=True,
            created_by=other,
        )
        self.model.is_active = False
        self.model.save(update_fields=["is_active"])
        for config_id in (self.model.id, foreign.id, 999999):
            with self.subTest(config_id=config_id), patch(
                "web_testing.script_assistant_views._dispatch"
            ) as dispatch:
                result = self._post(
                    ScriptAssistantMessageView,
                    {
                        "expected_revision": session.revision,
                        "model_config_id": config_id,
                        "script_content": SCRIPT,
                        "message": "next",
                        "use_candidate": False,
                    },
                    session_id=session.id,
                )
                self.assertEqual(result.status_code, 400, result.data)
                dispatch.assert_not_called()
                session.refresh_from_db()
                self.assertEqual(
                    (session.revision, session.model_config_id, session.messages),
                    before,
                )

    def test_message_revision_conflict_does_not_mutate_pending_payload(self):
        session = self._create_edit()
        session.status = "candidate_ready"
        session.save(update_fields=["status"])
        before = (session.pending_script, list(session.messages))
        response = self._post(
            ScriptAssistantMessageView,
            {
                "expected_revision": session.revision + 1,
                "script_content": SCRIPT + "# manual\n",
                "description": "x",
                "variables": [],
                "message": "next",
                "use_candidate": False,
            },
            session_id=session.id,
        )
        self.assertEqual(response.status_code, 409, response.data)
        session.refresh_from_db()
        self.assertEqual((session.pending_script, session.messages), before)

    def test_edit_message_uses_current_editor_as_baseline_unless_candidate_selected(
        self,
    ):
        session = self._create_edit()
        session.status = "candidate_ready"
        session.candidate_script = SCRIPT + "# candidate\n"
        session.candidate_hash = candidate_hash(session.candidate_script)
        original_edit_version = session.source_edit_version
        session.save(update_fields=["status", "candidate_script", "candidate_hash"])
        current_script = SCRIPT + "# current editor  \n"
        current_variables = [{"name": "CURRENT_VALUE", "value": "current"}]
        with patch("web_testing.script_assistant_views._dispatch"):
            response = self._post(
                ScriptAssistantMessageView,
                {
                    "expected_revision": session.revision,
                    "script_content": current_script,
                    "description": "current",
                    "variables": current_variables,
                    "message": "continue from editor",
                    "use_candidate": False,
                },
                session_id=session.id,
            )
        self.assertEqual(response.status_code, 202, response.data)
        session.refresh_from_db()
        self.assertEqual(session.source_script, current_script)
        self.assertEqual(
            session.source_variables,
            [
                {
                    "name": "CURRENT_VALUE",
                    "value": "current",
                    "is_secret": False,
                    "required": False,
                    "description": "",
                }
            ],
        )
        self.assertEqual(session.source_edit_version, original_edit_version)

        session.status = "candidate_ready"
        session.save(update_fields=["status"])
        preserved_source, preserved_variables = (
            session.source_script,
            session.source_variables,
        )
        with patch("web_testing.script_assistant_views._dispatch"):
            response = self._post(
                ScriptAssistantMessageView,
                {
                    "expected_revision": session.revision,
                    "script_content": SCRIPT + "# different UI\n",
                    "description": "current",
                    "variables": [],
                    "message": "continue from candidate",
                    "use_candidate": True,
                },
                session_id=session.id,
            )
        self.assertEqual(response.status_code, 202, response.data)
        session.refresh_from_db()
        self.assertEqual(session.source_script, preserved_source)
        self.assertEqual(session.source_variables, preserved_variables)

    def test_edit_message_cannot_select_a_missing_candidate(self):
        session = self._create_edit()
        session.status = "idle"
        session.save(update_fields=["status"])
        response = self._post(
            ScriptAssistantMessageView,
            {
                "expected_revision": session.revision,
                "script_content": SCRIPT,
                "description": "current",
                "variables": [],
                "message": "use it",
                "use_candidate": True,
            },
            session_id=session.id,
        )
        self.assertEqual(response.status_code, 409, response.data)

    def test_cancel_is_revision_task_cas_and_transport_failure_stays_successful(self):
        session = self._create_edit()
        conflict = self._post(
            ScriptAssistantCancelView,
            {"expected_revision": session.revision + 1},
            session_id=session.id,
        )
        self.assertEqual(conflict.status_code, 409, conflict.data)
        with patch(
            "web_testing.script_assistant_views.AsyncResult.revoke",
            side_effect=RuntimeError("broker gone"),
        ):
            response = self._post(
                ScriptAssistantCancelView,
                {"expected_revision": session.revision},
                session_id=session.id,
            )
        self.assertEqual(response.status_code, 200, response.data)
        session.refresh_from_db()
        self.assertEqual(session.status, "cancelled")

    def test_verify_requires_confirmation_and_current_hash(self):
        session = self._create_edit()
        session.status, session.candidate_script, session.candidate_hash = (
            "candidate_ready",
            SCRIPT,
            candidate_hash(SCRIPT),
        )
        session.save(update_fields=["status", "candidate_script", "candidate_hash"])
        response = self._post(
            ScriptAssistantVerifyView,
            {
                "expected_revision": session.revision,
                "candidate_hash": session.candidate_hash,
                "confirm_execution": False,
                "runtime_variables": [],
            },
            session_id=session.id,
        )
        self.assertEqual(response.status_code, 400, response.data)

    def test_normal_case_patch_rejects_stale_edit_version_without_writing(self):
        request = self.factory.patch(
            "/case/",
            {"title": "changed", "expected_edit_version": "stale"},
            format="json",
        )
        force_authenticate(request, user=self.user)
        response = WebUITestCaseRetrieveUpdateDestroyView.as_view()(
            request, project_id=self.project.id, pk=self.case.id
        )
        self.assertEqual(response.status_code, 409, response.data)
        self.case.refresh_from_db()
        self.assertEqual(self.case.title, "case")

    def test_missing_snapshot_repair_returns_conflict_without_dispatch(self):
        execution = WebUITestExecution.objects.create(
            exec_type="case",
            name="old",
            executor=self.user,
            project=self.project,
            status="failed",
        )
        WebUITestCaseExecutionDetail.objects.create(
            execution=execution, test_case=self.case, status="failed"
        )
        payload = {
            "mode": "repair",
            "model_config_id": self.model.id,
            "execution_id": execution.id,
            "confirm_execution": True,
            "runtime_variables": [],
        }
        with patch("web_testing.script_assistant_views._dispatch") as dispatch:
            response = self._post(ScriptAssistantListCreateView, payload)
        self.assertEqual(response.status_code, 409, response.data)
        dispatch.assert_not_called()

    def test_repair_requires_edit_version_snapshot_and_rejects_variable_drift(self):
        execution = self._failed_case_execution()
        detail = execution.case_execution_detail
        detail.source_edit_version = ""
        detail.save(update_fields=["source_edit_version"])
        with patch("web_testing.script_assistant_views._dispatch") as dispatch:
            response = self._post(
                ScriptAssistantListCreateView, self._repair_payload(execution)
            )
        self.assertEqual(response.status_code, 409, response.data)
        dispatch.assert_not_called()

        detail.source_edit_version = case_edit_version(self.case)
        detail.save(update_fields=["source_edit_version"])
        self.case.variables = [{"name": "CASE_VALUE", "value": "new"}]
        self.case.save(update_fields=["variables"])
        with patch("web_testing.script_assistant_views._dispatch") as dispatch:
            response = self._post(
                ScriptAssistantListCreateView, self._repair_payload(execution)
            )
        self.assertEqual(response.status_code, 409, response.data)
        dispatch.assert_not_called()

    def test_repair_reenters_runtime_names_and_keeps_values_out_of_session(self):
        execution = self._failed_case_execution(runtime_names=["LOGIN_TOKEN"])
        with patch("web_testing.script_assistant_views._dispatch") as dispatch:
            missing = self._post(
                ScriptAssistantListCreateView, self._repair_payload(execution)
            )
        self.assertEqual(missing.status_code, 400, missing.data)
        dispatch.assert_not_called()

        runtime = [
            {"name": "LOGIN_TOKEN", "value": "temporary-token", "required": True}
        ]
        with patch("web_testing.script_assistant_views._dispatch") as dispatch:
            response = self._post(
                ScriptAssistantListCreateView,
                self._repair_payload(execution, runtime_variables=runtime),
            )
        self.assertEqual(response.status_code, 202, response.data)
        session = WebUIScriptAssistant.objects.get(pk=response.data["data"]["id"])
        self.assertEqual(
            session.quality_report["runtime_variable_names"], ["LOGIN_TOKEN"]
        )
        self.assertNotIn("temporary-token", str(session.quality_report))
        self.assertNotIn("temporary-token", str(session.verification))

    def test_suite_repair_merges_variables_suite_over_case_and_partial_options(self):
        self.case.variables = [{"name": "SHARED", "value": "case"}]
        self.case.save(update_fields=["variables"])
        suite = WebUITestSuite.objects.create(
            name="suite",
            project=self.project,
            user=self.user,
            variables=[{"name": "SHARED", "value": "suite"}],
        )
        suite.add_test_case(self.case)
        execution = WebUITestExecution.objects.create(
            exec_type="suite",
            name="suite",
            executor=self.user,
            project=self.project,
            status="failed",
        )
        detail = capture_suite_snapshot(
            execution,
            suite,
            execution_options={"headed": False, "timeout": 120},
            runtime_variables=[{"name": "LOGIN_TOKEN", "value": "one-time"}],
        )
        row = detail.case_executions.get()
        row.status = "failed"
        row.save(update_fields=["status"])
        with patch("web_testing.script_assistant_views._dispatch"):
            response = self._post(
                ScriptAssistantListCreateView,
                {
                    "mode": "repair",
                    "model_config_id": self.model.id,
                    "execution_id": execution.id,
                    "suite_case_id": row.id,
                    "confirm_execution": True,
                    "runtime_variables": [
                        {"name": "LOGIN_TOKEN", "value": "reentered"}
                    ],
                    "options": {"timeout": 300},
                },
            )
        self.assertEqual(response.status_code, 202, response.data)
        session = WebUIScriptAssistant.objects.get(pk=response.data["data"]["id"])
        self.assertEqual(
            session.source_variables,
            [
                {
                    "name": "SHARED",
                    "value": "suite",
                    "is_secret": False,
                    "required": False,
                    "description": "",
                }
            ],
        )
        self.assertEqual(session.source_options, {"headed": False, "timeout": 300})

    def test_repair_verification_reenters_original_runtime_variables(self):
        execution = self._failed_case_execution(runtime_names=["LOGIN_TOKEN"])
        with patch("web_testing.script_assistant_views._dispatch"):
            created = self._post(
                ScriptAssistantListCreateView,
                self._repair_payload(
                    execution,
                    runtime_variables=[{"name": "LOGIN_TOKEN", "value": "initial"}],
                ),
            )
        session = WebUIScriptAssistant.objects.get(pk=created.data["data"]["id"])
        session.status = "candidate_ready"
        session.candidate_script = SCRIPT
        session.candidate_hash = candidate_hash(SCRIPT)
        session.save(update_fields=["status", "candidate_script", "candidate_hash"])
        payload = {
            "expected_revision": session.revision,
            "candidate_hash": session.candidate_hash,
            "confirm_execution": True,
            "runtime_variables": [],
        }
        with patch("web_testing.script_assistant_views._dispatch") as dispatch:
            rejected = self._post(
                ScriptAssistantVerifyView, payload, session_id=session.id
            )
        self.assertEqual(rejected.status_code, 400, rejected.data)
        self.assertIn("LOGIN_TOKEN", rejected.data["message"])
        dispatch.assert_not_called()
        payload["runtime_variables"] = [{"name": "LOGIN_TOKEN", "value": "reentered"}]
        with patch("web_testing.script_assistant_views._dispatch"):
            accepted = self._post(
                ScriptAssistantVerifyView, payload, session_id=session.id
            )
        self.assertEqual(accepted.status_code, 202, accepted.data)

    def test_repair_message_is_rejected_and_edit_message_resets_verification(self):
        execution = self._failed_case_execution()
        with patch("web_testing.script_assistant_views._dispatch"):
            repair = self._post(
                ScriptAssistantListCreateView, self._repair_payload(execution)
            )
        session = WebUIScriptAssistant.objects.get(pk=repair.data["data"]["id"])
        rejected = self._post(
            ScriptAssistantMessageView,
            {
                "expected_revision": session.revision,
                "script_content": SCRIPT,
                "description": "x",
                "variables": [],
                "message": "again",
                "use_candidate": False,
            },
            session_id=session.id,
        )
        self.assertEqual(rejected.status_code, 409, rejected.data)

        edit = self._create_edit()
        edit.status = "candidate_ready"
        edit.verification = {"status": "passed"}
        edit.save(update_fields=["status", "verification"])
        with patch("web_testing.script_assistant_views._dispatch"):
            accepted = self._post(
                ScriptAssistantMessageView,
                {
                    "expected_revision": edit.revision,
                    "script_content": SCRIPT,
                    "description": "x",
                    "variables": [],
                    "message": "revise",
                    "use_candidate": False,
                },
                session_id=edit.id,
            )
        self.assertEqual(accepted.status_code, 202, accepted.data)
        edit.refresh_from_db()
        self.assertEqual(edit.verification["status"], "unverified")

    def test_message_and_editor_input_limits_are_contract_errors(self):
        missing_first_message = self._post(
            ScriptAssistantListCreateView,
            {
                "mode": "edit",
                "model_config_id": self.model.id,
                "test_case_id": self.case.id,
                "expected_edit_version": case_edit_version(self.case),
                "script_content": SCRIPT,
            },
        )
        self.assertEqual(
            missing_first_message.status_code, 400, missing_first_message.data
        )
        oversized_create = self._post(
            ScriptAssistantListCreateView,
            {
                "mode": "edit",
                "model_config_id": self.model.id,
                "test_case_id": self.case.id,
                "expected_edit_version": case_edit_version(self.case),
                "script_content": SCRIPT + ("#" * 200001),
                "message": "valid",
            },
        )
        self.assertEqual(oversized_create.status_code, 400, oversized_create.data)

        edit = self._create_edit()
        invalid = {
            "expected_revision": edit.revision,
            "script_content": SCRIPT,
            "description": "x",
            "variables": [],
            "message": "",
            "use_candidate": False,
        }
        self.assertEqual(
            self._post(
                ScriptAssistantMessageView, invalid, session_id=edit.id
            ).status_code,
            400,
        )
        invalid["message"] = "x" * 2001
        self.assertEqual(
            self._post(
                ScriptAssistantMessageView, invalid, session_id=edit.id
            ).status_code,
            400,
        )
        invalid["message"] = "valid"
        invalid["description"] = "x" * 2001
        self.assertEqual(
            self._post(
                ScriptAssistantMessageView, invalid, session_id=edit.id
            ).status_code,
            400,
        )
        invalid["description"] = "valid"
        invalid["use_candidate"] = "false"
        self.assertEqual(
            self._post(
                ScriptAssistantMessageView, invalid, session_id=edit.id
            ).status_code,
            400,
        )

    def test_verify_requires_candidate_state_and_preserves_frozen_partial_options(self):
        session = self._create_edit()
        session.status = "failed"
        session.candidate_script, session.candidate_hash = SCRIPT, candidate_hash(
            SCRIPT
        )
        session.source_options = {"headed": False, "timeout": 120}
        session.save(
            update_fields=[
                "status",
                "candidate_script",
                "candidate_hash",
                "source_options",
            ]
        )
        rejected = self._post(
            ScriptAssistantVerifyView,
            {
                "expected_revision": session.revision,
                "candidate_hash": session.candidate_hash,
                "confirm_execution": True,
                "runtime_variables": [],
            },
            session_id=session.id,
        )
        self.assertEqual(rejected.status_code, 409, rejected.data)

        session.status = "candidate_ready"
        session.save(update_fields=["status"])
        with patch("web_testing.script_assistant_views._dispatch"):
            accepted = self._post(
                ScriptAssistantVerifyView,
                {
                    "expected_revision": session.revision,
                    "candidate_hash": session.candidate_hash,
                    "confirm_execution": True,
                    "runtime_variables": [],
                    "options": {"timeout": 300},
                },
                session_id=session.id,
            )
        self.assertEqual(accepted.status_code, 202, accepted.data)
        session.refresh_from_db()
        self.assertEqual(session.source_options, {"headed": False, "timeout": 300})

    def test_list_query_validation_and_mode_filter(self):
        edit = self._create_edit()
        execution = self._failed_case_execution()
        with patch("web_testing.script_assistant_views._dispatch"):
            self._post(ScriptAssistantListCreateView, self._repair_payload(execution))
        filtered = self._get(ScriptAssistantListCreateView, "mode=edit")
        self.assertEqual(filtered.status_code, 200, filtered.data)
        self.assertEqual([row["id"] for row in filtered.data["data"]], [str(edit.id)])
        self.assertEqual(
            self._get(ScriptAssistantListCreateView, "mode=other").status_code, 400
        )
        self.assertEqual(
            self._get(
                ScriptAssistantListCreateView, "execution_id=not-an-id"
            ).status_code,
            400,
        )
        self.assertEqual(
            self._get(ScriptAssistantListCreateView, "test_case_id=0").status_code, 400
        )

    def test_assistant_scopes_user_project_and_execute_capability(self):
        session = self._create_edit()
        member = get_user_model().objects.create_user(
            username="assistant-member",
            email="assistant-member@example.test",
        )
        ProjectMember.objects.create(
            project=self.project,
            user=member,
            role="editor",
            can_edit=True,
            can_execute_tests=False,
        )
        member_model = LLMConfiguration.objects.create(
            model_type=ModelType.LLM,
            provider="openai",
            provider_name="Member provider",
            is_active=True,
            api_key="member-key",
            base_url="https://llm.example.test",
            model_name="fixture",
            created_by=member,
        )
        hidden = self._get(
            ScriptAssistantDetailView, session_id=session.id, user=member
        )
        self.assertEqual(hidden.status_code, 404)

        execution = self._failed_case_execution()
        denied_payload = self._repair_payload(execution)
        denied_payload["model_config_id"] = member_model.id
        denied = self._post(
            ScriptAssistantListCreateView,
            denied_payload,
            user=member,
        )
        self.assertEqual(denied.status_code, 403)

        other_project = Project.objects.create(
            name="other",
            project_type="web",
            owner=self.user,
            created_by=self.user,
        )
        cross_project = self._post(
            ScriptAssistantListCreateView,
            {
                "mode": "edit",
                "model_config_id": self.model.id,
                "test_case_id": self.case.id,
                "expected_edit_version": case_edit_version(self.case),
                "script_content": SCRIPT,
            },
            project_id=other_project.id,
        )
        self.assertEqual(cross_project.status_code, 404)

    def test_apply_forwards_expected_revision_to_locked_core_boundary(self):
        session = self._create_edit()
        session.status = "candidate_passed"
        session.candidate_script, session.candidate_hash = SCRIPT, candidate_hash(
            SCRIPT
        )
        session.save(update_fields=["status", "candidate_script", "candidate_hash"])
        with patch(
            "web_testing.script_assistant.apply_repair", return_value=self.case
        ) as apply:
            response = self._post(
                ScriptAssistantApplyView,
                {
                    "expected_revision": session.revision,
                    "expected_edit_version": case_edit_version(self.case),
                    "candidate_hash": session.candidate_hash,
                },
                session_id=session.id,
            )
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["data"]["id"], str(session.id))
        self.assertEqual(apply.call_args.kwargs["expected_revision"], session.revision)

    def _repair_candidate_session(self, *, blockers, candidate=None):
        candidate = candidate or SCRIPT.replace(
            '    await expect(page).to_have_title("Home")',
            '    await page.locator("#save").click()\n'
            '    await expect(page).to_have_title("Home")',
        )
        execution = self._failed_case_execution()
        return WebUIScriptAssistant.objects.create(
            project=self.project,
            user=self.user,
            test_case=self.case,
            execution=execution,
            mode="repair",
            model_config_id=self.model.id,
            status="candidate_ready",
            revision=7,
            source_script=SCRIPT,
            source_script_version=self.case.script_version,
            source_edit_version=case_edit_version(self.case),
            source_variables=self.case.variables,
            source_options=normalize_webui_execution_options({"headed": False}),
            candidate_script=candidate,
            candidate_hash=candidate_hash(candidate),
            blockers=blockers,
            verification={"status": "unverified"},
        )

    def test_scope_only_candidate_requires_acknowledgement_then_saves_without_running(
        self,
    ):
        session = self._repair_candidate_session(
            blockers=[{"code": "REPAIR_SCOPE_CHANGED", "message": "新增点击"}],
        )
        original_case_script = self.case.test_script_content
        execution_count = WebUITestExecution.objects.count()
        rejected = self._post(
            ScriptAssistantApplyView,
            {
                "expected_revision": session.revision,
                "expected_edit_version": case_edit_version(self.case),
                "candidate_hash": session.candidate_hash,
            },
            session_id=session.id,
        )
        self.assertEqual(rejected.status_code, 409, rejected.data)
        detail = self._get(ScriptAssistantDetailView, session_id=session.id)
        self.assertEqual(detail.data["data"]["adoption"]["kind"], "manual_review")
        self.case.refresh_from_db()
        session.refresh_from_db()
        self.assertEqual(self.case.test_script_content, original_case_script)
        self.assertEqual(session.status, "candidate_ready")
        self.assertEqual(session.verification, {"status": "unverified"})
        self.assertEqual(WebUITestExecution.objects.count(), execution_count)

        accepted = self._post(
            ScriptAssistantApplyView,
            {
                "expected_revision": session.revision,
                "expected_edit_version": case_edit_version(self.case),
                "candidate_hash": session.candidate_hash,
                "acknowledge_review": True,
            },
            session_id=session.id,
        )
        self.assertEqual(accepted.status_code, 200, accepted.data)
        self.assertEqual(accepted.data["data"]["adoption"]["kind"], "unavailable")
        self.case.refresh_from_db()
        session.refresh_from_db()
        self.assertEqual(
            self.case.test_script_content, session.candidate_script.strip()
        )
        self.assertEqual(session.status, "applied")
        self.assertEqual(session.blockers[0]["code"], "REPAIR_SCOPE_CHANGED")
        self.assertEqual(session.verification, {"status": "unverified"})
        self.assertEqual(
            session.quality_report["manual_adoption"]["candidate_hash"],
            session.candidate_hash,
        )
        self.assertEqual(
            session.quality_report["manual_adoption"]["acknowledged_by"], self.user.id
        )
        self.assertTrue(session.quality_report["manual_adoption"]["acknowledged_at"])
        self.assertEqual(WebUITestExecution.objects.count(), execution_count)

    def test_repair_verify_action_requires_fresh_scope_acknowledgement(self):
        session = self._repair_candidate_session(
            blockers=[{"code": "REPAIR_SCOPE_CHANGED", "message": "新增点击"}],
        )
        session.summary = "上次候选已实际验证通过。"
        session.save(update_fields=["summary"])
        detail = self._get(ScriptAssistantDetailView, session_id=session.id)
        self.assertEqual(detail.data["data"]["verify_action"]["can_verify"], True)
        self.assertTrue(
            detail.data["data"]["verify_action"]["requires_acknowledge_review"]
        )
        payload = {
            "expected_revision": session.revision,
            "candidate_hash": session.candidate_hash,
            "confirm_execution": True,
            "runtime_variables": [],
        }
        with patch("web_testing.script_assistant_views._dispatch") as dispatch:
            rejected = self._post(
                ScriptAssistantVerifyView, payload, session_id=session.id
            )
        self.assertEqual(rejected.status_code, 409, rejected.data)
        dispatch.assert_not_called()
        payload["acknowledge_review"] = "true"
        invalid = self._post(ScriptAssistantVerifyView, payload, session_id=session.id)
        self.assertEqual(invalid.status_code, 400, invalid.data)
        payload["acknowledge_review"] = True
        with patch("web_testing.script_assistant_views._dispatch"):
            accepted = self._post(
                ScriptAssistantVerifyView, payload, session_id=session.id
            )
        self.assertEqual(accepted.status_code, 202, accepted.data)
        session.refresh_from_db()
        self.assertEqual(session.status, "queued")
        self.assertEqual(session.summary, "已发起本次候选验证，请以本次运行结果为准。")
        self.assertEqual(
            session.verification["authorization"]["candidate_hash"],
            session.candidate_hash,
        )
        self.assertEqual(
            session.verification["authorization"]["revision"], session.revision
        )
        self.assertEqual(
            session.verification["authorization"]["task_id"], session.task_id
        )
        self.assertTrue(session.verification["authorization"]["acknowledge_review"])
        self.assertEqual(
            session.verification["authorization"]["acknowledged_by"], self.user.id
        )
        self.assertTrue(session.verification["authorization"]["acknowledged_at"])

    def test_repair_verify_rejects_unknown_blockers_and_stale_hash_or_revision(self):
        session = self._repair_candidate_session(
            blockers=[{"code": "UNKNOWN", "message": "unknown"}],
        )
        payload = {
            "expected_revision": session.revision,
            "candidate_hash": session.candidate_hash,
            "confirm_execution": True,
            "acknowledge_review": True,
            "runtime_variables": [],
        }
        rejected = self._post(ScriptAssistantVerifyView, payload, session_id=session.id)
        self.assertEqual(rejected.status_code, 409, rejected.data)

        session = self._repair_candidate_session(blockers=[])
        payload.update(
            expected_revision=session.revision,
            candidate_hash="wrong-hash",
        )
        wrong_hash = self._post(
            ScriptAssistantVerifyView, payload, session_id=session.id
        )
        self.assertEqual(wrong_hash.status_code, 409, wrong_hash.data)
        payload.update(
            candidate_hash=session.candidate_hash,
            expected_revision=session.revision + 1,
        )
        stale_revision = self._post(
            ScriptAssistantVerifyView, payload, session_id=session.id
        )
        self.assertEqual(stale_revision.status_code, 409, stale_revision.data)

    def test_verified_scope_candidate_can_be_saved_without_erasing_verification(self):
        session = self._repair_candidate_session(
            blockers=[{"code": "REPAIR_SCOPE_CHANGED", "message": "scope"}],
        )
        session.status = "candidate_passed"
        session.verification = {
            "status": "passed",
            "candidate_hash": session.candidate_hash,
            "execution_id": 99,
            "summary": "本次候选已实际验证通过。",
        }
        session.save(update_fields=["status", "verification"])
        response = self._post(
            ScriptAssistantApplyView,
            {
                "expected_revision": session.revision,
                "expected_edit_version": case_edit_version(self.case),
                "candidate_hash": session.candidate_hash,
                "acknowledge_review": True,
            },
            session_id=session.id,
        )
        self.assertEqual(response.status_code, 200, response.data)
        session.refresh_from_db()
        self.assertEqual(session.status, "applied")
        self.assertEqual(session.verification["status"], "passed")
        self.assertEqual(session.verification["execution_id"], 99)
        self.assertEqual(session.blockers[0]["code"], "REPAIR_SCOPE_CHANGED")

    def test_manual_apply_rejects_non_scope_blockers_invalid_contract_and_conflicts(
        self,
    ):
        cases = [
            ([{"code": "UNKNOWN", "message": "unknown"}], SCRIPT),
            ([{"code": "ASSERTION_REMOVED", "message": "assertion"}], SCRIPT),
            ([{"code": "RUNTIME_VALUE_LEAK", "message": "leak"}], SCRIPT),
            (
                [{"code": "REPAIR_SCOPE_CHANGED", "message": "scope"}],
                "async def run(page):\n    invalid syntax",
            ),
        ]
        for blockers, candidate in cases:
            session = self._repair_candidate_session(
                blockers=blockers,
                candidate=candidate,
            )
            response = self._post(
                ScriptAssistantApplyView,
                {
                    "expected_revision": session.revision,
                    "expected_edit_version": case_edit_version(self.case),
                    "candidate_hash": session.candidate_hash,
                    "acknowledge_review": True,
                },
                session_id=session.id,
            )
            self.assertEqual(response.status_code, 409, response.data)
            session.refresh_from_db()
            self.assertEqual(session.status, "candidate_ready")

        session = self._repair_candidate_session(
            blockers=[{"code": "REPAIR_SCOPE_CHANGED", "message": "scope"}],
        )
        wrong_hash = self._post(
            ScriptAssistantApplyView,
            {
                "expected_revision": session.revision,
                "expected_edit_version": case_edit_version(self.case),
                "candidate_hash": "wrong-hash",
                "acknowledge_review": True,
            },
            session_id=session.id,
        )
        self.assertEqual(wrong_hash.status_code, 409, wrong_hash.data)
        stale_revision = self._post(
            ScriptAssistantApplyView,
            {
                "expected_revision": session.revision + 1,
                "expected_edit_version": case_edit_version(self.case),
                "candidate_hash": session.candidate_hash,
                "acknowledge_review": True,
            },
            session_id=session.id,
        )
        self.assertEqual(stale_revision.status_code, 409, stale_revision.data)
        invalid_acknowledgement = self._post(
            ScriptAssistantApplyView,
            {
                "expected_revision": session.revision,
                "expected_edit_version": case_edit_version(self.case),
                "candidate_hash": session.candidate_hash,
                "acknowledge_review": "true",
            },
            session_id=session.id,
        )
        self.assertEqual(
            invalid_acknowledgement.status_code, 400, invalid_acknowledgement.data
        )

    def test_manual_apply_rejects_changed_candidate_and_changed_source_case(self):
        session = self._repair_candidate_session(
            blockers=[{"code": "REPAIR_SCOPE_CHANGED", "message": "scope"}],
        )
        original_candidate = session.candidate_script
        original_case_script = self.case.test_script_content
        payload = {
            "expected_revision": session.revision,
            "expected_edit_version": case_edit_version(self.case),
            "candidate_hash": session.candidate_hash,
            "acknowledge_review": True,
        }
        session.candidate_script += "\n# changed after candidate hash was recorded\n"
        session.save(update_fields=["candidate_script"])
        corrupt_candidate = self._post(
            ScriptAssistantApplyView, payload, session_id=session.id
        )
        self.assertEqual(corrupt_candidate.status_code, 409, corrupt_candidate.data)

        session.candidate_script = original_candidate
        session.save(update_fields=["candidate_script"])
        self.case.description = "Edited after the repair source was frozen"
        self.case.save(update_fields=["description"])
        payload["expected_edit_version"] = case_edit_version(self.case)
        changed_source = self._post(
            ScriptAssistantApplyView, payload, session_id=session.id
        )
        self.assertEqual(changed_source.status_code, 409, changed_source.data)
        self.case.refresh_from_db()
        session.refresh_from_db()
        self.assertEqual(self.case.test_script_content, original_case_script)
        self.assertEqual(session.status, "candidate_ready")

    def test_suite_snapshot_freezes_normalized_options_version_and_variables(self):
        suite = WebUITestSuite.objects.create(
            name="suite",
            project=self.project,
            user=self.user,
            variables=[{"name": "SUITE_VALUE", "value": "suite"}],
        )
        suite.add_test_case(self.case)
        execution = WebUITestExecution.objects.create(
            exec_type="suite", name="suite", executor=self.user, project=self.project
        )
        detail = capture_suite_snapshot(execution, suite)
        row = detail.case_executions.get()
        self.assertEqual(
            detail.execution_options, normalize_webui_execution_options(None)
        )
        self.assertEqual(row.source_script_version, self.case.script_version)
        self.assertEqual(row.source_edit_version, case_edit_version(self.case))
        self.assertEqual(row.variables, self.case.variables)
        self.assertEqual(detail.suite_variables, suite.variables)

    def test_normal_case_run_freezes_source_before_task_dispatch(self):
        request = self.factory.post(
            "/execute/",
            {
                "options": {"timeout": 120},
                "runtime_variables": [{"name": "RUN_TOKEN", "value": "once"}],
            },
            format="json",
        )
        force_authenticate(request, user=self.user)
        with patch(
            "web_testing.tasks.execute_webui_test_case_task.delay",
            return_value=type("Task", (), {"id": "run-task"})(),
        ):
            response = ExecuteWebUITestCaseView.as_view()(
                request, project_id=self.project.id, pk=self.case.id
            )
        self.assertEqual(response.status_code, 200, response.data)
        detail = WebUITestExecution.objects.get(
            pk=response.data["data"]["execution_id"]
        ).case_execution_detail
        self.assertEqual(detail.source_script, SCRIPT)
        self.assertEqual(detail.source_script_version, self.case.script_version)
        self.assertEqual(detail.source_edit_version, case_edit_version(self.case))
        self.assertEqual(detail.source_variables, self.case.variables)
        self.assertEqual(detail.runtime_variable_names, ["RUN_TOKEN"])
        self.assertEqual(detail.execution_options["timeout"], 120)
