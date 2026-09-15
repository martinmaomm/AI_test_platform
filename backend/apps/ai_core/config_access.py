"""Shared runtime access to platform-managed AI configurations.

``created_by`` is audit metadata only. Runtime callers deliberately do not
filter configurations by the requesting user or by the creator's current role.
"""

from django.db.models import QuerySet

from .models import LLMConfiguration, MCPConfiguration, ModelType


def usable_llm_configurations() -> QuerySet[LLMConfiguration]:
    """Return globally usable chat-model configurations."""
    return LLMConfiguration.objects.filter(
        model_type=ModelType.LLM,
        is_active=True,
    )


def usable_mcp_configurations() -> QuerySet[MCPConfiguration]:
    """Return globally usable MCP configurations."""
    return MCPConfiguration.objects.filter(is_active=True)
