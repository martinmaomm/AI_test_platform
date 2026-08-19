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
