# 商品搜索 total 差异诊断

后续用户已授权修复性能探索的信息保留，完整请求头采集及草稿传递现已实现并在本机生效，见 [完整采集验收](2026-09-22-performance-full-capture.md)。以下保留定位时的现场证据与判断边界。

## 当前结论：Accept-Language 缺失能复现搜索结果差异

2026-09-22 12:48–12:49（北京时间）已通过单变量 HTTP 对照定位到 `Accept-Language`。同一目标 IP `117.72.83.248`、相同 URL、无认证和 Cookie，只改变这个请求头，原商品名“RIO西柚伏特加风味鸡尾酒”稳定地从 `total=0` 变为 `total=1`。复测前后移除、恢复语言头，结果相应回到 0、1；仅修改 Windows User-Agent 或页面导航 Accept 不能产生这一变化。

| Accept-Language | 原商品名的 total | 另一名称的 total |
| --- | --- | --- |
| 不发送 | 0 | 1 |
| `zh-CN,zh;q=0.9`（用户 Windows 实际值） | 1 | 0 |
| `zh-CN,zh;q=0.9,en;q=0.8` | 1 | 0 |
| `en-US,en;q=0.9` | 1 | 0 |
| `zh-CN` | 1 | 未单独验证 |

原商品名命中时仍是商品 ID 41，价格 9.9。英文语言头也可命中原商品名，因此不能将结果简化为“中文商品与英文商品不同”；已确认的是请求头的有无及上述具体值会改变这个接口的搜索结果，目标服务内部的语言解析、查询或路由逻辑尚无后端证据。

现场计划 ID 2 的两个步骤，以及运行 `ab5ec810-acd1-4263-910d-4d5acdae8f56` 的实际请求头，均没有 `Accept-Language`。当前计划可保留原商品名、token 引用及断言，在第二步请求头补充与用户 Windows 浏览器一致且已验证的 `Accept-Language: zh-CN,zh;q=0.9`。这次只完成对照和诊断，未修改计划、业务代码或运行节点，没有重新启动单用户验证或压测。

平台侧也发现确定的保留缺口：`backend/apps/api_testing/browser_discovery.py` 的 `_SAFE_HEADER` 只有 `content-type` 和 `accept`；`_header_value()` 因此会过滤 `Accept-Language`。压测草稿编译读取过滤后的 `observed_request.headers`，不会自动恢复这一项。后续应保留实际采集到的语言头并验证样本、草稿、执行的完整链路，而非给所有网站硬编码中文。已记入 `docs/TODO-OPT.md`。

原始无凭据对照结果保存在 `backend/temp/performance-search-recheck/windows-browser-header-comparison.json` 和 `accept-language-isolated-comparison.json`。以下 Windows PowerShell 命令可重复对照，只改变一个请求头：

```powershell
$searchUrl = 'http://shop.lemonban.com:8107/search/searchProdPage?categoryId=&current=1&isAllProdType=true&orderBy=0&size=12&sort=0&st=0&prodName=RIO%E8%A5%BF%E6%9F%9A%E4%BC%8F%E7%89%B9%E5%8A%A0%E9%A3%8E%E5%91%B3%E9%B8%A1%E5%B0%BE%E9%85%92'
curl.exe --noproxy '*' --resolve 'shop.lemonban.com:8107:117.72.83.248' --connect-timeout 5 --max-time 10 -sS "$searchUrl"
curl.exe --noproxy '*' --resolve 'shop.lemonban.com:8107:117.72.83.248' --connect-timeout 5 --max-time 10 -sS -H 'Accept-Language: zh-CN,zh;q=0.9' "$searchUrl"
```

用户确认 Windows 无痕窗口仍返回 `total=0`，提供的 Network Request URL 与对照 URL 一致，随后确认原请求的 `Accept-Language` 为 `zh-CN,zh;q=0.9`。12:52 使用这一确切值复测：原商品名返回 1，另一名称返回 0，原始结果保存在 `backend/temp/performance-search-recheck/windows-exact-accept-language.json`。Windows 浏览器、curl 与节点之间的请求差异已有对应证据；Mac mini 浏览器的实际语言头尚未取得，不能把其具体语言配置写成已核实事实。

## 此前判断与排查记录

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

随后对照 Windows 无痕 / InPrivate 窗口和原窗口 Network Request URL：用户报告无痕仍为 0，实际 URL 与给定百分号编码 URL 一致。现有 Cookie 差异与 URL 编码差异均缺少支持，后续语言请求头对照结果见本文当前结论。

用户消息中的 curl URL 带有 Markdown 链接表示；不能据此认定实际命令或浏览器请求包含反斜杠，需以上述 Network Request URL 为准。
