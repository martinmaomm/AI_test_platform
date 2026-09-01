"""Callback-owned, bounded evidence ledger for WebUI MCP exploration.

The trace is deliberately independent from an agent's final answer.  It is
the only exploration artifact accepted by the v2 pipeline.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .generation_security import redact_text


TRACE_SCHEMA_VERSION = 2
_MAX_EVENTS = 120
_MAX_EXCERPT = 1200
_MAX_INPUT = 320
_MAX_GENERATOR_EVIDENCE_CHARS = 24_000
_MAX_GENERATOR_EVENT_RESULT = 320
_MAX_GENERATOR_EVENT_INPUT = 220
_MAX_GENERATOR_EVENT_LOCATORS = 8
_SECRET_KEY_RE = re.compile(r"(?i)(password|passwd|token|secret|cookie|authorization|api[_-]?key)")
_ABSOLUTE_URL_RE = re.compile(r"https?://[^\s'\"<>]+", re.I)
_OBSERVATION_TOOLS = frozenset({"playwright_get_visible_text", "playwright_get_visible_html", "playwright_snapshot"})
_INTERACTION_MARKERS = ("click", "fill", "select", "press", "check", "uncheck", "hover", "drag")
_OPERATION_INTENT_TOOL_MARKERS = ("click", "press", "check")
_RUNTIME_NAMESPACE_RE = re.compile(r"\baits-explore-[a-zA-Z0-9-]+-[a-f0-9]{12}\b")
_DYNAMIC_ELEMENT_ID_RE = re.compile(r"\bel-id[-_]?\d+\b|#el-id[-_]?\d+\b", re.I)
_BARE_VISIBLE_INPUT_RE = re.compile(r"^(?:input|textarea|select):visible$", re.I)
_NTH_SELECTOR_RE = re.compile(r"(?:\.nth\s*\(|:nth-(?:child|of-type)\s*\()", re.I)
_PYTHON_LOCATOR_RE = re.compile(
    r"^(?:page\.)?(?P<method>locator|get_by_role|get_by_text|get_by_label|get_by_placeholder|get_by_test_id)\(\s*['\"](?P<value>[^'\"]+)['\"](?P<kwargs>.*)\)$"
)
_INTENT_MARKERS = {
    "create": ("新增", "添加", "创建", "add", "create"),
    "update": ("编辑", "修改", "更新", "edit", "update"),
    "delete": ("删除", "移除", "delete", "remove"),
}
_GENERIC_STEP_TERMS = (
    "进入", "打开", "查看", "读取", "确认", "验证", "点击", "操作", "功能", "按钮",
    "页面", "系统", "表单", "提交", "请求", "流程", "加载", "跳转", "到达", "前往",
    "成功", "完成", "可见", "显示", "是否",
    "本轮", "测试", "数据", "结果", "清理", "获取", "探索", "信息", "结构", "名称", "字段",
)


def _relative_path(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if text.startswith(("http://", "https://")):
        parsed = urlsplit(text)
        return parsed.path or "/"
    if text.startswith("/"):
        return text.split("?", 1)[0].split("#", 1)[0] or "/"
    return ""


def _safe_text(value: Any, *, limit: int, sensitive_values: tuple[str, ...] = ()) -> str:
    text = str(value or "")
    for sensitive_value in sensitive_values:
        if sensitive_value:
            text = text.replace(sensitive_value, "<runtime_sensitive_data>")
    text = redact_text(text)
    text = _ABSOLUTE_URL_RE.sub(lambda item: _relative_path(item.group(0)) or "<url>", text)
    return re.sub(r"\s+", " ", text).strip()[:limit]


def _as_mapping(value: Any, fallback: str = "") -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    if fallback:
        try:
            parsed = json.loads(fallback)
            return dict(parsed) if isinstance(parsed, Mapping) else {}
        except (TypeError, ValueError):
            return {}
    return {}


def _output_text(value: Any) -> str:
    if hasattr(value, "content"):
        return _output_text(value.content)
    if isinstance(value, Mapping):
        return json.dumps(value, ensure_ascii=False, default=str)
    if isinstance(value, (list, tuple)):
        return " ".join(_output_text(item) for item in value)
    return str(value or "")


def _tool_failed(output: Any) -> bool:
    if isinstance(output, Mapping) and (
        output.get("error") or output.get("isError") or output.get("is_error")
        or output.get("status") == "error"
    ):
        return True
    if (
        getattr(output, "isError", False) or getattr(output, "is_error", False)
        or getattr(output, "status", None) == "error"
    ):
        return True
    text = _output_text(output).lower()
    return bool(re.search(r"operation failed|error executing tool|timeout .* exceeded|failed to|invalid .*selector|tool[ _-]?error", text))


def _locator_and_summary(
    inputs: Mapping[str, Any],
    tool_name: str,
    *,
    sensitive_values: tuple[str, ...] = (),
    runtime_namespace: str = "",
) -> tuple[dict[str, str], str]:
    locator: dict[str, str] = {}
    safe_items: list[str] = []
    fill_like = "fill" in tool_name or "type" in tool_name
    runtime_value_keys = {"value", "text", "input", "content"}
    for key, value in inputs.items():
        key_text = str(key)
        normalized_key = key_text.lower()
        if _SECRET_KEY_RE.search(key_text):
            continue
        if fill_like and normalized_key in runtime_value_keys | {"name"}:
            safe_items.append(f"{key_text}=<runtime_test_data>")
            continue
        if normalized_key in {"selector", "locator", "role", "name", "text", "label", "placeholder", "testid", "test_id", "exact"}:
            candidate = _safe_text(value, limit=180, sensitive_values=sensitive_values)
            if candidate:
                if runtime_namespace:
                    candidate = candidate.replace(runtime_namespace, "{run_id}")
                candidate = _RUNTIME_NAMESPACE_RE.sub("{run_id}", candidate)
                locator[key_text] = candidate
            continue
        if normalized_key in runtime_value_keys:
            safe_items.append(f"{key_text}=<runtime_test_data>")
            continue
        candidate = _safe_text(value, limit=120, sensitive_values=sensitive_values)
        if candidate:
            safe_items.append(f"{key_text}={candidate}")
    return locator, "; ".join(safe_items)[:_MAX_INPUT]


class ExplorationEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sequence: int = Field(ge=1)
    tool_name: str = Field(min_length=1, max_length=120)
    category: Literal["navigate", "observe", "interact", "screenshot", "error"]
    status: Literal["succeeded", "failed", "blocked"]
    relative_path: str = Field(default="", max_length=300)
    locator: dict[str, str] = Field(default_factory=dict)
    input_summary: str = Field(default="", max_length=_MAX_INPUT)
    # Additive v2 field. It retains only a classified CRUD operation, never the
    # raw tool input from which the classification was derived.
    operation_intent: Literal["", "create", "update", "delete"] = ""
    output_excerpt: str = Field(default="", max_length=_MAX_EXCERPT)
    state_fingerprint: str = Field(default="", max_length=32)
    screenshot_path: str = Field(default="", max_length=500)

    @field_validator("relative_path")
    @classmethod
    def only_relative_path(cls, value: str) -> str:
        if value and not value.startswith("/"):
            raise ValueError("relative_path 必须是相对路径")
        return value


class ElementEvidence(BaseModel):
    """One deterministic, callback-backed locator record for script generation."""

    model_config = ConfigDict(extra="forbid")

    evidence_id: str = Field(pattern=r"^E\d{6}$")
    source_sequence: int = Field(ge=1)
    scenario_step_ids: list[str] = Field(default_factory=list, max_length=30)
    relative_path: str = Field(default="", max_length=300)
    tool_name: str = Field(min_length=1, max_length=120)
    action: Literal["click", "fill", "select", "press", "observe"]
    locator_kind: Literal["css", "role", "text", "label", "placeholder", "testid"]
    locator_value: str = Field(min_length=1, max_length=300)
    locator_kwargs: dict[str, Any] = Field(default_factory=dict)
    runtime_placeholders: list[str] = Field(default_factory=list, max_length=8)
    stability: Literal["stable", "acceptable", "fragile", "rejected"]
    result_excerpt: str = Field(default="", max_length=_MAX_GENERATOR_EVENT_RESULT)
    state_fingerprint: str = Field(default="", max_length=32)

    @field_validator("relative_path")
    @classmethod
    def only_relative_evidence_path(cls, value: str) -> str:
        if value and not value.startswith("/"):
            raise ValueError("relative_path 必须是相对路径")
        return value


class ExplorationTrace(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[2] = TRACE_SCHEMA_VERSION
    start_path: str = "/"
    events: list[ExplorationEvent] = Field(default_factory=list, max_length=_MAX_EVENTS)
    observed_paths: list[str] = Field(default_factory=list, max_length=80)
    successful_interactions: list[int] = Field(default_factory=list, max_length=_MAX_EVENTS)
    failed_interactions: list[int] = Field(default_factory=list, max_length=_MAX_EVENTS)
    element_evidence: list[ElementEvidence] = Field(default_factory=list, max_length=_MAX_EVENTS)
    coverage: dict[str, dict[str, Any]] = Field(default_factory=dict)
    cleanup: dict[str, Any] = Field(default_factory=lambda: {"status": "not_required", "attempted": False, "residuals": [], "reason": ""})
    tool_stats: dict[str, Any] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list, max_length=50)
    termination_reason: str = Field(default="", max_length=120)
    last_location: str = Field(default="", max_length=300)

    @field_validator("start_path", "last_location")
    @classmethod
    def relative_trace_paths(cls, value: str) -> str:
        if value and not value.startswith("/"):
            raise ValueError("trace 路径必须是相对路径")
        return value


class ExplorationTraceRecorder:
    """Records tool callbacks before any ORM persistence or model parsing."""

    def __init__(
        self,
        start_path: str = "/",
        *,
        sensitive_values: tuple[str, ...] = (),
        trace_file: str | Path | None = None,
        runtime_namespace: str = "",
    ):
        self.start_path = _relative_path(start_path) or "/"
        self._sensitive_values = tuple(
            value for value in (str(item) for item in sensitive_values) if value
        )
        self._trace_file = Path(trace_file).resolve() if trace_file else None
        self._runtime_namespace = str(runtime_namespace or "")
        self._events: list[ExplorationEvent] = []
        self._next_sequence = 1
        self._active: dict[Any, tuple[int, str, dict[str, Any]]] = {}
        self._observed_fingerprints: set[tuple[str, str]] = set()
        self._last_location = self.start_path
        if self._trace_file is not None:
            self._trace_file.parent.mkdir(parents=True, exist_ok=True)
            self._trace_file.unlink(missing_ok=True)

    @staticmethod
    def _category(tool_name: str) -> str:
        if "screenshot" in tool_name:
            return "screenshot"
        if "navigate" in tool_name or tool_name.endswith("_goto"):
            return "navigate"
        if tool_name in _OBSERVATION_TOOLS or "visible_" in tool_name or "snapshot" in tool_name:
            return "observe"
        if any(marker in tool_name for marker in _INTERACTION_MARKERS):
            return "interact"
        return "observe"

    def on_tool_start(self, serialized: Mapping[str, Any] | None, input_str: str, *, run_id: Any = None, inputs: Any = None) -> None:
        tool_name = str((serialized or {}).get("name") or "browser_tool").lower()
        if not tool_name.startswith("playwright_") and tool_name != "browser_console_logs":
            return
        payload = _as_mapping(inputs, input_str)
        sequence = self._next_sequence
        self._next_sequence += 1
        self._active[run_id] = (sequence, tool_name, payload)

    @staticmethod
    def _operation_intent(tool_name: str, category: str, inputs: Mapping[str, Any]) -> str:
        """Classify a CRUD action from complete raw interaction inputs only."""
        if category != "interact" or not any(marker in tool_name for marker in _OPERATION_INTENT_TOOL_MARKERS):
            return ""
        values = (
            _output_text(value).lower()
            for key, value in inputs.items()
            if not _SECRET_KEY_RE.search(str(key))
        )
        input_text = " ".join(values)
        for intent, markers in _INTENT_MARKERS.items():
            if any(marker in input_text for marker in markers):
                return intent
        return ""

    def _complete(self, output: Any, *, run_id: Any, status: str, blocked: bool = False) -> None:
        active = self._active.pop(run_id, None)
        if active is None:
            return
        if len(self._events) >= _MAX_EVENTS:
            return
        sequence, tool_name, inputs = active
        category = self._category(tool_name)
        path = ""
        for key in ("url", "path", "target", "href"):
            path = _relative_path(inputs.get(key))
            if path:
                break
        runtime_values: tuple[str, ...] = ()
        if "fill" in tool_name or "type" in tool_name:
            runtime_values = tuple(
                str(value) for key, value in inputs.items()
                if str(key).lower() in {"value", "text", "input", "content", "name"}
                and value not in (None, "")
            )
        excerpt = _safe_text(
            _output_text(output),
            limit=_MAX_EXCERPT,
            sensitive_values=(*self._sensitive_values, *runtime_values),
        )
        if self._runtime_namespace:
            excerpt = excerpt.replace(self._runtime_namespace, "{run_id}")
        excerpt = _RUNTIME_NAMESPACE_RE.sub("{run_id}", excerpt)
        if not path:
            match = _ABSOLUTE_URL_RE.search(_output_text(output))
            path = _relative_path(match.group(0)) if match else ""
        if path:
            self._last_location = path
        locator, summary = _locator_and_summary(
            inputs,
            tool_name,
            sensitive_values=self._sensitive_values,
            runtime_namespace=self._runtime_namespace,
        )
        fingerprint = ""
        if category == "observe" and status == "succeeded" and excerpt:
            fingerprint = hashlib.sha256(excerpt.encode("utf-8", errors="replace")).hexdigest()[:16]
            key = (path or self._last_location, fingerprint)
            if key in self._observed_fingerprints:
                return
            self._observed_fingerprints.add(key)
        screenshot_path = ""
        if category == "screenshot":
            match = re.search(r"(?:[\w.-]+/)*[\w.-]+\.png\b", _output_text(output))
            screenshot_path = match.group(0) if match else ""
        event = ExplorationEvent(
            sequence=sequence, tool_name=tool_name, category="error" if blocked else category,
            status="blocked" if blocked else status, relative_path=path or self._last_location,
            locator=locator, input_summary=summary,
            operation_intent=(
                self._operation_intent(tool_name, category, inputs)
                if status == "succeeded" and not blocked else ""
            ),
            output_excerpt=excerpt,
            state_fingerprint=fingerprint, screenshot_path=screenshot_path,
        )
        self._events.append(event)
        if self._trace_file is not None:
            with self._trace_file.open("a", encoding="utf-8") as trace_stream:
                trace_stream.write(event.model_dump_json() + "\n")

    def on_tool_end(self, output: Any, *, run_id: Any = None) -> None:
        self._complete(output, run_id=run_id, status="failed" if _tool_failed(output) else "succeeded")

    def on_tool_error(self, error: BaseException, *, run_id: Any = None) -> None:
        self._complete(error, run_id=run_id, status="failed")

    def mark_blocked(self, serialized: Mapping[str, Any] | None, input_str: str, *, run_id: Any = None, inputs: Any = None, error: BaseException | None = None) -> None:
        if run_id not in self._active:
            self.on_tool_start(serialized, input_str, run_id=run_id, inputs=inputs)
        self._complete(error or "blocked by safety policy", run_id=run_id, status="failed", blocked=True)

    def build(self, *, tool_stats: Mapping[str, Any], termination_reason: str = "", cleanup: Mapping[str, Any] | None = None, warnings: list[str] | None = None) -> ExplorationTrace:
        events = list(self._events)
        observed_paths = list(dict.fromkeys(event.relative_path for event in events if event.relative_path and event.category in {"navigate", "observe"}))
        successful = [event.sequence for event in events if event.category == "interact" and event.status == "succeeded"]
        failed = [event.sequence for event in events if event.category in {"interact", "error"} and event.status != "succeeded"]
        return ExplorationTrace(
            start_path=self.start_path, events=events, observed_paths=observed_paths,
            successful_interactions=successful, failed_interactions=failed,
            element_evidence=_build_element_evidence(events, {}),
            cleanup=dict(cleanup or {"status": "not_required", "attempted": False, "residuals": [], "reason": ""}),
            tool_stats=dict(tool_stats), warnings=list(dict.fromkeys(warnings or [])),
            termination_reason=str(termination_reason or ""), last_location=self._last_location,
        )


def trace_has_minimum_page_state(trace: ExplorationTrace) -> bool:
    return bool(trace.observed_paths or any(event.category == "observe" and event.status == "succeeded" for event in trace.events))


def assess_trace_coverage(scenario, trace: ExplorationTrace) -> ExplorationTrace:
    """Conservatively map intent-specific callback evidence to scenario steps."""
    coverage: dict[str, dict[str, Any]] = {}
    successful_events = sorted(
        (event for event in trace.events if event.status == "succeeded"),
        key=lambda event: event.sequence,
    )
    for step in scenario.steps:
        matches = _step_coverage_sequences(step, successful_events)
        # Historic snapshots predate callback-owned event categories. Preserve
        # their already-validated step mapping without allowing new broad text
        # observations to bypass the intent-aware rules above.
        if not matches and (trace.coverage.get(step.id) or {}).get("status") in {"confirmed", "partially_confirmed"}:
            matches = [
                event.sequence for event in successful_events
                if event.tool_name.startswith("legacy_")
            ]
        coverage[step.id] = {
            "status": "confirmed" if matches else "missing",
            "event_sequences": matches[:12],
            "reason": "" if matches else "未取得符合步骤意图的成功交互或页面观察。",
        }
    return trace.model_copy(update={
        "coverage": coverage,
        "element_evidence": _build_element_evidence(trace.events, coverage),
    })


def _event_text(event: ExplorationEvent) -> str:
    return " ".join((
        event.relative_path,
        event.input_summary,
        event.output_excerpt,
        *event.locator.values(),
    )).lower()


def _keywords_from_values(values: tuple[str, ...]) -> list[str]:
    candidates: list[str] = []
    for value in values:
        cleaned = value
        for marker in (*_GENERIC_STEP_TERMS, *[item for markers in _INTENT_MARKERS.values() for item in markers]):
            cleaned = re.sub(re.escape(marker), " ", cleaned, flags=re.I)
        for token in re.findall(r"[\u4e00-\u9fff]{2,}|[a-z][a-z0-9_-]{1,}", cleaned.lower()):
            if token not in candidates:
                candidates.append(token)
    return candidates


def _target_keywords(step) -> list[str]:
    return _keywords_from_values((
        str(step.name or ""), str(step.target_hint or ""), str(step.expected or ""),
    ))


def _expected_keywords(step) -> list[str]:
    return _keywords_from_values((str(step.expected or ""),))


def _matches_target(event: ExplorationEvent, keywords: list[str]) -> bool:
    return bool(keywords) and any(keyword in _event_text(event) for keyword in keywords)


def _following_observe_sequences(
    interaction: ExplorationEvent,
    events: list[ExplorationEvent],
) -> list[int]:
    """Keep only observes immediately attributable to one successful interaction."""
    following: list[int] = []
    found = False
    for event in events:
        if event.sequence <= interaction.sequence:
            continue
        if event.category in {"interact", "navigate"}:
            break
        if event.category == "observe":
            following.append(event.sequence)
            found = True
            continue
        if found:
            break
    return following


def _step_coverage_sequences(step, events: list[ExplorationEvent]) -> list[int]:
    intent = str(step.intent or "").lower()
    keywords = _target_keywords(step)
    if intent in _INTENT_MARKERS:
        return _crud_coverage_sequences(intent, events, keywords)

    if intent == "cleanup":
        return _crud_coverage_sequences("delete", events, keywords, require_observe=False)

    if intent == "navigate":
        # A navigation step is complete only after observing its expected
        # destination. Seeing the initial login page must not satisfy a
        # "login successfully and enter home" step before form submission.
        destination_keywords = _expected_keywords(step) or keywords
        navigations: list[int] = []
        for event in events:
            if event.category not in {"navigate", "interact"}:
                continue
            following = _matching_following_observes(event, events, destination_keywords)
            if following:
                navigations.extend([event.sequence, *following])
        return list(dict.fromkeys(navigations))

    if intent == "assert":
        mentioned_intents = _mentioned_crud_intents(step)
        if mentioned_intents:
            matched_groups = [
                _crud_coverage_sequences(action, events, keywords, require_observe=True)
                for action in mentioned_intents
            ]
            if any(not group for group in matched_groups):
                return []
            return list(dict.fromkeys(sequence for group in matched_groups for sequence in group))

    # Read steps need an observed page state. A target interaction only
    # helps when a subsequent successful observe confirms the changed state.
    observed = [
        event.sequence for event in events
        if event.category == "observe" and _matches_target(event, keywords)
    ]
    related: list[int] = []
    for event in events:
        if event.category != "interact" or not _matches_target(event, keywords):
            continue
        following = _following_observe_sequences(event, events)
        if following:
            related.extend([event.sequence, *following])
    return list(dict.fromkeys([*observed, *related]))


def _matching_following_observes(
    interaction: ExplorationEvent,
    events: list[ExplorationEvent],
    keywords: list[str],
) -> list[int]:
    following = _following_observe_sequences(interaction, events)
    if not keywords:
        return following
    return [
        event.sequence for event in events
        if event.sequence in following and _matches_target(event, keywords)
    ]


def _crud_coverage_sequences(
    intent: str,
    events: list[ExplorationEvent],
    keywords: list[str],
    *,
    require_observe: bool = False,
) -> list[int]:
    sequences: list[int] = []
    for event in events:
        if event.category != "interact" or not _matches_operation_intent(event, intent):
            continue
        following = _matching_following_observes(event, events, keywords)
        target_matches = not keywords or _matches_target(event, keywords) or bool(following)
        if not target_matches or (require_observe and not following):
            continue
        sequences.extend([event.sequence, *following])
    return list(dict.fromkeys(sequences))


def _matches_operation_intent(event: ExplorationEvent, intent: str) -> bool:
    """Prefer recorder-owned intent; only pre-field events may use text fallback."""
    if event.operation_intent:
        return event.operation_intent == intent
    if "operation_intent" in event.model_fields_set:
        return False
    return any(marker in _event_text(event) for marker in _INTENT_MARKERS[intent])


def _mentioned_crud_intents(step) -> list[str]:
    text = " ".join((str(step.name or ""), str(step.target_hint or ""), str(step.expected or ""))).lower()
    return [
        intent for intent, markers in _INTENT_MARKERS.items()
        if any(marker in text for marker in markers)
    ]


def _evidence_action(tool_name: str) -> str:
    if "fill" in tool_name or "type" in tool_name:
        return "fill"
    if "select" in tool_name:
        return "select"
    if "press" in tool_name:
        return "press"
    if any(marker in tool_name for marker in ("click", "check", "uncheck")):
        return "click"
    return "observe"


def _stability(locator_kind: str, value: str, kwargs: Mapping[str, Any]) -> str:
    lowered = value.strip().lower()
    if not lowered or _DYNAMIC_ELEMENT_ID_RE.search(value):
        return "rejected"
    if _BARE_VISIBLE_INPUT_RE.fullmatch(lowered) or _NTH_SELECTOR_RE.search(value):
        return "fragile"
    if locator_kind == "css":
        if "," in value or value.count("+") >= 2 or value.count(">") >= 3 or re.fullmatch(r"(?:input|textarea|select|button|a|div)(?::visible)?", lowered):
            return "fragile"
        if "data-testid" in lowered or "data-test-id" in lowered:
            return "stable"
        return "acceptable"
    if locator_kind == "role":
        return "stable" if str(kwargs.get("name") or "").strip() else "acceptable"
    if locator_kind in {"testid", "label", "placeholder"}:
        return "stable"
    if locator_kind == "text":
        return "stable" if bool(kwargs.get("exact")) and len(value.strip()) >= 2 else "fragile"
    return "fragile"


def _python_locator_parts(value: str) -> tuple[str, str, dict[str, Any]] | None:
    match = _PYTHON_LOCATOR_RE.match(value.strip())
    if match is None:
        return None
    method = match.group("method")
    kind = {
        "locator": "css", "get_by_role": "role", "get_by_text": "text",
        "get_by_label": "label", "get_by_placeholder": "placeholder", "get_by_test_id": "testid",
    }[method]
    kwargs: dict[str, Any] = {}
    tail = match.group("kwargs") or ""
    name_match = re.search(r"\bname\s*=\s*['\"]([^'\"]+)['\"]", tail)
    if name_match:
        kwargs["name"] = name_match.group(1)
    if re.search(r"\bexact\s*=\s*True\b", tail):
        kwargs["exact"] = True
    return kind, match.group("value"), kwargs


def _event_locator(event: ExplorationEvent) -> tuple[str, str, dict[str, Any]] | None:
    locator = {str(key).lower(): str(value) for key, value in event.locator.items() if str(value).strip()}
    if "role" in locator:
        kwargs = {key: locator[key] for key in ("name", "exact") if key in locator}
        if kwargs.get("exact", "").lower() in {"true", "1"}:
            kwargs["exact"] = True
        elif "exact" in kwargs:
            kwargs.pop("exact")
        return "role", locator["role"], kwargs
    for key, kind in (("testid", "testid"), ("test_id", "testid"), ("label", "label"), ("placeholder", "placeholder"), ("text", "text")):
        if key in locator:
            kwargs = {"exact": True} if locator.get("exact", "").lower() in {"true", "1"} else {}
            return kind, locator[key], kwargs
    for key in ("selector", "locator"):
        if key not in locator:
            continue
        parsed = _python_locator_parts(locator[key])
        return parsed or ("css", locator[key], {})
    for value in locator.values():
        parsed = _python_locator_parts(value)
        if parsed is not None:
            return parsed
    return None


def _build_element_evidence(
    events: list[ExplorationEvent],
    coverage: Mapping[str, Mapping[str, Any]],
) -> list[ElementEvidence]:
    step_ids_by_sequence: dict[int, list[str]] = {}
    for step_id, item in coverage.items():
        for sequence in item.get("event_sequences") or []:
            if isinstance(sequence, int):
                step_ids_by_sequence.setdefault(sequence, []).append(str(step_id))
    evidence: list[ElementEvidence] = []
    for event in sorted(events, key=lambda item: item.sequence):
        if event.status != "succeeded":
            continue
        locator = _event_locator(event)
        if locator is None:
            continue
        kind, value, kwargs = locator
        evidence.append(ElementEvidence(
            evidence_id=f"E{event.sequence:06d}", source_sequence=event.sequence,
            scenario_step_ids=sorted(set(step_ids_by_sequence.get(event.sequence, []))),
            relative_path=event.relative_path, tool_name=event.tool_name,
            action=_evidence_action(event.tool_name), locator_kind=kind, locator_value=value,
            locator_kwargs=kwargs,
            runtime_placeholders=["run_id"] if "{run_id}" in value or any("{run_id}" in str(item) for item in kwargs.values()) else [],
            stability=_stability(kind, value, kwargs), result_excerpt=event.output_excerpt[:_MAX_GENERATOR_EVENT_RESULT],
            state_fingerprint=event.state_fingerprint,
        ))
    return evidence


def ensure_element_evidence(trace: ExplorationTrace) -> ExplorationTrace:
    """Rebuild callback-backed evidence for old v2 traces without changing schema."""
    rebuilt = _build_element_evidence(trace.events, trace.coverage)
    return trace.model_copy(update={"element_evidence": rebuilt or trace.element_evidence})


def required_trace_evidence_gaps(scenario, trace: ExplorationTrace) -> list[dict[str, str]]:
    """Return explicit blockers before the generator can invent a complete script."""
    trace = ensure_element_evidence(trace)
    usable_by_step: dict[str, list[ElementEvidence]] = {}
    for item in trace.element_evidence:
        if item.stability not in {"stable", "acceptable"}:
            continue
        for step_id in item.scenario_step_ids:
            usable_by_step.setdefault(step_id, []).append(item)
    gaps: list[dict[str, str]] = []
    for step in scenario.steps:
        coverage = trace.coverage.get(step.id) or {}
        if coverage.get("status") != "confirmed":
            gaps.append({"step_id": step.id, "reason": str(coverage.get("reason") or "缺少成功页面证据。")})
            continue
        if step.intent in {"create", "update", "delete"} and not usable_by_step.get(step.id):
            gaps.append({"step_id": step.id, "reason": "已观察到步骤，但缺少稳定或可接受的成功元素定位器。"})
    return gaps


def structured_trace_evidence(trace: ExplorationTrace) -> dict[str, Any]:
    """Bounded generator input: structured locators plus only necessary page facts."""
    trace = ensure_element_evidence(trace)
    cleanup = trace.cleanup or {}
    payload: dict[str, Any] = {
        "schema_version": TRACE_SCHEMA_VERSION,
        "element_evidence": [],
        "step_evidence": {},
        "page_summary": {
            "start_path": trace.start_path,
            "observed_paths": list(dict.fromkeys(trace.observed_paths))[:20],
            "states": [],
        },
        "cleanup": {
            "status": str(cleanup.get("status") or "")[:80],
            "attempted": bool(cleanup.get("attempted")),
            "residuals": [str(item)[:120] for item in (cleanup.get("residuals") or [])[:8]],
            "reason": str(cleanup.get("reason") or "")[:160],
        },
        "warnings": [str(item)[:120] for item in trace.warnings[:8]],
    }
    def payload_size(value: dict[str, Any] | None = None) -> int:
        return len(json.dumps(value or payload, ensure_ascii=False, separators=(",", ":")))

    for step_id, item in trace.coverage.items():
        payload["step_evidence"][step_id] = {
            "status": item.get("status", "missing"), "evidence_ids": [],
            "reason": str(item.get("reason") or "")[:160],
        }

    # Malformed/legacy traces can contain more step entries than the current
    # ScenarioSpec contract permits. Keep room for actual locator evidence
    # instead of allowing metadata alone to consume the full request budget.
    metadata_ceiling = _MAX_GENERATOR_EVIDENCE_CHARS - 2_000
    for item in reversed(list(payload["step_evidence"].values())):
        if payload_size() <= metadata_ceiling:
            break
        item["reason"] = ""
    while payload_size() > metadata_ceiling and payload["step_evidence"]:
        payload["step_evidence"].pop(next(reversed(payload["step_evidence"])))

    eligible_evidence = [
        item for item in trace.element_evidence
        if item.stability in {"stable", "acceptable"}
    ]
    mandatory_ids: set[str] = set()
    for step_id in payload["step_evidence"]:
        first = next((
            item for item in eligible_evidence if step_id in item.scenario_step_ids
        ), None)
        if first is not None:
            mandatory_ids.add(first.evidence_id)
    priority_evidence = sorted(eligible_evidence, key=lambda item: (
        0 if item.evidence_id in mandatory_ids else (1 if item.scenario_step_ids else 2),
        item.source_sequence,
    ))
    for item in priority_evidence:
        candidate = [*payload["element_evidence"], item.model_dump(mode="json")]
        if payload_size({**payload, "element_evidence": candidate}) <= _MAX_GENERATOR_EVIDENCE_CHARS:
            payload["element_evidence"] = candidate
    payload["element_evidence"].sort(key=lambda item: item["source_sequence"])

    retained_ids = {item["evidence_id"] for item in payload["element_evidence"]}
    for step_id, item in payload["step_evidence"].items():
        item["evidence_ids"] = [
            evidence.evidence_id for evidence in trace.element_evidence
            if step_id in evidence.scenario_step_ids and evidence.evidence_id in retained_ids
        ]

    # Page summaries are useful context, but never displace validated locators.
    for event in trace.events:
        if event.status != "succeeded" or event.category not in {"navigate", "observe"} or not event.output_excerpt:
            continue
        candidate = [*payload["page_summary"]["states"], {
            "sequence": event.sequence, "relative_path": event.relative_path,
            "result_excerpt": event.output_excerpt[:_MAX_GENERATOR_EVENT_RESULT],
        }]
        candidate_payload = {
            **payload,
            "page_summary": {**payload["page_summary"], "states": candidate},
        }
        if payload_size(candidate_payload) <= _MAX_GENERATOR_EVIDENCE_CHARS:
            payload["page_summary"]["states"] = candidate
        if len(payload["page_summary"]["states"]) >= 12:
            break

    while payload_size() > _MAX_GENERATOR_EVIDENCE_CHARS and payload["warnings"]:
        payload["warnings"].pop()
    for item in payload["step_evidence"].values():
        if payload_size() <= _MAX_GENERATOR_EVIDENCE_CHARS:
            break
        item["reason"] = ""
    while payload_size() > _MAX_GENERATOR_EVIDENCE_CHARS and payload["element_evidence"]:
        removed = payload["element_evidence"].pop()
        removed_id = removed["evidence_id"]
        for item in payload["step_evidence"].values():
            item["evidence_ids"] = [value for value in item["evidence_ids"] if value != removed_id]
    while payload_size() > _MAX_GENERATOR_EVIDENCE_CHARS and payload["step_evidence"]:
        payload["step_evidence"].pop(next(reversed(payload["step_evidence"])))
    return payload


def successful_trace_evidence(trace: ExplorationTrace) -> dict[str, Any]:
    """Return a bounded, deterministic generator view of successful evidence.

    Persisted traces remain complete for review.  This projection keeps the
    navigation/action/locator/result facts needed for a script, while folding
    repeated page observations and enforcing a request-size ceiling without a
    second model summarisation pass.
    """
    def clip(value: Any, limit: int) -> str:
        return str(value or '')[:limit]

    def payload_size(value: dict[str, Any]) -> int:
        return len(json.dumps(value, ensure_ascii=False, separators=(',', ':')))

    def state_signature(event: ExplorationEvent) -> tuple[str, str]:
        if event.state_fingerprint:
            return ('fingerprint', event.state_fingerprint)
        normalized_output = re.sub(r'\s+', ' ', event.output_excerpt).strip()
        digest = hashlib.sha256(normalized_output.encode('utf-8', errors='replace')).hexdigest()[:16]
        return ('output', digest)

    def event_signature(event: ExplorationEvent) -> tuple[Any, ...]:
        return (
            event.category, event.relative_path, event.tool_name,
            tuple(sorted(event.locator.items())), event.input_summary,
            state_signature(event),
        )

    successful_events = sorted((
        event for event in trace.events
        if event.status == 'succeeded' and event.category in {'navigate', 'observe', 'interact'}
    ), key=lambda event: event.sequence)

    # Establish one chronological representative for each truly identical
    # state before priority selection. Coverage pointing at a folded duplicate
    # is remapped to the retained representative rather than left dangling.
    representative_by_signature: dict[tuple[Any, ...], ExplorationEvent] = {}
    representative_events: list[ExplorationEvent] = []
    sequence_representative: dict[int, int] = {}
    for event in successful_events:
        signature = event_signature(event)
        representative = representative_by_signature.get(signature)
        if representative is None:
            representative = event
            representative_by_signature[signature] = event
            representative_events.append(event)
        sequence_representative[event.sequence] = representative.sequence

    coverage_details: dict[str, dict[str, Any]] = {}
    evidence: dict[str, Any] = {
        'schema_version': TRACE_SCHEMA_VERSION,
        'start_path': clip(trace.start_path, 300),
        'observed_paths': [],
        'events': [],
        'coverage': {},
        'cleanup': {
            'status': clip((trace.cleanup or {}).get('status'), 80),
            'attempted': bool((trace.cleanup or {}).get('attempted')),
            'residuals': [],
            'reason': '',
        },
        'warnings': [],
    }

    # Keep room for at least one compact key event. Coverage is added in a
    # deterministic order and starts conservatively: a claimed status is only
    # restored after its referenced event is actually selected below.
    key_event_reserve = 1_600
    for raw_step_id in sorted(trace.coverage, key=str):
        step_id = clip(raw_step_id, 80)
        if not step_id or step_id in evidence['coverage']:
            continue
        item = trace.coverage.get(raw_step_id) or {}
        original_status = clip(item.get('status'), 40) or 'missing'
        claims_evidence = original_status in {'confirmed', 'partially_confirmed'}
        source_sequence = next((
            sequence_representative[sequence]
            for sequence in (item.get('event_sequences') or [])
            if isinstance(sequence, int) and sequence in sequence_representative
        ), None)
        original_reason = clip(item.get('reason'), 120)
        if claims_evidence:
            safe_reason = (
                '对应成功事件尚未进入压缩证据，已安全降级。'
                if source_sequence is not None
                else '原覆盖未引用可用的成功事件，已安全降级。'
            )
            compacted = {'status': 'missing', 'event_sequences': [], 'reason': safe_reason}
        else:
            compacted = {'status': original_status, 'event_sequences': [], 'reason': original_reason}
        candidate_coverage = {**evidence['coverage'], step_id: compacted}
        candidate = {**evidence, 'coverage': candidate_coverage}
        if payload_size(candidate) > _MAX_GENERATOR_EVIDENCE_CHARS - key_event_reserve:
            compacted = {**compacted, 'reason': ''}
            candidate_coverage = {**evidence['coverage'], step_id: compacted}
            candidate = {**evidence, 'coverage': candidate_coverage}
        if payload_size(candidate) > _MAX_GENERATOR_EVIDENCE_CHARS - key_event_reserve:
            break
        evidence['coverage'] = candidate_coverage
        coverage_details[step_id] = {
            'original_status': original_status,
            'original_reason': original_reason,
            'claims_evidence': claims_evidence,
            'source_sequence': source_sequence,
        }

    mandatory_sequences = {
        item['source_sequence']
        for item in coverage_details.values()
        if item['claims_evidence'] and item['source_sequence'] is not None
    }

    def priority(event: ExplorationEvent) -> tuple[int, int]:
        if event.sequence in mandatory_sequences:
            return (0, event.sequence)
        if event.category == 'interact':
            return (1, event.sequence)
        if event.category == 'navigate':
            return (2, event.sequence)
        return (3, event.sequence)

    def compact_event(event: ExplorationEvent, *, minimal: bool = False) -> dict[str, Any]:
        locator_limit = 80 if minimal else 160
        max_locators = 4 if minimal else _MAX_GENERATOR_EVENT_LOCATORS
        locator = {
            clip(key, 80): clip(value, locator_limit)
            for key, value in list(event.locator.items())[:max_locators]
            if clip(key, 80) and clip(value, locator_limit)
        }
        return {
            'sequence': event.sequence,
            'tool_name': clip(event.tool_name, 120),
            'category': event.category,
            'relative_path': clip(event.relative_path, 300),
            'locator': locator,
            'input_summary': clip(event.input_summary, 100 if minimal else _MAX_GENERATOR_EVENT_INPUT),
            'output_excerpt': clip(event.output_excerpt, 120 if minimal else _MAX_GENERATOR_EVENT_RESULT),
        }

    def coverage_after_selecting(sequence: int) -> dict[str, dict[str, Any]]:
        updated = dict(evidence['coverage'])
        for step_id, details in coverage_details.items():
            if not details['claims_evidence'] or details['source_sequence'] != sequence:
                continue
            updated[step_id] = {
                'status': details['original_status'],
                'event_sequences': [sequence],
                'reason': details['original_reason'],
            }
        return updated

    for event in sorted(representative_events, key=priority):
        candidate_coverage = coverage_after_selecting(event.sequence)
        candidate_events = [*evidence['events'], compact_event(event)]
        candidate = {**evidence, 'events': candidate_events, 'coverage': candidate_coverage}
        if payload_size(candidate) > _MAX_GENERATOR_EVIDENCE_CHARS:
            candidate_events[-1] = compact_event(event, minimal=True)
            candidate = {**evidence, 'events': candidate_events, 'coverage': candidate_coverage}
        if payload_size(candidate) > _MAX_GENERATOR_EVIDENCE_CHARS:
            continue
        evidence['events'] = candidate_events
        evidence['coverage'] = candidate_coverage

    # Priority controls admission only. The generator always receives the
    # retained trace in its original temporal order.
    evidence['events'].sort(key=lambda event: event['sequence'])

    for path in dict.fromkeys(trace.observed_paths):
        if len(evidence['observed_paths']) >= 20:
            break
        candidate_paths = [*evidence['observed_paths'], clip(path, 300)]
        candidate = {**evidence, 'observed_paths': candidate_paths}
        if payload_size(candidate) > _MAX_GENERATOR_EVIDENCE_CHARS:
            break
        evidence['observed_paths'] = candidate_paths

    cleanup_reason = clip((trace.cleanup or {}).get('reason'), 160)
    if cleanup_reason:
        candidate_cleanup = {**evidence['cleanup'], 'reason': cleanup_reason}
        candidate = {**evidence, 'cleanup': candidate_cleanup}
        if payload_size(candidate) <= _MAX_GENERATOR_EVIDENCE_CHARS:
            evidence['cleanup'] = candidate_cleanup
    for residual in (trace.cleanup or {}).get('residuals') or []:
        if len(evidence['cleanup']['residuals']) >= 8:
            break
        candidate_cleanup = {
            **evidence['cleanup'],
            'residuals': [*evidence['cleanup']['residuals'], clip(residual, 120)],
        }
        candidate = {**evidence, 'cleanup': candidate_cleanup}
        if payload_size(candidate) > _MAX_GENERATOR_EVIDENCE_CHARS:
            break
        evidence['cleanup'] = candidate_cleanup
    for warning in dict.fromkeys(trace.warnings):
        if len(evidence['warnings']) >= 6:
            break
        candidate_warnings = [*evidence['warnings'], clip(warning, 120)]
        candidate = {**evidence, 'warnings': candidate_warnings}
        if payload_size(candidate) > _MAX_GENERATOR_EVIDENCE_CHARS:
            break
        evidence['warnings'] = candidate_warnings
    return evidence


def coerce_trace(value: Any) -> ExplorationTrace:
    """Adapt in-memory test doubles; persisted pipeline records must already be v2."""
    if isinstance(value, ExplorationTrace):
        return value
    data = value.model_dump(mode="json") if hasattr(value, "model_dump") else dict(value or {})
    if data.get("schema_version") == TRACE_SCHEMA_VERSION:
        return ExplorationTrace.model_validate(data)
    events: list[ExplorationEvent] = []
    confirmed_names = {
        str(name)
        for item in (data.get("step_evidence") or {}).values()
        if item.get("status") in {"confirmed", "partially_confirmed"}
        for name in (item.get("element_names") or [])
    }
    for index, element in enumerate(data.get("elements") or [], start=1):
        visible_name = str(element.get("visible_name") or "")
        if not confirmed_names or visible_name not in confirmed_names:
            continue
        locators = list(element.get("candidate_locators") or [])
        events.append(ExplorationEvent(
            sequence=index, tool_name="legacy_observation", category="observe", status="succeeded",
            relative_path=(data.get("visited_paths") or [data.get("start_url_path") or "/"])[0],
            locator={f"locator_{position}": locator for position, locator in enumerate(locators, start=1)},
            output_excerpt=visible_name,
        ))
    if not events and data.get("visited_paths"):
        events.append(ExplorationEvent(sequence=1, tool_name="legacy_navigation", category="navigate", status="succeeded", relative_path=data["visited_paths"][0]))
    coverage = {
        step_id: {"status": "confirmed" if item.get("status") in {"confirmed", "partially_confirmed"} else "missing", "event_sequences": [1] if events else [], "reason": item.get("reason") or ""}
        for step_id, item in (data.get("step_evidence") or {}).items()
    }
    stats = data.get("tool_stats") or {}
    cleanup = data.get("cleanup_report") or {"status": "not_required", "attempted": False, "residuals": [], "reason": ""}
    return ExplorationTrace(
        start_path=data.get("start_url_path") or "/", events=events,
        observed_paths=list(data.get("visited_paths") or []), coverage=coverage,
        cleanup=cleanup, tool_stats=stats, warnings=list(data.get("warnings") or []),
        termination_reason=str(stats.get("termination_reason") or ""),
    )
