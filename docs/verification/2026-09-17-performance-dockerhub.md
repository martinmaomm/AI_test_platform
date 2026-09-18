# 性能节点 Docker Hub 分发验收（2026-09-17～18）

## 当前结论

2026-09-18 已将 Linux amd64、arm64 镜像发布到公开仓库 [martinmao9/performance-node](https://hub.docker.com/r/martinmao9/performance-node)，简介和完整说明均标注“仅限测试使用”。发布前重新执行定向全层审计，发布后验证远端摘要、架构、配置及未登录拉取。平台已切换到新发行目录，后端、Celery 和节点网关重启后检查正常。

现有节点未升级，身份卷、业务数据和原发行目录均保留；没有安装或删除远程节点，也没有发起远程压测。目标 Linux 网络的真实安装及长期运行仍待验收。

## 已验证

- 隔离 SQLite 后端性能模块 63 项通过；安装器离线 Docker 仿真 27 项通过。
- 归档审计器 8 项通过，覆盖已删除下层密钥、跨读取边界的密钥、配置/历史泄漏、敏感路径及压缩/未压缩 OCI 层。
- 发布器 19 项 mock 测试通过，验证命名空间、摘要、固定标签冲突、匿名读取、classic/containerd ID 差异、失败原子性，以及审计失败时归档不会进入 Caddy 固定公开路径；真实读取官方 Python 单架构远端 manifest，确认本机 CLI 的返回格式可解析。以上共 117 项自动化检查通过。
- `amd64`：11 层、10,307 个文件通过审计；`arm64`：11 层、10,312 个文件通过审计。检查覆盖应用文件白名单、私钥/令牌模式、本地平台敏感配置值和敏感文件名，不输出命中值。
- 最终镜像只保留节点源文件、包描述及 README；使用多阶段构建排除本地 Python 缓存和依赖包的测试私钥样本，未通过“后续层删除”掩盖历史层内容。
- 两种架构均校验 Agent 0.2.0、协议 2、Locust 2.43.3 和固定运行时 SHA256；在禁网、只读、去权限容器中验证 Agent 入口导入、UID10001 和状态目录。Locust 版本命令返回 0，但退出告警见下节。
- 后端和 Celery 在确认无活动任务后重启：本地安装器 HTTP200、严格 CA 验证的 HTTPS 入口 HTTP200、Celery ping 返回 pong。
- 2026-09-18 使用已有本机登录身份创建公开仓库；匿名 Docker Hub API 读取仓库及两个标签均成功，仓库为公开状态，两种架构均为 Linux。凭据仅在内存中使用，没有写入项目、镜像或日志。
- 发布器对两个架构均完成远端 manifest/config 核对，使用空 `DOCKER_CONFIG` 验证固定摘要的匿名读取和拉取成功后，才写入 `registry_ref`；发布命令退出码为 0。
- 切换前再次确认 Celery 无活动任务、无活动压测；切换后三个服务的发行路径均指向新目录。HTTPS 安装器内容 SHA256 与源码一致，两种架构归档 HEAD 返回 200 且长度与 manifest 一致；加载实际配置生成的安装命令包含两个固定仓库摘要，`bash -n` 通过（使用无效占位凭证，未签发真实节点凭证）。

## 公开发行资产

归档及 manifest 位于 Git 忽略的 `backend/resource/performance-node-dockerhub/`，两个架构均已添加 `registry_ref`。平台安装器按宿主机架构从 Docker Hub 拉取固定摘要，不通过 Mac 下载大镜像归档。

| 架构 | Docker Hub 标签 | 公开 manifest 摘要 |
| --- | --- | --- |
| amd64 | `0.2.0-amd64-7223c9256b67` | `sha256:baf73bda484a7fc960bcf2bf5e3f77b9ba69095ccd56f7ea4af164647c7b0e0b` |
| arm64 | `0.2.0-arm64-d9791a9010d7` | `sha256:512a1004c2d1ec1955d59976886f01a57cd86fc413bc818f74e0d9fe977b109e` |

实际引用格式为 `docker.io/martinmao9/performance-node@sha256:…`，不使用 `latest` 或浮动标签。用户需要从网页重新生成安装命令，不继续使用之前复制的命令。

## 本机激活与回退

- 仅将 `backend/.env` 中 `PERFORMANCE_NODE_RELEASE_DIR` 切换为新发行目录的绝对路径，其他配置未改；后台服务启动环境同步更新该项。
- 原配置备份保存在 Git 忽略的 `backend/temp/dockerhub-activation-20260918.env.backup`，权限 0600。备份可能含敏感信息，禁止提交或公开；只用于核对恢复本次改动的字段。
- 原 `backend/resource/performance-node/` 保留。需要回退时，只恢复发行目录这一项，并在无活动任务时重启后端、Celery 和节点网关；不要整份覆盖已有新配置。不会自动回滚已经安装到远程机器的镜像。

## 待完成与边界

1. 从目标 Linux 网络验证拉取、登记及心跳；本机真实匿名拉取不替代 Ubuntu/Unraid 目标网络验收。
2. `python -m locust --version` 在解释器退出阶段有 `greenlet is being finalized` ignored 异常，已记录至 `docs/TODO-OPT.md`，当前没有证明真实 Worker 长期生命周期不受影响。
3. Docker Hub 可达性和限流仍取决于目标网络；注册凭证仍为15分钟，镜像下载时间过长时需重新生成安装命令。已有节点不自动换镜像、不重置身份。
