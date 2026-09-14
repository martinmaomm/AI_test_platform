# API 系统回归：第一批隔离验收

日期：2026-09-14。业务代码基线：`4ff5a4c`；开始执行时 HEAD：`3ecbf87`。本次仅维护测试脚本和文档，没有修改产品业务代码、前端源码或数据库结构。

对应 [59 项清单与验收标准](../plans/2026-09-14-api-regression-checklist.md)。

## 结论

**本轮执行的隔离回归通过，未发现新的产品主流程阻断问题；不代表 59 项全量验收或真实模型验收通过。**

- 后端、前端、核心编辑流程、账号保护、计划任务和邮件页面已有本轮证据。
- 本机真实 Chrome/MCP/HTTP 完成探索、采集、工作区生成验证、保存后两次独立执行及 Python 导出；模型为可控替身，不证明付费模型的生成质量。
- 维护了 5 个过时的验收脚本；另为 2 个本机 MCP 测试入口补充预装 npm 目录选项，解决离线包存在但 registry 元数据缺失时无法启动的问题。
- 第二批尚未开始：真实平台登录账号未配置、Beat 无心跳、SMTP/通知接收组为空。已询问用户登录准备和邮件验收安排。

## 1. 验证边界与环境

| 项目 | 本轮事实 |
| --- | --- |
| 系统 | macOS / arm64；Python 3.13.11；Node 26.7.0；npm 11.19.0 |
| Python | 项目 `backend/.venv`；没有重新安装依赖 |
| 业务测试数据库 | 临时 SQLite；不使用 NAS 数据库进行迁移或造测试数据 |
| 网络 | 单元测试拦截 socket；浏览器平台服务仅回环；目标站为本机 HTTP 夹具；模型、Celery 派发、SMTP 按专项替换 |
| 本机 MCP | 已安装的 `@executeautomation/playwright-mcp-server@1.0.12`，依赖 Node Playwright 1.57.0，使用本机 Chrome；未下载或升级 |
| 真实服务预检 | 单独只读查询 NAS 数据库配置/迁移及 Redis/worker 状态；数据库会话设为 `TRANSACTION READ ONLY`；不执行真实业务请求 |
| 真实模型/邮件 | 未调用平台配置的付费模型，未向真实收件人发信 |
| 服务 | 未重启后端或 Celery，未启动 Beat；隔离测试的临时服务自行关闭 |
| 原有文件 | 未改动或提交既有 `docs/api-generation-test-2026-09-14.md`、3 个浏览器 ZIP |

本轮没有把上一次报告中的测试数累计为新覆盖率。下表均为本轮运行结果；浏览器专项可能共享底层测试代码，不将其结果数量解释为相互独立的真实业务场景数。

## 2. 运行结果与证据

新日志目录：[20260914-batch1-ABbEeP](../../backend/logs/api-regression/20260914-batch1-ABbEeP/)。日志与截图仅本地保留，不提交 Git。部分脚本仍输出到既有固定截图目录，下面列出本轮实际查看的文件，不将目录里的旧 `failure.png` 误算为本轮失败。

| 验证层级 | 本轮结果 | 证据 |
| --- | --- | --- |
| Django/unittest：API、计划任务、通知 | 291/291 通过；迁移文件一致性检查通过 | `backend-django.log` |
| requests 与账号保护函数 | 57/57 通过；禁止真实 socket，禁用 pytest 插件自动加载 | `runtime-account-pytest.log` |
| 前端单测 | 289/289 通过，无跳过 | `frontend-unit.log` |
| 前端生产构建 | 通过，8.16 秒；仍有大 bundle 提示 | `frontend-build.log` |
| Node 网络采集器 | 最终 11/11 通过，无跳过，含真实 Chrome 测试 | `capture-js-local-package.log` |
| 工作区核心浏览器专项 | 9 个脚本通过 | 第 2.1 节 |
| 修正后的交互/失败浏览器专项 | 2 个脚本通过，由 Terra 执行、主 Agent 审查差异和截图 | 第 2.2 节 |
| 来源页、自动 origin 浏览器专项 | 2 个脚本通过；自动 origin 使用真实 MCP | 第 2.3 节 |
| 计划、邮件、原生报告浏览器专项 | 3 个脚本通过；邮件仅本地 outbox | 第 2.4 节 |
| 多场景/依赖/验证保护离线专项 | 3 个维护后的脚本通过 | 第 2.2 节 |
| 定向迁移 | 0018→0019、0019→0020 在临时 SQLite 通过 | 两个 migration 脚本同名日志 |
| 本机 MCP→API→独立导出 | 通过；两次独立复跑、无遗留测试对象 | 第 2.3 节 |

