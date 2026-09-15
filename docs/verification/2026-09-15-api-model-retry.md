# API 模型故障分类与显式重试验收

日期：2026-09-15

## 范围与行为

- 网页探索、场景规划、生成和修复分别记录模型故障阶段，展示固定中文原因，不把被测接口错误或普通浏览器定位失败当成模型故障。
- 过载、临时限流、模型超时和暂时不可用可重试；认证、额度耗尽、请求参数或模型配置错误需先处理。
- 探索重试需确认，用原目标、描述、模型和探索时限新建任务。不是断点恢复，可能重复网页操作；旧任务和证据保留。
- 场景重试需确认，复用服务器冻结的输入，只重试失败规划或选中的失败场景，不调用 MCP、不重跑已通过的同级场景，不自动保存或覆盖正式用例。
- 重试保留所有权、执行权限、revision、任务标识、模型启用状态和接口范围检查。同一 revision 重复提交不会重复排队。
- 已有运行证据必须通过现有重放检查：请求超时、取消、远端结果未知、不安全重放或目标接口权限/5xx 错误不能借模型重试绕过保护。

## 验证结果

- 后端模型分类、API、计划任务和通知回归：321 项通过，临时 SQLite，网络仅允许 loopback。
- 前端单元测试：298 项通过；生产构建通过，仅有既有的 chunk 体积提示。
- 真实 Vue + Django 隔离浏览器验收通过：模拟第一场景通过、第二场景模型过载；取消重试不发请求；确认后只有第二场景重新生成并通过，第一场景状态和记录不变。
- 探索重试取消不创建任务；确认后新建独立任务，原失败记录保留。测试使用模拟模型、请求和任务派发，不访问真实被测网站。
- 原网页探索任务/场景工作区导航浏览器回归通过。
- 现有工作区 31 / 子场景 32：重启后真实 API 返回 `MODEL_OVERLOADED` 和 `retry.available=true`；真实前端显示可点击的「重试当前场景」。打开确认后取消，没有提交生成请求或触发模型/被测接口调用。
- 后端、Celery 在检查无活动、预留和定时在队任务后重启。后台认证入口、工作区读取和 worker ready 均检查通过。

截图与隔离结果保存在本地 `backend/logs/api-provider-retry-browser/`，不提交运行日志、认证数据或截图到 Git。

## 可重复的专项测试

在项目根目录执行；不要使用默认数据库配置直接运行测试：

```bash
backend/.venv/bin/python backend/scripts/test_api_workspace_provider_retry.py
npm --prefix frontend run test:unit
npm --prefix frontend run build
backend/.venv/bin/python backend/scripts/test_api_provider_retry_browser.py
backend/.venv/bin/python backend/scripts/test_api_browser_workspace_navigation_browser.py
```

两个浏览器脚本依赖本机 Chrome，可通过 `TEST_CHROME_EXECUTABLE` 指定路径。构建须先于浏览器脚本完成。

## 验证边界与执行纠正

最初子任务曾误用默认 Django 测试入口及 `--keepdb`，复用了 NAS 上已有的 `test_AI_platform` 测试库，发生测试写入与回滚；该次测试使用的不是业务库 `AI_platform`，没有创建或删除该测试库。已告知用户并纠正，后续使用临时 SQLite 和网络隔离，新增专用 runner 避免重复误用。

隔离验收可验证重试协议和实际前后端交互，不证明模型服务商已恢复。本次没有重试用户的真实生成任务，也没有额外消耗模型额度；用户可在原失败场景刷新后自行确认重试。
