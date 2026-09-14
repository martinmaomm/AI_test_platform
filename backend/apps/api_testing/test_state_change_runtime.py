"""Offline state-change regressions for generated API cases.

These tests deliberately use opaque paths and fields.  Every HTTP exchange is
handled by a local fake Session; no Django, network service, or business data
is involved.
"""

from copy import deepcopy
from datetime import timedelta
from unittest.mock import patch

from api_testing.case_contract import export_python
from api_testing.requests_runtime import run_case


class FakeResponse:
    def __init__(self, body=None, status_code=200):
        self.status_code = status_code
        self._body = body if body is not None else {}
        self.headers = {"content-type": "application/json"}
        self.url = "https://api.example.test/"
        self.elapsed = timedelta(milliseconds=1)
        self.text = "{}"
        self.content = b"{}"

    def json(self):
        return deepcopy(self._body)


class StatefulSession:
    """A tiny opaque-resource service whose PATCH may intentionally be a no-op."""

    def __init__(self, marker, *, apply_updates):
        self.marker = marker
        self.apply_updates = apply_updates
        self.requests = []
        self.deleted_ids = []
        self.created_ids = []

    def request(self, **kwargs):
        self.requests.append(deepcopy(kwargs))
        method = kwargs["method"]
        path = kwargs["url"].removeprefix("https://api.example.test")
        if method == "POST":
            opaque_id = "opaque-created"
            self.created_ids.append(opaque_id)
            self.marker = kwargs["json"]["marker"]
            return FakeResponse({"opaque_id": opaque_id})
        if method == "GET":
            opaque_id = path.rsplit("/", 1)[-1]
            return FakeResponse({"opaque_id": opaque_id, "marker": self.marker})
        if method == "PATCH":
            if self.apply_updates:
                self.marker = kwargs["json"]["marker"]
            return FakeResponse({"accepted": True})
        if method == "DELETE":
            self.deleted_ids.append(path.rsplit("/", 1)[-1])
            return FakeResponse({"removed": True})
        raise AssertionError(f"unexpected request: {method} {path}")

    def close(self):
        pass


def _state_change_case(*, before_template="before_${uuid4}", after_template="after_${uuid4}", cleanup=False):
    steps = [
        *([{
            "name": "create this run object",
            "request": {"method": "POST", "url": "/records", "json": {"marker": "seed_${uuid4}"}},
            "extract": {"created_id": "body.opaque_id"},
            "validate": [{"eq": ["status_code", 200]}],
        }] if cleanup else []),
        {
            "name": "read prior state",
            "request": {"url": "/records/${created_id}" if cleanup else "/records/opaque-current"},
            "extract": {"extracted_id": "body.opaque_id", "before_value": "body.marker"},
            "validate": [{"eq": ["status_code", 200]}],
        },
        {
            "name": "update same opaque object",
            "request": {"method": "PATCH", "url": "/records/${extracted_id}", "json": {"marker": "${after_marker}"}},
            "validate": [{"eq": ["status_code", 200]}],
        },
        {
            "name": "read changed state",
            "request": {"url": "/records/${extracted_id}"},
            "validate": [
                {"eq": ["body.marker", "${after_marker}"]},
                {"ne": ["body.marker", "${before_value}"]},
            ],
        },
    ]
    if cleanup:
        steps.append({
            "name": "remove only this run object",
            "phase": "cleanup",
            "requires": ["created_id"],
            "request": {"method": "DELETE", "url": "/records/${created_id}"},
            "validate": [{"eq": ["status_code", 200]}],
        })
    return {
        "config": {
            "base_url": "https://api.example.test",
            "variables": {
                # A stale config value must never authorize cleanup to target it.
                "created_id": "opaque-stale",
                "before_marker": before_template,
                "after_marker": after_template,
            },
        },
        "teststeps": steps,
    }


def _exported_runner(case):
    namespace = {"__name__": "exported_state_change_case"}
    exec(compile(export_python(case), "exported_state_change_case.py", "exec"), namespace)
    return namespace


def test_phase_templates_are_stable_per_run_distinct_by_prefix_and_regenerated_between_runs():
    case = {
        "config": {
            "base_url": "https://api.example.test",
            "variables": {
                "before_marker": "before_${uuid4}",
                "after_marker": "after_${uuid4}",
                "before_stamp": "before_${timestamp_ns}",
                "after_stamp": "after_${timestamp_ns}",
                "nested": {"markers": {"before": "${before_marker}", "after": "${after_marker}"}},
                "nested_alias": "${nested}",
            },
        },
        "teststeps": [
            {"request": {"method": "POST", "url": "/signals", "json": {"payload": "${nested_alias}", "before_stamp": "${before_stamp}", "after_stamp": "${after_stamp}"}}},
            {"request": {"method": "POST", "url": "/signals", "json": {"payload": "${nested_alias}", "before": "${before_marker}", "after": "${after_marker}"}}},
        ],
    }
    first, second = StatefulSession("unused", apply_updates=False), StatefulSession("unused", apply_updates=False)
    # PATCH is not involved here, so use a simple response-only session shape.
    first.request = lambda **kwargs: (first.requests.append(deepcopy(kwargs)) or FakeResponse())
    second.request = lambda **kwargs: (second.requests.append(deepcopy(kwargs)) or FakeResponse())
    with patch("api_testing.requests_runtime.time.time_ns", side_effect=[101, 202]), patch(
        "api_testing.requests_runtime.uuid.uuid4", side_effect=["run-one", "run-two"],
    ), patch("api_testing.requests_runtime.requests.Session", side_effect=[first, second]):
        assert run_case("phase-one", case)["success"]
        assert run_case("phase-two", case)["success"]

    first_payload, first_repeat = first.requests[0]["json"], first.requests[1]["json"]
    second_payload = second.requests[0]["json"]
    assert first_payload["payload"] == {"markers": {"before": "before_run-one", "after": "after_run-one"}}
    assert first_payload["before_stamp"] == "before_101"
    assert first_payload["after_stamp"] == "after_101"
    assert first_repeat["payload"] == first_payload["payload"]
    assert first_repeat["before"] != first_repeat["after"]
    assert second_payload["payload"] != first_payload["payload"]
    assert second_payload["before_stamp"] == "before_202"


