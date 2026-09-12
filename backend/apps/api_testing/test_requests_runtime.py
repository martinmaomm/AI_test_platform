"""Offline contract tests for the requests API-test runtime.

Every request is made through a mocked Session.  No environment variables,
network services, credentials, or Django database are involved.
"""

import ast
import builtins
import json
from datetime import timedelta
from unittest.mock import patch

import requests

from api_testing.case_contract import CaseContractError, export_python, normalize_case
from api_testing.requests_runner import requests_runner
from api_testing.requests_runtime import (
    ExtractionFailure, _select, run_case, selector_has_filter, validate_selector,
)


class FakeResponse:
    def __init__(self, status_code=200, body=None, headers=None, url="https://api.example.test/"):
        self.status_code = status_code
        self._body = body
        self.headers = headers or {"content-type": "application/json"}
        self.url = url
        self.elapsed = timedelta(milliseconds=12)
        self.text = json.dumps(body, ensure_ascii=False) if not isinstance(body, str) else body
        self.content = self.text.encode()

    def json(self):
        if isinstance(self._body, str):
            raise ValueError("not json")
        return self._body


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []
        self.closed = False

    def request(self, **kwargs):
        self.requests.append(kwargs)
        item = self.responses.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item

    def close(self):
        self.closed = True


def test_selector_filter_binds_variables_as_data_and_preserves_scalar_types():
    injected_name = "中文 ' ) ][0].id ${not_a_path}"
    context = {"body": {"data": [
        {"id": 1, "name": "1", "meta": {"name": "old"}},
        {"id": 2, "name": 1, "meta": {"name": injected_name}},
        {"id": 3, "name": True, "meta": {"name": "other"}},
    ]}}
    variables = {"role_name": injected_name}
    assert _select(
        'body.data[?(@.meta.name == "${role_name}")][0].id', context, variables=variables,
    ) == 2
    assert _select("body.data[?(@.name == '1')][0].id", context) == 1
    assert _select("body.data[?(@.name == 1)][0].id", context) == 2
    assert _select("body.data[?(@.name == true)][0].id", context) == 3
    assert _select("body.data.0.id", context) == 1
    assert selector_has_filter("body.data[?(@.name == ${role_name})][0].id")
    assert _select('body.data[?(@.name == "absent")]', context) == []
    try:
        _select('body.data[?(@.name == "absent")]', context, require_unique=True)
    except ExtractionFailure as exc:
        assert "唯一匹配" in str(exc)
    else:
        raise AssertionError("unique filters must reject an empty result")


def test_validator_selector_uses_the_full_current_variable_scope():
    session = FakeSession([FakeResponse(body={"data": [{"name": "current"}]})])
    case = {"config": {"base_url": "https://api.example.test", "variables": {"role_name": "current"}}, "teststeps": [{
        "request": {"url": "/roles"},
        "validate": [{"length": ["body.data[?(@.name == ${role_name})]", 1]}],
    }]}
    with patch("api_testing.requests_runtime.requests.Session", return_value=session):
        assert run_case("validator-filter", case)["status"] == "passed"


def _length_gt_case(expected=0, selector="body.data", variables=None):
    return {"config": {"base_url": "https://api.example.test", "variables": variables or {}}, "teststeps": [{
        "request": {"url": "/items"}, "validate": [{"length_gt": [selector, expected]}],
    }]}


def test_length_gt_compares_length_strictly_and_preserves_actual_response_evidence():
    for actual, expected, passes in [([], 0, False), ([1], 0, True), ([1, 2], 2, False),
                                      ([1, 2, 3], 2, True), ("abc", 2, True), ({"id": 1}, 0, True)]:
        session = FakeSession([FakeResponse(body={"data": actual})])
        with patch("api_testing.requests_runtime.requests.Session", return_value=session):
            result = run_case("length-gt", _length_gt_case(expected))
        assert result["success"] is passes, result
        record = result["step_datas"][0]["validators"]["validate_extractor"][0]
        assert record["check_value"] == actual
        assert record["expect_value"] == expected
        assert f"实际长度为 {len(actual)}" in record["message"]


def test_length_gt_rejects_invalid_literal_thresholds_before_any_request():
    for expected in (True, False, -1, 0.5, 0.0, None, "", "0", '"0"', [], {}, "${limit} suffix"):
        with patch("api_testing.requests_runtime.requests.Session") as session_factory:
            result = run_case("invalid-length-threshold", _length_gt_case(expected))
        assert result["status"] == "error", expected
        assert "预期值必须是非负整数" in result["error"]
        session_factory.assert_not_called()


def test_length_gt_allows_variable_thresholds_but_validates_the_resolved_type():
    for expected, passes in [(0, True), (1, False), ("0", False), (True, False), (-1, False), (None, False)]:
        session = FakeSession([FakeResponse(body={"data": [1]})])
        with patch("api_testing.requests_runtime.requests.Session", return_value=session):
            result = run_case("variable-length", _length_gt_case("${limit}", variables={"limit": expected}))
        assert result["success"] is passes, result
        if type(expected) is not int or expected < 0:
            assert "预期值必须是非负整数" in result["step_datas"][0]["validators"]["validate_extractor"][0]["message"]


