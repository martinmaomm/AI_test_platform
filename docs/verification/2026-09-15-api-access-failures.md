# API 权限隔离、异常与媒体入口专项验收

日期：2026-09-15  
代码基线：`68fd4d5`  
结论：**本轮代码、隔离验收及重启后的实际服务权限复测通过。** 修复前确认一项 P1 API 规范列表数据泄漏、一组 P2 越权状态码契约问题，以及一项截图原始媒体入口绕过；三项均已最小修复。真实服务补测和邮件剩余边界见 [第三批汇总](2026-09-15-api-regression-batch3.md)。

## 1. 范围与安全边界

本轮先完成测试验收；用户随后明确授权 API 项目访问边界和私有截图媒体入口的最小修复。主代理负责真实邮件、实际平台、重启与提交；UI 共享冒烟由另一 Agent 独立执行。

- 所有新增账号、项目、规范、工作区、用例、套件和执行记录均由 `test_api_workspace_browser.bootstrap(tempdir)` 后写入临时 SQLite。
- cache 为 LocMem、channel layer 为内存、Celery broker/result backend 为内存；`socket.connect/connect_ex` 仅允许 loopback。
- 浏览器为本机真实 Chrome，页面只允许访问本轮 `127.0.0.1` Django+Vue origin；登录通过真实用户名/密码表单和 `/users/login/`，没有注入管理员 token。
- 模型、业务 HTTP、任务派发均使用替身；没有访问 NAS 数据库、Redis、SMTP、真实 LLM 或目标平台。
- 未运行裸 `manage.py test`，未接触此前误用的 NAS `test_AI_platform`。
- 主代理修复前使用真实受限账号确认：workspace 31 为 403、API report 90 为 404、UI screenshot 16 为 404、SMTP 配置为 403，但 spec list 为 200 且含项目数据。证据：[restricted-live-access-before-fix.json](../../backend/logs/api-regression/20260915-batch3-lxupvC/restricted-live-access-before-fix.json)。

## 2. AUTH 权限结果

### 2.1 已通过的隔离边界

非项目成员真实登录后：

- 项目详情 404；API 工作区 UI 直链 403 且未显示工作区数据；报告 UI 直链 404 且显示“报告不存在或无权访问”。
- 工作区列表、详情、修改、删除、生成、保存、Python 导出、调试、重试、取消均为 403。
- 用例执行、套件执行、场景截断调试均为 403；执行报告和任务状态为 404。
- 四类任务派发计数均为 0：case、suite、debug、generation。
- 数据库读回确认原工作区、用例、套件、执行记录均存在且关键字段未变化，执行记录总数仍为 1。

显式 view-only 成员使用 `can_edit=false`、`can_delete=false`、`can_execute_tests=false`、`can_view_reports=true`：

- 工作区列表/保存/调试/重试及用例、套件、场景执行均为 403。
- 执行报告为 200，符合独立 `can_view_reports` 能力。

浏览器证据：

- [脱敏访问矩阵](../../backend/logs/api-access-failures-browser-check/access-matrix.json)
- [无权限报告直链](../../backend/logs/api-access-failures-browser-check/outsider-report-direct-link.png)
- [无权限工作区直链](../../backend/logs/api-access-failures-browser-check/outsider-workspace-direct-link.png)

### 2.2 D-AUTH-01：非成员可读取项目 API 规范列表（P1，已修）

复现：非项目成员真实登录后请求 `GET /api/v1/projects/{project_id}/api-testing/api-specs/`。

预期：403 或 404，不返回项目数据。

修复前实际：200，响应包含本项目规范标识及规范元数据。首次浏览器矩阵以 `owner_marker_visible=true` 确认泄露；未继续枚举其他项目或资源。矩阵文件当前保存的是修复后结果。

根因：`APISpecificationListView.get_queryset()` 仅调用 `_source_visible_specifications(project_id, request.user)`；普通 Swagger 规范只按 `project_id` 进入 queryset，没有项目成员/查看权限前置检查。详情接口另有权限判断，本轮返回 403，因此缺陷集中在列表边界。

影响：知道项目 ID 的任意已登录用户可读取该项目 API 规范列表及其序列化元数据，违反 AUTH-01 的跨项目不可读要求。按清单严重度属于跨项目数据泄露，阻断放行。

修复：在相关 spec/endpoint/case/execution list/detail/update/delete 查询前统一解析 API 项目；项目不存在、类型不符或非成员均隐藏为 404，成员缺少修改能力为 403。独立 owner（`owner != created_by`）读取 spec/endpoint 保持 200。execution 报告可见性继续由现有 `_report_execution_queryset` 决定，没有新增 `executor=user` 旁路；仅在 list、case detail、suite detail 三个新增 read guard 保留原有 superuser 豁免，report API 语义不变。

