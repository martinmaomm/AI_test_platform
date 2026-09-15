"""Real-login API access isolation against disposable Django/Vue/SQLite.

This acceptance deliberately tampers only with the browser's selected project
id after a normal username/password login.  It never injects an owner token,
contacts Redis/NAS/SMTP/LLM, or sends a target API request.
"""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import socket
import tempfile
import threading
from unittest.mock import patch
from wsgiref.simple_server import make_server

from test_api_workspace_browser import BACKEND, bootstrap, database
from test_platform_reports_browser import CHROME, loopback_only
from test_project_knowledge_browser import _QuietHandler, _static_or_django


PASSWORD = "fixture-only"
OWNER_MARKERS = ("API 工作区隔离验收", "ACCESS-OWNER-WORKSPACE", "ACCESS-OWNER-REPORT")


def build_access_fixture(fixture):
    from django.contrib.auth import get_user_model
    from projects.models import Project, ProjectMember
    from api_testing.models import (
        APIEndpoint, APISpecification, APITestCase, APITestCaseExecutionDetail,
        APITestExecution, APITestSuite, APIWorkspace,
    )

    user_model = get_user_model()
    owner = user_model.objects.get(pk=fixture["user_id"])
    outsider = user_model.objects.create_user(
        username="access-outsider", email="access-outsider@example.test", password=PASSWORD,
    )
    viewer = user_model.objects.create_user(
        username="access-viewer", email="access-viewer@example.test", password=PASSWORD,
    )
    project = Project.objects.get(pk=fixture["project_id"])
    ProjectMember.objects.create(
        project=project, user=viewer, role="viewer",
        can_edit=False, can_delete=False,
        can_execute_tests=False, can_view_reports=True,
    )
    spec = APISpecification.objects.get(pk=fixture["spec_id"])
    endpoint = APIEndpoint.objects.get(pk=fixture["endpoint_id"])
    draft = {
        "version": 1,
        "config": {"name": "ACCESS-OWNER-WORKSPACE", "base_url": "https://example.test", "variables": {}},
        "teststeps": [{
            "name": "owner-only-step", "endpoint_id": endpoint.id,
            "request": {"method": "GET", "url": "/health"},
            "extract": {}, "validate": [{"eq": ["status_code", 200]}],
        }],
    }
    workspace = APIWorkspace.objects.create(
        project=project, owner=owner, spec=spec, model_id=fixture["model_id"],
        endpoint_ids=[endpoint.id], draft=deepcopy(draft),
        title="ACCESS-OWNER-WORKSPACE", status=APIWorkspace.Status.FAILED,
        error="isolated prior provider failure", task_id="owner-workspace-task",
        generation={
            "status": "failed", "phase": "finished", "source_revision": 0,
            "summary": "isolated prior provider failure", "rounds": [],
        },
    )
    case = APITestCase.objects.create(
        project=project, endpoint=endpoint, test_case_type="endpoint",
        title="ACCESS-OWNER-CASE", created_by=owner,
        script_content=json.dumps(draft, ensure_ascii=False),
    )
    suite = APITestSuite.objects.create(
        project=project, user=owner, name="ACCESS-OWNER-SUITE",
        test_case_order=[case.id], variables={},
    )
    suite.test_cases.add(case)
    execution = APITestExecution.objects.create(
        project=project, executor=owner, exec_type="case",
        name="ACCESS-OWNER-REPORT", status="failed", progress=100,
        task_id="owner-execution-task", input_snapshot={"source": "workspace_debug"},
    )
    APITestCaseExecutionDetail.objects.create(
        execution=execution, test_case=case, name="ACCESS-OWNER-REPORT",
        status="failed", error_message="owner-only-error",
        httprunner_result=json.dumps({
            "success": False, "error": "owner-only-error", "step_datas": [],
        }),
    )
    return {
        **fixture,
        "outsider": outsider.username,
        "viewer": viewer.username,
        "workspace_id": workspace.id,
        "case_id": case.id,
        "suite_id": suite.id,
        "execution_id": execution.id,
    }


