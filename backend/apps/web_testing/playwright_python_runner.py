"""
Playwright Python脚本运行器
使用pytest执行Playwright Python测试脚本
"""

import json
import os
import re
import sys
import tempfile
import subprocess
import logging
import shutil
from typing import Dict, Any, Optional, List
from dataclasses import dataclass, field

from .constants import (
    WEBUI_BROWSER_ENGINE,
    WEBUI_DEFAULT_HEADED,
    WEBUI_DEFAULT_TIMEOUT,
    normalize_webui_execution_options,
)
from .script_contract import materialize_script
from .assertion_state import analyze_assertion_state, evaluation_status, read_runtime_assertion_count
from .execution_diagnostics import diagnose_failure

logger = logging.getLogger(__name__)

PYTEST_TIMEOUT_BUFFER = 30


@dataclass
class ExecutionConfig:
    """执行配置类"""
    browser: str = field(default=WEBUI_BROWSER_ENGINE, init=False)
    headed: bool = WEBUI_DEFAULT_HEADED
    timeout: int = WEBUI_DEFAULT_TIMEOUT
    failure_screenshot_path: Optional[str] = None
    failure_screenshot_dir: Optional[str] = None
    runtime_assertion_count_path: Optional[str] = None
    environment_variables: Dict[str, str] = field(default_factory=dict)


@dataclass
class ExecutionResult:
    """执行结果类"""
    success: bool
    return_code: int
    stdout: str
    stderr: str
    work_dir: str
    config: ExecutionConfig
    test_summary: Optional[Dict[str, Any]] = None
    case_results: Optional[List[Dict[str, Any]]] = None
    runtime_assertion_count: int = 0


