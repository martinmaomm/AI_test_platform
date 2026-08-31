"""Bounded, fact-preserving decoding for explorer output only."""

from __future__ import annotations

import json
import re
from typing import Any

from langchain_core.messages import BaseMessage


MAX_OUTPUT_CHARS = 500_000
MAX_REPAIR_CHARS = 128_000
MAX_DEPTH = 64
MAX_TOKENS = 30_000
_SCALAR = re.compile(r'true\b|false\b|null\b|-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?')
_SCRIPT = re.compile(r'(?im)^\s*(?:async\s+def\b|def\s+\w+|from\s+\w+\s+import\b|import\s+\w+|(?:const|let|var)\s+\w+\s*=|function\b|<script\b)')
_OPEN_FENCE = re.compile(r'(?:^|\n)[ \t]*```(?:json)?[ \t]*\n?\s*$', re.I)
_CLOSE_FENCE = re.compile(r'^\s*```[ \t]*(?:\n|$)')


class ExplorationOutputError(ValueError):
    """Contains safe diagnostics; candidate data must never be logged."""

    def __init__(self, kind: str, *, offset: int | None = None, repair_payload: dict | None = None):
        super().__init__(kind)
        self.kind = kind
        self.offset = offset
        self.repair_payload = repair_payload


def output_text(value: Any) -> str:
    if value is None:
        return ''
    if isinstance(value, BaseMessage):
        value = value.content
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts = []
        for part in value:
            if isinstance(part, str):
                parts.append(part)
            elif isinstance(part, dict) and part.get('type') == 'text' and isinstance(part.get('text'), str):
                parts.append(part['text'])
            else:
                raise ExplorationOutputError('unsupported_type')
        return ''.join(parts)
    raise ExplorationOutputError('unsupported_type')


def output_summary(value: Any) -> tuple[str, int]:
    kind = 'message' if isinstance(value, BaseMessage) else (
        'str' if isinstance(value, str) else 'content_blocks' if isinstance(value, list) else 'unsupported'
    )
    try:
        return kind, len(output_text(value))
    except ExplorationOutputError:
        return kind, 0


def _object_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ExplorationOutputError('duplicate_keys')
        result[key] = value
    return result


def _reject_constant(_value):
    raise ExplorationOutputError('invalid_constant')


def _wrapper_is_prose(text: str) -> bool:
    # Conservative: never skip JSON-looking fragments to recover an inner object.
    # Ignore English contraction apostrophes only in this wrapper check.
    prose = re.sub(r"(?<=[A-Za-z])'(?=[A-Za-z])", '', text)
    return not (
        re.search(r'[{}\[\]"\'`=;]', prose)
        or _SCRIPT.search(text)
        or re.fullmatch(r'\s*(?:null|true|false|-?[0-9.]+)\s*', text)
    )


def extract_object_text(value: Any) -> str:
    text = output_text(value).strip().lstrip('\ufeff').strip()
    if not text or text == 'No output generated':
        raise ExplorationOutputError('empty')
    if len(text) > MAX_OUTPUT_CHARS:
        raise ExplorationOutputError('too_large')
    first = re.search(r'[\[{]', text)
    if first is None:
        raise ExplorationOutputError('script' if _SCRIPT.search(text) or '```python' in text.lower() else 'no_object')
    start = first.start()
    prefix = text[:start]
    if _SCRIPT.search(prefix) or re.search(r'```(?:python|javascript|js|typescript|ts)\b', prefix, re.I):
        raise ExplorationOutputError('script')
    fence = _OPEN_FENCE.search(prefix)
    if fence:
        prefix = prefix[:fence.start()]
    if not _wrapper_is_prose(prefix):
        raise ExplorationOutputError('invalid_wrapper')
    if text[start] != '{':
        raise ExplorationOutputError('non_object')

    stack = []
    quoted = escaped = False
    end = None
    for index in range(start, len(text)):
        char = text[index]
        if quoted:
            if escaped:
                escaped = False
            elif char == '\\':
                escaped = True
            elif char == '"':
                quoted = False
            continue
        if char == '"':
            quoted = True
        elif char in '{[':
            stack.append(char)
            if len(stack) > MAX_DEPTH:
                raise ExplorationOutputError('too_deep', offset=index)
        elif char in '}]':
            if not stack or stack.pop() != ('{' if char == '}' else '['):
                raise ExplorationOutputError('damaged_structure', offset=index)
            if not stack:
                end = index + 1
                break
    if end is None:
        raise ExplorationOutputError('truncated', offset=len(text))
    suffix = text[end:]
    if fence:
        closing = _CLOSE_FENCE.match(suffix)
        if not closing:
            raise ExplorationOutputError('invalid_wrapper', offset=end)
        suffix = suffix[closing.end():]
    if not _wrapper_is_prose(suffix):
        raise ExplorationOutputError('ambiguous_output', offset=end)
    return text[start:end]


