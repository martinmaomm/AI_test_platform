# 第二批清理：旧 APP 自动化退役

## 清理范围

- 删除 6 个旧 APP 页面及 MidScene 前端 API 模块；移除路由、侧栏、标签标题、报告类型映射和 Appium 环境表单。
- 删除 MidScene 生成智能体、3 个 HTTP 入口、Celery 任务及取消分支、WebSocket 路由与注册。
- 删除随 MidScene 退役后已无调用者的任务 WebSocket 包装器及内部辅助函数；保留 UI/API 共用任务进度、状态查询与流式通道。
- 项目创建仅支持 API、Web、性能类型；环境配置仅支持 API、Web。旧 `app` 类型请求被拒绝，不再把无效类型过滤悄悄当作查询所有项目。
- 不删除业务数据、浏览器资源、日志或 `.env`；保留历史迁移与 `MidSceneScript` 表映射。本机数据库核对为 0 个 APP 项目、0 个 APP 环境、0 条 MidScene 脚本。

## 数据库与运行验证

- `projects.0006_retire_app_choices` 仅更改两个字段的 choices。MariaDB `sqlmigrate` 输出两处 no-op，无数据清理或表结构删除；已应用。
- `manage.py check`、`makemigrations --check --dry-run`、`git diff --check` 通过。
- 迁移时仍会显示已有 RAG 条件唯一约束不受 MariaDB 支持的警告，本次没有更改 RAG 配置或约束。
- 在确认 Celery active / reserved / scheduled 均为空后，温和停止并重启本项目后端和 Worker。
- 运行中的 Worker 返回 pong，注册 20 个任务，不含 MidScene；UI/API 单用例、套件执行与取消任务仍在注册。
- 实际 HTTP：后端管理员登录页与前端登录页返回 200；项目和 UI 生成接口匿名请求返回预期 401；3 个旧 MidScene 地址返回 404。

## 自动化回归

- 前端 `npm run test:unit`：327 项通过；`npm run build` 通过，保留既有大 chunk 提示。
- 后端通过 `scripts/test_webui_generation_offline.py` 在临时 SQLite、内存缓存/队列并封禁网络的环境验证，不使用业务库，也不调用真实模型或发送邮件。
- 项目、环境、权限、首页与调度环境契约：39 项通过。
- MidScene 退役、流式调用、Python 执行器、脚本契约、脚本助手核心、requests、API 解析、调度可靠性、平台报告和通知：136 项通过。
- 补充退役后的 UI 取消、生成记录删除与公开报告回归：24 项通过（其中 3 项与前一组重复）。
- 共享任务包装器新增 4 项回归通过：成功、失败、异常及退役导出边界。
- 最终在合并后的代码上一次性运行上述测试（去重后共 200 项），全部通过。

## 全量回归的已知边界

| 版本 | web_testing 测试数 | failures | errors |
| --- | ---: | ---: | ---: |
| 修改前 `fbc2420` 独立临时源码快照 | 541 | 51 | 3 |
| 清理后（含新增退役与取消测试） | 545 | 51 | 3 |

逐项排序对比 `FAIL:` / `ERROR:` 测试 ID，集合完全一致，没有新增失败；不能把全套旧测试描述为全绿。主要涉及旧权限夹具、报告与修复响应断言，详见 [技术债清单](../TODO-OPT.md)。

这次验收针对退役边界与保留链路，不代表重新执行了真实模型探索或真实网站端到端测试。后续若要删历史存储表，需要单独确认目标库与影响。
