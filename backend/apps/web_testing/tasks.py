"""Celery tasks for Web UI script generation and independent script execution."""
from __future__ import annotations

import logging
import os
import shutil
import uuid
from typing import Any, Dict

from celery import shared_task
from celery.result import AsyncResult
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.utils import timezone

from ai_core.midscene_script_agent import create_midscene_agent
from common.task import (
    build_error_result,
    execute_async_task_with_progress,
    execute_async_task_with_websocket,
    update_task_progress,
)
from projects.models import Environment, Project

from .constants import WEBUI_BROWSER_ENGINE, normalize_webui_execution_options
from .execution_diagnostics import friendly_failure_summary
from .execution_variables import merge_execution_variables, pop_runtime_variables
from .models import (
    MidSceneScript,
    WebUITestExecution,
    WebUITestSuiteCaseExecution,
)
from .project_access import EDIT, get_project_for_user

User = get_user_model()
logger = logging.getLogger(__name__)


def _failure_screenshot_paths(execution_id: int, filename: str):
    """Return controlled absolute and persisted paths for one PNG screenshot."""
    root = os.path.abspath(os.path.join(str(settings.MEDIA_ROOT), 'webui_failure_screenshots'))
    execution_dir = os.path.join(root, f'execution_{int(execution_id)}')
    os.makedirs(execution_dir, exist_ok=True)
    safe_filename = (
        filename
        if filename.endswith('.png') and os.path.basename(filename) == filename
        else 'failure.png'
    )
    absolute = os.path.join(execution_dir, safe_filename)
    relative = os.path.relpath(absolute, str(settings.MEDIA_ROOT)).replace(os.sep, '/')
    return absolute, relative


def _normalize_persisted_screenshot_path(execution_id: int, value):
    """Accept only generated paths below the current execution directory."""
    if not value:
        return None
    text = str(value).replace('\\', '/')
    expected_prefix = f'webui_failure_screenshots/execution_{int(execution_id)}/'
    if os.path.isabs(text):
        media_root = os.path.abspath(str(settings.MEDIA_ROOT))
        candidate = os.path.abspath(text)
        if os.path.commonpath([media_root, candidate]) != media_root:
            return None
        text = os.path.relpath(candidate, media_root).replace(os.sep, '/')
    if not text.startswith(expected_prefix) or not text.endswith('.png'):
        return None
    filename = text[len(expected_prefix):]
    if not filename or '/' in filename or filename in {'.', '..'}:
        return None
    return text


def _remove_failure_screenshots(execution_id: int):
    target = os.path.abspath(
        os.path.join(
            str(settings.MEDIA_ROOT),
            'webui_failure_screenshots',
            f'execution_{int(execution_id)}',
        )
    )
    try:
        shutil.rmtree(target)
    except FileNotFoundError:
        return
    except Exception:
        logger.warning('删除执行 %s 的失败截图目录失败', execution_id, exc_info=True)


@shared_task(bind=True, name='web_testing.generate_webui_script_generation_v2')
def generate_webui_script_generation_v2_task(self, generation_id: str):
    """Run the durable AI + Playwright MCP generation pipeline by record ID."""
    from .generation_orchestrator import run_v2_generation

    return run_v2_generation(str(generation_id), celery_task_id=self.request.id)


def _run_test_script(
    script_content: str,
    base_url: str,
    options: dict | None = None,
    failure_screenshot_path: str | None = None,
    environment_variables: dict[str, str] | None = None,
) -> Dict[str, Any]:
    """Execute one complete Python Playwright script in an isolated workspace."""
    from .playwright_python_runner import playwright_runner

    script_id = str(uuid.uuid4())
    execution_options = normalize_webui_execution_options(options)
    logger.info(
        '开始执行独立 WebUI 脚本: script_id=%s browser=%s headed=%s timeout=%s',
        script_id,
        WEBUI_BROWSER_ENGINE,
        execution_options['headed'],
        execution_options['timeout'],
    )
    result = playwright_runner(
        script_id=script_id,
        script_content=script_content,
        base_url=base_url,
        options=execution_options,
        failure_screenshot_path=failure_screenshot_path,
        environment_variables=environment_variables or {},
    )
    payload = {
        'status': 'passed' if result.get('success') else 'failed',
        'script_id': script_id,
        'stdout': result.get('stdout', ''),
        'stderr': result.get('stderr', ''),
        'test_file': result.get('test_file', ''),
        'return_code': result.get('return_code', 0 if result.get('success') else 1),
        'allure_report': result.get('allure_report', ''),
        'screenshot_path': result.get('screenshot_path'),
    }
    return {
        'success': bool(result.get('success')),
        'error': result.get('error', '') if not result.get('success') else '',
        'result': payload,
        'log': result.get('stdout') or result.get('stderr') or '测试执行完成',
    }


