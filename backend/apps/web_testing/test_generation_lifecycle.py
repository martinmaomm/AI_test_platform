"""Offline ownership, heartbeat, stale-salvage, and explicit-resume coverage."""

from __future__ import annotations

from contextlib import AbstractContextManager
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.contrib.auth import get_user_model
from django.db import connection, models
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from rest_framework.test import APIRequestFactory, force_authenticate

from ai_core.models import LLMConfiguration, ModelType
from projects.models import Project, ProjectMember

from .generation_lifecycle import (
    DEFAULT_HEARTBEAT_INTERVAL_SECONDS,
    GenerationHeartbeat,
    claim_lifecycle_metadata,
    heartbeat_interval_seconds,
    lifecycle_for_generation,
    reconcile_stale_generation,
    stale_timeout_seconds,
    touch_generation_heartbeat,
)
from .generation_repository import (
    GenerationResolutionConflict,
    claim_generation_worker,
    claim_trace_generation_retry,
    fail_generation_run,
    finalize_generation_artifact,
    persist_generation_checkpoint,
    prepare_exploration_resume,
    prepare_trace_generation_retry,
)
from .models import WebUIScriptGeneration
from .serializers import WebUIScriptGenerationSerializer
from .views import (
    WebUIScriptGenerationCreateView,
    WebUIScriptGenerationDetailView,
    WebUIScriptGenerationResumeExplorationView,
    WebUIScriptGenerationResolveView,
)


SCRIPT = '''\
"""检查商品列表。"""
from playwright.async_api import expect

async def run(page, variables):
    await page.goto('https://web.example.test/items')
    await expect(page.locator('#items')).to_be_visible()
'''


def v5_snapshot(*, revision=2):
    return {
        'schema_version': 5,
        'events': [{'event_id': 'E2', 'action': 'navigate'}],
        'page_states': [{'state_id': 'P2'}],
        'locator_evidence': [{'selector': '#items'}],
        'tool_stats': {'total_tool_calls': 2},
        'artifact': {
            'revision': revision,
            'completion': 'partial',
            'completed_steps': ['打开商品列表'],
            'remaining_steps': ['检查商品详情'],
            'variables': [],
        },
    }


class _RecordingHeartbeat(AbstractContextManager):
    def __init__(self, calls, generation_id, revision, task_id, enter=None):
        self.calls = calls
        self.values = (str(generation_id), int(revision), str(task_id))
        self.enter = enter

    def __enter__(self):
        self.calls.append(('enter', *self.values))
        if self.enter:
            self.enter(*self.values)
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.calls.append(('exit', *self.values))
        return False


class GenerationLifecycleTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.owner = user_model.objects.create_user(
            username='lifecycle-owner', email='lifecycle-owner@example.test',
        )
        self.author = user_model.objects.create_user(
            username='lifecycle-author', email='lifecycle-author@example.test',
        )
        self.other_member = user_model.objects.create_user(
            username='lifecycle-other', email='lifecycle-other@example.test',
        )
        self.no_execute = user_model.objects.create_user(
            username='lifecycle-no-execute', email='lifecycle-no-execute@example.test',
        )
        self.project = Project.objects.create(
            name='Lifecycle project', project_type='web',
            owner=self.owner, created_by=self.owner,
        )
        for user, can_execute in (
            (self.author, True), (self.other_member, True), (self.no_execute, False),
        ):
            ProjectMember.objects.create(
                project=self.project, user=user, role='editor',
                can_edit=True, can_delete=False,
                can_execute_tests=can_execute, can_view_reports=True,
            )
        self.model = LLMConfiguration.objects.create(
            model_type=ModelType.LLM, provider='openai', provider_name='fixture',
            api_key='offline-key', base_url='https://llm.example.test',
            model_name='offline-model', is_active=True, created_by=self.owner,
        )
        self.factory = APIRequestFactory()

    def generation(self, *, user=None, status=None, task_id=None, **overrides):
        status = status or WebUIScriptGeneration.Status.NEEDS_REVIEW
        values = {
            'project': self.project,
            'user': user or self.author,
            'status': status,
            'current_stage': (
                WebUIScriptGeneration.Stage.EXPLORING
                if status == WebUIScriptGeneration.Status.EXPLORING
                else WebUIScriptGeneration.Stage.COMPLETED
            ),
            'description_safe': '打开 https://web.example.test/items 并检查商品。',
            'target_url': 'https://web.example.test/items',
            'scenario_spec': {'schema_version': 5, 'title': '检查商品'},
            'exploration_snapshot': v5_snapshot(),
            'script_draft': SCRIPT,
            'model_info': {'config_id': self.model.id},
            'celery_task_id': task_id,
        }
        values.update(overrides)
        return WebUIScriptGeneration.objects.create(**values)

    def stale_generation(self, *, user=None, malformed=False, task_id='worker-old'):
        old = timezone.now() - timedelta(seconds=300)
        generation = self.generation(
            user=user,
            status=WebUIScriptGeneration.Status.EXPLORING,
            task_id=task_id,
            workspace={},
        )
        workspace = claim_lifecycle_metadata(
            {}, generation_revision=generation.revision,
            task_id=task_id, now=old,
        )
        if malformed:
            workspace['_generation_lifecycle']['heartbeat_at'] = '2026-99-invalid'
            workspace['_generation_lifecycle']['claimed_at'] = 'also-invalid'
        generation.workspace = workspace
        generation.save(update_fields=['workspace'])
        if malformed:
            WebUIScriptGeneration.objects.filter(pk=generation.pk).update(updated_at=old)
            generation.refresh_from_db()
        return generation

    def request(self, view, user, generation, payload=None, method='post'):
        factory_method = getattr(self.factory, method)
        request = factory_method('/script-generations/lifecycle/', payload or {}, format='json')
        force_authenticate(request, user=user)
        return view.as_view()(
            request, project_id=self.project.id, generation_id=generation.pk,
        )

    def test_non_finite_configuration_falls_back_and_stale_is_at_least_three_intervals(self):
        with patch.dict('os.environ', {
            'WEBUI_GENERATION_HEARTBEAT_INTERVAL_SECONDS': 'inf',
            'WEBUI_GENERATION_STALE_TIMEOUT_SECONDS': 'nan',
        }):
            self.assertEqual(heartbeat_interval_seconds(), DEFAULT_HEARTBEAT_INTERVAL_SECONDS)
            self.assertEqual(stale_timeout_seconds(), 120.0)
        with patch.dict('os.environ', {
            'WEBUI_GENERATION_HEARTBEAT_INTERVAL_SECONDS': '20',
            'WEBUI_GENERATION_STALE_TIMEOUT_SECONDS': '30',
        }):
            self.assertEqual(stale_timeout_seconds(), 60.0)

    def test_transient_heartbeat_database_error_retries_next_interval(self):
        heartbeat = GenerationHeartbeat('fixture-id', 4, 'fixture-task')
        heartbeat._stop = SimpleNamespace(wait=Mock(side_effect=[False, False, True]))
        with patch(
            'web_testing.generation_lifecycle.touch_generation_heartbeat',
            side_effect=[RuntimeError('temporary database error'), False],
        ) as touch, patch(
            'web_testing.generation_lifecycle.close_old_connections',
        ):
            heartbeat._run()
        self.assertEqual(touch.call_count, 2)

    def test_unclaimed_record_stays_queued_and_is_never_replayed_or_interrupted(self):
        generation = self.generation(
            status=WebUIScriptGeneration.Status.CREATED,
            task_id=None,
            exploration_snapshot={},
            script_draft='',
            workspace={},
        )
        reconciled = reconcile_stale_generation(
            generation.pk, now=timezone.now() + timedelta(days=1), timeout_seconds=120,
        )
        self.assertEqual(reconciled.status, WebUIScriptGeneration.Status.CREATED)
        self.assertEqual(lifecycle_for_generation(reconciled)['state'], 'queued')

    def test_legacy_claim_metadata_without_heartbeat_is_not_guessed_stale(self):
        old = timezone.now() - timedelta(days=1)
        generation = self.generation(
            status=WebUIScriptGeneration.Status.EXPLORING,
            task_id='legacy-task',
            workspace={
                '_agent_run': {'generation_revision': 0, 'task_id': 'legacy-task'},
                '_generation_lifecycle': {
                    'revision': 0, 'task_id': 'legacy-task',
                    'claimed_at': old.isoformat(),
                },
            },
        )
        reconciled = reconcile_stale_generation(
            generation.pk, now=timezone.now(), timeout_seconds=120,
        )
        self.assertEqual(reconciled.status, WebUIScriptGeneration.Status.EXPLORING)

    def test_heartbeat_keeps_slow_work_alive_then_stale_salvage_preserves_artifacts(self):
        generation = self.stale_generation()
        pulse_at = timezone.now()
        self.assertTrue(touch_generation_heartbeat(
            generation.pk,
            generation_revision=generation.revision,
            task_id='worker-old',
            now=pulse_at,
        ))
        alive = reconcile_stale_generation(
            generation.pk, now=pulse_at + timedelta(seconds=119), timeout_seconds=120,
        )
        self.assertEqual(alive.status, WebUIScriptGeneration.Status.EXPLORING)
        interrupted = reconcile_stale_generation(
            generation.pk, now=pulse_at + timedelta(seconds=120), timeout_seconds=120,
        )
        self.assertEqual(interrupted.status, WebUIScriptGeneration.Status.NEEDS_REVIEW)
        self.assertEqual(interrupted.current_stage, WebUIScriptGeneration.Stage.EXPLORING)
        self.assertEqual(interrupted.error_code, 'GENERATION_INTERRUPTED')
        self.assertEqual(interrupted.script_draft, SCRIPT)
        self.assertEqual(interrupted.exploration_snapshot['events'], v5_snapshot()['events'])
        self.assertEqual(
            interrupted.exploration_snapshot['actual_failure_stage'],
            WebUIScriptGeneration.Stage.EXPLORING,
        )
        lifecycle = lifecycle_for_generation(interrupted)
        self.assertEqual(lifecycle['state'], 'interrupted')
        self.assertTrue(lifecycle['can_resume'])
        self.assertIsNotNone(lifecycle['interrupted_at'])

    def test_high_frequency_ownership_check_projects_only_agent_run_metadata(self):
        from .generation_lifecycle import is_generation_run_active

        generation = self.stale_generation(task_id='poll-task')
        workspace = dict(generation.workspace)
        workspace['artifact_history'] = [{'script_draft': SCRIPT * 100}]
        generation.workspace = workspace
        generation.save(update_fields=['workspace'])

        with CaptureQueriesContext(connection) as captured:
            active = is_generation_run_active(
                generation.pk,
                generation_revision=generation.revision,
                task_id='poll-task',
            )

        self.assertTrue(active)
        self.assertEqual(len(captured), 1)
        select_sql = captured.captured_queries[0]['sql'].split(' FROM ', 1)[0]
        self.assertIn('AS "run_metadata"', select_sql)
        self.assertNotIn(', "web_testing_webuiscriptgeneration"."workspace"', select_sql)

    def test_malformed_timestamps_use_updated_at_grace_and_never_raise(self):
        generation = self.stale_generation(malformed=True)
        reconciled = reconcile_stale_generation(
            generation.pk, now=timezone.now(), timeout_seconds=120,
        )
        self.assertEqual(reconciled.error_code, 'GENERATION_INTERRUPTED')

        fresh = self.stale_generation(malformed=True, task_id='worker-fresh')
        WebUIScriptGeneration.objects.filter(pk=fresh.pk).update(updated_at=timezone.now())
        fresh.refresh_from_db()
        reconciled = reconcile_stale_generation(
            fresh.pk, now=timezone.now(), timeout_seconds=120,
        )
        self.assertEqual(reconciled.status, WebUIScriptGeneration.Status.EXPLORING)

    def test_interruption_fences_late_checkpoint_final_and_failure_after_resume(self):
        generation = reconcile_stale_generation(
            self.stale_generation().pk,
            now=timezone.now(), timeout_seconds=120,
        )
        self.assertFalse(persist_generation_checkpoint(
            generation.pk, generation_revision=0, task_id='worker-old',
            script_draft='late checkpoint', snapshot=v5_snapshot(revision=3),
        ))
        self.assertIsNone(finalize_generation_artifact(
            generation.pk, generation_revision=0, task_id='worker-old',
            target_status=WebUIScriptGeneration.Status.NEEDS_REVIEW,
            script_draft='late final', snapshot=v5_snapshot(revision=3),
            quality_report={}, variables=[], warnings=[],
            error_code='OLD_FAILURE', error_message='late',
        ))
        resumed = prepare_exploration_resume(
            generation.pk, expected_revision=0, recovery_notes='继续检查详情页。',
            user_id=self.author.id,
        )
        self.assertIsNotNone(claim_generation_worker(resumed.pk, 'worker-new'))
        self.assertIsNone(fail_generation_run(
            resumed.pk, generation_revision=0, task_id='worker-old',
            error_code='INTERNAL_GENERATION_ERROR', error_message='late failure',
        ))
        resumed.refresh_from_db()
        self.assertEqual(resumed.revision, 1)
        self.assertEqual(resumed.status, WebUIScriptGeneration.Status.PREFLIGHTING)
        self.assertEqual(resumed.script_draft, SCRIPT)

    def test_old_task_unexpected_exception_cannot_fail_new_claimed_resume(self):
        from .generation_orchestrator import fail_unexpected_generation

        interrupted = reconcile_stale_generation(
            self.stale_generation(task_id='worker-old').pk,
            now=timezone.now(), timeout_seconds=120,
        )
        resumed = prepare_exploration_resume(
            interrupted.pk, expected_revision=0,
            recovery_notes='确认现场后继续。', user_id=self.author.id,
        )
        claimed = claim_generation_worker(resumed.pk, 'worker-new')
        self.assertIsNotNone(claimed)

        result = fail_unexpected_generation(str(resumed.pk), 'worker-old')

        self.assertEqual(result['status'], 'stale')
        resumed.refresh_from_db()
        self.assertEqual(resumed.revision, 1)
        self.assertEqual(resumed.status, WebUIScriptGeneration.Status.PREFLIGHTING)
        self.assertEqual(resumed.celery_task_id, 'worker-new')
        self.assertEqual(resumed.error_code, '')

    def test_checkpoint_failure_finalization_cannot_replace_durable_script(self):
        from .generation_orchestrator import _persist_agent_result
        generation = self.stale_generation(task_id='checkpoint-owner')
        candidate = SCRIPT + '\n# not durable\n'
        incoming = v5_snapshot(revision=3)
        incoming['draft_state'] = {'latest_candidate': candidate}
        with patch('web_testing.generation_orchestrator.publish_terminal'):
            result = _persist_agent_result(
                generation, task_id='checkpoint-owner', script_draft=candidate,
                snapshot=incoming, completion='complete', error_code='CHECKPOINT_FAILED',
                error_message='保存检查点失败', final_message='',
            )
        generation.refresh_from_db()
        self.assertEqual(result['status'], 'needs_review')
        self.assertEqual(generation.script_draft, SCRIPT)
        self.assertEqual(generation.error_code, 'CHECKPOINT_FAILED')
        self.assertEqual(generation.exploration_snapshot['checkpoint_failure_candidate'], candidate)

    def test_code_only_retry_rejects_obsolete_delivery_before_new_task_attach(self):
        generation = self.generation(
            status=WebUIScriptGeneration.Status.FAILED,
            task_id='obsolete-code-task',
        )
        retried = prepare_trace_generation_retry(generation.pk, expected_revision=0)
        self.assertIsNone(claim_trace_generation_retry(
            retried.pk, 'obsolete-code-task',
        ))
        claimed = claim_trace_generation_retry(retried.pk, 'current-code-task')
        self.assertIsNotNone(claimed)
        self.assertIsNone(fail_generation_run(
            retried.pk, generation_revision=0, task_id='obsolete-code-task',
            error_code='INTERNAL_GENERATION_ERROR', error_message='late failure',
        ))
        retried.refresh_from_db()
        self.assertEqual(retried.status, WebUIScriptGeneration.Status.GENERATING)
        self.assertEqual(retried.celery_task_id, 'current-code-task')

    def test_code_only_retry_rejects_busy_debug_and_repair_states(self):
        for section, busy_status in (('verification', 'running'), ('repair', 'pending')):
            with self.subTest(section=section):
                generation = self.generation(status=WebUIScriptGeneration.Status.FAILED)
                generation.workspace = {section: {'status': busy_status}}
                generation.save(update_fields=['workspace'])

                with self.assertRaises(GenerationResolutionConflict):
                    prepare_trace_generation_retry(generation.pk, expected_revision=0)

                generation.refresh_from_db()
                self.assertEqual(generation.status, WebUIScriptGeneration.Status.FAILED)
                self.assertEqual(generation.revision, 0)

    def test_code_only_retry_accepts_ready_draft_only_after_failed_debug(self):
        for status in (WebUIScriptGeneration.Status.READY, WebUIScriptGeneration.Status.READY_WITH_WARNINGS):
            for verification in ('failed', 'passed', 'unverified'):
                with self.subTest(status=status, verification=verification):
                    generation = self.generation(status=status)
                    generation.workspace = {'verification': {'status': verification}}
                    generation.save(update_fields=['workspace'])
                    if verification == 'failed':
                        retried = prepare_trace_generation_retry(generation.pk, expected_revision=0)
                        self.assertEqual(retried.status, WebUIScriptGeneration.Status.GENERATING)
                    else:
                        with self.assertRaises(GenerationResolutionConflict):
                            prepare_trace_generation_retry(generation.pk, expected_revision=0)

    def test_ninth_code_only_invalidation_still_fences_first_obsolete_task(self):
        first_task = 'code-task-oldest'
        generation = self.generation(
            status=WebUIScriptGeneration.Status.FAILED,
            task_id=first_task,
        )
        for index in range(8):
            generation = prepare_trace_generation_retry(
                generation.pk, expected_revision=generation.revision,
            )
            task_id = f'code-task-{index}'
            claimed = claim_trace_generation_retry(generation.pk, task_id)
            self.assertIsNotNone(claimed)
            generation = fail_generation_run(
                generation.pk,
                generation_revision=generation.revision,
                task_id=task_id,
                error_code='FIXTURE_RETRY',
                error_message='继续整理',
            )
            self.assertIsNotNone(generation)

        generation = prepare_trace_generation_retry(
            generation.pk, expected_revision=generation.revision,
        )
        self.assertEqual(generation.revision, 9)
        self.assertIsNone(generation.celery_task_id)
        self.assertIsNone(claim_trace_generation_retry(generation.pk, first_task))

    def test_detail_checks_record_owner_before_stale_reconciliation(self):
        generation = self.stale_generation(user=self.author)
        denied = self.request(
            WebUIScriptGenerationDetailView, self.other_member,
            generation, method='get',
        )
        self.assertEqual(denied.status_code, 403)
        generation.refresh_from_db()
        self.assertEqual(generation.status, WebUIScriptGeneration.Status.EXPLORING)

        allowed = self.request(
            WebUIScriptGenerationDetailView, self.owner,
            generation, method='get',
        )
        self.assertEqual(allowed.status_code, 200, allowed.data)
        self.assertEqual(allowed.data['data']['lifecycle']['state'], 'interrupted')

    def test_history_selects_only_lifecycle_json_and_reconciles_current_page(self):
        from .generation_lifecycle import reconcile_stale_generations

        older = self.stale_generation(task_id='older-page-task')
        latest = self.stale_generation(task_id='latest-page-task')
        queryset = WebUIScriptGeneration.objects.filter(
            project=self.project, user=self.author,
        ).only(
            'id', 'status', 'created_at', 'updated_at', 'test_case_id',
            'scenario_spec', 'model_info',
        ).annotate(
            lifecycle_metadata=models.F('workspace___generation_lifecycle'),
        ).order_by('-created_at', '-id')

        with CaptureQueriesContext(connection) as captured:
            page = list(queryset[:1])
        self.assertEqual([item.pk for item in page], [latest.pk])
        self.assertIn('workspace', page[0].get_deferred_fields())
        select_sql = captured.captured_queries[-1]['sql'].split(' FROM ', 1)[0]
        self.assertIn('AS "lifecycle_metadata"', select_sql)
        self.assertNotIn(', "web_testing_webuiscriptgeneration"."workspace"', select_sql)

        reconciled = reconcile_stale_generations(page, now=timezone.now())
        self.assertEqual(reconciled[0].error_code, 'GENERATION_INTERRUPTED')
        older.refresh_from_db()
        self.assertEqual(older.status, WebUIScriptGeneration.Status.EXPLORING)

    def test_resume_requires_record_ownership_and_execute_capability(self):
        generation = self.generation(user=self.author)
        payload = {'expected_revision': 0, 'confirmed': True, 'recovery_notes': '继续检查详情。'}
        denied_other = self.request(
            WebUIScriptGenerationResumeExplorationView,
            self.other_member, generation, payload,
        )
        self.assertEqual(denied_other.status_code, 403)

        own_generation = self.generation(user=self.no_execute)
        denied_execute = self.request(
            WebUIScriptGenerationResumeExplorationView,
            self.no_execute, own_generation, payload,
        )
        self.assertEqual(denied_execute.status_code, 403)

    def test_create_and_paused_resolution_require_execute_capability(self):
        create_request = self.factory.post('/script-generations/', {
            'description': '打开 https://web.example.test/items 并检查商品。',
            'model_config_id': self.model.id,
        }, format='json')
        force_authenticate(create_request, user=self.no_execute)
        create_response = WebUIScriptGenerationCreateView.as_view()(
            create_request, project_id=self.project.id,
        )
        self.assertEqual(create_response.status_code, 403)

        generation = self.generation(
            user=self.no_execute,
            status=WebUIScriptGeneration.Status.NEEDS_CONFIRMATION,
            current_stage=WebUIScriptGeneration.Stage.PREFLIGHTING,
            error_code='INPUT_AMBIGUOUS',
        )
        resolve_request = self.factory.post('/script-generations/resolve/', {
            'expected_status': WebUIScriptGeneration.Status.NEEDS_CONFIRMATION,
            'expected_revision': generation.revision,
        }, format='json')
        force_authenticate(resolve_request, user=self.no_execute)
        resolve_response = WebUIScriptGenerationResolveView.as_view()(
            resolve_request, project_id=self.project.id, generation_id=generation.pk,
        )
        self.assertEqual(resolve_response.status_code, 403)

    def test_project_owner_can_resume_member_record_once_and_metadata_is_publicly_bounded(self):
        generation = self.generation(user=self.author)
        before_snapshot = generation.exploration_snapshot
        payload = {
            'expected_revision': 0,
            'confirmed': True,
            'recovery_notes': '  已确认登录仍有效，继续检查详情。  ',
        }
        with patch(
            'web_testing.views.generate_webui_script_generation_task.delay',
            return_value=SimpleNamespace(id='resume-task'),
        ) as delay:
            response = self.request(
                WebUIScriptGenerationResumeExplorationView,
                self.owner, generation, payload,
            )
            duplicate = self.request(
                WebUIScriptGenerationResumeExplorationView,
                self.owner, generation, payload,
            )
        self.assertEqual(response.status_code, 202, response.data)
        self.assertEqual(duplicate.status_code, 409, duplicate.data)
        delay.assert_called_once_with(str(generation.pk))
        generation.refresh_from_db()
        self.assertEqual(generation.revision, 1)
        self.assertEqual(generation.resume_count, 1)
        self.assertEqual(generation.status, WebUIScriptGeneration.Status.PREFLIGHTING)
        self.assertIsNone(generation.cancel_requested_at)
        self.assertEqual(generation.exploration_snapshot, before_snapshot)
        self.assertEqual(generation.script_draft, SCRIPT)
        self.assertEqual(generation.clarifications[-1]['recovery_notes'], '已确认登录仍有效，继续检查详情。')
        lifecycle = response.data['data']['lifecycle']
        self.assertEqual(set(lifecycle), {
            'state', 'heartbeat_at', 'last_checkpoint_at', 'interrupted_at', 'can_resume',
        })
        self.assertEqual(lifecycle['state'], 'queued')
        self.assertFalse(lifecycle['can_resume'])
        self.assertNotIn('_agent_run', response.data['data']['workspace'])

    def test_resume_dispatch_failure_preserves_history_and_artifacts(self):
        generation = self.generation(user=self.author)
        before_snapshot = generation.exploration_snapshot
        with patch(
            'web_testing.views.generate_webui_script_generation_task.delay',
            side_effect=RuntimeError('broker unavailable'),
        ):
            response = self.request(
                WebUIScriptGenerationResumeExplorationView,
                self.author,
                generation,
                {'expected_revision': 0, 'confirmed': True, 'recovery_notes': '保留现场后重试。'},
            )
        self.assertEqual(response.status_code, 503, response.data)
        generation.refresh_from_db()
        self.assertEqual(generation.status, WebUIScriptGeneration.Status.FAILED)
        self.assertEqual(generation.revision, 1)
        self.assertEqual(generation.resume_count, 1)
        self.assertEqual(generation.exploration_snapshot['events'], before_snapshot['events'])
        self.assertEqual(generation.script_draft, SCRIPT)
        self.assertEqual(generation.clarifications[-1]['recovery_notes'], '保留现场后重试。')

    def test_resume_count_is_capped_at_three(self):
        generation = self.generation(resume_count=3)
        lifecycle = lifecycle_for_generation(generation)
        self.assertFalse(lifecycle['can_resume'])
        response = self.request(
            WebUIScriptGenerationResumeExplorationView,
            self.author,
            generation,
            {'expected_revision': 0, 'confirmed': True, 'recovery_notes': '再次恢复。'},
        )
        self.assertEqual(response.status_code, 409)

    def test_serializer_exposes_exact_lifecycle_shape_for_detail_contract(self):
        generation = self.generation()
        payload = WebUIScriptGenerationSerializer(generation).data
        self.assertEqual(set(payload['lifecycle']), {
            'state', 'heartbeat_at', 'last_checkpoint_at', 'interrupted_at', 'can_resume',
        })
        self.assertEqual(payload['lifecycle']['state'], 'idle')


