# 性能模块第一批验收：计划管理与节点接入

日期：2026-09-16。范围见 [第一批契约](../plans/2026-09-16-performance-phase1-node-access.md)。

## 已完成

- 首页性能入口可进入项目；工作区提供计划、节点、目标管理，明确标注尚不能发起压测。
- 新 `performance_testing` app：权限、受控目标、结构化请求计划、节点身份、一次性登记和心跳；不引入 Celery 长任务或 Run 空壳。
- `performance-node/` 独立 Python/非 root Docker 包，HTTPS、证书校验、禁止重定向、有限请求重试、身份持久化和空闲心跳。
- 主审修正了前端管理员/项目权限混淆、启动速率误标 RPS、复制失败即关闭凭证、项目切换残留表单、后端 JSON 编码默认值、心跳响应契约、项目删除关联限制及 Docker 状态目录权限。

## 验证证据

| 检查 | 结果与边界 |
| --- | --- |
| 前端 `npm run test:unit` / `npm run build` | 336 项通过，构建通过；仍有已有 bundle 大小警告 |
| Chromium 页面操作 | 目标→计划→节点新增、计划参数提交、凭证弹窗清理通过，0 页面脚本错误；API 全部模拟，不写业务库 |
| 新模块及跨包接入 | 21 项通过，隔离 SQLite；包含真正的 Agent→本机 HTTPS→Django 注册、心跳、重启身份复用、重放拒绝、吊销失效 |
| 相关原有功能回归 | 94 项通过：项目权限、首页、APP 退役、用户管理、配置/依赖、通知、计划任务环境；未发送真实邮件或运行模型 |
| 节点独立测试 | 20 项通过：URL/TLS、CA、重试、协议、状态、权限、并发无覆盖落盘、停止等 |
| Docker | 本机 Colima Linux arm64 镜像构建通过；断网、只读根目录、无 capabilities、非 root UID 10001 容器冒烟通过，状态目录 0700 |
| Locust/stunnel 原型 | 授权后安装 stunnel 5.80；1 VU、5 秒，本机临时服务 5 请求/0 失败，Master 与服务端计数一致，停止后 0 新请求；缺客户端证书和错误服务器名称均拒绝 |
| 当前 MariaDB 迁移 | `performance_testing.0001_initial` 成功，仅新增 3 张表和新表约束；原有 RAG 条件唯一约束警告仍存在 |
| 服务重启 | 确认 active/reserved/scheduled 均为 0 后正常重启后端和 Celery；8000 监听、Worker ready |
| 实际 HTTP 冒烟 | 未认证 heartbeat 返回 401，缺字段 enroll 返回 400；3 张新表可查询，均为空，未创建业务测试数据 |

本机此时没有性能项目，因此没有把实际业务库上的计划 CRUD 称作已验收；其正反向功能由隔离后端与浏览器模拟分别覆盖。旧全量 UI/API 测试夹具问题仍见 [技术债](../TODO-OPT.md)，不宣称全仓所有测试全绿。

## 复现命令

在 `backend/`、其虚拟环境下：

```bash
python scripts/test_webui_generation_offline.py performance_testing scripts.test_performance_agent_contract
python scripts/test_webui_generation_offline.py projects.test_permissions_scheme_a projects.test_dashboard projects.test_app_retirement users.test_management common.test_configuration_cleanup common.test_dependency_contract notifications scheduled_tasks.test_environment_contract
python scripts/verify_performance_ui.py
python scripts/verify_performance_transport.py --confirm-local-load
```

页面冒烟需本机 Vite 5173 和已安装的 Python Playwright Chromium；所有 API 被拦截模拟。传输原型需 stunnel，仅允许固定本机目标，不提供改为业务网站的参数。临时测试进程结束后自动退出，不注册系统常驻服务。

节点测试在 `performance-node/` 执行：

```bash
PYTHONPATH=src ../backend/.venv/bin/python -m unittest discover -s tests -v
docker build -t automation-performance-node:0.1.0-local .
```

## 尚未完成

- 平台发起/停止真实压测、Master/Worker 生命周期、跨节点统计/报告。
- 正式节点 mTLS 证书与每轮准入；管理 HTTPS 发布配置。
- 远程 Linux、公网/NAT、amd64、多节点及失联停止的完整执行验收。

未访问 NAS/商城发压，未配置远程节点或开放公网端口；本机原型不代表上述能力已交付。
