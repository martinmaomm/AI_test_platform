"""Deterministic v4 generation preflight; this module never calls a model or browser."""

from __future__ import annotations

import copy
import os
import re
from pathlib import Path
from dataclasses import dataclass, field
from typing import Any, Literal
from uuid import UUID, uuid4

from django.conf import settings

from ai_core.models import LLMConfiguration, MCPConfiguration, ModelType

from .generation_contracts import ScenarioPlan

_EXTRA_RISK_ACTION_RE = re.compile(
    r'(?:审批|付款|支付|发布|上传|发短信|发送邮件|approve|pay(?:ment)?|publish|upload|send\s+(?:sms|email))',
    re.IGNORECASE,
)
_NEGATED_EXTRA_RISK_RE = re.compile(
    r'(?:不要|禁止|不得|避免|不允许|不可|不应|别|勿|do\s+not|don[\'’]?t|never)'
    r'.{0,24}(?:审批|付款|支付|发布|上传|发短信|发送邮件|approve|pay(?:ment)?|publish|upload|send\s+(?:sms|email))',
    re.IGNORECASE,
)

_EXECUTEAUTOMATION_PLAYWRIGHT_MCP_PACKAGE = '@executeautomation/playwright-mcp-server'
_AITS_MCP_LOG_FILE_ENV = 'AITS_MCP_LOG_FILE'
_AITS_MCP_SCREENSHOT_DIR_ENV = 'AITS_MCP_SCREENSHOT_DIR'
_AITS_MCP_WORKING_DIR_ENV = 'AITS_MCP_WORKING_DIR'
_AITS_MCP_DISABLE_FILE_LOG_ENV = 'AITS_MCP_DISABLE_FILE_LOG'


def validate_generation_output_id(generation_id: str | None) -> str:
    """Return a canonical UUID directory segment without accepting path-like ids."""
    if generation_id is None:
        return str(uuid4())
    try:
        return str(UUID(str(generation_id)))
    except (AttributeError, TypeError, ValueError) as exc:
        raise ValueError('generation_id 必须是 UUID，不能作为输出目录路径使用。') from exc


def _is_output_bootstrap_args(args: Any, bootstrap_path: Path) -> bool:
    """Recognize this project's wrapper so repeated preparation stays idempotent."""
    if not isinstance(args, list) or not all(isinstance(arg, str) for arg in args):
        return False
    for index, arg in enumerate(args[:-1]):
        if arg != '--package' or not args[index + 1].startswith(
            f'{_EXECUTEAUTOMATION_PLAYWRIGHT_MCP_PACKAGE}@'
        ):
            continue
        server_args = args[index + 2:]
        if server_args[:1] == ['--']:
            server_args = server_args[1:]
        if len(server_args) < 2 or server_args[:1] != ['node']:
            continue
        if Path(server_args[1]).resolve() != bootstrap_path:
            raise ValueError('Playwright MCP 已包装为非本项目输出启动器，不能安全重复包装。')
        if any(arg == '--port' or arg.startswith('--port=') for arg in server_args[2:]):
            raise ValueError('Playwright MCP 输出重定向仅支持 stdio，不能使用 --port。')
        return True
    return False


def _executeautomation_package_spec(args: Any) -> tuple[list[str], str, list[str]] | None:
    """Parse the supported npx stdio form while preserving npx and server options."""
    if not isinstance(args, list) or not all(isinstance(arg, str) for arg in args):
        return None

    package_indexes = [
        index for index, arg in enumerate(args)
        if (index == 0 or args[index - 1] != '--package')
        and (arg == _EXECUTEAUTOMATION_PLAYWRIGHT_MCP_PACKAGE
        or arg.startswith(f'{_EXECUTEAUTOMATION_PLAYWRIGHT_MCP_PACKAGE}@')
        )
    ]
    package_option_indexes = [index for index, arg in enumerate(args[:-1]) if arg == '--package']
    if not package_indexes and not package_option_indexes:
        return None
    if len(package_indexes) > 1 or package_indexes and package_option_indexes:
        raise ValueError('Playwright MCP npx 配置只能包含一个 @executeautomation 包版本。')

    if package_option_indexes:
        if len(package_option_indexes) != 1:
            raise ValueError('Playwright MCP npx 配置只能包含一个 --package 选项。')
        package_index = package_option_indexes[0]
        package_spec = args[package_index + 1]
        if not (
            package_spec == _EXECUTEAUTOMATION_PLAYWRIGHT_MCP_PACKAGE
            or package_spec.startswith(f'{_EXECUTEAUTOMATION_PLAYWRIGHT_MCP_PACKAGE}@')
        ):
            return None
        npx_options = args[:package_index]
        server_args = args[package_index + 2:]
        if server_args[:1] == ['--']:
            server_args = server_args[1:]
        if not server_args or server_args[0] != 'playwright-mcp-server':
            raise ValueError('npx --package 形式必须以 playwright-mcp-server 启动 stdio 服务。')
        server_args = server_args[1:]
    else:
        package_index = package_indexes[0]
        package_spec = args[package_index]
        npx_options = args[:package_index]
        server_args = args[package_index + 1:]
    if package_spec == _EXECUTEAUTOMATION_PLAYWRIGHT_MCP_PACKAGE:
        raise ValueError('Playwright MCP 必须固定到明确版本后才能重定向任务输出。')
    if server_args[:1] == ['--']:
        server_args = server_args[1:]
    if any(arg == '--port' or arg.startswith('--port=') for arg in server_args):
        raise ValueError('Playwright MCP 输出重定向仅支持 stdio，不能使用 --port。')
    return npx_options, package_spec, server_args