隔离修复后，真实 Chrome 中 spec list 为 404 且不含 owner marker；后端正向回归确认非成员 superuser 对 execution list/case detail/report 仍为 200。

### 2.3 D-AUTH-02：多个越权接口未返回 403/404（P2，已修）

未发现这些接口返回所有者数据或触发任务，但状态码不符合本轮验收标准：

| 接口类别 | 修复前实际 | 数据情况 |
| --- | ---: | --- |
| 端点列表 | 200 | 空数组 |
| 用例列表 | 200 | 空分页 |
| 执行记录列表 | 200 | 空分页 |
| 用例详情、用例更新 | 400 | 无所有者数据 |
| 执行详情、执行删除 | 400 | 无所有者数据，未删除 |

根因分为三类：端点列表在权限不足时返回 `QuerySet.none()` 后仍包装成功；用例/执行列表只做 owner/report queryset 过滤而无项目访问前置拒绝；部分详情/删除把 `Http404`/`DoesNotExist` 包装成默认 400。

这些问题未形成新的数据泄漏，但降低客户端区分“无数据”和“无权访问”的可靠性。修复后均为 403/404；`APITestExecutionDeleteView` 还增加 `project_id` 查询条件，同一用户不能通过项目 B URL 删除项目 A execution，原 execution 与 detail 保留。

### 2.4 共享媒体匿名读取绕过（已修，实际实例复测通过）

主代理仅验证本轮自有 execution 16 截图：正常 report 与截图认证接口匿名均为 401，但直接访问 `/media/webui_failure_screenshots/execution_16/single_case.png` 返回 200、`image/png`、52115 bytes。证据为 [anonymous-report-check.json](../../backend/logs/api-regression/20260915-batch3-lxupvC/anonymous-report-check.json)。

根因是 `backend/config/urls.py` 的 Django static media route 绕过认证截图 view。现改为受限 public-media serve：URL 有界重复解码并规范化，原始首段与 resolved 私有子树均使用 `casefold`，再以 `realpath/commonpath` 拒绝 traversal、大小写变体、目录/文件 symlink 和跳出 `MEDIA_ROOT` 的外部 symlink；目录不展示，普通头像仍可读取。匿名和已登录用户直读私有截图均为 404，截图鉴权 API 为匿名 401、非成员 404、owner 200。

生产 Nginx deny/`autoindex off`/`disable_symlinks on` 要求已写入安装文档；没有扫描、搬移或删除真实历史截图。

主代理重启后验证匿名原始路径、点路径、编码和大小写变体均为 404，管理员直读原始路径也为 404；鉴权截图仍为 200，文件字节数和 SHA-256 未变化。证据见第三批汇总。

### 2.5 viewer 角色语义风险

`ProjectMember.role` 与能力字段独立；模型默认 `can_execute_tests=true`、`can_view_reports=true`，`ProjectMemberCreateSerializer` 也不按 `role=viewer` 派生或覆盖能力。当前前端没有独立成员创建表单，“管理成员”仍为占位跳转。

因此仅提交 `role=viewer` 不是 view-only。调用方必须显式提交能力字段；本轮夹具已显式关闭执行权。该项记录为配置/交互风险，不把默认 viewer 当作权限绕过证据。

## 3. ERR 异常与恢复结果

新增 API 工作区边界专项覆盖：

- 模型 401 → `MODEL_AUTH_ERROR`，不可直接重试。
- 模型 429 → `MODEL_RATE_LIMITED`；500/503 → `MODEL_UNAVAILABLE`；504 → `MODEL_TIMEOUT`；overload → `MODEL_OVERLOADED`，均形成可重试的固定公开分类。
- 每种故障都进入 failed/finished 终态，保留原草稿，不产生候选，不调用业务 requests runner，不公开原始异常文本。
- 流式输出中途先回调部分内容再抛 503：任务进入 failed/finished，既有候选与上一轮 HTTP 422 失败证据保持不变，公开结果不含替身中的敏感错误片段。

结合本轮复跑的既有专项：

