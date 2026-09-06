"""HTTP boundary for project knowledge; workers own parsing and LLM work."""

from __future__ import annotations

import hashlib
import uuid

from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import F, Max, OuterRef, Subquery, TextField, Value
from django.db.models.functions import Coalesce
from django.http import Http404, HttpResponse
from django.utils import timezone
from rest_framework import status
from rest_framework.exceptions import PermissionDenied
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from ai_core.models import LLMConfiguration, ModelType
from common.storage import KnowledgeBaseFileService
from projects.knowledge.models import KnowledgeBaseFile
from projects.models import Project, UploadedFile

from . import runtime
from .models import (DocumentRevision, KnowledgeChunk, KnowledgeConversation,
                     KnowledgeDocument, KnowledgeMessage, KnowledgeTask, ManualTestCase)
from .permissions import can_edit, require_project
from .serializers import (CaseGenerationRequestSerializer, CaseSaveRequestSerializer,
                          CleanupRetryRequestSerializer,
                          ConversationCreateSerializer, DocumentCreateSerializer,
                          DocumentUpdateSerializer, ExportRequestSerializer,
                          KnowledgeChunkSerializer, KnowledgeConversationSerializer,
                          KnowledgeDocumentSerializer, KnowledgeMessageSerializer,
                          KnowledgeTaskSerializer, ManualCaseUpdateSerializer,
                          ManualTestCaseSerializer, MessageCreateSerializer,
                          PrepareRequestSerializer)


READY = 'ready'


def _ok(data=None, message='请求成功', code=status.HTTP_200_OK):
    return Response({'success': True, 'data': data, 'message': message}, status=code)


def _fail(message, code=status.HTTP_400_BAD_REQUEST, details=None):
    return Response({'success': False, 'data': {'details': details} if details is not None else {}, 'message': message}, status=code)


def _feature_guard():
    if not getattr(settings, 'PROJECT_KNOWLEDGE_ENABLED', True):
        return _fail('项目知识库功能当前已关闭，暂不接受新的写入请求。', status.HTTP_503_SERVICE_UNAVAILABLE)
    return None


def _task_data(task):
    return KnowledgeTaskSerializer(runtime.expire_task(task)).data


def _snapshot(documents):
    return [{'document_id': item.id, 'revision_id': str(item.current_revision_id)} for item in documents if item.current_revision_id]


def _model_info(config_id):
    config = LLMConfiguration.objects.filter(pk=config_id, model_type=ModelType.LLM, is_active=True).first()
    if config is None:
        return None
    provider = config.provider_name or config.provider
    return {'config_id': config.id, 'name': f'{provider} - {config.model_name}', 'provider': provider, 'model_name': config.model_name}


def _hash_and_rewind(uploaded_file):
    digest = hashlib.sha256()
    for chunk in uploaded_file.chunks():
        digest.update(chunk)
    # Shared upload code hashes the stream a second time, so reset explicitly.
    uploaded_file.seek(0)
    return digest.hexdigest()


def _upload(project, user, uploaded_file):
    content_hash = _hash_and_rewind(uploaded_file)
    result = KnowledgeBaseFileService.upload_knowledge_file(uploaded_file, project.id, user)
    if not result.get('success'):
        raise ValueError(result.get('error') or '文件上传失败。')
    uploaded = UploadedFile.objects.filter(pk=result.get('uploaded_file_id'), project=project).first()
    if uploaded is None:
        raise ValueError('上传文件记录未创建。')
    try:
        knowledge_file, _ = KnowledgeBaseFile.objects.get_or_create(
            project=project, uploaded_file=uploaded,
            defaults={'uploaded_by': user, 'status': KnowledgeBaseFile.RAGIngestionStatus.PENDING},
        )
    except IntegrityError:
        knowledge_file = KnowledgeBaseFile.objects.filter(project=project, uploaded_file=uploaded).first()
        if knowledge_file is None:
            raise
    return uploaded, knowledge_file, content_hash


def _source(chunk):
    revision = chunk.revision
    return {
        'id': str(chunk.id), 'section_key': chunk.section_key, 'heading': chunk.heading,
        'content': chunk.content, 'location': chunk.location, 'document_id': revision.document_id,
        'revision_id': str(revision.id), 'file_name': revision.uploaded_file.original_name,
    }


