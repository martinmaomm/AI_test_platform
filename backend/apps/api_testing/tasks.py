"""
Celery异步任务
"""
import json
import logging
import os
import shutil
import subprocess
import tempfile
import time
import uuid
from celery import shared_task
from django.core.cache import cache
from typing import Dict, Any, Optional, List, Tuple
from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone
from datetime import timedelta
from ai_core.api_testcase_generator import ApiTestcaseGeneratorService
from api_testing.models import APISpecification, APIEndpoint, APITestCase

from common.task import (
    execute_async_task_with_progress,
    execute_async_task_with_websocket,
    update_task_progress,
    build_error_result
)

User = get_user_model()

logger = logging.getLogger(__name__)

# ============ 全局常量配置 ============

# 默认测试类型配置
DEFAULT_TEST_TYPE_CONFIGS = {
    'positive': 3,
    'negative': 3,
    'boundary': 3
}


# ============ 通用任务执行框架 ============


def _validate_project_and_resources(project_id: int, user_id: int, 
                                  require_api_spec: bool = True) -> Tuple[Optional[Any], Optional[User]]:
    """
    验证项目和必要资源
    
    Args:
        project_id: 项目ID
        user_id: 用户ID
        require_api_spec: 是否需要API规范
    
    Returns:
        (project, user) 元组，验证失败时返回(None, None)
    """
    try:
        from projects.models import Project
        project = Project.objects.get(id=project_id)
    except Project.DoesNotExist:
        return None, None
    
    try:
        user = User.objects.get(id=user_id) if user_id else None
    except User.DoesNotExist:
        user = None
    
    # 检查API规范（如果需要）
    if require_api_spec and not APISpecification.objects.filter(project=project).exists():
        return None, None
    
    return project, user


# ============ 端点测试用例生成任务 ============

@shared_task(bind=True, name='api_testing.tasks.generate_endpoint_test_cases_async')
def generate_endpoint_test_cases_async(self, spec_id: int, endpoint_id: int, 
                                     test_type_configs: dict = None, 
                                     user_id: int = None) -> Dict[str, Any]:
    """
    异步生成端点测试用例
    
    Args:
        spec_id: API规范ID
        endpoint_id: 端点ID
        test_type_configs: 测试类型配置字典，格式：{'positive': 3, 'negative': 2, 'boundary': 1}
        user_id: 用户ID
    
    Returns:
        包含任务状态和结果的字典
    """
    # 使用默认配置
    test_type_configs = test_type_configs or DEFAULT_TEST_TYPE_CONFIGS.copy()
    
    # 使用通用任务执行框架（直接传递参数，无需拆开赋值）
    return execute_async_task_with_progress(
        self, 
        '端点测试用例生成',
        _execute_endpoint_test_case_generation,
        spec_id, endpoint_id, test_type_configs, user_id
    )


def _execute_endpoint_test_case_generation(task_instance, spec_id: int, endpoint_id: int,
                                         test_type_configs: dict, 
                                         user_id: int) -> Dict[str, Any]:
    """
    执行端点测试用例生成逻辑
    
    Args:
        task_instance: Celery任务实例（用于进度更新）
        spec_id: API规范ID
        endpoint_id: 端点ID
        test_type_configs: 测试类型配置字典
        user_id: 用户ID
    
    Returns:
        生成结果
    """
    try:
        # 步骤1: 获取API规范和端点
        update_task_progress(task_instance, 10, '正在获取API规范和端点信息...')
        api_spec = APISpecification.objects.get(id=spec_id)
        endpoint = api_spec.endpoints.get(id=endpoint_id)
        
        # 步骤2: 初始化AI测试生成服务
        update_task_progress(task_instance, 20, '正在初始化AI测试生成服务...')
        service = ApiTestcaseGeneratorService()
        
        # 构建端点信息
        endpoint_info = _build_endpoint_info(endpoint)
        
        # 步骤3: 生成测试用例和HttpRunner脚本（统一在服务中处理）
        update_task_progress(task_instance, 40, 'AI正在生成测试用例和HttpRunner脚本...')
        case_types = list(test_type_configs.keys()) if test_type_configs else ['positive', 'negative']
        result = service.generate_test_cases_and_script(endpoint_info, case_types, test_type_configs)
        
        if not result.get('success'):
            return build_error_result(None, result.get('error', 'AI生成测试用例失败'))
        
        # 提取结果
        test_cases = result.get('test_cases', {})
        formatted_cases = result.get('formatted_cases', {})
        full_script = result.get('script', '')
        
        # 步骤4: 保存测试用例到数据库
        update_task_progress(task_instance, 85, '正在保存测试用例到数据库...')
        save_result = service.save_test_cases_to_db(formatted_cases, endpoint, api_spec.project, user_id, test_type_configs)
        
        # 构建最终结果
        return _build_endpoint_result(
            spec_id, endpoint_id, endpoint, test_cases, full_script, 
            save_result['created_cases'], result, None, user_id,
            save_result['save_errors'], save_result['cases_generated']
        )
        
    except (APISpecification.DoesNotExist, APIEndpoint.DoesNotExist) as e:
        return build_error_result(None, f'API规范或端点不存在: {str(e)}')
    except Exception as e:
        error_msg = f"端点测试用例生成过程中发生错误: {str(e)}"
        logger.error(error_msg, exc_info=True)
        return build_error_result(None, error_msg)


