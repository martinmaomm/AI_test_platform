"""HTTP contract for the saved-script assistant (no frontend-specific state)."""

from __future__ import annotations

import copy
import uuid
from datetime import timedelta

from celery.result import AsyncResult
from django.db import transaction
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView

from ai_core.models import LLMConfiguration, ModelType
from common.api import response
from .constants import normalize_webui_execution_options
from .execution_variables import (
    ExecutionVariableError,
    merge_variable_definitions,
    normalize_variable_definitions,
    require_runtime_variables,
    runtime_variable_names,
    store_repair_runtime_variables,
)
from .models import (
    WebUIScriptAssistant,
    WebUITestCase,
    WebUITestExecution,
    WebUITestSuiteCaseExecution,
)
from .project_access import (
    EDIT,
    EXECUTE,
    READ,
    get_project_for_user,
    project_access_required,
)
from .script_assistant import (
    SCRIPT_ASSISTANT_TOTAL_TIMEOUT_SECONDS,
    ScriptAssistantConflict,
    candidate_hash,
    case_edit_version,
    expire_if_needed,
    model_info,
    repair_adoption_state,
    verify_action_state,
)
from .tasks import run_script_assistant_operation_task


def assistant_payload(item: WebUIScriptAssistant) -> dict:
    return {
        "id": str(item.id),
        "mode": item.mode,
        "test_case_id": item.test_case_id,
        "execution_id": item.execution_id,
        "suite_case_id": item.suite_case_id,
        "status": item.status,
        "operation": item.operation,
        "revision": item.revision,
        "phase": item.phase,
        "message": item.message,
        "model_info": item.model_info,
        "source_script": item.source_script,
        "source_edit_version": item.source_edit_version,
        "candidate_script": item.candidate_script,
        "candidate_hash": item.candidate_hash,
        "candidate_diff": item.candidate_diff,
        "summary": item.summary,
        "blockers": item.blockers,
        "quality_report": item.quality_report,
        "messages": item.messages,
        "attempts": item.attempts,
        "verification": item.verification,
        "adoption": repair_adoption_state(item),
        "verify_action": verify_action_state(item),
        "created_at": item.created_at,
        "updated_at": item.updated_at,
    }


def _model(request, config_id: object) -> LLMConfiguration:
    try:
        return LLMConfiguration.objects.get(
            id=int(config_id),
            is_active=True,
            model_type=ModelType.LLM,
            created_by=request.user,
        )
    except (LLMConfiguration.DoesNotExist, TypeError, ValueError):
        raise ValueError("所选模型不存在、未启用或不属于当前用户。")


def _dispatch(item: WebUIScriptAssistant) -> None:
    """Persist UUID first, then submit. A fast worker can always claim it."""
    run_script_assistant_operation_task.apply_async(
        args=[str(item.id), item.revision, item.task_id],
        task_id=item.task_id,
    )


def _merged_execution_options(source_options, overrides) -> dict:
    """Apply partial UI overrides to the exact options frozen by execution."""
    base = normalize_webui_execution_options(source_options)
    if overrides is None:
        return base
    if not isinstance(overrides, dict):
        raise ValueError("WebUI执行参数必须是对象")
    return normalize_webui_execution_options({**base, **overrides})


