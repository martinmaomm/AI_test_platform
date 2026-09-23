# 公网性能节点部署与联调

这是 0.4.1 可配置请求超时与多节点执行的部署参考，不是整个平台的公网发布方案。每个节点主动连接平台，无需 VPN；不修改节点机器上的已有网站、代理或容器。

## 推荐：从网页一键接入

平台管理员进入性能项目的“节点管理”，点击“添加节点”，填写名称和网络位置。创建后复制网页提供的**一条 `docker run` 命令**，在已有 Docker 的 Linux 终端执行。需要 root 或 Docker 操作权限；页面不需要填写 SSH 密码。

- 安装命令复用已经安装、可用的本地 Docker；缺少 Docker 或不支持的系统会给出明确提示，不静默安装/升级宿主机。
- 命令引用 Docker Hub 多架构索引的固定 `sha256` 摘要，Docker 自动选择 amd64/arm64 并显示分层下载进度。不再从平台下载长安装脚本，也不在网页内拼接架构判断、整段证书或归档下载逻辑。
- 镜像内 `start` 自动完成首次注册和持续运行。命令含15分钟有效的一次性注册令牌，消费后失效；令牌会出现在终端历史及 `docker inspect` 的容器参数中，请勿公开分享命令或检查输出。长期身份只保存在节点私有卷中，不进入容器参数、环境或日志。
- 私有 CA 场景只传 **CA 公共证书的 SHA256 指纹**，不传服务器私钥。容器先无认证取回固定端点的公开 CA，核对指纹与证书合法性，再以严格 HTTPS 发送注册及心跳请求；指纹错误时不会发送令牌。公共 CA 场景使用系统信任。
- 复制后安装向导保持打开，真实收到注册/心跳后才更新状态；关闭页面不影响远程安装，重新打开不会回显旧凭证。
- 每个节点使用 UUID 命名的独立容器和身份卷。重复执行 `docker run` 遇到同名容器会由 Docker 拒绝，不自动覆盖或删除。已有节点重启请使用安装指导中的 `docker restart <容器名>`，不要再次安装。
- 未完成注册时，可重新生成安装命令；旧令牌随即失效。若旧命令已创建失败容器，先确认这是该节点且尚未注册，再移除该失败容器（**不删除身份卷**）并执行新命令。同一令牌的失败注册不会因自动重启重复提交；注册结果不确定时先检查平台状态，已注册但身份丢失可使用原节点“重新安装”。
- 已注册但离线时先查看原容器和网络，保留身份卷后重启即可继续使用原身份。需要安装新版或身份文件丢失时，可使用下方“重新安装”流程，保留节点记录并更换连接凭证。
- 吊销不可恢复。有未结束的测试时，必须确认请求停止；停止收尾期间报告可能不完整。只有已吊销且全部测试结束的节点才能删除。
- 删除只将节点移出管理列表，历史执行记录和报告保留；平台不会代替用户卸载远程容器或删除身份卷。

镜像初次下载可能较慢。下载完成但尚未注册、注册凭证已过期时，重新生成命令后复用已有校验通过的镜像；不要删除身份卷重装。编辑节点仅修改名称和网络位置，不重新发放凭证；网络位置只用于说明，不会自动配置网络。

### 保留原节点重新安装新版

管理员进入“节点管理”，点击原节点的“重新安装”。只需两步：

1. 等全部任务结束后，在 Docker 中停止并删除该节点的旧容器。无需删除旧身份卷，也无需先删除镜像。
2. 回网页勾选“我已停止并删除旧容器”，点击“生成新版安装命令”，复制一条 `docker run` 到原节点主机执行。

节点 UUID、名称、所属项目及历史执行记录保持不变。生成命令时原容器的连接凭证立即失效；新命令使用全新的身份卷重新注册，不能换回旧身份卷。页面不会代替用户删除远端资源。新容器使用页面的标准部署参数；原来有自定义网络或资源限制的，按实际需要重新配置。

