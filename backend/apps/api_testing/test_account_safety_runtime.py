"""Pure-Python, offline tests for explicit account/role safety declarations."""

from datetime import timedelta
from unittest.mock import patch

from api_testing.requests_runtime import (
    account_safety_issues,
    account_safety_prompt_rules,
    export_python,
    normalize_case,
    run_case,
    validate_account_safety,
)


class FakeResponse:
    def __init__(self, status_code=200, body=None, url="https://api.example.test/"):
        self.status_code = status_code
        self._body = body
        self.url = url
        self.headers = {"content-type": "application/json"}
        self.elapsed = timedelta(milliseconds=1)
        self.text = ""

    def json(self):
        return self._body


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []
        self.closed = False

    def request(self, **kwargs):
        self.requests.append(kwargs)
        return self.responses.pop(0)

    def close(self):
        self.closed = True


def _case(steps, variables=None):
    return {
        "version": 1,
        "config": {"base_url": "https://api.example.test", "variables": variables or {}},
        "teststeps": steps,
    }


def _protect_extract_step(variable="auth_id", entity="account"):
    return {
        "name": "login",
        "request": {"method": "POST", "url": "/login", "json": {"username": "runner"}},
        "extract": {variable: "body.id"},
        "validate": [{"eq": ["status_code", 200]}],
        "account_safety": [{
            "operation": "capture_protected", "entity": entity,
            "identity_source": "extract", "identity_path": [variable],
        }],
    }


def _create_extract_step(identity=42, variable="temp_id", entity="account"):
    return {
        "name": "create temporary",
        "request": {"method": "POST", "url": f"/{entity}s", "json": {"name": "temporary"}},
        "extract": {variable: "body.id"},
        "validate": [{"eq": ["status_code", 201]}],
        "account_safety": [{
            "operation": "create", "entity": entity,
            "identity_source": "extract", "identity_path": [variable],
        }],
    }


def _mutate(entity="account", effect="delete", *, source="path", path=None, request=None, many=False):
    target = {"source": source, "path": path or ["temp_id"]}
    if many:
        target["many"] = True
    return {
        "name": effect,
        "request": request or {"method": "DELETE", "url": f"/{entity}s/${{temp_id}}"},
        "account_safety": [{
            "operation": "mutate", "entity": entity, "effect": effect, "target": target,
        }],
    }


def test_static_helpers_are_nonthrowing_and_ignore_ordinary_steps():
    ordinary = _case([
        {"request": {"method": "POST", "url": "/login"}},
        {"request": {"method": "POST", "url": "/products", "json": {"id": 1}}},
        {"request": {"method": "POST", "url": "/logout"}},
    ])
    assert account_safety_issues(ordinary) == []
    assert validate_account_safety({"request": {"url": "/read"}}) == []
    assert account_safety_prompt_rules()

    malformed_values = [
        {}, [], None, "bad", [None],
        [{"operation": [], "entity": {}, "identity_source": [], "identity_path": [{}]}],
        [{"operation": "mutate", "entity": [], "effect": {}, "target": {"source": [], "path": [{}]}}],
        [{
            "operation": "bind_created", "entity": "account", "identity_source": {},
            "identity_path": [{}], "unique_key": {"name": [], "source": {}, "path": [{}]},
        }],
    ]
    for value in malformed_values:
        step = {"request": {"method": "POST", "url": "/x"}, "account_safety": value}
        issues = validate_account_safety(step, index=3)
        assert issues and all(issue["step"] == 3 and issue["repairable"] is True for issue in issues)
        normalize_case(_case([step]))
    malformed_session = FakeSession([])
    with patch("api_testing.requests_runtime.requests.Session", return_value=malformed_session):
        malformed_result = run_case(
            "malformed-safety",
            _case([{"request": {"method": "POST", "url": "/x"}, "account_safety": {}}]),
        )
    assert malformed_result["error_type"] == "AccountSafetyReview"
    assert malformed_session.requests == []


