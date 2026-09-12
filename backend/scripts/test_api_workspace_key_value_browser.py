"""Offline browser regression for API workspace key/value parameter editing.

Run after the frontend build.  This reuses the API-workspace browser harness:
the database is temporary SQLite, Django and Vue share a loopback origin, and
the browser plus Python socket guards block all external traffic.
"""
import json

import test_api_workspace_browser as harness


def seed(fixture):
    """Create a root plan and one editable scenario without touching user data."""
    from api_testing.models import APIWorkspace

    root = APIWorkspace.objects.create(
        project_id=fixture["project_id"],
        owner_id=fixture["user_id"],
        title="键值参数编辑计划",
        model_id=fixture["model_id"],
        spec_id=fixture["spec_id"],
        endpoint_ids=[fixture["endpoint_id"]],
    )
    workspace = APIWorkspace.objects.create(
        project_id=fixture["project_id"],
        owner_id=fixture["user_id"],
        parent=root,
        title="键值参数编辑场景",
        model_id=fixture["model_id"],
        spec_id=fixture["spec_id"],
        endpoint_ids=[fixture["endpoint_id"]],
        draft={
            "version": 1,
            "config": {
                "name": "键值参数编辑场景",
                "base_url": "https://example.test",
                "variables": {
                    "role_name": "test_role",
                    "count": 2,
                    "enabled": True,
                    "roles": ["reader", "auditor"],
                },
            },
            "teststeps": [
                {
                    "name": "查询健康",
                    "endpoint_id": fixture["endpoint_id"],
                    "request": {
                        "method": "GET",
                        "url": "/health",
                        "params": {
                            "keyword": "${role_name}",
                            "pageNum": 2,
                            "pageSize": 2,
                        },
                        "headers": {"X-Test": "original", "X-Keep": "untouched"},
                    },
                    "extract": {
                        "selected_id": "body.data.0.id",
                        "preserved_name": "body.data[1].name",
                    },
                    "validate": [{"eq": ["status_code", 200]}],
                },
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


def row_for_key(container, key):
    """Return the rendered key/value row with the requested key."""
    rows = container.locator(".key-value-row")
    for index in range(rows.count()):
        row = rows.nth(index)
        if row.locator("input").first.input_value() == key:
            return row
    raise AssertionError(f"未找到键值行 {key!r}")


def value_input(row):
    return row.locator("input").last


def replace_by_typing(input_box, text, *, label):
    """Use the same Select-All/delete/keystroke path that regressed in Vue."""
    input_box.click()
    input_box.press("Meta+A")
    input_box.press("Backspace")
    input_box.press_sequentially(text, delay=20)
    input_box.press("Tab")
    actual = input_box.input_value()
    assert actual == text, (
        f"{label} 输入值被旧 modelValue 覆盖：期望 {text!r}，实际 {actual!r}；"
        "请检查 KeyValueRows 的 input value 同步，而不是隔离 seed。"
    )


def key_values(container):
    rows = container.locator(".key-value-row")
    return [rows.nth(index).locator("input").first.input_value() for index in range(rows.count())]


def verify(origin, fixture, output):
    from api_testing.models import APIWorkspace
    from playwright.sync_api import expect, sync_playwright

    root_id, workspace_id = harness.database(lambda: seed(fixture))

    def stored():
        return harness.database(lambda: APIWorkspace.objects.get(pk=workspace_id))

    with sync_playwright() as pw:
        browser = pw.chromium.launch(executable_path=str(harness.CHROME), headless=True)
        context = browser.new_context(viewport={"width": 1440, "height": 1050})
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
            editor.get_by_text("请求参数与请求头", exact=True).click()

            request_section = editor.locator(".el-collapse-item").filter(
                has_text="Query 参数"
            )
            query_rows = request_section.locator(".key-value-rows").nth(0)
            page_num = value_input(row_for_key(query_rows, "pageNum"))
            page_size = value_input(row_for_key(query_rows, "pageSize"))

            # Keep this first: the old frontend dist must fail here with an
            # input-value assertion, proving the UI defect rather than seed data.
            replace_by_typing(page_num, "1", label="Query 参数 pageNum")
            replace_by_typing(page_size, "20", label="Query 参数 pageSize")
            assert page_num.input_value() == "1", (
                "Query 参数 pageNum 在编辑 pageSize 后恢复为旧值；"
                "请检查 KeyValueRows input value 同步。"
            )

            headers_rows = request_section.locator(".key-value-rows").nth(1)
            replace_by_typing(
                value_input(row_for_key(headers_rows, "X-Test")),
                "changed",
                label="Header X-Test",
            )
            headers_rows.get_by_role("button", name="+ 添加", exact=True).click()
            temporary = headers_rows.locator(".key-value-row").last
            replace_by_typing(
                temporary.locator("input").first,
                "X-Added",
                label="新增 Header 名称",
            )
            temporary = row_for_key(headers_rows, "X-Added")
            replace_by_typing(
                temporary.locator("input").first,
                "X-Renamed",
                label="重命名 Header",
            )
            row_for_key(headers_rows, "X-Renamed").get_by_role(
                "button", name="删除一行", exact=True
            ).click()
            assert "X-Renamed" not in key_values(headers_rows), "临时 Header 删除未生效"

            editor.get_by_text("提取与断言", exact=True).click()
            extract_rows = editor.locator(".el-collapse-item").filter(
                has_text="提取变量"
            ).locator(".key-value-rows")
            replace_by_typing(
                value_input(row_for_key(extract_rows, "selected_id")),
                "body.data[0].id",
                label="提取字段 selected_id",
            )

            config_rows = page.locator(".config-editor .key-value-rows")
            replace_by_typing(
                value_input(row_for_key(config_rows, "count")),
                "0",
                label="typed 变量 count",
            )

            with page.expect_response(
                lambda response: response.url.endswith(f"/workspaces/{workspace_id}/")
                and response.request.method == "PATCH"
            ) as saved:
                page.get_by_role("button", name="保存草稿", exact=True).click()
            assert saved.value.status == 200, saved.value.text()

            draft = stored().draft
            request = draft["teststeps"][0]["request"]
            assert request["params"] == {
                "keyword": "${role_name}",
                "pageNum": "1",
                "pageSize": "20",
            }, request["params"]
            assert request["headers"] == {"X-Test": "changed", "X-Keep": "untouched"}, request["headers"]
            assert draft["teststeps"][0]["extract"] == {
                "selected_id": "body.data[0].id",
                "preserved_name": "body.data[1].name",
            }, draft["teststeps"][0]["extract"]
            variables = draft["config"]["variables"]
            assert variables == {
                "role_name": "test_role",
                "count": 0,
                "enabled": True,
                "roles": ["reader", "auditor"],
            }, variables
            assert type(variables["count"]) is int
            assert type(variables["enabled"]) is bool
            assert type(variables["roles"]) is list

            page.reload()
            expect(editor).to_be_visible(timeout=15000)
            assert value_input(row_for_key(page.locator(".config-editor .key-value-rows"), "count")).input_value() == "0"
            editor.get_by_text("请求参数与请求头", exact=True).click()
            request_section = editor.locator(".el-collapse-item").filter(
                has_text="Query 参数"
            )
            query_rows = request_section.locator(".key-value-rows").nth(0)
            assert value_input(row_for_key(query_rows, "keyword")).input_value() == "${role_name}"
            assert value_input(row_for_key(query_rows, "pageNum")).input_value() == "1"
            assert value_input(row_for_key(query_rows, "pageSize")).input_value() == "20"
            headers_rows = request_section.locator(".key-value-rows").nth(1)
            assert value_input(row_for_key(headers_rows, "X-Test")).input_value() == "changed"
            assert value_input(row_for_key(headers_rows, "X-Keep")).input_value() == "untouched"
            editor.get_by_text("提取与断言", exact=True).click()
            extract_rows = editor.locator(".el-collapse-item").filter(
                has_text="提取变量"
            ).locator(".key-value-rows")
            assert value_input(row_for_key(extract_rows, "selected_id")).input_value() == "body.data[0].id"
            page.screenshot(path=str(output / "key-value-passed.png"), full_page=True)
            assert not errors, errors
        except Exception:
            page.screenshot(path=str(output / "key-value-failure.png"), full_page=True)
            raise
        finally:
            context.close()
            browser.close()


if __name__ == "__main__":
    harness.verify = verify
    harness.main()
    print("PASS: key/value parameter typing, typed value preservation, draft persistence and reload")
