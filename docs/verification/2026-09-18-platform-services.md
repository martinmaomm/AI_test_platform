# 本机统一服务入口验收

日期：2026-09-18。运行环境：macOS、项目 `backend/.venv`。

## 范围

新增 `./platform start/status/stop/restart` 与 `logs SERVICE --lines N`，管理后端 ASGI、Celery solo、性能控制器、节点 Caddy 网关。保持四个独立子进程，仅统一操作与生命周期管理。

未修改测试业务代码、数据库结构、节点容器、证书或前端；没有运行压测、模型调用、迁移或主动启动 Beat。前端仍独立运行。

## 自动回归

| 检查 | 结果 |
| --- | --- |
| `backend/.venv/bin/python -W error::ResourceWarning backend/scripts/test_platform_services.py` | 19 项通过 |
| `backend/.venv/bin/python backend/scripts/test_platform_service_config.py -v` | 10 项通过 |
| 在 `backend` 执行 `.venv/bin/python scripts/test_webui_generation_offline.py scripts.test_performance_controller` | 7 项隔离测试通过 |
| Python 编译、`sh -n platform`、`git diff --check` | 通过 |

管理器覆盖：重复启动、单/多服务重启顺序、停止后不复活、退避上限、过期 PID、未纳管手工进程、占用端口、原管理器遗留进程身份、配置重读、环境不落盘、连接中断/超时、Celery 优雅停止超时保留身份。控制器回归使用隔离数据库和 mock，不是真实压力运行。

## 本机实际验证

1. 切换前确认压测活动数为 0，Celery 的 active/reserved/scheduled 均为空；四个手工实例已通过健康探针。
2. 原 Caddy 启动参数原先仅存在进程环境；将域名、监听端口/地址、证书存储路径持久化到本地 `.env`。修改前保存私有本地备份，未提交配置值或备份。
3. 严格核对原进程 PID、创建时间、工作目录与完整命令后，按控制器、Celery、Caddy、后端顺序发送 SIGTERM；未强杀其他进程。
4. 首次切换发现 macOS 无权限枚举系统连接时，备用 bind 检测误判 TCP TIME_WAIT 为监听占用。已按原环境恢复旧服务，复现并修复为 SO_REUSEADDR 检测（不用 SO_REUSEPORT）；新增测试同时验证真实监听仍被拒绝、TIME_WAIT 不再阻止重启。
5. 再次切换成功，四个托管进程均健康：API HTTP 响应、Celery 队列心跳、控制器数据库心跳、网关原 CA 严格 TLS 校验。
6. 顺序及两个并发 `start` 均不改变管理器/子服务 PID，未产生重复实例。
7. `stop controller`、`start controller`、`restart controller` 实测通过；其他三个服务 PID 不变。
8. 节点 CA 文件 SHA256 未变，已有压测记录 ID/状态未变；两个原在线节点均有切换后的新心跳，无需重新安装。
9. 日志入口能读取控制器日志；Vite 根路径返回 200、经 Vite 代理的后端鉴权接口 GET 返回预期 405。
10. 验收结束后保留四项托管服务与原前端运行。

## 边界

- 只验证本机服务管理与现有节点心跳；没有新增压力负载，也未重新安装公网/内网节点。
- 自动退避及进程身份边界由隔离 dummy 进程测试验证；没有在真实业务运行中注入进程崩溃。
- Celery 心跳是队列级探针，不表示某个 PID 一定空闲；solo 忙时可能无回应，不能据此强杀。
- 管理器需保持运行才能自动恢复退出的子服务；机器重启后仍需手动执行 `./platform start`，不提供开机自启。
- 日志在服务下次启动前按 20 MiB 阈值轮转并保留 5 份旧文件，不是长期运行中的实时大小上限。
- Linux 通过兼容接口设计，但本轮没有在 Linux 上运行管理器验收；生产部署仍需系统级服务与日志管理。
