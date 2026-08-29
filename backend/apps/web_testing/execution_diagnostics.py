"""Deterministic, user-facing diagnostics for Python Playwright execution output."""

from __future__ import annotations

import re
import os
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class FailureDiagnostic:
    category: str
    title: str
    action: Optional[str] = None
    target: Optional[str] = None
    timeout_ms: Optional[int] = None
    suggestion: str = ''
    technical_message: str = ''
    summary: str = ''


_ACTIONS = {
    'click': ('点击', '点击元素超时', '请确认目标元素已显示、未被遮挡且当前页面正确'),
    'fill': ('输入', '输入元素超时', '请确认输入框已显示、可编辑且当前页面正确'),
    'check': ('勾选', '勾选元素超时', '请确认复选框已显示且可操作'),
    'select_option': ('选择', '选择选项超时', '请确认下拉框已显示且包含目标选项'),
    'press': ('按键', '按键操作超时', '请确认目标元素已显示并支持该按键'),
    'hover': ('悬停', '悬停元素超时', '请确认目标元素已显示且未被其他元素遮挡'),
}


def _technical_message(text: str) -> str:
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith(('====', '----', '___')):
            continue
        if stripped.startswith('E   '):
            stripped = stripped[4:].strip()
        if re.search(r'(Error|Exception|Timeout|Assertion|strict mode|net::ERR)', stripped, re.I):
            return stripped[:1000]
    for line in text.splitlines():
        stripped = line.strip()
        if stripped:
            return stripped[:1000]
    return ''


def _extract_timeout(text: str) -> Optional[int]:
    match = re.search(r'Timeout\s+(\d+)\s*ms', text, re.I)
    return int(match.group(1)) if match else None


def _extract_target(text: str) -> Optional[str]:
    patterns = (
        (r'get_by_role\(\s*["\']([^"\']+)["\']\s*,\s*name\s*=\s*["\']([^"\']+)["\']',
         lambda m: f'按钮“{m.group(2)}”' if m.group(1).lower() == 'button' else f'{m.group(1)}“{m.group(2)}”'),
        (r'get_by_text\(\s*["\']([^"\']+)["\']', lambda m: f'文本“{m.group(1)}”'),
        (r'get_by_label\(\s*["\']([^"\']+)["\']', lambda m: f'标签为“{m.group(1)}”的输入项'),
        (r'get_by_placeholder\(\s*["\']([^"\']+)["\']', lambda m: f'占位提示为“{m.group(1)}”的输入项'),
        (r'locator\(\s*["\']([^"\']+)["\']', lambda m: f'定位器“{m.group(1)}”'),
    )
    for pattern, formatter in patterns:
        match = re.search(pattern, text)
        if match:
            return formatter(match)
    return None


def _format_summary(title: str, action: Optional[str], target: Optional[str], timeout_ms: Optional[int], suggestion: str) -> str:
    lines = [title]
    if target:
        lines.append(f'操作对象：{target}')
    if timeout_ms is not None:
        lines.append(f'等待时间：{timeout_ms / 1000:g} 秒')
    if suggestion:
        lines.append(f'建议：{suggestion}')
    return '\n'.join(lines)


def diagnose_failure(stdout: str = '', stderr: str = '') -> FailureDiagnostic:
    """Classify known Playwright/Pytest failures without guessing missing details."""

    text = '\n'.join(part for part in (stdout or '', stderr or '') if part)
    technical = _technical_message(text)
    timeout_ms = _extract_timeout(text)
    target = _extract_target(text)

    for operation, (action, title, suggestion) in _ACTIONS.items():
        if re.search(rf'(?:Locator\.)?{re.escape(operation)}\b', text, re.I) and re.search(
            r'(TimeoutError|Timeout\s+\d+\s*ms)', text, re.I
        ):
            return FailureDiagnostic(
                category='action_timeout', title=title, action=action,
                target=target, timeout_ms=timeout_ms, suggestion=suggestion,
                technical_message=technical,
                summary=_format_summary(title, action, target, timeout_ms, suggestion),
            )

    if re.search(r'expect\s*\(|to_be_|to_have_|AssertionError', text, re.I):
        title = '页面校验未通过'
        suggestion = '请检查页面实际状态是否符合断言条件'
        return FailureDiagnostic(
            category='assertion_failure', title=title, target=target,
            timeout_ms=timeout_ms, suggestion=suggestion, technical_message=technical,
            summary=_format_summary(title, None, target, timeout_ms, suggestion),
        )

    if re.search(r'strict mode violation', text, re.I):
        title = '定位元素不唯一'
        suggestion = '请缩小定位范围，确保定位器只匹配一个元素'
        return FailureDiagnostic(
            category='strict_mode', title=title, target=target,
            suggestion=suggestion, technical_message=technical,
            summary=_format_summary(title, None, target, None, suggestion),
        )

    if re.search(r'page\.goto.*net::ERR_|net::ERR_[A-Z_]+', text, re.I):
        title = '页面访问失败'
        suggestion = '请检查测试环境地址、网络连接和目标服务状态'
        return FailureDiagnostic(
            category='navigation_error', title=title, target=target,
            suggestion=suggestion, technical_message=technical,
            summary=_format_summary(title, None, target, None, suggestion),
        )

    if re.search(r'TargetClosedError|Target page, context or browser has been closed', text, re.I):
        title = '页面或浏览器已提前关闭'
        suggestion = '请检查脚本是否提前关闭了页面或浏览器'
        return FailureDiagnostic(
            category='target_closed', title=title, suggestion=suggestion,
            technical_message=technical,
            summary=_format_summary(title, None, None, None, suggestion),
        )

    if re.search(r'SyntaxError|ImportError|ModuleNotFoundError|cannot import name', text, re.I):
        title = '测试脚本无法启动'
        suggestion = '请检查脚本语法、导入模块和运行环境依赖'
        return FailureDiagnostic(
            category='script_error', title=title, suggestion=suggestion,
            technical_message=technical,
            summary=_format_summary(title, None, None, None, suggestion),
        )

    title = '测试执行失败'
    suggestion = '请展开技术日志查看详细信息'
    return FailureDiagnostic(
        category='unknown', title=title, suggestion=suggestion,
        technical_message=technical,
        summary=_format_summary(title, None, None, None, suggestion),
    )


def friendly_failure_summary(stdout: str = '', stderr: str = '', fallback: str = '') -> str:
    """Return a friendly summary for recognized failures, preserving explicit fallback errors."""

    diagnostic = diagnose_failure(stdout, stderr)
    if diagnostic.category != 'unknown':
        return diagnostic.summary
    if fallback and not re.search(
        r'(Traceback|playwright|Locator\.|TimeoutError|AssertionError|[A-Za-z]+Error\b)',
        fallback,
        re.I,
    ):
        return fallback
    return diagnostic.summary


def safe_screenshot_relative_path(value) -> Optional[str]:
    """Return a controlled relative PNG path, hiding absolute or traversing paths."""
    if not value or os.path.isabs(str(value)):
        return None
    normalized = str(value).replace('\\', '/')
    if not normalized.startswith('webui_failure_screenshots/'):
        return None
    if '..' in normalized.split('/') or not normalized.endswith('.png'):
        return None
    return normalized
