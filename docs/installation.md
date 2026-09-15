# 安装、配置与运行指南

本文是AI测试平台（AI Testing Platform）的安装与运行手册；项目能力与 UI / API 业务流程见 [README](../README.md)。命令以 macOS/zsh 为主，也可作为 Linux 部署的参考。示例仅使用本机地址和占位符，不包含实际部署凭据。

> 核对日期：2026-09-08。现有开发环境为 macOS 26.6.2、Python 3.13.11、Node.js 26.7.0、npm 11.19.0；Python 包元数据记录 Django 4.2.25、PyMySQL 1.2.0。这是当前环境记录，不是跨平台兼容性承诺或最低版本声明。本次仅整理文档并核对代码，没有重做全新环境安装或真实服务联调。

推荐阅读顺序：准备数据库与 Redis → 安装依赖与配置 `.env` → 迁移 → 安装浏览器（仅 UI 需要）→ 启动服务 → 页面配置 → 最小验证。已有项目升级请先看第 11 节，不要覆盖现有 `.env`。

## 1. 组件与运行边界

| 组件 | 当前实现 | 作用 |
| --- | --- | --- |
| 后端 | Django 4.2.25、Channels、Uvicorn | HTTP API、JWT、WebSocket、管理后台与媒体文件开发路由 |
| 数据与队列 | MySQL/MariaDB + PyMySQL、Redis | Django 数据、缓存、Celery broker/result backend、Channels layer |
| 异步任务 | Celery 5.3.4，当前推荐 `--pool=solo` | AI 生成、接口运行、WebUI 执行、知识库、通知、计划任务 |
| 前端 | Vue 3、Vite 5、Element Plus、Axios | 管理页面；开发服务器默认 5173 |
| WebUI 浏览器 | Node Playwright MCP 与 Python Playwright 两套运行时 | 前者供 AI 页面探索；后者运行生成/保存的 Python 测试 |

按本指南从 `backend` 启动时，后端使用该目录的 `.env`。LLM、MCP、RAG 配置由登录用户在页面中保存到数据库，而不是写进 `.env`。不要把密钥提交到仓库或对外分享；当前开发日志不能当作已全面脱敏。

## 2. 前置条件

准备以下可用资源后再开始：

- Python 3.13：当前 `requirements.txt` 已在现有 macOS Python 3.13 虚拟环境中使用；新平台的二进制依赖兼容性仍需自行验证。
- Node.js 与 npm：前端存在 `frontend/package-lock.json`（lockfile v3），应使用 npm 安装。
- 一个已经规划好备份、字符集和访问控制的 MySQL 或 MariaDB 实例，以及一个 Redis 实例。
- 可供前端访问的后端地址；跨主机时还需要反向代理正确转发 HTTP 与 WebSocket。
- 若启用 WebUI AI 页面探索：可执行的 Node/npx、浏览器目录，以及在页面中配置的 LLM 与 Playwright MCP。

当前依赖文件的关键事实如下：

- `backend/requirements.txt` 固定 `Django==4.2.25`、`playwright==1.62.0`，Pillow 使用范围 `>=11.0.0,<13`；`PyMySQL`、`pytest` 等未锁定版本。新环境解析的依赖可能变化；需要可重复发布时应另外记录并审核实装版本。
- `config/__init__.py` 调用 `pymysql.install_as_MySQLdb()`，因此当前数据库引擎使用 PyMySQL，不需要为此路径安装 `mysqlclient`。
- Node MCP 与 Python Playwright 的浏览器不能混用，详见第 6 节。

## 3. 获取代码与建立 Python 环境

以下命令从仓库根目录开始。路径含空格时请自行加引号。

```bash
cd /path/to/automation-platform
cd backend
python3.13 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python --version
python -m pip check
```

建议将虚拟环境保留在 `backend/.venv`；启动 Django、Celery、Python Playwright 安装和测试时都使用同一环境。若 Python 3.13 在新机器上无法解析或编译某个依赖，先记录完整错误、Python/CPU/系统版本，再决定调整解释器或依赖版本；不要在生产机器上临时删改锁定条目来“装到能跑”。

