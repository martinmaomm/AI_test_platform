import uuid
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db.models.deletion import RestrictedError
from django.test import TestCase, override_settings
from django.utils import timezone

from projects.models import Project, UploadedFile
from project_knowledge.models import DocumentRevision, KnowledgeDocument, KnowledgeTask, KnowledgeConversation, KnowledgeMessage
from project_knowledge.runtime import TaskContext, TaskConflict, TaskStopped, enqueue_task, execute_task, expire_task


class KnowledgeRuntimeTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username='knowledge-owner', email='knowledge-owner@example.test')
        self.project = Project.objects.create(name='知识库', project_type='web', owner=self.user, created_by=self.user)
        self.upload = UploadedFile.objects.create(
            project=self.project, uploaded_by=self.user, original_name='需求.md', file_size=20,
            file_hash='a' * 64, file_type='md', file=SimpleUploadedFile('requirements.md', '# 用户\n用户名称不能重复。'.encode()),
        )
        self.document = KnowledgeDocument.objects.create(project=self.project, created_by=self.user, name='需求')
        self.revision = DocumentRevision.objects.create(document=self.document, number=1, uploaded_file=self.upload)
        self.document.current_revision = self.revision
        self.document.save()

    def task(self, kind='prepare', **kwargs):
        values = {
            'project': self.project, 'created_by': self.user, 'kind': kind,
            'client_request_id': str(uuid.uuid4()), 'request_hash': 'a',
            'payload': {'document_id': self.document.pk, 'revision_id': str(self.revision.pk)},
            'snapshot': [{'document_id': self.document.pk, 'revision_id': str(self.revision.pk)}],
        }
        values.update(kwargs)
        return KnowledgeTask.objects.create(**values)

    def test_request_is_idempotent_and_conflicting_payload_is_rejected(self):
        one, created = enqueue_task(self.project, self.user, 'generate', {'goal': '生成'}, 'same')
        two, recreated = enqueue_task(self.project, self.user, 'generate', {'goal': '生成'}, 'same')
        self.assertTrue(created)
        self.assertFalse(recreated)
        self.assertEqual(one.id, two.id)
        with self.assertRaises(TaskConflict):
            enqueue_task(self.project, self.user, 'generate', {'goal': '不同内容'}, 'same')

    def test_source_asset_is_preserved_without_blocking_whole_project_deletion(self):
        with self.assertRaises(RestrictedError):
            self.upload.delete()
        project_id, revision_id = self.project.pk, self.revision.pk
        self.project.delete()
        self.assertFalse(Project.objects.filter(pk=project_id).exists())
        self.assertFalse(DocumentRevision.objects.filter(pk=revision_id).exists())

    def test_queue_failure_is_terminal_and_not_left_queued(self):
        with patch('project_knowledge.tasks.execute_knowledge_task.delay', side_effect=RuntimeError('offline')):
            with self.captureOnCommitCallbacks(execute=True):
                task, _ = enqueue_task(self.project, self.user, 'generate', {}, 'queue-down')
        task.refresh_from_db()
        self.assertEqual(task.status, 'failed')
        self.assertEqual(task.error_code, 'QUEUE_UNAVAILABLE')

    def test_parse_survives_index_failure_and_retry_keeps_chunk_identifiers(self):
        first = self.task()
        sections = [{'ordinal': 0, 'section_key': '用户', 'heading': '用户', 'content': '用户名称不能重复。', 'location': {'line': 2}}]
        with patch('project_knowledge.parsing.parse_document', return_value=sections), patch('project_knowledge.indexing.index_revision', side_effect=RuntimeError('索引暂时不可用')):
            result = execute_task(first.id)
        self.assertEqual(result['status'], 'failed')
        self.revision.refresh_from_db()
        self.assertEqual(self.revision.parse_status, 'ready')
        self.assertEqual(self.revision.index_status, 'failed')
        ids = list(self.revision.chunks.values_list('id', flat=True))
        second = self.task()
        with patch('project_knowledge.parsing.parse_document') as parser, patch('project_knowledge.indexing.index_revision'):
            self.assertEqual(execute_task(second.id)['status'], 'completed')
        parser.assert_not_called()
        self.assertEqual(list(self.revision.chunks.values_list('id', flat=True)), ids)

    def test_duplicate_worker_delivery_does_not_execute_again(self):
        task = self.task(status='running', started_at=timezone.now())
        with patch('project_knowledge.runtime._prepare_document') as prepare:
            self.assertEqual(execute_task(task.id)['status'], 'skipped')
        prepare.assert_not_called()

    def test_disabled_source_stops_task_before_parsing(self):
        task = self.task()
        self.document.is_active = False
        self.document.save()
        with patch('project_knowledge.runtime._prepare_document') as prepare:
            result = execute_task(task.id)
        prepare.assert_not_called()
        self.assertEqual(result['status'], 'failed')
        task.refresh_from_db()
        self.assertEqual(task.error_code, 'SOURCES_CHANGED')

    def test_cross_project_snapshot_is_rejected(self):
        other = Project.objects.create(name='其他项目', created_by=self.user)
        task = self.task(project=other, status='running', started_at=timezone.now())
        with self.assertRaises(TaskStopped) as error:
            TaskContext(task).check_active()
        self.assertEqual(error.exception.code, 'SOURCES_CHANGED')

    def test_revoked_membership_stops_task(self):
        another = get_user_model().objects.create_user(username='former', email='former@example.test')
        task = self.task(created_by=another, status='running', started_at=timezone.now())
        with self.assertRaises(TaskStopped) as error:
            TaskContext(task).check_active()
        self.assertEqual(error.exception.code, 'ACCESS_REVOKED')

    def test_late_checkpoint_cannot_overwrite_cancelled_task(self):
        task = self.task(status='running', started_at=timezone.now(), result={'cases': [{'title': '保留'}]})
        context = TaskContext(task)
        KnowledgeTask.objects.filter(pk=task.id).update(cancel_requested=True, status='cancelled')
        with self.assertRaises(TaskStopped):
            context.checkpoint('后来的结果', 100, result={})
        task.refresh_from_db()
        self.assertEqual(task.status, 'cancelled')
        self.assertEqual(task.result['cases'][0]['title'], '保留')

    def test_worker_loss_expires_with_saved_output(self):
        task = self.task(status='running', started_at=timezone.now() - timedelta(seconds=1600), total_timeout_seconds=1200, partial_output='保留的内容')
        expired = expire_task(task)
        self.assertEqual(expired.status, 'partial')
        self.assertEqual(expired.partial_output, '保留的内容')
        self.assertEqual(expired.error_code, 'TASK_TIMEOUT')

    def test_expired_document_work_is_retryable_without_losing_parsed_text(self):
        self.revision.parse_status = 'ready'
        self.revision.index_status = 'running'
        self.revision.save()
        task = self.task(status='running', started_at=timezone.now() - timedelta(seconds=1600), total_timeout_seconds=1200)
        self.assertEqual(expire_task(task).status, 'failed')
        self.revision.refresh_from_db()
        self.assertEqual(self.revision.parse_status, 'ready')
        self.assertEqual(self.revision.index_status, 'failed')
        self.assertIn('重试', self.revision.index_error)

    def test_budget_checked_even_when_no_new_model_output(self):
        task = self.task(status='running', started_at=timezone.now())
        context = TaskContext(task)
        context.deadline = 0
        with self.assertRaises(TaskStopped) as error:
            context.check_active()
        self.assertEqual(error.exception.code, 'TASK_TIMEOUT')

    def test_answer_is_persisted_with_sources_and_completed_once(self):
        conversation = KnowledgeConversation.objects.create(project=self.project, created_by=self.user)
        task = self.task(kind='answer', payload={'conversation_id': str(conversation.id)})
        answer = {'answer': '文档未规定密码长度。', 'result_type': 'insufficient', 'sources': [], 'warnings': []}
        with patch('project_knowledge.workflows.answer_question', return_value=answer):
            self.assertEqual(execute_task(task.id)['status'], 'completed')
            self.assertEqual(execute_task(task.id)['status'], 'skipped')
        self.assertEqual(KnowledgeMessage.objects.filter(task=task, role='assistant').count(), 1)
        self.assertEqual(KnowledgeMessage.objects.get(task=task).content, answer['answer'])

    def test_partial_generation_is_not_marked_complete(self):
        task = self.task(kind='generate')
        result = {'cases': [], 'coverage': {'total': 2, 'processed': 1, 'pending': ['section2']}}
        with patch('project_knowledge.workflows.generate_cases', return_value=result):
            self.assertEqual(execute_task(task.id)['status'], 'partial')