def semantic_tokens(candidate: str) -> tuple:
    """Only comma/colon/JSON whitespace may disappear; every other token is kept."""
    if len(candidate) > MAX_REPAIR_CHARS:
        raise ExplorationOutputError('repair_too_large')
    tokens = []
    decoder = json.JSONDecoder()
    index = 0
    while index < len(candidate):
        char = candidate[index]
        if char in ' \t\r\n,:':
            index += 1
            continue
        if char in '{}[]':
            tokens.append((char, char))
            index += 1
        elif char == '"':
            try:
                value, end = decoder.raw_decode(candidate, index)
            except (ValueError, RecursionError):
                raise ExplorationOutputError('unsafe_tokens', offset=index) from None
            tokens.append(('string', value))
            index = end
        else:
            match = _SCALAR.match(candidate, index)
            if not match:
                raise ExplorationOutputError('unsafe_tokens', offset=index)
            raw = match.group()
            index = match.end()
            # Do not reinterpret a damaged number/identifier as several new values.
            if index < len(candidate) and candidate[index] not in ' \t\r\n,:{}[]"':
                raise ExplorationOutputError('unsafe_tokens', offset=index)
            tokens.append(('scalar', raw))
        if len(tokens) > MAX_TOKENS:
            raise ExplorationOutputError('too_many_tokens')
    return tuple(tokens)


def _payload_without_separators(tokens: tuple) -> dict:
    """Prove that fixing separators alone yields one unambiguous, complete object."""
    index = 0

    def value(depth=0):
        nonlocal index
        if depth > MAX_DEPTH or index >= len(tokens):
            raise ExplorationOutputError('incomplete_values')
        kind, raw = tokens[index]
        index += 1
        if kind == '{':
            result = {}
            while index < len(tokens) and tokens[index][0] != '}':
                key_kind, key = tokens[index]
                index += 1
                if key_kind != 'string' or key in result:
                    raise ExplorationOutputError('ambiguous_keys')
                result[key] = value(depth + 1)
            if index >= len(tokens):
                raise ExplorationOutputError('incomplete_values')
            index += 1
            return result
        if kind == '[':
            result = []
            while index < len(tokens) and tokens[index][0] != ']':
                result.append(value(depth + 1))
            if index >= len(tokens):
                raise ExplorationOutputError('incomplete_values')
            index += 1
            return result
        if kind == 'string':
            return raw
        if kind == 'scalar':
            return json.loads(raw)
        raise ExplorationOutputError('incomplete_values')

    result = value()
    if index != len(tokens) or not isinstance(result, dict):
        raise ExplorationOutputError('ambiguous_output')
    return result


def parse_exploration_output(value: Any) -> dict:
    candidate = extract_object_text(value)
    try:
        return json.loads(candidate, object_pairs_hook=_object_pairs, parse_constant=_reject_constant)
    except json.JSONDecodeError as exc:
        try:
            tokens = semantic_tokens(candidate)
            payload = _payload_without_separators(tokens)
        except ExplorationOutputError:
            raise ExplorationOutputError('invalid_format', offset=exc.pos) from None
        raise ExplorationOutputError(
            'invalid_format', offset=exc.pos, repair_payload=payload,
        ) from None
    except (ValueError, RecursionError) as exc:
        if isinstance(exc, ExplorationOutputError):
            raise
        raise ExplorationOutputError('invalid_format') from None
