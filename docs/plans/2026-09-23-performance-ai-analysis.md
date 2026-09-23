# 压测运行结果 AI 分析（第一版）

## 范围

仅对已结束的正式压测手动发起分析，包括完成、失败、取消、数据不完整的运行。复用平台启用的 LLM 配置与 Celery；不自动发起压测或修改脚本，不改变现有执行详情。分析记录保存模型信息、可选目标、统计快照、结果和时间，刷新页面可继续查看。

分析前可选填 P95 上限（毫秒）、错误率上限（百分比）、目标 RPS 下限，目标只属于本次分析，不改变压测计划或历史运行。目标达标判断由程序计算；未配置、零请求、运行未完整结束或指标缺失时不能宣称整体达标。AI 负责解读、基于证据提出假设及排查建议。

模型输入仅包含白名单统计数据、节点资源、有限趋势和失败样本类型摘要。接口、节点以序号标识，不发送 URL、名称、变量、请求头、请求/响应体、断言实际值或原始异常。页面可将序号对应回本地运行中的接口和节点。错误样本数量不是失败请求总数。无服务端监控时不得断言数据库或应用内部根因。

## 接口约定

- `GET/POST /projects/{project_id}/performance/runs/{run_id}/analyses/`
- `GET /projects/{project_id}/performance/runs/{run_id}/analyses/{analysis_id}/`
- 响应沿用 `{success: true, data: ...}`；列表 `data.items`，最近 20 条。
- 读取要求 REPORT；创建同时要求 REPORT 与 EXECUTE。后台执行再次检查权限及模型可用性。
- POST：`{request_id: UUID, model_config_id: integer, targets: {p95_ms?: number, error_rate_percent?: number, rps_min?: number}}`。P95/RPS 大于 0，错误率 0–100；不接受其他字段、非有限值和布尔值。
- 每次运行同时至多一个 queued/running 分析，重复 request_id 同载荷返回原记录，内容变化返回 409。失败后用新 request_id 重试，保留旧记录。
- 分析对象：`id, run_id, status (queued/running/completed/failed), model_info ({config_id,name,provider,model_name}), targets, result, error_code, error_message, created_at, started_at, finished_at`。
- 结果：`summary`；`findings: [{category: performance/errors/nodes/load, kind: observation/hypothesis, severity: info/warning/critical, title, detail, recommendation, evidence_ids: [string]}]`；`limitations: [string]`；程序生成的 `assessment: {status: met/not_met/not_configured/insufficient_data, checks: [{key,target,actual,unit,status: met/not_met/insufficient_data}]}` 与 `evidence: [{id,label,value,unit}]`。
- 证据 value 为 JSON 值；模型只能引用统计快照中存在的证据 ID。所有展示为转义文本，不渲染模型 HTML。
- 排队/执行超时、队列不可用、模型停用、权限变化、模型返回无效结构等均转为明确失败，可重新发起；不展示包含提供商凭据的原始异常。

## 实现边界与验证

`analysis_data.build_analysis_input(run, targets)` 构建有限统计快照；`analysis_engine.generate_analysis(model_config_id, payload, check_active, remaining_seconds)` 生成并验证结构化结果。任务生命周期由独立分析记录和 Celery 任务维护。统计与目标判断先确定，再交给模型解读，原始模型响应不直接落库/展示。

验证包括统计白名单与边界、目标判断、证据引用和无效输出、跨项目权限、终态限制、幂等与任务互斥、排队/执行失败恢复，以及前端模型选择、空状态、轮询、历史结果和路由切换。使用隔离测试数据库与模型替身，不对真实目标发压。节点运行时协议和镜像不变，无需升级节点。
