"""Exercise the saved-script assistant against disposable Django and a real UI.

Only model transport, MCP and script execution are faked. Session APIs, database
updates and Vue components are real. External sockets are forbidden. Build the
frontend first; this never connects to the configured NAS or a target website.
"""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import re
import socket
import tempfile
import threading
from types import SimpleNamespace
from unittest.mock import patch
from wsgiref.simple_server import make_server

from test_platform_reports_browser import BACKEND, CHROME, PNG, bootstrap, loopback_only
from test_project_knowledge_browser import _QuietHandler, _static_or_django


SCRIPT = """# 场景：验证测试状态
import os
from playwright.async_api import expect

async def run(page):
    await page.goto("http://fixture.invalid/status")
    # 验证：状态为 ok
    await expect(page.locator("#status")).to_have_text("ok")
    print(time.time_ns())
"""
CANDIDATE = SCRIPT.replace("import os", "import os\nimport time")
REVIEW_CANDIDATE = CANDIDATE.replace(
    "    print(time.time_ns())",
    '    await page.locator("#status").click()\n    print(time.time_ns())',
)
UNSAVED = "# 当前编辑器尚未保存的备注\n" + SCRIPT
RUNS = []
MODEL_CALLS = []
MODEL_IDS = []
WORKER_RESULTS = []
STREAM_RELEASE = threading.Event()
STREAM_RELEASE.set()


class FakeStreamingModel:
    def stream(self, messages, **kwargs):
        MODEL_CALLS.append(messages)
        if any(
            "等待取消验收" in str(getattr(message, "content", ""))
            for message in messages
        ):
            if not STREAM_RELEASE.wait(timeout=15):
                raise TimeoutError("Isolated cancellation fixture was not released")
        for piece in ["```python\n", CANDIDATE, "\n```"]:
            yield SimpleNamespace(content=piece)

    async def astream(self, messages, **kwargs):
        for piece in self.stream(messages, **kwargs):
            yield piece

    def invoke(self, *args, **kwargs):
        raise AssertionError("Assistant must use streaming, not invoke")

    async def ainvoke(self, *args, **kwargs):
        raise AssertionError("Assistant must use streaming, not ainvoke")


class FakeManager:
    current_llm = FakeStreamingModel()

    def _ensure_initialized(self):
        pass

    @staticmethod
    def _extract_stream_chunk_content(chunk):
        return chunk.content


def run_worker(args):
    from django.db import close_old_connections
    from web_testing.script_assistant import run_script_assistant_operation

    close_old_connections()
    try:
        result = run_script_assistant_operation(*args)
        WORKER_RESULTS.append(result)
        return result
    finally:
        close_old_connections()


def fake_runner(script, options=None, **kwargs):
    RUNS.append({"script": script, "options": options, **kwargs})
    screenshot = kwargs.get("failure_screenshot_path")
    if screenshot:
        target = Path(screenshot)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(PNG)
    fail = kwargs.get("environment_variables", {}).get("QA_LABEL") == "force-failure"
    status = "failed" if fail else "passed"
    return {
        "success": not fail,
        "operation_success": not fail,
        "runtime_assertion_count": 1,
        "evaluation_status": status,
        "error": "AssertionError: 隔离候选验证失败" if fail else "",
        "result": {
            "stdout": (
                "开始验证候选（隔离执行器）"
                if fail
                else "验证 状态为 ok 通过\n测试用例执行完毕"
            ),
            "stderr": "AssertionError: 隔离候选验证失败" if fail else "",
            "screenshot_path": screenshot,
            "test_file": "",
            "evaluation_status": status,
            "operation_success": not fail,
            "runtime_assertion_count": 1,
        },
    }


