# WebUI AI 脚本生成 V2（方案 C）设计与实施计划

## 文档状态

- 状态：核心功能已实施，待数据库迁移、服务重启和真实业务场景验收
- 设计日期：2026-08-29
- 当前验证平台：macOS、zsh
- 项目路径：`/Users/maoyijiu/Documents/tg-work/aits_v2`
- 变更级别：高风险，涉及前端交互、生成主流程、数据库、Celery、WebSocket 和测试体系
- 目标模块：WebUI 自动化中的“探索网页生成 Python Playwright 脚本”

## 一句话结论

方案 C 不再让一个 MCP 智能体同时负责“理解需求、探索页面、编写代码和自我判断”，而是建设一条可持久化的生成流水线：

```text
需求理解 → 安全预检 → 只读页面探索 → 结构化证据 → 脚本生成
       → 静态质量检查 → 有限自动修复 → 生成结果说明 → 用户保存
```

用户仍然只需要发起一次生成，但平台内部会按阶段完成检查和修复，并把“生成了什么、为什么这样生成、哪些地方需要人工确认”展示清楚。

---

## 1. 背景与现状判断

### 1.1 当前实现的核心问题

当前 `WebUIPlaywrightAgent` 的主流程为：

```text
load_mcp_config → initialize_mcp → call_mcp → save_script
```

其中 `call_mcp` 同时承担以下职责：

1. 理解自然语言需求；
2. 登录目标系统；
3. 探索页面结构；
4. 查找元素定位器；
5. 推断完整业务流程；
6. 编写最终 Python Playwright 脚本；
7. 判断何时停止探索。

这会产生四类结构性问题：

- **目标冲突**：探索阶段需要不断获取证据，代码生成阶段则需要停止工具调用并稳定输出代码。
- **证据丢失**：页面结构和定位器只存在于智能体上下文中，没有形成可复用、可展示的中间结果。
- **无法验收**：平台只拿到一段代码，无法判断需求是否理解正确、动作是否都有说明、定位器是否脆弱、敏感信息是否泄漏。
- **修复代价高**：任一环节失败时容易从头重跑整个 MCP 流程，消耗时间和模型额度。

当前提示词还明确要求“严禁包含注释”，与本次“新人能够看懂每个动作”的产品目标直接冲突。

### 1.2 当前可复用能力

方案 C 不推翻已有能力，以下部分继续复用：

- Chrome/Chromium 单浏览器执行约束；
- Python Playwright 异步脚本契约：只保存 `async def run(page)`；
- `MCPBrowserToolGuard` 的工具总量、重复操作、连续失败和登录失败保护；
- 项目已有 POM（页面对象模型）元素优先逻辑；
- `script_contract.py` 的脚本语法和浏览器生命周期校验；
- `script_extraction.py` 的动作、定位器和断言 AST 提取能力；
- Celery 异步任务、Redis 取消信号和 WebSocket 消息通道；
- `WebUITestCase.generation_metadata` 的最终生成元数据存储能力。

---

## 2. 产品目标

### 2.1 核心目标

1. **新人看得懂**：脚本顶部说明测试场景，每个业务动作和断言都有中文备注。
2. **一次发起、内部闭环**：用户只点击一次，平台内部完成需求理解、探索、生成、校验和有限修复。
3. **生成过程可解释**：用户能看到 AI 理解的场景、探索到的页面证据、质量检查结果和风险提醒。
4. **失败可定位**：失败明确归属到需求理解、登录、页面探索、代码生成、质量校验或自动修复阶段。
5. **结果可恢复**：刷新页面或 WebSocket 断开后，生成记录和最终结果仍可通过接口重新获取。
6. **安全默认值**：探索阶段默认不提交新增、编辑、删除等业务变更，敏感信息不得写入脚本、日志、WebSocket 或生成元数据。
7. **统一生成逻辑**：AI 脚本生成页和“从测试用例生成脚本”共用同一条后端流水线，避免两套逻辑继续分叉。

### 2.2 “一次生成可用”的定义

本方案中的“一次生成可用”指：

- 用户只提交一次任务；
- 平台内部允许执行一次需求解析、一次页面探索、一次代码生成和最多两次代码修复；
- 最终脚本通过所有硬性静态检查，并达到 `ready` 或 `ready_with_warnings`；
- 用户不需要手工修复语法、浏览器生命周期、完整 URL、缺少断言或敏感信息泄漏等基础问题。

它不承诺所有动态网站都能一次真实执行通过。运行时仍可能受到验证码、权限、测试数据、网络、页面临时变更和第三方服务的影响。

### 2.3 非目标

- 不恢复 Firefox、WebKit、API 或 App 执行选项；WebUI 仍只使用 Chrome/Chromium。
- 不把 MCP 浏览器与 Python Playwright 执行器合并。
- 不在探索阶段默认真实提交新增、编辑、删除、上传或下载操作。
- 不在第一版引入视觉模型、向量模型或 RAG 依赖。
- 不在第一版自动执行生成出的破坏性业务脚本。
- 不用一个模糊的 0～100 分掩盖具体问题，质量结果使用“通过项、警告项、阻断项”展示。
- 不保证模型服务商故障时自动切换到另一个模型，以免不同模型产生不可追踪的结果。

---

## 3. 方案对比

| 方案 | 主要做法 | 优点 | 缺点 | 结论 |
| --- | --- | --- | --- | --- |
| A：只改提示词 | 增加示例、注释要求和场景说明 | 改动小 | 仍是单智能体黑盒，无法稳定校验和修复 | 不采用 |
| B：提示词 + 静态校验 | 保留单次 MCP 调用，生成后增加代码检查 | 能挡住部分坏脚本 | 探索证据仍丢失，修复时可能再次全量探索 | 适合短期补丁，不作为最终方案 |
| **C：分阶段生成流水线** | 探索与生成分离，持久化中间产物，静态质量门禁和有限修复 | 可解释、可恢复、可测试，适合持续迭代 | 涉及数据库和前后端多模块联动 | **采用** |

---

## 4. 用户体验设计

### 4.1 页面命名与说明

建议将用户可见名称从“AI 脚本实验室”调整为：

- 页面名称：`AI 脚本生成`
- 副标题：`AI 先理解测试场景，再通过 Playwright MCP 只读探索页面并生成 Python 脚本`
- 主按钮：`分析并生成脚本`

