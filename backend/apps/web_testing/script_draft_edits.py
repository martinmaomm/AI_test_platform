"""Pure, optimistic-concurrency edits for generated script drafts."""

from collections.abc import Sequence


MAX_EDITS = 20


def apply_script_edits(
    code,
    *,
    current_revision,
    expected_revision,
    edits,
    max_chars=200000,
):
    """Apply an ordered batch of exact replacements to a script draft.

    The input code is never modified.  A replacement is allowed only when its
    ``old`` text identifies exactly one occurrence in the working draft, so a
    caller cannot accidentally edit an ambiguous or stale location.
    """
    _validate_revision(current_revision, "当前")
    _validate_revision(expected_revision, "期望")
    if current_revision != expected_revision:
        raise ValueError("脚本草稿已更新，请刷新后再提交修改。")

    if not isinstance(code, str):
        raise ValueError("脚本内容必须是字符串。")
    _validate_max_chars(max_chars)
    _validate_edits(edits)

    working_code = code
    for index, edit in enumerate(edits, start=1):
        old, new = _validate_edit(edit, index)
        first = working_code.find(old)
        if first < 0:
            raise ValueError(f"第 {index} 项修改的原文本未在当前脚本中找到。")
        if first != working_code.rfind(old):
            raise ValueError(f"第 {index} 项修改的原文本在当前脚本中出现多次，无法确定修改位置。")
        working_code = working_code.replace(old, new, 1)

    if len(working_code) > max_chars:
        raise ValueError(f"修改后的脚本长度不能超过 {max_chars} 个字符。")
    return working_code


def _validate_revision(revision, label):
    if isinstance(revision, bool) or not isinstance(revision, int):
        raise ValueError(f"{label}修订号必须是整数。")


def _validate_max_chars(max_chars):
    if isinstance(max_chars, bool) or not isinstance(max_chars, int) or max_chars < 0:
        raise ValueError("脚本长度上限必须是非负整数。")


def _validate_edits(edits):
    if isinstance(edits, (str, bytes)) or not isinstance(edits, Sequence):
        raise ValueError("修改项必须是列表或元组。")
    if not 1 <= len(edits) <= MAX_EDITS:
        raise ValueError(f"修改项数量必须在 1 到 {MAX_EDITS} 项之间。")


def _validate_edit(edit, index):
    if not isinstance(edit, dict):
        raise ValueError(f"第 {index} 项修改必须包含 old 和 new 字段。")
    old = edit.get("old")
    new = edit.get("new")
    if not isinstance(old, str) or not old:
        raise ValueError(f"第 {index} 项修改的 old 必须是非空字符串。")
    if not isinstance(new, str):
        raise ValueError(f"第 {index} 项修改的 new 必须是字符串。")
    return old, new
