# 性能测试第二批验收：单节点执行

日期：2026-09-16。基于上一批 `d6d4652` 继续实施。压测范围仅本机临时 HTTP 服务，每轮 1 VU、最多 5 秒；没有向 NAS、商城或外部目标发压。

## 已交付

- 运行冻结快照、项目级幂等提交、全局串行排队、停止和执行记录。
- 独立控制器 → 每轮 mTLS → Agent → 固定 Locust Worker；Django/Celery/Agent 主进程不加载 gevent。
- 每轮 CA、节点身份与随机握手绑定；原始 RPC 只监听回环地址，固定脚本摘要与节点版本必须一致。
- 独立监督进程执行租约和最长运行时间；正常结束/停止回收子进程组，节点重启不重放旧任务。
- 请求数、失败数、累计 RPS、错误率、平均响应、P95/P99、接口明细及采样趋势。
- 项目执行/报告权限分离；轮换或吊销节点会中止任务；项目未结束运行会在文件清理前阻止项目删除。
- 配置不完整或控制器未在线时页面提示并禁止创建运行；HTTP 内网页面支持安全随机 UUID 后备实现。

## 实测结果

| 检查 | 结果 | 证明范围 |
| --- | --- | --- |
| 后端综合隔离回归 | 78/78 通过 | 性能接口、租约 fencing、版本、权限/幂等/删除隔离、项目/用户邻接回归及本机链路 |
| Node 独立回归 | 40/40 通过 | 租约失效/超时回收真实本机子进程，重启防重放、TLS、固定源/快照校验及秘密清理 |
| 前端单测 | 343/343 通过 | 状态、权限、UUID、停止确认与轮询作用域等 |
| 前端生产构建 | 通过 | 存在既有大 chunk 提示，不是构建失败 |
| 浏览器操作 | 通过，0 pageerror | API 全部模拟；目标/计划/节点新增、控制器状态轮询、创建运行、延迟确认停止、详情与记录跳转 |
| Node Docker | 构建及冒烟通过 | Colima Linux arm64，非 root UID10001、协议2、Locust2.43.3、stunnel4及0700状态目录 |

真实本机测试使用临时 SQLite 数据库和 HTTPS Django 服务，经过真实 Agent、stunnel、Master/Worker，目标服务实际收到的请求数与平台最终计数逐项相同：

| 场景 | 目标收到 / 最终统计 | 失败请求 | 平台状态 |
| --- | --- | --- | --- |
| 正常运行 | 11 / 11 | 0 | completed，最终统计完整 |
| 手动停止 | 2 / 2 | 0 | cancelled，最终统计完整 |
| 故意设置错误响应码预期 | 11 / 11 | 11 | completed，失败率100%，不冒充请求成功 |

每轮重复提交相同 request_id 返回原记录，不二次发压；终态后目标计数不再增加。结束后检查没有残留 Master/Worker/监督进程。本机租约故障测试使用无压测的临时进程树，不等同于真实跨网断线故障注入。

浏览器测试特别移除 `crypto.randomUUID`，验证内网 HTTP 的安全随机 UUID 后备路径；停止确认保持超过一次轮询周期后仍可提交。浏览器用的是模拟 API，不能把它描述成浏览器到真实节点的全链路验收。

## 命令

从 `backend` 执行综合验收（会产生上述本机低负载）：

```bash
.venv/bin/python scripts/test_webui_generation_offline.py performance_testing scripts.test_performance_controller scripts.test_performance_agent_contract scripts.test_performance_execution_contract users.test_management projects.test_permissions_scheme_a projects.test_app_retirement
.venv/bin/python scripts/verify_performance_ui.py
```

Node 测试在 `performance-node`：

```bash
PYTHONPATH=src ../backend/.venv/bin/python -m unittest discover -s tests -v
```

前端：`npm run test:unit`、`npm run build`。Docker：`docker build -t performance-node:0.2.0-local performance-node`（仓库根目录）。

## 当前环境与边界

- 实际 MariaDB 已应用 `performance_testing.0002_execution_control_plane`，只新增运行与控制器状态结构，没有删除业务数据。原有 RAG 条件唯一约束警告仍存在，与本批迁移无关。
- 后端/Celery 重启前均空闲；重启后 HTTP 未登录访问返回401、Celery ping返回pong。当前业务库性能运行数为0，本机验收记录未写入业务库。
- 未改实际 `.env`，性能执行默认关闭；未开放公网端口、未常驻新控制器。后续真实节点接入需按安装文档配置可信 HTTPS 平台入口和加密 RPC 入口。
- 远程 Linux、amd64、公网链路、混合/多节点、高负载容量与长期稳定性未验收。Docker 构建不代表远端部署成功；stunnel 镜像内 OpenSSL 编译/运行小版本提示需在远端 TLS 联调继续观察。
- 本批只接受固定结构化请求模板，不提供任意脚本执行、API 场景自动转换或高级报告。日志/运行目录长期保留策略后续补充。
- 这不是整个 UI/API 后端旧测试套件全绿声明；既有夹具问题仍见 `docs/TODO-OPT.md`。
