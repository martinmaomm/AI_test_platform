"""Personal login-record contracts; run only with isolated Django test settings."""
from unittest.mock import patch

from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from users.models import LoginRecord, User


class LoginRecordApiTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.password = 'fixture-login-secret-123'
        cls.user = User.objects.create_user(
            username='login-member', email='login-member@example.test', password=cls.password,
        )
        cls.other = User.objects.create_user(
            username='login-other', email='login-other@example.test', password=cls.password,
        )
        cls.admin = User.objects.create_user(
            username='login-admin', email='login-admin@example.test', password=cls.password, is_staff=True,
        )

    def setUp(self):
        self.client = APIClient()

    def login(self, user=None, password=None, **request_meta):
        user = user or self.user
        return self.client.post(
            '/api/v1/users/login/',
            {'username': user.username, 'password': password or self.password},
            format='json',
            **request_meta,
        )

    def test_only_successful_business_login_creates_one_minimal_record(self):
        result = self.login(
            REMOTE_ADDR='198.51.100.7',
            HTTP_X_FORWARDED_FOR='203.0.113.99',
            HTTP_USER_AGENT='fixture browser',
            HTTP_COOKIE='sessionid=fixture-cookie-secret',
        )
        self.assertEqual(result.status_code, 200, result.data)
        self.assertIn('access', result.data['data'])
        self.assertIn('refresh', result.data['data'])
        self.assertEqual(LoginRecord.objects.count(), 1)

        record = LoginRecord.objects.get()
        self.assertEqual(record.user, self.user)
        self.assertEqual(record.ip_address, '198.51.100.7')
        self.assertEqual(record.user_agent, 'fixture browser')
        persisted = str(LoginRecord.objects.values().get())
        self.assertNotIn(self.password, persisted)
        self.assertNotIn('fixture-cookie-secret', persisted)
        self.assertNotIn(result.data['data']['access'], persisted)
        self.assertNotIn(result.data['data']['refresh'], persisted)

    def test_each_password_token_entry_records_once_but_failures_and_refresh_verify_do_not(self):
        failed = self.login(password='wrong-fixture-password')
        self.assertEqual(failed.status_code, 400)
        self.assertEqual(LoginRecord.objects.count(), 0)

        failed_simple_jwt = self.client.post(
            '/api/v1/auth/token/',
            {'username': self.user.username, 'password': 'wrong-fixture-password'},
            format='json',
        )
        self.assertEqual(failed_simple_jwt.status_code, 401)
        self.assertEqual(LoginRecord.objects.count(), 0)

        simple_jwt_login = self.client.post(
            '/api/v1/auth/token/',
            {'username': self.user.username, 'password': self.password},
            format='json',
        )
        self.assertEqual(simple_jwt_login.status_code, 200, simple_jwt_login.data)
        self.assertEqual(set(simple_jwt_login.data), {'access', 'refresh'})
        self.assertEqual(LoginRecord.objects.count(), 1)

        logged_in = self.login()
        self.assertEqual(logged_in.status_code, 200, logged_in.data)
        self.assertEqual(LoginRecord.objects.count(), 2)
        access = logged_in.data['data']['access']
        refresh = logged_in.data['data']['refresh']

        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {access}')
        refreshed = self.client.post('/api/v1/users/refresh-token/', {'refresh': refresh}, format='json')
        self.assertEqual(refreshed.status_code, 200, refreshed.data)
        verified = self.client.post('/api/v1/users/verify-token/', {'token': access}, format='json')
        self.assertEqual(verified.status_code, 200, verified.data)
        jwt_refreshed = self.client.post('/api/v1/auth/token/refresh/', {'refresh': refresh}, format='json')
        self.assertEqual(jwt_refreshed.status_code, 200, jwt_refreshed.data)
        self.assertEqual(LoginRecord.objects.count(), 2)

    def test_records_are_personal_even_for_admin_and_user_id_is_ignored(self):
        own = LoginRecord.objects.create(user=self.admin, ip_address='192.0.2.1', user_agent='admin')
        LoginRecord.objects.create(user=self.user, ip_address='192.0.2.2', user_agent='member')
        LoginRecord.objects.create(user=self.other, ip_address='192.0.2.3', user_agent='other')

        self.client.force_authenticate(self.admin)
        result = self.client.get('/api/v1/users/login-records/', {'user_id': self.user.pk})
        self.assertEqual(result.status_code, 200, result.data)
        self.assertEqual(result['Cache-Control'], 'no-store')
        self.assertEqual(result.data['data']['pagination']['total'], 1)
        self.assertEqual([item['id'] for item in result.data['data']['items']], [own.pk])

    def test_list_requires_authentication_and_is_read_only(self):
        self.assertEqual(self.client.get('/api/v1/users/login-records/').status_code, 401)
        self.client.force_authenticate(self.user)
        for method in ('post', 'put', 'patch', 'delete'):
            with self.subTest(method=method):
                result = getattr(self.client, method)('/api/v1/users/login-records/', {}, format='json')
                self.assertEqual(result.status_code, 405)
        self.assertEqual(LoginRecord.objects.count(), 0)

    def test_pagination_defaults_to_twenty_and_rejects_invalid_parameters(self):
        records = [LoginRecord.objects.create(user=self.user, user_agent=f'agent-{index}') for index in range(25)]
        self.client.force_authenticate(self.user)

        first = self.client.get('/api/v1/users/login-records/')
        self.assertEqual(first.status_code, 200, first.data)
        self.assertEqual(first.data['data']['pagination'], {
            'total': 25,
            'page': 1,
            'page_size': 20,
            'total_pages': 2,
            'has_next': True,
            'has_previous': False,
        })
        self.assertEqual(len(first.data['data']['items']), 20)
        self.assertEqual(first.data['data']['items'][0]['id'], records[-1].pk)

        second = self.client.get('/api/v1/users/login-records/', {'page': 2})
        self.assertEqual(second.status_code, 200, second.data)
        self.assertEqual(len(second.data['data']['items']), 5)
        self.assertTrue(second.data['data']['pagination']['has_previous'])

        for params in (
            {'page': 'invalid'}, {'page': 0}, {'page': -1},
            {'page_size': 'invalid'}, {'page_size': 0}, {'page_size': 101},
        ):
            with self.subTest(params=params):
                self.assertEqual(self.client.get('/api/v1/users/login-records/', params).status_code, 400)

    def test_user_agent_is_truncated_to_model_limit(self):
        result = self.login(REMOTE_ADDR='192.0.2.8', HTTP_USER_AGENT='界' * 600)
        self.assertEqual(result.status_code, 200, result.data)
        self.assertEqual(LoginRecord.objects.get().user_agent, '界' * 512)

    def test_insert_failure_rolls_back_savepoint_and_does_not_break_login(self):
        existing = LoginRecord.objects.create(user=self.other, user_agent='existing')
        original_create = LoginRecord.objects.create

        def duplicate_primary_key(**kwargs):
            return original_create(pk=existing.pk, **kwargs)

        with patch('users.login_history.LoginRecord.objects.create', side_effect=duplicate_primary_key):
            with self.assertLogs('users.login_history', level='ERROR') as captured:
                result = self.login(REMOTE_ADDR='192.0.2.20')

        self.assertEqual(result.status_code, 200, result.data)
        self.assertIn('access', result.data['data'])
        self.assertEqual(LoginRecord.objects.count(), 1)
        self.assertTrue(User.objects.filter(pk=self.user.pk).exists())
        log_output = ' '.join(captured.output)
        self.assertIn(f'user_id={self.user.pk}', log_output)
        self.assertNotIn(self.password, log_output)
        self.assertNotIn('UNIQUE constraint', log_output)
        self.assertNotIn('INSERT INTO', log_output)