# ============ 智能场景生成任务 ============

@shared_task(bind=True, name='api_testing.tasks.generate_scenario_async')
def generate_scenario_async(self, project_id: int, user_request: str, user_id: int) -> Dict[str, Any]:
    """
    异步生成智能场景测试用例
    
    Args:
        project_id: 项目ID
        user_request: 用户场景描述
        user_id: 用户ID
    
    Returns:
        包含任务状态和结果的字典
    """
    # 使用支持WebSocket的任务执行框架（直接传递参数）
    return execute_async_task_with_websocket(
        self,
        'scenario_generation',
        _execute_scenario_generation,
        project_id, user_request, user_id
    )


def _execute_scenario_generation(task_instance, project_id: int, user_request: str, user_id: int) -> Dict[str, Any]:
    """
    执行智能场景生成逻辑
    
    Args:
        task_instance: Celery任务实例（用于进度更新）
        project_id: 项目ID
        user_request: 用户场景描述
        user_id: 用户ID
    
    Returns:
        生成结果
    """
    try:
        # 步骤1: 验证项目和资源
        update_task_progress(task_instance, 20, '正在验证项目权限和资源...')
        project, user = _validate_project_and_resources(project_id, user_id, require_api_spec=True)
        if not project:
            return build_error_result(None, '项目不存在或项目中没有API规范')
        
        # 步骤2: 初始化场景生成Agent
        update_task_progress(task_instance, 40, '正在初始化AI场景生成Agent...')
        try:
            from ai_core.api_scenario_agent import ScenarioGenerationAgent
            agent = ScenarioGenerationAgent(project_id=project_id, user_request=user_request, user_id=user_id)
        except ImportError as e:
            logger.error(f"导入场景生成Agent失败: {e}")
            return build_error_result(None, f'场景生成Agent初始化失败: {str(e)}')
        except Exception as e:
            logger.error(f"场景生成Agent初始化失败: {e}")
            return build_error_result(None, f'场景生成Agent初始化失败: {str(e)}')
        
        # 步骤3: 运行场景生成（包含保存测试用例）
        update_task_progress(task_instance, 70, '正在分析业务场景并生成测试用例...')
        try:
            logger.info(f"开始生成场景，项目ID: {project_id}, 用户描述: {user_request[:100]}...")
            
            # 调用Agent生成场景（Agent内部会保存测试用例）
            result = agent.run()
            
            logger.info(f"场景生成完成，结果: {result.get('success')}")
            
            # 检查场景生成是否成功
            if not result.get('success'):
                error_msg = result.get('error', '场景生成失败')
                logger.error(f"场景生成失败: {error_msg}")

                # 预检查属于“生成流程已完成但脚本未通过校验”的业务结果，
                # 仍通过 task_completed 返回报告，便于前端展示具体问题。
                validation_report = result.get('validation_report')
                if validation_report:
                    return {
                        'success': False,
                        'status': 'completed',
                        'message': '场景脚本自动修复后仍有问题，测试用例未保存；当前草稿已保留',
                        'error': error_msg,
                        'validation_report': validation_report,
                        'generated_script': result.get('generated_script', ''),
                        'test_case_id': None,
                        'task_id': None,
                        'user_id': user_id
                    }

                return build_error_result(None, f'AI场景生成失败: {error_msg}')
            
            # 构建最终结果（test_case_id 已由 Agent 保存并返回）
            return {
                'success': True,
                'status': 'completed',
                'message': '智能场景测试用例生成成功',
                'test_case_id': result.get('test_case_id'),
                'generated_script': result.get('generated_script', ''),
                'validation_report': result.get('validation_report'),
                'task_id': None,
                'user_id': user_id
            }
        
        except Exception as e:
            logger.error(f"场景生成过程中发生错误: {e}")
            return build_error_result(None, f'AI场景生成失败: {str(e)}')
        
    except Exception as e:
        error_msg = f"智能场景生成过程中发生错误: {str(e)}"
        logger.error(error_msg, exc_info=True)
        return build_error_result(None, error_msg)