日志中的 `DiscoveryStopped`、provider 不可用、SMTP 认证失败、损坏规范等包含测试主动制造的异常分支。结论以对应断言、退出码和终态为准，不能仅统计日志里的 ERROR 数量。

### 2.1 核心浏览器专项（顺序运行）

以下脚本均通过；日志为新日志目录下的 `<脚本名>.log`：

1. `test_api_workspace_browser.py`：生成、采用、调试、失败修复、保存、复制/导出、报告、禁用模型。
2. `test_api_endpoint_workspace_browser.py`：端点入口、三步骤保留、边界分类、重新编辑及正式执行。
3. `test_api_workspace_reverify_browser.py`：草稿恢复、编辑后重验成功/失败，无额外 AI 调用。
4. `test_api_workspace_navigation_browser.py`：干净/已保存工作区切换、不丢未保存输入。
5. `test_api_workspace_reliability_browser.py`：取消、历史执行证据、版本、受保护断言确认。
6. `test_api_workspace_assertion_types_browser.py`：数字与显式字符串的保存、重载和实际比较。
7. `test_api_workspace_key_value_browser.py`：Query/Headers 数字输入可改，原类型和未编辑值保留。
8. `test_api_workspace_length_browser.py`：长度大于零配置，非空通过、空数组失败。
9. `test_api_multi_scenario_browser.py`：独立场景切换、采用、保存，重命名/删除工作区保留正式用例。

### 2.2 维护后的 5 个专项

| 脚本 | 修正依据和最终结果 |
| --- | --- |
| `test_api_multi_scenario_offline.py` | 适配进度/取消回调；子场景记录自己的创建排队时间、共享根批次截止时间；PASS |
| `test_api_scenario_dependencies_offline.py` | 适配回调；弱化既有断言时保留候选并进入人工审阅，断言没有新增业务请求/额外模型调用；PASS |
| `test_api_verification_guards_offline.py` | 适配回调，保留禁用模型、静态失败、授权与修复保护断言；PASS |
| `test_api_workspace_interactions_browser.py` | 采用按钮使用精确名称，不匹配问号说明按钮；PASS |
| `test_api_scenario_failure_browser.py` | 按“关联 API 端点”表单项定位，不误点“步骤阶段”；PASS |

三个离线脚本输出归档为新日志目录下 `terra-<脚本名（不含 .py）>.log`。浏览器证据见 [interaction-saved.png](../../backend/logs/api-workspace-browser-check/interaction-saved.png)、[scenario-failure-evidence.png](../../backend/logs/api-workspace-browser-check/scenario-failure-evidence.png)、[scenario-manual-editor.png](../../backend/logs/api-workspace-browser-check/scenario-manual-editor.png)。

没有通过放宽业务断言、跳过执行保护或修改产品逻辑来让测试通过。实际 runner 在父进程接收 `on_progress/should_cancel`，底层运行核心接收 `on_checkpoint`；替身按照已有浏览器 harness 桥接两层回调。

### 2.3 网页来源与本机真实 MCP

- `test_api_browser_discovery_browser.py` 通过：来源分离、已授权样本、重复采样、部分完成、交接、删除失败记录、迟到响应等。采集器在该脚本里为替身。
- `test_api_browser_auto_origin_browser.py` 最终通过：真实本机 MCP 请求先等待 origin 确认，确认后只登录 1 次；新增/修改/删除各 1 次，剩余对象 0；取消等待中的探索没有发出业务请求，浏览器无 JS 错误。
- `test_api_browser_discovery_e2e.py` 通过：17 次工具调用、18 次**模拟模型**调用，9 条网络记录形成 5 个端点；进入工作区后验证通过，正式用例两次独立复跑和 Python 导出通过；重复派发未重复执行，交接幂等，剩余对象 0。

