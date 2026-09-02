"""v4 script generation is deterministic compilation, not locator generation."""

from __future__ import annotations

from .generation_contracts import ScenarioPlan
from .exploration_trace import ExplorationTrace
from .replay_plan import PythonReplayCompiler, ReplayPlan, ReplayPlanner


class ScriptGeneratorOutputError(ValueError):
    pass


class ScriptGenerator:
    """Compatibility-shaped façade for the orchestration boundary.

    ``llm_model`` is accepted only because the surrounding pipeline owns a
    locked model. It is deliberately not consulted for actions or locators.
    """
    def __init__(self, llm_model=None):
        self.llm_model = llm_model

    def generate(self, *, plan: ScenarioPlan, trace: ExplorationTrace) -> tuple[str, ReplayPlan]:
        replay_plan = ReplayPlanner.build(plan, trace)
        return PythonReplayCompiler.compile(plan, trace, replay_plan), replay_plan
