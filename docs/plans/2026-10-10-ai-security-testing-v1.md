# AI 辅助网站与 API 安全测试：第一版

用户确认采用“导入接口/网页探索样本 → 配置账号与范围 → AI 生成计划 → 勾选执行 → 查看证据与修复建议 → 导出 HTML”流程。复用后端 ASGI/WebSocket 和 Celery；性能控制器、Caddy 保持停止。

## 第一版边界

新增独立 `security` 项目类型和 `/security-testing` 工作区。第一版检查明确选择的 GET/HEAD 接口，支持认证缺失/无效认证、双账号资源越权、响应信息泄露、Cookie/安全响应头/CORS 配置。账号使用已有测试 Token/Cookie 请求头；自动登录、写入请求、注入探测后续扩展。

来源包括手动 URL，以及从用户有权限的 API 项目导入文档端点或本人网页探索任务中的可用 GET/HEAD 样本。导入是复制草稿，用户核对域名、参数和测试范围后保存；不自动带入采集到的认证凭据。

## 后端与前端契约

新增 `security_testing` Django app。项目级路径前缀 `/api/v1/projects/{project_id}/security/`，响应 `{success:true,data:...}`，错误 `{success:false,error:{code,message}}` 或标准DRF校验错误。

`SecurityPlan`：UUID id、project、created_by、name、config JSON、identities_ciphertext（加密、不可返回）、cases JSON 默认[]、revision 正整数默认1、created_at/updated_at。

`SecurityTask`：UUID id、project、plan(FK PROTECT)、created_by、kind(`planning`/`execution`)、request_id UUID、request_hash、status(`queued`/`running`/`completed`/`failed`/`cancelled`)、snapshot JSON、identities_ciphertext、model_config_id nullable、result JSON、progress JSON、cancel_requested bool、error_code/message、started_at/heartbeat_at/finished_at/created_at/updated_at、deadline_at。project/request_id 唯一。重复投递不重复执行，输入快照固定，权限撤销或任务停止时尽快终止；队列和执行超时有明确终态。

Plan create/PATCH 请求 `{name,config,identities?}`；identities 可选 `{a:{name,headers},b:{name,headers}}`，整体替换，省略保留，{}清除。返回仅 `identities:{a:{name,configured:true},...}`。config:

```json
{
 "allowed_origins":["https://test.example"],
 "allow_private_network":false,
 "endpoints":[{"name":"我的资源","url":"https://test.example/items/1","method":"GET","headers":{"Accept-Language":"zh-CN"},"auth_required":true,"owner_only_a":false,"proof":null}],
 "checks":["authentication","authorization","information","configuration"],
 "max_requests":40,"rate_per_second":1,"timeout_seconds":10
}
```

endpoints 1–20；预算1–100；速率0.1–2 req/s；单请求等待1–30秒。`proof`可选`{path:"body.id",expected:1}`，受限JSON selector、标量期望值，用来确认受保护资源确实返回。URL必须是批准的精确origin，不允许userinfo、片段、模板占位符、非HTTP(S)。配置中的headers不得带认证字段（认证仅来自账号）；过滤Host、连接、代理、转发等危险头。全局配置、匿名请求和替代账号不能意外继承原账号凭据。

生成的 cases 固定结构 `[{id:"case-1",endpoint_index:0,check:"authentication",title:"认证校验",rationale:"…"}]`。每端点/检查至多一项，最多80项。AI只能从已有候选选择和解释，不可新增URL、请求方法或代码。生成只向模型发送端点编号、方法、认证意图、proof是否配置、有限结构信息，不发送凭据、URL参数值或业务响应。模型输出严格校验。用户可不选模型先生成规则计划，选模型则执行AI规划，界面清晰区分。

