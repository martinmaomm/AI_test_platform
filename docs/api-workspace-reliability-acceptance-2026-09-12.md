# API 工作区可靠性验收（2026-09-12）

## 本次交付边界

保留接口文档、网页采集两个来源；复用现有 API 对话工作区、requests JSON 契约、平台执行记录和 Python 导出。业务请求、子场景与套件仍串行执行，不修改 UI 自动化生成流程。

- 生成验证、人工调试分别保存标准执行记录，不因重新生成覆盖历史；目标地址取执行快照。
- 每步发送前及完成后保存检查点，取消、硬超时保留完成结果及状态未知的在途请求。证据保存失败时停止继续发送请求。
- 受保护断言变动保留候选并要求确认；确认与候选版本/hash 绑定。未采用的 AI 提议不冒充用户断言；仅 HTTP 状态码校验会提示业务验证不足。
- 支持显式清理阶段，要求清理定位变量来自本轮成功提取；失败后有请求副作用则停止自动重放。不自动判断对象的业务归属，不承诺断电、硬超时或取消后一定能清理。
- 排队、单场景、整批分别计时；模型单次请求同时受其传输配置和业务预算约束。未知原生 provider 不强塞 OpenAI 参数；取消不能撤回已发送到模型或网站的请求。
- 当前场景和依赖的完整定义进入模型上下文，其余接口保留轻量目录；按需补全仅使用冻结范围内的定义。完整响应留在平台记录，模型收到的是有界失败摘要。
- 网页来源选择后重算依赖，已选来源禁止旧页面覆盖；直接点击和新窗口首请求增加来源检查。

## 验证方式

数据库测试使用临时 SQLite，不在 NAS 上建测试库；模型和业务 HTTP 使用替身或回环服务器。浏览器使用真实 Chrome 与临时 Django/Vue 服务。只读验证了当前 MariaDB 的运行历史查询及 Celery 空闲状态。

| 验证项 | 结果 |
| --- | --- |
| API 后端完整回归 | 169 项通过，包含取消后候选版本、执行快照、历史与来源交接 |
| requests 执行器与检查点 | 44 项、8 个子测试通过，包含取消、硬超时及进度字段 |
| 原有 UI/通知/计划任务隔离回归 | 621 项通过 |
| 前端单元测试 | 264 项通过 |
| 前端构建 | 通过，保留原有大 chunk 警告 |
| 浏览器采集 | 11 项通过，包含真实 Chrome |
| 工作区原流程 | 生成、采用、调试、修复、保存、导出与报告通过 |
| 新增交互 | 真实 Chrome 验收通过：工作区/场景历史切换、报告打开、停止整批、保留未验证候选、断言变更确认后采用 |
| 当前 MariaDB 只读检查 | 历史 JSON 注解查询成功；当前没有新格式工作区执行记录，已有数据展示由隔离浏览器夹具覆盖 |
| 数据结构与代码检查 | `makemigrations --check` 无新增迁移；`git diff --check` 通过 |

本机浏览器证据位于 `backend/logs/api-workspace-browser-check/`，日志与截图不提交 Git。

复跑入口（项目根目录）：

```bash
backend/.venv/bin/python backend/scripts/test_webui_generation_offline.py api_testing
backend/.venv/bin/python backend/scripts/test_webui_generation_offline.py
PYTHONPATH=backend/apps backend/.venv/bin/python -m pytest -q backend/apps/api_testing/test_requests_runtime.py backend/apps/api_testing/test_requests_checkpoints.py -p no:cacheprovider
backend/.venv/bin/python backend/scripts/test_api_workspace_browser.py
backend/.venv/bin/python backend/scripts/test_api_workspace_reliability_browser.py
```

前端在 `frontend/` 执行 `npm run test:unit` 和 `npm run build`。采集回归入口为 `node --test backend/scripts/tests/test_playwright_mcp_capture.mjs`，完整 11 项验收需要 Chrome 及已安装的 `playwright-mcp-server` 可被当前 PATH 找到；缺少浏览器条件时真实浏览器测试会跳过，不能当作完整验收。

## 已知限制

- Chromium 的服务端重定向链并非每一跳都经过 Playwright route。不能把网页采集来源确认当作完整网络安全沙箱；仅用于可信测试站点。详见 [重定向边界](TODO-OPT.md)。本次未保留会改变内网网络语义的响应代取方案，也未关闭浏览器安全策略。
- 执行器父进程的 pipe selector 当前验收平台为 macOS/Linux，不承诺 Windows 支持。
- 未用真实付费模型重新生成业务场景，未改变 NAS 测试站数据；模型对具体 Swagger/页面的生成质量由用户网页复验。
- 不做旧在途任务兼容。缺少新冻结预算的旧任务需重新发起；历史已完成执行记录保留。

## 部署

本次没有新增数据库结构迁移。保留本地 `.env` 不覆盖，新增预算变量未填写时采用默认值。代码提交后，在没有在途任务时重启后端与 Celery，前端开发服务通过热更新加载；生产静态部署需重新构建。