def _draft_map(task):
    rows = (task.result or {}).get('cases') or (task.result or {}).get('drafts') or []
    return {str(row.get('id') or row.get('draft_id')): row for row in rows if isinstance(row, dict) and (row.get('id') or row.get('draft_id'))}


def _active_request(project, user, kind, client_request_id, conversation_id=None):
    rows = KnowledgeTask.objects.filter(project=project, created_by=user, kind=kind, status__in=('queued', 'running'))
    if conversation_id is not None:
        rows = rows.filter(payload__conversation_id=str(conversation_id))
    for task in rows:
        # Expire abandoned work so a lost worker never permanently blocks retry.
        if runtime.expire_task(task).status in runtime.TERMINAL:
            continue
        if task.client_request_id != client_request_id:
            return task
    return None


class KnowledgeAPIView(APIView):
    permission_classes = [IsAuthenticated]
    parser_classes = [JSONParser, MultiPartParser, FormParser]

    def project(self, request, project_id, edit=False):
        try:
            return require_project(project_id, request.user, edit=edit), None
        except Http404 as exc:
            return None, _fail(str(exc), status.HTTP_404_NOT_FOUND)
        except PermissionDenied as exc:
            return None, _fail(str(exc), status.HTTP_403_FORBIDDEN)

    def payload(self, serializer_class, request):
        serializer = serializer_class(data=request.data)
        if not serializer.is_valid():
            return None, _fail('请求参数不正确。', status.HTTP_400_BAD_REQUEST, serializer.errors)
        return serializer.validated_data, None


class KnowledgeOptionsView(KnowledgeAPIView):
    def get(self, request, project_id):
        project, error = self.project(request, project_id)
        if error:
            return error
        models = LLMConfiguration.objects.filter(model_type=ModelType.LLM, is_active=True).order_by('-created_at')
        return _ok({
            'enabled': bool(getattr(settings, 'PROJECT_KNOWLEDGE_ENABLED', True)),
            'can_edit': can_edit(project, request.user),
            'models': [{'id': item.id, 'name': f'{item.provider_name or item.provider} - {item.model_name}',
                        'provider': item.provider_name or item.provider, 'model_name': item.model_name} for item in models],
            'total_timeout_seconds': getattr(settings, 'PROJECT_KNOWLEDGE_TOTAL_TIMEOUT', 1200),
        }, '项目知识库选项获取成功。')


class DocumentListView(KnowledgeAPIView):
    def get(self, request, project_id):
        project, error = self.project(request, project_id)
        if error:
            return error
        rows = KnowledgeDocument.objects.filter(project=project, is_deleted=False).select_related('current_revision', 'created_by')
        return _ok({'items': KnowledgeDocumentSerializer(rows, many=True).data}, '项目资料获取成功。')

    def post(self, request, project_id):
        if (guard := _feature_guard()):
            return guard
        project, error = self.project(request, project_id, edit=True)
        if error:
            return error
        data, error = self.payload(DocumentCreateSerializer, request)
        if error:
            return error
        restored = False
        try:
            with transaction.atomic():
                uploaded, knowledge_file, content_hash = _upload(project, request.user, data['file'])
                existing = KnowledgeDocument.objects.filter(knowledge_file=knowledge_file).first()
                if existing:
                    if not existing.is_deleted:
                        return _fail('该文件内容已作为项目资料存在，不能重复创建资料。', status.HTTP_409_CONFLICT, {'document_id': existing.id})
                    document = KnowledgeDocument.objects.select_for_update().get(pk=existing.pk)
                    if not document.is_deleted:
                        return _fail('该文件内容已作为项目资料存在，不能重复创建资料。', status.HTTP_409_CONFLICT, {'document_id': document.id})
                    revision = DocumentRevision.objects.create(
                        document=document,
                        number=(document.revisions.aggregate(max_number=Max('number'))['max_number'] or 0) + 1,
                        uploaded_file=uploaded, content_hash=content_hash,
                    )
                    document.knowledge_file = knowledge_file
                    document.name = data.get('name') or document.name
                    document.document_type = data['document_type']
                    document.module = data.get('module') or '未分类'
                    document.is_active, document.is_deleted = True, False
                    restored = True
                else:
                    document = KnowledgeDocument.objects.create(
                        project=project, knowledge_file=knowledge_file, name=data.get('name') or uploaded.original_name,
                        document_type=data['document_type'], module=data.get('module') or '未分类', created_by=request.user,
                    )
                    revision = DocumentRevision.objects.create(document=document, number=1, uploaded_file=uploaded, content_hash=content_hash)
                document.current_revision = revision
                document.save()
        except ValueError as exc:
            return _fail(str(exc))
        except IntegrityError:
            return _fail('相同文件正在被并发创建或恢复，请刷新后重试。', status.HTTP_409_CONFLICT)
        message = '已恢复已删除资料，并创建新的内容版本。' if restored else '项目资料上传成功。'
        return _ok(KnowledgeDocumentSerializer(document).data, message, status.HTTP_201_CREATED)


