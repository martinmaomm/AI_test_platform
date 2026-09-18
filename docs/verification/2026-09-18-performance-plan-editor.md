# 压测计划编辑器 B 方案验收

## 本轮范围

- 移除性能工作区内部重复的横向菜单和阶段说明，保留左侧导航与应用页签。
- 压测计划采用宽抽屉、分区配置、左侧步骤列表与右侧请求编辑。
- 目标可按名称和地址搜索；打开编辑器、回到缓存页面时重新加载，并区分加载、空列表、失败与重试。
- 支持步骤增删、复制、调整顺序，Query/Headers 键值编辑、URL 预览、无请求体/JSON 切换、JSON 格式化和行列错误提示。
- 修改未保存时关闭需确认；加载目标或保存期间阻止重复提交。独立请求序号防止同项目的旧响应覆盖最新目标列表。

后端与节点协议保持不变：Query 编码到 `path`，步骤仍只有 `name/method/path/expected_status/headers/body`。没有新增变量提取、动态表达式、复杂断言、表单上传或其他请求体协议。

## 验证

- 前端单元回归：367 项通过。
- 前端生产构建通过，保留既有大分块提示。
- 后端性能模块：临时 SQLite、禁止外部网络的 71 项测试通过，无模型迁移。
- 新增 `backend/scripts/test_performance_plan_browser.py`：真实 Chrome + 生产构建 Vue + Django API + 一次性 SQLite。
  - 初始无目标；切换到目标页创建隔离目标，再回到缓存的计划页，目标立即可选。
  - 真正 POST 保存、PATCH 修改、重新打开核对；重复 Query、中文、空值和 JSON 原始类型保留。
  - 复制、排序、删除步骤时保留编辑内容；无修改直接关闭，有修改提示，取消关闭仍保留草稿。
  - 非法 JSON 不提交，目标刷新不清空草稿；加载失败禁用保存，重试恢复。
  - 延迟返回的旧空列表不会覆盖新目标列表。
  - 桌面与 900×720 窗口检查，保存按钮可见，长 URL 自动换行；深色模式等待主题过渡完成后核对背景、文字与截图。
  - 保存结果通过真实节点快照验证器；没有创建运行记录，浏览器无未捕获错误。

复测命令：

```bash
cd frontend
npm run test:unit
npm run build
cd ../backend
.venv/bin/python scripts/test_webui_generation_offline.py performance_testing
.venv/bin/python scripts/test_performance_plan_browser.py
```

浏览器脚本输出截图至 Git 忽略的 `backend/temp/performance-plan-browser/`，临时数据库在退出时清理。

## 边界

本轮没有请求被测网站、发起负载、改动真实项目计划、修改节点或发布镜像。新文件中的后端脚本仅用于验收；运行时改动均在前端，无须数据库迁移、重启后端/Celery或重新安装节点。开发模式刷新网页即可体验。