def test_length_gt_rejects_missing_or_unsized_values_instead_of_treating_them_as_nonempty():
    for body in ({}, {"data": None}, {"data": 1}, {"data": True}):
        session = FakeSession([FakeResponse(body=body)])
        with patch("api_testing.requests_runtime.requests.Session", return_value=session):
            result = run_case("invalid-length-value", _length_gt_case())
        assert result["status"] == "failed"
        record = result["step_datas"][0]["validators"]["validate_extractor"][0]
        assert record["passed"] is False
        assert "断言无法执行" in record["message"]


def test_length_gt_export_and_filtered_list_use_the_same_runtime_contract():
    selector = "body.data[?(@.name == ${name})]"
    case = _length_gt_case(selector=selector, variables={"name": "current"})
    # A separate exact-length assertion must remain unchanged.
    case["teststeps"][0]["validate"].append({"length": [selector, 1]})
    namespace = {"__name__": "exported_length_gt"}
    exec(compile(export_python(case), "exported_length_gt.py", "exec"), namespace)
    for body, status in [({"data": [{"name": "other"}, {"name": "current"}]}, "passed"),
                         ({"data": []}, "failed")]:
        with patch("api_testing.requests_runtime.requests.Session", return_value=FakeSession([FakeResponse(body=body)])):
            original = run_case("length-export", case)
        with patch.object(namespace["requests"], "Session", return_value=FakeSession([FakeResponse(body=body)])):
            exported = namespace["run_case"]("length-export", namespace["CASE"])
        assert original["status"] == exported["status"] == status
        assert original["step_datas"] == exported["step_datas"]


def test_selector_parser_rejects_complete_invalid_syntax_and_implicit_projection():
    for selector in (
        "body.data[?(@.name != 'x')]", "body.data[?(@.name == ${role})] trailing",
        "body.data[*]", "body..data", "body.data[0]junk", "body.data[?(@.name == foo)]",
        "body.data[?(@[0] == 'x')]",
    ):
        try:
            validate_selector(selector)
        except CaseContractError:
            pass
        else:
            raise AssertionError(f"selector must be rejected: {selector}")
    try:
        _select("body.data.id", {"body": {"data": [{"id": 1}]}})
    except CaseContractError as exc:
        assert "隐式字段投影" in str(exc)
    else:
        raise AssertionError("lists cannot implicitly project fields")
    try:
        _select("body.data[?(@.name == 'x')].id", {"body": {"data": [{"name": "x", "id": 1}]}})
    except CaseContractError as exc:
        assert "隐式字段投影" in str(exc)
    else:
        raise AssertionError("filtered lists cannot implicitly project fields")


def test_filter_variables_are_checked_before_http_and_same_extract_cannot_bind_another_extract():
    unknown = {"config": {"base_url": "https://api.example.test"}, "teststeps": [{
        "request": {"url": "/items"}, "extract": {"id": "body.data[?(@.name == ${role_name})][0].id"},
    }]}
    same_step = {"config": {"base_url": "https://api.example.test"}, "teststeps": [{
        "request": {"url": "/items"}, "extract": {
            "role_name": "body.role_name", "id": "body.data[?(@.name == ${role_name})][0].id",
        },
    }]}
    with patch("api_testing.requests_runtime.requests.Session") as session_factory:
        for case in (unknown, same_step):
            result = run_case("unknown-filter", case)
            assert result["status"] == "error"
            assert "未知变量" in result["error"]
        session_factory.return_value.request.assert_not_called()


def test_filter_extraction_requires_one_match_then_allows_update_and_delete_with_old_first_row_unchanged():
    role_name = "本轮 '角色'"
    session = FakeSession([
        FakeResponse(body={"data": None}),
        FakeResponse(body={"data": {"list": [{"id": 1, "name": "旧记录"}, {"id": 9, "name": role_name}]}}),
        FakeResponse(body={"data": None}),
        FakeResponse(body={"data": None}),
    ])
    case = {"config": {"base_url": "https://api.example.test", "variables": {"role_name": role_name}}, "teststeps": [
        {"name": "create", "request": {"method": "POST", "url": "/roles", "json": {"name": "${role_name}"}}},
        {"name": "lookup", "request": {"url": "/roles", "params": {"name": "${role_name}"}},
         "extract": {"role_id": 'body.data.list[?(@.name == "${role_name}")][0].id'}},
        {"name": "update", "request": {"method": "POST", "url": "/roles/${role_id}", "json": {"name": "updated"}}},
        {"name": "delete", "request": {"method": "DELETE", "url": "/roles/${role_id}"}},
    ]}
    with patch("api_testing.requests_runtime.requests.Session", return_value=session):
        result = run_case("filtered-write", case)
    assert result["status"] == "passed", result
    assert [request["url"] for request in session.requests[2:]] == [
        "https://api.example.test/roles/9", "https://api.example.test/roles/9",
    ]
    assert session.requests[1]["params"] == {"name": role_name}


