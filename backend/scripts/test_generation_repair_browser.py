"""Exercise draft repair UI/real APIs in disposable SQLite; no model/browser task runs.

Build frontend first. Only loopback sockets are allowed. Candidate results are
fixture data; saved-case repair/edit regressions have a separate browser script.
"""

from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import socket
import tempfile
import threading
from types import SimpleNamespace
from unittest.mock import patch
from wsgiref.simple_server import make_server

from test_platform_reports_browser import BACKEND, CHROME, PNG, bootstrap, loopback_only
from test_project_knowledge_browser import _QuietHandler, _static_or_django

SCRIPT = """from playwright.async_api import expect
async def run(page):
    await page.goto('http://fixture.invalid/')
    await expect(page.locator('#result')).to_have_text('ok')
"""
CANDIDATE = SCRIPT.replace("'#result'", "'#result-fixed'")
BLOCKER = {
    "code": "ASSERTION_REGRESSION",
    "message": "候选改变了原断言的预期值，请人工核对。",
    "line": 4,
}


def add_fixture(fixture):
    from django.conf import settings
    from projects.models import Project
    from web_testing.models import (
        WebUIScriptGeneration,
        WebUITestExecution,
        WebUITestCaseExecutionDetail,
    )
    from web_testing.generation_workspace import script_hash

    project = Project.objects.get(pk=fixture["project_id"])
    execution = WebUITestExecution.objects.create(
        project=project,
        executor_id=fixture["user_id"],
        exec_type="case",
        name="草稿失败修复验收",
        status="failed",
    )
    screenshot = f"webui_failure_screenshots/execution_{execution.id}/fixture.png"
    path = Path(settings.MEDIA_ROOT) / screenshot
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(PNG)
    WebUITestCaseExecutionDetail.objects.create(
        execution=execution,
        status="failed",
        error_message="点击按钮超时",
        log="fixture original debug log",
        screenshot_path=screenshot,
    )
    generation = WebUIScriptGeneration.objects.create(
        project=project,
        user_id=fixture["user_id"],
        status="ready",
        current_stage="completed",
        progress=100,
        target_url="http://fixture.invalid/",
        script_draft=SCRIPT,
        description_safe="检查测试状态",
        scenario_spec={"schema_version": 5, "title": "草稿修复隔离验收"},
        quality_report={"status": "ready", "blockers": []},
        workspace={
            "revision": 1,
            "variables": [],
            "verification": {
                "status": "failed",
                "execution_id": execution.id,
                "locked_revision": 1,
                "script_hash": script_hash(SCRIPT),
                "diagnostics": [{"code": "RUNTIME_FAILURE", "message": "点击按钮超时"}],
            },
            "repair": {"status": "idle"},
        },
    )
    fixture.update(
        project={"id": project.id, "name": project.name, "project_type": "web"},
        generation=generation,
        draft_execution=execution,
    )


def set_candidate(generation, *, actual_execution=None, passed=False):
    from web_testing.generation_workspace import script_hash

    generation.refresh_from_db()
    workspace = deepcopy(generation.workspace)
    repair = {
        "status": "candidate_passed" if passed else "candidate_ready",
        "phase": "completed",
        "candidate_script": CANDIDATE,
        "candidate_hash": script_hash(CANDIDATE),
        "candidate_diff": "- old locator\n+ corrected locator",
        "source_revision": 1,
        "script_hash": script_hash(SCRIPT),
        "candidate_quality_report": {
            "status": "ready" if actual_execution else "needs_review",
            "blockers": [] if actual_execution else [BLOCKER],
        },
        "message": "候选已实际验证通过。" if passed else "候选待审核。",
        "attempts": [
            {
                "round": 1,
                "candidate_hash": script_hash(CANDIDATE),
                "execution_id": actual_execution.id if actual_execution else None,
                "execution_status": (
                    ("passed" if passed else "failed")
                    if actual_execution
                    else "not_run"
                ),
                "static_status": "ready" if actual_execution else "needs_review",
                "summary": (
                    "已运行候选"
                    if actual_execution
                    else "候选未通过静态检查，未执行浏览器验证。"
                ),
                "blockers": [] if actual_execution else [BLOCKER],
            }
        ],
    }
    workspace["repair"] = repair
    generation.workspace = workspace
    generation.save(update_fields=["workspace", "updated_at"])


