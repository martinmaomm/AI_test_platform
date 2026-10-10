import json
import uuid
from copy import deepcopy
from datetime import timedelta
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from ai_core.models import LLMConfiguration
from api_testing.models import APISpecification, APIEndpoint, BrowserDiscoveryTask, BrowserDiscoveryRecord
from projects.models import Project, ProjectMember
from users.models import User

from security_testing.credentials import decrypt_identities, encrypt_identities
from security_testing.models import SecurityPlan, SecurityTask
from security_testing.planning import candidates, model_input, validate_output, PlanningOutputError
from security_testing.runtime import enqueue, execute_task, recover_expired, stop_task


def config():
    return {'allowed_origins': ['https://security.example.test'], 'allow_private_network': False,
            'endpoints': [{'name': '私有资源', 'url': 'https://security.example.test/items/1?search=private-query',
                           'method': 'GET', 'headers': {'Accept-Language': 'zh-CN'}, 'auth_required': True,
                           'owner_only_a': True, 'proof': {'path': 'body.id', 'expected': 'private-proof'}}],
            'checks': ['authentication', 'authorization', 'information', 'configuration'],
            'max_requests': 40, 'rate_per_second': 1, 'timeout_seconds': 10}


IDENTITIES = {'a': {'name': '账号甲', 'headers': {'Authorization': 'Bearer fixture-very-secret', 'Cookie': 'sid=cookie-secret'}}}


class SecurityFixture:
    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(username='security-editor', email='security@example.test')
        self.outsider = User.objects.create_user(username='security-other', email='other@example.test')
        self.project = Project.objects.create(name='安全项目', project_type='security', created_by=self.user)
        self.member = ProjectMember.objects.create(project=self.project, user=self.user, can_edit=True,
                                                  can_execute_tests=True, can_view_reports=True)
        self.plan = SecurityPlan.objects.create(project=self.project, created_by=self.user, name='接口检查',
                                               config=config(), identities_ciphertext=encrypt_identities(IDENTITIES),
                                               cases=candidates(config()))
        self.client.force_authenticate(self.user)

    def path(self, suffix='', project=None):
        return f'/api/v1/projects/{(project or self.project).pk}/security/{suffix}'

    def task(self, kind='execution', **kwargs):
        with patch('security_testing.tasks.run_security_task.apply_async'), self.captureOnCommitCallbacks(execute=True):
            return enqueue(self.plan.pk, self.user, kind, uuid.uuid4(),
                           case_ids=[self.plan.cases[0]['id']] if kind == 'execution' else None, **kwargs)[0]


