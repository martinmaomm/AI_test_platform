"""Scoped write policy for target-driven WebUI exploration.

The browser protocol cannot prove which database row a generic click affects.
This policy therefore combines the narrow protections available at the tool
boundary with instructions and an auditable result contract for page-specific
scope decisions.  It deliberately does not claim browser-level sandboxing.
"""

from __future__ import annotations

import re
from uuid import uuid4
from dataclasses import dataclass


CRUD_OPERATIONS = frozenset({'create', 'update', 'delete'})
_READ_ONLY_RE = re.compile(
    r'(?:只查看|只读|禁止(?:任何)?写入|禁止(?:任何)?修改|不得(?:进行)?写(?:入|操作)|不允许(?:进行)?写(?:入|操作))',
    re.IGNORECASE,
)
_SUBMISSION_PROHIBITED_RE = re.compile(
    r'(?:不(?:要|得|允许)?|禁止)\s*提交(?:表单|新增|编辑|修改|更新|删除)?|'
    r'不提交\s*(?:新增|编辑|修改|更新|删除)',
    re.IGNORECASE,
)
_EXISTING_DATA_RE = re.compile(r'(?:已有|现有|existing)\s*(?:数据|记录|用户|订单|条目|item)', re.IGNORECASE)
_EXPLICIT_IDENTIFIER_RE = re.compile(
    r'(?:\b(?:id|ID)\b|编号)\s*(?:=|:|：|为|是)\s*[a-zA-Z0-9_-]+',
)
_NEGATED_MUTATION_RE = re.compile(
    r'(?:不要|禁止|不得|不允许|勿|不能)\s*(?:新增|创建|添加|编辑|修改|更新|删除|移除|create|add|edit|update|delete|remove)',
    re.IGNORECASE,
)
_OPERATION_MARKERS = {
    'create': ('新增', '创建', '添加', 'create', 'add', 'new'),
    'update': ('编辑', '修改', '更新', 'update', 'edit'),
    'delete': ('删除', '移除', 'delete', 'remove'),
}


def _operations_named_in(text: str) -> set[str]:
    lowered = str(text or '').lower()
    return {
        operation for operation, markers in _OPERATION_MARKERS.items()
        if any(marker.lower() in lowered for marker in markers)
    }


def _explicit_read_only(user_constraints: str, forbidden_actions: list[str]) -> bool:
    for value in [str(user_constraints or ''), *forbidden_actions]:
        for matcher in (_READ_ONLY_RE, _SUBMISSION_PROHIBITED_RE):
            for match in matcher.finditer(value):
                phrase = match.group(0)
                # "只读" and "只查看" state the exploration mode itself. A
                # later existing-data caveat cannot weaken that direct mode.
                if '只读' in phrase or '只查看' in phrase:
                    return True
                local_tail = re.split(r'[，,。；;！!\n]', value[match.start():], maxsplit=1)[0]
                if not _EXISTING_DATA_RE.search(local_tail):
                    return True
    return False


def _globally_forbidden_operations(forbidden_actions: list[str]) -> set[str]:
    """Keep a ban on existing records scoped to those records, not the task."""
    forbidden: set[str] = set()
    for item in forbidden_actions:
        lowered = item.lower()
        names = _operations_named_in(item)
        refers_to_existing = any(marker in lowered for marker in ('已有', '现有', 'existing'))
        if not refers_to_existing:
            forbidden.update(names)
    return forbidden


def _has_positive_user_specified_existing_scope(user_constraints: str) -> bool:
    """Allow existing data only for an affirmative, identifier-bounded request."""
    text = str(user_constraints or '')
    if not _EXPLICIT_IDENTIFIER_RE.search(text) or _NEGATED_MUTATION_RE.search(text):
        return False
    return bool(_operations_named_in(text))


@dataclass(frozen=True)
class ExplorationPolicy:
    """One task's explicit CRUD scope, derived from user-owned inputs only."""

    namespace: str
    data_scope: str
    allowed_operations: frozenset[str]
    forbidden_operations: frozenset[str]
    explicit_read_only: bool

    @classmethod
    def for_scenario(cls, scenario, *, generation_id: str | None, user_constraints: str | None) -> 'ExplorationPolicy':
        raw_constraints = str(user_constraints or '')
        forbidden_actions = [str(item) for item in scenario.forbidden_actions]
        requested = {
            step.intent for step in scenario.steps
            if step.intent in CRUD_OPERATIONS
        }
        # A normalizer may label a step mutating without naming the exact
        # operation.  Do not invent a capability in that case.
        # A declared cleanup is part of a CRUD scenario's lifecycle.  It must
        # be able to delete this attempt's own namespace data even for a
        # create-only target.
        if requested and scenario.cleanup:
            requested.add('delete')
        forbidden = _globally_forbidden_operations(forbidden_actions)
        read_only = _explicit_read_only(raw_constraints, forbidden_actions)
        allowed = set() if read_only else requested - forbidden
        stable_id = re.sub(r'[^a-zA-Z0-9-]', '', str(generation_id or 'local'))[:64] or 'local'
        return cls(
            namespace=f'aits-explore-{stable_id}-{uuid4().hex[:12]}',
            data_scope='user_specified' if _has_positive_user_specified_existing_scope(raw_constraints) else 'namespace',
            allowed_operations=frozenset(allowed),
            forbidden_operations=frozenset(forbidden),
            explicit_read_only=read_only,
        )

    @classmethod
    def read_only(cls) -> 'ExplorationPolicy':
        return cls(
            namespace='aits-explore-local', data_scope='namespace',
            allowed_operations=frozenset(), forbidden_operations=frozenset(CRUD_OPERATIONS),
            explicit_read_only=True,
        )

    def operation_from_tool_input(self, input_text: str) -> str | None:
        named = _operations_named_in(input_text)
        return next((operation for operation in ('create', 'update', 'delete') if operation in named), None)

    def allows(self, operation: str | None) -> bool:
        if self.explicit_read_only:
            return False
        if operation is None:
            return bool(self.allowed_operations)
        return operation in self.allowed_operations

    def prompt_scope(self) -> dict[str, object]:
        return {
            'exploration_namespace': self.namespace,
            'data_scope': self.data_scope,
            'allowed_crud_operations': sorted(self.allowed_operations),
            'explicit_read_only': self.explicit_read_only,
            'tool_guard_limits': [
                'The guard always blocks evaluate, upload_file, and close.',
                'The guard blocks recognisable forbidden submit controls; only submit tools that pass the guard are counted as potential writes.',
                'Generic browser clicks cannot prove database-row ownership; this is not a complete website sandbox.',
            ],
            'model_execution_requirements': [
                'Use only the supplied namespace for created or edited data unless data_scope is user_specified.',
                'Never select, edit, or delete existing data outside the user-specified scope.',
                'After a submit, observe the result before another action. If the result is unknown, do not retry it.',
                'Attempt the declared cleanup before ending and observe its visible result; the platform records the tool trace automatically.',
            ],
        }