@override_settings(LOGIN_RECORD_TRUSTED_PROXIES=(
    '127.0.0.1/32', '::1/128', '10.0.0.0/8', '::ffff:10.0.0.0/104',
))
class LoginRecordIpTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.password = 'fixture-ip-secret-123'
        cls.user = User.objects.create_user(
            username='ip-member', email='ip-member@example.test', password=cls.password,
        )

    def setUp(self):
        self.client = APIClient()

    def login_ip(self, peer=None, forwarded_for=None):
        meta = {}
        if peer is not None:
            meta['REMOTE_ADDR'] = peer
        if forwarded_for is not None:
            meta['HTTP_X_FORWARDED_FOR'] = forwarded_for
        result = self.client.post(
            '/api/v1/users/login/',
            {'username': self.user.username, 'password': self.password},
            format='json',
            **meta,
        )
        self.assertEqual(result.status_code, 200, result.data)
        return LoginRecord.objects.latest('id').ip_address

    def test_untrusted_peer_cannot_spoof_forwarded_ipv4(self):
        self.assertEqual(self.login_ip('198.51.100.10', '203.0.113.9'), '198.51.100.10')

    def test_trusted_chain_selects_first_untrusted_address_from_right(self):
        self.assertEqual(
            self.login_ip('127.0.0.1', '198.51.100.11, 10.1.2.3'),
            '198.51.100.11',
        )

    def test_ipv6_and_ipv4_mapped_ipv6_are_normalized(self):
        self.assertEqual(
            self.login_ip('::1', '2001:db8::123, ::ffff:10.1.2.3'),
            '2001:db8::123',
        )
        self.assertEqual(
            self.login_ip('::ffff:127.0.0.1', '::ffff:192.0.2.44'),
            '192.0.2.44',
        )

    def test_invalid_or_overlong_forwarded_chain_falls_back_to_peer(self):
        self.assertEqual(self.login_ip('127.0.0.1', '198.51.100.1, not-an-ip'), '127.0.0.1')
        too_many_hops = ','.join(f'10.0.0.{index}' for index in range(1, 34))
        self.assertEqual(self.login_ip('127.0.0.1', too_many_hops), '127.0.0.1')
        self.assertIsNone(self.login_ip('not-an-ip', '198.51.100.1'))

    @override_settings(LOGIN_RECORD_TRUSTED_PROXIES=(
        'invalid-cidr', '0.0.0.0/0', '::/0', '::ffff:0.0.0.0/96',
    ))
    def test_invalid_and_full_network_trust_configuration_cannot_expand_trust(self):
        self.assertEqual(self.login_ip('198.51.100.12', '203.0.113.12'), '198.51.100.12')
        self.assertEqual(self.login_ip('127.0.0.1', '203.0.113.13'), '127.0.0.1')