class SecurityAPIRuntimeTests(SecurityFixture, TestCase):
    def test_plan_credentials_encrypted_and_replace_omit_clear(self):
        response = self.client.post(self.path('plans/'), {'name': '新计划', 'config': config(), 'identities': IDENTITIES}, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        plan = SecurityPlan.objects.get(pk=response.data['data']['id'])
        self.assertNotIn('fixture-very-secret', plan.identities_ciphertext)
        self.assertNotIn('cookie-secret', json.dumps(response.data))
        self.assertEqual(decrypt_identities(plan.identities_ciphertext), IDENTITIES)
        self.assertEqual(response.data['data']['identities'], {'a': {'name': '账号甲', 'configured': True}})
        path = self.path(f'plans/{plan.pk}/')
        self.client.patch(path, {'name': '改名'}, format='json')
        plan.refresh_from_db()
        self.assertEqual(decrypt_identities(plan.identities_ciphertext), IDENTITIES)
        replacement = {'b': {'name': '账号乙', 'headers': {'Authorization': 'Bearer second-secret'}}}
        self.client.patch(path, {'identities': replacement}, format='json')
        plan.refresh_from_db()
        self.assertEqual(decrypt_identities(plan.identities_ciphertext), replacement)
        self.client.patch(path, {'identities': {}}, format='json')
        plan.refresh_from_db()
        self.assertEqual(plan.identities_ciphertext, '')
        self.assertEqual(plan.revision, 4)

    def test_credential_tampering_and_key_rotation_fail_closed(self):
        value = encrypt_identities(IDENTITIES)
        with self.assertRaises(Exception):
            decrypt_identities(value[:-2] + 'aa')
        with override_settings(SECRET_KEY='unrelated-rotation-key'):
            with self.assertRaises(Exception):
                decrypt_identities(value)
        self.plan.identities_ciphertext = 'corrupt-encrypted-value'
        self.plan.save()
        task = self.task()
        with patch('security_testing.runner.execute') as runner:
            execute_task(task.pk)
        runner.assert_not_called()
        task.refresh_from_db()
        self.assertEqual(task.error_code, 'task_failed')
        self.assertEqual(task.identities_ciphertext, '')

    def test_cross_project_and_capabilities(self):
        self.client.force_authenticate(self.outsider)
        self.assertEqual(self.client.get(self.path('plans/')).status_code, 404)
        self.client.force_authenticate(self.user)
        self.member.can_edit = False
        self.member.can_execute_tests = False
        self.member.can_view_reports = False
        self.member.save()
        self.assertEqual(self.client.get(self.path('plans/')).status_code, 200)
        self.assertEqual(self.client.post(self.path('plans/'), {'name': '无权限', 'config': config()}, format='json').status_code, 403)
        self.assertEqual(self.client.get(self.path('tasks/')).status_code, 403)
        payload = {'request_id': str(uuid.uuid4())}
        self.assertEqual(self.client.post(self.path(f'plans/{self.plan.pk}/generate/'), payload, format='json').status_code, 403)
        other = Project.objects.create(name='另项目', project_type='security', created_by=self.user)
        ProjectMember.objects.create(project=other, user=self.user, can_edit=True, can_execute_tests=True, can_view_reports=True)
        self.assertEqual(self.client.get(self.path(f'plans/{self.plan.pk}/', project=other)).status_code, 404)
        api_project = Project.objects.create(name='接口项目', project_type='api', created_by=self.user)
        ProjectMember.objects.create(project=api_project, user=self.user, can_edit=True)
        self.assertEqual(self.client.get(self.path('plans/', project=api_project)).status_code, 404)

    def test_idempotent_submission_conflict_and_no_second_delivery(self):
        payload = {'request_id': str(uuid.uuid4()), 'case_ids': [self.plan.cases[0]['id']]}
        url = self.path(f'plans/{self.plan.pk}/runs/')
        with patch('security_testing.tasks.run_security_task.apply_async') as dispatch, self.captureOnCommitCallbacks(execute=True):
            first = self.client.post(url, payload, format='json')
            second = self.client.post(url, payload, format='json')
        self.assertEqual(first.status_code, 202, first.data)
        self.assertEqual(second.status_code, 200, second.data)
        self.assertEqual(first.data['data']['id'], second.data['data']['id'])
        dispatch.assert_called_once()
        self.assertEqual(dispatch.call_args.kwargs['expires'], 300)
        changed = {**payload, 'case_ids': [self.plan.cases[1]['id']]}
        self.assertEqual(self.client.post(url, changed, format='json').status_code, 409)
        with patch('security_testing.runner.execute', return_value={'items': [], 'summary': {}, 'limitations': []}) as runner:
            execute_task(first.data['data']['id'])
            execute_task(first.data['data']['id'])
        runner.assert_called_once()

    def test_immutable_snapshot_and_detail_projection(self):
        task = self.task()
        self.client.patch(self.path(f'plans/{self.plan.pk}/'), {'name': '后来的名字', 'config': {**config(), 'max_requests': 2}}, format='json')
        self.plan.refresh_from_db()
        self.assertEqual(self.plan.cases, [])
        task.refresh_from_db()
        self.assertEqual(task.snapshot['config']['max_requests'], 40)
        self.assertEqual(task.snapshot['plan_name'], '接口检查')
        detail = self.client.get(self.path(f'tasks/{task.pk}/')).data['data']
        encoded = json.dumps(detail)
        for secret in ('fixture-very-secret', 'cookie-secret', 'private-query', 'private-proof', 'zh-CN'):
            self.assertNotIn(secret, encoded)
        listed = self.client.get(self.path('tasks/')).data['data']['items'][0]
        self.assertNotIn('snapshot', listed)
        self.assertNotIn('result', listed)
        self.assertNotIn('identities_ciphertext', listed)
        self.assertEqual(self.client.delete(self.path(f'plans/{self.plan.pk}/')).status_code, 409)

    def test_rule_generation_applies_and_old_revision_does_not_overwrite(self):
        task = self.task('planning')
        execute_task(task.pk)
        task.refresh_from_db()
        self.assertEqual(task.status, 'completed')
        self.assertTrue(task.result['plan_applied'])
        self.assertEqual(task.result['source'], 'rules')
        self.assertEqual(len(task.result['cases']), 4)
        stale = self.task('planning')
        self.client.patch(self.path(f'plans/{self.plan.pk}/'), {'name': '用户新版本'}, format='json')
        execute_task(stale.pk)
        stale.refresh_from_db()
        self.plan.refresh_from_db()
        self.assertEqual(stale.status, 'completed')
        self.assertFalse(stale.result['plan_applied'])
        self.assertEqual(self.plan.cases, [])
        self.assertEqual(self.plan.name, '用户新版本')

    def test_cancel_queued_and_running_tasks_blocks_further_requests(self):
        queued = self.task()
        self.assertEqual(self.client.post(self.path(f'tasks/{queued.pk}/stop/'), {}, format='json').status_code, 200)
        with patch('security_testing.runner.execute') as runner:
            execute_task(queued.pk)
        runner.assert_not_called()
        running = self.task()

        def cancelled(snapshot, identities, check_active, on_progress):
            stop_task(running.pk)
            check_active()
            self.fail('cancelled task continued')

        with patch('security_testing.runner.execute', side_effect=cancelled):
            execute_task(running.pk)
        running.refresh_from_db()
        self.assertEqual(running.status, 'cancelled')
        self.assertEqual(running.identities_ciphertext, '')

    def test_cancel_and_timeout_preserve_completed_items_and_latest_request_count(self):
        from security_testing.runtime import TaskStopped
        from security_testing.tests.test_runner import response

        config_value = config()
        config_value['checks'] = ['information', 'authentication']
        self.plan.config = config_value
        self.plan.cases = candidates(config_value)
        self.plan.save()
        for stop in ('cancelled', 'task_timeout'):
            with self.subTest(stop=stop):
                with patch('security_testing.tasks.run_security_task.apply_async'), self.captureOnCommitCallbacks(execute=True):
                    task = enqueue(self.plan.pk, self.user, 'execution', uuid.uuid4(),
                                   case_ids=[item['id'] for item in self.plan.cases])[0]
                calls, ticks = [], iter(range(10000))

                def send(*args, **kwargs):
                    calls.append(args)
                    if len(calls) == 1:
                        return response({'id': 'private-proof', 'access_token': 'never-save-response-secret'})
                    if stop == 'cancelled':
                        stop_task(task.pk)
                    raise TaskStopped(stop)

                with patch('security_testing.runner.transport.request', side_effect=send), patch(
                        'security_testing.runner.time.monotonic', side_effect=lambda: next(ticks)):
                    execute_task(task.pk)
                task.refresh_from_db()
                self.assertEqual(len(calls), 2)
                self.assertEqual(task.status, 'cancelled' if stop == 'cancelled' else 'failed')
                self.assertEqual(task.error_code, stop)
                self.assertEqual(len(task.result['items']), 1)
                self.assertEqual(task.result['items'][0]['check'], 'information')
                self.assertEqual(task.result['summary']['requests_sent'], 2)
                self.assertEqual(task.progress['requests_sent'], 2)
                self.assertFalse(task.result['summary']['complete'])
                self.assertEqual(task.identities_ciphertext, '')
                report = self.client.get(self.path(f'tasks/{task.pk}/report.html'))
                self.assertEqual(report.status_code, 200)
                html = report.content.decode()
                self.assertIn('执行未完整完成', html)
                self.assertIn('响应包含', html)
                self.assertIn('未完成或未保存的检查不能视为通过', html)
                for secret in ('fixture-very-secret', 'private-proof', 'never-save-response-secret'):
                    self.assertNotIn(secret, json.dumps(task.result))
                    self.assertNotIn(secret, html)

    def test_permission_revocation_at_start_and_during_execution(self):
        task = self.task()
        self.member.can_execute_tests = False
        self.member.save()
        with patch('security_testing.runner.execute') as runner:
            execute_task(task.pk)
        runner.assert_not_called()
        task.refresh_from_db()
        self.assertEqual(task.error_code, 'permission_revoked')
        self.member.can_execute_tests = True
        self.member.save()
        task2 = self.task()

        def revoke(snapshot, identities, check_active, on_progress):
            ProjectMember.objects.filter(pk=self.member.pk).delete()
            check_active()
            self.fail('revoked task continued')

        with patch('security_testing.runner.execute', side_effect=revoke):
            execute_task(task2.pk)
        task2.refresh_from_db()
        self.assertEqual(task2.error_code, 'permission_revoked')

    def test_queue_failure_and_expiration_are_terminal_and_safe(self):
        with patch('security_testing.tasks.run_security_task.apply_async', side_effect=RuntimeError('credential-secret')), self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(self.path(f'plans/{self.plan.pk}/generate/'), {'request_id': str(uuid.uuid4())}, format='json')
        self.assertEqual(response.status_code, 202)  # transaction.on_commit follows response under TestCase
        failed = SecurityTask.objects.get(pk=response.data['data']['id'])
        self.assertEqual(failed.error_code, 'queue_unavailable')
        self.assertNotIn('credential-secret', failed.error_message)
        expired = self.task()
        SecurityTask.objects.filter(pk=expired.pk).update(deadline_at=timezone.now() - timedelta(seconds=1))
        with patch('security_testing.runner.execute') as runner:
            execute_task(expired.pk)
        runner.assert_not_called()
        expired.refresh_from_db()
        self.assertEqual(expired.error_code, 'queue_timeout')
        running = self.task()
        SecurityTask.objects.filter(pk=running.pk).update(status='running', deadline_at=timezone.now() - timedelta(seconds=1))
        recover_expired(self.project.pk)
        running.refresh_from_db()
        self.assertEqual(running.error_code, 'task_timeout')
        self.assertEqual(running.identities_ciphertext, '')

    def test_active_deadline_and_raw_exception_never_become_success(self):
        task = self.task()

        def expire(snapshot, identities, check_active, on_progress):
            with patch('security_testing.runtime.ExecutionContext.remaining_seconds', return_value=0):
                check_active()

        with patch('security_testing.runner.execute', side_effect=expire):
            execute_task(task.pk)
        task.refresh_from_db()
        self.assertEqual(task.error_code, 'task_timeout')
        second = self.task()
        with patch('security_testing.runner.execute', side_effect=RuntimeError('Bearer private-cookie-value')):
            execute_task(second.pk)
        second.refresh_from_db()
        self.assertEqual(second.status, 'failed')
        self.assertNotIn('private-cookie-value', str(self.client.get(self.path(f'tasks/{second.pk}/')).data))

    def test_invalid_cases_unknown_fields_and_invalid_model(self):
        url = self.path(f'plans/{self.plan.pk}/runs/')
        for ids in ([], ['unknown'], [self.plan.cases[0]['id']] * 2):
            response = self.client.post(url, {'request_id': str(uuid.uuid4()), 'case_ids': ids}, format='json')
            self.assertIn(response.status_code, (400, 409))
        response = self.client.post(self.path('plans/'), {'name': 'bad', 'config': config(), 'execute_code': 'print(1)'}, format='json')
        self.assertEqual(response.status_code, 400)
        response = self.client.post(self.path(f'plans/{self.plan.pk}/generate/'), {'request_id': str(uuid.uuid4()), 'model_config_id': 999999}, format='json')
        self.assertEqual(response.status_code, 409)

    def test_model_payload_is_minimal_and_invalid_outputs_rejected(self):
        payload = json.dumps(model_input(config()))
        for secret in ('security.example', 'private-query', 'private-proof', 'zh-CN', '私有资源'):
            self.assertNotIn(secret, payload)
        first = candidates(config())[0]
        result = validate_output(json.dumps({'cases': [{'id': first['id'], 'rationale': '验证匿名请求是否返回受保护资源'}]}), config())
        self.assertEqual(result[0]['endpoint_index'], 0)
        self.assertEqual(result[0]['check'], 'authentication')
        invalid = [
            {'cases': [{'id': first['id'], 'rationale': 'check', 'url': 'https://other.test'}]},
            {'cases': [{'id': 'invented', 'rationale': 'check'}]},
            {'cases': [{'id': first['id'], 'rationale': 'x'}] * 2},
            {'cases': []}, {'cases': [{'id': first['id'], 'rationale': 'x' * 501}]},
            {'cases': [{'id': [], 'rationale': 'x'}]},
        ]
        for item in invalid:
            with self.subTest(item=item), self.assertRaises(PlanningOutputError):
                validate_output(json.dumps(item), config())

    def test_model_disabled_between_queue_and_execution(self):
        model = LLMConfiguration.objects.create(model_type='llm', provider='offline', provider_name='替身',
                                               model_name='fixture', api_key='not-used', created_by=self.user)
        task = self.task('planning', model_config_id=model.pk)
        model.is_active = False
        model.save()
        with patch('security_testing.planning.generate') as generate:
            execute_task(task.pk)
        generate.assert_not_called()
        task.refresh_from_db()
        self.assertEqual(task.error_code, 'model_unavailable')

    def test_report_requires_report_permission_and_terminal_execution(self):
        task = self.task()
        url = self.path(f'tasks/{task.pk}/report.html')
        self.assertEqual(self.client.get(url).status_code, 409)
        SecurityTask.objects.filter(pk=task.pk).update(status='completed', result={'items': [], 'summary': {}, 'limitations': []})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertIn('attachment', response['Content-Disposition'])
        self.assertIn('text/html', response['Content-Type'])
        self.member.can_view_reports = False
        self.member.save()
        self.assertEqual(self.client.get(url).status_code, 403)

    def test_planner_can_poll_own_tasks_without_report_capability(self):
        execution = self.task()
        planning = self.task('planning')
        other = self.task('planning')
        SecurityTask.objects.filter(pk=other.pk).update(created_by=self.outsider)
        self.member.can_view_reports = False
        self.member.save()
        response = self.client.get(self.path('tasks/'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual([item['id'] for item in response.data['data']['items']], [str(planning.pk)])
        self.assertEqual(self.client.get(self.path(f'tasks/{planning.pk}/')).status_code, 200)
        self.assertEqual(self.client.get(self.path(f'tasks/{execution.pk}/')).status_code, 403)
        self.assertEqual(self.client.get(self.path(f'tasks/{other.pk}/')).status_code, 403)

    def test_security_project_creation_and_deletion_protects_history_before_files(self):
        self.assertEqual(self.client.post('/api/v1/projects/', {'name': 'new security', 'project_type': 'security'}, format='json').status_code, 403)
        self.user.is_staff = True
        self.user.save()
        response = self.client.post('/api/v1/projects/', {'name': 'new security', 'project_type': 'security'}, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        created = Project.objects.get(name='new security')
        self.assertEqual(created.project_type, 'security')
        self.assertEqual(self.client.delete(f'/api/v1/projects/{created.pk}/').status_code, 204)
        task = self.task()
        with patch('projects.views.default_storage.delete') as delete_file:
            self.assertEqual(self.client.delete(f'/api/v1/projects/{self.project.pk}/').status_code, 409)
        delete_file.assert_not_called()
        SecurityTask.objects.filter(pk=task.pk).update(status='completed')
        with patch('projects.views.default_storage.delete') as delete_file:
            self.assertEqual(self.client.delete(f'/api/v1/projects/{self.project.pk}/').status_code, 409)
        delete_file.assert_not_called()
        self.assertTrue(SecurityTask.objects.filter(pk=task.pk).exists())


class SecuritySourceTests(SecurityFixture, TestCase):
    def setUp(self):
        super().setUp()
        self.source = Project.objects.create(name='来源API', project_type='api', created_by=self.user)
        self.source_member = ProjectMember.objects.create(project=self.source, user=self.user, can_view_reports=True)
        self.spec = APISpecification.objects.create(project=self.source, created_by=self.user, metadata={'servers': [{'url': 'https://security.example.test'}]})

    def test_import_documents_and_only_own_terminal_discovery_without_auth(self):
        APIEndpoint.objects.create(spec=self.spec, method='GET', path='/items?token=auth-secret&name=test', summary='列表')
        APIEndpoint.objects.create(spec=self.spec, method='DELETE', path='/items/1')
        APIEndpoint.objects.create(spec=self.spec, method='GET', path='/items/{id}')
        for owner, status in [(self.user, 'completed'), (self.outsider, 'completed'), (self.user, 'running')]:
            task = BrowserDiscoveryTask.objects.create(project=self.source, owner=owner, model_id=1,
                target_url='https://security.example.test', task_id=str(uuid.uuid4()), status=status)
            BrowserDiscoveryRecord.objects.create(task=task, sequence=1, origin='https://security.example.test',
                path='/mine', method='GET', is_eligible=True, public_summary={'observed_request': {
                    'query': [{'name': 'q', 'value': owner.username}, {'name': 'token', 'value': 'query-secret'}],
                    'headers': {'Authorization': 'Bearer capture-secret', 'Cookie': 'sid=raw-cookie', 'Accept-Language': 'zh-CN', 'X-Tenant': 'test-tenant'},
                }})
        response = self.client.get(self.path(f'sources/?source_project_id={self.source.pk}'))
        self.assertEqual(response.status_code, 200)
        items = response.data['data']['endpoints']
        self.assertEqual(len(items), 2)
        rendered = json.dumps(items)
        for forbidden in ('auth-secret', 'query-secret', 'capture-secret', 'raw-cookie', 'security-other'):
            self.assertNotIn(forbidden, rendered)
        self.assertTrue(all(item['method'] == 'GET' for item in items))
        sample = next(item for item in items if item['source_id'].startswith('discovery:'))
        self.assertEqual(sample['headers']['X-Tenant'], 'test-tenant')
        self.assertTrue(any('Authorization' in warning for warning in response.data['data']['warnings']))

    def test_sources_require_read_and_report_in_source_project(self):
        self.source_member.can_view_reports = False
        self.source_member.save()
        result = self.client.get(self.path('sources/')).data['data']['projects']
        self.assertEqual(result, [])
        self.assertEqual(self.client.get(self.path(f'sources/?source_project_id={self.source.pk}')).status_code, 403)
        self.source_member.delete()
        self.assertEqual(self.client.get(self.path(f'sources/?source_project_id={self.source.pk}')).status_code, 404)