# ============ 通用辅助函数 ============
def _build_endpoint_info(endpoint) -> Dict[str, Any]:
    """构建端点信息"""
    return {
        'path': endpoint.path,
        'method': endpoint.method,
        'summary': endpoint.summary,
        'description': endpoint.description,
        'parameters': endpoint.parameters or [],
        'request_body': endpoint.request_body or {},
        'responses': endpoint.responses or {},
        'tags': endpoint.tags or [],
        'operation_id': endpoint.operation_id
    }


def _build_endpoint_result(spec_id: int, endpoint_id: int, endpoint, test_cases, 
                          full_script: str, created_cases: List[APITestCase], result: Dict[str, Any], 
                          task_id: str, user_id: int, save_errors: List[str] = None, 
                          cases_generated: int = 0) -> Dict[str, Any]:
    """构建端点结果"""
    return {
        'success': True,
        'status': 'completed',
        'message': '端点测试用例生成成功',
        'spec_id': spec_id,
        'endpoint_id': endpoint_id,
        'endpoint_path': endpoint.path,
        'endpoint_method': endpoint.method,
        'test_cases': test_cases,
        'full_script': full_script,
        'total_cases': len(test_cases),
        'created_cases_count': len(created_cases),
        'cases_generated': cases_generated,
        'cases_saved': len(created_cases),
        'save_errors': save_errors or [],
        'generation_method': result.get('generation_method', 'unknown'),
        'model_info': result.get('model_info', {}),
        'task_id': task_id,
        'user_id': user_id
    }


# ============ API测试运行执行任务 ============

@shared_task(bind=True, name='api_testing.tasks.execute_api_test_case_async')
def execute_api_test_case_async(self, execution_id: int, test_case_id: int, environment_id: int) -> Dict[str, Any]:
    """
    异步执行API测试用例
    
    Args:
        execution_id: 执行记录ID
        test_case_id: 测试用例ID
        environment_id: 环境ID
    
    Returns:
        包含任务状态和结果的字典
    """
    return execute_async_task_with_progress(
        self,
        'API测试用例执行',
        _execute_api_test_case,
        execution_id, test_case_id, environment_id
    )


