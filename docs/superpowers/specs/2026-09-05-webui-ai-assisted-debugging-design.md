# WebUI AI 辅助调试设计

## 1. 目标

在 AI 生成的 Python Playwright 草稿真实调试失败后，提供一个用户主动触发的“AI 分析并修复”闭环：平台读取本次失败证据，必要时使用 Playwright MCP 定向检查页面，生成独立候选脚本，自动执行候选验证，并让用户比较后决定是否采用。

本功能不改变首次脚本生成流程，不覆盖用户当前草稿，不扩展到测试套件，也不把正常脚本执行改成逐步由 AI 控制。

## 2. 用户流程

1. 用户真实调试当前草稿。
2. 调试状态为 `failed` 或 `error` 且具有失败诊断时，显示“AI 分析并修复”。
3. 用户确认后提交当前 revision，以及变量表中填写的“本次调试覆盖值”。这次确认授权修复任务进行必要的页面检查和候选验证。
4. 后端依次执行：收集失败证据 → AI 分析/必要的定向 MCP 检查 → 生成候选 → 静态检查 → 在新浏览器中顺序验证。
5. 首个候选未通过时，可将新失败证据反馈给 AI 再尝试一次；单次用户请求最多两轮，禁止并发运行。
6. 页面展示失败原因摘要、AI 处理状态、候选代码差异、每轮实际执行结果及截图。
7. 原草稿始终保留。用户点击“采用此版本”后，才以事务方式将候选写入工作区的新 revision；未通过候选也允许采用，但验证状态重置为未验证。通过候选采用后可继承该候选实际执行的验证证据。
8. 用户也可以“放弃候选并继续编辑原草稿”；该操作仅清除候选状态，不改变原草稿和 revision。

## 3. API 契约

### 发起辅助调试

`POST /api/v1/projects/{project_id}/web-testing/script-generations/{generation_id}/repair/`

```json
{
  "expected_revision": 3,
  "confirm_execution": true,
  "runtime_variables": [
    {"name": "UI_TEST_USERNAME", "value": "test"}
  ]
}
```

- `confirm_execution` 必须为 `true`。
- `runtime_variables` 沿用调试接口校验，仅作为 Redis/进程内临时输入，不写入数据库、Celery 参数、workspace 或 API 响应。它仍遵循项目现有的测试环境明文策略：可能发送给模型服务商、MCP 和目标网站，也可能出现在开发日志或截图中，因此只允许使用测试账号。
- 仅允许失败或异常的当前 revision 发起；生成、调试或修复进行中返回 `409`。

### 采用候选

`POST /api/v1/projects/{project_id}/web-testing/script-generations/{generation_id}/repair/apply/`

```json
{
  "expected_revision": 3,
  "candidate_hash": "sha256..."
}
```

- revision、原草稿 hash、候选 hash 必须同时匹配，否则返回 `409`。
- 采用操作不调用模型、不启动浏览器。

### 放弃候选

`POST /api/v1/projects/{project_id}/web-testing/script-generations/{generation_id}/repair/discard/`

```json
{
  "expected_revision": 3,
  "candidate_hash": "sha256..."
}
```

- 与采用候选使用相同的 revision、原草稿 hash 和候选 hash 并发校验。
- 成功后只把 `workspace.repair` 重置为 `idle`，原草稿、revision 和原调试结果均不改变。

## 4. Workspace 状态

继续使用 `workspace.repair`，不新增数据库列或迁移。状态为：

- `idle`：无修复任务；
- `pending` / `running`：等待或正在分析、探索、生成、验证；
- `candidate_ready`：候选已生成但实际验证未通过或未完成；
- `candidate_passed`：候选已通过实际验证；
- `failed`：没有可审核候选或任务异常。

关键字段：

- `phase`：`collecting`、`analyzing`、`exploring`、`validating`、`completed`；
- `source_revision`、`script_hash`、`candidate_hash`；
- `candidate_script`、`candidate_diff`、`candidate_quality_report`；
- `summary`、`message`、`blockers`；
- `attempt_count`、`attempts`（最多两项）；
- 每轮记录候选 hash、是否进行 MCP 检查、静态检查结果、execution id/status、用户可读失败摘要和截图是否存在，不保存运行变量值。

序列化时延续现有 workspace 清洗规则，只返回受控字段。

## 5. 失败证据与 AI 输入

输入包含：场景描述、目标网址、当前完整脚本、失败 execution id、友好失败摘要、受长度限制的原始 stdout/stderr/log、原始生成探索证据以及上一轮候选的执行诊断。

若定向页面检查需要登录，任务可在本次用户明确授权内把临时运行变量提供给模型/MCP 使用；这意味着相关值会发送给当前选择的模型服务商和目标网站。平台不得把值写入候选脚本、workspace 或 API 响应；日志和截图继续遵循现有测试环境明文策略，页面必须明确提示只使用测试账号。

对于 `action_timeout`、`strict_mode`、`assertion_failure` 等页面相关失败，允许 Playwright MCP 定向检查失败页面和相关定位器；对于语法、导入、浏览器关闭、网络不可达等确定性错误，不盲目探索页面。截图主要提供给用户；首版不要求语言模型具备视觉能力。

AI 必须返回完整 Python 脚本，并遵守既有脚本契约：完整目标 URL、唯一 `async def run(page)` 入口、中文步骤/断言注释、不得删除断言来规避失败、不得扩大原测试目标。

## 6. 候选验证

- 使用与真实调试相同的 Python Playwright 执行器、变量优先级、Chromium、日志和结束截图。
- 每轮新建独立的 `WebUITestExecution` 与详情记录，触发类型为 `llm`，以便复用现有执行详情和截图 API。
- 只有操作成功、断言状态完整且本次确实执行过真实断言，才能标记 `candidate_passed`。
- 页面/元素错误可以进入第二轮；网络不可达、账号错误、环境依赖、任务取消等非代码问题提前终止，防止 AI 为了通过而误改脚本。

## 7. 前端

- 调试失败详情附近显示“AI 分析并修复”；调试通过、验证未完成、没有诊断、草稿有本地未保存修改或任务繁忙时不允许发起。
- 发起前弹窗说明会访问目标网站并实际运行候选，最多两轮。
- 过程展示当前阶段和轮次。
- 完成后展示摘要、每轮状态及“查看执行详情/截图”、代码差异、候选脚本。
- “采用此版本”需要二次确认；采用后同步新 revision。候选失败时明确提示采用后仍需继续修改和调试。
- “放弃候选并继续编辑原草稿”需要二次确认；成功后恢复原草稿的编辑、调试和保存操作。
- 同一时刻禁用保存草稿、真实调试、再次生成修复和保存到用例，避免并发覆盖。

## 8. 边界与验收

- 不修改原生成流程、套件流程和普通已保存用例的执行页面。
- 不新增旧数据兼容逻辑；历史记录缺少新字段时按无候选展示。
- 自动修复不得超过两轮；任务异常必须进入终态并保留原草稿。
- revision 或 hash 冲突不能覆盖草稿。
- 放弃候选不得改变原草稿和 revision。
- 变量值不作为配置进入数据库、Celery 参数、workspace 或 API 响应；开发日志和截图按测试环境明文策略处理。
- 后端离线测试覆盖状态机、并发/过期任务、变量临时性、候选运行和采用语义；前端测试覆盖按钮门槛、轮询、候选展示、采用及失败保留。
