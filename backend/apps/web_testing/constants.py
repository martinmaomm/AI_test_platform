# web_testing/constants.py
# 单一数据源：WebUI 动作配置，供前端、POM 代码生成、测试脚本生成复用
WEB_UI_ACTION_OPTIONS = [
    {
        "value": "goto",
        "label": "goto - 访问网址",
        "needTarget": False,
        "needValue": True,
        "valuePlaceholder": "输入完整URL",
        "code_template": "await page.goto('{value}')",
        "pom_body_template": None,  # goto 为页面级操作，不生成 POM 元素方法
    },
    {
        "value": "click",
        "label": "click - 点击元素",
        "needTarget": True,
        "needValue": False,
        "code_template": "await {page_object}.click_{target}()",
        "pom_body_template": "await {locator}.click()",
    },
    {
        "value": "fill",
        "label": "fill - 输入内容",
        "needTarget": True,
        "needValue": True,
        "valuePlaceholder": "请输入文本",
        "code_template": "await {page_object}.fill_{target}('{value}')",
        "pom_body_template": "await {locator}.fill(value)",
    },
    {
        "value": "select",
        "label": "select - 下拉选择",
        "needTarget": True,
        "needValue": True,
        "code_template": "await {page_object}.select_{target}('{value}')",
        "pom_body_template": "await {locator}.select_option(value)",
    },
    {
        "value": "check",
        "label": "check - 勾选",
        "needTarget": True,
        "needValue": False,
        "code_template": "await {page_object}.check_{target}()",
        "pom_body_template": "await {locator}.check()",
    },
    {
        "value": "hover",
        "label": "hover - 悬停",
        "needTarget": True,
        "needValue": False,
        "code_template": "await {page_object}.hover_{target}()",
        "pom_body_template": "await {locator}.hover()",
    },
]

# 动作映射表（供 AI 用例生成等场景使用，key 为 action 代码）
WEBUI_STEPS_MAPPING = {item["value"]: item for item in WEB_UI_ACTION_OPTIONS}


# WebUI 执行契约：平台只提供一套由 Playwright 管理的 Chrome for Testing。
# Playwright Python API 中对应的浏览器引擎名称仍为 chromium；该内部名称不暴露给前端选择。
WEBUI_BROWSER_ENGINE = "chromium"
WEBUI_BROWSER_DISPLAY_NAME = "Chrome"
WEBUI_DEFAULT_HEADED = True
WEBUI_DEFAULT_TIMEOUT = 300
WEBUI_MIN_TIMEOUT = 30
WEBUI_MAX_TIMEOUT = 1800


def normalize_webui_execution_options(options=None):
    """只接受平台实际支持的 WebUI 运行参数，丢弃客户端浏览器等无效配置。"""

    if options is None:
        options = {}
    if not isinstance(options, dict):
        raise ValueError("WebUI执行参数必须是对象")

    headed = options.get("headed", WEBUI_DEFAULT_HEADED)
    if not isinstance(headed, bool):
        raise ValueError("显示模式参数 headed 必须是布尔值")

    timeout = options.get("timeout", WEBUI_DEFAULT_TIMEOUT)
    if isinstance(timeout, bool):
        raise ValueError("执行超时时间必须是整数")
    try:
        timeout = int(timeout)
    except (TypeError, ValueError) as exc:
        raise ValueError("执行超时时间必须是整数") from exc
    if not WEBUI_MIN_TIMEOUT <= timeout <= WEBUI_MAX_TIMEOUT:
        raise ValueError(
            f"执行超时时间必须在 {WEBUI_MIN_TIMEOUT} 到 {WEBUI_MAX_TIMEOUT} 秒之间"
        )

    return {
        "headed": headed,
        "timeout": timeout,
    }
