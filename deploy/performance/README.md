# 公网性能节点部署与联调

这是第一版单节点的部署参考，不是整个平台的公网发布方案。节点主动连接平台，无需 VPN；不修改节点机器上的已有网站、代理或容器。

## 推荐：从网页一键接入

平台管理员进入性能项目的“节点管理”，点击“添加节点”，填写名称和网络位置。创建后按安装向导，将生成的完整命令复制到远程 Linux **root** 终端执行（其他用户先 `sudo -i`）。页面不需要填写 SSH 密码。

- 第一版复用已经安装、可用的本地 Docker；缺少 Docker 或不支持的系统会给出明确提示，不静默安装/升级宿主机。
- 安装器下载平台发布的固定版本镜像并校验归档 SHA256、镜像 ID 和系统架构；无需用户构建源码。
- 命令包含15分钟有效的一次性注册凭证，过期需重新生成；有效期内不能公开分享。粘贴命令可能进入本机剪贴板、终端历史或短时进程参数，请勿录屏公开，使用后按本机策略清理。长期身份只保存在节点私有卷中，不进入运行容器环境变量。
- 平台使用私有 CA 时，命令包含 **CA 公钥**，先验证 HTTPS 和安装器摘要再执行；不会关闭 TLS 校验，不含 CA 私钥。
- 复制后安装向导保持打开，真实收到注册/心跳后才更新状态；关闭页面不影响远程安装，重新打开不会回显旧凭证。
- 每个节点使用独立资源；重复执行先检查已有身份和归属，不覆盖别的项目/容器。不因网络失败自动重置身份。
- 已注册但离线时先查看容器和网络；“重置身份”会作废原凭证，不是普通重启按钮。

镜像初次下载可能较慢。下载完成但注册凭证已过期时，重新生成命令后复用已有校验通过的镜像；不要删除身份卷重装。

### 管理员首次配置镜像发布

一键命令依赖已发布镜像，不能只配置域名。先在对应架构构建（或从可信构建机导入）镜像，固定命名：

```text
automation-platform-performance-node:0.2.0-amd64
automation-platform-performance-node:0.2.0-arm64
```

发布机需要能运行对应架构的镜像以核对安装包内的 Agent、Locust、协议和固定执行脚本。然后在仓库根目录执行（可以只发布一种架构）：

```bash
backend/.venv/bin/python backend/scripts/publish_performance_node_release.py \
  --image amd64=automation-platform-performance-node:0.2.0-amd64 \
  --image arm64=automation-platform-performance-node:0.2.0-arm64
```

生成目录默认 `backend/resource/performance-node/`，包含 manifest 和受控镜像归档，已被 Git 忽略。同版本已发布镜像拒绝直接覆盖，修改引擎后应按版本契约重新发布。这个目录只放可公开的发行资产，禁止放 `.env`、身份文件、数据库备份、CA 私钥和符号链接。

后端实际 `.env` 增加（示例路径请替换）：

```dotenv
PERFORMANCE_NODE_PUBLIC_URL=https://load.example.com:18443
PERFORMANCE_NODE_RELEASE_DIR=/absolute/path/backend/resource/performance-node
# 私有 CA 场景填公共证书；可信公共 CA 场景留空。
PERFORMANCE_NODE_CA_CERT_FILE=/absolute/private/gateway/storage/pki/authorities/local/root.crt
```

启动 Caddy 时也设置同一个 `PERFORMANCE_NODE_RELEASE_DIR` 绝对路径；改完重启后端并按新环境启动网关。首次缺配置/镜像/安装器时，页面明确显示暂不可安装，而不是生成必然失败的命令。

普通节点管理员只用网页向导。以下手动接入步骤保留用于维护和排障。

## 1. 两条独立连接

| 节点访问地址 | 平台监听 | 用途 |
| --- | --- | --- |
| `https://load.example.com:18443` | Caddy → `127.0.0.1:8000` | 节点登记、心跳、指令与报告 |
| `load.example.com:9443` | stunnel → 本机随机回环端口 | 每次运行的 Locust mTLS 连接 |

路由器按 TCP 映射上述两个端口；当前 RPC 外部端口和 `PERFORMANCE_RPC_PORT` 必须一致。节点不发布入站端口。**不要开放原始 Locust RPC、Redis、MySQL、Django 8000 或 Vite 5173 来代替这两个入口。**

9443 只在任务准备/执行期间监听，空闲时连接失败是正常现象，不代表映射错误。域名应解析到平台路由器公网地址；不要用 Mac 的代理/Fake-IP DNS 结果配置公网 DNS。

## 2. 平台侧 HTTPS 入口（Mac）

