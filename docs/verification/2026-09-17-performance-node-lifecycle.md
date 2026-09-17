# 性能节点管理简化验收

## 变更与边界

- 节点仅编辑名称、内网/公网位置，移除应用层自定义标签和“重置身份”；Docker 容器的资源归属标签不受影响。
- 尚未注册的节点可重新生成安装命令，旧命令失效；安装失败或下载超时不会作废节点。已经注册但未发送首次心跳，也不能再次签发注册凭证。
- 已注册节点离线时检查原容器、网络及身份卷。身份丢失需吊销原节点并新建；吊销不可恢复。
- 吊销前发现未结束运行（排队、准备、运行、停止中），服务端返回 `409 node_has_active_runs`。只有用户明确确认后才发送严格布尔值 `confirm_stop: true`，否则身份和运行状态均不变。
- 仅已吊销且没有未结束运行的节点可删除。使用软删除，保留历史执行记录、报告及节点关联；不会卸载远程容器。
- 删除、吊销与新建运行遵守项目到节点的加锁顺序；并发软删除后的吊销请求返回 404，不再落入 500。

## 已执行验证

| 检查 | 结果与范围 |
| --- | --- |
| 前端全量单测 | 360 项通过；最终安装命令展示/复制保护再次运行节点相关 17 项通过 |
| 前端构建 | 通过；保留既有的大体积 chunk 提示 |
| 性能后端隔离回归 | 60 项通过；审查后将删除节点拒绝执行拆为独立测试，执行模块 16 项再次通过，避免不同拒绝原因互相遮蔽 |
| 控制器、用户、项目隔离回归 | 33 项通过，不访问 NAS 业务库 |
| 本机真实 TLS 接入与 mTLS Worker | 2 项通过，覆盖注册、重启复用身份、吊销，以及低负载正常结束、主动停止、失败请求统计；仅请求临时回环目标 |
| 浏览器交互 | 本机 Vite + 拦截全部 API，完成编辑、命令再生成、409 二次确认取消、明确确认后吊销、停止中禁止删除、结束后删除；10 次模拟写操作，无页面 JS 错误 |
| 实际平台 HTTP | 临时性能项目内 21 项通过，覆盖创建/编辑/重发/注册/吊销/删除以及 400、401、404、409 边界；没有创建真实压测运行 |
| 迁移与服务 | `performance_testing.0003` 已应用；后端、Celery、性能控制器已重启，API 200、Celery pong、控制器在线 |

隔离回归通过 SQLite 验证契约，不冒充 MariaDB 并发压力测试。真实 HTTP 检查使用当前平台的 MariaDB，但没有制造真实任务与删节点的并发压力。未重新安装、删除或修改任何远程容器。

## 数据保留与回退

- 迁移前有 3 个节点、3 条执行记录，1 个节点有非空自定义标签。已导出不含身份凭证的本地备份：`backend/temp/node-lifecycle-acceptance/metadata-before-20260917T113057Z.json`（Git 忽略、权限 0600）。
- 迁移和验收后原有节点及执行记录数量不变，3 条历史运行的 ID、节点关联、快照摘要和状态与备份完全一致。
- 本轮临时验收项目及节点已清理；未清理用户已有节点、压测计划、执行记录或报告。
- 回退迁移只能重建空标签列，原标签需从备份恢复。回退前先停止对应服务并核对匹配的代码/数据库版本，不可直接用旧模型访问新 Schema。
- 迁移出现既有 `RAGConfiguration` 条件唯一约束的 MariaDB 支持警告，与本次节点迁移无关；本次迁移成功。

## 可复现检查

```bash
# backend
.venv/bin/python scripts/test_project_knowledge_offline.py performance_testing
.venv/bin/python scripts/test_project_knowledge_offline.py scripts.test_performance_controller users.test_management projects.test_permissions_scheme_a
.venv/bin/python scripts/test_webui_generation_offline.py scripts.test_performance_agent_contract scripts.test_performance_execution_contract
.venv/bin/python scripts/verify_performance_ui.py

# frontend
npm run test:unit
npm run build
```

浏览器脚本需要本机 Vite 和 Python Playwright 浏览器；加密执行契约需要 stunnel，只允许明确的临时回环端口。不可改用默认业务数据库的测试命令，也不要手动设置绕过隔离保护的标志。