排队、准备、执行或停止中的任务都会阻止重装；已吊销或删除节点不能重装。当前发行不可用时保留原身份，不生成无法执行的凭证。关闭弹窗不会撤销已完成的凭证轮换；尚未注册时可重新打开安装指导，重新生成命令。每次重新签发命令都会使用新的身份卷，旧卷不会自动删除。

新版在线后先重新进行单用户验证，再开始正式压测。同版本重装也需要重新验证，历史记录仍然可查。确认新版正常后，旧镜像和旧身份卷可按需要单独清理。

### 管理员首次配置镜像发布

网页 Docker 命令依赖已发布的双架构镜像索引，不能只配置域名。先在对应架构构建（或从可信构建机导入）镜像，固定命名：

```text
automation-platform-performance-node:0.4.1-amd64
automation-platform-performance-node:0.4.1-arm64
```

发布机需要能运行对应架构的镜像以核对安装包内的 Agent、Locust、协议和固定执行脚本。以下命令只生成归档，供审计或手工部署使用，**不能单独启用网页安装命令**；网页发行继续执行下一节的双架构公开发布：

```bash
backend/.venv/bin/python backend/scripts/publish_performance_node_release.py \
  --image amd64=automation-platform-performance-node:0.4.1-amd64 \
  --image arm64=automation-platform-performance-node:0.4.1-arm64 \
  --output backend/resource/performance-node-dockerhub-0.4.1
```

生成目录默认 `backend/resource/performance-node/`，包含 manifest 和受控镜像归档，已被 Git 忽略。同版本已发布镜像拒绝直接覆盖，修改引擎后应按版本契约重新发布。这个目录只放可公开的发行资产，禁止放 `.env`、身份文件、数据库备份、CA 私钥和符号链接。

#### 推荐：公开 Docker Hub 发行（仅限测试使用）

