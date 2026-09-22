# 商品搜索 total 差异诊断

## 当前结论：差异尚未定位

用户随后重新在被测站点网页和网页版 Postman 验证，仍需搜索“RIO西柚伏特加风味鸡尾酒”才能命中，未发现工具返回的另一个名称。此前将工具侧结果直接归因为“商品已改名”的判断证据不足，现撤回该结论；不能据此要求用户更改查询词。

2026-09-22 11:07（北京时间）的工具结果中，`prodId=41` 对应名称为“挺好的金额还怪别人替换”，价格 `9.9`。同一接口只替换 `prodName`，原查询词返回 `total=0`，另一个名称返回 `total=1`。这只能证明当时工具侧收到上述响应，尚不能证明用户访问的是相同的数据来源，也没有证据表明用户截图为历史响应。

2026-09-22 12:06 又用独立 curl 复核，工具仍返回上述结果，连接 IP 显示为 `198.18.0.87`。原始响应和请求元数据保存在 `backend/temp/performance-search-recheck/original.json`、`observed.json`、`request-metadata.json`；未包含认证头或 Cookie。需要继续核实访问链路及用户请求与工具请求的真实差异。未修改现场计划或商品数据，未启动单用户验证或压测，没有修改节点代码。

## 现场记录

- 单用户验证 `ab5ec810-acd1-4263-910d-4d5acdae8f56`，创建于 2026-09-22 09:58:32，计划“柠檬班登录搜索”，节点 `osjp`。
- 第二步 URL 与用户提供的完整 URL 逐字符一致；8 个 Query 参数与计划一致，查询词没有二次编码。
- Authorization 使用正确的 Bearer 前缀，值与第一步提取的 token 一致；Cookie 包含登录响应中的会话。凭证值不写入此记录。
- 第二步耗时 164.34 ms，HTTP 200，响应完整；数据库和保留的 Master 原始指标文件均为 `total=0`、`records=[]`。

## 只读对照

接口为 `GET http://shop.lemonban.com:8107/search/searchProdPage`。对照保留 `categoryId=`、`current=1`、`isAllProdType=true`、`orderBy=0`、`size=12`、`sort=0`、`st=0`，仅替换 `prodName`。

| 访问方式 | prodName | HTTP | total | 命中商品 |
| --- | --- | --- | --- | --- |
| 域名 | RIO西柚伏特加风味鸡尾酒 | 200 | 0 | 无 |
| 域名 | 挺好的金额还怪别人替换 | 200 | 1 | ID 41，价格 9.9 |
| 解析 IP + 原 Host | RIO西柚伏特加风味鸡尾酒 | 200 | 0 | 无 |
| 解析 IP + 原 Host | 挺好的金额还怪别人替换 | 200 | 1 | ID 41，价格 9.9 |

对照服务器响应 Date 均为 `Tue, 22 Sep 2026 03:07:06 GMT`。公共 DNS 查询返回 `117.72.83.248`；直接访问该 IP 时保留 `Host: shop.lemonban.com:8107`，结果与域名方式一致。

辅助检查中，补充 JSON Content-Type、换浏览器 User-Agent、修改 Accept、关闭压缩、移除认证与 Cookie，都没有使旧商品名命中。仅分页查询返回 33 条，完整列表中的 ID 41 已是当前名称。只用原商品名或 ASCII 关键字 RIO 查询同样为空。

后续应对照同一时刻用户成功请求与工具请求的原始响应、请求头及实际访问链路。在来源差异确认前保留用户原有查询词，不把修改筛选条件当作修复。

## 两台设备浏览器与网络对照

用户补充的两张浏览器截图均查询“挺好的金额还怪别人替换”：第一台设备返回 `total=0`，Mac mini 浏览器返回 `total=1`、商品 ID 41。这独立确认了两台设备观察到的数据差异，仍不证明商品在被测站点上被统一改名。

- 当前工具实际运行主机为 macOS 的 Mac mini。系统 HTTP/HTTPS 代理为 `127.0.0.1:1088`，该监听属于 Shadowrocket 的 MacPacketTunnel 进程；Shadowrocket 正在运行。
- Mac mini 默认 DNS 为 `utun4` 上的 `198.18.0.2`，域名通过系统解析得到 `198.18.0.87`；该地址不能当作被测服务的真实公网地址。
- 用户在第一台设备使用阿里 DNS `223.5.5.5`，得到 `117.72.83.248`。Mac mini 向 `223.5.5.5`、`114.114.114.114`、`1.1.1.1` 查询也得到该 A 记录。没有观察到两边公网 DNS A 记录不同。
- Mac mini 的普通路由走 `utun4`，指定 `en0` 的路由查询指向本地网关。单次 curl 使用 `--interface en0 --resolve shop.lemonban.com:8107:117.72.83.248 --noproxy '*'`，连接地址为该公网 IP，仍是原名称 0、另一个名称 1。此结果不足以认定代理导致差异，也不能仅凭网卡绑定选项确认所有透明网络层均被绕过。
- Mac mini 固定该公网 IP、不传 Cookie、添加 `Cache-Control: no-cache` 查询另一名称，仍返回 1；响应日期为 `Tue, 22 Sep 2026 04:30:40 GMT`，响应 Cache-Control 为 `no-cache, no-store, max-age=0, must-revalidate`。

用户随后在第一台 Windows 设备执行了固定 IP、不带浏览器 Cookie、添加 `Cache-Control: no-cache` 的 curl 查询，返回 `total=1`、商品 ID 41、名称“挺好的金额还怪别人替换”，并显示 `REMOTE_IP=117.72.83.248`。该结果与 Mac mini 一致。

因此目前的差异集中在第一台设备的浏览器请求与同设备 curl 请求之间，尚不能确定是会话、扩展或其他请求差异。未更改系统代理、VPN、DNS 或被测服务配置。

## Windows 浏览器停用缓存后的补充对照

用户进一步确认：

- 浏览器勾选“停用缓存”并刷新后仍返回 `total=0`，Remote Address 为 `117.72.83.248:8107`，请求列表 Size 显示 `603 B`。
- 同一 Windows 设备直接使用普通 curl 请求该搜索 URL，同样可以返回商品，不仅是此前固定 IP 的 curl 能命中。
- 两种请求处于相同网络环境，用户确认未开启代理。

上述信息不支持继续将普通浏览器缓存或两台设备的 DNS 差异作为已确认原因。`603 B` 是用户报告的 Network Size，并非已经核实的响应正文长度；仅凭大小和相同远程 IP，也不能证明两次请求的 URL、请求头、会话及后端处理完全相同。

下一步只对照两项：Windows 无痕 / InPrivate 窗口打开给定的完整百分号编码 URL 后的 `total`，以及原窗口失败请求在 Network → Headers → General 中的实际 Request URL。前者用于检查差异是否与现有浏览器环境有关，后者用于核对查询参数与编码；即便无痕窗口成功，也不能单凭该结果断定具体是 Cookie 或扩展。当前等待用户提供，不要求发送 Cookie、令牌或登录信息。

用户消息中的 curl URL 带有 Markdown 链接表示；不能据此认定实际命令或浏览器请求包含反斜杠，需以上述 Network Request URL 为准。
