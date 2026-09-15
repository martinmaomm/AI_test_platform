"""MCP tool-list discovery without invoking an LLM or any MCP tool."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from typing import Any


DEFAULT_DISCOVERY_TIMEOUT_SECONDS = 20.0


class MCPToolDiscoveryError(RuntimeError):
    """A credential-safe, user-facing MCP discovery failure."""

    def __init__(self, code: str, message: str, *, cause_type: str = ''):
        self.code = code
        self.cause_type = cause_type
        super().__init__(message)


def build_mcp_connections(mcp_servers: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Build adapter connections while retaining managed output routing and cwd."""
    from web_testing.generation_preflight import prepare_playwright_mcp_output_config

    runtime_config = prepare_playwright_mcp_output_config(
        {'mcpServers': mcp_servers}, None,
    )
    connections: dict[str, dict[str, Any]] = {}
    for server_name, server_config in runtime_config['mcpServers'].items():
        if not isinstance(server_config, dict):
            continue
        command = server_config.get('command')
        url = server_config.get('url')
        if isinstance(command, str) and command.strip():
            connection = {
                'command': command,
                'transport': 'stdio',
            }
            for field in ('args', 'env', 'cwd'):
                if field in server_config:
                    connection[field] = server_config[field]
            connections[server_name] = connection
        elif isinstance(url, str) and url.strip():
            connection = {
                'url': url,
                'transport': 'streamable_http',
            }
            if 'headers' in server_config:
                connection['headers'] = server_config['headers']
            connections[server_name] = connection
    return connections


def _exception_chain(exc: BaseException):
    pending = [exc]
    seen: set[int] = set()
    while pending:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        yield current
        nested = getattr(current, 'exceptions', None)
        if nested:
            pending.extend(nested)
        if current.__cause__ is not None:
            pending.append(current.__cause__)
        elif current.__context__ is not None:
            pending.append(current.__context__)


def _public_error(exc: BaseException, timeout_seconds: float) -> MCPToolDiscoveryError:
    causes = list(_exception_chain(exc))
    for item in causes:
        if isinstance(item, MCPToolDiscoveryError):
            return item
    cause_type = type(causes[-1] if causes else exc).__name__
    if any(isinstance(item, (TimeoutError, asyncio.TimeoutError)) for item in causes):
        return MCPToolDiscoveryError(
            'timeout',
            f'MCP工具检测超时（{timeout_seconds:g}秒），请检查服务启动和网络连接。',
            cause_type=cause_type,
        )
    if any(isinstance(item, (ModuleNotFoundError, ImportError)) for item in causes):
        return MCPToolDiscoveryError(
            'dependency_missing',
            'MCP工具检测组件不可用，请检查后端依赖安装。',
            cause_type=cause_type,
        )
    if any(isinstance(item, FileNotFoundError) for item in causes):
        return MCPToolDiscoveryError(
            'command_not_found',
            '无法启动MCP服务，请检查命令路径和运行环境。',
            cause_type=cause_type,
        )
    if any(isinstance(item, PermissionError) for item in causes):
        return MCPToolDiscoveryError(
            'permission_denied',
            'MCP服务启动权限不足，请检查命令和工作目录权限。',
            cause_type=cause_type,
        )
    if any(isinstance(item, (ConnectionError, OSError)) for item in causes):
        return MCPToolDiscoveryError(
            'connection_failed',
            '无法连接MCP服务，请检查服务进程、地址和网络。',
            cause_type=cause_type,
        )
    return MCPToolDiscoveryError(
        'discovery_failed',
        f'MCP工具检测失败（{cause_type}），请检查配置和服务日志。',
        cause_type=cause_type,
    )


def _tool_schema(tool: Any) -> dict[str, Any]:
    schema = getattr(tool, 'inputSchema', None)
    if schema is None:
        schema = getattr(tool, 'input_schema', None)
    if isinstance(schema, dict):
        return schema
    args_schema = getattr(tool, 'args_schema', None)
    if args_schema is not None:
        for method_name in ('model_json_schema', 'schema'):
            method = getattr(args_schema, method_name, None)
            if callable(method):
                try:
                    value = method()
                except Exception:
                    continue
                if isinstance(value, dict):
                    return value
    return {}