def test_unselected_or_duplicate_list_extractions_stop_before_a_later_write_and_preserve_response_evidence():
    base_case = {"config": {"base_url": "https://api.example.test"}, "teststeps": [
        {"name": "lookup", "request": {"url": "/roles"}, "extract": {"role_id": "body.data.list"}},
        {"name": "must-not-write", "request": {"method": "POST", "url": "/roles/${role_id}"}},
    ]}
    duplicate_case = {"config": {"base_url": "https://api.example.test", "variables": {"role_name": "same"}}, "teststeps": [
        {"name": "lookup", "request": {"url": "/roles"},
         "extract": {"role_id": "body.data.list[?(@.name == ${role_name})][0].id"}},
        {"name": "must-not-write", "request": {"method": "POST", "url": "/roles/${role_id}"}},
    ]}
    for case in (base_case, duplicate_case):
        session = FakeSession([FakeResponse(body={"data": {"list": [
            {"id": 1, "name": "same"}, {"id": 2, "name": "same"},
        ]}})])
        with patch("api_testing.requests_runtime.requests.Session", return_value=session):
            result = run_case("unsafe-list", case)
        first, second = result["step_datas"]
        assert result["status"] == first["status"] == "failed"
        assert result["error_type"] == "ExtractionFailure"
        assert second["status"] == "skipped"
        assert len(session.requests) == 1
        assert first["data"]["req_resps"][0]["response"]["body"]["data"]["list"][0]["id"] == 1
        assert "唯一匹配" in first["error"]


def _indexed_allocation_case():
    return {"config": {"base_url": "https://api.example.test"}, "teststeps": [
        {"name": "查询资源", "request": {"url": "/resources"},
         "extract": {"first_id": "body.data[0].id", "second_id": "body.data[1].id"},
         "validate": [{"type": ["body.data", "list"]}, {"length_gt": ["body.data", 0]}]},
        {"name": "关联选中的资源", "request": {"method": "POST", "url": "/roles/99/resources",
         "params": {"resourceIds": ["${first_id}", "${second_id}"]},
         "headers": {"X-Selected-ID": "${second_id}"}}, "validate": [{"eq": ["status_code", 200]}]},
    ]}


def test_explicit_indexes_feed_later_post_and_export_without_requiring_the_full_list_to_be_unique():
    case = _indexed_allocation_case()
    body = {"data": [{"id": index} for index in range(1, 37)]}
    namespace = {"__name__": "exported_indexed_allocation"}
    exec(compile(export_python(case), "exported_indexed_allocation.py", "exec"), namespace)
    results = []
    for execute in (run_case, namespace["run_case"]):
        session = FakeSession([FakeResponse(body=body), FakeResponse(body={"code": 200})])
        with patch("api_testing.requests_runtime.requests.Session", return_value=session):
            result = execute("indexed-allocation", case)
        assert result["status"] == "passed", result
        assert len(session.requests) == 2
        assert session.requests[1]["params"] == {"resourceIds": [1, 2]}
        assert session.requests[1]["headers"]["X-Selected-ID"] == "2"
        assert result["step_datas"][0]["export_vars"] == {"first_id": 1, "second_id": 2}
        results.append(result)
    assert results[0]["step_datas"] == results[1]["step_datas"]


def test_explicit_index_errors_keep_response_and_skip_the_later_write():
    for body, message in [({"data": []}, "索引 [0] 越界"), ({"data": [{"id": 1}]}, "索引 [1] 越界"),
                          ({"data": [{}, {"id": 2}]}, "id"), ({"data": None}, "失败"), ({}, "data")]:
        session = FakeSession([FakeResponse(body=body)])
        with patch("api_testing.requests_runtime.requests.Session", return_value=session):
            result = run_case("bad-index", _indexed_allocation_case())
        assert result["status"] == "failed", result
        assert result["error_type"] == "ExtractionFailure"
        assert len(session.requests) == 1
        first, later = result["step_datas"]
        assert message in first["error"]
        assert first["data"]["req_resps"][0]["response"]["body"] == body
        # Partial successes may remain in report evidence, but no later write runs.
        assert any(item["passed"] is False for item in first["extraction_results"])
        assert later["status"] == "skipped"


def test_explicit_dot_and_nested_indexes_do_not_change_filter_uniqueness():
    context = {"body": {"data": [{"id": 1}, {"id": 2}], "groups": [[], [{"id": 3}, {"id": 4}]]}}
    assert _select("body.data.1.id", context, require_unique=True) == 2
    assert _select("body.groups[1][1].id", context, require_unique=True) == 4
    for selector in ("body.data[-1].id", "body.data[1.5].id"):
        try:
            validate_selector(selector)
        except CaseContractError:
            pass
        else:
            raise AssertionError(f"invalid index must still fail: {selector}")
    for items in ([], [{"id": 3, "name": "same"}, {"id": 4, "name": "same"}]):
        filtered = {"body": {"groups": [[], items]}}
        selector = "body.groups[1][?(@.name == 'same')][0].id"
        try:
            _select(selector, filtered, require_unique=True)
        except ExtractionFailure as exc:
            assert "筛选结果必须唯一匹配" in str(exc)
        else:
            raise AssertionError("an explicit index must not bypass filter uniqueness")


