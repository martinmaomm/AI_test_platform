# 性能节点 0.4.0 发布与多节点平台切换

2026-09-22，按已确认的多节点方案完成公开发行、主工作区合入、数据库迁移及本机服务激活。功能和隔离验收见 [多节点实施验收](2026-09-22-performance-multi-node.md)。远端节点继续由用户手动升级。

## 不可变发行

统一镜像引用：

```text
docker.io/martinmao9/performance-node@sha256:6561c059c5d15c8e52bc44ad82f718b21671fc4a1b735c8ed9b23c1d518c36ce
```

| 架构 | 远端成员摘要 | 归档大小 |
| --- | --- | --- |
| amd64 | `sha256:54a087af8a65663d3ca4f3a5bb4d0b2a9648dabac3e15a7ac215bdfa7d4910fe` | 85,108,222 字节 |
| arm64 | `sha256:7121d56bc3bf916772a0eb5ea2d42e86210e94be3329e69d35ae9e1a57103c3e` | 84,387,985 字节 |

Agent 0.4.0，协议 3，快照 3，Locust 2.43.3。固定运行时 SHA256：`9d73f052e312018d4c4426818da63755951c951379de4775ae26db1a43d8ec9b`。

两个架构均在本机 Colima 的 Unix socket 对应 Docker 中构建。现有发布脚本完成容器版本及模板检查、全部镜像层审计、固定配置摘要核验、匿名读取和拉取；统一索引只含核验过的 amd64/arm64 镜像成员。发行文件位于 Git 忽略目录 `backend/resource/performance-node-dockerhub-0.4.0/`。

```bash
backend/.venv/bin/python backend/scripts/publish_performance_node_release.py \
  --image amd64=automation-platform-performance-node:0.4.0-amd64 \
  --image arm64=automation-platform-performance-node:0.4.0-arm64 \
  --output backend/resource/performance-node-dockerhub-0.4.0 \
  --registry docker.io/martinmao9/performance-node --publish-index
```

修正 Docker 启动验收模拟平台的硬编码旧协议，改读当前 `PROTOCOL_VERSION`。两个架构的真实容器均通过正常注册/重启复用身份、错误 CA 拒绝、注册被拒绝不重试；测试仅连接临时模拟平台，没有注册真实节点。临时容器和卷已全部清理。

## 数据库备份、迁移与切换

- 切换前重复检查：活动/排队压测为 0，Celery active/reserved/scheduled 均为空。先停止后端接收入口，复查无活动运行，再优雅停止其余服务并确认旧管理器退出。
- 备份当前代码版本、`.env`、服务管理状态和前端构建。使用 `mariadb-dump --single-transaction` 保存完整 SQL，包含 routines/events/triggers；凭据仅放在本机权限 0600 的临时配置文件，使用后删除，数据库内容不写日志或 Git。
- 切换前预备备份和停止后的最终备份分别恢复到本轮独立的临时 MariaDB 容器。75 张表、1113 行、11 条 Run 的数量核对一致；恢复验收容器已删除。最终压缩备份 SHA256：`2d6cfbb08be9ac2ff1cd919f5d37971647886bc9670732e0d8d8b9edd0cd214c`。
- 主工作区从 `764dfb5` 快进到已验收的实施提交；三个浏览器 ZIP 保持未跟踪，未提交或修改。仅执行 `performance_testing.0005` 迁移并成功完成。
- 11 条旧 Run 的快照、SHA、公共字段、请求/响应证据、指标及原命令/报告均与最终备份一致；迁移为 11 条参与记录，未知历史名称、版本和停止确认不补造。
- 同步固定发行目录及前端构建，`.env` 的发行目录改为 0.4.0。启动新管理器时去掉继承的 `.env` 变量覆盖，保留原 CA 和其他配置。

备份和只读验收摘要位于主工作区 Git 忽略的 `backend/temp/performance-node-0.4.0-rollout/`。该目录可能包含真实测试凭据和数据库内容，不能提交或公开。

迁移过程保留了一条既有 Django 警告：MariaDB 不支持 RAGConfiguration 的条件唯一约束；不涉及本次性能参与表约束，没有为此修改其他模块。

## 实际服务与接口验证

2026-09-22 17:47（北京时间），通过实际 HTTP 服务与严格 TLS 回读：

| 检查 | 结果 |
| --- | --- |
| 平台配置 | Agent 0.4.0，控制器在线，execution_enabled=true |
| 四项服务 | 后端 PID43012、Celery PID43013、控制器 PID43014、Caddy PID43019 均运行、纳管且健康 |
| 实际进程环境 | 四项进程发行目录均为 0.4.0，无旧管理器环境覆盖 |
| 历史报告 | 11 条旧执行详情均 HTTP 可读，参与节点数量及当前名称来源正确 |
| 旧节点 | unraid、osjp 均仍在线，上报 0.3.2 / 协议2；installation 显示 upgrade_required=true |
| 新资格 API | 旧节点不能加入新执行，明确要求升级 |
| 安装入口 | 可用；返回上述固定多架构镜像及正确运行时 |
| HTTPS 公共 CA | 使用原 CA 严格校验成功，接口内容与原证书一致 |
| amd64/arm64 归档 | HEAD 200、长度一致、Range 206，前 1024 字节与本地归档逐字节一致 |
| 实际前端 | Vite 返回新的节点资格刷新、多节点选择组件 |
| 活动运行 | 0；切换过程没有发起业务目标请求或新压测 |

## 升级与回退边界

用户在两台远端设备保留原身份卷、平台地址和 CA，替换为网页安装指导中的 0.4.0 固定镜像。不要重新注册、删除身份卷或同时启动两个共用身份卷的容器。看到 0.4.0 在线后，先逐节点做单用户验证，再在正式压测弹窗多选节点。

本次已验证双节点最多3个虚拟用户的隔离真实 mTLS 联动；真实多机器/跨网和1000总用户容量仍待授权的受控服务逐级验收，不向外部商城直接加压。

0005 明确不可逆，不能仅切回旧镜像或执行逆向 migration。需要回退时关闭执行、停止新轮次，保留升级后的证据，再同时恢复旧代码、匹配节点版本、旧发行配置和已演练恢复的升级前完整数据库；备份之后的数据须单独保留或合并。
