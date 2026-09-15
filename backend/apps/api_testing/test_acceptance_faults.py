"""Focused offline acceptance for API access and provider-failure boundaries.

The surrounding runner must install the disposable SQLite/cache/broker settings
before collecting this module.  No provider or target HTTP call is permitted.
"""
from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from ai_core.models import LLMConfiguration
from projects.models import Project, ProjectMember

from .models import (
    APIEndpoint,
    APISpecification,
    APITestCase,
    APITestCaseExecutionDetail,
    APITestExecution,
    APIWorkspace,
)
from .workspace_service import endpoint_specs, generation_budget, serialize_workspace
from .workspace_tasks import generate_and_verify_api_workspace
from .workspace_verification import draft_hash


class APIProjectAccessBoundaryTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.owner = user_model.objects.create_user(
            username="access-boundary-owner",
            email="access-boundary-owner@example.test",
            password="fixture-only",
        )
        self.creator = user_model.objects.create_user(
            username="access-boundary-creator",
            email="access-boundary-creator@example.test",
            password="fixture-only",
        )
        self.outsider = user_model.objects.create_user(
            username="access-boundary-outsider",
            email="access-boundary-outsider@example.test",
            password="fixture-only",
        )
        self.viewer = user_model.objects.create_user(
            username="access-boundary-viewer",
            email="access-boundary-viewer@example.test",
            password="fixture-only",
        )
        self.superuser = user_model.objects.create_superuser(
            username="access-boundary-superuser",
            email="access-boundary-superuser@example.test",
            password="fixture-only",
        )
        self.project = Project.objects.create(
            name="ACCESS-BOUNDARY-PROJECT", project_type="api",
            owner=self.owner, created_by=self.creator,
        )
        self.other_project = Project.objects.create(
            name="ACCESS-BOUNDARY-OTHER", project_type="api",
            owner=self.owner, created_by=self.owner,
        )
        ProjectMember.objects.create(
            project=self.project, user=self.viewer, role="viewer",
            can_edit=False, can_delete=False,
            can_execute_tests=False, can_view_reports=False,
        )
        self.spec = APISpecification.objects.create(
            project=self.project, created_by=self.owner,
            spec_name="ACCESS-BOUNDARY-SPEC", status="completed",
        )
        self.endpoint = APIEndpoint.objects.create(
            spec=self.spec, method="GET", path="/owner-only",
            summary="ACCESS-BOUNDARY-ENDPOINT",
        )
        self.case = APITestCase.objects.create(
            project=self.project, endpoint=self.endpoint,
            test_case_type="endpoint", title="ACCESS-BOUNDARY-CASE",
            created_by=self.owner, script_content="{}",
        )
        self.execution = APITestExecution.objects.create(
            project=self.project, executor=self.owner, exec_type="case",
            name="ACCESS-BOUNDARY-EXECUTION", status="failed",
        )
        APITestCaseExecutionDetail.objects.create(
            execution=self.execution, test_case=self.case,
            name="ACCESS-BOUNDARY-DETAIL", status="failed",
        )
        self.client = APIClient()

    def test_distinct_project_owner_can_read_spec_and_endpoint(self):
        base = f"/api/v1/projects/{self.project.id}/api-testing"
        self.client.force_authenticate(self.owner)
        for path, marker in (
            (f"{base}/api-specs/", b"ACCESS-BOUNDARY-SPEC"),
            (f"{base}/api-specs/{self.spec.id}/", b"ACCESS-BOUNDARY-SPEC"),
            (f"{base}/api-specs/{self.spec.id}/endpoints/", b"ACCESS-BOUNDARY-ENDPOINT"),
            (
                f"{base}/api-specs/{self.spec.id}/endpoints/{self.endpoint.id}/",
                b"ACCESS-BOUNDARY-ENDPOINT",
            ),
        ):
            with self.subTest(path=path):
                reply = self.client.get(path)
                self.assertEqual(reply.status_code, 200, reply.data)
                self.assertIn(marker, reply.content)

    def test_non_member_list_detail_update_and_delete_are_hidden(self):
        base = f"/api/v1/projects/{self.project.id}/api-testing"
        checks = (
            ("get", f"{base}/api-specs/", None),
            ("get", f"{base}/api-specs/{self.spec.id}/", None),
            ("patch", f"{base}/api-specs/{self.spec.id}/", {"spec_name": "forbidden"}),
            ("delete", f"{base}/api-specs/{self.spec.id}/", None),
            ("get", f"{base}/api-specs/{self.spec.id}/endpoints/", None),
            ("get", f"{base}/api-specs/{self.spec.id}/endpoints/{self.endpoint.id}/", None),
            ("patch", f"{base}/api-specs/{self.spec.id}/endpoints/{self.endpoint.id}/", {}),
            ("delete", f"{base}/api-specs/{self.spec.id}/endpoints/{self.endpoint.id}/", None),
            ("get", f"{base}/test-cases/", None),
            ("get", f"{base}/test-cases/{self.case.id}/", None),
            ("patch", f"{base}/test-cases/{self.case.id}/", {"title": "forbidden"}),
            ("delete", f"{base}/test-cases/{self.case.id}/", None),
            ("get", f"{base}/executions/", None),
            ("get", f"{base}/executions/case/{self.execution.id}/", None),
            ("delete", f"{base}/executions/{self.execution.id}/delete/", None),
        )
        self.client.force_authenticate(self.outsider)
        for method, path, data in checks:
            with self.subTest(method=method, path=path):
                reply = getattr(self.client, method)(path, data=data, format="json")
                self.assertIn(reply.status_code, {403, 404})
                self.assertNotIn(b"ACCESS-BOUNDARY", reply.content)

        self.assertTrue(APISpecification.objects.filter(pk=self.spec.id).exists())
        self.assertTrue(APIEndpoint.objects.filter(pk=self.endpoint.id).exists())
        self.case.refresh_from_db()
        self.assertEqual(self.case.title, "ACCESS-BOUNDARY-CASE")
        self.assertTrue(APITestExecution.objects.filter(pk=self.execution.id).exists())
        self.assertTrue(
            APITestCaseExecutionDetail.objects.filter(execution=self.execution).exists(),
        )

    def test_execution_delete_cannot_cross_the_url_project(self):
        self.client.force_authenticate(self.owner)
        wrong_project_url = (
            f"/api/v1/projects/{self.other_project.id}/api-testing/"
            f"executions/{self.execution.id}/delete/"
        )
        reply = self.client.delete(wrong_project_url)
        self.assertEqual(reply.status_code, 404)
        self.assertTrue(APITestExecution.objects.filter(pk=self.execution.id).exists())
        self.assertTrue(
            APITestCaseExecutionDetail.objects.filter(execution=self.execution).exists(),
        )

    def test_non_member_superuser_keeps_existing_report_visibility(self):
        base = f"/api/v1/projects/{self.project.id}/api-testing/executions"
        self.client.force_authenticate(self.superuser)
        for path, marker in (
            (f"{base}/", b"ACCESS-BOUNDARY-EXECUTION"),
            (f"{base}/case/{self.execution.id}/", b"ACCESS-BOUNDARY-DETAIL"),
            (f"{base}/{self.execution.id}/report/", b"ACCESS-BOUNDARY-EXECUTION"),
        ):
            with self.subTest(path=path):
                reply = self.client.get(path)
                self.assertEqual(reply.status_code, 200, reply.data)
                self.assertIn(marker, reply.content)

    def test_member_without_mutation_capability_cannot_change_spec_or_endpoint(self):
        base = f"/api/v1/projects/{self.project.id}/api-testing"
        checks = (
            ("patch", f"{base}/api-specs/{self.spec.id}/", {"spec_name": "forbidden"}),
            ("delete", f"{base}/api-specs/{self.spec.id}/", None),
            ("patch", f"{base}/api-specs/{self.spec.id}/endpoints/{self.endpoint.id}/", {}),
            ("delete", f"{base}/api-specs/{self.spec.id}/endpoints/{self.endpoint.id}/", None),
        )
        self.client.force_authenticate(self.viewer)
        for method, path, data in checks:
            with self.subTest(method=method, path=path):
                reply = getattr(self.client, method)(path, data=data, format="json")
                self.assertEqual(reply.status_code, 403, reply.data)

        self.spec.refresh_from_db()
        self.assertEqual(self.spec.spec_name, "ACCESS-BOUNDARY-SPEC")
        self.assertTrue(APIEndpoint.objects.filter(pk=self.endpoint.id).exists())


class APIWorkspaceFaultAcceptanceTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="fault-acceptance", password="fixture-only",
        )
        self.project = Project.objects.create(
            name="Fault acceptance", project_type="api",
            owner=self.user, created_by=self.user,
        )
        self.model = LLMConfiguration.objects.create(
            model_type="llm", provider="openai", model_name="offline-fault-model",
            api_key="fixture-only", base_url="https://never-called.invalid",
            created_by=self.user, is_active=True,
        )
        self.spec = APISpecification.objects.create(
            project=self.project, created_by=self.user,
            spec_name="Fault API", status="completed",
        )
        self.endpoint = APIEndpoint.objects.create(
            spec=self.spec, method="GET", path="/health", summary="health",
            responses={"200": {"description": "OK"}},
        )

    def draft(self):
        return {
            "version": 1,
            "config": {"name": "fault", "base_url": "", "variables": {}},
            "teststeps": [{
                "name": "health", "endpoint_id": self.endpoint.id,
                "request": {"method": "GET", "url": "/health"},
                "extract": {}, "validate": [{"eq": ["status_code", 200]}],
            }],
        }

    def queued_workspace(self, *, task_id, mode="generate", candidate=None, previous=None):
        draft = self.draft()
        workspace = APIWorkspace.objects.create(
            project=self.project, owner=self.user, spec=self.spec,
            model_id=self.model.id, endpoint_ids=[self.endpoint.id],
            draft=deepcopy(draft), candidate=deepcopy(candidate),
            status=APIWorkspace.Status.GENERATING, task_id=task_id,
        )
        budget = generation_budget(model_id=self.model.id, owner=self.user)
        snapshot = {
            "revision": workspace.revision, "task_id": task_id, "mode": mode,
            "draft": deepcopy(draft), "user_draft": deepcopy(draft),
            "model_id": self.model.id, "spec_id": self.spec.id,
            "target_endpoint_id": None,
            "endpoints": endpoint_specs(
                self.project.id, [self.endpoint.id],
                spec_id=self.spec.id, owner=self.user,
            ),
            "target_url": "https://example.test", "variables": {},
            "messages": [], "failure_evidence": None,
            **deepcopy(budget),
        }
        workspace.generation = {
            "status": "queued", "phase": "queued", "attempt": 0,
            "max_attempts": 3, "source_revision": workspace.revision,
            "summary": "", "rounds": [], "_snapshot": snapshot,
            **{key: deepcopy(budget[key]) for key in (
                "queued_at", "claimed_at", "started_at", "finished_at",
                "timeouts", "deadlines",
            )},
        }
        if previous is not None:
            workspace.generation["_retry_previous"] = deepcopy(previous)
        workspace.save(update_fields=["generation", "updated_at"])
        return workspace

    @staticmethod
    def provider_error(message, status_code=None):
        error = RuntimeError(message)
        if status_code is not None:
            error.status_code = status_code
        return error

    def run_failure(self, workspace, stream):
        manager = SimpleNamespace(
            config={"provider": "openai"}, stream_invoke=Mock(side_effect=stream),
        )
        with patch("api_testing.workspace_tasks.get_llm_manager", return_value=manager), \
                patch("api_testing.requests_runner.requests_runner") as runner:
            result = generate_and_verify_api_workspace.apply(
                args=(workspace.id, workspace.revision, workspace.task_id),
                task_id=workspace.task_id,
            )
        workspace.refresh_from_db()
        runner.assert_not_called()
        self.assertEqual(result.result["status"], "failed")
        self.assertEqual(workspace.status, APIWorkspace.Status.FAILED)
        self.assertEqual(workspace.generation["status"], "failed")
        self.assertEqual(workspace.generation["phase"], "finished")
        return serialize_workspace(workspace)

    def test_provider_status_matrix_is_terminal_safe_and_never_dispatches_http(self):
        cases = [
            ("auth", self.provider_error("credential rejected", 401), "MODEL_AUTH_ERROR", False),
            ("rate", self.provider_error("request throttled", 429), "MODEL_RATE_LIMITED", True),
            ("internal", self.provider_error("provider internal", 500), "MODEL_UNAVAILABLE", True),
            ("unavailable", self.provider_error("provider unavailable", 503), "MODEL_UNAVAILABLE", True),
            ("gateway", self.provider_error("gateway timeout", 504), "MODEL_TIMEOUT", True),
            (
                "overload",
                self.provider_error("Our servers are currently overloaded. Please try again later."),
                "MODEL_OVERLOADED", True,
            ),
        ]
        for index, (label, error, expected_code, retryable) in enumerate(cases, start=1):
            with self.subTest(label=label):
                workspace = self.queued_workspace(task_id=f"fault-{index}")
                original_draft = deepcopy(workspace.draft)
                public = self.run_failure(workspace, error)
                self.assertEqual(workspace.draft, original_draft)
                self.assertIsNone(workspace.candidate)
                self.assertEqual(public["model_failure"]["code"], expected_code)
                self.assertEqual(public["model_failure"]["retryable"], retryable)
                self.assertEqual(public["retry"]["available"], retryable)
                self.assertNotIn(str(error), str(public))

    def test_midstream_exception_keeps_previous_candidate_and_http_evidence(self):
        draft = self.draft()
        digest = draft_hash(draft)
        original_result = {
            "success": False, "error_type": "ExtractionFailure",
            "replay_safety": {"safe_to_retry": True},
            "step_datas": [{
                "status": "failed",
                "data": {"req_resps": [{"response": {"status_code": 422}}]},
            }],
        }
        candidate = {
            "draft": deepcopy(draft), "draft_hash": digest,
            "source_revision": 0, "verification_status": "failed", "mode": "generate",
        }
        previous = {
            "status": "failed", "phase": "finished", "summary": "original failure",
            "source_revision": 0,
            "rounds": [{
                "attempt": 1, "draft": deepcopy(draft),
                "draft_hash": digest, "result": deepcopy(original_result),
            }],
        }
        workspace = self.queued_workspace(
            task_id="midstream-fault", mode="repair",
            candidate=candidate, previous=previous,
        )

        def interrupted_stream(_messages, callback=None, **_kwargs):
            if callback:
                callback('{"version":1,"config":')
            raise self.provider_error("server disconnected secret-upstream-body", 503)

        public = self.run_failure(workspace, interrupted_stream)
        self.assertEqual(workspace.candidate, candidate)
        self.assertEqual(
            workspace.generation["_retry_previous"]["rounds"][0]["result"],
            original_result,
        )
        self.assertEqual(public["model_failure"]["code"], "MODEL_UNAVAILABLE")
        self.assertEqual(public["model_failure"]["stage"], "repairing")
        self.assertNotIn("secret-upstream-body", str(public))
