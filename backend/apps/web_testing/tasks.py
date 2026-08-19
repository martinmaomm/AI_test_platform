"""
Web Testing Celery异步任务
统一管理WebUI测试、MidScene脚本生成和执行等任务
"""
import json
import logging
import asyncio
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from celery import shared_task
from django.core.cache import cache
from typing import Dict, Any, Optional
from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone
from datetime import timedelta

# 导入模型
from .models import (
    MidSceneScript, WebUITestCase, WebUITestExecution,
    WebUITestCaseExecutionDetail, WebUITestSuiteExecutionDetail, WebUITestSuiteCaseExecution,
    WebPage, WebElement, WebUITestModule
)
from projects.models import Project

# 导入智能体
from ai_core.midscene_script_agent import create_midscene_agent

# 导入工具函数
from common.task import (
    execute_async_task_with_websocket,
    execute_async_task_with_progress,
    update_task_progress,
    update_task_success,
    build_error_result
)
from celery.result import AsyncResult

User = get_user_model()
logger = logging.getLogger(__name__)


# ============ WebUI测试脚本生成任务 ============

@shared_task(bind=True, name='web_testing.generate_webui_test_script')
def generate_webui_test_script_task(self, script_name: str, description: str, url: str, user_id: int, project_id: int, mcp_config: dict = None):
    """
    生成WebUI测试脚本的异步任务
    
    Args:
        script_name: 脚本名称
        description: 测试描述
        url: 目标URL
        user_id: 用户ID
        project_id: 项目ID
        mcp_config: MCP配置
    
    Returns:
        Dict: 任务执行结果
    """
    return execute_async_task_with_websocket(
        self,
        'webui_auto_test',
        _execute_webui_script_generation,
        script_name, description, url, user_id, project_id, mcp_config or {}
    )


def _execute_webui_script_generation(task_instance, script_name: str, description: str, url: str, user_id: int, project_id: int, mcp_config: dict) -> Dict[str, Any]:
    """执行WebUI测试脚本生成逻辑"""
    try:
        update_task_progress(task_instance, 10, '正在获取用户和项目信息...')
        User.objects.get(id=user_id)
        Project.objects.get(id=project_id)
        
        update_task_progress(task_instance, 20, '正在初始化WebUI智能体...')
        update_task_progress(task_instance, 50, '正在生成WebUI测试脚本...')
        
        from ai_core.webui_playwright_agent import WebUIPlaywrightAgent
        agent = WebUIPlaywrightAgent(user_id=user_id)
        agent.project_id = project_id
        agent.script_name = script_name
        agent.mcp_config = mcp_config
        # 传入Celery任务ID，供智能体内部协作式取消
        agent.celery_task_id = task_instance.request.id
        
        result = asyncio.run(agent.run(description, url))
        
        update_task_progress(task_instance, 80, '正在处理生成结果...')
        
        # 检查是否是因为取消
        if result.get('cancelled'):
            return {
                'success': False,
                'status': 'cancelled',
                'message': '任务已被取消',
                'error': '任务已被取消',
                'task_id': task_instance.request.id
            }
        
        if result.get('success'):
            return {
                'success': True,
                'status': 'completed',
                'message': 'WebUI测试脚本生成成功',
                'script_id': result.get('script_id'),
                'test_script': result.get('test_script'),
                'model_info': result.get('model_info', {}),
                'model_type': result.get('model_type', 'unknown')
            }
        else:
            return build_error_result(None, result.get('error', '未知错误'))
            
    except (User.DoesNotExist, Project.DoesNotExist) as e:
        logger.error(f"资源不存在: {str(e)}")
        return build_error_result(None, f"资源不存在: {str(e)}")
    except Exception as e:
        logger.error(f"WebUI测试脚本生成任务异常: {str(e)}", exc_info=True)
        return build_error_result(None, f"WebUI测试脚本生成任务异常: {str(e)}")


@shared_task(bind=True, name='web_testing.generate_webui_test_script_from_testcase')
def generate_webui_test_script_from_testcase_task(self, test_case_id: int, user_id: int, project_id: int, environment_id: int = None, mcp_config: dict = None):
    """
    基于测试用例生成WebUI测试脚本的异步任务
    
    Args:
        test_case_id: 测试用例ID
        user_id: 用户ID
        project_id: 项目ID
        environment_id: 环境变量ID（可选）
        mcp_config: MCP配置
    
    Returns:
        Dict: 任务执行结果
    """
    return execute_async_task_with_websocket(
        self,
        'webui_auto_test',
        _execute_webui_script_generation_from_testcase,
        test_case_id, user_id, project_id, environment_id, mcp_config or {}
    )


def _execute_webui_script_generation_from_testcase(task_instance, test_case_id: int, user_id: int, project_id: int, environment_id: int = None, mcp_config: dict = None) -> Dict[str, Any]:
    """基于测试用例执行WebUI测试脚本生成逻辑"""
    try:
        update_task_progress(task_instance, 10, '正在获取用户、项目和测试用例信息...')
        user = User.objects.get(id=user_id)
        project = Project.objects.get(id=project_id)
        test_case = WebUITestCase.objects.get(id=test_case_id, user=user)
        
        environment_url = None
        if environment_id:
            from projects.models import Environment
            environment = Environment.objects.get(id=environment_id, project=project)
            environment_url = environment.get_web_config()['base_url']
        
        update_task_progress(task_instance, 20, '正在初始化WebUI智能体...')
        update_task_progress(task_instance, 50, '正在基于测试用例生成WebUI测试脚本...')
        
        from ai_core.webui_playwright_agent import WebUIPlaywrightAgent
        agent = WebUIPlaywrightAgent(user_id=user_id)
        agent.project_id = project_id
        agent.script_name = f"{test_case.title}_脚本"
        agent.mcp_config = mcp_config
        agent.test_case_id = test_case_id
        # 传入Celery任务ID，供智能体内部协作式取消
        agent.celery_task_id = task_instance.request.id
        
        test_description = f"""
测试用例标题: {test_case.title}
测试用例描述: {test_case.description}
测试步骤:
{json.dumps(test_case.steps, ensure_ascii=False, indent=2)}
预期结果: {test_case.expected_result}
"""
        steps_info = json.dumps(test_case.steps, ensure_ascii=False, indent=2)
        expected_result = (test_case.expected_result or '').strip()
        logger.info(
            f"[Celery] 测试用例脚本生成 task_id={task_instance.request.id} "
            f"test_case_id={test_case_id} expected_result_len={len(expected_result)} steps_count={len(test_case.steps) or 0}"
        )
        
        result = asyncio.run(agent.run(
            test_description,
            environment_url or test_case.url or '',
        ))
        
        logger.info(
            f"[Celery] 测试用例脚本生成完成 task_id={task_instance.request.id} "
            f"success={result.get('success')} has_script={bool(result.get('test_script'))}"
        )
        update_task_progress(task_instance, 90, '正在处理生成结果...')
        
        # 检查是否是因为取消
        if result and result.get('cancelled'):
            return {
                'success': False,
                'status': 'cancelled',
                'message': '任务已被取消',
                'error': '任务已被取消',
                'task_id': task_instance.request.id
            }
        
        if result and result.get('success'):
            update_task_progress(task_instance, 100, 'WebUI测试脚本生成完成')
            return update_task_success(task_instance, 'WebUI测试脚本生成完成', result)
        else:
            error_msg = result.get('error', '未知错误') if result else 'agent.run返回了None'
            logger.error(f"WebUI脚本生成失败: {error_msg}")
            return build_error_result(None, error_msg)
        
    except Exception as e:
        error_msg = f"基于测试用例的WebUI测试脚本生成任务异常: {str(e)}"
        logger.error(error_msg, exc_info=True)
        return build_error_result(None, error_msg)


# ============ WebUI测试执行任务 ============

@shared_task(bind=True, name='web_testing.execute_webui_test')
def execute_webui_test_task(self, execution_id: int, user_id: int):
    """
    执行WebUI测试的异步任务
    
    Args:
        execution_id: WebUI测试执行记录ID
        user_id: 用户ID
    
    Returns:
        Dict: 任务执行结果
    """
    return execute_async_task_with_websocket(
        self,
        'webui_test_execution',
        _execute_webui_test_logic,
        execution_id, user_id
    )


def _execute_webui_test_logic(task_instance, execution_id: int, user_id: int) -> Dict[str, Any]:
    """
    执行WebUI测试逻辑
    """
    try:
        # 步骤1: 获取执行记录和脚本
        update_task_progress(task_instance, 10, '正在获取测试信息...')
        execution = WebUITestExecution.objects.get(id=execution_id)
        script = execution.script
        
        # 更新执行状态
        execution.task_id = task_instance.request.id
        execution.status = 'running'
        execution.started_at = timezone.now()
        execution.save()
        
        # 步骤2: 执行测试脚本
        update_task_progress(task_instance, 30, '正在执行WebUI测试...')
        
        # 使用Playwright执行器执行脚本
        execution_result = _run_test_script(script.test_script_content, script.url, script.project_id, None)
        
        # 步骤3: 保存执行结果
        update_task_progress(task_instance, 80, '正在保存执行结果...')
        
        # 获取执行结果详情
        result_data = execution_result.get('result', {})
        allure_report = result_data.get('allure_report', '')
        stdout = result_data.get('stdout', '')
        stderr = result_data.get('stderr', '')
        
        # 构建完整的执行日志
        execution_log = f"=== 测试执行日志 ===\n"
        if stdout:
            execution_log += f"\n--- 标准输出 ---\n{stdout}\n"
        if stderr:
            execution_log += f"\n--- 错误输出 ---\n{stderr}\n"
        
        # 更新执行记录
        execution.completed_at = timezone.now()
        if execution.started_at:
            duration = (execution.completed_at - execution.started_at).total_seconds()
            execution.duration = duration
        
        # 保存日志和报告路径
        if allure_report:
            execution.report_path = allure_report
        execution.log_path = result_data.get('test_file', '')  # 保存工作目录路径
        
        if execution_result.get('success'):
            execution.status = 'passed'
            execution.save()
            
            logger.info(f"WebUI测试执行完成: {task_instance.request.id}")
            return {
                'success': True,
                'status': 'completed',
                'message': 'WebUI测试执行成功',
                'execution_id': execution_id,
                'result': result_data,
                'log': execution_log
            }
        else:
            execution.status = 'failed'
            execution.error_message = execution_result.get('error', '测试执行失败')
            execution.save()
            
            logger.error(f"WebUI测试执行失败: {task_instance.request.id}, error: {execution_result.get('error')}")
            return {
                'success': False,
                'status': 'completed',  # 任务执行完成，只是结果失败
                'message': f'WebUI测试执行失败: {execution_result.get("error", "未知错误")}',
                'error': execution_result.get('error', '未知错误'),
                'execution_id': execution_id,
                'result': result_data,
                'log': execution_log
            }
            
    except WebUITestExecution.DoesNotExist as e:
        error_msg = f"测试执行记录不存在: {str(e)}"
        logger.error(error_msg)
        return build_error_result(None, error_msg)
        
    except Exception as e:
        error_msg = f"WebUI测试执行任务异常: {str(e)}"
        logger.error(error_msg, exc_info=True)
        
        # 更新失败状态
        try:
            execution = WebUITestExecution.objects.get(id=execution_id)
            execution.status = 'failed'
            execution.error_message = str(e)
            execution.completed_at = timezone.now()
            execution.save()
        except:
            pass
        
        return build_error_result(None, error_msg)




