# MCP 工具清单与状态验收

## 改动

- 配置启用与工具清单检测分离：创建/开关不启动探测；管理员通过“刷新工具”读取 MCP `tools/list`，不会调用模型或执行浏览器工具。
- 清单状态持久化为 `unchecked` / `ready` / `error`。真实零工具为 `ready / 0`；失败保留上次清单、显示安全错误及本次检测时间，不再假报零工具。
- 配置实质变化使清单失效；JSON 排版变化不失效。探测在数据库事务外进行，短事务及探测令牌阻止过期结果覆盖编辑或较新的检测结果。
- 获取全部分页；多服务器存在失败、重复游标或不可用配置时，不把部分结果当完整清单。整个探测约 20 秒超时，退出时清理会话。
- 状态开关按行绑定，刷新、编辑、删除和开关同一行互斥。过期检测返回 `tools_refresh_applied=false`，页面明确提示结果未采用。
- 旧模拟 `test-connection` 改为复用真实检测。

## 验证

- 后端 40 项通过：`ai_core.tests.test_mcp_configuration_tools`、`ai_core.tests.test_mcp_output_connections`、`ai_core.tests.test_global_config_access`、`web_testing.test_shared_ai_config_access`；使用隔离 SQLite，不调用外部模型/数据库。
- 前端 323 项单测通过；`npm run build` 通过，仍有既有大体积构建产物提示。
- `backend/scripts/test_mcp_config_browser.py`：真实 Vue + Django + 本地 stdio 协议测试服务，验证未检测、两项工具、零工具、启动失败、独立开关、禁用时检测、清单展开与刷新页面后状态保留。不会操作测试网站。
- `ai_core.0003_mcpconfiguration_tool_discovery_status`：隔离真实迁移保留原配置及工具行；实际数据库已应用，原配置名称、JSON、启用状态和原时间字段哈希未变。原 RAG/MariaDB 条件唯一约束警告仍在，与本次新增字段无关。
- 实际 ASGI 刷新配置 `playwright`：约 2.23 秒发现 **33 个工具**，状态 `ready`、启用状态未变；后续列表读取与真实前端工具清单展开均通过。
- 后端与 Celery 在 Worker 空闲、无预取任务时重启；8000 端口监听正常，Celery `inspect ping` 返回 `pong`。未重启前端或 Beat。

截图位于 `backend/logs/mcp-config-browser-check/`，仅保留本地，不提交仓库。

## 多配置与多用户边界

- 支持多条全局配置。当前 UI/API 网页探索选最新创建、启用且结构符合要求的 Playwright 配置，不是轮询或按用户分配。
- 33 是服务提供的完整工具清单；任务会按业务限制工具，因此运行日志中的数量可能不同。清单检测成功不等于浏览器安装、目标网页操作或脚本生成已经验证通过。
- 当前仅一个 `solo` Celery Worker，探索任务排队。不同任务创建独立 stdio 会话并按任务保存输出；本次没有做多 Worker 并发或任意第三方远程 MCP 的隔离验收。
- 以后增加 Worker 前仍需考虑测试账号、测试数据与资源配额隔离；同一账号的登录限制或同时修改同一业务数据不能靠浏览器会话隔离解决。后续事项已记录在 `docs/TODO-OPT.md`。
