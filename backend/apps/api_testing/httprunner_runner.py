"""
HttpRunner测试运行器
支持单个测试用例执行和测试套件批量执行
"""

import os
import tempfile
import subprocess
import logging
import shutil
import uuid
import io
import sys
import yaml
import json
from typing import Dict, Any, Optional, List
from datetime import datetime
from dataclasses import dataclass

from httprunner import HttpRunner
from httprunner.exceptions import ValidationFailure, ParamsError, RequestFailure
from httprunner.loader import load_project_meta
from httprunner.cli import main_run
from loguru import logger as loguru_logger

logger = logging.getLogger(__name__)

# 常量定义
ALLURE_TIMEOUT = 90
ALLURE_CHECK_TIMEOUT = 15

# 用户本地 Allure CLI 绝对路径（优先使用，确保报告生成正常）
ALLURE_COMMAND: str = r"D:\dev\allure-2.37.0\bin\allure.bat"


@dataclass
class ExecutionConfig:
    """执行配置类"""
    timeout: int = 30
    generate_allure: bool = False
    base_url: Optional[str] = None
    headers: Optional[Dict[str, Any]] = None
    variables: Optional[Dict[str, Any]] = None
    suite_name: Optional[str] = None  # 测试套件名称，用于Allure报告
    external_allure_results_dir: Optional[str] = None  # 外部 allure-results 目录（多用例汇总用）
    workspace_dir: Optional[str] = None  # 外部工作目录（隔离多任务，防止竞态）


@dataclass
class ExecutionResult:
    """执行结果类（用于测试套件）"""
    success: bool
    return_code: int
    stdout: str
    stderr: str
    work_dir: str
    allure_report: Optional[str]
    config: ExecutionConfig
    test_summary: Optional[Dict[str, Any]] = None
    case_results: Optional[List[Dict[str, Any]]] = None
    log: Optional[str] = None


# ==================== 公共工具函数 ====================

def _get_project_root() -> str:
    """获取项目根目录"""
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _get_temp_base_dir() -> str:
    """获取临时工作目录基础路径"""
    project_root = _get_project_root()
    temp_base_dir = os.path.join(project_root, "httprunner_workspace")
    os.makedirs(temp_base_dir, exist_ok=True)
    return temp_base_dir


def _read_log_file(log_path: str) -> str:
    """读取日志文件内容"""
    try:
        if os.path.exists(log_path):
            with open(log_path, 'r', encoding='utf-8') as f:
                return f.read()
        return "日志文件不存在"
    except Exception as e:
        return f"读取日志文件失败: {str(e)}"


def _read_logs_from_dir(work_dir: str) -> Optional[str]:
    """从工作目录读取所有日志文件并合并"""
    logs_dir = os.path.join(work_dir, "logs")
    if not os.path.exists(logs_dir):
        return None
    
    # 查找所有日志文件
    log_files = [f for f in os.listdir(logs_dir) if f.endswith('.run.log')]
    if not log_files:
        return None
    
    # 按修改时间排序，确保按执行顺序合并
    log_files_with_time = [(f, os.path.getmtime(os.path.join(logs_dir, f))) for f in log_files]
    log_files_with_time.sort(key=lambda x: x[1])
    
    # 合并所有日志文件
    all_logs = []
    for log_file, _ in log_files_with_time:
        log_path = os.path.join(logs_dir, log_file)
        log_content = _read_log_file(log_path)
        if log_content and log_content != "日志文件不存在":
            all_logs.append(f"\n{'='*80}\n日志文件: {log_file}\n{'='*80}\n{log_content}\n")
    
    return "\n".join(all_logs) if all_logs else None


def _update_config_dict(config: Dict[str, Any], key: str, value: Any) -> None:
    """更新配置字典，如果key不存在则创建"""
    if value:
        if key not in config:
            config[key] = {}
        if isinstance(value, dict):
            config[key].update(value)
        else:
            config[key] = value