def _raw_execution_log(result_data: dict[str, Any]) -> str:
    sections = []
    if result_data.get('stdout'):
        sections.append(f"--- 标准输出 ---\n{result_data['stdout']}")
    if result_data.get('stderr'):
        sections.append(f"--- 错误输出 ---\n{result_data['stderr']}")
    return '\n\n'.join(sections)


@shared_task(bind=True, name='web_testing.execute_webui_test_case')
def execute_webui_test_case_task(
    self,
    execution_id: int,
    options: dict | None = None,
    script_content: str | None = None,
    base_url: str | None = None,
):
    return execute_async_task_with_progress(
        self,
        'webui_test_case_execution',
        _execute_webui_test_case_logic,
        execution_id,
        options or {},
        script_content,
        base_url,
    )


def _execute_webui_test_case_logic(
    task_instance,
    execution_id: int,
    options: dict | None = None,
    script_content: str | None = None,
    base_url: str | None = None,
) -> Dict[str, Any]:
    """Execute one saved test case with one-time variable overrides."""
    execution = None
    case_detail = None
    test_case = None
    try:
        update_task_progress(task_instance, 10, '正在读取测试用例...')
        execution = WebUITestExecution.objects.select_related('environment').get(id=execution_id)
        case_detail = execution.case_execution_detail
        test_case = case_detail.test_case
        if test_case is None:
            raise ValueError('测试用例已删除，无法继续执行')
        options = normalize_webui_execution_options(options)

        execution.task_id = task_instance.request.id
        execution.status = 'running'
        execution.error_message = ''
        execution.browser = WEBUI_BROWSER_ENGINE
        execution.start_time = timezone.now()
        execution.save()

        case_detail.status = 'running'
        case_detail.start_time = execution.start_time
        case_detail.save()
        test_case.last_execute_status = 'running'
        test_case.last_error_message = ''
        test_case.save(update_fields=['last_execute_status', 'last_error_message'])

        script_content = (script_content or test_case.test_script_content or '').strip()
        if not script_content:
            raise ValueError('测试脚本内容为空，无法执行')
        base_url = (base_url or '').rstrip('/')
        if not base_url:
            raise ValueError('WebUI 测试环境缺少基础 URL')

        update_task_progress(task_instance, 45, '正在执行测试脚本...')
        screenshot_absolute, screenshot_relative = _failure_screenshot_paths(
            execution.id, 'single_case.png'
        )
        runtime_variables = pop_runtime_variables(execution.id)
        result = _run_test_script(
            script_content,
            base_url,
            options,
            failure_screenshot_path=screenshot_absolute,
            environment_variables=merge_execution_variables(
                (execution.environment.config or {}).get('variables') or {},
                test_case.variables,
                runtime_variables,
            ),
        )
        result_data = result.get('result') or {}
        end_time = timezone.now()
        duration = (end_time - execution.start_time).total_seconds()
        succeeded = bool(result.get('success'))
        error_message = '' if succeeded else friendly_failure_summary(
            result_data.get('stdout', ''),
            result_data.get('stderr', ''),
            result.get('error', ''),
        )

        execution.status = 'passed' if succeeded else 'failed'
        execution.error_message = error_message
        execution.end_time = end_time
        execution.duration = duration
        execution.log_path = result_data.get('test_file') or ''
        execution.report_path = result_data.get('allure_report') or ''
        execution.save()

        case_detail.status = execution.status
        case_detail.end_time = end_time
        case_detail.duration = duration
        case_detail.error_message = error_message or None
        case_detail.log = _raw_execution_log(result_data)
        persisted = _normalize_persisted_screenshot_path(
            execution.id,
            result_data.get('screenshot_path') or screenshot_relative,
        )
        if persisted and os.path.exists(os.path.join(str(settings.MEDIA_ROOT), persisted)):
            case_detail.screenshot_path = persisted
        case_detail.save()

        test_case.last_execute_status = execution.status
        test_case.last_execute_time = end_time
        test_case.last_error_message = error_message[:500]
        test_case.save(
            update_fields=['last_execute_status', 'last_execute_time', 'last_error_message']
        )
        update_task_progress(task_instance, 100, '测试用例执行完成')
        return {
            'success': succeeded,
            'status': 'completed',
            'message': f'WebUI 测试用例执行{"成功" if succeeded else "失败"}',
            'execution_id': execution.id,
            'execution_status': execution.status,
            'result': result_data,
            'error': error_message,
        }
    except WebUITestExecution.DoesNotExist:
        return build_error_result(None, f'测试执行记录不存在: {execution_id}')
    except Exception as exc:
        error_message = f'执行 WebUI 测试用例失败: {exc}'
        logger.error(error_message, exc_info=True)
        end_time = timezone.now()
        if execution is not None:
            execution.status = 'failed'
            execution.error_message = error_message
            execution.end_time = end_time
            execution.save(update_fields=['status', 'error_message', 'end_time', 'updated_at'])
        if case_detail is not None:
            case_detail.status = 'failed'
            case_detail.error_message = error_message
            case_detail.end_time = end_time
            case_detail.save(update_fields=['status', 'error_message', 'end_time'])
        if test_case is not None:
            test_case.last_execute_status = 'failed'
            test_case.last_execute_time = end_time
            test_case.last_error_message = error_message[:500]
            test_case.save(
                update_fields=['last_execute_status', 'last_execute_time', 'last_error_message']
            )
        return build_error_result(None, error_message)