def test_static_same_unshadowed_protected_extract_flow_is_repairable_issue_only():
    protected = _protect_extract_step()
    dangerous = _mutate(
        source="params", path=["account_id"],
        request={"method": "POST", "url": "/roles/replace", "params": {"account_id": "${auth_id}"}},
    )
    issues = account_safety_issues(_case([protected, dangerous]))
    assert [(item["step"], item["code"]) for item in issues] == [(2, "ACCOUNT_SAFETY_PROTECTED_FLOW")]

    shadowed = {
        "request": {"url": "/temporary"}, "extract": {"auth_id": "body.id"},
    }
    assert account_safety_issues(_case([protected, shadowed, dangerous])) == []
    embedded = _mutate(
        source="params", path=["account_id"],
        request={"method": "POST", "url": "/roles/replace", "params": {"account_id": "x-${auth_id}"}},
    )
    assert account_safety_issues(_case([protected, embedded])) == []


def test_old_dangerous_scene_is_blocked_and_protection_survives_variable_overwrite():
    overwrite = {"request": {"url": "/other"}, "extract": {"auth_id": "body.id"}}
    dangerous = _mutate(
        source="params", path=["account_id"],
        request={"method": "POST", "url": "/roles/replace", "params": {"account_id": "7"}},
    )
    session = FakeSession([FakeResponse(body={"id": 7}), FakeResponse(body={"id": 99})])
    with patch("api_testing.requests_runtime.requests.Session", return_value=session):
        result = run_case("protected-overwrite", _case([_protect_extract_step(), overwrite, dangerous]))
    assert result["error_type"] == "AccountSafetyBlocked"
    assert len(session.requests) == 2
    assert "受保护身份" in result["error"]


def test_successful_request_source_capture_protects_the_normalized_identity():
    login = {
        "request": {"method": "POST", "url": "/login", "json": {"account_id": "007"}},
        "account_safety": [{
            "operation": "capture_protected", "entity": "account",
            "identity_source": "json", "identity_path": ["account_id"],
        }],
    }
    dangerous = _mutate(
        source="params", path=["id"],
        request={"method": "POST", "url": "/disable", "params": {"id": 7}},
    )
    session = FakeSession([FakeResponse(body={"token": "ok"})])
    with patch("api_testing.requests_runtime.requests.Session", return_value=session):
        result = run_case("request-capture", _case([login, dangerous]))
    assert result["error_type"] == "AccountSafetyBlocked"
    assert len(session.requests) == 1


def test_fresh_temporary_lifecycle_runs_mutation_and_cleanup():
    mutate = _mutate(
        effect="password", source="json", path=["account_id"],
        request={"method": "PATCH", "url": "/accounts/password", "json": {"account_id": "${temp_id}"}},
    )
    cleanup = _mutate(
        request={"method": "DELETE", "url": "/accounts/${temp_id}"},
    )
    cleanup.update({"phase": "cleanup", "requires": ["temp_id"]})
    session = FakeSession([
        FakeResponse(201, {"id": "042"}), FakeResponse(200, {}), FakeResponse(204, {}),
    ])
    with patch("api_testing.requests_runtime.requests.Session", return_value=session):
        result = run_case("temporary", _case([_create_extract_step(), mutate, cleanup]))
    assert result["success"] is True
    assert [request["method"] for request in session.requests] == ["POST", "PATCH", "DELETE"]
    assert result["replay_safety"]["cleanup_status"] == "completed"


def test_failed_create_cannot_authorize_cleanup_even_when_extract_had_an_id():
    cleanup = _mutate(request={"method": "DELETE", "url": "/accounts/${temp_id}"})
    cleanup.update({"phase": "cleanup", "requires": ["temp_id"]})
    session = FakeSession([FakeResponse(500, {"id": 42})])
    with patch("api_testing.requests_runtime.requests.Session", return_value=session):
        result = run_case("failed-create", _case([_create_extract_step(), cleanup]))
    assert result["error_type"] == "ValidationFailure"
    assert len(session.requests) == 1
    assert result["step_datas"][1]["status"] == "error"
    assert "没有本轮成功 create 证据" in result["step_datas"][1]["error"]