def _process_script_content(script_content: str, base_url: str = None, 
                           headers: Dict = None, variables: Dict = None) -> str:
    """处理脚本内容，添加环境配置（支持JSON和YAML格式）"""
    if not script_content or not script_content.strip():
        return script_content
    
    script_content = script_content.strip()
    is_json_format = False
    script_data = None
    
    # 尝试检测格式并解析
    try:
        # 先尝试解析为JSON（JSON格式通常以 { 开头）
        if script_content.startswith('{'):
            try:
                script_data = json.loads(script_content)
                is_json_format = True
                logger.info("检测到JSON格式的脚本内容")
            except json.JSONDecodeError:
                # JSON解析失败，尝试YAML
                pass
        
        # 如果不是JSON或JSON解析失败，尝试YAML
        if script_data is None:
            try:
                script_data = yaml.safe_load(script_content)
                is_json_format = False
                logger.info("检测到YAML格式的脚本内容")
            except yaml.YAMLError:
                # YAML解析也失败
                raise ValueError("无法解析脚本内容，既不是有效的JSON也不是有效的YAML")
                
    except Exception as e:
        logger.error(f"脚本内容解析错误: {e}")
        # 如果解析失败，返回原始内容
        return script_content
    
    # 确保config配置存在
    if 'config' not in script_data:
        script_data['config'] = {}
    
    config = script_data['config']
    
    # 更新配置项
    if base_url:
        config['base_url'] = base_url
    
    _update_config_dict(config, 'variables', variables)
    
    # 添加默认配置
    config.setdefault('timeout', 30)
    
    # 确保teststeps存在
    if 'teststeps' not in script_data:
        script_data['teststeps'] = []
    
    # 处理每个teststep，修复格式问题
    for teststep in script_data['teststeps']:
        if 'request' not in teststep:
            teststep['request'] = {}
        
        request = teststep['request']
        
        # 处理 validate 字段：将列表格式转换为字典格式
        # HttpRunner期望格式: [{"eq": ["status_code", 200]}] 
        # 但我们可能生成: [["eq", ["status_code", 200]]]
        if 'validate' in teststep and isinstance(teststep['validate'], list):
            converted_validators = []
            for validator in teststep['validate']:
                if isinstance(validator, list) and len(validator) >= 2:
                    # 列表格式: ["body.code", "ne", 0]
                    # 应该转换为: {"ne": ["body.code", 0]}
                    if len(validator) == 3:
                        # 标准格式: [check_field, comparator, expect_value]
                        check_field = validator[0]
                        comparator = validator[1]
                        expect_value = validator[2]
                        converted_validators.append({comparator: [check_field, expect_value]})
                    elif len(validator) == 2:
                        # 两元素格式: [check_field, comparator] (如exists/not_exists)
                        check_field = validator[0]
                        comparator = validator[1]
                        converted_validators.append({comparator: [check_field]})
                elif isinstance(validator, dict):
                    # 已经是字典格式，直接使用
                    converted_validators.append(validator)
            teststep['validate'] = converted_validators
        
        # 处理 request.data 和 request.json 为 None 的情况
        # HttpRunner 的 Pydantic 模型不接受 None，需要删除这些字段或使用空值
        if 'data' in request and request['data'] is None:
            del request['data']
        if 'json' in request and request['json'] is None:
            del request['json']
        
        # httprunner的TConfig模型不支持headers字段，需要将headers合并到每个teststep的request中
        if headers:
            if 'headers' not in request:
                request['headers'] = {}
            
            # 合并headers：config中的headers优先，teststep中的headers可以覆盖
            # 先应用config的headers，再应用teststep的headers（teststep优先级更高）
            merged_headers = {}
            merged_headers.update(headers)  # 先应用config的headers
            merged_headers.update(request['headers'])  # teststep的headers可以覆盖
            request['headers'] = merged_headers
    
    # 根据原始格式返回相应格式的内容
    # HttpRunner支持JSON格式，但为了兼容性，我们统一转换为YAML格式
    # 因为HttpRunner的CLI工具默认期望YAML格式
    try:
        return yaml.dump(script_data, default_flow_style=False, allow_unicode=True)
    except Exception as e:
        logger.error(f"转换为YAML格式失败: {e}")
        # 如果转换失败，尝试返回JSON格式
        if is_json_format:
            try:
                return json.dumps(script_data, ensure_ascii=False, indent=2)
            except Exception as json_error:
                logger.error(f"转换为JSON格式也失败: {json_error}")
                return script_content
        return script_content


def _clear_project_meta_cache():
    """清除httprunner的项目元数据缓存"""
    try:
        import httprunner.loader as loader_module
        loader_module.project_meta = None
    except Exception as e:
        logger.warning(f"清除项目元数据缓存失败: {e}")


def _ensure_debugtalk_py(work_dir: str) -> None:
    """
    在工作目录写入 debugtalk.py，将项目内置自定义函数注册进 HttpRunner 执行上下文。

    HttpRunner 通过 load_project_meta() 从项目根目录（向上搜索）加载 debugtalk.py。
    Celery 异步任务使用的是临时工作目录（httprunner_workspace/），目录树中没有
    debugtalk.py，导致 project_meta.functions 为空，进而引发 FunctionNotFound 异常。

    解决方案：在每次执行前将包含全量自定义函数的 debugtalk.py 写入 work_dir，
    保证 load_project_meta(work_dir) 能找到并注册所有函数。
    """
    debugtalk_path = os.path.join(work_dir, "debugtalk.py")
    if os.path.exists(debugtalk_path):
        return

    # backend/apps/ 目录的绝对路径（让 debugtalk.py 在被 importlib 加载时能定位本地 httprunner）
    apps_dir = os.path.normpath(
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
    )

    debugtalk_content = (
        "# Auto-generated by AITS: registers all custom HttpRunner functions\n"
        "# into the project_meta.functions context for this execution.\n"
        "import sys as _sys\n"
        f"_apps_dir = {repr(apps_dir)}\n"
        "if _apps_dir not in _sys.path:\n"
        "    _sys.path.insert(0, _apps_dir)\n"
        "\n"
        "# 通配符导入所有自定义函数（包括 get_random_string / get_random_int 等）\n"
        "from httprunner.builtin.functions import *\n"  # noqa
    )

    with open(debugtalk_path, "w", encoding="utf-8") as f:
        f.write(debugtalk_content)

    logger.info(f"debugtalk.py 已写入工作目录: {debugtalk_path}")