def add_fixtures(fixture):
    from django.contrib.auth import get_user_model
    from django.utils import timezone
    from rest_framework.test import APIRequestFactory, force_authenticate
    from ai_core.models import LLMConfiguration, ModelType
    from projects.models import Project
    from web_testing.execution_snapshots import capture_suite_snapshot
    from web_testing.models import (
        WebUIScriptAssistant,
        WebUITestCase,
        WebUITestCaseExecutionDetail,
        WebUITestExecution,
        WebUITestSuite,
    )
    from web_testing.script_assistant import (
        _validate_candidate,
        candidate_hash,
        model_info,
    )
    from web_testing.views import ExecuteWebUITestCaseView

    user = get_user_model().objects.get(pk=fixture["user_id"])
    project = Project.objects.get(pk=fixture["project_id"])
    model = LLMConfiguration.objects.create(
        model_type=ModelType.LLM,
        provider="openai",
        provider_name="隔离模型提供商",
        model_name="offline-assistant",
        api_key="offline-only",
        is_active=True,
        created_by=user,
    )
    next_model = LLMConfiguration.objects.create(
        model_type=ModelType.LLM,
        provider="openai",
        provider_name="下轮隔离提供商",
        model_name="offline-next-assistant",
        api_key="offline-only",
        is_active=True,
        created_by=user,
    )
    disabled_model = LLMConfiguration.objects.create(
        model_type=ModelType.LLM,
        provider="openai",
        provider_name="已停用的隔离提供商",
        model_name="disabled-model-not-needed-for-running",
        api_key="offline-only",
        is_active=False,
        created_by=user,
    )
    cases = []
    for name in (
        "对话编辑验收",
        "单用例修复验收",
        "套件子项修复验收",
        "人工确认修复验收",
    ):
        case = WebUITestCase.objects.create(
            title=name,
            description="验证页面状态并输出中文日志",
            user=user,
            project=project,
            variables=[{"name": "QA_LABEL", "value": "case-value"}],
        )
        case.set_script_content(SCRIPT)
        cases.append(case)

    request = APIRequestFactory().post(
        "/", {"options": {"timeout": 60, "headed": False}}, format="json"
    )
    force_authenticate(request, user)
    with patch(
        "web_testing.tasks.execute_webui_test_case_task.delay",
        return_value=SimpleNamespace(id="fixture-not-dispatched"),
    ):
        response = ExecuteWebUITestCaseView.as_view()(
            request, project_id=project.pk, pk=cases[1].pk
        )
    assert response.status_code in (200, 201, 202), response.data
    execution_id = response.data["data"]["execution_id"]
    execution = WebUITestExecution.objects.get(pk=execution_id)
    execution.status, execution.error_message = (
        "failed",
        "NameError: name 'time' is not defined",
    )
    execution.start_time = execution.end_time = timezone.now()
    execution.save()
    detail = execution.case_execution_detail
    detail.status, detail.error_message, detail.log = (
        "failed",
        execution.error_message,
        execution.error_message,
    )
    detail.save()

    suite = WebUITestSuite.objects.create(
        name="只修复失败子项验收",
        user=user,
        project=project,
        variables=[{"name": "QA_LABEL", "value": "suite-value"}],
    )
    suite.test_cases.add(cases[2])
    suite_execution = WebUITestExecution.objects.create(
        project=project,
        executor=user,
        exec_type="suite",
        name=suite.name,
        status="failed",
        error_message="子用例脚本名称错误",
    )
    suite_detail = capture_suite_snapshot(suite_execution, suite)
    suite_case = suite_detail.case_executions.get()
    suite_case.status, suite_case.log, suite_case.error_message = (
        "failed",
        execution.error_message,
        execution.error_message,
    )
    suite_case.save()
    review_execution = WebUITestExecution.objects.create(
        name=cases[3].title,
        project=project,
        executor=user,
        exec_type="case",
        status="failed",
        error_message=execution.error_message,
    )
    WebUITestCaseExecutionDetail.objects.create(
        execution=review_execution,
        test_case=cases[3],
        status="failed",
        error_message=execution.error_message,
        log=execution.error_message,
        source_script=cases[3].test_script_content,
        source_script_version=cases[3].script_version,
        source_edit_version=cases[3].edit_version,
        source_variables=cases[3].variables,
        execution_options={"timeout": 60, "headed": False},
    )
    # Reproduce an already-generated candidate: its actual scope checker rejects
    # the added action, but syntax and original assertions remain unchanged.
    review_session = WebUIScriptAssistant.objects.create(
        mode="repair",
        user=user,
        project=project,
        test_case=cases[3],
        execution=review_execution,
        model_config_id=disabled_model.id,
        model_info=model_info(disabled_model),
        source_script=cases[3].test_script_content,
        source_script_version=cases[3].script_version,
        source_edit_version=cases[3].edit_version,
        source_variables=cases[3].variables,
        source_options={"timeout": 60, "headed": False},
        candidate_script=REVIEW_CANDIDATE,
        candidate_hash=candidate_hash(REVIEW_CANDIDATE),
        status="candidate_ready",
        verification={
            "status": "unverified",
            "candidate_hash": candidate_hash(REVIEW_CANDIDATE),
        },
        message="候选脚本已生成",
    )
    review_session.blockers = _validate_candidate(review_session, REVIEW_CANDIDATE)
    assert {item["code"] for item in review_session.blockers} == {
        "REPAIR_SCOPE_CHANGED"
    }
    review_session.save(update_fields=["blockers"])
    fixture.update(
        project={"id": project.pk, "name": project.name, "project_type": "web"},
        model_id=model.pk,
        next_model_id=next_model.pk,
        edit_case_id=cases[0].pk,
        repair_case_id=cases[1].pk,
        repair_execution_id=execution.pk,
        suite_execution_id=suite_execution.pk,
        suite_case_id=suite_case.pk,
        suite_test_case_id=cases[2].pk,
        review_execution_id=review_execution.pk,
        review_case_id=cases[3].pk,
        review_session_id=str(review_session.id),
    )


