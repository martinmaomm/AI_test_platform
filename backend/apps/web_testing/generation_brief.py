"""Local, lossless task brief; page semantics belong to the exploring agent."""

from dataclasses import asdict, dataclass, field

from .exploration_policy import _READ_ONLY_RE
from .generation_contracts import ScenarioInputInsufficientError


@dataclass(frozen=True)
class GenerationBrief:
    title: str
    objective: str
    original_user_target: str
    instructions: list[str]
    schema_version: int = 5
    explicit_read_only: bool = False
    allow_test_data_writes: bool = True
    # No pre-exploration guess about cleanup semantics. Login instructions stay
    # in the original description, not in a separate credential contract.
    cleanup_expected: bool = False
    cleanup_policy: str = '按用户目标决定，未明确要求时不额外推断'
    forbidden_actions: list[str] = field(default_factory=list)
    discovery_notes: list[str] = field(default_factory=list)

    def model_dump(self, *, mode: str = 'python') -> dict:
        return asdict(self)


def build_generation_brief(description: str, *, title: str = '') -> GenerationBrief:
    description = str(description or '').strip()
    if not description:
        raise ScenarioInputInsufficientError('scenario_target_missing')
    read_only = bool(_READ_ONLY_RE.search(description))
    return GenerationBrief(
        title=str(title or 'AI 生成的 WebUI 用例').strip()[:200],
        objective=description,
        original_user_target=description,
        # This is the original instruction, not a speculative executable plan.
        instructions=[description],
        explicit_read_only=read_only,
        allow_test_data_writes=not read_only,
    )
