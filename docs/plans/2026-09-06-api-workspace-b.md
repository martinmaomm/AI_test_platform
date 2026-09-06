# API 自动化 B 方案实施与验收

用户已批准：统一对话工作区，requests 执行；Python 只读查看、复制、导出，不做反向编辑。保留接口文档、分类、用例、顺序套件、计划任务及平台报告。UI 自动化不改动。

## 唯一用例契约

结构化 JSON 为唯一数据源，继续采用可视化编辑器的 `config` / `teststeps` 容器，但不再依赖 HttpRunner 执行：

```json
{"version":1,"config":{"name":"登录后查询","base_url":"https://example.test","variables":{},"verify":true},"teststeps":[{"name":"登录","endpoint_id":1,"request":{"method":"POST","url":"/login","json":{"username":"${username}"}},"extract":{"token":"body.data.token"},"validate":[{"eq":["status_code",200]}]}]}
```

变量完整占位保留原类型，插入字符串时转字符串；支持 `${name}`、`$name`、`{{name}}`。仅允许显式支持的值函数，不执行任意表达式。JSON、表单、原始文本必须区别处理。独立用例使用独立 Session；同用例步骤共用 Cookie 和提取变量；失败后后续步骤记录 skipped，不能消失。套件按顺序执行，用例变量 < 套件变量 < 本次覆盖。冻结执行输入，不能在排队后改用例影响当前任务。

Python 导出携带同一 requests 运行核心，无仓库依赖，可以 `python test_api.py` 或 `pytest test_api.py` 执行。展示明确场景与各步骤，不支持双向同步。

## 工作区接口约定

前缀 `/api/v1/projects/{project_id}/api-testing/workspaces/`。所有对象同时约束项目和当前用户权限。

- `GET /` 会话列表；`POST /` 新建：`title?, case_id?, model_id?, endpoint_ids?`。
- `GET /{id}/` 详情；`PATCH /{id}/` 更新 `draft`、`model_id`、`endpoint_ids`，携带 `revision` 防止覆盖。
- `POST /{id}/messages/`：`message, revision, mode=generate|repair`，异步生成候选；repair 使用当前 revision 的失败调试证据，不自动重发请求。
- `POST /{id}/debug/`：`revision, environment_id?, variables?`；显式发起真实请求，异步执行；先保存本地编辑再执行。
- `POST /{id}/save/`：`revision, title?, description?`，首次新建，后续只更新本会话绑定用例；原用例已被别人修改需冲突提示。
- `GET /{id}/python/`：`{code, filename, revision}`，提供只读展示/复制/下载。
- 详情响应：`id,title,model_id,endpoint_ids,draft,revision,status,error,messages,candidate?,debug_result,debug_revision,saved_case_id,task_id,updated_at`。
- `status`: `idle|generating|debugging|ready|failed`；轮询详情为事实来源，不单靠 WebSocket。任何草稿修改使旧验证过期。
- 生成返回候选及摘要，用户确认采用候选再更新草稿；修复不可暗中删除/放宽断言。候选含风险说明，确认后才替换当前草稿；保存始终显式。

## 实施分工

1. requests 规范化、运行、导出及离线测试。
2. 持久会话/草稿/修订、流式 LLM 聚合、多轮生成与修复、后台任务和权限测试。
3. 工作区页面、可视化步骤、Python 面板、调试结果及入口整合。
4. Swagger 参数继承、类型、媒体类型、引用组合修正。
5. 主 Agent 集成原执行/套件/计划任务路径，冻结快照，审查和回归。

## 验收与上线边界

- 离线假模型、假 HTTP、临时 SQLite，禁止使用现有 NAS/Redis/真实模型和被测系统。
- 检查类型、提取失败、响应正文、JSON/表单、Cookie 隔离、失败跳过、导出语法与行为一致性。
- 检查会话隔离、并发修订冲突、异步错误/终态、未保存候选不覆盖、修复不伪造成功。
- 前端单测、构建、浏览器本地模拟验证；数据库迁移检查。
- 不执行数据库删除，不提交资源 ZIP；完成后中文提交，告知迁移及重启后端/Celery。
- 回退：部署前备份数据库，代码可回到变更前提交；新增工作区表不破坏现有数据，降级前停止本次新增任务。

## 首版范围及操作说明

- Swagger/OpenAPI 使用单文件文档，本地 `$ref` 支持；外部 URL/文件 `$ref` 明确拒绝，上传前请合并规范文件。仅保留显式示例，不把合成示例当作业务断言依据。
- 请求支持 JSON、表单字段、原始文本，不执行旧引擎的任意 Python 钩子；文件上传等未声明的请求字段会明确报错，不忽略。
- 候选采用使用 `PATCH draft + revision`，无第二套候选保存接口。新单步骤且绑定端点的用例进入端点列表，多步骤进入场景列表。
- 生成/修复不会自动调试，也不会自动保存；验证结果只对对应修订有效。修复保留原步骤断言，不能靠删断言变绿。
- requests 本地子进程硬超时后，没有完整步骤证据时只记录执行异常，不伪造“首步失败、其余跳过”；远端写请求是否完成可能未知。
- 工作区及执行快照迁移为 `api_testing.0015`、`0016`，均需迁移后重启后端/Celery。未执行用户数据库迁移、服务重启或真实模型测试。

离线验收命令：

```bash
PYTHONPATH=backend/apps backend/.venv/bin/python -m pytest -q backend/apps/api_testing/test_requests_runtime.py
backend/.venv/bin/python backend/scripts/test_webui_generation_offline.py api_testing ai_core.tests common scheduled_tasks notifications web_testing
backend/.venv/bin/python backend/scripts/test_api_workspace_migration_offline.py
cd frontend
npm run test:unit
npm run build
cd ..
backend/.venv/bin/python backend/scripts/test_api_workspace_browser.py
```

浏览器验收使用真实前端和 Django HTTP 接口、临时 SQLite、模拟模型/HTTP；包括候选、修复、验证、保存、复制、下载和原生 API 报告。它不证明真实模型能一次生成正确脚本。

本轮已验证：requests 核心 17 项、后端离线回归 516 项、前端单测 160 项、前端生产构建、临时 SQLite 实际迁移，以及上述浏览器完整流程。额外覆盖局域网 HTTP 无 Clipboard API 时的复制兜底、候选采用后旧 Python 缓存失效、同名用例执行结果隔离。构建仍有现存大资源包提示，不影响本次构建成功；未验证真实 MariaDB 迁移和真实模型生成质量。