class PlaywrightRunner:
    """Playwright测试运行器"""
    
    def __init__(self):
        self.project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        self.temp_base_dir = os.path.join(self.project_root, "playwright_workspace")
        os.makedirs(self.temp_base_dir, exist_ok=True)
    
    def run_single_test(self, script_id: str, script_content: str, config: ExecutionConfig) -> ExecutionResult:
        """执行单个测试脚本"""
        work_dir = self._create_work_dir(f"playwright_python_{script_id}_")
        config.runtime_assertion_count_path = os.path.join(work_dir, 'runtime_assertions.json')
        
        try:
            self._create_test_file(work_dir, script_content, config)
            self._create_pytest_config(work_dir)
            result = self._run_pytest_command(work_dir, config)
            execution_result = self._build_execution_result(result, work_dir, config)
            self._cleanup_work_dir(work_dir)
            return execution_result
        except subprocess.TimeoutExpired:
            logger.error(f"测试执行超时: {script_id}")
            return self._build_error_result(f'测试执行超时（{config.timeout}秒）', work_dir, config)
        except Exception as e:
            logger.error(f"执行测试时发生错误: {e}", exc_info=True)
            return self._build_error_result(str(e), work_dir, config)
    
    def run_suite_test(self, suite_id: str, test_cases_data: List[Dict[str, Any]], config: ExecutionConfig) -> ExecutionResult:
        """执行测试套件"""
        work_dir = self._create_work_dir(f"playwright_suite_{suite_id}_")
        try:
            test_files, skipped_results = self._create_suite_test_files(
                work_dir, test_cases_data, config
            )

            # 统计临时目录下生成的 .py 文件数量，若为 0 则直接返回错误，避免盲目启动 Pytest
            py_files = [f for f in os.listdir(work_dir) if f.endswith('.py')]
            py_count = len(py_files)
            logger.info(f"[套件执行] 临时目录 {work_dir} 下生成 .py 文件数量: {py_count}, 文件列表: {py_files}")

            if py_count == 0:
                error_msg = "脚本生成失败，未发现可执行的测试文件"
                skipped_errors = [
                    item.get('error_message', '') for item in skipped_results
                    if item.get('error_message')
                ]
                if skipped_errors:
                    error_msg = f"{error_msg}：{skipped_errors[0]}"
                logger.error(f"[套件执行] {error_msg}，跳过 Pytest 启动")
                return self._build_error_result(error_msg, work_dir, config)

            self._create_pytest_config(work_dir)
            result = self._run_pytest_command(work_dir, config)
            parsed_case_results = self._parse_suite_test_results(result.stdout, test_cases_data, config)
            
            # 合并跳过的用例结果
            all_case_results = skipped_results + parsed_case_results
            
            execution_result = self._build_execution_result(result, work_dir, config, all_case_results)
            self._cleanup_work_dir(work_dir)
            return execution_result
        except subprocess.TimeoutExpired:
            logger.error(f"测试套件执行超时: {suite_id}")
            return self._build_error_result(f'测试套件执行超时（{config.timeout}秒）', work_dir, config)
        except Exception as e:
            logger.error(f"执行测试套件时发生错误: {e}", exc_info=True)
            return self._build_error_result(str(e), work_dir, config)
        finally:
            # 清理逻辑在正常流程中已处理
            pass
    
    def _create_work_dir(self, prefix: str) -> str:
        """创建临时工作目录"""
        return tempfile.mkdtemp(prefix=prefix, dir=self.temp_base_dir)
    
    def _create_test_file(self, work_dir: str, script_content: str, config: ExecutionConfig) -> str:
        """创建测试文件"""
        test_file = os.path.join(work_dir, "test_playwright.py")
        with open(test_file, 'w', encoding='utf-8') as f:
            f.write(materialize_script(
                script_content,
                "test_webui_case",
                headed=config.headed,
                failure_screenshot_path=config.failure_screenshot_path,
                runtime_assertion_count_path=config.runtime_assertion_count_path,
            ))
        return test_file
    
    def _create_suite_test_files(
        self,
        work_dir: str,
        test_cases_data: List[Dict[str, Any]],
        config: Optional[ExecutionConfig] = None,
    ) -> tuple:
        """创建套件测试文件，并输出详细日志便于排查"""
        config = config or ExecutionConfig()
        test_files = []
        skipped_results = []

        logger.info(f"[脚本生成] 开始处理 {len(test_cases_data)} 个测试用例，工作目录: {work_dir}")

        for idx, case_data in enumerate(test_cases_data):
            test_case_id = case_data.get('test_case_id')
            script_content = case_data.get('script_content', '').strip()
            test_case_title = case_data.get('test_case_title', f'Test Case {test_case_id}')

            if not script_content:
                logger.warning(
                    f"[脚本生成] 用例 #{idx + 1} (id={test_case_id}, title={test_case_title}): 转换失败，原因: 无脚本内容"
                )
                skipped_results.append({
                    'test_case_id': test_case_id,
                    'test_case_title': test_case_title,
                    'status': 'skipped',
                    'error_message': '测试用例没有脚本内容'
                })
                continue

            try:
                test_file = os.path.join(work_dir, f"test_case_{test_case_id}.py")
                with open(test_file, 'w', encoding='utf-8') as f:
                    f.write(materialize_script(
                        script_content,
                        f"test_case_{test_case_id}",
                        headed=config.headed,
                        failure_screenshot_path=(
                            os.path.join(config.failure_screenshot_dir, f'case_{test_case_id}.png')
                            if config.failure_screenshot_dir else None
                        ),
                    ))
                test_files.append(test_file)
                logger.info(
                    f"[脚本生成] 用例 #{idx + 1} (id={test_case_id}, title={test_case_title}): 成功写入 {os.path.basename(test_file)}"
                )
            except Exception as e:
                if os.path.exists(test_file):
                    os.remove(test_file)
                logger.error(
                    f"[脚本生成] 用例 #{idx + 1} (id={test_case_id}, title={test_case_title}): 写入失败, 异常: {e}",
                    exc_info=True
                )
                skipped_results.append({
                    'test_case_id': test_case_id,
                    'test_case_title': test_case_title,
                    'status': 'skipped',
                    'error_message': f'脚本写入失败: {str(e)}'
                })

        logger.info(f"[脚本生成] 完成: 成功 {len(test_files)} 个, 跳过 {len(skipped_results)} 个")
        return test_files, skipped_results
    
    def _create_pytest_config(self, work_dir: str) -> None:
        """创建pytest配置文件"""
        pytest_ini = os.path.join(work_dir, "pytest.ini")
        config_content = """[pytest]
testpaths = .
python_files = test_*.py
python_classes = Test*
python_functions = test_*
"""
        with open(pytest_ini, 'w', encoding='utf-8') as f:
            f.write(config_content)

    def _resolve_python_browser_path(self) -> Optional[str]:
        """解析 Python Playwright 独立缓存目录；未配置时使用其系统默认缓存。"""
        browser_path = os.getenv('PYTHON_PLAYWRIGHT_BROWSERS_PATH', '').strip()
        if not browser_path:
            return None
        browser_path = os.path.expanduser(browser_path)
        if not os.path.isabs(browser_path):
            browser_path = os.path.join(self.project_root, browser_path)
        return os.path.abspath(browser_path)

    @staticmethod
    def _safe_environment_variables(values: Dict[str, Any] | None) -> Dict[str, str]:
        """Allow user test variables without allowing process/runtime overrides."""
        protected_prefixes = ('PYTHON', 'PLAYWRIGHT_', 'PYTEST_', 'DJANGO_', 'CELERY_', 'LD_')
        protected_names = {
            'PATH', 'HOME', 'SHELL', 'VIRTUAL_ENV', 'PWD', 'TMPDIR',
            'HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'NO_PROXY',
            'SSL_CERT_FILE', 'SSL_CERT_DIR', 'REQUESTS_CA_BUNDLE',
            'CURL_CA_BUNDLE', 'NODE_OPTIONS', 'WEBUI_RUNTIME_VARIABLES',
        }
        safe: Dict[str, str] = {}
        for key, value in (values or {}).items():
            name = str(key)
            if (
                not re.fullmatch(r'[A-Z_][A-Z0-9_]{0,127}', name)
                or name in protected_names
                or name.startswith(protected_prefixes)
            ):
                continue
            safe[name] = str(value)
        return safe

    def _run_pytest_command(self, work_dir: str, config: ExecutionConfig) -> subprocess.CompletedProcess:
        """执行pytest命令"""
        # Keep captured output for passing tests too. Disabling capture would
        # interleave progress messages with the per-case status lines.
        cmd = [sys.executable, "-m", "pytest", "-v", "--tb=short", "-rA"]
        
        env = os.environ.copy()
        env.pop('PLAYWRIGHT_BASE_URL', None)
        env.update({
            'PLAYWRIGHT_BROWSER': config.browser,
            'PLAYWRIGHT_HEADED': str(config.headed).lower(),
            'PYTEST_TIMEOUT': str(config.timeout),
            'PYTHONIOENCODING': 'utf-8',  # 防止 Windows GBK 编码报错
        })
        # Values intentionally never appear in command arguments or logs.  The
        # JSON copy is consumed only by the managed run(page, variables)
        # wrapper; individual variables remain available to manually-authored
        # run(page) scripts that read os.environ.
        runtime_variables = self._safe_environment_variables(config.environment_variables)
        env.update(runtime_variables)
        env['WEBUI_RUNTIME_VARIABLES'] = json.dumps(
            runtime_variables,
            ensure_ascii=False,
            separators=(',', ':'),
        )

        python_browser_path = self._resolve_python_browser_path()
        if python_browser_path:
            env['PLAYWRIGHT_BROWSERS_PATH'] = python_browser_path
            logger.info("Python Playwright浏览器缓存目录: %s", python_browser_path)
        else:
            # Celery 可能为 Node MCP 配置了旧的通用变量；Python 子进程必须移除它，
            # 否则相对路径会按临时 pytest 工作目录解析，并与 MCP 浏览器版本冲突。
            env.pop('PLAYWRIGHT_BROWSERS_PATH', None)
            logger.info("Python Playwright使用系统默认浏览器缓存目录")

        result = subprocess.run(
            cmd,
            cwd=work_dir,
            env=env,
            capture_output=True,
            text=True,
            encoding='utf-8',
            timeout=config.timeout + PYTEST_TIMEOUT_BUFFER
        )
        
        if result.stderr:
            logger.warning(f"pytest stderr: {result.stderr}")
        
        return result

    def _build_execution_result(self, result: subprocess.CompletedProcess, work_dir: str,
                               config: ExecutionConfig,
                               case_results: Optional[List[Dict[str, Any]]] = None) -> ExecutionResult:
        """构建执行结果"""
        test_summary = self._extract_test_summary(result.stdout) if result.stdout else None
        runtime_assertion_count = read_runtime_assertion_count(config.runtime_assertion_count_path)
        
        return ExecutionResult(
            success=result.returncode == 0,
            return_code=result.returncode,
            stdout=result.stdout,
            stderr=result.stderr,
            work_dir=work_dir,
            config=config,
            test_summary=test_summary,
            case_results=case_results,
            runtime_assertion_count=runtime_assertion_count,
        )
    
    def _build_error_result(self, error_msg: str, work_dir: str, config: ExecutionConfig) -> ExecutionResult:
        """构建错误结果"""
        return ExecutionResult(
            success=False,
            return_code=-1,
            stdout='',
            stderr=error_msg,
            work_dir=work_dir,
            config=config
        )
    
    def _cleanup_work_dir(self, work_dir: str) -> None:
        """清理工作目录"""
        try:
            shutil.rmtree(work_dir)
        except Exception as e:
            logger.warning(f"清理临时目录失败: {e}")

    def _extract_test_summary(self, stdout: str) -> Dict[str, Any]:
        """从pytest输出中提取测试统计信息"""
        summary = {'total': 0, 'passed': 0, 'failed': 0, 'skipped': 0, 'errors': 0}
        
        try:
            for line in stdout.split('\n'):
                if 'passed' in line.lower() and ('failed' in line.lower() or 'skipped' in line.lower()):
                    parts = line.split()
                    for part in parts:
                        if part.isdigit():
                            line_lower = line.lower()
                            if 'passed' in line_lower and part in line:
                                summary['passed'] = int(part)
                            elif 'failed' in line_lower and part in line:
                                summary['failed'] = int(part)
                            elif 'skipped' in line_lower and part in line:
                                summary['skipped'] = int(part)
                            elif 'error' in line_lower and part in line:
                                summary['errors'] = int(part)
                    
                    summary['total'] = summary['passed'] + summary['failed'] + summary['skipped'] + summary['errors']
                    break
        except Exception as e:
            logger.warning(f"提取测试统计信息失败: {e}")
        
        return summary

    def _parse_suite_test_results(
        self,
        stdout: str,
        test_cases_data: List[Dict[str, Any]],
        config: Optional[ExecutionConfig] = None,
    ) -> List[Dict[str, Any]]:
        """解析测试套件执行结果"""
        case_results = []
        parsed_case_ids = set()

        if not stdout:
            stdout = ""
            logger.warning("Pytest stdout 为空，可能是执行崩溃或被异常中断")

        status_entries = []
        status_pattern = re.compile(
            r'^(test_case_(\d+)\.py)::[^\s]+\s+(PASSED|FAILED|SKIPPED|ERROR)\b',
            re.I,
        )
        for raw_line in stdout.splitlines():
            line = raw_line.strip()
            match = status_pattern.match(line)
            if match:
                status_entries.append((line, match.group(2), match.group(3).upper()))

        failed_case_count = sum(
            1 for _, _, status in status_entries if status in {'FAILED', 'ERROR'}
        )

        for line, test_case_id_str, status_part in status_entries:
            try:
                if not test_case_id_str or test_case_id_str in parsed_case_ids:
                    continue
                
                parsed_case_ids.add(test_case_id_str)
                
                case_data = next((c for c in test_cases_data if str(c.get('test_case_id')) == test_case_id_str), None)
                if not case_data:
                    continue
                
                # 确定状态
                if 'PASSED' in status_part:
                    status, error_message = 'passed', None
                elif 'FAILED' in status_part:
                    status = 'failed'
                    case_output = self._extract_suite_case_failure(
                        stdout, test_case_id_str, allow_full_output=failed_case_count == 1
                    )
                    diagnostic = diagnose_failure(case_output)
                    error_message = diagnostic.summary
                elif 'SKIPPED' in status_part:
                    status, error_message = 'skipped', '测试用例被跳过'
                elif 'ERROR' in status_part:
                    status = 'error'
                    case_output = self._extract_suite_case_failure(
                        stdout, test_case_id_str, allow_full_output=failed_case_count == 1
                    )
                    diagnostic = diagnose_failure(case_output)
                    error_message = diagnostic.summary
                else:
                    continue

                if status in {'failed', 'error'}:
                    case_log = case_output
                else:
                    captured = self._extract_suite_case_output(stdout, test_case_id_str)
                    case_log = '\n'.join(part for part in (line, captured) if part)
                screenshot_file = (
                    os.path.join(config.failure_screenshot_dir, f'case_{test_case_id_str}.png')
                    if config and config.failure_screenshot_dir else None
                )
                
                case_results.append({
                    'test_case_id': int(test_case_id_str),
                    'test_case_title': case_data.get('test_case_title', f'Test Case {test_case_id_str}'),
                    'status': status,
                    'error_message': error_message,
                    'log': case_log,
                    'stdout': case_log,
                    'screenshot_path': (
                        os.path.abspath(screenshot_file)
                        if screenshot_file and os.path.exists(screenshot_file)
                        else None
                    ),
                })
            except (IndexError, ValueError) as e:
                logger.debug(f"解析测试用例结果失败: {line}, 错误: {e}")
                continue
        
        return case_results

    @staticmethod
    def _extract_suite_case_output(stdout: str, test_case_id: str) -> str:
        """提取该用例的捕获输出，成功和失败均不得混入其他用例日志。"""
        escaped_id = re.escape(str(test_case_id))
        case_header = re.compile(
            rf'^_+\s+.*\btest_case_{escaped_id}\b.*\s+_+\s*$',
            re.I | re.M,
        )
        match = case_header.search(stdout or '')
        if not match:
            return ''
        remainder = stdout[match.start():]
        header_end = match.end() - match.start()
        # PASSES/FAILURES/warnings/summary headings are boundaries too. In a
        # mixed suite, the final failure is followed by the passing reports.
        next_section = re.search(
            r'(?m)^(?:_+\s+.*\s+_+|=+\s+.*\s+=+)\s*$',
            remainder[header_end:],
        )
        end = header_end + next_section.start() if next_section else len(remainder)
        return remainder[:end].strip()

    @staticmethod
    def _extract_suite_case_failure(
        stdout: str,
        test_case_id: str,
        *,
        allow_full_output: bool = False,
    ) -> str:
        """提取一个 Pytest 子用例的失败段落，避免把其他用例错误错误归属。"""
        captured = PlaywrightRunner._extract_suite_case_output(stdout, test_case_id)
        if captured:
            return captured

        escaped_id = re.escape(str(test_case_id))
        summary_match = re.search(
            rf'(?m)^(?:FAILED|ERROR)\s+test_case_{escaped_id}\.py::[^\n]+$',
            stdout or '',
            re.I,
        )
        if summary_match:
            return summary_match.group(0).strip()

        if allow_full_output:
            return stdout or ''
        return f'test_case_{test_case_id}.py 执行失败，未提取到独立错误段落'


