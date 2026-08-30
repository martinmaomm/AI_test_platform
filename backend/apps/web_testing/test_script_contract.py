import ast
import os
import shutil
import sys
import tempfile
import types
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from .constants import normalize_webui_execution_options
from .playwright_python_runner import (
    ExecutionConfig,
    PlaywrightRunner,
    extract_execution_error,
)
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

    def test_materialize_uses_fixed_chrome_engine_and_valid_identifier(self):
        materialized = materialize_script("async def run(page):\n    pass\n", "1-case")
        self.assertIn("def test_1_case():", materialized)
        self.assertIn("getattr(playwright, 'chromium')", materialized)
        self.assertNotIn("firefox", materialized)
        self.assertNotIn("webkit", materialized)

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

    def test_execution_options_ignore_client_browser_and_unknown_fields(self):
        options = normalize_webui_execution_options({
            'browser': 'firefox',
            'headed': False,
            'timeout': 60,
            'html_report': True,
        })

        self.assertEqual(options, {'headed': False, 'timeout': 60})

    def test_execution_options_validate_types_and_timeout_range(self):
        with self.assertRaisesRegex(ValueError, 'headed'):
            normalize_webui_execution_options({'headed': 'false'})
        with self.assertRaisesRegex(ValueError, '30 到 1800'):
            normalize_webui_execution_options({'timeout': 10})

    def test_execution_error_extracts_real_pytest_exception(self):
        stdout = """
test_playwright.py::test_webui_case FAILED
E   playwright._impl._errors.TimeoutError: Locator.click: Timeout 30000ms exceeded.
=========================== short test summary info ============================
"""

        self.assertEqual(
            extract_execution_error(stdout),
            'playwright._impl._errors.TimeoutError: Locator.click: Timeout 30000ms exceeded.',
        )

    def test_materialized_script_owns_browser_and_uses_context_base_url(self):
        headed_materialized = materialize_script(
            "from playwright.async_api import expect\n\nasync def run(page):\n    await page.goto('/')\n",
            "test_case_1",
            headed=True,
            base_url="http://example.test",
        )
        headless_materialized = materialize_script(
            "from playwright.async_api import expect\n\nasync def run(page):\n    await page.goto('/')\n",
            "test_case_2",
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

    def test_materialized_script_captures_failure_screenshot_before_closing_browser(self):
        materialized = materialize_script(
            "async def run(page):\n    await page.click('#missing')\n",
            "test_failure",
            headed=False,
            base_url="http://example.test",
            failure_screenshot_path="/controlled/execution_1/single_case.png",
        )

        screenshot_index = materialized.index("page.screenshot")
        context_close_index = materialized.index("await context.close()")
        self.assertLess(screenshot_index, context_close_index)
        self.assertIn("full_page=False", materialized)
        self.assertIn("/controlled/execution_1/single_case.png", materialized)
        self.assertIn("raise", materialized)

    def test_screenshot_failure_preserves_original_script_exception(self):
        class FakePage:
            def __init__(self):
                self.screenshot_args = None

            async def screenshot(self, **kwargs):
                self.screenshot_args = kwargs
                raise RuntimeError('screenshot unavailable')

        class FakeContext:
            def __init__(self, page):
                self.page = page
                self.closed = False

            async def new_page(self):
                return self.page

            async def close(self):
                self.closed = True

        class FakeBrowser:
            def __init__(self, context):
                self.context = context
                self.closed = False

            async def new_context(self, **kwargs):
                return self.context

            async def close(self):
                self.closed = True

        page = FakePage()
        context = FakeContext(page)
        browser = FakeBrowser(context)

        class BrowserType:
            async def launch(self, **kwargs):
                return browser

        class Playwright:
            chromium = BrowserType()

        class PlaywrightContext:
            async def __aenter__(self):
                return Playwright()

            async def __aexit__(self, exc_type, exc, tb):
                return False

        fake_module = types.ModuleType('playwright.async_api')
        fake_module.async_playwright = lambda: PlaywrightContext()
        materialized = materialize_script(
            "async def run(page):\n    raise ValueError('original failure')\n",
            "test_preserve_original",
            failure_screenshot_path="/controlled/failure.png",
        )
        namespace = {}
        with patch.dict(sys.modules, {'playwright': types.ModuleType('playwright'), 'playwright.async_api': fake_module}):
            exec(materialized, namespace)
            with self.assertRaisesRegex(ValueError, 'original failure'):
                namespace['test_preserve_original']()

        self.assertEqual(page.screenshot_args, {'path': '/controlled/failure.png', 'full_page': False})
        self.assertTrue(context.closed)
        self.assertTrue(browser.closed)

    def test_runner_writes_real_base_url_and_rejects_empty_or_multiline_values(self):
        runner = PlaywrightRunner()
        work_dir = tempfile.mkdtemp(dir=runner.temp_base_dir)
        try:
            runner._create_pytest_config(
                work_dir, ExecutionConfig(base_url="https://web.example.test/root")
            )
            with open(os.path.join(work_dir, "pytest.ini"), encoding="utf-8") as file:
                self.assertIn("base_url = https://web.example.test/root", file.read())

            with self.assertRaisesRegex(ValueError, "基础 URL"):
                runner._create_pytest_config(work_dir, ExecutionConfig(base_url=""))
            with self.assertRaisesRegex(ValueError, "换行"):
                runner._create_pytest_config(work_dir, ExecutionConfig(base_url="https://bad.test\nbase_url = evil"))
        finally:
            shutil.rmtree(work_dir, ignore_errors=True)

    def test_suite_result_parser_keeps_failure_details_and_screenshots_per_case(self):
        runner = PlaywrightRunner()
        screenshot_dir = tempfile.mkdtemp(dir=runner.temp_base_dir)
        try:
            for case_id in (11, 12):
                with open(
                    os.path.join(screenshot_dir, f'case_{case_id}.png'), 'wb'
                ) as screenshot:
                    screenshot.write(b'PNG')

            stdout = '''
test_case_11.py::test_case_11 FAILED
test_case_12.py::test_case_12 FAILED

_______________________________ test_case_11 ________________________________
E   playwright._impl._errors.TimeoutError: Locator.click: Timeout 30000ms exceeded.
E   waiting for get_by_role("button", name="登录")
_______________________________ test_case_12 ________________________________
E   playwright._impl._errors.TimeoutError: Locator.fill: Timeout 5000ms exceeded.
E   waiting for get_by_label("用户名")
=========================== short test summary info ============================
FAILED test_case_11.py::test_case_11
FAILED test_case_12.py::test_case_12
'''
            results = runner._parse_suite_test_results(
                stdout,
                [
                    {'test_case_id': 11, 'test_case_title': '登录'},
                    {'test_case_id': 12, 'test_case_title': '输入用户'},
                ],
                ExecutionConfig(
                    base_url='https://web.example.test',
                    failure_screenshot_dir=screenshot_dir,
                ),
            )

            first, second = results
            self.assertIn('点击元素超时', first['error_message'])
            self.assertIn('按钮“登录”', first['error_message'])
            self.assertNotIn('用户名', first['log'])
            self.assertIn('输入元素超时', second['error_message'])
            self.assertIn('标签为“用户名”的输入项', second['error_message'])
            self.assertNotIn('按钮“登录”', second['log'])
            self.assertTrue(first['screenshot_path'].endswith('case_11.png'))
            self.assertTrue(second['screenshot_path'].endswith('case_12.png'))
        finally:
            shutil.rmtree(screenshot_dir, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