> 目录迁移注意：根目录从 `aits_v2` 改为 `automation-platform` 后，现有虚拟环境中的解释器入口可能仍指向旧绝对路径；请重建虚拟环境或逐一检查入口。VSCode 与 Codex 也应重新打开新目录。目录改名不要求、也不应手动改动现有数据库名或 `DB_NAME`；继续使用同一数据库并按正常迁移流程核对即可。
>
> 旧版本升级还需同步本地 `.env`：环境变量已去掉 `AITS_` 前缀，例如 `LLM_TIMEOUT_SECONDS`、`API_BROWSER_DISCOVERY_ENABLED`，旧变量名不再读取。已有生成草稿的内部标记、API 规范元数据及绝对日志路径应先备份并核对，再做定向更新；不要对整个数据库做字符串替换，也不要把历史执行日志当作配置重写。Git 历史和历史验收文档保留当时的名称与路径。

## 4. 安全初始化数据库、Redis 与 `.env`

### 4.1 创建应用库与账户

由数据库管理员在目标 MySQL/MariaDB 上创建**独立数据库和仅限本应用库的账户**。以下是需要替换尖括号内容的示例；密码应由密码管理器生成，使用受控管理会话执行，不要将实际密码留在共享命令记录或仓库：

```sql
CREATE DATABASE <database_name>
  CHARACTER SET utf8mb4
  COLLATE utf8mb4_unicode_ci;

CREATE USER '<app_user>'@'<application_host>'
  IDENTIFIED BY '<generated_secret>';

GRANT ALL PRIVILEGES ON <database_name>.*
  TO '<app_user>'@'<application_host>';
```

迁移需要在该数据库内创建和调整表结构；如果组织策略不允许 `ALL PRIVILEGES`，请由 DBA 授予 Django 迁移所需的同等库级 DDL/DML 权限。不要复用 root、跨项目共享账户或把数据库暴露到公网。

确认 Redis 使用独立库/实例，并为跨主机部署启用访问控制、网络隔离与 TLS/认证策略（如组织环境支持）。不要把 Redis、MySQL 或模型服务直接暴露给互联网。

### 4.2 创建 `.env`

`backend/.env` 已被 Git 忽略。仅在文件不存在时复制模板，再用本地编辑器修改；不要在工单中粘贴完整 `.env`：

```bash
cd /path/to/automation-platform/backend
test -f .env || cp env.example .env
chmod 600 .env
```

`env.example` 只是起始模板，**不能原样复制后直接运行**：其中“SQLite 默认”注释已过期，而且模板没有列出当前数据库连接所需的五个 `DB_*` 键。复制后必须手工补全以下 `DB_NAME`、`DB_USER`、`DB_PASSWORD`、`DB_HOST`、`DB_PORT`；占位符必须替换为实际值，示例不提供真实主机、密码或密钥。

```dotenv
# 本机开发示例；仅在可信测试网络使用
DEBUG=True
DJANGO_SECRET_KEY=<use-a-long-random-secret>
ALLOWED_HOSTS=localhost,127.0.0.1,::1
FRONTEND_BASE_URL=http://127.0.0.1:5173
# 可选；未设置时使用 FRONTEND_BASE_URL
# SITE_URL=http://127.0.0.1:5173

# MySQL/MariaDB；当前 settings.py 固定使用 django.db.backends.mysql，
# 没有 DB_ENGINE 环境变量或 SQLite 切换路径
DB_NAME=<database_name>
DB_USER=<app_user>
DB_PASSWORD=<generated_secret>
DB_HOST=<database-host>
DB_PORT=3306

# Redis 同时用于缓存、Celery broker/result backend 与 Channels
REDIS_URL=redis://<redis-host>:6379/<isolated-db-number>

# 遥测关闭开关
ANONYMIZED_TELEMETRY=FALSE
MCP_USE_ANONYMIZED_TELEMETRY=false

# 两套 Playwright 浏览器目录，详见第 6 节
MCP_PLAYWRIGHT_BROWSERS_PATH=.playwright-browsers
PYTHON_PLAYWRIGHT_BROWSERS_PATH=.python-playwright-browsers
```