def test_source_use_tracks_current_extract_version_including_headers_and_cookies():
    replacement = {"config": {"base_url": "https://api.example.test"}, "teststeps": [
        {"request": {"url": "/old"}, "extract": {"id": "body.list[0].id"}},
        {"request": {"url": "/new"}, "extract": {"id": "body.id"}},
        {"request": {"method": "POST", "url": "/items/${id}"}},
    ]}
    safe_session = FakeSession([FakeResponse(body={"list": [{"id": 1}, {"id": 2}]}), FakeResponse(body={"id": 9}), FakeResponse()])
    with patch("api_testing.requests_runtime.requests.Session", return_value=safe_session):
        assert run_case("overridden", replacement)["status"] == "passed"
    for extract, transfer_selector, write_variable in (
        ({"id": "body.list[?(@.name == 'same')][0].id"}, None, "write_id"),
        ({"old_id": "body.list[?(@.name == 'same')][0].id"}, "extract.old_id", "new_id"),
        ({"old_object": "body.list"}, "extract.old_object[0].id", "new_id"),
    ):
        config_variables = {"id": "", "write_id": "${id}"} if write_variable == "write_id" else {}
        steps = [{"request": {"url": "/roles"}, "extract": extract}]
        if transfer_selector is not None:
            steps.append({"request": {"url": "/copy"}, "extract": {"new_id": transfer_selector}})
        steps.append({"request": {"method": "POST", "url": f"/roles/${{{write_variable}}}"}})
        session = FakeSession([FakeResponse(body={"list": [{"id": 1, "name": "same"}, {"id": 2, "name": "same"}]})])
        with patch("api_testing.requests_runtime.requests.Session", return_value=session):
            result = run_case("alias-source", {"config": {"base_url": "https://api.example.test", "variables": config_variables}, "teststeps": steps})
        assert result["error_type"] == "ExtractionFailure"
        assert len(session.requests) == 1
    for request_part in ({"headers": {"X-ID": "${id}"}}, {"cookies": {"id": "${id}"}}):
        case = {"config": {"base_url": "https://api.example.test"}, "teststeps": [
            {"request": {"url": "/roles"}, "extract": {"id": "body.list[?(@.name == 'same')][0].id"}},
            {"request": {"method": "POST", "url": "/roles", **request_part}},
        ]}
        session = FakeSession([FakeResponse(body={"list": [{"id": 1, "name": "same"}, {"id": 2, "name": "same"}]})])
        with patch("api_testing.requests_runtime.requests.Session", return_value=session):
            result = run_case("header-cookie-source", case)
        assert result["error_type"] == "ExtractionFailure"
        assert len(session.requests) == 1


def test_exported_runtime_embeds_filter_selector_without_new_dependencies():
    case = {"config": {"base_url": "https://api.example.test", "variables": {"role_name": "current"}}, "teststeps": [
        {"request": {"url": "/roles"},
         "extract": {"role_id": 'body.data[?(@.name == "${role_name}")][0].id'}},
        {"request": {"method": "POST", "url": "/roles/${role_id}"}},
    ]}
    source = export_python(case)
    assert "class _SelectorParser:" in source
    namespace = {"__name__": "exported_filter_runtime"}
    exec(compile(source, "exported_filter_runtime.py", "exec"), namespace)
    session = FakeSession([FakeResponse(body={"data": [{"id": 7, "name": "current"}]}), FakeResponse()])
    with patch.object(namespace["requests"], "Session", return_value=session):
        result = namespace["run_case"]("exported-filter", namespace["CASE"])
    assert result["status"] == "passed"
    assert session.requests[1]["url"] == "https://api.example.test/roles/7"


def test_object_transfer_allows_explicit_array_index_before_write():
    case = {"config": {"base_url": "https://api.example.test"}, "teststeps": [
        {"request": {"url": "/list"}, "extract": {"payload": "body"}},
        {"request": {"url": "/copy"}, "extract": {"id": "extract.payload.data[1].id"}},
        {"request": {"method": "POST", "url": "/items/${id}"}},
    ]}
    session = FakeSession([FakeResponse(body={"data": [{"id": 1}, {"id": 2}]}), FakeResponse(), FakeResponse()])
    with patch("api_testing.requests_runtime.requests.Session", return_value=session):
        result = run_case("transfer-index", case)
    assert result["status"] == "passed"
    assert len(session.requests) == 3
    assert session.requests[2]["url"] == "https://api.example.test/items/2"


