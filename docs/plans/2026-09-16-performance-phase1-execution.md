# 性能第一阶段第二批：单节点真实执行契约

沿用已批准的分布式设计。这一批实现单节点 Master/Worker 闭环、停止、基础指标和执行记录，不做多节点分摊/API 转换/AI 生成。测试仅本机临时目标 1 VU、5 秒；不修改公网入口或 NAS。

## 版本与边界

- 协议 `2`，Agent `0.2.0`，Locust `2.43.3`。不兼容协议 1 客户端，旧版本明确提示升级；不删除既有节点。
- 独立 `python manage.py run_performance_controller` 管理全局串行运行，不在 Django 请求或 Celery 内 import Locust/gevent。
- 同时仅一个全局活动运行，每次恰选一个已在线、未吊销、版本匹配的本项目节点。计划参数沿用上批上限。
- 只支持固定模板结构化请求步骤，不执行用户 Python。禁跳转，校验 HTTP 目标/方法/路径/headers；requests 流量不经平台。
- 每次运行新建 CA、服务器证书和该节点客户端证书；TLS 转接校验证书链和服务器名称。每轮唯一 CA + 随机握手值绑定 run/node/snapshot，旧轮身份不准入；不暴露原始 RPC。

## 数据与接口（Sol 后端负责）

`PerformanceRun`: UUID id、project(FK)、plan(SET_NULL)、node(RESTRICT)、created_by(SET_NULL)、request_id(UUID)、status、snapshot(JSON)、snapshot_sha256、node_command(JSON，私有)、node_report(JSON)、node_report_seq(默认0)、latest_metrics(JSON)、metrics_samples(JSON，限400)、reason_code、reason、created_at、started_at、finished_at、stop_requested_at。

status：queued/preparing/running/stopping/completed/failed/cancelled/incomplete。终态不被迟到请求覆盖；请求失败数>0与执行进程失败分开，completed 可以有失败请求。project+request_id 唯一防重复提交。同 ID 不同参数返回409。

`PerformanceControllerState` 单例 pk=1：owner_id 字符串、heartbeat_at、lease_until、current_run(SET_NULL)。主 Agent controller 操作其租约，接口只读在线状态（heartbeat 15秒内且租约未到期）。

- `GET config/`：phase=execution；controller_online；execution_enabled（环境启用且控制器新鲜）；execution_unavailable_reason；max_nodes_per_run=1。常规管理仍可使用。
- `POST plans/{id}/runs/`: `{node_id, request_id}`；项目 EXECUTE 权限；需要控制器在线，严格复验完整计划/目标、节点及版本并冻结快照，返回运行摘要201；重复同请求返回原记录，不复跑。创建与停止只返回摘要，不绕过REPORT权限返回完整快照/采样历史。
- `GET runs/`: 最近100项 `data.items`；READ；`GET runs/{uuid}/` READ/REPORT（使用REPORT能力）；`POST runs/{uuid}/stop/` EXECUTE，幂等；queued直接cancelled，其他活动态stopping+stop_requested_at。无执行删除接口。
- 管理详情输出字段：id,plan_id,plan_name,node_id,node_name,status,created_at,started_at,finished_at,reason_code,reason,latest_metrics,metrics_samples,snapshot（不含握手/TLS秘密）。列表不带snapshot/采样历史。
- 运行创建快照（固定字段）：`schema_version:1,run_id,node_id,engine_version,plan_name,base_url,allowed_methods,users,spawn_rate,duration_seconds,wait_seconds,steps`。SHA为 `json.dumps(snapshot,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False)` 的UTF8 SHA256。
- 运行所在项目有非终态记录时，删除计划/目标/节点轮换/吊销要正确处理：目标/计划修改不改变已冻结快照；轮换/吊销节点把关联活动运行置stopping（控制器会立刻关TLS）；项目删除非终态需拒绝（可通过模型信号实现，不改其他业务）。

心跳请求新增 `run_report`（可null）：`{run_id,sequence,state:preparing|ready|running|stopped|failed,reason_code,reason}`；必须身份与Run.node匹配，严格递增sequence，不接受客户端统计，不直接将run设completed。
成功响应共同字段保留，execution_enabled表示平台执行配置可用（客户端能力另由协议/版本判断），实际能否下发运行仍取决于控制器租约；command：

- `{type:'idle'}`：没有当前指派（如果本地有活动进程，应停止）。
- `{type:'prepare',run_id,snapshot,snapshot_sha256,script_source,script_sha256,lease_seconds:15,max_seconds,tls:{host,port,server_name,ca_pem,cert_pem,key_pem},handshake_token}`：在controller心跳新鲜、run preparing/running、node未吊销时发送 `node_command`；手动停止进入stopping的收尾阶段继续保留当前prepare，由Master先停止发压并收齐尾统计，之后controller切stop。准备指令重复投递必须幂等。TLS/握手字段不进管理序列化。
- `{type:'stop',run_id,reason}`：controller完成收尾、运行终态或失去controller租约时发送；节点吊销/身份轮换直接中止并允许统计不完整。只有匹配的节点收到自己的指令。未知run_report拒绝；终态报告可接受但不改变运行终态。