后两项日志分别是 `cached-path-test_api_browser_auto_origin_browser.py.log`、`cached-path-test_api_browser_discovery_e2e.py.log`。对应汇总：[自动 origin](../../backend/logs/api-browser-auto-origin-browser/summary.json)、[探索到导出](../../backend/logs/api-browser-discovery-e2e/summary.json)。**两次本机复跑不能替代第二批要求的真实候选三连跑。**

MCP 环境问题经过如下区分：

1. 直接 `node --test` 只有 8 项通过、3 项跳过，原因是 PATH 不包含固定 MCP 包，不能记 11 项通过。
2. `npx --offline --package=...@1.0.12` 返回 `ENOTCACHED`，并非不存在包文件；缺的是 npm registry 元数据。
3. 确认本机已安装包版本为 1.0.12 后，给测试进程指定 npm prefix 并补齐 PATH；最终 Node 11 项及真实 MCP 集成均通过。

两个 Python MCP 验收脚本新增可选 `TEST_MCP_NPM_PREFIX`，默认行为不变。复现本机解决方式（前缀是本轮已核验的本地目录，不是通用部署路径）：

```bash
TEST_MCP_NPM_PREFIX=/Users/maoyijiu/.npm/_npx/655b6792dec26b14 \
PATH="/Users/maoyijiu/.npm/_npx/655b6792dec26b14/node_modules/.bin:$PATH" \
backend/.venv/bin/python backend/scripts/test_api_browser_auto_origin_browser.py
```

未修改生产 MCP 配置、npm 缓存里的包源码或依赖版本。真实平台当前 MCP 配置没有 `--offline`，不能据此推断线上配置也存在同样故障。

### 2.4 共享页面

- `test_scheduled_tasks_browser.py`：UI/API 套件选择、排序、加载失败重试、Beat 离线/未知/恢复提示通过。
- `test_notifications_browser.py`：SMTP 表单、UI/API 接收组、通知选择、报告邮件内容通过；实际发送进本地 outbox，未连接 SMTP。
- `test_platform_reports_browser.py`：UI/API/计划任务原生报告、历史快照、刷新和未登录跳转通过。

主 Agent 查看了本轮 [端点三步骤](../../backend/logs/api-workspace-browser-check/endpoint-generation/multistep-endpoint.png)、[历史报告](../../backend/logs/api-workspace-browser-check/reliability-standard-report.png)、[来源确认完成](../../backend/logs/api-browser-auto-origin-browser/approved-complete.png)，未见阻断操作的遮挡。报告部分表头仍为英文，这是体验项，不是本轮业务故障修复范围。

## 3. 对照清单的覆盖与剩余项

“隔离通过”只指列出的单元/夹具/浏览器证据，不等于该项真实联调完成；一项包含多个要求时，未证实部分在最后一列明确保留。没有将任何真实模型结果记为通过。

