"""v3 generation notifications.

The durable database record is the source of truth.  These notifications use
the existing node-start event shape for old WebSocket clients.  Terminal events
are emitted only by :func:`publish_terminal`.
"""

from __future__ import annotations

import logging

from django.core.cache import cache

from common.websocket import send_node_start_notification_helper
from common.websocket.websocket_core import websocket_message_service

from .models import WebUIScriptGeneration

logger = logging.getLogger(__name__)
TERMINAL_EVENT_TTL_SECONDS = 60 * 60


def publish_stage_changed(generation, display_name: str) -> None:
    """Publish one safe, non-terminal stage event; failure is non-fatal."""
    task_id = generation.celery_task_id or str(generation.pk)
    logger.info(
        'WebUI v3 generation stage: generation_id=%s stage=%s status=%s progress=%s',
        generation.pk,
        generation.current_stage,
        generation.status,
        generation.progress,
    )
    try:
        send_node_start_notification_helper(
            user_id=generation.user_id,
            node_name=f'v3_{generation.current_stage}',
            node_display_name=display_name,
            task_id=task_id,
            enable_streaming=True,
            room_type='webui_auto_test',
        )
    except Exception:
        logger.warning('WebUI v3 阶段事件发送失败: generation_id=%s', generation.pk, exc_info=True)


def publish_terminal(generation) -> None:
    """The single v3 source for safe completion, review, failure and cancel events."""
    task_id = generation.celery_task_id or str(generation.pk)
    result = {'generation_id': str(generation.pk), 'status': generation.status}
    cache_key = f'webui:script-generation:terminal-event:{generation.pk}'
    cache_available = True
    try:
        if not cache.add(cache_key, generation.status, timeout=TERMINAL_EVENT_TTL_SECONDS):
            return
    except Exception:
        # Notification deduplication is an optimisation.  The database state is
        # already terminal, so a temporary cache outage must not turn a
        # completed generation into a worker/API failure.
        cache_available = False
        logger.warning(
            'WebUI v3 终态事件去重缓存不可用，将降级发送: generation_id=%s',
            generation.pk,
        )

    def release_deduplication_key() -> None:
        if not cache_available:
            return
        try:
            cache.delete(cache_key)
        except Exception:
            logger.warning(
                'WebUI v3 终态事件去重缓存清理失败: generation_id=%s',
                generation.pk,
            )

    try:
        if generation.status in {
            WebUIScriptGeneration.Status.READY,
            WebUIScriptGeneration.Status.READY_WITH_WARNINGS,
        }:
            delivered = websocket_message_service.send_task_completed(
                user_id=generation.user_id,
                task_id=task_id,
                result=result,
                message='WebUI 测试脚本已生成。',
                room_type='webui_auto_test',
            )
        else:
            delivered = websocket_message_service.send_task_failed(
                user_id=generation.user_id,
                task_id=task_id,
                error=generation.error_code or generation.status,
                message=generation.error_message or 'WebUI 测试脚本需要人工处理。',
                room_type='webui_auto_test',
            )
        if not delivered:
            release_deduplication_key()
            logger.warning('WebUI v3 终态事件未送达，将允许后续重试: generation_id=%s', generation.pk)
    except Exception:
        # Keep the database record as source of truth but permit a later caller
        # to retry if this notification was not handed to the websocket service.
        release_deduplication_key()
        logger.warning('WebUI v3 终态事件发送失败: generation_id=%s', generation.pk, exc_info=True)
