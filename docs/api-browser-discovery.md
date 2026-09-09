# 从网页探索发现 API

没有 Swagger 时，可以在 **API 对话工作区 → 从网页探索生成** 中，让 AI 操作测试网站，平台记录真实接口，再交接到原有 API 工作区生成用例。

流程：`描述目标 → MCP 操作网页并采集网络 → 查看/选择接口样本 → 交接 API 工作区 → 生成并验证 → 审阅保存 → requests 重复执行 / Python 导出`。

## 部署与启用

这是独立开关，默认关闭，不改变原 Swagger 或 UI 脚本生成流程。

1. 等待现有任务完成，在维护窗口停止旧 worker、ASGI；有 Beat 时先停止定时派发。备份数据库后，在 backend 虚拟环境执行：

   ```bash
   python manage.py migrate api_testing
   python manage.py check
   ```

   新迁移 `0019_apispecification_source_selection_key_and_more` 增加采集任务、记录、交接关系与来源字段，不清理已有用例、Swagger 或执行记录。

2. 在 `backend/.env` 增加：

   ```dotenv
   AITS_API_BROWSER_DISCOVERY_ENABLED=true
   AITS_API_BROWSER_DISCOVERY_TOTAL_TIMEOUT_SECONDS=900
   AITS_API_BROWSER_DISCOVERY_MAX_MODEL_STEPS=100
   AITS_API_BROWSER_DISCOVERY_MAX_TOOL_CALLS=100
   ```

3. 当前用户需要有启用的聊天模型和 Playwright MCP。沿用固定 `@executeautomation/playwright-mcp-server@1.0.12`、Node Playwright 1.57.0 与对应浏览器；不使用 Python Playwright 的浏览器目录。安装见 [安装指南](installation.md) 与 [离线浏览器说明](../backend/resource/README.md)。不需要新增 npm/Python 依赖，也不要修改 npm 缓存里的包。

4. 重启 ASGI、唯一 Celery worker；发布前端新构建（`npm run dev` 开发模式通常会自动更新）。按原部署需要恢复 Beat。

关闭开关可隐藏新入口并停止接受新的探索任务。关闭开关不是回滚远端测试数据；不要清空 Redis 或删除既有报告。数据库与代码回退需要配套评审。

## 使用方法

填写以下信息：

- **页面 URL**：完整入口，包括路径、查询和 `#` 路由。
- **API origin**：业务接口的协议、主机、端口。例如页面在 `https://web.example.test`，接口在 `https://api.example.test`，应填写后者；不能含路径、查询、账号密码。
- **描述**：目标模块、业务步骤、验证要求、按需的测试登录信息。首次建议一个小范围的新增/修改/删除流程。
- **模型**：当前用户已启用的聊天模型，不会自动切换到其他提供商。
- **探索总时限**：整个浏览器探索阶段的预算，默认读取服务器配置；不是单次模型请求超时。
- **授权确认**：仅在允许修改测试数据的范围内操作。取消或失败不能撤销网站上已经生效的写入。

不知道 API origin 时可以留空先发现地址，但这种探索只保存地址/方法等元信息，不能生成可执行用例。确认地址后必须新建一次探索，未采集的登录正文无法事后恢复。

探索期间可查看进度、工具次数、记录数、当前动作，也可以取消。刷新页面后从历史任务继续查看。取消不会终止其他任务，也不会恢复原浏览器会话；需要继续时新建任务。

探索完成后按接口组检查样本和依赖提示，再选择交接范围。相同接口调用多次会保留各次记录，不会只留下最后一个响应。依赖信息是候选证据，不是已经确认的接口业务契约。

**交接只创建接口来源和工作区，不发送被测请求，不保存正式用例。** 原测试描述保存在需求历史，API 地址、模型、已选接口和生成提示自动带入工作区。检查后再明确点击生成并验证。后续操作与 Swagger 来源一致。

## 样本与可重复执行