| 清单 | 结论 | 本轮证据 |
| --- | --- | --- |
| ERR-01 | 通过（隔离） | 新增 401/429/500/503/504/overload/流中断矩阵；既有空响应/无效候选受限三轮复跑通过 |
| ERR-02 | 通过（隔离） | 排队与执行预算、总 deadline、hard timeout 未知边界通过；不代表真实长流服务 |
| ERR-03 | 通过（隔离） | 取消、迟到结果、后续请求停止通过 |
| ERR-04 | 通过（隔离） | 历史、检查点、执行记录恢复通过；UI 切换沿用第一批证据，本轮未重复全量浏览器脚本 |
| ERR-05 | 通过（隔离） | 写请求发出后检查点失败/超时保留 unknown 和不安全重放证据 |
| ERR-06 | 通过（隔离） | 队列派发失败进入 error；持久化失败在请求前停止；未停止真实 worker |
| ERR-07 | 通过（隔离） | revision 冲突、重复 debug/执行/重试及原结果保留通过 |

## 4. REC 执行记录结果

- 工作区调试/生成记录可列出并读取，来源与正式执行区分；无环境、无正式用例 ID 的记录仍可见。
- 检查点、失败/跳过/取消/运行异常、快照冻结、重复派发和队列失败定向测试通过。
- 报告权限 queryset、工作区历史和标准报告在重新生成后保留通过；非成员原生 API 报告为 404，view-only 报告为 200。
- 主代理另行完成 REC-03 终态 5 组：empty/all-skipped=`skipped` 0%，stopped/exception=`error` 50%，incomplete=`incomplete` 50%，均未显示运行中或通过。证据：[terminal-report-matrix.json](../../backend/logs/api-regression/20260915-batch3-lxupvC/terminal-report-matrix.json)。
- 通知、投递、平台报告和调度可靠性另有 41 tests passed；与本报告此前 120 项可能部分重叠，因此不机械相加。SMTP 已连接并发信，用户已确认收到接收组测试邮件；报告链接邮件仍待补测。

## 5. 验证命令与结果

均从项目根目录执行，所有 Django runner 先调用 `test_api_workspace_browser.bootstrap(tempdir)` 并安装 loopback-only socket patch；未使用 `manage.py test`。

```bash
backend/.venv/bin/python backend/scripts/test_api_access_failures_browser.py
```

- 修复后浏览器权限矩阵：37 条结果记录，其中 35 个 HTTP 检查为 200×1（授权 viewer report）、403×21、404×13；`failures=[]`，非授权 owner marker 为 0，四类任务派发为 0，数据库读回不变。
- 前端无本轮新增改动，因此未重复构建；此前同一基线构建通过，3241 modules，仅有既有 chunk 体积提示。

隔离 Django 定向 runner：

- 权限 labels：`api_testing.test_acceptance_faults.APIProjectAccessBoundaryTests`、`api_testing.test_execution_visibility.APITestExecutionVisibilityTests`，**10 tests passed**。
- media label：`common.test_public_media`，**6 tests passed**。
- 主代理独立小集：common、execution visibility、browser source handoff 共 **12 tests passed**（早于 media 两个拆分用例，不与上述计数合并）。
- 代码修复前已完成的 provider/reliability/execution visibility/snapshots/checkpoints、workspace 及 requests 精选合计 **120 个不重复定向测试通过**；本轮未重跑。

没有重跑全部 321 项。所有增量 runner 都在 bootstrap 临时 SQLite 和 loopback-only socket 下执行。

## 6. 文件、回退与剩余缺口

本轮业务/部署修改：

- `backend/apps/api_testing/views.py`
- `backend/apps/common/media.py`
- `backend/config/urls.py`
- `docs/installation.md`

测试/报告修改：

- `backend/apps/api_testing/test_acceptance_faults.py`
- `backend/apps/api_testing/test_execution_visibility.py`
- `backend/apps/common/test_public_media.py`
- `backend/scripts/test_api_access_failures_browser.py`
- `docs/verification/2026-09-15-api-access-failures.md`

运行证据位于被忽略的 `backend/logs/`，不含账号密码或 token。回退 views 或 media guard 会重新打开已确认缺陷，因此不能在没有等价保护时回退。本 Agent 未提交、未重启。

剩余项：

1. 主代理已重启并使用真实账号复测读取接口及 execution 16 的图片路径；修改/删除越权在隔离夹具验证，不对实际数据发送破坏性请求。
2. 若生产反向代理直接托管 `MEDIA_ROOT`，需实际落地并验证安装文档中的 deny 规则，防止绕过 Django guard。
3. 接收组邮件已确认送达，UI 共享冒烟已合并；跨设备报告链接邮件仍待配置确认和发送授权。

最终建议：**本次权限与截图修复通过；剩余报告链接邮件事项单独跟进，不因此否认已取得的权限、真实 UI 和邮件送达证据，也不宣称全部清单已通过。**