局域网访问时，将实际使用的主机名 / IP 加入 `ALLOWED_HOSTS`，逗号之间不加空格；把 `FRONTEND_BASE_URL` 改为收件人能访问的**前端地址**，不要填后端 8000 端口。`REDIS_URL` 如需密码，可采用 `redis://:密码@主机:端口/库号`，特殊字符须 URL 编码。

生产环境必须使用独立随机密钥、`DEBUG=False`、明确的允许主机和 HTTPS；关闭 `DEBUG` 后 Django 不再代替生产服务器托管开发静态资源，详见第 5 节。不要沿用模板中带空格的 `*` 或默认开发密钥。

完成配置后，在**尚未启动任何工作进程**的 shell 中先做不发起模型/浏览器请求的 Django 检查：

```bash
cd /path/to/automation-platform/backend
source .venv/bin/activate
python manage.py check
```

若通过，再运行已有迁移并创建第一个管理员。不要在部署机上运行 `makemigrations`，也不要用清库或重建库代替迁移。

```bash
python manage.py migrate
python manage.py createsuperuser
```

### 4.3 环境变量分类与超时边界

| 类别 | 键/位置 | 当前语义与重启边界 |
| --- | --- | --- |
| 安全与浏览器来源 | `DEBUG`、`DJANGO_SECRET_KEY`、`ALLOWED_HOSTS`、`FRONTEND_BASE_URL`、`SITE_URL` | Django 导入时读取；变更后重启 ASGI、worker，计划任务场景同时重启 Beat。生产必须关闭 `DEBUG`、使用 HTTPS 与随机密钥。 |
| 数据与队列 | `DB_NAME`、`DB_USER`、`DB_PASSWORD`、`DB_HOST`、`DB_PORT`、`REDIS_URL` | 当前已确认的数据库键仅为这五个；Django、Celery、Channels 共用相关连接配置。变更后重启 ASGI、worker、Beat。先验证网络与权限，再迁移。 |
| 遥测 | `ANONYMIZED_TELEMETRY`、`MCP_USE_ANONYMIZED_TELEMETRY` | 关闭 Chroma/mcp-use 匿名遥测；重启 ASGI 与 worker 后生效。 |
| 单次模型请求 | `LLM_TIMEOUT_SECONDS`，或 LLM 页面配置的 `extra_config.timeout` | 代码未设置时默认 600 秒；`env.example` 显式写 300 秒。传给模型客户端的请求等待配置，不等于整个任务时限；流式请求还受客户端连接 / 读取超时语义影响。 |
| API 单场景执行时限 | `API_GENERATION_TIMEOUT_SECONDS` | 默认 1800 秒，代码最低 60 秒；从 worker 开始处理计时，覆盖该场景的生成、修复和试运行，不含排队。超时不撤销已经发到远端的写请求。 |
| API 整批执行时限 | `API_GENERATION_BATCH_TIMEOUT_SECONDS` | 默认 7200 秒；覆盖整批规划及串行子场景，与单场景时限同时生效。 |
| API 排队时限 | `API_GENERATION_QUEUE_TIMEOUT_SECONDS` | 默认 1800 秒；尚未被 worker 领取时单独计时，不消耗执行预算。修改这三项后重启后端和 Celery。 |
| WebUI 探索总时限 | `WEBUI_EXPLORATION_TOTAL_TIMEOUT_SECONDS` | 默认 600 秒，代码限制在 60–1800 秒；只覆盖探索阶段。 |
| 项目知识库任务总时限 | `KNOWLEDGE_TOTAL_TIMEOUT_SECONDS` | 默认 1200 秒，代码限制在 30–1500 秒；从 worker 开始处理计时，不含排队。`PROJECT_KNOWLEDGE_ENABLED=false` 会关闭新任务提交。 |
| Celery 任务限制配置 | `CELERY_TASK_TIME_LIMIT`（源码，30 分钟） | 与业务总时限独立；当前 solo 池在主进程内执行，不能依赖此配置保证强制中断。仍需各流程的业务超时和执行器中止机制。 |
| MCP JSON | Playwright MCP 的 `timeout` | 若未设置，预检代码赋值为 30；它是 MCP 调用设置，不是 LLM 或 Celery 总时限。 |

