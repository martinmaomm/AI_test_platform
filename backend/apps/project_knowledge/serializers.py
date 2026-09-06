"""Serializers and Chinese request contracts for project knowledge APIs."""

from collections.abc import Mapping
from copy import deepcopy
from pathlib import Path

from rest_framework import serializers

from .models import (
    DocumentRevision,
    KnowledgeChunk,
    KnowledgeConversation,
    KnowledgeDocument,
    KnowledgeMessage,
    KnowledgeTask,
    ManualTestCase,
)


MAX_KNOWLEDGE_FILE_SIZE = 50 * 1024 * 1024
ALLOWED_KNOWLEDGE_EXTENSIONS = {'txt', 'md', 'docx', 'pdf', 'xlsx'}
LEGACY_KNOWLEDGE_EXTENSIONS = {'doc', 'xls'}


class StrictSerializer(serializers.Serializer):
    """Reject unknown request fields with a stable Chinese validation error."""

    def to_internal_value(self, data):
        if not isinstance(data, Mapping):
            raise serializers.ValidationError({'请求体': '请求体必须是对象。'})
        unknown = set(data) - set(self.fields)
        if unknown:
            raise serializers.ValidationError({
                key: '该字段不属于当前请求。' for key in sorted(unknown)
            })
        return super().to_internal_value(data)


def _char(**kwargs):
    return serializers.CharField(
        error_messages={
            'required': '该字段不能为空。', 'blank': '该字段不能为空。',
            'max_length': '内容长度超过限制。', 'null': '该字段不能为空。',
            'invalid': '字段格式不正确。',
        },
        **kwargs,
    )


def _integer(**kwargs):
    return serializers.IntegerField(
        error_messages={
            'required': '该字段不能为空。', 'invalid': '必须是整数。',
            'min_value': '数值小于允许范围。', 'max_value': '数值超过允许范围。',
            'null': '该字段不能为空。',
        },
        **kwargs,
    )


def _uuid(**kwargs):
    return serializers.UUIDField(
        error_messages={
            'required': '该字段不能为空。', 'invalid': '标识格式不正确。',
            'null': '该字段不能为空。',
        },
        **kwargs,
    )


def _validate_knowledge_file(value):
    suffix = Path(value.name or '').suffix.lower().lstrip('.')
    if suffix in LEGACY_KNOWLEDGE_EXTENSIONS:
        raise serializers.ValidationError('暂不支持 DOC/XLS，请转换为 DOCX/XLSX 后上传。')
    if suffix not in ALLOWED_KNOWLEDGE_EXTENSIONS:
        raise serializers.ValidationError('仅支持 TXT、Markdown、DOCX、PDF 和 XLSX 文件。')
    if value.size > MAX_KNOWLEDGE_FILE_SIZE:
        raise serializers.ValidationError('文件不能超过 50MiB。')
    return value


class DocumentRevisionSerializer(serializers.ModelSerializer):
    class Meta:
        model = DocumentRevision
        fields = (
            'id', 'number', 'content_hash', 'parse_status', 'index_status',
            'parse_error', 'index_error', 'character_count', 'created_at',
        )
        read_only_fields = fields


class KnowledgeDocumentSerializer(serializers.ModelSerializer):
    current_revision = DocumentRevisionSerializer(read_only=True)

    class Meta:
        model = KnowledgeDocument
        fields = (
            'id', 'name', 'document_type', 'module', 'is_active', 'is_deleted',
            'current_revision', 'created_by', 'created_at', 'updated_at',
        )
        read_only_fields = ('id', 'is_deleted', 'current_revision', 'created_by', 'created_at', 'updated_at')


class KnowledgeChunkSerializer(serializers.ModelSerializer):
    document_id = serializers.IntegerField(source='revision.document_id', read_only=True)
    revision_id = serializers.UUIDField(read_only=True)
    file_name = serializers.CharField(source='revision.uploaded_file.original_name', read_only=True)

    class Meta:
        model = KnowledgeChunk
        fields = ('id', 'section_key', 'heading', 'content', 'location', 'document_id', 'revision_id', 'file_name')
        read_only_fields = fields


class KnowledgeTaskSerializer(serializers.ModelSerializer):
    result = serializers.SerializerMethodField()

    def get_result(self, instance):
        result = deepcopy(instance.result or {})
        if not isinstance(result, dict):
            return result
        if instance.kind == 'generate':
            result['saved_case_ids'] = list(ManualTestCase.objects.filter(source_task=instance).values_list('draft_id', flat=True))
        if isinstance(result.get('sources'), list):
            result['sources'] = _with_source_state(result['sources'])
        for field in ('cases', 'drafts'):
            if isinstance(result.get(field), list):
                result[field] = [
                    {**item, 'sources': _with_source_state(item.get('sources', []))}
                    if isinstance(item, dict) else item
                    for item in result[field]
                ]
        return result

    class Meta:
        model = KnowledgeTask
        fields = (
            'id', 'kind', 'client_request_id', 'payload', 'snapshot', 'model_info',
            'status', 'phase', 'progress', 'partial_output', 'result', 'error_code',
            'error_message', 'cancel_requested', 'total_timeout_seconds', 'started_at',
            'finished_at', 'created_at', 'updated_at',
        )
        read_only_fields = fields