# 全局运行器实例
_runner = PlaywrightRunner()


def extract_execution_error(stdout: str = '', stderr: str = '') -> str:
    """从 Pytest 输出中提取适合页面展示的首个真实错误。"""

    lines = (stdout or '').splitlines()
    preferred = []
    fallback = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith('E   '):
            message = stripped[4:].strip()
            if message and not re.fullmatch(r'[╔╗╚╝═║─-╿\s]+', message):
                fallback.append(message)
                if re.search(r'(Error|Exception|AssertionError|TimeoutError):', message):
                    preferred.append(message)

    if preferred:
        return preferred[0][:1000]
    if fallback:
        return fallback[0][:1000]

    stderr_text = (stderr or '').strip()
    if stderr_text:
        return stderr_text.splitlines()[-1][:1000]

    for line in reversed(lines):
        stripped = line.strip()
        if stripped.startswith('FAILED '):
            return stripped[:1000]
    return '测试执行失败，未获取到具体错误'


def playwright_runner(
    script_id: str,
    script_content: str,
    options: Dict[str, Any] = None,
    failure_screenshot_path: Optional[str] = None,
    environment_variables: Dict[str, str] | None = None,
) -> Dict[str, Any]:
    """使用pytest执行Playwright Python测试脚本"""
    normalized_options = normalize_webui_execution_options(options)
    config = ExecutionConfig(
        headed=normalized_options['headed'],
        timeout=normalized_options['timeout'],
        failure_screenshot_path=failure_screenshot_path,
        environment_variables=environment_variables or {},
    )
    
    result = _runner.run_single_test(script_id, script_content, config)
    
    operation_success = bool(result.success)
    evaluation, assertion_state, runtime_assertion_count = evaluation_status(
        script_content,
        operation_success=operation_success,
        runtime_assertion_count=result.runtime_assertion_count,
    )
    error = None if operation_success else diagnose_failure(result.stdout, result.stderr).summary
    return {
        # Keep raw browser/process success separate from verification status.
        'success': operation_success,
        'operation_success': operation_success,
        'evaluation_status': evaluation,
        'assertion_state': assertion_state,
        'runtime_assertion_count': runtime_assertion_count,
        'error': error,
        'return_code': result.return_code,
        'stdout': result.stdout,
        'stderr': result.stderr,
        'test_file': result.work_dir,
        'work_dir': result.work_dir,
        'browser': result.config.browser,
        'headed': result.config.headed,
        'timeout': result.config.timeout,
        'status': evaluation,
        'test_summary': result.test_summary,
        'screenshot_path': (
            result.config.failure_screenshot_path
            if result.config.failure_screenshot_path and os.path.exists(result.config.failure_screenshot_path)
            else None
        )
    }


def playwright_suite_runner(suite_id: str, test_cases_data: List[Dict[str, Any]], options: Dict[str, Any] = None, environment_variables: Dict[str, str] | None = None) -> Dict[str, Any]:
    """使用 pytest 批量执行多个 Playwright Python 测试脚本。"""
    normalized_options = normalize_webui_execution_options(options)
    config = ExecutionConfig(
        headed=normalized_options['headed'],
        timeout=normalized_options['timeout'],
        failure_screenshot_dir=(options or {}).get('failure_screenshot_dir'),
        environment_variables=environment_variables or {},
    )
    
    result = _runner.run_suite_test(suite_id, test_cases_data, config)

    # 失败时提供明确的 error 字段，便于上层展示
    error_msg = (
        diagnose_failure(result.stdout, result.stderr).summary
        if not result.success else None
    )

    return {
        'success': result.success,
        'operation_success': bool(result.success),
        'stdout': result.stdout,
        'stderr': result.stderr,
        'error': error_msg,
        'return_code': result.return_code,
        'test_files': [result.work_dir],
        'case_results': result.case_results,
        'execution_info': {
            'browser': result.config.browser,
            'headed': result.config.headed,
            'timeout': result.config.timeout,
            'work_dir': result.work_dir
        }
    }
