"""Offline checks for live-test infrastructure; no real HTTP, DB or model use."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

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


if __name__ == '__main__':
    unittest.main()
