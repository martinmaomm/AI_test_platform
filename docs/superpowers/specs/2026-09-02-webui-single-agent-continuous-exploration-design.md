# WebUI 单智能体连续探索与最终路径定稿设计

## 状态与范围

- 状态：已实施，待真实页面验收
- 日期：2026-09-02
- 范围：WebUI 生成任务的连续探索、callback 轨迹、最终路径定稿、确定性 Python 编译、工作区保存门禁
- 非范围：真实数据库记录、真实网页、运行中的 Celery 任务、网站专属字段或按钮词表
- 数据策略：保持 `schema_version=4`；移除旧 checkpoint 字段后由 `extra=forbid` 拒绝旧 v4 payload，不提供双读或兼容 shim，也无需迁移

## 已确认缺陷

真实浏览器 callback 会被平台完整记录，但旧的“动作前 checkpoint 仅绑定下一次 callback”契约使未标记的成功 navigate、登录、点击和断言全部只能作为诊断。一次漏标就会把完整探索压缩为残缺脚本，根因不是页面未打开。

## 目标流程

```text
ScenarioPlan
  → 一个 MCP Client、一个浏览器会话、一个 MCPAgent、一次 agent.run
  → 自动记录所有 Playwright callback（成功、失败、绕路均保留）
  → 同一 Agent 读取安全候选摘要
  → 同一 Agent 一次提交最终路径定稿
  → 本地确定性验证
  → 生成 Python 或保留轨迹并阻断草稿
```

不新增第二个模型、Agent、浏览器会话或 LLM 请求。模型最终文本不承担 JSON、定位器或脚本契约。

## 单 Agent 约束

1. 每个任务只创建一个 MCP Client、一个浏览器会话、一个 `MCPAgent`，并只调用一次 `agent.run`。
2. Agent 连续完成登录、导航、操作、验证和清理；中间不自动回到 `start_path`。
3. 平台为同一个 Agent 注册两个本地 `StructuredTool`：`aits_get_path_candidates` 与 `aits_finalize_path`。输入由 Pydantic 模型验证，不解析脆弱 JSON 文本。
4. `aits_get_path_candidates` 只返回安全字段：事件 ID、顺序、动作、相对路径、安全 locator/input refs、安全观察摘要、可编译标记和 unmapped fill/select 标记，以及本次摘要绑定的最新 callback sequence。凭据、完整 URL、运行时值不返回。
5. Agent 接近结束时先读候选，再一次提交有序主动作、每个 `assertion_id` 的观察事件、可选清理动作和短中文步骤名。定稿只能使用已读取且 sequence 未过期的候选摘要；首次成功 navigate 不由 Agent 选择，平台自动加入为可信入口。
6. 定稿成功后任一 Playwright callback 都使定稿变为 `FINALIZATION_STALE`，同时使旧候选摘要过期；必须再次读取候选并重新定稿。

## 轨迹与定稿契约

`ContinuousExplorationTrace` 保留全部 callback 事件、页面状态、定位器证据、`replay_event_ids`、断言和清理展示字段。事件不再有 `phase`、`intent`、`checkpoint_id` 或 checkpoint 列表。

`finalization` 是唯一可选择回放事件的来源：

- `status`: `missing`、`valid` 或 `invalid`；
- `entry_event_id`: 首个成功 navigate；
- `main_actions`: 有序的成功交互动作与安全步骤名；
- `assertions`: 每个 ScenarioPlan assertion requirement 对应的观察事件；
- `cleanup_actions`: 有序的成功清理交互动作；
- 失效时记录安全 `error_code` 与失效 callback，不产生可回放选择。

失败、拦截、重复探索和不稳定定位器永远保留诊断，但不能进入脚本。

## 本地确定性验证

定稿工具拒绝并给 Agent 可行动错误码，如果出现以下任一情况：

- 没有成功入口 navigate，未知 ID、重复 ID，或事件顺序不递增；
- 动作不成功、不是可回放交互、定位器缺失/fragile/不可编译；
- `fill` 或 `select` 未精确映射到 `runtime_input_values` 中 ScenarioPlan 已声明的 input ref；
- 未先读取候选、候选摘要已被后续 callback 淘汰，或 `press` 缺少非空 key；
- 主路径未覆盖 ScenarioPlan 的所有 `generated` / `credential` input ref（清理路径不能代替登录或建数输入）；每个引用 input ref 的 assertion 前没有选中的 `fill` / `select` 动作提供该 ref；
- assertion 未覆盖每个 requirement，未指向带 selector 的成功 observation，或观察摘要不满足 visible/contains/not_contains；
- cleanup 缺失、主路径之后顺序错误，或 cleanup assertion 未在最后一个 cleanup action 后验证；
- 定稿中试图手工选择入口 navigate。

定位器只来自真实成功 callback，后处理不生成或修改 selector。正常约束继续禁止凭据落盘、高风险操作、固定等待和网站业务词表。

## 编译与保存行为

1. 有效定稿的入口事件在脚本中始终编译为 `run()` 的首个浏览器动作：`await page.goto(start_path)`。执行器继续注入 `base_url`，脚本只保留相对路径。
2. 步骤注释使用定稿的安全中文名称并包含 `event_id` 溯源；断言注释使用对应 success criterion。
3. 有效定稿但只有非阻断警告时，仍可进入正常工作区流程。
4. 定稿缺失/无效、入口缺失、断言不全时，保留探索轨迹、诊断和 `error_code`，但 `script_draft` 必须为空；前端不展示可编辑草稿，前后端保存入口均拒绝。
5. 若清理要求存在，脚本以真实 cleanup action 和其后的语义验证生成 `finally`，不使用占位 `pass`。

## 验收标准

1. 无 checkpoint callback；完整最终路径可一次定稿。
2. 首次成功 navigate 自动进入 replay，并成为 `page.goto('/')` 的第一个浏览器动作。
3. 未知、失败、fragile、unmapped fill/select、重复、乱序事件被拒绝。
4. 定稿前必须读取最新候选摘要；后续 callback 后旧摘要不能再次定稿。
5. 每条 requirement 都绑定真实 selector observation，且语义满足；`generated` / `credential` 输入和 assertion 输入依赖不得遗漏。
6. 缺失/无效定稿不产草稿、不允许保存，轨迹和明确错误仍保留。
7. 定稿后新增 callback 自动失效。
8. 同任务维持 one client / one agent / one run，无第二模型调用。
9. 凭据、完整 URL、运行时敏感值不进入候选摘要、轨迹或脚本。
10. cleanup 仅在真实成功动作且后续语义验证时完成。
11. 后端离线全套、前端单测与构建、`git diff --check` 通过。

## 回退

代码回退可使用正常 Git 提交流程；开发阶段旧生成记录不保证可读取，真实目标站已写入的测试数据不随代码回退自动清理，需按任务清理证据核对。