def _run_test_suite_from_db_workspace(
    project_id: int,
    test_cases_data: list,
    base_url: str = None,
    options: dict = None,
) -> Dict[str, Any]:
    """
    从数据库搬运代码到标准化目录，然后执行 Pytest。
    不调用代码生成器，直接使用 WebPage.pom_code 和 WebUITestCase.test_script_content。
    """
    try:
        from .playwright_python_runner import PlaywrightRunner, ExecutionConfig
        import uuid

        # 1. 创建临时工作目录
        project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        temp_base = os.path.join(project_root, 'playwright_workspace')
        os.makedirs(temp_base, exist_ok=True)
        suite_id = str(uuid.uuid4())
        work_dir = tempfile.mkdtemp(prefix=f'playwright_suite_{suite_id}_', dir=temp_base)

        # 2. 搬运 POM + 测试脚本到标准化目录（注入 base_url 用于动态替换 goto）
        suite_name = (options or {}).get('suite_name', '')
        base_url_val = base_url or "http://mall.lemonban.com:3344"
        base_url_val = (base_url_val or '').rstrip('/') or "http://mall.lemonban.com:3344"
        test_files, skipped_results = _build_suite_workspace_from_db(
            project_id=project_id,
            test_cases_data=test_cases_data,
            work_dir=work_dir,
            suite_name=suite_name,
            base_url=base_url_val,
        )

        # 3. 统计 .py 文件数量，若为 0 则直接返回错误
        py_files = [f for f in os.listdir(work_dir) if f.endswith('.py')]
        py_count = len(py_files)
        logger.info(f"[套件执行] 临时目录 {work_dir} 下 .py 文件数量: {py_count}, 列表: {py_files}")

        if py_count == 0:
            error_msg = "脚本生成失败，未发现可执行的测试文件"
            logger.error(f"[套件执行] {error_msg}，跳过 Pytest 启动")
            return {
                'success': False,
                'error': error_msg,
                'result': {
                    'stdout': '',
                    'stderr': error_msg,
                    'case_results': skipped_results,
                },
            }

        # 4. 运行前打印目录内容，确保文件已成功写入
        all_entries = os.listdir(work_dir)
        logger.info(f"[套件执行] Pytest 启动前 workspace 内容: {all_entries}")

        # 5. 执行 Pytest（复用 PlaywrightRunner 的 pytest 逻辑）
        config = ExecutionConfig(
            browser=(options or {}).get('browser', 'chromium'),
            headed=(options or {}).get('headed', True),
            timeout=(options or {}).get('timeout', 300),
            generate_allure=True,
            base_url=base_url,
            suite_name=suite_name,
        )
        runner = PlaywrightRunner()
        result = runner._run_pytest_command(work_dir, config)

        # 6. 解析结果、生成 Allure 报告、清理
        allure_report_path = runner._generate_report(work_dir, config) if config.generate_allure else None
        parsed_case_results = runner._parse_suite_test_results(result.stdout, test_cases_data)
        all_case_results = skipped_results + parsed_case_results
        execution_result = runner._build_execution_result(
            result, work_dir, allure_report_path, config, all_case_results
        )
        runner._cleanup_work_dir(work_dir, execution_result.allure_report)

        return {
            'success': execution_result.success,
            'stdout': execution_result.stdout,
            'stderr': execution_result.stderr,
            'error': execution_result.stderr if not execution_result.success else None,
            'return_code': execution_result.return_code,
            'allure_report': execution_result.allure_report,
            'case_results': execution_result.case_results,
        }
    except Exception as e:
        logger.error(f"套件执行异常: {e}", exc_info=True)
        return {
            'success': False,
            'error': str(e),
            'result': {'case_results': []},
        }


def _run_test_suite_script(test_cases_data: list, base_url: str = None, options: dict = None) -> Dict[str, Any]:
    """
    运行测试套件脚本（批量执行多个测试用例并生成Allure报告）
    已重构为直接搬运数据库资产，不调用代码生成器。
    
    Args:
        test_cases_data: 测试用例数据列表，每个元素包含 {'test_case_id': int, 'script_content': str, 'test_case_title': str}
        base_url: 基础URL
        options: 执行选项，需包含 project_id 和 suite_name
    
    Returns:
        Dict: 执行结果
    """
    try:
        project_id = (options or {}).get('project_id')
        if not project_id:
            return {
                'success': False,
                'error': '缺少 project_id',
                'result': {'case_results': []},
            }

        result = _run_test_suite_from_db_workspace(
            project_id=project_id,
            test_cases_data=test_cases_data,
            base_url=base_url,
            options=options,
        )

        suite_id = str((options or {}).get('suite_id', ''))
        if result.get('success'):
            logger.info(f"Playwright测试套件执行成功: {suite_id}")
            return {
                'success': True,
                'result': {
                    'status': 'passed',
                    'suite_id': suite_id,
                    'stdout': result.get('stdout', ''),
                    'stderr': result.get('stderr', ''),
                    'test_files': [],
                    'return_code': result.get('return_code', 0),
                    'allure_report': result.get('allure_report', ''),
                    'case_results': result.get('case_results', [])
                },
                'log': result.get('stdout', '') if result.get('stdout') else '测试套件执行完成'
            }
        else:
            logger.error(f"Playwright测试套件执行失败: {suite_id}, error: {result.get('error')}")
            return {
                'success': False,
                'error': result.get('error', 'Playwright测试套件执行失败'),
                'result': {
                    'status': 'failed',
                    'suite_id': suite_id,
                    'stdout': result.get('stdout', ''),
                    'stderr': result.get('stderr', ''),
                    'test_files': [],
                    'return_code': result.get('return_code', 1),
                    'allure_report': result.get('allure_report', ''),
                    'case_results': result.get('case_results', [])
                },
                'log': result.get('stderr', '') if result.get('stderr') else '测试套件执行失败'
            }

    except Exception as e:
        logger.error(f"测试套件脚本执行失败: {e}")
        return {
            'success': False,
            'error': str(e)
        }


def _run_sandbox_test_script(script_content: str, project_id: int, base_url: str = None) -> dict:
    """
    构建本地沙箱，动态注入 POM 并强制弹出浏览器执行。
    当 base_url 存在时，注入标准的 BrowserContext 以支持相对路径导航。
    """
    try:
        # 1. 强制激活本地执行入口与可视化模式，并加入慢动作(500ms)
        script_content = re.sub(
            r'headless\s*=\s*(True|False)',
            'headless=False, slow_mo=500',
            script_content,
        )
        if "asyncio.run(main())" not in script_content or "# 如需直接运行" in script_content:
            script_content += "\n\nif __name__ == '__main__':\n    import asyncio\n    asyncio.run(main())\n"

        # 2. 注入标准的 BrowserContext 基础 URL，并强制兜底访问根路径
        escaped_url = base_url.replace('\\', '\\\\').replace("'", "\\'") if base_url else ""

        def _inject_context(match):
            indent = match.group(1)
            if base_url:
                return (
                    f"{indent}context = await browser.new_context(base_url='{escaped_url}')\n"
                    f"{indent}page = await context.new_page()\n"
                    f"{indent}await page.goto('/')  # 框架级兜底导航"
                )
            return f"{indent}page = await browser.new_page()"

        script_content = re.sub(
            r'^(\s*)page\s*=\s*await\s+browser\.new_page\(\)',
            _inject_context,
            script_content,
            count=1,
            flags=re.MULTILINE,
        )

        # 3. 在关闭浏览器前强制停留 3 秒，便于观察最后一步
        script_content = re.sub(
            r'^(\s*)await browser\.close\(\)',
            r'\1await page.wait_for_timeout(3000)\n\1await browser.close()',
            script_content,
            count=1,
            flags=re.MULTILINE,
        )

        # 4. 解析需要的 Page 类
        import_pattern = re.compile(r'from\s+pages\.(\w+)\s+import\s+(\w+)')
        imports_found = import_pattern.findall(script_content)

        with tempfile.TemporaryDirectory() as tmpdirname:
            base_path = Path(tmpdirname)
            pages_dir = base_path / "pages"
            pages_dir.mkdir()
            (pages_dir / "__init__.py").touch()

            # 5. 从数据库提取最新的 POM 代码写入沙箱
            for module_name, class_name in imports_found:
                page = WebPage.objects.filter(project_id=project_id, page_class_name=class_name).first()
                if page and page.pom_code:
                    page_file = pages_dir / f"{module_name}.py"
                    page_file.write_text(page.pom_code, encoding='utf-8')

            # 6. 写入测试用例主脚本
            test_file = base_path / "run_test.py"
            test_file.write_text(script_content, encoding='utf-8')

            # 7. 启动子进程执行 (注入 PYTHONPATH)
            env = os.environ.copy()
            env['PYTHONPATH'] = str(base_path)

            logger.info(f"在本地沙箱中启动测试: {base_path}")
            process = subprocess.run(
                ['python', 'run_test.py'],
                cwd=str(base_path),
                env=env,
                capture_output=True,
                text=True,
                encoding='utf-8'  # 解决 Windows 下的 GBK 解码报错
            )

            success = process.returncode == 0
            return {
                'success': success,
                'result': {
                    'stdout': process.stdout,
                    'stderr': process.stderr,
                    'test_file': str(test_file)
                },
                'error': process.stderr if not success else '',
                'log': process.stdout if success else (process.stderr or '执行失败')
            }
    except Exception as e:
        logger.error(f"沙箱执行异常: {e}")
        return {'success': False, 'error': str(e)}