@shared_task(bind=True, name='web_testing.generate_midscene_script')
def generate_midscene_script_task(self, script_id: int, user_id: int, project_id: int):
    return execute_async_task_with_websocket(
        self,
        'midscene_script_generation',
        _execute_midscene_script_generation,
        script_id,
        user_id,
        project_id,
    )


def _execute_midscene_script_generation(
    task_instance,
    script_id: int,
    user_id: int,
    project_id: int,
) -> Dict[str, Any]:
    """Keep the App automation generation flow isolated from Web UI scripts."""
    script = None
    try:
        update_task_progress(task_instance, 10, '正在获取用户和项目信息...')
        user = User.objects.get(id=user_id)
        get_project_for_user(project_id, user, EDIT, expected_project_type='app')
        script = MidSceneScript.objects.get(id=script_id, project_id=project_id)
        script.task_id = task_instance.request.id
        script.status = 'running'
        script.save()

        update_task_progress(task_instance, 40, '正在生成 MidScene 脚本...')
        agent = create_midscene_agent(user=user, user_id=user_id, enable_streaming=True)
        result = agent.run(
            description=script.natural_language,
            screenshot_b64=script.screenshot_b64,
        )
        if result.get('success'):
            script.script_content = result['script']
            script.status = 'completed'
            script.is_executed = True
            script.execution_result = {
                'model_info': result.get('model_info', {}),
                'model_type': result.get('model_type', 'unknown'),
                'generated_at': timezone.now().isoformat(),
            }
            script.save()
            update_task_progress(task_instance, 100, 'MidScene 脚本生成完成')
            return {
                'success': True,
                'status': 'completed',
                'message': 'MidScene 脚本生成成功',
                'script_id': script_id,
                'script': result['script'],
            }
        error = result.get('error', '未知错误')
        script.status = 'failed'
        script.execution_error = error
        script.save()
        return {
            'success': False,
            'status': 'completed',
            'message': f'MidScene 脚本生成失败: {error}',
            'error': error,
            'script_id': script_id,
        }
    except (User.DoesNotExist, Project.DoesNotExist, MidSceneScript.DoesNotExist) as exc:
        return build_error_result(None, f'资源不存在: {exc}')
    except Exception as exc:
        logger.error('MidScene 脚本生成任务异常: %s', exc, exc_info=True)
        if script is not None:
            script.status = 'failed'
            script.execution_error = str(exc)
            script.save()
        return build_error_result(None, f'MidScene 脚本生成任务异常: {exc}')


