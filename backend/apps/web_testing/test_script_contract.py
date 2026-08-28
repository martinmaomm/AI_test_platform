import ast
import os
import shutil
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from .playwright_python_runner import ExecutionConfig, PlaywrightRunner
from .script_contract import (
    ScriptContractError,
    materialize_script,
    normalize_for_storage,
    normalize_script,
    validate_script,
)


class ScriptContractTests(unittest.TestCase):
    def test_valid_run_script_is_kept_without_browser_lifecycle(self):
        script = "from playwright.async_api import expect\n\nasync def run(page):\n    await page.goto('/')\n    await expect(page).to_have_title('Home')\n"

        result = validate_script(script)

        self.assertFalse(result.legacy)
        self.assertEqual(result.content, script.strip())

    def test_syntax_error_is_readable(self):
        with self.assertRaisesRegex(ScriptContractError, r"脚本语法错误.*第 1 行"):
            validate_script("async def run(page)\n    pass")

    def test_run_cannot_manage_browser(self):
        script = "from playwright.async_api import async_playwright\n\nasync def run(page):\n    async with async_playwright() as p:\n        await p.chromium.launch()\n"

        with self.assertRaisesRegex(ScriptContractError, "不得创建或管理浏览器"):
            validate_script(script)

    def test_run_signature_only_allows_one_page_argument(self):
        invalid_scripts = [
            "async def run(page, extra):\n    pass",
            "async def run(page, *, required):\n    pass",
            "async def run(*args):\n    pass",
            "async def run(**kwargs):\n    pass",
            "async def run(page='default'):\n    pass",
        ]

        for script in invalid_scripts:
            with self.subTest(script=script):
                with self.assertRaisesRegex(ScriptContractError, "run 函数必须"):
                    validate_script(script)

    def test_legacy_main_is_converted_to_run(self):
        script = "async def main():\n    print('legacy')\n\nif __name__ == '__main__':\n    pass\n"

        result = normalize_script(script)

        self.assertTrue(result.legacy)
        self.assertIn("async def run(page):", result.content)
        self.assertIn("await main()", result.content)
        ast.parse(result.content)

    def test_legacy_main_with_page_keeps_page_argument(self):
        result = normalize_script("async def main(page):\n    await page.goto('/')\n")

        self.assertIn("await main(page)", result.content)

    def test_legacy_script_that_owns_browser_is_not_double_wrapped(self):
        script = """from playwright.async_api import async_playwright

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        await browser.close()
"""

        materialized = materialize_script(script, "test_legacy")

        self.assertIn("asyncio.run(main())", materialized)
        self.assertNotIn("browser_type.launch", materialized)

    def test_run_wins_over_legacy_main_and_keeps_managed_configuration(self):
        script = """from playwright.async_api import async_playwright

async def main():
    async with async_playwright() as p:
        await p.chromium.launch()

async def run(page):
    await page.goto('/')
"""

        materialized = materialize_script(script, "test_mixed", headed=True, base_url="http://managed.test")

        self.assertIn("browser_type.launch(headless=False)", materialized)
        self.assertIn('context_kwargs["base_url"]', materialized)
        self.assertIn("await run(page)", materialized)
        self.assertNotIn("asyncio.run(main())", materialized)

    def test_legacy_main_page_that_owns_browser_is_rejected(self):
        script = """from playwright.async_api import async_playwright

async def main(page):
    async with async_playwright() as p:
        await p.chromium.launch()
"""

        with self.assertRaisesRegex(ScriptContractError, r"main\(page\).*自行管理浏览器"):
            materialize_script(script, "legacy_page")

    def test_materialize_validates_browser_and_identifier(self):
        with self.assertRaisesRegex(ScriptContractError, "只支持 chromium"):
            materialize_script("async def run(page):\n    pass\n", "1-case", browser="edge")

        materialized = materialize_script("async def run(page):\n    pass\n", "1-case")
        self.assertIn("def test_1_case():", materialized)

    def test_generated_steps_code_is_a_storage_valid_run_script(self):
        from django.conf import settings
        if not settings.configured:
            self.skipTest("step generator test requires Django settings")
        from types import SimpleNamespace
        from .views import _force_inject_playwright_rules, _generate_playwright_from_steps

        test_case = SimpleNamespace(
            steps=[{"step_id": "step-1", "action": "click", "description": "点击按钮"}],
            expected_result="首页显示成功",
            url="/",
        )
        result = _generate_playwright_from_steps(test_case, project_id=999999)
        final_code = _force_inject_playwright_rules(result["full_code"], test_case)

        ast.parse(final_code)
        self.assertIn("from playwright.async_api import expect", final_code)
        self.assertIn("async def run(page)", final_code)
        self.assertNotIn("async def main", final_code)
        self.assertNotIn("launch(", final_code)
        normalize_for_storage(final_code)

    def test_storage_validation_rejects_legacy_and_browser_entries(self):
        invalid_scripts = [
            "async def main():\n    pass",
            "async def run(page):\n    pass\n\nasync def main():\n    pass",
            "from playwright.async_api import async_playwright\nasync def run(page):\n    async with async_playwright() as p:\n        pass",
            "async def run(page):\n    pass\n\ndef test_old():\n    pass",
        ]

        for script in invalid_scripts:
            with self.subTest(script=script):
                with self.assertRaises(ScriptContractError):
                    normalize_for_storage(script)

    def test_both_write_serializers_use_strict_storage_validation(self):
        from django.conf import settings
        if not settings.configured:
            self.skipTest("serializer test requires Django settings")
        from .serializers import WebUITestCaseCreateSerializer, WebUITestCaseDetailSerializer

        invalid = "async def main():\n    pass"
        for serializer_class in (WebUITestCaseCreateSerializer, WebUITestCaseDetailSerializer):
            with self.subTest(serializer=serializer_class.__name__):
                with self.assertRaises(Exception):
                    serializer_class().validate_test_script_content(invalid)

    def test_runner_materialization_does_not_share_config(self):
        runner = PlaywrightRunner()
        headed_dir = tempfile.mkdtemp(dir=runner.temp_base_dir)
        headless_dir = tempfile.mkdtemp(dir=runner.temp_base_dir)
        try:
            script = "async def run(page):\n    await page.goto('/')\n"
            headed_file = runner._create_test_file(
                headed_dir, script, ExecutionConfig(headed=True, base_url="http://headed.test")
            )
            headless_file = runner._create_test_file(
                headless_dir, script, ExecutionConfig(headed=False, base_url="http://headless.test")
            )

            with open(headed_file, encoding="utf-8") as file:
                headed_content = file.read()
            with open(headless_file, encoding="utf-8") as file:
                headless_content = file.read()
            self.assertIn("headless=False", headed_content)
            self.assertIn("http://headed.test", headed_content)
            self.assertIn("headless=True", headless_content)
            self.assertIn("http://headless.test", headless_content)
            self.assertNotIn("http://headless.test", headed_content)
            self.assertNotIn("http://headed.test", headless_content)
        finally:
            shutil.rmtree(headed_dir, ignore_errors=True)
            shutil.rmtree(headless_dir, ignore_errors=True)

    def test_runner_drops_legacy_mcp_browser_path_for_python_execution(self):
        runner = PlaywrightRunner()
        work_dir = tempfile.mkdtemp(dir=runner.temp_base_dir)
        completed = SimpleNamespace(returncode=0, stdout="", stderr="")
        try:
            with patch.dict(
                os.environ,
                {"PLAYWRIGHT_BROWSERS_PATH": ".playwright-browsers"},
                clear=True,
            ), patch(
                "web_testing.playwright_python_runner.subprocess.run",
                return_value=completed,
            ) as run:
                runner._run_pytest_command(work_dir, ExecutionConfig())

            child_env = run.call_args.kwargs["env"]
            self.assertNotIn("PLAYWRIGHT_BROWSERS_PATH", child_env)
            self.assertEqual(run.call_args.args[0][0], sys.executable)
        finally:
            shutil.rmtree(work_dir, ignore_errors=True)

    def test_runner_resolves_independent_python_browser_path_from_project_root(self):
        runner = PlaywrightRunner()
        work_dir = tempfile.mkdtemp(dir=runner.temp_base_dir)
        completed = SimpleNamespace(returncode=0, stdout="", stderr="")
        try:
            with patch.dict(
                os.environ,
                {
                    "PLAYWRIGHT_BROWSERS_PATH": ".playwright-browsers",
                    "PYTHON_PLAYWRIGHT_BROWSERS_PATH": ".python-playwright-browsers",
                },
                clear=True,
            ), patch(
                "web_testing.playwright_python_runner.subprocess.run",
                return_value=completed,
            ) as run:
                runner._run_pytest_command(work_dir, ExecutionConfig())

            child_env = run.call_args.kwargs["env"]
            expected = os.path.join(runner.project_root, ".python-playwright-browsers")
            self.assertEqual(child_env["PLAYWRIGHT_BROWSERS_PATH"], expected)
        finally:
            shutil.rmtree(work_dir, ignore_errors=True)

    def test_materialized_script_owns_browser_and_uses_context_base_url(self):
        headed_materialized = materialize_script(
            "from playwright.async_api import expect\n\nasync def run(page):\n    await page.goto('/')\n",
            "test_case_1",
            browser="chromium",
            headed=True,
            base_url="http://example.test",
        )
        headless_materialized = materialize_script(
            "from playwright.async_api import expect\n\nasync def run(page):\n    await page.goto('/')\n",
            "test_case_2",
            browser="chromium",
            headed=False,
            base_url="http://example.test",
        )

        tree = ast.parse(headed_materialized)
        self.assertTrue(any(isinstance(node, ast.AsyncFunctionDef) and node.name == "run" for node in tree.body))
        self.assertIn("browser_type.launch(headless=False)", headed_materialized)
        self.assertIn("browser_type.launch(headless=True)", headless_materialized)
        self.assertIn('context_kwargs["base_url"]', headed_materialized)
        self.assertIn("asyncio.run(_run_with_managed_browser())", headed_materialized)
        self.assertNotIn("--base-url", headed_materialized)


if __name__ == "__main__":
    unittest.main()