class DocumentDetailView(KnowledgeAPIView):
    @staticmethod
    def _get(project, document_id):
        return KnowledgeDocument.objects.filter(project=project, pk=document_id, is_deleted=False).select_related('current_revision').first()

    def get(self, request, project_id, document_id):
        project, error = self.project(request, project_id)
        if error:
            return error
        document = self._get(project, document_id)
        return _ok(KnowledgeDocumentSerializer(document).data, '项目资料详情获取成功。') if document else _fail('项目资料不存在。', status.HTTP_404_NOT_FOUND)

    def patch(self, request, project_id, document_id):
        if (guard := _feature_guard()):
            return guard
        project, error = self.project(request, project_id, edit=True)
        if error:
            return error
        document = self._get(project, document_id)
        if document is None:
            return _fail('项目资料不存在。', status.HTTP_404_NOT_FOUND)
        data, error = self.payload(DocumentUpdateSerializer, request)
        if error:
            return error
        try:
            with transaction.atomic():
                document = KnowledgeDocument.objects.select_for_update().get(pk=document.pk, project=project)
                replacement = data.pop('file', None)
                if replacement is not None:
                    uploaded, knowledge_file, content_hash = _upload(project, request.user, replacement)
                    occupied = KnowledgeDocument.objects.filter(knowledge_file=knowledge_file).exclude(pk=document.pk).first()
                    if occupied:
                        return _fail('替换文件已被另一份项目资料使用。', status.HTTP_409_CONFLICT, {'document_id': occupied.id})
                    number = (document.revisions.aggregate(max_number=Max('number'))['max_number'] or 0) + 1
                    revision = DocumentRevision.objects.create(document=document, number=number, uploaded_file=uploaded, content_hash=content_hash)
                    document.knowledge_file, document.current_revision = knowledge_file, revision
                for field in ('name', 'document_type', 'module', 'is_active'):
                    if field in data:
                        setattr(document, field, data[field])
                document.save()
        except ValueError as exc:
            return _fail(str(exc))
        except IntegrityError:
            return _fail('替换文件时发生并发冲突，请刷新后重试。', status.HTTP_409_CONFLICT)
        return _ok(KnowledgeDocumentSerializer(document).data, '项目资料已更新。')

    def delete(self, request, project_id, document_id):
        if (guard := _feature_guard()):
            return guard
        project, error = self.project(request, project_id, edit=True)
        if error:
            return error
        document = self._get(project, document_id)
        if document is None:
            return _fail('项目资料不存在。', status.HTTP_404_NOT_FOUND)
        revision_ids = [str(item) for item in document.revisions.values_list('id', flat=True)]
        with transaction.atomic():
            document.is_active, document.is_deleted = False, True
            document.save(update_fields=['is_active', 'is_deleted', 'updated_at'])
            task, _ = runtime.enqueue_task(
                project, request.user, 'cleanup', {'revision_ids': revision_ids},
                f'cleanup-document-{document.id}-{int(timezone.now().timestamp() * 1000)}', snapshot=[],
            )
        return _ok(_task_data(task), '资料已删除；历史引用快照会保留，索引清理已排队。', status.HTTP_202_ACCEPTED)


