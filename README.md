# AI_test_platform

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