def login(page, origin, username, target="/dashboard", expected_after_login=None):
    from playwright.sync_api import expect

    page.goto(origin + target)
    expect(page).to_have_url(__import__("re").compile(r"/login\?redirect="), timeout=15000)
    page.get_by_placeholder("用户名", exact=True).fill(username)
    page.get_by_placeholder("密码", exact=True).fill(PASSWORD)
    if expected_after_login:
        with page.expect_response(expected_after_login) as after_login:
            with page.expect_response(
                lambda item: item.url.endswith("/api/v1/users/login/") and item.request.method == "POST"
            ) as login_response:
                page.get_by_role("button", name="登录", exact=True).click()
        assert login_response.value.status == 200, login_response.value.status
        return after_login.value
    with page.expect_response(
        lambda item: item.url.endswith("/api/v1/users/login/") and item.request.method == "POST"
    ) as login_response:
        page.get_by_role("button", name="登录", exact=True).click()
    assert login_response.value.status == 200, login_response.value.status
    expect(page).not_to_have_url(__import__("re").compile(r"/login(?:\?|$)"), timeout=15000)
    return None


def browser_fetch(page, item):
    return page.evaluate(
        """async ({path, method, body}) => {
          const auth = JSON.parse(localStorage.getItem('auth-store') || '{}');
          const response = await fetch(path, {
            method,
            headers: {
              'Authorization': `Bearer ${auth.accessToken || ''}`,
              'Content-Type': 'application/json'
            },
            body: body === null ? undefined : JSON.stringify(body)
          });
          return {status: response.status, text: await response.text()};
        }""",
        item,
    )