def _run_test_script(script_content: str, base_url: str = None, options: dict = None) -> Dict[str, Any]:
    """
    运行测试脚本（使用Playwright执行器）
    """
    try:
        from .playwright_python_runner import playwright_runner
        import uuid
        
        # 生成测试ID
        script_id = str(uuid.uuid4())
        
        # 执行Playwright脚本
        logger.info(f"开始执行Playwright测试: {script_id}")
        
        # 构建执行选项
        execution_options = {
            'headed': True,  # 默认使用有头模式
            'browser': 'chromium',
            'timeout': 300,
            'generate_allure': True  # 单用例执行时也生成Allure报告
        }
        
        # 如果传递了选项，则覆盖默认值
        if options:
            execution_options.update(options)
        
        logger.info(f"执行选项: {execution_options}")
        
        result = playwright_runner(
            script_id=script_id,
            script_content=script_content,
            base_url=base_url,
            options=execution_options
        )
        
        if result.get('success'):
            logger.info(f"Playwright测试执行成功: {script_id}")
            return {
                'success': True,
                'result': {
                    'status': 'passed',
                    'script_id': script_id,
                    'stdout': result.get('stdout', ''),
                    'stderr': result.get('stderr', ''),
                    'test_file': result.get('test_file', ''),
                    'return_code': result.get('return_code', 0),
                    'allure_report': result.get('allure_report', '')
                },
                'log': result.get('stdout', '') if result.get('stdout') else '测试执行完成'
            }
        else:
            logger.error(f"Playwright测试执行失败: {script_id}, error: {result.get('error')}")
            return {
                'success': False,
                'error': result.get('error', 'Playwright测试执行失败'),
                'result': {
                    'status': 'failed',
                    'script_id': script_id,
                    'stdout': result.get('stdout', ''),
                    'stderr': result.get('stderr', ''),
                    'test_file': result.get('test_file', ''),
                    'return_code': result.get('return_code', 1),
                    'allure_report': result.get('allure_report', '')
                },
                'log': result.get('stderr', '') if result.get('stderr') else '测试执行失败'
            }
        
    except Exception as e:
        logger.error(f"测试脚本执行失败: {e}")
        return {
            'success': False,
            'error': str(e)
        }


@shared_task(bind=True, name='web_testing.execute_webui_test_case')
def execute_webui_test_case_task(self, execution_id: int, options: dict = None, script_content: str = None, base_url: str = None):
    """
    执行WebUI测试用例的异步任务
    
    Args:
        execution_id: WebUI测试执行记录ID
        options: Playwright执行选项
        script_content: 测试脚本内容
        base_url: 基础URL
    
    Returns:
        Dict: 任务执行结果
    """
    return execute_async_task_with_progress(
        self,
        'webui_test_case_execution',
        _execute_webui_test_case_logic,
        execution_id,
        options or {},
        script_content,
        base_url
    )


def _execute_webui_test_case_logic(task_instance, execution_id: int, options: dict = None, script_content: str = None, base_url: str = None) -> Dict[str, Any]:
    """
    执行WebUI测试用例逻辑
    """
    try:
        # 步骤1: 获取执行记录和测试用例
        update_task_progress(task_instance, 10, '正在获取测试用例信息...')
        execution = WebUITestExecution.objects.get(id=execution_id)
        
        # 获取单用例执行详情
        case_detail = execution.case_execution_detail
        test_case = case_detail.test_case

        # 回写 WebUITestCase 执行状态：任务开始
        test_case.last_execute_status = 'running'
        test_case.last_error_message = ''
        test_case.save(update_fields=['last_execute_status', 'last_error_message'])
        
        # 更新执行状态
        execution.task_id = task_instance.request.id
        execution.status = 'running'
        execution.start_time = timezone.now()
        
        # 更新用例详情状态
        case_detail.status = 'running'
        case_detail.start_time = timezone.now()
        
        # 设置浏览器类型
        if options and 'browser' in options:
            execution.browser = options['browser']
        else:
            execution.browser = 'chromium'  # 默认浏览器
        
        execution.save()
        case_detail.save()
        
        # 步骤2: 验证测试脚本
        update_task_progress(task_instance, 30, '正在验证测试脚本...')
        
        # 验证脚本内容是否有效
        if not script_content:
            raise Exception("测试脚本内容为空，无法执行")
        
        # 步骤3: 验证环境配置
        update_task_progress(task_instance, 40, '正在验证环境配置...')
        
        # 验证基础URL是否有效
        if not base_url:
            raise Exception("基础URL为空，无法执行测试")
        
        logger.info(f"使用脚本内容长度: {len(script_content)}")
        logger.info(f"使用基础URL: {base_url}")
        
        # 步骤4: 执行测试脚本（沙箱模式：临时目录 + POM 注入 + 本地浏览器弹窗）
        update_task_progress(task_instance, 50, '正在执行WebUI测试...')
        
        project_id = test_case.project.id
        execution_result = _run_sandbox_test_script(script_content, project_id, base_url)
        
        # 步骤5: 保存执行结果
        update_task_progress(task_instance, 80, '正在保存执行结果...')
        
        # 计算执行时长
        end_time = timezone.now()
        duration = (end_time - execution.start_time).total_seconds()
        
        # 更新执行状态和结果
        if execution_result.get('success'):
            execution.status = 'passed'
            case_detail.status = 'passed'
            # 回写 WebUITestCase 执行状态：执行成功
            test_case.last_execute_status = 'passed'
            test_case.last_execute_time = timezone.now()
            test_case.save(update_fields=['last_execute_status', 'last_execute_time'])
        else:
            execution.status = 'failed'
            case_detail.status = 'failed'
            # 回写 WebUITestCase 执行状态：执行失败
            test_case.last_execute_status = 'failed'
            test_case.last_execute_time = timezone.now()
            test_case.last_error_message = (execution_result.get('error') or '')[-500:]
            test_case.save(update_fields=['last_execute_status', 'last_execute_time', 'last_error_message'])
        
        # 更新执行记录
        execution.end_time = end_time
        execution.duration = duration
        
        # 更新用例详情
        case_detail.end_time = end_time
        case_detail.duration = duration
        case_detail.error_message = execution_result.get('error')
        case_detail.log = execution_result.get('result', {}).get('stdout', '')
        case_detail.stdout = execution_result.get('result', {}).get('stdout', '')
        case_detail.stderr = execution_result.get('result', {}).get('stderr', '')
        
        # 设置路径信息
        result_data = execution_result.get('result', {})
        if result_data.get('screenshot_path'):
            case_detail.screenshot_path = result_data.get('screenshot_path')
        if result_data.get('video_path'):
            case_detail.video_path = result_data.get('video_path')
        
        execution.save()
        case_detail.save()
        
        
        # 更新完成时间
        execution.completed_at = timezone.now()
        execution.save()
        
        # 重新从数据库读取以验证保存结果
        execution.refresh_from_db()
        logger.info(f"WebUITestExecution记录已更新: ID={execution.id}, Status={execution.status}")
        
        # 步骤5: 返回结果
        update_task_progress(task_instance, 100, '测试用例执行完成')
        
        final_result = {
            'success': execution.status == 'passed',
            'status': 'completed',  # 任务执行完成，无论成功或失败
            'message': f'WebUI测试用例执行{"成功" if execution.status == "passed" else "失败"}',
            'execution_id': execution.id,
            'execution_status': execution.status,  # 保留执行状态用于详细信息
            'result': execution_result.get('result', {}),
            'log': execution_result.get('log', ''),
            'error': execution_result.get('error', '')
        }
        
        logger.info(f"_execute_webui_test_case_logic 成功返回: {final_result}")
        return final_result
        
    except WebUITestExecution.DoesNotExist:
        error_msg = f"测试执行记录不存在: {execution_id}"
        logger.error(error_msg)
        return build_error_result(None, error_msg)
    except Exception as e:
        error_msg = f"执行WebUI测试用例失败: {str(e)}"
        logger.error(f"_execute_webui_test_case_logic 异常: {str(e)}")
        # 回写 WebUITestCase 执行状态：异常时尝试更新（若已获取到 test_case）
        try:
            execution = WebUITestExecution.objects.get(id=execution_id)
            test_case = execution.case_execution_detail.test_case
            test_case.last_execute_status = 'failed'
            test_case.last_execute_time = timezone.now()
            test_case.last_error_message = str(e)[-500:]
            test_case.save(update_fields=['last_execute_status', 'last_execute_time', 'last_error_message'])
        except Exception:
            pass  # 忽略回写失败，避免掩盖原始异常
        return build_error_result(None, error_msg)


def _compose_script_for_execution(script_content: str, project_id: int) -> str:
    """
    若脚本包含 from pages.xxx import XxxPage，则从 WebPage 的 Page 库注入类定义，
    生成可独立运行的完整脚本。
    """
    import re
    if not script_content or 'from pages.' not in script_content or ' import ' not in script_content:
        return script_content

    # 解析 from pages.{module} import {ClassName} 格式
    import_pattern = re.compile(r'from\s+pages\.(\w+)\s+import\s+(\w+)')
    imports_found = import_pattern.findall(script_content)
    if not imports_found:
        return script_content

    # 从 WebPage 获取已保存的 Page 类代码（优先 pom_code，兼容 generated_class_code）
    page_class_map = {}  # class_name -> code
    for module_name, class_name in imports_found:
        page = WebPage.objects.filter(
            project_id=project_id,
            page_class_name=class_name
        ).first()
        if page:
            code = (page.pom_code or page.generated_class_code or '').strip()
            if code:
                page_class_map[class_name] = code

    if not page_class_map:
        return script_content

    # 移除 from pages.xxx import 行，在合适位置注入类定义
    lines = script_content.split('\n')
    new_lines = []
    inject_after_playwright = False
    injected = set()

    for line in lines:
        if import_pattern.match(line.strip()):
            class_name = import_pattern.search(line).group(2)
            if class_name in page_class_map and class_name not in injected:
                # 在此处注入类定义（紧跟 playwright import 之后）
                new_lines.append('')
                new_lines.append(page_class_map[class_name])
                new_lines.append('')
                injected.add(class_name)
            continue
        if 'from playwright.async_api import' in line:
            new_lines.append(line)
            inject_after_playwright = True
            continue
        new_lines.append(line)

    return '\n'.join(new_lines)


