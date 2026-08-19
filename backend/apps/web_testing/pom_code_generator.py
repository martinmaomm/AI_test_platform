"""
POM 代码生成器 - 供 WebPage.refresh_pom_code 及 views 复用
根据 WebPage 及其 WebElement 生成 Page 类代码
标识符（类名、方法名、变量名）仅含英文字母、数字、下划线，杜绝中文字符。
数据驱动：动作配置来自 constants.WEB_UI_ACTION_OPTIONS，单一数据源。
"""
import re

from pypinyin import lazy_pinyin

from .constants import WEB_UI_ACTION_OPTIONS

# 根据 value 查找动作配置的映射表
ACTION_MAP = {item["value"]: item for item in WEB_UI_ACTION_OPTIONS}


def _escape_for_py_string(s):
    if s is None:
        return ''
    return str(s).replace('\\', '\\\\').replace('"', '\\"').replace('\n', '\\n')


def _build_playwright_selector(locator_type, locator_value):
    if not locator_value or not locator_value.strip():
        return None
    lt = (locator_type or 'css').lower().strip()
    lv = locator_value.strip()
    if lt == 'xpath':
        return f'xpath={lv}' if not lv.startswith('xpath=') else lv
    if lt == 'text':
        return f'text={lv}' if not lv.startswith('text=') else lv
    if lt == 'id':
        return f'#{lv}' if not lv.startswith('#') else lv
    if lt == 'role':
        return f'role={lv}' if not lv.startswith('role=') else lv
    if lt == 'placeholder':
        return f'[placeholder="{lv}"]'
    return lv


def _to_page_class_name(title):
    """生成仅含英文字母和数字的类名，首字母大写。如：商城首页 -> ShangchengshouyePage"""
    if not title or not str(title).strip():
        return 'MainPage'
    s = str(title).strip()
    mapping = {'注册': 'Register', '登录': 'Login', '用户': 'User', '页面': 'Page',
               '表单': 'Form', '按钮': 'Btn', '输入': 'Input', '搜索': 'Search', '下单': 'Order'}
    result = s
    for cn, en in mapping.items():
        result = result.replace(cn, en)
    # 将剩余中文转为拼音；errors='default' 保留英文等非中文字符，最终用正则过滤
    py_list = lazy_pinyin(result, errors='default', v_to_u=False)
    combined = ''.join(py_list)
    # 只保留英文字母和数字
    combined = re.sub(r'[^a-zA-Z0-9]', '', combined)
    if not combined:
        return 'MainPage'
    result = combined[0].upper() + combined[1:]
    if not result.lower().endswith('page'):
        result += 'Page'
    return result


def _get_action_config(action_type):
    """根据 action_type 获取动作配置，支持别名，未知时回退到 click"""
    if not action_type:
        return ACTION_MAP.get("click", {})
    action = str(action_type).strip().lower()
    # 别名映射
    aliases = {"input": "fill", "输入": "fill", "选择": "select", "悬停": "hover"}
    resolved = aliases.get(action, action)
    return ACTION_MAP.get(resolved, ACTION_MAP.get("click", {}))


def _is_input_like_element(elem):
    """判断是否为输入类元素（需要 text 参数）- 基于 needValue 配置"""
    config = _get_action_config(elem.action_type)
    if elem.action_type:
        return config.get("needValue", False)
    # 兜底猜测：未配置类型时，根据名称猜测
    name = (elem.name or '').lower()
    if any(kw in name for kw in ('按钮', 'btn', 'button', '链接', 'link')):
        return False
    input_keywords = ('phone', 'code', 'input', '输入', '验证码', '手机', '密码', 'password', '用户名', 'username', '框')
    return any(kw in name for kw in input_keywords)


def _is_click_like_element(elem):
    """判断是否为点击/无参类元素（不需要参数）- 基于 needValue 配置"""
    config = _get_action_config(elem.action_type)
    if elem.action_type:
        return not config.get("needValue", False)
    name = (elem.name or '').lower()
    click_keywords = ('button', 'btn', '按钮', '链接', 'link', 'a ', 'span')
    return any(kw in name for kw in click_keywords)


