"""Goal-owned exploration safety policy.

This module intentionally never classifies a page control from its visible
text. The browser protocol cannot prove a database operation from a generic
click, so potential writes are attributed only to the active Goal metadata.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from uuid import uuid4

from .generation_contracts import Goal, GoalPlan

_READ_ONLY_RE = re.compile(r'(?:只查看|只读|禁止(?:任何)?写入|不得(?:进行)?写(?:入|操作)|不允许(?:进行)?写(?:入|操作))', re.I)


@dataclass
class ExplorationPolicy:
    namespace: str
    data_scope: str
    explicit_read_only: bool
    goals: dict[str, Goal]
    active_goal_id: str = ''

    @classmethod
    def for_plan(cls, plan: GoalPlan, *, generation_id: str | None, user_constraints: str | None) -> 'ExplorationPolicy':
        stable_id = re.sub(r'[^a-zA-Z0-9-]', '', str(generation_id or 'local'))[:64] or 'local'
        constraints = [str(user_constraints or ''), *plan.forbidden_actions]
        read_only = any(_READ_ONLY_RE.search(item) for item in constraints)
        return cls(
            namespace=f'aits-explore-{stable_id}-{uuid4().hex[:12]}',
            data_scope='goal_scoped',
            explicit_read_only=read_only,
            goals={goal.id: goal for goal in plan.goals},
        )

    @classmethod
    def read_only(cls) -> 'ExplorationPolicy':
        return cls('aits-explore-local', 'goal_scoped', True, {})

    def set_active_goal(self, goal_id: str) -> None:
        if goal_id not in self.goals:
            raise ValueError('探索策略收到未知 Goal')
        self.active_goal_id = goal_id

    @property
    def active_goal(self) -> Goal | None:
        return self.goals.get(self.active_goal_id)

    def current_goal_may_write(self) -> bool:
        goal = self.active_goal
        if self.explicit_read_only or goal is None:
            return False
        # ``external`` and ``unknown`` are not granted browser write authority.
        # Cleanup is allowed only because it explicitly references a test-data
        # Goal in the validated plan.
        return goal.side_effect == 'test_data' or (
            goal.kind == 'cleanup' and bool(goal.cleanup_for_goal_ids)
        )

    def prompt_scope(self) -> dict[str, object]:
        goal = self.active_goal
        return {
            'exploration_namespace': self.namespace,
            'data_scope': self.data_scope,
            'active_goal_id': self.active_goal_id,
            'active_goal_side_effect': goal.side_effect if goal else 'none',
            'active_goal_may_write': self.current_goal_may_write(),
            'explicit_read_only': self.explicit_read_only,
            'model_execution_requirements': [
                'Only operate within the active Goal. Do not infer a goal from button text.',
                'A Goal without test_data side effects is observation-only.',
                'Use only the supplied namespace for any test data and do not touch existing records.',
                'After any possible write, observe the result before another action; unknown results stop exploration.',
            ],
        }