class ManualTestCaseSerializer(serializers.ModelSerializer):
    sources = serializers.SerializerMethodField()

    def get_sources(self, instance):
        return _with_source_state(instance.sources or [])

    class Meta:
        model = ManualTestCase
        fields = (
            'id', 'source_task', 'draft_id', 'title', 'module', 'test_type',
            'preconditions', 'test_data', 'steps', 'sources', 'test_point_ids',
            'pending_questions', 'review_status', 'revision', 'created_by',
            'created_at', 'updated_at',
        )
        read_only_fields = ('id', 'source_task', 'draft_id', 'sources', 'test_point_ids', 'revision', 'created_by', 'created_at', 'updated_at')


class KnowledgeConversationSerializer(serializers.ModelSerializer):
    class Meta:
        model = KnowledgeConversation
        fields = ('id', 'title', 'created_at')
        read_only_fields = ('id', 'created_at')


class KnowledgeMessageSerializer(serializers.ModelSerializer):
    task_id = serializers.UUIDField(read_only=True, allow_null=True)
    task_status = serializers.CharField(source='task.status', read_only=True, allow_null=True)
    sources = serializers.SerializerMethodField()

    def get_sources(self, instance):
        return _with_source_state(instance.sources or [])

    class Meta:
        model = KnowledgeMessage
        fields = (
            'id', 'role', 'content', 'result_type', 'sources', 'warnings',
            'task_id', 'task_status', 'created_at',
        )
        read_only_fields = fields


class DocumentCreateSerializer(StrictSerializer):
    file = serializers.FileField(error_messages={'required': '请选择要上传的文件。', 'invalid': '上传文件不正确。'})
    name = _char(required=False, allow_blank=False, max_length=255)
    document_type = serializers.ChoiceField(
        choices=('requirement', 'rule', 'guide', 'other'), required=False, default='requirement',
        error_messages={'invalid_choice': '文档类型不支持。', 'required': '文档类型不能为空。'},
    )
    module = _char(required=False, allow_blank=True, max_length=100, default='未分类')

    def validate_file(self, value):
        return _validate_knowledge_file(value)


class DocumentUpdateSerializer(StrictSerializer):
    file = serializers.FileField(required=False, error_messages={'invalid': '上传文件不正确。'})
    name = _char(required=False, allow_blank=False, max_length=255)
    document_type = serializers.ChoiceField(
        choices=('requirement', 'rule', 'guide', 'other'), required=False,
        error_messages={'invalid_choice': '文档类型不支持。'},
    )
    module = _char(required=False, allow_blank=True, max_length=100)
    is_active = serializers.BooleanField(required=False, error_messages={'invalid': '启用状态必须是布尔值。'})

    def validate_file(self, value):
        return _validate_knowledge_file(value)

    def validate(self, attrs):
        if not attrs:
            raise serializers.ValidationError({'请求体': '至少提供一个要修改的字段。'})
        return attrs


class PrepareRequestSerializer(StrictSerializer):
    stage = serializers.ChoiceField(
        choices=('all', 'index'), required=False, default='all',
        error_messages={'invalid_choice': '处理阶段仅支持 all 或 index。'},
    )
    client_request_id = _uuid(required=False)


class CleanupRetryRequestSerializer(StrictSerializer):
    """Retry scope is server-derived; an empty object is the only valid body."""
    pass


class CaseGenerationRequestSerializer(StrictSerializer):
    document_ids = serializers.ListField(
        child=_integer(min_value=1), allow_empty=False,
        error_messages={'required': '请选择主需求资料。', 'empty': '至少选择一份主需求资料。', 'invalid': '主需求资料格式不正确。'},
    )
    section_ids = serializers.ListField(child=_uuid(), required=False, allow_empty=True, default=list, error_messages={'not_a_list': '章节必须是列表。'})
    supplemental_document_ids = serializers.ListField(child=_integer(min_value=1), required=False, allow_empty=True, error_messages={'not_a_list': '补充资料必须是列表。'})
    goal = _char(max_length=2000)
    module = _char(required=False, allow_blank=True, max_length=100, default='未分类')
    model_config_id = _integer(min_value=1)
    text_only = serializers.BooleanField(required=False, default=False, error_messages={'invalid': '仅正文生成标识必须是布尔值。'})
    client_request_id = _char(max_length=100)

    def validate_document_ids(self, value):
        if len(set(value)) != len(value):
            raise serializers.ValidationError('主需求资料不能重复选择。')
        return value

    def validate_section_ids(self, value):
        if len(set(value)) != len(value):
            raise serializers.ValidationError('章节不能重复选择。')
        return value

    def validate_supplemental_document_ids(self, value):
        if len(set(value)) != len(value):
            raise serializers.ValidationError('补充资料不能重复选择。')
        return value