“实验室”容易让用户误以为生成结果只是临时演示；页面实际已经承担生成并保存正式测试用例的职责，应使用更明确的产品名称。

生成表单按以下顺序展示：

1. WebUI 测试环境（必选）；
2. 环境 Base URL（只读展示，来自项目环境）；
3. 起始相对路径（可选，默认 `/`）；
4. 场景描述；
5. 本次探索登录信息（按需展开）；
6. 当前使用模型（明确展示，多个可用模型时可选择）。

不再把任意完整 URL 作为主要输入。兼容期如仍接收完整 URL，后端必须校验它与所选 WebUI 环境属于同一 Origin（协议、主机和端口相同），并转换为相对路径。这样生成脚本和后续执行始终使用同一套 Base URL 语义。

### 4.2 测试描述示例

测试描述输入框保留 2000 字限制，增加“插入示例”按钮和可折叠的编写提示。不要只把长文本塞进 placeholder，避免用户输入后无法再次查看示例。

建议示例：

```text
目标：验证“权限 > 用户列表”的新增、查询、编辑和删除流程。

前置条件：使用测试账号登录；登录信息从本次任务的临时登录信息或环境变量读取。

步骤：
1. 登录后进入“权限 > 用户列表”；
2. 使用唯一名称和账号新增用户，并验证列表中出现该用户；
3. 编辑本轮新增用户的昵称，并验证更新成功；
4. 删除本轮新增用户，并查询验证该用户不存在。

约束：
- 唯一数据使用 time.time_ns() 生成；
- 不操作已有业务数据；
- 使用 try/finally 清理本轮创建的数据；
- 探索阶段只查看页面和打开表单，不提交新增、编辑或删除；
- 登录密码不得写入脚本、日志、截图或报告。
```

编写提示只强调四件事：

1. 测试目标；
2. 主要操作步骤；
3. 每一步如何判断成功；
4. 不允许执行的操作和清理要求。

### 4.3 登录信息与场景描述分离

用户目前容易把账号密码直接写入测试描述。V2 建议增加独立的“本次登录信息”区域：

- 用户名：可选，仅供当前生成任务探索使用；
- 密码：密码框，可选，仅供当前生成任务探索使用；
- 不显示在场景描述、实时输出、脚本、生成记录或质量报告中；
- Worker 只通过短期凭据引用读取，任务结束或取消后立即删除；
- 生成脚本中如需要登录，只能引用环境变量，例如 `UI_TEST_USERNAME` 和 `UI_TEST_PASSWORD`，不得写入真实值。

第一版凭据可使用 Redis 短期缓存保存，并设置明确 TTL；数据库只记录 `credentials_required`、`credentials_provided` 和 `credentials_expired` 等状态，不记录值。Redis 数据丢失时任务进入“登录信息已失效”，不得退回到场景描述中查找密码。

这里必须区分两类数据：

- **临时探索凭据**：只供本次 MCP 登录页面，任务结束后删除；页面要明确提示该值会发送给当前配置的 AI 模型用于完成登录探索。
- **可复用脚本凭据引用**：保存脚本只读取所选 WebUI 环境中的变量名或部署进程环境变量，例如 `UI_TEST_USERNAME`，不使用临时值。

如果场景需要登录，但所选环境没有可复用凭据引用，平台应在质量报告中提示“保存后执行还需要配置环境变量”，不能让新人误以为临时探索密码会自动写进正式用例。

前后端都要检测场景描述中常见的账号密码写法。发现疑似凭据时不持久化原文，并提示用户将它移动到“本次探索登录信息”区域。`mcp_use` 等第三方库可能在 INFO 日志中打印完整查询，因此 V2 必须关闭该库的原始查询日志，改由平台记录经过脱敏的安全提示摘要；不能只依赖正则在日志产生后补救。

### 4.4 生成前是否要求用户确认

为了满足“一次点击完成”，普通场景默认自动继续，不强制增加一次人工确认。

只有以下情况暂停并要求确认：

- 需求存在互相冲突的步骤；
- 没有明确成功标准，且模型无法安全补全；
- 用户要求探索阶段提交新增、编辑、删除等业务变更；
- 检测到上传文件、付款、发布、审批、批量删除等高风险操作；
- 需要登录，但没有可用的临时凭据或环境变量引用。

其余情况在“场景理解”面板中实时展示 AI 的理解，并自动进入探索阶段。

### 4.5 生成进度

前端时间线改为真实阶段：

| 阶段 | 用户文案 | 主要产物 |
| --- | --- | --- |
| `normalize_requirement` | 理解测试场景 | 场景摘要、步骤、断言和约束 |
| `safety_preflight` | 检查风险与登录条件 | 风险项、是否可自动继续 |
| `explore_page` | 探索页面 | 页面路径、元素和定位器证据 |
| `generate_script` | 生成 Python 脚本 | 第一版脚本 |
| `validate_script` | 检查脚本质量 | 通过项、警告项、阻断项 |
| `repair_script` | 修复脚本 | 修复后的脚本，按需出现 |
| `finalize_result` | 整理生成结果 | 最终脚本、摘要和元数据 |

每个阶段展示开始时间、耗时和状态。前端不得依靠固定延时猜测节点完成状态，必须使用后端事件或查询结果。

### 4.6 结果页面

生成结果由当前“实时输出 / 最终脚本”两项扩展为：

1. **场景摘要**：脚本目的、前置条件、步骤、断言、清理逻辑。
2. **最终脚本**：可复制，保存后可进入用例管理继续编辑。
3. **探索证据**：访问过的相对路径、识别到的关键元素、采用的定位器、未确认项。
4. **质量报告**：硬性检查、警告、自动修复记录。
5. **技术日志**：开发调试信息，默认折叠。

生成状态定义：

| 状态 | 含义 | 保存行为 |
| --- | --- | --- |
| `ready` | 所有硬性检查通过，无重要警告 | 可以保存 |
| `ready_with_warnings` | 硬性检查通过，但有定位器或业务证据警告 | 可以保存，保存前提示 |
| `needs_review` | 有无法自动修复的阻断项或探索证据不足 | 不作为可执行用例保存，生成记录仍保留 |
| `failed` | 模型、浏览器、配置或系统阶段失败 | 不可保存，展示阶段和解决建议 |
| `cancelled` | 用户主动取消 | 不可保存，可重新发起 |