def set_editor_text(page, editor, script):
    textarea = editor.locator(".monaco-editor textarea").first
    textarea.focus()
    page.keyboard.press("Meta+A")
    # Paste through Monaco's DOM input path. Typing a multiline string triggers
    # per-line autoindent; a synthetic clipboard payload avoids touching the
    # user's system clipboard and preserves the exact unsaved editor text.
    textarea.evaluate(
        """(node, text) => {
        const transfer = new DataTransfer();
        transfer.setData('text/plain', text);
        node.dispatchEvent(new ClipboardEvent('paste', {
            clipboardData: transfer, bubbles: true, cancelable: true
        }));
    }""",
        script,
    )


def verify_ui(origin, fixture, output):
    from playwright.sync_api import expect, sync_playwright

    page_errors, requests = [], []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(executable_path=str(CHROME), headless=True)
        context = browser.new_context(viewport={"width": 1600, "height": 1100})
        context.route(
            "**/*",
            lambda route: (
                route.continue_()
                if route.request.url.startswith(origin + "/")
                else route.abort()
            ),
        )
        fail_first_edit_restore = [True]

        def restore_fixture(route):
            if fail_first_edit_restore[0] and "mode=edit" in route.request.url:
                fail_first_edit_restore[0] = False
                route.fulfill(status=503, json={"message": "isolated restore failure"})
            else:
                route.continue_()

        context.route(
            re.compile(re.escape(origin) + r"/api/.*?/script-assistants/\?"),
            restore_fixture,
        )
        context.add_init_script(
            "localStorage.setItem('auth-store', JSON.stringify("
            + json.dumps(
                {
                    "accessToken": fixture["token"],
                    "refreshToken": None,
                    "user": {"id": fixture["user_id"], "username": "reports-offline"},
                }
            )
            + "));localStorage.setItem('project-store', JSON.stringify("
            + json.dumps({"currentProject": fixture["project"]})
            + "));"
        )
        page = context.new_page()
        page.on("pageerror", lambda error: page_errors.append(str(error)))
        page.on("request", lambda request: requests.append(request))
        try:
            page.goto(origin + "/web-testing/test-cases")
            row = page.get_by_role("row").filter(has_text="对话编辑验收")
            row.get_by_role("button", name="编辑", exact=True).click()
            editor = page.locator(".el-drawer").filter(has_text="编辑测试脚本")
            set_editor_text(page, editor, UNSAVED)
            editor.get_by_role("button", name="AI 对话编辑", exact=True).click()
            panel = page.get_by_label("AI 脚本助手", exact=True)
            expect(panel).to_be_visible()
            expect(
                panel.get_by_role("button", name="重新获取状态", exact=True)
            ).to_be_visible()
            expect(
                panel.get_by_role("button", name="新建会话", exact=True)
            ).to_be_disabled()
            panel.get_by_role("button", name="重新获取状态", exact=True).click()
            expect(
                panel.get_by_role("button", name="重新获取状态", exact=True)
            ).to_have_count(0)
            expect(
                panel.get_by_role("button", name="最近会话", exact=True)
            ).to_have_count(0)
            expect(panel.locator(".assistant-config .el-select")).to_have_count(1)
            expect(
                panel.get_by_role("button", name="发送", exact=True)
            ).to_be_disabled()
            panel.get_by_role("button", name="发送说明", exact=True).hover()
            expect(
                page.get_by_role("tooltip").filter(
                    has_text="以当前编辑器里的脚本为基础修改"
                )
            ).to_be_visible()
            assert not MODEL_CALLS, "Reading action help must not invoke a model"
            panel.locator(".assistant-config .el-select").click()
            page.get_by_role(
                "option", name="隔离模型提供商 · offline-assistant", exact=True
            ).click()
            panel.get_by_placeholder("说明你希望如何修改脚本…").fill(
                "保留断言，修复变量错误并添加中文日志。"
            )
            with page.expect_response(
                lambda response: response.request.method == "POST"
                and response.url.endswith("/script-assistants/")
            ) as created:
                panel.get_by_role("button", name="发送", exact=True).click()
            response = created.value
            assert response.status == 202, response.text()
            payload = response.request.post_data_json
            assert payload["script_content"] == UNSAVED, payload["script_content"]
            expect(
                panel.get_by_role("button", name="采用到编辑器", exact=True)
            ).to_be_enabled(timeout=20000)
            assert MODEL_IDS[-1] == fixture["model_id"], MODEL_IDS
            assert not RUNS, "A conversation edit must not execute a browser script"
            assert not [
                r for r in requests if r.method == "PATCH"
            ], "An AI reply must not save the case"
            actions = panel.locator(".conversation-section .message-actions")
            expect(
                actions.get_by_role("button", name="发送", exact=True)
            ).to_be_disabled()
            expect(
                actions.get_by_role("button", name="继续调整候选", exact=True)
            ).to_be_enabled()
            expect(
                panel.locator(".candidate-actions").get_by_role(
                    "button", name="继续调整候选", exact=True
                )
            ).to_have_count(0)
            help_calls = len(MODEL_CALLS)
            actions.get_by_role("button", name="继续调整候选说明", exact=True).click()
            expect(
                page.get_by_role("tooltip").filter(
                    has_text="以最新的候选脚本为基础继续修改"
                )
            ).to_be_visible()
            page.wait_for_function(
                """() => [...document.querySelectorAll('.assistant-action-help-popper')].some(node => node.textContent.includes('以最新的候选脚本为基础继续修改') && getComputedStyle(node).opacity === '1' && getComputedStyle(node).visibility === 'visible' && node.getBoundingClientRect().height > 0 && !node.className.includes('leave'))"""
            )
            assert len(MODEL_CALLS) == help_calls
            page.screenshot(
                path=str(output / "chat-action-help.png"),
                full_page=True,
                animations="disabled",
            )
            actions.get_by_role("button", name="发送说明", exact=True).focus()
            expect(
                page.get_by_role("tooltip").filter(
                    has_text="以当前编辑器里的脚本为基础修改"
                )
            ).to_be_visible()
            page.screenshot(
                path=str(output / "chat-candidate.png"),
                full_page=True,
                animations="disabled",
            )
            editor.get_by_role("button", name="收起 AI 助手", exact=True).click()
            editor.get_by_role("button", name="AI 对话编辑", exact=True).click()
            expect(
                panel.get_by_role("button", name="采用到编辑器", exact=True)
            ).to_be_enabled()
            panel.locator(".assistant-config .el-select").click()
            page.get_by_role(
                "option", name="下轮隔离提供商 · offline-next-assistant", exact=True
            ).click()
            panel.get_by_placeholder("说明你希望如何修改脚本…").fill(
                "继续保留业务断言，确认日志准确。"
            )
            with page.expect_response(
                lambda response: response.request.method == "POST"
                and response.url.endswith("/messages/")
            ) as continued:
                panel.get_by_role("button", name="继续调整候选", exact=True).click()
            assert continued.value.status == 202, continued.value.text()
            assert continued.value.request.post_data_json["use_candidate"] is True
            assert (
                continued.value.request.post_data_json["model_config_id"]
                == fixture["next_model_id"]
            )
            expect(panel.locator(".assistant-config .el-tag")).to_contain_text(
                "offline-next-assistant"
            )
            expect(
                panel.get_by_role("button", name="采用到编辑器", exact=True)
            ).to_be_enabled()
            assert MODEL_IDS[-1] == fixture["next_model_id"], MODEL_IDS
            assert not RUNS, "Continuing a candidate must not execute it"
            panel.get_by_role("button", name="调试验证", exact=True).click()
            page.get_by_role("button", name="确认执行", exact=True).click()
            expect(panel.get_by_text("候选已实际验证", exact=True)).to_be_visible(
                timeout=30000
            )
            assert len(RUNS) == 1, (RUNS, WORKER_RESULTS)
            expect(
                panel.locator(".attempt-section").get_by_role(
                    "button", name=re.compile("第 .* 次验证")
                )
            ).to_have_count(1)
            expect(
                panel.get_by_role("button", name="查看调试验证详情", exact=True)
            ).to_have_count(0)
            page.screenshot(
                path=str(output / "chat-verified.png"),
                full_page=True,
                animations="disabled",
            )
            set_editor_text(page, editor, UNSAVED + "\n# 生成候选后的手工修改\n")
            panel.get_by_role("button", name="采用到编辑器", exact=True).click()
            expect(page.get_by_text(re.compile("编辑器已有.*手工修改"))).to_be_visible()
            assert not [r for r in requests if r.method == "PATCH"]
            set_editor_text(page, editor, UNSAVED)
            panel.get_by_role("button", name="采用到编辑器", exact=True).click()
            assert not [
                r for r in requests if r.method == "PATCH"
            ], "Adoption into editor must remain unsaved"
            editor.get_by_role("button", name="收起 AI 助手", exact=True).click()
            with page.expect_response(
                lambda response: response.request.method == "PATCH"
                and f'/test-cases/{fixture["edit_case_id"]}/' in response.url
            ) as saved:
                editor.get_by_role("button", name="保存", exact=True).click()
            assert saved.value.status == 200, saved.value.text()
            assert (
                "import time"
                in saved.value.request.post_data_json["test_script_content"]
            )
            page.goto(origin + "/web-testing/test-executions")
            row = page.get_by_role("row").filter(has_text="单用例修复验收")
            row.get_by_role("button", name=re.compile("详情")).click()
            page.get_by_role("button", name="AI 修复", exact=True).click()
            panel = page.get_by_label("AI 脚本助手", exact=True)
            expect(panel).to_be_visible()
            panel.get_by_role("button", name="确认并开始 AI 修复", exact=True).click()
            page.get_by_role("button", name="确认执行", exact=True).click()
            expect(panel.get_by_text("候选已实际验证", exact=True)).to_be_visible(
                timeout=30000
            )
            assert len(RUNS) == 2, (RUNS, WORKER_RESULTS)
            assert RUNS[-1]["options"]["headed"] is False, RUNS[-1]
            expect(
                panel.locator(".attempt-section").get_by_role(
                    "button", name=re.compile("第 .* 次验证")
                )
            ).to_have_count(1)
            panel.get_by_role("button", name=re.compile("第 1 次验证")).click()
            expect(panel.get_by_role("img").first).to_be_visible()
            page.wait_for_function(
                "document.querySelector('[aria-label=\"AI 脚本助手\"] img')?.naturalWidth > 0"
            )
            panel.get_by_role(
                "button", name="查看原始 stdout / stderr / log", exact=True
            ).click()
            expect(panel.get_by_text("测试用例执行完毕", exact=False)).to_be_visible()
            assert page.get_by_role("dialog", name="执行详情", exact=True).evaluate(
                """dialog => {
                const rect = dialog.getBoundingClientRect();
                const x = rect.left + rect.width / 2;
                const y = Math.min(window.innerHeight - 2, rect.bottom + 6);
                return y <= rect.bottom || !dialog.contains(document.elementFromPoint(x, y));
            }"""
            ), "Nested assistant result must not paint below the execution dialog"
            page.screenshot(
                path=str(output / "repair-verified.png"),
                full_page=True,
                animations="disabled",
            )
            panel.get_by_role("button", name="采用并保存", exact=True).click()
            with page.expect_response(
                lambda response: response.request.method == "POST"
                and response.url.endswith("/apply/")
            ) as adopted:
                page.locator(".el-message-box").get_by_role(
                    "button", name="采用并保存", exact=True
                ).click()
            assert adopted.value.status == 200, adopted.value.text()
            expect(panel.get_by_text("已采用", exact=True)).to_be_visible()
            page.screenshot(
                path=str(output / "repair-adopted.png"),
                full_page=True,
                animations="disabled",
            )
            page.goto(origin + "/web-testing/test-executions")
            page.get_by_role("row").filter(has_text="只修复失败子项验收").get_by_role(
                "button", name="查看详情", exact=True
            ).click()
            page.get_by_role("button", name="AI 修复", exact=True).click()
            panel = page.get_by_label("AI 脚本助手", exact=True)
            expect(panel).to_be_visible()
            panel.get_by_role("button", name="确认并开始 AI 修复", exact=True).click()
            with page.expect_response(
                lambda response: response.request.method == "POST"
                and response.url.endswith("/script-assistants/")
            ) as suite_created:
                page.get_by_role("button", name="确认执行", exact=True).click()
            assert suite_created.value.status == 202, suite_created.value.text()
            payload = suite_created.value.request.post_data_json
            assert payload["execution_id"] == fixture["suite_execution_id"], payload
            assert payload["suite_case_id"] == fixture["suite_case_id"], payload
            expect(panel.get_by_text("候选已实际验证", exact=True)).to_be_visible(
                timeout=30000
            )
            assert len(RUNS) == 3, (RUNS, WORKER_RESULTS)
            assert RUNS[-1]["environment_variables"]["QA_LABEL"] == "suite-value", RUNS[
                -1
            ]
            page.screenshot(
                path=str(output / "suite-item-verified.png"),
                full_page=True,
                animations="disabled",
            )
            page.goto(origin + "/web-testing/test-executions")
            page.get_by_role("row").filter(has_text="人工确认修复验收").get_by_role(
                "button", name="查看详情", exact=True
            ).click()
            calls_before_review = len(MODEL_CALLS)
            page.get_by_role("button", name="AI 修复", exact=True).click()
            panel = page.get_by_label("AI 脚本助手", exact=True)
            manual_save = panel.get_by_role("button", name="人工确认并保存", exact=True)
            expect(manual_save).to_be_enabled(timeout=10000)
            panel.get_by_text("完整候选代码", exact=True).click()
            expect(panel.locator(".candidate-section pre")).to_contain_text(
                'await page.locator("#status").click()'
            )
            run_candidate = panel.get_by_role("button", name="运行验证", exact=True)
            expect(run_candidate).to_be_enabled()
            options = panel.locator(".verification-options")
            options.get_by_role("button", name="添加", exact=True).click()
            options.get_by_placeholder("变量名", exact=True).fill("QA_LABEL")
            options.get_by_placeholder("本次值", exact=True).fill("force-failure")
            verify_path = f'/script-assistants/{fixture["review_session_id"]}/verify/'
            run_candidate.click()
            confirmation = page.locator(".el-message-box")
            expect(confirmation).to_contain_text("范围")
            confirmation.get_by_role("button", name="取消", exact=True).click()
            assert not [
                r
                for r in requests
                if r.method == "POST" and r.url.endswith(verify_path)
            ]
            assert len(RUNS) == 3

            run_candidate.click()
            with page.expect_response(
                lambda response: response.request.method == "POST"
                and response.url.endswith(verify_path)
            ) as first_verification:
                confirmation.get_by_role(
                    "button", name="确认风险并运行", exact=True
                ).click()
            assert (
                first_verification.value.status == 202
            ), first_verification.value.text()
            verify_payload = first_verification.value.request.post_data_json
            assert (
                verify_payload["confirm_execution"] is True
                and verify_payload["acknowledge_review"] is True
            )
            expect(panel.get_by_text("实际验证失败", exact=True)).to_be_visible(
                timeout=20000
            )
            assert len(RUNS) == 4, (RUNS, WORKER_RESULTS)
            panel.get_by_role("button", name=re.compile("第 1 次验证")).click()
            expect(panel.get_by_role("img").first).to_be_visible()
            panel.get_by_role(
                "button", name="查看原始 stdout / stderr / log", exact=True
            ).click()
            expect(
                panel.get_by_text("AssertionError: 隔离候选验证失败", exact=False).last
            ).to_be_visible()
            page.screenshot(
                path=str(output / "repair-manual-verification-failed.png"),
                full_page=True,
                animations="disabled",
            )
            manual_save.click()
            expect(confirmation).to_contain_text("未通过")
            expect(confirmation).not_to_contain_text("尚未实际验证")
            confirmation.get_by_role("button", name="取消", exact=True).click()

            # Retry is another explicit single run, not a new model repair.
            options.get_by_placeholder("本次值", exact=True).fill("review-value")
            expect(run_candidate).to_be_enabled()
            run_candidate.click()
            with page.expect_response(
                lambda response: response.request.method == "POST"
                and response.url.endswith(verify_path)
            ) as second_verification:
                confirmation.get_by_role(
                    "button", name="确认风险并运行", exact=True
                ).click()
            assert (
                second_verification.value.status == 202
            ), second_verification.value.text()
            expect(panel.get_by_text("实际验证通过", exact=True)).to_be_visible(
                timeout=20000
            )
            assert len(RUNS) == 5, (RUNS, WORKER_RESULTS)
            expect(
                panel.locator(".attempt-section").get_by_role(
                    "button", name=re.compile("第 .* 次验证")
                )
            ).to_have_count(2)
            expect(
                panel.get_by_role("button", name="查看调试验证详情", exact=True)
            ).to_have_count(0)
            assert RUNS[-1]["script"] == REVIEW_CANDIDATE
            assert RUNS[-1]["options"]["headed"] is False
            assert RUNS[-1]["environment_variables"]["QA_LABEL"] == "review-value"
            panel.get_by_role("button", name=re.compile("第 2 次验证")).click()
            expect(panel.get_by_role("img").first).to_be_visible()
            panel.get_by_role(
                "button", name="查看原始 stdout / stderr / log", exact=True
            ).click()
            expect(panel.get_by_text("测试用例执行完毕", exact=False)).to_be_visible()
            original_case = context.request.get(
                origin
                + f'/api/v1/projects/{fixture["project_id"]}/web-testing/test-cases/{fixture["review_case_id"]}/',
                headers={"Authorization": f'Bearer {fixture["token"]}'},
            )
            assert original_case.status == 200, original_case.text()
            assert (
                original_case.json()["data"]["test_script_content"] == SCRIPT.strip()
            ), "Verifying a candidate must not overwrite the saved case"
            assert (
                len(MODEL_CALLS) == calls_before_review
            ), "Candidate verification must not call the model again"
            page.screenshot(
                path=str(output / "repair-manual-verification-passed.png"),
                full_page=True,
                animations="disabled",
            )

            review_path = f'/script-assistants/{fixture["review_session_id"]}/apply/'
            manual_save.click()
            confirmation = page.locator(".el-message-box")
            expect(confirmation).to_contain_text("验证")
            expect(confirmation).not_to_contain_text("尚未实际验证")
            confirmation.get_by_role("button", name="取消", exact=True).click()
            assert not [
                r
                for r in requests
                if r.method == "POST" and r.url.endswith(review_path)
            ]
            manual_save.click()
            with page.expect_response(
                lambda response: response.request.method == "POST"
                and response.url.endswith(review_path)
            ) as manually_adopted:
                confirmation.get_by_role(
                    "button", name="人工确认并保存", exact=True
                ).click()
            assert manually_adopted.value.status == 200, manually_adopted.value.text()
            assert (
                manually_adopted.value.request.post_data_json["acknowledge_review"]
                is True
            )
            body = manually_adopted.value.json()["data"]
            assert body["status"] == "applied", body
            expect(confirmation).not_to_be_visible()
            assert body["verification"]["status"] == "passed", body
            assert {item["code"] for item in body["blockers"]} == {
                "REPAIR_SCOPE_CHANGED"
            }
            assert (
                body["quality_report"]["manual_adoption"]["acknowledged_by"]
                == fixture["user_id"]
            )
            assert (
                body["quality_report"]["manual_adoption"]["candidate_hash"]
                == body["candidate_hash"]
            )
            assert body["adoption"]["can_apply"] is False
            expect(panel.get_by_text("实际验证通过", exact=True)).to_be_visible()
            assert len(RUNS) == 5, "Manual adoption must not execute a candidate"
            assert (
                len(MODEL_CALLS) == calls_before_review
            ), "Restoring and adopting must not regenerate"
            page.screenshot(
                path=str(output / "repair-manually-adopted.png"),
                full_page=True,
                animations="disabled",
            )
            panel.get_by_role("button", name="最近会话", exact=True).click()
            expect(
                panel.get_by_role("button", name="采用并保存", exact=True)
            ).to_be_disabled()
            expect(panel.get_by_text("实际验证通过", exact=True)).to_be_visible()
            page.goto(origin + "/web-testing/test-cases")
            page.get_by_role("row").filter(has_text="对话编辑验收").get_by_role(
                "button", name="编辑", exact=True
            ).click()
            editor = page.locator(".el-drawer").filter(has_text="编辑测试脚本")
            editor.get_by_role("button", name="AI 对话编辑", exact=True).click()
            panel = page.get_by_label("AI 脚本助手", exact=True)
            expect(
                panel.get_by_role("button", name="新建会话", exact=True)
            ).to_be_enabled()
            panel.get_by_role("button", name="新建会话", exact=True).click()
            page.locator(".el-message-box").get_by_role(
                "button", name="取消", exact=True
            ).click()
            expect(panel.locator(".candidate-section")).to_be_visible()
            panel.get_by_role("button", name="新建会话", exact=True).click()
            with page.expect_response(
                lambda response: response.request.method == "POST"
                and response.url.endswith("/reset/")
            ) as reset:
                page.locator(".el-message-box").get_by_role(
                    "button", name="清空并新建", exact=True
                ).click()
            assert reset.value.status == 200, reset.value.text()
            blank = reset.value.json()["data"]
            assert blank["messages"] == [] and blank["candidate_script"] == "", blank
            assert blank["attempts"] == [] and blank["verification"] == {}, blank
            expect(panel.locator(".candidate-section")).to_have_count(0)
            # Closing/reopening remounts the assistant and reads the real API.
            editor.get_by_role("button", name="收起 AI 助手", exact=True).click()
            editor.get_by_role("button", name="AI 对话编辑", exact=True).click()
            expect(
                panel.get_by_role("button", name="新建会话", exact=True)
            ).to_be_enabled()
            expect(panel.locator(".candidate-section")).to_have_count(0)
            expect(panel.locator(".message-list article")).to_have_count(0)
            panel.locator(".assistant-config .el-select").click()
            page.get_by_role(
                "option", name="隔离模型提供商 · offline-assistant", exact=True
            ).click()
            STREAM_RELEASE.clear()
            panel.get_by_placeholder("说明你希望如何修改脚本…").fill(
                "等待取消验收，请保留业务动作。"
            )
            panel.get_by_role("button", name="发送", exact=True).click()
            expect(panel.get_by_text("正在生成候选脚本", exact=True)).to_be_visible(
                timeout=15000
            )
            expect(
                panel.get_by_role("button", name="新建会话", exact=True)
            ).to_be_disabled()
            assert MODEL_IDS[-1] == fixture["model_id"], MODEL_IDS
            panel.locator(".assistant-config .el-select").click()
            page.get_by_role(
                "option", name="下轮隔离提供商 · offline-next-assistant", exact=True
            ).click()
            # A real polling response must leave the next-round model alone.
            with page.expect_response(
                lambda response: response.request.method == "GET"
                and f'/script-assistants/{blank["id"]}/' in response.url
            ):
                pass
            expect(panel.locator(".assistant-config .el-select")).to_contain_text(
                "offline-next-assistant"
            )
            assert MODEL_IDS[-1] == fixture["model_id"], MODEL_IDS
            panel.get_by_role("button", name="取消任务", exact=True).click()
            page.locator(".el-message-box").get_by_role(
                "button", name="取消任务", exact=True
            ).click()
            expect(panel.get_by_text("已取消", exact=True)).to_be_visible()
            STREAM_RELEASE.set()
            expect(
                panel.get_by_role("button", name="最近会话", exact=True)
            ).to_have_count(0)
            expect(panel.get_by_text("已取消", exact=True)).to_be_visible()
            panel.get_by_placeholder("说明你希望如何修改脚本…").fill(
                "继续基于当前编辑器完善中文日志。"
            )
            with page.expect_response(
                lambda response: response.request.method == "POST"
                and response.url.endswith("/messages/")
            ) as next_round:
                panel.get_by_role("button", name="发送", exact=True).click()
            assert next_round.value.status == 202, next_round.value.text()
            assert (
                next_round.value.request.post_data_json["model_config_id"]
                == fixture["next_model_id"]
            )
            expect(
                panel.get_by_role("button", name="采用到编辑器", exact=True)
            ).to_be_enabled(timeout=20000)
            assert MODEL_IDS[-1] == fixture["next_model_id"], MODEL_IDS
            assert len(RUNS) == 5, "Cancelling a conversation must not start a browser"
            page.screenshot(
                path=str(output / "reset-and-model-switch.png"),
                full_page=True,
                animations="disabled",
            )
            assert not page_errors, page_errors
        except Exception:
            page.screenshot(
                path=str(output / "failure.png"), full_page=True, animations="disabled"
            )
            raise
        finally:
            STREAM_RELEASE.set()
            context.close()
            browser.close()


