# WebUI 描述直接驱动网址与独立脚本

## 已批准范围

整个 UI 自动化链路取消测试环境依赖：生成、草稿调试与保存、单用例及套件执行。API 测试与共享项目环境记录不变。生成页面删除环境选择、Base URL、起始相对路径及独立登录信息表单。

- 创建输入：description、可选 module_id、model_config_id、exploration_timeout_seconds。
- 描述是目标网址和登录信息的唯一来源。单个 HTTP(S) 网址自动采用，多个网址须单独一行明确 `目标网址：完整网址`；缺失或歧义返回输入错误，绝不猜测 `/` 或 localhost。
- target_url 保留完整路径、查询参数和 hash。生成脚本直接使用完整 URL，不依赖 pytest/base_url 或 PLAYWRIGHT_BASE_URL。
- 账号密码保留在原始描述中供模型理解，不再结构化抽取、缓存或要求单独补充登录表单；保留“请勿使用生产账号”的测试凭据提醒。
- 保留项目权限、脚本版本/乐观锁、真实断言计数、截图、运行选项和可选变量；变量优先级为本次覆盖 > 套件 > 用例。
- 脚本调试标记只跟脚本版本、变量与本次目标匹配，不再受共享环境编辑影响。套件内各脚本可访问各自的完整网址。

## 数据与迁移

对 web_testing 模型移除生成/执行记录的环境关联和生成记录中的路径、独立凭据状态字段，target_url_safe 重命名为 target_url。定时任务共享模型的 environment 改为可空，WebUI 定时任务不再选环境，API/App 定时任务在接口层仍必须选择环境。不做旧相对路径脚本转换，不删除共享 Environment 记录，也不自动执行 NAS 数据库迁移。历史脚本需要重新生成或手工改为完整网址。

本次不夹带页面可见性识别或增量保存保障改造；这些是此前已定位的独立问题。

## 验收

1. 全新无环境项目可创建生成任务，描述中深层路径、参数及 hash 原样传到 MCP 和 Python。
2. 没有网址、多个未明确入口的网址返回清楚的输入错误；无环境/凭据字段的前端请求可用。
3. 草稿调试、保存、用例和套件不读取项目环境；变量优先级仍正确，修改草稿后旧通过标记失效。
4. 本地受控 HTTP fixture 用完整 URL 回放成功，不注入 base_url；失败截图和真实断言计数继续有效。
5. 后端离线回归、迁移状态一致性检查、前端测试与构建通过；API 环境相关代码与数据不变。

### 2026-09-04 本地验收结果

- `backend/.venv/bin/python scripts/test_webui_generation_offline.py`：272 项通过；使用临时 SQLite、内存缓存，禁止联网，不连接 NAS 数据库。迁移状态检查无差异。
- 前端 `npm run test:unit`：40 项通过；`npm run build` 通过，仍有现存的大 chunk 提示。
- `backend/.venv/bin/python scripts/verify_webui_without_environment.py`：本地 Chromium 单用例、不同站点的两个脚本、故意失败的截图均通过；首次网址的路径、查询参数和 hash 未丢失。
- 未调用真实模型、未操作用户测试网站、未执行部署库迁移、未重启后端或 Celery。实际 AI 探索与生成质量仍需部署后验证。

## 部署与回退

停用旧 worker 后由用户依次运行 `python manage.py migrate scheduled_tasks` 和 `python manage.py migrate web_testing`，再重启后端及 Celery。迁移删除字段，回滚代码不能恢复已删列的旧值；需要保留旧字段数据时应在迁移前备份数据库。不自动重启服务或修改开发者现有测试数据。