def _positive_query_id(request, key: str) -> int | None:
    raw = request.query_params.get(key)
    if raw is None:
        return None
    try:
        value = int(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{key} 必须是正整数。") from exc
    if value <= 0:
        raise ValueError(f"{key} 必须是正整数。")
    return value


def _queue(
    item: WebUIScriptAssistant,
    *,
    operation: str,
    runtime_variables: list[dict] | None = None,
    expected_revision: int | None = None,
    updates: dict | None = None,
    verify_candidate_hash: str | None = None,
    verify_acknowledge_review: bool = False,
) -> WebUIScriptAssistant:
    task_id = str(uuid.uuid4())
    with transaction.atomic():
        locked = WebUIScriptAssistant.objects.select_for_update().get(pk=item.pk)
        if expected_revision is not None and locked.revision != expected_revision:
            raise ScriptAssistantConflict("会话已变化，请刷新后重试。")
        if locked.status in {"queued", "running"}:
            raise ScriptAssistantConflict("当前会话已有操作在运行，请等待完成或取消。")
        if operation == "verify":
            action = verify_action_state(locked)
            if not action["can_verify"]:
                raise ScriptAssistantConflict("当前没有可验证的候选脚本。")
            if (
                not verify_candidate_hash
                or verify_candidate_hash != locked.candidate_hash
            ):
                raise ScriptAssistantConflict("候选已变化，请刷新后验证。")
            if (
                action["requires_acknowledge_review"]
                and verify_acknowledge_review is not True
            ):
                raise ScriptAssistantConflict(
                    "该候选未通过自动修复范围检查，必须确认风险后才能运行验证。"
                )
        if (
            operation == "edit"
            and (updates or {}).get("pending_use_candidate") is True
            and not locked.candidate_script
        ):
            raise ScriptAssistantConflict("当前没有可采用的候选脚本。")
        for field, value in (updates or {}).items():
            setattr(locked, field, value)
        locked.revision += 1
        locked.task_id = task_id
        locked.operation = operation
        locked.status = WebUIScriptAssistant.Status.QUEUED
        locked.phase = "queued"
        locked.message = "任务已排队"
        locked.cancel_requested_at = None
        locked.deadline_at = timezone.now() + timedelta(
            seconds=SCRIPT_ASSISTANT_TOTAL_TIMEOUT_SECONDS
        )
        names = runtime_variable_names(runtime_variables or [])
        if operation == "verify":
            locked.summary = "已发起本次候选验证，请以本次运行结果为准。"
            locked.verification = {
                "status": "queued",
                "runtime_variables_present": bool(runtime_variables),
                "runtime_variable_names": names,
                "authorization": {
                    "candidate_hash": locked.candidate_hash,
                    "revision": locked.revision,
                    "task_id": task_id,
                    "acknowledge_review": verify_acknowledge_review,
                    "acknowledged_by": locked.user_id,
                    "acknowledged_at": timezone.now().isoformat(),
                },
            }
        elif operation == "edit":
            locked.verification = {
                "status": "unverified",
                "runtime_variable_names": names,
            }
        quality_report = dict(locked.quality_report or {})
        quality_report["runtime_variable_names"] = names
        quality_report["runtime_variables_revision"] = locked.revision
        locked.quality_report = quality_report
        locked.save(
            update_fields=sorted(
                set((updates or {}).keys())
                | {
                    "revision",
                    "task_id",
                    "operation",
                    "status",
                    "phase",
                    "message",
                    "cancel_requested_at",
                    "deadline_at",
                    "verification",
                    "summary",
                    "quality_report",
                    "updated_at",
                }
            )
        )
        if runtime_variables:
            store_repair_runtime_variables(
                locked.id,
                locked.revision,
                candidate_hash(locked.source_script),
                runtime_variables,
            )
    # Runs after commit by construction, avoiding the task_id visibility race.
    try:
        _dispatch(locked)
    except Exception as exc:
        WebUIScriptAssistant.objects.filter(
            pk=locked.pk,
            revision=locked.revision,
            task_id=task_id,
            status="queued",
        ).update(status="failed", phase="queue", message="任务队列不可用，请稍后重试。")
        raise RuntimeError("任务队列不可用，请稍后重试。") from exc
    return WebUIScriptAssistant.objects.get(pk=locked.pk)


def _repair_source(project_id: int, execution_id: object, suite_case_id: object | None):
    execution = get_object_or_404(
        WebUITestExecution, pk=execution_id, project_id=project_id
    )
    if execution.status not in {"failed", "error"}:
        raise ValueError("仅失败或错误的执行记录可以发起 AI 修复。")
    if suite_case_id is not None:
        suite_case = get_object_or_404(
            WebUITestSuiteCaseExecution,
            pk=suite_case_id,
            suite_execution__execution=execution,
        )
        if (
            suite_case.status not in {"failed", "error"}
            or not suite_case.test_case_id
            or suite_case.source_script_version is None
            or not suite_case.source_edit_version
            or not suite_case.script_content
            or not suite_case.suite_execution.execution_options
        ):
            raise ScriptAssistantConflict(
                "该套件用例缺少可修复的冻结快照，请重新执行。"
            )
        case = suite_case.test_case
        script, version = suite_case.script_content, suite_case.source_script_version
        variables = merge_variable_definitions(
            suite_case.variables or [],
            suite_case.suite_execution.suite_variables or [],
        )
        options = suite_case.suite_execution.execution_options
        source_edit_version = suite_case.source_edit_version
        saved_runtime_names = suite_case.suite_execution.runtime_variable_names
    else:
        if execution.exec_type != "case":
            raise ValueError("套件执行必须提交 suite_case_id。")
        try:
            detail = execution.case_execution_detail
        except Exception as exc:
            raise ScriptAssistantConflict("执行详情缺失，无法读取冻结快照。") from exc
        if (
            not detail.test_case_id
            or not detail.source_script
            or detail.source_script_version is None
            or not detail.source_edit_version
            or not detail.execution_options
        ):
            raise ScriptAssistantConflict("历史执行缺少冻结快照，请重新执行后再修复。")
        case = detail.test_case
        suite_case = None
        script, version, variables, options = (
            detail.source_script,
            detail.source_script_version,
            detail.source_variables,
            detail.execution_options,
        )
        source_edit_version = detail.source_edit_version
        saved_runtime_names = detail.runtime_variable_names
    if (
        case.script_version != version
        or case.test_script_content != script
        or case_edit_version(case) != source_edit_version
    ):
        raise ScriptAssistantConflict(
            "来源用例已变化，不能以旧快照覆盖新版本；请重新执行。"
        )
    return (
        execution,
        suite_case,
        case,
        script,
        version,
        source_edit_version,
        copy.deepcopy(variables or []),
        copy.deepcopy(options or []),
        runtime_variable_names(saved_runtime_names or []),
    )


def _create_edit_session(*, test_case, expected_edit_version, **fields):
    # Serialize first creation for this case. New conversations subsequently
    # reset this same record instead of creating selectable history records.
    with transaction.atomic():
        case = WebUITestCase.objects.select_for_update().get(pk=test_case.pk)
        if expected_edit_version != case_edit_version(case):
            raise ScriptAssistantConflict("用例已变化，请刷新后再开始对话。")
        if WebUIScriptAssistant.objects.filter(
            test_case=case, user=fields["user"], mode="edit"
        ).exists():
            raise ScriptAssistantConflict(
                "当前用例已有对话，请刷新后继续或点击新建会话。"
            )
        return WebUIScriptAssistant.objects.create(test_case=case, **fields)


class ScriptAssistantListCreateView(APIView):
    permission_classes = [IsAuthenticated]

    @project_access_required(READ)
    def get(self, request, project_id):
        try:
            mode = request.query_params.get("mode")
            if mode is not None and mode not in {
                WebUIScriptAssistant.Mode.EDIT,
                WebUIScriptAssistant.Mode.REPAIR,
            }:
                raise ValueError("mode 只能是 edit 或 repair。")
            query = WebUIScriptAssistant.objects.filter(
                project_id=project_id, user=request.user
            )
            if mode is not None:
                query = query.filter(mode=mode)
            for key in ("test_case_id", "execution_id", "suite_case_id"):
                value = _positive_query_id(request, key)
                if value is not None:
                    query = query.filter(**{key: value})
        except ValueError as exc:
            return response(kind="error", message=str(exc), status_code=400)
        limit = 1 if mode == "edit" and request.query_params.get("test_case_id") else 20
        return response(
            kind="success",
            data=[
                assistant_payload(expire_if_needed(item))
                for item in query[:limit]
            ],
            message="获取近期助手会话成功",
        )

    @project_access_required(EDIT)
    def post(self, request, project_id):
        mode = request.data.get("mode")
        try:
            config = _model(request, request.data.get("model_config_id"))
            if mode == "edit":
                case = get_object_or_404(
                    WebUITestCase,
                    pk=request.data.get("test_case_id"),
                    project_id=project_id,
                )
                if request.data.get("expected_edit_version") != case_edit_version(case):
                    raise ScriptAssistantConflict("用例已变化，请刷新后再开始对话。")
                source = str(request.data.get("script_content") or "")
                if not source.strip():
                    raise ValueError("script_content 不能为空。")
                if len(source) > 200000:
                    raise ValueError("script_content 不能超过 200000 个字符。")
                description = str(request.data.get("description") or case.description)
                if len(description) > 2000:
                    raise ValueError("description 不能超过 2000 个字符。")
                initial_message = str(request.data.get("message") or "")
                if not initial_message.strip():
                    raise ValueError("message 不能为空。")
                if len(initial_message) > 2000:
                    raise ValueError("message 不能超过 2000 个字符。")
                variables = normalize_variable_definitions(
                    request.data.get("variables")
                    if request.data.get("variables") is not None
                    else case.variables
                )
                item = _create_edit_session(
                    expected_edit_version=request.data.get("expected_edit_version"),
                    project_id=project_id,
                    user=request.user,
                    test_case=case,
                    mode="edit",
                    model_config_id=config.id,
                    model_info=model_info(config),
                    source_script=source,
                    source_script_version=case.script_version,
                    source_edit_version=case_edit_version(case),
                    source_variables=copy.deepcopy(variables),
                    pending_script=source,
                    pending_description=description,
                    pending_variables=copy.deepcopy(variables),
                    pending_message=initial_message,
                    messages=[
                        {
                            "role": "user",
                            "content": initial_message,
                            "created_at": timezone.now().isoformat(),
                        }
                    ],
                )
            elif mode == "repair":
                if request.data.get("confirm_execution") is not True:
                    raise ValueError("修复前必须确认会实际操作测试网站。")
                get_project_for_user(project_id, request.user, EXECUTE)
                (
                    execution,
                    suite_case,
                    case,
                    source,
                    version,
                    source_edit_version,
                    variables,
                    options,
                    saved_runtime_names,
                ) = _repair_source(
                    project_id,
                    request.data.get("execution_id"),
                    request.data.get("suite_case_id"),
                )
                runtime = require_runtime_variables(
                    request.data.get("runtime_variables") or [], saved_runtime_names
                )
                item = WebUIScriptAssistant.objects.create(
                    project_id=project_id,
                    user=request.user,
                    test_case=case,
                    execution=execution,
                    suite_case=suite_case,
                    mode="repair",
                    model_config_id=config.id,
                    model_info=model_info(config),
                    source_script=source,
                    source_script_version=version,
                    source_edit_version=source_edit_version,
                    source_variables=variables,
                    source_options=_merged_execution_options(
                        options, request.data.get("options")
                    ),
                    pending_script=source,
                    pending_description=case.description,
                    pending_message="根据失败证据修复脚本。",
                    messages=[
                        {
                            "role": "user",
                            "content": "根据失败证据修复脚本。",
                            "created_at": timezone.now().isoformat(),
                        }
                    ],
                )
            else:
                raise ValueError("mode 只能是 edit 或 repair。")
            item = _queue(
                item,
                operation=mode,
                expected_revision=item.revision,
                runtime_variables=runtime if mode == "repair" else None,
            )
        except (ValueError, ExecutionVariableError, ScriptAssistantConflict) as exc:
            return response(
                kind="error",
                message=str(exc),
                status_code=409 if isinstance(exc, ScriptAssistantConflict) else 400,
            )
        except RuntimeError as exc:
            return response(kind="error", message=str(exc), status_code=503)
        return response(
            kind="success",
            data=assistant_payload(item),
            message="助手会话已创建",
            status_code=202,
        )


class ScriptAssistantDetailView(APIView):
    permission_classes = [IsAuthenticated]

    @project_access_required(READ)
    def get(self, request, project_id, session_id):
        item = get_object_or_404(
            WebUIScriptAssistant,
            pk=session_id,
            project_id=project_id,
            user=request.user,
        )
        return response(
            kind="success",
            data=assistant_payload(expire_if_needed(item)),
            message="获取助手会话成功",
        )


class ScriptAssistantMessageView(APIView):
    permission_classes = [IsAuthenticated]

    @project_access_required(EDIT)
    def post(self, request, project_id, session_id):
        item = get_object_or_404(
            WebUIScriptAssistant,
            pk=session_id,
            project_id=project_id,
            user=request.user,
        )
        try:
            expected = int(request.data.get("expected_revision"))
            if item.mode != WebUIScriptAssistant.Mode.EDIT:
                raise ScriptAssistantConflict(
                    "修复会话不接受消息；请验证候选或重新发起修复。"
                )
            # The model is frozen when this round is queued. Merely selecting
            # another model in the UI must not change an in-flight operation.
            config = _model(
                request, request.data.get("model_config_id", item.model_config_id)
            )
            case = get_object_or_404(
                WebUITestCase, pk=item.test_case_id, project_id=project_id
            )
            if "expected_edit_version" in request.data and request.data[
                "expected_edit_version"
            ] != case_edit_version(case):
                raise ScriptAssistantConflict("用例已变化，请刷新后再发送。")
            script = str(request.data.get("script_content") or "")
            if not script.strip():
                raise ValueError("script_content 不能为空。")
            if len(script) > 200000:
                raise ValueError("script_content 不能超过 200000 个字符。")
            messages = list(item.messages or [])
            message = str(request.data.get("message") or "")
            if not message.strip():
                raise ValueError("message 不能为空。")
            if len(message) > 2000:
                raise ValueError("message 不能超过 2000 个字符。")
            description = str(request.data.get("description") or "")
            if len(description) > 2000:
                raise ValueError("description 不能超过 2000 个字符。")
            use_candidate = request.data.get("use_candidate")
            if not isinstance(use_candidate, bool):
                raise ValueError("use_candidate 必须是布尔值。")
            messages.append(
                {
                    "role": "user",
                    "content": message,
                    "created_at": timezone.now().isoformat(),
                }
            )
            updates = {
                "model_config_id": config.id,
                "model_info": model_info(config),
                "pending_script": script,
                "pending_description": description,
                "pending_variables": normalize_variable_definitions(
                    request.data.get("variables") or []
                ),
                "pending_message": message,
                "pending_use_candidate": use_candidate,
                "messages": messages[-20:],
            }
            if not use_candidate:
                # The raw editor buffer becomes the next candidate baseline.
                # Do not strip it: trailing whitespace is meaningful to the UI
                # optimistic comparison and the source snapshot contract.
                updates["source_script"] = script
                updates["source_script_version"] = case.script_version
                updates["source_edit_version"] = case_edit_version(case)
                updates["source_variables"] = copy.deepcopy(
                    updates["pending_variables"]
                )
            item = _queue(
                item, operation=item.mode, expected_revision=expected, updates=updates
            )
        except (
            TypeError,
            ValueError,
            ExecutionVariableError,
            ScriptAssistantConflict,
        ) as exc:
            return response(
                kind="error",
                message=str(exc),
                status_code=409 if isinstance(exc, ScriptAssistantConflict) else 400,
            )
        except RuntimeError as exc:
            return response(kind="error", message=str(exc), status_code=503)
        return response(
            kind="success",
            data=assistant_payload(item),
            message="助手消息已排队",
            status_code=202,
        )


class ScriptAssistantResetView(APIView):
    """Start a blank conversation without touching the case or its executions."""

    permission_classes = [IsAuthenticated]

    @project_access_required(EDIT)
    def post(self, request, project_id, session_id):
        try:
            expected = int(request.data.get("expected_revision"))
        except (TypeError, ValueError):
            return response(
                kind="error", message="expected_revision 必填。", status_code=400
            )
        try:
            with transaction.atomic():
                item = get_object_or_404(
                    WebUIScriptAssistant.objects.select_for_update(),
                    pk=session_id,
                    project_id=project_id,
                    user=request.user,
                )
                if item.mode != WebUIScriptAssistant.Mode.EDIT:
                    raise ScriptAssistantConflict("只有用例编辑对话可以清空重建。")
                if item.revision != expected:
                    raise ScriptAssistantConflict("会话已变化，请刷新后重试。")
                if item.status in {"queued", "running"}:
                    raise ScriptAssistantConflict(
                        "任务正在运行，请等待完成或取消后再新建会话。"
                    )
                # Reset all transient content from model defaults, retaining
                # only identity/scope, timestamps and the last-used model.
                # Revision invalidation fences out delayed worker writes.
                retained = {
                    "id",
                    "project",
                    "user",
                    "test_case",
                    "mode",
                    "model_config_id",
                    "model_info",
                    "revision",
                    "created_at",
                    "updated_at",
                }
                fields = []
                for field in item._meta.concrete_fields:
                    if field.name not in retained:
                        setattr(item, field.attname, field.get_default())
                        fields.append(field.name)
                item.revision += 1
                item.message = "已新建会话，请输入对当前脚本的修改要求。"
                item.save(update_fields=[*fields, "revision", "updated_at"])
        except ScriptAssistantConflict as exc:
            return response(kind="error", message=str(exc), status_code=409)
        return response(
            kind="success", data=assistant_payload(item), message="已清空并新建会话"
        )


class ScriptAssistantVerifyView(APIView):
    permission_classes = [IsAuthenticated]

    @project_access_required(EXECUTE)
    def post(self, request, project_id, session_id):
        item = get_object_or_404(
            WebUIScriptAssistant,
            pk=session_id,
            project_id=project_id,
            user=request.user,
        )
        try:
            expected = int(request.data.get("expected_revision"))
            if request.data.get("confirm_execution") is not True:
                raise ValueError("验证前必须确认会实际操作测试网站。")
            acknowledge_review = request.data.get("acknowledge_review", False)
            if not isinstance(acknowledge_review, bool):
                raise ValueError("acknowledge_review 必须是布尔值。")
            runtime = normalize_variable_definitions(
                request.data.get("runtime_variables") or []
            )
            if item.mode == WebUIScriptAssistant.Mode.REPAIR:
                # Reopening a repair must not silently replace the failed run's
                # one-time inputs with case defaults when verifying again.
                runtime = require_runtime_variables(
                    runtime,
                    (item.quality_report or {}).get("runtime_variable_names", []),
                )
            item = _queue(
                item,
                operation="verify",
                runtime_variables=runtime,
                expected_revision=expected,
                updates={
                    "source_options": _merged_execution_options(
                        item.source_options, request.data.get("options")
                    ),
                },
                verify_candidate_hash=str(request.data.get("candidate_hash") or ""),
                verify_acknowledge_review=acknowledge_review,
            )
        except (
            TypeError,
            ValueError,
            ExecutionVariableError,
            ScriptAssistantConflict,
        ) as exc:
            return response(
                kind="error",
                message=str(exc),
                status_code=409 if isinstance(exc, ScriptAssistantConflict) else 400,
            )
        except RuntimeError as exc:
            return response(kind="error", message=str(exc), status_code=503)
        return response(
            kind="success",
            data=assistant_payload(item),
            message="候选验证已排队",
            status_code=202,
        )


class ScriptAssistantCancelView(APIView):
    permission_classes = [IsAuthenticated]

    @project_access_required(EDIT)
    def post(self, request, project_id, session_id):
        item = get_object_or_404(
            WebUIScriptAssistant,
            pk=session_id,
            project_id=project_id,
            user=request.user,
        )
        try:
            expected = int(request.data.get("expected_revision"))
        except (TypeError, ValueError):
            return response(
                kind="error", message="expected_revision 必填。", status_code=400
            )
        task_id = item.task_id
        updated = WebUIScriptAssistant.objects.filter(
            pk=item.pk,
            revision=expected,
            task_id=task_id,
            status__in=["queued", "running"],
        ).update(
            status="cancelled",
            revision=expected + 1,
            phase="cancelled",
            message="已请求取消；已发生的网站操作不会回滚。",
            cancel_requested_at=timezone.now(),
        )
        if not updated:
            return response(
                kind="error", message="会话已变化，请刷新后重试。", status_code=409
            )
        if task_id:
            from django.core.cache import cache

            cache.set(f"celery:cancel:{task_id}", True, timeout=60 * 60)
            try:
                AsyncResult(task_id).revoke(terminate=False)
            except Exception:
                # Durable cancellation already won the CAS; transport failure
                # must not turn a cancellation response into HTTP 500.
                pass
        item.refresh_from_db()
        return response(
            kind="success", data=assistant_payload(item), message=item.message
        )


class ScriptAssistantApplyView(APIView):
    permission_classes = [IsAuthenticated]

    @project_access_required(EDIT)
    def post(self, request, project_id, session_id):
        item = get_object_or_404(
            WebUIScriptAssistant,
            pk=session_id,
            project_id=project_id,
            user=request.user,
        )
        try:
            expected_revision = int(request.data.get("expected_revision"))
        except (TypeError, ValueError):
            return response(
                kind="error", message="expected_revision 必填。", status_code=400
            )
        acknowledge_review = request.data.get("acknowledge_review", False)
        if not isinstance(acknowledge_review, bool):
            return response(
                kind="error",
                message="acknowledge_review 必须是布尔值。",
                status_code=400,
            )
        try:
            case = __import__(
                "web_testing.script_assistant", fromlist=["apply_repair"]
            ).apply_repair(
                item,
                expected_revision=expected_revision,
                expected_edit_version=str(
                    request.data.get("expected_edit_version") or ""
                ),
                expected_hash=str(request.data.get("candidate_hash") or ""),
                acknowledge_review=acknowledge_review,
            )
        except (TypeError, ValueError, ScriptAssistantConflict) as exc:
            return response(kind="error", message=str(exc), status_code=409)
        item.refresh_from_db()
        payload = assistant_payload(item)
        payload["case_edit_version"] = case_edit_version(case)
        return response(kind="success", data=payload, message="修复候选已采用")
