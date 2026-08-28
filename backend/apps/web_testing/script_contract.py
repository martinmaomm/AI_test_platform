"""Web UI Python Playwright script contract.

Database scripts contain only an async ``run(page)`` business function.  The
execution service owns the browser lifecycle and creates the synchronous
pytest entry point used by Celery.
"""

from __future__ import annotations

import ast
import keyword
import re
import textwrap
from dataclasses import dataclass
from typing import Optional

from .script_extraction import extract_playwright_metadata, redact_sensitive_text


class ScriptContractError(ValueError):
    """Raised when a script cannot be safely validated or normalized."""


@dataclass(frozen=True)
class NormalizedScript:
    content: str
    legacy: bool = False
    warning: Optional[str] = None


SCRIPT_SOURCE_VALUES = {
    "manual",
    "requirement_ai",
    "mcp_exploration",
    "step_generator",
    "legacy",
}
SCRIPT_FRAMEWORK = "playwright_python_async"
_SENSITIVE_METADATA_KEY_RE = re.compile(
    r"(?i)(password|passwd|token|secret|api[_ -]?key|authorization|credential|"
    r"用户名|账号|帐号|密码|口令|令牌)"
)


def _parse(content: str) -> ast.Module:
    if not isinstance(content, str) or not content.strip():
        raise ScriptContractError("脚本内容为空，无法执行")
    try:
        return ast.parse(content, filename="webui_test_script.py")
    except SyntaxError as exc:
        location = f"第 {exc.lineno} 行" if exc.lineno else "脚本"
        detail = f"：{exc.msg}" if exc.msg else ""
        raise ScriptContractError(f"脚本语法错误（{location}{detail}）") from exc


def _function(module: ast.Module, name: str) -> Optional[ast.AsyncFunctionDef]:
    return next(
        (node for node in module.body if isinstance(node, ast.AsyncFunctionDef) and node.name == name),
        None,
    )


def _uses_browser_lifecycle(node: ast.AST) -> bool:
    if node is None:
        return False
    for child in ast.walk(node):
        if isinstance(child, ast.Call) and isinstance(child.func, ast.Attribute):
            if child.func.attr in {"launch", "launch_persistent_context", "new_page"}:
                return True
        if isinstance(child, ast.Name) and child.id == "async_playwright":
            return True
    return False


def validate_script(content: str) -> NormalizedScript:
    """Validate a script and return its contract metadata.

    New scripts must expose ``async def run(page)`` and must not manage a
    browser.  A legacy ``async def main(...)`` is accepted for compatibility;
    :func:`normalize_script` converts it to the new entry point.
    """

    module = _parse(content)
    run = _function(module, "run")
    main = _function(module, "main")

    if run:
        args = run.args
        if (
            args.posonlyargs
            or len(args.args) != 1
            or args.args[0].arg != "page"
            or args.defaults
            or args.kwonlyargs
            or args.kw_defaults
            or args.vararg
            or args.kwarg
        ):
            raise ScriptContractError("run 函数必须定义为 async def run(page)")
        if _uses_browser_lifecycle(run):
            raise ScriptContractError(
                "run(page) 不得创建或管理浏览器，请移除 async_playwright/launch/new_page，交由统一执行器管理"
            )
        warning = None
        if main:
            warning = "脚本同时包含 run 和旧版 main，执行时优先使用 run(page)"
        return NormalizedScript(content=content.strip(), warning=warning)

    if main:
        if len(main.args.args) > 1:
            raise ScriptContractError("旧脚本的 main 函数参数超过 1 个，无法安全兼容，请人工修改为 async def run(page)")
        return NormalizedScript(
            content=content.strip(),
            legacy=True,
            warning="检测到旧版 async def main，执行时将通过兼容入口调用；建议重新保存或生成脚本",
        )

    raise ScriptContractError("脚本缺少 async def run(page) 入口；旧脚本至少需要 async def main() 才能兼容")


def normalize_for_storage(content: str) -> str:
    """Strictly validate content before writing WebUITestCase.test_script_content."""

    module = _parse(content)
    run = _function(module, "run")
    if not run:
        raise ScriptContractError("保存脚本必须包含 async def run(page)，旧版 main 只能在执行时兼容")

    args = run.args
    if (
        args.posonlyargs
        or len(args.args) != 1
        or args.args[0].arg != "page"
        or args.defaults
        or args.kwonlyargs
        or args.kw_defaults
        or args.vararg
        or args.kwarg
    ):
        raise ScriptContractError("run 函数必须严格定义为 async def run(page)")

    if any(isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "main" for node in module.body):
        raise ScriptContractError("保存脚本不得包含 main 函数；请只保留 async def run(page)")
    if any(
        isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test_")
        for node in module.body
    ):
        raise ScriptContractError("保存脚本不得包含 pytest test_* 入口")
    if _uses_browser_lifecycle(module):
        raise ScriptContractError("保存脚本不得创建或管理浏览器，请交由统一执行器管理")
    if any(
        isinstance(node, ast.If)
        and isinstance(node.test, ast.Compare)
        and any(isinstance(part, ast.Constant) and part.value == "__main__" for part in node.test.comparators)
        for node in module.body
    ):
        raise ScriptContractError("保存脚本不得包含 if __name__ 运行入口")
    return content.strip()