---

## 5. 生成脚本的可读性契约

### 5.1 脚本顶部场景说明

最终脚本顶部必须包含经过脱敏和规范化的模块文档字符串，不直接复制用户原始输入：

```python
"""
场景：用户管理新增、编辑、查询与删除
目标：验证本轮创建的用户能够完成完整生命周期操作
前置条件：测试环境可访问，登录信息由环境变量提供
清理策略：在 finally 中删除本轮创建的数据
"""
```

约束：

- 不包含完整目标 URL；
- 不包含用户名、密码、Token、Cookie 或其他凭据值；
- 不复制用户输入中的无关内容；
- 不声称探索阶段没有确认的业务事实。

### 5.2 动作备注

所有业务动作前必须有中文备注：

```python
# 步骤 1：打开用户列表页面
await page.goto("/permission/users")

# 步骤 2：点击“新增用户”按钮
await page.get_by_role("button", name="新增用户").click()

# 步骤 3：填写本轮生成的唯一用户账号
await page.get_by_label("用户账号").fill(unique_username)
```

### 5.3 断言备注

所有 Playwright `expect` 断言前必须有中文备注：

```python
# 断言 1：确认新增用户出现在列表中
await expect(page.get_by_text(unique_username, exact=True)).to_be_visible()
```

### 5.4 清理备注

涉及新增业务数据时，`finally` 中的清理动作必须说明清理对象和触发条件：

```python
finally:
    # 清理：仅删除本轮使用 unique_username 创建的测试用户
    if created:
        ...
```

### 5.5 注释覆盖规则

- 业务动作覆盖率必须为 100%；
- 断言覆盖率必须为 100%；
- 变量赋值、纯计算和 `try/finally` 结构不要求逐行注释；
- 不接受 `# 点击按钮`、`# 填写内容` 这类缺少业务含义的重复注释；
- 优先由生成模型根据 `ScenarioSpec` 和探索证据生成语义备注；
- 质量检查器使用 Python `tokenize` 保留并检查注释，AST 只负责识别动作和断言；
- 若缺少备注且能从角色、标签或文本中确定业务含义，可确定性补充；无法确定时进入自动修复，不生成虚假的业务描述。

---

## 6. 后端总体架构

### 6.1 设计原则

- Celery 任务负责确定性的阶段编排和终态通知；
- MCP 智能体只负责页面探索，不负责输出最终 Python 代码；
- 普通 LLM 调用负责需求规范化、脚本生成和代码修复；
- 每个阶段都产生结构化数据并持久化；
- 每个阶段有独立超时、重试和错误码，不从头重跑整个任务；
- 阶段服务不直接发送终态 WebSocket 消息，避免当前重复完成通知；
- Django ORM 操作保持在同步服务层，异步 MCP 探索不直接调用 ORM。

### 6.2 目标组件

```text
Generation API
      ↓
WebUIScriptGenerationTask（Celery，唯一编排者）
      ├── RequirementNormalizer（普通 LLM）
      ├── SafetyPreflight（确定性规则 + 结构化需求）
      ├── MCPPageExplorer（MCPAgent，仅探索）
      ├── ScriptGenerator（普通 LLM，不使用浏览器工具）
      ├── ScriptQualityGate（AST + tokenize + 规则）
      ├── ScriptRepairer（普通 LLM，最多两次）
      ├── GenerationRepository（阶段状态和产物持久化）
      └── GenerationEventPublisher（WebSocket 事件）
```

阶段编排不再依赖一个开放式 LangGraph 循环。MCPAgent 内部仍可使用自己的工具推理，但只被允许输出结构化探索结果，并继续受工具守卫约束。

### 6.3 两种入口统一

| 入口 | 输入适配 | 最终行为 |
| --- | --- | --- |
| AI 脚本生成页 | URL + 场景描述 + 可选环境/登录信息 | 生成记录完成后由用户保存为测试用例 |
| 测试用例管理中的“生成脚本” | 已有用例标题、描述、步骤、预期结果 + 环境 | 生成完成后更新该用例的脚本和最终元数据 |

两种入口只在 `GenerationInputAdapter` 和最终保存策略上不同，中间阶段完全共用。

---

## 7. 状态机与重试边界

### 7.1 状态机

```text
created
  ↓
normalizing
  ├──→ needs_input
  ↓
preflighting
  ├──→ needs_confirmation
  ├──→ needs_credentials
  ↓
exploring
  ├──→ needs_review（证据不足且不适合继续探索）
  ↓
generating
  ↓
validating
  ├──→ ready
  ├──→ ready_with_warnings
  └──→ repairing ──→ validating（最多两次）
                         └──→ needs_review

任意非终态 ──→ cancelled
系统异常 ──→ failed
```

### 7.2 调用预算

| 阶段 | 默认预算 | 说明 |
| --- | --- | --- |
| 需求规范化 | 1 次 LLM | JSON 格式错误时允许 1 次轻量格式修复，不重新理解需求 |
| 页面探索 | 1 轮 MCP | 默认最多 50 次浏览器工具、60 个智能体步骤，沿用现有配置入口 |
| 补充探索 | 最多 1 次 | 仅当质量门禁明确给出 `missing_evidence`，且不是登录/重复/预算错误 |
| 脚本生成 | 1 次 LLM | 不允许调用浏览器工具 |
| 脚本修复 | 最多 2 次 LLM | 每次只接收原脚本和结构化问题列表 |
| 完整任务重启 | 0 次 | 非暂时性错误不得自动从需求理解重新开始 |

### 7.3 重试策略

| 错误 | 当前阶段重试 | 全流程重跑 | 用户提示 |
| --- | --- | --- | --- |
| 模型 `429` | 否 | 否 | 当前模型限流，请稍后重试或更换模型 |
| 模型 `503/504` | 1 次 | 否 | 模型服务暂时不可用 |
| MCP 连接临时断开 | 1 次 | 否 | 浏览器探索连接中断 |
| Chromium 缺失 | 否 | 否 | 检查 Celery 机器浏览器安装与路径 |
| 登录失败 | 否 | 否 | 检查登录信息和登录页面 |
| 工具调用上限 | 否 | 否 | 显示已完成探索和未确认项 |
| 重复操作/连续定位失败 | 否 | 否 | 显示最后动作和目标元素 |
| 脚本语法/契约错误 | 最多修复 2 次 | 否 | 展示仍未修复的质量问题 |
| 用户取消 | 否 | 否 | 已取消 |