Controller配置从环境读取（在新模块独立runtime_settings.py，由主Agent实现）：`PERFORMANCE_EXECUTION_ENABLED` 默认false，`PERFORMANCE_RPC_BIND_HOST` 默认127.0.0.1，`PERFORMANCE_RPC_PORT` 默认9443（加密入口），`PERFORMANCE_RPC_PUBLIC_HOST` 与 `PERFORMANCE_RPC_SERVER_NAME` 必填，`PERFORMANCE_RUNTIME_DIR` 默认 backend/temp/performance-runtime，`PERFORMANCE_STUNNEL_BINARY` 默认stunnel。不修改现有实际.env，不自动开放公网端口。

## 节点执行（Sol 节点负责）

- Agent v2长期进程保持HTTPS心跳。prepare先校验唯一run_id、固定模板SHA、本机已安装引擎版本、整个snapshot的边界、TLS地址/PEM/摘要，文件0600目录0700按run分离；不得使用shell拼接或远程任意命令。
- 固定模板由主Agent编写 `performance-node/src/performance_node/locust_runtime.py`；请勿修改该文件。提供 `validate_snapshot(snapshot)` 和 `canonical_sha256(obj)` 可被Agent导入（模块顶层不能import Locust/gevent）。运行时CLI：`--role worker|master --config PATH --master-host HOST --master-port PORT`；Master还收 `--start-file PATH --metrics-file PATH --complete-file PATH`。config是 `{snapshot,handshake_token}`，worker额外不需要JWT/Agent凭证。
- Agent保存平台给的script_source但必须SHA与本地上述固定文件完全一致；用平台下发该固定文件运行Worker。Worker的host始终是本地stunnel转接，TLS远端来源显式配置。
- 节点实现 `process_supervisor.py`：CLI `python -m performance_node.process_supervisor CONFIG_PATH`，供主控制器及节点共同使用。CONFIG含 `processes:[{name:'engine'|'tunnel',argv:[...],cwd,log_path}], lease_file,lease_seconds,max_seconds,result_file`。只处理本机可信配置，不直接使用远程argv。超时/租约文件过期/任一子进程退出/信号时回收全部自建进程组，result原子写 `{reason:'finished|lease_expired|timeout|child_failed|stopped',exit_codes:{engine:...,tunnel:...}}`；engine正常0退出才finished，任何tunnel早退失败。支持没有启动顺序假设的有限连接等待（tunnel先起，engine后起）。
- Agent在每次成功且匹配prepare的心跳后续写lease文件；Supervisor独立进程检查过期（即HTTP阻塞也停止）。本地max_seconds不超过duration+准备60+回收15，总≤675。
- Agent状态记录active/已结束run，进程重启不能重新执行旧run；重复prepare不会二次启动，已结束run只报告，遇到新run才重新准备。重启时先回收旧监督进程（核对PID+create_time），上报failed，不自动重放。
- 发出心跳时携带递增run_report；Supervisor启动不等于Worker ready，至少确认子进程存活后ready，最终是否握手由Master把关。心跳未知/错误身份停止当前任务；SIGTERM/SIGINT正常清理。
- 升级Docker固定Locust依赖与stunnel；最小环境启动子进程，剥除Agent token/.env/代理等；无特权、无Docker socket。程序日志不可泄露PEM/token/完整响应。

## 控制器与引擎（主Agent负责）

- 控制器DB租约与本地进程监督，不能启动两个全局活动Run。取得租约时发现遗留活动记录，设stopping→回收/等待节点租约过期→incomplete，不重放。失去自己租约则立即停止本机执行子进程。
- queued运行重新检查node在线/目标仍有效，冻结内容不可变。创建准备资源：本轮CA、stunnel配置、随机握手、固定脚本、配置文件。Master原始端口随机且只bind127.0.0.1；加密入口地址独立，默认仍loopback。
- Master使用Locust库模式且只在独立进程导入；Worker custom hello与run/node/snapshot/随机值相符才接纳，开始文件且节点ready才正式start。准备60秒上限。
- 固定模板每用户独立HttpUser session、步骤顺序、wait_time。响应码断言，默认TLS校验、禁跟随重定向、固定请求超时、限制响应体缓存。不对用户提供的表达式eval。
- Master每2秒输出原子metrics文件：`requests,failures,error_rate,rps,avg_response_time,p95,p99,users,elapsed_seconds,worker_count,started,entries:[{name,method,requests,failures,avg_response_time,p95,p99}],complete`。控制器保存累计快照（替换而非累加），采样历史最多400。
- 用户停止只先发Master停止信号/文件，等待尾统计，再关闭tunnel/回收Worker。工作节点丢失、引擎异常、缺最终统计标incomplete/failed。终态前确认agent stopped或过租约期限，下一轮不与旧压力重叠。

## 前端（Terra负责）

- 新运行记录页，计划列表执行按钮、单节点选择和确认负载；生成UUID request_id一次提交/网络重试复用，成功后跳到执行详情。
- 控制器未启动明确提示，不伪装空白或成功。节点在线不代表Worker ready；按钮按项目EXECUTE/REPORT权限。
- 详情周期拉取状态、指标、接口明细和有限趋势；状态异常用中文，停止按钮防重。completed且failures>0显示“执行完成，存在失败请求”。不显示node_command/private TLS字段。
- 加入路由/菜单并保留计划、目标、节点已有能力，切项目/卸载停止轮询、防止迟到响应串项目。

## 验收

隔离单元/权限/版本/幂等/状态回归，浏览器交互与构建；真实本机 HTTPS平台→Agent→mTLS→Master/Worker→临时目标 1VU5秒，核对请求数/统计、正常结束、手动停止、管理失联停止、重复命令不重跑。Docker重建。未验证的远程Linux/公网/多节点明确保留，不把loopback测试算跨网验收。