def validate_discovered_tools(tools: list[dict[str, Any]]) -> None:
    """Validate the snapshot against MCPTool persistence constraints."""
    seen_names: set[str] = set()
    for tool in tools:
        if not isinstance(tool, dict):
            raise MCPToolDiscoveryError(
                'invalid_tool',
                'MCP服务返回了无效的工具结构，工具清单未更新。',
                cause_type=type(tool).__name__,
            )
        name = tool.get('name')
        if not isinstance(name, str) or not name.strip():
            raise MCPToolDiscoveryError(
                'missing_tool_name',
                'MCP服务返回了缺少名称的工具，工具清单未更新。',
                cause_type='MissingToolName',
            )
        if len(name) > 100:
            raise MCPToolDiscoveryError(
                'tool_name_too_long',
                'MCP服务返回的工具名称超过100个字符，工具清单未更新。',
                cause_type='ToolNameTooLong',
            )
        if name in seen_names:
            raise MCPToolDiscoveryError(
                'duplicate_tool_name',
                '检测到重复工具名称；请将包含同名工具的MCP服务拆分为独立配置。',
                cause_type='DuplicateToolName',
            )
        seen_names.add(name)


async def _discover_async(connections: dict[str, dict[str, Any]], client_factory: Callable):
    client = client_factory(connections)

    async def list_server_tools(server_name: str):
        async with client.session(server_name) as session:
            tools = []
            cursor = None
            seen_cursors: set[str] = set()
            while True:
                result = await session.list_tools(cursor=cursor)
                tools.extend(list(getattr(result, 'tools', result) or []))
                next_cursor = getattr(result, 'nextCursor', None)
                if next_cursor is None:
                    return server_name, tools
                if not isinstance(next_cursor, str) or not next_cursor:
                    raise MCPToolDiscoveryError(
                        'invalid_cursor',
                        'MCP服务返回了无效的工具分页游标，工具清单未更新。',
                        cause_type=type(next_cursor).__name__,
                    )
                if next_cursor in seen_cursors:
                    raise MCPToolDiscoveryError(
                        'repeated_cursor',
                        'MCP服务重复返回相同的工具分页游标，工具清单未更新。',
                        cause_type='RepeatedCursor',
                    )
                seen_cursors.add(next_cursor)
                cursor = next_cursor

    tasks = [asyncio.create_task(list_server_tools(name)) for name in connections]
    try:
        return await asyncio.gather(*tasks)
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)


def discover_mcp_tools(
    raw_config: str | dict[str, Any],
    *,
    timeout_seconds: float = DEFAULT_DISCOVERY_TIMEOUT_SECONDS,
    client_factory: Callable | None = None,
) -> list[dict[str, Any]]:
    """Call MCP ``tools/list`` and return a persistence-ready tool snapshot."""
    try:
        config = json.loads(raw_config) if isinstance(raw_config, str) else raw_config
        if not isinstance(config, dict):
            raise ValueError('root config must be an object')
        mcp_servers = config.get('mcpServers')
        if not isinstance(mcp_servers, dict) or not mcp_servers:
            raise ValueError('mcpServers must be a non-empty object')
        connections = build_mcp_connections(mcp_servers)
        if set(connections) != set(mcp_servers):
            missing_count = len(set(mcp_servers) - set(connections))
            raise MCPToolDiscoveryError(
                'invalid_server_config',
                f'有{missing_count}个MCP服务配置无法用于工具检测，请检查command或url。',
                cause_type='InvalidServerConfiguration',
            )
        if not connections:
            raise ValueError('no supported MCP connections')
        if client_factory is None:
            from langchain_mcp_adapters.client import MultiServerMCPClient
            client_factory = MultiServerMCPClient

        async def run_with_timeout():
            return await asyncio.wait_for(
                _discover_async(connections, client_factory),
                timeout=timeout_seconds,
            )

        discovered = asyncio.run(run_with_timeout())
        tools: list[dict[str, Any]] = []
        for server_name, server_tools in discovered:
            for tool in server_tools:
                name = str(getattr(tool, 'name', '') or '').strip()
                if not name:
                    raise MCPToolDiscoveryError(
                        'missing_tool_name',
                        'MCP服务返回了缺少名称的工具，工具清单未更新。',
                        cause_type='MissingToolName',
                    )
                tools.append({
                    'name': name,
                    'description': str(getattr(tool, 'description', '') or ''),
                    'tool_schema': _tool_schema(tool),
                    'server_name': server_name,
                })
        validate_discovered_tools(tools)
        return tools
    except MCPToolDiscoveryError:
        raise
    except Exception as exc:
        raise _public_error(exc, timeout_seconds) from exc
