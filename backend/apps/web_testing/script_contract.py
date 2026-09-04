"""Web UI Python Playwright script contract.

Database scripts contain an async ``run(page)`` or v3 ``run(page, variables)`` business function.  The
execution service owns the browser lifecycle and creates the synchronous
pytest entry point used by Celery.
"""

from __future__ import annotations

import ast
import keyword
import textwrap
from dataclasses import dataclass
from typing import Optional

from .constants import WEBUI_BROWSER_ENGINE
from .assertion_state import instrument_runtime_assertions
from .script_extraction import extract_playwright_metadata
from .target_urls import validate_target_url


class ScriptContractError(ValueError):
    """Raised when a script cannot be safely validated or normalized."""


@dataclass(frozen=True)
class NormalizedScript:
    content: str
    legacy: bool = False
    warning: Optional[str] = None


SCRIPT_SOURCE_VALUES = {
    "manual",
    "mcp_exploration",
}
SCRIPT_FRAMEWORK = "playwright_python_async"

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


def _valid_run_signature(run: ast.AsyncFunctionDef) -> bool:
    """Accept the established page entry and the v3 variable-aware entry."""
    args = run.args
    names = [item.arg for item in args.args]
    return (
        not args.posonlyargs
        and names in (['page'], ['page', 'variables'])
        and not args.defaults
        and not args.kwonlyargs
        and not args.kw_defaults
        and not args.vararg
        and not args.kwarg
    )


def _validate_static_goto_urls(module: ast.Module) -> None:
    """Require literal navigation targets to be absolute HTTP(S) URLs.

    Dynamic expressions remain valid so a manually maintained script can use
    its explicit ``variables`` mapping.  Their runtime value cannot be
    established from the stored source alone.
    """
    for node in ast.walk(module):
        if not (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "goto"
        ):
            continue
        target = node.args[0] if node.args else next(
            (keyword.value for keyword in node.keywords if keyword.arg == "url"),
            None,
        )
        if target is None:
            continue
        if not isinstance(target, ast.Constant) or not isinstance(target.value, str):
            continue
        try:
            validate_target_url(target.value)
        except ValueError as exc:
            raise ScriptContractError(
                "保存脚本中的 page.goto 静态 URL 必须是完整的 http(s) 地址；不要使用相对路径"
            ) from exc


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
            not _valid_run_signature(run)
        ):
            raise ScriptContractError("run 函数必须定义为 async def run(page) 或 async def run(page, variables)")
        if _uses_browser_lifecycle(run):
            raise ScriptContractError(
                "run 不得创建或管理浏览器，请移除 async_playwright/launch/new_page，交由统一执行器管理"
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
        raise ScriptContractError("保存脚本必须包含 async def run(page) 或 async def run(page, variables)")

    args = run.args
    if (
        not _valid_run_signature(run)
    ):
        raise ScriptContractError("run 函数必须严格定义为 async def run(page) 或 async def run(page, variables)")

    if any(isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "main" for node in module.body):
        raise ScriptContractError("保存脚本不得包含 main 函数；请只保留 async def run(page)")
    if any(
        isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test_")
        for node in module.body
    ):
        raise ScriptContractError("保存脚本不得包含 pytest test_* 入口")
    if _uses_browser_lifecycle(module):
        raise ScriptContractError("保存脚本不得创建或管理浏览器，请交由统一执行器管理")
    _validate_static_goto_urls(module)
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
    # Re-saving byte-identical content updates provenance but not the executable version.
    should_advance = bool(getattr(test_case, "pk", None)) and normalized != old_content

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
                "assertion_state",
                "extraction_version",
            )
        })

    test_case.test_script_content = normalized
    test_case.script_source = source
    test_case.script_status = "ready" if normalized else "none"
    test_case.script_framework = SCRIPT_FRAMEWORK
    test_case.script_validation_error = ""
    test_case.generation_metadata = metadata
    test_case.script_version = old_version + 1 if should_advance else old_version
    if should_advance:
        # Execution records retain history; a changed script has not run yet.
        test_case.last_execute_status = 'untested'
        test_case.last_execute_time = None
        test_case.last_error_message = ''
        update_fields.extend(['last_execute_status', 'last_execute_time', 'last_error_message'])
    test_case.save(update_fields=update_fields)
    return test_case


