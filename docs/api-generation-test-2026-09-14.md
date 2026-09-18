# API 自动化生成隔离测试报告（2026-09-14）

本轮完成的是当前 automation-platform 代码的内部回归及隔离浏览器验收。真实模型、线上数据库、Celery/Redis 链路和商城接口未执行，不能将本报告称为真实业务端到端全量通过。测试目标依据当前项目代码与“AI测试平台开发”任务定位；用户对真实环境的选择仍待回复。

## 已验证结果

| 范围 | 本轮结果 | 边界 |
| --- | --- | --- |
| API 模块 Django/unittest | 200/200 通过 | 临时 SQLite；只允许回环网络，包含本地真实 HTTPS 证书测试 |
| requests 执行与账号保护函数测试 | 57/57 通过 | pytest；模拟业务 HTTP |
| 前端单测 | 289/289 通过 | 包含全站前端共享逻辑，并非 289 个真实 API 场景 |
| 前端生产构建 | 通过 | 保留大包体积提示 |
| 浏览器主流程及专项 | 11 项流程完成 | 9 项原脚本直接通过，2 项需临时校正测试定位器后通过 |
| 独立验证保护脚本 | 临时适配回调后通过 | 原脚本尚未修复 |
| 两项独立多场景/依赖脚本 | 未完整通过 | 回调失配及旧断言预期，详见下文 |

浏览器覆盖：生成、采用候选、调试、修复、保存、Python 下载；修改后重新验证；端点用例；多场景；取消和候选保留；断言类型；键值参数；长度断言；来源导航；模型隔离、输入保留、重复保存；失败证据和人工编辑。

浏览器运行真实 Vue 与 Django，使用临时数据库、模拟模型/业务接口，限定回环请求。登录通过测试令牌注入，不覆盖真实账号登录；Celery 调度使用测试替身，不代表现有 worker 的验证结果。主流程截图已人工查看，未见明显布局遮挡。

## 发现的测试维护问题

1. **P2：三个离线验收脚本的执行器替身没有同步进度回调契约。**
   - 文件：`test_api_multi_scenario_offline.py`、`test_api_scenario_dependencies_offline.py`、`test_api_verification_guards_offline.py`。
   - 直接运行出现 `run_case() got an unexpected keyword argument 'on_progress'`。
   - 产品的 `requests_runner` 接受 `on_progress/should_cancel`，底层 `run_case` 使用 `on_checkpoint`；旧替身直接透传造成失败。
   - 仅在临时测试入口中按现有浏览器主测试的方式桥接回调后，保护专项通过；原仓库文件未改。

2. **P2：两项浏览器脚本定位器已过时。**
   - interactions 用模糊按钮名同时匹配“采用候选并替换草稿”和其说明按钮；改用精确名称的临时版本通过完整流程。
   - scenario_failure 将第一个 combobox 当作关联端点，当前第一个实际是新增的“步骤阶段”；按“关联 API 端点”表单项定位的临时版本通过完整流程。
   - 这是测试定位失配，不能依据原超时认定产品按钮失效。

3. **P2：多场景/依赖专项仍有旧行为预期，暂未判定为产品缺陷。**
   - 多场景脚本要求全部子场景排队时间与根任务完全相同；当前代码在各子场景派发时设置其排队时间。前面的场景状态及请求行为断言通过，脚本在时间相等断言停止。
   - 依赖脚本要求削弱受保护断言后自动再修复并通过；当前代码明确返回 `needs_review`，未执行削弱断言的候选。这与 README 所述人工审阅边界一致。
   - 本轮没有删掉这些断言来把原脚本标为通过；后续应先确认应保留的产品契约，再维护测试。

## 待完成的真实验收

需用户确认被测测试环境及可操作范围后，再验证真实模型场景规划/生成、真实 worker 派发、真实接口执行、正式保存后独立复跑，以及导出 Python 的独立真实执行。项目文档记录的商城地址为 `http://192.168.31.188:9999`，本轮没有对该地址发起业务操作。

## 证据与变更

证据目录：[api-generation-test-20260914-191403](/Users/maoyijiu/Documents/tg-work/automation-platform/backend/logs/api-generation-test-20260914-191403)。

- [生成验证截图](/Users/maoyijiu/Documents/tg-work/automation-platform/backend/logs/api-generation-test-20260914-191403/generation-verified.png)
- [失败证据截图](/Users/maoyijiu/Documents/tg-work/automation-platform/backend/logs/api-generation-test-20260914-191403/scenario-failure-evidence.png)
- [人工编辑截图](/Users/maoyijiu/Documents/tg-work/automation-platform/backend/logs/api-generation-test-20260914-191403/scenario-manual-editor.png)

未修改业务代码或仓库测试代码，未改数据库结构、重启服务、发送邮件或刷写 ESP32。仅生成前端构建产物、测试日志/截图和本报告；隔离数据库自动清理。`git diff --check` 通过；原有三个浏览器 ZIP 保留。
