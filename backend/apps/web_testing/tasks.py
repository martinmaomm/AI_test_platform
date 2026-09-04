"""Celery tasks for Web UI script generation and independent script execution."""
from __future__ import annotations

import asyncio
import logging
import os
import shutil
import uuid
from difflib import unified_diff
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
from .assertion_state import evaluation_status
from .execution_diagnostics import friendly_failure_summary
from .execution_variables import merge_execution_variables, pop_runtime_variables
from .models import (
    MidSceneScript,
    WebUIScriptGeneration,
    WebUITestCaseExecutionDetail,
    WebUITestExecution,
    WebUITestSuiteCaseExecution,
)
from .project_access import EDIT, get_project_for_user

User = get_user_model()
logger = logging.getLogger(__name__)


def _run_generation_with_terminal_guard(generation_id: str, celery_task_id: str | None, runner):
    """Keep an unexpected Celery exception from leaving a generation non-terminal."""
    try:
        return runner(generation_id, celery_task_id=celery_task_id)
    except Exception:
        logger.exception('WebUI 生成 Celery 任务发生未处理异常: generation_id=%s', generation_id)
        from .generation_orchestrator import fail_unexpected_generation

        return fail_unexpected_generation(generation_id)


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


@shared_task(bind=True, name='web_testing.generate_webui_script_generation')
def generate_webui_script_generation_task(self, generation_id: str):
    """Run the durable AI + Playwright MCP generation pipeline by record ID."""
    from .generation_orchestrator import run_generation

    return _run_generation_with_terminal_guard(
        str(generation_id), self.request.id, run_generation,
    )


@shared_task(bind=True, name='web_testing.retry_webui_script_generation_from_trace')
def retry_webui_script_generation_from_trace_task(self, generation_id: str):
    from .generation_orchestrator import run_generation_from_trace

    return _run_generation_with_terminal_guard(
        str(generation_id), self.request.id, run_generation_from_trace,
    )


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
    operation_success = bool(result.get('operation_success', result.get('success')))
    status, assertion_state, runtime_assertion_count = evaluation_status(
        script_content,
        operation_success=operation_success,
        runtime_assertion_count=result.get('runtime_assertion_count'),
    )
    payload = {
        'status': status,
        'operation_success': operation_success,
        'evaluation_status': status,
        'assertion_state': assertion_state,
        'runtime_assertion_count': runtime_assertion_count,
        'script_id': script_id,
        'stdout': result.get('stdout', ''),
        'stderr': result.get('stderr', ''),
        'test_file': result.get('test_file', ''),
        'return_code': result.get('return_code', 0 if result.get('success') else 1),
        'allure_report': result.get('allure_report', ''),
        'screenshot_path': result.get('screenshot_path'),
    }
    return {
        'success': operation_success,
        'operation_success': operation_success,
        'evaluation_status': status,
        'assertion_state': assertion_state,
        'runtime_assertion_count': runtime_assertion_count,
        'error': result.get('error', '') if not operation_success else '',
        'result': payload,
        'log': result.get('stdout') or result.get('stderr') or '测试执行完成',
    }


def _incomplete_message(assertion_state: dict[str, Any], runtime_assertion_count: int) -> str:
    pending_count = int(assertion_state.get('pending_count') or 0)
    if pending_count:
        return f'操作已完成，但仍有 {pending_count} 项断言待补充；删除对应 marker 并补入真实断言后重跑。'
    if runtime_assertion_count == 0:
        return '操作已完成，但本次未成功执行任何真实断言，验证未完成。'
    return '操作已完成，但当前脚本验证条件未完成。'


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
        operation_success = bool(result.get('operation_success', result.get('success')))
        execution_status, assertion_state, runtime_assertion_count = evaluation_status(
            script_content,
            operation_success=operation_success,
            runtime_assertion_count=result.get('runtime_assertion_count'),
        )
        error_message = '' if operation_success else friendly_failure_summary(
            result_data.get('stdout', ''),
            result_data.get('stderr', ''),
            result.get('error', ''),
        )
        if execution_status == 'incomplete':
            error_message = _incomplete_message(assertion_state, runtime_assertion_count)

        execution.status = execution_status
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
            'success': operation_success,
            'operation_success': operation_success,
            'evaluation_status': execution_status,
            'assertion_state': assertion_state,
            'runtime_assertion_count': runtime_assertion_count,
            'status': 'completed',
            'message': (
                'WebUI 测试用例验证通过' if execution_status == 'passed'
                else 'WebUI 测试用例验证未完成' if execution_status == 'incomplete'
                else 'WebUI 测试用例执行失败'
            ),
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