先按[安装文档](../../docs/installation.md#131-启动执行控制器)安装 stunnel、配置实际 `backend/.env` 并启动独立执行控制器。

安装 Caddy（本次验证版本 `2.11.4`）：

```bash
brew install caddy
```

在**仓库根目录**执行，替换域名和 Mac 局域网地址：

```bash
export PERFORMANCE_GATEWAY_DOMAIN=load.example.com
export PERFORMANCE_GATEWAY_PORT=18443
export PERFORMANCE_GATEWAY_BIND=192.168.31.205
export PERFORMANCE_GATEWAY_DATA="$PWD/backend/temp/performance-gateway/storage"
export PERFORMANCE_NODE_RELEASE_DIR="$PWD/backend/resource/performance-node"
install -d -m 0700 backend/temp/performance-gateway/storage
caddy validate --config deploy/performance/Caddyfile --adapter caddyfile
caddy run --config deploy/performance/Caddyfile --adapter caddyfile
```

`validate` 和 `run` 都需要这些环境变量；缺少 `DATA` 会报 `root` 参数为空。已经存在的存储目录必须是专用私有目录，不要将其他宽权限目录直接挪来使用。

入口允许两个精确的 POST Agent 路由：

- `/api/v1/performance-agent/enroll/`
- `/api/v1/performance-agent/heartbeat/`

另外只开放 GET/HEAD `/api/v1/performance-agent/install/install.sh` 和严格限定版本/架构的 `install/artifacts/<版本>/linux-<amd64或arm64>.tar.gz`。大镜像由 Caddy 静态分发并支持 Range，不经过 Django/ASGI 缓冲；不提供目录浏览，不公开 manifest 或任意资源文件。

其他路由和方法返回 404。Agent请求体上限为65536字节，与后端一致；Caddy管理API、HTTP重定向端口和访问日志均未开启，不能通过此入口打开平台管理网页。静态镜像下载写超时为15分钟；Agent上游响应头仍有独立20秒限制。

这里用独立持久化私有 CA，由 Caddy 管理站点证书续签；不向 Mac 的全局信任库安装 CA。将以下**公钥证书文件**通过已验证主机身份的 SSH/SCP 复制到节点：

```text
backend/temp/performance-gateway/storage/pki/authorities/local/root.crt
```

不要复制整个 storage、CA 私钥、`backend/.env` 或平台数据库凭证。节点通过 `PERFORMANCE_NODE_CA_BUNDLE` 明确信任该 CA；不要使用 `-k` 或关闭 TLS 验证。浏览器/默认 curl 没有信任私有 CA 时提示不受信任是预期行为。升级和重启应保留 CA 存储目录；删除它会导致现有节点不再信任入口。

参考：[Caddy TLS](https://caddyserver.com/docs/caddyfile/directives/tls)、[全局选项](https://caddyserver.com/docs/caddyfile/options)。这套配置不是面向不可信租户的防滥用网关；公网限流、准入网段等需上线前单独配置。

## 3. Linux Docker 节点

只传输 `performance-node/` 源码，在目标架构机器构建：

```bash
docker build -t automation-platform-performance-node:0.2.0 /opt/automation-performance-node/source
docker network create automation-performance-net
docker volume create automation-performance-state
```

名字已存在时先检查归属，不要直接重建或删除。只读挂载到节点的文件：

- `/opt/automation-performance-node/config/gateway-ca.crt`：上一步复制的 CA 公钥，0644。
- `/opt/automation-performance-node/config/enrollment-token`：管理员通过 `POST /api/v1/projects/<项目ID>/performance/nodes/` 创建节点时，响应中的 `enrollment_token`；0600，属主 UID/GID 为 `10001:10001`。网页向导已将这个步骤包含在命令内，不需要手工提取凭证。

整个部署目录归 root 管理（0700）。登记凭证用受保护文件输入，不写在命令参数、Git、日志或容器环境里。令牌会过期，建议镜像构建、网络检查完成后再从平台取得。登记一次：

```bash
docker run --rm --name automation-performance-enroll \
  --network automation-performance-net --init --read-only \
  --tmpfs /tmp:rw,noexec,nosuid,size=64m \
  --cap-drop ALL --security-opt no-new-privileges \
  --memory 512m --cpus 1 --pids-limit 128 \
  -e PERFORMANCE_PLATFORM_URL=https://load.example.com:18443 \
  -e PERFORMANCE_NODE_CA_BUNDLE=/run/gateway-ca.crt \
  -e PERFORMANCE_NODE_ENROLLMENT_TOKEN_FILE=/run/enrollment-token \
  --mount type=volume,src=automation-performance-state,dst=/var/lib/performance-node \
  --mount type=bind,src=/opt/automation-performance-node/config/gateway-ca.crt,dst=/run/gateway-ca.crt,readonly \
  --mount type=bind,src=/opt/automation-performance-node/config/enrollment-token,dst=/run/enrollment-token,readonly \
  automation-platform-performance-node:0.2.0 enroll
```

确认“节点注册成功”后删除**本轮已消费的一次性凭证文件**，不删除 volume/identity.json。启动常驻 Agent：

```bash
docker run -d --name automation-performance-node \
  --network automation-performance-net --restart unless-stopped \
  --stop-timeout 25 --init --read-only \
  --tmpfs /tmp:rw,noexec,nosuid,size=64m \
  --cap-drop ALL --security-opt no-new-privileges \
  --memory 512m --cpus 1 --pids-limit 128 \
  --log-opt max-size=10m --log-opt max-file=3 \
  -e PERFORMANCE_PLATFORM_URL=https://load.example.com:18443 \
  -e PERFORMANCE_NODE_CA_BUNDLE=/run/gateway-ca.crt \
  --mount type=volume,src=automation-performance-state,dst=/var/lib/performance-node \
  --mount type=bind,src=/opt/automation-performance-node/config/gateway-ca.crt,dst=/run/gateway-ca.crt,readonly \
  automation-platform-performance-node:0.2.0 run
```

这里 1 CPU/512MiB 仅作为低负载联调限制，不代表正式压测容量配置。身份目录由镜像初始化为 UID10001、0700；身份文件为0600。运行容器不含一次性登记凭证，不挂载 Docker socket 或平台目录。重启容器使用同一 volume，不再次执行 enroll。镜像升级必须与平台 Agent 协议、Locust 版本和固定脚本摘要一致。

## 4. 隔离联调与验收

`smoke_fixture.py` 只能作为临时测试目标。将它复制到节点专用部署目录，运行同网络容器，**不加 `-p` 发布端口**：

```bash
docker run -d --name automation-performance-fixture \
  --network automation-performance-net --network-alias performance-fixture.test \
  --read-only --tmpfs /tmp --cap-drop ALL --security-opt no-new-privileges \
  --memory 64m --cpus 0.25 --pids-limit 64 \
  --log-opt max-size=5m --log-opt max-file=2 \
  --mount type=bind,src=/opt/automation-performance-node/smoke_fixture.py,dst=/app/smoke_fixture.py,readonly \
  --entrypoint python automation-platform-performance-node:0.2.0 /app/smoke_fixture.py
```

在平台创建专用性能项目，目标为 `http://performance-fixture.test:8080`，仅允许 GET；计划为 1 用户、生成速率1、时长5秒、等待1秒，步骤 `GET /probe`，预期200。域名只在节点 Docker 网络解析，不是可从 Mac 浏览器打开的网站。

用以下只读命令读取目标计数；每轮记录前后差值，不预设请求数一定为5：

```bash
docker exec automation-performance-fixture python -c 'import urllib.request; print(urllib.request.urlopen("http://127.0.0.1:8080/stats").read().decode())'
```

验收要求：正常完成、最终统计完整且失败为0；平台请求数等于目标计数增量；重复 request_id 不创建新运行；停止后不再发请求；无残留 Worker/监督进程。fixture 重启会清零计数，不能跨重启比较。

完成后停止临时目标，但保留平台记录供查看：

```bash
docker stop --time 5 automation-performance-fixture
```

需要重跑时执行 `docker start automation-performance-fixture`，并重新记录计数基线。正式目标必须另外获得压测授权，不能直接将联调计划替换为第三方生产站点。

## 5. 当前运行维护

```bash
# 节点端
docker logs --tail 100 automation-performance-node
docker stats --no-stream automation-performance-node
docker restart automation-performance-node

# 平台端（仓库根目录）
tail -n 100 backend/logs/performance-controller.log
tail -n 100 backend/logs/performance-gateway.log
```

重启节点前先确认没有正在运行的任务；活动任务因重启会停止，不会自动重放。不要删除 volume 来解决网络问题。

本次 Mac 的 Caddy、控制器是后台进程，**未配置开机自启**；Mac 重启后除后端/Celery外，还需按第2节启动 Caddy、按安装文档启动 `run_performance_controller`。前台启动时不要与现有后台实例重复监听端口或争用控制器租约。节点容器配置了 `unless-stopped`，依赖 Docker 服务正常启动；手动停掉的容器需要手动启动。

公网链路通过不等于容量/长期稳定性通过。Ubuntu20.04宿主机的安全维护状态、Docker/内核升级、公网入口限流、日志保留、进程守护和多节点仍需分别验收；不要因短时低负载通过而宣称生产可用。