- 实际方法、路径、请求类型、已观察参数和响应是事实；必填性、全部枚举、完整权限规则不是抓一次包就能知道的。
- 数字路径不会一律替换为参数；动态 ID 必须有前置响应来源。重新运行要重新登录、重新创建本轮数据，不能写死探索时的 Token、Cookie 或 ID。
- 一个工作区只采用一个确认的 API origin，最多 50 个接口组（包括依赖）。其他 origin 或不可用样本会明确列出，不混入执行范围。
- 支持普通 HTTP(S) JSON、查询和 URL 编码表单。GraphQL 语义、WebSocket、SSE、文件、复杂 SSO、验证码、动态签名/前端加密不在首版自动生成承诺中。
- 正常结束表示采集会话结束，不表示完整覆盖业务，更不表示测试通过。用例是否通过，以后续独立 requests 执行和断言结果为准。
- 自动依赖只能识别已有证据中的关系。创建接口仅返回影响行数、列表缺少唯一查询条件等情况，仍需用户选齐完整生命周期的接口。认证入口候选也不等于已经证明能重建登录。
- 审阅生成断言，特别是分页列表：当前页没有数据不等于全局不存在。可用时应按本轮对象 ID 查询详情，或采用有证据的精确查询。平台不会为了通过而删除失败断言。

## 预算、证据与限制

| 配置 | 默认值 | 含义 |
| --- | --- | --- |
| `AITS_API_BROWSER_DISCOVERY_TOTAL_TIMEOUT_SECONDS` | 900 秒 | 本轮探索总时限，当前最多 1800 秒 |
| `AITS_API_BROWSER_DISCOVERY_MAX_MODEL_STEPS` | 100 | 单轮智能体模型步骤上限 |
| `AITS_API_BROWSER_DISCOVERY_MAX_TOOL_CALLS` | 100 | 实际浏览器工具调用上限 |
| `AITS_API_BROWSER_DISCOVERY_MAX_REQUESTS` | 500 | 每轮记录的网络请求上限 |
| `AITS_API_BROWSER_DISCOVERY_MAX_BODY_BYTES` | 262144 | 单正文保存上限，256 KiB |
| `AITS_API_BROWSER_DISCOVERY_MAX_TOTAL_BYTES` | 20971520 | 单任务证据预算，20 MiB |
| `AITS_API_BROWSER_DISCOVERY_HEARTBEAT_LEASE_SECONDS` | 45 秒 | 失联检测阈值 |

单次模型请求仍由 `AITS_LLM_TIMEOUT_SECONDS`/模型配置控制；后续 API 生成与验证用 `AITS_API_GENERATION_TIMEOUT_SECONDS`。这些限制彼此独立，不影响现有 UI 探索预算。

采集文件位于 `backend/logs/api-browser-discovery/<task-id>/`，不通过 `media` 静态地址公开：

- `network.jsonl`：程序记录的请求、请求体、响应及失败事件。
- `network.raw.jsonl`：必要的授权 origin 原始认证证据，仅限任务私有目录。
- `actions.jsonl`：工具起止事件，不包含填充值。请求与动作默认标记“关联待确认”，不能仅凭时间相近认定因果关系。

这些文件可能包含测试登录信息和业务内容，应限制服务器文件访问。前端读取的是经过权限检查和脱敏的摘要。磁盘证据需要纳入运维保留/清理策略；本版没有自动按天清理任务。清理原始证据后不能宣称原始正文仍可恢复，已发布来源和正式用例不应被连带删除。

读取失败、截断、超量、网络失败、不支持的内容和未知来源会记录原因。**存储限制不等于浏览器进程的绝对内存上限**：固定 Playwright 的 `response.body()` 会整块读取；虽然检查传输尺寸并限制保存量，对于 chunked/压缩后解码膨胀的正文仍有读取后检查边界，记录中会标注 `post_read_only`。部署到不可信大流量站点时，还应使用容器/进程的内存限制。

## 开发验收

所有数据库测试使用隔离 SQLite，不要直接对开发 NAS 数据库执行 `manage.py test`。

```bash
# 在项目根目录；不连接 NAS、Redis 或模型服务
backend/.venv/bin/python backend/scripts/test_webui_generation_offline.py api_testing

# 真实本地 MCP/浏览器/HTTP，模型输出为可控 fixture；不是实际模型验收
backend/.venv/bin/python backend/scripts/test_api_browser_discovery_e2e.py

# 前端构建后，对隔离 Django 服务进行真实浏览器操作
npm --prefix frontend run build
backend/.venv/bin/python backend/scripts/test_api_browser_discovery_browser.py
```

真实模型/NAS 验收必须单独授权并显式启动；`test_api_browser_discovery_live.py --help` 说明其写入和模型费用边界。它只读取现有平台配置，业务记录放入临时数据库，但会实际操作被测网站。

本次实际结果与未覆盖范围见 [2026-09-09 验收记录](api-browser-discovery-acceptance-2026-09-09.md)。