- `GET/POST plans/`；`GET/PATCH plans/{uuid}/`；`DELETE`仅未有任务引用的计划可删。
- `POST plans/{uuid}/generate/` body `{request_id,model_config_id?}` → task(202或幂等200)，异步规划，完成后仅当revision未变化才更新plan.cases，否则保留task结果并提示重新生成。
- `POST plans/{uuid}/runs/` body `{request_id,case_ids:[...]}` → execution task；至少选1项，只允许当前plan.cases中的项。
- `GET tasks/` → `{items:[task...]}`；`GET tasks/{uuid}/` → task详情；`POST tasks/{uuid}/stop/` → task；`GET tasks/{uuid}/report.html` →终态execution单文件HTML。
- `GET sources/` → `{projects:[{id,name}]}`，仅有源项目READ/REPORT权限的API项目。
- `GET sources/?source_project_id=N` → `{endpoints:[{source_id,name,url,method,headers,auth_required:false,owner_only_a:false,proof:null}],warnings:[...]}`；最多100个来自文档/本人终态探索的样本，缺少完整URL/仍有模板的端点跳过。结果只是草稿，无自动执行。
- 配置/计划READ；编辑EDIT；生成EDIT+EXECUTE；执行EXECUTE+REPORT；任务列表/详情/报告REPORT；停止EXECUTE。所有请求强制同项目及security类型，源项目另验权限。

## 执行器契约与证据

`runner.execute(snapshot, identities, check_active, on_progress)` → `{items:[...],summary:{...},limitations:[...]}`。snapshot含`{config,cases,plan_name,plan_revision}`，identities为解密账号。`check_active()`回调在每次网络操作/等待前调用，抛异常即停止。`on_progress({completed,total,requests_sent,phase})`保存进度。

每项包含case_id/check/endpoint_index/title/status(`passed`/`confirmed`/`suspected`/`inconclusive`/`error`/`skipped`)、summary、recommendation、evidence（基准与变体的URL、方法、状态、耗时、有限响应预览与差异）。持久化请求头必须隐藏认证，响应预览敏感值脱敏；完整Cookie/Token仅短暂参与执行，不进入AI/报告/日志。单响应最多64KiB，截断明确标记。不得将执行异常或数据缺失当通过。

每项完成后保存已脱敏的部分结果；后续取消或超时保留已完成项，未完成的检查不能视为通过。第一版身份只支持请求头，配置拒绝 URL 中可识别的认证参数，避免匿名/替代账号继承认证。A/B 请求头完全相同则无法验证不同账号，返回未验证。

HTTP通过固定解析IP的连接访问，保留正确Host/TLS主机校验；不使用环境代理、不跟随重定向，所有解析地址按allow_private_network校验，始终拒绝metadata/link-local/multicast/unspecified，DNS pinning防止检查后重绑定。预算实际按每次请求消耗。超时、总任务时限、有限响应读取和每请求检查取消共同限制任务。

认证/越权先验证账号A基准请求；2xx不代表业务成功。只有明确auth_required/owner_only_a和可验证proof的成功基准，匿名/无效认证/账号B也返回相同受保护proof时才能“已确认”；无proof的相似响应最多“疑似”。缺账号B/缺资源归属声明/无有效基准为未验证。401/403可作为拒绝证据。配置缺失/错误文本类发现先标疑似或配置观察，不宣称可利用漏洞。报告明确检查覆盖范围，无发现不代表系统整体安全。

## 文件所有权（实施协作）

- Backend worker：security_testing 除runner/transport/validation/reporting以外全部文件，含model/API/任务/AI/加密、测试、迁移；projects模型/迁移、settings/urls注册。
- Runtime worker：security_testing/runner.py、transport.py、validation.py 及 test_runner/test_transport/test_validation。提供`validation.validate_config(value)`与`validate_identities(value)`返回标准化对象，ValueError中文。不操作DB。
- Frontend worker：frontend安全项目/工作区/详情、API、路由、首页/菜单、必要项目类型映射与针对性测试。
- Root：reporting.py/report测试、文档、最终集成审查、运行环境激活、只读/替身验证与中文提交。

验证使用本地HTTP fixture、隔离数据库、模型替身。未经单独指定，不对真实被测站点或真实模型发起安全检查。保持已有历史数据和三个浏览器ZIP。
