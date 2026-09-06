"""Offline contract tests for the requests API-test runtime.

Every request is made through a mocked Session.  No environment variables,
network services, credentials, or Django database are involved.
"""

import ast
import builtins
import json
from datetime import timedelta
import subprocess
from types import SimpleNamespace
from unittest.mock import patch

import requests

from api_testing.case_contract import CaseContractError, export_python, normalize_case
from api_testing.requests_runner import requests_runner
from api_testing.requests_runtime import run_case


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


def test_runtime_supports_editor_timeout_redirect_cookies_and_verify_fields():
    session = FakeSession([FakeResponse()])
    case = {"config": {"base_url": "https://api.example.test"}, "teststeps": [{
        "request": {"url": "/health", "timeout": 5, "allow_redirects": False, "cookies": {"sid": "test"}, "verify": False},
    }]}
    with patch("api_testing.requests_runtime.requests.Session", return_value=session):
        result = run_case("editor-fields", json.dumps(case), options={"total_timeout": 20})
    assert result["status"] == "passed"
    request = session.requests[0]
    assert request["timeout"] == (5.0, 5.0)
    assert request["allow_redirects"] is False
    assert request["cookies"] == {"sid": "test"}
    assert request["verify"] is False


def test_environment_verify_ssl_and_base_url_api_prefix_are_honoured():
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
    assert session.requests[1]["verify"] is True


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


def test_empty_steps_are_editable_but_not_executable():
    assert normalize_case({"config": {}, "teststeps": []})["teststeps"] == []
    result = requests_runner("empty", json.dumps({"config": {}, "teststeps": []}))
    assert result["success"] is False
    assert result["status"] == "error"
    assert "至少需要一个测试步骤" in result["error"]


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


def test_platform_runner_uses_controlled_json_worker_with_hard_deadline():
    case = {"config": {"base_url": "https://api.example.test"}, "teststeps": [{"request": {"url": "/health"}}]}
    worker_result = {"success": True, "status": "passed", "step_datas": []}
    with patch("api_testing.requests_runner.subprocess.run", return_value=SimpleNamespace(stdout=json.dumps(worker_result), stderr="", returncode=0)) as run:
        result = requests_runner("platform", json.dumps(case), options={"total_timeout": 1})
    assert result == worker_result
    payload = json.loads(run.call_args.kwargs["input"])
    assert payload["case"]["teststeps"][0]["request"]["method"] == "GET"
    assert run.call_args.kwargs["timeout"] == 3.0
    assert run.call_args.kwargs["check"] is False


def test_platform_runner_hard_timeout_preserves_unknown_step_boundary():
    case = {"config": {"base_url": "https://api.example.test"}, "teststeps": [
        {"name": "in-flight", "request": {"url": "/first"}},
        {"name": "not-run", "request": {"url": "/second"}},
    ]}
    with patch("api_testing.requests_runner.subprocess.run", side_effect=subprocess.TimeoutExpired("requests_worker", 3)):
        result = requests_runner("hard-timeout", json.dumps(case), options={"total_timeout": 1})
    assert result["status"] == "error"
    assert result["step_datas"] == []
    assert "硬总超时" in result["error"]
    assert "是否生效未知" in result["error"]