def prepare_playwright_mcp_output_config(
    raw_config: dict[str, Any],
    generation_id: str | None,
    *,
    base_dir: str | None = None,
    sensitive_runtime: bool = False,
) -> dict[str, Any]:
    """Derive an outputs-only runtime config for the pinned Playwright MCP server.

    Database JSON is never mutated.  Unsupported MCP entries stay untouched so
    callers that contain other MCP servers retain their existing behaviour.
    """
    config = copy.deepcopy(raw_config)
    playwright = (config.get('mcpServers') or {}).get('playwright')
    if not isinstance(playwright, dict):
        return config

    resolved_base_dir = Path(base_dir or str(settings.BASE_DIR)).resolve()
    bootstrap_path = resolved_base_dir / 'scripts' / 'playwright_mcp_output_bootstrap.mjs'
    command = playwright.get('command')
    is_wrapped = _is_output_bootstrap_args(playwright.get('args', []), bootstrap_path)
    parsed_args = None if is_wrapped else _executeautomation_package_spec(playwright.get('args', []))
    if not is_wrapped and parsed_args is None:
        return config
    if not isinstance(command, str) or os.path.basename(command) != 'npx':
        raise ValueError('固定的 @executeautomation Playwright MCP 仅支持通过 npx stdio 启动。')
    if not bootstrap_path.is_file():
        raise ValueError('Playwright MCP 输出启动器不存在，无法安全重定向输出。')
    if is_wrapped and generation_id is None:
        return config

    output_id = validate_generation_output_id(generation_id)

    configured_cwd = playwright.get('cwd')
    if configured_cwd is not None and (not isinstance(configured_cwd, str) or not configured_cwd.strip()):
        raise ValueError('MCP playwright cwd 必须是非空路径。')
    resolved_cwd = None
    if configured_cwd:
        cwd_path = Path(configured_cwd).expanduser()
        resolved_cwd = str((cwd_path if cwd_path.is_absolute() else resolved_base_dir / cwd_path).resolve())

    env = playwright.setdefault('env', {})
    if not isinstance(env, dict):
        raise ValueError('MCP playwright env 必须是对象')
    env[_AITS_MCP_LOG_FILE_ENV] = str(
        resolved_base_dir / 'logs' / 'playwright-mcp' / f'{output_id}.log'
    )
    env[_AITS_MCP_SCREENSHOT_DIR_ENV] = str(
        resolved_base_dir / 'temp' / 'playwright-mcp' / output_id / 'screenshots'
    )
    # The upstream server logs complete CallTool request bodies. Credential
    # values must therefore disable its file logger instead of relying on its
    # incomplete field-name redaction.
    env[_AITS_MCP_DISABLE_FILE_LOG_ENV] = '1' if sensitive_runtime else '0'
    if resolved_cwd is not None:
        env[_AITS_MCP_WORKING_DIR_ENV] = resolved_cwd

    if is_wrapped:
        return config

    npx_options, package_spec, server_args = parsed_args
    playwright['args'] = [
        *npx_options,
        '--package', package_spec,
        '--',
        'node', str(bootstrap_path),
        *server_args,
    ]
    return config


def exploration_requires_write_confirmation(description: str) -> bool:
    """Keep only a dedicated high-risk safety deny-list.

    This does not identify goals, browser writes, event coverage, or replay
    evidence. Normal interactions are governed exclusively by scenario policy.
    """
    for clause in re.split(r'[。！？!?.\n；;]+', str(description or '')):
        if _EXTRA_RISK_ACTION_RE.search(clause) and not _NEGATED_EXTRA_RISK_RE.search(clause):
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


def environment_credentials(environment) -> dict[str, str] | None:
    """Return the two supported login slots without copying them into artifacts."""
    variables = (environment.config or {}).get('variables') or {}
    if not isinstance(variables, dict):
        return None
    username = variables.get('UI_TEST_USERNAME') or variables.get('ui_test_username')
    password = variables.get('UI_TEST_PASSWORD') or variables.get('ui_test_password')
    if username in (None, '') or password in (None, ''):
        return None
    return {'username': str(username), 'password': str(password)}


def _has_environment_credentials(environment) -> bool:
    return environment_credentials(environment) is not None


def run_safety_preflight(generation, plan: ScenarioPlan, *, credentials_available: bool) -> PreflightResult:
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

    if plan.credentials_required and not (credentials_available or _has_environment_credentials(environment)):
        return PreflightResult('needs_credentials', 'CREDENTIALS_REQUIRED', '场景需要登录，请提供本次探索登录信息或配置环境变量。')

    if exploration_requires_write_confirmation(generation.description_safe):
        return PreflightResult(
            'needs_confirmation',
            'EXPLORATION_EXTRA_RISK_BLOCKED',
            '本次探索包含审批、支付、发布或文件/外部消息操作，超出普通测试数据操作范围，请调整目标后继续。',
        )
    mcp_config_id, mcp_config = mcp_selection
    discovery_count = len(set(plan.discovery_notes))
    if plan.allow_test_data_writes:
        scope_message = '本场景仅授权处理本轮命名空间内的测试数据。'
        if plan.cleanup_expected:
            scope_message += '结束前必须执行清理并用后续页面观察确认。'
    else:
        scope_message = '本场景不授予测试数据写入权限。'
    warnings = [scope_message]
    if discovery_count:
        warnings.append(f'将通过页面探索自动确认 {discovery_count} 项信息。')
    return PreflightResult(
        'continue',
        warnings=warnings,
        mcp_config_id=mcp_config_id,
        mcp_config=mcp_config,
    )