def store_script_content(
    test_case,
    content: Optional[str],
    *,
    source: str = "manual",
    generation_metadata: Optional[dict] = None,
):
    """Persist script content and its metadata in one place.

    A real content replacement and clearing an existing script both advance
    the version.  Initial creation without a script keeps version ``0``.
    Invalid content is rejected before the model is changed.
    """

    if source not in SCRIPT_SOURCE_VALUES:
        raise ScriptContractError(f"不支持的脚本来源: {source}")
    has_content = content is not None and bool(str(content).strip())
    normalized = normalize_for_storage(content) if has_content else None
    old_content = getattr(test_case, "test_script_content", None)
    old_version = int(getattr(test_case, "script_version", 0) or 0)
    should_advance = bool(getattr(test_case, "pk", None)) and (has_content or bool(old_content))

    metadata = _sanitize_metadata(generation_metadata or {})
    update_fields = [
        "test_script_content",
        "script_source",
        "script_status",
        "script_framework",
        "script_version",
        "script_validation_error",
        "generation_metadata",
        "updated_at",
    ]

    if source == "mcp_exploration" and normalized:
        extracted = extract_playwright_metadata(normalized, description=test_case.description or "")
        metadata.update({
            key: extracted[key]
            for key in (
                "extracted_steps",
                "locator_candidates",
                "assertion_candidates",
                "extraction_version",
            )
        })
        test_case.steps = extracted["extracted_steps"]
        test_case.expected_result = extracted["expected_result"]
        update_fields.extend(["steps", "expected_result"])

    test_case.test_script_content = normalized
    test_case.script_source = source
    test_case.script_status = "ready" if normalized else "none"
    test_case.script_framework = SCRIPT_FRAMEWORK
    test_case.script_validation_error = ""
    test_case.generation_metadata = metadata
    test_case.script_version = old_version + 1 if should_advance else old_version
    test_case.save(update_fields=update_fields)
    return test_case


def _sanitize_metadata(value):
    """Recursively redact strings before metadata is persisted as JSON."""

    if isinstance(value, str):
        return redact_sensitive_text(value)
    if isinstance(value, dict):
        sanitized = {}
        for key, item in value.items():
            key_text = str(key)
            sanitized[key_text] = (
                '<redacted>'
                if _SENSITIVE_METADATA_KEY_RE.search(key_text)
                else _sanitize_metadata(item)
            )
        return sanitized
    if isinstance(value, list):
        return [_sanitize_metadata(item) for item in value]
    if isinstance(value, tuple):
        return [_sanitize_metadata(item) for item in value]
    return value


def normalize_script(content: str) -> NormalizedScript:
    """Normalize a contract-compatible or conservatively compatible script."""

    result = validate_script(content)
    if not result.legacy:
        return result

    module = _parse(result.content)
    main = _function(module, "main")
    call = "await main(page)" if main and len(main.args.args) == 1 else "await main()"
    normalized = result.content + f"\n\nasync def run(page):\n    {call}\n"
    return NormalizedScript(content=normalized, legacy=True, warning=result.warning)


def materialize_script(
    content: str,
    test_name: str,
    *,
    browser: str = "chromium",
    headed: bool = True,
    base_url: Optional[str] = None,
    suite_name: Optional[str] = None,
) -> str:
    """Create the pytest file content for one async business script.

    The generated wrapper is synchronous for pytest, but drives the async
    Playwright API with ``asyncio.run``.  Browser/context/page lifecycle and
    the optional context base URL are owned exclusively by this wrapper.
    """

    original = validate_script(content)
    original_has_run = _function(_parse(content), "run") is not None
    original_main = _function(_parse(content), "main")
    if (
        original.legacy
        and not original_has_run
        and original_main
        and len(original_main.args.args) == 1
        and _uses_browser_lifecycle(original_main)
    ):
        raise ScriptContractError(
            "旧版 main(page) 同时自行管理浏览器，无法安全兼容；请改为仅保留 async def run(page)"
        )
    normalized = normalize_script(content)
    if browser not in {"chromium", "firefox", "webkit"}:
        raise ScriptContractError("browser 只支持 chromium、firefox 或 webkit")
    safe_name = "".join(ch if ch.isalnum() or ch == "_" else "_" for ch in str(test_name)) or "webui_test"
    if safe_name[0].isdigit() or keyword.iskeyword(safe_name) or not safe_name.isidentifier():
        safe_name = f"test_{safe_name}"
    browser_literal = repr(browser)
    base_url_literal = repr(base_url) if base_url else "None"
    suite_decorator = f"@allure.suite({suite_name!r})\n" if suite_name else ""
    allure_import = "import allure\n" if suite_name else ""

    # A script that already has run(page) always uses the managed browser,
    # even when an old main() remains in the same file.
    module = _parse(normalized.content)
    if original.legacy and not original_has_run and _uses_browser_lifecycle(_function(module, "main")):
        wrapper = f'''

import asyncio
{allure_import}
{suite_decorator}def {safe_name}():
    asyncio.run(main())
'''
        return textwrap.dedent(normalized.content + wrapper).strip() + "\n"

    wrapper = f'''

import asyncio
import os
from playwright.async_api import async_playwright
{allure_import}
async def _run_with_managed_browser():
    async with async_playwright() as playwright:
        browser_type = getattr(playwright, {browser_literal})
        browser = await browser_type.launch(headless={not headed!r})
        context_kwargs = {{}}
        base_url = {base_url_literal} or os.environ.get("PLAYWRIGHT_BASE_URL")
        if base_url:
            context_kwargs["base_url"] = base_url.rstrip("/")
        context = await browser.new_context(**context_kwargs)
        page = await context.new_page()
        try:
            await run(page)
        finally:
            await context.close()
            await browser.close()

{suite_decorator}def {safe_name}():
    asyncio.run(_run_with_managed_browser())
'''
    return textwrap.dedent(normalized.content + wrapper).strip() + "\n"
