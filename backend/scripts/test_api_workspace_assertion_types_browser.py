"""Regression for typed API-workspace assertion literals in an isolated browser.

The shared harness serves the already-built Vue dist with Django on a random
loopback port.  It uses a disposable SQLite database, blocks every external
socket, mocks the model/queue and permits only the fixture GET /health request.

Run after building the frontend:
    python backend/scripts/test_api_workspace_assertion_types_browser.py

Against the old dist this intentionally fails at the first persisted type
assertion: editing ``401`` stores a string.  That is the reproduction evidence;
the type-label checks run only after this persistence boundary passes.
"""
from __future__ import annotations

import json

import test_api_workspace_browser as harness


def seed(fixture):
    """Create a root and one editable child with deliberately typed literals."""
    from api_testing.models import APIWorkspace

    root = APIWorkspace.objects.create(
        project_id=fixture["project_id"],
        owner_id=fixture["user_id"],
        title="断言字面量类型验收计划",
        model_id=fixture["model_id"],
        spec_id=fixture["spec_id"],
        endpoint_ids=[fixture["endpoint_id"]],
    )
    workspace = APIWorkspace.objects.create(
        project_id=fixture["project_id"],
        owner_id=fixture["user_id"],
        parent=root,
        title="401 数字与字符串断言",
        model_id=fixture["model_id"],
        spec_id=fixture["spec_id"],
        endpoint_ids=[fixture["endpoint_id"]],
        draft={
            "version": 1,
            "config": {
                "name": "401 数字与字符串断言",
                "base_url": "https://example.test",
                "variables": {"expected_code": "401"},
            },
            "teststeps": [
                {
                    "name": "读取健康状态",
                    "endpoint_id": fixture["endpoint_id"],
                    "request": {"method": "GET", "url": "/health"},
                    # The first three rows are the reported regression shape.
                    # Remaining rows prove untouched values survive an edit.
                    "validate": [
                        {"eq": ["status_code", 200]},
                        {"eq": ["body.code", "401"]},
                        {"eq": ["body.string_code", "401"]},
                        {"eq": ["body.decimal", 0]},
                        {"eq": ["body.variable", "${expected_code}"]},
                        {"eq": ["body.null_value", None]},
                        {"eq": ["body.list_value", [401, "ok"]]},
                        {"eq": ["body.object_value", {"code": 401, "ok": True}]},
                    ],
                }
            ],
        },
    )
    root.generation = {
        "status": "passed",
        "phase": "finished",
        "source_revision": 0,
        "active_scenario_id": workspace.id,
        "scenario_ids": [workspace.id],
    }
    root.save(update_fields=["generation"])
    return root.id, workspace.id


def expected_checks(*, code, decimal, status_operator="eq"):
    return [
        {status_operator: ["status_code", 200]},
        {"eq": ["body.code", code]},
        {"eq": ["body.string_code", "401"]},
        {"eq": ["body.decimal", decimal]},
        {"eq": ["body.variable", "${expected_code}"]},
        {"eq": ["body.null_value", None]},
        {"eq": ["body.list_value", [401, "ok"]]},
        {"eq": ["body.object_value", {"code": 401, "ok": True}]},
    ]


def open_assertions(editor):
    if not editor.locator(".assertion-row").first.is_visible():
        editor.get_by_role("button", name="提取与断言", exact=True).click()
    editor.locator(".assertion-row").first.wait_for(state="visible")


def assertion_input(editor, index):
    labelled = editor.get_by_role("textbox", name=f"断言 {index + 1} 期望值", exact=True)
    # The current component retains this aria-label.  Old dist has no label,
    # so use its established final input in the same assertion row to reach
    # the intended persistence regression before new-UI-only checks run.
    if labelled.count():
        return labelled
    return editor.locator(".assertion-row").nth(index).get_by_role("textbox").last


def assertion_type(editor, index):
    return editor.locator(".assertion-row").nth(index).get_by_test_id(
        "assertion-expected-type"
    )


def save_draft(page, origin, workspace_id):
    with page.expect_response(
        lambda response: response.url.endswith(f"/workspaces/{workspace_id}/")
        and response.request.method == "PATCH"
    ) as saved:
        page.get_by_role("button", name="保存草稿", exact=True).click()
    assert saved.value.status == 200, saved.value.text()


