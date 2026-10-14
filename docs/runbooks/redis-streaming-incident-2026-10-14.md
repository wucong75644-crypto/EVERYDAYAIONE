# 2026-10-14 聊天推送周期停顿：Redis 异常复制

状态：根因已确认，已停止异常复制、恢复磁盘/运行配置，并完成 Redis 端口收口。应用与 Redis 监听均为 127.0.0.1；收口后实际 Manager 合成推送 1000 发/1000 收，无 5 秒断档。认证及更广主机排查仍未完成，用户真实聊天待复验，不能把后端恢复等同于主机已排除入侵。

## 因果证据

普通智能聊天正文按小块生成，但约每隔一段时间停顿 5 秒。生产 `d3a39889` 的一条样本为 94 块 / 455 字，最大 17 字；append 累计 383ms，send 累计 31.48 秒，单次 4.99 秒。

不调用模型、不读写业务数据库，仅在随机临时 diagnostics channel 上发布固定合成内容，仍复现近 5 秒 ACK 等待。原始 RESP2 与 redis-py、公网与同机私网均复现。原始私网短测 100 条仅持续 7ms，曾未覆盖周期；改为 200 条、间隔 5ms 后也出现 4996ms 等待。该证据推翻“只修公网绕行即可”的假设，未因此修改生产路由。

Redis 6.2.20 当前为 slave，连接非项目配置的外部上游（端口 60100），无下游副本。运行时目录 `/etc`、dbfilename `crontab`、replica-read-only=no；复制日志反复出现：

1. Full resync（异常重复字符 replid）。
2. 精确约 5 秒后：`Opening the temp file needed for MASTER <-> REPLICA synchronization: Permission denied`。
3. 约下一秒重新连接上游，重复上述流程。

Redis 6.2.20 的 `syncWithMaster` 设置 maxtries=5，创建临时 RDB 失败后每次同步 sleep(1)，直接阻塞 Redis 主事件循环。因此 PUBLISH/PING 都无法及时返回；应用又在每块 await 推送完成后才读下一块模型流，停顿传播到网页并在恢复后集中输出。

