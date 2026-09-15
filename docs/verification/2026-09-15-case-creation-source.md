# AI 生成用例占比：固定初始来源

## 统计定义

当前项目中，初次保存时属于 AI 生成的用例数 / 当前已保存用例总数 × 100%，保留两位小数；没有用例时为 0%。

- 同时适用于 UI 和 API。来源存入用例自身的 `creation_source`，不再临时从工作区或当前脚本来源推断。
- 新建来源分为 `ai`（AI 生成）和 `manual`（手工创建）。保存以后手动编辑、AI 修复、重复运行、覆盖保存或删除关联工作区，都不改变初始来源。
- 手工创建的用例之后只经过 AI 修复，仍算手工创建；AI 生成的用例后续由人修改，仍算 AI 生成。
- 只统计已保存、尚未删除且属于当前项目的用例。未保存草稿、生成次数、运行次数不计入；不限定“今天”或“近七天”。
- 历史来源确实无法证明时为 `unknown`。它计入总用例数，但不臆测为 AI；因此历史项目的比例可能小于实际曾经由 AI 生成的比例。
- API 场景/端点类型、模型被选中、调试通过都不等于 AI 生成。UI 当前 `script_source` 继续用于脚本管理，不承担初始来源统计。

## 数据保护及验收范围

来源迁移只增加来源字段并在有明确证据时回填，不修改脚本、变量、执行记录、工作区正文或修改时间。不能为了让占比变高而把所有旧用例都标成 AI。

验收覆盖新建来源、不可由请求任意篡改、手工编辑与 AI 修复后保留、工作区删除后保留、未知来源分母、两类项目统计一致，以及旧数据迁移前后原有字段不变。使用临时 SQLite 和回环网络验证创建/编辑逻辑，不调用真实模型、邮件或被测网站。

## 验证结果

- 来源创建/保存/修改/修复与首页统计：20 项隔离测试通过；模型和迁移一致性检查 `No changes detected`。
- 真实迁移应用：临时 SQLite 上执行 API `0021_apitestcase_creation_source`、UI `0023_webuitestcase_creation_source` 的全部 AddField / RunPython / AlterField 操作通过；覆盖手工用例后续被 AI 覆盖不能认作初始 AI 的时间反例，以及脚本、变量、当前来源和修改时间不变。
- 真实 Vue + Django + Chrome 隔离浏览器验收通过，包含 AI 占比与新说明、项目切换、角色入口、错误重试和窄屏布局。前端 314 项单测通过，构建通过（已有大 chunk 提示不变）。
- 真实数据库仅应用上述两条来源迁移：15 条 API 用例、4 条 UI 用例和 33 条工作区所有原有字段逐行散列与迁移前一致，包含脚本、变量、修改时间；未修改测试网站或调用真实模型/邮件。
- 迁移后来源快照：API 10 条 AI、5 条未知，共 15 条，占比 66.67%；UI 4 条 AI，共 4 条，占比 100%。其中曾经手改而变成当前 `script_source=manual` 的 UI 用例，通过保留的创建关联恢复了初始 AI 来源。数字随后续新建/删除用例自然变化。
- 确认任务空闲后平滑重启后端与 Celery；Beat、前端未重启。后台两个项目的 summary / trend / top-failures 接口均 200，Celery ping 返回 pong，两条迁移已记为完成。
- 扩展邻接回归不是全绿：147 项在修改前 `e68b168` 与修改后均为 45 失败、1 错误，失败测试 ID 与错误完全相同，没有新增回归；旧 API 权限测试夹具问题已记入 `docs/TODO-OPT.md`，未通过放宽权限来绕过。

复现隔离验证（在 `backend` 目录）：

```bash
.venv/bin/python scripts/test_webui_generation_offline.py \
  api_testing.test_creation_source web_testing.test_creation_source projects.test_dashboard
.venv/bin/python scripts/test_case_creation_source_migration_offline.py
.venv/bin/python scripts/test_dashboard_browser.py
```

其他部署环境需执行这两条迁移后重启服务；不要将来源字段删除重建来修正占比，这会丢失之后累积的真实初始来源。