def verify(origin, fixture, output):
    from api_testing.models import APIWorkspace
    from playwright.sync_api import expect, sync_playwright

    root_id, workspace_id = harness.database(lambda: seed(fixture))
    fixture["http_body"] = {
        "code": 401,
        "string_code": "401",
        "decimal": 1.25,
        "variable": "401",
        "null_value": None,
        "list_value": [401, "ok"],
        "object_value": {"code": 401, "ok": True},
    }

    def stored():
        return harness.database(lambda: APIWorkspace.objects.get(pk=workspace_id))

    with sync_playwright() as pw:
        browser = pw.chromium.launch(executable_path=str(harness.CHROME), headless=True)
        context = browser.new_context(viewport={"width": 1440, "height": 1200})
        context.route(
            "**/*",
            lambda route: route.continue_()
            if route.request.url.startswith(origin + "/")
            else route.abort(),
        )
        context.add_init_script(
            "localStorage.setItem('auth-store', JSON.stringify("
            + json.dumps(
                {
                    "accessToken": fixture["token"],
                    "refreshToken": None,
                    "user": {"id": fixture["user_id"], "username": "workspace-offline"},
                }
            )
            + ")); localStorage.setItem('project-store', JSON.stringify("
            + json.dumps(
                {
                    "currentProject": {
                        "id": fixture["project_id"],
                        "name": "API 工作区隔离验收",
                        "project_type": "api",
                    }
                }
            )
            + "));"
        )
        page = context.new_page()
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        try:
            page.goto(origin + f"/api-testing/workspace/documents?workspace_id={root_id}")
            editor = page.locator(".step-editor").first
            expect(editor).to_be_visible(timeout=15000)
            open_assertions(editor)

            # Keep the reported initial value as a string, then make the exact
            # user edit that must become an integer after a new UI build.
            code_input = assertion_input(editor, 1)
            initial_type = assertion_type(editor, 1)
            if initial_type.count():
                # New UI quotes a numeric-looking persisted string so it
                # cannot be mistaken for the numeric literal the user enters.
                expect(code_input).to_have_value('"401"')
                expect(initial_type).to_have_text("类型：字符串")
            else:
                # Old dist has neither the type tag nor quoted string display;
                # retain this path solely to reproduce the persistence defect.
                expect(code_input).to_have_value("401")
            code_input.fill("")
            code_input.press_sequentially("401")

            # Decimal input must remain editable during every parent echo.
            decimal_input = assertion_input(editor, 3)
            decimal_input.fill("")
            decimal_input.press_sequentially("1.25")
            expect(decimal_input).to_have_value("1.25")

            save_draft(page, origin, workspace_id)
            checks = stored().draft["teststeps"][0]["validate"]
            assert checks == expected_checks(code=401, decimal=1.25), checks
            assert type(checks[1]["eq"][1]) is int, checks[1]
            assert type(checks[2]["eq"][1]) is str, checks[2]
            assert type(checks[3]["eq"][1]) is float, checks[3]
            assert checks[4]["eq"][1] == "${expected_code}", checks[4]
            assert checks[5]["eq"][1] is None, checks[5]

            # New build-only display contract.  Old dist never reaches here:
            # it has already failed at the persisted integer assertion above.
            page.reload()
            expect(editor).to_be_visible(timeout=15000)
            open_assertions(editor)
            expect(assertion_input(editor, 1)).to_have_value("401")
            expect(assertion_type(editor, 1)).to_have_text("类型：数字")
            expect(assertion_input(editor, 2)).to_have_value('"401"')
            expect(assertion_type(editor, 2)).to_have_text("类型：字符串")
            expect(assertion_input(editor, 3)).to_have_value("1.25")
            expect(assertion_type(editor, 3)).to_have_text("类型：数字")
            expect(assertion_input(editor, 4)).to_have_value("${expected_code}")
            expect(assertion_type(editor, 4)).to_have_text("类型：变量（运行时确定）")
            expect(assertion_input(editor, 5)).to_have_value("null")
            expect(assertion_type(editor, 5)).to_have_text("类型：空值（null）")
            expect(assertion_input(editor, 6)).to_have_value('[401,"ok"]')
            expect(assertion_type(editor, 6)).to_have_text("类型：数组")
            expect(assertion_input(editor, 7)).to_have_value('{"code":401,"ok":true}')
            expect(assertion_type(editor, 7)).to_have_text("类型：对象")

            # Editing an operator must not serialize the untouched values back
            # to strings, null, or another comparator shape.
            status_row = editor.locator(".assertion-row").nth(0)
            status_row.locator(".el-select").click()
            page.get_by_role("option", name="大于等于", exact=True).click()
            save_draft(page, origin, workspace_id)
            assert stored().draft["teststeps"][0]["validate"] == expected_checks(
                code=401, decimal=1.25, status_operator="ge"
            )

            # Strict runner comparison verifies numeric and string body values
            # separately, before changing body.code back to a string literal.
            harness.execute_debug(page)
            debug = stored().debug_result
            records = debug["step_datas"][0]["validators"]["validate_extractor"]
            assert debug["success"] is True, debug
            assert records[1]["passed"] is True
            assert type(records[1]["check_value"]) is int
            assert type(records[1]["expect_value"]) is int
            assert records[2]["passed"] is True
            assert type(records[2]["check_value"]) is str
            assert type(records[2]["expect_value"]) is str

            code_input = assertion_input(editor, 1)
            code_input.fill('"401"')
            save_draft(page, origin, workspace_id)
            checks = stored().draft["teststeps"][0]["validate"]
            assert checks == expected_checks(code="401", decimal=1.25, status_operator="ge")
            assert type(checks[1]["eq"][1]) is str

            page.reload()
            expect(editor).to_be_visible(timeout=15000)
            open_assertions(editor)
            expect(assertion_input(editor, 1)).to_have_value('"401"')
            expect(assertion_type(editor, 1)).to_have_text("类型：字符串")

            harness.execute_debug(page)
            debug = stored().debug_result
            records = debug["step_datas"][0]["validators"]["validate_extractor"]
            assert debug["success"] is False, debug
            assert records[1]["passed"] is False, records[1]
            assert type(records[1]["check_value"]) is int
            assert type(records[1]["expect_value"]) is str
            assert records[2]["passed"] is True, records[2]
            page.screenshot(path=str(output / "assertion-types-passed.png"), full_page=True)
            assert not errors, errors
        except Exception:
            page.screenshot(path=str(output / "assertion-types-failure.png"), full_page=True)
            raise
        finally:
            context.close()
            browser.close()


if __name__ == "__main__":
    harness.verify = verify
    harness.main()
    print(
        "PASS: typed assertion literals persist, reload, retain untouched values, "
        "and preserve strict numeric/string debug comparisons"
    )
