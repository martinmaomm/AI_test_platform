"""Celery tasks for Web UI script generation and independent script execution."""
from __future__ import annotations

import asyncio
import logging
import os
import shutil
import traceback
import uuid
from copy import deepcopy
from difflib import unified_diff
from typing import Any, Dict

from celery import shared_task
from celery.result import AsyncResult
from django.conf import settings
from django.core.cache import cache
from django.utils import timezone

from common.task import (
    build_error_result,
    execute_async_task_with_progress,
    update_task_progress,
)
from .constants import WEBUI_BROWSER_ENGINE, normalize_webui_execution_options
from .assertion_state import analyze_assertion_state, evaluation_status
from .execution_diagnostics import friendly_failure_summary
from .execution_variables import (
    merge_execution_variables, pop_runtime_variables, pop_repair_runtime_variables,
)
from .models import (
    WebUIScriptGeneration,
    WebUITestCaseExecutionDetail,
    WebUITestExecution,
    WebUITestSuiteCaseExecution,
)
from .project_access import EDIT, get_project_for_user

logger = logging.getLogger(__name__)


def _run_generation_with_terminal_guard(generation_id: str, celery_task_id: str | None, runner):
    """Keep an unexpected Celery exception from leaving a generation non-terminal."""
    try:
        return runner(generation_id, celery_task_id=celery_task_id)
    except Exception:
        logger.exception('WebUI 生成 Celery 任务发生未处理异常: generation_id=%s', generation_id)
        from .generation_orchestrator import fail_unexpected_generation

        return fail_unexpected_generation(generation_id, celery_task_id)


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


@shared_task(bind=True, name='web_testing.run_script_assistant_operation')
def run_script_assistant_operation_task(self, session_id: str, revision: int, task_id: str):
    """The single durable worker entry for saved-script assistant operations."""
    from .script_assistant import run_script_assistant_operation
    return run_script_assistant_operation(str(session_id), int(revision), str(task_id))


def _run_test_script(
    script_content: str,
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
        'return_code': result.get('return_code', 0 if result.get('success') else 1),
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
):
    return execute_async_task_with_progress(
        self,
        'webui_test_case_execution',
        _execute_webui_test_case_logic,
        execution_id,
        options or {},
        script_content,
    )