不要把 `env.example` 中的每个键都视为已接入配置：`FILE_UPLOAD_MAX_MEMORY_SIZE` 和 `DATA_UPLOAD_MAX_MEMORY_SIZE` 当前在 `settings.py` 中固定为 10 MiB，未读取同名环境变量。前者是上传文件的内存存储阈值，后者是请求数据的内存限制，不能统称为所有模块的文件上传上限；例如项目知识库有自己的 50 MiB 文件校验。单改这两个 `.env` 键不会生效。

## 5. 前端安装、开发代理与生产边界

前端必须按 lockfile 安装：

```bash
cd /path/to/automation-platform/frontend
npm ci
npm run dev
```

前端命令保持前台运行，后续安装与后端启动操作请另开终端。开发页面地址为 `http://127.0.0.1:5173`；若端口被占用，以 Vite 输出为准。

`vite.config.js` 当前监听 `0.0.0.0:5173`，开发代理仅包含：

- `/api/v1` → `http://127.0.0.1:8000`
- `/ws` → `ws://127.0.0.1:8000`（WebSocket）

前端 Axios 基址是 `/api/v1`，WebSocket 使用当前浏览器 host 的 `ws://`/`wss://` 加 `/ws/...`。因此开发时应从同一 Vite 地址访问页面，而不是让浏览器直接拼接一个未代理的后端地址。

构建生产静态资源：

```bash
cd /path/to/automation-platform/frontend
npm run build
# 产物：frontend/dist/
```

当前仓库没有现成的 Docker / Compose 或生产反向代理、进程守护配置。生产部署还需配置 HTTPS、`/api/v1` 的 HTTP 反向代理、`/ws` 的 Upgrade/WebSocket 转发、`dist/` 托管和 SPA 路由回退，以及合适的 `ALLOWED_HOSTS`、`FRONTEND_BASE_URL`、CORS/CSRF 来源。使用 Django 管理后台时还需 `collectstatic` 和 `/static/` 托管；不要公开挂载日志或整个媒体目录。这些不是 `npm run build` 自动完成的内容。

生产反向代理若直接托管 `MEDIA_ROOT`，必须先拒绝执行截图子目录，再配置其余公开媒体；不能依赖 Django 的开发 media guard。Nginx 至少应使用大小写不敏感的精确目录边界，且 deny 规则必须位于通用 `/media/` alias 之前：

```nginx
location ~* ^/media/webui_failure_screenshots(?:/|$) {
    return 404;
}

location /media/ {
    alias /absolute/path/to/automation-platform/backend/media/;
    autoindex off;
    disable_symlinks on from=/absolute/path/to/automation-platform/backend/media/;
}
```

不要在公开媒体目录中创建指向 `webui_failure_screenshots` 或其他私有目录的符号链接。执行截图只通过 `/api/v1/projects/<project_id>/web-testing/executions/.../screenshot/` 的受控报告接口读取；该接口随报告公开，无需登录，但校验执行关联和文件路径。修改反向代理配置后，用自有夹具分别验证原始路径、大小写变体、URL 编码路径和符号链接别名均为 404，再验证报告截图接口可读，而不是扫描历史截图目录。

此外，当前 Vite `allowedHosts` 列了项目原开发域名。换用其他自定义域名时，在 `frontend/vite.config.js` 的 `server.allowedHosts` 中添加该域名并重启 Vite；不要改成无条件放行。直接使用 IP 的访问不能简单按这份域名名单判断。旧资料若提到 Vite 代理 `/playwright-reports`，与当前代码不一致：当前只代理 `/api/v1` 和 `/ws`，原生报告使用平台路由与接口。

