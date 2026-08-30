"""Shared, durable save-state helpers for V2 WebUI generations."""

from __future__ import annotations

import hashlib
from typing import Any


def generation_reference(generation_or_id: Any) -> str:
    """Return the stable metadata marker used for save idempotency."""
    generation_id = getattr(generation_or_id, 'pk', generation_or_id)
    return hashlib.sha256(str(generation_id).encode('utf-8')).hexdigest()


def is_generation_saved(generation: Any) -> bool:
    """A relation alone is not a save: require this generation's marker."""
    if not getattr(generation, 'test_case_id', None):
        return False
    try:
        test_case = generation.test_case
    except Exception:
        return False
    metadata = getattr(test_case, 'generation_metadata', None)
    return isinstance(metadata, dict) and metadata.get('generation_ref') == generation_reference(generation)
