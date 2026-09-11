import asyncio
from unittest.mock import Mock, patch

from django.test import SimpleTestCase

from .exploration_test_data import ExplorationTestData
from .script_exploration_agent import ScriptExplorationAgent, ScriptExplorationAgentError


class ExplorationTestDataTests(SimpleTestCase):
    def test_real_clock_idempotence_and_new_values(self):
        data = ExplorationTestData()
        with patch('web_testing.exploration_test_data.time.time_ns', return_value=1789100000123456789) as clock:
            first = data.generate('record_name', 'record_', '')
            self.assertEqual(first, data.generate('record_name', 'record_', ''))
            second = data.generate('edited_name', 'record_', '')
        self.assertEqual(clock.call_count, 2)
        self.assertEqual(first['value'], 'record_1789100000123456789')
        self.assertNotEqual(first['value'], second['value'])
        with self.assertRaises(ValueError):
            data.generate('record_name', 'changed_')

    def test_assignment_regenerates_on_every_replay_without_eval_of_model_code(self):
        data = ExplorationTestData()
        value = data.generate('record_name', "a'\\\n", '末尾')
        namespace = {'time': Mock()}
        namespace['time'].time_ns.side_effect = [111, 222]
        # Only platform-constructed code is executed, never a model expression.
        exec(value['python_assignment'], namespace)
        self.assertEqual(namespace['record_name'], "a'\\\n111末尾")
        exec(value['python_assignment'], namespace)
        self.assertEqual(namespace['record_name'], "a'\\\n222末尾")
        self.assertNotIn(value['timestamp_ns'], value['python_assignment'])

    def test_snapshot_restores_values_not_regenerating_existing_written_data(self):
        first = ExplorationTestData()
        item = first.generate('entity')
        second = ExplorationTestData()
        second.restore(first.snapshot())
        self.assertEqual(item, second.generate('entity'))
        self.assertNotEqual(item['value'], second.generate('next_entity')['value'])
        self.assertNotEqual(item['value'], ExplorationTestData().generate('entity')['value'])

    def test_rejects_injection_reserved_names_and_inconsistent_requests(self):
        data = ExplorationTestData()
        for name in ('x;print(1)', '_private', 'time', 'page', 'class', 'variables'):
            with self.subTest(name=name), self.assertRaises(ValueError):
                data.generate(name)
        with self.assertRaises(ValueError):
            data.generate('value', 'x' * 121)
        for number in range(50):
            data.generate(f'value{number}')
        with self.assertRaises(ValueError):
            data.generate('overflow')

    def test_known_literals_detected_but_comments_and_docstrings_are_not_code(self):
        data = ExplorationTestData()
        item = data.generate('record', 'entity_')
        self.assertEqual(data.fixed_sample_variables(f'record = {item["value"]!r}'), ['record'])
        self.assertEqual(data.fixed_sample_variables(f'record = {item["timestamp_ns"]}'), ['record'])
        self.assertEqual(data.fixed_sample_variables(item['python_assignment']), [])
        self.assertEqual(data.fixed_sample_variables(f'"""Sample {item["value"]}"""\n# {item["value"]}\npass'), [])
        self.assertEqual(data.fixed_sample_variables('not valid python'), [])

    def agent(self, callback=None):
        return ScriptExplorationAgent(Mock(), {}, None, lambda: False, 10, callback)

    def test_value_checkpoint_before_return_and_prompt_recovery(self):
        checkpoints = []
        agent = self.agent(checkpoints.append)
        value = asyncio.run(agent._unique_value_tool().ainvoke({'name': 'entity', 'prefix': 'new_'}))
        self.assertEqual(value['status'], 'ready')
        self.assertEqual(checkpoints[-1]['snapshot']['generated_test_data']['entity']['value'], value['value'])
        self.assertIn(value['value'], agent._prompt())
        self.assertEqual(agent._runtime_factory([]).checkpoint()['generated_test_data']['entity']['value'], value['value'])
        restored = self.agent()
        restored._restore_snapshot(checkpoints[-1]['snapshot'])
        reused = asyncio.run(restored._unique_value_tool().ainvoke({'name': 'entity', 'prefix': 'new_'}))
        self.assertEqual(value['value'], reused['value'])

    def test_checkpoint_failure_does_not_expose_writable_value(self):
        agent = self.agent(lambda _: False)
        with self.assertRaises(ScriptExplorationAgentError):
            asyncio.run(agent._unique_value_tool().ainvoke({'name': 'entity'}))

    def test_saved_script_cannot_freeze_platform_generated_sample(self):
        agent = self.agent()
        value = asyncio.run(agent._unique_value_tool().ainvoke({'name': 'entity'}))
        old_script = agent._last_valid_script
        result = agent._consider_candidate(
            f'async def run(page, variables):\n    await page.fill("input", {value["value"]!r})',
            completed_steps=[], remaining_steps=[], variables=[], completion='complete', source='save_tool',
        )
        self.assertEqual(result['error_code'], 'FIXED_EXPLORATION_SAMPLE')
        self.assertEqual(agent._last_valid_script, old_script)
        self.assertNotIn(value['value'], str(result))
