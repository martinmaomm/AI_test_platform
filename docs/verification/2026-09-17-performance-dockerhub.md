# 性能节点 Docker Hub 分发验收（2026-09-17）

## 当前结论

代码支持 Docker Hub 固定摘要拉取；Linux amd64、arm64 的干净镜像已在本机构建并通过定向全层审计。**尚未上传 Docker Hub**：本机 Docker 未登录，用户提供的是邮箱，尚缺真实 Docker ID。没有修改现有 `.env` 或线上发行目录，没有安装或删除远程节点，没有发起远程压测。

## 已验证

- 隔离 SQLite 后端性能模块 63 项通过；安装器离线 Docker 仿真 27 项通过。
- 归档审计器 8 项通过，覆盖已删除下层密钥、跨读取边界的密钥、配置/历史泄漏、敏感路径及压缩/未压缩 OCI 层。
- 发布器 19 项 mock 测试通过，验证命名空间、摘要、固定标签冲突、匿名读取、classic/containerd ID 差异、失败原子性，以及审计失败时归档不会进入 Caddy 固定公开路径；真实读取官方 Python 单架构远端 manifest，确认本机 CLI 的返回格式可解析。以上共 117 项自动化检查通过。
- `amd64`：11 层、10,307 个文件通过审计；`arm64`：11 层、10,312 个文件通过审计。检查覆盖应用文件白名单、私钥/令牌模式、本地平台敏感配置值和敏感文件名，不输出命中值。
- 最终镜像只保留节点源文件、包描述及 README；使用多阶段构建排除本地 Python 缓存和依赖包的测试私钥样本，未通过“后续层删除”掩盖历史层内容。
- 两种架构均校验 Agent 0.2.0、协议 2、Locust 2.43.3 和固定运行时 SHA256；在禁网、只读、去权限容器中验证 Agent 入口导入、UID10001 和状态目录。Locust 版本命令返回 0，但退出告警见下节。
- 后端和 Celery 在确认无活动任务后重启：本地安装器 HTTP200、严格 CA 验证的 HTTPS 入口 HTTP200、Celery ping 返回 pong。

## 本机待发布资产

归档位于 Git 忽略的 `backend/resource/performance-node-dockerhub/`，未添加 `registry_ref`，不影响原发行目录。

| 架构 | 最终镜像 manifest ID | config ID |
| --- | --- | --- |
| amd64 | `sha256:baf73bda484a7fc960bcf2bf5e3f77b9ba69095ccd56f7ea4af164647c7b0e0b` | `sha256:7223c9256b67f9e55622b0dc5c38cc9c6fb8d7679cfccc0678d26fa3ad6d2aa7` |
| arm64 | `sha256:512a1004c2d1ec1955d59976886f01a57cd86fc413bc818f74e0d9fe977b109e` | `sha256:d9791a9010d75f15192ca505f35605711382fb81224b4de2e8d5c2fb7f11dfa7` |

上述 ID 是本机构建证据，不应当作已公开仓库地址。

## 待完成与边界

1. 用户在本机完成 `docker login` 并确认 Docker ID；创建/核验公开仓库，添加“仅限测试使用”介绍。
2. 使用发布器的显式 `--registry` 推送两种架构，并匿名验证固定摘要可拉取；通过后才切换平台发行目录。
3. 发布后从目标 Linux 网络验证拉取、登记及心跳，不能用离线仿真替代真实 Docker Hub/目标网络验收。
4. `python -m locust --version` 在解释器退出阶段有 `greenlet is being finalized` ignored 异常，已记录至 `docs/TODO-OPT.md`，当前没有证明真实 Worker 长期生命周期不受影响。
5. Docker Hub 可达性和限流仍取决于目标网络；注册凭证仍为15分钟，镜像下载时间过长时需重新生成安装命令。已有节点不自动换镜像、不重置身份。
