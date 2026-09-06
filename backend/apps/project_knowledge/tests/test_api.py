"""Offline API contracts; enqueueing is always mocked in this module."""

import hashlib
from io import BytesIO
import uuid
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from rest_framework.test import APIRequestFactory, force_authenticate

from ai_core.models import LLMConfiguration, ModelType
from projects.knowledge.models import KnowledgeBaseFile
from projects.models import Project, ProjectMember, UploadedFile
from project_knowledge.models import (DocumentRevision, KnowledgeChunk,
                                      KnowledgeConversation, KnowledgeDocument, KnowledgeMessage,
                                      KnowledgeTask, ManualTestCase)
from project_knowledge.serializers import (DocumentCreateSerializer,
                                           DocumentUpdateSerializer,
                                           DraftSelectionSerializer,
                                           KnowledgeChunkSerializer,
                                           ManualTestCaseSerializer,
                                           MAX_KNOWLEDGE_FILE_SIZE)
from project_knowledge.views import (CaseGenerationView, MessageListView, ConversationListView, ConversationDetailView,
                                     DocumentSectionsView, KnowledgeOptionsView,
                                     SourceDetailView, TaskCancelView,
                                     CaseGenerationSaveView, DocumentListView,
                                     DocumentPrepareView, ManualCaseDetailView,
                                     ManualCaseExportView, TaskDetailView,
                                     CleanupTaskRetryView,
                                     _hash_and_rewind)


class ProjectKnowledgeApiTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username='knowledge-api-owner', email='knowledge-api-owner@example.test')
        self.viewer = get_user_model().objects.create_user(username='knowledge-api-viewer', email='knowledge-api-viewer@example.test')
        self.project = Project.objects.create(name='知识 API', project_type='web', owner=self.user, created_by=self.user)
        self.other_project = Project.objects.create(name='其他知识 API', project_type='web', owner=self.viewer, created_by=self.viewer)
        ProjectMember.objects.create(project=self.project, user=self.viewer, role='viewer', can_view_reports=True, can_edit=False)
        self.model = LLMConfiguration.objects.create(
            model_type=ModelType.LLM, provider='openai', provider_name='测试提供商',
            model_name='test-model', api_key='test-only', created_by=self.user, is_active=True,
        )
        self.document, self.revision, self.chunk = self._document_with_chunk(self.project, self.user, ready=True)
        self.factory = APIRequestFactory()

    def _document_with_chunk(self, project, user, *, ready, name='需求.md'):
        upload = UploadedFile.objects.create(
            project=project, uploaded_by=user, original_name=name,
            file=SimpleUploadedFile(name, '角色停用后禁止登录。'.encode()), file_size=30,
            file_hash=uuid.uuid4().hex, file_type='md', upload_status='uploaded',
        )
        document = KnowledgeDocument.objects.create(project=project, name='登录规则', created_by=user)
        revision = DocumentRevision.objects.create(
            document=document, number=1, uploaded_file=upload,
            parse_status='ready' if ready else 'pending', index_status='ready' if ready else 'pending',
        )
        document.current_revision = revision
        document.save(update_fields=['current_revision'])
        chunk = KnowledgeChunk.objects.create(
            revision=revision, ordinal=0, section_key='登录规则', heading='登录规则',
            content='角色停用后禁止登录。', location={'kind': 'text', 'character_start': 0, 'character_end': 10},
        )
        return document, revision, chunk

    def _request(self, method, path, data=None, user=None):
        request = getattr(self.factory, method.lower())(path, data or {}, format='json')
        force_authenticate(request, user=user or self.user)
        return request

    def _enqueue(self, project, user, kind, payload, client_request_id, model_info=None, snapshot=None):
        task = KnowledgeTask.objects.create(
            project=project, created_by=user, kind=kind, client_request_id=client_request_id,
            request_hash=hashlib.sha256(str(payload).encode()).hexdigest(), payload=payload,
            snapshot=snapshot or [], model_info=model_info or {},
        )
        return task, True

    def test_sections_serializer_and_source_preview_are_versioned_and_have_no_storage_url(self):
        serialized = KnowledgeChunkSerializer(self.chunk).data
        self.assertEqual(serialized['revision_id'], str(self.revision.id))
        self.assertEqual(serialized['file_name'], '需求.md')
        sections = DocumentSectionsView.as_view()(self._request('get', '/sections/'), project_id=self.project.id, document_id=self.document.id)
        self.assertEqual(sections.status_code, 200, sections.data)
        self.assertEqual(sections.data['data']['items'][0]['id'], str(self.chunk.id))
        self.assertEqual(sections.data['data']['items'][0]['revision_id'], str(self.revision.id))
        response = SourceDetailView.as_view()(self._request('get', '/source/'), project_id=self.project.id, chunk_id=self.chunk.id)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['data']['id'], str(self.chunk.id))
        self.assertEqual(response.data['data']['version']['number'], 1)
        self.assertTrue(response.data['data']['active'])
        self.assertNotIn('url', response.data['data'])
        self.assertNotIn('storage', response.data['data'])

    def test_options_exposes_provider_name_without_api_key_or_unsupported_model_name(self):
        response = KnowledgeOptionsView.as_view()(self._request('get', '/options/'), project_id=self.project.id)
        self.assertEqual(response.status_code, 200, response.data)
        model = response.data['data']['models'][0]
        self.assertEqual(model['provider'], '测试提供商')
        self.assertEqual(model['model_name'], 'test-model')
        self.assertNotIn('api_key', model)

    def test_source_from_another_project_is_hidden(self):
        _, _, chunk = self._document_with_chunk(self.other_project, self.viewer, ready=True, name='other.md')
        response = SourceDetailView.as_view()(self._request('get', '/source/'), project_id=self.project.id, chunk_id=chunk.id)
        self.assertEqual(response.status_code, 404)

    def test_generation_freezes_string_uuid_snapshot_and_never_calls_real_queue(self):
        payload = {
            'document_ids': [self.document.id], 'section_ids': [str(self.chunk.id)],
            'goal': '验证角色停用', 'model_config_id': self.model.id,
            'text_only': False, 'client_request_id': 'case-api-1',
        }
        with patch('project_knowledge.views.runtime.enqueue_task', side_effect=self._enqueue) as enqueue:
            response = CaseGenerationView.as_view()(self._request('post', '/case-generations/', payload), project_id=self.project.id)
        self.assertEqual(response.status_code, 202, response.data)
        arguments = enqueue.call_args.kwargs
        self.assertEqual(arguments['snapshot'], [{'document_id': self.document.id, 'revision_id': str(self.revision.id)}])
        self.assertEqual(response.data['data']['snapshot'][0]['revision_id'], str(self.revision.id))
        self.assertEqual(response.data['data']['payload']['section_ids'], [str(self.chunk.id)])
        self.assertEqual(response.data['data']['payload']['module'], '未分类')
        self.assertEqual(response.data['data']['payload']['model_config_id'], self.model.id)

    def test_unready_default_question_requires_explicit_exclusion_without_queueing(self):
        self._document_with_chunk(self.project, self.user, ready=False, name='未处理.md')
        conversation = KnowledgeConversation.objects.create(project=self.project, created_by=self.user)
        payload = {'question': '角色停用后是否还能登录？', 'model_config_id': self.model.id, 'client_request_id': 'answer-api-1'}
        with patch('project_knowledge.views.runtime.enqueue_task') as enqueue:
            response = MessageListView.as_view()(self._request('post', '/messages/', payload), project_id=self.project.id, conversation_id=conversation.id)
        self.assertEqual(response.status_code, 409, response.data)
        self.assertIn('明确排除', response.data['message'])
        self.assertTrue(response.data['data']['details']['documents'])
        enqueue.assert_not_called()

    def test_active_generation_rejects_a_new_request_but_allows_same_request_id(self):
        payload = {'document_ids': [self.document.id], 'goal': '检查规则', 'model_config_id': self.model.id,
                   'client_request_id': 'same-active-generation'}
        with patch('project_knowledge.tasks.execute_knowledge_task.delay'):
            response = CaseGenerationView.as_view()(self._request('post', '/', payload), project_id=self.project.id)
            repeated = CaseGenerationView.as_view()(self._request('post', '/', payload), project_id=self.project.id)
            different = CaseGenerationView.as_view()(self._request('post', '/', {**payload, 'client_request_id': 'new-active-generation'}), project_id=self.project.id)
        self.assertEqual(response.status_code, 202, response.data)
        self.assertEqual(repeated.data['data']['id'], response.data['data']['id'])
        self.assertEqual(different.status_code, 409)
        self.assertEqual(KnowledgeTask.objects.filter(kind='generate').count(), 1)

    def test_a_conversation_accepts_only_one_active_answer_without_losing_idempotency(self):
        conversation = KnowledgeConversation.objects.create(project=self.project, created_by=self.user)
        payload = {'question': '角色停用后可以登录吗？', 'model_config_id': self.model.id,
                   'client_request_id': 'same-active-answer'}
        with patch('project_knowledge.tasks.execute_knowledge_task.delay'):
            responses = [MessageListView.as_view()(self._request('post', '/', body), project_id=self.project.id, conversation_id=conversation.id)
                         for body in [payload, payload, {**payload, 'client_request_id': 'new-answer'}]]
        self.assertEqual([response.status_code for response in responses], [202, 202, 409])
        self.assertEqual(conversation.messages.filter(role='user').count(), 1)
        self.assertEqual(conversation.messages.filter(role='assistant').count(), 1)

    def test_creating_another_conversation_preserves_messages_and_identifiable_history(self):
        original = KnowledgeConversation.objects.create(project=self.project, created_by=self.user)
        question = '角色停用后可以登录吗？'
        payload = {'question': question, 'model_config_id': self.model.id, 'client_request_id': 'preserve-history'}
        with patch('project_knowledge.tasks.execute_knowledge_task.delay'):
            accepted = MessageListView.as_view()(self._request('post', '/', payload), project_id=self.project.id, conversation_id=original.id)
        self.assertEqual(accepted.status_code, 202, accepted.data)
        before = list(original.messages.values_list('id', 'content'))
        created = ConversationListView.as_view()(self._request('post', '/', {'title': '新建知识问答'}), project_id=self.project.id)
        self.assertEqual(created.status_code, 201, created.data)
        self.assertEqual(created.data['data']['first_question'], '')
        self.assertEqual(list(original.messages.values_list('id', 'content')), before)
        history = ConversationListView.as_view()(self._request('get', '/'), project_id=self.project.id)
        rows = {row['id']: row for row in history.data['data']['items']}
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[str(original.id)]['first_question'], question)
        self.assertEqual(rows[created.data['data']['id']]['first_question'], '')
        restored = MessageListView.as_view()(self._request('get', '/'), project_id=self.project.id, conversation_id=original.id)
        self.assertEqual(restored.data['data']['items'][0]['content'], question)
        self.assertEqual(len(restored.data['data']['items']), 2)

    def test_conversation_previews_do_not_expose_another_users_questions(self):
        private = KnowledgeConversation.objects.create(project=self.project, created_by=self.viewer)
        KnowledgeMessage.objects.create(conversation=private, role='user', content='private question')
        own = KnowledgeConversation.objects.create(project=self.project, created_by=self.user)
        KnowledgeMessage.objects.create(conversation=own, role='assistant', content='not the question')
        KnowledgeMessage.objects.create(conversation=own, role='user', content='first question')
        KnowledgeMessage.objects.create(conversation=own, role='user', content='second question')
        result = ConversationListView.as_view()(self._request('get', '/'), project_id=self.project.id)
        self.assertEqual([row['id'] for row in result.data['data']['items']], [str(own.id)])
        self.assertEqual(result.data['data']['items'][0]['first_question'], 'first question')

    def test_delete_conversation_removes_only_its_messages_and_answer_tasks(self):
        conversation = KnowledgeConversation.objects.create(project=self.project, created_by=self.user)
        other = KnowledgeConversation.objects.create(project=self.project, created_by=self.user)
        task, _ = self._enqueue(self.project, self.user, 'answer', {'conversation_id': str(conversation.pk)}, 'delete-own')
        task.status = 'completed'
        task.save(update_fields=['status'])
        KnowledgeMessage.objects.create(conversation=conversation, task=task, role='user', content='question')
        KnowledgeMessage.objects.create(conversation=conversation, task=task, role='assistant', content='answer')
        other_message = KnowledgeMessage.objects.create(conversation=other, role='user', content='keep')
        unrelated_task, _ = self._enqueue(self.project, self.user, 'answer', {'conversation_id': str(other.pk)}, 'keep-answer')
        generation, _ = self._enqueue(self.project, self.user, 'generate', {}, 'keep-generation')
        manual_case = ManualTestCase.objects.create(project=self.project, created_by=self.user, source_task=generation, draft_id='keep', title='保留手工用例')
        result = ConversationDetailView.as_view()(self._request('delete', '/'), project_id=self.project.id, conversation_id=conversation.pk)
        self.assertEqual(result.status_code, 200, result.data)
        self.assertFalse(KnowledgeConversation.objects.filter(pk=conversation.pk).exists())
        self.assertFalse(KnowledgeMessage.objects.filter(conversation_id=conversation.pk).exists())
        self.assertFalse(KnowledgeTask.objects.filter(pk=task.pk).exists())
        self.assertTrue(KnowledgeConversation.objects.filter(pk=other.pk).exists())
        self.assertTrue(KnowledgeMessage.objects.filter(pk=other_message.pk).exists())
        self.assertTrue(KnowledgeTask.objects.filter(pk=unrelated_task.pk).exists())
        self.assertTrue(KnowledgeTask.objects.filter(pk=generation.pk).exists())
        self.assertTrue(ManualTestCase.objects.filter(pk=manual_case.pk).exists())
        self.assertTrue(KnowledgeDocument.objects.filter(pk=self.document.pk).exists())
        self.assertTrue(KnowledgeChunk.objects.filter(pk=self.chunk.pk).exists())

    def test_delete_conversation_rejects_active_tasks_even_when_cancel_requested(self):
        for state, cancel_requested in [('queued', False), ('running', False), ('running', True)]:
            with self.subTest(state=state, cancel_requested=cancel_requested):
                conversation = KnowledgeConversation.objects.create(project=self.project, created_by=self.user)
                task, _ = self._enqueue(self.project, self.user, 'answer', {'conversation_id': str(conversation.pk)}, str(uuid.uuid4()))
                task.status, task.cancel_requested = state, cancel_requested
                task.save(update_fields=['status', 'cancel_requested'])
                result = ConversationDetailView.as_view()(self._request('delete', '/'), project_id=self.project.id, conversation_id=conversation.pk)
                self.assertEqual(result.status_code, 409, result.data)
                self.assertTrue(KnowledgeConversation.objects.filter(pk=conversation.pk).exists())
                self.assertTrue(KnowledgeTask.objects.filter(pk=task.pk).exists())

    def test_delete_accepts_all_terminal_answer_states(self):
        for state in ('completed', 'partial', 'failed', 'cancelled'):
            with self.subTest(state=state):
                conversation = KnowledgeConversation.objects.create(project=self.project, created_by=self.user)
                task, _ = self._enqueue(self.project, self.user, 'answer', {'conversation_id': str(conversation.pk)}, str(uuid.uuid4()))
                task.status = state
                task.save(update_fields=['status'])
                result = ConversationDetailView.as_view()(self._request('delete', '/'), project_id=self.project.id, conversation_id=conversation.pk)
                self.assertEqual(result.status_code, 200, result.data)
                self.assertFalse(KnowledgeTask.objects.filter(pk=task.pk).exists())

    def test_only_conversation_owner_can_delete_even_for_project_owner(self):
        private = KnowledgeConversation.objects.create(project=self.project, created_by=self.viewer)
        result = ConversationDetailView.as_view()(self._request('delete', '/'), project_id=self.project.id, conversation_id=private.pk)
        self.assertEqual(result.status_code, 404)
        self.assertTrue(KnowledgeConversation.objects.filter(pk=private.pk).exists())
        # Managing a personal conversation is independent of editing shared docs.
        result = ConversationDetailView.as_view()(self._request('delete', '/', user=self.viewer), project_id=self.project.id, conversation_id=private.pk)
        self.assertEqual(result.status_code, 200, result.data)

    def test_delete_hides_cross_project_and_nonmember_conversations(self):
        private = KnowledgeConversation.objects.create(project=self.other_project, created_by=self.viewer)
        for project in (self.project, self.other_project):
            result = ConversationDetailView.as_view()(self._request('delete', '/'), project_id=project.id, conversation_id=private.pk)
            self.assertEqual(result.status_code, 404)
            self.assertTrue(KnowledgeConversation.objects.filter(pk=private.pk).exists())

    def test_delete_missing_or_already_deleted_conversation_returns_404(self):
        conversation = KnowledgeConversation.objects.create(project=self.project, created_by=self.user)
        target = conversation.pk
        for expected in (200, 404):
            result = ConversationDetailView.as_view()(self._request('delete', '/'), project_id=self.project.id, conversation_id=target)
            self.assertEqual(result.status_code, expected, result.data)
        result = ConversationDetailView.as_view()(self._request('delete', '/'), project_id=self.project.id, conversation_id=uuid.uuid4())
        self.assertEqual(result.status_code, 404)

    def test_unauthenticated_user_cannot_delete_a_conversation(self):
        conversation = KnowledgeConversation.objects.create(project=self.project, created_by=self.user)
        result = ConversationDetailView.as_view()(self.factory.delete('/'), project_id=self.project.id, conversation_id=conversation.pk)
        self.assertIn(result.status_code, (401, 403))
        self.assertTrue(KnowledgeConversation.objects.filter(pk=conversation.pk).exists())

    @override_settings(PROJECT_KNOWLEDGE_ENABLED=False)
    def test_disabled_feature_rejects_conversation_delete_without_removing_history(self):
        conversation = KnowledgeConversation.objects.create(project=self.project, created_by=self.user)
        result = ConversationDetailView.as_view()(self._request('delete', '/'), project_id=self.project.id, conversation_id=conversation.pk)
        self.assertEqual(result.status_code, 503)
        self.assertTrue(KnowledgeConversation.objects.filter(pk=conversation.pk).exists())

    def test_question_whose_conversation_was_deleted_before_lock_returns_404(self):
        conversation = KnowledgeConversation.objects.create(project=self.project, created_by=self.user)
        payload = {'question': '并行请求', 'model_config_id': self.model.id, 'client_request_id': 'deleted-before-lock'}
        with patch.object(KnowledgeConversation.objects, 'select_for_update') as locking, patch('project_knowledge.views.runtime.enqueue_task') as enqueue:
            locking.return_value.get.side_effect = KnowledgeConversation.DoesNotExist
            response = MessageListView.as_view()(self._request('post', '/', payload), project_id=self.project.id, conversation_id=conversation.pk)
        self.assertEqual(response.status_code, 404, response.data)
        enqueue.assert_not_called()

    def test_hashing_upload_rewinds_before_shared_service_reads_it(self):
        file = SimpleUploadedFile('rules.md', b'# rules\nread after hash')
        digest = _hash_and_rewind(file)
        self.assertEqual(digest, hashlib.sha256(b'# rules\nread after hash').hexdigest())
        self.assertEqual(file.read(), b'# rules\nread after hash')

    def test_document_file_validation_rejects_legacy_unsupported_and_oversize_before_upload(self):
        legacy = DocumentCreateSerializer(data={'file': SimpleUploadedFile('legacy.doc', b'old')})
        self.assertFalse(legacy.is_valid())
        self.assertIn('转换为 DOCX/XLSX', str(legacy.errors))
        legacy_xls = DocumentUpdateSerializer(data={'file': SimpleUploadedFile('legacy.xls', b'old')})
        self.assertFalse(legacy_xls.is_valid())
        self.assertIn('转换为 DOCX/XLSX', str(legacy_xls.errors))
        too_large_file = SimpleUploadedFile('large.md', b'x')
        too_large_file.size = MAX_KNOWLEDGE_FILE_SIZE + 1
        too_large = DocumentCreateSerializer(data={'file': too_large_file})
        self.assertFalse(too_large.is_valid())
        self.assertIn('50MiB', str(too_large.errors))

    def test_steps_require_nonempty_action_and_expected_with_no_extra_keys(self):
        for steps in ([], [{'action': '', 'expected': '结果'}], [{'action': '操作'}], [{'action': '操作', 'expected': '结果', 'extra': 'x'}]):
            with self.subTest(steps=steps):
                serializer = DraftSelectionSerializer(data={'draft_id': 'draft-step', 'steps': steps})
                self.assertFalse(serializer.is_valid())

    def test_prepare_defaults_stage_accepts_uuid_and_reuses_running_revision_task(self):
        request_id = uuid.uuid4()
        with patch('project_knowledge.views.runtime.enqueue_task', side_effect=self._enqueue) as enqueue:
            response = DocumentPrepareView.as_view()(self._request('post', '/prepare/', {
                'client_request_id': str(request_id),
            }), project_id=self.project.id, document_id=self.document.id)
        self.assertEqual(response.status_code, 202, response.data)
        self.assertEqual(response.data['data']['payload']['stage'], 'all')
        self.assertEqual(enqueue.call_args.args[4], str(request_id))
        with patch('project_knowledge.views.runtime.enqueue_task') as duplicate_enqueue:
            duplicate = DocumentPrepareView.as_view()(self._request('post', '/prepare/', {
                'client_request_id': str(uuid.uuid4()),
            }), project_id=self.project.id, document_id=self.document.id)
        self.assertEqual(duplicate.status_code, 202, duplicate.data)
        self.assertEqual(duplicate.data['data']['id'], response.data['data']['id'])
        duplicate_enqueue.assert_not_called()

    @override_settings(PROJECT_KNOWLEDGE_ENABLED=False)
    def test_read_only_member_can_cancel_own_answer_while_feature_is_disabled(self):
        task = KnowledgeTask.objects.create(
            project=self.project, created_by=self.viewer, kind='answer', client_request_id='viewer-answer',
            request_hash='viewer-answer', payload={}, snapshot=[], status='running',
        )
        response = TaskCancelView.as_view()(self._request('post', '/cancel/', user=self.viewer), project_id=self.project.id, task_id=task.id)
        self.assertEqual(response.status_code, 200, response.data)
        task.refresh_from_db()
        self.assertTrue(task.cancel_requested)
        self.assertEqual(task.status, 'cancelled')

    def test_case_save_validates_entire_batch_before_persisting_any_case(self):
        task = KnowledgeTask.objects.create(
            project=self.project, created_by=self.user, kind='generate', client_request_id='save-batch',
            request_hash='save-batch', status='completed', snapshot=[],
            result={'cases': [
                {'id': 'draft-valid', 'title': '有效草稿', 'steps': [{'action': '操作', 'expected': '结果'}], 'sources': []},
                {'id': 'draft-broken', 'title': '', 'steps': []},
            ]},
        )
        response = CaseGenerationSaveView.as_view()(self._request('post', '/save/', {
            'cases': [{'draft_id': 'draft-valid'}, {'draft_id': 'draft-broken'}],
        }), project_id=self.project.id, task_id=task.id)
        self.assertEqual(response.status_code, 400, response.data)
        self.assertFalse(ManualTestCase.objects.filter(source_task=task).exists())

    def test_manual_case_update_uses_rendered_revision_as_optimistic_lock_value(self):
        case = ManualTestCase.objects.create(project=self.project, created_by=self.user, draft_id='manual-revision', title='修改前')
        response = ManualCaseDetailView.as_view()(self._request('patch', '/manual-case/', {
            'revision': case.revision, 'title': '修改后',
        }), project_id=self.project.id, case_id=case.id)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['data']['revision'], 2)
        case.refresh_from_db()
        self.assertEqual(case.title, '修改后')
        stale = ManualCaseDetailView.as_view()(self._request('patch', '/manual-case/', {
            'revision': 1, 'title': '过期修改',
        }), project_id=self.project.id, case_id=case.id)
        self.assertEqual(stale.status_code, 409, stale.data)
        invalid_steps = ManualCaseDetailView.as_view()(self._request('patch', '/manual-case/', {
            'revision': 2, 'steps': [{'action': '缺少预期'}],
        }), project_id=self.project.id, case_id=case.id)
        self.assertEqual(invalid_steps.status_code, 400, invalid_steps.data)

    def test_case_save_is_idempotent_and_never_overwrites_user_edit(self):
        task = KnowledgeTask.objects.create(
            project=self.project, created_by=self.user, kind='generate', client_request_id='save-idempotent',
            request_hash='save-idempotent', status='completed', snapshot=[],
            result={'cases': [{'id': 'draft-one', 'title': '模型标题', 'steps': [{'action': '操作', 'expected': '结果'}], 'sources': []}]},
        )
        first = CaseGenerationSaveView.as_view()(self._request('post', '/save/', {
            'cases': [{'draft_id': 'draft-one', 'title': '首次保存标题'}],
        }), project_id=self.project.id, task_id=task.id)
        self.assertEqual(first.status_code, 200, first.data)
        case = ManualTestCase.objects.get(source_task=task, draft_id='draft-one')
        case.title = '用户手工编辑标题'
        case.save(update_fields=['title'])
        second = CaseGenerationSaveView.as_view()(self._request('post', '/save/', {
            'cases': [{'draft_id': 'draft-one', 'title': '重复请求不应覆盖'}],
        }), project_id=self.project.id, task_id=task.id)
        self.assertEqual(second.status_code, 200, second.data)
        case.refresh_from_db()
        self.assertEqual(case.title, '用户手工编辑标题')
        refreshed_task = TaskDetailView.as_view()(self._request('get', '/task/'), project_id=self.project.id, task_id=task.id)
        self.assertEqual(refreshed_task.data['data']['result']['saved_case_ids'], ['draft-one'])

    def test_export_rejects_other_project_and_escapes_formula_cells(self):
        foreign = ManualTestCase.objects.create(project=self.other_project, created_by=self.viewer, draft_id='foreign', title='其他项目')
        isolated = ManualCaseExportView.as_view()(self._request('post', '/export/', {'ids': [str(foreign.id)]}), project_id=self.project.id)
        self.assertEqual(isolated.status_code, 400, isolated.data)
        case = ManualTestCase.objects.create(project=self.project, created_by=self.user, draft_id='formula', title='=SUM(A1:A2)',
                                             steps=[{'action': '操作', 'expected': '结果'}], pending_questions=['确认权限'])
        exported = ManualCaseExportView.as_view()(self._request('post', '/export/', {'ids': [str(case.id)]}), project_id=self.project.id)
        self.assertEqual(exported.status_code, 200)
        from openpyxl import load_workbook
        sheet = load_workbook(BytesIO(exported.content)).active
        self.assertEqual(sheet['A2'].value, '=SUM(A1:A2)')
        self.assertEqual(sheet['A2'].data_type, 's')
        self.assertEqual(sheet['F2'].value, '1. 操作')
        self.assertEqual(sheet['G2'].value, '1. 结果')
        self.assertEqual(sheet['J2'].value, '确认权限')
        self.assertEqual(sheet.freeze_panes, 'A2')

    def test_task_and_conversation_history_are_hidden_from_other_project_members(self):
        task = KnowledgeTask.objects.create(
            project=self.project, created_by=self.viewer, kind='answer', client_request_id='viewer-history',
            request_hash='viewer-history', payload={}, snapshot=[],
        )
        task_response = TaskDetailView.as_view()(self._request('get', '/task/'), project_id=self.project.id, task_id=task.id)
        self.assertEqual(task_response.status_code, 404, task_response.data)
        conversation = KnowledgeConversation.objects.create(project=self.project, created_by=self.viewer)
        conversation_response = MessageListView.as_view()(self._request('get', '/messages/'), project_id=self.project.id, conversation_id=conversation.id)
        self.assertEqual(conversation_response.status_code, 404, conversation_response.data)

    def test_failed_cleanup_retries_frozen_scope_without_replaying_other_kinds(self):
        revision_id = str(self.revision.id)
        cleanup = KnowledgeTask.objects.create(
            project=self.project, created_by=self.user, kind='cleanup', client_request_id='cleanup-failed',
            request_hash='cleanup-failed', status='failed', payload={'revision_ids': [revision_id]}, snapshot=[],
        )
        with patch('project_knowledge.views.runtime.enqueue_task', side_effect=self._enqueue) as enqueue:
            response = CleanupTaskRetryView.as_view()(self._request('post', '/retry-cleanup/', {}), project_id=self.project.id, task_id=cleanup.id)
        self.assertEqual(response.status_code, 202, response.data)
        self.assertEqual(response.data['data']['kind'], 'cleanup')
        self.assertEqual(response.data['data']['payload'], {'revision_ids': [revision_id]})
        self.assertEqual(response.data['data']['snapshot'], [])
        self.assertTrue(enqueue.call_args.args[4].startswith('cleanup-retry-'))
        self.assertEqual(enqueue.call_args.kwargs['snapshot'], [])

        blocked = KnowledgeTask.objects.create(
            project=self.project, created_by=self.user, kind='generate', client_request_id='generation-failed',
            request_hash='generation-failed', status='failed', payload={}, snapshot=[],
        )
        with patch('project_knowledge.views.runtime.enqueue_task') as enqueue:
            response = CleanupTaskRetryView.as_view()(self._request('post', '/retry-cleanup/', {}), project_id=self.project.id, task_id=blocked.id)
        self.assertEqual(response.status_code, 400, response.data)
        enqueue.assert_not_called()

    def test_cleanup_retry_rejects_untrusted_request_fields(self):
        cleanup = KnowledgeTask.objects.create(
            project=self.project, created_by=self.user, kind='cleanup', client_request_id='cleanup-strict',
            request_hash='cleanup-strict', status='failed', payload={'revision_ids': [str(self.revision.id)]}, snapshot=[],
        )
        with patch('project_knowledge.views.runtime.enqueue_task') as enqueue:
            response = CleanupTaskRetryView.as_view()(self._request('post', '/retry-cleanup/', {'revision_ids': []}), project_id=self.project.id, task_id=cleanup.id)
        self.assertEqual(response.status_code, 400, response.data)
        enqueue.assert_not_called()

    def test_soft_deleted_hash_collision_restores_document_as_a_new_revision(self):
        knowledge_file = KnowledgeBaseFile.objects.create(project=self.project, uploaded_file=self.revision.uploaded_file, uploaded_by=self.user)
        self.document.knowledge_file = knowledge_file
        self.document.is_active, self.document.is_deleted = False, True
        self.document.save(update_fields=['knowledge_file', 'is_active', 'is_deleted'])
        replacement = SimpleUploadedFile('需求.md', '角色停用后禁止登录。'.encode())
        request = self.factory.post('/documents/', {'file': replacement, 'document_type': 'requirement'}, format='multipart')
        force_authenticate(request, user=self.user)
        with patch('project_knowledge.views._upload', return_value=(self.revision.uploaded_file, knowledge_file, self.revision.uploaded_file.file_hash)):
            response = DocumentListView.as_view()(request, project_id=self.project.id)
        self.assertEqual(response.status_code, 201, response.data)
        self.document.refresh_from_db()
        self.assertFalse(self.document.is_deleted)
        self.assertTrue(self.document.is_active)
        self.assertEqual(self.document.current_revision.number, 2)
        self.assertIn('恢复', response.data['message'])

    def test_serialized_case_marks_deleted_source_inactive_without_erasing_snapshot(self):
        case = ManualTestCase.objects.create(
            project=self.project, created_by=self.user, draft_id='source-state', title='来源状态',
            sources=[{'id': str(self.chunk.id), 'revision_id': str(self.revision.id), 'document_id': self.document.id}],
        )
        self.document.is_active = False
        self.document.save(update_fields=['is_active'])
        rendered = ManualTestCaseSerializer(case).data['sources'][0]
        self.assertFalse(rendered['active'])
        self.assertEqual(rendered['version']['number'], 1)
