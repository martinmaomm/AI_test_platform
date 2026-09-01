"""Shared, durable save-state helpers for v3 WebUI generations."""

from __future__ import annotations

import hashlib
import json
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
    if not isinstance(metadata, dict) or metadata.get('generation_ref') != generation_reference(generation):
        return False
    fingerprint = metadata.get('content_fingerprint')
    if not fingerprint:
        # Legacy clients omitted workspace revisions; do not let that bypass a new workspace lock.
        return not getattr(generation, 'workspace', None) and int(getattr(generation, 'revision', 0) or 0) == 0
    script = getattr(generation, 'script_draft', '') or ''
    if fingerprint != hashlib.sha256(script.strip().encode('utf-8')).hexdigest():
        return False
    from .generation_workspace import normalize_workspace
    workspace = normalize_workspace(getattr(generation, 'workspace', None), script=script)
    if metadata.get('workspace_revision') != workspace.get('revision', 0):
        return False
    variables = workspace.get('variables', [])
    variable_fingerprint = hashlib.sha256(
        json.dumps(variables, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')
    ).hexdigest()
    return (
        metadata.get('variables_fingerprint') == variable_fingerprint
        and metadata.get('variables_fingerprint') == hashlib.sha256(
            json.dumps(getattr(test_case, 'variables', []) or [], ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')
        ).hexdigest()
    )
