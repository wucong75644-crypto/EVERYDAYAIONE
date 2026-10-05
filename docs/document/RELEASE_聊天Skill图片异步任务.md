# 聊天 Skill 图片异步任务迁移、验收与回滚

2026-10-04。用户后续指示使用管理员账号直接生产验证，停止扩建复杂隔离测试；按受控入口准备当前任务候选，复用有效定向测试，保留构建/迁移/数据库身份/健康检查。本文保留原完整验收建议及证据缺口，不把生产冒烟测试称作完整故障验证。实际代码/测试和T编号见[实施记录](TECH_聊天Skill图片异步生成_实施记录.md)，协议见[API文档](API_聊天Skill图片异步任务.md)。

此前生产版本：`0e162515`，DEPLOYED_PENDING_ACCEPTANCE；配置仍仅管理员账号、每轮最多2张/24用户积分、透明关闭。自然语言两张生产测试误选旧image_agent并失败，没有出图，12积分误扣尚未补回。用户随后确认聊天固定generate_image，并授权本批“提交部署”；本次路由候选经1562项定向回归后进入受控发布，最终SHA/技术状态以本次RELEASE_RESULT为准，不能把此前版本验收结果当成本次验证。

当前生产已受控完整发布`95a18e188a34198cb76051a32b5a10a93c8906c4`，`DEPLOYED_PENDING_ACCEPTANCE`；普通聊天真实双图已完成、各6积分且刷新恢复。随后用户参考图跨轮确认暴露定位缺失，本地2026-10-05修复尚未提交/发布；不能据此前文生图通过宣称图生图跨轮验证通过。旧ImageAgent误扣退款尚未处理。

本批发布前只读核对：正式数据库身份匹配，没有pending/running父聊天或在途图片，历史paused聊天43条；scheduled_tasks的execution_policy/plan_snapshot/skill_revision_snapshot与scheduled_task_drafts的execution_policy/plan均无image_agent依赖。最新origin/main仍32e8ba0d且已包含；没有迁移、生产Skill/配置/积分写入，本次自动验证不发起付费生图。

### 聊天单一图片入口增补

此增补无SQL、依赖、Skill正文/控制面或配置变化。发布时完整更新后端并重启聊天Actor，使初始核心目录、两套提示词和调用时Registry使用一致版本；先排空/暂停在途父聊天，避免旧进程仍展示旧目录。既有图片Worker、完成/结算/恢复和已冻结快照继续工作。部署后先无付费核对普通聊天与自动Skill展示仅generate_image、旧调用not_started、关闭/权限拒绝不回退，再在明确额度内验证两次单图接受及实际双图结算。

回滚应用会恢复旧image_agent公共路由，可能再次触发已知误扣；关闭CHAT_IMAGE_ASYNC_ENABLED无法关闭旧image_agent。若需回滚，保留本批image_agent内部可见性及聊天提示词收口的兼容补丁，或停止相关新聊天生成；不得重新开放有已知账本缺陷的旧公共工具。原生电商内部账本缺陷与测试误扣退款须分别处理，不通过回滚或手改任务状态掩盖。

### 历史图片定位第一批（2026-10-05，历史验证记录，已纳入下述来源修复）

本批不需要SQL、依赖、Skill正文/发布、权限或生产配置变更。获准后按既有受控入口发布后端并重启聊天Actor，使历史投影、工具说明和接受端错误合同一致。闭合历史缓存使用`conv:msgs:outcomes-v2`新投影命名空间，信封schema仍为2，旧outcomes-v1不复用且由原TTL自然到期；无Redis批量删除或数据回填。已经启动的父chat与图片输入快照不重写，已有Worker任务继续完成。

上线先无付费核对：确认新轮上下文提供原消息id与正确content_index，错误fid仍在接受前拒绝、没有新任务/账本，字段错误与资源错误提示分别准确。再在用户明确的额度内复验上传→分析打磨→确认图生图、A分析/B执行、继续编辑；不能自动重发本次失败请求或一次执行截图中六个方案。生产灰度仍每轮最多2张/24积分，没有扩预算。回退不撤销273–276、账本、资产或图片收尾；若退回旧历史投影，参考图定位故障会再现，需保留本修复或明确停止相关新请求，不把旧缓存恢复当作验证通过。本轮未做生产发布/回滚演练。

### 图片来源与已有文件ID复用（2026-10-05，本地完成，未提交部署）

