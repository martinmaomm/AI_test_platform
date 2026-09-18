"""HTTPS-only JSON transport with bounded retries and strict response envelopes."""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any
from urllib.parse import urlsplit

import requests

from .errors import ProtocolError, RequestRejected, RetryExhausted

MAX_ATTEMPTS = 3


class AgentTransport:
    def __init__(
        self,
        verify: str | bool = True,
        session: requests.Session | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ):
        if verify is False:
            raise ValueError("TLS 证书校验不能关闭")
        self.verify = verify
        self.session = session or requests.Session()
        # Node credentials must not be replaced by process-wide proxies or .netrc.
        self.session.trust_env = False
        self.sleep = sleep

    def post(
        self,
        url: str,
        payload: dict[str, Any],
        token: str | None = None,
        *,
        retry_transient: bool = True,
    ) -> dict[str, Any]:
        _validate_endpoint(url)
        headers = {"Accept": "application/json"}
        if token is not None:
            headers["Authorization"] = f"Node {token}"
        attempts = MAX_ATTEMPTS if retry_transient else 1
        for attempt in range(attempts):
            try:
                response = self.session.post(
                    url,
                    json=payload,
                    headers=headers,
                    timeout=(5, 15),
                    verify=self.verify,
                    allow_redirects=False,
                )
            except requests.RequestException as exc:
                if attempt == attempts - 1:
                    raise RetryExhausted("网络请求失败，已停止本次尝试") from exc
                self.sleep(2**attempt)
                continue
            if response.is_redirect:
                raise RequestRejected("服务端重定向被拒绝")
            if response.status_code in {401, 403, 409}:
                raise RequestRejected(f"身份或协议被服务端拒绝（HTTP {response.status_code}）")
            if response.status_code == 429 or response.status_code >= 500:
                if attempt == attempts - 1:
                    raise RetryExhausted(f"服务暂时不可用（HTTP {response.status_code}）")
                self.sleep(2**attempt)
                continue
            if response.status_code < 200 or response.status_code >= 300:
                raise RequestRejected(f"服务端拒绝请求（HTTP {response.status_code}）")
            return _unwrap(response)
        raise AssertionError("unreachable")


def _unwrap(response: requests.Response) -> dict[str, Any]:
    try:
        wrapped = response.json()
    except ValueError as exc:
        raise ProtocolError("服务端响应不是有效 JSON") from exc
    if not isinstance(wrapped, dict) or set(wrapped) != {"success", "data"}:
        raise ProtocolError("服务端响应包装不符合协议")
    if wrapped["success"] is not True or not isinstance(wrapped["data"], dict):
        raise ProtocolError("服务端响应包装不符合协议")
    return wrapped["data"]


def _validate_endpoint(url: object) -> None:
    """Keep direct transport use inside the same HTTPS boundary as NodeConfig."""
    if not isinstance(url, str) or not url or any(char == "\\" or char.isspace() or ord(char) < 32 or ord(char) == 127 for char in url):
        raise ProtocolError("请求 URL 不安全")
    if "?" in url or "#" in url:
        raise ProtocolError("请求 URL 不安全")
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except ValueError as exc:
        raise ProtocolError("请求 URL 不安全") from exc
    if (
        parsed.scheme != "https"
        or not parsed.netloc
        or not parsed.hostname
        or port == 0
        or parsed.username is not None
        or parsed.password is not None
        or (port is None and parsed.netloc.endswith(":"))
    ):
        raise ProtocolError("请求 URL 不安全")
