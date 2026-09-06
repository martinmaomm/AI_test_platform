# 通知设置检查与修复

## 现有流程

管理员维护全局通知通道和 SMTP 配置；项目成员按权限配置接收对象。计划任务选择接收对象及“始终/仅失败”条件，串行执行结束后发送结果摘要和平台报告链接。单用例或套件的普通手动运行不会自动触发这条通知流程。

## 本次修复范围

- 删除创建接收对象后对已删除审计字段的访问，避免入库成功后返回错误。
- 接收对象严格按所属项目、成员权限授权，拒绝路径/请求体项目冲突；全局渠道修改、SMTP 管理限管理员；有关联接收对象的全局渠道不能级联删除，应先停用。
- 统一测试消息与正式消息的传输校验：只支持已接入的钉钉、企业微信、邮件；只接受对应官方 HTTPS Webhook，禁止跳转到其他地址。HTTP 成功不代表业务成功，需同时检查返回的 errcode。
- Webhook 与 SMTP 都有 10 秒网络超时。SMTP 统一邮箱分隔、有效性及发送计数检查；测试和正式通知共用规则。列表不再返回 Webhook 明文凭证，留空编辑仍保留已配置值。
- 停用的接收对象、停用的全局渠道、跨项目接收对象不发送；仅失败条件覆盖实际失败、执行错误、未完成，不把运行中或成功误发为失败。
- 只在所有实际目标都确认发送成功后写通知时间；单个目标失败不影响剩余目标及测试结果。
- 正式消息也含 AITS 标识，避免钉钉以 AITS 为关键词时测试通过、正式推送却被拒绝。邮件 HTML 中任务名与链接进行转义。
- 修复前端隐藏接口错误的逻辑及全局渠道编辑缺字段问题；切换项目时清除旧列表/表单，避免迟到请求覆盖当前项目。
- 接收对象列表自动加载后续页，管理页面和计划任务选项不再只显示前 20 条。非嵌套的接收对象列表接口必须提供 `project_id`，当前页面继续使用项目内接口。

## 使用与部署

1. 重启 Django 后端及 Celery Worker。开发前端刷新即可，静态部署需重新构建。没有新增数据库迁移，不重启或改写 NAS 服务。
2. `backend/env.example` 新增报告地址说明。请在自己的 `.env` 中把 `FRONTEND_BASE_URL` 配成收件人能访问的前端地址；本次未修改现有 `.env`。
3. 邮件通知使用最近更新的已启用 SMTP 配置。项目中创建接收对象后，“测试”会实际发送一条测试消息。
4. 再在计划任务中选择接收对象和触发条件，运行一次完整计划任务验证正式消息和报告链接。

## 已知边界

- 未新增飞书、Slack 或任意自定义 Webhook 适配器；不能仅添加渠道名称就获得发送能力。
- 当前不自动计算钉钉签名。静态粘贴的带时间戳签名链接不适合长期定时推送；使用现有关键词/IP 配置时仍需满足渠道自身的安全设置。
- 未新增独立投递队列、逐接收对象投递记录或独立自动重试策略。本轮保留原有完成回调结构；部分目标成功、部分失败后，重复完成回调或人工重新触发可能使已成功目标再次收到，不能宣称严格一次投递。
- 网络超时只限制相应网络操作，不代表多个接收对象合计耗时上限为 10 秒。
- 本轮仅做离线及隔离浏览器测试，不向真实群组或邮箱发消息，不证明第三方账号、SMTP、网络或机器人安全配置已可用。

## 验证结果

- 后端隔离回归 465 项通过，覆盖通知 API、项目权限、传输校验、正式发送、计划任务和 WebUI 相关流程；迁移检查没有新增变更。
- 前端单元测试 156 项通过，生产构建成功；保留已有的大体积打包提示。
- 隔离浏览器通过创建、空白凭证编辑保留、按 ID 测试、渠道拒收错误展示、非法地址校验及全局渠道更新。使用临时 SQLite，浏览器及 Python 外部网络被阻断，仅模拟 Webhook 传输；未调用真实 SMTP。
- 验证截图输出在 `backend/logs/notifications-browser/`，日志和截图不提交到仓库。

复现命令（前端构建完成后才能运行浏览器验收）：

```bash
# frontend 目录
npm run test:unit
npm run build

# backend 目录
.venv/bin/python scripts/test_webui_generation_offline.py notifications ai_core.tests.test_webui_playwright_agent ai_core.tests.test_mcp_output_connections ai_core.tests.test_mcp_agent_budget scheduled_tasks common.test_native_report_contract web_testing
.venv/bin/python scripts/test_notifications_browser.py
```

## 协议核验

使用 agent-reach 的官方文档检索流程核对发送地址与返回字段。钉钉的类型表写 Number、成功示例写字符串 `"0"`，两种明确的成功值均按该协议处理；企业微信按错误码而非错误文本判断。

- [钉钉：自定义机器人发送群消息](https://open.dingtalk.com/document/orgapp/custom-robots-send-group-messages)
- [企业微信：消息推送配置说明](https://developer.work.weixin.qq.com/document/path/91770)
- [企业微信：全局错误码](https://developer.work.weixin.qq.com/document/path/96213)
