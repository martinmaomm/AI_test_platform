"""Deterministic V2 generation preflight; this module never calls a model or browser."""

from __future__ import annotations

import copy
import os
import re
from dataclasses import dataclass, field
from typing import Any, Literal

from django.conf import settings

from ai_core.models import LLMConfiguration, MCPConfiguration, ModelType

from .generation_contracts import ScenarioSpec

_EXPLORATION_CONTEXT_RE = re.compile(
    r'(?:探索阶段|探索期|exploration(?:\s+phase)?)', re.IGNORECASE,
)
_WRITE_ACTION_RE = re.compile(
    r'(?:提交|新增|创建|编辑|修改|删除|审批|付款|发布|上传|'
    r'submit|create|update|delete|approve|pay|publish|upload)',
    re.IGNORECASE,
)
_NEGATED_WRITE_RE = re.compile(
    r'(?:不要|禁止|不得|避免|不允许|不可|不应|别)\s*(?:提交|新增|创建|编辑|修改|删除|审批|付款|发布|上传|'
    r'submit|create|update|delete|approve|pay|publish|upload)',
    re.IGNORECASE,
)
_NEGATED_CONTEXT_WRITE_RE = re.compile(
    r'(?:不要|禁止|不得|避免|不允许|不可|不应|别).{0,24}'
    r'(?:探索阶段|探索期|exploration(?:\s+phase)?).{0,36}'
    r'(?:提交|新增|创建|编辑|修改|删除|审批|付款|发布|上传|'
    r'submit|create|update|delete|approve|pay|publish|upload)',
    re.IGNORECASE,
)
_EXPLICIT_WRITE_INTENT_RE = re.compile(
    r'(?:请|需|需要|必须|应当|应该|要|允许|可以|可|执行|进行|完成|直接|先|同时)'
    r'\s*(?:提交|新增|创建|编辑|修改|删除|审批|付款|发布|上传|'
    r'submit|create|update|delete|approve|pay|publish|upload)',
    re.IGNORECASE,
)


def exploration_requires_write_confirmation(description: str) -> bool:
    """Return true only for an explicit request to write during exploration.

    CRUD is a valid *script* objective.  This check intentionally examines the
    exploration clause only, so safety guidance such as "探索阶段不要提交新增"
    cannot accidentally block the request.
    """
    for sentence in re.split(r'[。！？!?.\n]+', description or ''):
        context_match = _EXPLORATION_CONTEXT_RE.search(sentence)
        if not context_match:
            continue
        suffix = sentence[context_match.end():]
        action_match = _WRITE_ACTION_RE.search(suffix)
        if not action_match:
            continue
        # The whole exploration clause is negated, including wording such as
        # "禁止在探索阶段提交" where the negation appears before the context.
        before_action = sentence[:context_match.end() + action_match.end()]
        if _NEGATED_WRITE_RE.search(before_action) or _NEGATED_CONTEXT_WRITE_RE.search(before_action):
            continue
        action_prefix = suffix[:action_match.end()]
        direct_action = bool(re.match(r'^\s*[，,:：;；-]*\s*' + _WRITE_ACTION_RE.pattern, suffix, re.I))
        if direct_action or _EXPLICIT_WRITE_INTENT_RE.search(action_prefix):
            return True
    return False


@dataclass(frozen=True)
class PreflightResult:
    outcome: Literal['continue', 'needs_confirmation', 'needs_credentials', 'failed']
    error_code: str = ''
    message: str = ''
    warnings: list[str] = field(default_factory=list)
    mcp_config_id: int | None = None
    mcp_config: dict[str, Any] | None = None


