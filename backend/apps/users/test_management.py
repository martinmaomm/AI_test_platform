"""Role and assignment contracts; run only with isolated Django test settings."""
from django.test import TestCase
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import AccessToken

from projects.models import Project, ProjectMember
from users.models import User


class UserManagementTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        def user(name, **kwargs):
            return User.objects.create_user(username=name, email=f'{name}@example.test', password='fixture-only-123', **kwargs)
        cls.root = user('root', is_staff=True, is_superuser=True)
        cls.admin = user('admin', is_staff=True)
        cls.peer = user('peer', is_staff=True)
        cls.member = user('member')
        cls.other = user('other')
        cls.web = Project.objects.create(name='Shared UI', project_type='web', created_by=cls.root)
        cls.api = Project.objects.create(name='Shared API', project_type='api', created_by=cls.root)

    def setUp(self):
        self.client = APIClient()

    def as_user(self, user):
        self.client.force_authenticate(user)
        return self.client

    def detail(self, user):
        return f'/api/v1/users/manage/{user.pk}/'

    def assignment(self, user):
        return self.detail(user) + 'projects/'

    def test_only_admins_can_list_create_update_or_assign(self):
        for actor, expected in ((None, 401), (self.member, 403)):
            self.as_user(actor)
            for method, url, payload in (
                ('get', '/api/v1/users/manage/', None),
                ('post', '/api/v1/users/manage/', {}),
                ('patch', self.detail(self.other), {'role': 'admin'}),
                ('put', self.assignment(self.other), {'project_ids': [self.web.pk]}),
            ):
                with self.subTest(actor=bool(actor), method=method):
                    result = getattr(self.client, method)(url, payload, format='json')
                    self.assertEqual(result.status_code, expected)

    def test_admin_list_paginates_searches_and_never_returns_passwords(self):
        self.as_user(self.admin)
        result = self.client.get('/api/v1/users/manage/', {'search': 'member', 'page_size': 1})
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.data['data']['pagination']['total'], 1)
        item = result.data['data']['items'][0]
        self.assertEqual(item['role'], 'user')
        self.assertNotIn('password', item)
        result = self.client.get('/api/v1/users/manage/', {'page_size': 'invalid'})
        self.assertEqual(result.status_code, 400)

    def test_admin_creates_normal_user_and_password_is_hashed(self):
        self.as_user(self.admin)
        result = self.client.post('/api/v1/users/manage/', {
            'username': 'created', 'email': 'created@example.test', 'password': 'fixture-created-123',
        }, format='json')
        self.assertEqual(result.status_code, 201, result.data)
        created = User.objects.get(username='created')
        self.assertTrue(created.check_password('fixture-created-123'))
        self.assertFalse(created.is_staff)
        self.assertFalse(created.is_superuser)
        self.assertEqual(result.data['data']['role'], 'user')
        self.assertNotIn('password', result.data['data'])

    def test_only_superuser_can_grant_or_revoke_admin(self):
        payload = {'username': 'elevated', 'email': 'elevated@example.test', 'password': 'fixture-elevated', 'role': 'admin'}
        self.assertEqual(self.as_user(self.admin).post('/api/v1/users/manage/', payload, format='json').status_code, 400)
        self.assertFalse(User.objects.filter(username='elevated').exists())
        result = self.as_user(self.root).patch(self.detail(self.member), {'role': 'admin'}, format='json')
        self.assertEqual(result.status_code, 200, result.data)
        self.member.refresh_from_db()
        self.assertTrue(self.member.is_staff)
        self.assertFalse(self.member.is_superuser)
        self.assertEqual(self.as_user(self.admin).patch(self.detail(self.member), {'role': 'user'}, format='json').status_code, 403)
        self.assertEqual(self.as_user(self.root).patch(self.detail(self.member), {'role': 'user'}, format='json').status_code, 200)
        self.member.refresh_from_db()
        self.assertFalse(self.member.is_staff)

    def test_superuser_self_and_peer_accounts_are_protected(self):
        for actor, target in ((self.admin, self.peer), (self.admin, self.root), (self.root, self.root), (self.admin, self.admin)):
            with self.subTest(actor=actor.username, target=target.username):
                result = self.as_user(actor).patch(self.detail(target), {'password': 'reset-test-only', 'is_active': False}, format='json')
                self.assertEqual(result.status_code, 403)
                target.refresh_from_db()
                self.assertTrue(target.is_active)
                self.assertFalse(target.check_password('reset-test-only'))
        self.assertEqual(self.as_user(self.root).delete(self.detail(self.member)).status_code, 405)

    def test_privilege_flags_are_rejected_by_management(self):
        for actor in (self.root, self.admin):
            for field, value in (('is_superuser', True), ('is_staff', True), ('groups', []), ('user_permissions', [])):
                result = self.as_user(actor).patch(self.detail(self.member), {field: value}, format='json')
                self.assertEqual(result.status_code, 400)
        self.member.refresh_from_db()
        self.assertFalse(self.member.is_staff or self.member.is_superuser)

    def test_public_registration_and_own_profile_cannot_grant_roles(self):
        result = self.client.post('/api/v1/users/register/', {
            'username': 'registered', 'email': 'registered@example.test',
            'password': 'fixture-registered', 'confirm_password': 'fixture-registered',
            'role': 'admin', 'is_staff': True, 'is_superuser': True,
        }, format='json')
        self.assertEqual(result.status_code, 200)
        registered = User.objects.get(username='registered')
        self.assertFalse(registered.is_staff or registered.is_superuser)
        result = self.as_user(registered).patch('/api/v1/users/manage/profile/', {
            'role': 'admin', 'is_staff': True, 'is_superuser': True, 'password': 'fixture-new-pass',
        }, format='json')
        self.assertEqual(result.status_code, 200, result.data)
        registered.refresh_from_db()
        self.assertFalse(registered.is_staff or registered.is_superuser)
        self.assertTrue(registered.check_password('fixture-new-pass'))

    def test_assignment_is_multi_user_multi_project_and_invalid_input_is_atomic(self):
        self.as_user(self.admin)
        for user, ids in ((self.member, [self.api.pk, self.web.pk, self.api.pk]), (self.other, [self.web.pk])):
            result = self.client.put(self.assignment(user), {'project_ids': ids}, format='json')
            self.assertEqual(result.status_code, 200, result.data)
        self.assertEqual(ProjectMember.objects.filter(project=self.web).count(), 2)
        self.assertEqual(ProjectMember.objects.filter(user=self.member).count(), 2)
        membership = ProjectMember.objects.get(user=self.member, project=self.web)
        self.assertTrue(membership.can_edit and membership.can_execute_tests and membership.can_delete)
        result = self.client.put(self.assignment(self.member), {'project_ids': [self.web.pk, 999999]}, format='json')
        self.assertEqual(result.status_code, 400)
        self.assertEqual(ProjectMember.objects.filter(user=self.member).count(), 2)
        self.assertEqual(self.client.put(self.assignment(self.member), {'project_ids': []}, format='json').status_code, 200)
        self.assertFalse(ProjectMember.objects.filter(user=self.member).exists())
        self.assertTrue(ProjectMember.objects.filter(user=self.other, project=self.web).exists())

    def test_personal_profile_put_needs_no_password_and_does_not_restore_old_roles(self):
        # Simulate an admin revoked after request authentication, before saving.
        User.objects.filter(pk=self.admin.pk).update(is_staff=False)
        result = self.as_user(self.admin).put('/api/v1/users/manage/profile/', {
            'first_name': 'Updated', 'profile': {'title': 'Tester'},
            'role': 'admin', 'is_staff': True, 'is_active': False,
        }, format='json')
        self.assertEqual(result.status_code, 200, result.data)
        self.assertTrue(result.data['success'])
        self.assertEqual(result.data['data']['role'], 'user')
        self.admin.refresh_from_db()
        self.assertEqual(self.admin.first_name, 'Updated')
        self.assertEqual(self.admin.profile.title, 'Tester')
        self.assertTrue(self.admin.check_password('fixture-only-123'))
        self.assertFalse(self.admin.is_staff)
        self.assertTrue(self.admin.is_active)

    def test_disabled_account_cannot_reuse_existing_access_token(self):
        token = str(AccessToken.for_user(self.member))
        self.as_user(self.admin).patch(self.detail(self.member), {'is_active': False}, format='json')
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION='Bearer ' + token)
        self.assertEqual(client.get('/api/v1/users/current-user/').status_code, 401)

    def test_role_downgrade_applies_to_existing_token(self):
        token = str(AccessToken.for_user(self.admin))
        result = self.as_user(self.root).patch(self.detail(self.admin), {'role': 'user'}, format='json')
        self.assertEqual(result.status_code, 200)
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION='Bearer ' + token)
        self.assertEqual(client.get('/api/v1/users/manage/').status_code, 403)

    def test_assignment_is_unnecessary_for_admins(self):
        result = self.as_user(self.root).put(self.assignment(self.admin), {'project_ids': [self.web.pk]}, format='json')
        self.assertEqual(result.status_code, 400)
        self.assertFalse(ProjectMember.objects.filter(user=self.admin).exists())

    def test_disabled_account_cannot_open_websocket_with_existing_token(self):
        from asgiref.sync import async_to_sync
        from common.websocket.websocket_core import WebSocketJWTAuthMiddleware
        token = str(AccessToken.for_user(self.member))
        middleware = WebSocketJWTAuthMiddleware(None)
        self.assertEqual(async_to_sync(middleware._get_user_from_token)(token).pk, self.member.pk)
        self.as_user(self.admin).patch(self.detail(self.member), {'is_active': False}, format='json')
        self.assertIsNone(async_to_sync(middleware._get_user_from_token)(token))

    def test_legacy_global_file_utilities_are_not_available_to_members(self):
        for path in ('/api/v1/util/files/list/', '/api/v1/util/files/download/'):
            result = self.as_user(self.member).get(path)
            self.assertEqual(result.status_code, 403)

    def test_global_task_socket_is_admin_only_but_personal_stream_remains_available(self):
        from asgiref.sync import async_to_sync
        from asgiref.testing import ApplicationCommunicator
        from common.websocket.websocket_handlers import StreamingConsumer, TaskStatusConsumer

        async def check(consumer, user, expected, path):
            socket = ApplicationCommunicator(consumer.as_asgi(), {
                'type': 'websocket', 'path': path, 'user': user,
                'url_route': {'kwargs': {'task_id': 'fixture-task'}},
            })
            await socket.send_input({'type': 'websocket.connect'})
            event = await socket.receive_output()
            self.assertEqual(event['type'], 'websocket.accept' if expected else 'websocket.close')
            await socket.send_input({'type': 'websocket.disconnect', 'code': 1000})
            await socket.wait()

        async_to_sync(check)(TaskStatusConsumer, self.member, False, '/ws/task-status/fixture-task/')
        async_to_sync(check)(TaskStatusConsumer, self.admin, True, '/ws/task-status/fixture-task/')
        async_to_sync(check)(StreamingConsumer, self.member, True, '/ws/webui_auto_test-streaming/')
