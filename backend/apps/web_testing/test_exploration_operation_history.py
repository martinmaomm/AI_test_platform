"""Offline contracts for literal-preserving, bounded operation history."""

from copy import deepcopy
import json
from unittest import TestCase

from .exploration_operation_history import compact_operation_history


def event(number, *, action='click', status='succeeded', locator=None):
    return {
        'event_id': f'E{number:06d}', 'tool_name': f'playwright_{action}',
        'action': action, 'status': status,
        'locator_input': locator if locator is not None else {'selector': 'button:has-text("确认")'},
    }


def wire(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'))


class ExplorationOperationHistoryTests(TestCase):
    def test_keeps_separate_consecutive_confirmations_and_input_order(self):
        # Sequence numbers deliberately disagree with list order: never sort or deduplicate.
        events = [event(3), event(1), event(2, action='fill', locator={
            'selector': '.dialog input[name="名称"]', 'input_value': '运行测试样本',
        }), event(4)]
        result = compact_operation_history(events)
        self.assertEqual([item['event_id'] for item in result['actions']],
                         ['E000003', 'E000001', 'E000002', 'E000004'])
        self.assertEqual(result['actions'][0]['locator_input'], result['actions'][1]['locator_input'])
        self.assertEqual(result['actions'][2]['locator_input'], events[2]['locator_input'])
        self.assertEqual(result['omitted_count'], 0)
        self.assertIs(result['truncated'], False)

    def test_excludes_failed_blocked_and_observe_without_counting_them_as_truncation(self):
        events = [event(1, status='failed'), event(2, action='observe'),
                  event(3, status='blocked'), event(4, action='navigate'), event(5, action='screenshot')]
        result = compact_operation_history(events)
        self.assertEqual([item['event_id'] for item in result['actions']], ['E000004', 'E000005'])
        self.assertEqual(result['omitted_count'], 0)
        self.assertIs(result['truncated'], False)

    def test_no_observations_outputs_or_navigation_input_inference(self):
        source = event(1, action='navigate', locator={'selector': '#original', 'role': 'button'})
        source.update({
            'page_context': {'page_url': 'https://observed.invalid/after-redirect',
                             'observation': {'html': '<private-page>'}},
            'relative_path': '/after-redirect', 'raw_output': '<raw-html>',
            'result_excerpt': 'private-excerpt', 'action_arguments': {'url': '/not-projected'},
        })
        result = compact_operation_history([source])
        self.assertEqual(result['actions'], [{
            'event_id': 'E000001', 'tool_name': 'playwright_navigate',
            'locator_input': {'selector': '#original'},
        }])
        for excluded in ('observed.invalid', 'private-page', 'raw_output', 'raw-html', 'private-excerpt'):
            self.assertNotIn(excluded, wire(result))
        self.assertIn('成功工具动作历史', result['note'])
        self.assertIn('不代表业务通过', result['note'])
        self.assertIn('不是当前页面现场', result['note'])

    def test_preserves_literal_whitespace_quotes_unicode_and_keyword_like_test_data(self):
        locator = {
            'selector': '  .dialog input[placeholder="名称\\\"测试"]\n >> nth=0  ',
            'input_value': '  password token secret 测试样本\\\n"原值"  ',
        }
        result = compact_operation_history([event(1, action='fill', locator=locator)])
        self.assertEqual(json.loads(wire(result))['actions'][0]['locator_input'], locator)

    def test_empty_and_filtered_only_histories_are_not_truncated(self):
        for events in ([], [event(1, status='failed'), event(2, action='observe')]):
            with self.subTest(events=events):
                result = compact_operation_history(events)
                self.assertEqual(set(result), {'note', 'actions', 'omitted_count', 'truncated'})
                self.assertEqual(result['actions'], [])
                self.assertEqual(result['omitted_count'], 0)
                self.assertIs(result['truncated'], False)

    def test_oversized_input_is_wholly_omitted_and_marked_but_selector_survives(self):
        source = event(1, action='fill', locator={'selector': '  #exact  ', 'input_value': '值' * 20000})
        result = compact_operation_history([source], max_chars=700)
        self.assertLessEqual(len(wire(result)), 700)
        self.assertEqual(result['actions'][0]['locator_input'], {'selector': '  #exact  '})
        self.assertEqual(result['actions'][0]['omitted_fields'], {'locator_input.input_value': 'max_chars'})
        self.assertEqual(result['omitted_count'], 0)
        self.assertIs(result['truncated'], True)
        self.assertEqual(source['locator_input']['input_value'], '值' * 20000)

    def test_oversized_selector_omits_whole_record_then_keeps_next_fitting_action(self):
        source = event(1, action='fill', locator={'selector': '#' + 'x' * 20000, 'input_value': 'exact'})
        result = compact_operation_history([source, event(2)], max_chars=700)
        self.assertLessEqual(len(wire(result)), 700)
        self.assertEqual([item['event_id'] for item in result['actions']], ['E000002'])
        self.assertEqual(result['actions'][0]['locator_input'], event(2)['locator_input'])
        self.assertEqual(result['omitted_count'], 1)
        self.assertIs(result['truncated'], True)

    def test_budget_prefers_earliest_complete_records_and_reports_omitted_tail(self):
        first_two = compact_operation_history([event(1), event(2)])
        budget = len(wire(first_two))
        events = [event(number) for number in range(1, 10)] + [event(10, action='observe')]
        result = compact_operation_history(events, max_chars=budget)
        self.assertEqual(result['actions'], first_two['actions'])
        self.assertEqual(result['omitted_count'], 7)
        self.assertIs(result['truncated'], True)
        self.assertLessEqual(len(wire(result)), budget)

    def test_exact_compact_json_character_boundary_includes_escaping_not_utf8_bytes(self):
        events = [event(1, action='fill', locator={'selector': '#名称\\"引号', 'input_value': '中文\n"\\' * 50})]
        full = compact_operation_history(events)
        budget = len(wire(full))
        self.assertGreater(len(wire(full).encode('utf-8')), budget)
        self.assertEqual(compact_operation_history(events, max_chars=budget), full)
        reduced = compact_operation_history(events, max_chars=budget - 1)
        self.assertLessEqual(len(wire(reduced)), budget - 1)
        self.assertNotIn('input_value', reduced['actions'][0]['locator_input'])
        self.assertEqual(reduced['actions'][0]['locator_input']['selector'], events[0]['locator_input']['selector'])
        self.assertIn('locator_input.input_value', reduced['actions'][0]['omitted_fields'])

    def test_budget_bound_holds_across_count_digit_changes(self):
        events = [event(number) for number in range(1, 121)]
        for budget in (300, 400, 600, 1200, 12000):
            with self.subTest(budget=budget):
                result = compact_operation_history(events, max_chars=budget)
                self.assertLessEqual(len(wire(result)), budget)
                self.assertEqual(result['omitted_count'], 120 - len(result['actions']))
                self.assertEqual(result['actions'], compact_operation_history(events[:len(result['actions'])])['actions'])

    def test_invalid_or_too_small_budget_raises_instead_of_exceeding_limit(self):
        for budget in (-1, 0, 1, True, 12000.0, '12000', None):
            with self.subTest(budget=budget), self.assertRaises(ValueError):
                compact_operation_history([], max_chars=budget)

    def test_result_has_no_mutable_aliases_and_source_data_is_unchanged(self):
        events = [event(1, action='select', locator={
            'selector': '#multiple', 'input_value': ['选项一', {'nested': ['原值']}],
        }), event(2, action='fill', locator={'selector': '#long', 'input_value': 'x' * 20000})]
        before = deepcopy(events)
        result = compact_operation_history(events, max_chars=900)
        self.assertEqual(events, before)
        result['actions'][0]['locator_input']['input_value'][1]['nested'].append('修改返回值')
        result['actions'][0]['locator_input']['selector'] = '#changed'
        self.assertEqual(events, before)

    def test_falsey_json_input_values_are_retained_exactly(self):
        for value in ('', None, False, 0, [], {}):
            with self.subTest(value=value):
                result = compact_operation_history([event(1, action='fill', locator={'input_value': value})])
                self.assertEqual(result['actions'][0]['locator_input'], {'input_value': value})