当前测试发行仓库：[martinmao9/performance-node](https://hub.docker.com/r/martinmao9/performance-node)。0.4.1 单条命令使用固定摘要的多架构索引，节点机器无需登录 Docker Hub。协议 2 旧节点（如 0.3.1、0.3.2）可继续管理心跳和显示升级提示，但不能接收协议 3 的执行任务；更早且不使用协议 2 的节点必须升级，不能依赖兼容心跳。0.4.0 节点可继续心跳并显示版本提示，但无法执行新版快照。旧节点按上方“保留原节点重新安装新版”流程手动更新至 0.4.1，完成单用户验证后才能加入运行。设计见 [单条 Docker 接入](../../docs/plans/2026-09-18-performance-direct-docker-install.md)和[多节点方案](../../docs/plans/2026-09-22-performance-multi-node.md)。

本次固定索引、匿名拉取、双架构启动、协议 3 和本机平台切换证据见 [0.4.1 发布验收](../../docs/verification/2026-09-23-performance-node-0.4.1-release.md)。

只发布 `performance-node/` 的独立客户端，不发布平台镜像或整个项目目录。Docker 构建上下文已使用白名单，仅允许节点 Python 源码、包配置、README 和 Dockerfile；不包含平台 `.env`、身份卷、日志、数据库和私钥。

1. 在发布机执行 `docker login`，在 Docker 官方网页登录。仓库命名使用 **Docker ID**，不能用邮箱；不要把密码或 Token 发到聊天、放进脚本或提交到 Git。
2. 在 Docker Hub 创建公开仓库，建议名为 `performance-node`，简介填写：**仅限测试使用 / For authorized testing only. Not for production.** 仓库完整介绍可使用 [DOCKERHUB.md](DOCKERHUB.md)。
3. 在项目根目录构建所需架构：

   ```bash
   docker buildx build --platform linux/amd64 --provenance=false --load \
     -t automation-platform-performance-node:0.4.1-amd64 performance-node
   docker buildx build --platform linux/arm64 --provenance=false --load \
     -t automation-platform-performance-node:0.4.1-arm64 performance-node
   ```

4. 把下方 `YOUR_DOCKER_ID` 替换成真实 Docker ID，显式启用公开发布：

   ```bash
   backend/.venv/bin/python backend/scripts/publish_performance_node_release.py \
     --image amd64=automation-platform-performance-node:0.4.1-amd64 \
     --image arm64=automation-platform-performance-node:0.4.1-arm64 \
     --output backend/resource/performance-node-dockerhub-0.4.1 \
     --registry docker.io/YOUR_DOCKER_ID/performance-node --publish-index
   ```

归档进入公开发行路径及推送仓库之前，会校验运行时版本并检查镜像的**所有层**（包括后来删除的文件）、私钥、访问令牌、本地平台敏感配置值及应用文件白名单。发现问题时拒绝发布，不打印匹配到的敏感值。多阶段构建还会剔除第三方依赖的测试目录（其中可能包含公开测试私钥样本），这些内容不会进入最终镜像的历史层。此检查是定向泄漏防护，不代替完整安全审计或依赖漏洞扫描。使用新的发行目录，不覆盖旧的同版本归档。

发布工具验证各架构远端摘要、配置与匿名拉取后写入 `registry_ref`；`--publish-index` 将两种已验证的镜像组成固定索引，验证索引及其两个平台均可匿名拉取后，才写入顶层 `registry_index_ref`。网页仅生成该索引的 Docker 命令，**不使用 `latest`，不因仓库失败自动降级或关闭 TLS**。平台公共 CA 只用于平台控制连接，不传给 Docker Hub。Docker Hub 在目标网络不可达或限流时仍可能下载失败，并不能保证所有网络都提速。

发布成功后，将下方 `PERFORMANCE_NODE_RELEASE_DIR` 改为新发行目录的绝对路径，并在没有活动压测时重启后端和性能控制器，确保两个进程同时加载 0.4.1、协议 3 和相同的固定执行模板。Caddy 使用更新后的白名单，允许 GET/HEAD 公共 CA 路径；若保留手工归档下载，Caddy 的发行目录也同步更新。改版后从网页重新生成命令，不使用之前复制的长 Shell 命令。

已有节点不会自动升级。需要安装新版时，使用上方“保留原节点重新安装新版”流程。平台显示 Agent 0.4.1、协议 3 且心跳正常后，逐节点重新运行单用户验证，再将节点加入多节点计划。重装已经轮换凭证，旧容器和旧身份卷不能直接恢复连接；旧执行记录不受影响。

新命令不接管旧安装器的容器、网络和身份卷。旧安装器中途失败时，先确认机器上的实际容器已经停止并移除：已注册节点使用“重新安装”，尚未注册节点使用“重新生成安装命令”。旧机器资源按归属单独清理，切勿删除不明身份卷。

后端实际 `.env` 增加（示例路径请替换）：

```dotenv
PERFORMANCE_NODE_PUBLIC_URL=https://load.example.com:18443
PERFORMANCE_NODE_RELEASE_DIR=/absolute/path/backend/resource/performance-node-dockerhub-0.4.1
# 私有 CA 场景填公共证书；可信公共 CA 场景留空。
PERFORMANCE_NODE_CA_CERT_FILE=/absolute/private/gateway/storage/pki/authorities/local/root.crt
```

启动 Caddy 时也设置同一个 `PERFORMANCE_NODE_RELEASE_DIR` 绝对路径；改完重启后端并按新环境启动网关。首次缺配置/双架构索引时，页面明确显示暂不可安装，而不是回退旧长脚本。

普通节点管理员只用网页向导。以下手动接入步骤保留用于维护和排障。

### 平台数据库迁移与回退

从 0.4.0 升级到 0.4.1 时，`performance_testing.0006` 仅给计划增加连接超时、读取超时及取值约束；现有计划初始化为 10 秒、30 秒，历史运行快照和报告不变。回退 0006 会删除新增的超时设置，须先备份这些值；回退时仍须恢复匹配的代码、发行目录和节点版本。

0.4.0 的 `performance_testing.0005` 会把旧 Run 执行数据回填到参与表，并删除 Run 上的旧单节点执行字段。该迁移明确不可逆，不能用 `migrate performance_testing 0004` 恢复旧结构或数据。

1. 在维护窗口先停止接收新运行，通过页面等待现有轮次结束或明确停止；排队轮次按升级原因取消，不能跨版本继续执行。确认没有活动性能运行后，在仓库根目录执行 `./platform stop`，再用 `./platform status` 确认后端、Celery、控制器和 Caddy 均已停止，避免迁移期间继续写入。
2. 使用组织既定工具创建数据库完整一致性备份并实际验证可恢复；同时记录当前 Git revision、发行目录和 `.env` 键名（不记录或提交值），保留旧平台代码和旧发行目录。备份必须包含性能运行、节点、验证与报告数据。
3. 使用待发布代码执行 `backend/.venv/bin/python backend/manage.py migrate performance_testing`，检查迁移记录包含 `0006`，并核对历史 Run 数、参与者、快照 SHA 和报告证据，以及现有计划的 10/30 秒默认值。跨过 0.4.0 升级时还须核对 0005 的参与者回填。迁移失败时保持入口关闭，不要反复执行、`--fake` 或手工拼回旧字段；MySQL/MariaDB 的部分 DDL 可能已经提交，应先检查实际结构，再从已验证备份恢复。
4. 配置 0.4.1 发行目录后执行 `./platform start` 和 `./platform status`，确认四项托管服务健康，再核对配置接口返回 Agent 0.4.1、协议 3，并逐节点升级和重新验证。

需要回退时，先再次关闭执行入口并停止全部新轮次，保留升级后产生的报告与审计证据；停止托管服务后恢复升级前的完整数据库备份，同时恢复匹配的旧代码、旧发行目录和节点镜像。备份之后产生的数据须另行决定保留或合并方式。涉及不可逆的 0005 迁移时，不能仅切换镜像或发行目录恢复旧平台。

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

另外只开放 GET/HEAD `/api/v1/performance-agent/install/ca.pem`（公开 CA）、保留的 `install/install.sh`，以及严格限定版本/架构的 `install/artifacts/<版本>/linux-<amd64或arm64>.tar.gz`。网页 Docker 命令只使用 CA 端点；归档仍可由 Caddy 静态分发并支持 Range，不经过 Django/ASGI 缓冲。不提供目录浏览，不公开 manifest 或任意资源文件。

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
docker build -t automation-platform-performance-node:0.4.1 /opt/automation-performance-node/source
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
  automation-platform-performance-node:0.4.1 enroll
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
  automation-platform-performance-node:0.4.1 run
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
  --entrypoint python automation-platform-performance-node:0.4.1 /app/smoke_fixture.py
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
./platform status
./platform logs controller --lines 100
./platform logs caddy --lines 100
```

重启节点前先确认没有正在运行的任务；活动任务因重启会停止，不会自动重放。不要删除 volume 来解决网络问题。

Caddy、控制器、后端和 Celery 由仓库根目录的 `./platform start/status/stop` 统一管理，管理器会拒绝重复托管或端口冲突。管理器仍**未配置开机自启**；Mac 重启后执行 `./platform start`，并用 `./platform status` 核对四项服务，不要再手工并行启动 Caddy 或 `run_performance_controller`。节点容器配置了 `unless-stopped`，依赖 Docker 服务正常启动；手动停掉的容器需要手动启动。

公网链路通过不等于容量或长期稳定性通过。Ubuntu20.04 宿主机的安全维护状态、Docker/内核升级、公网入口限流、日志保留、进程守护和多节点容量仍需分别验收；不要因短时低负载通过而宣称生产可用。
