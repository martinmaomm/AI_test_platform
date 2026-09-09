# 网页探索自动识别接口来源

用户已确认实施。仅调整 API 网页探索链路，保留现有 API 对话工作区及 UI 自动化流程；不删除用户数据、不重放写操作。

## 行为与边界

- 不填接口地址即自动模式。完整页面 URL 和测试目标仍由用户提供；手动接口地址放入高级设置。
- 在首次导航前安装监听。由采集器识别页面触发的 fetch/XHR 请求，不依赖模型猜测接口 URL。
- 自动接纳与入口完全相同 hostname 的 HTTP(S) fetch/XHR（允许不同端口）。不是子串或顶级域名匹配；不按路径词表推测业务。非目标页面上下文和非 fetch/XHR 资源不据此接纳。
- 无法确定归属的跨主机 fetch/XHR，在发送前暂停该请求，只展示 origin、无查询参数的 path、method 供任务所有者确认。确认后继续原请求和同一浏览器，不重放登录或其他写操作；拒绝则中止原请求。未确认前不保存正文、认证头或把其发送给模型。
- 确认等待仍受本轮总时限约束；取消、关闭、超时必须释放挂起请求。已结束任务不能被确认接口重新启动。
- 一轮发现多个业务 origin 时保留已授权证据，结束后选择本次交接的主接口来源。现有工作区仍为单 base URL，不把不同 origin 的同路径接口混在一起，也不把跨源依赖假装为完整。
- 页面文字/HTML分别记录观察指纹；相同页面的不同表示不能清空重复操作计数。成功工具返回不等于业务成功。模型自行结束后保留其中文总结，不再把所有终态统一描述为完整成功。
- 平台为每轮提供带时间及随机部分的唯一后缀，提示模型用于新增/编辑测试数据；不依赖模型编造日期。

## 跨进程协议（本次实现约定）

### 验收发现的支持边界

发送前确认仅覆盖页面直接发起的 fetch/XHR。Playwright 当前不会再次路由 HTTP 重定向后续请求，不能保证在重定向转发前询问；本次不引入 CDP 拦截层重构。仅限可信测试站点，HTTP 重定向与复杂跨站登录不在支持范围，详见 [问题跟踪](TODO-OPT.md)。后续请求仅记录元信息，不宣称已安全阻止或完整采集。

### 状态协议

现有 JSONL protocol_version=1 保持不变，新增事件可被旧解析器忽略。任务根目录仍为 logs/api-browser-discovery/<celery-task-id>。

- 自动采集环境变量：`MCP_NETWORK_CAPTURE_AUTO_ORIGIN=1`、`MCP_NETWORK_CAPTURE_TARGET_URL`。显式接口地址继续使用已有 ALLOWED_ORIGINS；未启用自动模式时不改变其他调用者。
- Node 原子维护 `origin-state.json`：`{version:1, resolved_origins:[origin], pending:[{origin,method,path}], rejected_origins:[origin]}`。不含正文、query、凭据。数组、大小有界。
- Python 原子维护 `origin-control.json`：`{version:1, approved_origins:[origin], rejected_origins:[origin], cancelled:false}`。仅有权限的任务所有者通过后端接口写入；不从网页或模型输出接受控制指令。
- 采集器发送 `origin_resolved` / `origin_pending` / `origin_rejected` 网络元事件，后端不把事件当作接口记录。
- 后端创建时在任务 limits 中保存自动模式；无需数据库 Schema 迁移。任务序列化增加 `origin_resolution`：`{mode:'auto'|'manual', state:'detecting'|'resolved'|'awaiting_confirmation'|'awaiting_selection', origins:[origin], pending:[{origin,method,path}], selected_origin:origin, can_confirm:boolean}`。
- 新增 POST `browser-discoveries/<task-id>/origins/`，请求 `{version, origin, decision:'approve'|'reject'|'select'}`。approve/reject 仅处理仍活动任务真实 pending 候选；select 仅选择已采集来源，不触发网络调用。返回更新后的任务。所有权、项目权限、version、终态、候选存在性均需校验。
- 不增加笼统的“登录失败”状态。前端依据 origin_resolution 和 evidence_summary 区分等待确认、无有效证据、探索失败、部分完成及已完成。

## 验收

1. 省略接口地址，跨端口登录及 CRUD 第一次请求/响应被完整采集、整理、交接工作区。
2. 显式来源继续工作；第三方候选未批准不发请求、不留正文，批准仅发送原请求一次；拒绝/取消/超时不产生后续写操作。
3. 不同 hostname、iframe 来源、静态资源、多个来源及同路径冲突不误接纳。
4. 交替文字/HTML的重复查询可终止；每轮后缀不同；模型结束有明确总结。
5. Node 单元测试、隔离 Django 回归、前端单测/构建、真实浏览器本地模拟站点端到端验收。模拟模型通过不等于第三方模型现场验收通过。

## 本次验收结果

- 隔离 Django API 回归：130 项通过；检查未产生新的迁移。
- 前端单元测试：192 项通过；生产构建通过，仅保留既有 bundle 体积警告。
- Node：9 项通过、无跳过，包含真实 Chrome/MCP 与真实 307 重定向边界验证。
- 同 hostname 跨端口自动识别：首条登录请求与响应完整采集；共 9 条有效记录、5 个接口；交接后的工作区生成验证、两次独立 requests 重跑和导出 Python 执行均通过；无重复登录、无遗留测试数据。
- 真实 Vue → Django → MCP 确认链路：确认前 API 服务收到 0 条登录请求，批准后原请求只发送 1 次；任务完成后样本自动刷新；另开任务在待确认时取消，API 服务收到 0 条该轮请求，页面无脚本错误。
- 原手动 origin、文档/网页来源切换、历史任务查看、交接与取消浏览器回归通过。
- 以上使用一次性 SQLite、本地测试网站和确定性模型输出，没有调用用户的 NAS、Redis 或第三方模型，没有重启用户服务。实际提供商的探索决策质量需在重启服务后另行体验。
- 取消验收中观察到依赖层 `RuntimeWarning: coroutine 'ahandle_event' was never awaited`，任务仍正确结束为 cancelled、没有继续发送请求；未屏蔽警告，尚未深入定位该依赖回调的释放问题。后续独立修复此警告，不能将本轮功能回归通过理解为依赖日志完全无警告。

可复现命令见 [使用与开发验收文档](api-browser-discovery.md)。本地验收输出（不提交私有日志）：`backend/logs/api-browser-discovery-e2e/summary.json`、`backend/logs/api-browser-auto-origin-browser/summary.json` 及同目录截图。

生效方式：重启 Django 后端与 Celery worker，开发模式前端刷新；不需要执行数据库迁移。旧任务缺失的正文不能补回，使用新任务验证。
