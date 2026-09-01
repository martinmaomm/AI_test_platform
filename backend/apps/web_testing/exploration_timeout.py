"""Configuration helpers for the WebUI exploration stage."""

from __future__ import annotations

import logging
import os


logger = logging.getLogger(__name__)

EXPLORATION_TIMEOUT_DEFAULT_SECONDS = 600
EXPLORATION_TIMEOUT_MIN_SECONDS = 60
EXPLORATION_TIMEOUT_MAX_SECONDS = 1800


def exploration_total_timeout_seconds() -> float:
    """Read only the dedicated WebUI exploration timeout configuration."""
    raw_value = os.getenv('WEBUI_EXPLORATION_TOTAL_TIMEOUT_SECONDS')
    if raw_value is None or not str(raw_value).strip():
        return float(EXPLORATION_TIMEOUT_DEFAULT_SECONDS)
    try:
        timeout_seconds = int(raw_value)
    except (TypeError, ValueError):
        logger.warning(
            'WEBUI_EXPLORATION_TOTAL_TIMEOUT_SECONDS 配置无效: %r，回退到%s秒',
            raw_value,
            EXPLORATION_TIMEOUT_DEFAULT_SECONDS,
        )
        return float(EXPLORATION_TIMEOUT_DEFAULT_SECONDS)
    return float(min(EXPLORATION_TIMEOUT_MAX_SECONDS, max(EXPLORATION_TIMEOUT_MIN_SECONDS, timeout_seconds)))
