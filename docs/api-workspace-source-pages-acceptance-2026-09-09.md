# API 工作区来源页面验收（2026-09-09）

## 结果与范围

两类来源现在是独立路由页面，共用已验证的场景编辑和执行流程。数据库无需新增迁移，未删除既有工作区、规范、用例或执行记录。

本轮测试使用 macOS arm64、项目虚拟环境、隔离 SQLite、临时本地 Django 服务和真实 Chromium。第三方模型使用可控 fixture；未连接 NAS、Redis 或真实模型提供商。测试结论不等于第三方模型生成质量已经实测。

## 已通过

| 验证 | 结果 |
| --- | --- |
| API Django 回归 | 120 个测试通过，含来源序列化、来源过滤、权限、排序和多接口用例重开 |
| requests 运行时 | 30 个测试通过 |
| 前端单元测试 | 185 个测试通过 |
| 前端生产构建 | 通过 |
| 新来源页面浏览器验收 | 默认进入文档页、页内表单、来源隔离、未提交内容确认、关闭功能提示、禁用模型过滤通过 |
| 网页探索浏览器验收 | 进度、显式取消、部分证据、多样本、分页、50 接口组限制、交接和只读来源通过 |
| 路由与生命周期 | 切换不取消任务、离开后停止轮询、刷新来源不变、workspace/endpoint/case 深链通过 |
| 原工作区浏览器回归 | 生成、采用、调试、修复、保存、导出，以及输入保留和重复保存通过 |
| 本地真实 MCP 链路 | 17 次工具调用，采集 8 条记录、5 个端点；生成验证、两次新会话重跑及 Python 导出通过 |
| Git 差异格式检查 | `git diff --check` 通过 |

## 复现命令

在项目根目录执行：

```bash
backend/.venv/bin/python backend/scripts/test_webui_generation_offline.py api_testing
npm --prefix frontend run test:unit
npm --prefix frontend run build
backend/.venv/bin/python backend/scripts/test_api_browser_discovery_browser.py
backend/.venv/bin/python backend/scripts/test_api_workspace_browser.py
backend/.venv/bin/python backend/scripts/test_api_workspace_interactions_browser.py
backend/.venv/bin/python backend/scripts/test_api_browser_discovery_e2e.py
```

运行时测试在 `backend` 目录执行：

```bash
PYTHONPATH=apps PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 .venv/bin/python -m pytest -q apps/api_testing/test_requests_runtime.py
```

浏览器截图在本地 `backend/logs/api-browser-discovery-browser/` 和 `backend/logs/api-workspace-browser-check/`，不提交运行日志与截图到 Git。

## 本轮发现并修正的问题

- 来源不能依赖会被后台任务替换的 `generation.source`；改从根工作区的规范关联推导。
- 同一规范的多端点去重查询受默认排序影响；清除排序后再 `distinct()`。
- 多接口用例没有单一 `endpoint` 关联；重开时从脚本步骤恢复端点范围并重新执行来源权限校验。
- 全局 `keep-alive` 曾使离开的工作区继续监听、请求数据；仅两类来源页退出缓存，并按页面路径复用当前实例，其他页面缓存容器保持常驻。
- 数字输入组件的动态 ARIA 禁用状态未恢复；采用局部组件重建处理，数值保留，详见 [依赖问题跟踪](TODO-OPT.md)。

## 部署提示

发布同一版本的前后端并重启后端和 Celery；`npm run dev` 模式刷新浏览器即可。本次无需执行数据库迁移，也不需要重新下载 Playwright 浏览器。