class DocumentPrepareView(KnowledgeAPIView):
    def post(self, request, project_id, document_id):
        if (guard := _feature_guard()):
            return guard
        project, error = self.project(request, project_id, edit=True)
        if error:
            return error
        data, error = self.payload(PrepareRequestSerializer, request)
        if error:
            return error
        with transaction.atomic():
            document = KnowledgeDocument.objects.select_for_update().select_related('current_revision').filter(
                project=project, pk=document_id, is_deleted=False, is_active=True,
            ).first()
            if document is None or document.current_revision is None:
                return _fail('项目资料不存在或已停用。', status.HTTP_404_NOT_FOUND)
            if data['stage'] == 'index' and document.current_revision.parse_status != READY:
                return _fail('正文尚未解析完成，不能只重试建立索引。', status.HTTP_409_CONFLICT)
            existing = KnowledgeTask.objects.filter(
                project=project, kind='prepare', status__in=('queued', 'running'),
                payload__revision_id=str(document.current_revision_id),
            ).order_by('-created_at').first()
            if existing is not None:
                if existing.created_by_id != request.user.id:
                    return _fail('该资料已有其他成员正在处理，请等待当前任务结束后再试。', status.HTTP_409_CONFLICT)
                return _ok(_task_data(existing), '该资料正在处理中，已返回现有任务。', status.HTTP_202_ACCEPTED)
            request_id = str(data['client_request_id']) if data.get('client_request_id') else f'prepare-{document.id}-{document.current_revision_id}-{data["stage"]}'
            task, _ = runtime.enqueue_task(
                project, request.user, 'prepare',
                {'document_id': document.id, 'revision_id': str(document.current_revision_id), 'stage': data['stage']},
                request_id, snapshot=_snapshot([document]),
            )
        return _ok(_task_data(task), '资料处理任务已排队。', status.HTTP_202_ACCEPTED)


class DocumentSectionsView(KnowledgeAPIView):
    def get(self, request, project_id, document_id):
        project, error = self.project(request, project_id)
        if error:
            return error
        document = KnowledgeDocument.objects.filter(project=project, pk=document_id, is_deleted=False).select_related('current_revision').first()
        if document is None or document.current_revision is None:
            return _fail('项目资料不存在。', status.HTTP_404_NOT_FOUND)
        rows = KnowledgeChunk.objects.filter(revision=document.current_revision).select_related('revision__document', 'revision__uploaded_file')
        return _ok({'items': KnowledgeChunkSerializer(rows, many=True).data}, '资料章节获取成功。')


class SourceDetailView(KnowledgeAPIView):
    def get(self, request, project_id, chunk_id):
        project, error = self.project(request, project_id)
        if error:
            return error
        chunk = KnowledgeChunk.objects.select_related('revision__document', 'revision__uploaded_file').filter(pk=chunk_id, revision__document__project=project).first()
        if chunk is None:
            return _fail('来源片段不存在。', status.HTTP_404_NOT_FOUND)
        document = chunk.revision.document
        source = _source(chunk)
        source.update({'active': bool(document.is_active and not document.is_deleted and document.current_revision_id == chunk.revision_id),
                       'version': {'id': str(chunk.revision_id), 'number': chunk.revision.number}})
        return _ok(source, '来源片段获取成功。')


class TaskListView(KnowledgeAPIView):
    def get(self, request, project_id):
        project, error = self.project(request, project_id)
        if error:
            return error
        rows = KnowledgeTask.objects.filter(project=project, created_by=request.user)
        kind = request.query_params.get('kind')
        if kind:
            if kind not in {'prepare', 'generate', 'answer', 'cleanup'}:
                return _fail('任务类型不支持。')
            rows = rows.filter(kind=kind)
        return _ok({'items': [_task_data(item) for item in rows]}, '任务历史获取成功。')


class TaskDetailView(KnowledgeAPIView):
    def get(self, request, project_id, task_id):
        project, error = self.project(request, project_id)
        if error:
            return error
        task = KnowledgeTask.objects.filter(pk=task_id, project=project, created_by=request.user).first()
        return _ok(_task_data(task), '任务状态获取成功。') if task else _fail('任务不存在。', status.HTTP_404_NOT_FOUND)