def _to_locator_var_name(elem, index):
    """生成仅含小写字母、数字、下划线的变量名。如：登录按钮 -> dengluanniu_1"""
    name = (elem.name or '').strip()
    if name:
        # 中文转拼音；errors='default' 保留英文，最终用正则只保留 a-z0-9
        py_list = lazy_pinyin(name, errors='default', v_to_u=False)
        combined = ''.join(py_list).lower()
        # 只保留 a-z、0-9，其余替换为下划线
        safe = re.sub(r'[^a-z0-9]', '_', combined)
        safe = re.sub(r'_+', '_', safe).strip('_')[:25]
        if safe and (safe[0].isalpha() or safe[0] == '_'):
            base = safe
        else:
            base = f'locator_{index}'
    else:
        base = f'locator_{index}'
    base = base or f'locator_{index}'
    return f'{base}_{index}'


def _to_clean_var_name(elem, index, used_names=None):
    """返回干净的拼音变量名（去除尾部序号）；若有重名则保留序号以去重"""
    if elem.name:
        raw = _to_locator_var_name(elem, index)
        clean = re.sub(r'_\d+$', '', raw) or raw
        if used_names is not None:
            if clean in used_names:
                return raw  # 重名时保留序号
            used_names.add(clean)
        return clean
    return f'element_{elem.id}'


def generate_pom_code_from_page(web_page):
    """
    根据 WebPage 及其 WebElement 生成 Page 类代码（含 Locator 与 Actions）。
    返回: (class_name, code)，无元素时返回最小占位类
    """
    elements = list(web_page.elements.all().order_by('id'))
    class_name = _to_page_class_name(web_page.name)
    if not elements:
        stub = f'class {class_name}:\n    """POM 页面对象 - {web_page.name}（暂无元素）"""\n\n    def __init__(self, page):\n        self._page = page\n'
        return class_name, stub

    class_name = _to_page_class_name(web_page.name)
    lines = [
        f'class {class_name}:',
        f'    """POM 页面对象 - {web_page.name}"""',
        '',
        '    def __init__(self, page):',
        '        self._page = page',
    ]

    used_var_names = set()
    var_name_by_idx = {}
    for i, elem in enumerate(elements):
        if not elem.locator_value or not elem.locator_value.strip():
            continue
        sel = _build_playwright_selector(elem.locator_type, elem.locator_value)
        if not sel:
            continue
        var_name = _to_clean_var_name(elem, i + 1, used_var_names)
        var_name_by_idx[i + 1] = var_name
        sel_esc = _escape_for_py_string(sel)
        lines.append(f'        # {elem.name or "未命名"}')
        lines.append(f'        self.{var_name} = page.locator("{sel_esc}")')

    lines.append('')
    lines.append('    # ----- 业务方法 -----')

    for i, elem in enumerate(elements):
        if not elem.locator_value or not elem.locator_value.strip():
            continue
        sel = _build_playwright_selector(elem.locator_type, elem.locator_value)
        if not sel:
            continue
        var_name = var_name_by_idx[i + 1]
        config = _get_action_config(elem.action_type)
        action_value = config.get("value", "click")
        needs_value = config.get("needValue", False)
        template = config.get("pom_body_template") or "await {locator}.click()"
        locator_ref = f"self.{var_name}"  # generate_pom_code_from_page 使用 self.var_name

        method_base = var_name if var_name != "locator" else str(i + 1)
        method_name = f"{action_value}_{method_base}"

        if needs_value:
            param_name = "value"
            lines.append(f'    async def {method_name}(self, {param_name}):')
            body = template.replace("{locator}", locator_ref).replace("value)", f"{param_name})")
        else:
            lines.append(f'    async def {method_name}(self):')
            body = template.replace("{locator}", locator_ref)
        lines.append(f'        """{config.get("label", action_value)}: {elem.name or ""}"""')
        lines.append(f"        {body}")
        lines.append('')

    code = '\n'.join(lines)
    return class_name, code


