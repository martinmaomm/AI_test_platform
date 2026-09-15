"""Public media guard and controlled public report screenshot regressions."""
from __future__ import annotations

import os
from pathlib import Path
import tempfile

from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import re_path
from rest_framework.test import APIClient

from common.media import _public_media_file, public_media_serve
from projects.models import Project
from web_testing.models import WebUITestCaseExecutionDetail, WebUITestExecution


urlpatterns = [
    re_path(r"^media/(?P<path>.*)$", public_media_serve),
]


class PublicMediaAccessTests(TestCase):
    def setUp(self):
        media = tempfile.TemporaryDirectory(prefix="automation-public-media-")
        self.addCleanup(media.cleanup)
        override = override_settings(
            MEDIA_ROOT=media.name, DEBUG=True, ROOT_URLCONF=__name__,
        )
        override.enable()
        self.addCleanup(override.disable)

        self.media_root = Path(settings.MEDIA_ROOT)
        self.private_root = self.media_root / "webui_failure_screenshots"
        self.private_file = self.private_root / "execution_1" / "single_case.png"
        self.private_file.parent.mkdir(parents=True)
        self.private_file.write_bytes(b"private screenshot")
        self.public_file = self.media_root / "avatars" / "public-avatar.png"
        self.public_file.parent.mkdir(parents=True)
        self.public_file.write_bytes(b"public avatar")
        self.client = APIClient()

    def test_public_file_remains_available_and_directories_have_no_index(self):
        media_root, relative_path = _public_media_file("avatars/public-avatar.png")
        self.assertEqual(media_root, os.path.realpath(self.media_root))
        self.assertEqual(relative_path, "avatars/public-avatar.png")

        response = self.client.get("/media/avatars/public-avatar.png")
        self.assertEqual(
            response.status_code, 200,
            response.content.decode("utf-8", errors="replace") if not response.streaming else "",
        )
        self.assertEqual(b"".join(response.streaming_content), b"public avatar")
        response.close()

        for path in ("/media/", "/media/avatars/", "/media/webui_failure_screenshots/"):
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 404)

    def test_private_raw_dot_case_and_encoded_aliases_are_not_public(self):
        paths = (
            "/media/webui_failure_screenshots/execution_1/single_case.png",
            "/media/WEBUI_FAILURE_SCREENSHOTS/execution_1/single_case.png",
            "/media/WebUI_Failure_Screenshots/execution_1/single_case.png",
            "/media/a/../webui_failure_screenshots/execution_1/single_case.png",
            "/media/a/%2e%2e/webui_failure_screenshots/execution_1/single_case.png",
            "/media/%77ebui_failure_screenshots/execution_1/single_case.png",
            "/media/%2577ebui_failure_screenshots/execution_1/single_case.png",
            "/media/webui_failure_screenshots%2fexecution_1%2fsingle_case.png",
        )
        for path in paths:
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 404)
                self.assertNotContains(response, b"private screenshot", status_code=404)

    def test_authenticated_user_still_cannot_read_private_static_path(self):
        authenticated_user = get_user_model().objects.create_user(
            username="public-media-owner",
            email="public-media-owner@example.test",
            password="fixture-only",
        )
        self.client.force_authenticate(authenticated_user)
        self.assertEqual(
            self.client.get(
                "/media/webui_failure_screenshots/execution_1/single_case.png",
            ).status_code,
            404,
        )

    def test_symlink_alias_to_private_file_or_directory_is_not_public(self):
        directory_alias = self.media_root / "shared-screens"
        file_alias = self.media_root / "avatars" / "latest.png"
        try:
            os.symlink(self.private_root, directory_alias, target_is_directory=True)
            os.symlink(self.private_file, file_alias)
        except (NotImplementedError, OSError) as exc:
            self.skipTest(f"symlink unsupported: {exc}")
        for path in (
            "/media/shared-screens/execution_1/single_case.png",
            "/media/avatars/latest.png",
        ):
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 404)

    def test_symlink_alias_outside_media_root_is_not_public(self):
        external = tempfile.TemporaryDirectory(prefix="automation-external-media-")
        self.addCleanup(external.cleanup)
        external_root = Path(external.name)
        external_file = external_root / "outside.png"
        external_file.write_bytes(b"outside media")
        external_directory_alias = self.media_root / "external-files"
        external_file_alias = self.media_root / "avatars" / "external.png"
        try:
            os.symlink(external_root, external_directory_alias, target_is_directory=True)
            os.symlink(external_file, external_file_alias)
        except (NotImplementedError, OSError) as exc:
            self.skipTest(f"symlink unsupported: {exc}")
        for path in (
            "/media/external-files/outside.png",
            "/media/avatars/external.png",
        ):
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 404)


class ReportScreenshotRouteTests(TestCase):
    def setUp(self):
        media = tempfile.TemporaryDirectory(prefix="automation-auth-screenshot-")
        self.addCleanup(media.cleanup)
        override = override_settings(MEDIA_ROOT=media.name, DEBUG=True)
        override.enable()
        self.addCleanup(override.disable)

        user_model = get_user_model()
        self.owner = user_model.objects.create_user(
            username="media-owner", email="media-owner@example.test", password="fixture-only",
        )
        self.outsider = user_model.objects.create_user(
            username="media-outsider", email="media-outsider@example.test", password="fixture-only",
        )
        self.project = Project.objects.create(
            name="Media guard", project_type="web", owner=self.owner, created_by=self.owner,
        )
        self.execution = WebUITestExecution.objects.create(
            exec_type="case", name="Media screenshot", executor=self.owner,
            project=self.project, status="failed",
        )
        relative = f"webui_failure_screenshots/execution_{self.execution.id}/single_case.png"
        screenshot = Path(settings.MEDIA_ROOT) / relative
        screenshot.parent.mkdir(parents=True)
        screenshot.write_bytes(b"authenticated screenshot")
        WebUITestCaseExecutionDetail.objects.create(
            execution=self.execution, test_case=None, status="failed", screenshot_path=relative,
        )
        self.url = (
            f"/api/v1/projects/{self.project.id}/web-testing/"
            f"executions/{self.execution.id}/screenshot/"
        )
        self.client = APIClient()

    def test_report_screenshot_is_public_but_only_through_controlled_endpoint(self):
        for user in (None, self.outsider, self.owner):
            with self.subTest(user=user):
                self.client.force_authenticate(user)
                reply = self.client.get(self.url)
                self.assertEqual(reply.status_code, 200)
                self.assertEqual(reply["Content-Type"], "image/png")
                self.assertEqual(b"".join(reply.streaming_content), b"authenticated screenshot")
                reply.close()