源码：[重试次数](https://github.com/redis/redis/blob/6.2.20/src/replication.c#L2234)、[临时文件失败与阻塞](https://github.com/redis/redis/blob/6.2.20/src/replication.c#L2487-L2498)、[失败后重连](https://github.com/redis/redis/blob/6.2.20/src/replication.c#L2522-L2533)。`repl-diskless-sync-delay=5` 不是该同步阻塞的原因，客户端超时也不是 Redis 最初停住的原因。

## 时间与配置边界

源码及可见 Git 历史没有项目要求的 Redis 外部复制、写 crontab 或 exp.so 配置。部署初始化约定为同机 Redis，并无该复制拓扑。

磁盘 redis.conf 还存在 `/tmp`、`exp.so`、外部 replicaof、replica-read-only=no，mtime 为 2026-03-25 10:52:38。它与当前运行时 `/etc/crontab` 不同。该时间只证明文件修改时间，不能当作首次攻击时间或流式症状开始时间。当前 MODULE LIST 为空，不能据此排除历史入侵、其他持久化或其他影响。

没有读取 Redis 业务 keys、密码、用户正文或系统 crontab 内容。生产探测仅使用受控配置、标量元数据、Redis 运维日志与随机诊断频道，不发送真实用户消息、不调用付费模型。

## 最小处置与保护

先以受控发布锁保护一次性修复，默认 dry-run。必须匹配异常 role/master port/dir/dbfilename，并确认无下游副本；保存 0600 元数据证据，写前再次校验锁，仅执行 `REPLICAOF NO ONE`，再核验同一 run_id 与 master 角色。

这一步停止外部复制，不清 Redis 数据、不重启服务、不触碰 PostgreSQL。不得先让 `/etc` 可写、修成可写数据目录或恢复恶意上游；否则可能让下一轮异常 RDB 真正落地。不得通过 FLUSH、SAVE/BGSAVE 或全量 CONFIG REWRITE 处理。

修复后用相同原始 RESP2 与实际 Manager 私有诊断频道做有间隔连续测试，采样超过 15–30 秒，确认 5 秒 ACK 停顿消失、角色保持 master、复制/fork 不再周期增长。

磁盘异常配置必须单独恢复，公网访问边界与凭证须盘点所有消费者后收敛；不能把临时转主当作这些事项已完成。需备份实际配置、核对包默认数据目录与 Redis 用户所有权，再仅修改已确认异常字段；不直接整份覆盖未知编辑，不加载旧 RDB，不以重连恶意上游作为回滚。

## 已执行的最小修复与验证

一次性工具 `deploy/repair-redis-streaming.py` 默认 dry-run，13 项定向测试及编译检查通过。生产 dry-run 匹配异常 guard；在现有发布锁内执行后返回 `status=updated, role=master, no_data_cleared=true`，元数据证据以 0600 保存，正常释放本次拥有的锁。未发布应用代码、未修改 Redis 客户端超时、未重启或清空 Redis。

随后以真实 `WebSocketManager.send_to_task_or_user` → Redis PUBLISH → 同实例独立订阅在随机诊断频道连续运行 26.25 秒：1000 条发出、1000 条收到，无发布异常；最慢发送 235.99ms、p95 5.118ms，最大收帧间隔 278.078ms，5 秒断档消失。此为后端交付验证，不冒充用户网页与真实模型端到端验收。fork 仍增加 5 次，符合错误目录导致自动保存继续失败，持久化配置需继续恢复。

用户已确认仅当前应用服务器需要连接 Redis；访问边界核验按这一已确认事实继续，不预设其他外部消费者。

## 持久配置恢复完成

磁盘旧上游端口实际为 7178，与运行时 60100 不同；首次 dry-run 因该差异正确拒绝，核实具体磁盘字段后只修正本次恢复工具的事故匹配条件，未放宽普通 HA 保护。实际 redis.service 用户及 PID/UID 与 Redis INFO 一致，已有 `/var/lib/redis` 属于该用户，目录 0750；SELinux Disabled、配置无扩展属性。

`deploy/restore-redis-configuration.py` 的 16 项定向测试通过。默认 dry-run 确认匹配，在同一发布锁机制内完成：0600 备份原 redis.conf 和既有 dump.rdb；原子移除外部 replicaof、将 dir/dbfilename 恢复为 `/var/lib/redis/dump.rdb`、replica-read-only=yes，保留原 owner/group/mode 及无关字节；执行三条对应 CONFIG SET。每条 SET 前重新核验 master、零下游、同 run_id。未 CONFIG REWRITE、未重启、未加载旧 RDB、未清数据。

生产结果为 updated/master；运行配置正确、磁盘上游移除、RDB 最近保存状态 ok、后端 health=ok、锁正常释放。随后真实 Manager 私有频道再次连续运行 26.36 秒：1000 发/1000 收，无发布异常，send p95 4.967ms、max 235.475ms，最大收帧间隔 280.224ms，fork 增量 0。与修复前固定约 5 秒阻塞相对照；不声称所有网络抖动或用户网页实际体验已验收。

端口整改前的只读快照：24 条 Redis 客户端均来自本机，loopback PING 正常；firewalld/iptables 服务 inactive。当时公网入口尚未限制，后续收口结果见下一节；认证及更广主机排查仍未完成。运维工具和记录保留在当前任务，尚未提交或部署新应用版本。

## 生产端口收口完成

用户继续要求处理生产端，已确认仅当前服务器使用 Redis。收口前的本机只读核验：四个实际 systemd 服务均 active、EnvironmentFiles 唯一指向 backend/.env、PrivateNetwork=no、NetworkNamespacePath 为空；进程的 REDIS_HOST 均为原配置。24 个 Redis 客户端来源实际分类为自身公网 22、回环 2；不能直接用“仅允许内核 LOCAL 源地址”的防火墙规则，因为会误断当时通过自身公网绕行的业务连接。

收口前，从 Codex 所在机器直接连接生产公网 6379 并发送一次 PING，连接成功且无需认证返回 PONG，确认存在外部未认证访问入口。当时 Redis 运行及持久 bind 均为唯一 0.0.0.0，REDIS_HOST 为唯一配置项；127 与公网 INFO run_id 相同。运行中的聊天任务/定时执行为 0，另有一条既有图片 pending，未修改其状态。

`deploy/secure-redis-local-access.py` 默认 dry-run，23 项定向测试通过。三项运维工具合计 52 项测试（13+16+23），语法与 diff 检查通过。操作限定本机同一 Redis：公网与回环 run_id/PID 一致、master、零下游、6379 非 TLS；四个有效 unit 的 EnvironmentFiles、active、进程 REDIS_HOST、PrivateNetwork 与 namespace 均核验。初始与每次重启前以只读事务查询 tasks/scheduled_tasks，running 必须为 0；pending 图片不被修改或取消。

在原有发布锁下，先将 backend/.env 与 redis.conf 以 0600 备份，再仅原子替换 REDIS_HOST，保留其他字节及 owner/group/mode。四服务迁移完成并确认 CLIENT LIST 全回环后，才原子保存唯一 bind=127.0.0.1 并执行一条 CONFIG SET bind。未修改密码、未重启 Redis、未 FLUSH/SAVE/BGSAVE/CONFIG REWRITE、未加载旧 RDB或改业务数据。backend 重启短暂中断服务并断开 WebSocket，不能宣称零停机。

首次 apply 在 backend 的 systemd Type=simple restart 返回后立即读取进程环境，因 exec/environ 尚未就绪而安全停止：env 已为 127、backend 随后正常且 health=ok，其他三服务仍用原路由，bind 保持 0.0.0.0，备份和原锁保留。补充最多 15 秒、每 100ms 退让的完整就绪核验；永久不匹配仍拒绝。恢复复用同一锁，已通过完整回环 guard 的 backend 只检查健康并跳过重启，仅重启其余三服务；没有重新开锁、重复中断 backend 或反向恢复通配监听。

恢复执行返回 updated，原锁正常释放，再次只读运行工具返回 nochange。最终核验：Redis PID 保持 689、master、零下游，运行与持久 bind 均为 127.0.0.1，RDB 最近保存 ok；四服务 active、实际进程 REDIS_HOST=127.0.0.1、后端 `/api/health` JSON status=ok，锁目录不存在。仅有回环监听；本机非回环新连接失败，外部 Codex 所在机器连接公网 6379 返回 ConnectionRefusedError。应用候选仍为 d3a39889，未发布新应用代码。

收口后真实 `WebSocketManager.send_to_task_or_user` 在随机私有诊断频道连续运行 20.32 秒：1000 发/1000 收，无发布异常；send p95 0.233ms、max 0.714ms、超过 100ms 为 0，最大收帧间隔 20.531ms，fork 增量 0，前后 role 均为 master。此为后端合成交付验证；真实用户网页与模型端到端体验仍待复验。公网入口已收口，认证及更广主机排查尚未完成，不宣称安全整改已闭环。