def test_partial_declaration_failure_cannot_authorize_cleanup():
    create = _create_extract_step()
    create['account_safety'].append({
        'operation': 'create', 'entity': 'role',
        'identity_source': 'response', 'identity_path': ['missing_role_id'],
    })
    cleanup = _mutate()
    cleanup.update({'phase': 'cleanup', 'requires': ['temp_id']})
    session = FakeSession([FakeResponse(201, {'id': 42})])
    with patch('api_testing.requests_runtime.requests.Session', return_value=session):
        result = run_case('partial-proof', _case([create, cleanup]))
    assert result['error_type'] == 'AccountSafetyReview'
    assert len(session.requests) == 1
    assert '没有本轮成功 create 证据' in result['step_datas'][1]['error']


def test_protected_wins_over_created_and_entities_do_not_share_ids():
    create_role = _create_extract_step(entity="role")
    create_role["extract"] = {"temp_id": "body.id"}
    mutate_role = _mutate(entity="role")
    session = FakeSession([FakeResponse(body={"id": 42}), FakeResponse(201, {"id": 42}), FakeResponse(204, {})])
    with patch("api_testing.requests_runtime.requests.Session", return_value=session):
        result = run_case("cross-entity", _case([_protect_extract_step(), create_role, mutate_role]))
    assert result["success"] is True, result

    protect_and_create = _case([
        _protect_extract_step(),
        _create_extract_step(),
        _mutate(),
    ])
    session = FakeSession([FakeResponse(body={"id": 42}), FakeResponse(201, {"id": 42})])
    with patch("api_testing.requests_runtime.requests.Session", return_value=session):
        result = run_case("protected-first", protect_and_create)
    assert result["error_type"] == "AccountSafetyBlocked"
    assert len(session.requests) == 2


def test_shared_roles_are_explicitly_protected_and_empty_role_list_is_legal():
    protect_roles = {
        "request": {"method": "POST", "url": "/login"},
        "validate": [{"eq": ["status_code", 200]}],
        "account_safety": [{
            "operation": "capture_protected", "entity": "role", "identity_source": "response",
            "identity_path": ["roles", "*", "id"], "many": True,
        }],
    }
    dangerous = _mutate(
        entity="role", source="params", path=["role_id"],
        request={"method": "POST", "url": "/permissions", "params": {"role_id": 6}},
    )
    session = FakeSession([FakeResponse(body={"roles": [{"id": 5}, {"id": "6"}]})])
    with patch("api_testing.requests_runtime.requests.Session", return_value=session):
        result = run_case("shared-role", _case([protect_roles, dangerous]))
    assert result["error_type"] == "AccountSafetyBlocked"
    assert len(session.requests) == 1

    empty_session = FakeSession([FakeResponse(body={"roles": []}), FakeResponse(body={"ok": True})])
    with patch("api_testing.requests_runtime.requests.Session", return_value=empty_session):
        empty_result = run_case("no-roles", _case([protect_roles, {"request": {"url": "/products"}}]))
    assert empty_result["success"] is True


def test_unrelated_product_id_and_unannotated_login_logout_reads_writes_are_not_blocked():
    steps = [
        {"request": {"method": "POST", "url": "/login"}},
        {"request": {"url": "/products/7"}},
        {"request": {"method": "POST", "url": "/products", "json": {"id": 7}}},
        {"request": {"method": "POST", "url": "/logout"}},
    ]
    session = FakeSession([FakeResponse() for _ in steps])
    with patch("api_testing.requests_runtime.requests.Session", return_value=session):
        result = run_case("ordinary", _case(steps))
    assert result["success"] is True
    assert len(session.requests) == 4


def test_missing_declared_identity_or_request_target_returns_review_at_the_related_step():
    missing_identity = {
        "request": {"method": "POST", "url": "/login"},
        "account_safety": [{
            "operation": "capture_protected", "entity": "account",
            "identity_source": "response", "identity_path": ["user", "id"],
        }],
    }
    session = FakeSession([FakeResponse(body={"token": "ok"})])
    with patch("api_testing.requests_runtime.requests.Session", return_value=session):
        result = run_case("missing-protected", _case([missing_identity]))
    assert result["error_type"] == "AccountSafetyReview"
    assert len(session.requests) == 1

    missing_target = _mutate(
        source="params", path=["account_id"],
        request={"method": "POST", "url": "/disable", "params": {}},
    )
    session = FakeSession([])
    with patch("api_testing.requests_runtime.requests.Session", return_value=session):
        result = run_case("missing-target", _case([missing_target]))
    assert result["error_type"] == "AccountSafetyReview"
    assert session.requests == []