class TaskCancelView(KnowledgeAPIView):
    def post(self, request, project_id, task_id):
        # Cancellation does not submit a new task, so it remains available
        # while the feature switch is off to prevent stranded running work.
        project, error = self.project(request, project_id)
        if error:
            return error
        with transaction.atomic():
            task = KnowledgeTask.objects.select_for_update().filter(pk=task_id, project=project, created_by=request.user).first()
            if task is None:
                return _fail('任务不存在。', status.HTTP_404_NOT_FOUND)
            if task.status not in runtime.TERMINAL:
                task.cancel_requested, task.status, task.phase, task.finished_at = True, 'cancelled', '已取消', timezone.now()
                task.save(update_fields=['cancel_requested', 'status', 'phase', 'finished_at', 'updated_at'])
                if task.kind == 'prepare':
                    runtime._stop_preparation(task)
                runtime._sync_answer(task)
        return _ok(_task_data(task), '任务已取消。')


class CleanupTaskRetryView(KnowledgeAPIView):
    def post(self, request, project_id, task_id):
        if (guard := _feature_guard()):
            return guard
        project, error = self.project(request, project_id, edit=True)
        if error:
            return error
        data, error = self.payload(CleanupRetryRequestSerializer, request)
        if error:
            return error
        with transaction.atomic():
            task = KnowledgeTask.objects.select_for_update().filter(
                pk=task_id, project=project, created_by=request.user,
            ).first()
            if task is None:
                return _fail('任务不存在。', status.HTTP_404_NOT_FOUND)
            if task.kind != 'cleanup':
                return _fail('仅支持重试索引清理任务。', status.HTTP_400_BAD_REQUEST)
            if task.status not in runtime.TERMINAL or task.status == 'completed':
                return _fail('仅已结束但未完成的索引清理任务可以重试。', status.HTTP_409_CONFLICT)
            raw_revision_ids = (task.payload or {}).get('revision_ids')
            if not isinstance(raw_revision_ids, list) or not raw_revision_ids:
                return _fail('原索引清理任务缺少可重试的版本范围。', status.HTTP_409_CONFLICT)
            try:
                revision_ids = [str(uuid.UUID(str(value))) for value in raw_revision_ids]
            except (TypeError, ValueError, AttributeError):
                return _fail('原索引清理任务的版本范围格式不正确。', status.HTTP_409_CONFLICT)
            if len(revision_ids) != len(set(revision_ids)):
                return _fail('原索引清理任务的版本范围重复，不能安全重试。', status.HTTP_409_CONFLICT)
            inflight = KnowledgeTask.objects.filter(
                project=project, created_by=request.user, kind='cleanup',
                status__in=('queued', 'running'), payload__revision_ids=revision_ids,
            ).order_by('-created_at').first()
            if inflight is not None:
                return _ok(_task_data(inflight), '相同版本范围的索引清理正在重试。', status.HTTP_202_ACCEPTED)
            retried, _ = runtime.enqueue_task(
                project, request.user, 'cleanup', {'revision_ids': revision_ids},
                f'cleanup-retry-{uuid.uuid4()}', snapshot=[],
            )
        return _ok(_task_data(retried), '索引清理重试任务已排队；已删除资料仍不会重新进入检索范围。', status.HTTP_202_ACCEPTED)


