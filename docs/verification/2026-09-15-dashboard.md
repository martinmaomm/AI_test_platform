# 首页固定布局与统计修复验收

## 变更与口径

- 删除自定义布局开关、拖拽缩放及布局本地存储读写，首页使用固定响应式布局；普通用户不显示配置入口，性能/App 入口保持禁用，且性能在 App 前。
- API 未保存草稿的失败记录原先都按空用例 ID 合并为“用例#None”。现在按用例/工作区身份分组，使用用例标题或执行快照名称；已保存工作区可关联回用例。没有稳定身份的 UI 草稿按执行编号区分，不按通用执行名称猜测它们是同一用例。
- 今日执行数：按应用时区、执行创建日期统计已结束的顶层执行记录，套件计一次；包含调试/AI 验证。通过率及趋势按实际单用例和套件子用例结果统计，分母为 passed、failed、error；pending/running/skipped/incomplete 不当作失败。
- UI 范围改为执行记录的项目外键，包含尚未保存或源用例已删除的草稿执行。近 7 日包含今天，从应用时区当天零点划分，不依赖数据库时区表。
- UI AI 占比使用脚本来源；API 使用仍保留的 AI 候选采纳记录，不再把所有场景用例误算为 AI 用例。来源记录长期持久化的限制已记录到 `docs/TODO-OPT.md`。
- 项目清空后清除统计但保留模块入口；切换项目拒绝迟到的统计响应。项目下拉请求扩大为 1000 条（当前项目数远低于此上限）。统计异常返回 503 和中文提示，前端显示 `--` / 错误与重试，不伪装为零数据。

## 已验证

- `projects.test_dashboard`：11 项通过。临时 SQLite、内存缓存/队列、禁止非回环网络；覆盖 NULL 用例、快照名称、工作区保存前后合并、同名 UI 草稿、套件子结果、跨日/七日边界、项目隔离、AI 来源、401/403/404 和统计 503。
- `npm run test:unit`：312 项通过；`npm run build` 通过，仅有已有的大 chunk 提示；`git diff --check` 通过。
- `backend/.venv/bin/python backend/scripts/test_dashboard_browser.py`：真实 Vue + Django + Chrome，在临时 SQLite/回环网络中通过。覆盖旧布局不生效、25 个项目可选、灰色入口不可点击、切换迟到响应、503 重试、清空选项、普通用户配置入口隔离、无项目用户仍可进入模块、390px 窄屏布局。截图在 `backend/logs/dashboard-browser-check/`（不提交运行产物）。
- 真实数据库仅做读取复核，没有修改业务记录，没有发邮件、调用模型或执行测试网站操作。原 API 40 次失败被错误合并；修复后按场景呈现，当前失败最多的场景为 14 次，第二为 12 次。UI 草稿执行纳入趋势；当前 UI AI 占比 75%，API 可追溯 AI 占比 66.67%。这些是验收时快照，后续执行会自然变化。
- 确认 Celery active/reserved/scheduled 全为 0 后平滑重启后端与 worker；Beat 和前端未重启。重启后两项目三个统计接口均 200，今日汇总与趋势最后一天一致、失败榜无 `None`，Celery ping 返回 pong。

## 测试命令注意

不要直接对当前 NAS 配置运行 Django 建库测试。隔离运行方式：在 `backend` 目录中，先将 `scripts/test_api_workspace_browser.py` 的 `bootstrap(Path(temp_dir))` 与 `test_platform_reports_browser.loopback_only` socket 限制载入，再调用 `DiscoverRunner(...).run_tests(['projects.test_dashboard'])`。浏览器测试脚本已经内置该隔离配置。