---

## 8. 阶段数据契约

所有阶段使用 Pydantic 模型进行运行时校验，数据库保存 JSON 形式。字段名称可以在实施时微调，但语义不得退化为自由文本。

### 8.1 `ScenarioSpec`

```text
title                 场景标题
objective             测试目标
preconditions[]       前置条件
steps[]               规范化业务步骤
  id                  稳定步骤编号，如 S1
  name                面向用户的中文步骤名
  intent              navigate/read/create/update/delete/assert/cleanup
  target_hint         目标页面或元素提示
  input_refs[]        输入引用，不保存敏感值
  mutates_data        是否修改业务数据
  expected            本步骤成功标准
assertions[]          场景级断言
cleanup[]             清理动作
forbidden_actions[]   禁止动作
credentials_required 是否需要登录
ambiguities[]         无法安全确定的需求
risk_level            low/medium/high
```

### 8.2 `ExplorationSnapshot`

```text
start_url_path        起始相对路径
visited_paths[]       已访问相对路径，不保存带 Token 的查询参数
page_states[]         页面名称、标题和关键区域
elements[]            元素角色、可见名、稳定属性、候选定位器
navigation_paths[]    从起点到目标页面的操作链
step_evidence{}       ScenarioSpec.step.id 到页面证据的映射
unresolved_steps[]    未取得足够证据的步骤及原因
warnings[]            iframe、动态列表、重复文本等风险
tool_stats            调用次数、失败次数、终止原因和耗时
```

不保存完整 HTML、截图 Base64、Cookie、localStorage、响应正文或输入框真实值。探索截图只用于 MCP 临时判断，默认不进入数据库和 WebSocket。

### 8.3 `QualityReport`

```text
status                ready/ready_with_warnings/needs_review
checks[]
  code                稳定检查码
  level               pass/warning/blocker
  message             用户可读说明
  line                 可选脚本行号
  step_id              可选业务步骤编号
repair_attempts[]      每次修复前后的问题码，不保存模型思维过程
summary               通过、警告、阻断数量
```

### 8.4 `GenerationResult`

```text
generation_id
status
scenario_spec
exploration_summary
test_script
quality_report
warnings
model_info
tool_stats
test_case_id           保存后或用例入口生成时返回
```

---

## 9. 数据库存储设计

### 9.1 新增 `WebUIScriptGeneration` 模型

`WebUITestCase.generation_metadata` 只适合保存最终结果摘要，无法记录失败任务、阶段进度和待确认状态。方案 C 建议新增独立生成记录模型。

建议字段：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `id` | UUID | 对外生成记录 ID |
| `project` | ForeignKey | 项目隔离 |
| `user` | ForeignKey | 发起用户 |
| `environment` | ForeignKey | 本次生成使用的 WebUI 环境 |
| `test_case` | nullable ForeignKey | 从已有用例生成时关联 |
| `source_mode` | CharField | `manual_prompt` / `test_case` |
| `celery_task_id` | CharField | 当前异步任务 ID |
| `status` | CharField | 状态机状态 |
| `current_stage` | CharField | 当前阶段 |
| `progress` | PositiveSmallInteger | 后端提供的真实阶段进度 |
| `start_path` | CharField | 环境内的相对起始路径 |
| `target_url_safe` | URL/Text | 只用于审计展示的脱敏组合地址 |
| `description_safe` | Text | 脱敏后的用户描述 |
| `scenario_spec` | JSONField | 规范化场景 |
| `exploration_snapshot` | JSONField | 结构化探索证据 |
| `script_draft` | TextField | 当前脚本草稿 |
| `quality_report` | JSONField | 质量检查结果 |
| `warnings` | JSONField | 用户可见警告 |
| `model_info` | JSONField | 配置 ID、提供方、模型名，不保存 API Key |
| `tool_stats` | JSONField | 工具调用与终止统计 |
| `repair_count` | PositiveSmallInteger | 已修复次数 |
| `error_code` | CharField | 稳定错误码 |
| `error_message` | TextField | 用户可读错误，不保存原始敏感异常 |
| `started_at/completed_at` | DateTimeField | 阶段耗时统计 |
| `created_at/updated_at` | DateTimeField | 审计时间 |

索引建议：

- `(project, -created_at)`；
- `(user, status)`；
- `celery_task_id` 唯一或唯一非空；
- `(status, updated_at)`，用于后续识别僵尸任务。

### 9.2 与测试用例的关系

- 未保存前，脚本只存在于 `WebUIScriptGeneration.script_draft`；
- 保存成功后，继续通过 `store_script_content(..., source="mcp_exploration")` 写入 `WebUITestCase`；
- `generation_metadata` 保存最终精简投影：`generation_id`、提示版本、模型信息、探索统计、质量摘要和提取元数据；
- 不把每次修复的大段脚本副本重复塞进 `generation_metadata`；
- `needs_review` 状态的脚本保留在生成记录中，但不写成 `ready` 测试用例。

### 9.3 敏感信息

- 数据库、Celery 任务结果、WebSocket 和普通日志都只使用脱敏描述；
- 临时凭据以 `generation_id` 为键保存在 Redis，设置 TTL，并在成功、失败、取消时删除；
- Celery 参数只传 `generation_id`，不传用户名和密码；
- 原始 MCP 查询日志关闭，由平台写入不含凭据的阶段摘要；
- 原始 URL 中的 `token`、`auth`、`key`、`session` 等查询参数保存前替换为 `<redacted>`；
- 即使开发环境开启完整提示日志，凭据字段也必须始终脱敏，不能把安全能力推迟到正式环境。

---

## 10. 各阶段详细设计

### 10.1 需求规范化

输入：安全描述、目标 URL 的站点信息、可选现有用例步骤。

输出：`ScenarioSpec`。

规则：

- 模型只能整理用户明确表达的目标，不得编造响应内容或业务字段；
- 自动补充项仅限通用测试工程约束，例如唯一数据、清理逻辑和断言必要性，并在 UI 标记“平台补充”；
- 对歧义内容写入 `ambiguities`，不隐藏不确定性；
- 解析结果使用严格 JSON + Pydantic 校验；
- JSON 格式错误只允许一次格式修复，不重新调用页面探索。

