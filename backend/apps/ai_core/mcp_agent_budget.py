"""Project-level MCPAgent limits compatible with LangChain's agent graph."""

from langchain.agents import create_agent
from langchain.agents.middleware import ModelCallLimitMiddleware
from langchain_core.language_models.chat_models import BaseChatModel
from mcp_use import MCPAgent
from mcp_use.agents.middleware import tool_error_handler


def mcp_graph_recursion_limit(max_steps: int) -> int:
    """Allow LangChain's before/model/after/tools graph nodes for each model turn."""
    return (max_steps * 4) + 4


class BudgetedMCPAgent(MCPAgent):
    """Keep mcp-use's model budget while sizing its LangGraph recursion budget."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if not self._is_remote:
            self.recursion_limit = mcp_graph_recursion_limit(self.max_steps)

    def _create_agent(self):
        """Use an explicit model-limit error instead of a synthetic final answer."""
        system_prompt = self._system_message or "You are a helpful assistant"
        middleware = []
        if self.retry_on_error:
            middleware.append(tool_error_handler)
        middleware.append(
            ModelCallLimitMiddleware(run_limit=self.max_steps, exit_behavior="error")
        )

        llm_model = self.llm
        assert isinstance(llm_model, BaseChatModel), "LLM must be a BaseChatModel instance"
        return create_agent(
            model=llm_model,
            tools=self._tools,
            system_prompt=system_prompt,
            middleware=middleware,
            debug=self.verbose,
        ).with_config({"recursion_limit": self.recursion_limit})