class CaseGenerationView(KnowledgeAPIView):
    def post(self, request, project_id):
        if (guard := _feature_guard()):
            return guard
        project, error = self.project(request, project_id, edit=True)
        if error:
            return error
        data, error = self.payload(CaseGenerationRequestSerializer, request)
        if error:
            return error
        model_info = _model_info(data['model_config_id'])
        if model_info is None:
            return _fail('所选模型不存在或未启用。')
        docs = list(KnowledgeDocument.objects.filter(project=project, is_active=True, is_deleted=False).select_related('current_revision'))
        by_id = {item.id: item for item in docs if item.current_revision_id}
        primary = [by_id.get(item) for item in data['document_ids']]
        if any(item is None for item in primary):
            return _fail('主需求资料不存在、不属于当前项目或已删除。')
        unparsed = [item.name for item in primary if item.current_revision.parse_status != READY]
        if unparsed:
            return _fail('主需求资料尚未完成正文解析。', status.HTTP_409_CONFLICT, {'documents': unparsed})
        not_indexed = [item.name for item in primary if item.current_revision.index_status != READY]
        if not_indexed and not data['text_only']:
            return _fail('主需求资料尚未建立检索索引；请明确选择仅基于所选正文生成。', status.HTTP_409_CONFLICT, {'documents': not_indexed})
        supplied_supplemental = 'supplemental_document_ids' in data
        if data['text_only'] and supplied_supplemental and data['supplemental_document_ids']:
            return _fail('仅基于所选正文生成时不能选择补充资料。')
        if data['text_only']:
            supplemental = []
        elif supplied_supplemental:
            supplemental = [by_id.get(item) for item in data['supplemental_document_ids']]
            if any(item is None for item in supplemental):
                return _fail('补充资料不存在、不属于当前项目或已删除。')
            unavailable = [item.name for item in supplemental if item.current_revision.index_status != READY]
            if unavailable:
                return _fail('补充资料尚未建立检索索引。', status.HTTP_409_CONFLICT, {'documents': unavailable})
        else:
            primary_ids = {item.id for item in primary}
            supplemental = [item for item in docs if item.id not in primary_ids and item.current_revision_id and item.current_revision.index_status == READY]
        if data['section_ids']:
            valid = set(KnowledgeChunk.objects.filter(pk__in=data['section_ids'], revision_id__in=[item.current_revision_id for item in primary]).values_list('id', flat=True))
            if {str(item) for item in valid} != {str(item) for item in data['section_ids']}:
                return _fail('所选章节不属于当前主需求资料版本。')
        scope, seen = [], set()
        for item in [*primary, *supplemental]:
            if item.id not in seen:
                seen.add(item.id)
                scope.append(item)
        payload = {'document_ids': data['document_ids'], 'section_ids': [str(item) for item in data['section_ids']],
                   'supplemental_document_ids': [item.id for item in supplemental], 'goal': data['goal'],
                   'module': data['module'] or '未分类', 'model_config_id': data['model_config_id'],
                   'text_only': data['text_only']}
        with transaction.atomic():
            Project.objects.select_for_update().get(pk=project.pk)
            if _active_request(project, request.user, 'generate', data['client_request_id']):
                return _fail('当前项目已有您的手工用例生成任务，请等待完成或取消后再提交。', status.HTTP_409_CONFLICT)
            task, _ = runtime.enqueue_task(project, request.user, 'generate', payload, data['client_request_id'], model_info=model_info, snapshot=_snapshot(scope))
        return _ok(_task_data(task), '手工用例生成任务已排队。', status.HTTP_202_ACCEPTED)


class CaseGenerationSaveView(KnowledgeAPIView):
    def post(self, request, project_id, task_id):
        if (guard := _feature_guard()):
            return guard
        project, error = self.project(request, project_id, edit=True)
        if error:
            return error
        data, error = self.payload(CaseSaveRequestSerializer, request)
        if error:
            return error
        task = KnowledgeTask.objects.filter(pk=task_id, project=project, created_by=request.user, kind='generate').first()
        if task is None:
            return _fail('生成任务不存在。', status.HTTP_404_NOT_FOUND)
        if task.status not in runtime.TERMINAL:
            return _fail('生成任务尚未结束，暂不能保存草稿。', status.HTTP_409_CONFLICT)
        drafts, prepared, saved = _draft_map(task), [], []
        for choice in data['cases']:
            draft = drafts.get(choice['draft_id'])
            if draft is None:
                return _fail('选择的草稿不属于该生成任务。', details={'draft_id': choice['draft_id']})
            title, steps = choice.get('title', draft.get('title', '')).strip(), choice.get('steps', draft.get('steps', []))
            if not title or not isinstance(steps, list) or not steps:
                return _fail('草稿缺少标题或步骤，不能保存为正式用例。', details={'draft_id': choice['draft_id']})
            prepared.append((choice, draft, title, steps))
        try:
            with transaction.atomic():
                for choice, draft, title, steps in prepared:
                    defaults = {'project': project, 'created_by': request.user, 'title': title,
                                'module': choice.get('module', draft.get('module') or '未分类'),
                                'test_type': choice.get('test_type', draft.get('test_type') or '正常'),
                                'preconditions': choice.get('preconditions', draft.get('preconditions') or []),
                                'test_data': choice.get('test_data', draft.get('test_data') or ''), 'steps': steps,
                                'sources': draft.get('sources') or [], 'test_point_ids': draft.get('test_point_ids') or [],
                                'pending_questions': choice.get('pending_questions', draft.get('pending_questions') or []),
                                'review_status': choice.get('review_status', draft.get('review_status') or 'unreviewed')}
                    case, _ = ManualTestCase.objects.get_or_create(source_task=task, draft_id=choice['draft_id'], defaults=defaults)
                    saved.append(case)
        except IntegrityError:
            return _fail('保存用例时发生并发冲突，请刷新后重试。', status.HTTP_409_CONFLICT)
        return _ok({'items': ManualTestCaseSerializer(saved, many=True).data}, '草稿已保存；重复请求不会覆盖已编辑的用例。')