### 10.2 安全预检

使用确定性规则处理：

- URL 和项目权限校验；
- WebUI 环境是否启用、是否属于当前项目、是否配置 Base URL；
- 起始地址是否与所选环境同源，并规范化为相对路径；
- 是否存在激活的 MCP Playwright 配置；
- 是否存在可用的激活 LLM 配置；
- 是否需要登录信息；
- 是否包含高风险业务变更；
- 是否存在“不要修改数据”与“探索时提交新增”等冲突；
- 是否有可判断的成功标准和清理策略。

预检不调用浏览器，避免配置错误时先消耗 MCP 和模型时间。

### 10.3 页面探索

MCP 提示只要求返回 `ExplorationSnapshot`，明确禁止输出最终 Python 脚本。

探索行为分级：

| 行为 | 默认是否允许 | 说明 |
| --- | --- | --- |
| 打开页面、读取可见文本和结构 | 允许 | 必要证据 |
| 登录 | 有凭据时允许 | 只建立本次会话 |
| 打开菜单、Tab、查询条件、表单弹窗 | 允许 | 不提交业务数据 |
| 输入临时查询值 | 谨慎允许 | 不触发业务写入 |
| 提交新增、编辑、删除、审批、付款 | 禁止 | 除非后续专门设计受控探索模式 |
| 上传、下载、发布 | 禁止 | 第一版不支持 |

探索器必须将每个 `ScenarioSpec.step.id` 标记为：

- `confirmed`：有页面证据；
- `partially_confirmed`：只确认入口或字段；
- `unresolved`：没有证据。

工具守卫除现有总数、重复和失败计数外，增加：

- 当前业务步骤 ID；
- 最后成功页面路径；
- 最后失败动作和定位目标；
- 只读/交互/登录工具分类统计；
- 终止原因稳定错误码。

### 10.4 脚本生成

脚本生成器不连接 MCP，只接收：

- `ScenarioSpec`；
- `ExplorationSnapshot`；
- 项目 POM 候选；
- Python Playwright 脚本契约；
- 注释与场景说明契约；
- 质量门禁规则版本。

涉及登录的脚本只生成环境变量引用。执行器需要把所选 WebUI 环境的 `config.variables` 安全注入 Pytest 子进程环境；变量值不得出现在命令行、日志或执行详情中。若当前执行链尚未注入这些变量，应作为 V2 的必要兼容改动完成，而不是把临时探索密码写入脚本兜底。

定位器优先级：

1. 项目已保存且仍匹配本次探索证据的 POM 元素；
2. `get_by_role(..., name=...)`；
3. `get_by_label(...)`；
4. `get_by_placeholder(...)`；
5. `get_by_text(..., exact=True)`；
6. 稳定的 `data-testid` 或业务属性；
7. CSS/XPath 仅作为警告级兜底。

禁止生成：

- 完整 URL；
- 浏览器启动和关闭代码；
- `main()`、`test_*` 或 `if __name__ == "__main__"`；
- 明文凭据；
- `time.sleep()`；
- 无断言脚本；
- 对探索未确认字段的伪造定位器；
- 将整个业务流塞进一条无法解释的 `evaluate()`。

### 10.5 静态质量门禁

质量检查器必须是确定性代码，不调用 LLM。第一版检查项：

#### 阻断项

- Python 语法错误；
- 不满足 `async def run(page)`；
- 管理浏览器生命周期；
- 使用完整 URL；
- 明文凭据或疑似敏感值；
- 没有任何 Playwright `expect` 断言；
- 业务动作或断言缺少中文备注；
- 需求要求新增数据但没有清理逻辑；
- 引用了探索结果中明确不存在的元素；
- 使用未定义变量或未导入名称；
- 使用同步 Playwright API。

#### 警告项

- 使用 XPath、深层 CSS 或 `nth()`；
- 使用非精确文本且页面中存在多个同名元素；
- 关键步骤只有部分探索证据；
- 断言只验证“元素可见”，没有验证业务结果；
- 清理逻辑存在但无法确认入口；
- 过多固定超时或 `wait_for_timeout()`；
- 脚本步骤与 `ScenarioSpec` 不完全对应。

#### 通过项

- 顶部场景说明已脱敏；
- 动作和断言备注覆盖率 100%；
- 访问路径为相对路径；
- 脚本契约正确；
- 至少一个业务结果断言；
- 动态测试数据和清理逻辑符合需求；
- POM/语义定位器优先。

### 10.6 自动修复

修复器只接收：

- 当前脚本；
- 阻断项和警告项的结构化列表；
- 对应 `ScenarioSpec` 步骤；
- 必要的探索证据片段。

修复器不得重新自由探索页面，不得改变用户业务目标。每次修复后都重新执行完整静态门禁。

最多两次修复：

1. 第一次处理所有阻断项；
2. 第二次只处理仍存在的阻断项，不为消除普通警告大幅重写脚本；
3. 两次后仍有阻断项，状态为 `needs_review`；
4. 仅当问题码为 `missing_evidence` 时，编排器才允许一次定向补充探索。

### 10.7 最终化与保存

最终化阶段：

- 再次执行敏感信息扫描；
- 生成用户可读场景摘要；
- 固化提示版本、契约版本和质量规则版本；
- 只由 Celery 编排层发送一次终态 WebSocket 事件；
- 页面保存时再次执行 `normalize_for_storage()`，防止前端或并发过程绕过契约。

---

## 11. 模型配置策略

当前生成任务按创建时间选择激活模型；没有激活模型时还可能回退到已禁用配置。对长任务而言，这会让用户难以确认实际使用的模型。

V2 设计：

- 创建任务时解析并锁定一个 `model_config_id`；
- 生成记录保存提供方和模型名，不保存 API Key；
- 前端显示“本次使用模型”；
- 多个激活模型存在时允许用户从激活列表选择，默认使用平台当前默认项；
- 每个任务创建独立 `ModelManager`，禁止通过共享实例在并发任务中切换配置；
- 不允许回退到 `is_active=False` 的模型；
- 模型在任务中途失效时返回明确错误，不静默切换模型重写结果。

若本轮希望控制范围，可先实现“锁定并展示默认模型”，把下拉选择作为同一数据契约下的后续前端能力，但后端不能继续使用禁用模型兜底。

---

## 12. API 与 WebSocket 设计

### 12.1 API

