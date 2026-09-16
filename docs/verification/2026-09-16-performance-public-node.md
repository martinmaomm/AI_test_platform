# 公网性能节点真实联调

日期：2026-09-16。执行引擎基线 `b5f908a`，本次未修改压测业务代码。授权范围为公网 Linux 节点安装、域名端口联通和每轮1用户、最多5秒的临时 HTTP 目标验证。

## 部署结果

- 平台：Mac `192.168.31.205`；公网域名 `home.maoyijiu.top`。
- TCP18443：Caddy2.11.4私有CA HTTPS，仅允许节点登记和心跳两条POST路由；反代本机8000。
- TCP9443：控制器按运行启动 stunnel mTLS，原始 Locust RPC不对公网开放。
- 节点：授权的公网 Ubuntu20.04 amd64服务器，Docker26.1.3；独立非root容器 `automation-performance-node`，Agent0.2.0、协议2、Locust2.43.3。
- 节点身份独立volume持久化；CPU上限1核、内存512MiB、只读根文件系统、无新增对外端口。现有 `tm` 容器、Nginx、Xray和SSH配置未修改。
- 实际平台项目：`性能测试公网联调`（id3）；节点：`公网节点-MartinVPS`。
- 实际 `backend/.env` 只增加性能执行参数，修改前保留私有备份；后端与Celery空闲时重启，HTTP401与Celery ping/pong检查通过。

## 实测

| 项目 | 结果 |
| --- | --- |
| VPS → 平台HTTPS | 私有CA校验成功；无需VPN |
| 非节点管理路径 | `/api/v1/projects/` 返回404 |
| 空登记参数 / 无身份心跳 | 分别400 / 401 |
| 未提供私有CA | curl以证书不受信任拒绝连接（exit60），未关闭验证 |
| 平台控制器与节点心跳 | 在线；版本匹配 |
| 正常运行 | `8cfd1441-0ca3-4941-a3d4-fec7b9d23c3a`：completed，5请求，0失败，最终统计完整 |
| 手动停止 | `18e337c6-e8ec-4a7f-ae98-b5c0f2b1a235`：cancelled，4请求，0失败，最终统计完整 |
| 重复提交相同request_id | 返回原运行id，HTTP200，无额外运行 |
| 请求计数核对 | 临时fixture初始0，两轮后9；两次后续读取保持9，与平台5+4完全一致 |
| 进程与密钥收尾 | 无活动运行；9443空闲关闭；节点仅剩Agent/docker-init；平台每轮临时私钥已清理 |
| 节点重启 | 原node_id/身份volume保留，重新上线；fixture计数未增加，没有重放旧任务 |
| 真实前端展示 | 实际后端数据的“节点管理”与成功运行详情可见，0 pageerror、0 API错误；未模拟API、未点击再次发压 |

目标为节点专用 Docker bridge 网络中的 `performance-fixture.test:8080/probe`，没有发布宿主机端口。没有对NAS、商城、互联网业务接口发压。展示的平均响应约2.80ms、P95约5ms是这个**节点同机fixture**的值，不代表公网业务性能；公网验证的是控制与mTLS协调链路。

截图保存在本机私有工作目录 `backend/temp/performance-public-acceptance/node-online.png`、`run-completed.png`；原始两轮详情JSON同目录保存。此目录已被Git忽略，不提交令牌、证书私钥、`.env`备份或管理员浏览器状态。浏览器只使用临时内存会话，未保存认证storage_state。

## 留存与边界

- 节点继续在线；临时fixture容器已停止（可显式启动后重跑）；平台计划和两条记录保留供用户验收。停掉的fixture不是可直接运行的正式目标。
- Mac的Gateway/Controller未设置开机自启；重启机器后需要额外启动这两个进程。公网节点容器使用 `unless-stopped`，Docker服务已启用。
- 本次只证明单公网节点、小负载、顺序运行和停止可用；不证明高并发容量、多节点混合组网、长时间稳定性或真实跨网断线恢复。最后一项仍需独立故障注入验收。
- 服务器内核提示不支持Docker swap limit；memory上限仍生效，但不能宣称已限制容器swap。
- 旧Ubuntu宿主机安全维护、入口限流、长日志管理与Mac进程守护列入后续运维工作，不在本轮升级系统或影响其他服务。
- 可复现步骤与排障见[公网节点部署说明](../../deploy/performance/README.md)。