def test_normalize_case_is_json_only_copied_and_rejects_hooks():
    raw = {"config": {"variables": {"id": 7}}, "teststeps": [{"request": {"url": "/items/$id"}, "validate": [["status_code", "eq", 200]]}]}
    normalized = normalize_case(raw)
    assert normalized["teststeps"][0]["request"]["method"] == "GET"
    assert normalized["teststeps"][0]["validate"] == [{"eq": ["status_code", 200]}]
    normalized["config"]["variables"]["id"] = 8
    assert raw["config"]["variables"]["id"] == 7
    try:
        normalize_case("config: {name: legacy}")
    except CaseContractError as exc:
        assert "JSON" in str(exc)
    else:
        raise AssertionError("YAML must not be accepted")
    try:
        normalize_case({"setup_hooks": ["evil()"], "teststeps": []})
    except CaseContractError as exc:
        assert "任意代码" in str(exc)
    else:
        raise AssertionError("hooks must be rejected")


def test_runner_preserves_types_substitutes_extracts_and_reuses_one_session():
    session = FakeSession([
        FakeResponse(body={"data": {"token": "t-1"}}),
        FakeResponse(body={"active": True, "items": [1, 2, 3]}),
    ])
    case = {
        "config": {"name": "login", "base_url": "https://api.example.test/v1/", "variables": {"account_id": 7}, "headers": {"X-Env": "test"}},
        "teststeps": [
            {"name": "login", "request": {"method": "POST", "url": "login/${account_id}", "headers": {"X-ID": "$account_id"}, "json": {"id": "${account_id}", "title": "account-$account_id"}}, "extract": {"token": "$.body.data.token"}, "validate": [{"eq": ["$.status", 200]}]},
            {"name": "profile", "request": {"url": "users/$account_id", "headers": {"Authorization": "Bearer {{token}}"}, "params": {"token": "{{token}}"}}, "validate": [{"type": ["$.body.active", "boolean"]}, {"length": ["body.items", 3]}, {"contains": ["$.body.items", 2]}, {"eq": ["$.extract.token", "t-1"]}]},
        ],
    }
    with patch("api_testing.requests_runtime.requests.Session", return_value=session):
        result = run_case("case-1", json.dumps(case))
    assert result["success"] is True
    assert session.closed is True
    assert len(session.requests) == 2
    first, second = session.requests
    assert first["url"] == "https://api.example.test/v1/login/7"
    assert first["json"] == {"id": 7, "title": "account-7"}
    assert first["headers"] == {"X-Env": "test", "X-ID": "7"}
    assert all(29.9 < value <= 30.0 for value in first["timeout"])
    assert second["headers"]["Authorization"] == "Bearer t-1"
    assert result["step_datas"][0]["export_vars"] == {"token": "t-1"}
    assert result["step_datas"][1]["data"]["validators"]["validate_extractor"][0]["check_result"] == "pass"


def test_runner_keeps_json_form_and_raw_distinct():
    session = FakeSession([FakeResponse(), FakeResponse(), FakeResponse()])
    case = {"config": {"base_url": "https://api.example.test"}, "teststeps": [
        {"request": {"method": "POST", "url": "/json", "json": {"ok": True}}},
        {"request": {"method": "POST", "url": "/form", "data": {"page": 2}}},
        {"request": {"method": "POST", "url": "/raw", "raw": "plain text"}},
    ]}
    with patch("api_testing.requests_runtime.requests.Session", return_value=session):
        result = run_case("body-kinds", json.dumps(case), options={"connect_timeout": 1, "read_timeout": 2})
    assert result["success"] is True
    assert session.requests[0]["json"] == {"ok": True}
    assert session.requests[1]["data"] == {"page": 2}
    assert session.requests[2]["data"] == "plain text"
    assert session.requests[2]["timeout"] == (1.0, 2.0)


def test_runtime_removes_legacy_tls_fields_and_keeps_editor_request_fields():
    session = FakeSession([FakeResponse()])
    case = {"config": {"base_url": "https://api.example.test", "verify": True, "verify_ssl": True}, "teststeps": [{
        "request": {"url": "/health", "timeout": 5, "allow_redirects": False, "cookies": {"sid": "test"}, "verify": True, "verify_ssl": True},
    }]}
    with patch("api_testing.requests_runtime.requests.Session", return_value=session):
        result = run_case("editor-fields", json.dumps(case), options={"total_timeout": 20, "verify": True, "verify_ssl": True})
    assert result["status"] == "passed"
    request = session.requests[0]
    assert request["timeout"] == (5.0, 5.0)
    assert request["allow_redirects"] is False
    assert request["cookies"] == {"sid": "test"}
    assert request["verify"] is False
    normalized = normalize_case(case)
    assert "verify" not in normalized["config"]
    assert "verify_ssl" not in normalized["config"]
    assert "verify" not in normalized["teststeps"][0]["request"]
    assert "verify_ssl" not in normalized["teststeps"][0]["request"]


def test_legacy_environment_tls_options_cannot_reenable_certificate_validation():
    session = FakeSession([FakeResponse(), FakeResponse()])
    case = {"config": {"base_url": "https://api.example.test/api"}, "teststeps": [
        {"request": {"url": "/users"}},
        {"request": {"url": "https://override.example.test/health", "verify": True}},
    ]}
    with patch("api_testing.requests_runtime.requests.Session", return_value=session):
        result = run_case("environment-options", json.dumps(case), options={"verify_ssl": False})
    assert result["status"] == "passed"
    assert session.requests[0]["url"] == "https://api.example.test/api/users"
    assert session.requests[0]["verify"] is False
    assert session.requests[1]["url"] == "https://override.example.test/health"
    assert session.requests[1]["verify"] is False


