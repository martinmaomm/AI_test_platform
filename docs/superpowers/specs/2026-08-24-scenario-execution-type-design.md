# 场景测试执行类型设计

## 背景

场景测试用例页面和端点测试用例页面都调用 `ExecuteAPITestCaseView`。该视图创建 `APITestExecution` 时将 `exec_type` 写死为 `case`，导致场景执行记录在执行记录页面显示为“单用例”，并且无法按场景类型筛选。

## 目标

让执行记录准确区分三种 API 执行类型：

- `case`：单接口/单用例执行；
- `scenario`：场景测试用例执行；
- `suite`：测试套件执行。

## 方案

### 后端

1. 在 `APITestExecution.EXECUTION_TYPE_CHOICES` 中增加 `scenario`。
2. `ExecuteAPITestCaseView` 根据 `APITestCase.test_case_type` 选择 `case` 或 `scenario`。
3. 场景执行仍复用 `APITestCaseExecutionDetail` 和现有 Celery 执行任务，因为场景本质上是一个包含多个 HttpRunner 步骤的测试用例，不需要新增详情表。
4. 执行记录列表允许按 `case`、`scenario`、`suite` 过滤；详情和删除接口把 `scenario` 与 `case` 视为同一类“用例明细”处理。

### 前端

1. API 执行记录筛选器增加“场景”。
2. 执行类型显示使用统一映射：单用例、场景、套件，避免未知类型被错误显示为套件。
3. `case` 和 `scenario` 都打开 `APITestCaseExecutionDetail`；`suite` 继续打开套件详情。
4. 通过执行记录列表打开详情时保留后端返回的 `exec_type`，避免 URL 直达场景记录时被前端强制标成 `case`。

## 数据与兼容性

- 现有历史 `case`、`suite` 记录不修改。
- `exec_type` 字段当前最大长度为 10，`scenario` 长度为 8，无需扩充数据库字段。
- 新增 choices 只影响应用层显示与校验；实现后执行 `makemigrations --check` 确认是否需要迁移文件。

## 验收标准

- 从场景测试用例页面执行后，执行记录显示为“场景”。
- 从端点测试用例页面执行后，仍显示为“单用例”。
- 筛选器可以分别筛选单用例、场景和套件。
- 场景记录可以正常打开执行详情、删除，并能继续显示多步骤 HttpRunner 结果。
- 后端系统检查、迁移检查和前端生产构建通过。