def test_resolved_alias_and_option_override_cannot_reuse_created_permission():
    mutate = _mutate(
        source="path", path=["target_id"],
        request={"method": "DELETE", "url": "/accounts/${target_id}"},
    )
    session = FakeSession([FakeResponse(201, {"id": 42})])
    case = _case([_create_extract_step(), mutate], variables={"target_id": "${temp_id}"})
    with patch("api_testing.requests_runtime.requests.Session", return_value=session):
        result = run_case("override", case, options={"variables": {"target_id": 99}})
    assert result["error_type"] == "AccountSafetyReview"
    assert len(session.requests) == 1
    assert "99" in result["error"]

    wrong_binding = _mutate(
        source="path", path=["temp_id"],
        request={"method": "DELETE", "url": "/accounts/${target_id}"},
    )
    session = FakeSession([FakeResponse(201, {"id": 42})])
    with patch("api_testing.requests_runtime.requests.Session", return_value=session):
        result = run_case("wrong-binding", _case([_create_extract_step(), wrong_binding], {"target_id": 42}))
    assert result["error_type"] == "AccountSafetyReview"
    assert len(session.requests) == 1


def test_path_params_json_form_and_array_targets_use_actual_request_values():
    create_many = {
        "request": {"method": "POST", "url": "/accounts/bulk", "json": {"count": 4}},
        "account_safety": [{
            "operation": "create", "entity": "account", "identity_source": "response",
            "identity_path": ["items", "*", "id"], "many": True,
        }],
    }
    steps = [
        create_many,
        _mutate(request={"method": "DELETE", "url": "/accounts/1"}, path=[1]),
        _mutate(
            source="params", path=["id"], request={"method": "POST", "url": "/disable", "params": {"id": "2"}},
        ),
        _mutate(
            source="json", path=["account", "id"],
            request={"method": "PATCH", "url": "/permissions", "json": {"account": {"id": 3}}},
        ),
        _mutate(
            source="form", path=["id"], request={"method": "POST", "url": "/password", "data": {"id": "4"}},
        ),
        _mutate(
            source="json", path=["ids", "*"], many=True,
            request={"method": "DELETE", "url": "/accounts", "json": {"ids": [1, "2", 3, "04"]}},
        ),
    ]
    session = FakeSession([FakeResponse(201, {"items": [{"id": item} for item in range(1, 5)]})] + [FakeResponse() for _ in range(5)])
    with patch("api_testing.requests_runtime.requests.Session", return_value=session):
        result = run_case("locations", _case(steps))
    assert result["success"] is True, result
    assert len(session.requests) == 6


def _deferred_create_and_bind(response_items):
    create = {
        "request": {"method": "POST", "url": "/accounts", "json": {"username": "temp-1"}},
        "validate": [{"eq": ["status_code", 201]}],
        "account_safety": [{
            "operation": "create", "entity": "account",
            "unique_key": {"name": "username", "source": "json", "path": ["username"]},
        }],
    }
    unrelated = {"request": {"url": "/health"}}
    bind = {
        "request": {"url": "/accounts", "params": {"username": "temp-1"}},
        "validate": [{"eq": ["status_code", 200]}],
        "account_safety": [{
            "operation": "bind_created", "entity": "account", "identity_source": "response",
            "identity_path": ["items", "*", "id"], "many": True,
            "unique_key": {
                "name": "username", "source": "params", "path": ["username"],
                "response_path": ["items", "*", "username"],
            },
        }],
    }
    mutate = _mutate(request={"method": "DELETE", "url": "/accounts/42"}, path=[1])
    responses = [FakeResponse(201, {}), FakeResponse(200, {"ok": True}), FakeResponse(200, {"items": response_items})]
    return _case([create, unrelated, bind, mutate]), responses


