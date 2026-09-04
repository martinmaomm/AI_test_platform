"""Verify progress survives pytest capture and stays scoped to its suite case."""
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from .playwright_python_runner import ExecutionConfig, PlaywrightRunner


class RunnerProgressOutputTests(unittest.TestCase):
    def run_fixture(self, scripts):
        runner = PlaywrightRunner()
        with tempfile.TemporaryDirectory(prefix='aits-progress-capture-') as directory:
            runner._create_pytest_config(directory)
            for case_id, source in scripts.items():
                Path(directory, f'test_case_{case_id}.py').write_text(source, encoding='utf-8')
            # Run real pytest on local Python functions, without browser,
            # plugin auto-discovery, Django database or external services.
            with patch.dict(os.environ, {
                'PYTEST_DISABLE_PLUGIN_AUTOLOAD': '1', 'PYTEST_ADDOPTS': '',
            }):
                result = runner._run_pytest_command(directory, ExecutionConfig(timeout=15))
            cases = runner._parse_suite_test_results(result.stdout, [
                {'test_case_id': case_id, 'test_case_title': f'本地用例 {case_id}'}
                for case_id in scripts
            ])
            return result, {item['test_case_id']: item for item in cases}

    def test_success_output_survives_single_case_capture(self):
        result, cases = self.run_fixture({1: '''def test_case_1():
    print("验证 列表显示结果 通过", flush=True)
    print("测试用例执行完毕", flush=True)
'''})
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('验证 列表显示结果 通过', result.stdout)
        self.assertIn('测试用例执行完毕', cases[1]['log'])
        self.assertEqual(cases[1]['status'], 'passed')

    def test_mixed_suite_output_never_crosses_case_boundaries(self):
        result, cases = self.run_fixture({
            1: '''def test_case_1():
    print("验证 第一例新增 通过", flush=True)
    raise AssertionError("第一例后续验证失败")
''',
            2: '''def test_case_2():
    print("验证 第二例查询 通过", flush=True)
    print("测试用例执行完毕", flush=True)
''',
            3: '''def test_case_3():
    print("验证 第三例删除 通过", flush=True)
    print("测试用例执行完毕", flush=True)
''',
        })
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertEqual([cases[i]['status'] for i in (1, 2, 3)], ['failed', 'passed', 'passed'])
        for case_id, label in ((1, '第一例'), (2, '第二例'), (3, '第三例')):
            log = cases[case_id]['log']
            self.assertIn(label, log)
            for other in {'第一例', '第二例', '第三例'} - {label}:
                self.assertNotIn(other, log)
        self.assertNotIn('测试用例执行完毕', cases[1]['log'])

    def test_missing_passed_capture_retains_status_without_fake_failure(self):
        runner = PlaywrightRunner()
        cases = runner._parse_suite_test_results(
            'test_case_8.py::test_case_8 PASSED [100%]\n',
            [{'test_case_id': 8}],
        )
        self.assertEqual(cases[0]['log'], 'test_case_8.py::test_case_8 PASSED [100%]')
        self.assertIsNone(cases[0]['error_message'])
