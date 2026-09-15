# 共享 UI 隔离冒烟验收

执行时间：2026-09-15 18:30–18:33 CST  
范围：回归清单 `SMK-01`、`SMK-02`；仅临时 SQLite、回环 Django/已构建 `frontend/dist` 与本地 Chrome。

## 结果

| 项目 | 结果 | 已验证内容 |
| --- | --- | --- |
| SMK-01：UI 脚本调试、日志、截图、脚本助手 | 通过 | 已保存脚本的 AI 对话编辑、候选调试验证、失败执行详情、原始 stdout/stderr/log、失败截图、人工采用、会话取消与离线模型切换均通过。 |
| SMK-01：原生报告 | 通过 | Web UI、API、计划任务报告的历史快照、失败筛选、截图展示、刷新、无 iframe 嵌入和匿名登录回跳均通过。 |
| SMK-02：项目、模型、知识库、邮件页面只读访问 | 通过 | 项目列表、LLM 配置、预置知识库记录、邮件配置均显示正确；浏览器 API 请求仅允许 GET/HEAD，未触发配置写入、知识库处理、模型调用或邮件发送。 |

## 执行命令

```sh
backend/.venv/bin/python backend/scripts/test_webui_script_assistant_browser.py
backend/.venv/bin/python backend/scripts/test_platform_reports_browser.py
backend/.venv/bin/python backend/scripts/test_shared_ui_readonly_browser.py
```

三项均退出为 0 并输出 `PASS`。

## 截图证据

- `/Users/maoyijiu/Documents/tg-work/automation-platform/backend/logs/script-assistant-browser/chat-verified.png`
- `/Users/maoyijiu/Documents/tg-work/automation-platform/backend/logs/script-assistant-browser/repair-verified.png`
- `/Users/maoyijiu/Documents/tg-work/automation-platform/backend/logs/script-assistant-browser/repair-manual-verification-failed.png`
- `/Users/maoyijiu/Documents/tg-work/automation-platform/backend/logs/script-assistant-browser/repair-manual-verification-passed.png`
- `/Users/maoyijiu/Documents/tg-work/automation-platform/backend/logs/script-assistant-browser/reset-and-model-switch.png`
- `/Users/maoyijiu/Documents/tg-work/automation-platform/backend/logs/native-report-browser-check/suite-report.png`
- `/Users/maoyijiu/Documents/tg-work/automation-platform/backend/logs/native-report-browser-check/scheduled-report.png`
- `/Users/maoyijiu/Documents/tg-work/automation-platform/backend/logs/native-report-browser-check/api-report.png`
- `/Users/maoyijiu/Documents/tg-work/automation-platform/backend/logs/shared-ui-readonly-browser/project-list-readonly.png`
- `/Users/maoyijiu/Documents/tg-work/automation-platform/backend/logs/shared-ui-readonly-browser/llm-config-readonly.png`
- `/Users/maoyijiu/Documents/tg-work/automation-platform/backend/logs/shared-ui-readonly-browser/knowledge-readonly.png`
- `/Users/maoyijiu/Documents/tg-work/automation-platform/backend/logs/shared-ui-readonly-browser/email-config-readonly.png`

`failure.png` 文件不作为本轮通过证据：报告/脚本助手目录中的文件为早期残留；只读专项中的该文件记录了首次夹具遗漏项目成员权限后的定位过程，修正测试夹具后第二次针对性运行通过。

## 隔离与真实边界

- 临时目录内创建 SQLite 和 media；`MIGRATION_MODULES` 指向无迁移夹具，未运行默认 `manage.py test`，未连接 NAS 数据库。
- Python socket 的 `connect`/`connect_ex` 仅允许 loopback；浏览器请求也仅允许本次随机回环 WSGI origin。模型流、MCP、脚本执行与报告数据均为确定性夹具。
- 未启动、停止或重启真实共享服务；未使用真实付费模型；未调用 SMTP 或发送邮件；本轮没有真实站点探索或业务脚本执行。
- 主代理正在处理的真实 Chrome execution 16 不属于本隔离结论，本报告未读取或修改其日志、截图或清理状态。

## 测试维护与未覆盖项

- 新增 `backend/scripts/test_shared_ui_readonly_browser.py`，用于补足现有专项无法满足的“只读”共享页面检查。它预置临时夹具，并断言浏览器 API 不出现非 GET/HEAD 方法。
- 首次运行发现知识库列表的授权查询依赖 `ProjectMember.can_view_reports`，而不是创建者/负责人身份；为测试夹具增加该成员记录后通过。未修改产品代码。
- 本轮不覆盖真实模型质量、真实 SMTP 投递、真实知识库解析/向量化、真实权限异常（由其他专项负责），也不替代主代理的真实平台只读/清理检查。
