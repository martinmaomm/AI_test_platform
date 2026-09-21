# 性能节点 0.3.2 发布与平台切换

## 发布结果

用户明确授权发布 0.3.2，并自行手动更新远端节点。本次复用已验证的功能提交 `c3678b4` 和双架构安装包，完成公开 Docker Hub 发行与本机平台切换。

统一固定镜像引用：

```text
docker.io/martinmao9/performance-node@sha256:43415a32bba8e486892d90a47114b0f7e6d4d603e8fcc77415c86a2e0813d6ab
```

| 架构 | 远端镜像摘要 |
| --- | --- |
| amd64 | `sha256:8602eee585e21ad82e3e60bba1c438c5ee29162b0e78553fd772f5f93b93498a` |
| arm64 | `sha256:1afbf4cb832936c2b3264f33f0a720db6cf40c46c857182ffae36c98bd26c003` |

Agent 为 0.3.2，Locust 为 2.43.3，协议为 2。固定运行时 SHA-256 为 `a4c74ee52140e1e7a9c13abd71c8597d9e1dd8c173381200f19382492f9969ae`。

发行目录：`backend/resource/performance-node-dockerhub-0.3.2/`。公开引用、清单与归档由现有发布脚本生成，未手工替换哈希或放宽检查。

## 发行验证

```bash
backend/.venv/bin/python backend/scripts/publish_performance_node_release.py \
  --image amd64=automation-platform-performance-node:0.3.2-amd64 \
  --image arm64=automation-platform-performance-node:0.3.2-arm64 \
  --output backend/resource/performance-node-dockerhub-0.3.2 \
  --registry docker.io/martinmao9/performance-node --publish-index
```

使用已确认的本机 Colima Unix socket。发布脚本完成两个架构的隔离运行时检查、归档全部镜像层审计、远端镜像配置摘要核验、匿名 manifest 读取及匿名镜像拉取；统一索引的两个成员摘要与已审计镜像一致，匿名分架构拉取检查通过。

功能、权限和实际执行器/浏览器验证沿用 [执行详情原值验收](2026-09-21-performance-original-details.md)，本次没有为发布修改已验证的运行时或重新构建镜像。

## 本机平台切换

切换前再次确认活动压测为 0，Celery active/reserved/scheduled 均为空。备份 `.env`、管理器状态、Git 版本和旧前端构建，逐项优雅停止四项服务并等待旧管理器退出，然后将 `codex/validation-raw-details` 快进合入主工作区。

将发行目录同步到主工作区，修改 `.env` 的 `PERFORMANCE_NODE_RELEASE_DIR` 指向 0.3.2，保留 0.3.1 目录；校验 Caddy 配置和模板哈希后，安装已通过浏览器验证的前端构建并通过 `./platform start` 启动服务。

检查发现旧管理器继承了 0.3.1 发行目录环境变量，单独重启子服务会覆盖新 `.env` 值。因此本次重新启动管理器时去掉从 `.env` 派生的继承覆盖，已确认新管理器没有该旧覆盖，四项子服务的实际环境均指向 0.3.2。此处只调整本次启动方式，未修改服务管理器代码。

| 进程 | PID | 结果 |
| --- | --- | --- |
| 管理器 | 58523 | v2 进程身份核验有效 |
| 后端 | 58526 | 已纳管，HTTP API 健康 |
| Celery | 58527 | 已纳管，1 个 Worker 响应心跳 |
| 控制器 | 58529 | 已纳管，数据库连接及心跳有效 |
| Caddy | 58536 | 已纳管，原 CA 校验和节点入口正常 |

启动约 106 秒后，所有进程身份仍有效，四项服务均运行且健康，重试计数为 0。

## 实际接口回读

2026-09-21 23:22:33（北京时间），通过实际 HTTP 服务读取配置及现有节点安装信息：

- `agent_version=0.3.2`、`controller_online=true`、`execution_enabled=true`。
- 安装入口 `available=true`，支持 amd64、arm64，返回上述统一固定镜像引用。
- `osjp3.1` 节点仍在线，上报版本 0.3.1，`upgrade_required=true`；未更新或重新注册该节点。
- 控制器心跳距当前 0.42 秒，无活动压测。
- 使用原 CA 严格验证实际 Caddy TLS，CA 内容一致；两种归档的 HTTPS HEAD 均为 200，长度分别为 85,092,993 和 84,366,220 字节；Range 请求均返回 206，字节内容与本地归档一致。
- 实际 Vite 服务已返回“密码、令牌和 Cookie 按原值显示”的新版组件；检查脚本按 UTF-8 解码 JavaScript。原始响应的默认 ISO-8859-1 解码曾造成一次检查误报，确认页面源码正确后修正检查方式。

检查摘要保存在 Git 忽略的 `backend/temp/performance-node-0.3.2-rollout/live-verification.json`。没有写入计划、运行记录、注册凭证或向业务目标发请求。

## 用户后续操作与回退

远端节点由用户手动升级：保留原容器配置、平台地址、CA 与身份卷，使用上述新镜像替换旧镜像；无需重新登记节点。升级后确认页面显示 0.3.2，再重新运行单用户验证，新增执行详情即可读取原始密码、令牌和 Cookie。旧记录已脱敏的值无法还原。

本次未操作远端容器，也未执行真实目标的单用户验证或正式压测。远端升级及其业务执行验收仍待用户完成。

旧发行目录及 `backend/temp/performance-node-0.3.2-rollout/` 中的配置/前端备份保留。回退需要同时恢复配套的平台代码、发行目录和节点镜像，保留身份卷；仅切回 `.env` 发行目录会再次触发模板哈希不匹配。
