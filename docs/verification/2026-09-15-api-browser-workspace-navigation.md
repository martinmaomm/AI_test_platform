# 网页探索任务与生成工作区分离验收

日期：2026-09-15。

## 交付范围

- 网页来源入口默认显示探索任务列表，不自动恢复最近的生成工作区。
- 新建探索、单次探索详情、关联场景工作区通过独立 URL 展示；切换时清理本地状态并隔离过期响应。
- 历史列表显示创建时间、有效样本、耗时和关联结果；详情补充开始/结束时间。关联结果只返回同项目、同所有者、来源匹配且仍存在的根工作区。
- 「确认接口并生成场景」交接样本后打开生成前确认框。确认才会调用模型及执行接口；取消、刷新、打开历史结果不自动生成。
- 保留原任务与工作区、既有删除保护、来源授权、样本去重/分页和 50 组限制。不新增迁移，不改模型探索或 requests 执行引擎。

## 已验证

| 检查 | 结果与边界 |
| --- | --- |
| 前端 `npm run test:unit` | 294 项通过 |
| 前端 `npm run build` | 通过；保留既有大体积 chunk 提示 |
| 后端 DiscoverRunner | 临时 SQLite + 阻断外网，`api_testing`、`scheduled_tasks`、`notifications` 共 302 项通过 |
| 后端关联导航专项 | 标准 DiscoverRunner 测试可发现；配合既有探索契约共 22 项通过，验证归属、缺失引用、时间排序与关联预取 |
| 新增导航浏览器测试 | 真实 Vue + Django：同 URL 不同任务、新建、时间、准确关联、前进后退、刷新、未保存确认、延迟样本隔离、错误 ID、交接只弹确认、新任务不覆盖旧任务均通过 |
| 既有网页探索浏览器测试 | 来源隔离、禁用模型、取消、部分结果、分页、样本选择、交接、删除保护、409 冲突、删除后的过期响应均通过 |
| 自动来源浏览器测试 | 本地测试站点：确认前不释放请求、确认后登录/CRUD、取消不释放请求均通过 |
| 文档来源导航浏览器测试 | 清洁状态无需确认、未保存输入保护，以及既有生成/采用/调试/修复/保存/导出流程通过 |
| 重启后的真实环境只读检查 | 历史 1 个探索任务及 1 个关联工作区可正确打开；时间、新建空白页、来源返回和刷新通过，无页面异常、无写请求 |

浏览器回归使用临时数据库、可控模型/队列或本地测试站点，不连接 NAS 业务接口或收费模型。本轮真实环境仅检查平台读取与导航，没有重新探索、重新生成或执行被测业务；上述结果不等于本轮重做了全模块真实模型验收。

## 本地证据与复跑

主要证据位于 `backend/logs/api-browser-workspace-navigation/`：

- `node-tests.log`、`backend-tests.log`、`build.log`。
- `result.json`、`test-run.log`、`task-list.png`、`handoff-confirmation.png`。
- `live-readonly-result.json`、`live-task-list.png`。
- `document-navigation.log`。

其他浏览器输出位于既有 `backend/logs/api-browser-discovery-browser/`、`backend/logs/api-browser-auto-origin-browser/`、`backend/logs/api-workspace-browser-check/`。日志与私有认证文件不纳入 Git。

在根目录完成 `frontend` 构建后，可单独运行以下隔离脚本（不使用 NAS 数据库）：

```sh
backend/.venv/bin/python backend/scripts/test_browser_discovery_navigation.py
backend/.venv/bin/python backend/scripts/test_api_browser_workspace_navigation_browser.py
backend/.venv/bin/python backend/scripts/test_api_browser_discovery_browser.py
backend/.venv/bin/python backend/scripts/test_api_browser_auto_origin_browser.py
backend/.venv/bin/python backend/scripts/test_api_workspace_navigation_browser.py
```

## 运行状态

确认 Celery 的 active/reserved/scheduled 均为 0 后，正常停止并重启后端与 Celery，未强杀活动任务。重启后后端未登录请求返回预期 401，已登录只读请求返回 200，Celery ping 返回 pong。开发前端继续使用既有 Vite 服务，刷新即可，无需数据库迁移。