def _get_script_content_from_testcase(test_case):
    """
    从测试用例获取脚本内容。若为带 import 的解耦脚本，则自动组合为可执行完整脚本。
    """
    try:
        script_content = test_case.test_script_content
        if not script_content:
            logger.warning(f"测试用例 {test_case.id} 没有测试脚本内容")
            return None
        project_id = getattr(test_case, 'project_id', None) or (test_case.project.id if test_case.project else None)
        if project_id:
            script_content = _compose_script_for_execution(script_content, project_id)
        return script_content
    except Exception as e:
        logger.error(f"获取测试脚本失败: {str(e)}")
        return None


def _to_module_name(class_name):
    """PascalCase -> snake_case 模块名，如 RegisterPage -> register_page"""
    s = re.sub(r'(?<!^)(?=[A-Z])', '_', class_name).lower()
    return s.replace('__', '_')


def _build_suite_workspace_from_db(
    project_id: int,
    test_cases_data: list,
    work_dir: str,
    suite_name: str = None,
    base_url: str = "http://mall.lemonban.com:3344",
) -> tuple:
    """
    从数据库直接搬运代码资产到标准化目录结构，不调用代码生成器。

    目录结构:
        work_dir/
        ├── pages/
        │   ├── __init__.py
        │   ├── {module1}.py   # WebPage.pom_code
        │   └── {module2}.py
        ├── test_case_{id}.py  # WebUITestCase.test_script_content（原始，保留 import）
        └── pytest.ini

    Returns:
        (test_files: list, skipped_results: list)
    """
    import re as re_module
    pages_dir = os.path.join(work_dir, 'pages')
    os.makedirs(pages_dir, exist_ok=True)

    # 1. 解析所有脚本中的 from pages.xxx import Yyy，收集需要的 Page 类
    import_pattern = re_module.compile(r'from\s+pages\.(\w+)\s+import\s+(\w+)')
    imports_needed = set()  # (module_name, class_name)
    for case_data in test_cases_data:
        script = (case_data.get('script_content') or '').strip()
        for m in import_pattern.finditer(script):
            imports_needed.add((m.group(1), m.group(2)))

    # 2. 搬运 POM 代码：从 WebPage 提取 pom_code 写入 pages/{module}.py
    for module_name, class_name in imports_needed:
        page = WebPage.objects.filter(
            project_id=project_id,
            page_class_name=class_name
        ).first()
        if page:
            code = (page.pom_code or page.generated_class_code or '').strip()
            if code:
                page_file = os.path.join(pages_dir, f'{module_name}.py')
                with open(page_file, 'w', encoding='utf-8') as f:
                    f.write(code)
                logger.info(f"[套件搬运] POM 已写入 pages/{module_name}.py (class={class_name})")
            else:
                logger.warning(f"[套件搬运] WebPage {class_name} 无 pom_code，跳过")
        else:
            logger.warning(f"[套件搬运] 未找到 WebPage (project={project_id}, class_name={class_name})")

    # 3. 创建 pages/__init__.py
    init_file = os.path.join(pages_dir, '__init__.py')
    if not os.path.exists(init_file):
        Path(init_file).touch()

    # 4. 搬运测试脚本：直接写入 test_case_{id}.py（保留 from pages.xxx import，不内联）
    test_files = []
    skipped_results = []
    for idx, case_data in enumerate(test_cases_data):
        test_case_id = case_data.get('test_case_id')
        script_content = (case_data.get('script_content') or '').strip()
        test_case_title = case_data.get('test_case_title', f'Test Case {test_case_id}')

        if not script_content:
            logger.warning(f"[套件搬运] 用例 #{idx + 1} (id={test_case_id}): 无脚本内容，跳过")
            skipped_results.append({
                'test_case_id': test_case_id,
                'test_case_title': test_case_title,
                'status': 'skipped',
                'error_message': '测试用例没有脚本内容'
            })
            continue

        test_file = os.path.join(work_dir, f'test_case_{test_case_id}.py')
        try:
            content = script_content
            # 【核心修复】：动态替换相对路径为完整 URL（兼容双引号和单引号）
            content = content.replace('await page.goto("/")', f'await page.goto("{base_url}/")')
            content = content.replace("await page.goto('/')", f"await page.goto('{base_url}/')")
            # 【Pytest 兼容层】：若脚本无 test_ 函数，注入包装入口
            if 'def test_' not in content:
                content += "\n\n# =============== Pytest 兼容层 ==============="
                content += f"\ndef test_case_{test_case_id}_wrapper():"
                content += "\n    import asyncio"
                content += "\n    # 调用脚本自带的 main() 函数"
                content += "\n    asyncio.run(main())"
                content += "\n"

            with open(test_file, 'w', encoding='utf-8') as f:
                f.write(content)
            test_files.append(test_file)
            logger.info(f"[套件搬运] 用例 #{idx + 1} (id={test_case_id}): 已写入 {os.path.basename(test_file)}")
        except Exception as e:
            logger.error(f"[套件搬运] 用例 #{idx + 1} (id={test_case_id}): 写入失败, {e}", exc_info=True)
            skipped_results.append({
                'test_case_id': test_case_id,
                'test_case_title': test_case_title,
                'status': 'skipped',
                'error_message': f'脚本写入失败: {str(e)}'
            })

    # 5. 创建 pytest.ini
    pytest_ini = os.path.join(work_dir, 'pytest.ini')
    with open(pytest_ini, 'w', encoding='utf-8') as f:
        f.write("""[pytest]
base_url = http://localhost:8000
testpaths = .
python_files = test_*.py
python_classes = Test*
python_functions = test_*
""")

    logger.info(f"[套件搬运] 完成: 测试文件 {len(test_files)} 个, 跳过 {len(skipped_results)} 个")
    return test_files, skipped_results


# ============ POM 骨架提取任务 ============

@shared_task(bind=True, name='web_testing.extract_pom_from_doc')
def extract_pom_from_doc_task(self, project_id: int, file_id: int):
    """
    从知识库文档异步提取 POM 骨架任务

    Args:
        project_id: 项目ID
        file_id: 知识库文件ID

    Returns:
        Dict: 任务执行结果
    """
    return execute_async_task_with_progress(
        self,
        'POM骨架提取',
        _execute_extract_pom_from_doc,
        project_id,
        file_id
    )


def _execute_extract_pom_from_doc(task_instance, project_id: int, file_id: int) -> Dict[str, Any]:
    """执行 POM 骨架提取逻辑"""
    from json_repair import repair_json
    from langchain_core.prompts import ChatPromptTemplate
    from ai_core.model_manager import get_llm_manager
    from projects.knowledge.models import KnowledgeBaseFile

    try:
        update_task_progress(task_instance, 5, '正在获取文档内容...')
        knowledge_file = KnowledgeBaseFile.objects.get(id=file_id, project_id=project_id)
        doc_content = (knowledge_file.parsed_content or "").strip()

        if not doc_content:
            return build_error_result(task_instance.request.id, "该文档无解析内容，请先完成知识库入库或等待解析完成")

        update_task_progress(task_instance, 20, '正在调用大模型解析文档...')
        llm_manager = get_llm_manager()
        messages = _get_pom_extraction_prompt().format_messages(requirements_context=doc_content)
        output_text = llm_manager.invoke(messages)

        update_task_progress(task_instance, 60, '正在解析并落库...')
        if "```json" in output_text:
            json_str = output_text.split("```json")[1].split("```")[0].strip()
        elif "```" in output_text:
            json_str = output_text.split("```")[1].split("```")[0].strip()
        else:
            json_str = output_text.strip()

        parsed_data = json.loads(repair_json(json_str))

        # 支持两种格式：1) 新格式 {"modules": [...]} 2) 旧格式 [...]
        if isinstance(parsed_data, dict) and "modules" in parsed_data:
            modules_list = parsed_data["modules"]
        elif isinstance(parsed_data, list):
            modules_list = parsed_data
        else:
            return build_error_result(task_instance.request.id, "大模型返回格式异常，期望 JSON 对象(含 modules) 或数组")

        created_modules_count = 0
        created_pages_count = 0
        created_elements_count = 0

        with transaction.atomic():
            for module_data in modules_list:
                module_name = module_data.get("module_name") or "未命名模块"
                # 新格式：创建或获取原生的 WebUITestModule，再创建 associated_pages
                if "associated_pages" in module_data:
                    module_obj, _ = WebUITestModule.objects.get_or_create(
                        project_id=project_id,
                        name=module_name,
                        defaults={
                            'description': module_data.get("description") or "",
                            'business_rules': module_data.get("business_rules") or []
                        }
                    )
                    created_modules_count += 1
                    for page_data in module_data.get("associated_pages", []):
                        page_name = page_data.get("page_name") or "未命名页面"
                        page = WebPage.objects.create(
                            project_id=project_id,
                            module=module_obj,
                            name=page_name,
                            url_path="/"
                        )
                        created_pages_count += 1
                        for elem_data in page_data.get("elements", []):
                            elem_name = elem_data.get("name") or "未命名元素"
                            action_type = elem_data.get("action") or "click"
                            WebElement.objects.create(
                                page=page,
                                name=elem_name,
                                locator_type="",
                                locator_value="",
                                action_type=action_type,
                            )
                            created_elements_count += 1
                else:
                    # 旧格式：直接作为页面处理（兼容）
                    page = WebPage.objects.create(
                        project_id=project_id,
                        name=module_name,
                        url_path="/"
                    )
                    created_pages_count += 1
                    for elem_data in module_data.get("elements", []):
                        elem_name = elem_data.get("name") or "未命名元素"
                        action_type = elem_data.get("action") or "click"
                        WebElement.objects.create(
                            page=page,
                            name=elem_name,
                            locator_type="",
                            locator_value="",
                            action_type=action_type,
                        )
                        created_elements_count += 1

        msg = f"成功抽取 {created_modules_count} 个模块、{created_pages_count} 个页面和 {created_elements_count} 个元素骨架！"
        return {
            'status': 'completed',
            'success': True,
            'message': msg,
            'created_modules_count': created_modules_count,
            'created_pages_count': created_pages_count,
            'created_elements_count': created_elements_count,
            'data': parsed_data
        }

    except json.JSONDecodeError as e:
        logger.error(f"POM 提取 JSON 解析失败: {e}", exc_info=True)
        return build_error_result(task_instance.request.id, f"JSON 解析失败: {str(e)}")
    except Exception as e:
        logger.error(f"POM 提取失败: {e}", exc_info=True)
        return build_error_result(task_instance.request.id, f"提取失败: {str(e)}")