## 6. 两套 Playwright 浏览器运行时

WebUI AI 探索使用 **Node Playwright MCP**；实际运行 Python 测试使用 **Python Playwright**。两者版本和浏览器缓存必须隔离：

| 用途 | 版本/目录 | 配置 |
| --- | --- | --- |
| Node MCP | `@executeautomation/playwright-mcp-server@1.0.12`，Node Playwright 1.57.0，Chromium build 1200 | `MCP_PLAYWRIGHT_BROWSERS_PATH=.playwright-browsers` |
| Python 执行器 | `requirements.txt` 中 `playwright==1.62.0` | `PYTHON_PLAYWRIGHT_BROWSERS_PATH=.python-playwright-browsers`，留空则使用 Python Playwright 系统默认缓存 |

### 6.1 Node MCP 浏览器

固定 MCP 配置在页面“AI 配置 → MCP 配置”中保存；Playwright 条目必须固定版本并以 stdio 方式由 `npx` 启动：

```json
{
  "mcpServers": {
    "playwright": {
      "command": "npx",
      "args": ["-y", "@executeautomation/playwright-mcp-server@1.0.12"]
    }
  }
}
```

在线安装（仅在允许下载时执行）：

```bash
cd /path/to/automation-platform/backend
PLAYWRIGHT_BROWSERS_PATH="$PWD/.playwright-browsers" \
  npx -y playwright@1.57.0 install chromium
```

离线下载链接、平台/CPU 匹配、解压目录、`INSTALLATION_COMPLETE` 标记和离线注册步骤统一见 [浏览器离线资源说明](../backend/resource/README.md)。ZIP 不随 Git 提交，需要自行准备；离线浏览器 ZIP 也不包含 npm 包，完全离线运行前还须准备指定版本的 MCP / Playwright npm 缓存。不要把 macOS 浏览器复制给 Linux/Docker，需匹配实际运行 Celery 的系统与 CPU。

### 6.2 Python Playwright 浏览器

Python 侧使用不同目录：

```bash
cd /path/to/automation-platform/backend
source .venv/bin/activate
PLAYWRIGHT_BROWSERS_PATH="$PWD/.python-playwright-browsers" \
  python -m playwright install chromium
```

然后在 `.env` 填写：

```dotenv
PYTHON_PLAYWRIGHT_BROWSERS_PATH=.python-playwright-browsers
```

不要使用通用的 `PLAYWRIGHT_BROWSERS_PATH` 同时指向两套目录。Python 执行器会在子进程中移除可能遗留的 Node MCP 通用路径，避免从临时 pytest 工作目录错误解析旧浏览器。变更 MCP 或 Python 浏览器路径后重启 Celery worker；Node MCP 浏览器路径只传给 MCP 子进程。

Linux 还可能需要浏览器共享库。在相应机器上为对应版本执行 `playwright install-deps chromium`（Python 侧用 `python -m playwright install-deps chromium`，Node 侧用 `npx -y playwright@1.57.0 install-deps chromium`）；该操作可能需要管理员权限，不要在 macOS 照搬 Linux 的系统包命令。

## 7. 启动顺序与可选 Beat

以下命令会启动本地/目标环境服务，应在 `.env`、数据库、Redis、迁移和浏览器目录准备完毕后执行。不同终端都先进入 `backend` 并激活同一 `.venv`。

1. 启动 ASGI 后端（HTTP 与 WebSocket）：

   ```bash
   cd /path/to/automation-platform/backend
   source .venv/bin/activate
   python run_asgi.py
   ```

   当前 `run_asgi.py` 绑定 `0.0.0.0:8000`。请使用主机防火墙、私网或反向代理限制来源；不要将开发服务器裸露在公网。

2. 在另一个终端启动单 worker：

   ```bash
   cd /path/to/automation-platform/backend
   source .venv/bin/activate
   celery -A config worker --loglevel=info --pool=solo
   ```

   `--pool=solo` 使**这个 worker 进程**一次只执行一个任务；同一队列上的多个 worker、其他主机或其他队列仍可能并发，因而它不是全平台或外部系统的严格串行保证。不要在未评估幂等、并发和外部写入风险前增加 worker 数量。