def test_normalize_rejects_unknown_request_fields_before_execution():
    try:
        normalize_case({"teststeps": [{"request": {"url": "/health", "auth_script": "do_not_run"}}]})
    except CaseContractError as exc:
        assert "不支持字段" in str(exc)
    else:
        raise AssertionError("unknown request fields must be rejected")


def test_runtime_builtins_are_unique_and_templates_preserve_json_types():
    first_session = FakeSession([FakeResponse()])
    second_session = FakeSession([FakeResponse()])
    case = {"config": {"base_url": "https://api.example.test", "variables": {
        "username": "test_${timestamp_ns}", "numeric_id": "${timestamp_ns}", "request_id": "${uuid4}",
    }}, "teststeps": [{"request": {"method": "POST", "url": "/register", "json": {
        "username": "${username}", "id": "${numeric_id}", "request_id": "${request_id}",
    }}}]}
    with patch("api_testing.requests_runtime.time.time_ns", side_effect=[101, 202]), patch(
        "api_testing.requests_runtime.requests.Session", side_effect=[first_session, second_session],
    ):
        first = run_case("builtins-1", json.dumps(case))
        second = run_case("builtins-2", json.dumps(case))
    assert first["status"] == second["status"] == "passed"
    first_json, second_json = first_session.requests[0]["json"], second_session.requests[0]["json"]
    assert first_json["username"] == "test_101"
    assert second_json["username"] == "test_202"
    assert first_json["id"] == 101 and isinstance(first_json["id"], int)
    assert isinstance(first_json["request_id"], str)


def test_explicit_builtin_override_is_repeatable_and_variable_cycles_send_nothing():
    fixed_case = {"config": {"base_url": "https://api.example.test", "variables": {"username": "test_${timestamp_ns}"}}, "teststeps": [{"request": {"url": "/users/${username}"}}]}
    fixed_session = FakeSession([FakeResponse()])
    with patch("api_testing.requests_runtime.requests.Session", return_value=fixed_session):
        result = run_case("fixed", json.dumps(fixed_case), options={"variables": {"timestamp_ns": 42}})
    assert result["status"] == "passed"
    assert fixed_session.requests[0]["url"] == "https://api.example.test/users/test_42"

    circular = {"config": {"base_url": "https://api.example.test", "variables": {"first": "${second}", "second": "${first}"}}, "teststeps": [{"request": {"url": "/${first}"}}]}
    with patch("api_testing.requests_runtime.requests.Session") as session_factory:
        circular_result = run_case("cycle", json.dumps(circular))
    assert circular_result["status"] == "error"
    assert "循环引用" in circular_result["error"]
    session_factory.return_value.request.assert_not_called()


def test_runner_marks_later_steps_skipped_after_validation_failure():
    session = FakeSession([FakeResponse(status_code=500, body={"error": "bad"})])
    case = {"config": {"base_url": "https://api.example.test"}, "teststeps": [
        {"name": "fails", "request": {"url": "/first"}, "validate": [{"eq": ["status_code", 200]}]},
        {"name": "skipped", "request": {"url": "/second"}},
    ]}
    with patch("api_testing.requests_runtime.requests.Session", return_value=session):
        result = run_case("skip", json.dumps(case))
    assert result["success"] is False
    assert [step["status"] for step in result["step_datas"]] == ["failed", "skipped"]
    assert len(session.requests) == 1
    assert result["status"] == "failed"
    assert result["stat"]["teststeps"] == {"total": 2, "successes": 0, "failures": 1, "errors": 0, "skipped": 1}


def test_request_timeout_is_step_error_with_counter_and_attempted_request_context():
    session = FakeSession([requests.Timeout("read timed out")])
    case = {"config": {"base_url": "https://api.example.test"}, "teststeps": [{
        "name": "timeout", "request": {"method": "POST", "url": "/register", "headers": {"X-Test": "yes"}, "json": {"email": "test@example.test"}},
    }]}
    with patch("api_testing.requests_runtime.requests.Session", return_value=session):
        result = run_case("timeout", json.dumps(case))
    step = result["step_datas"][0]
    assert result["status"] == step["status"] == "error"
    assert result["stat"]["teststeps"] == {"total": 1, "successes": 0, "failures": 0, "errors": 1, "skipped": 0}
    req_resp = step["data"]["req_resps"][0]
    assert req_resp["request"] == {
        "method": "POST", "url": "https://api.example.test/register", "headers": {"X-Test": "yes"},
        "params": None, "body": {"email": "test@example.test"},
    }
    assert "read timed out" in req_resp["response"]["error"]


