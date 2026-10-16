# 主图详情受理待核实：KIE 代理连接失败

2026-10-16；新隔离任务 detail-submission-stuck。生产只读诊断，本轮没有生产配置变更、重启、提交或部署。

## 现场与根因

用户截图的 3 张图停在“正在核实供应商受理结果”。生产数据库确认三个任务都为 uncertain、external_task_id 为空，日志同一时段记录 ConnectError；前一批相同故障任务等待 15 分钟后退款，自动恢复又创建新尝试，仍遇到相同连接故障。不是前端漏刷新，也不是 150 槽位满额。

生产后端 systemd 环境设置 HTTP_PROXY/HTTPS_PROXY=http://127.0.0.1:7890。只读 GET 实测：api.kie.ai 直连得到 HTTP 404（正常建立 TLS/HTTP 连接），走代理报 ConnectError；www.aliyun.com 直连和走代理均 HTTP 200。代理端口在监听，因此是该代理到 KIE 的连接路径异常，不能误报整个代理进程宕机。没有发送生图 POST 或触发付费验证。

第二个原因是应用在持久化 dispatch 标记后，将包括连接建立失败在内的异常统一按受理未知处理。标记记录的是允许发送，不能证明 HTTP 请求实际已发出。单次提交适配器不会重试 POST；HTTPX ConnectError、ConnectTimeout、PoolTimeout 证明该次请求未进入发送阶段，可以确定失败。ReadTimeout、ReadError、WriteError、未识别异常仍不能作此推断。

## 本地修复

ChatImageLifecycle 在当前提交阶段仍为 submitting 且遇到上述连接/池异常时，使用既有结果结算流程记录 SUBMISSION_CONNECTION_FAILED、退款并释放槽位。不重发 POST，不修改积分规则、SQL 或回调匹配。其他不确定异常保留原 uncertain 策略。历史 uncertain 任务没有可用于安全分类的持久化错误事实，不按日志自动改写或重置。

新增回归使用真实 KIE adapter/client 编解码、真实 PostgreSQL/Redis，仅替换 HTTP 传输边界。分别覆盖六种异常、每次只发起一次传输、连接错误及时结算/一次退款/槽位释放、重启和重复结果不重扣退；读写错误保留 pending 账本及 uncertain。旧代码 3 项失败、3 项通过，修复后 6 项通过。日志 /tmp/everydayai-detail-submission-stuck/connect-before.log、connect-after.log。

## 待授权生产操作

让 KIE 恢复正常提交还需修复其网络路径。推荐只为后端的 api.kie.ai 增加 NO_PROXY/no_proxy 绕行，保留其他流量原代理路由；按受控运维保存原配置，重启后端，再使用进程实际环境只读 GET 验证 KIE 可达。不需要改供应商模型、不需要手动补扣或重复退款。该操作涉及生产配置写入和服务重启，尚未执行；代码也尚未部署。直连 GET 成功不等于真实生图验收成功。

完整相关后端回归 450 项通过（connect-full.log），含 150 满额/20 维护槽、异常恢复、多进程超时回调竞争与账务幂等；编译与 diff 检查通过。供应商传输为替身，不代表真实生图已恢复。


## 用户要求代理专项测试后的补证

再次生产只读测试确认：sing-box 为 active/running，本地 mixed 端口 7890/7891 均监听。路由将 api.kie.ai、openrouter.ai、generativelanguage.googleapis.com 发往海外 VLESS 节点，其余流量默认 direct；7891 入站则强制使用海外节点。此前阿里云经 7890 成功只说明本机转发/direct 正常，不能证明海外节点可用。

- 7890：KIE 直连 3/3 收到 HTTP 404，走代理 0/3 成功（全部 ConnectError）；阿里云直连与经代理均 3/3 HTTP 200。
- 直接 TCP 探测配置中的海外节点端口：ConnectionRefusedError / errno 111。
- 7891 强制海外节点：KIE、阿里云、Cloudflare 各三次，共 9/9 ConnectError。

结论：本机代理进程未停止，当前海外出口/节点端口不可用，不是只有 KIE 单站故障。拒绝连接本身不能区分远端服务停止、端口配置改变或中间网络主动拒绝，未擅自给这些更深层原因下结论；也未登录、重启或修改海外节点。建议后续修复/更换海外节点，或在获得授权后仅让 KIE 绕过该出口。测试没有发起生图 POST，没有调用付费模型。证据：/tmp/everydayai-detail-submission-stuck/proxy-probe.log、proxy-node-probe.log。


## 原节点恢复检查（用户已授权优先恢复原节点）

生产应用服务器到当前节点：SSH 22 端口可连接，代理 41122 端口 ConnectionRefusedError。现有本地 SSH 身份尝试登录该节点 root 被拒绝（publickey,password），应用服务器也没有可用的节点 SSH 私钥/配置。未尝试密码猜测、扫描其他端口或修改远端认证。

保存的 sing-box 配置中，当前 VLESS 节点仅有同一 IP:41122 配置，没有该节点可供核验的旧端口。较早备份指向另一个 Shadowsocks 订阅节点，不能当作当前原节点服务的恢复证据，未擅自切换或覆盖当前配置。单纯重启应用服务器上的 sing-box 不能解决远端端口拒绝，未作无依据重启。