3. 只有启用“项目计划任务”自动到点执行时，才在第三个终端启动**一个** Beat：

   ```bash
   cd /path/to/automation-platform/backend
   source .venv/bin/activate
   celery -A config beat --loglevel=info --logfile=logs/celery-beat.log
   ```

   同一数据库只运行一个 Beat。Beat 会派发已启用的计划任务，启动前先核对计划、测试套件、环境和执行时间。状态、暂停及防重复执行边界见 [计划任务说明](scheduled-tasks.md)。保持上面的默认启动命令，不要附加旧的 `--scheduler django_celery_beat.schedulers:DatabaseScheduler`，否则会绕过项目的健康心跳调度器。

修改边界总结：

- 修改 `.env`、数据库、Redis、`FRONTEND_BASE_URL` 或安全来源：重启 ASGI、worker；使用计划任务时再重启 Beat。
- 修改 MCP/Python 浏览器目录：至少重启 worker；Node MCP 在每次任务中由 worker 发起。
- 修改 Vite 开发代理：重启 `npm run dev`；生产前端变更需要重新构建并由外部静态服务器发布。
- 修改页面中的 LLM/MCP/RAG 配置：它们存于数据库。保存或“测试连接”可能对模型/MCP/向量服务发起外部请求，使用测试账号与最小权限，不要把测试成功误称为业务或被测网站验收。

## 8. 首次页面配置

完成 `createsuperuser` 并能登录后，在“AI 配置”页面按实际需要配置：

1. **LLM 配置**：填写提供商、模型名、服务地址、密钥和可选 `extra_config`。仅启用需要使用的配置；模型配置按用户归属存储。UI 探索使用的模型还须支持工具调用；当前统一使用流式请求，提供商也需支持。先使用供应商允许的测试项目验证连接。
2. **MCP 配置**：首次添加上一节的固定 Playwright MCP JSON 并启用。全平台只保留一份配置，JSON 中也只能有一个 `playwright` 服务；已有配置只能编辑、启停和检测，不支持继续添加或删除。后续版本升级请编辑现有配置。保存后点击“刷新工具”，确认显示“已发现 X 个工具”；“尚未检测”不代表零工具，检测失败会显示原因及检测时间。刷新只读取工具清单，不执行浏览器操作，也不改变启用状态。预检要求 `@executeautomation/playwright-mcp-server` 使用明确版本、`npx` stdio 启动；不要添加端口模式，也不要把 token 写进日志或截图。
3. **可选 RAG 配置**：选择 Chroma 或 Milvus、embedding 模型、存储位置/连接信息并设置活动/默认配置。RAG 不是 API 或 WebUI 基础运行的必要条件；启用项目知识库前还应确认 embedding 模型下载、磁盘、内存、向量库备份和资料外发策略。项目知识库的资料处理、权限、回退与验收边界见 [项目知识库指南](project-knowledge-guide.md)。

模型、MCP、向量库连接成功只证明对应连接路径可用，不证明生成结果质量、目标网站权限、真实浏览器流程或外部写操作安全。

### 可选：邮件通知

通知方式仅保留邮件。管理员在 **首页 → 系统配置 → 邮件服务配置** 添加 SMTP 并测试连接，再进入 UI/API 项目的 **邮件通知** 添加接收组、确认发送测试邮件，最后在计划任务中选择接收组。测试连接不发信；SMTP 接受也不等于实际收信。详细字段、加密方式、启用规则及验收步骤见 [邮件通知指南](email-notifications.md)。

### 最小验证清单

先确认基础服务，再使用自己的测试站点做一小段真实业务验证；不要一开始就要求覆盖所有接口或整站。

