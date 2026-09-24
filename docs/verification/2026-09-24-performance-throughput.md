# 运行结果吞吐量验收

## 改动

运行详情将平均 RPS 命名为“平均请求吞吐量（次/秒）”，新增采样区间峰值、各接口和各节点的平均吞吐量。趋势同时显示全程累计平均及区间平均，提示区间长度；切换节点时全部指标使用对应节点数据，不将全局值用于缺数据节点。

详情 API 在运行与节点对象增加只读 `throughput`，列表不增加序列。计算来自已有累计指标和保留的最近 400 个样本，不修改历史数据、节点协议或镜像，无迁移和节点升级。

接口吞吐量使用同轮完整统计时长。区间值使用相邻有效累计计数增量与时间增量。重复快照不制造新区间；正常空闲区间为零，未知显示 `-`。缺失、计数回退、时间倒序、同刻冲突及不同计时来源会断开计算；异常点不会成为下一区间的基线。峰值只代表保留窗口内的最高区间平均，不代表瞬时最高 QPS 或服务容量。

新 AI 分析复用相同计算结果，增加区间峰值和趋势、接口吞吐量证据，明确采样和抽稀局限。区间证据只包含数值 `sample_index/rps/interval_seconds`，不传递原始时间字符串或 URL。既有分析正文与记录保留。

## 验证

- 性能模块 SQLite 隔离回归 **181 项通过**；运行前迁移状态检查无漂移。没有访问实际数据库、Redis、模型或被测网站。
- 独立复核：吞吐量及 AI 证据专项 23 项通过，另验证 6 组反例，包括坏点制造峰值、重复终态、真实空闲、时钟切换、旧版时间回退及敏感时间字符串剔除。
- 前端吞吐量/分析状态 11 项、性能工作区 23 项、多节点渲染 1 项通过；生产构建通过，保留既有资源体积提示。
- `git diff --check` 通过。
- 实际已认证详情 API 与 Chrome 页面验证通过：已有运行平均吞吐量约 2.76 次/秒、保留区间峰值约 4.95 次/秒，共 27 个有效区间；两个接口均值约 0.06 和 2.70 次/秒，与累计请求数除完整统计时长一致。
- 浏览器核对真实历史的总览、双吞吐量曲线、接口列及两节点切换；另用只读响应替身验证节点缺少派生数据时不回退全局，以及区间不足显示未知。无页面异常、写请求或外部请求。截图在本地忽略目录。

复验命令：

```bash
cd backend
.venv/bin/python scripts/test_webui_generation_offline.py performance_testing
cd ../frontend
node --test tests/performanceThroughput.test.js tests/performanceAnalysisState.test.js
node --test tests/performanceWorkspace.test.js
node --test tests/performanceMultiNodeRender.test.js
npm run build
```

本地激活前后数据哈希探针和浏览器检查脚本保存在 Git 忽略的 `backend/temp/performance-throughput/`。本次未发起真实压测或调用模型，不能据此宣称吞吐量是目标系统的最大容量。

## 启用

在活动压测/分析为 0，且 Celery active/reserved/scheduled 均为空时，通过服务管理器重启 backend、celery。backend、celery、controller、caddy 均健康、重试为 0；未重启 controller/caddy。原分析、压测运行、参与节点、计划及节点身份哈希均一致。无需数据库迁移，已有节点无需更新。

如需回退，恢复本次修改前代码并重启 backend、celery 即可，不涉及数据回滚。
