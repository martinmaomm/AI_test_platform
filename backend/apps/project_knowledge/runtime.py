"""Durable, sequential task lifecycle. Never uses the WebUI agent or runner."""
import hashlib
import json
import logging
import time
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import APIException, PermissionDenied

from .models import DocumentRevision, KnowledgeChunk, KnowledgeDocument, KnowledgeMessage, KnowledgeTask

logger = logging.getLogger(__name__)
TERMINAL = {'completed', 'partial', 'failed', 'cancelled'}


class TaskConflict(APIException):
    status_code = 409
    default_detail = '相同请求编号对应的内容已变化，请重新提交。'


class TaskStopped(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def enqueue_task(project, user, kind, payload, client_request_id, model_info=None, snapshot=None):
    request_hash = hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    with transaction.atomic():
        task, created = KnowledgeTask.objects.get_or_create(
            project=project, created_by=user, kind=kind, client_request_id=client_request_id,
            defaults={
                'payload': payload, 'request_hash': request_hash, 'snapshot': snapshot or [],
                'model_info': model_info or {},
                'total_timeout_seconds': getattr(settings, 'PROJECT_KNOWLEDGE_TOTAL_TIMEOUT', 1200),
            },
        )
        if task.request_hash != request_hash:
            raise TaskConflict()
        if created:
            def publish():
                from .tasks import execute_knowledge_task
                try:
                    queued = execute_knowledge_task.delay(str(task.id))
                    KnowledgeTask.objects.filter(pk=task.pk).update(celery_task_id=queued.id)
                except Exception:
                    logger.exception('知识库任务投递失败: %s', task.id)
                    _finish(task.id, 'failed', code='QUEUE_UNAVAILABLE', message='任务未能进入队列，请检查 Celery 与 Redis 后重新发起。')
            transaction.on_commit(publish)
    return task, created


def _sync_answer(task):
    if task.kind != 'answer':
        return
    conversation_id = task.payload.get('conversation_id')
    if not conversation_id:
        return
    from .models import KnowledgeConversation
    conversation = KnowledgeConversation.objects.filter(
        pk=conversation_id, project_id=task.project_id, created_by_id=task.created_by_id,
    ).first()
    if not conversation:
        return
    data = task.result or {}
    message, _ = KnowledgeMessage.objects.get_or_create(
        conversation=conversation, task=task, role='assistant',
        defaults={'content': ''},
    )
    message.content = data.get('answer') or task.partial_output or task.error_message
    message.result_type = data.get('result_type', 'partial' if task.status != 'completed' else 'insufficient')
    message.sources = data.get('sources') or []
    message.warnings = data.get('warnings') or ([task.error_message] if task.error_message else [])
    message.save(update_fields=['content', 'result_type', 'sources', 'warnings'])


def _stop_preparation(task):
    """Expose a retryable document state even if the worker was terminated."""
    if task.kind != 'prepare':
        return
    revision_id = task.payload.get('revision_id')
    revisions = DocumentRevision.objects.filter(pk=revision_id, document__project_id=task.project_id)
    revisions.filter(parse_status='running').update(parse_status='failed', parse_error='处理已停止，可以重试。')
    revisions.filter(index_status='running').update(index_status='failed', index_error='索引处理已停止，可以重试。')


def _finish(task_id, status, result=None, code='', message=''):
    with transaction.atomic():
        task = KnowledgeTask.objects.select_for_update().get(pk=task_id)
        if task.status in TERMINAL:
            return task
        task.status = status
        task.phase = {'completed': '已完成', 'partial': '部分完成，需要检查', 'failed': '任务失败', 'cancelled': '已取消'}[status]
        if result is not None:
            task.result = result
        task.error_code, task.error_message = code, message
        task.finished_at = timezone.now()
        task.progress = 100 if status == 'completed' else task.progress
        task.save()
        if status != 'completed':
            _stop_preparation(task)
        _sync_answer(task)
        return task


def expire_task(task):
    if task.status == 'running' and task.started_at and timezone.now() > task.started_at + timedelta(seconds=task.total_timeout_seconds + 30):
        task = _finish(task.id, 'partial' if task.result or task.partial_output else 'failed', code='TASK_TIMEOUT', message='任务已超过总时限，已保留最近保存的内容。')
    return task


class TaskContext:
    def __init__(self, task):
        self.task = task
        elapsed = (timezone.now() - task.started_at).total_seconds() if task.started_at else 0
        self.deadline = time.monotonic() + max(0, task.total_timeout_seconds - elapsed)

    def remaining_seconds(self):
        return max(0, self.deadline - time.monotonic())

    def check_active(self):
        from .permissions import require_project
        task = KnowledgeTask.objects.select_related('created_by').get(pk=self.task.pk)
        if task.cancel_requested or task.status in TERMINAL:
            raise TaskStopped('CANCELLED', '任务已停止，保留最近保存的内容。')
        if self.remaining_seconds() <= 0:
            raise TaskStopped('TASK_TIMEOUT', '任务已达到总时限，保留已生成内容。')
        if not task.created_by.is_active:
            raise TaskStopped('ACCESS_REVOKED', '当前用户已停用，任务停止。')
        try:
            require_project(task.project_id, task.created_by, edit=task.kind != 'answer')
        except Exception as exc:
            raise TaskStopped('ACCESS_REVOKED', '项目访问权限已变化，任务停止。') from exc
        for item in task.snapshot:
            if not KnowledgeDocument.objects.filter(
                pk=item['document_id'], project_id=task.project_id, is_active=True,
                is_deleted=False, current_revision_id=item['revision_id'],
            ).exists():
                raise TaskStopped('SOURCES_CHANGED', '所选资料已经更新、停用或删除，请重新选择资料后发起任务。')
        return task

    def checkpoint(self, phase, progress, result=None, partial_output=None):
        self.check_active()
        fields = {'phase': phase, 'progress': min(99, max(0, int(progress))), 'updated_at': timezone.now()}
        if result is not None:
            fields['result'] = result
        if partial_output is not None:
            fields['partial_output'] = partial_output
        updated = KnowledgeTask.objects.filter(pk=self.task.pk, status='running', cancel_requested=False).update(**fields)
        if not updated:
            raise TaskStopped('CANCELLED', '任务已停止。')
        for name, value in fields.items():
            setattr(self.task, name, value)

    def llm(self, messages, on_chunk=None):
        from .llm import stream_call
        self.check_active()
        return stream_call(
            model_config_id=self.task.model_info['config_id'], messages=messages,
            on_chunk=on_chunk, check_active=self.check_active,
            remaining_seconds=self.remaining_seconds,
        )

    def search(self, query, revision_ids=None):
        from .indexing import search_chunks
        self.check_active()
        allowed = {str(item['revision_id']) for item in self.task.snapshot}
        revisions = allowed if revision_ids is None else {str(value) for value in revision_ids}
        if not revisions.issubset(allowed):
            raise PermissionDenied('检索范围不属于当前任务。')
        return search_chunks(self.task.project_id, sorted(revisions), query)


def _prepare_document(task, context):
    from .indexing import index_revision
    from .parsing import parse_document
    revision = DocumentRevision.objects.select_related('document', 'uploaded_file').get(
        pk=task.payload['revision_id'], document__project_id=task.project_id,
    )
    context.check_active()
    try:
        if revision.parse_status != 'ready':
            context.checkpoint('正在解析文档', 10)
            DocumentRevision.objects.filter(pk=revision.pk).update(parse_status='running', parse_error='')
            sections = parse_document(revision.uploaded_file.file.path, revision.uploaded_file.original_name)
            if not sections:
                raise ValueError('文件未提取到有效正文，请检查格式或提供可选中文字的文件。')
            context.check_active()
            with transaction.atomic():
                locked = DocumentRevision.objects.select_for_update().get(pk=revision.pk)
                # A successfully parsed revision is immutable; reindex uses stable chunk IDs.
                if locked.parse_status != 'ready':
                    KnowledgeChunk.objects.filter(revision=locked).delete()
                    KnowledgeChunk.objects.bulk_create([
                        KnowledgeChunk(revision=locked, ordinal=i, section_key=item['section_key'], heading=item.get('heading', ''), content=item['content'], location=item.get('location', {}))
                        for i, item in enumerate(sections)
                    ])
                    locked.parse_status = 'ready'
                    locked.character_count = sum(len(item['content']) for item in sections)
                    locked.content_hash = revision.uploaded_file.file_hash
                    locked.save(update_fields=['parse_status', 'character_count', 'content_hash'])
            revision.refresh_from_db()
        context.checkpoint('正文已解析，正在建立检索索引', 55)
        DocumentRevision.objects.filter(pk=revision.pk).update(index_status='running', index_error='')
        index_revision(revision)
        context.check_active()
        DocumentRevision.objects.filter(pk=revision.pk).update(index_status='ready', index_error='')
        return {'document_id': revision.document_id, 'revision_id': str(revision.pk), 'chunks_count': revision.chunks.count()}
    except TaskStopped:
        DocumentRevision.objects.filter(pk=revision.pk, parse_status='running').update(parse_status='failed', parse_error='处理已停止，可以重试。')
        DocumentRevision.objects.filter(pk=revision.pk, index_status='running').update(index_status='failed', index_error='索引处理已停止，可以重试。')
        raise
    except Exception as exc:
        revision.refresh_from_db()
        if revision.parse_status != 'ready':
            DocumentRevision.objects.filter(pk=revision.pk).update(parse_status='failed', parse_error=str(exc)[:1500])
        else:
            DocumentRevision.objects.filter(pk=revision.pk).update(index_status='failed', index_error=str(exc)[:1500])
        raise


def execute_task(task_id):
    claimed = KnowledgeTask.objects.filter(pk=task_id, status='queued', cancel_requested=False).update(
        status='running', phase='正在启动', started_at=timezone.now(), updated_at=timezone.now(),
    )
    if not claimed:
        return {'task_id': str(task_id), 'status': 'skipped'}
    try:
        return _execute_claimed_task(task_id)
    except KnowledgeTask.DoesNotExist:
        # A cancelled/expired answer can be deleted with its conversation while
        # an in-flight model call is returning. Do not recreate deleted history.
        if KnowledgeTask.objects.filter(pk=task_id).exists():
            raise
        return {'task_id': str(task_id), 'status': 'skipped'}


def _execute_claimed_task(task_id):
    task = KnowledgeTask.objects.select_related('created_by', 'project').get(pk=task_id)
    context = TaskContext(task)
    try:
        context.check_active()
        if task.kind == 'prepare':
            result = _prepare_document(task, context)
        elif task.kind == 'cleanup':
            from .indexing import delete_revision_index
            for revision in DocumentRevision.objects.filter(pk__in=task.payload['revision_ids'], document__project_id=task.project_id):
                context.check_active()
                delete_revision_index(str(revision.id))
            result = {'message': '对应版本的检索索引已清理。'}
        elif task.kind == 'generate':
            from .workflows import generate_cases
            result = generate_cases(task, context)
        elif task.kind == 'answer':
            from .workflows import answer_question
            result = answer_question(task, context)
        else:
            raise ValueError('不支持的知识库任务类型。')
        context.check_active()
        partial = bool(result.get('coverage', {}).get('pending')) or result.get('result_type') == 'partial' or result.get('partial', False)
        finished = _finish(task.id, 'partial' if partial else 'completed', result=result)
    except TaskStopped as exc:
        saved = KnowledgeTask.objects.get(pk=task.id)
        status = 'cancelled' if exc.code == 'CANCELLED' else ('partial' if saved.result or saved.partial_output else 'failed')
        finished = _finish(task.id, status, code=exc.code, message=str(exc))
    except Exception as exc:
        saved = KnowledgeTask.objects.filter(pk=task.id).first()
        if saved is None:
            return {'task_id': str(task.id), 'status': 'skipped'}
        logger.exception('知识库任务失败: task_id=%s kind=%s', task.id, task.kind)
        finished = _finish(task.id, 'partial' if saved.result or saved.partial_output else 'failed', code='KNOWLEDGE_TASK_FAILED', message=str(exc)[:1500] or '任务处理失败，请查看技术日志。')
    return {'task_id': str(task.id), 'status': finished.status}
