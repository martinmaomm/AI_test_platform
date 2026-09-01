# WebUI 通用目标探索与可回放脚本生成 v3

## 文档状态

- 状态：已实施并通过离线回归，待真实网站验收
- 日期：2026-09-01
- 目标：移除页面文案和 CRUD 词表对探索证据的绑定，使任意网站都能通过 Playwright MCP 探索并生成可重复运行的 Python Playwright 脚本
- 数据策略：开发阶段不兼容 v2 生成记录或轨迹；允许清理旧数据并直接采用 v3 契约
- 范围：需求归一化、MCP 探索编排、事件账本、定位证据、回放计划、Python 生成、质量门禁、生成进度展示和离线回归
- 非范围：自动运行生成脚本、生产环境写入、API/App 自动化、跨用例复用

## 1. 已确认问题

当前实现存在三个互相放大的词表依赖：

1. 需求预检使用固定中文动作词判断描述是否包含步骤和结果；
2. 场景步骤使用 `create/update/delete` 等 CRUD 业务意图；
3. 探索轨迹通过按钮、选择器和页面文本中的固定词，将成功事件反向匹配到场景步骤。

这会导致按钮使用“保存”“确定”、英文文案、其他语言、图标按钮或自定义组件时，平台无法确认步骤覆盖；即使 MCP 已经完成真实操作，也可能把事件归错步骤，最终造成证据不足或生成错误定位器。

固定的 Playwright 原子动作和定位策略不是本问题。`click/fill/select/press/navigate/observe` 描述浏览器协议，与网站业务无关，应继续保留；需要删除的是页面文案到业务意图的固定映射。

## 2. 目标流程

```text
用户描述
  → AI 整理为通用 GoalPlan
  → 同一浏览器会话按 Goal 逐段探索
  → Recorder 在调用发生时直接绑定 goal_id
  → 平台保存真实 EventLedger 和页面前后状态
  → 平台按 VerificationContract 从 callback 事件确定 Goal 状态
  → 平台构建并校验 LocatorEvidence
  → ReplayPlanner 选择必要成功事件，剔除失败和绕路
  → PythonReplayCompiler 确定性生成核心动作代码
  → 静态质量门禁
  → 用户调试和保存
```

MCP 智能体最终文本不再承担完整轨迹、元素 JSON 或 Python 代码。模型或格式失败后，真实事件账本仍可用于单独重试整理和编译，不重新操作网页。

## 3. v3 场景契约

删除基于 CRUD 的 `ScenarioStep.intent`、独立 assertions/cleanup 回指和旧契约兼容，使用通用目标：

```text
GoalPlan
├── schema_version = 3
├── title
├── objective
├── preconditions[]
├── goals[]
│   ├── id                         # G1, G2 ...
│   ├── kind                       # setup | exercise | verify | cleanup
│   ├── objective                  # 自然语言业务目标，不包含定位器
│   ├── completion_criteria        # 可观察完成标准
│   ├── input_refs[]
│   │   ├── name                   # 大写变量名
│   │   ├── source                 # generated | runtime | credential
│   │   └── credential_slot        # username | password，仅 credential 使用
│   ├── verification
│   │   ├── mode                   # visible | contains_ref | not_contains_ref
│   │   └── input_ref              # contains 模式引用的变量
│   ├── side_effect                # none | test_data | external | unknown
│   └── cleanup_for_goal_ids[]
├── forbidden_actions[]
├── credentials_required
├── discovery_notes[]
├── ambiguities[]
└── risk_level
```

规则：

1. 不使用关键词正则决定描述是否包含操作或断言；除空白、纯泛化请求等明显无目标输入外，交给结构化归一化模型理解。
2. `kind` 只表示测试阶段，不代表页面上的按钮或具体业务动作。
3. `side_effect` 只服务于风险、重放和清理控制，不通过按钮文案推断；由用户目标的语义归一化得到。
4. 写入测试数据的目标必须存在 cleanup 目标；清理目标明确引用对应 goal_id。
5. 页面字段、按钮名称、菜单层级和路径属于探索内容，不在探索前要求用户提供。
6. 登录变量统一使用 `UI_TEST_USERNAME` 和 `UI_TEST_PASSWORD`，真实值只在任务内存中传给 MCP，不进入 GoalPlan、轨迹或脚本。
7. 同名变量在全部 Goal 中必须保持相同来源；`runtime` 和 `credential` 变量缺失时执行脚本应立即报错，`generated` 变量可由平台生成唯一值。
8. 每个计划至少包含一个 VerificationContract；断言目标和值来自契约，不由后续模型自由补写。

