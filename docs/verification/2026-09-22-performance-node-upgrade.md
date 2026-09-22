# 原节点升级指导验收

> 历史验收：用户随后选择[保留原节点重新安装](2026-09-22-performance-node-reinstall.md)。下文工具及旧流程可在功能提交 `8e0eedb` 上复现，当前页面已使用简化重装。

范围：为现有旧版本节点提供明确升级入口和可复制操作，沿用 Agent 0.4.0 固定发行；不重新注册、不修改远端节点、不发起业务目标请求。

## 平台接口与命令

- 隔离 SQLite 下 `performance_testing` 共 103 项测试通过，包含新增的管理员权限、旧节点升级元数据、脚本 SHA256、活动运行阻断、缺失脚本和公开 GET/HEAD 行为。
- 前端 `performanceWorkspace.test.js` 与 `performanceMultiNodeRender.test.js` 共 27 项通过。命令测试使用真实 Bash、临时 fake curl/docker 和无敏感内容的 Python 文件，验证带单引号 URL 的参数传递、错误 SHA256 不执行、容器/服务名限制以及不污染调用终端的 shell 状态。
- `npm run build` 通过；保留既有大包提示。没有为本次升级入口改动项目依赖。

复现命令：

```bash
cd backend
.venv/bin/python scripts/test_webui_generation_offline.py performance_testing
cd ../frontend
node --test tests/performanceWorkspace.test.js tests/performanceMultiNodeRender.test.js
npm run build
```

## 真实浏览器

`backend/scripts/test_performance_node_upgrade_browser.py` 启动临时 SQLite + Django + 编译后的 Vue，使用真实 Chrome。升级发行元数据使用明确的 fixture，其他列表读取真实隔离 API。只允许 loopback GET；剪贴板为页面内存替身，不执行命令。

验证旧节点的“升级节点”入口、当前/目标版本、完整 Docker 命令、复制前刷新、非法容器名阻断、Unraid 说明、单服务 Compose 命令、任务未结束/读取失败不复制，以及重开弹窗清空输入。外部请求 0、写接口请求 0、远端升级 0。

```bash
cd backend
.venv/bin/python scripts/test_performance_node_upgrade_browser.py
```

截图由脚本输出到 Git 忽略目录 `backend/temp/performance-node-upgrade-browser/`：`docker-upgrade.png`、`compose-upgrade.png`。已按截图修正长镜像摘要把版本标签挤成逐字换行的问题。

## 升级工具与真实 Docker

`test_performance_node_upgrade.py` 的 19 项测试通过，覆盖原身份/平台/启动方式、挂载保留、活动任务与待确认报告阻断、受管理/复杂网络与自动删除容器拒绝、镜像失败不停止旧节点、替换失败回滚、无法清理新容器/恢复名称时不启动旧备份、离线原容器、重复执行与并发锁。

在本机 Colima 的 Unix socket 对应 Docker 中执行以下三个隔离验收，均通过：

```bash
python3 backend/scripts/test_performance_node_upgrade.py
python3 backend/scripts/test_performance_node_upgrade_docker.py
python3 backend/scripts/test_performance_node_upgrade_docker.py --source-mode start
python3 backend/scripts/test_performance_node_upgrade_docker.py --source-mode start --inject-start-failure
```

真实 Docker 使用缓存的 0.3.2 原镜像、已发布的 0.4.0 固定镜像和随机命名的临时身份卷。分别保留 `run` 与 `start` 原命令、身份卷、只读根文件系统、init、tmpfs、cap-drop、no-new-privileges、CPU/内存限制与重启策略；成功后旧容器停用备份，重复执行为 no-op；注入新版启动失败时只清理新容器并恢复旧容器。测试结束后按独立所有权标签确认临时容器和卷均已清理，没有创建额外网络。

该验收在 macOS 上只替换测试专用的 Linux/root 主机检查与锁目录，实际容器仍由 Linux Docker daemon 创建；平台地址使用容器内关闭的 loopback 端口，不向真实平台注册或发心跳。它验证容器和身份保留及恢复，不证明远端设备上的主机依赖、真实平台心跳或业务请求成功。

## 主工作区启用与回读

2026-09-22 20:29（北京时间）已将功能提交 `8e0eedb` 快进合入主工作区，复制已验收前端构建，仅通过服务管理器停止/启动后端与 Caddy。无需数据库迁移、发行目录变更或新镜像发布；Celery 与控制器保持原进程。

- 四项服务均运行、纳管且健康：backend PID70457、Caddy PID70459、Celery PID43013、controller PID43014。
- 管理员真实 HTTP 接口返回 `unraid`、`osjp` 两台旧节点可用升级信息、目标 0.4.0 固定镜像及对应节点参数；远端节点版本仍是 0.3.2 / 协议 2。
- 通过原 CA 严格 TLS 回读 Caddy 升级脚本，GET/HEAD 成功、POST 被拒绝、Cache-Control 为 no-store；字节及 SHA256 与页面和已提交脚本一致：`ebb9810e4945570920216cf21d2e1f5d4651ef28145e0a6caf6bb49abda4a183`。
- 原 CA 未改变；Vite 实际入口已包含“升级节点”。活动压测 0，11 条历史运行内容哈希与切换前完全一致，没有新增运行。
- 三个浏览器 ZIP 保持未跟踪，没有提交或修改。切换前版本记录、前端构建备份及只读回读摘要位于主工作区 Git 忽略目录 `backend/temp/performance-node-upgrade-rollout/`。

## 使用边界

页面空闲检查不会创建平台维护锁；命令复制以后到升级结束期间仍需避免安排新任务。脚本在本机停止前后检查执行与待确认报告。自动工具仅支持独立容器的单一动态 bridge 网络，受管理或复杂配置回原管理工具操作。

Unraid 和 Compose 说明依据官方 [Unraid 容器管理](https://docs.unraid.net/unraid-os/using-unraid-to/run-docker-containers/managing-and-customizing-containers/)与 [Docker Compose up](https://docs.docker.com/reference/cli/docker/compose/up/)。需保留原模板、项目、挂载和启动参数。

启动成功仅表示新容器通过早期存活检查，最终需回平台确认 Agent 0.4.0 / 协议 3 在线，再进行单用户验证。本次不代替用户执行远端升级或正式压测。
