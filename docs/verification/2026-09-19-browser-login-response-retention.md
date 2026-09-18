# 网页探索登录响应正文保留验证（2026-09-19）

## 问题与证据

性能项目「柠檬商城」历史探索中，登录返回 HTTP 200，后续携带认证信息的请求成功，但登录响应正文标记为 `body_read_failed / body_unavailable`，因而接口样本被排除为不完整。历史任务没有保存精确底层错误与导航事件，不能将其触发原因完全等同于本机复现。

本机真实 Chrome 夹具中，网页读取登录 JSON 后立即刷新或导航，可以稳定触发 `Network.getResponseBody: No resource with given identifier found`。把 `body()` 调用移到 `response` 事件仍无改善，因为当前 Playwright 会先等待请求结束，再读取响应正文。

## 修复

- 在探索独立 Node 进程中，为固定 Playwright Core 的 `CRNetworkManager.addSession` 添加小范围适配：自有 CDP session 首次 `Network.enable` 时启用 `enableDurableMessages`，在浏览器进程保留跨导航响应正文。
- 使用同一 Playwright session / requestId 读取正文，成功启用时覆盖页面、弹窗及 renderer session 初始化，早于首次请求。没有增加第二次业务请求、响应代取、页面注入或 CORS 放宽。
- CDP 每个 session 的响应缓冲使用任务正文大小与总量配置；落盘总量仍由原任务级限制控制。原来的来源授权、静态资源过滤、解压后大小检查、取消与认证信息隔离继续生效。
- 不支持内部适配或能力时退回原采集方式，只输出固定 stderr 诊断，不打印底层异常，不增加会被后端误认为非法请求的 JSONL 事件。能力拒绝后的重试可能晚于新 target 开始运行，因此回退只保证不因该可选能力阻断初始化，不保证首请求或立即导航正文仍被捕获。
- 900 秒默认时间与默认自动允许跨域策略不变。

## 验证

环境：macOS、本机 Chrome 152.0.7977.76、Playwright MCP 1.0.12、Playwright Core 1.57.0；仅访问临时回环 HTTP 服务，使用虚构登录数据。

- 正式新增回归在修复前失败：同源刷新、跨主机刷新、同源导航、跨主机导航、新窗口初始登录后导航，均在首轮复现正文不可用；不跳转对照正常。
- 修复后采集器全量回归 23 项通过、0 跳过，其中新回归包含 7 个场景、每场景 3 轮，共 21 次真实浏览器登录：同源/跨主机停留、同源/跨主机刷新、同源/跨主机导航、新窗口登录后导航。全部保存完整请求体和响应 JSON，每轮只有一次实际登录请求，无补发 GET 或 POST，无待确认来源残留。
- 另新增能力不支持时的降级回归 1 项通过，验证普通浏览器初始化仍继续、原 session 方法在成功与异常后均恢复、固定诊断不泄漏异常中的模拟凭证、不重复刷日志。
- 原 MCP 工具链采集、gzip 正文限制、来源授权、取消、浏览器关闭、存储预算与认证脱敏回归通过。
- 原诊断夹具再次运行 30/30 捕获成功，之前立即导航的 18 次均失败。

复现命令（使用本机已安装包，无依赖升级）：

```sh
PATH=/Users/maoyijiu/.npm/_npx/655b6792dec26b14/node_modules/.bin:$PATH node --test backend/scripts/tests/test_playwright_mcp_capture.mjs
```

## 生效与边界

每个新探索任务都会启动自己的 MCP Node 进程并导入当前采集脚本，因此新任务直接使用修复，无需重启 backend / Celery。已经运行的探索与历史不完整记录不会因此补抓响应。

本轮未重新登录真实商城、提交用户凭证或重放历史接口；真实商城登录采集需在下一次获授权探索中验收。该适配依赖当前 Playwright Chromium 内部网络结构，升级 MCP / Playwright / Chrome 时须重跑这些回归；Firefox / WebKit 未新增此项正文保留能力。历史原始日志中的凭证清理仍是独立事项。

适配只在启用网络采集的独立探索进程安装。探索工具白名单不开放 `playwright_expect_response` / `playwright_assert_response`；若未来将这些工具接入同一进程，需要另验超过采集正文上限的断言读取行为，因为 CDP 缓冲上限作用于整个 session。普通未启用采集的 MCP 会话不受此补丁影响。