#元素列表管理-》智能提取（提取所有模块以及每个模块包含的页面和页面包含的元素）
def _get_pom_extraction_prompt():
    """获取 POM 提取 Prompt 模板（三维一体 + 动态动作规范）"""
    from langchain_core.prompts import ChatPromptTemplate
    from .constants import WEB_UI_ACTION_OPTIONS

    # 动态组装提示词需要的格式，例如："goto (访问网址), click (点击元素)..."
    actions_list = []
    for item in WEB_UI_ACTION_OPTIONS:
        # 从 "click - 点击元素" 中提取中文部分
        desc = item['label'].split('-')[1].strip() if '-' in item['label'] else item['label']
        actions_list.append(f"{item['value']} ({desc})")

    valid_actions_str = ", ".join(actions_list)

    template = """
你是一名资深的自动化测试架构师和系统分析师。你的任务是阅读并深度分析下方的《需求规格说明书》片段，从中提取出结构化的系统测试资产。
【任务目标】
你需要建立"模块包含页面，页面包含元素"的三维立体结构。即使文档描述散乱，你也必须根据业务常识进行合理归类。

【提取核心军规】

模块（modules）划分要符合业务流程和真实项目中的业务场景，如：注册模块、登录模块、购物车模块、下单模块等

页面（associated_pages）必须归属于某个具体的模块，有些模块可能包含多个页面，请根据需求文档中描述的业务依赖关系和流程将该模块下涉及到的页面全部找到

禁用定位器：当前只需要提取元素名称，不需要 CSS/XPath 定位器，留空。

操作类型 (action)：严格从以下系统预设的动作枚举中选择：{valid_actions}。绝对禁止捏造枚举之外的动作！

【需求规格说明书内容】
{requirements_context}

【最高输出指令】
请直接输出严格的 JSON 格式数据，必须包裹在 json 和 ``` 之间。绝对禁止输出任何 Markdown 表格或多余的解释！
JSON 格式要求如下：
{{
	"modules": [{{
		"module_name": "注册模块",
		"description": "处理用户注册",
		"business_rules": ["用户名为4~16位字母、数字或下划线", "密码长度为6-18个字母、数字、以及符号至少包含两种", "短信验证码6位数字，有效期为5min，支持重新获取", "用户名不能重复", "手机号不能重复"],
		"associated_pages": [{{
				"page_name": "商城首页",
				"elements": [{{
					"name": "用户注册入口",
					"action": "click",
					"description": "点击注册入口"
				}}]
			}},
			{{
				"page_name": "用户注册页",
				"elements": [{{
					"name": "手机号输入框",
					"action": "fill",
					"description": "用于输入手机号"
				}}]
			}}]
	}}]
}}
"""
    # 使用 partial 将动作规则提前注入模板，调用时只需传入 requirements_context
    return ChatPromptTemplate.from_template(template).partial(valid_actions=valid_actions_str)


# ============ HTML 源码智能回填定位器任务 ============

def _get_fill_locators_prompt():
    """获取 HTML 回填定位器 Prompt 模板"""
    from langchain_core.prompts import ChatPromptTemplate
    return ChatPromptTemplate.from_template("""
你是一名资深的自动化测试架构师。你的任务是根据提供的【HTML 网页源码】，为下方的【待提取 UI 元素列表】提取最稳定、最精准的 CSS 选择器。

【提取核心军规】
⚠️ 极其重要的定位军规：
1. 绝对禁止使用包含随机数字或哈希值的动态 ID（如 el-input-*、tab-*）。
2. 优先使用语义化属性：placeholder、name、type。
3. 优先使用精确的文本匹配。
4. 实在不行再使用稳定的层级选择器，避免使用极其脆弱的绝对路径，优先找寻最近的稳定父节点。
5. 如果元素在源码中不存在，对应的 locator 请留空。

【待提取 UI 元素列表】
{elements_json}

【HTML 网页源码片段】
{html_context}

【最高输出指令】
直接输出严格的 JSON 数组，必须包含在 json 和 ``` 之间。不要解释！格式如下：
[
  {{
    "element_id": 123,
    "locator_type": "css",
    "locator": "#app > div.login-btn"
  }}
]
""")


@shared_task(bind=True, name='web_testing.fill_locators_from_html')
def fill_locators_from_html_task(self, project_id: int, page_id: int, html_source: str):
    """
    从 HTML 源码智能回填定位器任务

    Args:
        project_id: 项目ID
        page_id: 页面ID
        html_source: HTML 源码字符串

    Returns:
        Dict: 任务执行结果
    """
    return execute_async_task_with_progress(
        self,
        'HTML定位器回填',
        _execute_fill_locators_from_html,
        project_id,
        page_id,
        html_source
    )


def _execute_fill_locators_from_html(
    task_instance, project_id: int, page_id: int, html_source: str
) -> Dict[str, Any]:
    """执行 HTML 源码回填定位器逻辑"""
    from json_repair import repair_json
    from ai_core.model_manager import get_llm_manager

    try:
        update_task_progress(task_instance, 5, '正在获取页面元素...')
        elements = WebElement.objects.filter(page_id=page_id)
        if not elements.exists():
            return {
                'status': 'completed',
                'success': True,
                'message': '该页面无元素需要回填',
                'updated_count': 0
            }

        elements_data = [
            {"element_id": e.id, "name": e.name, "action": e.action_type or ''}
            for e in elements
        ]

        safe_html_source = (html_source or '')[:80000]

        update_task_progress(task_instance, 20, '正在调用大模型匹配定位器...')
        llm_manager = get_llm_manager()
        messages = _get_fill_locators_prompt().format_messages(
            elements_json=json.dumps(elements_data, ensure_ascii=False),
            html_context=safe_html_source
        )
        output_text = llm_manager.invoke(messages)

        update_task_progress(task_instance, 70, '正在解析并更新数据库...')
        if "```json" in output_text:
            json_str = output_text.split("```json")[1].split("```")[0].strip()
        elif "```" in output_text:
            json_str = output_text.split("```")[1].split("```")[0].strip()
        else:
            json_str = output_text.strip()

        parsed_data = json.loads(repair_json(json_str))

        if not isinstance(parsed_data, list):
            return build_error_result(
                task_instance.request.id,
                "大模型返回格式异常，期望 JSON 数组"
            )

        updated_count = 0
        with transaction.atomic():
            for item in parsed_data:
                elem_id = item.get('element_id')
                locator = item.get('locator') or item.get('locator_value', '')
                if elem_id and locator:
                    WebElement.objects.filter(
                        id=elem_id, page_id=page_id
                    ).update(
                        locator_type=item.get('locator_type', 'css'),
                        locator_value=locator
                    )
                    updated_count += 1

        msg = f"成功从 HTML 源码中回填了 {updated_count} 个元素的定位器！"

        # 触发所属页面的 POM 代码全量重构
        try:
            from .pom_code_generator import generate_page_class_code
            generate_page_class_code(page_id)
        except Exception as e:
            logger.error(f"回填后触发 POM 生成失败: {str(e)}")

        return {
            'status': 'completed',
            'success': True,
            'message': msg,
            'updated_count': updated_count
        }

    except json.JSONDecodeError as e:
        logger.error(f"定位器回填 JSON 解析失败: {e}", exc_info=True)
        return build_error_result(
            task_instance.request.id,
            f"JSON 解析失败: {str(e)}"
        )
    except Exception as e:
        logger.error(f"定位器回填失败: {e}", exc_info=True)
        return build_error_result(
            task_instance.request.id,
            f"回填失败: {str(e)}"
        )


# ============ MidScene脚本生成任务 ============

@shared_task(bind=True, name='web_testing.generate_midscene_script')
def generate_midscene_script_task(self, script_id: int, user_id: int, project_id: int):
    """
    生成MidScene脚本的异步任务
    
    Args:
        script_id: MidScene脚本ID
        user_id: 用户ID
        project_id: 项目ID
    
    Returns:
        Dict: 任务执行结果
    """
    return execute_async_task_with_websocket(
        self,
        'midscene_script_generation',
        _execute_midscene_script_generation,
        script_id, user_id, project_id
    )


