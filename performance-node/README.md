# 性能节点 Agent 0.2.1

> **仅限测试使用 / For authorized testing only.** 这是开发中的压测节点，非生产发行版。只允许对已获授权的测试目标运行；不要用于未获授权的网站或生产环境。

这是独立于 automation-platform 后端、Redis 与平台 `.env` 的性能节点客户端，当前版本为 `0.2.1`，节点协议为 `2`，固定引擎版本为 `2.43.3`。

当前只支持：

- 用一次性登记凭证注册节点；
- 用长期节点身份发送主机 CPU/内存心跳及递增执行报告；
- 校验固定脚本、冻结快照和每轮 mTLS 身份后启动一个 Locust Worker；
- 通过独立监督进程执行租约、总时限、停止和进程组回收；
- 持久化活动及已结束运行，重复命令、Agent 重启均不会重放旧任务。

节点只执行平台固定模板，不执行用户 Python。Worker 仅连接本机 stunnel，stunnel 校验证书链和服务器名称后连接平台 Master；目标 HTTP 请求不继承 Agent 的代理或凭证环境。

## 安装与配置

需要 Python 3.11+、Locust `2.43.3` 和 stunnel。Agent、Django、Celery 进程不会导入 Locust/gevent；只有独立 Worker 子进程加载执行引擎。

```bash
cd performance-node
python -m venv .venv
.venv/bin/pip install .
export PERFORMANCE_PLATFORM_URL='https://platform.example/platform'
export PERFORMANCE_NODE_STATE_DIR="$HOME/.local/state/automation-platform-performance-node"
export PERFORMANCE_NODE_ENROLLMENT_TOKEN_FILE='/secure/path/enrollment-token'
# 可选；默认从 Agent 当前 PATH 解析 stunnel，再以绝对路径启动最小环境子进程。
export PERFORMANCE_STUNNEL_BINARY='/absolute/path/to/stunnel'
.venv/bin/python -m performance_node enroll
.venv/bin/python -m performance_node run
```

`enroll/run` 是保留的环境变量入口。`PERFORMANCE_NODE_ENROLLMENT_TOKEN` 也可直接由运行环境注入；不能同时设置它和 `PERFORMANCE_NODE_ENROLLMENT_TOKEN_FILE`。可选的 `PERFORMANCE_NODE_CA_BUNDLE` 必须指向私有 CA 文件；客户端始终校验证书，不能关闭验证。

发行页面生成的单命令入口使用 `start`：

```bash
.venv/bin/python -m performance_node start \
  --server 'https://platform.example' \
  --node-id '00000000-0000-0000-0000-000000000000' \
  --token 'ONE_TIME_TOKEN' \
  --ca-sha256 'HEX64'
```

`--ca-sha256` 可省略；省略时只使用系统信任库，不会自动降级 TLS。提供该参数时，Agent 先从平台 URL 的部署前缀下固定路径 `api/v1/performance-agent/install/ca.pem` 下载不超过 64KiB 的公开 CA（例如平台 URL 为 `https://host/base` 时下载 `/base/api/v1/performance-agent/install/ca.pem`），按 PEM 原始 bytes 校验 SHA-256、CA 用途和有效期，随后才用该 CA 严格校验注册与心跳 TLS。此 CA 下载不携带 token、Cookie、认证、代理配置，不跟随重定向。

平台地址必须是具有主机名、非 0 端口且没有用户信息、查询或片段的 HTTPS URL，可保留部署子路径。客户端拒绝重定向。状态保存在专用 `PERFORMANCE_NODE_STATE_DIR`，目录权限为 `0700`、状态文件权限为 `0600`，采用原子写入。`start` 在整个 Agent 生命周期持有该目录的独占锁，并把平台 URL、node UUID、CA 指纹绑定到卷；绑定不保存一次性 token，后续参数必须严格一致。新目录会以 `0700` 创建；已有宽权限目录会被拒绝，绝不会被客户端 chmod 或改所有权，请改用专用目录。

注册前会持久化只含 token SHA-256 的尝试标记。明确拒绝、网络不确定或成功响应无法验证时，同一 token 重启不会再次 POST；必须在平台生成真实不同的新 token。身份已原子保存时，即使尝试标记残留也会直接复用身份并跳过注册。正常离线或替换容器请保留身份卷。已注册的身份丢失则由管理员吊销旧节点并新建，不支持重置原身份。平台删除节点不会卸载本机容器或删除身份卷。

## Docker

镜像固定安装 Locust `2.43.3` 与 stunnel4，以 UID `10001` 非 root 用户运行，默认 `CMD` 仍为 `run`。自动注册必须显式使用 `start`。只有独立命名卷持久化节点身份和执行状态；没有特权权限、Docker socket 挂载、`env_file` 或平台 `.env` 依赖。运行时必须使用只读根文件系统和 tmpfs，例如：

```bash
docker run --name 'performance-node-NODE_UUID' \
  --user 10001:10001 \
  --read-only \
  --tmpfs /tmp:rw,noexec,nosuid,nodev \
  --mount type=volume,source='performance-node-NODE_UUID',target=/var/lib/performance-node \
  IMAGE_REFERENCE start \
  --server 'HTTPS_URL' --node-id 'NODE_UUID' --token 'ONE_TIME_TOKEN' \
  --ca-sha256 'HEX64'
```

一次性 token 会出现在本机 Docker 参数中；不得把长期节点凭证放进参数、日志或子进程环境。命名卷应按 node UUID 稳定复用。重复相同容器名会由 Docker 拒绝；注册尚未成功且平台签发了新 token 时，可以移除旧容器但保留卷，再用新 token 重建容器。不要挂载平台目录或其 `.env`。

## 验证

```bash
cd performance-node
PYTHONPATH=src ../backend/.venv/bin/python -m unittest discover -s tests -v
```

测试只使用模拟传输和本机临时 TLS 服务，不访问平台，也不会发起任何压力流量。
