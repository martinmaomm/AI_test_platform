# WebUI 单智能体连续探索与脚本生成设计

## 文档状态

- 状态：已实施，等待真实页面验收
- 日期：2026-09-02
- 目标：一个 Playwright MCP 智能体在一个浏览器会话和一段连续上下文中完成整个测试场景，平台根据真实工具轨迹生成可重复运行的 Python Playwright 脚本
- 数据策略：开发阶段不兼容旧 GoalPlan 执行记录、GoalRun 或 v3 轨迹；允许清理旧生成数据并直接采用新契约
- 范围：需求整理、MCP 探索编排、轨迹记录、回放选择、Python 编译、质量门禁、生成状态和证据展示
- 非范围：测试用例执行器、测试套件、API/App 自动化、生产环境写入

## 1. 已确认问题

当前实现虽然复用一个 MCP 浏览器会话，但会为每个 Goal 新建一个 Agent，并分别调用模型：

1. 新 Agent 不继承上一段工具调用和定位器上下文；
2. 后续 Goal 容易重新导航起始页面，破坏已到达的业务状态；
3. 已成功定位的元素不能自然复用，模型会重新猜测 selector；
4. 一个安全的定位失败也可能被当前 Goal 的写入属性放大为整次探索终止；
5. Goal 状态、事件归属和最终重组增加了失败点，却没有提高真实页面探索能力；
6. 用户描述一个完整业务场景，平台却把它拆成多个互相失忆的执行单元，与 Codex 连续探索行为不一致。

## 2. 目标流程

```text
用户测试描述
  → AI 一次性整理完整场景计划、运行时变量和安全边界
  → 创建一个 MCP Client、一个浏览器会话、一个 MCP Agent
  → Agent 在一次连续运行中完成登录、导航、操作、验证和清理
  → 平台 callback 增量记录真实工具调用、页面状态和允许保存的截图
  → Agent 用平台本地 checkpoint 工具标记最终动作、机器断言和清理归属
  → 平台从成功事件整理可回放动作，不采用失败 selector
  → 确定性编译 Python Playwright 脚本
  → 静态质量检查
  → 用户在脚本工作区检查、调试和保存
```

“不拆分执行”不等于没有计划。场景计划是交给同一个 Agent 的完整待办和约束，不再成为多个 Agent 的运行边界，也不再维护逐 Goal 状态。

## 3. 单智能体执行边界

1. 每个生成任务只创建一个 `MCPAgent`，只执行一次完整场景探索。
2. 每个生成任务只创建一个 MCP Client 和浏览器会话。
3. 目标地址只在首次进入或确认会话丢失时导航；业务步骤之间不得自动回到 `start_path`。
4. Agent 始终保留本轮工具输出、页面状态、成功定位器和失败原因。
5. 完整场景计划、运行时变量映射、临时凭据和安全策略一次性传入 Agent。
6. Agent 最终文本不承担 JSON、定位器或 Python 契约；callback 轨迹是唯一事实来源。
7. 平台允许一次安全的定位失败后重新观察并调整；连续重复失败、登录失败、总预算、总时限和高风险动作仍会终止。
8. 测试环境可按场景目标写入本轮唯一命名空间内的测试数据；审批、付款、发布、上传等额外高风险动作继续阻止。
9. 平台在同一个已初始化 Agent 中注册 `aits_record_checkpoint` 本地工具，不额外创建 Agent、MCP Client 或浏览器会话。
10. checkpoint 只约束下一次真实 Playwright callback；失败 callback 会消费标记，未标记的探索动作只用于诊断。

## 4. 新场景计划

旧 `GoalPlan.goals[]` 不再作为执行契约。新计划只描述完整场景：

```text
ScenarioPlan
├── schema_version
├── title
├── objective
├── instructions[]              # 完整业务流程清单，不承担独立状态
├── success_criteria[]          # 用户可验证结果
├── assertion_requirements[]    # 每条成功标准对应的机器可编译语义
├── input_refs[]                # generated | runtime | credential
├── preconditions[]
├── forbidden_actions[]
├── credentials_required
├── allow_test_data_writes
├── cleanup_expected
├── discovery_notes[]
└── risk_level
```

规则：

- 页面字段、按钮、菜单路径和 selector 由 Agent 在目标网站动态探索；
- 不通过固定业务词表识别页面控件；
- 登录凭据只在任务内存中使用，不写入计划、轨迹、脚本或日志；
- `generated` 输入由平台生成唯一值，`runtime` 和 `credential` 输入在执行时显式提供；
- 每条 `success_criteria` 必须且只能对应一个 `assertion_requirement`，断言语义限定为可见、包含或不包含变量/字面值；
- 清理场景必须声明独立的 cleanup 断言，且不能用“仍包含目标值”证明清理成功；
- 计划可以包含自然语言步骤，但不生成 G1/G2 等独立执行状态。

## 5. 连续探索轨迹

新轨迹删除 `goal_runs` 和事件 `goal_id`，按真实发生顺序记录：

