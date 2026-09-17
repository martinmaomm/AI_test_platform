# 性能测试第一阶段 · 第一批：计划管理与节点接入

> 此文为第一批历史设计，版本与能力并非当前状态。2026-09-17 已移除自定义标签和已注册节点身份轮换，补充吊销确认及保留历史报告的节点删除；当前安装与管理流程见 [节点部署说明](../../deploy/performance/README.md)。

总体架构沿用 [分布式性能测试设计](2026-09-15-distributed-performance-testing.md)，不重选引擎。

本批交付可用的性能项目入口、受控目标和压测计划管理、节点登记/心跳及独立客户端。后续接入独立 Locust Master、Worker、双向 TLS 协调、运行租约及指标/报告；本批没有发压能力，不创建假的执行记录，也不将节点在线表述为 Worker 就绪。

## 固定契约

- 协议版本 `1`，节点版本 `0.1.0`，候选引擎版本 `2.43.3`。
- 平台接口成功统一 `{success: true, data: ...}`；列表 `data.items`。错误使用合适的 400/401/403/404/409，错误信息不得包含凭证。
- 项目接口根路径 `/api/v1/projects/{project_id}/performance/`，仅接受 `project_type=perf`，沿用项目成员/管理员权限。
- `GET config/`：`phase=node_access`、`execution_enabled=false`、`protocol_version`、`engine_version`、`agent_version`、`heartbeat_interval_seconds=5`、`node_offline_after_seconds=30`、`limits={max_users:100,max_duration_seconds:600,max_spawn_rate:100,max_steps:20}`。
- `GET/POST targets/`、`GET/PATCH/DELETE targets/{id}/`：字段 `id,name,base_url,allowed_methods,created_at,updated_at`。管理员管理，项目成员可读；base_url 必须为无用户信息、查询或片段的 HTTP(S) origin（允许结尾 /，不含业务路径）。`allowed_methods` 默认 `['GET']`，可选 GET/POST/PUT/PATCH/DELETE/HEAD/OPTIONS。已有计划引用时不允许删除目标。
- `GET/POST plans/`、`GET/PATCH/DELETE plans/{id}/`：字段 `id,name,description,target_id,users,spawn_rate,duration_seconds,wait_seconds,steps,created_at,updated_at`；默认 1 用户、1 用户/秒、30 秒、等待 1 秒。步骤 `[{name,method,path,expected_status,headers,body}]`，至少 1 项，最多 20 项；path 是以单个 / 开头的相对路径，禁止绝对 URL、反斜线、控制字符与 //；默认 expected_status=200，headers={}，body=null。方法须在目标批准列表，拒绝跨项目目标；用户数/时长/速率在服务端限制，等待时间 0.1–60 秒，JSON 大小受限。普通成员按 READ/EDIT/DELETE 权限操作。此批不支持任意 Python 脚本或动态表达式执行。
- `GET/POST nodes/`、`GET/PATCH nodes/{uuid}/`：节点绑定性能项目，普通成员只读，管理员可管理。字段 `id,name,network_mode=lan|public,labels,status,last_seen_at,agent_version,engine_version,protocol_version,resources,created_at`；status 为 pending/online/offline/revoked，在线按服务端心跳时间计算。
- `POST nodes/{uuid}/enrollment/`：生成/重置短期一次性注册凭证，同时作废旧注册凭证和旧长期身份；返回 `{node, enrollment_token, expires_at}`。新建节点也返回此结构。凭证只在这两个显式操作响应中返回，列表/详情不返回 token/hash。重置操作前 UI 要说明原客户端将失效。
- `POST nodes/{uuid}/revoke/`：吊销长期身份及注册凭证，幂等；本批没有运行任务，未来接入执行时需补现有运行终止逻辑。
- 注册凭证与长期 Agent 身份均采用高熵随机 secret，数据库仅存摘要，恒定时间比较；凭证消费使用事务锁。过期、重放、吊销、跨节点请求必须被拒绝。节点不会拿到平台用户 JWT、数据库或模型密钥。

## 节点协议

- 根路径 `/api/v1/performance-agent/`，与用户 JWT 管理接口隔离。
- `POST enroll/`：`{enrollment_token,protocol_version:1,agent_version:'0.1.0',engine_version:'2.43.3'}`。返回 `{node_id,agent_token,heartbeat_interval_seconds:5,lease_seconds:30,protocol_version:1,execution_enabled:false}`。
- `POST heartbeat/`：`Authorization: Node <agent_token>`；请求 `{protocol_version,agent_version,engine_version,resources:{cpu_percent,memory_percent}}`，服务端从认证身份确认节点，不信任客户端 node_id/project_id。返回 `{node_id,server_time,heartbeat_interval_seconds:5,lease_seconds:30,command:{type:'idle'},execution_enabled:false}`。版本不匹配返回 409，不发任务、不续在线状态。
- 注册和心跳响应均包含 `protocol_version:1`，客户端检查协议与身份一致性。
- token 格式为 `uuid.secret`，两类 secret 独立；摘要字段不能进入通用模型序列化输出。
- Agent 强制 HTTPS、校验服务端证书；支持私有 CA 文件，不提供 verify=false。拒绝重定向、userinfo、查询/片段，平台 URL 可含部署子路径。测试只使用本机临时 TLS 服务或模拟传输。
- Agent CLI：`python -m performance_node enroll` / `run`。配置通过 `PERFORMANCE_PLATFORM_URL`、`PERFORMANCE_NODE_ENROLLMENT_TOKEN`（或 `PERFORMANCE_NODE_ENROLLMENT_TOKEN_FILE`）、`PERFORMANCE_NODE_STATE_DIR`、`PERFORMANCE_NODE_CA_BUNDLE` 传入；不能把 token 放到命令行参数或日志。
- 注册成功将长期身份原子保存为仅当前用户可读写的状态文件；重启复用已有身份。网络失败有限退避，401/403/409 等身份/协议错误明确停止；响应未知命令 fail closed，不执行任意服务器字符串。
- 心跳客户端只报告主机状态，不伪造 Worker 状态或上传请求统计。Docker/Compose 同仓独立打包，不依赖 Django、Redis 或平台 .env，不挂 Docker socket/特权模式；以非 root 用户运行。

## 页面边界

- 首页性能入口可以进入「性能测试（建设中）」；安全测试仍禁用。
- 性能工作区仅提供压测计划、节点管理、压测目标三个入口；移除旧性能环境、定时任务、通知占位路由，后续完成对应能力再启用。
- 明确展示本批能力边界；不提供可以点击但没有执行引擎的开始按钮，不将 HTTP 心跳等同于可压测。
- 项目切换、轮询销毁、重复提交、错误态均需处理；token 不写 localStorage，不进入 URL。只在创建/重置后的临时弹窗显示。

## 本批验收

1. 管理员创建性能项目/目标/计划/节点；项目成员只能访问分配的项目，API/UI 项目不能调用这些接口。
2. 计划参数、目标范围、方法、步骤、跨项目关联在后端校验；不能靠改前端发出压测请求。
3. 注册一次性、过期、重放、版本错误、吊销/轮换、心跳身份绑定；列表不泄露身份凭证。
4. 节点 HTTPS/私有 CA、证书错误、重定向、网络退避、重启复用、文件权限、未知命令和优雅停止。
5. 前端单测及构建、后端隔离回归、节点独立测试；不触发真实模型、不向 NAS/商城发压。
6. 当前平台增量迁移、后端/Celery 重启及真实 HTTP 冒烟。Docker 镜像构建、Linux 公钥 SSH 部署和公网连通性单独标记是否已验证。