def _create_work_dir(prefix: str) -> str:
    """创建临时工作目录"""
    temp_base_dir = _get_temp_base_dir()
    return tempfile.mkdtemp(prefix=prefix, dir=temp_base_dir)


def _write_test_file(work_dir: str, filename: str, script_content: str, 
                    base_url: Optional[str] = None, headers: Optional[Dict] = None, 
                    variables: Optional[Dict] = None) -> str:
    """写入测试文件（统一函数）"""
    test_file = os.path.join(work_dir, filename)
    processed_content = _process_script_content(script_content, base_url, headers, variables)
    with open(test_file, 'w', encoding='utf-8') as f:
        f.write(processed_content)
    logger.info(f"httprunner_runner processed_content: {processed_content}")
    return test_file


def _convert_to_relative_paths(test_files: List[str], work_dir: str) -> List[str]:
    """将绝对路径转换为相对于工作目录的相对路径"""
    return [
        os.path.relpath(test_file, work_dir) if os.path.isabs(test_file) else test_file
        for test_file in test_files
    ]


class MockCompletedProcess:
    """模拟subprocess.CompletedProcess的对象"""
    def __init__(self, returncode, stdout, stderr):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


# ==================== 测试用例执行功能 ====================

def _build_runner_result(summary: Any, log_path: str, success: bool = True, error: str = None) -> Dict[str, Any]:
    """构建运行器结果（用于单个测试用例）"""
    log_content = _read_log_file(log_path)
    
    try:
        result = summary.model_dump() if hasattr(summary, 'model_dump') else dict(summary) if summary else {}
        result['log'] = log_content
        
        # 如果执行失败，确保包含错误信息
        if not success:
            result['success'] = False
            # 优先使用传入的error，其次从summary中提取，最后从日志中提取
            if error:
                result['error'] = error
            elif not result.get('error'):
                # 尝试从summary中提取错误信息
                if hasattr(summary, 'log') and summary.log:
                    result['error'] = summary.log
                elif log_content:
                    # 从日志中提取最后几行错误信息
                    log_lines = log_content.split('\n')
                    error_lines = [line for line in log_lines if any(keyword in line.lower() for keyword in ['error', 'failed', 'exception', 'traceback'])]
                    if error_lines:
                        result['error'] = '\n'.join(error_lines[-10:])  # 取最后10行错误信息
                    else:
                        result['error'] = '测试执行失败，请查看日志获取详细信息'
                else:
                    result['error'] = '测试执行失败'
        
        return {
            'success': success,
            'result': result,
            **({'error': result.get('error') or error, 'error_type': 'execution_error'} if not success else {})
        }
    except Exception as e:
        # 如果无法获取summary，至少尝试读取日志文件
        logger.error(f"构建运行器结果失败: {e}", exc_info=True)
        error_msg = error or str(e) or '执行错误'
        
        # 尝试从日志中提取错误信息
        if log_content:
            log_lines = log_content.split('\n')
            error_lines = [line for line in log_lines if any(keyword in line.lower() for keyword in ['error', 'failed', 'exception', 'traceback'])]
            if error_lines:
                error_msg = f"{error_msg}\n\n日志错误信息:\n" + '\n'.join(error_lines[-10:])
        
        return {
            'success': False,
            'error': error_msg,
            'error_type': 'execution_error',
            'log': log_content,
            'result': {
                'success': False,
                'error': error_msg,
                'log': log_content
            }
        }


