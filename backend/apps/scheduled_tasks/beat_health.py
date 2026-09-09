"""Beat scheduler heartbeat and the read-only service status contract."""
from __future__ import annotations

import hashlib
import json
import logging
import math
import time
from datetime import datetime, timezone as datetime_timezone

from django.conf import settings
from django.core.cache import cache
from django.utils import timezone
from django_celery_beat.schedulers import DatabaseScheduler


logger = logging.getLogger(__name__)

HEARTBEAT_INTERVAL_SECONDS = 10
HEARTBEAT_TIMEOUT_SECONDS = 60
HEARTBEAT_TTL_SECONDS = 120
MAX_FUTURE_HEARTBEAT_SKEW_SECONDS = 60


def heartbeat_cache_key() -> str:
    """Return a stable, non-secret cache key for this database-backed instance."""
    database = settings.DATABASES.get('default', {})
    identity = {
        field: str(database.get(field, '') or '')
        for field in ('ENGINE', 'NAME', 'HOST', 'PORT')
    }
    digest = hashlib.sha256(
        json.dumps(identity, sort_keys=True, separators=(',', ':')).encode('utf-8')
    ).hexdigest()
    return f'automation:scheduled-tasks:beat-heartbeat:{digest}'


def _status_payload(status: str, timestamp: float | None = None) -> dict:
    last_heartbeat_at = None
    if timestamp is not None:
        try:
            last_heartbeat_at = datetime.fromtimestamp(
                timestamp, tz=datetime_timezone.utc,
            ).isoformat()
        except (OverflowError, OSError, ValueError):
            status = 'unknown'
    return {
        'status': status,
        'last_heartbeat_at': last_heartbeat_at,
        'heartbeat_timeout_seconds': HEARTBEAT_TIMEOUT_SECONDS,
        'check_interval_seconds': 15,
    }


def get_beat_service_status(now=None) -> dict:
    """Read the shared heartbeat without exposing cache implementation failures."""
    now = now or timezone.now()
    try:
        value = cache.get(heartbeat_cache_key())
    except Exception:
        return _status_payload('unknown')

    if value is None:
        return _status_payload('offline')
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return _status_payload('unknown')

    timestamp = float(value)
    if not math.isfinite(timestamp):
        return _status_payload('unknown')

    try:
        now_timestamp = now.timestamp()
        datetime.fromtimestamp(timestamp, tz=datetime_timezone.utc)
    except (AttributeError, OverflowError, OSError, ValueError):
        return _status_payload('unknown')

    if timestamp > now_timestamp + MAX_FUTURE_HEARTBEAT_SKEW_SECONDS:
        return _status_payload('unknown')
    if now_timestamp - timestamp > HEARTBEAT_TIMEOUT_SECONDS:
        return _status_payload('offline', timestamp)
    return _status_payload('online', timestamp)


class BeatHealthDatabaseScheduler(DatabaseScheduler):
    """DatabaseScheduler that records a heartbeat only after a successful tick."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        try:
            self.max_interval = min(float(self.max_interval), HEARTBEAT_INTERVAL_SECONDS)
        except (TypeError, ValueError):
            self.max_interval = HEARTBEAT_INTERVAL_SECONDS
        self._last_heartbeat_attempt_monotonic = None

    def tick(self, *args, **kwargs):
        delay = super().tick(*args, **kwargs)
        self._report_heartbeat()
        return delay

    def _report_heartbeat(self) -> None:
        now_monotonic = time.monotonic()
        previous_attempt = self._last_heartbeat_attempt_monotonic
        if (
            previous_attempt is not None
            and now_monotonic - previous_attempt < HEARTBEAT_INTERVAL_SECONDS
        ):
            return

        # Keep attempts and warning logs at the reporting cadence when cache is down.
        self._last_heartbeat_attempt_monotonic = now_monotonic
        try:
            cache.set(
                heartbeat_cache_key(),
                timezone.now().timestamp(),
                timeout=HEARTBEAT_TTL_SECONDS,
            )
        except Exception:
            logger.warning('Unable to record Celery Beat heartbeat', exc_info=True)