| ID | 本轮覆盖 | 尚未覆盖 / 下一批要求 |
| --- | --- | --- |
| PRE-01 | 版本、修改范围、真实数据库无待迁移、前后端进程目录已核对 | 真实任务需登记届时运行版本 |
| PRE-02 | 临时 SQLite、网络边界、模型/SMTP 替身检查通过 | — |
| PRE-03 | 5 个过时脚本维护并通过；MCP 离线入口适配并通过 | — |
| PRE-04 | 已只读确认 API 项目、规范和环境地址 | 被测站账号身份/角色读回、本轮数据清单待真实联调 |
| PRE-05 | worker 在线且空闲，启用模型及 MCP 可读取 | Beat 无心跳；SMTP/接收组为空；真实平台登录凭证待配置 |
| DOC-01 | Swagger/OpenAPI、请求类型、响应示例和本地引用解析隔离通过 | 真实页面上传文档路径待验 |
| DOC-02 | 损坏规范、外部引用拒绝、事务回滚、失效目标隔离通过 | 实际页面删除验收规范待验 |
| DOC-03 | 来源页、项目/工作区切换、未保存提示浏览器通过 | — |
| DOC-04 | 重命名、删除、恢复及迟到响应隔离通过 | — |
| END-01 | 固定目标、入口、辅助范围及归属校验通过 | 真实模型端点生成待验 |
| END-02 | 三步骤保存为端点、重开修改、保留保护声明和执行通过 | 真实端点任务待验 |
| END-03 | 边界类型浏览器保存、类型持久化/列表相关单测通过 | 全部四分类逐项浏览器操作不作已验声明 |
| END-04 | 冷启动/恢复、旧响应隔离、目标覆盖率单测通过 | 全组合加载失败场景未逐项真实操作 |
| GEN-01 | 模型选择、禁用/所有者变更、无隐式回退隔离通过 | 所选真实模型请求待验 |
| GEN-02 | 多场景独立认证、目标/依赖、未定义 token 隔离通过 | 真实多场景生成质量待验 |
| GEN-03 | 静态失败、有限修复、验证结果和不自动保存通过 | 真实生成/修复耗时及费用待验 |
| GEN-04 | 中间场景失败后继续、人工编辑入口及历史通过 | 真实场景修复待验 |
| GEN-05 | 弱化断言/认证/保护声明被要求审阅，无越界自动执行 | 真实候选仍须逐个审阅 |
| GEN-06 | 输入、重复派发、规划修正、无效候选轮次相关测试通过 | 50/20 上下边界逐项组合不作已验声明 |
| EDIT-01 | Query/Headers 输入、JSON/form/raw、保存重载通过 | 真实请求读回待验 |
| EDIT-02 | 数字与字符串断言、严格类型、实际错误类型隔离通过 | — |
| EDIT-03 | 显式索引、过滤唯一性、缺失/多匹配、后续写步骤隔离通过 | 真实响应结构待验 |
| EDIT-04 | 长度大于零及空/非列表/变量阈值通过 | 真实删除结果的查询完整性待人工核对 |
| EDIT-05 | 提取、缺失变量、当前作用域、唯一值、会话隔离通过 | 真实独立重跑待验 |
| EDIT-06 | 已修改待验证、按钮可用、不调用 AI、版本及保存通过 | — |
| RUN-01 | 本机独立 HTTP 重跑两次通过 | 真实每个最终候选三连跑未做 |
| RUN-02 | 失败跳过、清理依赖、未知写入边界/取消通过 | 真实对象清理读回待验 |
| RUN-03 | 执行/套件/用例/环境变量及历史快照隔离通过 | 真实套件传参待验 |
| RUN-04 | 套件顺序、失败继续、成员快照和变量隔离单测通过 | 真实套件运行待验 |
| RUN-05 | 浏览器复制/导出、本机独立 Python 执行和运行核心一致性通过 | 真实候选独立环境导出运行待验 |
| RUN-06 | 本机 HTTPS、固定 API TLS 策略和导出一致性通过 | 不是生产 TLS 安全验收 |
| CAP-01 | origin 留空、入口/来源分离、本机 MCP 与输入通过 | 真实站点探索和模型能力待验 |
| CAP-02 | 本机直接跨 origin 请求确认、不重放登录、取消和跳转边界通过 | 重定向自动追随的既有限制仍保留 |
| CAP-03 | 重复摘要合并与原始证据保留、响应体/状态差异相关测试通过 | 真实接口样本待验 |
| CAP-04 | 部分完成、失败/停止原因、原始轨迹及样本状态通过 | 真实覆盖范围待验 |
| CAP-05 | 选样本交接、不自动生成用例、不重放、幂等与归属通过 | 真实工作区交接待验 |
| CAP-06 | 本机重新认证、动态 ID、独立数据及失败任务管理通过 | 真实登录和复跑待验 |
| ERR-01 | 模型故障、禁用、无效候选、证据保留相关隔离通过 | 各 HTTP 错码及流中断排列未全量注入，第三批补齐 |
| ERR-02 | 排队/执行/根批次预算分离、总时限、过期任务通过 | 不据此宣称真实长流式请求通过 |
| ERR-03 | 排队/运行取消、迟到结果、等待来源时取消通过 | — |
| ERR-04 | 刷新、切换、恢复、标准执行记录通过 | — |
| ERR-05 | 已发送写请求超时/异常后保留证据，不盲目自动重放通过 | 不对真实站主动制造未知写入 |
| ERR-06 | worker 子进程协议/检查点、失联过期和派发失败通过 | 没有停止真实共享 worker 验证重启恢复 |
| ERR-07 | revision 冲突、绑定用例外部修改、旧确认/候选隔离通过 | — |
| AUTH-01 | 项目/所有者/执行和报告权限隔离通过 | 真实登录/退出和无权限账号浏览器待验 |
| AUTH-02 | 保护身份、共享角色、变量覆盖和清理阶段拦截通过 | 真实敏感修改不作为验收手段 |
| AUTH-03 | 临时对象、唯一关联、同 ID 不同资源类型、普通业务通过 | — |
| AUTH-04 | 显式语义声明、移除声明拒绝、导出一致性通过 | 缺失声明仍不是自动安全沙箱，真实候选须审阅 |
| REC-01 | 无环境/正式用例的工作区记录、来源、列表及权限通过 | 真实 Celery 执行记录待验 |
| REC-02 | 步骤响应/提取/断言/跳过、冻结地址及原生报告通过 | 真实套件完整报告待验 |
| REC-03 | 空脚本、失败/取消/跳过及汇总相关测试通过 | 零成员套件等全部组合的专项证据待第三批补齐 |
| REC-04 | 历史刷新、报告链接、未登录跳转、原生日志通过 | 真实接收人的邮件链接访问待验 |
| SCH-01 | API/UI 套件归属、环境要求、顺序和非法通知对象通过 | 真实 API 验收计划尚未创建 |
| SCH-02 | 计划规则、锁和重复派发相关隔离通过 | 真实 Beat 到点派发、立即执行待验 |
| SCH-03 | 离线/未知/恢复显示和心跳时间窗通过 | 当前真实 Beat 为离线，尚未启动 |
| SCH-04 | SMTP 表单、通知条件、测试接收组和 outbox 通过 | SMTP 配置及实际收信待用户准备 |
| SCH-05 | 通知失败处理、部分拒收及报告链接规则相关隔离通过 | 真实 SMTP 接受、送达、登录查看均未验 |
| SMK-01 | UI/计划报告及共享前端单测通过 | 已有 UI 脚本真实调试/截图仍未执行 |
| SMK-02 | 共享模型/邮件/项目切换随专项覆盖 | 知识库页面只读冒烟尚未单独运行 |

