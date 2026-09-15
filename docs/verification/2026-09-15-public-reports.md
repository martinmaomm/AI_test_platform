# 测试报告公开访问验收（2026-09-15）

## 当前权限边界

按用户要求，独立的 UI、API、计划任务报告及其执行截图无需登录和项目成员权限。报告只读，知道或猜到报告 URL 的任何人都可读取请求响应、日志和截图，可能包含测试数据或凭据；链接不带密码、签名或有效期。

项目、执行列表及管理详情、用例编辑、执行/删除、AI 修复继续使用原有权限。报告页面隐藏 AI 修复入口，不请求管理数据或模型配置。截图只通过绑定项目、执行和子用例的接口读取，原始 `/media/webui_failure_screenshots/` 路径仍不可访问。

本次没有数据库迁移、历史数据清理或新的邮件发送，也没有调整 `FRONTEND_BASE_URL`。本文取代此前验收中“独立报告需登录”的产品规则，其他权限边界不变。

## 隔离回归

自动化后端及浏览器测试在 Django 初始化前切到临时 SQLite、内存缓存/队列并限制为 loopback 网络，未连接 NAS、LLM、SMTP 或被测网站。

- 后端相关测试 43 项通过：`common.test_public_reports`、`common.test_public_media`、`web_testing.test_native_execution_reports`、`web_testing.test_execution_screenshots`、`api_testing.test_execution_visibility`、`api_testing.test_acceptance_faults`、`scheduled_tasks.test_platform_reports`。
- 新增边界覆盖匿名、非成员、无效 token；报告只读；UI 套件子项顺序、结束后未执行状态；跨项目 ID 错配和截图路径穿越；管理接口仍拒绝匿名。
- 前端 `npm run test:unit` 300 项通过。最终报告文案和加载状态调整后，报告/截图定向检查再跑 18 项通过；`npm run build` 通过，仅有现存大包提示。
- `test_platform_reports_browser.py` 通过：匿名、非成员、过期登录访问三类报告；子用例日志、截图、刷新；不附加认证头，不刷新/清除登录状态，不加载 AI 面板，不请求管理 API。
- `test_api_access_failures_browser.py` 通过：非成员可读公开报告，但其他管理接口仍 403/404；数据库不变，生成/执行任务 dispatch 为 0。

隔离浏览器证据在忽略目录 `backend/logs/native-report-browser-check/` 与 `backend/logs/api-access-failures-browser-check/`。

## 实际部署只读验证

确认 worker 的 active、reserved、scheduled 均为空后，重启当前项目后端和 Celery；HTTP 与 Celery ping 均正常，未重启 Beat 或前端。

使用已保存结果，无新增执行或业务数据修改：

- UI 项目 1 执行 16、API 项目 2 执行 90 与套件执行 86、计划报告 3：匿名报告接口均 200。
- UI 执行 16 的受控截图 200，52,115 字节，SHA-256 为 `650f6590252e7b022dda480e066e0fc0070e852d36ce631de13538d9ce26c9d8`，与重启前截图一致。
- 原始截图媒体路径和错误项目 ID 返回 404；UI/API 执行管理列表匿名访问仍 401。
- 带无效 token 直接读取报告接口仍 200。
- Chrome 全新匿名上下文及模拟过期登录上下文，各访问上述四个报告页面并刷新，共 8 项通过。真实 UI 日志和截图可见；计划报告关联 API 套件链接正确；只发送报告/截图 GET，无认证头、无登录弹窗或页面 JS 错误。
- 匿名打开管理页面仍跳转登录。

真实浏览器截图及结构化结果在忽略目录 `backend/logs/20260915-public-reports-n_ndleb9/`。验证的是报告读取与权限边界，未重新运行测试业务、重新生成脚本或发送验收邮件。
