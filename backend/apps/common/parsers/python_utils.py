"""Extract model-produced Python without rewriting or stitching source lines."""

import ast
import logging
import re

logger = logging.getLogger(__name__)


def _is_python(source: str) -> bool:
    try:
        return bool(ast.parse(source).body)
    except (SyntaxError, ValueError, RecursionError):
        return False


def extract_python_from_output(raw_output: str) -> str:
    """Return a whole script, or an explicit Python fence for caller validation.

    Never filter by keywords: ordinary assignments, multiline arguments and
    closing parentheses are essential source too. Extraction does not imply
    that the script satisfies the platform's execution contract.
    """
    if not isinstance(raw_output, str) or not raw_output.strip():
        return ""
    source = raw_output.strip()
    # A valid script may itself contain Markdown fences in a string/docstring.
    if _is_python(source):
        return source

    blocks = re.findall(
        r'^```([\w-]*)[ \t]*\r?\n(.*?)^```[ \t]*$',
        source, re.MULTILINE | re.DOTALL,
    )
    python_blocks = [code.strip() for language, code in blocks if language.lower() in {'python', 'py'}]
    if python_blocks:
        # Preserve invalid explicit source as well; the caller can report the
        # actual syntax error rather than silently keeping a previous draft.
        return python_blocks[-1]
    for language, code in reversed(blocks):
        if not language and _is_python(code.strip()):
            return code.strip()
    logger.warning('模型输出未包含完整 Python 源码或明确的 Python 代码块')
    return ""