def _execute_webui_test_case_logic(
    task_instance,
    execution_id: int,
    options: dict | None = None,
    script_content: str | None = None,
) -> Dict[str, Any]:
    """Execute one saved test case with one-time variable overrides."""
    execution = None
    case_detail = None
    test_case = None
    try:
        update_task_progress(task_instance, 10, '正在读取测试用例...')
        execution = WebUITestExecution.objects.get(id=execution_id)
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

        # The API always creates this frozen source before dispatch.  Historical
        # rows remain non-runnable rather than silently reading mutable code.
        script_content = case_detail.source_script or ''
        if not script_content.strip():
            raise ValueError('测试脚本内容为空，无法执行')
        update_task_progress(task_instance, 45, '正在执行测试脚本...')
        screenshot_absolute, screenshot_relative = _failure_screenshot_paths(
            execution.id, 'single_case.png'
        )
        runtime_variables = pop_runtime_variables(execution.id)
        result = _run_test_script(
            script_content,
            case_detail.execution_options or options,
            failure_screenshot_path=screenshot_absolute,
            environment_variables=merge_execution_variables(
                case_detail.source_variables,
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
        # Execution logs live in the detail record, not the runner's temporary directory.
        execution.log_path = ''
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
        finish_debug, mark_debug_running, script_hash, variables_fingerprint, workspace_for_generation,
    )

    execution = None
    detail = None
    runtime_variables = []
    active_execution = None
    active_detail = None
    try:
        generation = mark_debug_running(
            generation_id, execution_id=execution_id,
            locked_revision=locked_revision, locked_hash=locked_hash, task_id=self.request.id,
        )
        execution = WebUITestExecution.objects.get(
            pk=execution_id, exec_type='case', project_id=WebUIScriptGeneration.objects.get(pk=generation_id).project_id,
        )
        detail = execution.case_execution_detail
        if generation is None:
            return build_error_result(self.request.id, '调试任务重复或已过期，未再次执行。')

        verification = (generation.workspace or {}).get('verification') or {}
        current_workspace = workspace_for_generation(generation)
        if (
            verification.get('target_url_fingerprint') != script_hash(generation.target_url)
            or verification.get('variables_fingerprint') != variables_fingerprint(current_workspace['variables'])
        ):
            raise ValueError('草稿变量定义或目标网址已变化，过期调试任务未执行。')
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
            script, {}, failure_screenshot_path=screenshot_absolute,
            environment_variables=merge_execution_variables(
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
        execution.log_path = ''
        execution.save(update_fields=['status', 'error_message', 'end_time', 'duration', 'log_path', 'updated_at'])
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
    """Generate and independently validate at most two reviewable candidates."""
    from ai_core.model_manager import get_llm_manager
    from .ai_assisted_debugging import (
        MAX_CANDIDATE_SCRIPT_CHARS, bounded_candidate_diff, candidate_leaks_runtime_values,
        failure_evidence, is_non_code_failure, redact_runtime_values, requires_directed_mcp,
    )
    from .generation_workspace import evaluate_workspace_draft, script_hash, update_repair_state
    from .model_service_errors import classify_model_service_error
    from .script_repair_policy import validate_assertion_preservation
    from .script_exploration_agent import ScriptExplorationAgent
    from .generation_workspace import (
        finish_repair_failure, mark_repair_running,
    )

    active_execution = None
    active_detail = None
    generation = mark_repair_running(generation_id, locked_revision=locked_revision, locked_hash=locked_hash, task_id=self.request.id)
    if generation is None:
        return build_error_result(self.request.id, '草稿已变化，过期修复任务未执行。')
    runtime_variables = []
    attempts = []
    latest = None
    active_round = 0
    active_mcp_checked = False

    def apply_assertion_regression_blocker(
        quality_report: dict[str, Any], candidate_script: str, baseline_script: str,
    ) -> bool:
        baseline_assertions = analyze_assertion_state(baseline_script)
        candidate_assertions = analyze_assertion_state(candidate_script)
        semantic_blockers = validate_assertion_preservation(
            baseline_script, candidate_script,
        )
        if not semantic_blockers and (
            candidate_assertions['confirmed_count'] >= baseline_assertions['confirmed_count']
            and candidate_assertions['pending_count'] <= baseline_assertions['pending_count']
        ):
            return False
        quality_report['status'] = 'needs_review'
        quality_report['assertion_state'] = candidate_assertions
        messages = [item['message'] for item in semantic_blockers]
        if (
            candidate_assertions['confirmed_count'] < baseline_assertions['confirmed_count']
            or candidate_assertions['pending_count'] > baseline_assertions['pending_count']
        ):
            messages.append('候选减少了已确认断言或增加了待补充断言。')
        quality_report['blockers'] = list(quality_report.get('blockers') or []) + [{
            'level': 'blocker', 'code': 'ASSERTION_REGRESSION',
            'message': ' '.join(messages) or '候选断言保护检查未通过，不能执行验证。',
        }]
        return True

    try:
        runtime_variables = pop_repair_runtime_variables(generation_id, locked_revision, locked_hash)
        workspace = generation.workspace if isinstance(generation.workspace, dict) else {}
        verification = workspace.get('verification') if isinstance(workspace.get('verification'), dict) else {}
        execution_id = verification.get('execution_id')
        detail = None
        if execution_id:
            detail = WebUITestCaseExecutionDetail.objects.filter(execution_id=execution_id).first()
        issues = verification.get('diagnostics') or []
        if repair_runtime_required := bool((workspace.get('repair') or {}).get('runtime_variables_present')):
            if not runtime_variables:
                raise ValueError('一次性运行变量已过期，修复任务未执行。')
        effective_execution_variables = merge_execution_variables(
            workspace.get('variables') or [], runtime_variables,
        )
        if not issues and detail is None:
            raise ValueError('缺少运行失败诊断，需要人工补充证据或重新调试。')
        evidence = failure_evidence(
            stdout='', stderr='',
            log=(detail.log if detail else ''),
            fallback=str((issues[0] if issues else {}).get('message') or ''),
            runtime_variables=runtime_variables,
        )
        if is_non_code_failure(evidence):
            if not update_repair_state(
                generation_id, locked_revision=locked_revision, locked_hash=locked_hash,
                message='检测到网络、账号、环境依赖或任务取消等非代码问题，已停止自动修改脚本。',
                summary=evidence['summary'], final_status='failed', task_id=self.request.id,
            ):
                return build_error_result(self.request.id, '草稿已变化，过期修复任务未写回。')
            return build_error_result(self.request.id, '非代码错误不能通过自动改写脚本解决。')
        brief = generation.scenario_spec if isinstance(generation.scenario_spec, dict) else {}
        snapshot = generation.exploration_snapshot if isinstance(generation.exploration_snapshot, dict) else {}
        if brief.get('schema_version') != 5 or snapshot.get('schema_version') != 5:
            raise ValueError('仅支持当前 v5 草稿的代码修复；旧版记录请人工处理源码。')
        manager = get_llm_manager(config_id=generation.model_info['config_id'])
        attempt_script = generation.script_draft
        attempt_snapshot = snapshot
        for round_number in range(1, 3):
            active_round = round_number
            use_mcp = requires_directed_mcp(evidence)
            active_mcp_checked = use_mcp
            mcp_config = {}
            if use_mcp:
                from .generation_preflight import run_safety_preflight
                preflight = run_safety_preflight(generation, brief)
                if preflight.outcome != 'continue':
                    raise ValueError(preflight.message)
                mcp_config = preflight.mcp_config or {}
            phase = 'exploring' if use_mcp else 'analyzing'
            if not update_repair_state(generation_id, locked_revision=locked_revision, locked_hash=locked_hash,
                                       phase=phase, message=f'正在生成第 {round_number} 轮候选。', attempts=attempts, task_id=self.request.id):
                return build_error_result(self.request.id, '草稿已变化，过期修复任务未执行。')
            repair_brief = {
                **brief, 'repair_only': True, 'debug_diagnostics': evidence,
                # This dictionary is task-local.  It is never copied to snapshot/workspace.
                'runtime_input_values': effective_execution_variables,
                'repair_rules': ['不得删除、弱化或跳过原有断言以通过验证。', '不得在输出脚本、摘要或日志中回显 runtime_input_values。'],
            }
            repair_agent_id = str(uuid.uuid5(
                uuid.NAMESPACE_URL,
                f'automation:webui-repair:{generation.pk}:{self.request.id}:{round_number}',
            ))
            agent = ScriptExplorationAgent(
                llm_model=manager.current_llm, mcp_config=mcp_config, generation_id=repair_agent_id,
                cancel_check=lambda: bool(cache.get(f'celery:cancel:{self.request.id}')),
                exploration_timeout_seconds=generation.exploration_timeout_seconds, checkpoint_callback=None,
            )
            result = asyncio.run(agent.generate(
                brief=repair_brief, target_url=generation.target_url, saved_snapshot=attempt_snapshot,
                script_draft=attempt_script, code_only=not use_mcp,
            ))
            candidate = str(getattr(result, 'script_draft', '') or '')
            agent_error_code = str(getattr(result, 'error_code', '') or '')
            agent_error_message = redact_runtime_values(
                str(getattr(result, 'error_message', '') or ''), runtime_variables,
            )
            if agent_error_code:
                if not candidate.strip() or script_hash(candidate) == script_hash(attempt_script):
                    raise ValueError(agent_error_message or '修复智能体未生成新的候选脚本。')
                if len(candidate) > MAX_CANDIDATE_SCRIPT_CHARS:
                    raise ValueError('修复候选脚本超过允许长度，已拒绝保存。')
                if candidate_leaks_runtime_values(
                    candidate, runtime_variables, baseline_script=generation.script_draft,
                ):
                    raise ValueError('修复候选包含一次性运行变量，已拒绝保存。')
                quality = evaluate_workspace_draft(candidate, target_url=generation.target_url, snapshot=getattr(result, 'snapshot', None) or attempt_snapshot)
                apply_assertion_regression_blocker(
                    quality, candidate, generation.script_draft,
                )
                diff = bounded_candidate_diff(''.join(unified_diff(generation.script_draft.splitlines(keepends=True), candidate.splitlines(keepends=True), fromfile='原草稿', tofile='修复候选', n=3)))
                attempts.append({'round': round_number, 'candidate_hash': script_hash(candidate), 'mcp_checked': use_mcp,
                                 'static_status': quality.get('status'), 'execution_id': None,
                                 'execution_status': 'not_run', 'summary': agent_error_message or '修复智能体异常，候选未执行。',
                                 'has_screenshot': False, 'runtime_assertion_count': 0,
                                 'blockers': deepcopy(quality.get('blockers') or [])})
                if not update_repair_state(
                    generation_id, locked_revision=locked_revision, locked_hash=locked_hash, attempts=attempts,
                    candidate_script=candidate, candidate_diff=diff, candidate_quality_report=quality,
                    candidate_error_code=agent_error_code, candidate_error_message=agent_error_message,
                    summary=agent_error_message or '修复智能体异常，已保留新候选供人工审核。',
                    message='修复已完成：智能体异常，但新候选已保留供人工审核。',
                    final_status='candidate_ready', task_id=self.request.id,
                ):
                    return build_error_result(self.request.id, '草稿已变化，候选结果未写回。')
                return {'success': False, 'status': 'candidate_ready', 'generation_id': str(generation.pk)}
            if not candidate.strip() or script_hash(candidate) == script_hash(attempt_script):
                raise ValueError('修复智能体未生成有变化的候选脚本。')
            if len(candidate) > MAX_CANDIDATE_SCRIPT_CHARS:
                raise ValueError('修复候选脚本超过允许长度，已拒绝保存。')
            if candidate_leaks_runtime_values(
                candidate, runtime_variables, baseline_script=generation.script_draft,
            ):
                raise ValueError('修复候选包含一次性运行变量，已拒绝保存。')
            candidate_snapshot = getattr(result, 'snapshot', None) or attempt_snapshot
            quality = evaluate_workspace_draft(candidate, target_url=generation.target_url, snapshot=candidate_snapshot)
            diff = bounded_candidate_diff(''.join(unified_diff(generation.script_draft.splitlines(keepends=True), candidate.splitlines(keepends=True), fromfile='原草稿', tofile='修复候选', n=3)))
            attempt = {'round': round_number, 'candidate_hash': script_hash(candidate), 'mcp_checked': use_mcp,
                       'static_status': quality.get('status'), 'execution_id': None, 'execution_status': 'not_run',
                       'summary': '', 'has_screenshot': False, 'runtime_assertion_count': 0}
            if apply_assertion_regression_blocker(
                quality, candidate, generation.script_draft,
            ):
                attempt['static_status'] = quality['status']
                attempt['summary'] = '候选减少了已确认断言或增加了待补充断言，未执行浏览器验证。'
            latest = (candidate, diff, quality)
            if quality.get('blockers') or quality.get('status') == 'needs_review':
                attempt['summary'] = '候选未通过静态检查，未执行浏览器验证。'
                attempt['blockers'] = deepcopy(quality.get('blockers') or [])
                attempts.append(attempt)
                attempt_script, attempt_snapshot = candidate, candidate_snapshot
                continue
            if not update_repair_state(
                generation_id, locked_revision=locked_revision, locked_hash=locked_hash,
                phase='validating', message=f'正在验证第 {round_number} 轮候选。',
                attempts=attempts, task_id=self.request.id,
            ):
                return build_error_result(self.request.id, '草稿已变化，过期修复任务未执行。')
            execution = active_execution = WebUITestExecution.objects.create(
                exec_type='case',
                name=f'AI 修复候选第 {round_number} 轮',
                description=generation.description_safe,
                executor=generation.user,
                project=generation.project,
                browser=WEBUI_BROWSER_ENGINE,
                status='running',
                trigger_type='llm',
                start_time=timezone.now(),
            )
            detail = active_detail = WebUITestCaseExecutionDetail.objects.create(
                execution=execution,
                test_case=None,
                status='running',
                start_time=execution.start_time,
            )
            screenshot_absolute, screenshot_relative = _failure_screenshot_paths(execution.id, f'repair_candidate_{round_number}.png')
            try:
                runner_result = _run_test_script(candidate, {}, failure_screenshot_path=screenshot_absolute,
                                                 environment_variables=effective_execution_variables)
            except Exception as runner_exc:
                ended = timezone.now()
                message = redact_runtime_values(f'候选执行器异常：{runner_exc}', runtime_variables)
                execution.status = 'error'
                execution.error_message = message
                execution.end_time = ended
                execution.duration = (ended - execution.start_time).total_seconds()
                detail.status = 'error'
                detail.error_message = message
                detail.end_time = ended
                detail.duration = execution.duration
                detail_update_fields = [
                    'status', 'error_message', 'end_time', 'duration',
                ]
                if os.path.exists(screenshot_absolute):
                    detail.screenshot_path = screenshot_relative
                    detail_update_fields.append('screenshot_path')
                execution.save(update_fields=[
                    'status', 'error_message', 'end_time', 'duration', 'updated_at',
                ])
                detail.save(update_fields=detail_update_fields)
                attempt.update({
                    'execution_id': execution.id,
                    'execution_status': 'error',
                    'summary': failure_evidence(
                        fallback=message, runtime_variables=runtime_variables,
                    )['summary'],
                    'has_screenshot': bool(detail.screenshot_path),
                    'runtime_assertion_count': 0,
                })
                attempts.append(attempt)
                active_execution = None
                active_detail = None
                evidence = failure_evidence(fallback=message, runtime_variables=runtime_variables)
                attempt_script, attempt_snapshot = candidate, candidate_snapshot
                if is_non_code_failure(evidence):
                    break
                continue
            result_data = runner_result.get('result') or {}
            safe_result_data = {
                **result_data,
                'stdout': redact_runtime_values(result_data.get('stdout', ''), runtime_variables),
                'stderr': redact_runtime_values(result_data.get('stderr', ''), runtime_variables),
            }
            operation_success = bool(runner_result.get('operation_success', runner_result.get('success')))
            execution_status, assertion_state, runtime_assertion_count = evaluation_status(candidate, operation_success=operation_success, runtime_assertion_count=runner_result.get('runtime_assertion_count'))
            error_message = '' if operation_success else friendly_failure_summary(safe_result_data.get('stdout', ''), safe_result_data.get('stderr', ''), redact_runtime_values(runner_result.get('error', ''), runtime_variables))
            if execution_status == 'incomplete':
                error_message = _incomplete_message(
                    assertion_state, runtime_assertion_count,
                )
            persisted = _normalize_persisted_screenshot_path(
                execution.id,
                safe_result_data.get('screenshot_path') or screenshot_relative,
            )
            if persisted and os.path.exists(
                os.path.join(str(settings.MEDIA_ROOT), persisted),
            ):
                detail.screenshot_path = persisted
            ended = timezone.now()
            execution.status = execution_status
            execution.error_message = error_message
            execution.end_time = ended
            execution.duration = (ended - execution.start_time).total_seconds()
            execution.log_path = ''
            execution.save()
            detail.status = execution_status
            detail.end_time = ended
            detail.duration = execution.duration
            detail.error_message = error_message or None
            detail.log = _raw_execution_log(safe_result_data)
            detail.save()
            safe_error_message = failure_evidence(fallback=error_message, runtime_variables=runtime_variables)['summary'] if error_message else ''
            attempt.update({'execution_id': execution.id, 'execution_status': execution_status, 'summary': safe_error_message or '候选验证通过。', 'has_screenshot': bool(detail.screenshot_path), 'runtime_assertion_count': runtime_assertion_count})
            attempts.append(attempt)
            active_execution = None
            active_detail = None
            if execution_status == 'passed':
                if not update_repair_state(generation_id, locked_revision=locked_revision, locked_hash=locked_hash, attempts=attempts, candidate_script=candidate, candidate_diff=diff, candidate_quality_report=quality, summary='候选已通过实际验证。', message='修复已完成：候选已通过实际验证。', final_status='candidate_passed', task_id=self.request.id):
                    return build_error_result(self.request.id, '草稿已变化，候选结果未写回。')
                return {'success': True, 'status': 'candidate_passed', 'generation_id': str(generation.pk)}
            evidence = failure_evidence(
                stdout=safe_result_data.get('stdout', ''),
                stderr=safe_result_data.get('stderr', ''),
                fallback=error_message,
                runtime_variables=runtime_variables,
            )
            attempt_script, attempt_snapshot = candidate, candidate_snapshot
            if is_non_code_failure(evidence):
                break
        if latest is None:
            raise ValueError('修复代理没有返回可审核候选脚本。')
        candidate, diff, quality = latest
        if not update_repair_state(generation_id, locked_revision=locked_revision, locked_hash=locked_hash, attempts=attempts, candidate_script=candidate, candidate_diff=diff, candidate_quality_report=quality, summary='候选未通过验证或验证被环境问题终止；原草稿未变。', message='修复已完成：候选未通过验证，保留供人工审核。', final_status='candidate_ready', task_id=self.request.id):
            return build_error_result(self.request.id, '草稿已变化，候选结果未写回。')
        return {'success': False, 'status': 'candidate_ready', 'generation_id': str(generation.pk)}
    except Exception as exc:
        model_error = classify_model_service_error(exc, stage='repairing')
        code = model_error[0] if model_error else 'REPAIR_CANDIDATE_UNAVAILABLE'
        message = (
            model_error[1]
            if model_error else (
                redact_runtime_values(str(exc), runtime_variables)
                or '修复服务未能生成可审核候选，请人工审核或重新调试。'
            )
        )
        logger.error('生成草稿修复失败: generation_id=%s\n%s', generation_id, redact_runtime_values(traceback.format_exc(), runtime_variables))
        active_execution_id = active_execution.pk if active_execution is not None else None
        active_has_screenshot = bool(active_detail is not None and active_detail.screenshot_path)
        if active_execution is not None:
            ended_at = timezone.now()
            try:
                duration = max(0.0, (ended_at - active_execution.start_time).total_seconds())
            except (TypeError, ValueError):
                duration = 0.0
            active_execution.status = 'error'
            active_execution.error_message = message
            active_execution.end_time = ended_at
            active_execution.duration = duration
            try:
                # QuerySet.update is intentional: a failed instance save must not
                # leave the previously-created execution running in the database.
                WebUITestExecution.objects.filter(pk=active_execution.pk).update(
                    status='error', error_message=message, end_time=ended_at,
                    duration=duration, updated_at=ended_at,
                )
            except Exception:
                logger.exception('修复候选执行异常后无法回填 execution: execution_id=%s', active_execution_id)
            if active_detail is not None:
                active_detail.status = 'error'
                active_detail.error_message = message
                active_detail.end_time = ended_at
                active_detail.duration = duration
                try:
                    detail_updates = {
                        'status': 'error',
                        'error_message': message,
                        'end_time': ended_at,
                        'duration': duration,
                    }
                    if active_detail.screenshot_path:
                        detail_updates['screenshot_path'] = active_detail.screenshot_path
                    WebUITestCaseExecutionDetail.objects.filter(pk=active_detail.pk).update(
                        **detail_updates,
                    )
                except Exception:
                    logger.exception('修复候选执行异常后无法回填 detail: execution_id=%s', active_execution_id)
        if latest is not None:
            candidate, diff, quality = latest
            if active_round and not any(item.get('round') == active_round for item in attempts):
                attempts.append({
                    'round': active_round, 'candidate_hash': script_hash(candidate),
                    'mcp_checked': active_mcp_checked, 'static_status': 'error',
                    'execution_id': active_execution_id,
                    'execution_status': (
                        'error' if active_execution_id is not None else 'not_run'
                    ),
                    'summary': message, 'has_screenshot': active_has_screenshot,
                    'runtime_assertion_count': 0,
                })
            if not update_repair_state(
                generation_id, locked_revision=locked_revision, locked_hash=locked_hash,
                attempts=attempts, candidate_script=candidate, candidate_diff=diff,
                candidate_quality_report=quality, candidate_error_code=code,
                candidate_error_message=message,
                summary=f'后续第 {active_round or 2} 轮修复未完成：{message}；已保留最近候选供人工审核。',
                message='修复已完成：后续修复异常，已保留已有候选供人工审核。',
                final_status='candidate_ready', task_id=self.request.id,
            ):
                logger.warning('过期修复任务未能保留已有候选: generation_id=%s', generation_id)
                return build_error_result(self.request.id, '草稿已变化，过期修复任务未写回。')
            return {
                'success': False, 'status': 'candidate_ready', 'generation_id': str(generation_id),
                'message': '后续修复异常，已保留已有候选供人工审核。',
            }
        if not finish_repair_failure(
            generation_id, locked_revision=locked_revision, locked_hash=locked_hash,
            message=message, task_id=self.request.id,
            blockers=[{'severity': 'blocker', 'code': code, 'message': message}],
        ):
            logger.warning('过期修复任务未能回填失败状态: generation_id=%s', generation_id)
            return build_error_result(self.request.id, '草稿已变化，过期修复任务未写回。')
        return build_error_result(self.request.id, message)


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
        return {'success': True, 'message': '任务已取消', 'task_id': task_id}
    except Exception as exc:
        logger.error('取消任务失败: %s', exc, exc_info=True)
        return {'success': False, 'error': f'取消任务失败: {exc}', 'task_id': task_id}


def _finalize_scheduled_execution(
    scheduled_log_id: int | None,
    *,
    execution_id: int,
    total_cases: int,
    passed_cases: int,
    failed_cases: int,
    incomplete_cases: int,
    skipped_cases: int,
    log: str,
) -> None:
    """Refresh a scheduled report and let its serial controller advance once."""
    if not scheduled_log_id:
        return
    try:
        from scheduled_tasks.scheduling import finish_scheduled_suite
        finish_scheduled_suite(scheduled_log_id, execution_id, append_log=log)
    except Exception:
        logger.error('回填定时任务日志或推进串行套件失败', exc_info=True)


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
    """Run persisted suite snapshots sequentially and retain partial progress."""
    execution = None
    suite_detail = None
    active_case = None
    log_sections: list[str] = []
    options = dict(options or {})
    scheduled_log_id = options.pop('scheduled_log_id', None)

    def persist_progress(*, log: str):
        rows = suite_detail.case_executions.values_list('status', flat=True)
        statuses = list(rows)
        counts = {
            'total_cases': len(statuses),
            'passed_cases': statuses.count('passed'),
            'failed_cases': sum(status in {'failed', 'error'} for status in statuses),
            'incomplete_cases': statuses.count('incomplete'),
            'skipped_cases': statuses.count('skipped'),
            'unexecuted_cases': sum(status in {'pending', 'running'} for status in statuses),
        }
        suite_detail.total_cases = counts['total_cases']
        suite_detail.passed_cases = counts['passed_cases']
        suite_detail.failed_cases = counts['failed_cases']
        suite_detail.incomplete_cases = counts['incomplete_cases']
        suite_detail.skipped_cases = counts['skipped_cases']
        suite_detail.log = log
        suite_detail.save(update_fields=[
            'total_cases', 'passed_cases', 'failed_cases', 'incomplete_cases',
            'skipped_cases', 'log',
        ])
        return counts

    try:
        query = WebUITestExecution.objects.select_related('suite_execution_detail').filter(
            id=execution_id, exec_type='suite',
        )
        if user_id is not None:
            query = query.filter(executor_id=user_id)
        execution = query.get()
        suite_detail = execution.suite_execution_detail
        case_executions = list(suite_detail.case_executions.select_related('test_case').order_by('execution_order', 'id'))
        if not case_executions:
            raise ValueError('执行快照中没有测试用例，任务未执行')

        options = normalize_webui_execution_options(options)
        started_at = timezone.now()
        execution.task_id = task_instance.request.id
        execution.status = 'running'
        execution.error_message = ''
        execution.browser = WEBUI_BROWSER_ENGINE
        execution.start_time = started_at
        execution.save(update_fields=['task_id', 'status', 'error_message', 'browser', 'start_time', 'updated_at'])
        suite_detail.start_time = started_at
        suite_detail.save(update_fields=['start_time'])

        runtime_variables = pop_runtime_variables(execution.id)
        log_sections = [f'=== 测试套件：{execution.name} ===']
        execution_results = []
        total_cases = len(case_executions)

        for index, case_execution in enumerate(case_executions, start=1):
            if case_execution.status not in {'pending'}:
                continue
            active_case = case_execution
            update_task_progress(
                task_instance,
                15 + int((index - 1) / total_cases * 75),
                f'正在执行第 {index}/{total_cases} 个用例：{case_execution.name}',
            )
            case_started_at = timezone.now()
            case_execution.status = 'running'
            case_execution.save(update_fields=['status'])
            script_content = case_execution.script_content or ''
            if not script_content.strip():
                case_execution.status = 'skipped'
                case_execution.error_message = '测试用例没有可执行脚本'
                case_execution.duration = 0
                case_execution.save(update_fields=['status', 'error_message', 'duration'])
                log_sections.append(f'\n--- {index}. {case_execution.name} [SKIPPED] ---\n{case_execution.error_message}')
                persist_progress(log='\n'.join(log_sections))
                execution_results.append({
                    'test_case_id': case_execution.test_case_id,
                    'test_case_title': case_execution.name,
                    'status': case_execution.status,
                    'error_message': case_execution.error_message,
                })
                active_case = None
                continue

            try:
                screenshot_absolute, screenshot_relative = _failure_screenshot_paths(
                    execution.id, f'suite_case_{index}_{case_execution.id}.png',
                )
                result = _run_test_script(
                    script_content,
                    suite_detail.execution_options or options,
                    failure_screenshot_path=screenshot_absolute,
                    environment_variables=merge_execution_variables(
                        case_execution.variables, suite_detail.suite_variables, runtime_variables,
                    ),
                )
                result_data = result.get('result') or {}
                operation_success = bool(result.get('operation_success', result.get('success')))
                case_status, assertion_state, runtime_assertion_count = evaluation_status(
                    script_content,
                    operation_success=operation_success,
                    runtime_assertion_count=result.get('runtime_assertion_count'),
                )
                error_message = '' if operation_success else friendly_failure_summary(
                    result_data.get('stdout', ''), result_data.get('stderr', ''), result.get('error', ''),
                )
                if case_status == 'incomplete':
                    error_message = _incomplete_message(assertion_state, runtime_assertion_count)
            except Exception as exc:
                logger.error('套件用例执行异常: case_execution_id=%s', case_execution.id, exc_info=True)
                result_data = {}
                operation_success = False
                case_status = 'error'
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
                execution.id, result_data.get('screenshot_path') or screenshot_relative,
            )
            if persisted and os.path.exists(os.path.join(str(settings.MEDIA_ROOT), persisted)):
                case_execution.screenshot_path = persisted
            case_execution.save()
            if case_execution.test_case_id:
                case_execution.test_case.last_execute_status = case_execution.status
                case_execution.test_case.last_execute_time = case_ended_at
                case_execution.test_case.last_error_message = error_message[:500]
                case_execution.test_case.save(update_fields=[
                    'last_execute_status', 'last_execute_time', 'last_error_message',
                ])
            log_sections.append(
                f'\n--- {index}. {case_execution.name} [{case_execution.status.upper()}] ---\n'
                f'{case_execution.log or error_message or "执行完成"}'
            )
            persist_progress(log='\n'.join(log_sections))
            execution_results.append({
                'test_case_id': case_execution.test_case_id,
                'test_case_title': case_execution.name,
                'status': case_execution.status,
                'operation_success': operation_success,
                'assertion_state': assertion_state,
                'runtime_assertion_count': runtime_assertion_count,
                'error_message': error_message,
                'result': result_data,
            })
            active_case = None

        counts = persist_progress(log='\n'.join(log_sections))
        ended_at = timezone.now()
        all_skipped = counts['passed_cases'] == counts['failed_cases'] == counts['incomplete_cases'] == 0
        if counts['failed_cases']:
            execution_status = 'failed'
            error_message = f"测试套件中有 {counts['failed_cases']} 个用例失败"
        elif counts['incomplete_cases']:
            execution_status = 'incomplete'
            error_message = f"测试套件中有 {counts['incomplete_cases']} 个用例尚未完成验证"
        elif counts['unexecuted_cases']:
            execution_status = 'failed'
            error_message = f"测试套件有 {counts['unexecuted_cases']} 个用例未执行"
        elif all_skipped:
            execution_status = 'failed'
            error_message = '测试套件没有可执行脚本'
        else:
            execution_status = 'passed'
            error_message = ''
        operation_success = execution_status in {'passed', 'incomplete'}
        summary = (
            f"测试套件执行完成：通过 {counts['passed_cases']}，验证未完成 {counts['incomplete_cases']}，"
            f"失败 {counts['failed_cases']}，跳过 {counts['skipped_cases']}"
        )
        suite_detail.end_time = ended_at
        suite_detail.duration = (ended_at - started_at).total_seconds()
        suite_detail.save(update_fields=['end_time', 'duration'])
        execution.status = execution_status
        execution.error_message = error_message
        execution.end_time = ended_at
        execution.duration = suite_detail.duration
        execution.save(update_fields=['status', 'error_message', 'end_time', 'duration', 'updated_at'])

        _finalize_scheduled_execution(
            scheduled_log_id,
            execution_id=execution.id,
            total_cases=counts['total_cases'], passed_cases=counts['passed_cases'],
            failed_cases=counts['failed_cases'], incomplete_cases=counts['incomplete_cases'],
            skipped_cases=counts['skipped_cases'], log=suite_detail.log or '',
        )
        update_task_progress(task_instance, 100, summary)
        return {
            'success': operation_success, 'operation_success': operation_success,
            'evaluation_status': execution_status, 'status': 'completed', 'message': summary,
            'execution_id': execution.id, **{key: value for key, value in counts.items() if key != 'unexecuted_cases'},
            'pass_rate': execution.pass_rate, 'execution_results': execution_results, 'error': error_message,
        }
    except WebUITestExecution.DoesNotExist:
        error_message = f'测试套件执行记录不存在: {execution_id}'
        try:
            from scheduled_tasks.scheduling import finish_scheduled_suite
            # A deleted prepared execution is an aggregate execution error,
            # not a reason to leave the parent serial plan running forever.
            finish_scheduled_suite(scheduled_log_id, execution_id, append_log=error_message)
        except Exception:
            logger.warning('缺失 WebUI 执行记录后推进串行定时任务失败', exc_info=True)
        return build_error_result(None, error_message)
    except Exception as exc:
        error_message = f'测试套件执行任务异常: {exc}'
        logger.error(error_message, exc_info=True)
        ended_at = timezone.now()
        if active_case is not None and active_case.status == 'running':
            active_case.status = 'error'
            active_case.error_message = error_message
            active_case.duration = max(0.0, (ended_at - (execution.start_time or ended_at)).total_seconds())
            active_case.log = error_message
            active_case.save(update_fields=['status', 'error_message', 'duration', 'log'])
        counts = None
        if suite_detail is not None:
            log_sections.append(error_message)
            counts = persist_progress(log='\n'.join(log_sections))
            suite_detail.end_time = ended_at
            suite_detail.duration = max(0.0, (ended_at - (execution.start_time or ended_at)).total_seconds()) if execution else 0.0
            suite_detail.save(update_fields=['end_time', 'duration'])
        if execution is not None:
            execution.status = 'failed'
            execution.error_message = error_message
            execution.end_time = ended_at
            execution.duration = suite_detail.duration if suite_detail is not None else 0.0
            execution.save(update_fields=['status', 'error_message', 'end_time', 'duration', 'updated_at'])
        _finalize_scheduled_execution(
            scheduled_log_id,
            execution_id=execution_id,
            total_cases=(counts or {}).get('total_cases', 0),
            passed_cases=(counts or {}).get('passed_cases', 0),
            failed_cases=(counts or {}).get('failed_cases', 0),
            incomplete_cases=(counts or {}).get('incomplete_cases', 0),
            skipped_cases=(counts or {}).get('skipped_cases', 0),
            log='\n'.join(log_sections) or error_message,
        )
        return build_error_result(task_instance.request.id, error_message)