def _execute_midscene_script_generation(task_instance, script_id: int, user_id: int, project_id: int) -> Dict[str, Any]:
    """
    执行MidScene脚本生成逻辑
    """
    try:
        # 步骤1: 获取用户、项目和脚本记录
        update_task_progress(task_instance, 10, '正在获取用户和项目信息...')
        user = User.objects.get(id=user_id)
        project = Project.objects.get(id=project_id)
        script = MidSceneScript.objects.get(id=script_id)
        
        # 更新任务状态
        script.task_id = task_instance.request.id
        script.status = 'running'
        script.save()
        
        # 步骤2: 初始化MidScene智能体
        update_task_progress(task_instance, 20, '正在初始化MidScene智能体...')
        
        # 步骤3: 创建并运行MidScene智能体
        update_task_progress(task_instance, 50, '正在生成MidScene脚本...')
        try:
            # 创建MidScene智能体
            agent = create_midscene_agent(
                user=user,
                user_id=user_id,
                enable_streaming=True
            )
            
            # 运行智能体生成脚本
            result = agent.run(
                description=script.natural_language,
                screenshot_b64=script.screenshot_b64
            )
            
        except Exception as e:
            logger.error(f"MidScene脚本生成失败: {str(e)}")
            result = {
                'success': False,
                'error': str(e)
            }
        
        # 步骤4: 处理生成结果
        update_task_progress(task_instance, 80, '正在保存脚本结果...')
        if result['success']:
            # 更新脚本内容
            script.script_content = result['script']
            script.status = 'completed'
            script.is_executed = True
            script.execution_result = {
                'model_info': result.get('model_info', {}),
                'model_type': result.get('model_type', 'unknown'),
                'generated_at': timezone.now().isoformat()
            }
            script.save()
            
            logger.info(f"MidScene脚本生成任务完成: {task_instance.request.id}")
            return {
                'success': True,
                'status': 'completed',
                'message': 'MidScene脚本生成成功',
                'script_id': script_id,
                'script': result['script'],
                'model_info': result.get('model_info', {}),
                'model_type': result.get('model_type', 'unknown')
            }
        else:
            # 更新失败状态
            script.status = 'failed'
            script.execution_error = result.get('error', '未知错误')
            script.save()
            
            logger.error(f"MidScene脚本生成失败: {task_instance.request.id}, error: {result.get('error')}")
            # 任务执行完成，只是结果失败，所以返回 status='completed'
            return {
                'success': False,
                'status': 'completed',
                'message': f'MidScene脚本生成失败: {result.get("error", "未知错误")}',
                'error': result.get('error', '未知错误'),
                'script_id': script_id
            }
            
    except (User.DoesNotExist, Project.DoesNotExist, MidSceneScript.DoesNotExist) as e:
        error_msg = f"资源不存在: {str(e)}"
        logger.error(error_msg)
        return build_error_result(None, error_msg)
        
    except Exception as e:
        error_msg = f"MidScene脚本生成任务异常: {str(e)}"
        logger.error(error_msg, exc_info=True)
        
        # 更新失败状态
        try:
            script = MidSceneScript.objects.get(id=script_id)
            script.status = 'failed'
            script.execution_error = str(e)
            script.save()
        except:
            pass
        
        return build_error_result(None, error_msg)

    """
    获取任务状态
    
    Args:
        task_id: Celery任务ID
    
    Returns:
        任务状态信息
    """
    try:
        # 获取Celery任务结果
        result = AsyncResult(task_id)
        
        if result.state == 'PENDING':
            return {
                'status': 'pending',
                'progress': 0,
                'message': '任务等待中...',
                'task_id': task_id
            }
        elif result.state == 'PROGRESS':
            meta = result.info
            return {
                'status': 'running',
                'progress': meta.get('progress', 0),
                'message': meta.get('message', '任务执行中...'),
                'task_id': task_id
            }
        elif result.state == 'SUCCESS':
            meta = result.info
            return {
                'status': 'completed',
                'progress': 100,
                'message': meta.get('message', '任务完成'),
                'task_id': task_id,
                'result': meta.get('result', {})
            }
        elif result.state == 'FAILURE':
            meta = result.info
            return {
                'status': 'failed',
                'progress': 0,
                'message': meta.get('message', '任务失败'),
                'task_id': task_id,
                'error': meta.get('exc_message', '未知错误'),
                'result': meta.get('result', {})
            }
        else:
            return {
                'status': 'unknown',
                'progress': 0,
                'message': f'未知状态: {result.state}',
                'task_id': task_id
            }
            
    except Exception as e:
        logger.error(f"获取任务状态失败: {e}")
        return {
            'status': 'error',
            'progress': 0,
            'message': f'获取任务状态失败: {str(e)}',
            'task_id': task_id
        }

# ============ 任务取消 ============

@shared_task(name='web_testing.cancel_task')
def cancel_task(task_id: str) -> Dict[str, Any]:
    """
    取消任务
    
    Args:
        task_id: Celery任务ID
    
    Returns:
        取消结果
    """
    try:
        # 写入协作式取消标记（跨进程/Windows下可靠）
        cache.set(f"celery:cancel:{task_id}", True, timeout=60 * 60)
        # 获取任务结果
        result = AsyncResult(task_id)
        
        # 取消任务
        result.revoke(terminate=True)
        
        # 更新相关记录状态
        # 注意：WebUITestCase模型没有task_id和status字段，所以不需要更新
        
        try:
            # 尝试更新WebUITestExecution
            execution = WebUITestExecution.objects.get(task_id=task_id)
            execution.status = 'cancelled'
            execution.completed_at = timezone.now()
            execution.save()
        except WebUITestExecution.DoesNotExist:
            pass
        
        try:
            # 尝试更新MidSceneScript
            script = MidSceneScript.objects.get(task_id=task_id)
            script.status = 'cancelled'
            script.completed_at = timezone.now()
            script.save()
        except MidSceneScript.DoesNotExist:
            pass
        
        logger.info(f"任务已取消: {task_id}")
        return {
            'success': True,
            'message': '任务已取消',
            'task_id': task_id
        }
        
    except Exception as e:
        logger.error(f"取消任务失败: {e}")
        return {
            'success': False,
            'error': f'取消任务失败: {str(e)}',
            'task_id': task_id
        }


# ============ Web UI测试用例生成任务 ============

@shared_task(bind=True, name='web_testing.generate_webui_test_cases')
def generate_webui_test_cases_task(self, user_input: str, project_id: int, user_id: int, module_id: int = None):
    """
    Web UI测试用例生成任务
    在后台运行智能体，通过WebSocket发送实时进度
    """
    return execute_async_task_with_websocket(
        self,
        'webui_test_generation',
        _execute_webui_test_cases_generation,
        user_input, project_id, user_id, module_id
    )


def _execute_webui_test_cases_generation(task_instance, user_input: str, project_id: int, user_id: int, module_id: int = None) -> Dict[str, Any]:
    """
    执行WebUI测试用例生成逻辑
    """
    try:
        logger.info(f"开始执行Web UI测试用例生成任务: 用户={user_id}, 项目={project_id}, 需求={user_input}")
        
        # 更新任务状态
        update_task_progress(task_instance, 10, '正在初始化智能体...')
        
        # 导入智能体
        from ai_core.webui_testcase_agent import create_webui_test_case_generator
        
        # 创建智能体实例
        generator = create_webui_test_case_generator(user_id, enable_streaming=True)
        
        # 更新任务状态
        update_task_progress(task_instance, 20, '智能体初始化完成，开始生成测试用例...')
        
        # 运行智能体
        result = generator.run(user_input, project_id, module_id)
        
        if result['success']:
            # 更新任务状态为完成
            update_task_progress(task_instance, 100, 'WebUI测试用例生成完成')
            return {
                'success': True,
                'status': 'completed',
                'message': 'WebUI测试用例生成成功',
                'test_cases': result.get('test_cases', []),
                'created_test_cases': result.get('created_test_cases', []),
                'test_cases_count': result.get('test_cases_count', 0),
                'test_suite_name': result.get('test_suite_name', ''),
                'execution_timeline': result.get('execution_timeline', [])
            }
        else:
            error_msg = result.get('error', '未知错误')
            logger.error(f"WebUI测试用例生成失败: {error_msg}")
            return build_error_result(None, error_msg)
            
    except Exception as e:
        error_msg = f"WebUI测试用例生成任务异常: {str(e)}"
        logger.error(error_msg, exc_info=True)
        return build_error_result(None, error_msg)


# ============ WebUI测试套件执行任务 ============

@shared_task(bind=True)
def execute_webui_test_suite_task(self, execution_id, user_id=None, options=None, scheduled_log_id=None):
    """
    执行WebUI测试套件任务

    Args:
        execution_id: 测试套件执行记录ID
        user_id: 用户ID
        options: 执行选项
        scheduled_log_id: 定时任务执行日志ID（由定时任务中心调用时传入，用于完成后回填状态与企微通知）

    Returns:
        Dict: 任务执行结果
    """
    opts = dict(options or {})
    opts['scheduled_log_id'] = scheduled_log_id
    return execute_async_task_with_progress(
        self,
        'webui_test_suite_execution',
        _execute_webui_test_suite_logic,
        execution_id,
        user_id,
        opts
    )