class StrictStepSerializer(StrictSerializer):
    action = _char(max_length=4000)
    expected = _char(max_length=4000)


def _steps_field():
    return serializers.ListField(
        child=StrictStepSerializer(), required=False, allow_empty=False,
        error_messages={'not_a_list': '步骤必须是列表。', 'empty': '至少提供一个步骤。'},
    )


class DraftSelectionSerializer(StrictSerializer):
    draft_id = _char(max_length=100)
    title = _char(required=False, allow_blank=False, max_length=255)
    module = _char(required=False, allow_blank=True, max_length=100)
    test_type = _char(required=False, allow_blank=False, max_length=50)
    preconditions = serializers.ListField(child=_char(), required=False)
    test_data = _char(required=False, allow_blank=True)
    steps = _steps_field()
    pending_questions = serializers.ListField(child=_char(), required=False)
    review_status = serializers.ChoiceField(
        choices=('unreviewed', 'reviewed'), required=False,
        error_messages={'invalid_choice': '审核状态不支持。'},
    )


class CaseSaveRequestSerializer(StrictSerializer):
    cases = serializers.ListField(
        child=DraftSelectionSerializer(), allow_empty=False,
        error_messages={'required': '请选择要保存的草稿。', 'empty': '至少选择一条草稿。', 'not_a_list': '草稿必须是列表。'},
    )


class ManualCaseUpdateSerializer(StrictSerializer):
    # The response field and the optimistic-lock request field intentionally
    # share one name so the workspace can send back the revision it rendered.
    revision = _integer(min_value=1)
    title = _char(required=False, allow_blank=False, max_length=255)
    module = _char(required=False, allow_blank=True, max_length=100)
    test_type = _char(required=False, allow_blank=False, max_length=50)
    preconditions = serializers.ListField(child=_char(), required=False)
    test_data = _char(required=False, allow_blank=True)
    steps = _steps_field()
    pending_questions = serializers.ListField(child=_char(), required=False)
    review_status = serializers.ChoiceField(
        choices=('unreviewed', 'reviewed'), required=False,
        error_messages={'invalid_choice': '审核状态不支持。'},
    )

    def validate(self, attrs):
        if len(attrs) == 1:
            raise serializers.ValidationError({'请求体': '至少提供一个要修改的字段。'})
        return attrs


class ExportRequestSerializer(StrictSerializer):
    ids = serializers.ListField(
        child=_uuid(), allow_empty=False,
        error_messages={'required': '请选择要导出的用例。', 'empty': '至少选择一条用例。', 'not_a_list': '用例标识必须是列表。'},
    )


class ConversationCreateSerializer(StrictSerializer):
    title = _char(required=False, allow_blank=False, max_length=255, default='新的知识问答')


class MessageCreateSerializer(StrictSerializer):
    question = _char(max_length=4000)
    document_ids = serializers.ListField(child=_integer(min_value=1), required=False, allow_empty=False, error_messages={'not_a_list': '资料必须是列表。', 'empty': '至少选择一份资料。'})
    model_config_id = _integer(min_value=1)
    client_request_id = _char(max_length=100)

    def validate_document_ids(self, value):
        if len(set(value)) != len(value):
            raise serializers.ValidationError('资料不能重复选择。')
        return value


def _with_source_state(sources):
    """Add live availability/version state without trusting stored snapshots."""
    if not isinstance(sources, list):
        return []
    revision_ids = [item.get('revision_id') for item in sources if isinstance(item, Mapping) and item.get('revision_id')]
    revisions = DocumentRevision.objects.select_related('document').filter(pk__in=revision_ids)
    by_id = {str(item.id): item for item in revisions}
    rendered = []
    for item in sources:
        if not isinstance(item, Mapping):
            continue
        row = dict(item)
        revision = by_id.get(str(row.get('revision_id')))
        if revision is None:
            row.update({'active': False, 'version': None})
        else:
            document = revision.document
            row.update({
                'active': bool(document.is_active and not document.is_deleted and document.current_revision_id == revision.id),
                'version': {'id': str(revision.id), 'number': revision.number},
            })
        rendered.append(row)
    return rendered
