# 性能探索完整信息采集验收

## 变更

用户要求性能项目后续探索保留浏览器实际请求信息，避免请求头白名单改变回放语义。本次不再只补 `Accept-Language` 一项，而是默认保留已授权来源的完整请求头、响应头及原始头列表，包括 Cookie、Authorization、语言、自定义业务头和重复 Set-Cookie。

- 性能探索为任务专属 MCP 进程开启完整采集。请求完成后通过 Playwright `allHeaders()`、`headersArray()` 获取实际头信息；同步 `headers()` 只作为初始记录，完整结果在终态之前补充，不能把部分头信息标记为完整。
- 请求/响应头值不再按字段白名单丢弃或按 4096 字节静默截断。文件仍受任务总量约束，权限保持目录 0700、文件 0600；来源确认和不可拦截重定向的既有边界保留。
- 后端在性能项目权限范围内保留 URL、Query、JSON/Form、已采集正文及请求/响应头原值，原始列表保留头名称大小写和重复项。API 项目的既有公开摘要策略不变。
- 生成压测草稿时保留业务头，包括语言、Cookie、认证、Origin、Referer、自定义租户字段等。Host、Content-Length、连接字段、代理认证以及 Accept-Encoding 等传输字段保留在样本中，但不复制进可执行草稿；由客户端按实际目标、正文和支持的压缩能力处理，草稿说明列出被省略的字段。
- 样本页展示完整头信息、重复响应头和采集完整状态，优先显示实际捕获 URL。完整头读取失败的样本不可导入；不会退回部分字段却宣称采集完整。

## 验证

```bash
backend/.venv/bin/python backend/scripts/test_webui_generation_offline.py performance_testing.tests.test_discovery api_testing.test_browser_discovery api_testing.test_browser_discovery_agent api_testing.test_browser_discovery_credentials
PATH=/Users/maoyijiu/.npm/_npx/655b6792dec26b14/node_modules/.bin:$PATH node --test backend/scripts/tests/test_playwright_mcp_capture.mjs
```

后端 73 项通过，使用隔离 SQLite、内存队列并禁止外部网络；采集器 26 项通过，包含本地真实 Chrome。

- Chrome 实际发送的完整请求头与本地 HTTP 服务收到的头逐项相等，验证自动附加的 HttpOnly Cookie、中文语言头、Authorization、自定义租户头及 5000 字符长字段；响应的两个 Set-Cookie 分别保留。
- 本地响应按实际语言头返回 total=1，完整采集保留该值。未通过改写查询词或硬编码中文来模拟成功。
- 读取完整头失败时保留不完整标记，不输出底层异常内容；后端拒绝导入这种样本。跨域 307 重定向仍仅保留原有允许的元信息，未扩大来源权限。
- 完整头模式下，登录后立即刷新、同域/跨域跳转、弹窗等 7 类场景复测通过，每类执行 3 轮；请求头、响应头及登录正文均完整，服务端仅收到一次对应登录请求，没有补发或重放。
- 合成完整采集事件经过真实持久化、样本接口与草稿接口后，语言、Cookie、认证、自定义头、参数和正文原值保持一致；客户端管理字段仍能在样本中查看，草稿不存在失效的 Host/长度/压缩设置。
- 原 API 探索的凭据摘要、权限和来源边界回归通过。

前端执行 `node --test tests/performanceDiscoveryState.test.js`，7 项通过；`npm run build` 通过（仅有已有的大 chunk 提示）。

执行 `backend/.venv/bin/python backend/scripts/test_performance_discovery_browser.py`，使用隔离 SQLite、本地模拟采集和真实 Vue/Django/Chrome：样本页可见语言头、Cookie、重复响应头；选中两个样本、生成草稿、保存计划后业务请求头仍存在，Host 与 Accept-Encoding 未被错误复制。页面错误为 0、模型调用为 0、压测运行数为 0。此页面验证不替代上面的真实 MCP 采集器验证。

页面截图保存在 `backend/temp/performance-discovery-browser/full-request-headers.png`，仅含测试夹具数据；其余采集测试证据使用本地临时目录。

## 本机生效及边界

切换前确认活动压测和探索均为 0，Celery active/reserved/scheduled 均为 0。通过 `./platform restart backend celery` 优雅重启后端和 Worker，新 PID 分别为 87489、87490；`./platform status` 显示均已纳管且健康。控制器 PID 58529、Caddy PID 58536 保持不变且健康。

本机 Vite 5173 已返回新样本说明。未变更数据库结构、现场计划、历史记录、节点镜像或固定运行时，因此不需要更新性能节点。未对被测商城重新执行登录、探索或压测。

新探索采用本次完整信息保留；旧样本已丢失的字段无法还原。正文采集仍遵守现有类型、单条大小及总量上限，二进制和流式正文不在当前可执行样本范围内。捕获的固定 Cookie/令牌仍是采集时的值，若业务要求每次重新登录，计划应使用新的登录会话或提取变量。该边界不通过静默丢弃证据处理。

采集完整不等于任意请求均满足既有节点执行契约：当前草稿/节点仍限制最多 50 个请求头、单个头值最多 4096 字符。超出时完整证据仍保留，生成草稿会返回校验错误，不截断成另一个请求；扩大运行时限制需另行同步节点版本。
