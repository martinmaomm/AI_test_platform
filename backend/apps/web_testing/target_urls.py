"""Explicit HTTP(S) entry URLs from user descriptions, without environments."""

import ipaddress
import re
from urllib.parse import urlsplit


_URL_RE = re.compile(r'https?://(?:\[[^\]\s]+\])?[^\s<>"\'`\[\]，。；！？、【】「」]*', re.I)
_ENTRY_RE = re.compile(
    r'^\s*(?:[-*]\s*)?(?:目标网址|目标地址|入口网址|入口地址|起始网址|起始地址|target\s+url|entry\s+url)\s*[:：]\s*(.+)$',
    re.I | re.M,
)


def validate_target_url(value: str) -> str:
    """Validate without dropping or rewriting path, query string or fragment."""
    url = str(value or '').strip()
    if not url or re.search(r'[\s\x00-\x1f\x7f\\]', url):
        raise ValueError('请在测试描述中填写完整的 http:// 或 https:// 网址。')
    try:
        parsed = urlsplit(url)
        hostname = parsed.hostname
        port = parsed.port
        if parsed.scheme.lower() not in {'http', 'https'} or not hostname:
            raise ValueError()
        if parsed.username is not None or parsed.password is not None:
            raise ValueError('请将登录账号和密码写在描述正文中，不要放在网址内。')
        if port is not None and not 1 <= port <= 65535:
            raise ValueError()
        if ':' in hostname:
            ipaddress.IPv6Address(hostname)
        else:
            ascii_host = hostname.encode('idna').decode('ascii').rstrip('.')
            if not ascii_host or any(
                not re.fullmatch(r'[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?', label)
                for label in ascii_host.split('.')
            ):
                raise ValueError()
    except (ValueError, UnicodeError) as exc:
        if str(exc).startswith('请将登录'):
            raise ValueError(str(exc)) from exc
        raise ValueError('目标网址格式不正确，请填写包含主机和有效端口的完整 HTTP(S) 网址。') from exc
    return url


def _urls(text: str) -> list[str]:
    found = []
    for match in _URL_RE.finditer(text):
        url = match.group()
        # A Markdown link or prose may wrap the URL. Keep balanced parentheses
        # that genuinely belong to a path rather than stripping all of them.
        for closing, opening in [(')', '('), ('}', '{'), ('）', '（')]:
            while url.endswith(closing) and url.count(closing) > url.count(opening):
                url = url[:-1]
        if url not in found:
            found.append(url)
    return found


def extract_target_url(description: str) -> str:
    """Pick a sole URL, or an explicitly labelled entry among multiple URLs."""
    text = str(description or '')
    urls = _urls(text)
    if not urls:
        raise ValueError('测试描述缺少目标网址，请填写完整的 http:// 或 https:// 网址。')
    labelled = list(dict.fromkeys(
        url for line in _ENTRY_RE.findall(text) for url in _urls(line)
    ))
    candidates = labelled or urls
    if len(candidates) != 1:
        raise ValueError('描述包含多个网址，无法确定入口；请单独一行写“目标网址：完整网址”。')
    return validate_target_url(candidates[0])


def target_origin(url: str) -> tuple[str, str, int]:
    parsed = urlsplit(validate_target_url(url))
    return (
        parsed.scheme.lower(), parsed.hostname.lower(),
        parsed.port or (443 if parsed.scheme.lower() == 'https' else 80),
    )
