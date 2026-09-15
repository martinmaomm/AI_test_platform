"""DiscoverRunner contracts for browser-discovery workspace navigation."""
from __future__ import annotations

import uuid
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from rest_framework.test import APIRequestFactory, force_authenticate

from ai_core.models import LLMConfiguration
from projects.models import Project, ProjectMember

from .browser_discovery_views import BrowserDiscoveryCollectionView, BrowserDiscoveryDetailView
from .models import APISpecification, APIWorkspace, BrowserDiscoveryHandoff, BrowserDiscoveryTask


class BrowserDiscoveryNavigationTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.owner = user_model.objects.create_user(
            username='navigation-owner', email='navigation-owner@example.test', password='fixture-only',
        )
        self.other = user_model.objects.create_user(
            username='navigation-other', email='navigation-other@example.test', password='fixture-only',
        )
        self.outsider = user_model.objects.create_user(
            username='navigation-outsider', email='navigation-outsider@example.test', password='fixture-only',
        )
        self.project = Project.objects.create(
            name='探索导航隔离', project_type='api', owner=self.owner, created_by=self.owner,
        )
        self.other_project = Project.objects.create(
            name='其他项目', project_type='api', owner=self.other, created_by=self.other,
        )
        ProjectMember.objects.create(
            project=self.project, user=self.owner, role='editor', can_edit=True,
            can_delete=True, can_execute_tests=True, can_view_reports=True,
        )
        ProjectMember.objects.create(
            project=self.other_project, user=self.other, role='editor', can_edit=True,
            can_delete=True, can_execute_tests=True, can_view_reports=True,
        )
        ProjectMember.objects.create(
            project=self.project, user=self.other, role='editor', can_edit=True, can_execute_tests=True,
        )
        self.model = LLMConfiguration.objects.create(
            model_type='llm', provider='offline', model_name='navigation-offline',
            created_by=self.owner, is_active=True,
        )
        self.factory = APIRequestFactory()

    def task(self, *, project=None, owner=None, label='task'):
        return BrowserDiscoveryTask.objects.create(
            project=project or self.project, owner=owner or self.owner, model_id=self.model.id,
            task_id=str(uuid.uuid4()), target_url='https://app.example.test/', description=label,
            api_origin='https://api.example.test', allow_test_data_writes=True,
            status=BrowserDiscoveryTask.Status.COMPLETED,
        )

    def handoff(self, task, *, source_version, title, created_at=None, workspace_kwargs=None, spec_kwargs=None):
        spec_values = {
            'project': task.project, 'created_by': task.owner, 'spec_name': title,
            'spec_type': APISpecification.SpecType.BROWSER_CAPTURE,
            'status': APISpecification.TaskStatus.COMPLETED, 'source_task': task,
        }
        spec_values.update(spec_kwargs or {})
        spec = APISpecification.objects.create(**spec_values)
        workspace_values = {'project': task.project, 'owner': task.owner, 'spec': spec, 'title': title}
        workspace_values.update(workspace_kwargs or {})
        workspace = APIWorkspace.objects.create(**workspace_values)
        handoff = BrowserDiscoveryHandoff.objects.create(
            task=task, source_version=source_version, selection_hash=uuid.uuid4().hex,
            selected_record_ids=[], spec=spec, workspace=workspace,
        )
        if created_at:
            BrowserDiscoveryHandoff.objects.filter(pk=handoff.pk).update(created_at=created_at)
            handoff.refresh_from_db()
        return handoff, workspace, spec

    def request(self, user):
        request = self.factory.get('/')
        force_authenticate(request, user)
        return request

    def test_detail_returns_only_intact_root_browser_handoffs_in_history_order(self):
        task = self.task(label='主任务')
        timestamp = timezone.now() - timedelta(minutes=3)
        BrowserDiscoveryTask.objects.filter(pk=task.pk).update(started_at=timestamp, finished_at=timestamp)
        task.refresh_from_db()
        older, old_workspace, _ = self.handoff(
            task, source_version=7, title='历史工作区', created_at=timezone.now() - timedelta(minutes=2),
        )
        newer, _, _ = self.handoff(
            task, source_version=8, title='最新工作区', created_at=timezone.now() - timedelta(minutes=1),
        )
        deleted, deleted_workspace, _ = self.handoff(task, source_version=9, title='已删除工作区')
        deleted_workspace.delete()

        other_task = self.task(label='另一任务')
        foreign_spec = APISpecification.objects.create(
            project=self.project, created_by=self.owner, spec_name='另一任务来源',
            spec_type=APISpecification.SpecType.BROWSER_CAPTURE,
            status=APISpecification.TaskStatus.COMPLETED, source_task=other_task,
        )
        foreign_workspace = APIWorkspace.objects.create(
            project=self.project, owner=self.owner, spec=foreign_spec, title='另一任务工作区',
        )
        BrowserDiscoveryHandoff.objects.create(
            task=task, source_version=10, selection_hash=uuid.uuid4().hex, spec=foreign_spec, workspace=foreign_workspace,
        )
        BrowserDiscoveryHandoff.objects.create(
            task=task, source_version=11, selection_hash=uuid.uuid4().hex, spec=older.spec,
            workspace=APIWorkspace.objects.create(
                project=self.project, owner=self.other, spec=older.spec, title='其他用户工作区',
            ),
        )
        cross_project_spec = APISpecification.objects.create(
            project=self.other_project, created_by=self.other, spec_name='其他项目来源',
            spec_type=APISpecification.SpecType.BROWSER_CAPTURE,
            status=APISpecification.TaskStatus.COMPLETED, source_task=task,
        )
        BrowserDiscoveryHandoff.objects.create(
            task=task, source_version=12, selection_hash=uuid.uuid4().hex, spec=cross_project_spec,
            workspace=APIWorkspace.objects.create(
                project=self.other_project, owner=self.owner, spec=cross_project_spec, title='其他项目工作区',
            ),
        )
        document_spec = APISpecification.objects.create(
            project=self.project, created_by=self.owner, spec_name='文档来源',
            status=APISpecification.TaskStatus.COMPLETED, source_task=task,
        )
        BrowserDiscoveryHandoff.objects.create(
            task=task, source_version=13, selection_hash=uuid.uuid4().hex, spec=document_spec,
            workspace=APIWorkspace.objects.create(project=self.project, owner=self.owner, spec=document_spec, title='非浏览器工作区'),
        )
        BrowserDiscoveryHandoff.objects.create(
            task=task, source_version=14, selection_hash=uuid.uuid4().hex, spec=older.spec,
            workspace=APIWorkspace.objects.create(
                project=self.project, owner=self.owner, spec=older.spec, parent=old_workspace, title='子工作区',
            ),
        )

        with CaptureQueriesContext(connection) as queries:
            reply = BrowserDiscoveryDetailView.as_view()(self.request(self.owner), project_id=self.project.id, task_id=task.id)
        self.assertEqual(reply.status_code, 200, reply.data)
        self.assertEqual(reply.data['data']['started_at'], timestamp.isoformat())
        self.assertEqual(reply.data['data']['finished_at'], timestamp.isoformat())
        self.assertEqual(reply.data['data']['created_at'], task.created_at.isoformat())
        self.assertEqual(reply.data['data']['handoffs'], [
            {'workspace_id': newer.workspace_id, 'workspace_title': '最新工作区', 'created_at': newer.created_at.isoformat(), 'source_version': 8},
            {'workspace_id': older.workspace_id, 'workspace_title': '历史工作区', 'created_at': older.created_at.isoformat(), 'source_version': 7},
        ])
        self.assertIsNone(BrowserDiscoveryHandoff.objects.get(pk=deleted.pk).workspace_id)
        handoff_queries = [query for query in queries.captured_queries if 'api_browser_discovery_handoffs' in query['sql']]
        self.assertEqual(len(handoff_queries), 1, handoff_queries)

    def test_collection_uses_created_order_prefetches_once_and_keeps_owner_boundary(self):
        first = self.task(label='较早创建')
        second = self.task(label='较晚创建')
        shared_created_at = timezone.now() - timedelta(hours=1)
        BrowserDiscoveryTask.objects.filter(pk__in=[first.pk, second.pk]).update(created_at=shared_created_at)
        BrowserDiscoveryTask.objects.filter(pk=first.pk).update(updated_at=timezone.now())
        self.handoff(first, source_version=1, title='较早工作区')
        self.handoff(second, source_version=2, title='较晚工作区')

        with CaptureQueriesContext(connection) as queries:
            reply = BrowserDiscoveryCollectionView.as_view()(self.request(self.owner), project_id=self.project.id)
        self.assertEqual(reply.status_code, 200, reply.data)
        expected_ids = [str(item.id) for item in BrowserDiscoveryTask.objects.filter(
            project=self.project, owner=self.owner,
        ).order_by('-created_at', '-id')]
        self.assertEqual([item['id'] for item in reply.data['data']], expected_ids)
        self.assertEqual(
            len([query for query in queries.captured_queries if 'api_browser_discovery_handoffs' in query['sql']]), 1,
        )

        member_reply = BrowserDiscoveryCollectionView.as_view()(self.request(self.other), project_id=self.project.id)
        self.assertEqual(member_reply.status_code, 200, member_reply.data)
        self.assertEqual(member_reply.data['data'], [])
        denied_reply = BrowserDiscoveryCollectionView.as_view()(self.request(self.outsider), project_id=self.project.id)
        self.assertEqual(denied_reply.status_code, 403, denied_reply.data)
