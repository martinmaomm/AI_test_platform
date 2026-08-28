# Playwright 1.57.0 浏览器离线资源

本目录用于保存 WebUI AI 脚本生成所需的 Playwright 浏览器离线安装包。

当前 MCP 配置固定使用：

```text
@executeautomation/playwright-mcp-server@1.0.12
```

该 MCP 包依赖 Node.js Playwright `1.57.0`，对应版本如下：

| 组件 | 版本或构建号 | 项目内安装目录 |
| --- | --- | --- |
| Chromium | `143.0.7499.4` / build `1200` | `.playwright-browsers/chromium-1200` |
| Chromium Headless Shell | `143.0.7499.4` / build `1200` | `.playwright-browsers/chromium_headless_shell-1200` |
| FFmpeg | build `1011` | `.playwright-browsers/ffmpeg-1011` |

浏览器压缩包只需下载与部署机器操作系统、CPU 架构匹配的一组。不同平台的文件不能混用。

## 官方下载地址

以下地址来自 Playwright `1.57.0` 安装器使用的官方 CDN。

### macOS ARM64（Apple Silicon）

- [Chromium](https://cdn.playwright.dev/dbazure/download/playwright/builds/chromium/1200/chromium-mac-arm64.zip)
- [Chromium Headless Shell](https://cdn.playwright.dev/dbazure/download/playwright/builds/chromium/1200/chromium-headless-shell-mac-arm64.zip)
- [FFmpeg](https://cdn.playwright.dev/dbazure/download/playwright/builds/ffmpeg/1011/ffmpeg-mac-arm64.zip)

下载地址中使用的是 `chromium-*.zip`，但 CDN 保存到本地时可能显示为 `chrome-*.zip`。当前机器实际使用的文件名以本目录中的 `chrome-mac-arm64.zip` 和 `chrome-headless-shell-mac-arm64.zip` 为准。

### macOS x64（Intel）

- [Chromium](https://cdn.playwright.dev/dbazure/download/playwright/builds/chromium/1200/chromium-mac.zip)
- [Chromium Headless Shell](https://cdn.playwright.dev/dbazure/download/playwright/builds/chromium/1200/chromium-headless-shell-mac.zip)
- [FFmpeg](https://cdn.playwright.dev/dbazure/download/playwright/builds/ffmpeg/1011/ffmpeg-mac.zip)

### Linux x64

- [Chromium](https://cdn.playwright.dev/dbazure/download/playwright/builds/chromium/1200/chromium-linux.zip)
- [Chromium Headless Shell](https://cdn.playwright.dev/dbazure/download/playwright/builds/chromium/1200/chromium-headless-shell-linux.zip)
- [FFmpeg](https://cdn.playwright.dev/dbazure/download/playwright/builds/ffmpeg/1011/ffmpeg-linux.zip)

### Linux ARM64

- [Chromium](https://cdn.playwright.dev/dbazure/download/playwright/builds/chromium/1200/chromium-linux-arm64.zip)
- [Chromium Headless Shell](https://cdn.playwright.dev/dbazure/download/playwright/builds/chromium/1200/chromium-headless-shell-linux-arm64.zip)
- [FFmpeg](https://cdn.playwright.dev/dbazure/download/playwright/builds/ffmpeg/1011/ffmpeg-linux-arm64.zip)

### Windows x64

- [Chromium](https://cdn.playwright.dev/dbazure/download/playwright/builds/chromium/1200/chromium-win64.zip)
- [Chromium Headless Shell](https://cdn.playwright.dev/dbazure/download/playwright/builds/chromium/1200/chromium-headless-shell-win64.zip)
- [FFmpeg](https://cdn.playwright.dev/dbazure/download/playwright/builds/ffmpeg/1011/ffmpeg-win64.zip)

## 当前 Mac ARM64 离线安装

当前开发机是 macOS ARM64。把下载好的三个压缩包放在本目录，并保持下面的文件名：

```text
chrome-mac-arm64.zip
chrome-headless-shell-mac-arm64.zip
ffmpeg-mac-arm64.zip
```

其中 `chrome-mac-arm64.zip` 已经放入本目录，并已确认其 Chromium 版本为 `143.0.7499.4`。

进入后端目录：

```bash
cd  backend
```

创建项目内的 Playwright 浏览器缓存目录：

```bash
mkdir -p \
  .playwright-browsers/chromium-1200 \
  .playwright-browsers/chromium_headless_shell-1200 \
  .playwright-browsers/ffmpeg-1011
```

解压三个离线包：

```bash
unzip -q resource/chrome-mac-arm64.zip \
  -d .playwright-browsers/chromium-1200

unzip -q resource/chrome-headless-shell-mac-arm64.zip \
  -d .playwright-browsers/chromium_headless_shell-1200

unzip -q resource/ffmpeg-mac-arm64.zip \
  -d .playwright-browsers/ffmpeg-1011
```

设置可执行权限，并写入 Playwright 的安装完成标记：

```bash
chmod +x \
  ".playwright-browsers/chromium-1200/chrome-mac-arm64/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing" \
  ".playwright-browsers/chromium_headless_shell-1200/chrome-headless-shell-mac-arm64/chrome-headless-shell" \
  ".playwright-browsers/ffmpeg-1011/ffmpeg-mac"

touch \
  .playwright-browsers/chromium-1200/INSTALLATION_COMPLETE \
  .playwright-browsers/chromium_headless_shell-1200/INSTALLATION_COMPLETE \
  .playwright-browsers/ffmpeg-1011/INSTALLATION_COMPLETE
```

## 配置后端环境变量

在 `backend/.env` 中增加：

```dotenv
MCP_PLAYWRIGHT_BROWSERS_PATH=.playwright-browsers
PYTHON_PLAYWRIGHT_BROWSERS_PATH=
```

`MCP_PLAYWRIGHT_BROWSERS_PATH` 会被解析为 `backend/.playwright-browsers` 的绝对路径，并且只传递给 Node MCP 进程。`PYTHON_PLAYWRIGHT_BROWSERS_PATH` 留空时，Python Playwright 使用操作系统默认缓存目录；如果需要项目内缓存，请使用 `.python-playwright-browsers`，不要与 MCP 共用目录。

Python Playwright 浏览器可按当前虚拟环境版本单独安装：

```bash
PLAYWRIGHT_BROWSERS_PATH="$PWD/.python-playwright-browsers" \
  .venv/bin/python -m playwright install chromium
```

对应配置为：

```dotenv
PYTHON_PLAYWRIGHT_BROWSERS_PATH=.python-playwright-browsers
```

修改 `.env` 后需要重启 Celery Worker：

```bash
celery -A aits_backend worker --loglevel=info --pool=solo
```

## 验证安装结果

先验证 Chromium 本身：

```bash
".playwright-browsers/chromium-1200/chrome-mac-arm64/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing" \
  --version
```

预期输出中包含：

```text
143.0.7499.4
```

手动解压不会生成 Playwright 使用的 `.links` 注册信息。先执行一次离线安装命令完成注册；由于浏览器目录和 `INSTALLATION_COMPLETE` 标记已经存在，该命令不会重新下载浏览器：

```bash
PLAYWRIGHT_BROWSERS_PATH="$PWD/.playwright-browsers" \
  npx --offline -y playwright@1.57.0 install chromium
```

命令显示“未先安装项目依赖”的警告可以忽略。当前后端并不是 Node.js Playwright 项目，`npx` 只用于启动固定版本的 Playwright MCP 和登记浏览器缓存。

注册完成后，再让 Playwright `1.57.0` 检查项目内缓存：

```bash
PLAYWRIGHT_BROWSERS_PATH="$PWD/.playwright-browsers" \
  npx --offline -y playwright@1.57.0 install --list
```

如果本机还没有缓存 `playwright@1.57.0` 的 Node.js 包，可以在注册时去掉 `--offline`，首次允许 `npx` 获取该包：

```bash
PLAYWRIGHT_BROWSERS_PATH="$PWD/.playwright-browsers" \
  npx -y playwright@1.57.0 install chromium
```

最后重启 Celery，在“AI 脚本实验室”生成一条简单脚本进行验证。

## 注意事项

- 仅把 ZIP 放进 `resource` 目录不会自动完成安装，必须解压到上述固定目录并创建 `INSTALLATION_COMPLETE` 标记。
- `chrome-mac-arm64.zip` 只适用于 Apple Silicon Mac，不能用于 Intel Mac、Linux、Windows 或 Docker Linux 容器。
- 三个离线包应来自同一组 Playwright `1.57.0` 构建，不要与其他 Playwright 版本的浏览器文件混用。
- 浏览器文件体积较大，不建议直接提交到普通 Git 仓库；需要纳入版本管理时可使用 Git LFS 或独立制品存储。