def test_extraction_failure_is_failed_not_runtime_error():
    session = FakeSession([FakeResponse(body={})])
    case = {"config": {"base_url": "https://api.example.test"}, "teststeps": [{
        "request": {"url": "/login"}, "extract": {"token": "body.data.token"},
    }]}
    with patch("api_testing.requests_runtime.requests.Session", return_value=session):
        result = run_case("extract", json.dumps(case))
    assert result["status"] == result["step_datas"][0]["status"] == "failed"
    assert result["stat"]["teststeps"] == {"total": 1, "successes": 0, "failures": 1, "errors": 0, "skipped": 0}
    step = result["step_datas"][0]
    assert step["data"]["req_resps"][0]["response"]["status_code"] == 200
    assert step["data"]["req_resps"][0]["response"]["body"] == {}
    assert step["extraction_results"] == [{"name": "token", "selector": "body.data.token", "passed": False, "error": "'data'"}]
    assert result["error_type"] == "ExtractionFailure"


def test_partial_extract_preserves_response_and_never_leaks_variables_to_later_steps():
    session = FakeSession([FakeResponse(body={"token": "only-this-step"})])
    case = {"config": {"base_url": "https://api.example.test"}, "teststeps": [
        {
            "request": {"url": "/login"},
            "extract": {"token": "body.token", "missing": "body.nope"},
            "validate": [{"eq": ["status_code", 200]}, {"eq": ["status_code", "${token}"]}],
        },
        {"request": {"url": "/next/${token}"}, "validate": [{"eq": ["status_code", 200]}]},
    ]}
    with patch("api_testing.requests_runtime.requests.Session", return_value=session):
        result = run_case("partial-extract", json.dumps(case))
    first, second = result["step_datas"]
    assert first["status"] == "failed" and second["status"] == "skipped"
    assert first["data"]["req_resps"][0]["response"]["body"] == {"token": "only-this-step"}
    assert first["export_vars"] == {"token": "only-this-step"}
    assert [record["passed"] for record in first["validators"]["validate_extractor"]] == [True, False]
    assert "未知变量：token" in first["validators"]["validate_extractor"][1]["message"]
    assert len(session.requests) == 1


def test_runner_rejects_unknown_variables_and_illegal_urls_before_session_request():
    unknown = {"config": {"base_url": "https://api.example.test"}, "teststeps": [{"request": {"url": "/$missing"}}]}
    bad_url = {"teststeps": [{"request": {"url": "ftp://example.test/file"}}]}
    with patch("api_testing.requests_runtime.requests.Session") as session_factory:
        unknown_result = run_case("unknown", json.dumps(unknown))
        bad_url_result = run_case("bad-url", json.dumps(bad_url))
    assert unknown_result["success"] is False
    assert unknown_result["status"] == "error"
    assert "未知变量" in unknown_result["error"]
    # URL validation occurs before the mocked Session can issue a request.
    session_factory.return_value.request.assert_not_called()
    assert bad_url_result["success"] is False
    assert bad_url_result["status"] == "error"
    assert "HTTP(S)" in bad_url_result["error"]


def test_allowed_origin_blocks_substituted_or_extracted_cross_origin_url_before_http():
    case = {
        "config": {"base_url": "https://api.example.test", "variables": {"next": "https://other.invalid/path"}},
        "teststeps": [{"request": {"url": "${next}"}}],
    }
    with patch("api_testing.requests_runtime.requests.Session") as session_factory:
        result = run_case("origin", json.dumps(case), options={"allowed_origin": "https://api.example.test"})
    assert result["status"] == "error"
    assert "超出本轮确认目标地址" in result["error"]
    session_factory.return_value.request.assert_not_called()


def test_empty_steps_are_editable_but_not_executable():
    assert normalize_case({"config": {}, "teststeps": []})["teststeps"] == []
    result = requests_runner("empty", json.dumps({"config": {}, "teststeps": []}))
    assert result["success"] is False
    assert result["status"] == "error"
    assert "至少需要一个测试步骤" in result["error"]


def test_effective_global_headers_are_overlaid_before_variable_validation():
    case = {
        "config": {"base_url": "https://api.example.test", "headers": {"Authorization": "Bearer ${not_supplied}"}},
        "teststeps": [{"request": {"url": "/health", "headers": {"X-Run": "${timestamp_ns}"}},
                       "validate": [{"eq": ["status_code", 200]}]}],
    }
    session = FakeSession([FakeResponse(body={})])
    with patch("api_testing.requests_runtime.requests.Session", return_value=session):
        result = run_case("header-overlay", case, options={"headers": {"authorization": "Bearer provided"}})
    assert result["success"], result
    sent = session.requests[0]["headers"]
    assert sent["authorization"] == "Bearer provided"
    assert "Authorization" not in sent
    assert sent["X-Run"].isdigit()


def test_extracted_header_values_are_not_interpreted_as_templates_twice():
    opaque_token = "opaque_${server_value}"
    case = {"config": {"base_url": "https://api.example.test"}, "teststeps": [
        {"request": {"url": "/login"}, "extract": {"token": "body.token"},
         "validate": [{"eq": ["status_code", 200]}]},
        {"request": {"url": "/profile", "headers": {"Authorization": "Bearer ${token}"}},
         "validate": [{"eq": ["status_code", 200]}]},
    ]}
    namespace = {"__name__": "exported_header_acceptance"}
    exec(compile(export_python(case), "exported_headers.py", "exec"), namespace)
    for runtime in (run_case, namespace["run_case"]):
        session = FakeSession([FakeResponse(body={"token": opaque_token}), FakeResponse(body={})])
        with patch("requests.Session", return_value=session):
            result = runtime("opaque-header", case)
        assert result["success"], result
        assert session.requests[1]["headers"]["Authorization"] == f"Bearer {opaque_token}"