## 4. 目标分段探索

1. 预检通过后创建一个 MCP 客户端和一个浏览器会话，全部目标在该会话内顺序执行。
2. 每次只向 MCP Agent 提交一个当前 Goal、已完成目标摘要、运行时变量引用、当前路径和剩余总预算。
3. Agent 运行前，Recorder 设置 `active_goal_id`；该轮所有真实 callback 事件在产生时直接写入 `goal_id`，不再事后按文字匹配。
4. 当前目标结束后必须存在至少一个新的页面观察。平台基于 callback 事件、Goal 验证契约和操作前后状态，确定性判断 `completed/blocked/uncertain`。
5. 选中事件和断言事件均由平台从当前 `goal_id` 的成功事件中产生；不存在额外评估模型，也不允许最终文本返回或改写 selector。
6. `uncertain` 可在同一会话内进行一次有界定向补探；`blocked`、登录失败、安全边界、提交结果未知、总预算或总时限到达时停止。
7. 已完成目标不得重做；可能发生写入后 worker 中断时，不自动从头重放。

## 5. v3 事件账本

```text
ExplorationTraceV3
├── schema_version = 3
├── start_path
├── goal_runs[]
│   ├── goal_id
│   ├── status
│   ├── selected_event_ids[]
│   ├── assertion_event_ids[]
│   └── reason
├── events[]
│   ├── event_id
│   ├── sequence
│   ├── goal_id
│   ├── tool_name
│   ├── action                    # navigate/observe/click/fill/select/press/...
│   ├── status
│   ├── relative_path
│   ├── locator_input
│   ├── input_refs[]
│   ├── before_state_id
│   ├── after_state_id
│   ├── result_excerpt
│   └── screenshot_path
├── page_states[]
├── locator_evidence[]
├── cleanup
├── tool_stats
└── warnings[]
```

Recorder 只信任 callback：

- 失败事件作为诊断保存，但不能进入回放计划；
- 填充值转换为变量引用或运行时数据类型，不保存凭据和具体探索数据；
- callback 参数中与运行时值相同的片段转换为 `{{VARIABLE_NAME}}` 模板，回放时再由变量解析器注入；
- 页面状态按指纹去重并有长度上限；
- 所有事件都可回指目标和来源序号；
- 删除 `operation_intent`、`_INTENT_MARKERS`、目标关键词匹配和 legacy event fallback。

## 6. 定位证据

定位器来自真实成功工具调用和相邻页面状态，不由脚本生成模型编造：

```text
LocatorEvidence
├── evidence_id
├── event_id
├── goal_id
├── action
├── relative_path
├── strategy                  # testid/role/label/placeholder/css/text
├── value
├── kwargs
├── validation               # succeeded/acceptable/fragile/rejected
├── validation_reasons[]
└── state_fingerprint
```

通用规则：

1. 已成功执行的原始 selector 是最低事实来源；失败 selector 永不进入脚本。
2. 优先级为 testid、role + accessible name、label、placeholder、稳定属性 CSS、作用域内精确文本。
3. 动态 ID、裸可见元素、无上下文 `nth()`、深层 CSS、组合回退 selector 标记为 fragile/rejected。
4. 若当前 MCP 工具无法单独查询定位器唯一性，不能伪造 `count == 1`；以真实动作成功、相邻页面状态和语法稳定性标记为 acceptable，并在质量报告中明确验证边界。
5. 图标按钮依赖实际可访问名称、稳定属性或作用域定位，不要求代码预先知道“新增”“确定”等文字。

## 7. 回放计划与 Python 编译

`ReplayPlanner` 只消费平台确定性验证所选出的成功 event_id：

1. 保持目标顺序和同一目标内的事件顺序；
2. 去除失败调用、重复读取和没有进入选中路径的探索绕路；
3. 保留到达目标状态所必需的导航、点击、填写、选择和按键；
4. 每个动作必须引用一个 LocatorEvidence；无法溯源时阻断生成；
5. 运行时测试数据使用变量或 `time.time_ns()`，探索数据不进入代码。

`PythonReplayCompiler` 确定性生成：