def verify(origin, fixture, output, dispatches):
    from playwright.sync_api import expect, sync_playwright

    project = fixture["project_id"]
    spec = fixture["spec_id"]
    endpoint = fixture["endpoint_id"]
    workspace = fixture["workspace_id"]
    case = fixture["case_id"]
    suite = fixture["suite_id"]
    execution = fixture["execution_id"]
    api = f"/api/v1/projects/{project}/api-testing"
    report_path = f"/reports/api/{project}/{execution}"
    results = []
    failures = []

    outsider_checks = [
        ("project-detail", "GET", f"/api/v1/projects/{project}/", None),
        ("spec-list", "GET", f"{api}/api-specs/", None),
        ("spec-detail", "GET", f"{api}/api-specs/{spec}/", None),
        ("endpoint-list", "GET", f"{api}/api-specs/{spec}/endpoints/", None),
        ("workspace-list", "GET", f"{api}/workspaces/", None),
        ("workspace-detail", "GET", f"{api}/workspaces/{workspace}/", None),
        ("workspace-update", "PATCH", f"{api}/workspaces/{workspace}/", {"revision": 0, "title": "forbidden"}),
        ("workspace-delete", "DELETE", f"{api}/workspaces/{workspace}/", {"revision": 0, "confirmed": True}),
        ("workspace-generate", "POST", f"{api}/workspaces/{workspace}/messages/", {
            "revision": 0, "message": "forbidden", "base_url": "https://example.test",
            "variables": {}, "execution_confirmed": True,
        }),
        ("workspace-debug", "POST", f"{api}/workspaces/{workspace}/debug/", {"revision": 0, "variables": {}}),
        ("workspace-save", "POST", f"{api}/workspaces/{workspace}/save/", {"revision": 0, "title": "forbidden"}),
        ("workspace-export", "GET", f"{api}/workspaces/{workspace}/python/", None),
        ("workspace-retry", "POST", f"{api}/workspaces/{workspace}/retry-generation/", {
            "revision": 0, "execution_confirmed": True,
        }),
        ("workspace-cancel", "POST", f"{api}/workspaces/{workspace}/cancel/", {"revision": 0}),
        ("case-list", "GET", f"{api}/test-cases/", None),
        ("case-detail", "GET", f"{api}/test-cases/{case}/", None),
        ("case-update", "PATCH", f"{api}/test-cases/{case}/", {"title": "forbidden"}),
        ("case-execute", "POST", f"{api}/test-cases/{case}/execute/", {}),
        ("suite-execute", "POST", f"{api}/test-suites/{suite}/execute/", {}),
        ("scenario-debug", "POST", f"{api}/debug-scenario-steps/", {
            "base_url": "https://example.test", "teststeps": [{
                "name": "forbidden", "request": {"method": "GET", "url": "/health"},
            }],
        }),
        ("execution-list", "GET", f"{api}/executions/", None),
        ("execution-detail", "GET", f"{api}/executions/case/{execution}/", None),
        ("execution-report", "GET", f"{api}/executions/{execution}/report/", None),
        ("execution-task", "GET", f"{api}/task-status/owner-execution-task/", None),
        ("execution-delete", "DELETE", f"{api}/executions/{execution}/delete/", None),
    ]
    viewer_checks = [
        ("viewer-workspace-list", "GET", f"{api}/workspaces/", None, {403, 404}),
        ("viewer-workspace-save", "POST", f"{api}/workspaces/{workspace}/save/", {"revision": 0}, {403, 404}),
        ("viewer-workspace-debug", "POST", f"{api}/workspaces/{workspace}/debug/", {"revision": 0}, {403, 404}),
        ("viewer-workspace-retry", "POST", f"{api}/workspaces/{workspace}/retry-generation/", {
            "revision": 0, "execution_confirmed": True,
        }, {403, 404}),
        ("viewer-case-execute", "POST", f"{api}/test-cases/{case}/execute/", {}, {403, 404}),
        ("viewer-suite-execute", "POST", f"{api}/test-suites/{suite}/execute/", {}, {403, 404}),
        ("viewer-scenario-debug", "POST", f"{api}/debug-scenario-steps/", {
            "base_url": "https://example.test", "teststeps": [{
                "name": "forbidden", "request": {"method": "GET", "url": "/health"},
            }],
        }, {403, 404}),
        ("viewer-report", "GET", f"{api}/executions/{execution}/report/", None, {200}),
    ]

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=str(CHROME), headless=True)
        outsider_context = browser.new_context(viewport={"width": 1440, "height": 1000})
        outsider_context.route(
            "**/*",
            lambda route: route.continue_() if route.request.url.startswith(origin + "/") else route.abort(),
        )
        outsider_page = outsider_context.new_page()
        report_response = login(
            outsider_page, origin, fixture["outsider"], report_path,
            expected_after_login=lambda item: item.url.endswith(
                f"/api/v1/projects/{project}/api-testing/executions/{execution}/report/"
            ),
        )
        results.append({"actor": "outsider-ui", "check": "direct-report", "status": report_response.status})
        if report_response.status not in {403, 404}:
            failures.append(f"outsider-ui direct-report returned {report_response.status}")
        expect(outsider_page.get_by_text("ACCESS-OWNER-REPORT", exact=False)).to_have_count(0)
        outsider_page.screenshot(path=str(output / "outsider-report-direct-link.png"), full_page=True)

        outsider_page.evaluate(
            """project => localStorage.setItem('project-store', JSON.stringify({currentProject: project}))""",
            {"id": project, "name": "tampered-selection", "project_type": "api"},
        )
        with outsider_page.expect_response(
            lambda item: f"/api/v1/projects/{project}/api-testing/workspaces/" in item.url
            and item.request.method == "GET"
        ) as workspace_response:
            outsider_page.goto(origin + f"/api-testing/workspace/documents?workspace_id={workspace}")
        results.append({"actor": "outsider-ui", "check": "direct-workspace", "status": workspace_response.value.status})
        if workspace_response.value.status not in {403, 404}:
            failures.append(f"outsider-ui direct-workspace returned {workspace_response.value.status}")
        expect(outsider_page.get_by_text("ACCESS-OWNER-WORKSPACE", exact=False)).to_have_count(0)
        outsider_page.screenshot(path=str(output / "outsider-workspace-direct-link.png"), full_page=True)

        for label, method, path, body in outsider_checks:
            reply = browser_fetch(outsider_page, {"path": path, "method": method, "body": body})
            leaked = any(marker in reply["text"] for marker in OWNER_MARKERS)
            results.append({
                "actor": "outsider", "check": label, "status": reply["status"],
                "owner_marker_visible": leaked,
            })
            if reply["status"] not in {403, 404}:
                failures.append(f"outsider {label} returned {reply['status']}")
            if leaked:
                failures.append(f"outsider {label} exposed owner marker")
        outsider_context.close()

        viewer_context = browser.new_context(viewport={"width": 1280, "height": 900})
        viewer_context.route(
            "**/*",
            lambda route: route.continue_() if route.request.url.startswith(origin + "/") else route.abort(),
        )
        viewer_page = viewer_context.new_page()
        login(viewer_page, origin, fixture["viewer"])
        for label, method, path, body, expected in viewer_checks:
            reply = browser_fetch(viewer_page, {"path": path, "method": method, "body": body})
            marker_visible = any(marker in reply["text"] for marker in OWNER_MARKERS)
            results.append({
                "actor": "viewer", "check": label, "status": reply["status"],
                "owner_marker_visible": marker_visible,
            })
            if reply["status"] not in expected:
                failures.append(f"viewer {label} returned {reply['status']}")
            if label == "viewer-report" and not marker_visible:
                failures.append("viewer report permission did not expose the authorized report")
            if label != "viewer-report" and marker_visible:
                failures.append(f"viewer {label} unexpectedly exposed owner marker")
        viewer_context.close()
        browser.close()

    from api_testing.models import APITestCase, APITestExecution, APITestSuite, APIWorkspace

    unchanged = database(lambda: {
        "workspace": APIWorkspace.objects.filter(
            pk=workspace, title="ACCESS-OWNER-WORKSPACE", revision=0,
        ).count(),
        "case": APITestCase.objects.filter(pk=case, title="ACCESS-OWNER-CASE").count(),
        "suite": APITestSuite.objects.filter(pk=suite, name="ACCESS-OWNER-SUITE").count(),
        "execution_count": APITestExecution.objects.count(),
        "execution": APITestExecution.objects.filter(
            pk=execution, name="ACCESS-OWNER-REPORT", status="failed",
        ).count(),
    })
    results.append({"actor": "database", "check": "owner-data-unchanged", **unchanged})
    if unchanged != {"workspace": 1, "case": 1, "suite": 1, "execution_count": 1, "execution": 1}:
        failures.append(f"owner data changed: {unchanged}")
    dispatch_counts = {name: mock.call_count for name, mock in dispatches.items()}
    results.append({"actor": "dispatch", "check": "no-task-dispatch", **dispatch_counts})
    if any(dispatch_counts.values()):
        failures.append(f"unauthorized request dispatched work: {dispatch_counts}")

    evidence = {
        "network_boundary": "loopback-only",
        "database": "temporary-sqlite",
        "cache_broker": "in-memory",
        "results": results,
        "failures": failures,
    }
    (output / "access-matrix.json").write_text(
        json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
    )
    if failures:
        raise AssertionError("; ".join(failures))


