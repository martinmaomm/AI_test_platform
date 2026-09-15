# 从网页探索发现 API

没有 Swagger 时，可以在 **API 对话工作区 → 从网页探索生成** 中，让 AI 操作测试网站，平台记录真实接口，再进入该次探索关联的工作区生成用例。

左侧「API 对话工作区」默认进入「从接口文档生成」，可以直接选择已有文档。点击旁边的「从网页探索生成」标签进入独立页面，不跳转文档上传、不显示 Swagger 选择器。两页的工作区列表按来源分开，浏览器刷新保留当前页面。

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
   API_BROWSER_DISCOVERY_ENABLED=true
   API_BROWSER_DISCOVERY_TOTAL_TIMEOUT_SECONDS=900
   API_BROWSER_DISCOVERY_MAX_MODEL_STEPS=100
   API_BROWSER_DISCOVERY_MAX_TOOL_CALLS=100
   ```

3. 当前用户需要有启用的聊天模型和 Playwright MCP。沿用固定 `@executeautomation/playwright-mcp-server@1.0.12`、Node Playwright 1.57.0 与对应浏览器；不使用 Python Playwright 的浏览器目录。安装见 [安装指南](installation.md) 与 [离线浏览器说明](../backend/resource/README.md)。不需要新增 npm/Python 依赖，也不要修改 npm 缓存里的包。

4. 重启 ASGI、唯一 Celery worker；发布前端新构建（`npm run dev` 开发模式通常会自动更新）。按原部署需要恢复 Beat。

关闭开关后，网页探索页面显示未启用提示，不再提供新探索表单，也不接受新的探索任务。关闭开关不是回滚远端测试数据；不要清空 Redis 或删除既有报告。数据库与代码回退需要配套评审。

### MariaDB 已执行 0019 但报 1071 时

早期版本把 1000 字符的 `path` 放入联合索引，在 utf8mb4 下超过 3072 字节限制。已修正为只索引任务和方法，仍完整保留路径，不截断数据。

MySQL/MariaDB 上失败前的建表和加列可能已提交，部分延迟外键/索引又尚未执行。此时不要直接清库、反复重跑原迁移或使用 `--fake`。停止后端/Celery（及 Beat）写入、备份数据库后，在 backend 虚拟环境执行：

```bash
# 只读核查与展示待补齐项，不改数据库
python manage.py repair_api_browser_discovery_migration

# 核查通过后，仅补齐缺失的索引/约束并核对完整性
python manage.py repair_api_browser_discovery_migration --apply

# 确认正常迁移流程已经没有遗漏
python manage.py migrate api_testing
```

该命令仅处理本次 0019 的已知半迁移结构；遇到字段、索引、外键冲突或其他未知状态会停止，不删除表、列或业务数据。只有结构已补齐并验证后才登记 0019，不能当作任意迁移的跳过工具。全新数据库仍直接使用正常 `migrate`。

`ai_core.RAGConfiguration (models.W036)` 是已有条件唯一约束的 MariaDB 警告，与这次索引过长的失败无关；本次不通过屏蔽警告来替代约束修复。

## 使用方法

进入「从网页探索生成」后默认展示任务列表，包含目标 URL、任务标识、创建时间、状态、有效样本、耗时和关联生成结果。点击「新建探索」进入独立表单，无需先新建空白 API 工作区，也不会复用或显示旧工作区的内容。填写以下信息：

- **页面 URL**：完整入口，包括路径、查询和 `#` 路由。
- **API origin（高级设置，可选）**：默认由真实网络请求自动识别。已知接口地址时也可手动指定协议、主机、端口；不能含路径、查询、账号密码。
- **描述**：目标模块、业务步骤、验证要求、按需的测试登录信息。首次建议一个小范围的新增/修改/删除流程。
- **模型**：当前用户已启用的聊天模型，不会自动切换到其他提供商。
- **探索总时限**：整个浏览器探索阶段的预算，默认读取服务器配置；不是单次模型请求超时。
- **授权确认**：仅在允许修改测试数据的范围内操作。取消或失败不能撤销网站上已经生效的写入。

