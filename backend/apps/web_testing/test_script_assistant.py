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
from .script_assistant import candidate_hash, case_edit_version
from .script_assistant_views import (
    ScriptAssistantApplyView,
    ScriptAssistantCancelView,
    ScriptAssistantDetailView,
    ScriptAssistantListCreateView,
    ScriptAssistantMessageView,
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