本批取代上述v2候选，实际使用缓存前缀`conv:msgs:outcomes-v3`；schema信封仍为2，旧缓存按TTL自然失效。增加迁移`277_chat_image_sources.sql`及对应rollback，复用tasks私有JSON，在原串行/分支claim事务内冻结旧来源并防止改写；无新表、文件ID算法变化、账单算法变化、依赖或Skill发布。正常版本化历史、旧接受快照及全部完成/恢复路径保持兼容。

获准提交部署后按以下顺序执行受控入口的完整发布，不单独开启新写端：

1. 关闭新图片接受；排空或暂停在途父Actor，保留全部图片Worker/回调/结算/恢复。核对真实迁移账本、tasks owner、现有claim owner/ACL、runtime/worker scope和实际RLS状态；277只支持现有claim owner为everydayai/everydayai_owner，不通过扩大权限解决owner冲突。隔离fixture不能代替生产完整schema预检。
2. 事务执行277，再部署所有兼容的Actor、历史/精确读取、接受解析器、Worker及详情/replay读端和前端。277保持claim owner/ACL，触发器由everydayai创建，预检其既有tasks TRIGGER权限，不复制fixture的GRANT到生产。旧Actor已启动且没有目录的任务维持旧严格边界；不要重写其输入。
3. 无付费核对新父claim目录不可变、跨组织/会话拒绝、错误fid未创建图片任务/账本、来源与原content_index一致。既有任务能够完成、结算和恢复后，再恢复原管理员接受范围；不扩大每轮2张/24积分预算，不自动重发历史失败请求。
4. 在既有授权与剩余额度内验证自然聊天自动Skill激活→A分析/打磨→B明确选用→确认生成两张→详情核对B字节/完整prompt→刷新恢复；另验旧原生结果继续编辑。脚本化离线测试不能代替真实模型的选图和自动Skill行为。

回滚先停新接受并排空采用新旧历史证明的父/子任务，保留兼容解析器、Worker和详情/replay直到全部settling/published投递完成。然后才可事务执行`backend/migrations/rollback/277_chat_image_sources.sql`，它恢复迁移121的两项claim函数、保留owner/ACL，删除保护触发器/helper，**保留所有任务私有目录及子任务快照数据**。不直接降级到不能核验新来源证明的旧Worker，不删除证据，不全库回填NULL revision。真实隔离PG已验证回滚owner/ACL与数据保留；生产发布/回滚尚未演练。

本批真实隔离DB/Redis复测（fixture创建并销毁随机测试DB，绝不读取应用DATABASE_URL）：

```sh
PYTHONPATH=.:/private/tmp/mcp-task-test-deps \
DATABASE_URL=postgresql://invalid/test JWT_SECRET_KEY=isolated-tests \
CHAT_IMAGE_TEST_ADMIN_DSN='host=/private/tmp port=55439 user=wucong dbname=postgres' \
CHAT_IMAGE_TEST_REDIS_SOCKET=/private/tmp/everydayai-chat-image-redis.sock \
/Users/wucong/EVERYDAYAIONE/.venv/bin/python -m pytest -q \
  tests/test_chat_image_sources_postgres.py tests/test_chat_image_lifecycle_postgres.py
```

本批最初58 passed / 0 skipped；发布前新增模拟生产基础表无RLS配置的来源/身份拒绝用例，最终59 passed / 0 skipped。生产基础表沿用可信服务边界而未启用RLS，资产表RLS非FORCE；保持现有配置，依靠显式scope/归属/fencing校验，不将FORCE作为本批新增前置条件。缺显式隔离DSN/socket会skip，不能当作通过。`/private/tmp/mcp-task-test-deps`仅复用本机已存在的测试包路径，不是产品新增依赖；不在其它机器自动安装或用生产连接替代。后端654 passed/3既有skip；额外scheduled Skill HTTP 7项因本机缺少既有supabase包阻塞。前端/构建及具体日志见本批实施记录。

## 配置与依赖