def _execute_webui_test_suite_logic(task_instance, execution_id: int, user_id: int, options: dict = None) -> Dict[str, Any]:
    """
    执行WebUI测试套件逻辑
    """
    try:
        # 步骤1: 获取执行记录和测试套件
        update_task_progress(task_instance, 10, '正在获取测试套件信息...')
        execution = WebUITestExecution.objects.get(id=execution_id, executor_id=user_id, exec_type='suite')
        
        # 获取套件执行详情
        suite_detail = execution.suite_execution_detail
        test_suite = suite_detail.test_suite
        
        # 更新执行状态
        execution.task_id = task_instance.request.id
        execution.status = 'running'
        execution.start_time = timezone.now()
        
        # 更新套件详情状态
        suite_detail.start_time = timezone.now()
        
        # 设置浏览器类型
        if options and 'browser' in options:
            execution.browser = options['browser']
        else:
            execution.browser = 'chromium'  # 默认浏览器
        
        # 确保options存在并包含suite_name
        if not options:
            options = {}
        if 'suite_name' not in options:
            options['suite_name'] = test_suite.name
        
        execution.save()
        suite_detail.save()
        
        # 步骤2: 获取套件中的测试用例
        update_task_progress(task_instance, 20, '正在获取测试用例列表...')
        
        test_cases = test_suite.test_cases.all()
        
        if not test_cases.exists():
            error_msg = "测试套件中没有测试用例"
            execution.status = 'failed'
            execution.error_message = error_msg
            execution.save()
            return build_error_result(task_instance.request.id, error_msg)
        
        total_cases = test_cases.count()
        passed_cases = 0
        failed_cases = 0
        skipped_cases = 0
        
        
        # 步骤3: 准备环境配置（获取 Base URL 用于动态注入脚本）
        update_task_progress(task_instance, 30, '正在准备环境配置...')

        # 默认 fallback 域名（防止未配置环境时报错）
        base_url = "http://mall.lemonban.com:3344"
        if execution.environment_id:
            try:
                from projects.models import Environment
                environment = Environment.objects.get(id=execution.environment_id)
                if hasattr(environment, 'config') and isinstance(environment.config, dict):
                    base_url = environment.config.get('base_url', base_url)
                elif hasattr(environment, 'base_url') and environment.base_url:
                    base_url = environment.base_url
            except Exception as e:
                logger.warning(f"获取环境配置失败: {e}")
        # 确保 base_url 不以斜杠结尾，方便统一拼接
        base_url = (base_url or '').rstrip('/') or "http://mall.lemonban.com:3344"
        
        # 步骤4: 准备测试用例数据（直接使用数据库字段，不调用代码生成器）
        update_task_progress(task_instance, 40, f'正在准备 {total_cases} 个测试用例...')

        logger.info(f"[套件执行] 开始准备测试用例数据，套件={test_suite.name}，共 {total_cases} 个用例（直接搬运 DB 资产）")

        project_id = test_suite.project_id
        test_cases_data = []

        for idx, test_case in enumerate(test_cases):
            case_info = f"用例 #{idx + 1} (id={test_case.id}, title={test_case.title})"

            # 检查测试用例是否有脚本内容
            if not test_case.has_script:
                logger.warning(f"[套件执行] {case_info}: 跳过，原因: 数据库中无脚本内容 (has_script=False)")
                skipped_cases += 1
                continue

            # 直接使用 test_script_content，不调用 _compose_script_for_execution（搬运时创建 pages/ 目录）
            script_content = (test_case.test_script_content or '').strip()
            if not script_content:
                logger.warning(f"[套件执行] {case_info}: 跳过，原因: test_script_content 为空")
                skipped_cases += 1
                continue

            test_cases_data.append({
                'test_case_id': test_case.id,
                'script_content': script_content,
                'test_case_title': test_case.title
            })
            logger.info(f"[套件执行] {case_info}: 脚本获取成功，已加入执行队列")
        
        if not test_cases_data:
            error_msg = "脚本生成失败，未发现可执行的测试文件"
            logger.error(
                f"[套件执行] {error_msg}，套件={test_suite.name}，"
                f"总用例数={total_cases}，全部被跳过 (无脚本或获取失败)"
            )
            execution.status = 'failed'
            execution.error_message = error_msg
            execution.save()
            return build_error_result(task_instance.request.id, error_msg)

        logger.info(
            f"[套件执行] 准备完成: 可执行 {len(test_cases_data)} 个，跳过 {skipped_cases} 个，"
            f"即将写入 playwright_workspace 并启动 Pytest"
        )

        # 步骤5: 批量执行测试用例
        update_task_progress(task_instance, 50, f'开始批量执行 {len(test_cases_data)} 个测试用例...')

        logger.info(f"开始执行WebUI测试套件: 套件={test_suite.name}，包含 {len(test_cases_data)} 个测试用例")

        # 直接搬运 DB 资产并执行 Pytest（不调用代码生成器）
        exec_options = dict(options or {})
        exec_options['project_id'] = test_suite.project_id
        exec_options['suite_name'] = test_suite.name
        suite_result = _run_test_suite_script(test_cases_data, base_url, exec_options)
        execution_results = []
        
        if suite_result.get('success'):
            logger.info("测试套件批量执行成功")
            
            # 解析批量执行结果
            case_results = suite_result.get('result', {}).get('case_results', [])
            
            # 如果解析结果为空，尝试从pytest输出中提取统计信息
            if not case_results:
                logger.warning("未能解析到测试用例结果，尝试从pytest统计摘要中提取")
                result_data = suite_result.get('result', {})
                stdout = result_data.get('stdout', '')
                
                # 从stdout中提取统计信息
                import re
                # 查找类似 "1 failed in 23.12s" 或 "1 passed, 1 failed in 2.34s" 的行
                summary_pattern = r'(\d+)\s+(passed|failed|skipped|error)'
                matches = re.findall(summary_pattern, stdout, re.IGNORECASE)
                
                for count_str, status_type in matches:
                    count = int(count_str)
                    if 'passed' in status_type.lower():
                        passed_cases = count
                    elif 'failed' in status_type.lower():
                        failed_cases = count
                    elif 'skipped' in status_type.lower():
                        skipped_cases = count
                
                logger.info(f"从统计摘要提取: passed={passed_cases}, failed={failed_cases}, skipped={skipped_cases}")
            
            # 更新每个测试用例的执行状态
            for case_result in case_results:
                test_case_id = case_result.get('test_case_id')
                status = case_result.get('status')
                
                # 查找对应的测试用例
                test_case = next((tc for tc in test_cases if tc.id == test_case_id), None)
                if not test_case:
                    continue
                
                # 创建套件用例执行明细
                case_execution = WebUITestSuiteCaseExecution.objects.create(
                    suite_execution=suite_detail,
                    test_case=test_case,
                    name=test_case.title,
                    status=status,
                    error_message=case_result.get('error_message')
                )
                
                # 更新统计
                if status == 'passed':
                    passed_cases += 1
                elif status == 'failed':
                    failed_cases += 1
                elif status == 'skipped':
                    skipped_cases += 1
                
                execution_results.append({
                    'test_case_id': test_case.id,
                    'test_case_title': test_case.title,
                    'status': status,
                    'error_message': case_result.get('error_message'),
                    'result': case_result
                })
            
            # 确保统计数据正确（如果解析失败，使用从统计摘要提取的数据）
            logger.info(f"最终统计: total={total_cases}, passed={passed_cases}, failed={failed_cases}, skipped={skipped_cases}")
            
            # 更新套件执行结果
            result_data = suite_result.get('result', {})
            allure_report = result_data.get('allure_report', '')
            suite_detail.allure_report = allure_report
            
            # 构建执行日志
            stdout = result_data.get('stdout', '')
            stderr = result_data.get('stderr', '')
            execution_log = f"=== 测试套件执行日志 ===\n"
            if stdout:
                execution_log += f"\n--- 标准输出 ---\n{stdout}\n"
            if stderr:
                execution_log += f"\n--- 错误输出 ---\n{stderr}\n"
            
            # 保存日志到suite_detail
            suite_detail.log = execution_log
            
        else:
            logger.error(f"测试套件批量执行失败: {suite_result.get('error')}")
            execution.status = 'failed'
            execution.error_message = suite_result.get('error', '测试套件执行失败')
            
            # 即使失败也保存日志
            result_data = suite_result.get('result', {})
            stdout = result_data.get('stdout', '')
            stderr = result_data.get('stderr', '')
            execution_log = f"=== 测试套件执行日志 ===\n"
            if stdout:
                execution_log += f"\n--- 标准输出 ---\n{stdout}\n"
            if stderr:
                execution_log += f"\n--- 错误输出 ---\n{stderr}\n"
            
            # 保存日志到suite_detail
            suite_detail.log = execution_log
            
            # 尝试从输出中提取统计数据（即使失败也可能有部分执行结果）
            case_results = result_data.get('case_results', [])
            if case_results:
                # 如果有解析结果，更新统计
                for case_result in case_results:
                    status = case_result.get('status')
                    if status == 'passed':
                        passed_cases += 1
                    elif status == 'failed':
                        failed_cases += 1
                    elif status == 'skipped':
                        skipped_cases += 1
            elif stdout:
                # 尝试从stdout中提取统计信息
                import re
                summary_pattern = r'(\d+)\s+(passed|failed|skipped|error)'
                matches = re.findall(summary_pattern, stdout, re.IGNORECASE)
                
                for count_str, status_type in matches:
                    count = int(count_str)
                    if 'passed' in status_type.lower():
                        passed_cases = count
                    elif 'failed' in status_type.lower():
                        failed_cases = count
                    elif 'skipped' in status_type.lower():
                        skipped_cases = count
                
                logger.info(f"从失败输出中提取统计: passed={passed_cases}, failed={failed_cases}, skipped={skipped_cases}")
            
            # 更新套件执行详情（即使失败也保存统计数据）
            suite_detail.total_cases = total_cases
            suite_detail.passed_cases = passed_cases
            suite_detail.failed_cases = failed_cases
            suite_detail.skipped_cases = skipped_cases
            suite_detail.end_time = timezone.now()
            if execution.start_time:
                suite_detail.duration = (timezone.now() - execution.start_time).total_seconds()
            
            # 报告搬运与路径回填（失败分支也可能有部分 Allure 报告）
            report_url_fail = ""
            allure_report_fail = result_data.get('allure_report', '')
            source_report_dir_fail = os.path.dirname(allure_report_fail) if allure_report_fail else ""
            if source_report_dir_fail and os.path.exists(source_report_dir_fail):
                try:
                    from django.conf import settings
                    target_report_dir_fail = os.path.join(settings.MEDIA_ROOT, 'allure_reports', str(execution.id))
                    os.makedirs(target_report_dir_fail, exist_ok=True)
                    for item in os.listdir(source_report_dir_fail):
                        src = os.path.join(source_report_dir_fail, item)
                        dst = os.path.join(target_report_dir_fail, item)
                        if os.path.isdir(src):
                            shutil.copytree(src, dst, dirs_exist_ok=True)
                        else:
                            shutil.copy2(src, dst)
                    report_url_fail = f"/allure_reports/{execution.id}/index.html"
                    suite_detail.allure_report = os.path.join(target_report_dir_fail, 'index.html')
                    logger.info(f"[报告搬运] Allure 报告已持久化至 {target_report_dir_fail}（失败分支）")
                except Exception as e:
                    logger.warning(f"[报告搬运] 复制 Allure 报告失败: {e}", exc_info=True)

            # 保存日志和报告路径
            if allure_report_fail and not report_url_fail:
                execution.report_path = allure_report_fail
                suite_detail.allure_report = allure_report_fail
            elif report_url_fail:
                execution.report_path = report_url_fail
            suite_detail.save()
            execution.log_path = result_data.get('test_files', [''])[0] if result_data.get('test_files') else ''
            execution.save()

            # --- 定时任务异步回调与企微通知（失败分支）---
            scheduled_log_id = (options or {}).get('scheduled_log_id')
            if scheduled_log_id:
                try:
                    from django.db.models import F, Value
                    from django.db.models.functions import Concat, Coalesce
                    from django.conf import settings
                    from scheduled_tasks.models import TaskExecutionLog
                    from notifications.services import trigger_notification

                    exec_log = TaskExecutionLog.objects.get(id=scheduled_log_id)
                    TaskExecutionLog.objects.filter(id=scheduled_log_id).update(
                        passed_cases=F('passed_cases') + passed_cases,
                        failed_cases=F('failed_cases') + failed_cases,
                        skipped_cases=F('skipped_cases') + skipped_cases,
                    )
                    # 回填 Pytest 执行日志（stdout/stderr）到 step_log
                    step_content_fail = (suite_detail.log or '').strip()
                    if not step_content_fail:
                        stdout_fail = result_data.get('stdout', '')
                        stderr_fail = result_data.get('stderr', '')
                        if stdout_fail or stderr_fail:
                            step_content_fail = "=== 测试套件执行日志 ===\n"
                            if stdout_fail:
                                step_content_fail += f"\n--- 标准输出 ---\n{stdout_fail}\n"
                            if stderr_fail:
                                step_content_fail += f"\n--- 错误输出 ---\n{stderr_fail}\n"
                    if step_content_fail:
                        TaskExecutionLog.objects.filter(id=scheduled_log_id).update(
                            step_log=Concat(
                                Coalesce(F('step_log'), Value('')),
                                Value('\n\n'),
                                Value(step_content_fail),
                            ),
                        )
                    exec_log.refresh_from_db()
                    exec_log.status = 'success' if exec_log.failed_cases == 0 else 'failed'
                    exec_log.end_time = timezone.now()
                    exec_log.total_cases = exec_log.total_cases or total_cases
                    save_fields_fail = ['status', 'end_time', 'total_cases']
                    if report_url_fail:
                        base = (getattr(settings, 'FRONTEND_BASE_URL', None) or getattr(settings, 'SITE_URL', 'http://127.0.0.1:8000')).rstrip('/')
                        exec_log.report_url = f"{base}/media{report_url_fail}" if not report_url_fail.startswith('http') else report_url_fail
                        exec_log.allure_report_url = report_url_fail
                        save_fields_fail.extend(['report_url', 'allure_report_url'])
                    exec_log.save(update_fields=save_fields_fail)

                    trigger_notification(
                        scheduled_task_id=exec_log.task_id,
                        execution_log=exec_log,
                        result={
                            'total_cases': exec_log.total_cases,
                            'passed_cases': exec_log.passed_cases,
                            'failed_cases': exec_log.failed_cases,
                        },
                    )
                    logger.info(f"成功回填定时任务日志 {scheduled_log_id} 并触发企微通知（失败分支）")
                except Exception as e:
                    logger.error(f"回填定时任务日志或触发通知失败: {e}", exc_info=True)

            # 构建错误结果，但包含allure_report和统计数据（供前端通知展示）
            # 即使执行失败，任务也已经完成，所以返回 status='completed'
            return {
                'success': False,
                'status': 'completed',  # 任务执行完成，只是结果失败
                'message': f'测试套件执行失败: {suite_result.get("error", "未知错误")}',
                'error': suite_result.get('error', '测试套件执行失败'),
                'execution_id': execution_id,
                'total_cases': total_cases,
                'passed_cases': passed_cases,
                'failed_cases': failed_cases,
                'skipped_cases': skipped_cases,
                'allure_report': allure_report,
                'result': {
                    'allure_report': allure_report,
                    'stdout': stdout,
                    'stderr': stderr,
                    'test_files': result_data.get('test_files', []),
                    'total_cases': total_cases,
                    'passed_cases': passed_cases,
                    'failed_cases': failed_cases,
                    'skipped_cases': skipped_cases,
                }
            }
        
        # 步骤6: 保存执行结果
        update_task_progress(task_instance, 90, '正在保存执行结果...')
        
        # 计算执行时长
        end_time = timezone.now()
        duration = (end_time - execution.start_time).total_seconds()
        
        # 更新套件执行详情
        suite_detail.total_cases = total_cases  # 确保total_cases被设置
        suite_detail.passed_cases = passed_cases
        suite_detail.failed_cases = failed_cases
        suite_detail.skipped_cases = skipped_cases
        suite_detail.end_time = end_time
        suite_detail.duration = duration
        # 确保日志已保存（如果之前没有保存）
        if not suite_detail.log and 'execution_log' in locals():
            suite_detail.log = execution_log
        suite_detail.save()
        
        logger.info(f"套件执行详情已更新: total={suite_detail.total_cases}, passed={suite_detail.passed_cases}, failed={suite_detail.failed_cases}, skipped={suite_detail.skipped_cases}, pass_rate={suite_detail.pass_rate}")
        
        # 确定最终状态
        if failed_cases == 0:
            execution.status = 'passed'
            final_message = f"测试套件执行完成: 通过 {passed_cases}/{total_cases} 个测试用例"
        else:
            execution.status = 'failed'
            final_message = f"测试套件执行完成: 通过 {passed_cases}/{total_cases} 个测试用例，失败 {failed_cases} 个"

        # --- 报告搬运与路径回填（持久化存储，解决 404）---
        report_url = ""
        source_report_dir = os.path.dirname(allure_report) if allure_report else ""
        if source_report_dir and os.path.exists(source_report_dir):
            try:
                from django.conf import settings
                target_report_dir = os.path.join(settings.MEDIA_ROOT, 'allure_reports', str(execution.id))
                os.makedirs(target_report_dir, exist_ok=True)
                for item in os.listdir(source_report_dir):
                    src = os.path.join(source_report_dir, item)
                    dst = os.path.join(target_report_dir, item)
                    if os.path.isdir(src):
                        shutil.copytree(src, dst, dirs_exist_ok=True)
                    else:
                        shutil.copy2(src, dst)
                report_url = f"/allure_reports/{execution.id}/index.html"
                suite_detail.allure_report = os.path.join(target_report_dir, 'index.html')
                suite_detail.save(update_fields=['allure_report'])
                logger.info(f"[报告搬运] Allure 报告已持久化至 {target_report_dir}")
            except Exception as e:
                logger.warning(f"[报告搬运] 复制 Allure 报告失败: {e}", exc_info=True)

        # 更新执行记录（保存日志和报告路径）
        execution.end_time = end_time
        execution.duration = duration
        if report_url:
            execution.report_path = report_url
        elif allure_report:
            execution.report_path = allure_report
        execution.log_path = result_data.get('test_files', [''])[0] if result_data.get('test_files') else ''
        execution.save()

        # --- 定时任务异步回调与企微通知 ---
        scheduled_log_id = (options or {}).get('scheduled_log_id')
        if scheduled_log_id:
            try:
                from django.db.models import F, Value
                from django.db.models.functions import Concat, Coalesce
                from django.conf import settings
                from scheduled_tasks.models import TaskExecutionLog
                from notifications.services import trigger_notification

                exec_log = TaskExecutionLog.objects.get(id=scheduled_log_id)
                # 原子累加（多套件并发安全）
                update_kw = {
                    'passed_cases': F('passed_cases') + passed_cases,
                    'failed_cases': F('failed_cases') + failed_cases,
                    'skipped_cases': F('skipped_cases') + skipped_cases,
                }
                TaskExecutionLog.objects.filter(id=scheduled_log_id).update(**update_kw)
                # 回填 Pytest 执行日志（stdout/stderr）到 step_log
                step_content = (suite_detail.log or '').strip()
                if not step_content:
                    stdout = result_data.get('stdout', '')
                    stderr = result_data.get('stderr', '')
                    if stdout or stderr:
                        step_content = "=== 测试套件执行日志 ===\n"
                        if stdout:
                            step_content += f"\n--- 标准输出 ---\n{stdout}\n"
                        if stderr:
                            step_content += f"\n--- 错误输出 ---\n{stderr}\n"
                if step_content:
                    TaskExecutionLog.objects.filter(id=scheduled_log_id).update(
                        step_log=Concat(
                            Coalesce(F('step_log'), Value('')),
                            Value('\n\n'),
                            Value(step_content),
                        ),
                    )
                exec_log.refresh_from_db()
                exec_log.status = 'success' if exec_log.failed_cases == 0 else 'failed'
                exec_log.end_time = timezone.now()
                exec_log.total_cases = exec_log.total_cases or total_cases
                save_fields = ['status', 'end_time', 'total_cases']
                if report_url:
                    base = (getattr(settings, 'FRONTEND_BASE_URL', None) or getattr(settings, 'SITE_URL', 'http://127.0.0.1:8000')).rstrip('/')
                    # report_url 不含 /media/ 前缀，供前端拼接；完整 URL 需加 /media/
                    exec_log.report_url = f"{base}/media{report_url}" if not report_url.startswith('http') else report_url
                    exec_log.allure_report_url = report_url
                    save_fields.extend(['report_url', 'allure_report_url'])
                exec_log.save(update_fields=save_fields)

                trigger_notification(
                    scheduled_task_id=exec_log.task_id,
                    execution_log=exec_log,
                    result={
                        'total_cases': exec_log.total_cases,
                        'passed_cases': exec_log.passed_cases,
                        'failed_cases': exec_log.failed_cases,
                    },
                )
                logger.info(f"成功回填定时任务日志 {scheduled_log_id} 并触发企微通知")
            except Exception as e:
                logger.error(f"回填定时任务日志或触发通知失败: {e}", exc_info=True)

        # 确保从数据库获取最新的报告路径（优先使用持久化 URL）
        if report_url:
            allure_report = report_url
        elif not allure_report and suite_detail.allure_report:
            allure_report = suite_detail.allure_report
        if not allure_report and execution.report_path:
            allure_report = execution.report_path

        # 更新任务状态
        update_task_progress(task_instance, 100, final_message)
        
        logger.info(f"测试套件执行完成: {final_message}, allure_report: {allure_report}")
        
        return {
            'success': True,
            'status': 'completed',
            'message': final_message,
            'execution_id': execution.id,
            'total_cases': total_cases,
            'passed_cases': passed_cases,
            'failed_cases': failed_cases,
            'skipped_cases': skipped_cases,
            'pass_rate': execution.pass_rate,
            'execution_results': execution_results,
            'allure_report': allure_report,  # 添加allure_report字段
            'result': {
                'allure_report': allure_report,
                'stdout': result_data.get('stdout', ''),
                'stderr': result_data.get('stderr', ''),
                'test_files': result_data.get('test_files', [])
            }
        }
        
    except WebUITestExecution.DoesNotExist:
        error_msg = f"测试套件执行记录不存在: {execution_id}"
        logger.error(error_msg)
        return build_error_result(None, error_msg)
    except Exception as e:
        error_msg = f"测试套件执行任务异常: {str(e)}"
        logger.error(error_msg, exc_info=True)
        
        # 尝试更新执行状态
        try:
            execution = WebUITestExecution.objects.get(id=execution_id)
            execution.status = 'failed'
            execution.error_message = error_msg
            execution.save()
        except Exception:
            pass
        
        return build_error_result(task_instance.request.id, error_msg)