def _execute_api_test_case(task_instance, execution_id: int, test_case_id: int, environment_id: int) -> Dict[str, Any]:
    """
    执行API测试用例逻辑
    
    Args:
        task_instance: Celery任务实例（用于进度更新）
        execution_id: 执行记录ID
        test_case_id: 测试用例ID
        environment_id: 环境ID
    
    Returns:
        执行结果
    """
    try:
        from api_testing.models import APITestExecution, APITestCase, APITestCaseExecutionDetail
        from projects.models import Environment
        
        # 步骤1: 获取执行记录和测试用例
        update_task_progress(task_instance, 10, '正在获取执行记录和测试用例...')
        execution = APITestExecution.objects.get(id=execution_id)
        test_case = APITestCase.objects.get(id=test_case_id)
        environment = Environment.objects.get(id=environment_id)
        
        # 步骤2: 更新执行状态为运行中
        update_task_progress(task_instance, 20, '正在启动测试执行...')
        execution.status = 'running'
        execution.start_time = timezone.now()
        execution.save()
        
        # 更新用例执行详情状态
        case_detail = execution.case_execution_detail
        case_detail.status = 'running'
        case_detail.start_time = timezone.now()
        case_detail.save()
        
        # 步骤3: 执行测试用例
        update_task_progress(task_instance, 50, '正在执行API测试...')
        
        # 使用HttpRunner执行测试用例
        from api_testing.httprunner_runner import httprunner_runner
        from api_testing.runner import ScriptExecutor
        # 构建环境配置
        environment_config = environment.get_api_config() or {}
        if not environment_config:
            raise ValueError(f"环境 {environment.name} 不是API环境或配置无效")
        
        # 变量替换
        environment_vars = environment_config.get('variables', {}) or {}
        script_executor = ScriptExecutor(
            environment_vars=environment_vars,
            debug_context={"test_case_id": test_case.id}
        )
        script_content = script_executor.apply_variable_substitution(test_case.script_content or '')

        # 执行HttpRunner测试
        httprunner_result = httprunner_runner(
            script_id=str(test_case.id),
            script_content=script_content,
            base_url=environment_config['base_url'],
            options={
                **environment_config,
                'variables': environment_vars
            }
        )

        # 回写环境变量
        response_context = ScriptExecutor.extract_response_context(httprunner_result or {})
        try:
            if isinstance(environment_vars, dict):
                config = environment.config or {}
                variables = config.get('variables') or {}
                if not isinstance(variables, dict):
                    variables = {}
                variables.update(environment_vars)
                config['variables'] = variables
                environment.config = config
                environment.save(update_fields=['config'])
        except Exception as save_error:
            logger.warning(f"自动保存环境变量失败: {save_error}")
        # 步骤4: 更新执行结果
        update_task_progress(task_instance, 80, '正在更新执行结果...')
        
        # 根据HttpRunner结果更新执行状态
        step_datas = httprunner_result.get('step_datas', [])
        total_steps = len(step_datas) if step_datas else 1
        success_steps = sum(1 for step in step_datas if step.get('success', False)) if step_datas else 0
        failure_steps = sum(1 for step in step_datas if not step.get('success', True)) if step_datas else 0
        
        if httprunner_result.get('success', False):
            execution.status = 'passed'
            execution.total_steps = total_steps
            execution.success_steps = success_steps if success_steps > 0 else total_steps
            execution.failure_steps = failure_steps
            execution.error_steps = 0
        else:
            execution.status = 'failed'
            execution.total_steps = total_steps
            execution.success_steps = success_steps
            execution.failure_steps = failure_steps if failure_steps > 0 else 1
            execution.error_steps = 0
            # 尝试从多个位置提取错误信息
            error_msg = (
                httprunner_result.get('error') or 
                httprunner_result.get('result', {}).get('error') or
                '测试执行失败'
            )
            execution.error_message = error_msg
        
        execution.end_time = timezone.now()
        execution.duration = (execution.end_time - execution.start_time).total_seconds()
        execution.execution_log = httprunner_result.get('log', '')
        execution.save()
        
        # 更新用例执行详情
        case_detail.status = execution.status
        case_detail.end_time = execution.end_time
        case_detail.duration = execution.duration
        case_detail.error_message = execution.error_message
        case_detail.httprunner_result = json.dumps(httprunner_result, ensure_ascii=False)
        case_detail.log = httprunner_result.get('log', '')
        case_detail.save()
        
        # 步骤5: 构建结果
        update_task_progress(task_instance, 100, '测试执行完成')
        
        # 从httprunner_result中提取信息
        result_success = httprunner_result.get('success', False)
        result_time = httprunner_result.get('time', {})
        result_duration = result_time.get('duration', execution.duration) if result_time else execution.duration
        
        # 根据执行结果设置消息
        if result_success:
            message = 'API测试用例执行成功'
        else:
            # 尝试从多个位置提取错误信息
            error_msg = (
                httprunner_result.get('error') or 
                httprunner_result.get('result', {}).get('error') or
                execution.error_message or
                '测试执行失败'
            )
            # 如果错误信息太长，截取前500个字符
            if error_msg and len(error_msg) > 500:
                error_msg = error_msg[:500] + '...'
            message = f'API测试用例执行失败: {error_msg}'
        
        return {
            'success': result_success,
            'status': 'completed',
            'message': message,
            'execution_id': execution_id,
            'test_case_id': test_case_id,
            'test_case_name': test_case.title,
            'environment_name': environment.name,
            'execution_status': execution.status,
            'duration': result_duration,
            'task_id': None,
            'user_id': execution.executor.id,
            # 添加httprunner_result的详细信息
            'httprunner_result': {
                'case_id': httprunner_result.get('case_id'),
                'name': httprunner_result.get('name'),
                'total_steps': total_steps,
                'success_steps': success_steps,
                'failure_steps': failure_steps,
                'error': httprunner_result.get('error') or httprunner_result.get('result', {}).get('error'),
                'error_type': httprunner_result.get('error_type'),
                'has_step_datas': len(step_datas) > 0,
                'duration': result_duration,
                'log': httprunner_result.get('log') or httprunner_result.get('result', {}).get('log', '')
            }
        }
        
    except Exception as e:
        error_msg = f"API测试用例执行过程中发生错误: {str(e)}"
        logger.error(error_msg, exc_info=True)
        
        # 更新执行状态为失败
        try:
            execution = APITestExecution.objects.get(id=execution_id)
            execution.status = 'failed'
            execution.end_time = timezone.now()
            execution.duration = max(
                0.0,
                (execution.end_time - (execution.start_time or execution.end_time)).total_seconds(),
            )
            execution.error_message = error_msg
            execution.save()
            
            # 更新用例执行详情
            case_detail = execution.case_execution_detail
            case_detail.status = 'failed'
            case_detail.end_time = timezone.now()
            case_detail.duration = execution.duration
            case_detail.error_message = error_msg
            case_detail.save()
        except:
            pass
        
        return build_error_result(None, error_msg)


