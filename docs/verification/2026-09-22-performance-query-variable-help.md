# 执行详情 Query 参数与变量引用说明

## 页面变化

- 单用户验证的每个请求下新增 Query 参数表，从本次执行记录中的请求 URL 解析，保留同名参数、空字符串和参数顺序，并展示解码后的中文及特殊字符。
- 明确区分没有 Query 参数与 URL 缺失、无效。请求体类型为“无”时说明 Query 应在上方列表查看。
- 计划编辑页说明 Query 在 URL 中发送，Headers 提供 `Authorization: Bearer ${token}` 示例，并说明 Bearer 后的空格。
- 响应提取区说明 `token` / `body.access_token` 的填写方式与后续步骤引用规则，提醒 `{token}` 会按普通文本发送；填写名称后直接显示 `${变量名}`。执行详情的提取表也增加“后续引用”列。

本次为页面改进，不修改已有计划、执行记录、节点运行模板或请求超时设置，无需更新节点。现有单用户验证记录已有完整请求 URL 的，可以直接显示 Query 列表；若原记录 URL 已截断，列表只能反映该记录中保留的内容。

## 验证

```bash
cd frontend
node --test tests/performanceWorkspace.test.js tests/performancePlanEditorState.test.js
npm run build
cd ..
backend/.venv/bin/python backend/scripts/test_performance_validation_browser.py
backend/.venv/bin/python backend/scripts/test_performance_plan_browser.py
```

- 前端针对性检查 25 项通过，包括同名参数、空值、中文、空格、加号、单次 URL 解码、无效 URL 与无 Query 的区分。
- 前端构建通过，保留已有的大分块提示。
- 隔离 SQLite + 真实 Vue/Django/Chrome 检查通过：Query 表展示与 URL 一致、HTML 作为文本显示、空请求体说明、提取引用列、既有请求响应与断言明细正常。
- 计划编辑页保存、重新打开、同名参数、步骤操作、窄窗口及既有快照契约检查通过。另检查了变量引用预览及新增提示的桌面、窄窗口布局。
- 浏览器检查使用虚构数据，外部请求、真实压测启动和页面错误均为 0。本机 Vite 的两个修改组件均返回 200，并包含新增文案。

截图仅包含隔离夹具数据，保存在 Git 忽略的临时目录：

- `backend/temp/performance-validation-browser/failed-step.png`
- `backend/temp/performance-plan-browser/query-variable-help-desktop.png`
- `backend/temp/performance-plan-browser/response-extraction-help-compact.png`