建议接口：

```text
POST   /api/projects/{project_id}/webui/script-generations/
GET    /api/projects/{project_id}/webui/script-generations/{generation_id}/
POST   /api/projects/{project_id}/webui/script-generations/{generation_id}/cancel/
POST   /api/projects/{project_id}/webui/script-generations/{generation_id}/save/
POST   /api/projects/{project_id}/webui/script-generations/{generation_id}/continue/
```

创建接口只返回：

```json
{
  "generation_id": "uuid",
  "task_id": "celery-task-id",
  "status": "created"
}
```

查询接口返回完整 `GenerationResult`。因此即使 WebSocket 断开或用户刷新页面，也能恢复当前进度和最终结果。

现有创建、停止和保存接口保留一轮兼容，由内部转发到新服务；前端切换完成后再移除旧路径。

### 12.2 WebSocket 事件

建议新增统一生成事件，而不是继续让前端猜测节点：

```text
generation_stage_changed
generation_artifact_ready
generation_warning
generation_completed
generation_failed
generation_cancelled
```

所有事件必须包含：

```text
generation_id
task_id
stage
status
progress
timestamp
```

终态事件只允许 Celery 编排器发送一次。页面收到不属于当前 `generation_id` 的事件必须忽略，避免同一用户同时运行多个任务时串消息。

技术流式日志仍可保留，但它不再承担业务状态来源。

---

## 13. 错误码与用户提示

| 错误码 | 阶段 | 用户提示重点 |
| --- | --- | --- |
| `INPUT_INVALID` | 预检 | URL、描述或环境不完整 |
| `MODEL_CONFIG_MISSING` | 预检 | 没有启用的 LLM 配置 |
| `MODEL_UNAVAILABLE` | 任意 LLM | 当前模型不可用，展示模型名 |
| `MODEL_RATE_LIMITED` | 任意 LLM | 当前模型限流，不会自动全流程重试 |
| `MCP_CONFIG_MISSING` | 预检 | 没有启用的 Playwright MCP 配置 |
| `BROWSER_UNAVAILABLE` | 探索 | Celery 机器的 Chromium 不可用 |
| `CREDENTIALS_REQUIRED` | 预检 | 需要提供临时登录信息或环境变量 |
| `CREDENTIALS_IN_DESCRIPTION` | 预检 | 描述中疑似包含凭据，请移到临时登录信息 |
| `CREDENTIALS_EXPIRED` | 探索 | 临时登录信息已过期 |
| `LOGIN_FAILED` | 探索 | 账号、密码或登录页面不匹配 |
| `EXPLORATION_LIMIT_REACHED` | 探索 | 显示工具数、已确认步骤和未确认步骤 |
| `REPEATED_INTERACTION` | 探索 | 显示重复动作和目标元素 |
| `LOCATOR_FAILURE_LIMIT` | 探索 | 显示连续失败的定位目标 |
| `EVIDENCE_INSUFFICIENT` | 探索/质量 | 无法确认的业务步骤 |
| `SCRIPT_FORMAT_INVALID` | 生成 | 模型没有输出可解析脚本 |
| `QUALITY_BLOCKED` | 校验 | 自动修复后仍存在的阻断项 |
| `TASK_CANCELLED` | 任意 | 用户已取消任务 |
| `TRANSIENT_SERVICE_ERROR` | 任意 | 临时网络或服务错误 |

后端日志可保留异常堆栈，但前端只显示稳定错误码对应的用户提示和解决建议。日志、WebSocket 和数据库中的错误文本都必须先脱敏。

---

## 14. 可观测性设计

每次生成记录至少保留：

- 各阶段开始、结束和耗时；
- 模型配置 ID、提供方和模型名；
- 需求解析、生成和修复调用次数；
- MCP 浏览器工具总数及分类；
- 重复操作、定位失败、登录尝试和终止原因；
- 静态检查通过、警告、阻断数量；
- 自动修复次数及修复的问题码；
- 最终状态和稳定错误码。

不记录：

- API Key；
- 登录密码、Token、Cookie；
- 浏览器存储状态；
- 完整 HTML；
- 模型内部思维过程；
- 未脱敏的工具输入和输出。

日志使用 `generation_id` 作为关联 ID。相同模型消息不在根日志和模块日志重复输出；WebSocket 技术日志也执行短窗口去重。

---

## 15. 预计代码结构

后端建议新增：

```text
backend/apps/web_testing/
├── generation_contracts.py       # Pydantic 阶段数据契约
├── generation_orchestrator.py    # 确定性阶段编排和状态转换
├── generation_repository.py      # 持久化和幂等更新
├── generation_events.py          # WebSocket 事件封装
├── requirement_normalizer.py     # 需求规范化
├── mcp_page_explorer.py          # 只读 MCP 探索
├── script_generator.py           # 脚本生成与修复调用
├── script_quality.py             # AST/tokenize 静态质量门禁
└── generation_security.py        # URL、描述、日志和凭据脱敏
```

现有文件改动：

| 文件 | 计划改动 |
| --- | --- |
| `backend/apps/web_testing/models.py` | 新增生成记录模型 |
| `backend/apps/web_testing/migrations/0011_*.py` | 新增表和索引 |
| `backend/apps/web_testing/tasks.py` | 由单体 Agent 调用改为编排器入口 |
| `backend/apps/web_testing/views.py` | 新增创建、查询、取消、继续和保存接口 |
| `backend/apps/web_testing/serializers.py` | 新增输入和结果序列化器 |
| `backend/apps/web_testing/urls.py` | 注册生成记录接口 |
| `backend/apps/ai_core/model_manager.py` | 支持按配置 ID 创建任务级模型，不回退禁用配置 |
| `backend/apps/ai_core/webui_playwright_agent.py` | 收缩为探索能力或由新 Explorer 替代，移除代码生成职责 |
| `backend/apps/web_testing/script_contract.py` | 接入最终质量状态和场景注释契约 |
| `backend/apps/web_testing/script_extraction.py` | 提取步骤备注和断言备注，保留脱敏 |
| `backend/apps/web_testing/playwright_python_runner.py` | 将所选 WebUI 环境变量安全注入执行子进程 |

前端建议拆分，避免继续扩大单个 `WebUIAutoTest.vue`：