class GenerationOrchestratorLifecycleTests(TestCase):
    """The orchestrator tests replace heartbeat threads with deterministic contexts."""

    setUp = GenerationLifecycleTests.setUp
    generation = GenerationLifecycleTests.generation

    def fake_heartbeat_factory(self, calls, enter=None):
        return lambda generation_id, revision, task_id: _RecordingHeartbeat(
            calls, generation_id, revision, task_id, enter=enter,
        )

    def agent_patches(self, agent_type, heartbeat_factory):
        return (
            patch(
                'web_testing.generation_orchestrator.run_safety_preflight',
                return_value=SimpleNamespace(outcome='continue', warnings=[], mcp_config={}),
            ),
            patch(
                'web_testing.generation_orchestrator.get_llm_manager',
                return_value=SimpleNamespace(current_llm=object()),
            ),
            patch('web_testing.script_exploration_agent.ScriptExplorationAgent', agent_type),
            patch('web_testing.generation_orchestrator.generation_heartbeat', heartbeat_factory),
            patch('web_testing.generation_orchestrator.publish_stage_changed'),
            patch('web_testing.generation_orchestrator.publish_terminal'),
            patch(
                'web_testing.generation_orchestrator.evaluate_workspace_draft',
                return_value={'status': 'ready', 'completion': 'complete', 'blockers': [], 'warnings': []},
            ),
        )

    def run_with_patches(self, patches, callback):
        entered = []
        try:
            for item in patches:
                entered.append(item.__enter__())
            return callback()
        finally:
            for item in reversed(patches):
                item.__exit__(None, None, None)

    def test_fresh_and_code_only_dispatch_both_hold_independent_heartbeat_scope(self):
        from .generation_orchestrator import run_generation, run_generation_from_trace

        calls = []
        observed = []

        class FakeAgent:
            def __init__(self, **_kwargs):
                pass

            async def generate(self, **kwargs):
                observed.append(kwargs)
                return SimpleNamespace(
                    script_draft=SCRIPT,
                    snapshot={**v5_snapshot(revision=3), 'artifact': {
                        **v5_snapshot(revision=3)['artifact'],
                        'completion': 'complete', 'remaining_steps': [],
                    }},
                    completion='complete', error_code='', error_message='', final_message='完成',
                )

        fresh = self.generation(
            status=WebUIScriptGeneration.Status.CREATED,
            task_id=None, workspace={}, exploration_snapshot={}, script_draft='',
        )
        patches = self.agent_patches(FakeAgent, self.fake_heartbeat_factory(calls))
        result = self.run_with_patches(
            patches,
            lambda: run_generation(str(fresh.pk), celery_task_id='fresh-task'),
        )
        self.assertEqual(result['status'], WebUIScriptGeneration.Status.READY)
        self.assertIsNone(observed[0]['saved_snapshot'])
        self.assertEqual(observed[0]['script_draft'], '')

        retry = self.generation(status=WebUIScriptGeneration.Status.FAILED)
        retry = prepare_trace_generation_retry(retry.pk, expected_revision=0)
        patches = self.agent_patches(FakeAgent, self.fake_heartbeat_factory(calls))
        result = self.run_with_patches(
            patches,
            lambda: run_generation_from_trace(str(retry.pk), celery_task_id='code-task'),
        )
        self.assertEqual(result['status'], WebUIScriptGeneration.Status.READY)
        self.assertTrue(observed[1]['code_only'])
        self.assertEqual(observed[1]['saved_snapshot']['schema_version'], 5)
        self.assertEqual(observed[1]['script_draft'], SCRIPT)
        self.assertEqual([item[0] for item in calls], ['enter', 'exit', 'enter', 'exit'])

    def test_slow_agent_is_not_stale_when_independent_pulse_advances(self):
        from .generation_orchestrator import run_generation

        calls = []
        pulse_at = timezone.now() + timedelta(seconds=300)

        def pulse(generation_id, revision, task_id):
            self.assertTrue(touch_generation_heartbeat(
                generation_id,
                generation_revision=revision,
                task_id=task_id,
                now=pulse_at,
            ))
            current = reconcile_stale_generation(
                generation_id,
                now=pulse_at + timedelta(seconds=119),
                timeout_seconds=120,
            )
            self.assertEqual(current.status, WebUIScriptGeneration.Status.CREATED)

        class SlowFakeAgent:
            def __init__(self, **_kwargs):
                pass

            async def generate(inner_self, **_kwargs):
                return SimpleNamespace(
                    script_draft=SCRIPT,
                    snapshot={**v5_snapshot(), 'artifact': {
                        **v5_snapshot()['artifact'], 'completion': 'complete', 'remaining_steps': [],
                    }},
                    completion='complete', error_code='', error_message='', final_message='完成',
                )

        generation = self.generation(
            status=WebUIScriptGeneration.Status.CREATED,
            task_id=None, workspace={}, exploration_snapshot={}, script_draft='',
        )
        patches = self.agent_patches(
            SlowFakeAgent, self.fake_heartbeat_factory(calls, enter=pulse),
        )
        result = self.run_with_patches(
            patches,
            lambda: run_generation(str(generation.pk), celery_task_id='slow-task'),
        )
        self.assertEqual(result['status'], WebUIScriptGeneration.Status.READY)
        self.assertEqual([item[0] for item in calls], ['enter', 'exit'])

    def test_explicit_resume_passes_recovery_context_snapshot_and_script_to_fresh_agent(self):
        from .generation_orchestrator import run_generation

        calls = []
        observed = {}

        class ResumeAgent:
            def __init__(self, **_kwargs):
                pass

            async def generate(self, **kwargs):
                observed.update(kwargs)
                return SimpleNamespace(
                    script_draft=SCRIPT,
                    snapshot=kwargs['saved_snapshot'], completion='partial',
                    error_code='', error_message='', final_message='继续探索',
                )

        generation = self.generation(status=WebUIScriptGeneration.Status.NEEDS_REVIEW)
        generation.workspace = {
            '_generation_lifecycle': {
                'last_checkpoint_at': '2026-09-10T12:00:00+00:00',
            },
        }
        generation.save(update_fields=['workspace'])
        resumed = prepare_exploration_resume(
            generation.pk, expected_revision=0,
            recovery_notes='登录状态仍有效，完成详情断言。', user_id=self.author.id,
        )
        patches = self.agent_patches(ResumeAgent, self.fake_heartbeat_factory(calls))
        self.run_with_patches(
            patches,
            lambda: run_generation(str(resumed.pk), celery_task_id='resume-agent-task'),
        )
        self.assertFalse(observed['code_only'])
        self.assertEqual(observed['saved_snapshot']['schema_version'], 5)
        self.assertEqual(observed['script_draft'], SCRIPT)
        self.assertEqual(observed['brief']['recovery_context'], {
            'notes': '登录状态仍有效，完成详情断言。',
            'completed_steps': ['打开商品列表'],
            'remaining_steps': ['检查商品详情'],
            'previous_status': WebUIScriptGeneration.Status.NEEDS_REVIEW,
            'last_checkpoint_at': '2026-09-10T12:00:00+00:00',
        })
