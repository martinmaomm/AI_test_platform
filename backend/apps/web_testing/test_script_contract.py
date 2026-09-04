import ast
import json
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
        script = "from playwright.async_api import expect\n\nasync def run(page):\n    await page.goto('https://web.example.test/')\n    await expect(page).to_have_title('Home')\n"

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

    def test_run_signature_allows_only_page_or_v3_variables(self):
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

    def test_v3_variable_aware_run_is_storable_and_receives_runtime_mapping(self):
        script = "async def run(page, variables):\n    await page.goto('https://web.example.test/')\n"
        self.assertEqual(normalize_for_storage(script), script.strip())
        materialized = materialize_script(script, 'v3_variables')
        self.assertIn('await run(page, runtime_variables)', materialized)
        self.assertIn('WEBUI_RUNTIME_VARIABLES', materialized)

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
    await page.goto('https://web.example.test/')
"""

        materialized = materialize_script(script, "test_mixed", headed=True)

        self.assertIn("browser_type.launch(headless=False)", materialized)
        self.assertIn("context = await browser.new_context()", materialized)
        self.assertNotIn("PLAYWRIGHT_BASE_URL", materialized)
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

    def test_storage_validation_rejects_invalid_static_goto_urls(self):
        for target, use_keyword in (
            ('/login', False),
            ('relative/path', True),
            ('http://', False),
            ('https://web.example.test:99999/', False),
            ('https://web.example.test/a b', False),
            ('http://user:pass@web.example.test/', False),
        ):
            argument = f"url={target!r}" if use_keyword else repr(target)
            script = f"async def run(page):\n    await page.goto({argument})\n"
            with self.subTest(target=target, use_keyword=use_keyword):
                with self.assertRaisesRegex(ScriptContractError, "完整的 http\\(s\\) 地址"):
                    normalize_for_storage(script)

    def test_storage_validation_allows_dynamic_goto_url_for_manual_variables(self):
        script = "async def run(page, variables):\n    await page.goto(variables['TARGET_URL'])\n"

        self.assertEqual(normalize_for_storage(script), script.strip())

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
            script = "async def run(page):\n    await page.goto('https://web.example.test/')\n"
            headed_file = runner._create_test_file(
                headed_dir, script, ExecutionConfig(headed=True)
            )
            headless_file = runner._create_test_file(
                headless_dir, script, ExecutionConfig(headed=False)
            )

            with open(headed_file, encoding="utf-8") as file:
                headed_content = file.read()
            with open(headless_file, encoding="utf-8") as file:
                headless_content = file.read()
            self.assertIn("headless=False", headed_content)
            self.assertIn("headless=True", headless_content)
            self.assertIn("https://web.example.test/", headed_content)
            self.assertIn("https://web.example.test/", headless_content)
            self.assertNotIn("PLAYWRIGHT_BASE_URL", headed_content)
            self.assertNotIn("PLAYWRIGHT_BASE_URL", headless_content)
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

    def test_runner_passes_safe_variables_to_v3_managed_wrapper(self):
        runner = PlaywrightRunner()
        work_dir = tempfile.mkdtemp(dir=runner.temp_base_dir)
        completed = SimpleNamespace(returncode=0, stdout="", stderr="")
        try:
            with patch.dict(os.environ, {'PLAYWRIGHT_BASE_URL': 'https://leaked.example.test'}, clear=True), patch(
                "web_testing.playwright_python_runner.subprocess.run",
                return_value=completed,
            ) as run:
                runner._run_pytest_command(
                    work_dir,
                    ExecutionConfig(environment_variables={
                        'UI_TEST_USERNAME': 'tester',
                        'USER_NAME': 'generated-override',
                        'PLAYWRIGHT_BASE_URL': 'must-not-override',
                        'WEBUI_RUNTIME_VARIABLES': 'must-not-override',
                    }),
                )

            child_env = run.call_args.kwargs['env']
            self.assertEqual(json.loads(child_env['WEBUI_RUNTIME_VARIABLES']), {
                'UI_TEST_USERNAME': 'tester',
                'USER_NAME': 'generated-override',
            })
            self.assertNotIn('PLAYWRIGHT_BASE_URL', child_env)
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

    def test_materialized_script_owns_browser_without_context_base_url(self):
        headed_materialized = materialize_script(
            "from playwright.async_api import expect\n\nasync def run(page):\n    await page.goto('https://web.example.test/')\n",
            "test_case_1",
            headed=True,
        )
        headless_materialized = materialize_script(
            "from playwright.async_api import expect\n\nasync def run(page):\n    await page.goto('https://web.example.test/')\n",
            "test_case_2",
            headed=False,
        )

        tree = ast.parse(headed_materialized)
        self.assertTrue(any(isinstance(node, ast.AsyncFunctionDef) and node.name == "run" for node in tree.body))
        self.assertIn("browser_type.launch(headless=False)", headed_materialized)
        self.assertIn("browser_type.launch(headless=True)", headless_materialized)
        self.assertIn("context = await browser.new_context()", headed_materialized)
        self.assertNotIn("PLAYWRIGHT_BASE_URL", headed_materialized)
        self.assertIn("asyncio.run(_run_with_managed_browser())", headed_materialized)
        self.assertNotIn("--base-url", headed_materialized)

    def test_materialized_script_captures_ending_screenshot_before_closing_browser(self):
        materialized = materialize_script(
            "async def run(page):\n    await page.click('#missing')\n",
            "test_failure",
            headed=False,
            failure_screenshot_path="/controlled/execution_1/single_case.png",
        )

        screenshot_index = materialized.index("page.screenshot")
        context_close_index = materialized.index("await context.close()")
        self.assertLess(screenshot_index, context_close_index)
        self.assertIn("full_page=True, timeout=5000", materialized)
        self.assertIn("/controlled/execution_1/single_case.png", materialized)

    def test_materialized_script_captures_one_ending_screenshot_for_success_and_failure(self):
        class FakePage:
            def __init__(self, events):
                self.events = events

            async def screenshot(self, **kwargs):
                self.events.append(('screenshot', kwargs))

        class FakeContext:
            def __init__(self, page, events):
                self.page = page
                self.events = events

            async def new_page(self):
                return self.page

            async def close(self):
                self.events.append(('context.close', None))

        class FakeBrowser:
            def __init__(self, context, events):
                self.context = context
                self.events = events

            async def new_context(self, **_kwargs):
                return self.context

            async def close(self):
                self.events.append(('browser.close', None))

        def fake_modules(events):
            page = FakePage(events)
            context = FakeContext(page, events)
            browser = FakeBrowser(context, events)

            class BrowserType:
                async def launch(self, **_kwargs):
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
            return {
                'playwright': types.ModuleType('playwright'),
                'playwright.async_api': fake_module,
            }

        for source, expected_error in (
            ("async def run(page):\n    return None\n", None),
            ("async def run(page):\n    raise ValueError('business failure')\n", 'business failure'),
        ):
            with self.subTest(expected_error=expected_error):
                events = []
                namespace = {}
                materialized = materialize_script(
                    source,
                    'test_ending_screenshot',
                    failure_screenshot_path='/controlled/ending.png',
                )
                with patch.dict(sys.modules, fake_modules(events)):
                    exec(materialized, namespace)
                    if expected_error:
                        with self.assertRaisesRegex(ValueError, expected_error):
                            namespace['test_ending_screenshot']()
                    else:
                        namespace['test_ending_screenshot']()

                self.assertEqual(events[0], (
                    'screenshot',
                    {'path': '/controlled/ending.png', 'full_page': True, 'timeout': 5000},
                ))
                self.assertEqual([event for event, _ in events].count('screenshot'), 1)
                self.assertEqual([event for event, _ in events][1:], ['context.close', 'browser.close'])

    def test_screenshot_failure_preserves_success_and_original_script_exception(self):
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

        for source, expected_error in (
            ("async def run(page):\n    return None\n", None),
            ("async def run(page):\n    raise ValueError('original failure')\n", 'original failure'),
        ):
            with self.subTest(expected_error=expected_error):
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
                    source,
                    "test_preserve_original",
                    failure_screenshot_path="/controlled/failure.png",
                )
                namespace = {}
                with patch.dict(sys.modules, {'playwright': types.ModuleType('playwright'), 'playwright.async_api': fake_module}):
                    exec(materialized, namespace)
                    if expected_error:
                        with self.assertRaisesRegex(ValueError, expected_error):
                            namespace['test_preserve_original']()
                    else:
                        namespace['test_preserve_original']()

                self.assertEqual(
                    page.screenshot_args,
                    {'path': '/controlled/failure.png', 'full_page': True, 'timeout': 5000},
                )
                self.assertTrue(context.closed)
                self.assertTrue(browser.closed)

    def test_runner_writes_pytest_config_without_base_url(self):
        runner = PlaywrightRunner()
        work_dir = tempfile.mkdtemp(dir=runner.temp_base_dir)
        try:
            runner._create_pytest_config(work_dir)
            with open(os.path.join(work_dir, "pytest.ini"), encoding="utf-8") as file:
                self.assertNotIn("base_url", file.read())
        finally:
            shutil.rmtree(work_dir, ignore_errors=True)

    def test_suite_result_parser_keeps_ending_screenshots_for_passed_and_failed_cases(self):
        runner = PlaywrightRunner()
        screenshot_dir = tempfile.mkdtemp(dir=runner.temp_base_dir)
        try:
            for case_id in (11, 12):
                with open(
                    os.path.join(screenshot_dir, f'case_{case_id}.png'), 'wb'
                ) as screenshot:
                    screenshot.write(b'PNG')

            stdout = '''
test_case_11.py::test_case_11 PASSED
test_case_12.py::test_case_12 FAILED

_______________________________ test_case_12 ________________________________
E   playwright._impl._errors.TimeoutError: Locator.fill: Timeout 5000ms exceeded.
E   waiting for get_by_label("用户名")
=========================== short test summary info ============================
FAILED test_case_12.py::test_case_12
'''
            results = runner._parse_suite_test_results(
                stdout,
                [
                    {'test_case_id': 11, 'test_case_title': '登录'},
                    {'test_case_id': 12, 'test_case_title': '输入用户'},
                ],
                ExecutionConfig(failure_screenshot_dir=screenshot_dir),
            )

            first, second = results
            self.assertEqual(first['status'], 'passed')
            self.assertIsNone(first['error_message'])
            self.assertIn('PASSED', first['log'])
            self.assertIn('输入元素超时', second['error_message'])
            self.assertIn('标签为“用户名”的输入项', second['error_message'])
            self.assertTrue(first['screenshot_path'].endswith('case_11.png'))
            self.assertTrue(second['screenshot_path'].endswith('case_12.png'))
        finally:
            shutil.rmtree(screenshot_dir, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
