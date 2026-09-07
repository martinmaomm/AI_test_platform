# AI_test_platform

## API 对话工作区（requests）

入口：API 自动化 → API 对话工作区。上传 Swagger/OpenAPI 后选取相关接口和模型，用对话生成或修改当前草稿。接口文档只作为上下文，不需要开启 RAG 或下载向量模型。

1. 选择一份可用 Swagger 和接口范围（最多 50 个），点击“生成并验证”，确认目标地址和可选变量。确认后会发起真实接口请求，可能新增、修改或删除测试数据。
2. 平台先检查候选，再用 requests 顺序试运行；请求/提取结构错误可自动修复并复测，最多三轮。认证、权限、服务故障、超时或普通业务断言失败会停止自动重放，保留证据供人工处理。
3. 查看每轮请求、响应、提取、断言及修改摘要。只有全部步骤实际运行且断言通过才标记“已验证通过”；失败候选也可采用后手动编辑。点击“采用候选”才更新可视化草稿，再显式保存为测试用例，不会自动覆盖原用例。修改草稿后，旧验证记录不再代表当前版本通过。
4. Python 支持只读查看、复制、导出，不支持 Python 反向同步为可视化步骤。导出文件携带相同运行核心，安装 `requests` 后可 `python test_api.py` 运行，或安装 `pytest` 后执行 `python -m pytest test_api.py`。

仍可手动点击“显式调试执行”，失败后选择“修复并验证”。用例和套件复用同一 requests 核心；套件内顺序执行，各用例独立 Session，不互相泄漏提取值或 Cookie。`backend/env.example` 中的 `AITS_API_GENERATION_TIMEOUT_SECONDS` 控制整个生成及验证任务期限（默认 1800 秒），与单次模型请求的 `AITS_LLM_TIMEOUT_SECONDS` 分开；修改后重启后端及 Celery。

变量覆盖顺序：本次覆盖 > 套件变量 > 用例变量 > API 环境默认值。`${name}`、`$name` 和 `{{name}}` 支持完整值占位保留类型；嵌入字符串时转为文字。内置 `timestamp_ns`、`uuid4` 每次运行生成新值，例如 `username: "test_${timestamp_ns}"`，可以显式覆盖以复现一次运行。

脚本、环境和套件顺序在提交执行时冻结；运行中修改原用例不会改变当前任务。平台在子进程中执行结构化请求并限制总时长；导出脚本使用连接/读取超时及步骤间总时限检查。超时停止本地进程不代表远端写操作已撤销，请检查测试数据。请勿使用生产账号。

升级前备份数据库并停止旧后端/Celery，在 `backend` 虚拟环境中执行：

```bash
python manage.py migrate api_testing
```

然后重启后端和 Celery；前端 `npm run dev` 刷新即可，静态部署需重新构建。迁移新增工作区与执行快照，不删除既有数据库记录。旧 HttpRunner 任意代码钩子不再支持，不自动转换旧脚本；建议使用工作区重新生成。

实施与验收边界见 [API 生成、验证与有限修复 B 方案](docs/plans/2026-09-07-api-generate-verify-repair.md)。

## WebUI 脚本生成（当前流程）

在测试描述中直接写完整 HTTP(S) 网址、测试目标，以及按需填写的测试账号和密码。
UI 自动化不再选择测试环境、起始相对路径或独立登录信息；生成、调试、单用例和套件都执行脚本中的完整网址，不依赖 Base URL。API 测试的环境管理不受影响。

```text
目标网址：http://localhost:9990/#/users
使用测试账号 test_user、密码 test_password 登录，进入用户列表。
新增本轮唯一用户，验证列表显示，修改后验证，删除后确认不存在。
```

描述缺少网址会提示补充；有多个网址时，使用单独的“目标网址：”行明确入口。
路径、查询参数和 `#` 路由会保留。每个脚本仍可配置可选变量，覆盖顺序为：本次运行 > 套件变量 > 用例变量。
测试凭据可能保留在记录、日志、截图和脚本中，**请勿使用生产账号**。

本次升级先停止旧 Celery worker，在 `backend` 虚拟环境中执行下面两条迁移，再重启后端和 Celery：

```bash
python manage.py migrate scheduled_tasks
python manage.py migrate web_testing
```

旧的相对路径脚本不做自动转换，请重新生成，或手工改成完整网址。
迁移不删除 API 的共享环境数据；详细范围见 [描述直接驱动网址](docs/superpowers/specs/2026-09-04-webui-description-url-design.md)。

