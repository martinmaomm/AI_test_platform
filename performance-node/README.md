# 性能节点客户端（第一批）

这是独立于 automation-platform 后端、Redis 与平台 `.env` 的性能节点客户端，当前版本为 `0.1.0`，节点协议为 `1`，候选引擎版本为 `2.43.3`。

当前只支持：

- 用一次性登记凭证注册节点；
- 用长期节点身份发送主机 CPU/内存心跳；
- 收到 `idle` 命令时保持空闲。

当前**不能执行压测**，不包含 Worker、Locust 或任何目标请求功能。2026-09-16 已验证本机 Linux arm64 Docker 镜像构建、非 root 启动及独立 Agent 到本机临时 Django 的 HTTPS 接入；远程 Linux、公网连通性及 amd64 架构尚未验收。

## 安装与配置

需要 Python 3.11+。依赖固定为 `requests==2.32.5` 与 `psutil==7.2.2`；下批如需引擎可安装 `.[engine]`（固定 `locust==2.43.3`），但本批源码不会导入 Locust 或 gevent。

```bash
cd performance-node
python -m venv .venv
.venv/bin/pip install .
export PERFORMANCE_PLATFORM_URL='https://platform.example/platform'
export PERFORMANCE_NODE_STATE_DIR="$HOME/.local/state/automation-platform-performance-node"
export PERFORMANCE_NODE_ENROLLMENT_TOKEN_FILE='/secure/path/enrollment-token'
.venv/bin/python -m performance_node enroll
.venv/bin/python -m performance_node run
```

`PERFORMANCE_NODE_ENROLLMENT_TOKEN` 也可直接由运行环境注入；不能同时设置它和 `PERFORMANCE_NODE_ENROLLMENT_TOKEN_FILE`。CLI 没有凭证参数。可选的 `PERFORMANCE_NODE_CA_BUNDLE` 必须指向私有 CA 文件；客户端始终校验证书，不能关闭验证。

平台地址必须是具有主机名、非 0 端口且没有用户信息、查询或片段的 HTTPS URL，可保留部署子路径。客户端拒绝重定向。状态保存在 `PERFORMANCE_NODE_STATE_DIR/identity.json`，目录权限为 `0700`、文件权限为 `0600`，采用原子写入。新目录会以 `0700` 创建；已有宽权限目录会被拒绝，绝不会被客户端 chmod 或改所有权，请改用专用目录。已有身份时 `enroll` 会停止，绝不自动重新登记；请由管理员重置节点凭证后按受控流程处理本地状态。

## Docker

镜像以 UID `10001` 非 root 用户运行，只有独立命名卷持久化节点身份；没有特权权限、Docker socket 挂载、`env_file` 或平台 `.env` 依赖。先在宿主机以安全的环境注入方式提供平台 URL 和注册凭证，再执行登记（例如覆盖默认命令）：

```bash
cd performance-node
docker compose run --rm performance-node enroll
docker compose up -d
```

Compose 文件未使用 `env_file`，也不挂载平台目录或其 `.env`。不要把 token 写进 compose 文件、命令行历史或日志。

## 验证

```bash
cd performance-node
PYTHONPATH=src ../backend/.venv/bin/python -m unittest discover -s tests -v
```

测试只使用模拟传输和本机临时 TLS 服务，不访问平台，也不会发起任何压力流量。