def prepare_playwright_mcp_config(
    raw_config: dict[str, Any],
    *,
    browser_path: str | None = None,
    base_dir: str | None = None,
) -> dict[str, Any]:
    """Prepare a safe MCP client config without logging its potentially secret values."""
    config = copy.deepcopy(raw_config)
    servers = config.get('mcpServers')
    if not isinstance(servers, dict):
        raise ValueError('MCP 配置中没有有效的 mcpServers')
    playwright = servers.get('playwright')
    if not isinstance(playwright, dict) or playwright.get('is_active', True) is False:
        raise ValueError('MCP 配置中没有启用的 playwright 服务')
    command = playwright.get('command')
    if not isinstance(command, str) or not command.strip():
        raise ValueError('MCP playwright 配置缺少 command 字段')

    server_env = playwright.get('env')
    if server_env is None:
        server_env = {}
    if not isinstance(server_env, dict):
        raise ValueError('MCP playwright env 必须是对象')
    playwright['env'] = copy.deepcopy(server_env)
    playwright['env']['PYTHONUNBUFFERED'] = '1'
    playwright['env']['MCP_USE_ANONYMIZED_TELEMETRY'] = 'false'

    configured_browser_path = (
        browser_path
        or os.getenv('MCP_PLAYWRIGHT_BROWSERS_PATH')
        or os.getenv('PLAYWRIGHT_BROWSERS_PATH')
    )
    if configured_browser_path:
        resolved_base_dir = os.path.abspath(base_dir or str(settings.BASE_DIR))
        if not os.path.isabs(configured_browser_path):
            configured_browser_path = os.path.join(resolved_base_dir, configured_browser_path)
        playwright['env']['PLAYWRIGHT_BROWSERS_PATH'] = os.path.abspath(configured_browser_path)
    if playwright.get('timeout') is None:
        playwright['timeout'] = 30
    return config


def resolve_active_playwright_mcp_config(user_id: int) -> tuple[int, dict[str, Any]] | None:
    """Synchronously select an active user-owned MCP configuration with Playwright."""
    configurations = MCPConfiguration.objects.filter(
        created_by_id=user_id,
        is_active=True,
    ).order_by('-created_at')
    for configuration in configurations:
        try:
            config = prepare_playwright_mcp_config(configuration.get_config_dict())
        except (TypeError, ValueError):
            continue
        return configuration.id, config
    return None


def _has_environment_credentials(environment) -> bool:
    variables = (environment.config or {}).get('variables') or {}
    username = variables.get('UI_TEST_USERNAME') or variables.get('ui_test_username')
    password = variables.get('UI_TEST_PASSWORD') or variables.get('ui_test_password')
    return bool(username and password)


def run_safety_preflight(generation, scenario: ScenarioSpec, *, credentials_available: bool) -> PreflightResult:
    """Return a stable, user-actionable decision without starting MCP."""
    environment = generation.environment
    if not environment.is_active or not environment.is_web_environment or not (environment.config or {}).get('base_url'):
        return PreflightResult('failed', 'INPUT_INVALID', '所选 WebUI 环境不可用或未配置 Base URL。')

    config_id = (generation.model_info or {}).get('config_id')
    active_model = LLMConfiguration.objects.filter(
        id=config_id,
        model_type=ModelType.LLM,
        is_active=True,
    ).exists()
    if not active_model:
        return PreflightResult('failed', 'MODEL_CONFIG_MISSING', '本次锁定的 LLM 配置不存在或已停用。')

    mcp_selection = resolve_active_playwright_mcp_config(generation.user_id)
    if not mcp_selection:
        return PreflightResult('failed', 'MCP_CONFIG_MISSING', '没有可用的 Playwright MCP 配置。')

    if scenario.credentials_required and not (credentials_available or _has_environment_credentials(environment)):
        return PreflightResult('needs_credentials', 'CREDENTIALS_REQUIRED', '场景需要登录，请提供本次探索登录信息或配置环境变量。')

    if exploration_requires_write_confirmation(generation.description_safe):
        return PreflightResult(
            'needs_confirmation',
            'EXPLORATION_WRITE_CONFIRMATION_REQUIRED',
            '描述要求探索阶段提交写操作，需要人工确认后才能继续。',
        )
    mcp_config_id, mcp_config = mcp_selection
    discovery_count = len({*scenario.discovery_targets, *scenario.ambiguities})
    warnings = ['探索阶段仅查看页面和打开表单，不会提交业务写操作。']
    if discovery_count:
        warnings.append(f'将通过页面探索自动确认 {discovery_count} 项信息。')
    return PreflightResult(
        'continue',
        warnings=warnings,
        mcp_config_id=mcp_config_id,
        mcp_config=mcp_config,
    )