这里没有用单元测试总数计算“59 项通过率”。“部分覆盖”和“待真实联调”不能改成“通过”。

## 4. 下一批准备项

只读预检结果（后续执行前需再次核对，不能当持续监控）：

- MariaDB 10.5.29；当前没有待执行迁移。
- API 项目：`后台商城API`（ID 2）；规范：`商城后台api-docs.json`（ID 1）；环境 `dev` 的地址为 `http://192.168.31.188:9999`。
- 之前指定的 `思辰科技 · gpt-5.6-luna`（ID 5）当前启用，用户的其他模型未修改。未发起连通性或生成请求。
- 后端端口 8000、前端端口 5173 在监听；1 个 worker 对 ping 响应，active/reserved 均为 0；检查时没有活动探索任务。工作区历史原样保留。
- Beat 无心跳；数据库没有计划任务。本轮未启动 Beat，也未创建真实计划。
- SMTP 配置 0 条，接收组 0 条。
- 尚未配置 `TEST_PLATFORM_USERNAME` / `TEST_PLATFORM_PASSWORD`；这些仅为后续验收入口读取的临时配置，不是新增产品功能。不要提交到 Git。

下一步在平台登录准备完成后，沿用已授权测试环境和指定模型按 LIVE-A/B/C 小样本执行；每个任务沿用现有有限修复预算，不自动扩大接口范围、换模型或提高调用上限。若邮件选择暂缓，报告必须保留“真实邮件未验证”。Beat 启动前再确认没有新增的其他计划或在途任务。

## 5. 本次改动与收尾

- 测试维护而非产品修复；不要求用户重启服务。
- 保留首次失败/跳过及修正后通过的日志，不将修改前的脚本标为直接通过。
- 临时 SQLite 与测试站对象由测试自身回收；本机 MCP E2E 汇总确认剩余对象 0。没有删除 NAS 数据、原有工作区、用例、截图或压缩包。
- 最终结论：**第一批已执行范围通过；全量验收继续待办，不应据此宣布 API 模块全部验收完成。**