- 在 `backend/.venv` 下执行 `python -m pip check`、`python manage.py check`，检查迁移是否全部应用：`python manage.py showmigrations`。
- Redis 检查只做 PING，不读写业务键：

  ```bash
  cd /path/to/automation-platform/backend
  source .venv/bin/activate
  python -c 'import os, redis; from dotenv import load_dotenv; load_dotenv(); print("Redis PING:", redis.Redis.from_url(os.environ["REDIS_URL"]).ping())'
  ```

- 浏览器安装后可做本地空白页检查，以下不调用模型或被测网站（npx 首次可能下载 npm 包）：

  ```bash
  cd /path/to/automation-platform/backend
  source .venv/bin/activate
  mkdir -p logs
  PLAYWRIGHT_BROWSERS_PATH="$PWD/.playwright-browsers" \
    npx -y playwright@1.57.0 screenshot --browser=chromium about:blank logs/playwright-node-smoke.png
  PLAYWRIGHT_BROWSERS_PATH="$PWD/.python-playwright-browsers" \
    python -m playwright screenshot --browser=chromium about:blank logs/playwright-python-smoke.png
  ```

  使用自定义缓存目录时同步替换路径。两张空白截图只验证对应浏览器能启动，不等于 MCP 探索成功。
- 登录前端，确认模型列表、项目页面正常；提交任务后观察排队、处理中和最终状态，确认 worker 能消费任务。
- UI：描述一个可控的短流程，生成、调试、保存、重新执行，核对日志和截图；运行中可能产生测试数据。
- API：先选少量接口并包含所需登录依赖，确认 Base URL 后生成并验证，核对请求、提取和断言，再保存和重跑。
- 计划任务：仅在需要时启用一条可控任务，确认 Beat 状态和实际报告；有心跳不代表 worker 或测试业务已通过。
- 报告/通知：用未登录窗口打开报告链接，确认详情、子用例和截图可读，且地址不是其他设备无法访问的 localhost；通知发送需另行确认接收对象。当前报告公开，不能放入不希望被链接接收者看到的真实凭据或敏感业务内容。

## 9. 日志、媒体与诊断产物

确保应用账户对以下目录可创建/写入，但不要把它们作为可公开下载目录：

| 位置 | 内容 |
| --- | --- |
| `backend/logs/django.log` | Django 根日志 |
| `backend/logs/celery.log` | Worker 日志；当前配置按 20 MiB 轮转，保留 5 个备份 |
| `backend/logs/celery-beat.log` | 仅在按第 7 节指定 `--logfile` 启动 Beat 时产生 |
| `backend/logs/playwright-mcp/<uuid>.log` | WebUI AI 探索的 MCP 诊断日志 |
| `backend/temp/playwright-mcp/<uuid>/screenshots` | MCP 探索截图；用于诊断，可能包含测试数据 |
| `backend/media/webui_failure_screenshots/execution_<id>/` | Python WebUI 执行的结束/失败截图；平台执行详情通过带权限校验的截图接口读取 |

日志、上传资料、截图、生成脚本和报告可能含页面内容、测试账号或内部接口信息。设置访问控制、保留期与备份策略；排查时按最小范围读取，禁止将整个目录公开映射到静态 Web 根目录。

## 10. 常见问题与排查顺序

| 现象 | 先检查什么 | 不要做什么 |
| --- | --- | --- |
| `manage.py check` 或迁移无法连接数据库 | `DB_*`、数据库监听/防火墙、账户的库级权限、`utf8mb4` | 不要改用 SQLite；当前代码不是 SQLite 默认。 |
| worker 收不到任务、WebSocket/页面状态异常 | `REDIS_URL`、Redis 连通性、ASGI 与 worker 是否都在运行 | 不要把 Redis 配成多个不一致的 broker/cache/channel 地址。 |
| Vite 页面 API 或 WS 失败 | 从 5173 同源访问、代理仅有 `/api/v1` 与 `/ws`、8000 是否可达 | 不要把 JWT 或 API 密钥写进 Vite 源码。 |
| MCP 找不到 Chromium | MCP 固定版本、`.playwright-browsers`、`MCP_PLAYWRIGHT_BROWSERS_PATH`、worker 是否已重启 | 不要让 Node MCP 使用 Python 1.62 浏览器目录。 |
| Python 测试找不到浏览器 | `PYTHON_PLAYWRIGHT_BROWSERS_PATH` 与 Python 1.62 的安装目录 | 不要复用 Node MCP build 1200 缓存。 |
| 计划任务不自动执行 | Beat 是否仅一个实例、worker 是否运行、任务是否启用、时区与 Cron | 不要因无心跳就把“未执行”判成“已通过”。 |
| 模型请求提前超时 | 区分单次 LLM、API pipeline、WebUI 探索、知识库任务和执行器限制 | 不要只提高一个超时就假定所有阶段都会延长。 |

