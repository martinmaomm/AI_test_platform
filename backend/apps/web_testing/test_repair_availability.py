"""Execution report repair entries must not route drafts to saved-case repair."""

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from projects.models import Project
from .constants import normalize_webui_execution_options
from .models import (
    WebUITestCase,
    WebUITestCaseExecutionDetail,
    WebUITestExecution,
    WebUITestSuiteExecutionDetail,
    WebUITestSuiteCaseExecution,
)
from .serializers import (
    WebUITestCaseExecutionDetailSerializer,
    WebUITestSuiteCaseExecutionSerializer,
)
from .views import TestExecutionReportView, TestExecutionCasesView


class RepairAvailabilityTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="repair-entry-owner")
        self.project = Project.objects.create(
            name="repair entries",
            project_type="web",
            owner=self.user,
            created_by=self.user,
        )
        self.case = WebUITestCase.objects.create(
            title="saved",
            user=self.user,
            project=self.project,
            test_script_content="async def run(page):\n    pass",
            script_version=1,
        )
        self.execution = WebUITestExecution.objects.create(
            project=self.project,
            executor=self.user,
            exec_type="case",
            name="failed",
            status="failed",
        )
        self.detail = WebUITestCaseExecutionDetail.objects.create(
            execution=self.execution,
            test_case=self.case,
            status="failed",
            source_script=self.case.test_script_content,
            source_script_version=1,
            source_edit_version=self.case.edit_version,
            execution_options=normalize_webui_execution_options({}),
        )

    def availability(self):
        return WebUITestCaseExecutionDetailSerializer(self.detail).data[
            "repair_availability"
        ]

    def request(self, view, execution):
        request = APIRequestFactory().get("/report/")
        force_authenticate(request, user=self.user)
        response = view.as_view()(request, project_id=self.project.id, pk=execution.id)
        self.assertEqual(response.status_code, 200)
        return response.data["data"]

    def test_complete_saved_case_is_repairable_without_exposing_script(self):
        self.assertTrue(self.availability()["available"])
        report = self.request(TestExecutionReportView, self.execution)
        self.assertTrue(report["repair_availability"]["available"])
        self.assertNotIn("source_script", report)

    def test_draft_or_deleted_case_is_not_repairable_even_with_snapshot(self):
        self.detail.test_case = None
        self.detail.save(update_fields=["test_case"])
        self.assertFalse(self.availability()["available"])
        self.assertIn("生成草稿", self.availability()["reason"])
        self.assertFalse(
            self.request(TestExecutionReportView, self.execution)[
                "repair_availability"
            ]["available"]
        )

    def test_each_missing_snapshot_field_hides_entry(self):
        for field, missing in [
            ("source_script", ""),
            ("source_script_version", None),
            ("source_edit_version", ""),
            ("execution_options", {}),
        ]:
            with self.subTest(field=field):
                previous = getattr(self.detail, field)
                setattr(self.detail, field, missing)
                self.assertFalse(self.availability()["available"])
                self.assertIn("快照", self.availability()["reason"])
                setattr(self.detail, field, previous)

    def test_non_failure_states_hide_entry(self):
        for state in ["pending", "running", "passed", "stopped", "incomplete"]:
            with self.subTest(state=state):
                self.execution.status = state
                self.assertFalse(self.availability()["available"])

    def test_suite_child_uses_its_own_snapshot_and_parent_status(self):
        execution = WebUITestExecution.objects.create(
            project=self.project,
            executor=self.user,
            exec_type="suite",
            name="suite",
            status="failed",
        )
        suite = WebUITestSuiteExecutionDetail.objects.create(
            execution=execution, execution_options=normalize_webui_execution_options({})
        )
        child = WebUITestSuiteCaseExecution.objects.create(
            suite_execution=suite,
            test_case=self.case,
            name="saved",
            status="failed",
            script_content=self.case.test_script_content,
            source_script_version=1,
            source_edit_version=self.case.edit_version,
        )
        self.assertTrue(
            WebUITestSuiteCaseExecutionSerializer(child).data["repair_availability"][
                "available"
            ]
        )
        cases = self.request(TestExecutionCasesView, execution)
        self.assertTrue(cases["cases"][0]["repair_availability"]["available"])
        child.source_edit_version = ""
        self.assertFalse(
            WebUITestSuiteCaseExecutionSerializer(child).data["repair_availability"][
                "available"
            ]
        )
        child.source_edit_version = self.case.edit_version
        execution.status = "running"
        self.assertFalse(
            WebUITestSuiteCaseExecutionSerializer(child).data["repair_availability"][
                "available"
            ]
        )