def test_invalid_runtime_options_and_type_comparator_are_contract_errors():
    case = {"config": {"base_url": "https://api.example.test"}, "teststeps": [{"request": {"url": "/health"}}]}
    invalid_options = requests_runner("options", json.dumps(case), options={"variables": []})
    assert invalid_options["status"] == "error"
    assert "options.variables" in invalid_options["error"]
    try:
        normalize_case({"teststeps": [{"request": {"url": "/health"}, "validate": [{"type": ["body", "mystery"]}]}]})
    except CaseContractError as exc:
        assert "type" in str(exc)
    else:
        raise AssertionError("unknown type names must be rejected")


def test_export_is_standalone_python_source_with_the_same_runtime_core():
    injection = "注册\n__import__('builtins').export_should_not_run = True"
    case = {"version": 1, "config": {
        "name": injection, "base_url": "https://api.example.test", "variables": {"id": 7},
    }, "teststeps": [{
        "name": "创建 '用户'\n第二行",
        "request": {"method": "POST", "url": "/users", "json": {"id": "${id}", "note": "O'Reilly\nquoted"}},
        "extract": {"id": "body.id"},
        "validate": [{"eq": ["status_code", 201]}, {"eq": ["body.id", "${id}"]}],
    }]}
    source = export_python(case)
    ast.parse(source)
    assert "from api_testing" not in source
    assert "def run_case(" in source
    assert "def test_generated_api_case" in source
    assert source.index("CASE = {") < source.index("from copy import deepcopy")
    assert "# 场景：" in source[:1000]
    assert "# 1. 名称=" in source[:1000]

    namespace = {"__name__": "exported_readonly_case"}
    exec(compile(source, "exported_api_case.py", "exec"), namespace)
    assert not hasattr(builtins, "export_should_not_run")
    assert "export_should_not_run" not in namespace
    assert namespace["CASE"] == normalize_case(case)

    original_session = FakeSession([FakeResponse(status_code=201, body={"id": 7})])
    with patch("api_testing.requests_runtime.requests.Session", return_value=original_session):
        original_result = run_case("export", case)
    exported_session = FakeSession([FakeResponse(status_code=201, body={"id": 7})])
    with patch.object(namespace["requests"], "Session", return_value=exported_session):
        exported_result = namespace["run_case"]("export", namespace["CASE"])
    assert exported_result["status"] == original_result["status"] == "passed"
    assert exported_result["stat"] == original_result["stat"]
    assert exported_result["step_datas"] == original_result["step_datas"]
    assert exported_session.requests[0]["json"] == {"id": 7, "note": "O'Reilly\nquoted"}


def test_export_uses_the_same_fixed_tls_policy_without_case_tls_fields():
    case = {"config": {"base_url": "https://api.example.test", "verify": True}, "teststeps": [{
        "request": {"url": "/health", "verify_ssl": True},
    }]}
    source = export_python(case)
    namespace = {"__name__": "exported_tls_policy"}
    exec(compile(source, "exported_tls_policy.py", "exec"), namespace)
    assert "verify" not in namespace["CASE"]["config"]
    assert "verify_ssl" not in namespace["CASE"]["teststeps"][0]["request"]
    session = FakeSession([FakeResponse()])
    with patch.object(namespace["requests"], "Session", return_value=session):
        result = namespace["run_case"]("exported-tls", namespace["CASE"], options={"verify": True, "verify_ssl": True})
    assert result["success"] is True
    assert session.requests[0]["verify"] is False


def test_platform_runner_uses_controlled_json_worker_with_hard_deadline():
    case = {"config": {"base_url": "https://api.example.test"}, "teststeps": [{"request": {"url": "/health"}}]}
    with patch("api_testing.requests_runner.subprocess.Popen") as popen:
        result = requests_runner("platform", json.dumps(case), should_cancel=lambda: True)
    popen.assert_not_called()
    assert result["error_type"] == "Cancelled"
    assert result["step_datas"][0]["status"] == "skipped"


def test_platform_runner_hard_timeout_preserves_unknown_step_boundary():
    case = {"config": {"base_url": "https://api.example.test"}, "teststeps": [
        {"name": "in-flight", "request": {"url": "/first"}},
        {"name": "not-run", "request": {"url": "/second"}},
    ]}
    with patch("api_testing.requests_runner.subprocess.Popen") as popen:
        result = requests_runner("hard-timeout", json.dumps(case), hard_timeout_seconds=0)
    popen.assert_not_called()
    assert result["status"] == "error"
    assert [step["status"] for step in result["step_datas"]] == ["skipped", "skipped"]
    assert "硬总超时" in result["error"]
    assert "副作用状态未知" in result["error"]
