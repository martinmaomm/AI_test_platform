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
_SECRET_KEY_RE = re.compile(r"(?i)(password|passwd|token|secret|cookie|authorization|api[_-]?key)")
_ABSOLUTE_URL_RE = re.compile(r"https?://[^\s'\"<>]+", re.I)
_OBSERVATION_TOOLS = frozenset({"playwright_get_visible_text", "playwright_get_visible_html", "playwright_snapshot"})
_INTERACTION_MARKERS = ("click", "fill", "select", "press", "check", "uncheck", "hover", "drag")


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
        if normalized_key in {"selector", "locator", "role", "name", "text", "label", "placeholder", "testid", "test_id"}:
            candidate = _safe_text(value, limit=180, sensitive_values=sensitive_values)
            if candidate:
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
    output_excerpt: str = Field(default="", max_length=_MAX_EXCERPT)
    state_fingerprint: str = Field(default="", max_length=32)
    screenshot_path: str = Field(default="", max_length=500)

    @field_validator("relative_path")
    @classmethod
    def only_relative_path(cls, value: str) -> str:
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
    ):
        self.start_path = _relative_path(start_path) or "/"
        self._sensitive_values = tuple(
            value for value in (str(item) for item in sensitive_values) if value
        )
        self._trace_file = Path(trace_file).resolve() if trace_file else None
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
        if not path:
            match = _ABSOLUTE_URL_RE.search(_output_text(output))
            path = _relative_path(match.group(0)) if match else ""
        if path:
            self._last_location = path
        locator, summary = _locator_and_summary(
            inputs,
            tool_name,
            sensitive_values=self._sensitive_values,
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
            locator=locator, input_summary=summary, output_excerpt=excerpt,
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
            cleanup=dict(cleanup or {"status": "not_required", "attempted": False, "residuals": [], "reason": ""}),
            tool_stats=dict(tool_stats), warnings=list(dict.fromkeys(warnings or [])),
            termination_reason=str(termination_reason or ""), last_location=self._last_location,
        )


def trace_has_minimum_page_state(trace: ExplorationTrace) -> bool:
    return bool(trace.observed_paths or any(event.category == "observe" and event.status == "succeeded" for event in trace.events))


def assess_trace_coverage(scenario, trace: ExplorationTrace) -> ExplorationTrace:
    """Map only successful observations/interactions to required scenario steps.

    It is intentionally conservative: a missing mapping is a quality blocker,
    never an invitation for the generator to invent a locator.
    """
    coverage: dict[str, dict[str, Any]] = {}
    for step in scenario.steps:
        tokens = [token.lower() for token in re.findall(r"[\w\u4e00-\u9fff]{2,}", f"{step.name} {step.target_hint}")]
        matches = [event.sequence for event in trace.events if event.status == "succeeded" and any(token in f"{event.input_summary} {event.output_excerpt} {' '.join(event.locator.values())}".lower() for token in tokens)]
        coverage[step.id] = {"status": "confirmed" if matches else "missing", "event_sequences": matches[:12], "reason": "" if matches else "未取得可安全映射到该步骤的成功定位或页面观察。"}
    return trace.model_copy(update={"coverage": coverage})


def successful_trace_evidence(trace: ExplorationTrace) -> dict[str, Any]:
    """Generator input: excludes failed locators, fill values and full URLs."""
    events = [event.model_dump(mode="json") for event in trace.events if event.status == "succeeded" and event.category in {"navigate", "observe", "interact"}]
    return {"schema_version": TRACE_SCHEMA_VERSION, "start_path": trace.start_path, "observed_paths": trace.observed_paths, "events": events, "coverage": trace.coverage, "cleanup": trace.cleanup, "warnings": trace.warnings}


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
