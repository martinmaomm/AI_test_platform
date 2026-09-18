# 性能测试：网页探索与可重复请求计划

用户已确认 B 方案，2026-09-18 开始实施。本文是本轮开发契约，不代表已完成验收。

## 产品范围

- 性能项目内新增网页探索入口，复用 API 模块的 MCP 浏览器采集引擎与真实样本，不要求创建 API 项目或 API 工作区。
- 用户描述目标、确认测试操作、选择模型，查看探索进度/诊断，勾选真实接口及必要依赖，生成独立的压测计划草稿。
- 草稿可编辑固定参数、每轮唯一值、准备步骤、响应变量提取及多条业务断言。探索或保存计划不自动发压。
- 先在所选节点用单用户验证，查看结果后再启动正式压测。每个虚拟用户隔离 Cookie 和变量；各用户内部顺序执行。
- 压测目标仍由管理员批准，不因发现新 origin 自动扩大允许目标或方法。没有已批准目标时保留采样结果供用户先创建目标。
- 本轮不做任意 Python 脚本、CSV 账号池、多 origin 场景、P95 门禁、定时任务或自动重装远程节点。

## 计划请求契约（v2）

保留计划名称、说明、target_id、users、spawn_rate、duration_seconds、wait_seconds，新增：

```json
{
  "variables": {"username": "测试账号", "password": "待填写"},
  "unique_variables": [{"name": "new_name", "prefix": "load_"}],
  "steps": [{
    "name": "获取用户列表",
    "phase": "main",
    "method": "GET",
    "path": "/users",
    "query": {"pageNum": 1},
    "headers": {"Authorization": "Bearer ${token}"},
    "body_type": "none",
    "body": null,
    "extract": [{"name": "user_id", "check": "body.data[0].id"}],
    "assertions": [
      {"check": "status_code", "comparator": "eq", "expected": 200},
      {"check": "body.data", "comparator": "type", "expected": "list"},
      {"check": "body.data", "comparator": "length_gt", "expected": 0}
    ]
  }]
}
```

- phase 为 setup/main；准备步骤在每个虚拟用户启动时执行一次，必须位于 main 之前，至少一个 main。
- body_type 为 none/json/form/raw；query 单独保存，不把参数转成不可编辑的 URL 文本。multipart 上传本轮明确不支持，不能默默转换为 JSON。
- selectors 使用 status_code、body、body.data[0].id、headers.Content-Type、text 等确定性选择器，不执行 Python/JavaScript/eval，不引入任意 JSONPath 函数。
- comparator 首批为 eq/ne/contains/not_contains/gt/ge/lt/le/type/length/length_gt/exists，类型严格，缺失值不同于 null；expected 为 JSON 类型。
- 每步至少一条可执行断言。HTTP 状态码也是断言，不另外保留 expected_status 可编辑字段，避免两套互相矛盾的配置。
- 完整 ${name} 引用保留原始类型，嵌入文本的引用转字符串；未定义变量执行前报具体错误。参数由 HTTP 客户端编码，变量不能更改批准的 origin。
- unique_variables 每轮生成唯一字符串，带运行/虚拟用户唯一分量；不复用探索样本的固定时间戳。setup 不得依赖每轮变量。
- 每轮 main 使用固定变量、setup 提取值和新唯一值初始化；上一轮提取不能残留。断言全部成功后再提交本步提取，失败不污染后续变量。
- setup 失败停止该虚拟用户；main 失败中止当前轮，不继续依赖步骤，下轮重新开始。
- 一次请求不论几条断言失败，都只统计一次失败请求。保留有限、截断的失败证据，报告能看到步骤、检查字段、期望/实际和错误类型。
- 响应与断言处理有长度上限，不逐请求持久化全部响应，不把断言 CPU 耗时混入网络响应时间。

## 网页探索 HTTP 边界

性能专用前缀 `/projects/<id>/performance/discovery/`：

- GET config/：模型、开关与 limits，返回既有探索配置 data。
- GET/POST tasks/：列表/创建；创建字段沿用 API 探索 target_url、description、model_id、api_origin(可选)、allow_test_data_writes、exploration_timeout_seconds，并增加 auto_approve_origins（默认 true，可关闭以逐次确认跨域来源）。
- GET/DELETE tasks/<uuid>/：详情/删除。
- GET tasks/<uuid>/records/：采集记录。
- POST tasks/<uuid>/cancel/、retry/、origin/：沿用明确取消、可重试原因和 origin 选择能力。
- POST tasks/<uuid>/draft/：传 version、record_ids、target_id；返回 `{draft, warnings, source}`，不保存计划、不运行，不创建 API 工作区。
- 响应沿用平台 `{success,data}` 或既有 common response 封装，前端统一解包。
- 草稿包含上述 v2 契约与默认最低负载（1用户），target_id 必须属于当前项目并匹配已确认接口 origin、允许方法。
- 同方法/路径的请求分组展示，保留样本选择，不丢弃不同参数变体。选择依赖闭包但不得跨任务或跨 origin 导入。
- 基于确切证据生成提取及变量绑定；不确定则 warnings 提醒人工编辑，不猜测固定业务成功码、登录用途或必要依赖，不采用站点按钮关键词推断。
- 只有当前项目权限、当前 owner 的任务可访问；API 路由不得访问性能任务，性能路由也不得访问 API 任务。采集引擎复用，业务出口和权限独立。

## 节点与运行

- 新版 snapshot schema_version=2，Agent 0.3.0；后台接收旧 0.2.x 心跳用于展示升级提示，但不得把 v2 发给旧节点执行。已有节点不自动重装或撤销。
- POST plans/<id>/runs/ 增加 mode=validation/load，默认 load；验证模式强制单用户、执行一轮（含 setup），不采用用户大负载参数。
- 验证与正式运行使用同一节点模板和断言规则；验证通过必须有真实执行请求并完成全部主步骤，不能仅以进程正常退出判定。
- 正式执行需当前计划/目标/所选节点有匹配的成功验证，编辑影响请求或数据的配置后失效；数据与节点变化不得复用陈旧通过结论。
- 已有简单计划通过一次性数据迁移补齐 v2 默认值并把 expected_status 转为状态码断言，不删除计划、历史运行、节点或凭据。历史运行只读保留，不重放。
- 整体响应时间阈值/错误率通过标准与请求断言分开，后续另做，不在本轮承诺已实现。

## 分工与验收

1. 节点执行 + 后台计划/运行契约：Sol；采集复用 + 性能草稿出口：Sol；前端编辑器/探索/验证展示：Terra。文件所有权明确，不能互相覆盖。
2. 主 Agent 审查集成、迁移、权限、共享 API 回归、前端构建及本机低负载链路。现有远端节点升级与公开镜像发布另获明确授权。
3. 验证矩阵：严格类型/缺失字段/列表长度、请求体类型、Cookie/变量隔离、唯一值、准备只执行一次、失败中止与计数、bounded 证据、静态变量错误、旧节点拒绝、验证结论失效、探索权限与同源限制、重复样本、取消/失败/重试、编辑保存与再次读取。
4. 不调用 NAS/商城发压；本机临时夹具 1 用户低负载。真实 LLM/MCP 全链路如未执行必须明确标记，不把 mock 当实机验收。