不知道 API origin 时留空即可。采集器在首次导航前启动，对入口主页面发起、与入口相同 hostname 的 fetch/XHR 自动采集（支持网页与 API 端口不同）。对于无法确定归属的跨主机请求，平台会在发送前暂停并展示地址、方法和路径。点击「允许」继续同一个浏览器的原请求，点击「拒绝」中止该请求，不重放登录或增删改查。等待确认也计入探索总时限；此前版本中未采集的登录正文仍无法事后恢复。

提交后进入该任务独立详情，可查看进度、工具次数、记录数、当前动作和创建/开始/结束时间，也可以取消。刷新保留当前任务，终态任务会自动读取已有样本；从任务列表可重新打开历史详情或其关联生成结果。切换标签或离开页面不会取消已提交的任务；未保存内容会提示确认。只有明确点击取消才请求停止对应任务。取消不会终止其他任务，也不会恢复原浏览器会话；需要继续时新建任务。

历史任务的「删除」需二次确认：失败、已取消、部分完成或已完成且未交接的任务可以删除，删除后同步移除列表、详情和数据库中的采样记录。运行中的任务需先取消并等待停止；已交接给工作区或发布为接口来源的任务保留引用保护，不能直接删除。此操作不清理磁盘日志、截图，不影响其他用例或工作区，也不撤销测试网站上的操作。

模型服务过载、临时限流、超时和不可用会显示明确的中文分类，以及是否已经开始网页操作。可恢复的模型失败提供「重试探索」：确认后用原目标、描述、模型和预算创建新任务，旧记录保留；这不是恢复旧浏览器现场，可能再次执行已发生的网页操作。认证、模型配置、额度耗尽问题需要先处理配置或账户，不提供盲目重试。普通定位失败、MCP 故障和被测接口错误不会被归类为模型过载。

探索完成后，如果场景生成遇到模型服务故障，进入对应失败场景点击「重试当前场景」。这会复用服务器冻结的参数，不要求重新填写描述、不调用 MCP、不重跑已通过的其他场景；确认后仍可能执行真实接口请求。没有子场景时的规划失败可「重试场景规划」。有未提交修改、失败快照过期或无法安全重放时会说明阻止原因，请改用相应编辑/调试入口处理。

探索完成后按接口组检查样本和依赖提示，再选择交接范围。发现多个来源时，先选择本次工作区的主接口来源，不同来源不会混合成一个 Base URL。相同接口调用多次会保留各次记录，不会只留下最后一个响应。依赖信息是候选证据，不是已经确认的接口业务契约。

点击「确认接口并生成场景」后，**交接只创建接口来源和工作区，不发送被测请求，不保存正式用例。** 原测试描述保存在需求历史，API 地址、模型、已选接口和生成提示自动带入工作区。页面进入该结果独立的场景生成视图，不再同时展示新建表单或其他探索任务。上下文有效时会打开一次「生成并验证确认」框，明确确认后才调用模型和执行被测请求；取消或刷新不会自动生成。历史关联结果只打开对应工作区，不再次弹出确认或启动任务。可通过「查看来源探索」返回准确的来源任务。场景编辑、调试、修复、保存和导出的操作与文档来源一致。

### 重定向边界

来源确认适用于页面直接发起的 fetch/XHR，**不是浏览器全流量安全防火墙**。当前 Playwright 路由机制不重新拦截 HTTP 302/307/308 等重定向的后续请求；浏览器可能自动跟随，307/308 还可能保留原方法与正文。因此 HTTP 重定向、复杂跨站登录暂不在自动探索支持范围内，只用于可信测试站点。相关限制及后续拦截层方案记录在 [问题跟踪](TODO-OPT.md)。平台不根据 `/metrics`、`/collect` 等路径词判断接口是否属于业务，以免误排除其他网站的真实接口。

## 样本与可重复执行