```text
ContinuousExplorationTrace
├── schema_version
├── start_path
├── events[]
│   ├── event_id / sequence
│   ├── tool_name / action / status
│   ├── phase / intent / checkpoint_id
│   ├── assertion_id / assertion_kind / assertion_status
│   ├── relative_path
│   ├── locator_input
│   ├── input_refs / input_source
│   ├── action_arguments
│   ├── before_state_id / after_state_id
│   ├── result_excerpt
│   └── screenshot_path
├── checkpoints[]
├── assertion_evidence[]
├── page_states[]
├── locator_evidence[]
├── replay_event_ids[]
├── cleanup_event_ids[]
├── cleanup_verification_event_ids[]
├── assertion_event_ids[]
├── cleanup
├── tool_stats
├── warnings[]
├── termination_reason
└── last_location
```

记录要求：

- callback 发生即落入任务 JSONL，异常结束也保留部分轨迹；
- `main/replay`、`assertion/evidence`、`cleanup/cleanup` 和 `cleanup/evidence` 必须由 checkpoint 显式绑定下一次 callback；
- checkpoint 本身不是动作、定位器、断言或清理成功证据；只有匹配语义且成功的 callback 才能入选；
- 失败和拦截事件仅用于诊断，永不进入脚本；
- MCP 返回“元素不存在”“不可见”“超时”等错误文本时必须记录为失败，而不是成功观察；
- 成功定位器来自真实成功工具调用，不允许后处理模型新增或修改 selector；
- `last_location` 应反映真实当前页面，而不是始终沿用起始路径；
- 失败快照保留真实已用时，不得写成 0 秒。
- 含临时凭据的任务禁用 MCP 原始文件日志和截图工具，避免第三方工具把请求体或会话状态落盘。

## 6. 回放整理与 Python 编译

1. 回放只引用已记录的成功事件 ID；失败事件不能进入脚本。
2. 导航、点击、填写、选择、按键、勾选等动作必须有可验证的 LocatorEvidence。
3. 填写和选择动作必须精确映射到一个运行时变量。
4. 回放整理可以剔除重复观察和探索绕路，但不得生成轨迹中不存在的动作或 selector。
5. 主流程和清理流程都按 callback `sequence` 混排动作与断言，必须保留“操作 → 断言 → 后续操作”的真实顺序。
6. 若无法安全判断某个成功动作是否属于最终路径，保留可检查草稿并给出警告，不把整个任务伪装成失败。
7. 编译器生成场景说明、按顺序编号的中文步骤注释、变量读取、语义断言和真实清理结构，不生成 `finally: pass` 等占位代码。
8. 模板变量支持字符串、字典、列表和元组中的递归解析；脚本仍使用统一 `async def run(page, variables)` 入口和相对地址。

## 7. 错误恢复

可恢复错误：

- selector 未找到、元素不可见/未启用、严格模式冲突；
- 页面尚未加载完成；
- 一次普通点击、填写或观察失败且可确认动作未执行。

处理方式：重新观察当前页面，在总预算内最多进行有限调整；不得立即归类为写入结果未知。

必须终止：

- 登录后连续检查仍停留在登录页面；
- 相同失败操作连续重复；
- 总工具预算、模型步数或总探索时间耗尽；
- 审批、付款、发布、越权上传等禁止动作；
- 有证据表明写入可能已经发生，但结果确实无法确认。

终止时保留最后成功事件、失败元素、截图和部分轨迹，并向用户显示准确原因。

## 8. 状态与前端展示

生成状态简化为：

```text
理解测试目标 → 连续探索页面 → 整理回放路径 → 生成 Python → 检查脚本 → 完成
```

前端不再展示 Goal 执行表，改为：

- 当前探索阶段和累计工具调用；
- 成功动作时间线；
- checkpoint 归属、机器断言覆盖和清理验证；
- 失败/拦截尝试及用户可读原因；
- 页面观察、截图和最后位置；
- 定位器证据质量；
- 清理与残留风险；
- “探索未完成但证据已保留”与“系统失败”的明确区分。

## 9. 验收标准

1. 同一个生成任务只初始化一个 MCP Agent，且完整场景只调用一次 Agent 运行入口。
2. 登录、进入业务页面和后续操作共享同一上下文，不会在步骤切换时自动回到首页。
3. 后续动作可以利用本轮先前发现的页面结构和定位器。
4. 一个尚未执行的元素定位失败不会直接终止整个探索。
5. 生成脚本中的每个业务 selector 均来自成功 callback 事件。
6. 失败 selector、重复探索和无关观察不会进入最终脚本。
7. 旧 v3 Goal 轨迹不兼容，代码和前端不再依赖 `goal_runs`。
8. 网站语言、菜单名称和业务模块变化无需修改平台固定词表。
9. 凭据、Token、Cookie、完整 URL 和运行时敏感值不进入持久化轨迹或脚本。
10. 动作和断言按真实 callback 顺序编译，清理必须有真实动作以及发生在最终清理动作之后的语义验证。
11. 后端离线回归、Django 检查、迁移漂移检查、Python 编译、MCP 启动脚本测试、前端测试与生产构建通过。

## 10. 回退

- 代码可回退到实施前提交 `1a15379`；
- 开发数据库中的新旧生成记录均可直接清理，不提供双读；
- 探索期间已写入目标测试环境的数据不随代码回退自动删除，需依据任务截图和清理状态人工核对。