填写测试目标后，一个智能体在同一 Playwright MCP 会话中探索页面，并逐步保存完整 Python 草稿。平台记录操作证据、未完成步骤和终止原因，不再要求模型先提交 ScenarioPlan 或最终路径 JSON。

- 草稿通过静态检查后可以编辑、调试、保存到独立测试用例；草稿生成成功不等于测试通过。
- `AITS_PENDING_STEP` / `AITS_PENDING_ASSERTION` 表示待补充操作或断言，有这些标记不能显示为调试通过。
- 工作区按代码行号区分待补步骤和待补断言；模型未说明具体缺项时会明确提示人工核对，不推断某个业务操作失败。确认完成后移除对应标记并重新调试，单纯删除注释不代表已验证。
- 普通提示显示简短中文摘要，模型最终回复、原始输出和完整诊断在“技术信息”中按需展开；本地编辑后的旧检查行号不再作为当前结论展示。
- 超时、失败或取消后保留最近已保存的草稿；“基于轨迹整理脚本”只处理已有证据，不重新打开浏览器、不重复业务写入。
- 使用测试账号，请勿使用生产账号。部署步骤见上方迁移与重启说明。

设计和验收说明见 [脚本优先生成](docs/superpowers/specs/2026-09-04-webui-script-first-generation.md)。

当前维护入口是 `generation_orchestrator.py`（流程调度）、`script_exploration_agent.py`（连续探索并保存 Python）、`draft_quality.py`（草稿检查）和 `assertion_state.py`（待补充项与运行判定），均位于 `backend/apps/web_testing/`。
旧版 ScenarioPlan 整理器、JSON 回放编译器和对应的 AST 一致性检查已移除；探索轨迹与浏览器工具守卫中的共用逻辑仍保留，不要仅凭文件里的旧版本命名删除它们。

离线回归可在 `backend` 虚拟环境中运行 `python scripts/test_webui_generation_offline.py`：使用独立内存数据库、禁止网络访问，不触碰配置中的 MySQL、Redis 或被测网站。它不代替真实模型生成和浏览器业务流程验收。

## 平台原生执行报告

WebUI / API 执行结果和定时任务报告使用平台自己的页面，不再安装或生成 Allure 报告。
执行详情展示实际保存的统计、用例结果、日志与截图；可通过需要登录和项目报告权限的独立链接查看。
报告汇总不重新执行测试，API 定时任务也不会为了生成报告重复请求被测接口。

本次升级需停止旧后端和 Celery，安装依赖并执行 `python manage.py migrate` 后重启；原始执行记录、日志和截图保留。
旧脚本若手工使用了 Allure，请改用 Python 日志。详细范围、依赖卸载、验收与回退见 [平台原生执行报告](docs/platform-native-reports.md)。

## 项目计划任务

计划任务跟随当前项目选择套件，不再重复选择测试类型；UI 无环境配置，API 保留环境。多个套件按指定顺序执行，同一任务上一轮未结束时拒绝重复启动。
执行结束沿用平台报告及通知。自动定时运行需要额外启动一个 Celery Beat，只有 worker 不会到点派发。升级迁移、启停和验收说明见 [项目计划任务](docs/scheduled-tasks.md)。

## 项目知识库：手工用例与项目问答

UI 项目的“项目知识库”现提供“项目资料、手工测试用例、知识问答”三个页签。
它们使用独立任务、数据表和向量集合，不接入 Playwright MCP，不修改已有自动化脚本生成、调试或执行流程。

升级时先停止旧 Celery worker，在 `backend` 的虚拟环境中执行：

```bash
python -m pip install -r requirements.txt
python manage.py migrate project_knowledge
```

然后重启后端与 Celery。前端 `npm run dev` 模式刷新即可，生产环境重新构建并发布静态文件。
迁移只新增结构，不清空旧知识库、API 资料、脚本或向量模型。现有资料不会自动导入新工作区，请在新入口上传要使用的文件并点击“处理文档”。

- 资料处理：支持 TXT、Markdown、DOCX、文本 PDF、XLSX，单文件最多 50 MiB；保留章节及原文位置。先解析正文，再建立索引。
- 手工用例：选择主需求与章节，顺序生成草稿，可检查来源、编辑、保存和导出 XLSX。正文已解析但索引失败时，可明确选择“仅基于所选正文生成”。
- 项目问答：使用当前项目已启用且索引就绪的资料，回答带来源；没有依据或资料冲突时提示核对。聊天模型的回答不会自动变成新资料。
- 向量检索：沿用已启用的 RAG 配置与 embedding 模型，但使用独立 collection。若模型已在本机缓存，可复用缓存；首次加载仍会占用内存，未缓存时可能需要下载。