class ManualCaseListView(KnowledgeAPIView):
    def get(self, request, project_id):
        project, error = self.project(request, project_id)
        if error:
            return error
        rows = ManualTestCase.objects.filter(project=project)
        if request.query_params.get('module'):
            rows = rows.filter(module=request.query_params['module'])
        return _ok({'items': ManualTestCaseSerializer(rows, many=True).data}, '手工测试用例获取成功。')


class ManualCaseDetailView(KnowledgeAPIView):
    @staticmethod
    def _get(project, case_id):
        return ManualTestCase.objects.filter(pk=case_id, project=project).first()

    def get(self, request, project_id, case_id):
        project, error = self.project(request, project_id)
        if error:
            return error
        case = self._get(project, case_id)
        return _ok(ManualTestCaseSerializer(case).data, '手工测试用例详情获取成功。') if case else _fail('手工测试用例不存在。', status.HTTP_404_NOT_FOUND)

    def patch(self, request, project_id, case_id):
        if (guard := _feature_guard()):
            return guard
        project, error = self.project(request, project_id, edit=True)
        if error:
            return error
        data, error = self.payload(ManualCaseUpdateSerializer, request)
        if error:
            return error
        expected = data.pop('revision')
        with transaction.atomic():
            changed = ManualTestCase.objects.filter(pk=case_id, project=project, revision=expected).update(**data, revision=F('revision') + 1, updated_at=timezone.now())
            if not changed:
                return _fail('用例已被其他修改，请刷新后再编辑。', status.HTTP_409_CONFLICT)
            case = self._get(project, case_id)
        return _ok(ManualTestCaseSerializer(case).data, '手工测试用例已更新。')

    def delete(self, request, project_id, case_id):
        if (guard := _feature_guard()):
            return guard
        project, error = self.project(request, project_id, edit=True)
        if error:
            return error
        case = self._get(project, case_id)
        if case is None:
            return _fail('手工测试用例不存在。', status.HTTP_404_NOT_FOUND)
        case.delete()
        return _ok({}, '手工测试用例已删除。')