def test_state_change_requires_actual_transition_not_just_a_200_response():
    case = _state_change_case()
    for apply_updates, expected_status in [(False, "failed"), (True, "passed")]:
        session = StatefulSession("stored-before", apply_updates=apply_updates)
        with patch("api_testing.requests_runtime.uuid.uuid4", return_value="case-token"), patch(
            "api_testing.requests_runtime.requests.Session", return_value=session,
        ):
            result = run_case("state-change", case)
        assert result["status"] == expected_status, result
        assert [item["method"] for item in session.requests] == ["GET", "PATCH", "GET"]


def test_two_identical_templates_cannot_make_a_noop_update_pass():
    # The final eq passes because both templates and the stored value are equal;
    # only ne against the extracted prior response catches this historical bug.
    case = _state_change_case(before_template="${uuid4}", after_template="${uuid4}")
    session = StatefulSession("same-token", apply_updates=False)
    with patch("api_testing.requests_runtime.uuid.uuid4", return_value="same-token"), patch(
        "api_testing.requests_runtime.requests.Session", return_value=session,
    ):
        result = run_case("same-template-noop", case)
    assert result["status"] == "failed", result
    records = result["step_datas"][-1]["validators"]["validate_extractor"]
    assert records[0]["passed"] is True
    assert records[1]["passed"] is False


def test_idempotent_same_value_update_is_allowed_when_only_equality_is_required():
    case = {
        "config": {"base_url": "https://api.example.test"},
        "teststeps": [
            {"request": {"url": "/records/opaque-current"}, "extract": {"before_value": "body.marker"}},
            {"request": {"method": "PATCH", "url": "/records/opaque-current", "json": {"marker": "${before_value}"}},
             "validate": [{"eq": ["status_code", 200]}]},
            {"request": {"url": "/records/opaque-current"}, "extract": {"after_value": "body.marker"},
             "validate": [{"eq": ["body.marker", "${before_value}"]}]},
        ],
    }
    session = StatefulSession("unchanged-by-design", apply_updates=False)
    with patch("api_testing.requests_runtime.requests.Session", return_value=session):
        result = run_case("idempotent-update", case)
    assert result["status"] == "passed", result
    assert result["step_datas"][-1]["export_vars"]["after_value"] == "unchanged-by-design"


def test_exported_python_keeps_the_same_state_change_semantics():
    case = _state_change_case()
    namespace = _exported_runner(case)
    for apply_updates, expected_status in [(False, "failed"), (True, "passed")]:
        session = StatefulSession("stored-before", apply_updates=apply_updates)
        with patch.object(namespace["uuid"], "uuid4", return_value="export-token"), patch.object(
            namespace["requests"], "Session", return_value=session,
        ):
            result = namespace["run_case"]("exported-state-change", namespace["CASE"])
        assert result["status"] == expected_status, result

    same_template_case = _state_change_case(before_template="${uuid4}", after_template="${uuid4}")
    same_template_namespace = _exported_runner(same_template_case)
    session = StatefulSession("export-same-token", apply_updates=False)
    with patch.object(same_template_namespace["uuid"], "uuid4", return_value="export-same-token"), patch.object(
        same_template_namespace["requests"], "Session", return_value=session,
    ):
        result = same_template_namespace["run_case"](
            "exported-same-template-noop", same_template_namespace["CASE"],
        )
    assert result["status"] == "failed", result
    records = result["step_datas"][-1]["validators"]["validate_extractor"]
    assert records[0]["passed"] is True
    assert records[1]["passed"] is False


def test_cleanup_uses_only_this_run_extracted_id_after_validation_failure():
    case = _state_change_case(cleanup=True)
    session = StatefulSession("stored-before", apply_updates=False)
    with patch("api_testing.requests_runtime.uuid.uuid4", return_value="cleanup-token"), patch(
        "api_testing.requests_runtime.requests.Session", return_value=session,
    ):
        result = run_case("cleanup-bound-id", case)
    assert result["status"] == "failed", result
    assert session.created_ids == ["opaque-created"]
    assert session.deleted_ids == ["opaque-created"]
    assert all("opaque-stale" not in request["url"] for request in session.requests)
    assert result["step_datas"][-1]["status"] == "passed"