def test_deferred_binding_allows_unrelated_query_but_requires_same_unique_response_record():
    case, responses = _deferred_create_and_bind([
        {"id": 99, "username": "old"}, {"id": "042", "username": "temp-1"},
    ])
    session = FakeSession(responses + [FakeResponse(204, {})])
    with patch("api_testing.requests_runtime.requests.Session", return_value=session):
        result = run_case("bound", case)
    assert result["success"] is True, result
    assert len(session.requests) == 4

    ignored_filter_case, responses = _deferred_create_and_bind([{"id": 99, "username": "old"}])
    session = FakeSession(responses)
    with patch("api_testing.requests_runtime.requests.Session", return_value=session):
        result = run_case("ignored-filter", ignored_filter_case)
    assert result["error_type"] == "AccountSafetyReview"
    assert len(session.requests) == 3
    assert "实际匹配 0 条" in result["error"]

    duplicate_case, responses = _deferred_create_and_bind([
        {"id": 42, "username": "temp-1"}, {"id": 43, "username": "temp-1"},
    ])
    session = FakeSession(responses)
    with patch("api_testing.requests_runtime.requests.Session", return_value=session):
        result = run_case("duplicate", duplicate_case)
    assert result["error_type"] == "AccountSafetyReview"
    assert "实际匹配 2 条" in result["error"]


def test_deferred_unique_key_comparison_is_type_and_value_strict():
    case, _responses = _deferred_create_and_bind([])
    case["teststeps"].pop(1)
    case["teststeps"][0]["request"]["json"]["username"] = 1
    case["teststeps"][1]["request"]["params"]["username"] = "1"
    session = FakeSession([FakeResponse(201, {})])
    with patch("api_testing.requests_runtime.requests.Session", return_value=session):
        result = run_case("strict-key", case)
    assert result["error_type"] == "AccountSafetyReview"
    assert len(session.requests) == 1


def test_get_cannot_claim_create_and_bind_paths_must_share_a_record_prefix():
    fake_create = {
        "request": {"method": "GET", "url": "/accounts/old"},
        "account_safety": [{
            "operation": "create", "entity": "account", "identity_source": "response",
            "identity_path": ["id"],
        }],
    }
    issues = account_safety_issues(_case([fake_create]))
    assert any(issue["code"] == "ACCOUNT_SAFETY_OPERATION_REQUIRED" for issue in issues)
    session = FakeSession([])
    with patch("api_testing.requests_runtime.requests.Session", return_value=session):
        result = run_case("fake-create", _case([fake_create]))
    assert result["error_type"] == "AccountSafetyReview"
    assert session.requests == []

    bad_bind = {
        "request": {"url": "/accounts", "params": {"username": "temp"}},
        "account_safety": [{
            "operation": "bind_created", "entity": "account", "identity_source": "response",
            "identity_path": ["items", "*", "id"], "many": True,
            "unique_key": {
                "name": "username", "source": "params", "path": ["username"],
                "response_path": ["other_items", "*", "username"],
            },
        }],
    }
    assert any(issue["code"] == "ACCOUNT_SAFETY_UNIQUE_KEY_REQUIRED" for issue in validate_account_safety(bad_bind))


def test_exported_python_uses_the_same_account_safety_runtime():
    case = _case([_protect_extract_step(), _mutate(
        source="params", path=["id"],
        request={"method": "POST", "url": "/disable", "params": {"id": "7"}},
    )])
    namespace = {"__name__": "exported_account_safety"}
    exec(compile(export_python(case), "exported_account_safety.py", "exec"), namespace)
    native_session = FakeSession([FakeResponse(body={"id": 7})])
    with patch("api_testing.requests_runtime.requests.Session", return_value=native_session):
        native = run_case("same", case)
    exported_session = FakeSession([FakeResponse(body={"id": 7})])
    with patch.object(namespace["requests"], "Session", return_value=exported_session):
        exported = namespace["run_case"]("same", namespace["CASE"])
    assert native["error_type"] == exported["error_type"] == "AccountSafetyBlocked"
    assert native["step_datas"] == exported["step_datas"]
    assert len(native_session.requests) == len(exported_session.requests) == 1
