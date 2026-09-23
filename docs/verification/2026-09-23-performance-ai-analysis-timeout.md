# 压测 AI 分析超时与进度修订

## 问题和改动

首次真实分析在领取任务约 180.07 秒后记录 `analysis_timeout`；排队约 0.07 秒，调用日志在约第 166 秒显示新一次模型请求。HTTP 200 不能证明正文完成，旧日志没有记录首次异常的具体类别，不能据此断定提供商根因。

新任务的 `PERFORMANCE_ANALYSIS_TIMEOUT_SECONDS` 默认 600 秒，允许 60–1800 秒，非法值回退默认；创建时快照，排队上限仍为 300 秒。迁移 0008 为旧分析补 180 秒和空进度，新任务默认 600 秒，旧失败记录和时间不改写。

总时限与单次模型调用时限分别展示。当前托管 backend/celery 环境的 `LLM_TIMEOUT_SECONDS` 为 300 秒，这一全局模型配置没有改变；模型调用仍取模型时限和任务剩余时间的较小值，重试不重置任务时间。页面显示阶段、已执行时间、正文字符数、尝试次数和最近重试原因，非正文片段不冒充正文，也不展示推测的百分比。

受控子进程执行模型流。父进程通过非阻塞管道持续检查截止、权限和状态，到期终止并回收；子进程在 Django 初始化前启动 watchdog，独立检查截止与父进程存活，避免模型阻塞或父进程被强杀后留下孤儿。Celery solo 仍串行等待，本次不增加后台并发。

阶段、尝试、单次调用预算和重试即时落库，普通计数及权限检查按两秒节流，提交结果前强制检查。失败保留阶段和安全原因；进度和日志不记录提示词、正文、推理文本、URL、凭据或原始异常。

## 验证

- SQLite 隔离后端回归：性能模块及项目知识模块共 **250 项通过**，包含已有流式适配器回归；外部网络、实际数据库、Redis 和模型服务均禁用。
- 虚拟时钟验证 600 秒任务在原 180 秒之后仍可生成正文；重试共用剩余预算、部分正文后不重放、空片段不计入正文、安全进度和终态保护通过。
- 真正启动本地测试子进程，验证无输出阻塞、未读 stdin、权限撤销、畸形协议、输出超限时截止及回收；验证父进程 SIGKILL 后子进程自行退出。进程专项 8 项通过。
- 模型调用替身配合真实子进程 Django 初始化验证通过；没有真实模型调用。
- `makemigrations --dry-run --check` 无模型漂移；`git diff --check` 通过。
- 前端分析状态 8 项、性能工作区 23 项、多节点渲染 1 项通过；生产构建通过，保留既有资源体积提示。
- 实际 Chrome 配合明确标注的分析 API 替身，验证 600/300 秒展示、等待正文、重试原因、正文计数、历史 180 秒/无进度展示、刷新与历史切换、网络不确定时复用原请求。无页面异常，无真实写入或外部请求。

复验：

```bash
cd backend
.venv/bin/python scripts/test_webui_generation_offline.py performance_testing project_knowledge
cd ../frontend
node --test tests/performanceAnalysisState.test.js
node --test tests/performanceWorkspace.test.js
node --test tests/performanceMultiNodeRender.test.js
npm run build
```

本地只读探针、模型替身与截图在 Git 忽略目录 `backend/temp/performance-ai-analysis/`。测试结论不等于真实模型成功返回；本次没有重跑用户的分析或发起压测。

## 启用与回退

启用前确认活动压测/分析为 0、Celery active/reserved/scheduled 均为空；已应用 `performance_testing.0008_performanceanalysis_timeout_progress`，并通过 `./platform restart backend celery` 重启两项服务。backend、celery、controller、caddy 均健康，重试计数为 0，controller/caddy 未重启。

实际已认证 GET 分析接口返回新增字段，旧记录仍为 180 秒、空进度，原失败信息与时间保留；新默认配置读回 600 秒，Celery 注册表包含分析任务。启用前后对原分析字段、压测运行、参与节点、计划和节点身份做哈希比较，一致。未新增真实分析。迁移仍出现既有 RAGConfiguration 的 MariaDB 条件唯一约束提示，与本次字段无关。

迁移只新增字段，不修改节点镜像、协议、计划或运行数据。回退时恢复本次改动前代码并通过服务管理器重启 backend、celery，保留新增字段与分析历史，无需反向删除迁移数据。
