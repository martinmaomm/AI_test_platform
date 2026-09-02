"""Scenario-owned safety policy without page-language heuristics."""

from __future__ import annotations

import re
from dataclasses import dataclass
from uuid import uuid4

from .generation_contracts import ScenarioPlan

_READ_ONLY_RE = re.compile(
    r'(?:只查看|仅查看|只读|禁止(?:任何)?写入|不得(?:进行)?写(?:入|操作)|'
    r'不允许(?:进行)?写(?:入|操作)|read[ -]?only)',
    re.I,
)


@dataclass
class ExplorationPolicy:
    namespace: str
    data_scope: str
    explicit_read_only: bool
    allow_test_data_writes: bool
    cleanup_expected: bool

    @classmethod
    def for_plan(cls, plan: ScenarioPlan, *, generation_id: str | None, user_constraints: str | None) -> 'ExplorationPolicy':
        stable_id = re.sub(r'[^a-zA-Z0-9-]', '', str(generation_id or 'local'))[:64] or 'local'
        read_only = any(_READ_ONLY_RE.search(item) for item in [str(user_constraints or ''), *plan.forbidden_actions])
        return cls(
            namespace=f'aits-explore-{stable_id}-{uuid4().hex[:12]}',
            data_scope='scenario_namespace',
            explicit_read_only=read_only,
            allow_test_data_writes=bool(plan.allow_test_data_writes and not read_only),
            cleanup_expected=bool(plan.cleanup_expected and not read_only),
        )

    @classmethod
    def read_only(cls) -> 'ExplorationPolicy':
        return cls('aits-explore-local', 'scenario_namespace', True, False, False)

    def may_write(self) -> bool:
        return self.allow_test_data_writes

    def prompt_scope(self) -> dict[str, object]:
        return {
            'exploration_namespace': self.namespace,
            'data_scope': self.data_scope,
            'allow_test_data_writes': self.may_write(),
            'cleanup_expected': self.cleanup_expected,
            'explicit_read_only': self.explicit_read_only,
            'model_execution_requirements': [
                'Execute the whole scenario continuously; do not restart from the start path between instructions.',
                'Use only the supplied namespace for any test data and do not touch existing records.',
                'After any possible write, observe the result before another action; unknown results stop exploration.',
            ],
        }