- `async def run(page, variables)` 入口；
- 相对 URL 导航；
- `page.get_by_*` 或证据支持的 `page.locator(...)`；
- `fill/select_option/click/press/check` 等核心动作；
- 场景说明、步骤注释和 `[Gx/Exxxxxx]` 证据引用；
- 凭据和业务变量读取；
- cleanup 目标对应的 `try/finally`。

场景说明、步骤注释、变量读取和断言表达同样由确定性编译器生成。GoalPlan 归一化之后不再调用 LLM 改写脚本，因此模型不能新增动作、修改 selector、加入失败事件或编造完成状态。

## 8. 状态与失败处理

- Goal 未完成但已有证据：任务进入 `needs_review`，显示缺失目标和最后成功事件，不生成伪完整脚本。
- Goal 验证证据不足：保留轨迹并进入 `needs_review`；只允许从已保存的 v3 轨迹重新整理，不重新探索已完成目标。
- Python 编译或质量检查失败：保留回放计划，可仅重试确定性编译阶段。
- 定位证据 fragile：默认阻断必要业务动作；仅观察性步骤可保留警告草稿。
- 页面不可达或登录失败：停止并返回明确阶段错误。
- 写入结果未知或清理未知：停止自动重放，标记可能残留并要求人工检查。

## 9. 前端展示

生成进度调整为：

1. 理解测试目标；
2. 按目标探索页面；
3. 整理可回放路径；
4. 生成 Python；
5. 检查脚本质量；
6. 整理结果。

页面展示 Goal 级状态、已选事件数量、最后页面、定位器质量、未完成原因和清理状态。用户不再看到 CRUD 词表命中、证据 JSON 格式错误或无法解释的“步骤缺失”。

## 10. 实施阶段

### 阶段一：契约和归一化（已完成）

- 新建 GoalPlan/Goal/TraceV3 契约；
- 删除描述关键词门禁、CRUD intent 和 v2 兼容；
- 更新 normalizer、preflight、repository 和离线 fixtures。

### 阶段二：目标分段探索（已完成）

- Recorder 增加 active_goal_id 和 GoalRun；
- Explorer 在同一会话逐 Goal 运行；
- 增加确定性 Goal 验证和一次有界补探；
- 保留预算、取消、登录、重复操作和风险边界。

### 阶段三：定位证据和回放编译（已完成）

- 从选中成功事件构建 LocatorEvidence；
- 增加 ReplayPlan；
- 确定性生成核心 Python；
- 编译器从验证契约生成可读注释与断言，不再让 LLM 改写代码。

### 阶段四：质量和前端（已完成）

- 重写 Goal/事件/定位器溯源质量门禁；
- 调整进度、轨迹和待检查展示；
- 删除旧字段展示与恢复入口。

### 阶段五：清理和验证（离线验证已完成，真实网站验收待进行）

- 清理开发环境旧生成记录；
- 删除未引用的 v2 代码、测试和文档说明；
- 完成离线回归、前端测试和构建；
- 在三类不同 UI 网站执行真实验收（待用户环境验证）。

当前离线结果：后端 WebUI/AI Core 共 128 项测试通过，前端 23 项单元测试通过，Django 系统检查、迁移漂移检查、Python 编译检查、前端生产构建和 `git diff --check` 均通过。

## 11. 验收标准

1. 中文“确定/保存”、英文按钮和无文本图标按钮均不需要新增后端词表。
2. 每个探索事件在产生时已经绑定 goal_id，不存在按页面文本反向猜步骤。
3. 生成脚本中的每个动作均可追溯到成功 event_id 和 LocatorEvidence。
4. 失败 selector、探索绕路和模型编造定位器不会进入脚本。
5. 同一测试目标换到不同网站时，无需修改平台代码即可生成各自页面证据支持的脚本。
6. 页面语言切换不影响目标覆盖判断。
7. 缺少证据时进入待检查并指出具体 Goal，不生成伪完整脚本。
8. 凭据、Token、Cookie、完整 URL 和探索时具体测试数据不进入轨迹或脚本。
9. 后端 WebUI/AI Core 离线测试、前端单元测试、生产构建和 `git diff --check` 通过。

## 12. 回退

代码回退到实施前提交；开发环境直接清理 v3 生成记录，不提供 v2/v3 双读。探索期间已经写入目标网站的测试数据不会随代码回退撤销，仍按任务清理状态人工核对。