因此原节点服务器并非完全失联，但代理服务/端口不可用的深层原因仍需节点服务器管理访问才能判断。恢复阻塞在 SSH 管理身份缺失，不能宣布节点永久不可恢复，也不能声称已恢复。需要用户提供原节点管理访问方式或在管理后台恢复代理服务，然后再按已有配置验证。未更换节点、未修改线上配置、未重启生产服务、未发起付费生成。


## 用户授权订阅节点池与自动故障切换（2026-10-16，生产代理配置已生效）

用户提供订阅并明确授权导入所有美国节点、单节点失败自动切换、全部失败提醒。使用直连方式读取，未转发订阅凭据给第三方转读服务。订阅有76个节点，其中16个美国VLESS节点；所有凭据仅在生产 /etc/sing-box/us-failover/ 受限文件和 sing-box 配置中保存，未纳入仓库。

实现 deploy/proxy/us_failover.py：保持已有7890/7891入站及KIE等既有域名路由，overseas-ss改为16节点selector。每节点增加一个仅127.0.0.1监听的检测入站，置顶规则固定指向该节点，避免把direct成功误算为海外节点健康。用GET https://api.kie.ai 判断实际目标连通性，200/204/404视为可用；不调用生图POST。每轮最多4个探测并行，每节点asyncio.timeout(6)总时限；HTTPX读超时不能代替总期限。优先保留当前健康节点，当前失败才顺序切换到下个健康节点。selector interrupt_exist_connections=false，不主动切断正在进行的连接，不重发任何任务或调用账务SQL。

systemd everydayai-us-proxy.timer 每轮结束30秒后再次检测，单轮最多约24秒节点检查加控制器通信，服务45秒总期限和flock防重叠。连续两轮全失败写all_failed；控制器/检测器错误写monitor_error；状态超过180秒视为监控停摆。非敏感状态文件 /var/lib/everydayai-us-proxy/status.json。曾误建当前Codex对话的heartbeat提醒；用户纠正要求后已删除，不再使用该渠道。当前已启用下述服务器原生企业微信告警与系统错误日志链路，独立于Codex。

验证：8项单元/故障状态回归通过，含慢/挂起探测的总超时；隔离sing-box实例测试16/16节点访问KIE正常；把首节点目标改为拒绝端口，检测到15健康并自动选备用，KIE请求正常；把全部16节点目标改为拒绝端口，连续两轮产生all_failed。所有故障注入只发生在隔离配置，不向线上节点注入故障。生产配置先sing-box check，再备份、热重载，失败自动恢复旧配置；未重启应用后端。正式7890出口KIE GET返回404，线上状态16健康、无告警。

原配置备份：/etc/sing-box/us-failover/backups/20261016T031952Z/config.json。回滚先停止新timer，再恢复备份并reload sing-box；备份原节点已失效，回滚只还原配置，不保证恢复连接。安装脚本 /usr/local/lib/everydayai-us-proxy/us_failover.py，unit源码位于deploy/proxy/。新订阅尚未配置定时刷新，节点池失效时由用户提供更新链接后人工导入。

应用端连接错误及时退款的源码修复仍未部署；本次生产变更仅为代理节点池与检测服务。未改写历史uncertain任务，原任务沿已有结算/恢复流程运行。


## 用户纠正提醒渠道：系统企业微信与错误记录（已在线生效）

用户明确要求删除Codex提醒，改为其系统的企业微信并记录错误。对应automation已确认deleteStatus=deleted。生产节点池再次核验16/16健康。

新增deploy/proxy/wecom_alert.py以及everydayai-us-proxy-alert.service/timer，服务器每轮结束30秒后独立检查状态；不依赖Codex运行。探测状态超过180秒、监控异常或全部节点两轮不可用触发critical；部分节点失败为warning，只记录不群发。恢复至少一个可用节点时结束critical事件，剩余节点故障另记warning。

事件先持久化至/var/lib/everydayai-us-proxy/alert-events/；使用系统error_logs表记录级别、来源us_proxy、发生次数、起止和恢复状态。先保存稳定BIGSERIAL log_id，再INSERT ON CONFLICT(id)更新，重启/提交回执丢失不重复建错误记录，不新增数据库迁移。只向现有唯一active平台super_admin的企微映射发送，复用企业配置解密与自建应用token/发送接口，不向其他组织管理员广播。企微API返回成功后才保存发送标记；失败保留队列，定时重试。同一故障不每轮刷屏；故障恢复有恢复通知。企微接受但本地标记落盘前进程中断可能重发一条通知，属于至少一次交付，不声称严格只送一次。

服务使用flock串行、90秒总期限、只读系统路径及受限写入目录。状态或控制器凭据不放到错误消息中，异常只打印类型，现有API异常的token参数在日志过滤器中脱敏。

16项代理/企微告警回归通过，包含真实隔离PostgreSQL日志、发送失败重试/重复处理不重复记录、恢复、异常状态、挂起探测总期限。生产唯一管理员及企微凭据预检通过。发送一条明确【测试】的消息，企微API确认成功；对应系统日志为INFO、已解决、非critical，不制造虚假的节点故障。安装后两个timer及sing-box均active，正式服务执行成功。未中断生产节点注入故障、未触发生图POST、未重启应用后端。连接错误及时退款的应用修复源码仍未发布。