| 配置 | 默认 | 用途 |
| --- | --- | --- |
| `CHAT_IMAGE_ASYNC_ENABLED` | false | 仅控制新的聊天工具/图片trial/快照新版本接受；false不停止既有任务的扫描、回调、结算、详情和回执查询 |
| `CHAT_IMAGE_ALLOWED_USER_IDS` | 空串 | 逗号分隔UUID；总开关开启时只允许所列账号接受，空串为全用户；生产验收明确限定管理员，不扩大原Policy权限 |
| `CHAT_IMAGE_TRANSPARENT_ENABLED` | false | 透明工具schema与新接受的附加门，关闭后仍保存/验证既有透明任务 |
| `CHAT_IMAGE_MAX_REQUESTS` | 4（1..8） | 普通首次接受冻结父轮上限；同一原父的快照新版本另有共享重试预算，首次取当前上限；后续更换variant/request_id不扩张 |
| `CHAT_IMAGE_MAX_CREDITS` | 100（1..200） | 接受时冻结用户积分预算，领取前再次核余额与价格 |
| `CHAT_IMAGE_SUBMISSION_LEASE_SECONDS` | 60（10..300） | 有界提交领取租约 |
| `CHAT_IMAGE_QUEUE_TIMEOUT_SECONDS` | 600（60..3600） | 未提交排队到期确定失败，未付费不产生平台支出估算 |
| `CHAT_IMAGE_UNCERTAIN_TIMEOUT_SECONDS` | 900（60..86400） | 受理不确定核实期限，到期退款/平台承担，不重新付费请求 |

沿用当前PostgreSQL、Redis、BackgroundTaskWorker、账本、KIE适配器、文件权限、工作区/OSS和Skill Runtime。没有新依赖/供应商/通用调度服务。图片执行预算不包括聊天模型和另行选择的语义质量检查费用。

2026-10-04后续范围调整：聊天 Skill 的新工具调用只使用平台默认模型及其图生图配对，AI无模型选择参数。无需新迁移或依赖。先部署默认模型工具schema、接受前校验及确定拒绝说明，再经现有个人Skill控制面发布相应正文；目录 `model_selectable` 仍为true，含义是允许AI激活该Skill，不是允许选择生图模型。旧任务/Worker/快照重试仍保留原model，原生入口不变；回滚也不删除旧快照或完成读端。管理员灰度范围和预算不扩大，已用完的2张真实验证额度不因发布重置。

273依赖当前主线Actor/fencing/tasks/messages、用户/成员、既有积分账本及退款函数；275依赖已有Skill草稿trial与change_sets；资产完成依赖既有145资产RPC及实际权限。生产实际数据库角色、owner、FORCE RLS/ACL必须在完整隔离部署副本核验，不能仅用新RPC的GRANT替代。

## 加性迁移顺序

使用项目既有数据库迁移受控入口，先在隔离环境执行，禁止拿生产连接做测试。待用户明确授权生产发布后再执行对应发布入口。

1. 核对当前完整主线schema与迁移记录、运行时/Worker scoped身份、Actor字段和账本权限。新接受与透明保持false。
2. 依次273 `chat_image_lifecycle` →274 `chat_image_settlement` →275 `chat_image_trials` →276 `chat_image_snapshot_replay`。新增部分索引、函数、trial窄策略；不新建图片任务表，不替换旧task/消息格式。迁移入口应逐文件事务执行。
3. 使用真实应用登录角色测试runtime接受、worker领取/结算/登记、runtime_admin trial/成本查询。针对正确org、跨org/个人/频道、NULL actor、撤销成员、非法普通角色、函数PUBLIC权限逐项核验T15。Worker越权不能靠模型字段或数据库scope标签放行。
4. 核验现有 `register_user_asset` 的owner与调用权限、storage scope映射和幂等ref。不能为了消除权限报错扩大资产全表PUBLIC权限。
5. 运行预算/账本/接受/消息故障回滚和多Worker领取/停止竞争，确保T05/T08/T09/T10。生产权限修复如有必要需按证据再做最小兼容变更，不能直接复制隔离fixture授权。

## 应用与Skill发布顺序

1. 在隔离部署中先更新后端兼容读端、Worker、回调、恢复和前端；保持接受false。旧native任务仍完成，已受理新版任务仍收尾。
2. 新版tool/trial唯一出口、task控制、前端恢复均就绪后重新检索 `_run_image_generation` / `_image_failure_result` 等旧同步消费者，确认无残留；不回补第二同步分支。
3. Skill源为 `examples/skills/catalog/platform/chat-image-orchestration/v1/SKILL.md`。沿用已有审核/发布与分配控制面，先隔离环境发布/分配并验证真实published/hash、个人general/interactive作用域、auto/manual激活后Registry/Policy交集；不是文件存在便视为发布。禁止自动生产发布/分配。
4. 在无付费stub环境跑闭环及下述手动场景。使用真实模型/供应商的付费测试须单独授权，并设有限张数和积分；真实NAS/OSS/浏览器验收不可以MockTransport成绩冒充。
5. T01–T16中对应实际发布范围的门禁通过后，才可在明确的隔离/授权部署环境开启 `CHAT_IMAGE_ASYNC_ENABLED`；透明继续false。生产Skill与接受开关的启用属于后续发布操作。
6. 按项目 `deploy/release.sh --message ... --file ...` 受控入口执行；不直接deploy.sh，不合并main，不清理任务工作树。部署前核验无旧Runtime平台路径。用户最新要求直接生产验收，本轮仅打开指定管理员范围；可复用已完成定向测试使用 `--skip-test`，不能跳过构建/迁移/健康检查。

