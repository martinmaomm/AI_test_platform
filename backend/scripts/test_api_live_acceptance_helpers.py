"""Offline checks for live-test infrastructure; no real HTTP, DB or model use."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import verify_api_live_delivery as delivery
import verify_api_live_acceptance as acceptance


class LiveHelperTests(unittest.TestCase):
    def session(self, body, status=200):
        session = Mock()
        session.request.return_value.status_code = status
        session.request.return_value.json.return_value = body
        return session

    def test_api_envelope(self):
        self.assertEqual(delivery.request_data(self.session({'success': True, 'data': {'id': 1}}),
                                              'GET', delivery.API_BASE + 'test-suites/1/'), {'id': 1})

    def test_successful_association_without_body(self):
        self.assertIsNone(delivery.request_data(self.session({'success': True, 'data': None}),
                                               'POST', delivery.API_BASE + 'test-suites/1/add-test-cases/'))

    def test_schedule_raw_create_without_id(self):
        body = {'name': 'fixture', 'suite_ids': [1], 'status': 'paused'}
        self.assertEqual(delivery.request_data(self.session(body, 201), 'POST', delivery.SCHEDULE_BASE + 'tasks/'), body)

    def test_schedule_raw_pagination(self):
        body = {'count': 1, 'results': [{'id': 1}]}
        self.assertEqual(delivery.request_data(self.session(body), 'GET', delivery.SCHEDULE_BASE + 'tasks/'), body)

    def test_error_does_not_echo_server_body(self):
        with self.assertRaises(RuntimeError) as error:
            delivery.request_data(self.session({'password': 'private-fixture'}, 400), 'POST', delivery.API_BASE)
        self.assertNotIn('private-fixture', str(error.exception))

    def test_pending_response_blocks_new_dispatch(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'ledger.json'
            ledger = {}
            delivery.begin_pending_response(ledger, path, 'create_suite')
            restored = json.loads(path.read_text())
            with self.assertRaises(RuntimeError):
                delivery.begin_pending_response(restored, path, 'create_suite')
            self.assertEqual(restored, ledger)
            delivery.complete_pending_response(restored, path, suite_id=2)
            delivery.ensure_no_pending_response(restored)
            self.assertEqual(json.loads(path.read_text())['suite_id'], 2)

    def test_private_artifact_permissions(self):
        with tempfile.TemporaryDirectory() as tmp:
            for index, writer in enumerate((delivery.write_json, acceptance.write_json)):
                path = Path(tmp) / f'{index}.json'
                writer(path, {'value': 'fixture'})
                os.chmod(path, 0o644)
                writer(path, {'value': 'private-fixture'}, private=True)
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_schedule_report_wrong_order_is_not_accepted(self):
        report = {'case_executions': [{'test_case': 11, 'status': 'passed'}, {'test_case': 10, 'status': 'passed'}]}
        with self.assertRaises(RuntimeError):
            delivery.assert_two_case_report(report, 10, 11)

    def test_crud_finish_refuses_unverified_or_mixed_candidate_before_writes(self):
        with tempfile.TemporaryDirectory() as tmp:
            args = SimpleNamespace(output=Path(tmp))
            acceptance.write_json(args.output / 'live-b.json', {'root_id': 10})
            for passed, mixed in ((False, False), (True, True)):
                with self.subTest(passed=passed, mixed=mixed):
                    steps = [{'endpoint_id': i, 'validate': [{'eq': ['status_code', 200]}]}
                             for i in (4, 27, 29, 30, 28)]
                    if mixed:
                        steps.append({'endpoint_id': 29, 'validate': [{'eq': ['body.code', 401]}]})
                    child = {'endpoint_ids': [27, 28, 29, 30], 'candidate': {
                        'verification_status': 'passed' if passed else 'failed', 'risks': [],
                        'draft': {'teststeps': steps},
                    }}
                    session = Mock()
                    session.get.return_value.json.return_value = {'data': {'status': 'ready', 'scenarios': [child]}}
                    with patch.object(acceptance, 'platform_session', return_value=session), \
                         patch.object(acceptance, 'run_formal_case') as run, self.assertRaises(AssertionError):
                        acceptance.finish_b_crud(args)
                    session.patch.assert_not_called()
                    session.post.assert_not_called()
                    run.assert_not_called()

    def test_crud_acceptance_rejects_noop_rename_even_if_runtime_passed(self):
        def report(updated):
            return {'success': True, 'step_datas': [{'data': {'req_resps': [{'request': {
                'method': 'POST', 'url': f'https://fixture.test/resourceCategory/{path}', 'body': {'name': name},
            }}]}} for path, name in [('create', 'unique-first'), ('update/13', updated)]]}
        with self.assertRaisesRegex(AssertionError, '新增名和修改名相同'):
            acceptance.require_distinct_crud_names(report('unique-first'))
        acceptance.require_distinct_crud_names(report('unique-second'))


if __name__ == '__main__':
    unittest.main()