def verify_ui(origin, fixture, output):
    from playwright.sync_api import sync_playwright, expect

    generation = fixture["generation"]
    with ThreadPoolExecutor(
        max_workers=1
    ) as fixture_db, sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=str(CHROME), headless=True)
        context = browser.new_context(viewport={"width": 1500, "height": 1050})
        context.add_init_script(
            'localStorage.setItem("auth-store", JSON.stringify('
            + json.dumps(
                {"accessToken": fixture["token"], "user": {"id": fixture["user_id"]}}
            )
            + "));"
            'localStorage.setItem("project-store", JSON.stringify('
            + json.dumps({"currentProject": fixture["project"]})
            + "));"
            f'localStorage.setItem("automation:webui-script-generation:v5:{fixture["user_id"]}:{fixture["project_id"]}", "{generation.id}");'
        )
        page = context.new_page()
        requests, errors = [], []
        page.on("request", lambda request: requests.append(request))
        page.on("pageerror", lambda error: errors.append(str(error)))
        try:
            page.goto(origin + "/web-testing/create")
            page.get_by_role("tab", name="脚本工作区", exact=True).click()
            expect(
                page.get_by_role("button", name="AI 修复", exact=True)
            ).to_have_count(0)
            expect(page.get_by_label("AI 脚本助手", exact=True)).to_have_count(0)
            page.get_by_role("button", name="AI 分析并修复", exact=True).click()
            with page.expect_response(
                lambda response: response.request.method == "POST"
                and response.url.endswith("/repair/")
            ) as repair_request:
                page.get_by_role("button", name="确认并开始", exact=True).click()
            assert repair_request.value.status == 202, repair_request.value.text()
            panel = page.locator(".repair-panel")
            expect(
                panel.get_by_role("heading", name="草稿 AI 修复", exact=True)
            ).to_be_visible()
            expect(panel.get_by_text("等待 AI 分析", exact=True)).to_be_visible()
            assert panel.evaluate(
                "(node) => node.getBoundingClientRect().top < innerHeight"
            )
            fixture_db.submit(set_candidate, generation).result()
            expect(panel.get_by_text("最终候选静态检查问题", exact=True)).to_be_visible(
                timeout=15000
            )
            expect(
                panel.get_by_text("未执行浏览器验证", exact=False).first
            ).to_be_visible()
            expect(
                panel.get_by_text("候选改变了原断言的预期值", exact=False).first
            ).to_be_visible()
            expect(
                panel.get_by_role("button", name="查看本轮执行详情", exact=True)
            ).to_have_count(0)
            assert page.locator(".repair-panel").evaluate(
                '(node) => Boolean(node.compareDocumentPosition(document.querySelector(".execution-section")) & Node.DOCUMENT_POSITION_FOLLOWING)'
            )
            page.screenshot(
                path=str(output / "draft-static-blocked.png"),
                full_page=True,
                animations="disabled",
            )
            fixture_db.submit(generation.refresh_from_db).result()
            assert generation.script_draft == SCRIPT
            page.reload()
            expect(
                page.get_by_role("tab", name="场景摘要", exact=True)
            ).to_have_attribute("aria-selected", "true")
            page.get_by_role("button", name="查看修复进度/结果", exact=True).click()
            expect(
                page.get_by_role("tab", name="脚本工作区", exact=True)
            ).to_have_attribute("aria-selected", "true")
            expect(
                panel.get_by_text("最终候选静态检查问题", exact=True)
            ).to_be_visible()
            for label in ["新建会话", "最近会话", "AI 修复"]:
                expect(
                    page.get_by_role("button", name=label, exact=True)
                ).to_have_count(0)
            for passed in (False, True):
                execution = fixture["draft_execution"]
                execution.status = "passed" if passed else "failed"
                fixture_db.submit(execution.save, update_fields=["status"]).result()
                fixture_db.submit(
                    set_candidate, generation, actual_execution=execution, passed=passed
                ).result()
                page.reload()
                page.get_by_role("button", name="查看修复进度/结果", exact=True).click()
                expect(
                    panel.get_by_text(
                        "执行通过" if passed else "执行失败", exact=False
                    ).first
                ).to_be_visible()
                expect(
                    panel.get_by_role("button", name="查看本轮执行详情", exact=True)
                ).to_be_visible()
                panel.get_by_role("button", name="查看本轮执行详情", exact=True).click()
                expect(panel.get_by_role("img").first).to_be_visible()
                expect(
                    panel.get_by_role("button", name="AI 修复", exact=True)
                ).to_have_count(0)
            assert not [
                request for request in requests if "/script-assistants/" in request.url
            ], "Draft page must never load saved-case assistants"
            assert not errors, errors
            page.screenshot(
                path=str(output / "draft-repair-verified.png"),
                full_page=True,
                animations="disabled",
            )
            execution.status = "failed"
            fixture_db.submit(execution.save, update_fields=["status"]).result()
            page.goto(origin + f'/reports/web/{fixture["project_id"]}/{execution.id}')
            expect(
                page.get_by_text("本记录未关联已保存的测试用例", exact=False)
            ).to_be_visible()
            expect(
                page.get_by_role("button", name="AI 修复", exact=True)
            ).to_have_count(0)
        except Exception:
            page.screenshot(
                path=str(output / "failure.png"), full_page=True, animations="disabled"
            )
            raise
        finally:
            context.close()
            browser.close()


def main():
    output = BACKEND / "logs" / "generation-repair-browser"
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix="draft-repair-browser-"
    ) as temp, patch.object(
        socket.socket, "connect", loopback_only(socket.socket.connect)
    ), patch.object(
        socket.socket, "connect_ex", loopback_only(socket.socket.connect_ex)
    ):
        fixture = bootstrap(Path(temp))
        add_fixture(fixture)
        from django.core.wsgi import get_wsgi_application

        server = make_server(
            "127.0.0.1",
            0,
            _static_or_django(get_wsgi_application()),
            handler_class=_QuietHandler,
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        with patch(
            "web_testing.tasks.repair_webui_script_generation_task.delay",
            return_value=SimpleNamespace(id="offline-repair-task"),
        ) as dispatch:
            thread.start()
            try:
                verify_ui(f"http://127.0.0.1:{server.server_port}", fixture, output)
                dispatch.assert_called_once()
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)
    print("PASS: isolated generation repair boundaries and result presentation")


if __name__ == "__main__":
    main()