def _run_single_test_case(test_file: str, work_dir: str, timeout: int) -> Dict[str, Any]:
    """执行单个测试用例（使用HttpRunner API）"""
    # 在工作目录写入 debugtalk.py（注册自定义函数），并强制刷新全局缓存，
    # 避免 Celery Worker 复用旧的、functions 为空的 project_meta 单例。
    _ensure_debugtalk_py(work_dir)
    _clear_project_meta_cache()

    runner = HttpRunner()

    # 设置项目元数据，确保日志文件能够正确生成
    # reload=True：强制重新加载，防止上次任务缓存的旧 project_meta 被复用
    project_meta = load_project_meta(work_dir, reload=True)
    runner.with_project_meta(project_meta)
    
    # 设置case_id，确保日志文件名唯一
    case_id = f"testcase_{uuid.uuid4().hex[:8]}"
    runner.with_case_id(case_id)
    
    # 确保logs目录存在
    logs_dir = os.path.join(work_dir, "logs")
    os.makedirs(logs_dir, exist_ok=True)
    
    # 设置日志路径
    log_path = os.path.join(logs_dir, f"{case_id}.run.log")
    runner._HttpRunner__log_path = log_path
    
    # 手动添加日志处理器
    log_handler = loguru_logger.add(log_path, level="DEBUG")
    
    try:
        runner.run_path(test_file)
        summary = runner.get_summary()
        return _build_runner_result(summary, log_path, success=True)
        
    except Exception as e:
        # 即使HttpRunner执行失败，也尝试获取部分结果
        error_type = f"{type(e).__module__}.{type(e).__name__}"
        error_message = f'{error_type}: {str(e)}'
        log_content = _read_log_file(log_path)
        
        try:
            summary = runner.get_summary()
            return _build_runner_result(summary, log_path, success=False, error=error_message)
        except Exception as summary_error:
            logger.error(f"获取summary失败: {summary_error}", exc_info=True)
            # 尝试从日志中提取更详细的错误信息
            detailed_error = error_message
            if log_content:
                log_lines = log_content.split('\n')
                # 查找包含错误、异常、失败等关键词的行
                error_lines = []
                for i, line in enumerate(log_lines):
                    if any(keyword in line.lower() for keyword in ['error', 'failed', 'exception', 'traceback', 'failed']):
                        # 包含错误关键词的行及其上下文
                        start = max(0, i - 2)
                        end = min(len(log_lines), i + 5)
                        error_lines.extend(log_lines[start:end])
                
                if error_lines:
                    detailed_error = f"{error_message}\n\n详细错误信息（来自日志）:\n" + '\n'.join(error_lines[-20:])
            
            return {
                'success': False,
                'error': detailed_error,
                'error_type': error_type,
                'log': log_content,
                'result': {
                    'success': False,
                    'error': detailed_error,
                    'error_type': error_type,
                    'log': log_content
                }
            }
    finally:
        # 移除日志处理器
        loguru_logger.remove(log_handler)


def httprunner_runner(
    script_id: str,
    script_content: str,
    base_url: str = None,
    options: Dict[str, Any] = None
) -> Dict[str, Any]:
    """
    使用HttpRunner执行单个API测试脚本
    
    Args:
        script_id: 脚本ID
        script_content: 脚本内容（支持JSON和YAML格式）
        base_url: 测试环境的基础URL
        options: 执行选项
            - timeout: 超时时间（秒）
            - headers: 请求头
            - variables: 变量
    
    Returns:
        执行结果字典
    """
    if options is None:
        options = {}
    
    # 获取选项
    timeout = options.get('timeout', 30)
    headers = options.get('headers', {})
    variables = options.get('variables', {})
    
    # 检查脚本内容
    if not script_content or not script_content.strip():
        return _build_error_result('error', '脚本内容为空，无法执行测试', '', base_url, timeout)
    
    # 创建临时工作目录
    work_dir = _create_work_dir(f"httprunner_python_{script_id}_")
    
    try:
        # 创建测试文件
        test_file = _write_test_file(work_dir, "test_api.yaml", script_content, base_url, headers, variables)
        logger.info(f"httprunner_runner test_file: {test_file}")
        # 执行HttpRunner
        result = _run_single_test_case(test_file, work_dir, timeout)
        
        # 构建返回结果，确保包含所有信息（包括错误信息）
        if result.get('success', False):
            return result.get('result', {})
        else:
            # 执行失败，返回包含错误信息的完整结果
            error_result = result.get('result', {})
            error_result['success'] = False
            error_result['error'] = result.get('error') or error_result.get('error') or '测试执行失败'
            error_result['error_type'] = result.get('error_type', 'execution_error')
            # 确保包含日志
            if 'log' not in error_result:
                error_result['log'] = result.get('log', '')
            return error_result
        
    except subprocess.TimeoutExpired:
        logger.error(f"HttpRunner测试执行超时: {script_id}")
        error_result = _build_error_result('timeout', f'测试执行超时（{timeout}秒）', work_dir, base_url, timeout)
        # 尝试读取日志文件
        logs_dir = os.path.join(work_dir, "logs")
        if os.path.exists(logs_dir):
            log_content = _read_logs_from_dir(work_dir)
            if log_content:
                error_result['log'] = log_content
        return error_result
        
    except Exception as e:
        logger.error(f"执行HttpRunner测试时发生错误: {e}", exc_info=True)
        error_result = _build_error_result('error', str(e), work_dir, base_url, timeout)
        # 尝试读取日志文件
        logs_dir = os.path.join(work_dir, "logs")
        if os.path.exists(logs_dir):
            log_content = _read_logs_from_dir(work_dir)
            if log_content:
                error_result['log'] = log_content
        return error_result
    
    # 注意：临时目录暂不清理，便于调试