## 11. 升级与回退

网页探索发现 API 是新增的可选来源，默认关闭。其增量迁移、独立超时配置、MCP 要求和启用步骤见 [网页探索发现 API](api-browser-discovery.md)；不需要重新安装一套浏览器，也不改变原有 Swagger 执行链路。

升级前备份 MySQL/MariaDB、Redis（按组织策略）、`backend/media/`、需要保留的日志/报告与浏览器目录清单；记录当前 Git revision、`.env` 键名（不记录值）、ASGI/worker/Beat 进程版本。

先评审目标提交、依赖差异和迁移内容。已有历史迁移包含结构调整及旧流程数据清理，不能假设任意跨版本升级均不影响数据。

维护窗口内：先停唯一 Beat、防止新调度，暂停用户提交；等待当前 worker 任务完成，再正常停止 worker 与 ASGI。不要让旧 worker 在新 Schema 上继续运行，也不要未经核对就强杀可能已发生远端写入的任务。

随后切到已审核的提交再安装与迁移；下面的 `REVIEWED_COMMIT` 必须替换为实际提交号，切换前保存好本地未提交改动：

```bash
cd /path/to/automation-platform
git fetch
git switch --detach REVIEWED_COMMIT

cd backend
source .venv/bin/activate
python -m pip install -r requirements.txt
python manage.py check
python manage.py migrate

cd ../frontend
npm ci
npm run build
```

发布前端产物，先启动 ASGI、worker 并执行第 8 节的最小验证，最后恢复唯一 Beat 和用户提交。当前项目未提供 `/api/v1/health`，不要把不存在的路径纳入部署脚本。

`pip install -r requirements.txt` 不会卸载已经从清单移除的依赖；需要干净环境时另建 venv 验证后替换，不要随意卸载仍被其他流程使用的包。若 Python Playwright 版本改变，要为新版本重新安装对应浏览器。

回退时先停止新服务，再按已评审方案恢复匹配的代码、数据库及必要文件。迁移是否可逆取决于实际 migration；反向迁移不能保证找回已删除数据。不要直接在新 Schema 上启动旧代码，也不要把删库、删媒体或清空 Redis 当作常规回退方式。历史模块文档中的回退版本只适用于其当次变更，不可作为所有版本通用命令。

## 12. 当前文档与配置差异

- `backend/env.example` 仍写“SQLite 开发环境默认”，但当前 `backend/config/settings.py` 固定为 MySQL backend 并读取 `DB_NAME`、`DB_USER`、`DB_PASSWORD`、`DB_HOST`、`DB_PORT`；没有 `DB_ENGINE` 环境变量可切换引擎，`config/__init__.py` 以 PyMySQL 适配 MySQLdb。本指南以代码为准。
- `env.example` 中的 `FILE_UPLOAD_MAX_MEMORY_SIZE` / `DATA_UPLOAD_MAX_MEMORY_SIZE` 目前不会被 settings 读取；代码固定为 10 MiB，其内存限制语义与各模块文件大小校验不同。
- `env.example` 为 `LLM_TIMEOUT_SECONDS` 写了 300 秒，而未设置该键时模型管理器代码默认 600 秒；显式 `.env` 值优先。
- 仓库当前没有可直接投入生产的反向代理、TLS、静态站点或进程守护配置；部署这些基础设施前应先完成安全评审。
