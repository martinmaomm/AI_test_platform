"""Lossless extraction regression: model source must reach draft validation intact."""

import ast
from unittest import TestCase

from common.parsers import extract_python_from_output


SOURCE = '''\
"""场景：保存偏好并验证。"""
import time
from playwright.async_api import expect

async def run(page, variables):
    target_url = "https://example.test/preferences"
    unique_name = "record_" + str(time.time_ns())
    await page.goto(target_url)
    # 普通赋值和多行定位器都必须保留。
    field = page.locator(
        '[role="dialog"] '
        'input[data-field="title"]'
    )
    await field.fill(unique_name)
    # 验证：字段保存本轮唯一名称
    await expect(field).to_have_value(unique_name)
'''


class PythonOutputExtractionTests(TestCase):
    def test_unfenced_script_is_preserved_whole(self):
        actual = extract_python_from_output(SOURCE)
        self.assertEqual(actual, SOURCE.strip())
        ast.parse(actual)

    def test_explicit_fences_preserve_multiline_code(self):
        for language in ('python', 'py', 'Python'):
            with self.subTest(language=language):
                self.assertEqual(extract_python_from_output(f'以下是脚本：\n```{language}\n{SOURCE}```\n完成。'), SOURCE.strip())

    def test_generic_fence_requires_whole_python(self):
        self.assertEqual(extract_python_from_output(f'脚本：\n```\n{SOURCE}```'), SOURCE.strip())
        self.assertEqual(extract_python_from_output('```\nimport time\n这不是完整代码！\n```'), '')

    def test_fence_in_valid_docstring_cannot_replace_script(self):
        source = SOURCE.replace('场景：保存偏好并验证。', '说明：\n```python\nx = 1\n```\n仍然是文档。')
        self.assertEqual(extract_python_from_output(source), source.strip())

    def test_invalid_explicit_python_reaches_syntax_validator_unchanged(self):
        invalid = 'async def run(page, variables):\n    await page.locator(\n'
        self.assertEqual(extract_python_from_output(f'```python\n{invalid}```'), invalid.strip())

    def test_prose_is_not_stitched_into_keyword_lines(self):
        self.assertEqual(extract_python_from_output('脚本如下：\nimport time\n    value = 3\n后续需要完善。'), '')

    def test_different_language_is_not_guessed_as_python(self):
        self.assertEqual(extract_python_from_output('```javascript\nimport something\n```'), '')

    def test_last_explicit_candidate_wins_without_joining_blocks(self):
        self.assertEqual(extract_python_from_output('```python\nx = 1\n```\n修订：\n```python\nx = 2\n```'), 'x = 2')

    def test_empty_and_non_text_outputs(self):
        for value in ('', None, {}, '   '):
            with self.subTest(value=value):
                self.assertEqual(extract_python_from_output(value), '')
