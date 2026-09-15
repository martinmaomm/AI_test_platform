# 简单用户管理（方案 A）验收

日期：2026-09-15。设计见 [方案 A](../plans/2026-09-15-simple-user-management.md)，操作见 [用户管理与项目分配](../user-management.md)。

## 结果

方案 A 已实施：两个可选角色、受保护的超级管理员、管理员管理全部项目、普通用户按项目成员关系协作。账号不物理删除；独立报告与截图仍公开只读。未新增数据表或迁移，也未改写真实账号、成员关系或业务测试数据。

## 自动化验证

- 主控合并后执行 262 项后端测试，全部通过：账号/项目/通知/知识库/计划任务/公开报告 189 项，AI 配置共享和 UI 生成/脚本助手 73 项。
- 前端 `npm run test:unit`：306 项通过；`npm run build` 通过；`git diff --check` 通过。
- Django 测试初始化前切换到临时 SQLite、内存缓存/队列并限制 Socket 仅回环；没有连接 NAS 测试库、真实模型、SMTP 或执行测试网站操作。
- 主要边界：伪造角色字段不能提权；只有超级管理员能授予管理员身份；本人、超级管理员和同级管理员受保护；无效项目 ID 不产生部分分配；成员移除、降级、停用后的旧凭据受限制。
- 普通成员可使用共享的启用模型/MCP，但不能读写全局配置；禁用模型不可回退使用。项目保存资产共享，个人草稿不因旧创建者指针而泄露。管理员无成员记录也能维护自己的生成记录。
- 修复联调发现的个人资料 PUT 与响应结构问题；个人资料保存不会覆盖并发管理员修改的角色/状态。

可重复执行的后端测试分组：

```text
users.test_management
common.test_public_reports
common.test_public_media
projects.test_permissions_scheme_a
projects.test_environments
api_testing.test_execution_visibility
api_testing.test_acceptance_faults
api_testing.test_browser_discovery_navigation
api_testing.test_browser_discovery
project_knowledge.tests.test_api
project_knowledge.tests.test_runtime
scheduled_tasks.test_environment_contract
scheduled_tasks.test_project_schedule_contract
scheduled_tasks.test_beat_health
scheduled_tasks.test_platform_reports
scheduled_tasks.test_scheduling_reliability
notifications.test_notification_api

ai_core.tests.test_global_config_access
ai_core.tests.test_llm_configuration_provider_name
web_testing.test_shared_ai_config_access
web_testing.test_script_assistant
web_testing.test_exploration_action_scope
web_testing.test_generation_deletion
```

这些 label 应在 `scripts/test_api_workspace_browser.py` 的隔离 bootstrap 与 `scripts/test_platform_reports_browser.py` 的 loopback 网络限制内运行，不能直接对部署数据库运行裸 `manage.py test`。

## 浏览器验收

使用真实 Vue 构建产物、Chromium、临时 SQLite 和回环 HTTP 执行 `backend/scripts/test_user_management_browser.py`，最终输出 `PASS: isolated user-management Vue/browser acceptance`。

覆盖：

- 超级管理员从页面创建普通账号与管理员账号。
- 普通管理员分配多个项目、清空分配，受保护账号不出现编辑入口。
- 分配读取 503 时禁止保存；权限读取 503 时保留会话、进入重试页，不循环跳转登录。
- 普通用户不显示全局配置/项目管理按钮，直达配置 URL 被拦截；安全模型选项无密钥字段。
- 移除成员后项目详情请求返回 404。

本地截图位于 `backend/logs/user-management-browser-check/`，不提交日志或真实账号截图；`member-gates.png` 是隔离浏览器证据，`live-admin-list.png` 是部署页面只读检查。

## 部署只读检查

- 确认 Celery active/reserved/scheduled 均为空后，正常停止旧 ASGI/Celery 并重启；未重启 Beat 或前端。
- 新 ASGI PID 14678；新 Celery PID 14679，`ping` 返回 `pong`。
- 实际管理员访问 current-user、用户管理、项目列表、可用模型列表均返回 200。真实网页 `/settings/users` 显示原有 2 个用户，角色与状态正常。
- 未登录访问用户管理返回 401。
- UI 执行 16、API 执行 90/86、计划报告 3 仍可匿名读取；UI 执行 16 的截图返回 200，内容校验和未变。
- 前后核对实际数据库仍为 2 个用户、2 个项目、原有 2 条成员关系，未自动给普通测试账号分配项目。

## 验证边界

本轮不重跑收费模型生成、不发送邮件、不更改实际平台用户/项目分配。页面写操作在隔离环境完成；未做 MariaDB 多进程并发锁压力测试。角色或成员变化不等于撤销已运行的测试网站操作，也不强行中止已有运行任务或 WebSocket；生产会话治理与审计留在 [技术债清单](../TODO-OPT.md)。
