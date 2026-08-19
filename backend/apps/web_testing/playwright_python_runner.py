"""
Playwright Python脚本运行器
使用pytest执行Playwright Python测试脚本
"""

import os
import tempfile
import subprocess
import logging
import shutil
from typing import Dict, Any, Optional, List
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# 常量定义
ALLURE_VERSION = "2.23.0"
ALLURE_TIMEOUT = 60
ALLURE_CHECK_TIMEOUT = 10
PYTEST_TIMEOUT_BUFFER = 30


@dataclass
class ExecutionConfig:
    """执行配置类"""
    browser: str = 'chromium'
    headed: bool = True
    timeout: int = 300
    generate_allure: bool = False
    base_url: Optional[str] = None
    suite_name: Optional[str] = None  # 测试套件名称，用于Allure报告


@dataclass
class ExecutionResult:
    """执行结果类"""
    success: bool
    return_code: int
    stdout: str
    stderr: str
    work_dir: str
    allure_report: Optional[str]
    config: ExecutionConfig
    test_summary: Optional[Dict[str, Any]] = None
    case_results: Optional[List[Dict[str, Any]]] = None


class PlaywrightRunner:
    """Playwright测试运行器"""
    
    def __init__(self):
        self.project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        self.temp_base_dir = os.path.join(self.project_root, "playwright_workspace")
        os.makedirs(self.temp_base_dir, exist_ok=True)
    
    def run_single_test(self, script_id: str, script_content: str, config: ExecutionConfig) -> ExecutionResult:
        """执行单个测试脚本"""
        work_dir = self._create_work_dir(f"playwright_python_{script_id}_")
        
        try:
            self._create_test_file(work_dir, script_content)
            self._create_pytest_config(work_dir)
            result = self._run_pytest_command(work_dir, config)
            allure_report_path = self._generate_report(work_dir, config) if config.generate_allure else None
            execution_result = self._build_execution_result(result, work_dir, allure_report_path, config)
            # 清理工作目录（保留报告）
            self._cleanup_work_dir(work_dir, execution_result.allure_report)
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
            test_files, skipped_results = self._create_suite_test_files(work_dir, test_cases_data, config.suite_name)

            # 统计临时目录下生成的 .py 文件数量，若为 0 则直接返回错误，避免盲目启动 Pytest
            py_files = [f for f in os.listdir(work_dir) if f.endswith('.py')]
            py_count = len(py_files)
            logger.info(f"[套件执行] 临时目录 {work_dir} 下生成 .py 文件数量: {py_count}, 文件列表: {py_files}")

            if py_count == 0:
                error_msg = "脚本生成失败，未发现可执行的测试文件"
                logger.error(f"[套件执行] {error_msg}，跳过 Pytest 启动")
                return self._build_error_result(error_msg, work_dir, config)

            self._create_pytest_config(work_dir)
            result = self._run_pytest_command(work_dir, config)
            allure_report_path = self._generate_report(work_dir, config) if config.generate_allure else None
            parsed_case_results = self._parse_suite_test_results(result.stdout, test_cases_data)
            
            # 合并跳过的用例结果
            all_case_results = skipped_results + parsed_case_results
            
            execution_result = self._build_execution_result(result, work_dir, allure_report_path, config, all_case_results)
            # 清理工作目录（保留报告）
            self._cleanup_work_dir(work_dir, execution_result.allure_report)
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
    
    def _create_test_file(self, work_dir: str, script_content: str) -> str:
        """创建测试文件"""
        if not script_content or not script_content.strip():
            raise ValueError("脚本内容为空，无法执行测试")
        
        test_file = os.path.join(work_dir, "test_playwright.py")
        with open(test_file, 'w', encoding='utf-8') as f:
            f.write(script_content)
        return test_file
    
    def _create_suite_test_files(self, work_dir: str, test_cases_data: List[Dict[str, Any]], suite_name: Optional[str] = None) -> tuple:
        """创建套件测试文件，并输出详细日志便于排查"""
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
                # 如果提供了套件名称且需要生成Allure报告，添加allure.suite装饰器
                if suite_name:
                    script_content = self._add_allure_suite_decorator(script_content, suite_name)

                test_file = os.path.join(work_dir, f"test_case_{test_case_id}.py")
                test_files.append(test_file)
                with open(test_file, 'w', encoding='utf-8') as f:
                    f.write(script_content)
                logger.info(
                    f"[脚本生成] 用例 #{idx + 1} (id={test_case_id}, title={test_case_title}): 成功写入 {os.path.basename(test_file)}"
                )
            except Exception as e:
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
    
    def _add_allure_suite_decorator(self, script_content: str, suite_name: str) -> str:
        """为测试脚本添加allure.suite装饰器"""
        import re
        
        # 检查是否已经导入了allure
        has_allure_import = 'import allure' in script_content or 'from allure' in script_content
        
        # 检查是否已经有suite装饰器
        has_suite_decorator = '@allure.suite' in script_content
        
        if has_suite_decorator:
            # 如果已有suite装饰器，替换它
            script_content = re.sub(
                r'@allure\.suite\(["\'][^"\']*["\']\)',
                f'@allure.suite("{suite_name}")',
                script_content
            )
        else:
            # 查找所有测试函数定义（def test_xxx）
            lines = script_content.split('\n')
            new_lines = []
            i = 0
            
            while i < len(lines):
                line = lines[i]
                # 检查是否是测试函数定义
                if re.match(r'^\s*def\s+test_\w+', line):
                    # 获取函数定义的缩进
                    indent_match = re.match(r'^(\s*)', line)
                    indent = indent_match.group(1) if indent_match else ''
                    
                    # 检查上一行是否已经有装饰器
                    if i > 0 and lines[i-1].strip().startswith('@'):
                        # 在最后一个装饰器后添加suite装饰器
                        new_lines.append(f'{indent}@allure.suite("{suite_name}")')
                    else:
                        # 在函数定义前添加suite装饰器
                        new_lines.append(f'{indent}@allure.suite("{suite_name}")')
                
                new_lines.append(line)
                i += 1
            
            script_content = '\n'.join(new_lines)
        
        # 如果没有导入allure，添加导入语句
        if not has_allure_import:
            # 查找第一个import或from语句的位置
            import_pattern = r'^(import\s+|from\s+)'
            match = re.search(import_pattern, script_content, re.MULTILINE)
            if match:
                # 在第一个import之前添加allure导入
                insert_pos = match.start()
                script_content = script_content[:insert_pos] + 'import allure\n' + script_content[insert_pos:]
            else:
                # 如果没有import语句，在文件开头添加
                script_content = 'import allure\n' + script_content
        
        return script_content
    
    def _create_pytest_config(self, work_dir: str) -> None:
        """创建pytest配置文件"""
        pytest_ini = os.path.join(work_dir, "pytest.ini")
        config_content = """[pytest]
base_url = http://localhost:8000
testpaths = .
python_files = test_*.py
python_classes = Test*
python_functions = test_*
"""
        with open(pytest_ini, 'w', encoding='utf-8') as f:
            f.write(config_content)

    def _run_pytest_command(self, work_dir: str, config: ExecutionConfig) -> subprocess.CompletedProcess:
        """执行pytest命令"""
        cmd = ["python", "-m", "pytest", "--browser", config.browser, "-v", "--tb=short"]
        
        if config.headed:
            cmd.append("--headed")
        
        if config.base_url:
            cmd.extend(["--base-url", config.base_url])
        
        if config.generate_allure:
            allure_results_dir = os.path.join(work_dir, "allure-results")
            os.makedirs(allure_results_dir, exist_ok=True)
            cmd.extend(["--alluredir", allure_results_dir])
        
        env = os.environ.copy()
        env.update({
            'PLAYWRIGHT_BROWSER': config.browser,
            'PLAYWRIGHT_HEADED': str(config.headed).lower(),
            'PYTEST_TIMEOUT': str(config.timeout),
            'PYTHONIOENCODING': 'utf-8',  # 防止 Windows GBK 编码报错
        })

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

    def _get_allure_path(self) -> Optional[str]:
        """获取Allure可执行文件路径"""
        if os.name == 'nt':  # Windows
            allure_path = os.path.join(self.project_root, "allure-2.23.0", "bin", "allure.bat")
        else:  # Linux/Unix
            allure_path = os.path.join(self.project_root, "allure-2.23.0", "bin", "allure")
            if os.path.exists(allure_path):
                os.chmod(allure_path, 0o755)
        
        return allure_path if os.path.exists(allure_path) else None

    def _generate_report(self, work_dir: str, config: ExecutionConfig) -> Optional[str]:
        """生成Allure报告"""
        allure_results_dir = os.path.join(work_dir, "allure-results")
        allure_report_dir = os.path.join(work_dir, "allure-report")
        
        if not os.path.exists(allure_results_dir) or not os.listdir(allure_results_dir):
            return None
        
        allure_path = self._get_allure_path()
        if not allure_path:
            logger.warning("Allure命令行工具未找到")
            return None
        
        try:
            # 检查Allure命令是否可用
            check_result = subprocess.run(
                [allure_path, "--version"],
                capture_output=True,
                text=True,
                encoding='utf-8',
                timeout=ALLURE_CHECK_TIMEOUT
            )
            
            if check_result.returncode != 0:
                logger.warning("Allure命令不可用")
                return None
            
            # 生成Allure报告
            allure_cmd = [allure_path, "generate", allure_results_dir, "-o", allure_report_dir, "--clean"]
            result = subprocess.run(
                allure_cmd,
                cwd=work_dir,
                capture_output=True,
                text=True,
                encoding='utf-8',
                timeout=ALLURE_TIMEOUT
            )
            
            if result.returncode == 0:
                return os.path.join(allure_report_dir, "index.html")
            else:
                logger.warning(f"Allure报告生成失败: {result.stderr}")
                return None
        except subprocess.TimeoutExpired:
            logger.warning("Allure命令执行超时")
            return None
        except Exception as e:
            logger.warning(f"生成Allure报告时发生错误: {e}")
            return None

    def _normalize_allure_report_path(self, allure_report_path: Optional[str]) -> Optional[str]:
        """规范化Allure报告路径"""
        if not allure_report_path:
            return None
        
        if os.path.exists(allure_report_path):
            return allure_report_path
        
        # 即使文件不存在，也返回路径（可能文件稍后才生成）
        return allure_report_path

    def _build_execution_result(self, result: subprocess.CompletedProcess, work_dir: str,
                               allure_report_path: Optional[str], config: ExecutionConfig,
                               case_results: Optional[List[Dict[str, Any]]] = None) -> ExecutionResult:
        """构建执行结果"""
        test_summary = self._extract_test_summary(result.stdout) if result.stdout else None
        
        return ExecutionResult(
            success=result.returncode == 0,
            return_code=result.returncode,
            stdout=result.stdout,
            stderr=result.stderr,
            work_dir=work_dir,
            allure_report=self._normalize_allure_report_path(allure_report_path),
            config=config,
            test_summary=test_summary,
            case_results=case_results
        )
    
    def _build_error_result(self, error_msg: str, work_dir: str, config: ExecutionConfig) -> ExecutionResult:
        """构建错误结果"""
        return ExecutionResult(
            success=False,
            return_code=-1,
            stdout='',
            stderr=error_msg,
            work_dir=work_dir,
            allure_report=None,
            config=config
        )
    
    def _cleanup_work_dir(self, work_dir: str, allure_report_path: Optional[str]) -> None:
        """清理工作目录"""
        try:
            if not (allure_report_path and os.path.exists(allure_report_path)):
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

    def _parse_suite_test_results(self, stdout: str, test_cases_data: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """解析测试套件执行结果"""
        case_results = []
        parsed_case_ids = set()

        if not stdout:
            stdout = ""
            logger.warning("Pytest stdout 为空，可能是执行崩溃或被异常中断")

        for line in stdout.split('\n'):
            line = line.strip()
            
            if 'test_case_' not in line or not any(status in line for status in ['PASSED', 'FAILED', 'SKIPPED']):
                continue
            
            try:
                parts = line.split()
                if len(parts) < 2:
                    continue
                
                test_file_part = parts[0]
                status_part = parts[1].upper()
                
                if 'test_case_' not in test_file_part:
                    continue
                
                # 提取test_case_4.py中的4
                file_name = test_file_part.split('::')[0]
                test_case_id_str = file_name.replace('test_case_', '').replace('.py', '')
                
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
                    status, error_message = 'failed', '测试执行失败'
                elif 'SKIPPED' in status_part:
                    status, error_message = 'skipped', '测试用例被跳过'
                else:
                    continue
                
                case_results.append({
                    'test_case_id': int(test_case_id_str),
                    'test_case_title': case_data.get('test_case_title', f'Test Case {test_case_id_str}'),
                    'status': status,
                    'error_message': error_message
                })
            except (IndexError, ValueError) as e:
                logger.debug(f"解析测试用例结果失败: {line}, 错误: {e}")
                continue
        
        return case_results


# 全局运行器实例
_runner = PlaywrightRunner()


def playwright_runner(script_id: str, script_content: str, base_url: str = None, options: Dict[str, Any] = None) -> Dict[str, Any]:
    """使用pytest执行Playwright Python测试脚本"""
    config = ExecutionConfig(
        browser=options.get('browser', 'chromium') if options else 'chromium',
        headed=options.get('headed', True) if options else True,
        timeout=options.get('timeout', 300) if options else 300,
        generate_allure=options.get('generate_allure', False) if options else False,
        base_url=base_url
    )
    
    result = _runner.run_single_test(script_id, script_content, config)
    
    return {
        'success': result.success,
        'return_code': result.return_code,
        'stdout': result.stdout,
        'stderr': result.stderr,
        'test_file': result.work_dir,
        'work_dir': result.work_dir,
        'allure_report': result.allure_report,
        'browser': result.config.browser,
        'headed': result.config.headed,
        'timeout': result.config.timeout,
        'status': 'passed' if result.success else 'failed',
        'test_summary': result.test_summary
    }


def playwright_suite_runner(suite_id: str, test_cases_data: List[Dict[str, Any]], base_url: str = None, options: Dict[str, Any] = None) -> Dict[str, Any]:
    """使用pytest批量执行多个Playwright Python测试脚本并生成Allure报告"""
    config = ExecutionConfig(
        browser=options.get('browser', 'chromium') if options else 'chromium',
        headed=options.get('headed', True) if options else True,
        timeout=options.get('timeout', 300) if options else 300,
        generate_allure=options.get('generate_allure', True) if options else True,
        base_url=base_url,
        suite_name=options.get('suite_name') if options else None
    )
    
    result = _runner.run_suite_test(suite_id, test_cases_data, config)

    # 失败时提供明确的 error 字段，便于上层展示
    error_msg = result.stderr if not result.success else None

    return {
        'success': result.success,
        'stdout': result.stdout,
        'stderr': result.stderr,
        'error': error_msg,
        'return_code': result.return_code,
        'test_files': [result.work_dir],
        'allure_report': result.allure_report,
        'case_results': result.case_results,
        'execution_info': {
            'browser': result.config.browser,
            'headed': result.config.headed,
            'timeout': result.config.timeout,
            'work_dir': result.work_dir
        }
    }