def generate_page_class_code(page_id):
    """纯数据驱动：根据元素库数据，重新拼装标准规范的 POM 代码（拼音命名+中文注释）"""
    from .models import WebPage, WebElement
    import logging

    logger = logging.getLogger(__name__)
    try:
        page = WebPage.objects.get(id=page_id)
        elements = WebElement.objects.filter(page_id=page_id).order_by('id')

        class_name = page.page_class_name or f"Page_{page.id}"

        lines = [
            "from playwright.async_api import expect",
            "",
            f"class {class_name}:",
            f'    """自动生成的 POM 类: {page.name}"""',
            "",
            "    def __init__(self, page):",
            "        self._page = page"
        ]

        # ---------------------------------------------------------
        # 第一阶段：在 __init__ 中集中定义所有存在定位器的元素
        # ---------------------------------------------------------
        has_locators = False
        elements_list = list(elements)
        used_var_names = set()
        var_name_by_idx = {}
        for idx, el in enumerate(elements_list, 1):
            if el.locator_value and str(el.locator_value).strip():
                if not has_locators:
                    lines.append("        # --- 元素定位器集中定义 ---")
                    has_locators = True

                var_name = _to_clean_var_name(el, idx, used_var_names)
                var_name_by_idx[idx] = var_name
                sel = _build_playwright_selector(el.locator_type, el.locator_value)
                safe_locator = _escape_for_py_string(sel) if sel else _escape_for_py_string(el.locator_value)

                lines.append(f'        # {el.name or "未命名"}')
                lines.append(f'        self.ele_{var_name} = page.locator("{safe_locator}")')

        lines.append("")

        # ---------------------------------------------------------
        # 第二阶段：生成页面动作方法，并添加中文 Docstring
        # ---------------------------------------------------------
        if elements_list:
            lines.append("    # --- 页面动作方法 ---")

        for idx, el in enumerate(elements_list, 1):
            var_name = var_name_by_idx.get(idx) or (_to_clean_var_name(el, idx, set()) if el.name else f"element_{el.id}")
            config = _get_action_config(el.action_type)
            action_value = config.get("value", "click")
            needs_value = config.get("needValue", False)
            template = config.get("pom_body_template") or "await {locator}.click()"
            locator_ref = f"self.ele_{var_name}"  # generate_page_class_code 使用 self.ele_var_name

            if not el.locator_value or not str(el.locator_value).strip():
                # 缺失定位器时的优雅占位与报错
                lines.append(f"    async def {action_value}_{var_name}(self, *args, **kwargs):")
                lines.append(f'        """{action_value}操作: {el.name or "未命名"} (定位器缺失)"""')
                lines.append(f'        # TODO: 元素【{el.name or "未命名"}】暂无定位器，请执行智能回填或手动补充')
                lines.append(f'        raise NotImplementedError("元素【{el.name or "未命名"}】定位器缺失，请前往元素库完善！")')
                lines.append("")
            else:
                # 数据驱动：根据 config 生成方法签名与 body
                method_name = f"{action_value}_{var_name}"
                if needs_value:
                    param_name = "value"
                    lines.append(f"    async def {method_name}(self, {param_name}: str):")
                    body = template.replace("{locator}", locator_ref).replace("value)", f"{param_name})")
                else:
                    lines.append(f"    async def {method_name}(self):")
                    body = template.replace("{locator}", locator_ref)
                lines.append(f'        """{config.get("label", action_value)}: {el.name or "未命名"}"""')
                lines.append(f"        {body}")
                lines.append("")

        final_code = "\n".join(lines)
        page.pom_code = final_code
        page.save(update_fields=['pom_code', 'updated_at'])
        return final_code

    except Exception as e:
        logger.error(f"生成 POM 类失败 (page_id={page_id}): {str(e)}")
        return None