## 无付费隔离测试复现

现有测试使用本机已安装的PostgreSQL17、Redis、backend venv及frontend依赖。临时PG cluster位于 `/private/tmp/everydayai-chat-image-pg`，仅Unix socket `/private/tmp`/55439；Redis仅 `/private/tmp/everydayai-chat-image-redis.sock`，port0，无持久化。不要复用项目配置或替换为生产DSN。本轮测试后已停止这两个专用测试进程，保留目录和日志；复测需先启动。

专用cluster不存在时，可在新临时目录运行已有 `initdb --auth=trust --locale=C --encoding=UTF8`，再以 `listen_addresses=''`、`unix_socket_directories='/private/tmp'`、port55439启动。目录已存在先识别该测试cluster，不覆盖；Unix socket权限按隔离本机测试设置。Redis以 `--port 0 --unixsocket <上述专用路径> --save '' --appendonly no`启动。测试fixture创建随机专用DB并销毁，不读应用DATABASE_URL。

在本任务backend目录运行：

```sh
DATABASE_URL=postgresql://invalid/test \
JWT_SECRET_KEY=isolated-test-key \
CHAT_IMAGE_TEST_ADMIN_DSN='host=/private/tmp port=55439 dbname=postgres' \
CHAT_IMAGE_TEST_REDIS_SOCKET=/private/tmp/everydayai-chat-image-redis.sock \
/Users/wucong/EVERYDAYAIONE/backend/venv/bin/python -m pytest \
  tests/test_chat_image_lifecycle_postgres.py tests/test_chat_image_slots_redis.py -q
```

该命令本次47通过（PG45/Redis2）。缺明确专用DSN/socket时测试会skip，不能报告数据库/Redis通过。fixture只覆盖最小字段与测试RLS及实际相关迁移，函数owner/权限部分为显式测试设置；**不能替代完整部署schema验收**。

输入/旧入口相关定向测试：

```sh
DATABASE_URL=postgresql://invalid/test JWT_SECRET_KEY=isolated-test-key \
/Users/wucong/EVERYDAYAIONE/backend/venv/bin/python -m pytest \
  tests/test_chat_image_request.py tests/test_kie_image_25.py \
  tests/test_media_tool_executor.py tests/test_image_handler_batch.py \
  tests/test_task_recovery.py tests/test_skill_chat_creation.py -q
```

在本任务frontend目录，用已有依赖：

```sh
./node_modules/.bin/vitest run \
  src/contexts/__tests__/wsMessageHandlers.test.ts \
  src/hooks/__tests__/useRegenerateHandlers.test.ts \
  src/utils/__tests__/taskRestoration.test.ts \
  src/components/chat/__tests__/ChatImageControls.test.tsx \
  src/components/chat/message/__tests__/SkillDraftCard.test.tsx \
  src/components/admin/__tests__/ErrorMonitorPanel.test.tsx
./node_modules/.bin/tsc -p tsconfig.app.json --noEmit --tsBuildInfoFile /private/tmp/chat-image-app.tsbuildinfo
./node_modules/.bin/tsc -p tsconfig.node.json --noEmit --tsBuildInfoFile /private/tmp/chat-image-node.tsbuildinfo
./node_modules/.bin/vite build
```

完整本次定向结果：后端1500通过/3废弃V1测试skip，PG45/Redis2独立通过，前端185+16通过，TypeScript/Vite通过。日志位于实施记录列出的临时路径。未运行真实供应商调用。

## 隔离产品验收场景

必须在隔离环境使用有限授权预算，检查真实执行输入和账本，不只看模型回复：

