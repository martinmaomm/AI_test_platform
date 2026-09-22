# 保留原节点重新安装验收

用户选择继续使用原节点以保留历史。当前流程改为：停止并删除旧容器，在原节点的“重新安装”弹窗确认后生成一条新版 Docker 安装命令。UUID、名称、项目、网络位置、创建时间与历史运行保留；新命令签发时立即失效旧连接凭证，使用新身份卷完成注册。

## 后端与前端

- 隔离 SQLite 下性能模块 109 项测试通过；覆盖严格确认、管理员权限、所有活动运行阻断、事务失败保留原身份、迟到旧凭证拒绝、历史保留及同版本重装后重新验证。
- 前端 `performanceWorkspace.test.js` 与 `performanceMultiNodeRender.test.js` 共 24 项通过，生产构建通过，保留既有大包提示。
- 最终补充验证状态展示与本次注册时间一致，`test_runs` 24 项再次通过；同版本重装后旧验证列表和详情均为已失效，新验证通过后恢复资格。
- Django 系统检查通过，没有数据库迁移变化。无需重新发布 Agent 镜像，继续使用已发布 Agent 0.4.0 / 协议 3。

```bash
cd backend
.venv/bin/python scripts/test_webui_generation_offline.py performance_testing
cd ../frontend
node --test tests/performanceWorkspace.test.js tests/performanceMultiNodeRender.test.js
npm run build
```

## 真实浏览器与 API 联动

`backend/scripts/test_performance_node_reinstall_browser.py` 使用临时 SQLite、真实 Django API、编译后的 Vue 与 Chrome，只允许 loopback 网络。发行文件采用测试 fixture，运行时源码来自当前代码。

验收通过：勾选后才能提交、提交时禁止关闭与重复点击、注入失败时保留原身份、成功返回单条 Docker 命令、新卷与原 UUID 关联、历史接口内容保持一致、实际注册和心跳后弹窗变为在线。GET 不回显注册凭证或命令，注册凭证不写入浏览器存储。外部请求 0、真实节点变更 0、启动压测 0。

```bash
backend/.venv/bin/python backend/scripts/test_performance_node_reinstall_browser.py
```

截图输出在 Git 忽略目录 `backend/temp/performance-node-reinstall-browser/`：`reinstall-guide.png`、`new-install-command.png`。命令截图已遮盖一次性凭证。

## 真实 Docker

使用已发布 0.4.0 的 amd64、arm64 本地缓存镜像及临时 TLS 平台 fixture，在本机 Colima 完成同节点重装：旧容器注册并心跳后删除容器，保留旧身份卷；同 UUID 使用新注册凭证、新卷启动并心跳成功；旧卷内容不变；新容器重启不重复注册。两个架构均通过。arm64 的正常启动、错误 CA、拒绝注册三个原有场景也通过。

```bash
backend/.venv/bin/python backend/scripts/verify_performance_docker_start.py --image automation-platform-performance-node:0.4.0-arm64 --case reinstall
backend/.venv/bin/python backend/scripts/verify_performance_docker_start.py --image automation-platform-performance-node:0.4.0-amd64 --architecture amd64 --case reinstall
```

测试资源通过独立所有权标签清理，不操作现有节点容器、真实平台注册或业务目标。

## 实际启用边界

当前仅更新平台页面及重装接口，远端设备仍由用户手动操作。生成新命令会使旧连接凭证失效；注册完成后须重新做单用户验证。自定义网络与资源限制需要按实际环境重新设置，旧镜像可在新节点确认正常后按需清理。

切换前只读基线：活动运行 0、历史运行 11 条，`unraid` 与 `osjp` 仍为 Agent 0.3.2 / 协议 2。真实节点凭证与历史内容仅计算摘要校验，不写入验收文档。三个浏览器 ZIP 不提交。