def main():
    output = BACKEND / "logs" / "api-access-failures-browser-check"
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="automation-api-access-") as temp, patch.object(
        socket.socket, "connect", loopback_only(socket.socket.connect),
    ), patch.object(socket.socket, "connect_ex", loopback_only(socket.socket.connect_ex)):
        fixture = build_access_fixture(bootstrap(Path(temp)))
        from django.core.wsgi import get_wsgi_application
        from api_testing.tasks import execute_api_test_case_async, execute_api_test_suite_async
        from api_testing.workspace_tasks import debug_api_workspace, generate_and_verify_api_workspace

        server = make_server(
            "127.0.0.1", 0, _static_or_django(get_wsgi_application()),
            handler_class=_QuietHandler,
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with patch.object(execute_api_test_case_async, "delay") as case_dispatch, \
                    patch.object(execute_api_test_suite_async, "delay") as suite_dispatch, \
                    patch.object(debug_api_workspace, "apply_async") as debug_dispatch, \
                    patch.object(generate_and_verify_api_workspace, "apply_async") as generation_dispatch:
                verify(
                    f"http://127.0.0.1:{server.server_port}", fixture, output,
                    {
                        "case": case_dispatch, "suite": suite_dispatch,
                        "debug": debug_dispatch, "generation": generation_dispatch,
                    },
                )
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)
    print(f"PASS: real-login API access isolation; evidence: {output}")


if __name__ == "__main__":
    main()
