"""Shared Web UI execution contract."""

# The product exposes Chrome only; Playwright's API engine name remains chromium.
WEBUI_BROWSER_ENGINE = 'chromium'
WEBUI_BROWSER_DISPLAY_NAME = 'Chrome'
WEBUI_DEFAULT_HEADED = True
WEBUI_DEFAULT_TIMEOUT = 300
WEBUI_MIN_TIMEOUT = 30
WEBUI_MAX_TIMEOUT = 1800


def normalize_webui_execution_options(options=None):
    """Validate the two execution options supported by the platform."""
    if options is None:
        options = {}
    if not isinstance(options, dict):
        raise ValueError('WebUI执行参数必须是对象')

    headed = options.get('headed', WEBUI_DEFAULT_HEADED)
    if not isinstance(headed, bool):
        raise ValueError('显示模式参数 headed 必须是布尔值')

    timeout = options.get('timeout', WEBUI_DEFAULT_TIMEOUT)
    if isinstance(timeout, bool):
        raise ValueError('执行超时时间必须是整数')
    try:
        timeout = int(timeout)
    except (TypeError, ValueError) as exc:
        raise ValueError('执行超时时间必须是整数') from exc
    if not WEBUI_MIN_TIMEOUT <= timeout <= WEBUI_MAX_TIMEOUT:
        raise ValueError(
            f'执行超时时间必须在 {WEBUI_MIN_TIMEOUT} 到 {WEBUI_MAX_TIMEOUT} 秒之间'
        )
    return {'headed': headed, 'timeout': timeout}
