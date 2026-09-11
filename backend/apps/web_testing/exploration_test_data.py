"""Deterministic run-local test data; no model-supplied expression is executed."""

import ast
import keyword
import time
from copy import deepcopy


class ExplorationTestData:
    def __init__(self):
        self._values = {}
        self._last_ns = 0

    @staticmethod
    def valid_name(name):
        return (isinstance(name, str) and name.isidentifier() and not keyword.iskeyword(name)
                and not name.startswith('_') and len(name) <= 64
                and name not in {'page', 'variables', 'time', 'run', 'expect', 'str'})

    def generate(self, name, prefix='', suffix=''):
        if not self.valid_name(name):
            raise ValueError('name 必须是普通 Python 变量名，不能覆盖 page、variables、time 等运行标识。')
        if not all(isinstance(v, str) and len(v) <= 120 for v in (prefix, suffix)):
            raise ValueError('prefix 和 suffix 必须是不超过 120 字符的固定文字。')
        existing = self._values.get(name)
        if existing:
            if (existing['prefix'], existing['suffix']) != (prefix, suffix):
                raise ValueError('该变量本轮已生成；请沿用已有值，或为新数据使用另一个变量名。')
            return deepcopy(existing)
        if len(self._values) >= 50:
            raise ValueError('本轮动态测试变量已达到 50 项上限。')
        self._last_ns = max(time.time_ns(), self._last_ns + 1)
        expression = f'{prefix!r} + str(time.time_ns()) + {suffix!r}'
        item = {
            'name': name, 'prefix': prefix, 'suffix': suffix,
            'timestamp_ns': str(self._last_ns),
            'value': f'{prefix}{self._last_ns}{suffix}',
            'python_assignment': f'{name} = {expression}',
        }
        self._values[name] = item
        return deepcopy(item)

    def snapshot(self):
        return deepcopy(self._values)

    def restore(self, data):
        # Internal checkpoint data, not a request to regenerate old inputs.
        if not isinstance(data, dict):
            return
        for name, item in list(data.items())[:50]:
            if not isinstance(item, dict) or item.get('name') != name:
                continue
            ns = item.get('timestamp_ns', '')
            prefix, suffix = item.get('prefix'), item.get('suffix')
            if (not isinstance(ns, str) or not ns.isdecimal() or not 16 <= len(ns) <= 24
                    or not self.valid_name(name)
                    or not all(isinstance(v, str) and len(v) <= 120 for v in (prefix, suffix))):
                continue
            self._values[name] = {
                'name': name, 'prefix': prefix, 'suffix': suffix, 'timestamp_ns': ns,
                'value': prefix + ns + suffix,
                'python_assignment': f'{name} = {prefix!r} + str(time.time_ns()) + {suffix!r}',
            }
            self._last_ns = max(self._last_ns, int(ns))

    def fixed_sample_variables(self, code):
        """Detect known exploration timestamps embedded in executable literals."""
        try:
            tree = ast.parse(code)
        except (SyntaxError, ValueError):
            return []  # Existing Python-quality checks own syntax errors.
        docstrings = {
            id(node.body[0].value)
            for node in ast.walk(tree)
            if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
            and node.body and isinstance(node.body[0], ast.Expr)
            and isinstance(node.body[0].value, ast.Constant)
        }
        literals = [str(node.value) for node in ast.walk(tree)
                    if isinstance(node, ast.Constant) and id(node) not in docstrings
                    and isinstance(node.value, (str, int))]
        return [item['name'] for item in self._values.values()
                if any(item['timestamp_ns'] in value for value in literals)]