```text
frontend/src/views/web-testing/WebUIAutoTest.vue
frontend/src/components/webui-generation/
├── GenerationInputPanel.vue
├── GenerationTimeline.vue
├── GenerationScenarioSummary.vue
├── GenerationEvidence.vue
├── GenerationQualityReport.vue
└── GenerationResultPanel.vue
frontend/src/composables/useWebUIScriptGeneration.js
frontend/src/api/webTesting.js
```

---

## 16. 分阶段实施计划

方案 C 应一次设计完整，但按可验收的阶段实施，避免一个大提交同时改变所有链路。

### 阶段 0：基线与契约测试

1. 为现有正常生成、停止任务、保存脚本和用例入口建立回归测试。
2. 固化当前脚本契约、MCP 工具守卫和 WebSocket 关键行为。
3. 准备 10 个不含真实凭据的代表性场景样本和对应模型桩输出。
4. 明确旧接口兼容窗口。

验收：现有功能基线可重复，后续失败能够明确归因到新改动。

### 阶段 1：生成记录、状态机和安全输入

1. 新增 `WebUIScriptGeneration` 模型和迁移。
2. 实现状态转换、项目权限、幂等更新和取消检查。
3. 新增创建、查询、取消接口。
4. 实现描述、URL、元数据和日志脱敏。
5. 实现临时凭据引用和 TTL 清理。
6. Celery 参数调整为只接收 `generation_id`。

验收：刷新页面后任务状态可恢复；数据库、Celery 参数和日志中找不到测试密码。

### 阶段 2：需求规范化与产品输入

1. 定义 `ScenarioSpec` Pydantic 契约。
2. 实现需求规范化和一次 JSON 格式修复。
3. 实现确定性安全预检和 `needs_*` 状态。
4. 前端改为选择 WebUI 环境和相对入口路径，完整 URL 仅做同源兼容。
5. 前端增加示例、编写提示、临时登录信息和场景理解展示。
6. 显示并锁定本次模型配置。

验收：示例可一键插入；相同输入产生结构稳定的步骤、断言和约束；高风险或缺少凭据时不会启动 MCP。

### 阶段 3：只读探索与结构化证据

1. 将 MCP 提示从“探索并输出代码”改为“只输出探索证据”。
2. 定义并校验 `ExplorationSnapshot`。
3. 将现有 POM 查询放到同步准备阶段，避免异步 ORM 调用。
4. 扩展工具守卫统计和稳定错误码。
5. 持久化相对路径、元素、定位器和未确认步骤。
6. 前端增加探索证据和未确认项展示。

验收：探索完成后数据库中存在结构化证据但没有最终代码；探索默认不提交业务写操作；工具上限和登录失败信息准确。

### 阶段 4：脚本生成、备注和场景说明

1. 新增独立 `ScriptGenerator`，不提供 MCP 工具。
2. 重写生成提示，移除“严禁注释”。
3. 强制生成脱敏场景文档字符串、动作备注、断言备注和清理备注。
4. 继续使用相对 URL、POM 优先和 `async def run(page)`。
5. 扩展脚本提取器，读取备注并关联业务步骤。
6. 将所选 WebUI 环境的可复用变量安全注入 Python 执行子进程。

验收：生成脚本中动作和断言备注覆盖率 100%；顶部场景说明不含凭据和完整 URL；脚本可通过现有存储契约。

### 阶段 5：质量门禁与有限自动修复

1. 实现 `QualityReport` 和全部 P0 阻断规则。
2. 实现定位器、证据和断言质量警告。
3. 实现最多两次的定向代码修复。
4. 实现一次 `missing_evidence` 定向补充探索。
5. 两次修复后仍失败则进入 `needs_review`，不伪装为成功。

验收：语法错误、明文凭据、无断言、无备注、完整 URL、浏览器生命周期和未定义变量都无法成为 `ready` 脚本。

### 阶段 6：结果页面、恢复和保存闭环

1. 前端接入真实阶段事件和 `generation_id` 过滤。
2. 增加场景摘要、最终脚本、探索证据、质量报告和技术日志。
3. 页面刷新时通过查询接口恢复任务。
4. 保存时将最终精简元数据写入测试用例。
5. 用例入口切换到同一生成流水线。
6. 移除 Agent 和 Celery 重复发送的终态消息。

验收：刷新或 WebSocket 重连不丢结果；同一用户并行任务不串消息；两种入口生成规则一致。

### 阶段 7：真实场景评估与灰度切换

1. 使用本地可控页面验证所有阶段。
2. 使用用户当前“权限 > 用户列表”场景进行只读探索和脚本生成。
3. 由用户确认后执行生成脚本，检查新增、编辑、查询、删除和清理。
4. 对失败样本只调整对应阶段，不整体放宽工具或递归上限。
5. 新旧流程通过配置开关并行一轮；V2 达标后移除旧生成入口。

验收：达到下文发布门槛，并有明确回滚开关。

---

## 17. 测试矩阵

### 17.1 后端单元测试

- `ScenarioSpec` 合法、缺字段、冲突和高风险解析；
- URL 查询参数、描述、日志和嵌套元数据脱敏；
- 状态机合法与非法跳转；
- 生成记录项目隔离；
- Redis 临时凭据创建、读取、过期和终态删除；
- WebUI 环境项目隔离、Base URL 同源校验和相对路径规范化；
- 环境变量注入执行子进程且不进入日志；
- MCP 探索结果 JSON 校验；
- POM 与探索证据优先级；
- AST 动作、断言、未定义变量和浏览器生命周期检查；
- `tokenize` 注释覆盖检查；
- 完整 URL、明文凭据、`time.sleep()` 和同步 API 拦截；
- 修复最多两次和补充探索最多一次；
- 取消任务在每个阶段生效；
- 终态 WebSocket 只发送一次。

### 17.2 后端集成测试

- 创建 → 规范化 → 探索 → 生成 → 校验 → 保存完整链路；
- 第一次生成失败、第一次修复成功；
- 两次修复失败进入 `needs_review`；
- WebSocket 不可用但数据库任务继续完成；
- Worker 重试时不重复创建生成记录或测试用例；
- 模型 `429`、`503`、浏览器缺失、登录失败和工具超限；
- AI 脚本页入口与已有用例入口结果契约一致。

### 17.3 前端测试