@shared_task(bind=True, name='web_testing.debug_webui_script_generation')
def debug_webui_script_generation_task(
    self,
    generation_id: str,
    execution_id: int,
    locked_revision: int,
    locked_hash: str,
):
    """Run an explicitly approved draft without creating a WebUITestCase."""
    from .generation_workspace import (
        base_url_fingerprint, environment_fingerprint, finish_debug, mark_debug_running, script_hash,
    )

    execution = None
    detail = None
    runtime_variables = []
    try:
        generation = mark_debug_running(
            generation_id, execution_id=execution_id,
            locked_revision=locked_revision, locked_hash=locked_hash, task_id=self.request.id,
        )
        execution = WebUITestExecution.objects.select_related('environment').get(
            pk=execution_id, exec_type='case', project_id=WebUIScriptGeneration.objects.get(pk=generation_id).project_id,
        )
        detail = execution.case_execution_detail
        if generation is None:
            return build_error_result(self.request.id, '调试任务重复或已过期，未再次执行。')

        environment = Environment.objects.get(
            id=generation.environment_id, project_id=generation.project_id,
            category=Environment.EnvironmentCategory.WEB, is_active=True,
        )
        verification = (generation.workspace or {}).get('verification') or {}
        if (
            verification.get('environment_fingerprint') != environment_fingerprint(environment.config)
            or verification.get('base_url_fingerprint') != base_url_fingerprint(environment.config)
        ):
            raise ValueError('调试环境配置已变化，过期调试任务未执行。')
        base_url = ((environment.config or {}).get('base_url') or '').rstrip('/')
        if not base_url:
            raise ValueError('WebUI 测试环境缺少基础 URL')
        script = (generation.script_draft or '').strip()
        if script_hash(script) != locked_hash:
            raise ValueError('草稿内容已变化，过期调试任务未执行')
        from .script_contract import normalize_for_storage
        normalize_for_storage(script)

        execution.task_id = self.request.id
        execution.status = 'running'
        execution.error_message = ''
        execution.browser = WEBUI_BROWSER_ENGINE
        execution.start_time = timezone.now()
        execution.save(update_fields=['task_id', 'status', 'error_message', 'browser', 'start_time', 'updated_at'])
        detail.status = 'running'
        detail.start_time = execution.start_time
        detail.save(update_fields=['status', 'start_time'])
        runtime_variables = pop_runtime_variables(execution.id)
        if verification.get('runtime_variables_present') and not runtime_variables:
            raise ValueError('一次性运行变量已过期，调试任务未执行。')
        screenshot_absolute, screenshot_relative = _failure_screenshot_paths(execution.id, 'generation_draft.png')
        result = _run_test_script(
            script, base_url, {}, failure_screenshot_path=screenshot_absolute,
            environment_variables=merge_execution_variables(
                (environment.config or {}).get('variables') or {},
                generation.workspace.get('variables') if isinstance(generation.workspace, dict) else [],
                runtime_variables,
            ),
        )
        result_data = result.get('result') or {}
        end_time = timezone.now()
        operation_success = bool(result.get('operation_success', result.get('success')))
        execution_status, assertion_state, runtime_assertion_count = evaluation_status(
            script,
            operation_success=operation_success,
            runtime_assertion_count=result.get('runtime_assertion_count'),
        )
        error_message = '' if operation_success else friendly_failure_summary(
            result_data.get('stdout', ''), result_data.get('stderr', ''), result.get('error', ''),
        )
        if execution_status == 'incomplete':
            error_message = _incomplete_message(assertion_state, runtime_assertion_count)
        execution.status = execution_status
        execution.error_message = error_message
        execution.end_time = end_time
        execution.duration = (end_time - execution.start_time).total_seconds()
        execution.log_path = result_data.get('test_file') or ''
        execution.report_path = result_data.get('allure_report') or ''
        execution.save(update_fields=['status', 'error_message', 'end_time', 'duration', 'log_path', 'report_path', 'updated_at'])
        detail.status = execution.status
        detail.end_time = end_time
        detail.duration = execution.duration
        detail.error_message = error_message or None
        detail.log = _raw_execution_log(result_data)
        persisted = _normalize_persisted_screenshot_path(execution.id, result_data.get('screenshot_path') or screenshot_relative)
        if persisted and os.path.exists(os.path.join(str(settings.MEDIA_ROOT), persisted)):
            detail.screenshot_path = persisted
        detail.save()
        finish_debug(
            generation_id, execution_id=execution.id, locked_revision=locked_revision, locked_hash=locked_hash,
            status=execution_status,
            diagnostics=[] if operation_success else [{'code': 'RUNTIME_FAILURE', 'message': error_message}],
            runtime_assertion_count=runtime_assertion_count,
            task_id=self.request.id,
        )
        return {
            'success': operation_success,
            'operation_success': operation_success,
            'evaluation_status': execution_status,
            'assertion_state': assertion_state,
            'runtime_assertion_count': runtime_assertion_count,
            'execution_id': execution.id,
            'execution_status': execution.status,
        }
    except Exception as exc:
        message = f'生成草稿调试失败: {exc}'
        logger.error('%s', message)
        ended_at = timezone.now()
        if execution is not None:
            execution.status = 'error'
            execution.error_message = message
            execution.end_time = ended_at
            execution.save(update_fields=['status', 'error_message', 'end_time', 'updated_at'])
        if detail is not None:
            detail.status = 'error'
            detail.error_message = message
            detail.end_time = ended_at
            detail.save(update_fields=['status', 'error_message', 'end_time'])
        try:
            finish_debug(
                generation_id, execution_id=execution_id, locked_revision=locked_revision, locked_hash=locked_hash,
                status='error', diagnostics=[{'code': 'RUNTIME_FAILURE', 'message': message}],
                task_id=self.request.id,
            )
        except Exception:
            logger.warning('回填生成草稿调试失败状态失败: generation_id=%s', generation_id, exc_info=True)
        return build_error_result(self.request.id, message)