def _build_error_result(error_type: str, error_message: str, work_dir: str, 
                       base_url: str, timeout: int) -> Dict[str, Any]:
    """构建错误结果"""
    return {
        'success': False,
        'status': 'failed',
        'work_dir': work_dir,
        'base_url': base_url,
        'timeout': timeout,
        'error': error_message,
        'error_type': error_type,
        'message': f'HttpRunner测试执行失败: {error_message}',
        'timestamp': datetime.now().isoformat()
    }


def execute_api_test_case(
    test_case_id: int,
    script_content: str,
    environment: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """
    执行API测试用例的便捷函数
    
    Args:
        test_case_id: 测试用例ID
        script_content: 脚本内容（支持JSON和YAML格式）
        environment: 环境配置
    
    Returns:
        执行结果
    """
    if not environment:
        environment = {}

    # 变量替换
    from api_testing.runner import ScriptExecutor

    environment_vars = environment.get('variables', {}) or {}
    script_executor = ScriptExecutor(
        environment_vars=environment_vars,
        debug_context={"test_case_id": test_case_id}
    )
    script_content = script_executor.apply_variable_substitution(script_content)
    
    result = httprunner_runner(
        script_id=str(test_case_id),
        script_content=script_content,
        base_url=environment.get('base_url'),
        options={
            'timeout': environment.get('timeout', 30),
            'headers': environment.get('headers', {}),
            'variables': environment_vars
        }
    )

    result = result or {}
    result['pm_environment_variables'] = environment_vars
    result['pm_console_logs'] = script_executor.console_logs
    return result


# ==================== 测试套件执行功能 ====================

class HttpRunnerSuiteRunner:
    """HttpRunner测试套件运行器"""
    
    def __init__(self):
        self.project_root = _get_project_root()
    
    def run_suite_test(self, suite_id: str, test_cases_data: List[Dict[str, Any]], 
                      config: ExecutionConfig) -> ExecutionResult:
        """执行测试套件"""
        if getattr(config, 'workspace_dir', None):
            work_dir = config.workspace_dir
            os.makedirs(work_dir, exist_ok=True)
        else:
            work_dir = _create_work_dir(f"httprunner_suite_{suite_id}_")
        
        try:
            test_files, skipped_results = self._create_suite_test_files(work_dir, test_cases_data, config)
            if not test_files:
                raise ValueError("没有可执行的测试用例")
            
            return self._execute_tests(test_files, work_dir, config, test_cases_data, skipped_results)
        except subprocess.TimeoutExpired:
            logger.error(f"测试套件执行超时: {suite_id}")
            return self._build_error_result(f"测试套件执行超时（{config.timeout}秒）", work_dir, config)
        except Exception as e:
            logger.error(f"执行测试套件时发生错误: {e}", exc_info=True)
            return self._build_error_result(f"执行测试套件时发生错误: {e}", work_dir, config)
    
    def _execute_tests(self, test_files: List[str], work_dir: str, config: ExecutionConfig, 
                      test_cases_data: Optional[List[Dict[str, Any]]] = None,
                      skipped_results: Optional[List[Dict[str, Any]]] = None) -> ExecutionResult:
        """执行测试的通用方法"""
        result = self._run_httprunner_command(test_files, work_dir, config)
        # 使用外部 alluredir 时不在本 work_dir 生成报告，由调用方统一生成
        use_external = getattr(config, 'external_allure_results_dir', None)
        allure_report_path = (
            self._generate_report(work_dir, config) if config.generate_allure and not use_external else None
        )
        
        # 如果是套件测试，解析用例结果
        case_results = None
        if test_cases_data is not None:
            parsed_case_results = self._parse_suite_test_results(result.stdout, test_cases_data)
            case_results = (skipped_results or []) + parsed_case_results
        
        execution_result = self._build_execution_result(result, work_dir, allure_report_path, config, case_results)
        # 清理工作目录（保留报告）；外部 workspace_dir 由调用方管理，此处不清理
        if not getattr(config, 'workspace_dir', None):
            self._cleanup_work_dir(work_dir, execution_result.allure_report)
        return execution_result
    
    def _create_suite_test_files(self, work_dir: str, test_cases_data: List[Dict[str, Any]], 
                                config: ExecutionConfig) -> tuple:
        """创建套件测试文件"""
        test_files = []
        skipped_results = []
        
        for case_data in test_cases_data:
            test_case_id = case_data.get('test_case_id')
            script_content = case_data.get('script_content', '').strip()
            test_case_title = case_data.get('test_case_title', f'Test Case {test_case_id}')
            
            if not script_content:
                logger.warning(f"测试用例 {test_case_title} 没有脚本内容，跳过")
                skipped_results.append({
                    'test_case_id': test_case_id,
                    'test_case_title': test_case_title,
                    'status': 'skipped',
                    'error_message': '测试用例没有脚本内容'
                })
                continue
            
            test_file = _write_test_file(
                work_dir,
                f"test_case_{test_case_id}.yaml",
                script_content,
                config.base_url,
                config.headers,
                config.variables
            )
            test_files.append(test_file)
        
        return test_files, skipped_results
    
    def _run_httprunner_command(self, test_files: List[str], work_dir: str, 
                               config: ExecutionConfig) -> subprocess.CompletedProcess:
        """执行HttpRunner命令；当有外部 workspace_dir 时使用 subprocess.run(hrun) 以捕获完整报错"""
        allure_results_dir = getattr(config, 'external_allure_results_dir', None) or "allure-results"
        if not os.path.isabs(allure_results_dir):
            abs_results = os.path.join(work_dir, allure_results_dir)
            if os.path.isdir(abs_results):
                try:
                    shutil.rmtree(abs_results)
                except Exception as e:
                    logger.warning(f"清理旧 allure-results 失败: {e}")
            os.makedirs(abs_results, exist_ok=True)
        else:
            os.makedirs(allure_results_dir, exist_ok=True)

        # 回归原生 API：不再使用 subprocess，始终走 main_run 以配合 tasks.py 的 monkey patch
        use_subprocess = False
        if use_subprocess:
            # 终极方案：直接使用 pytest 模块运行工作区内的所有 _test.py 脚本
            test_cmd = f'"{sys.executable}" -m pytest "{work_dir}" --alluredir="{allure_results_dir}"'
            logger.info("========== 开始执行 Pytest 底层测试 ==========")
            logger.info("动态构建的执行命令: %s", test_cmd)
            try:
                result = subprocess.run(
                    test_cmd,
                    shell=True,
                    capture_output=True,
                    text=True,
                    timeout=config.timeout + 60,
                    cwd=work_dir,
                )
                logger.info("Pytest 返回码: %s", result.returncode)
                logger.info("Pytest 标准输出 (stdout):\n%s", result.stdout or "")
                if result.stderr:
                    logger.error("Pytest 错误输出 (stderr):\n%s", result.stderr)
                logger.info("========== Pytest 执行结束 ==========")
                return MockCompletedProcess(
                    result.returncode,
                    result.stdout or "",
                    result.stderr or "",
                )
            except subprocess.TimeoutExpired as e:
                logger.error("Pytest 执行超时: %s", e)
                return MockCompletedProcess(-1, "", str(e))
            except Exception as e:
                logger.error("Pytest 执行抛出底层异常: %s", e, exc_info=True)
                return MockCompletedProcess(-1, "", str(e))

        # 默认：使用 main_run（Python API）
        original_cwd = os.getcwd()
        original_stdout = sys.stdout
        original_stderr = sys.stderr
        try:
            os.chdir(work_dir)
            _clear_project_meta_cache()
            extra_args = ["-p", "allure_pytest"] + _convert_to_relative_paths(test_files, work_dir)
            extra_args.append("-s")
            if config.generate_allure:
                extra_args.append(f"--alluredir={allure_results_dir}")
            stdout_capture, stderr_capture = io.StringIO(), io.StringIO()
            sys.stdout, sys.stderr = stdout_capture, stderr_capture
            pytest_exit_code = main_run(extra_args)
            return MockCompletedProcess(
                int(pytest_exit_code) if hasattr(pytest_exit_code, '__int__') else 0,
                stdout_capture.getvalue(),
                stderr_capture.getvalue()
            )
        finally:
            os.chdir(original_cwd)
            sys.stdout, sys.stderr = original_stdout, original_stderr
    
    def _build_execution_result(self, result: subprocess.CompletedProcess, work_dir: str,
                               allure_report_path: Optional[str], config: ExecutionConfig,
                               case_results: Optional[List[Dict[str, Any]]] = None) -> ExecutionResult:
        """构建执行结果"""
        test_summary = self._extract_test_summary(result.stdout) if result.stdout else None
        
        # 读取日志文件
        log_content = _read_logs_from_dir(work_dir)
        
        return ExecutionResult(
            success=result.returncode == 0,
            return_code=result.returncode,
            stdout=result.stdout,
            stderr=result.stderr,
            work_dir=work_dir,
            allure_report=allure_report_path,
            config=config,
            test_summary=test_summary,
            case_results=case_results,
            log=log_content
        )
    
    def _build_error_result(self, error_msg: str, work_dir: str, 
                          config: ExecutionConfig) -> ExecutionResult:
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
    
    @staticmethod
    def _extract_test_summary(stdout: str) -> Dict[str, Any]:
        """从pytest输出中提取测试统计信息"""
        summary = {'total': 0, 'passed': 0, 'failed': 0, 'skipped': 0, 'errors': 0}
        
        if not stdout:
            return summary
        
        try:
            for line in stdout.split('\n'):
                line_lower = line.lower()
                if 'passed' in line_lower and ('failed' in line_lower or 'skipped' in line_lower):
                    parts = line.split()
                    for part in parts:
                        if part.isdigit():
                            num = int(part)
                            if 'passed' in line_lower:
                                summary['passed'] = num
                            if 'failed' in line_lower:
                                summary['failed'] = num
                            if 'skipped' in line_lower:
                                summary['skipped'] = num
                            if 'error' in line_lower:
                                summary['errors'] = num
                    
                    summary['total'] = sum([summary['passed'], summary['failed'], summary['skipped'], summary['errors']])
                    break
        except Exception as e:
            logger.warning(f"提取测试统计信息失败: {e}")
        
        return summary
    
    @staticmethod
    def _parse_suite_test_results(stdout: str, test_cases_data: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """解析测试套件执行结果"""
        case_results = []
        parsed_case_ids = set()
        status_map = {
            'PASSED': ('passed', None),
            'FAILED': ('failed', '测试执行失败'),
            'SKIPPED': ('skipped', '测试用例被跳过')
        }
        
        for line in stdout.split('\n'):
            line = line.strip()
            
            if 'test_case_' not in line or not any(status in line for status in status_map.keys()):
                continue
            
            try:
                # 支持两种格式：
                # 1. "test_case_1_test.py PASSED"
                # 2. "FAILED test_case_1_test.py::TestCaseTestCase1::test_start - ..."
                parts = line.split()
                if len(parts) < 2:
                    continue
                
                # 检查第一个部分是否是状态
                first_part = parts[0].upper()
                if first_part in status_map:
                    # 格式：FAILED test_case_1_test.py::...
                    status_key = first_part
                    test_file_part = parts[1] if len(parts) > 1 else ''
                else:
                    # 格式：test_case_1_test.py PASSED
                    test_file_part = parts[0]
                    status_key = parts[1].upper() if len(parts) > 1 else ''
                
                if 'test_case_' not in test_file_part:
                    continue
                
                # 提取test_case_id
                file_name = test_file_part.split('::')[0]
                test_case_id_str = file_name.replace('test_case_', '').replace('_test.py', '').replace('.py', '')
                
                if not test_case_id_str or test_case_id_str in parsed_case_ids:
                    continue
                
                parsed_case_ids.add(test_case_id_str)
                
                # 查找对应的用例数据
                case_data = next(
                    (c for c in test_cases_data if str(c.get('test_case_id')) == test_case_id_str),
                    None
                )
                if not case_data:
                    continue
                
                # 确定状态
                if status_key in status_map:
                    status, error_msg = status_map[status_key]
                    case_results.append({
                        'test_case_id': int(test_case_id_str),
                        'test_case_title': case_data.get('test_case_title', f'Test Case {test_case_id_str}'),
                        'status': status,
                        'error_message': error_msg
                    })
            except (IndexError, ValueError) as e:
                logger.debug(f"解析测试用例结果失败: {line}, 错误: {e}")
                continue
        
        return case_results
    
    def _get_allure_path(self) -> Optional[str]:
        """获取Allure可执行文件路径（优先使用 ALLURE_COMMAND 常量中指定的路径）"""
        # 1. 优先使用顶部常量配置的本地路径
        if ALLURE_COMMAND and os.path.exists(ALLURE_COMMAND):
            logger.info(f"使用配置的 Allure CLI: {ALLURE_COMMAND}")
            return ALLURE_COMMAND

        # 2. 退而求其次，检查项目目录中捆绑的版本
        if os.name == 'nt':
            candidates = [
                os.path.join(self.project_root, "allure-2.37.0", "bin", "allure.bat"),
                os.path.join(self.project_root, "allure-2.23.0", "bin", "allure.bat"),
            ]
        else:
            candidates = [
                os.path.join(self.project_root, "allure-2.37.0", "bin", "allure"),
                os.path.join(self.project_root, "allure-2.23.0", "bin", "allure"),
            ]
        for path in candidates:
            if os.path.exists(path):
                if os.name != 'nt':
                    os.chmod(path, 0o755)
                logger.info(f"使用捆绑的 Allure CLI: {path}")
                return path

        logger.warning("未找到可用的 Allure CLI，请检查 ALLURE_COMMAND 常量或安装 Allure")
        return None
    
    def _generate_report(self, work_dir: str, config: ExecutionConfig) -> Optional[str]:
        """生成Allure报告"""
        allure_results_dir = os.path.join(work_dir, "allure-results")
        allure_report_dir = os.path.join(work_dir, "allure-report")
        
        if not os.path.exists(allure_results_dir) or not os.listdir(allure_results_dir):
            logger.warning("Allure结果目录为空，无法生成报告")
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
                timeout=ALLURE_TIMEOUT
            )
            
            if result.returncode == 0:
                report_path = os.path.join(allure_report_dir, "index.html")
                logger.info(f"Allure报告生成成功: {report_path}")
                return report_path
            else:
                logger.warning(f"Allure报告生成失败: {result.stderr}")
                return None
        except subprocess.TimeoutExpired:
            logger.warning("Allure命令执行超时")
            return None
        except Exception as e:
            logger.warning(f"生成Allure报告时发生错误: {e}")
            return None
    
    def _cleanup_work_dir(self, work_dir: str, allure_report_path: Optional[str]) -> None:
        """清理工作目录"""
        try:
            if not (allure_report_path and os.path.exists(allure_report_path)):
                shutil.rmtree(work_dir)
        except Exception as e:
            logger.warning(f"清理临时目录失败: {e}")


# 全局运行器实例
_suite_runner = HttpRunnerSuiteRunner()


def generate_allure_report_from_results_dir(
    allure_results_dir: str, output_dir: str
) -> Optional[str]:
    """
    从指定的 allure-results 目录生成 Allure 报告到 output_dir。
    供定时任务等场景在多用例写入同一 results 目录后统一生成报告。
    Returns:
        index.html 的完整路径，失败返回 None
    """
    if not os.path.isdir(allure_results_dir) or not os.listdir(allure_results_dir):
        logger.warning("Allure结果目录为空，无法生成报告")
        return None
    allure_path = _suite_runner._get_allure_path()
    if not allure_path:
        logger.warning("Allure命令行工具未找到")
        return None
    try:
        os.makedirs(output_dir, exist_ok=True)
        result = subprocess.run(
            [allure_path, "generate", allure_results_dir, "-o", output_dir, "--clean"],
            capture_output=True,
            text=True,
            timeout=ALLURE_TIMEOUT,
        )
        if result.returncode != 0:
            logger.warning(f"Allure报告生成失败: {result.stderr}")
            return None
        index_path = os.path.join(output_dir, "index.html")
        if os.path.exists(index_path):
            logger.info(f"Allure报告生成成功: {index_path}")
            return index_path
    except subprocess.TimeoutExpired:
        logger.warning("Allure命令执行超时")
    except Exception as e:
        logger.warning(f"生成Allure报告时发生错误: {e}")
    return None


def _build_execution_config(base_url: Optional[str], options: Optional[Dict[str, Any]], 
                            default_allure: bool = False) -> ExecutionConfig:
    """构建执行配置"""
    if options is None:
        options = {}
    return ExecutionConfig(
        timeout=options.get('timeout', 30),
        generate_allure=options.get('generate_allure', default_allure),
        base_url=base_url,
        headers=options.get('headers'),
        variables=options.get('variables'),
        suite_name=options.get('suite_name'),
        external_allure_results_dir=options.get('external_allure_results_dir'),
        workspace_dir=options.get('workspace_dir'),
    )


def _build_result_dict(result: ExecutionResult, include_case_results: bool = False) -> Dict[str, Any]:
    """构建结果字典"""
    result_dict = {
        'success': result.success,
        'return_code': result.return_code,
        'stdout': result.stdout,
        'stderr': result.stderr,
        'work_dir': result.work_dir,
        'allure_report': result.allure_report,
        'log': result.log,
        'status': 'passed' if result.success else 'failed',
        'test_summary': result.test_summary
    }
    
    if include_case_results:
        result_dict['case_results'] = result.case_results
        result_dict['test_files'] = [result.work_dir]
        result_dict['execution_info'] = {
            'timeout': result.config.timeout,
            'work_dir': result.work_dir,
            'base_url': result.config.base_url
        }
    
    return result_dict


def httprunner_suite_runner(
    suite_id: str,
    test_cases_data: List[Dict[str, Any]],
    base_url: str = None,
    options: Dict[str, Any] = None
) -> Dict[str, Any]:
    """
    使用HttpRunner批量执行多个API测试脚本并生成Allure报告
    
    Args:
        suite_id: 套件ID
        test_cases_data: 测试用例数据列表，每个元素包含：
            - test_case_id: 测试用例ID
            - script_content: 脚本内容（支持JSON和YAML格式）
            - test_case_title: 测试用例标题（可选）
        base_url: 测试环境的基础URL
        options: 执行选项
            - timeout: 超时时间（秒）
            - headers: 请求头
            - variables: 变量
            - generate_allure: 是否生成Allure报告（默认True）
            - suite_name: 套件名称（可选）
    
    Returns:
        执行结果字典
    """
    config = _build_execution_config(base_url, options or {}, default_allure=True)
    result = _suite_runner.run_suite_test(suite_id, test_cases_data, config)
    return _build_result_dict(result, include_case_results=True)