可选配置见 `backend/env.example`：

```dotenv
AITS_PROJECT_KNOWLEDGE_ENABLED=true
AITS_KNOWLEDGE_TOTAL_TIMEOUT_SECONDS=1200
```

知识任务总时限默认 1200 秒（允许 30–1500 秒），排队时间不计入；每次模型请求仍遵守模型配置的 `timeout` 或 `AITS_LLM_TIMEOUT_SECONDS`。
关闭功能开关会禁止新写入与提交，但仍可查看历史、取消本人任务；不会删除资料或影响原 API RAG / WebUI 自动化。

操作、测试和回退说明见 [项目知识库使用与验收](docs/project-knowledge-guide.md)。

## 部署依赖

项目的 WebUI AI 脚本生成和 Python 测试执行分别使用两套 Playwright 运行时。两套浏览器版本可以共存，但不要只安装其中一套。

### 1. Python 环境

在 `backend` 目录创建并激活虚拟环境后安装依赖：

```bash
cd /path/to/aits_v2/backend
python3.13 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

生成的 Python Playwright 脚本由 Python 虚拟环境执行，因此还需要安装 Python 侧浏览器：

```bash
python -m playwright install chromium
```

Python 测试执行使用独立环境变量。留空时使用 Playwright 的系统默认缓存目录：

```dotenv
PYTHON_PLAYWRIGHT_BROWSERS_PATH=
```

如需把 Python 浏览器也放进项目目录，应使用与 MCP 不同的目录：

```bash
export PLAYWRIGHT_BROWSERS_PATH="$PWD/.python-playwright-browsers"
python -m playwright install chromium
```

并配置：

```dotenv
PYTHON_PLAYWRIGHT_BROWSERS_PATH=.python-playwright-browsers
```

### 2. Node.js Playwright MCP

当前项目使用的 MCP 配置固定为：

```json
{
  "mcpServers": {
    "playwright": {
      "command": "npx",
      "args": [
        "-y",
        "@executeautomation/playwright-mcp-server@1.0.12"
      ]
    }
  }
}
```

该 MCP 包内部使用 Playwright `1.57.0`，因此必须安装对应的 Chromium。建议将浏览器缓存放在项目目录中，便于部署和迁移：

```bash
cd /path/to/aits_v2/backend
export PLAYWRIGHT_BROWSERS_PATH="$PWD/.playwright-browsers"
npx -y playwright@1.57.0 install chromium
```

安装后可以检查目录是否存在：

```bash
test -d "$PLAYWRIGHT_BROWSERS_PATH/chromium-1200" && echo "Chromium 安装成功"
```

为了让 Celery 每次启动都使用项目内的浏览器目录，可以将下面一行加入 `backend/.env`：

```dotenv
MCP_PLAYWRIGHT_BROWSERS_PATH=.playwright-browsers
```

程序会将相对路径按 `backend` 根目录解析，并只传给 Node MCP 进程。修改 `.env` 后需要重启 Celery Worker。

### 3. 平台和版本注意事项

- `@executeautomation/playwright-mcp-server@1.0.12`、Node Playwright `1.57.0` 和 `chromium-1200` 要保持匹配。
- Python Playwright 可以单独升级，例如 `1.62.0`；它只影响 Python 测试执行，不会改变 Node MCP 的浏览器版本。
- 不要再使用通用的 `PLAYWRIGHT_BROWSERS_PATH` 同时配置两套运行时；分别使用 `MCP_PLAYWRIGHT_BROWSERS_PATH` 和 `PYTHON_PLAYWRIGHT_BROWSERS_PATH`。
- 浏览器二进制与操作系统、CPU 架构相关。macOS 下载的浏览器不能直接复制到 Linux NAS 使用，应在实际运行 Celery 的服务器上安装。
- 浏览器目录体积较大且平台相关，项目只保留安装说明，不提交到 Git；`backend/.playwright-browsers/` 已加入忽略列表。

### 4. 启动 Celery

```bash
cd /path/to/aits_v2/backend
source .venv/bin/activate
celery -A aits_backend worker --loglevel=info --pool=solo
```

Celery 日志会写入：

```text
backend/logs/celery.log
```
