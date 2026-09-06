"""Cross-module checks for the platform-native report dependency boundary."""
from pathlib import Path
import subprocess
import sys
import textwrap

from django.test import SimpleTestCase
from django.urls import Resolver404, resolve


class NativeReportContractTests(SimpleTestCase):
    def test_report_workspaces_are_not_public_static_routes(self):
        for path in ('/playwright-reports/private.py', '/httprunner-reports/private.yaml'):
            with self.subTest(path=path), self.assertRaises(Resolver404):
                resolve(path)

    def test_current_runtime_has_no_allure_dependencies(self):
        backend = Path(__file__).resolve().parents[2]
        requirements = (backend / 'requirements.txt').read_text(encoding='utf-8').lower()
        self.assertNotIn('allure-pytest', requirements)
        self.assertNotIn('allure-python-commons', requirements)
        script = textwrap.dedent('''
            import importlib.abc
            import socket
            import sys
            from pathlib import Path
            sys.path.insert(0, str(Path.cwd() / 'apps'))
            class NoAllure(importlib.abc.MetaPathFinder):
                def find_spec(self, fullname, path=None, target=None):
                    if fullname.startswith('allure'):
                        raise ModuleNotFoundError('Allure intentionally unavailable')
            sys.meta_path.insert(0, NoAllure())
            def blocked(*args, **kwargs):
                raise RuntimeError('External connections forbidden in dependency smoke test')
            socket.socket.connect = blocked
            socket.socket.connect_ex = blocked
            from api_testing.requests_runner import requests_runner
            from api_testing.case_contract import export_python
            from web_testing.playwright_python_runner import ExecutionConfig as WebConfig
            from web_testing.script_contract import materialize_script
            assert callable(requests_runner)
            api_source = export_python({'config': {'name': 'health'}, 'teststeps': [{'name': 'health', 'request': {'method': 'GET', 'url': 'https://example.test'}}]})
            compile(api_source, '<api-export>', 'exec')
            assert not hasattr(WebConfig(), 'generate_allure')
            source = materialize_script("async def run(page):\\n    await page.goto('http://127.0.0.1/')\\n    assert 1 == 1\\n", 'test_native_report')
            compile(source, '<generated>', 'exec')
            assert 'allure' not in source.lower()
            print('NATIVE_REPORT_IMPORTS_OK')
        ''')
        result = subprocess.run([sys.executable, '-c', script], cwd=backend, capture_output=True, text=True, timeout=45)
        self.assertEqual(result.returncode, 0, result.stderr[-3000:])
        self.assertIn('NATIVE_REPORT_IMPORTS_OK', result.stdout)
