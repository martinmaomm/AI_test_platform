import inspect
import json
import os
import shutil
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from .playwright_python_runner import (
    ExecutionConfig,
    PlaywrightRunner,
    playwright_runner,
    playwright_suite_runner,
)


class PlaywrightRunnerContractTests(unittest.TestCase):
    def test_public_results_do_not_advertise_deleted_workspaces(self):
        script = "async def run(page):\n    await page.goto('https://web.example.test/')\n"
        for suite in (False, True):
            for return_code in (0, 1):
                with self.subTest(suite=suite, return_code=return_code), tempfile.TemporaryDirectory() as base:
                    runner = PlaywrightRunner()
                    runner.temp_base_dir = base
                    completed = SimpleNamespace(
                        returncode=return_code,
                        stdout='offline execution output',
                        stderr='offline diagnostic',
                    )
                    with patch('web_testing.playwright_python_runner._runner', runner), patch.object(
                        runner, '_run_pytest_command', return_value=completed,
                    ):
                        if suite:
                            result = playwright_suite_runner('fixture', [
                                {'test_case_id': 1, 'script_content': script},
                            ])
                        else:
                            result = playwright_runner('fixture', script)

                    self.assertEqual(os.listdir(base), [])
                    self.assertEqual(result['stdout'], completed.stdout)
                    self.assertEqual(result['stderr'], completed.stderr)
                    self.assertEqual(result['operation_success'], return_code == 0)
                    for name in ('test_file', 'test_files', 'work_dir'):
                        self.assertNotIn(name, result)
                        self.assertNotIn(name, result.get('execution_info', {}))

    def test_task_payload_preserves_output_without_promoting_a_temporary_path(self):
        from .tasks import _run_test_script

        with patch('web_testing.playwright_python_runner.playwright_runner', return_value={
            'success': True, 'operation_success': True,
            'stdout': 'offline stdout', 'stderr': 'offline stderr',
            'test_file': '/ephemeral/runner-workspace',
            'screenshot_path': '/controlled/screenshot.png',
            'runtime_assertion_count': 1,
        }):
            result = _run_test_script('async def run(page):\n    assert True\n', {})

        payload = result['result']
        self.assertNotIn('test_file', payload)
        self.assertEqual(payload['stdout'], 'offline stdout')
        self.assertEqual(payload['stderr'], 'offline stderr')
        self.assertEqual(payload['screenshot_path'], '/controlled/screenshot.png')

    def test_public_runner_contracts_and_execution_config_have_no_base_url(self):
        self.assertNotIn('base_url', ExecutionConfig.__dataclass_fields__)
        self.assertNotIn('generate_allure', ExecutionConfig.__dataclass_fields__)
        self.assertNotIn('suite_name', ExecutionConfig.__dataclass_fields__)
        self.assertNotIn('base_url', inspect.signature(playwright_runner).parameters)
        self.assertNotIn('base_url', inspect.signature(playwright_suite_runner).parameters)

    def test_pytest_child_drops_base_url_and_preserves_safe_runtime_variables(self):
        runner = PlaywrightRunner()
        work_dir = tempfile.mkdtemp(dir=runner.temp_base_dir)
        completed = SimpleNamespace(returncode=0, stdout='', stderr='')
        try:
            with patch.dict(os.environ, {'PLAYWRIGHT_BASE_URL': 'https://leaked.example.test'}, clear=True), patch(
                'web_testing.playwright_python_runner.subprocess.run', return_value=completed
            ) as run:
                runner._run_pytest_command(
                    work_dir,
                    ExecutionConfig(environment_variables={'UI_TEST_USERNAME': 'tester'}),
                )

            child_env = run.call_args.kwargs['env']
            self.assertNotIn('PLAYWRIGHT_BASE_URL', child_env)
            self.assertEqual(json.loads(child_env['WEBUI_RUNTIME_VARIABLES']), {'UI_TEST_USERNAME': 'tester'})
            self.assertEqual(run.call_args.args[0][0], sys.executable)
            self.assertNotIn('--alluredir', run.call_args.args[0])
        finally:
            shutil.rmtree(work_dir, ignore_errors=True)

    def test_generated_single_and_suite_scripts_use_independent_contexts(self):
        runner = PlaywrightRunner()
        work_dir = tempfile.mkdtemp(dir=runner.temp_base_dir)
        script = "async def run(page):\n    await page.goto('https://web.example.test/')\n"
        try:
            single_file = runner._create_test_file(work_dir, script, ExecutionConfig())
            suite_files, skipped = runner._create_suite_test_files(
                work_dir,
                [{'test_case_id': 1, 'script_content': script}],
                config=ExecutionConfig(),
            )

            self.assertEqual(skipped, [])
            self.assertEqual(len(suite_files), 1)
            for path in (single_file, *suite_files):
                with open(path, encoding='utf-8') as generated:
                    content = generated.read()
                self.assertIn('context = await browser.new_context()', content)
                self.assertNotIn('base_url', content)
                self.assertNotIn('PLAYWRIGHT_BASE_URL', content)
                self.assertNotIn('allure', content.lower())
        finally:
            shutil.rmtree(work_dir, ignore_errors=True)

    def test_suite_relative_url_is_rejected_with_an_actionable_script_error(self):
        runner = PlaywrightRunner()
        result = runner.run_suite_test(
            'relative-url',
            [{'test_case_id': 1, 'script_content': "async def run(page):\n    await page.goto('/login')\n"}],
            ExecutionConfig(),
        )
        try:
            self.assertFalse(result.success)
            self.assertIn('完整的 http(s) 地址', result.stderr)
        finally:
            shutil.rmtree(result.work_dir, ignore_errors=True)


if __name__ == '__main__':
    unittest.main()