@shared_task(bind=True, name='api_testing.tasks.execute_api_test_suite_async')
def execute_api_test_suite_async(
    self,
    execution_id: int,
    test_suite_id: int,
    environment_id: int,
    task_execution_log_id: int = None,
) -> Dict[str, Any]:
    """
    异步执行API测试套件
    
    Args:
        execution_id: 执行记录ID
        test_suite_id: 测试套件ID
        environment_id: 环境ID
        task_execution_log_id: 定时任务执行日志ID（由定时任务中心调用时传入，用于完成后回填通过/失败统计）
    
    Returns:
        包含任务状态和结果的字典
    """
    return execute_async_task_with_progress(
        self,
        'API测试套件执行',
        _execute_api_test_suite,
        execution_id, test_suite_id, environment_id, task_execution_log_id
    )


def _execute_api_test_suite(
    task_instance,
    execution_id: int,
    test_suite_id: int,
    environment_id: int,
    task_execution_log_id: int = None,
) -> Dict[str, Any]:
    """
    执行API测试套件逻辑
    
    Args:
        task_instance: Celery任务实例（用于进度更新）
        execution_id: 执行记录ID
        test_suite_id: 测试套件ID
        environment_id: 环境ID
        task_execution_log_id: 定时任务执行日志ID，完成后回填通过/失败统计
    
    Returns:
        执行结果
    """
    try:
        from api_testing.models import APITestExecution, APITestSuite, APITestSuiteExecutionDetail, APITestSuiteCaseExecution
        from projects.models import Environment
        
        # 步骤1: 获取执行记录和测试套件
        update_task_progress(task_instance, 10, '正在获取执行记录和测试套件...')
        execution = APITestExecution.objects.get(id=execution_id)
        test_suite = APITestSuite.objects.get(id=test_suite_id)
        environment = Environment.objects.get(id=environment_id)
        
        # 步骤2: 更新执行状态为运行中
        update_task_progress(task_instance, 20, '正在启动套件执行...')
        execution.status = 'running'
        execution.start_time = timezone.now()
        execution.save()
        
        # 更新套件执行详情状态
        suite_detail = execution.suite_execution_detail
        suite_detail.start_time = timezone.now()
        suite_detail.save()
        
        # 步骤3: 准备测试用例数据
        update_task_progress(task_instance, 30, '正在准备测试用例数据...')
        
        total_cases = test_suite.test_cases.count()
        passed_cases = 0
        failed_cases = 0
        
        # 每个用例只执行一次；持久化的结果直接作为平台报告数据源。
        from api_testing.httprunner_runner import httprunner_runner
        from api_testing.runner import ScriptExecutor
        
        # 构建环境配置
        environment_config = environment.get_api_config() or {}
        if not environment_config:
            raise ValueError(f"环境 {environment.name} 不是API环境或配置无效")
        
        def normalize_key(key):
            if not isinstance(key, str):
                return key
            cleaned = key.strip()
            if cleaned.startswith('{{') and cleaned.endswith('}}'):
                cleaned = cleaned[2:-2].strip()
            return cleaned

        def _extract_value(value):
            if isinstance(value, dict):
                if 'value' in value:
                    return value.get('value')
                if 'val' in value:
                    return value.get('val')
            return value

        def normalize_variables(variables):
            if isinstance(variables, dict):
                normalized = {}
                for key, value in variables.items():
                    cleaned_key = normalize_key(key)
                    normalized[cleaned_key] = _extract_value(value)
                return normalized
            if isinstance(variables, str):
                try:
                    parsed = json.loads(variables)
                    if isinstance(parsed, dict):
                        return normalize_variables(parsed)
                except Exception:
                    return {}
            if isinstance(variables, list):
                result = {}
                for item in variables:
                    if not isinstance(item, dict):
                        continue
                    key = item.get('key') or item.get('name')
                    if key is None:
                        continue
                    cleaned_key = normalize_key(key)
                    result[cleaned_key] = _extract_value(item.get('value'))
                return result
            return {}

        # 准备测试用例执行顺序
        order_list = test_suite.test_case_order or []
        order_map = {case_id: index for index, case_id in enumerate(order_list)}
        ordered_cases = sorted(
            list(test_suite.test_cases.all()),
            key=lambda case: (order_map.get(case.id, 10**9), case.id)
        )

        # 共享环境变量（前置/后置脚本会持续更新）
        environment_vars = normalize_variables(environment_config.get('variables', {}) or {})

        # 步骤4: 按顺序逐个执行测试用例（始终用 httprunner_runner 保证统计可靠）
        update_task_progress(task_instance, 50, '正在执行测试套件...')

        try:
            all_logs = []
            all_logs.append(f"\n{'='*80}\n套件执行日志\n{'='*80}\n")

            for index, test_case in enumerate(ordered_cases, start=1):
                update_task_progress(
                    task_instance,
                    50 + int((index / max(total_cases, 1)) * 30),
                    f'正在执行用例 {index}/{total_cases}: {test_case.title}'
                )

                case_execution = suite_detail.case_executions.get(test_case=test_case)
                case_execution.status = 'running'
                case_execution.start_time = timezone.now()
                case_execution.save()

                reserved_keys = {'path_params', 'query_params', 'body'}
                case_vars = normalize_variables(getattr(test_case, 'variables', {}) or {})
                if case_vars:
                    for key, value in case_vars.items():
                        if key in reserved_keys:
                            continue
                        if key not in environment_vars or environment_vars.get(key) in (None, ''):
                            environment_vars[key] = value

                script_executor = ScriptExecutor(
                    environment_vars=environment_vars,
                    debug_context={"test_suite_id": test_suite_id, "test_case_id": test_case.id}
                )

                try:
                    script_content = script_executor.apply_variable_substitution(test_case.script_content or '')
                    headers = environment_config.get('headers', {}) or {}
                    headers, _ = script_executor._walk_and_replace(headers)

                    result = httprunner_runner(
                        script_id=str(test_case.id),
                        script_content=script_content,
                        base_url=environment_config['base_url'],
                        options={
                            **environment_config,
                            'headers': headers,
                            'variables': environment_vars,
                        },
                    )
                    case_execution.status = 'passed' if result.get('success', False) else 'failed'
                    case_execution.error_message = result.get('error', '') if case_execution.status == 'failed' else ''
                    case_execution.httprunner_result = json.dumps(result or {}, ensure_ascii=False)
                    case_execution.log = result.get('log', '')

                except Exception as case_error:
                    case_execution.status = 'failed'
                    case_execution.error_message = str(case_error)
                    case_execution.httprunner_result = json.dumps({'error': str(case_error)}, ensure_ascii=False)

                case_execution.end_time = timezone.now()
                case_execution.duration = (case_execution.end_time - case_execution.start_time).total_seconds()
                case_execution.save()

                all_logs.append(f"\n{'='*80}\n")
                all_logs.append(f"测试用例: {test_case.title} (ID: {test_case.id})\n")
                all_logs.append(f"{'='*80}\n")
                all_logs.append(f"状态: {case_execution.status}\n")
                if case_execution.error_message:
                    all_logs.append(f"错误信息: {case_execution.error_message}\n")
                if case_execution.log:
                    all_logs.append(f"\n{case_execution.log}\n")
                if script_executor.console_logs:
                    all_logs.append(f"\n脚本输出: {script_executor.console_logs}\n")
                all_logs.append("\n")

                if case_execution.status == 'passed':
                    passed_cases += 1
                else:
                    failed_cases += 1

            # 保存环境变量回写到环境配置
            try:
                config = environment.config or {}
                variables = config.get('variables') or {}
                if not isinstance(variables, dict):
                    variables = {}
                safe_vars = {k: v for k, v in environment_vars.items() if k not in {'path_params', 'query_params', 'body'}}
                variables.update(safe_vars)
                config['variables'] = variables
                environment.config = config
                environment.save(update_fields=['config'])
            except Exception as save_error:
                logger.warning(f"自动保存环境变量失败: {save_error}")

        except Exception as e:
            # 套件执行异常
            error_msg = f'套件执行异常: {str(e)}'
            logger.error(f"执行测试套件 {test_suite.name} 时发生异常: {e}", exc_info=True)
            
            # 初始化日志列表（如果异常发生在日志初始化之前）
            if 'all_logs' not in locals():
                all_logs = []
            
            # 将所有用例标记为失败
            for test_case in test_suite.test_cases.all():
                case_execution = suite_detail.case_executions.get(test_case=test_case)
                case_execution.status = 'failed'
                case_execution.error_message = error_msg
                case_execution.end_time = timezone.now()
                case_execution.duration = (case_execution.end_time - case_execution.start_time).total_seconds()
                case_execution.httprunner_result = json.dumps({'error': error_msg}, ensure_ascii=False)
                case_execution.save()
                failed_cases += 1
            
            all_logs.append(f"\n{'='*80}\n")
            all_logs.append(f"套件执行异常: {error_msg}\n")
            all_logs.append(f"{'='*80}\n")
        
        # 步骤6: 更新套件执行结果
        update_task_progress(task_instance, 90, '正在更新套件执行结果...')
        
        # 根据执行结果设置状态
        if failed_cases == 0:
            execution.status = 'passed'
        else:
            execution.status = 'failed'
        
        execution.end_time = timezone.now()
        execution.duration = (execution.end_time - execution.start_time).total_seconds()
        execution.total_steps = total_cases
        execution.success_steps = passed_cases
        execution.failure_steps = failed_cases
        execution.error_steps = 0
        execution.save()
        
        # 更新套件执行详情
        suite_detail.end_time = timezone.now()
        suite_detail.duration = execution.duration
        suite_detail.total_cases = total_cases
        suite_detail.passed_cases = passed_cases
        suite_detail.failed_cases = failed_cases
        suite_detail.skipped_cases = 0
        # 保存汇总的执行日志
        suite_detail.log = ''.join(all_logs) if all_logs else ''
        suite_detail.save()
        
        # 定时报告从派发时关联的真实执行记录聚合；串行控制器仅在本
        # 套件终态后安排下一套件，并在全部结束时统一通知。
        if task_execution_log_id:
            try:
                from scheduled_tasks.scheduling import finish_scheduled_suite
                finish_scheduled_suite(
                    task_execution_log_id, execution_id,
                    append_log=(suite_detail.log or '').strip(),
                )
            except Exception as e:
                logger.warning('回填定时任务日志或推进串行套件失败: task_execution_log_id=%s, error=%s', task_execution_log_id, e)

        # 步骤6: 构建结果
        update_task_progress(task_instance, 100, '套件执行完成')
        
        # 无论成功还是失败，都返回任务完成状态，success 始终为 True
        # 通过 execution_status 字段区分成功和失败
        execution_passed = (execution.status == 'passed')
        if execution_passed:
            message = f'API测试套件执行成功，共执行 {total_cases} 个用例，全部通过'
        else:
            message = f'API测试套件执行完成，共执行 {total_cases} 个用例，{failed_cases} 个失败'
        
        return {
            'success': True,  # 无论成功还是失败，都返回 True 表示任务已完成
            'status': 'completed',  # 无论成功还是失败，都返回 completed
            'message': message,
            'execution_id': execution_id,
            'test_suite_id': test_suite_id,
            'test_suite_name': test_suite.name,
            'environment_name': environment.name,
            'execution_status': execution.status,  # 通过此字段查看实际执行状态：passed/failed
            'duration': execution.duration,
            # 前端弹窗需要的 4 个统计字段（键名与前端解析严格一致）
            'total_cases': total_cases,
            'passed_cases': passed_cases,
            'failed_cases': failed_cases,
            'skipped_cases': 0,
            'task_id': None,
            'user_id': execution.executor.id
        }
        
    except Exception as e:
        error_msg = f"API测试套件执行过程中发生错误: {str(e)}"
        logger.error(error_msg, exc_info=True)
        
        # 更新执行状态为失败
        try:
            execution = APITestExecution.objects.get(id=execution_id)
            execution.status = 'failed'
            execution.end_time = timezone.now()
            execution.duration = max(
                0.0,
                (execution.end_time - (execution.start_time or execution.end_time)).total_seconds(),
            )
            execution.error_message = error_msg
            execution.save()
            
            # 更新套件执行详情
            suite_detail = execution.suite_execution_detail
            suite_detail.end_time = timezone.now()
            suite_detail.duration = execution.duration
            suite_detail.save()
            if task_execution_log_id:
                try:
                    from scheduled_tasks.scheduling import finish_scheduled_suite
                    finish_scheduled_suite(task_execution_log_id, execution_id, append_log=error_msg)
                except Exception:
                    logger.warning('异常后推进串行定时任务失败: task_execution_log_id=%s', task_execution_log_id, exc_info=True)
            
            # 返回任务完成状态，success 为 True
            return {
                'success': True,  # 即使发生异常，也返回 True 表示任务已完成
                'status': 'completed',
                'message': f'API测试套件执行完成（发生错误）: {error_msg}',
                'execution_id': execution_id,
                'test_suite_id': test_suite_id if 'test_suite_id' in locals() else None,
                'test_suite_name': test_suite.name if 'test_suite' in locals() else '未知',
                'environment_name': environment.name if 'environment' in locals() else '未知',
                'execution_status': 'failed',
                'duration': execution.duration,
                'total_cases': 0,
                'passed_cases': 0,
                'failed_cases': 0,
                'skipped_cases': 0,
                'task_id': None,
                'user_id': execution.executor.id if execution.executor else None,
                'error': error_msg
            }
        except Exception as inner_e:
            logger.error(f"更新执行状态失败: {str(inner_e)}", exc_info=True)
            if task_execution_log_id:
                try:
                    from scheduled_tasks.scheduling import finish_scheduled_suite
                    # The execution may have been deleted after it was
                    # planned.  The scheduler treats that as a terminal
                    # execution-level error and can still advance safely.
                    finish_scheduled_suite(task_execution_log_id, execution_id, append_log=error_msg)
                except Exception:
                    logger.warning('缺失执行记录后推进串行定时任务失败: task_execution_log_id=%s', task_execution_log_id, exc_info=True)
            # 即使更新状态失败，也返回任务完成
            return {
                'success': True,
                'status': 'completed',
                'message': f'API测试套件执行完成（发生错误）: {error_msg}',
                'execution_id': execution_id,
                'execution_status': 'failed',
                'total_cases': 0,
                'passed_cases': 0,
                'failed_cases': 0,
                'skipped_cases': 0,
                'error': error_msg
            }