1. 自动/手动激活Skill；先写prompt再上传B；先分析A得到prompt再上传B执行；确认冻结prompt原文、mode与refs只为B。历史原文缺失/有多个候选时询问，不摘要重建。T01–T03/T12。
2. 上传多图，指定用途/顺序、引用历史或已生成图、替换/追加refs；接受后改聊天输入，旧快照不变化。无生成refs的分析附件仍可文生图。T02/T03/T11/T15。
3. 请求两张或同prompt稳定变体，确认两个task/message/slot、父聊天仍流式；只选方案2或“先样张”只提交所选/一张，用户确认前不自动剩余。T04/T08/T09。
4. 两图乱序、一个失败；刷新/断线/重启、多标签；确认子图发布、原父文本/Turn不结束、账本唯一。T05–T07。
5. 排队停止与领取竞争；已提交准确说明无法撤回。模拟单次HTTP受理响应丢失/ID绑定失败，禁止再创建；核实到期退款并记录平台估算，晚到结果不重扣。管理员面板刷新完整汇总/分页一致。T05/T09/T10。
6. 保存/资产/消息/WS故障后恢复，只重做保存或收尾；不新增供应商请求。失败重试与成功再生使用原快照、新task/message保留旧图；查看详情/cost/配方/比较/ZIP/反馈。T07/T11。
7. 图片trial先running后GET完成，不入聊天历史；文字trial不变；候选编辑/过期、刷新、同接受重放、反馈版本正确。T13。
8. 原生1..4图片、电商、video、文件、ERP/MCP、scheduled核心用例按受影响入口回归；scope/权限绕过拒绝；关闭新接受后在途任务仍结算/恢复。T14–T16。
9. 透明单独获得验证授权后才开透明门：确认真实adapter字段、PNG含alpha<255、无效/全不透明结果不能称透明，退款/平台统计正确；核对实际定价。其它T17能力保持不支持，不以提示词模拟mask。

## 保留型回滚与故障恢复

### 参数校验/一次纠错增补的发布顺序（2026-10-04，已授权提交部署）

此增补不需要新SQL迁移、Skill发布或生产配置修改。下一次获准“提交部署”时按受控入口发布前后端；先确认既有239 checkpoint RPC、safe_point没有拒绝before_tool的约束和默认模型/管理员灰度配置，停止接收新父聊天并排空或暂停在途聊天，再完整更新应用，避免旧/新Actor混跑。已接受图片继续由原Worker完成与结算。上线先验证不新增付费图片的自动Skill激活/确认续轮、错误字段未创建任务、管理员统计与恢复停止行为；此前2张/24积分授权的图片数量已使用，追加生图先明确新的测试额度。

本次提交前生产只读核验：正式数据库身份匹配，实际`save_generation_checkpoint(uuid,uuid,text,jsonb)`可执行，safe_point为text且无枚举约束，RPC支持before_tool；chat pending/running均为0（历史paused为43），在途图片为0，发布锁空闲。基座仍为`32e8ba0d`。此核验不写业务数据，也不代表已完成发布；最终SHA、构建、服务状态和候选状态以受控入口本次`RELEASE_RESULT`为准。

回退应用前排空本版纠错父聊天，或保留识别image_argument_validation与before_tool的兼容回滚补丁；旧版不识别额度/dispatch标记，不能恢复这些父任务后重新请求模型。不得移除既有图片Worker/读端/结算链，也不修改任务快照或通过最新消息补参数。统计字段为加性，可保留，旧聊天无字段显示暂无样本。本批尚未做生产发布/回滚演练。

- 第一动作关闭 `CHAT_IMAGE_ASYNC_ENABLED` 和透明门，阻止新接受；**保留** Worker/回调/轮询、读端、settling重试、平台退款/统计、trial GET及前端恢复。不得关闭整个图片Worker来回滚接受功能。
- 273–276对应rollback SQL仅为保留型说明，不DROP函数/索引/策略/快照；执行它们不会撤销既有任务事实。不要回滚到本基座那种完全不识别新版生命周期的应用；必须保留兼容 reader/completion/recovery 的回滚候选。
- queued未领取可按原子停止合同关闭；已发送/accepted/uncertain不能批量直接退款后重发。沿用核实期限与用户已授权的平台承担政策，真实支出仍需账单核实。
- settling保留provider_result与结果地址；保存失败继续保存同一结果，资产登记按稳定ref_key重放。published而delivery_pending只重投递与释放子slot，不再生成/扣款/推进revision。
- 发布与退款在同事务，失败整体回滚；修复权限/存储后推进原task。不要手工改task.status或用最新一条用户消息补输入。
- 有完整reader兼容候选并完成开关/在途恢复/错峰演练后，才称具备回滚条件；本轮未做真实部署演练。生产Skill撤销/重新分配仅在相应授权内进行。