class ManualCaseExportView(KnowledgeAPIView):
    def post(self, request, project_id):
        project, error = self.project(request, project_id)
        if error:
            return error
        data, error = self.payload(ExportRequestSerializer, request)
        if error:
            return error
        rows = list(ManualTestCase.objects.filter(project=project, pk__in=data['ids']))
        if len(rows) != len(set(data['ids'])):
            return _fail('存在不属于当前项目的手工测试用例。')
        from .exporting import build_workbook
        content = build_workbook(ManualTestCaseSerializer(rows, many=True).data)
        result = HttpResponse(content, content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
        result['Content-Disposition'] = 'attachment; filename="manual-test-cases.xlsx"'
        return result


class ConversationListView(KnowledgeAPIView):
    def get(self, request, project_id):
        project, error = self.project(request, project_id)
        if error:
            return error
        first_question = KnowledgeMessage.objects.filter(
            conversation_id=OuterRef('pk'), role='user',
        ).order_by('created_at', 'id').values('content')[:1]
        rows = KnowledgeConversation.objects.filter(project=project, created_by=request.user).annotate(
            first_question=Coalesce(Subquery(first_question), Value(''), output_field=TextField()),
        )
        return _ok({'items': KnowledgeConversationSerializer(rows, many=True).data}, '知识问答会话获取成功。')

    def post(self, request, project_id):
        if (guard := _feature_guard()):
            return guard
        project, error = self.project(request, project_id)
        if error:
            return error
        data, error = self.payload(ConversationCreateSerializer, request)
        if error:
            return error
        item = KnowledgeConversation.objects.create(project=project, created_by=request.user, title=data['title'])
        return _ok(KnowledgeConversationSerializer(item).data, '知识问答会话已创建。', status.HTTP_201_CREATED)


class MessageListView(KnowledgeAPIView):
    @staticmethod
    def _conversation(project, user, conversation_id):
        return KnowledgeConversation.objects.filter(pk=conversation_id, project=project, created_by=user).first()

    def get(self, request, project_id, conversation_id):
        project, error = self.project(request, project_id)
        if error:
            return error
        conversation = self._conversation(project, request.user, conversation_id)
        if conversation is None:
            return _fail('知识问答会话不存在。', status.HTTP_404_NOT_FOUND)
        return _ok({'items': KnowledgeMessageSerializer(conversation.messages.select_related('task'), many=True).data}, '知识问答消息获取成功。')

    def post(self, request, project_id, conversation_id):
        if (guard := _feature_guard()):
            return guard
        project, error = self.project(request, project_id)
        if error:
            return error
        conversation = self._conversation(project, request.user, conversation_id)
        if conversation is None:
            return _fail('知识问答会话不存在。', status.HTTP_404_NOT_FOUND)
        data, error = self.payload(MessageCreateSerializer, request)
        if error:
            return error
        model_info = _model_info(data['model_config_id'])
        if model_info is None:
            return _fail('所选模型不存在或未启用。')
        docs = list(KnowledgeDocument.objects.filter(project=project, is_active=True, is_deleted=False).select_related('current_revision'))
        if 'document_ids' in data:
            selected = [item for item in docs if item.id in set(data['document_ids'])]
            if len(selected) != len(data['document_ids']):
                return _fail('所选资料不存在、不属于当前项目或已删除。')
            unavailable = [item for item in selected if not item.current_revision_id or item.current_revision.index_status != READY]
            if unavailable:
                return _fail('所选资料尚未建立检索索引。', status.HTTP_409_CONFLICT,
                             {'documents': [{'id': item.id, 'name': item.name, 'index_status': item.current_revision.index_status if item.current_revision_id else 'pending'} for item in unavailable]})
        else:
            unavailable = [item for item in docs if not item.current_revision_id or item.current_revision.index_status != READY]
            if unavailable:
                return _fail('存在尚未建立检索索引的资料，请明确排除后再提问。', status.HTTP_409_CONFLICT,
                             {'documents': [{'id': item.id, 'name': item.name, 'index_status': item.current_revision.index_status if item.current_revision_id else 'pending'} for item in unavailable]})
            selected = docs
        if not selected:
            return _fail('当前项目没有可用于问答的已就绪资料。', status.HTTP_409_CONFLICT)
        payload = {'conversation_id': str(conversation.id), 'question': data['question'],
                   'document_ids': [item.id for item in selected], 'model_config_id': data['model_config_id']}
        try:
            with transaction.atomic():
                KnowledgeConversation.objects.select_for_update().get(pk=conversation.pk)
                if _active_request(project, request.user, 'answer', data['client_request_id'], conversation.id):
                    return _fail('当前会话的上一轮回答尚未结束，请等待完成或取消后继续提问。', status.HTTP_409_CONFLICT)
                task, created = runtime.enqueue_task(project, request.user, 'answer', payload, data['client_request_id'], model_info=model_info, snapshot=_snapshot(selected))
                if created:
                    user_message = KnowledgeMessage.objects.create(conversation=conversation, task=task, role='user', content=data['question'])
                else:
                    user_message = KnowledgeMessage.objects.filter(conversation=conversation, task=task, role='user').first()
                assistant, _ = KnowledgeMessage.objects.get_or_create(conversation=conversation, task=task, role='assistant', defaults={'content': ''})
        except runtime.TaskConflict as exc:
            return _fail(str(exc), status.HTTP_409_CONFLICT)
        return _ok({'task': _task_data(task), 'user_message': KnowledgeMessageSerializer(user_message).data if user_message else None,
                    'assistant_message': KnowledgeMessageSerializer(assistant).data}, '知识问答任务已排队。', status.HTTP_202_ACCEPTED)
