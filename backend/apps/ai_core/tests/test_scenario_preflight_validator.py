import unittest

from ai_core.scenario_contract import build_generation_endpoint
from ai_core.scenario_preflight_validator import ScenarioPreflightValidator


class ScenarioPreflightValidatorTests(unittest.TestCase):
    def setUp(self):
        self.validator = ScenarioPreflightValidator()

    def test_valid_scenario_allows_builtin_function_and_reports_unverified_paths(self):
        report = self.validator.validate(
            {
                "config": {"variables": {}},
                "teststeps": [
                    {
                        "name": "注册",
                        "request": {
                            "method": "POST",
                            "url": "/register",
                            "json": {"username": "test${get_timestamp()}"},
                        },
                        "extract": {"user_id": "body.data.id"},
                        "validate": [{"eq": ["status_code", 200]}],
                    },
                    {
                        "name": "详情",
                        "request": {
                            "method": "GET",
                            "url": "/users/${user_id}",
                        },
                        "extract": {},
                        "validate": [{"eq": ["body.data.id", "${user_id}"]}],
                    },
                ],
            }
        )

        self.assertTrue(report["valid"])
        self.assertEqual(report["summary"]["error_count"], 0)
        self.assertGreaterEqual(report["summary"]["warning_count"], 1)

    def test_undefined_variable_is_an_error(self):
        report = self.validator.validate(
            {
                "config": {"variables": {}},
                "teststeps": [
                    {
                        "name": "详情",
                        "request": {"method": "GET", "url": "/users/${user_id}"},
                        "extract": {},
                        "validate": [],
                    }
                ],
            }
        )

        self.assertFalse(report["valid"])
        self.assertEqual(report["errors"][0]["kind"], "undefined_variable")
        self.assertEqual(report["errors"][0]["variable"], "user_id")

    def test_extracted_variable_is_not_available_in_the_same_step(self):
        report = self.validator.validate(
            {
                "config": {"variables": {}},
                "teststeps": [
                    {
                        "name": "注册",
                        "request": {"method": "POST", "url": "/register/${user_id}"},
                        "extract": {"user_id": "body.data.id"},
                        "validate": [],
                    }
                ],
            }
        )

        self.assertFalse(report["valid"])
        self.assertTrue(
            any(
                issue["kind"] == "undefined_variable"
                and issue["variable"] == "user_id"
                for issue in report["errors"]
            )
        )

    def test_invalid_path_and_assertion_shape_are_errors(self):
        report = self.validator.validate(
            {
                "config": {"variables": {}},
                "teststeps": [
                    {
                        "name": "异常响应",
                        "request": {"method": "GET", "url": "/error"},
                        "extract": {"code": "response.data.code"},
                        "validate": [["eq", ["body.code", 200]]],
                    }
                ],
            }
        )

        self.assertFalse(report["valid"])
        error_kinds = {issue["kind"] for issue in report["errors"]}
        self.assertIn("invalid_response_path", error_kinds)
        self.assertIn("invalid_assertion", error_kinds)

    def test_self_referencing_step_variable_is_an_error(self):
        report = self.validator.validate(
            {
                "config": {"variables": {}},
                "teststeps": [
                    {
                        "name": "循环变量",
                        "variables": {"token": "Bearer ${token}"},
                        "request": {"method": "GET", "url": "/profile"},
                        "extract": {},
                        "validate": [],
                    }
                ],
            }
        )

        self.assertFalse(report["valid"])
        self.assertEqual(report["errors"][0]["kind"], "circular_variable")

    def test_response_contract_accepts_documented_path_and_rejects_invented_path(self):
        api_specs = [
            {
                "endpoints": [
                    {
                        "method": "POST",
                        "path": "/auth/login",
                        "responses": {
                            "200": {
                                "content": {
                                    "application/json": {
                                        "example": {"token": "abc", "user": {"id": 7}}
                                    }
                                }
                            }
                        },
                    }
                ]
            }
        ]

        valid_report = self.validator.validate(
            {
                "config": {"variables": {}},
                "teststeps": [
                    {
                        "name": "登录",
                        "request": {"method": "POST", "url": "/auth/login"},
                        "extract": {"token": "body.token"},
                        "validate": [
                            {"eq": ["status_code", 200]},
                            {"eq": ["body.token", "abc"]},
                        ],
                    }
                ],
            },
            api_specifications=api_specs,
        )
        self.assertTrue(valid_report["valid"])
        self.assertEqual(valid_report["summary"]["warning_count"], 0)

        invalid_report = self.validator.validate(
            {
                "config": {"variables": {}},
                "teststeps": [
                    {
                        "name": "登录",
                        "request": {"method": "POST", "url": "/auth/login"},
                        "extract": {},
                        "validate": [{"eq": ["body.success", True]}],
                    }
                ],
            },
            api_specifications=api_specs,
        )
        # OpenAPI 文档可能不完整，契约中没有声明的响应字段作为警告，
        # 不应阻断场景保存；真实调试响应再确认该字段是否存在。
        self.assertTrue(invalid_report["valid"])
        self.assertEqual(invalid_report["summary"]["error_count"], 0)
        self.assertEqual(
            invalid_report["warnings"][0]["kind"],
            "response_path_not_in_contract",
        )

    def test_generation_endpoint_resolves_swagger_response_refs(self):
        endpoint = build_generation_endpoint(
            {
                "method": "POST",
                "path": "/auth/login",
                "responses": {
                    "200": {
                        "description": "OK",
                        "schema": {"$ref": "#/definitions/CommonResult"},
                    }
                },
            },
            definitions={
                "CommonResult": {
                    "type": "object",
                    "properties": {
                        "code": {"type": "integer"},
                        "data": {"$ref": "#/definitions/LoginData"},
                    },
                },
                "LoginData": {
                    "type": "object",
                    "properties": {"token": {"type": "string"}},
                },
            },
        )

        schema = endpoint["responses"]["200"]["content"]["application/json"]["schema"]
        self.assertIn("code", schema["properties"])
        self.assertIn("token", schema["properties"]["data"]["properties"])


if __name__ == "__main__":
    unittest.main()