def main():
    output = BACKEND / "logs" / "script-assistant-browser"
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix="automation-script-assistant-"
    ) as temp, patch.object(
        socket.socket,
        "connect",
        loopback_only(socket.socket.connect),
    ), patch.object(
        socket.socket, "connect_ex", loopback_only(socket.socket.connect_ex)
    ):
        fixture = bootstrap(Path(temp))
        add_fixtures(fixture)
        from django.core.wsgi import get_wsgi_application

        server = make_server(
            "127.0.0.1",
            0,
            _static_or_django(get_wsgi_application()),
            handler_class=_QuietHandler,
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        with ThreadPoolExecutor(max_workers=1) as workers, patch(
            "web_testing.script_assistant.get_llm_manager",
            side_effect=lambda config_id: (MODEL_IDS.append(config_id), FakeManager())[
                1
            ],
        ), patch("web_testing.tasks._run_test_script", side_effect=fake_runner), patch(
            "web_testing.tasks.run_script_assistant_operation_task.apply_async"
        ) as dispatch:

            def enqueue(*, args, task_id, **kwargs):
                workers.submit(run_worker, args)
                return SimpleNamespace(id=task_id)

            dispatch.side_effect = enqueue
            thread.start()
            try:
                verify_ui(f"http://127.0.0.1:{server.server_port}", fixture, output)
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)
        from web_testing.models import (
            WebUIScriptAssistant,
            WebUITestCase,
            WebUITestExecution,
        )

        assert (
            "import time"
            in WebUITestCase.objects.get(pk=fixture["edit_case_id"]).test_script_content
        )
        assert (
            "import time"
            in WebUITestCase.objects.get(
                pk=fixture["repair_case_id"]
            ).test_script_content
        )
        assert (
            WebUITestExecution.objects.get(pk=fixture["repair_execution_id"]).status
            == "failed"
        )
        assert (
            WebUITestExecution.objects.get(pk=fixture["suite_execution_id"]).status
            == "failed"
        )
        assert (
            "import time"
            not in WebUITestCase.objects.get(
                pk=fixture["suite_test_case_id"]
            ).test_script_content
        )
        assert (
            WebUITestCase.objects.get(pk=fixture["review_case_id"]).test_script_content
            == REVIEW_CANDIDATE.strip()
        )
        assert (
            WebUITestExecution.objects.get(pk=fixture["review_execution_id"]).status
            == "failed"
        )
        review = WebUIScriptAssistant.objects.get(pk=fixture["review_session_id"])
        assert review.status == "applied" and review.verification["status"] == "passed"
        assert [attempt["execution_status"] for attempt in review.attempts] == [
            "failed",
            "passed",
        ]
        assert all(attempt["has_screenshot"] for attempt in review.attempts)
        assert review.verification["candidate_hash"] == review.candidate_hash
        authorizations = [attempt["authorization"] for attempt in review.attempts]
        assert len({item["task_id"] for item in authorizations}) == 2
        assert len({item["revision"] for item in authorizations}) == 2
        assert all(
            item["acknowledge_review"] is True
            and item["candidate_hash"] == review.candidate_hash
            and item["acknowledged_by"] == fixture["user_id"]
            and item["acknowledged_at"]
            for item in authorizations
        )
    print("PASS: isolated saved-script assistant browser integration")


if __name__ == "__main__":
    main()
