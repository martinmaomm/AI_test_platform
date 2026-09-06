"""Project-owned documents and human-readable artifacts, independent of WebUI scripts."""
import uuid

from django.conf import settings
from django.db import models


class KnowledgeDocument(models.Model):
    project = models.ForeignKey('projects.Project', on_delete=models.CASCADE)
    knowledge_file = models.OneToOneField('projects.KnowledgeBaseFile', null=True, blank=True, on_delete=models.SET_NULL)
    name = models.CharField(max_length=255)
    document_type = models.CharField(max_length=30, default='requirement')
    module = models.CharField(max_length=100, default='未分类')
    is_active = models.BooleanField(default=True)
    is_deleted = models.BooleanField(default=False)
    current_revision = models.ForeignKey('DocumentRevision', null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at', '-id']


class DocumentRevision(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    document = models.ForeignKey(KnowledgeDocument, on_delete=models.CASCADE, related_name='revisions')
    number = models.PositiveIntegerField()
    # Keep standalone asset deletion from breaking citations, while allowing
    # the existing whole-project cascade to remove both asset and revision.
    uploaded_file = models.ForeignKey('projects.UploadedFile', on_delete=models.RESTRICT)
    content_hash = models.CharField(max_length=64, blank=True)
    parse_status = models.CharField(max_length=20, default='pending')
    index_status = models.CharField(max_length=20, default='pending')
    parse_error = models.TextField(blank=True)
    index_error = models.TextField(blank=True)
    character_count = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['document', 'number'], name='knowledge_document_revision_unique')]


class KnowledgeChunk(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    revision = models.ForeignKey(DocumentRevision, on_delete=models.CASCADE, related_name='chunks')
    ordinal = models.PositiveIntegerField()
    section_key = models.CharField(max_length=255)
    heading = models.CharField(max_length=500, blank=True)
    content = models.TextField()
    location = models.JSONField(default=dict)

    class Meta:
        ordering = ['ordinal', 'id']
        constraints = [models.UniqueConstraint(fields=['revision', 'ordinal'], name='knowledge_chunk_ordinal_unique')]


class KnowledgeTask(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey('projects.Project', on_delete=models.CASCADE)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    kind = models.CharField(max_length=30)
    client_request_id = models.CharField(max_length=100)
    request_hash = models.CharField(max_length=64)
    payload = models.JSONField(default=dict)
    snapshot = models.JSONField(default=list)
    model_info = models.JSONField(default=dict)
    status = models.CharField(max_length=20, default='queued')
    phase = models.CharField(max_length=100, default='等待任务启动')
    progress = models.PositiveSmallIntegerField(default=0)
    partial_output = models.TextField(blank=True)
    result = models.JSONField(default=dict)
    error_code = models.CharField(max_length=100, blank=True)
    error_message = models.TextField(blank=True)
    cancel_requested = models.BooleanField(default=False)
    celery_task_id = models.CharField(max_length=100, blank=True)
    total_timeout_seconds = models.PositiveIntegerField(default=1200)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        constraints = [models.UniqueConstraint(fields=['project', 'created_by', 'kind', 'client_request_id'], name='knowledge_task_request_unique')]


class ManualTestCase(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey('projects.Project', on_delete=models.CASCADE)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    source_task = models.ForeignKey(KnowledgeTask, on_delete=models.SET_NULL, null=True, blank=True)
    draft_id = models.CharField(max_length=100)
    title = models.CharField(max_length=255)
    module = models.CharField(max_length=100, default='未分类')
    test_type = models.CharField(max_length=50, default='正常')
    preconditions = models.JSONField(default=list)
    test_data = models.TextField(blank=True)
    steps = models.JSONField(default=list)
    sources = models.JSONField(default=list)
    test_point_ids = models.JSONField(default=list)
    pending_questions = models.JSONField(default=list)
    review_status = models.CharField(max_length=20, default='unreviewed')
    revision = models.PositiveIntegerField(default=1)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        constraints = [models.UniqueConstraint(fields=['source_task', 'draft_id'], name='knowledge_case_draft_unique')]


class KnowledgeConversation(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey('projects.Project', on_delete=models.CASCADE)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    title = models.CharField(max_length=255, default='新的知识问答')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']


class KnowledgeMessage(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    conversation = models.ForeignKey(KnowledgeConversation, on_delete=models.CASCADE, related_name='messages')
    task = models.ForeignKey(KnowledgeTask, on_delete=models.SET_NULL, null=True, blank=True)
    role = models.CharField(max_length=20)
    content = models.TextField(blank=True)
    result_type = models.CharField(max_length=30, blank=True)
    sources = models.JSONField(default=list)
    warnings = models.JSONField(default=list)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['created_at', 'id']
