import json
import sys
import importlib.util
from pathlib import Path
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock, patch

from celery.exceptions import SoftTimeLimitExceeded

from performance_testing.analysis_data import build_analysis_input
from performance_testing.analysis_engine import (
    AnalysisOutputError, COMPARISON_SYSTEM_PROMPT, MAX_OUTPUT_CHARS, SYSTEM_PROMPT,
    VALIDATION_SYSTEM_PROMPT, generate_analysis, parse_analysis_output,
    _stream_analysis_text,
)
from .test_analysis_data import fixture_run


class AnalysisEngineTests(TestCase):
    def setUp(self):
        transport_patch = patch('performance_testing.analysis_engine.stream_in_process', _stream_analysis_text)
        transport_patch.start()
        self.addCleanup(transport_patch.stop)
        self.payload = build_analysis_input(fixture_run(), {})
        self.output = {'summary': '本次运行存在读取超时，未配置性能目标。', 'findings': [{
            'category': 'errors', 'kind': 'observation', 'severity': 'warning',
            'title': '存在读取超时样本', 'detail': '错误样本中记录了读取超时。',
            'recommendation': '对照服务端日志和超时配置排查。',
            'evidence_ids': ['errors.samples', 'overall.failures'],
        }], 'limitations': ['错误样本不能用于推断全部失败的分布。']}

    def parse(self):
        return parse_analysis_output(json.dumps(self.output, ensure_ascii=False), self.payload)

    def test_server_assessment_evidence_and_limitations_always_preserved(self):
        result = self.parse()
        self.assertEqual(result['assessment']['status'], 'not_configured')
        self.assertEqual(result['evidence'], self.payload['evidence'])
        self.assertTrue(all(value in result['limitations'] for value in self.payload['limitations']))

    def test_prompt_distinguishes_interval_average_from_instantaneous_qps(self):
        self.assertIn('trend.throughput', SYSTEM_PROMPT)
        self.assertIn('不是瞬时 QPS', SYSTEM_PROMPT)
        self.assertIn('被截断的更早历史', SYSTEM_PROMPT)

    def test_unknown_or_missing_evidence_references_reject_output(self):
        for refs in ([], ['database.cpu'], ['overall.p95', {}]):
            self.output['findings'][0]['evidence_ids'] = refs
            with self.assertRaises(AnalysisOutputError):
                self.parse()

    def test_type_specific_categories_and_assessment_statuses_are_enforced(self):
        cases = (
            ('validation_diagnosis', 'failed', 'request'),
            ('load_comparison', 'comparable', 'comparison'),
        )
        for analysis_type, assessment_status, category in cases:
            payload = {
                'analysis_type': analysis_type,
                'assessment': {'status': assessment_status},
                'evidence': [{'id': 'safe.evidence', 'value': 1}],
                'limitations': [],
            }
            output = {
                **self.output,
                'findings': [{
                    **self.output['findings'][0],
                    'category': category,
                    'evidence_ids': ['safe.evidence'],
                }],
            }
            parsed = parse_analysis_output(json.dumps(output), payload)
            self.assertEqual(parsed['assessment']['status'], assessment_status)

        invalid_payloads = (
            {**self.payload, 'analysis_type': 'unknown'},
            {**self.payload, 'analysis_type': []},
            {**self.payload, 'analysis_type': 'validation_diagnosis',
             'assessment': {'status': 'met'}},
            {**self.payload, 'analysis_type': 'validation_diagnosis',
             'assessment': {'status': []}},
            {**self.payload, 'analysis_type': 'validation_diagnosis',
             'assessment': {'status': {}}},
            {**self.payload, 'analysis_type': 'load_comparison',
             'assessment': {'status': 'not_met'}},
        )
        for payload in invalid_payloads:
            with self.assertRaises(AnalysisOutputError):
                parse_analysis_output(json.dumps(self.output), payload)

    def test_each_analysis_type_has_a_fixed_non_action_prompt(self):
        self.assertIn('不执行请求', VALIDATION_SYSTEM_PROMPT)
        self.assertIn('单用户验证', VALIDATION_SYSTEM_PROMPT)
        self.assertIn('不执行请求', COMPARISON_SYSTEM_PROMPT)
        self.assertIn('历史压测对比', COMPARISON_SYSTEM_PROMPT)
        self.assertIn('不调用工具', SYSTEM_PROMPT)

        cases = (
            ('validation_diagnosis', 'failed', VALIDATION_SYSTEM_PROMPT),
            ('load_comparison', 'comparable', COMPARISON_SYSTEM_PROMPT),
        )
        for analysis_type, status, expected_prompt in cases:
            transport = Mock(return_value='{}')
            payload = {
                'analysis_type': analysis_type,
                'assessment': {'status': status},
                'evidence': [], 'limitations': [],
            }
            with patch.dict(sys.modules, {
                'project_knowledge.llm': SimpleNamespace(stream_call=transport),
            }):
                _stream_analysis_text(7, payload, lambda: True, lambda: 10)
            self.assertEqual(
                transport.call_args.kwargs['messages'][0]['content'], expected_prompt,
            )

    def test_model_cannot_replace_target_evaluation_or_add_html_field(self):
        for key, value in (('assessment', {'status': 'met'}), ('html', '<script>alert(1)</script>')):
            candidate = {**self.output, key: value}
            with self.assertRaises(AnalysisOutputError):
                parse_analysis_output(json.dumps(candidate), self.payload)

    def test_invalid_json_duplicate_keys_nan_and_oversize_rejected(self):
        for raw in ('not json', '[]', '{"summary":"a","summary":"b"}',
                    '{"summary":NaN,"findings":[],"limitations":[]}', 'x' * (MAX_OUTPUT_CHARS + 1)):
            with self.assertRaises(AnalysisOutputError):
                parse_analysis_output(raw, self.payload)

    def test_invalid_classification_missing_text_and_excess_findings_rejected(self):
        for key, value in (('category', []), ('category', {}),
                           ('kind', 'proven_database_issue'), ('severity', []), ('detail', ''),
                           ('recommendation', 'x' * 1201)):
            saved = self.output['findings'][0][key]
            self.output['findings'][0][key] = value
            with self.assertRaises(AnalysisOutputError):
                self.parse()
            self.output['findings'][0][key] = saved
        self.output['findings'] *= 13
        with self.assertRaises(AnalysisOutputError):
            self.parse()

    def test_standard_json_fence_supported_without_unbounded_text_extraction(self):
        raw = json.dumps(self.output)
        result = parse_analysis_output('```json\n' + raw + '\n```', self.payload)
        self.assertEqual(len(result['findings']), 1)
        with self.assertRaises(AnalysisOutputError):
            parse_analysis_output('I will do this ' + raw, self.payload)

    def test_generation_passes_only_safe_payload_and_checks_task_after_call(self):
        transport = Mock(return_value=json.dumps(self.output))
        active = Mock()
        with patch.dict(sys.modules, {'project_knowledge.llm': SimpleNamespace(stream_call=transport)}):
            result = generate_analysis(7, self.payload, active, lambda: 120)
        args = transport.call_args.kwargs
        self.assertEqual(args['model_config_id'], 7)
        self.assertNotIn('secret-', args['messages'][1]['content'])
        active.assert_called_once()
        self.assertEqual(result['assessment']['status'], 'not_configured')

    def test_stream_output_budget_aborts_before_unbounded_collection(self):
        def transport(**kwargs):
            kwargs['on_chunk']('a' * MAX_OUTPUT_CHARS)
            kwargs['on_chunk']('a')
        with patch.dict(sys.modules, {'project_knowledge.llm': SimpleNamespace(stream_call=transport)}):
            with self.assertRaises(AnalysisOutputError):
                generate_analysis(7, self.payload, lambda: None, 120)

    def test_real_stream_adapter_preserves_our_output_limit_error_without_replay(self):
        calls = []

        def chunks(*args, **kwargs):
            calls.append(True)
            yield 'a' * MAX_OUTPUT_CHARS
            yield 'a'

        manager = SimpleNamespace(config={}, current_llm=SimpleNamespace(stream=chunks))
        stub = SimpleNamespace(DEFAULT_LLM_TIMEOUT=60, get_llm_manager=lambda **kwargs: manager)
        path = Path(__file__).resolve().parents[2] / 'project_knowledge' / 'llm.py'
        spec = importlib.util.spec_from_file_location('_analysis_stream_test', path)
        transport = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, {'ai_core.model_manager': stub}):
            spec.loader.exec_module(transport)
        with patch.dict(sys.modules, {'project_knowledge.llm': transport}):
            with self.assertRaises(AnalysisOutputError):
                generate_analysis(7, self.payload, lambda: True, 120)
        self.assertEqual(len(calls), 1)

    def test_stream_wrapped_soft_timeout_remains_a_timeout(self):
        def transport(**kwargs):
            try:
                raise SoftTimeLimitExceeded()
            except SoftTimeLimitExceeded as exc:
                raise RuntimeError('wrapped stream') from exc
        with patch.dict(sys.modules, {'project_knowledge.llm': SimpleNamespace(stream_call=transport)}):
            with self.assertRaises(SoftTimeLimitExceeded):
                generate_analysis(7, self.payload, lambda: None, 120)