- 示例插入、2000 字计数和表单校验；
- WebUI 环境选择、Base URL 只读展示和相对入口路径；
- 临时密码框不进入普通 form 日志；
- 七阶段时间线和条件阶段展示；
- `generation_id` 不匹配消息被忽略；
- 页面刷新恢复；
- `ready`、`ready_with_warnings`、`needs_review`、`failed` 和 `cancelled` 展示；
- 质量报告和技术日志折叠；
- 保存按钮状态与后端质量状态一致。

### 17.4 可控页面场景集

至少覆盖：

1. 登录后读取列表；
2. 菜单 + 二级菜单导航；
3. 查询表格；
4. 新增弹窗；
5. 编辑弹窗；
6. 删除确认框；
7. Tab 切换；
8. 下拉选择；
9. 重复文本和多匹配定位器；
10. 登录失败；
11. 页面元素缺失；
12. 需求互相冲突；
13. 包含敏感凭据的输入；
14. 用户主动取消；
15. 模型返回非 JSON 或非 Python 内容。

---

## 18. 发布验收标准

### 18.1 P0 必须满足

- 100% 生成脚本满足 `async def run(page)` 存储契约；
- 100% 业务动作和断言具有中文备注；
- 100% 脚本顶部具有脱敏场景说明；
- 凭据不出现在脚本、数据库生成元数据、Celery 参数、WebSocket 和普通日志中；
- 生成和执行使用同一个 WebUI 环境，保存脚本只包含相对路径；
- 没有断言、完整 URL、浏览器生命周期或语法错误的脚本不能进入 `ready`；
- 探索阶段默认不提交新增、编辑、删除、上传和发布；
- 自动修复和补充探索有硬上限，不出现无限循环；
- 刷新页面后能够恢复生成状态和结果；
- 同一用户并行任务不串消息；
- AI 脚本页和测试用例入口共用同一质量门禁。

### 18.2 受控样本目标

在固定模型、固定测试页面和固定提示版本下：

- 10 个基础样本中至少 8 个第一次生成后直接通过静态门禁；
- 经过内部自动修复后至少 9 个达到 `ready` 或 `ready_with_warnings`；
- 其余样本必须进入可解释的 `needs_review`，不得生成看似成功但不可执行的脚本；
- 同一 CRUD 样本连续运行 3 次，场景步骤和质量状态保持稳定，允许定位器代码存在无业务影响的差异。

这些指标是发布门槛，不是对所有外部网站的成功率承诺。

### 18.3 用户手工验收

以“权限 > 用户列表”场景为例：

1. 用户使用示例结构填写描述并提供临时登录信息；
2. 页面展示平台理解出的新增、查询、编辑、删除和清理步骤；
3. 探索阶段只打开页面、菜单和表单，不提交业务数据；
4. 结果页展示关键定位器和未确认项；
5. 最终脚本顶部有场景说明，每个动作和断言都有中文备注；
6. 脚本使用唯一测试数据并只清理本轮数据；
7. 质量报告能说明脚本为何可保存或为何需要检查；
8. 用户保存后可在测试用例管理中查看、编辑和运行。

---

## 19. 风险与控制措施

| 风险 | 影响 | 控制措施 |
| --- | --- | --- |
| 多次 LLM 调用增加耗时和费用 | 单任务成本上升 | 结构化上下文压缩；修复最多两次；不全流程重跑 |
| 不同模型 JSON 能力差异 | 规范化或探索结果解析失败 | Pydantic 校验；一次格式修复；固定提示版本和模型信息 |
| 只读探索无法确认提交后的页面 | 部分 CRUD 断言证据不足 | 标记警告；脚本依据明确需求生成；不编造未确认字段 |
| 临时凭据过期 | 长任务无法继续登录 | 独立错误状态；合理 TTL；页面提示重新提供，不从日志恢复 |
| 数据库 JSON 内容增长 | 查询和备份变大 | 不保存 HTML/截图/多版脚本；生成记录设置后续保留策略 |
| 新旧流程并存造成混乱 | 结果不一致 | 统一后端入口；配置开关只控制路由，不维护两套规则 |
| 过严质量门禁导致较多 `needs_review` | 初期可保存率下降 | 先区分 blocker/warning；基于真实样本调整规则，不直接放宽安全项 |
| 自动修复改变业务意图 | 脚本偏离需求 | 修复输入只包含问题列表；修复后再次核对 ScenarioSpec 步骤覆盖 |

---

## 20. 回滚策略

- 新流程通过 `WEBUI_SCRIPT_GENERATION_V2_ENABLED` 配置开关启用；
- 旧接口在灰度期保留兼容转发，但旧 Agent 不再与 V2 同时处理同一任务；
- 新增数据库表不影响已有测试用例和执行记录；
- 前端关闭开关后可恢复旧入口展示；
- 已生成并保存的脚本继续符合现有 `async def run(page)` 契约，无需回滚数据；
- 回滚时保留生成记录表只读，避免丢失用户已经生成的结果。

---

## 21. 建议实施顺序与提交边界

建议拆为以下独立提交，每个提交都能运行测试并回滚：

1. `WebUI生成V2：新增生成记录、状态机和安全数据契约`
2. `WebUI生成V2：增加需求规范化与安全预检`
3. `WebUI生成V2：分离MCP只读探索并保存结构化证据`
4. `WebUI生成V2：增加脚本场景说明和步骤备注`
5. `WebUI生成V2：增加静态质量门禁和有限自动修复`
6. `WebUI生成V2：重构前端生成流程和结果展示`
7. `WebUI生成V2：统一测试用例入口并完成灰度切换`

数据库迁移只放在第一个提交；后续提交不得反复修改同一迁移文件。每阶段完成后先跑聚焦测试，最后统一执行后端系统检查、前端构建和真实 Chrome 冒烟验证。

---

## 22. 最终建议

建议按本方案完整实施，但把“自动执行生成出的破坏性脚本并根据运行结果自愈”留到 V2 稳定之后。当前最重要的是先建立可靠的结构化流水线、质量门禁和可解释结果。

这能直接解决本次提出的三点：

1. 测试描述有可直接使用的示例和编写结构；
2. 每个动作和断言都有面向新人的中文备注；
3. 脚本顶部有脱敏后的场景说明。

同时补齐当前产品真正缺失的部分：需求理解可见、探索证据可追踪、脚本质量可校验、失败可恢复、凭据不泄漏、修复次数有边界，以及两个生成入口使用同一套规则。
