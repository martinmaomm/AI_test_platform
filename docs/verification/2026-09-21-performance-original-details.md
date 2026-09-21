# 执行详情显示原始凭证值

## 行为与范围

按用户要求，单用户验证的执行详情显示密码、令牌、Authorization、Cookie、Set-Cookie 等原值，用于核对实际请求和排查脚本。变更覆盖节点采集、Master 汇总、控制器持久化和详情序列化，取消这些环节对步骤明细的字段名识别、已知凭证替换和文本模式脱敏。

请求 URL、请求头和请求体、响应头和响应正文、断言期望/实际值、响应提取值均保持原值。原始文本不再为脱敏而重新解析或格式化。前端继续通过 Vue 文本插值显示内容，响应中的 HTML/脚本不会执行。

详情仍使用既有 REPORT 权限。READ 列表不携带步骤正文，趋势采样不重复保存它；列表共用的单用户验证失败摘要仍沿用原有处理。网页探索的采集脱敏、节点注册凭证处理和运行日志行为不在本次改动范围内。正式压测继续仅记录聚合统计与失败样本。

保留每份报告最多 20 步、每步最多 50 条断言和 50 项提取，以及现有字段字节数、嵌套深度和总大小限制；超长内容仍明确标为截断。历史记录里已经替换成 `<redacted>` 的值无法还原，配套更新后重新运行单用户验证才会获得原值。

## 验证

以下命令均在隔离工作树中运行；Python 依赖复用主工作区虚拟环境，源码及测试数据库独立。

```bash
PYTHONPATH=performance-node/src backend/.venv/bin/python -m unittest discover -s performance-node/tests -v
backend/.venv/bin/python backend/scripts/test_webui_generation_offline.py \
  scripts.test_performance_controller performance_testing.tests.test_runs \
  performance_testing.tests.test_installation
backend/.venv/bin/python backend/scripts/test_webui_generation_offline.py \
  scripts.test_performance_execution_contract
cd frontend && npm run build
cd .. && backend/.venv/bin/python backend/scripts/test_performance_validation_browser.py
```

- 节点完整测试 62 项通过；之后新增原始文本与共用摘要边界测试，定向重跑运行时 15 项全部通过。
- 隔离 Django 55 项通过，无迁移变化。验证报告权限、列表不包含原始令牌、详情包含原值、截断和趋势限制、0.3.1 节点升级提示及发行契约。
- 真实 HTTPS Agent → mTLS Locust → 回环 HTTP 夹具端到端通过（1 项，34.417 秒）。验证一次登录和主请求的密码、Authorization、Cookie、Set-Cookie、响应令牌与提取值进入数据库及详情接口；正式压测、优雅停止、失败断言、后续步骤跳过和验证门禁同时通过。
- 前端生产构建通过，保留既有大分块提示。真实 Vue/Django/Chrome 浏览器检查通过，验证请求密码、URL 令牌、Cookie、Set-Cookie、Authorization、提取原值、类型差异、截断、历史记录提示、正式压测视图及 HTML 转义，页面错误和外部请求均为 0。
- 已查看截图 `backend/temp/performance-validation-browser/raw-details.png`。截图仅包含测试夹具的虚构凭证。

没有访问现场业务目标、修改现场计划或历史执行记录。

## 发行与生效条件

固定运行模板已变化，节点源码、包版本、镜像标签和后端契约同步提升至 0.3.2；管理协议与快照协议仍为 2。旧节点可继续发送心跳，新平台在执行前明确提示升级，不放宽固定模板逐字节校验。

改动在 `codex/validation-raw-details` 隔离工作树中完成。当前现场仍运行配套的 0.3.1 平台与节点，尚未切换；需要发布两种架构的 0.3.2 镜像、合入并切换本机平台发行目录，再升级空闲节点并重新验证。公共发布及现场节点升级完成前，不能将本机夹具验证视为现场生效。

## 本地安装包准备

功能提交为 `c3678b4`。通过已核实的本机 Colima Docker 端点构建 amd64、arm64 镜像；未修改其他容器。分别使用 `publish_performance_node_release.py --image ARCH=automation-platform-performance-node:0.3.2-ARCH --output backend/resource/performance-node-dockerhub-0.3.2` 生成本地归档，未传 `--registry` 或 `--publish-index`，没有执行公共推送。

两种架构均通过非 root 镜像配置、隔离容器中的 Agent/Locust 版本和模板哈希校验、归档配置一致性及所有镜像层审计。归档与清单保存在 Git 忽略的 `backend/resource/performance-node-dockerhub-0.3.2/` 中。

- Agent：0.3.2；Locust：2.43.3；协议：2。
- 固定运行时 SHA-256：`a4c74ee52140e1e7a9c13abd71c8597d9e1dd8c173381200f19382492f9969ae`。
- amd64：85,092,993 字节；归档 SHA-256 `f80ff20f8853fb2d8a7d63beb01c7ffbe268d85a270688a617408d9c0ab8c54e`。
- arm64：84,366,220 字节；归档 SHA-256 `3a97bd129baae3fe771e18446b5435406200251ddaabe7b56cf3ca24fac111ca`。

后续公共发行应复用上述已验证的镜像和归档，再进行匿名拉取、索引及平台入口验证。切换平台前确认无活动任务，保留现有 0.3.1 发行目录作为回退；升级远端节点时保留原身份卷，不重新注册。当前主工作区仍只保留原有三个未跟踪的浏览器 ZIP，本次未提交这些文件。