@shared_task(name='web_testing.cancel_task')
def cancel_task(task_id: str) -> Dict[str, Any]:
    """Set cooperative cancellation and stop queued/running Celery work."""
    try:
        cache.set(f'celery:cancel:{task_id}', True, timeout=60 * 60)
        AsyncResult(task_id).revoke(terminate=True)
        now = timezone.now()
        WebUITestExecution.objects.filter(task_id=task_id).update(
            status='stopped',
            error_message='任务已取消',
            end_time=now,
        )
        MidSceneScript.objects.filter(task_id=task_id).update(
            status='cancelled',
            completed_at=now,
        )
        return {'success': True, 'message': '任务已取消', 'task_id': task_id}
    except Exception as exc:
        logger.error('取消任务失败: %s', exc, exc_info=True)
        return {'success': False, 'error': f'取消任务失败: {exc}', 'task_id': task_id}


def _finalize_scheduled_execution(
    scheduled_log_id: int | None,
    *,
    total_cases: int,
    passed_cases: int,
    failed_cases: int,
    skipped_cases: int,
    log: str,
) -> None:
    """Update scheduled-task aggregates without changing suite execution semantics."""
    if not scheduled_log_id:
        return
    try:
        from django.db.models import F, Value
        from django.db.models.functions import Coalesce, Concat
        from notifications.services import trigger_notification
        from scheduled_tasks.models import TaskExecutionLog

        TaskExecutionLog.objects.filter(id=scheduled_log_id).update(
            passed_cases=F('passed_cases') + passed_cases,
            failed_cases=F('failed_cases') + failed_cases,
            skipped_cases=F('skipped_cases') + skipped_cases,
        )
        if log:
            TaskExecutionLog.objects.filter(id=scheduled_log_id).update(
                step_log=Concat(
                    Coalesce(F('step_log'), Value('')),
                    Value('\n\n'),
                    Value(log),
                )
            )
        execution_log = TaskExecutionLog.objects.get(id=scheduled_log_id)
        execution_log.status = 'success' if execution_log.failed_cases == 0 else 'failed'
        execution_log.end_time = timezone.now()
        execution_log.total_cases = execution_log.total_cases or total_cases
        execution_log.save(update_fields=['status', 'end_time', 'total_cases'])
        trigger_notification(
            scheduled_task_id=execution_log.task_id,
            execution_log=execution_log,
            result={
                'total_cases': execution_log.total_cases,
                'passed_cases': execution_log.passed_cases,
                'failed_cases': execution_log.failed_cases,
            },
        )
    except Exception:
        logger.error('回填定时任务日志或触发通知失败', exc_info=True)


@shared_task(bind=True, name='web_testing.execute_webui_test_suite')
def execute_webui_test_suite_task(
    self,
    execution_id: int,
    user_id: int | None = None,
    options: dict | None = None,
    scheduled_log_id: int | None = None,
):
    task_options = dict(options or {})
    task_options['scheduled_log_id'] = scheduled_log_id
    return execute_async_task_with_progress(
        self,
        'webui_test_suite_execution',
        _execute_webui_test_suite_logic,
        execution_id,
        user_id,
        task_options,
    )