def _sanitize_metadata(value):
    """Normalize JSON-compatible metadata without altering test credentials."""

    if isinstance(value, dict):
        return {str(key): _sanitize_metadata(item) for key, item in value.items()}
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
    headed: bool = True,
    suite_name: Optional[str] = None,
    failure_screenshot_path: Optional[str] = None,
    runtime_assertion_count_path: Optional[str] = None,
) -> str:
    """Create the pytest file content for one async business script.

    The generated wrapper is synchronous for pytest, but drives the async
    Playwright API with ``asyncio.run``.  Browser/context/page lifecycle is
    owned exclusively by this wrapper; business scripts provide full URLs.
    """

    original = validate_script(content)
    _validate_static_goto_urls(_parse(content))
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
    safe_name = "".join(ch if ch.isalnum() or ch == "_" else "_" for ch in str(test_name)) or "webui_test"
    if safe_name[0].isdigit() or keyword.iskeyword(safe_name) or not safe_name.isidentifier():
        safe_name = f"test_{safe_name}"
    browser_literal = repr(WEBUI_BROWSER_ENGINE)
    screenshot_path_literal = repr(failure_screenshot_path) if failure_screenshot_path else "None"
    runtime_count_path_literal = repr(runtime_assertion_count_path) if runtime_assertion_count_path else "None"
    suite_decorator = f"@allure.suite({suite_name!r})\n" if suite_name else ""
    allure_import = "import allure\n" if suite_name else ""

    # A script that already has run always uses the managed browser,
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

    run_args = [item.arg for item in _function(module, "run").args.args]
    run_call = 'await run(page, runtime_variables)' if run_args == ['page', 'variables'] else 'await run(page)'
    # This temporary instrumentation records only assertions that returned
    # successfully.  It neither changes the saved source nor catches a user
    # assertion exception; explicit asserts are followed by a counter only
    # after Python has evaluated them successfully.
    instrumented_content = instrument_runtime_assertions(normalized.content)
    runtime_support = f'''

import inspect
_aits_runtime_assertion_count = 0

def _aits_record_assertion():
    global _aits_runtime_assertion_count
    _aits_runtime_assertion_count += 1

def _aits_write_assertion_count():
    if not {runtime_count_path_literal}:
        return
    try:
        with open({runtime_count_path_literal}, "w", encoding="utf-8") as __aits_handle:
            json.dump({{"runtime_assertion_count": _aits_runtime_assertion_count}}, __aits_handle)
    except OSError:
        pass

try:
    _aits_original_expect = expect
except NameError:
    _aits_original_expect = None

if _aits_original_expect is not None:
    class _AITSExpectationProxy:
        def __init__(self, value):
            self._aits_value = value

        def __getattr__(self, name):
            original = getattr(self._aits_value, name)
            if not name.startswith(("to_", "not_to_")):
                return original

            def tracked(*args, **kwargs):
                result = original(*args, **kwargs)
                if not inspect.isawaitable(result):
                    return result

                async def await_and_record():
                    value = await result
                    globals()["_aits_record_assertion"]()
                    return value

                return await_and_record()

            return tracked

    def expect(*args, **kwargs):
        return _AITSExpectationProxy(_aits_original_expect(*args, **kwargs))
'''
    wrapper = f'''

import asyncio
import json
import logging
import os
import sys
from playwright.async_api import async_playwright
{allure_import}
async def _run_with_managed_browser():
    async with async_playwright() as playwright:
        browser_type = getattr(playwright, {browser_literal})
        browser = await browser_type.launch(headless={not headed!r})
        context = await browser.new_context()
        page = await context.new_page()
        try:
            runtime_variables = json.loads(os.environ.get("WEBUI_RUNTIME_VARIABLES", "{{}}"))
        except (TypeError, ValueError):
            runtime_variables = {{}}
        if not isinstance(runtime_variables, dict):
            runtime_variables = {{}}
        try:
            {run_call}
        finally:
            if {screenshot_path_literal}:
                try:
                    await page.screenshot(
                        path={screenshot_path_literal}, full_page=True, timeout=5000,
                    )
                except Exception as screenshot_error:
                    logging.getLogger(__name__).warning(
                        "执行结束截图生成失败: %s", screenshot_error
                    )
            _aits_active_exception = sys.exc_info()[0] is not None
            _aits_close_error = None
            try:
                await context.close()
            except Exception as close_error:
                _aits_close_error = close_error
                if _aits_active_exception:
                    logging.getLogger(__name__).warning(
                        "上下文关闭失败（保留原始执行异常）: %s", close_error
                    )
            try:
                await browser.close()
            except Exception as close_error:
                if _aits_active_exception:
                    logging.getLogger(__name__).warning(
                        "浏览器关闭失败（保留原始执行异常）: %s", close_error
                    )
                elif _aits_close_error is None:
                    _aits_close_error = close_error
                else:
                    logging.getLogger(__name__).warning(
                        "浏览器关闭失败（保留先前关闭异常）: %s", close_error
                    )
            finally:
                _aits_write_assertion_count()
            if _aits_close_error is not None and not _aits_active_exception:
                raise _aits_close_error

{suite_decorator}def {safe_name}():
    asyncio.run(_run_with_managed_browser())
'''
    return textwrap.dedent(instrumented_content + runtime_support + wrapper).strip() + "\n"
