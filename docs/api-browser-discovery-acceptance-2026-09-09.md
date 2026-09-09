# 网页探索发现 API：首版验收记录

日期：2026-09-09。环境：macOS arm64、Python 3.13、本项目固定版本 Node Playwright MCP。

> 部署后补充：用户在实际 MariaDB 10.5.29 的 0019 迁移遇到索引过长错误。下文原始 SQLite 迁移结果不能视为 MariaDB 部署已通过；这是首轮验证遗漏。现已修正短索引并增加专用半迁移恢复检查，操作见 [迁移恢复说明](api-browser-discovery.md#mariadb-已执行-0019-但报-1071-时)。补充实测结果见文末。

## 结论与交付范围

已实现“网页探索 → 程序采集真实网络 → 选择样本 → 交接原 API 工作区 → 生成/验证/保存/requests 执行及 Python 导出”。不要求 Swagger，不依赖 UI Python 草稿或模型最终 JSON，不增加另一套执行器。

实际 NAS 菜单增删改查通过：真实模型依据 13 条浏览器 API 样本生成 8 步、18 条断言的候选，自动验证通过；两个新 requests Session 重跑及独立 Python 导出执行通过。另一次原样重跑后，人工追加按本轮对象 ID 查询详情，确认删除真实生效。

这证明该样本链路可运行，不代表任意网站、任意模型输出都能一次通过。测试数据、候选和工作区保存在隔离平台数据库中；当前开发平台数据库和 `.env` 未修改，未重启用户服务。部署操作见 [功能指南](api-browser-discovery.md)。

## 已实现

- 独立默认关闭开关；当前用户启用的模型选择与提供商展示。
- 一个 MCP 会话顺序探索；导航前监听网络，登录请求也被采集。
- 同 URL 多次请求、跳转、弹窗页面、请求失败等按请求标识记录，保留样本而非只留最后一次响应。
- 明确 API origin；未授权来源仅保存必要元信息，不读取认证、查询值或正文作为可执行证据。
- 采集总时间、真实工具次数、重复失败、请求数、正文与总文件预算；取消、心跳失联、版本与重复投递保护。
- 项目/所有者权限、私有原始证据与前端摘要分离；不可用样本有原因，不冒充空 JSON。
- 观察来源快照不可由普通规范/端点编辑接口覆盖；交接幂等，依赖展开后最多 50 组接口。
- 原测试目标、API 地址、接口范围与模型带入工作区；不会在交接时执行接口或自动保存正式用例。
- 基于证据检查动态 ID、认证变量、响应提取和断言；沿用现有候选验证、有限修复及审阅流程。
- 非破坏性迁移 `0019_apispecification_source_selection_key_and_more`，保留已有 Swagger、用例及记录。

## 测试结果与层级

| 验证 | 结果 | 实际边界 |
| --- | --- | --- |
| Django API/UI/模型预算/计划任务/通知回归 | 556 通过 | 隔离 SQLite；包含模拟服务与负例，不连接 NAS/Redis/模型 |
| requests runtime | 30 通过 | 独立 pytest 合约测试 |
| 前端单测 | 183 通过 | Node 测试 |
| Node 采集与 MCP 启动器 | 5 通过 | 真实本地 HTTP/浏览器；包含跳转、popup、压缩/超量正文与三文件共享预算 |
| 前端生产构建 | 通过 | 仍有原项目大 chunk 警告，不是构建失败 |
| 0018 → 0019 迁移 | 通过 | 独立历史 SQLite，旧 Swagger/工作区/用例保留；不证明 MariaDB 所有版本行为 |
| Vue 浏览器交互 | 通过 | 真实 Vue + Django + SQLite，broker/探索模拟；不调用真实模型 |
| MCP 本地合成站点闭环 | 通过 | 真实 MCP/HTTP、可控模型，不是实际模型智能验收 |
| NAS + 真实模型 | 通过一次完整采集及后续生成/重跑/导出 | 实际测试网站与模型；平台配置只读复制到临时数据库 |

前端浏览器覆盖：开关关闭/开启、模型提供商、创建、取消、刷新恢复、部分结果、多次样本展开、无有效证据、分页与 50 组限制、本地草稿放弃确认、交接后提示预填、清空来源不恢复旧选择。交接后核对数据库中尚未产生正式用例或执行记录。

本地 MCP 合成站点记录 17 次浏览器工具调用、18 次模型调用、8 条可用样本和 5 组接口。测试结束过快导致最后一个请求未正常收敛，采集状态正确为 `partial`，没有将它伪装为完整证据。可用样本生成验证、两个独立会话重跑和 Python 导出通过，测试对象剩余数 0；重复任务投递被忽略。

## 真实 NAS 验收

授权测试页面 `http://192.168.31.188:9990/`，业务 API `http://192.168.31.188:9999`；模型为当前启用的“基元律动 · deepseek-v4-flash-0731”。账号、密码、Token 和原始业务正文不写入本文。

1. 原始采集任务 `b6151627-32a2-47bb-83f1-6936934ec7fb`：同一浏览器完成登录、进入菜单、新增、编辑、删除；49 次浏览器工具调用、31 次模型调用，13 条可用接口样本、7 组接口。两种计数不同：一次模型响应可能顺序调用多个工具。
2. 后续为避免重复浏览器写入，复用该任务已经保存的真实网络文件；不是再次探索，也没有让模型编造采集记录。
3. 最终生成验收任务 `ffd20850-5f5c-4588-a2b6-839c32392148`：真实模型第一轮候选自动验证通过；8 步、18 条断言，包含重新登录、本轮唯一名称、唯一匹配提取 ID、新增/编辑/删除与查询验证。
4. 浏览器已关闭后，该候选用两个全新 requests Session 分别运行通过；导出的 Python 在独立进程运行通过。每轮获取新认证和新对象，不使用浏览器里的 Token 或对象 70。
5. 主 Agent 额外原样运行一次候选，8 步及 18 条断言全部通过；本轮对象 ID 为 79。随后使用本轮认证 GET `/menu/79`：HTTP 200、业务 `code=200`、`data=null`，独立确认删除。这个详情读回是人工验收，不冒充模型自动生成的断言。

本地证据（被 Git 忽略，可能含测试凭据，请限制访问）：

- `backend/logs/api-browser-discovery/b6151627-32a2-47bb-83f1-6936934ec7fb/network.jsonl`：真实原始采集。
- `backend/logs/api-browser-discovery-live/ffd20850-5f5c-4588-a2b6-839c32392148/verified-candidate.json`：最终实际验证的模型候选，文件权限 0600。
- `backend/logs/api-browser-discovery-live/summary.json`：最近一次真实验收的非敏感摘要；后续运行会更新，不是永久历史索引。
- `backend/logs/api-browser-discovery-browser/`：真实前端操作截图，包括交接、多个样本、运行进度和数量限制。
- `backend/logs/api-browser-discovery-e2e/summary.json`：最近一次本地合成站点摘要。

## 验收中发现并修正的问题

- 不能把 `/menu/list/0` 的静态 0 当动态对象 ID；路径模板需响应中的资源主键及已观察对象路径证据。
- 列表唯一筛选后的 ID 提取应匹配实际样本结构，不能要求逐字等于采集时数组下标；零/多匹配仍拒绝后续写入。
- 同数值的外键或同一 `/api/admin` 命名空间不能误判成同一个资源；变量后续重定义不能继续使用旧来源授权。
- 不仅检查 Authorization，还检查 Cookie、常见自定义认证头及正文/查询中的认证字面值；用户明确输入的运行变量和本轮提取值区别于模型自造常量。
- 压缩正文的解码长度大于 Content-Length 不等于截断；保留绝对保存预算及读取后限制标识。
- 页面交接路由重新挂载时需要从已保存来源恢复测试目标，而非只在旧组件里临时赋值。

曾有一轮实际模型连续返回非法的嵌套 `eq/length` 断言，平台三轮校验均拒绝，没有发出候选中的 HTTP 写入，也没有放宽断言伪造通过。随后在提示中明确合法断言格式；重新生成的最终候选通过。失败候选证据仍保留。

## 未覆盖与已知边界

- 真实模型只验收了一个菜单生命周期；真实多场景规划未全量跑，继承原工作区逻辑并有离线回归，不可写成全部接口已经覆盖。
- 模型生成的删除验证仍可能只看当前分页。提示已加强，但未实现通用业务完备性证明；应审阅断言。本次靠额外 ID 详情读回确认实际删除，不能把这个人工检查算作产品自动能力。
- 依赖仅能利用观察到的证据。创建只返回行数、列表缺少唯一过滤条件等情况不能保证自动推导所有前置步骤；认证入口是候选关系，用户需检查选择范围。
- GraphQL 语义、流式/长连接、文件上传、验证码、复杂 SSO、签名与前端加密不保证自动生成可复用 requests 用例。
- Playwright `response.body()` 会整块读取，保存字节预算不等于进程绝对内存上限；压缩膨胀/chunked 应结合部署资源限制。
- 取消不能撤销已发生的网站写入；没有原浏览器无缝恢复或自动按天清理原始证据功能。
- 未对当前 NAS 平台数据库执行迁移，未修改实际 `.env`，未替用户重启 ASGI/Celery；启用前仍需按部署说明操作。

## 可复现命令

在项目根目录使用已有虚拟环境；这些离线/本地命令不修改当前平台数据库：

```bash
backend/.venv/bin/python backend/scripts/test_webui_generation_offline.py api_testing web_testing ai_core.tests.test_mcp_agent_budget ai_core.tests.test_mcp_output_connections scheduled_tasks notifications
PYTHONPATH=backend/apps backend/.venv/bin/pytest -q backend/apps/api_testing/test_requests_runtime.py
node --test frontend/tests/*.test.js
npm --prefix frontend run build
backend/.venv/bin/python backend/scripts/test_api_browser_discovery_migration.py
backend/.venv/bin/python backend/scripts/test_api_browser_discovery_browser.py
backend/.venv/bin/python backend/scripts/test_api_browser_discovery_e2e.py
```

Node 采集测试需要可解析的固定 MCP npm 包所在 PATH；浏览器安装路径沿用项目配置。真实 NAS 测试必须通过 `test_api_browser_discovery_live.py --run-live` 显式启动，会操作测试网站并产生模型费用，不应混入常规离线 CI。

## 0019 MariaDB 部署失败后的补充验证

原错误为 `1071: Specified key was too long; max key length is 3072 bytes`。真实 NAS 只读确认：MariaDB 10.5.29、utf8mb4、16 KiB InnoDB 页面；0018 已登记，0019 未登记。三张新表已建好且为空，规范表的来源字段已添加，延迟外键和部分索引/唯一约束缺失。直接重跑建表迁移或 `--fake` 均不合适。

修复将联合索引缩为 `(task, method)`，不更改 `path` 的 1000 字符容量。专用命令以 0019 历史模型状态核查字段、索引、外键、迁移历史、重复键与孤儿引用；默认不写入。只有 `--apply` 才追加缺失 DDL，最终复核通过后登记迁移，不删除业务数据。

主 Agent 在仅绑定 localhost 的独立 MariaDB 11.4.13 容器实测：

- 新库历史状态升级 0019 通过。
- 还原原长索引，真实复现 1071；只读诊断、续接、再次续接、正常 `migrate` 均符合预期。
- 原 Swagger、原用例、新采集记录与完整 1000 字符中文路径均保留。
- 同名错误索引、重复唯一键、孤儿外键、字段长度不一致四类负例：只读及 `--apply` 都拒绝，未执行补齐 DDL 或登记迁移。
- 离线恢复命令单测 5 项、原 SQLite 历史迁移验收以及 API 回归通过；未把模拟测试当作真实 MariaDB 执行。

实际 NAS 10.5.29 仅运行带 SQL 写入拦截的只读诊断，确认可续接且列出 10 项补齐操作；**没有替用户运行 `--apply`**，因此尚不宣称 NAS 的最终迁移已经完成。独立容器内每次创建的随机临时数据库均在测试后删除，不影响 NAS。后续若需复现，本地专用 MariaDB 端口通过 `--port` 显式指定：

```bash
backend/.venv/bin/python backend/scripts/test_api_browser_discovery_mariadb.py --port <本地临时端口> --case fresh
backend/.venv/bin/python backend/scripts/test_api_browser_discovery_mariadb.py --port <本地临时端口> --case partial
backend/.venv/bin/python backend/scripts/test_api_browser_discovery_mariadb.py --port <本地临时端口> --case conflict
backend/.venv/bin/python backend/scripts/test_api_browser_discovery_mariadb.py --port <本地临时端口> --case duplicate
backend/.venv/bin/python backend/scripts/test_api_browser_discovery_mariadb.py --port <本地临时端口> --case orphan
backend/.venv/bin/python backend/scripts/test_api_browser_discovery_mariadb.py --port <本地临时端口> --case mismatch
backend/.venv/bin/python backend/scripts/test_api_browser_discovery_migration_repair.py
```

不要将该测试脚本改为连接 NAS 或普通平台库。它只连接 localhost 的专用实例，创建并清理本轮随机命名的数据库。