def _execute_webui_test_suite_logic(
    task_instance,
    execution_id: int,
    user_id: int | None,
    options: dict | None = None,
) -> Dict[str, Any]:
    """Run suite members sequentially in isolated workspaces and continue on failure."""
    execution = None
    suite_detail = None
    options = dict(options or {})
    scheduled_log_id = options.pop('scheduled_log_id', None)
    try:
        query = WebUITestExecution.objects.select_related(
            'environment',
            'suite_execution_detail__test_suite',
        ).filter(id=execution_id, exec_type='suite')
        if user_id is not None:
            query = query.filter(executor_id=user_id)
        execution = query.get()
        suite_detail = execution.suite_execution_detail
        suite = suite_detail.test_suite
        if suite is None:
            raise ValueError('测试套件已删除，无法继续执行')
        memberships = list(
            suite.case_memberships.select_related('test_case').order_by('order', 'id')
        )
        if not memberships:
            raise ValueError('测试套件中没有测试用例')
        if execution.environment is None:
            raise ValueError('执行 WebUI 测试套件必须指定测试环境')
        environment = Environment.objects.get(
            id=execution.environment_id,
            project_id=suite.project_id,
            category=Environment.EnvironmentCategory.WEB,
            is_active=True,
        )
        base_url = ((environment.config or {}).get('base_url') or '').rstrip('/')
        if not base_url:
            raise ValueError('WebUI 测试环境缺少基础 URL')

        options = normalize_webui_execution_options(options)
        started_at = timezone.now()
        execution.task_id = task_instance.request.id
        execution.status = 'running'
        execution.error_message = ''
        execution.browser = WEBUI_BROWSER_ENGINE
        execution.start_time = started_at
        execution.save()
        suite_detail.start_time = started_at
        suite_detail.total_cases = len(memberships)
        suite_detail.passed_cases = 0
        suite_detail.failed_cases = 0
        suite_detail.skipped_cases = 0
        suite_detail.case_executions.all().delete()
        suite_detail.save()

        runtime_variables = pop_runtime_variables(execution.id)
        environment_variables = (environment.config or {}).get('variables') or {}
        passed_cases = failed_cases = skipped_cases = 0
        execution_results = []
        log_sections = [f'=== 测试套件：{suite.name} ===']

        for index, membership in enumerate(memberships, start=1):
            test_case = membership.test_case
            progress = 15 + int((index - 1) / len(memberships) * 75)
            update_task_progress(
                task_instance,
                progress,
                f'正在执行第 {index}/{len(memberships)} 个用例：{test_case.title}',
            )
            case_started_at = timezone.now()
            case_execution = WebUITestSuiteCaseExecution.objects.create(
                suite_execution=suite_detail,
                test_case=test_case,
                name=test_case.title,
                status='running',
            )
            script_content = (test_case.test_script_content or '').strip()
            if not script_content:
                skipped_cases += 1
                case_execution.status = 'skipped'
                case_execution.error_message = '测试用例没有可执行脚本'
                case_execution.duration = 0
                case_execution.save()
                execution_results.append({
                    'test_case_id': test_case.id,
                    'test_case_title': test_case.title,
                    'status': 'skipped',
                    'error_message': case_execution.error_message,
                })
                log_sections.append(
                    f'\n--- {index}. {test_case.title} [SKIPPED] ---\n{case_execution.error_message}'
                )
                continue

            screenshot_absolute, screenshot_relative = _failure_screenshot_paths(
                execution.id,
                f'suite_case_{index}_{test_case.id}.png',
            )
            try:
                variables = merge_execution_variables(
                    environment_variables,
                    test_case.variables,
                    suite.variables,
                    runtime_variables,
                )
                result = _run_test_script(
                    script_content,
                    base_url,
                    options,
                    failure_screenshot_path=screenshot_absolute,
                    environment_variables=variables,
                )
                result_data = result.get('result') or {}
                succeeded = bool(result.get('success'))
                error_message = '' if succeeded else friendly_failure_summary(
                    result_data.get('stdout', ''),
                    result_data.get('stderr', ''),
                    result.get('error', ''),
                )
            except Exception as exc:
                logger.error('套件用例执行异常: case_id=%s', test_case.id, exc_info=True)
                result = {'success': False, 'error': str(exc)}
                result_data = {}
                succeeded = False
                error_message = f'执行准备失败: {exc}'

            case_ended_at = timezone.now()
            case_execution.status = 'passed' if succeeded else 'failed'
            case_execution.duration = (case_ended_at - case_started_at).total_seconds()
            case_execution.error_message = error_message or None
            case_execution.log = _raw_execution_log(result_data)
            case_execution.stdout = result_data.get('stdout', '')
            persisted = _normalize_persisted_screenshot_path(
                execution.id,
                result_data.get('screenshot_path') or screenshot_relative,
            )
            if persisted and os.path.exists(os.path.join(str(settings.MEDIA_ROOT), persisted)):
                case_execution.screenshot_path = persisted
            case_execution.save()

            if succeeded:
                passed_cases += 1
            else:
                failed_cases += 1
            test_case.last_execute_status = case_execution.status
            test_case.last_execute_time = case_ended_at
            test_case.last_error_message = error_message[:500]
            test_case.save(
                update_fields=['last_execute_status', 'last_execute_time', 'last_error_message']
            )
            execution_results.append({
                'test_case_id': test_case.id,
                'test_case_title': test_case.title,
                'status': case_execution.status,
                'error_message': error_message,
                'result': result_data,
            })
            log_sections.append(
                f'\n--- {index}. {test_case.title} [{case_execution.status.upper()}] ---\n'
                f'{case_execution.log or error_message or "执行完成"}'
            )

        ended_at = timezone.now()
        duration = (ended_at - started_at).total_seconds()
        all_skipped = passed_cases == 0 and failed_cases == 0
        succeeded = failed_cases == 0 and not all_skipped
        summary = (
            f'测试套件执行完成：通过 {passed_cases}，失败 {failed_cases}，跳过 {skipped_cases}'
        )
        error_message = '' if succeeded else (
            '测试套件没有可执行脚本' if all_skipped else f'测试套件中有 {failed_cases} 个用例失败'
        )
        full_log = '\n'.join(log_sections)

        suite_detail.passed_cases = passed_cases
        suite_detail.failed_cases = failed_cases
        suite_detail.skipped_cases = skipped_cases
        suite_detail.end_time = ended_at
        suite_detail.duration = duration
        suite_detail.log = full_log
        suite_detail.save()
        execution.status = 'passed' if succeeded else 'failed'
        execution.error_message = error_message
        execution.end_time = ended_at
        execution.duration = duration
        execution.save()

        _finalize_scheduled_execution(
            scheduled_log_id,
            total_cases=len(memberships),
            passed_cases=passed_cases,
            failed_cases=failed_cases,
            skipped_cases=skipped_cases,
            log=full_log,
        )
        update_task_progress(task_instance, 100, summary)
        return {
            'success': succeeded,
            'status': 'completed',
            'message': summary,
            'execution_id': execution.id,
            'total_cases': len(memberships),
            'passed_cases': passed_cases,
            'failed_cases': failed_cases,
            'skipped_cases': skipped_cases,
            'pass_rate': execution.pass_rate,
            'execution_results': execution_results,
            'error': error_message,
        }
    except WebUITestExecution.DoesNotExist:
        return build_error_result(None, f'测试套件执行记录不存在: {execution_id}')
    except Exception as exc:
        error_message = f'测试套件执行任务异常: {exc}'
        logger.error(error_message, exc_info=True)
        if execution is not None:
            execution.status = 'failed'
            execution.error_message = error_message
            execution.end_time = timezone.now()
            execution.save(update_fields=['status', 'error_message', 'end_time', 'updated_at'])
        if suite_detail is not None:
            suite_detail.end_time = timezone.now()
            suite_detail.log = error_message
            suite_detail.save(update_fields=['end_time', 'log'])
        _finalize_scheduled_execution(
            scheduled_log_id,
            total_cases=suite_detail.total_cases if suite_detail else 0,
            passed_cases=0,
            failed_cases=1,
            skipped_cases=0,
            log=error_message,
        )
        return build_error_result(task_instance.request.id, error_message)