@shared_task(bind=True, name='web_testing.repair_webui_script_generation')
def repair_webui_script_generation_task(self, generation_id: str, locked_revision: int, locked_hash: str):
    """Generate a code-only repair proposal; never replace or execute the draft."""
    from ai_core.model_manager import get_llm_manager
    from .generation_workspace import evaluate_workspace_draft
    from .model_service_errors import classify_model_service_error
    from .script_exploration_agent import ScriptExplorationAgent
    from .generation_workspace import (
        finish_repair_failure, mark_repair_running, store_repair_candidate,
    )

    generation = mark_repair_running(generation_id, locked_revision=locked_revision, locked_hash=locked_hash, task_id=self.request.id)
    if generation is None:
        return build_error_result(self.request.id, '草稿已变化，过期修复任务未执行。')
    try:
        workspace = generation.workspace if isinstance(generation.workspace, dict) else {}
        verification = workspace.get('verification') if isinstance(workspace.get('verification'), dict) else {}
        issues = verification.get('diagnostics') or []
        if not issues:
            raise ValueError('缺少运行失败诊断，需要人工补充证据或重新调试。')
        brief = generation.scenario_spec if isinstance(generation.scenario_spec, dict) else {}
        snapshot = generation.exploration_snapshot if isinstance(generation.exploration_snapshot, dict) else {}
        if brief.get('schema_version') != 5 or snapshot.get('schema_version') != 5:
            raise ValueError('仅支持当前 v5 草稿的代码修复；旧版记录请人工处理源码。')
        repair_brief = {
            **brief,
            'debug_diagnostics': [
                {
                    'code': str(item.get('code') or ''),
                    'message': str(item.get('message') or ''),
                }
                for item in issues if isinstance(item, dict)
            ],
        }
        manager = get_llm_manager(config_id=generation.model_info['config_id'])
        agent = ScriptExplorationAgent(
            llm_model=manager.current_llm,
            mcp_config={},
            generation_id=str(generation.pk),
            cancel_check=lambda: bool(cache.get(f'celery:cancel:{self.request.id}')),
            exploration_timeout_seconds=generation.exploration_timeout_seconds,
            checkpoint_callback=None,
        )
        result = asyncio.run(agent.generate(
            brief=repair_brief,
            start_path=generation.start_path,
            target_url=generation.target_url_safe,
            credentials=None,
            saved_snapshot=snapshot,
            script_draft=generation.script_draft,
            code_only=True,
        ))
        candidate = str(getattr(result, 'script_draft', '') or '')
        if not candidate.strip():
            raise ValueError(str(getattr(result, 'error_message', '') or '修复代理没有返回候选脚本。'))
        quality = evaluate_workspace_draft(
            candidate, start_path=generation.start_path,
            snapshot=getattr(result, 'snapshot', None) or snapshot,
        )
        diff = ''.join(unified_diff(
            generation.script_draft.splitlines(keepends=True), candidate.splitlines(keepends=True),
            fromfile='原草稿', tofile='修复候选', n=3,
        ))[:20000]
        result_error_code = str(getattr(result, 'error_code', '') or '')
        result_error_message = str(getattr(result, 'error_message', '') or '')
        if not store_repair_candidate(
            generation.pk, locked_revision=locked_revision, locked_hash=locked_hash,
            candidate_script=candidate, candidate_diff=diff, quality_report=quality,
            candidate_error_code=result_error_code, candidate_error_message=result_error_message,
            task_id=self.request.id,
        ):
            return build_error_result(self.request.id, '草稿已变化，过期修复候选未保存。')
        if result_error_code:
            return {
                'success': False, 'status': 'candidate_needs_review', 'generation_id': str(generation.pk),
                'error_code': result_error_code,
                'message': result_error_message or '修复代理返回了候选，但未完成整个修复过程，请人工审核。',
            }
        return {
            'success': True, 'status': 'candidate_ready', 'generation_id': str(generation.pk),
            'message': '修复候选已保存；原草稿未修改，需人工比较确认后再调试。',
        }
    except Exception as exc:
        model_error = classify_model_service_error(exc, stage='repairing')
        code = model_error[0] if model_error else 'REPAIR_CANDIDATE_UNAVAILABLE'
        message = model_error[1] if model_error else '修复服务未能生成可审核候选，请人工审核或重新调试。'
        logger.error('生成草稿修复失败: generation_id=%s', generation_id)
        finish_repair_failure(
            generation_id, locked_revision=locked_revision, locked_hash=locked_hash,
            message=message, task_id=self.request.id,
            blockers=[{'severity': 'blocker', 'code': code, 'message': message}],
        )
        return build_error_result(self.request.id, message)


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
    incomplete_cases: int,
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
        prior_incomplete = str(execution_log.error_message or '').startswith('验证未完成：')
        has_incomplete = incomplete_cases > 0 or prior_incomplete
        # The scheduled-task log has only success/failed states.  Preserve the
        # true per-suite failed count, but never mark an incomplete validation
        # as success or emit a passing notification.
        execution_log.status = 'success' if execution_log.failed_cases == 0 and not has_incomplete else 'failed'
        if has_incomplete:
            execution_log.error_message = (
                f'验证未完成：本次有 {incomplete_cases} 个 WebUI 用例尚未完成完整断言验证。'
                if incomplete_cases else '验证未完成：此前已有 WebUI 用例尚未完成完整断言验证。'
            )
        execution_log.end_time = timezone.now()
        execution_log.total_cases = execution_log.total_cases or total_cases
        execution_log.save(update_fields=['status', 'error_message', 'end_time', 'total_cases'])
        if not has_incomplete or execution_log.failed_cases > 0:
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
        suite_detail.incomplete_cases = 0
        suite_detail.skipped_cases = 0
        suite_detail.case_executions.all().delete()
        suite_detail.save()

        runtime_variables = pop_runtime_variables(execution.id)
        environment_variables = (environment.config or {}).get('variables') or {}
        passed_cases = failed_cases = incomplete_cases = skipped_cases = 0
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
                operation_success = bool(result.get('operation_success', result.get('success')))
                case_status, assertion_state, runtime_assertion_count = evaluation_status(
                    script_content,
                    operation_success=operation_success,
                    runtime_assertion_count=result.get('runtime_assertion_count'),
                )
                error_message = '' if operation_success else friendly_failure_summary(
                    result_data.get('stdout', ''),
                    result_data.get('stderr', ''),
                    result.get('error', ''),
                )
                if case_status == 'incomplete':
                    error_message = _incomplete_message(assertion_state, runtime_assertion_count)
            except Exception as exc:
                logger.error('套件用例执行异常: case_id=%s', test_case.id, exc_info=True)
                result = {'success': False, 'error': str(exc)}
                result_data = {}
                operation_success = False
                case_status = 'failed'
                assertion_state = {}
                runtime_assertion_count = 0
                error_message = f'执行准备失败: {exc}'

            case_ended_at = timezone.now()
            case_execution.status = case_status
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

            if case_status == 'passed':
                passed_cases += 1
            elif case_status == 'incomplete':
                incomplete_cases += 1
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
                'operation_success': operation_success,
                'assertion_state': assertion_state,
                'runtime_assertion_count': runtime_assertion_count,
                'error_message': error_message,
                'result': result_data,
            })
            log_sections.append(
                f'\n--- {index}. {test_case.title} [{case_execution.status.upper()}] ---\n'
                f'{case_execution.log or error_message or "执行完成"}'
            )

        ended_at = timezone.now()
        duration = (ended_at - started_at).total_seconds()
        all_skipped = passed_cases == 0 and failed_cases == 0 and incomplete_cases == 0
        operation_success = failed_cases == 0 and not all_skipped
        execution_status = (
            'failed' if failed_cases else
            'incomplete' if incomplete_cases else
            'passed' if operation_success else
            'failed'
        )
        summary = (
            f'测试套件执行完成：通过 {passed_cases}，验证未完成 {incomplete_cases}，失败 {failed_cases}，跳过 {skipped_cases}'
        )
        error_message = (
            '' if execution_status == 'passed' else
            '测试套件没有可执行脚本' if all_skipped else
            f'测试套件中有 {failed_cases} 个用例失败' if failed_cases else
            f'测试套件中有 {incomplete_cases} 个用例尚未完成验证'
        )
        full_log = '\n'.join(log_sections)

        suite_detail.passed_cases = passed_cases
        suite_detail.failed_cases = failed_cases
        suite_detail.incomplete_cases = incomplete_cases
        suite_detail.skipped_cases = skipped_cases
        suite_detail.end_time = ended_at
        suite_detail.duration = duration
        suite_detail.log = full_log
        suite_detail.save()
        execution.status = execution_status
        execution.error_message = error_message
        execution.end_time = ended_at
        execution.duration = duration
        execution.save()

        _finalize_scheduled_execution(
            scheduled_log_id,
            total_cases=len(memberships),
            passed_cases=passed_cases,
            failed_cases=failed_cases,
            incomplete_cases=incomplete_cases,
            skipped_cases=skipped_cases,
            log=full_log,
        )
        update_task_progress(task_instance, 100, summary)
        return {
            'success': operation_success,
            'operation_success': operation_success,
            'evaluation_status': execution_status,
            'status': 'completed',
            'message': summary,
            'execution_id': execution.id,
            'total_cases': len(memberships),
            'passed_cases': passed_cases,
            'failed_cases': failed_cases,
            'incomplete_cases': incomplete_cases,
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
            incomplete_cases=0,
            skipped_cases=0,
            log=error_message,
        )
        return build_error_result(task_instance.request.id, error_message)
