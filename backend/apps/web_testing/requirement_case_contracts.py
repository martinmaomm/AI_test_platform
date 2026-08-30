"""Strict contracts for AI-generated WebUI requirement drafts."""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


DraftPriority = Literal['high', 'medium', 'low']
DraftCategory = Literal['functional', 'negative', 'boundary']
DraftAction = Literal['goto', 'click', 'fill', 'select', 'check', 'hover']


class GeneratedDraftStep(BaseModel):
    """One natural-language WebUI test step returned by the model."""

    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)

    step_id: int = Field(ge=1, le=50)
    action: DraftAction
    target: Optional[str] = Field(default=None, max_length=200)
    value: Optional[str] = Field(default=None, max_length=1000)
    description: str = Field(min_length=1, max_length=500)

    @field_validator('target', 'value')
    @classmethod
    def normalize_optional_text(cls, value):
        if value is None:
            return None
        value = value.strip()
        return value or None


class GeneratedDraftCase(BaseModel):
    """One generated draft before backend provenance is injected."""

    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)

    title: str = Field(min_length=1, max_length=200)
    description: str = Field(min_length=1, max_length=1000)
    priority: DraftPriority
    category: DraftCategory
    preconditions: list[str] = Field(default_factory=list, max_length=20)
    steps: list[GeneratedDraftStep] = Field(min_length=1, max_length=30)
    expected_result: str = Field(min_length=1, max_length=1000)

    @field_validator('preconditions')
    @classmethod
    def normalize_preconditions(cls, value):
        normalized = []
        for item in value:
            text = str(item).strip()
            if text:
                normalized.append(text[:500])
        return normalized


class GeneratedDraftBatch(BaseModel):
    """Top-level JSON object required from the LLM."""

    model_config = ConfigDict(extra='forbid')

    test_cases: list[GeneratedDraftCase] = Field(min_length=1, max_length=10)
