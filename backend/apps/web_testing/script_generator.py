"""LLM-only generation of canonical Python Playwright scripts.

This module intentionally has no MCP client or browser dependency.  It can only
turn previously persisted, sanitised evidence into a script draft.
"""

from __future__ import annotations

import json
import re
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from .generation_contracts import ExplorationSnapshot, ScenarioSpec
from .generation_security import redact_exploration_metadata, redact_metadata, redact_text


GENERATOR_SYSTEM_PROMPT = """你是 Python Playwright 异步测试脚本生成器。只依据给定场景和页面探索证据生成代码，
不得调用 MCP、不得探索页面、不得编造定位器或业务字段。只输出 Python 源码，不输出 Markdown。

脚本约束：
1. 入口必须是 `async def run(page):`；不能创建或关闭 browser/context/page。
2. 所有页面跳转只能使用相对路径；账号密码只能从 `os.environ` 读取，绝不能写入脚本。
3. 文件顶部用已脱敏模块 docstring 写明“场景、目标、前置条件、清理策略”。
4. 每个业务动作前写 `# 步骤 N：中文可读名称`；每个 expect 前写 `# 断言 N：中文可读名称`；清理逻辑写 `# 清理：中文说明`。
5. 代码应让新手能顺着注释理解，每个动作和注释一一对应；有写操作必须使用 try/finally 清理本轮数据。
6. 未确认页面证据时不要猜测定位器，保留可读 TODO 并让脚本进入人工检查。"""

REPAIR_SYSTEM_PROMPT = """你是 Python Playwright 脚本静态修复器。只修复给定质量问题，不能调用 MCP 或浏览器，
不得新增未提供的页面事实、定位器、业务字段或敏感值。只输出完整 Python 源码。仍必须保留 async def run(page)、
模块 docstring、步骤/断言/清理中文注释、相对 URL 和环境变量凭据读取。"""


def _response_text(value: Any) -> str:
    """Extract model text from strings, AIMessage objects and content blocks."""
    if isinstance(value, str):
        return value
    content = getattr(value, 'content', value)
    if isinstance(content, str):
        return content
    if isinstance(content, (list, tuple)):
        chunks: list[str] = []
        for block in content:
            if isinstance(block, str):
                chunks.append(block)
            elif isinstance(block, dict):
                text = block.get('text') or block.get('content')
                if isinstance(text, str):
                    chunks.append(text)
            else:
                text = getattr(block, 'text', None) or getattr(block, 'content', None)
                if isinstance(text, str):
                    chunks.append(text)
        return '\n'.join(chunks)
    return str(content or '')


def _strip_code_fences(value: Any) -> str:
    text = _response_text(value).strip()
    text = re.sub(r'^```(?:python)?\s*', '', text, flags=re.IGNORECASE)
    return re.sub(r'\s*```$', '', text).strip()


class ScriptGenerator:
    """Thin model adapter that receives no credentials or live browser objects."""

    def __init__(self, llm_model: Any):
        self.llm_model = llm_model

    def generate(
        self,
        *,
        scenario: ScenarioSpec,
        snapshot: ExplorationSnapshot,
    ) -> str:
        payload = {
            'scenario': redact_metadata(scenario.model_dump(mode='json')),
            'exploration_snapshot': redact_exploration_metadata(snapshot.model_dump(mode='json')),
        }
        return self._invoke(GENERATOR_SYSTEM_PROMPT, payload)

    def repair(
        self,
        *,
        script: str,
        issues: list[dict[str, Any]],
        scenario: ScenarioSpec,
        snapshot: ExplorationSnapshot,
    ) -> str:
        payload = {
            'script': redact_text(script),
            'quality_issues': redact_metadata(issues),
            'scenario': redact_metadata(scenario.model_dump(mode='json')),
            'exploration_evidence': redact_exploration_metadata(snapshot.model_dump(mode='json')),
        }
        return self._invoke(REPAIR_SYSTEM_PROMPT, payload)

    def _invoke(self, system_prompt: str, payload: dict[str, Any]) -> str:
        output = self.llm_model.invoke([
            SystemMessage(content=system_prompt),
            HumanMessage(content=json.dumps(payload, ensure_ascii=False)),
        ])
        return _strip_code_fences(output)