- 实际方法、路径、请求类型、已观察参数和响应是事实；必填性、全部枚举、完整权限规则不是抓一次包就能知道的。
- 数字路径不会一律替换为参数；动态 ID 必须有前置响应来源。重新运行要重新登录、重新创建本轮数据，不能写死探索时的 Token、Cookie 或 ID。
- 一个工作区只采用一个确认的 API origin，最多 50 个接口组（包括依赖）。其他 origin 或不可用样本会明确列出，不混入执行范围。
- 支持普通 HTTP(S) JSON、查询和 URL 编码表单。GraphQL 语义、WebSocket、SSE、文件、复杂 SSO、验证码、动态签名/前端加密不在首版自动生成承诺中。
- 正常结束表示采集会话结束，不表示完整覆盖业务，更不表示测试通过。用例是否通过，以后续独立 requests 执行和断言结果为准。
- 平台为每轮提供时间加随机后缀，供模型构造唯一测试数据；最终仍需检查模型是否遵循。重复操作检测分别跟踪文字和 HTML 观察，交替查看两种内容不能绕过停止条件。
- 自动依赖只能识别已有证据中的关系。创建接口仅返回影响行数、列表缺少唯一查询条件等情况，仍需用户选齐完整生命周期的接口。认证入口候选也不等于已经证明能重建登录。
- 审阅生成断言，特别是分页列表：当前页没有数据不等于全局不存在。可用时应按本轮对象 ID 查询详情，或采用有证据的精确查询。平台不会为了通过而删除失败断言。

## 预算、证据与限制

| 配置 | 默认值 | 含义 |
| --- | --- | --- |
| `API_BROWSER_DISCOVERY_TOTAL_TIMEOUT_SECONDS` | 900 秒 | 本轮探索总时限，当前最多 1800 秒 |
| `API_BROWSER_DISCOVERY_MAX_MODEL_STEPS` | 100 | 单轮智能体模型步骤上限 |
| `API_BROWSER_DISCOVERY_MAX_TOOL_CALLS` | 100 | 实际浏览器工具调用上限 |
| `API_BROWSER_DISCOVERY_MAX_REQUESTS` | 500 | 每轮记录的网络请求上限 |
| `API_BROWSER_DISCOVERY_MAX_BODY_BYTES` | 262144 | 单正文保存上限，256 KiB |
| `API_BROWSER_DISCOVERY_MAX_TOTAL_BYTES` | 20971520 | 单任务证据预算，20 MiB |
| `API_BROWSER_DISCOVERY_HEARTBEAT_LEASE_SECONDS` | 45 秒 | 失联检测阈值 |

单次模型请求仍由 `LLM_TIMEOUT_SECONDS`/模型配置控制；后续 API 生成与验证用 `API_GENERATION_TIMEOUT_SECONDS`。这些限制彼此独立，不影响现有 UI 探索预算。

采集文件位于 `backend/logs/api-browser-discovery/<task-id>/`，不通过 `media` 静态地址公开：

- `network.jsonl`：程序记录的请求、请求体、响应及失败事件。
- `network.raw.jsonl`：必要的授权 origin 原始认证证据，仅限任务私有目录。
- `actions.jsonl`：工具起止事件，不包含填充值。请求与动作默认标记“关联待确认”，不能仅凭时间相近认定因果关系。
- `origin-state.json` / `origin-control.json`：自动识别状态及用户确认决定，原子更新，不含请求正文、查询值或认证头。

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
backend/.venv/bin/python backend/scripts/test_api_browser_auto_origin_browser.py

# Node 采集器：通过 npm 缓存提供固定 MCP 包，包含真实浏览器测试
npx --offline -y --package=@executeautomation/playwright-mcp-server@1.0.12 node --test backend/scripts/tests/test_playwright_mcp_capture.mjs
```

真实模型/NAS 验收必须单独授权并显式启动；`test_api_browser_discovery_live.py --help` 说明其写入和模型费用边界。它只读取现有平台配置，业务记录放入临时数据库，但会实际操作被测网站。

本次实际结果与未覆盖范围见 [2026-09-09 验收记录](api-browser-discovery-acceptance-2026-09-09.md)。
