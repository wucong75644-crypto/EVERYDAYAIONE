# 项目概览 (PROJECT_OVERVIEW)

## 项目基本信息
- **项目名称**：EVERYDAYAIONE
- **项目类型**：AI图片/视频生成平台（Web应用）
- **开发语言**：Python（后端）+ TypeScript/React（前端）
- **版本**：初始设计阶段

## 项目架构
- **架构模式**：前后端分离 + 任务队列 + 云数据库
- **主要技术栈**：
  - **后端**：
    - Python 3.x
    - FastAPI（异步Web框架）
    - Supabase（PostgreSQL云数据库 + Realtime）
    - Redis + Bull（任务队列）
    - loguru（日志管理）
    - tenacity（重试机制）
  - **前端**：
    - React + TypeScript
    - Zustand（状态管理）
    - TailwindCSS（样式）
    - Supabase-js（数据库客户端 + 实时订阅）
  - **基础设施**：
    - **计算**：阿里云ECS 2核4GB（MVP阶段）
    - **数据库**：Supabase免费版（500MB PostgreSQL）
    - **文件存储**：阿里云OSS（图片/视频存储 + CDN）
    - **实时通信**：Supabase Realtime（替代Socket.io）

Agent Runtime 单一运行时收敛：
- `docs/document/TECH_AGENT_RUNTIME_SINGLE_RUNTIME_CONVERGENCE.md`：冻结最终唯一
  ModelLoop/ActionLoop Owner、现有业务能力薄适配、平行配置/事实/Worker 删除边界，
  以及模型、ERP/Media、Scheduler/WeCom、Ingress、数据库和发布的实施顺序。

## 核心功能
- **多对话管理**：用户可创建多个AI对话，支持重命名、删除、搜索
- **多任务并发**：全局最多15个任务同时执行，单对话最多5个任务
- **AI内容生成**：调用大模型API生成图片和视频
- **实时进度跟踪**：通过Supabase Realtime实时推送任务进度
- **积分系统**：按任务消耗积分，失败自动退回
- **图片编辑**：智能编辑、区域重绘、扩图、擦除、变清晰等
- **用户管理**：注册、登录、密码重置、个人设置
- **管理员后台**：用户管理、积分充值、数据统计

## 目录结构

Sandbox Linux隔离安全合同：
- `.github/workflows/sandbox-linux-security.yml`：仅在AR-16 Phase 2验证分支或手动触发的
  无生产Secret托管Ubuntu合同，固定nsjail源码提交并使用一次性rootfs。
- `backend/tests/test_agent_runtime_sandbox_linux_external.py`：真实验证nsjail、cgroup v2、
  host/network/input mount隔离、内存上限及完整进程树终止；不属于默认测试集合。
- `backend/tests/test_agent_runtime_sandbox_job_reconciliation_postgres_external.py`：
  独立承载Sandbox Job未知结果协调、敏感回执拒绝与Runtime作用域隔离合同。

Tool Confirmation V3：
- `backend/config/tool_safety.py`：冻结的显式 SAFE/CONFIRM/DANGEROUS 注册表；未知工具
  不再继承默认 SAFE。
- `backend/services/tool_confirmation/`：确定性 JSON hash、脱敏 preview、Redis 三键 Lua、
  一次 challenge 与唯一 execution claim 服务。
- `backend/services/agent/safe_tool_logging.py`：确认门禁可达 Agent 的结构化脱敏日志助手。
- `docs/document/TECH_TOOL_CONFIRMATION_V3.md`：协议、状态机、失败关闭与发布门禁。
- 旧 ToolLoop 与 ChatTool 生产 composition 保持不变，仅关闭非 SAFE 授权绕过；未接
  Agent Runtime startup、Sandbox 专业 Executor 或数据库 migration。

Agent Runtime AR-14～AR-16 授权恢复与 Dispatch Gate：
- `backend/migrations/220_24_agent_runtime_authorization_dispatch_gate.sql`、
  `220_25_agent_runtime_authorization_recovery.sql`及同名 rollback：增加持久
  DispatchIntent、授权恢复、原子 GrantUse、取消/拒绝收敛和 reconcile-only 恢复。
- `backend/services/agent/runtime/ports/authorization.py`、
  `application/authorization_recovery.py`及
  `infrastructure/postgres/authorization.py`：提供唯一授权恢复与 gate Port/Worker 适配。
- `backend/services/agent/runtime/executors/resolver.py`与
  `application/action_loop.py`：Registry 保持唯一 Executor 映射 SSOT，按
  Resolver→gate→dispatch/reconcile 编排；未接 startup/ingress。
- `backend/services/agent/runtime/application/action_loop_support.py`与
  `executors/resource_support.py`：承载租约/结果辅助合同、Child Run、内容寻址
  materialize 与资源恢复辅助逻辑，保持核心编排模块在结构阈值内。
- `docs/document/TECH_AGENT_RUNTIME_AR-14-16授权恢复与DispatchGate.md`：记录状态机、
  原子边界、锁序、故障恢复、权限与 rollback 合同。

Agent Runtime Sandbox Job Controller 与专业 Executor：
- `backend/migrations/222_01_agent_runtime_sandbox_job_foundation.sql`、
  `222_02_agent_runtime_sandbox_job_rpcs.sql`、
  `222_03_agent_runtime_sandbox_job_recovery_rpcs.sql`及精确 rollback：建立幂等
  Job事实、execution/reconciliation fencing、精确响应丢失readback、durable
  recovery scanner、partial/cleanup合同、FORCE RLS和窄RPC。
- `backend/services/agent/runtime/domain/sandbox_job.py`、`ports/sandbox_job.py`及
  `infrastructure/postgres/sandbox_job_repository.py`：提供 fail-closed typed Port。
- `backend/services/agent/runtime/sandbox/`：独立Worker编排、Linux隔离探针、nsjail
  launcher边界、受限Capability、不可变输入、内容寻址输出和partial清理。
- `backend/services/agent/runtime/executors/sandbox_job.py`：Registry中唯一
  `code_execute`专业Executor映射；dispatch只创建Job，accepted/unknown只query/reconcile。
- `everydayai_sandbox_worker`由数据库 bootstrap 创建，NOINHERIT、无表直权且仅能执行
  222 Worker RPC；production composition/startup仍未连接。
- `docs/document/TECH_AGENT_RUNTIME_SandboxJobController_BatchA.md`：记录身份、
  状态机、锁序、权限、Worker/Executor和三层隔离验证门禁。

Agent Runtime C7-B3.1 production composition spine：
- `backend/services/agent/runtime/production_factory.py`：Runtime Worker 唯一 code-owned
  production composition 入口，不接受 Settings callable 或组件对象注入，不读取 Secret、
  不调用 Provider。
- 当前批次不接 Credential、ERP/Media、Object Store 或 Scheduler；缺少真实安全服务时以
  `RuntimeAssemblyReadiness` 携带 required/unavailable/disabled 能力状态并结构化失败关闭，
  production gate 不能将 composition readiness 提升为 ready。

Agent Runtime C7-BG4 Model Gateway Runtime 接线（历史实现，S1 已移除运行代码）：
- `ModelLoopDriver` 的显式 Gateway 分支通过 227_20 `start_dispatch` 在一个事务中推进
  ModelAttempt 并建立 operation；UDS request id、租户、provider、revision identity 与三层
  kill epoch 全部来自返回的 durable binding，direct/mock ModelPort 继续使用原路径。
- `ModelGatewayClient` 每个 operation 只连接一次本地 UDS，并用 Runtime scoped read RPC
  证明 terminal；终帧丢失或无法证明完整 completed 时收敛为 UNKNOWN/reconcile-only，绝不
  普通重派。Runtime 生产路径不解析 credential、不构造 Provider adapter，也不持有 Secret。
- production composition 仅在 flags、独立 call/health sockets、scoped repository 与健康
  projection 齐全时构造 safe read/model/action lane；ERP Write、Media、Scheduler、Sandbox
  保持关闭，BG5 前 flags 默认关闭且整体 `production_ready=false`。

Agent Runtime C7-BG5 Model Gateway Deploy 与统一验收（历史实现，S1 已移除部署资产）：
- 新增专用 Gateway systemd user/group/unit，以及最小 DB/process env 与仅含两个批准键的
  KEK env；`bootstrap-agent-model-gateway-role.sh` 以独立密码创建 migration 已冻结的窄 DB role；
  Runtime 只加入 `everydayai-model-gateway` UDS group；两份 env 属于独立
  `everydayai-model-gateway-secret` group，Gateway 是唯一服务成员，Runtime 由 DAC 与运行时
  身份探针共同禁止读取 Gateway env、KEK 和 Provider key。
- D0-A release transaction 扩为五 env/四 control-plane unit；reviewed SHA、全量预检、
  daemon-reload、内外层 inactive:disabled 后验任一点失败均按同一 release journal 恢复。
- `scripts/run_agent_model_gateway_disposable.sh` 统一执行部署静态合同、本地 UDS、mock Provider、
  227_18～227_20/227_25 disposable PostgreSQL、B4 cancel Barrier与真实 UDS blocked-provider
  回归，以及 crash/response-loss/UNKNOWN/drain 回归；`all` 自建 disposable PostgreSQL且不读取
  `DATABASE_URL`，数据库阶段未运行不得作为验收证据。CI 保持所有 production flags false，
  `production_ready=false`，不改 Sandbox。

Agent Runtime C7-B3.2-A safe Toolset 与 Authorization：
- `backend/services/agent/runtime/catalog/safe_read_release.py` 与生成脚本从
  `READ_TOOL_SPECS` 冻结 17 项已证明安全的只读 Catalog；Workspace `file_search`、
  远程 ERP、ERP Write、Media、Scheduler、Sandbox 均不进入该 release。
- `227_16` 新增默认关闭的 v4 Catalog/EffectiveToolset facts；`227_17` 仅为
  SAFE/NONE Action 建立 attempt、token、request hash、revision 与 kill epoch 绑定的
  durable PolicyReceipt activation，随后仍必须经过既有 Dispatch Gate。
- 既有 scope SSOT 保持不变：channel Toolset 为 17 项，user Toolset 为 9 项；
  production flags 与 `production_ready` 均未开启。

Agent Runtime C7-B3.2-BG1 Model Gateway protocol（历史实现，S1 已移除）：
- `backend/services/agent/runtime/model_gateway/` 定义严格的 UDS v2 framing、请求/响应
  校验、可注入 peer credential 验证以及仅供隔离测试使用的 client/fake server。
- `backend/tests/test_agent_runtime_c7_r2_result_integrity.py` 固定 canonical stop reason、两类
  tool call id、五项 usage 与 DB/wire/Runtime 三方 response hash 完整性及篡改收敛 UNKNOWN。
- BG1 不访问数据库、配置 Secret 或 Provider，也不进入 production composition；local harness
  与 fake server 均固定 `production_ready=false`。

Agent Runtime C7-B3.2-BG2 Model Gateway database owner（仅保留冻结 migration 账本）：
- `backend/migrations/227_18_agent_runtime_model_gateway.sql` 与精确 rollback 新增
  secret-free Gateway operation facts、专用数据库角色门禁、Runtime submit/read 与 Gateway
  claim/dispatch/renew/finalize/recover 窄 RPC；Runtime 旧 AI bundle 直权被撤销。
- `backend/services/agent/runtime/infrastructure/postgres/model_gateway.py` 以 scoped repository
  分离 Runtime 与 Gateway 可调用面；claimed 过期可恢复，dispatching 过期只收敛 UNKNOWN，
  Gateway finalize 不修改 ModelAttempt/ModelStep。systemd 与生产配置仍未实现，
  `production_ready=false`。

Agent Runtime C7-BG2.1 Model Gateway pre-dispatch failure closure（仅保留冻结 migration 账本）：
- `backend/migrations/227_19_agent_runtime_model_gateway_predispatch_failure.sql`
  新增 Gateway-only claimed→failed 窄 RPC；仅接受固定脱敏错误码，并以 claim token、
  operation version、tenant binding、request/revision/kill epochs 和有效租约失败关闭。
- rollback 只删除该 RPC、保留 operation facts 与 227_18 readback；repository 增加
  Gateway scope `fail_before_dispatch`，Runtime scope 不可调用，平台仍为 `production_ready=false`。

Agent Runtime C7-BG3 Model Gateway isolated process（历史实现，S1 已移除）：
- `backend/agent_model_gateway_main.py` 与 `model_gateway/service.py` 提供默认关闭、
  `production_ready=false` 的本地隔离进程、健康契约、drain，以及 claim→predispatch
  failure/dispatch→Provider→finalize 的唯一时序；不接 systemd 或 Runtime composition。
- `model_gateway/configuration.py` 是 Runtime 子树内唯一 KEK/SecretMaterial 构造边界，
  只在一次性 async consumer 生命周期内解密 BG2 bundle；`provider.py` 是唯一
  `create_runtime_chat_adapter` consumer，并保证 DB dispatch fact 先于网络调用。
- `infrastructure/model/stream_execution.py` 复用既有 `ResponseAccumulator`，统一 text、
  tool call、usage、错误与 UNKNOWN 分类；Provider adapter 在 UDS 终态后关闭，Secret、
  encrypted bundle 与原始异常均不进入协议、事实或日志。

Agent Runtime C7-BG3.5 ModelAttempt/Gateway atomic dispatch binding（仅保留冻结 migration 账本）：
- `backend/migrations/227_20_agent_runtime_model_gateway_dispatch_binding.sql` 在同一事务按
  Session→Run→ModelStep→ModelAttempt→operation 锁序验证 Runtime owner、credential receipt
  与 tenant/provider/capability gate，从数据库读取 kill epoch，并原子提交
  ModelAttempt `prepared→dispatching/request_started` 和唯一 Gateway operation。
- Gateway claim v2 只接受与当前 dispatching Attempt version 一致的 durable operation；
  227_18 submit/claim 对 Runtime/Gateway 撤权，其他 UNKNOWN/recovery/finalize 合同保持不变。
  Python scoped repository 暴露 secret-free typed dispatch binding，尚未接 ModelLoop、UDS
  Runtime client 或 production composition，`production_ready=false`。

Agent Runtime AR-18-A1.1 legacy lifecycle fence：
- `backend/migrations/227_21_agent_runtime_legacy_lifecycle_fence.sql` 仅将 Runtime key 缺失或
  严格为 JSON boolean false 的 task 视为 legacy-safe；其他值均从旧 startup orphan recovery
  与 legacy stale timeout 的 discovery/claim 排除，并在旧 complete/fail/stale RPC 内再次
  失败关闭。Rollback 遇到非终态非 legacy-safe marker 时拒绝；Actor 排除与普通 legacy
  task 行为不变。

Agent Runtime AR-18-A1.2-B1 atomic task cancel intent：
- `backend/migrations/227_22_01～227_22_03` 建立 owner-only、FORCE RLS 的 durable
  task cancel intent 与 Runtime/WeCom Runtime v1 facade；固定 Session→submit Command→Command Claim
  →Cancel Intent→Run→Task→既有 cancel helper 锁序。Cancel-first 原子创建并 CAS 绑定唯一
  cancelled 根 Run；claim scanner 与 direct `create_agent_run` 均在 Run DML 前受 intent fence。
- Request identity 使用 SHA-256，将 nullable session/command `scope_user_id` 与非空
  `requested_by_user_id` 分离并纳入不可变 idempotency identity；user scope 绑定 owner，
  channel scope 绑定 org/channel/conversation 并要求请求者为 active org member。
  Task/Message/Command 绑定保持不可变；
  rollback 遇到任意 intent fact 失败关闭。当前未接 Web API 或 Provider cancel handoff，
  production flags 保持关闭。
- `backend/migrations/227_23_agent_runtime_task_cancel_facade_callable.sql` 新增不接收
  client hash 的 v2 facade，由 PostgreSQL 私有 helper生成 canonical hash后委托 v1；v1 与
  hash helper 对外撤权，仅 v2 授权 Runtime/WeCom Runtime。该 lane 不拥有或删除 facts，
  rollback 只删除 v2 并精确恢复 v1 ACL，因此即使已有 227_22 intent facts 也无需 guard。

Agent Runtime AR-18-A1.2-B3 Provider cancellation handoff：
- `backend/migrations/227_24_agent_runtime_provider_cancel_handoff.sql` 为 accepted/unknown
  Action reconciliation claim 保留 220_25 对 durable dispatch intent + expired dispatching 的
  unknown/retry-after-reconcile 转换，再持久绑定 `reconcile|cancel` operation、父 Run identity/version；
  cancelled 父 Run 始终进入 cancel，正常 claim 与响应丢失 readback 不重新推导不同 operation。
- Provider cancel 只在续租 reconciliation lease 下执行；confirmed 必须同时满足 receipt 与
  durable provider fact、token/state-version/request-hash、owner/kill/revision fence，随后原子终结
  Attempt/Action、记录 refund/event并保持父 Run cancelled 与 blocker=0。UNKNOWN 写入明确的
  `next_reconcile_at`，保持 unknown/reconcile-only，后续仍领取 cancel且从不普通重派。
- `request_agent_runtime_provider_cancel` 仅授权 `everydayai_agent_runtime_worker`；rollback 在
  cancelled Run 的 accepted/unknown、活动 reconciliation lease 或未收敛 cancel facts 存在时
  失败关闭，并恢复 226_13 函数、227_04 ACL及新增 claim binding columns。

Agent Runtime AR-18-A1.2-B4 Model Gateway cancel fence：
- `backend/migrations/227_25_agent_runtime_model_gateway_cancel_fence.sql` 将 Run cancel
  扩展到 Model Gateway operation：按 Session→Run→ModelStep→ModelAttempt→operation 锁序，
  `submitted/claimed` 收敛为明确 pre-dispatch failed fact，`dispatching` 保守收敛为
  durable unknown，既有 `completed/failed/unknown` 不可变。
- Gateway mark/renew/finalize 每次重新锁定并验证父 Run/ModelAttempt；cancel 先赢时旧 claim
  只能 readback/fenced，finalize 先赢则保留真实终态并由 Runtime 使用 late-receipt 语义。
  Gateway 下一次 lease poll 发现 fence 后取消 provider coroutine并关闭 adapter，不依赖 Web
  进程内 gate；rollback 对 cancel-derived facts、cancelled Run 未收敛 operation与活动 lease
  失败关闭。

Agent Runtime AR-18-A1.2-B5 Sandbox cancellation handoff：
- `backend/migrations/227_26_agent_runtime_sandbox_cancel_handoff.sql` 将 cancelled Run 下的
  `code_execute` CANCEL operation绑定到唯一 Runtime facade；Action reconciliation token/lease、
  state/request hash、dispatch intent、父 Run version与kill epoch全量一致后才写Sandbox cancel intent。
- Sandbox worker仍是进程终止与cleanup证明的唯一owner；cancel intent 后数据库禁止写成
  succeeded/failed/timed_out。Runtime仅在durable cancelled receipt、cancel confirmation与cleanup
  proof一致时终结Attempt/Action，不改父Run或已清零blocker；unknown保持reconcile-only且不重派。
- 旧 `request_sandbox_job_cancel` 对Runtime撤权；rollback遇到cancel facts、活动reconciliation或
  cleanup residue失败关闭，零facts时恢复旧ACL。

Agent Runtime AR-18-A1.2-B6 Child Run recursive cancellation：
- `backend/migrations/227_27_agent_runtime_child_run_recursive_cancel.sql` 为每个直接 Child
  Action 建立owner-only、FORCE RLS durable cancel intent；Run cancel立即保留父Run cancelled，
  后台scanner按层递归创建孙层intent并等待Child、Provider或Sandbox各自proof收敛。
- Child create与cancel以parent action/ordinal唯一绑定：cancel-first确认not-created并禁止后建，
  create-first持久绑定权威child ID，且create context的org/user必须与父Run完全一致；响应丢失可按parent binding readback。Child专用finalizer仅在
  intent confirmed及reconciliation token/lease、state/request hash、kill epoch全部有效时终结父
  Attempt/Action，不改父Run、不递减blocker，也不复用Provider submission proof；它验证既有reserve
  fact，not-created幂等release，其余confirmed cancel幂等refund，重复finalize不重复结算或事件。
- requested/applied crash可由新scanner owner接管；任何UNKNOWN或未完成descendant proof永久保持
  reconcile-only，不按超时强制关闭；ownership-lost是正常takeover结果，不终止Runtime scanner。
  Rollback遇到任意intent事实或未收敛child依赖失败关闭，
  零事实时恢复227_25函数与既有ACL；budget递归治理不属于本批。

Agent Runtime S1 单一模型链收敛：
- `TECH_AGENT_RUNTIME_SINGLE_RUNTIME_CONVERGENCE.md` 是当前权威方案。Runtime 直接复用
  现有模型选择、配置 Bundle、KEK 和 adapter factory；`agent_model_attempts` 是唯一执行事实。
- 227_53 在 ModelAttempt 原子 dispatch 时冻结 kill epoch，并只允许同一 fenced Attempt
  读取 encrypted Bundle。独立 Gateway 进程、UDS、Python owner、systemd unit 与 env 已删除。
- 227_18～227_27 保留为不可改写历史 migration 账本，但无当前 Python 调用方或运行 Owner。

Agent Runtime C2.1 ERP 只读接线：
- `backend/services/agent/runtime/executors/erp_factory.py`：按不可变 Runtime
  scope 的 `org_id` 和 ActionAttempt fence 逐次解析既有企业 ERP Bundle，并构造现有
  `KuaiMaiClient`/`ErpDispatcher`；227_54 提供窄配置 readback 与 Token 版本 CAS。
- `erp_read_release.py` 与 227_55 确定性冻结包含六项 ERP Read 的 v5
  Catalog/Definition/EffectiveToolset；release 默认关闭，不改写 227_16/v4 历史身份。
- production composition 只注册六项 ERP Read；关闭请求参数日志与参数知识记录。
  ERP Write、ERP Sync、淘宝奇门、Media 继续关闭，整体 `production_ready=false`。

Agent Runtime S2-TAB-A 资源清单边界：
- `backend/services/agent/runtime/executors/resource_manifest.py` 与 227_56 只通过
  fenced ActionAttempt 读取现有 `task_attachment_refs`；Web 沿用固定输入消息回退，
  企微群沿用既有 channel Workspace owner 算法，不新增附件或 Workspace 体系。
- Runtime Worker 只有窄 RPC EXECUTE 权限且无附件表直权；`file_analyze`、
  `fetch_all_pages` 与 `local_data` 通过显式 adapter 接入，未注入时保持 unavailable，
  不回退通用 Artifact port。

Agent Runtime S2-TAB-B1 文件分析与 ERP 分页适配边界：
- `backend/services/agent/runtime/executors/data_adapters.py` 通过 TAB-A 冻结资源清单调用
  既有 `file_analyze`，并通过请求级 ERP dispatcher 调用既有只读分页逻辑；两者均不建立
  第二套文件、表格或 ERP 体系。
- 文件分析只允许清单内路径，暂不接受尚未证明可安全传递的 `sheet` 参数；分页保留脱敏的
  partial failure 证据且不会把自然结束误报为截断。

Agent Runtime S2-TAB-B3.2 数据读取发布闭环：
- `data_read_release.py` 与 227_58 确定性冻结 safe read、六项 ERP Read 以及
  `local_data`/`file_analyze`/`fetch_all_pages` 的 v6 Catalog/Definition/EffectiveToolset；
  不修改 227_02 或 227_54～227_57 历史事实。
- disabled toolset 只保留无副作用安全读取；ERP Write、Media、Scheduler、Sandbox、Export
  不注册，production flags 与 `production_ready` 继续关闭。
- `file_analyze`、ERP 只读分页和 `local_data` 已具备 Runtime 适配器；Runtime 主构造入口
  显式注入三项只读 adapter，其他 Composition 仍只有在显式提供 port 时注册，不启用生产 flags。

Agent Runtime S2-TAB-B2 `local_data` 查询接入边界：
- `227_57_agent_runtime_local_query_facade.sql` 只新增 Runtime Worker 可执行的
  attempt-fenced 查询 facade；租户从当前 Action/Run 事实取得，Worker 无 ERP 业务表直权。
- facade 复用既有 `UnifiedQueryEngine` 和 ERP analytics RPC，不复制 Filter DSL 或查询 SQL；
  当前只允许 trend、compare、distribution 及安全的 daily-stats cross 指标。
- detail、export、summary、ratio、alert 和复合跨域指标仍关闭；没有把全局 DATABASE_URL、
  DuckDB 导出或裸文件 staging 接入 Runtime。Catalog schema 只开放 trend、compare、distribution
  与安全 cross 指标。

Agent Runtime Projection dead stream恢复：
- `backend/migrations/220_26_agent_runtime_projection_dead_recovery.sql`及rollback：
  增加tenant-scoped inspect、严格幂等人工requeue、不可变恢复审计事实，并将通用
  Projection claim收紧为audit-only。
- `backend/services/agent/runtime/ports/projection_recovery.py`及
  `infrastructure/postgres/projection_recovery.py`：定义active super_admin Runtime
  调用的只读检查和人工恢复适配；未接API、UI、startup或ingress。
- `docs/document/TECH_AGENT_RUNTIME_ProjectionDeadStream恢复.md`：记录顺序阻塞、
  Session→Outbox锁序、恢复审计、权限和回滚合同。

Agent Runtime AR-11 ModelAttempt与唯一计费：
- `docs/document/TECH_AGENT_RUNTIME_AR-11ModelAttempt与唯一计费结算.md`：冻结
  Provider单dispatch、unknown/reconcile、late receipt和财务幂等边界。
- `backend/services/agent/runtime/domain/model_attempt.py`、`ports/model_attempt.py`：
  ModelAttempt状态与持久化Port的单一类型来源。
- `backend/services/agent/runtime/infrastructure/postgres/model_attempt_repository.py`：
  Worker Scoped RPC adapter和response-start observer。
- `backend/migrations/217_01_agent_runtime_model_attempt_foundation.sql`至
  `217_04_agent_runtime_model_attempt_reconciliation.sql`：按完整文件名顺序建立
  Attempt、唯一结算、生命周期和恢复能力；不修改迁移212～216。
- 当前仍是additive foundation，生产Chat Owner继续为Conversation Actor；AR-12完成
  Action统一终态前禁止切换Runtime Owner。

Agent Runtime AR-12 Action持久化与Tool终态：
- `docs/document/TECH_AGENT_RUNTIME_AR-12Action持久化与Tool终态.md`：冻结Tool Calls
  唯一原子终态、Action恢复、claim批次回读、依赖、取消、权限与回滚合同。
- `backend/services/agent/runtime/ports/action_repository.py`及
  `infrastructure/postgres/action_repository.py`：定义Action lifecycle typed Port，
  并通过Worker Scoped RPC映射terminal、claim、reconcile与readback。
- `backend/migrations/218_01_agent_runtime_action_foundation.sql`至
  `218_04_agent_runtime_action_reconciliation.sql`及同编号helper迁移：建立Action、
  ActionAttempt、ActionResult、claim batch、数据库SHA-256身份、FORCE RLS和窄RPC。
- 当前仍为additive基础设施，生产Chat Owner继续为Conversation Actor；Coordinator、
  Executor/Policy正式接线及生产切换属于后续AR任务。

Agent Runtime AR-13 Command Claim与Coordinator骨架：
- `docs/document/TECH_AGENT_RUNTIME_AR-13CommandClaim与Coordinator骨架.md`：冻结
  pending Command扫描、CommandClaim lease/fencing、唯一Run、恢复与终态兼容合同。
- `backend/services/agent/runtime/ports/command_claim.py`、
  `infrastructure/postgres/command_claim_repository.py`及
  `application/coordinator.py`：提供typed CommandClaim Port、Worker Scoped RPC adapter
  和PostgreSQL优先的Coordinator扫描/续租骨架。
- `backend/migrations/219_01_agent_runtime_command_claim_foundation.sql`至
  `219_02a_agent_runtime_command_claim_terminal_compatibility.sql`：建立CommandClaim
  事实、窄RPC、Run原子创建、Event/Outbox、历史Run状态兼容与有效lease保护。
- 当前仍未接入生产startup/ingress或完整Model/Action循环；Conversation Actor继续是
  生产Chat Owner，AR-14～AR-16完成前不得切换。

定时任务委托执行边界：
- `docs/document/TECH_定时任务委托执行边界.md`：定义 Worker 控制面、Runtime 工具面、
  任务级数据库身份和无人值守工具白名单。
- `backend/migrations/179_scheduled_run_fencing.sql`：为定时 run 增加 fencing token、
  租约、原子结果消息、终态失效和遗留积分退款能力。
- `backend/services/scheduler/run_lease.py`：持续续租并在执行权丢失时取消执行。

媒体 Worker 终态边界：
- `backend/migrations/186_worker_media_failure_settlement.sql`：由
  `everydayai_worker` 专属 SECURITY DEFINER 能力在同一事务内完成失败退款与媒体
  task 终态，Worker 不获得底层退款函数的直接执行权。
- `backend/migrations/187_worker_media_message_scope_types.sql`：在批次消息提交能力中
  以 TEXT 统一比较 JSON UUID 与历史 task 标识列，写入 messages 时仍执行 UUID 强校验。
- `backend/migrations/188_worker_media_message_write_types.sql`：按生产 messages schema
  显式转换 role、content 和 task_id，避免 JSON text 依赖 PostgreSQL 隐式赋值转换。
- `backend/services/worker_media_tasks.py`：封装跨租户媒体发现、领取、触达和终态 RPC；
  JSONB 参数由 `core/local_db.py` 的共享 RPC 边界统一适配。

统一生成 Turn 事务与任务生命周期设计：
- `docs/document/TECH_统一生成Turn事务与任务生命周期.md`：将 Web、企微、Chat Actor、图片、视频和
  电商图的 request/Turn/input/output/local task 收口为统一数据库原子准备入口；外部执行状态机保持
  分离，并定义历史锚点回填、媒体供应商孤儿任务治理、灰度部署和回滚边界。
- `backend/migrations/148_unified_generation_prepare.sql`：统一生成准备、供应商任务附加和 preparing
  失败终态 RPC；rollback 在无 preparing task 时恢复旧 task 状态约束。
- `backend/migrations/149_generation_message_content_type.sql`：修复统一准备函数中 JSONB payload 与
  TEXT 消息内容的显式类型边界，供已应用迁移 148 的环境增量升级。
- `backend/api/routes/message_chat_preparation.py`：Web Chat 原子准备 payload、权威上下文锚点与 Actor 启动编排。
- `backend/api/routes/message_image_preparation.py`：Web 普通图片批次的原子准备、占位消息响应和 Handler 启动编排。
- `backend/api/routes/message_ecom_preparation.py`：Web 电商图 Phase 1 策划任务与 Phase 2 生图批次的原子准备编排。
- `backend/services/handlers/image_prepared_submission.py`：已准备图片 task 的积分锁定、供应商提交、
  租户范围拒绝退款、跨模型重试和最终 attach。
- `backend/api/routes/message_video_preparation.py`：Web 视频消息、Turn 和本地 preparing task 的原子准备编排。
- `backend/services/handlers/video_prepared_submission.py`：视频参数计费解析、稳定 task 积分绑定、
  租户范围拒绝退款、跨模型重试和最终 attach。
- `backend/tests/test_unified_generation_prepare_migration.py`：固定迁移 scope、锁顺序、显式 Retry 锚点、
  task 状态转换、权限和回滚门禁。
- `backend/services/generation_lifecycle.py`：统一生成准备、供应商 task 附加、preparing 失败和
  提交拒绝退款的类型化 Python 边界，严格解析数据库权威结果并输出完整业务上下文日志；
  退款失败向上失败关闭，避免继续重试扣费或提前终结 task。
- `backend/tests/test_generation_lifecycle.py`：覆盖 payload 序列化、RPC 非法返回、task ID 完整性、
  ContextAnchor 与 attach/fail 幂等结果。
- `backend/tests/test_message_image_preparation.py`：覆盖图片批次先落库、稳定 task 积分绑定、明确失败、提交结果未知和跨模型重试。
- `backend/tests/test_message_ecom_preparation.py`：覆盖电商图 Phase 1 的 running task 先落库与 Phase 2 复用原子图片批次。
- `backend/tests/test_message_video_preparation.py`：覆盖视频先落库、参数计费、稳定 task 积分绑定、明确失败、结果未知和跨模型重试。
- `backend/scripts/backfill_generation_turns.py`：历史 assistant Turn/reply 关系的默认 dry-run、确定性分类、分批 apply、checkpoint 与无正文审计。
- `backend/scripts/migration_runner.py`：完整文件名身份、SHA-256、显式 legacy baseline、事务执行和 advisory lock 的数据库迁移账本 Runner。
- `deploy/run-migrations.sh`：部署重启前执行迁移 plan/apply；关闭迁移但存在 pending 时失败关闭。
- `backend/uvicorn-log-config.json`：为 Uvicorn HTTP 与 WebSocket 日志统一安装认证
  查询参数脱敏过滤器，避免访问 Token 进入生产日志。
- `backend/migrations/167_wecom_role_cutover_completion.sql`：恢复 WeCom runtime
  消息门面，并提供不依赖业务表授权的 Worker Outbox 租约能力与载荷读取门面。
- `backend/migrations/168_wecom_runtime_read_capabilities.sql`：将企微积分与生成设置读取、
  身份后昵称刷新、新对话绑定旋转、记忆查看/清空收口为 Actor/Org 双重校验的
  SECURITY DEFINER 能力门面，消息 runtime 不再依赖业务表直权。
- `backend/migrations/169_wecom_generation_context_org_scope.sql`：为生成上下文能力增加
  `OrgScopedDB` 三参数适配，并在委托核心能力前验证显式企业与事务 Scope 一致。
- `backend/services/wecom/memory_commands.py`：企微记忆指令的专用 RPC 适配，避免复用
  面向 Web 的直表查询服务。
- `docs/document/TECH_企微数据库角色切换闭环修复.md`：记录角色切换 ACL 覆盖根因、
  Worker fencing 能力边界、异常回复协议及部署回滚验证。
- `docs/document/TECH_角色撤权后核心链路修复.md`：记录旧 owner 继承撤销后 Web 对话
  RLS、生成 RPC、Scoped RPC、Actor 配置/指标与 CDN 证书故障的生产证据、窄能力修复
  和撤权后联合验收门禁。
- `git-push.sh`：仅暂存当前任务显式文件，应用发布禁止路径与敏感内容门禁，并校验远端提交 SHA。
- `deploy/release.sh`：统一“提交部署”入口；提交推送后从确定 SHA 创建隔离工作树，避免工作区其他文件进入生产。
- `deploy/release-policy.conf`：集中定义 `.env`、`.cursor`、密钥、依赖和生成物等禁止提交路径。
- `deploy/deploy-helpers.sh`：承载发布来源门禁和部署后远端状态检查，保持主部署脚本规模受控。
- `docs/document/TECH_数据库租户纵深防御.md`：基于生产角色/RLS/policy 审计，设计租户 Registry、
  事务级 DatabaseScope、owner/migrator/Web runtime/WeCom runtime/worker 角色和 Agent
  Runtime 第一组 FORCE RLS；WeCom 消息面使用独立 runtime 登录能力，控制面继续使用 Worker。
- `docs/document/TECH_Web认证数据库能力门面.md`：设计认证前 runtime Scope、最小候选查询、
  注册/登录提交/refresh轮换/密码重置/登出数据库门面，以及第二批 RLS 权限边界。
- `backend/core/tenant_registry.py`：租户表类别、个人身份来源、父事实和应用过滤激活状态的唯一
  Registry；提供生产 pg_catalog 双向合同，首步不改变旧 OrgScopedDB 过滤集合。
- `backend/core/db_scope.py`：以不可变 DatabaseScope 包装同步/异步 Query、RPC 和数据库 client，
  在同一事务中先注入用户、企业、访问类别与请求标识；复用原连接池且不改变旧 client 行为。
- `backend/tests/test_wecom_request_scope.py`：验证企微不同企业消息使用独立请求级 DatabaseScope，
  用户映射后只提升当前消息身份，且不修改共享根服务。
- `backend/services/conversation_db_scope.py`：装配 Actor 跨租户 Worker 扫描 Scope，以及 claim
  后共享同一身份的异步控制面、异步应用层和同步 Handler 数据库门面。
- `backend/tests/test_conversation_db_scope.py`、`backend/tests/test_conversation_execution_scope.py`、
  `backend/tests/test_conversation_execution_claims.py`：覆盖 Worker 无租户身份、claim RPC、
  任务身份失败关闭及执行/提交/通知切换到任务 DB。
- `backend/tests/test_db_scope_raw_connection.py`：覆盖异步 raw SQL 连接在同一事务注入 Scope、
  异常回滚、禁止显式结束事务、禁止绕过 scoped pool 取裸连接及双租户身份独立。
- `backend/tests/test_memory_scoped_database.py`：覆盖 Memory 从调用方解析可信 Scope、全局
  psycopg pool 的事务包装、跨租户 Scheduler 隔离、无 Scope 失败关闭及 Adapter 提交语义。
- `backend/services/knowledge_config.py`：Knowledge/Graph/Metrics 共享的 raw PostgreSQL 入口
  必须解析显式 DatabaseScope，并通过 `AsyncScopedConnectionPool` 在事务内注入；
  独立评分连接同样在业务 SQL 前注入 Worker Scope。
- `backend/services/knowledge_service.py`、`graph_service.py`、`knowledge_metrics.py`：在线调用
  显式透传 scoped DB，事务由 scoped connection context 统一提交/回滚。
- `deploy/setup-tenant-db-roles.sh`：由 PostgreSQL 管理员显式执行的幂等角色初始化入口；
  包含一次性 config-import-reader、migrator 与各运行角色，密码完全独立；
  密码只从环境变量读取，创建 NOLOGIN owner 和独立 migrator/runtime/worker，且不修改
  业务对象 owner、grant、policy 或现有服务连接。
- `deploy/run-psql-admin.py`：将管理员 PostgreSQL URL 安全解析为 libpq 环境变量后
  `exec psql`，避免凭证进入进程参数，并拒绝未知、重复或不安全连接参数。
- `backend/tests/test_run_psql_admin.py`：覆盖 URL 解码、TLS/超时参数及不安全连接串拒绝。
- `backend/tests/test_tenant_db_roles_script.py`：覆盖缺失/弱/复用密码拒绝、角色能力边界、
  SQL 字面量转义、密码不进入日志以及 psql 失败传播。
- `deploy/env-templates/*.env.template`：runtime、worker、migrator 的无凭证连接文件合同，
  分别约束 `DATABASE_URL` 或 `MIGRATION_DATABASE_URL` 的数据库登录角色。
- `deploy/everydayai-agent-runtime.service` 与
  `deploy/env-templates/agent-runtime-worker.env.template`：独立 Runtime Worker 只加载窄角色
  与进程运行配置；独立 typed settings 禁止读取 `backend/.env`，Systemd 也屏蔽该文件及
  历史模型环境文件；Provider Secret/KEK 不进入进程环境，凭证必须经 CredentialBroker。
- `deploy/validate-tenant-db-env.sh`：切换服务前验证真实角色文件的存在性、0600 权限、
  角色用户名、占位符清理和连接串独立性；Runtime/WeCom 文件使用显式多键白名单，其他
  角色保持单键。`--runtime-flags-off-v3` 严格入口额外锁定 v3 且拒绝任一启用开关，
  全程不输出连接内容。
- `backend/tests/test_tenant_db_env_contract.py`：覆盖模板安全性及角色环境合同的成功/失败边界。
- `deploy/env-templates/sync.env.template`：Sync/ERP 使用独立
  `everydayai_sync` 数据库角色的环境模板
- `backend/services/configuration/external_control.py`：快麦外部凭证的 Runtime
  管理员配置控制面适配器。
- `backend/services/kuaimai_external/manual_worker.py`：Sync 服务持久手动同步队列
  消费器。
- `backend/api/routes/kuaimai_external_credentials.py`：拆分后的快麦凭证管理与探活路由。
- `frontend/src/components/integrations/KuaimaiSourcesTab.tsx`：快麦数据源凭证卡片、
  cURL 配置引导与保存反馈。
- `docs/document/TECH_快麦凭证保存修复.md`：快麦 cURL 校验、原子保存、失败语义与回滚设计。
- `docs/document/TECH_异步任务占位消息原位更新.md`：定义媒体任务按消息 ID 原位更新、
  文字流条件式清理和乱序完成下的稳定展示顺序。
- `deploy/transfer-sync-domain-ownership.sh` /
  `deploy/rollback-sync-domain-ownership.sh`：Sync 数据域 owner 原子切换与回滚。
- `deploy/grant-sync-wecom-employee-access.sh` /
  `deploy/rollback-sync-wecom-employee-access.sh`：由数据库管理员精确授予或撤销
  `everydayai_owner` 对企微员工匹配所需四个字段的只读权限；迁移 219 负责验证
  RPC owner、`SECURITY DEFINER` 和禁止 Sync 直读的权限契约。
- `backend/tests/test_service_database_role_files.py`：固定 Backend/WeCom、Actor、Sync 的
  Systemd 数据库角色覆盖文件映射，防止服务再次全部回退到共享 `.env`。
- `deploy/env-templates/worker-client.env.template`：Web 内后台任务与 Actor raw SQL 使用的
  `WORKER_DATABASE_URL` 无凭证模板；必须与 `.env.worker` 指向同一 Worker 连接。
- `deploy/env-templates/wecom-runtime.env.template`：WeCom 入站消息面使用的独立
  `everydayai_wecom_runtime` 无凭证连接合同；与 Web runtime 和 Worker 连接完全分离。
- `backend/services/web_database_runtime.py`：集中管理 Web 内知识 Seed、启动恢复、
  BackgroundTaskWorker、错误监控及 runtime/worker 数据库池关闭生命周期。
- `backend/tests/test_worker_database_client.py`、`backend/tests/test_web_database_runtime.py`：
  覆盖缺失 Worker URL 失败关闭、独立池创建/关闭、runtime schema 与 worker 后台身份分离。
- `backend/wecom_ws_runner.py`：WeCom 单进程内分离 control/runtime 数据库身份；bot
  先用 actorless Worker 发现企业，再以逐企业精确 Scope 读取固定 Bundle；Outbox 走
  Worker client，入站消息与卡片事件走请求级 runtime Scope。
- `backend/tests/test_wecom_ws_runner.py`、`backend/tests/test_wecom_ws_runner_main.py`、
  `backend/tests/test_wecom_request_scope.py`：固定 WeCom 双客户端装配、消息级企业/用户
  作用域和三类连接池关闭合同。
- `backend/tests/test_wecom_ws_outbound_ack.py`：以本地 mock 固定 typed 主动发送的 ACK
  证明状态、稳定 provider request ID、并发隔离、幂等/冲突、late ACK 与有界清理合同。
- `backend/tests/test_wecom_app_outbound_hardening.py`：固定 App HTTP 部分失败字段类型、
  不等待吞取消依赖的绝对 deadline，以及 late completion 不升级 typed receipt 合同。
- `backend/services/agent/runtime/wecom_app_credentials.py`：Runtime-owned 企微 App
  tenant-scoped credential/token builder；每次 token 调用经 CredentialBroker lease controlled
  consumer 将 opaque material 交给具备 readiness 的显式 exchange port 派生 token；本模块
  不规定 material schema、不提供真实 token HTTP 实现，broker/exchange 非 production-ready
  或 lease handle 不匹配均失败关闭，exchange 取消在 material frame 退出后重建传播。
- `backend/tests/test_agent_runtime_wecom_app_credentials.py`、
  `backend/tests/test_agent_runtime_wecom_app_outbound_composition.py`：固定租户与 lease binding、
  material 不逃逸、exchange 失败关闭/取消传播，以及显式 send HTTP client 的
  ACK/NOT_STARTED 组合分类。
- `backend/migrations/152_wecom_runtime_capability.sql`：以不可变迁移增量扩展 WeCom
  runtime 角色匹配，并提供 org/corp/角色校验的身份、聊天地址和聊天目标安全门面。
- `backend/migrations/rollback/152_wecom_runtime_capability_rollback.sql`：删除 WeCom
  门面并恢复原 runtime/worker 角色匹配，不重新开放旧函数的 PUBLIC EXECUTE。
- `backend/tests/test_wecom_runtime_capability_migration.py`：固定 SECURITY DEFINER、
  search_path、PUBLIC revoke、历史 NULL 认领、跨企业冲突及 rollback 合同。
- `deploy/transfer-agent-runtime-ownership.sh`：管理员执行的迁移账本、首组 13 表和资产函数
  原子 owner 转移；先赋予旧角色临时 owner 成员关系，兼容生产既有无 policy RLS 表。
- `deploy/rollback-agent-runtime-ownership.sh`：受显式危险操作开关保护的 owner 恢复入口；
  恢复迁移账本、13 表和资产函数；任一目标表仍启用 FORCE RLS 时失败关闭。
- `deploy/finalize-tenant-db-role-cutover.sh`：仅在 150–180 已应用、Actor/媒体/定时 Worker
  Facade 权限完整、全部目标对象 Owner
  正确、服务已切换且旧连接归零后，撤销旧角色的临时 owner 成员关系。
- `backend/tests/test_tenant_db_role_finalize_script.py`：覆盖最终撤销双重人工门禁、迁移、
  Owner、独立管理员、旧连接检查及旧角色名注入拒绝。
- `backend/tests/test_agent_runtime_ownership_scripts.py`：覆盖精确 13 表范围、前置检查、
  owner 辅助读取授权、旧服务兼容授权、无提前 RLS、管理员 URL 隐藏及回滚保护。
- `deploy/transfer-memory-runtime-ownership.sh`、
  `deploy/rollback-memory-runtime-ownership.sh`：原子转移或受保护地恢复 Memory Runtime
  四表与两个提交函数 owner；回滚在任一目标表仍 FORCE RLS 时失败关闭。
- `backend/migrations/165_memory_runtime_tenant_boundary.sql`：为 Pipeline State、
  Session Log、Consolidation Run 与 Curated Atom 启用 FORCE RLS，绑定用户、企业、
  会话及来源日志，并只授予 runtime/worker 实际所需能力。
- `backend/tests/test_memory_runtime_tenant_boundary_migration.py`、
  `test_memory_runtime_ownership_scripts.py`、
  `test_memory_runtime_role_matrix_external.py`：覆盖迁移/回滚静态合同、所有权脚本门禁及
  真实 PostgreSQL Worker 跨租户隔离矩阵。
- `deploy/transfer-worker-control-ownership.sh`、
  `deploy/rollback-worker-control-ownership.sh`：原子转移或受保护地恢复错误日志、知识指标、
  定时任务和执行记录四表及其列序列 owner；保留 Sync 旧角色兼容，但不授予 Worker 直表权限。
- `backend/migrations/180_scheduled_task_tenant_boundary.sql`：对定时任务定义和运行记录启用
  FORCE RLS，只向 Web Runtime 开放企业 Scope 内的任务管理与运行历史读取。
- `backend/migrations/166_wecom_worker_discovery.sql`：提供无 Secret 的 WeCom 企业目标
  Discovery，只向 actorless Worker 返回 active 企业 ID 与凭证版本；rollback 仅撤销
  新能力。
- `deploy/install-service-units.sh`：普通模式验证角色与 KEK 环境文件并安装既有服务单元；
  `agent-runtime-only` 模式验证 Runtime Worker、Runtime Model、Projection、Authorization 与
  Sandbox 环境文件，只安装四个 Agent Runtime 单元与 wrapper 后执行 daemon-reload。已有目标
  不一致时在任何写入前失败关闭；`control-plane-only` 只处理 Runtime、Projection、Authorization，要求
  reviewed target SHA-256 manifest，不依赖或触碰 Sandbox env/assets/wrapper。
- `deploy/provision-control-plane-worker-envs.py` 与 `deploy/control_plane_env_source.py`：服务器端从
  安全的 `backend/.env`、`.env.migrator`、`.env.kek` 读取三个窄角色密码、既有运行配置和
  最小 KEK，保持 migrator DSN 非凭证部分不变并 URL encode 新凭证；四份 env 先在
  release-bound、root-only transaction
  目录完整 staging，并记录旧内容 hash、mode、uid/gid 与不存在状态，再以
  目录完整 staging；普通 env 为 root:everydayai-app，Runtime Model env 为
  root:everydayai-runtime-model-secret，均以 0640 原子发布，journal 精确记录并恢复 uid/gid。
  Runtime production flag 固定关闭，
  且不生成 Sandbox env。
- `deploy/update-control-plane-units.sh`：在三个 unit 严格 inactive + disabled 且当前 SHA-256
  全量匹配 reviewed manifest 后，将旧 unit 全部备份到同一 release transaction，再发布
  四 env 与三个 unit。状态为 `prepared → published → restored`；env/unit apply、daemon-reload、
  内外层 postcheck 任一失败均统一恢复，重复 rollback 幂等，错误 release 或外来内容由 hash
  fence 失败关闭。
- `deploy/check-control-plane-unit-manifest.sh`：由发布入口通过 stdin 发送到远端的只读预检，
  在 rsync 发生前核对三个当前 target unit 的 reviewed SHA-256，避免 mismatch 时产生同步写入。
- `deploy/deploy.sh` / `deploy/release.sh` / `deploy/runtime-flags-off-install.sh`：提供互斥的
  `--runtime-flags-off-install` 与 `--runtime-control-plane-flags-off-update` 路径；后者
  要求 reviewed manifest 且不执行 code/backend 同步、migration、Owner 或服务生命周期
  操作。`check-agent-runtime-unit-states.sh` 支持 all/control-plane scope；旧安装路径预检
  还允许未安装 unit 的 inactive + not-found，完成后要求四个 unit inactive + disabled；
  新更新路径前后均要求三个控制面 unit 严格 inactive + disabled。
- `backend/tests/test_agent_runtime_flags_off_install.py`：覆盖严格 Worker/Runtime Model 配置、四单元安装、
  差异目标零写入失败关闭、远端状态门禁顺序及模板/unit 合同。
- `backend/tests/test_agent_runtime_control_plane_update.py`：动态覆盖 Secret 隔离与 URL encode、
  env mode/owner、reviewed hash/state 零写入、七目标备份/发布顺序、unit/daemon/postcheck
  统一恢复、幂等/hash fence、Sandbox 零触碰及 release/deploy 互斥路由。
- `backend/tests/test_agent_runtime_control_plane_env_transaction.py`：动态覆盖四份 env 各阶段
  staging/publish 故障、后验故障、原内容与 mode/uid/gid/不存在状态恢复及 release/hash fence。
- `deploy/transfer-runtime-message-ownership.sh`：原子接管 Runtime/Message 第二批 19 张表、
  实际列 sequence 和 37 个固定业务函数签名（含 Actor 核心依赖、两个 WeCom enqueue
  重载及四个 Outbox 租约函数）；撤销
  PUBLIC/新角色权限并保留旧服务兼容权限。
- `deploy/rollback-runtime-message-ownership.sh`：要求服务先切回旧连接且目标表均未
  FORCE RLS，随后恢复第二批表、sequence 和函数 owner。
- `deploy/preflight-tenant-cutover.sh`：兼容总入口，编排
  `deploy/preflight/tenant-role-capabilities.sh`、`tenant-core.sh`、
  `admin-user-assets-capability.sh` 与 `worker-control.sh`；先核验隔离角色集合及
  Runtime 旧 Chat enqueue 撤权，再核验核心域、管理员资产和 Worker Control 的
  checksum、owner、ACL、RLS 与能力边界。
- `backend/migrations/217_organization_lifecycle_governance.sql`、
  `218_suspended_organization_execution_fence.sql`：提供平台企业原子停用/恢复、脱敏
  治理审计、active 邀请/任务发现与 suspended 企业服务写入 Fence。
- `deploy/preflight/organization-lifecycle.sh`：只读核验生命周期迁移、Runtime-only
  RPC 精确 ACL、owner/SECURITY DEFINER/search_path、完整 trigger 集合与 suspended
  执行 Fence。
- `backend/tests/test_organization_lifecycle_external.py`、
  `test_organization_lifecycle_permissions_external.py`：在显式隔离 PostgreSQL 中分离
  验证迁移/并发/事务/逆序 rollback 与 Actor/Scope/角色 ACL/四类服务 Fence，单文件
  均保持在 500 行以内。
- `backend/api/routes/org_lifecycle.py`：隔离平台企业停用/恢复路由及安全数据库故障映射，
  避免继续扩大既有企业管理路由文件。
- `frontend/src/components/admin/useOrganizationLifecycle.ts`、
  `SuperAdminPanelSections.tsx`：隔离生命周期请求状态和创建/列表/确认区块，保持请求取消、
  权威刷新与前端函数长度阈值。
- `deploy/transfer-admin-user-assets-ownership.sh`：在迁移 209 前仅校验并将旧
  `list_admin_user_assets` 查询函数从遗留角色转移给 `everydayai_owner`，不修改
  其他对象或 ACL。
- `backend/scripts/verify_worker_control_preconditions.py`：部署发现 171–180 pending 时，
  在 apply 前验证四张依赖表及其列序列均已属于 `everydayai_owner`，否则失败关闭。
- `backend/scripts/verify_runtime_generation_capabilities.py`：迁移完成、服务重启前验证
  Backend 实际使用 `everydayai_runtime`，公开统一生成准备/提交入口由 owner 持有
  且采用 DEFINER，私有实现/helper 与任务队列序列不向 Runtime 暴露；同时通过
  Worker 连接核验媒体查询、结算、重试和指标能力，否则部署失败关闭。
- `backend/migrations/206_runtime_generation_capability_facade.sql`：把 148 原子准备实现
  收口为 owner 私有函数，并以校验 Runtime、Actor 和企业 Scope 的同签名安全门面
  作为唯一公开入口。
- `backend/migrations/207_runtime_media_submission_capabilities.sql`：把媒体外部任务
  attach/fail 提交转换收口为校验 Runtime Actor、企业和任务归属的安全门面；Provider
  Webhook 使用专用 actorless Worker 数据库连接完成媒体回调结算。
- `backend/migrations/218_runtime_prepared_credit_refund.sql`：为图片/视频供应商明确拒绝及
  智能重试提供 Runtime 专用退款门面；绑定 Actor、企业、preparing task 与积分交易后
  才调用 owner 底层原子退款，不向 Runtime 开放通用退款能力。
- `backend/migrations/208_worker_periodic_monitor_completion.sql`：为双 Uvicorn
  进程内的模型评分与企微巡检增加数据库周期租约；企微巡检改用无 PII 的 Worker
  健康快照，模型评分过滤非性能信号并修复 32 位 Knowledge 哈希与重复审核提交。
- `docs/document/RUNBOOK_171_180_Worker_Control生产恢复.md`：固定 179 failed 协调、
  owner 转移、迁移重放、服务重启与真实链路验收顺序。
- `docs/document/RUNBOOK_150_161_生产租户架构切换.md`：串联生产只读审计、两批
  owner 转移、150–164 迁移、旧配置原子导入、分服务角色切换、最终权限收口与回滚
  边界；明确两批 owner 先就绪，再由标准 Runner 一次性应用全部 pending 迁移。
- `backend/tests/test_runtime_message_ownership_scripts.py`：覆盖第二批精确对象、动态列
  sequence、权限收紧、管理员 URL 隐藏和双重回滚保护。
- `backend/migrations/153_runtime_message_rls_and_auth.sql`：建立六个 Web 认证事务门面、
  第二批 17 表 ENABLE RLS/policy、Web 普通能力及 152 WeCom 门面最小授权。
- `backend/migrations/189_web_runtime_access_completion.sql`：在第二批 owner 转移后重建
  Web Runtime 核心 ACL，并以数据库验证的 active super_admin 只读 policy 支持平台
  管理页面；不扩大 WeCom、Worker 或 Sync 权限。
- `backend/migrations/190_message_idempotency_role_capabilities.sql`：把消息生成幂等领取
  绑定到精确 Runtime Actor/Org Scope，并将 TTL 清理收口为无 Actor 的 Worker
  SECURITY DEFINER 能力；所有权转移和回滚脚本同步覆盖两个函数。
- `backend/migrations/191_governance_actor_authority.sql`：向 Runtime 提供当前 Actor
  在指定企业内的窄角色能力，企业配置路由不再为鉴权直读组织和成员控制表。
- `backend/migrations/192_atomic_organization_permission_initialization.sql`：把企业、
  Owner 成员、系统职位、角色权限、默认部门、默认角色映射与 Owner Boss 任职合并为
  单个治理事务；权限蓝图不完整时整体回滚。
- `backend/migrations/193_runtime_assignment_read_capabilities.sql`：以 active 企业
  Scope 提供单人/批量任职、部门/职位目录和部门成员集合读取；权限计算与定时任务展示
  不再依赖服务角色直读企业权限模型表。
- `backend/migrations/194_governed_assignment_management.sql`：为 owner/admin 提供成员
  任职聚合、企微成员任职聚合和原子任职更新；admin 不可修改 owner/admin 或授予
  boss/vp，boss 只能绑定企业 owner，所有成功更新同事务审计。
- `backend/migrations/195_organization_member_display_name.sql`：只更新
  `org_members.display_name` 企业内显示名，禁止企业管理员覆盖用户个人
  `users.nickname`，并沿用治理角色层级与审计。
- `backend/migrations/196_runtime_tool_audit_capability.sql`：Runtime 工具审计改为
  由 task 反查 conversation/user/org 的窄 RPC；分区维护函数固定以 owner 身份执行，
  防止未来分区重新归属旧角色。
- `backend/migrations/197_runtime_knowledge_tenant_boundary.sql`：为知识节点和关系边增加
  明确的个人所有者，系统、企业、散客事实分别使用双空、org、owner 三类身份；Runtime
  RLS 与唯一索引同步隔离，Worker 收口前暂不 FORCE。
- `backend/migrations/198_worker_model_scoring_capabilities.sql`：Worker 通过 Snapshot
  RPC 读取七日指标和最近评分，并以单事务 Commit RPC 写评分知识和审核日志；无企业
  指标按 `user_id` 分组，禁止把不同散客聚合为系统知识。
- `backend/migrations/199_platform_error_monitor_capabilities.sql`：错误监控后台改用
  五个超管窄能力并撤销 Runtime 直表权限；`error_logs` 与尚未启用业务调用的
  `permission_audit_log` 均切为 owner-only + FORCE RLS。
- `backend/migrations/200_web_wecom_control_capabilities.sql`：Web 群管理、定时任务
  推送目标和主动推送地址改用企业治理窄能力；主动推送通过 Redis 跨进程交给独立
  WeCom WS Runner，不再读取另一个进程的内存连接。
- `backend/migrations/201_wecom_callback_inbox.sql`：增加企业级 Callback Bundle 和
  FORCE RLS 持久化 Inbox；Backend 验签解密后幂等入队，WeCom Runtime 以租约领取、
  完成或指数退避重试，Worker 清理到期终态。
- `backend/migrations/202_knowledge_audit_force_rls_completion.sql`：在 Runtime
  知识边界、Worker 模型评分和工具审计能力均收口后，为 Knowledge/Audit 五表补齐
  owner policy、启用 FORCE RLS，并固定四类服务角色的最小表级权限。
- `deploy/preflight/knowledge-audit-completion.sh`：最终撤销旧 owner 能力前独立核验
  Knowledge/Audit 的 owner policy、FORCE RLS 与 Runtime/WeCom/Worker/Sync ACL。
- `backend/tests/test_model_scorer_rpc.py`、`test_model_scorer_formatting.py`：分别覆盖
  Worker Snapshot/Commit 主链与评分时间格式；原评分测试文件保持结构门限以内。
- `backend/api/routes/scheduled_task_support.py`：承载定时任务请求模型、频率解析和
  创建者任职展示聚合；主路由保持 500 行以内，API 行为不变。
- `backend/api/routes/org_public.py`：公开企业登录页的名称查询入口，只调用迁移 189
  的窄能力，不直接穿越企业 RLS。
- `docs/document/TECH_Web运行时角色切换闭环修复.md`：记录 Web ACL 覆盖根因、请求级
  Runtime Scope、平台管理员读取、部署门禁、角色矩阵和回滚设计。
- `backend/migrations/rollback/153_runtime_message_rls_and_auth_rollback.sql`：撤销 153
  权限和 policy、恢复迁移前 RLS 状态并删除认证门面，不删除业务事实。
- `backend/tests/test_runtime_message_rls_and_auth_migration.py`：覆盖认证角色/Scope、
  注册与 refresh 原子性、精确 policy 表集合、敏感表 owner-only 和 rollback。
- `backend/testing/auth_test_support.py`：集中构造认证 RPC 测试用户事实，避免拆分后的
  登录与 token 测试重复维护敏感字段契约。
- `backend/testing/tenant_role_matrix.py`：真实 PostgreSQL 角色矩阵的失败关闭配置门禁；
  要求显式开关、同一测试库、数据库名二次确认及三个精确运行角色。
- `backend/tests/test_tenant_role_matrix_config.py`：覆盖矩阵开关、URL、测试库确认、
  数据库一致性和角色身份校验。
- `backend/tests/test_tenant_role_matrix_external.py`：在显式隔离测试库中验证 17 表 RLS、
  个人/企业隔离、停用员工失权、敏感表直访拒绝及 Web/WeCom/Worker RPC 能力分区。
- `backend/migrations/154_wecom_message_rpc_facades.sql`：将既有 WeCom 消息函数改为
  owner-only core，并以角色、企业、用户和 corp 校验的 SECURITY DEFINER 门面对外。
- `backend/migrations/rollback/154_wecom_message_rpc_facades_rollback.sql`：撤销消息门面、
  恢复原函数名与迁移前活动记录权限。
- `backend/tests/test_wecom_message_rpc_facades_migration.py`：覆盖 core 隐藏、Scope 门禁、
  最小授权、旧角色切换窗口和 rollback 对称性。
- `backend/migrations/155_web_wecom_oauth_capabilities.sql`：建立 Web WeCom OAuth
  未登录/已登录双 Scope、企业配置精确读取、原子登录及绑定管理门面；跨用户身份冲突
  失败关闭，禁止在企业治理能力就绪前自动合并账号。
- `backend/migrations/rollback/155_web_wecom_oauth_capabilities_rollback.sql`：仅撤销
  OAuth RPC 与授权，不删除用户、映射、成员、token 或活动事实。
- `backend/tests/test_web_wecom_oauth_capabilities_migration.py`：覆盖 Scope 门禁、
  原子提交组成、跨用户绑定拒绝、最小角色授权与 rollback 对称性。
- `backend/migrations/156_governance_authority_foundation.sql`：建立管理员与企业治理的
  runtime/actor/org 统一授权根，以及不记录秘密值、仅 owner 可访问的 FORCE RLS
  审计账本；第一批只读门面覆盖本人企业/邀请、企业安全详情/成员和超管企业/用户查询。
- `backend/tests/test_governance_authority_foundation_migration.py`：覆盖治理授权 Scope、
  服务角色撤权、审计来源、FORCE RLS 和 rollback 顺序。
- `backend/migrations/157_governance_write_capabilities.sql`：原子执行企业创建/更新、
  成员增删/角色变更和邀请创建/接受，并在同一事务写入不含秘密值的治理审计。
- `backend/tests/test_governance_write_capabilities_migration.py`：覆盖最小授权、角色不变量、
  企业/邀请并发锁、成员上限、审计原子性和历史审计兼容回滚。
- `backend/tests/test_atomic_organization_permission_initialization_migration.py`：验证迁移
  192 的权限蓝图、单事务调用链、私有 helper 权限和对称回滚合同。
- `backend/services/configuration/definitions.py`：平台、企业和个人配置的代码 Registry
  唯一来源；提供 canonical JSON 与契约 SHA-256，首版覆盖 AI、ERP、企微和快麦 Web。
- `backend/migrations/158_configuration_control_plane_foundation.sql`：固化 Registry v1
  数据库投影，创建 configuration entries/policies 和 envelope secret records；
  三张业务事实表从创建起启用 FORCE RLS，服务角色没有直表权限。
- `backend/migrations/rollback/158_configuration_control_plane_foundation_rollback.sql`：
  按外键依赖顺序撤销 158 的四张空基础表。
- `backend/tests/test_configuration_definitions.py` 与
  `backend/tests/test_configuration_control_plane_foundation_migration.py`：逐项校验代码
  Registry 与 SQL 快照、Scope/Secret 约束、FORCE RLS、最小权限和回滚对称性。
- `backend/services/configuration/envelope.py`：定义可替换的 KEK Provider 边界、
  Local current/previous keyring 和数据库安全的 SecretEnvelope。
- `backend/services/configuration/material_service.py`：为每条 Secret 生成随机 DEK，
  以 scope/name/version AAD 执行 payload 加解密，不复用旧企业密钥。
- `deploy/env-templates/kek.env.template` 与 `deploy/validate-kek-env.sh`：提供无真实密钥
  的 KEK 文件格式，并强制真实文件为 0600、仅包含 current version 与 keyring。
- `backend/tests/test_configuration_envelope.py` 与 `backend/tests/test_kek_env_contract.py`：
  覆盖信封随机性、AAD 防替换、KEK 轮换/缺失、篡改失败关闭及部署权限合同。
- `backend/migrations/159_configuration_management_core.sql` 与
  `backend/migrations/159_configuration_management_facades.sql`：提供 Registry 契约读取、
  owner-only 配置写入核心，以及平台/企业/个人最小授权管理门面；使用版本 CAS，
  状态和审计不返回 Secret 材料。
- `backend/migrations/rollback/159_configuration_management_core_rollback.sql` 与
  `backend/migrations/rollback/159_configuration_management_facades_rollback.sql`：
  先撤销公开门面，再撤销内部管理核心，不删除 158 基础表。
- `backend/services/configuration/control_service.py`：校验代码 Registry 与数据库投影
  完全一致，并为三种 Scope 提供分离的配置 set/delete/status 调用。
- `backend/services/configuration/resolver.py`：严格校验固定 Bundle RPC 返回的键顺序、
  来源、版本、普通值与 SecretRef，拒绝 Registry、Scope 或 envelope 漂移。
- `backend/services/configuration/bundles.py`：仅暴露固定命名 Bundle 方法，在
  请求/任务内按数据库选定 Scope 解密 Secret，并由 WeCom Target Resolver 执行
  无 Secret Discovery → 逐企业精确 Scope → `wecom.bot` Bundle；企业管理测试使用
  独立 Runtime owner/admin 门面，不复用 Worker 门面；Scheduled WeCom Runtime 另以
  async `wecom_app()` 读取现有企业 App 三键 Bundle。
- `backend/migrations/216_configuration_admin_test_bundle.sql` 与对称 rollback：
  新增 Runtime 企业 owner/admin 专用企微测试 Bundle，保持 Worker 原门面及 ACL 不变。
- `backend/tests/test_configuration_admin_test_bundle_migration.py`：覆盖迁移 216 的固定
  Bundle、Runtime owner/admin 权限依赖、精确角色 ACL、Worker 隔离与无数据回滚。
- `backend/services/web_database_runtime.py`：Web 数据库启动在创建后台任务前执行
  Registry 漂移门禁，不一致时失败关闭。
- `backend/tests/test_configuration_management_migrations.py`、
  `backend/tests/test_configuration_control_service.py` 与
  `backend/tests/test_web_database_runtime.py`：覆盖权限、CAS、Secret 脱敏、错误映射和
  启动顺序；真实临时 PostgreSQL 另验证 apply/行为/rollback。
- `backend/migrations/160_configuration_resolution_core.sql` 与
  `backend/migrations/160_configuration_resolution_facades.sql`：固化 Bundle Registry，
  执行 user→organization→platform 有效层解析，并按 runtime/worker/wecom 角色仅开放
  无参数固定 Bundle 能力。
- `backend/tests/test_configuration_resolution_core_migration.py`、
  `backend/tests/test_configuration_bundle_facades_migration.py`、
  `backend/tests/test_configuration_resolver.py` 与
  `backend/tests/test_configuration_bundles.py`：覆盖继承、企业策略、角色矩阵、
  SecretRef/解密 Schema 和固定 RPC 映射。
- `backend/migrations/227_50_agent_runtime_scheduled_wecom_configuration_facade.sql` 与
  对称 rollback：将现有 `wecom.corp_id`、`wecom.oauth_agent_id`、
  `wecom.oauth_agent_secret` 组成固定 `wecom.app` Bundle，仅向 actorless、精确 active
  企业 Scope 的 Scheduled WeCom worker 开放无参数读取门面，不新增配置事实或 fallback。
- `backend/tests/test_agent_runtime_scheduled_wecom_configuration_facade_migration.py` 与
  PostgreSQL external：覆盖固定 Bundle、227_38 worker authority、tenant isolation、ACL、
  无直表权限及 apply/rollback/reapply。
- `backend/services/configuration/legacy_migration.py`：定义旧 `org_configs` 与快麦
  外部凭证到 Registry v1 的不可变组合契约；固定三次批量读取旧表，在内存中验证旧
  密文与 Corp ID 来源，再生成不含配置值的失败关闭预检报告。
- `backend/tests/test_configuration_legacy_migration.py` 与
  `backend/tests/test_configuration_legacy_collector.py`：覆盖原子 Secret 组合、未知键、
  Corp ID 双来源冲突、企业/全局旧密钥、损坏密文、孤儿/畸形行、明文 Cookie 拒绝和
  过期凭证保持禁用。
- `backend/migrations/161_configuration_legacy_import.sql`：仅向 migrator 开放显式 apply
  门控的全量原子导入 RPC；所有目标固定使用版本 0 CAS，并写入 FORCE RLS 脱敏审计。
- `backend/migrations/rollback/161_configuration_legacy_import_rollback.sql`：撤销未使用的
  导入能力；已有导入审计时拒绝删除，避免抹除持久证据。
- `deploy/grant-legacy-config-export-access.sh` 与
  `deploy/rollback-legacy-config-export-access.sh`：由独立数据库管理员精确授予或撤销
  `everydayai_owner` 对快麦旧凭证源表的单表只读权限，不改变旧表 owner 或 Reader 权限。
- `backend/migrations/162_configuration_legacy_export_access.sql` 与对应 rollback：把上述
  管理员 ACL 固化为迁移账本合同；授权缺失或 Reader 获得直表权限时失败关闭。
- `backend/tests/test_configuration_legacy_export_access.py`：覆盖管理员门禁、最小 ACL、
  精确撤销、迁移状态验证和 rollback 执行顺序。
- `backend/tests/test_configuration_legacy_import_migration.py`：覆盖 migrator 独占授权、
  批量边界、输入精确形状、单事务 CAS、脱敏响应/审计和有数据回滚拒绝。
- `backend/services/configuration/legacy_import.py`：将预检通过的旧配置值转换为 Registry
  v1 普通值或 envelope；固定组合 Secret、仓库 ID 去重、Corp ID 来源选择，并保持
  expired/invalid 外部凭证不配置。
- `backend/tests/test_configuration_legacy_import_planner.py`：覆盖转换映射、密文计划、
  预检/组织集合一致性、外部凭证状态、缺值失败关闭和计划对象脱敏。
- `backend/services/configuration/legacy_import_source.py`：把固定旧数据集合转换为对齐的
  预检报告和解密值；保留三查询兼容入口，正式 CLI 只使用 export payload 入口。
- `backend/services/configuration/legacy_import_source_executor.py`：使用专用 Reader 单连接，
  在一个只读事务/游标中执行角色校验、read GUC 和单次 export RPC，再交给严格解析器。
- `backend/services/configuration/legacy_import_executor.py`：验证显式确认和
  `everydayai_migrator` session_user 后，在同一 psycopg 事务/游标执行 `SET LOCAL` 与
  161 批量 RPC，并严格校验脱敏响应。
- `backend/scripts/migrate_legacy_configuration.py`：默认 dry-run 的一次性迁移入口；
  source/migrator DSN 分离，apply 要求固定 import_id 与精确确认字符串。
- `frontend/src/components/admin/configuration/ErpConfigSection.tsx`：以
  `erp.app_credentials` 与 `erp.token_pair` 两个原子组管理企业 ERP 凭证，写入携带
  状态版本 CAS，并以统一状态卡展示配置与重新配置入口。
- `frontend/src/components/admin/configuration/CredentialGroupSection.tsx`：统一 ERP 与
  企业微信机器人凭证组的已配置/未配置状态、折叠展示和重新配置入口。
- `frontend/src/components/admin/__tests__/OrgCredentialSections.test.tsx`：覆盖 ERP 与
  企业微信凭证组隐藏内部版本、展示配置状态及展开重新配置。
- `frontend/src/components/admin/__tests__/AiConfigSection.test.tsx`：覆盖企业 AI Key
  切换平台服务时的逐键 CAS 停用、部分失败后的权威重载、防重复请求及组织生命周期隔离。
- `frontend/src/components/admin/useAiConfigLoader.ts`：隔离企业 AI 配置权威状态加载、
  active Key 判定和 orgId/卸载后的异步结果失效。
- `frontend/src/components/admin/OrgInfoSection.tsx`：从企业管理主面板拆出的企业信息
  与登录链接展示，使主面板保持在结构阈值内。
- `deploy/env-templates/legacy-config-import.env.template`：161 一次性迁移的旧库只读 DSN、
  migrator DSN、旧密钥兜底和新 KEK 示例，不包含真实材料。
- `backend/migrations/161_configuration_legacy_import.sql` 同时提供
  `export_legacy_configuration_snapshot`：仅允许一次性 Reader 在显式 read GUC 下，
  通过 owner-held SECURITY DEFINER 固定导出三张旧表的精确字段；Reader 无表权限。
- `backend/tests/test_configuration_legacy_import_source.py`、
  `backend/tests/test_configuration_legacy_import_source_executor.py`、
  `backend/tests/test_configuration_legacy_import_source_external.py`、
  `backend/tests/test_configuration_legacy_import_executor.py`、
  `backend/tests/test_migrate_legacy_configuration_script.py` 与
  `backend/tests/test_configuration_legacy_import_external.py`：覆盖单快照、角色/事务门禁、
  CLI 默认只读、Reader 单事务 export 和显式隔离 PostgreSQL 行为。
- `deploy/preflight-legacy-config-import.sh`：通过安全管理员 psql 启动器，在只读事务中
  检查 158–162 台账、角色属性、导入函数 owner/grant、definer 三张旧源表读取能力、
  Reader 无直表权限、配置表 FORCE RLS、Registry 固定计数和导入目标全空。
- `backend/tests/test_configuration_legacy_import_preflight_script.py`：覆盖管理员 URL 门禁、
  只读/回滚合同、无写 SQL、迁移/角色/授权/RLS/空目标和旧来源表检查。
- `backend/testing/org_config_test_support.py`：提供同步/异步企业配置测试共用的隔离
  QueryBuilder 与 FakeDB，测试文件不再互相导入。
- `backend/tests/test_org_config.py` 与 `backend/tests/test_org_config_async.py`：分别覆盖
  同步和异步企业配置解析，均保持在 500 行以内。
- `backend/testing/knowledge_test_support.py`：集中提供知识服务测试的隔离数据库与配置
  fixture；`test_knowledge_service.py`、`test_knowledge_features.py` 和
  `test_knowledge_policies.py` 分别覆盖核心、提取/图谱及 Schema/淘汰职责。
- `backend/tests/test_erp_agent.py`、`test_erp_agent_analysis.py`、
  `test_erp_agent_plans.py`、`test_erp_agent_contracts.py` 与
  `test_erp_agent_reliability.py`：按入口、分析、计划、契约和可靠性拆分 ERP Agent
  单元测试，所有文件保持在 500 行以内。
- `docs/document/RUNBOOK_161_旧配置迁移.md`：固定 158–162 应用、数据库只读 preflight、
  同 import_id dry-run/apply、双人确认、导入后核验和不删除持久审计的回退边界。
- `backend/tests/test_legacy_config_import_runbook.py`：防止运行手册阶段顺序、确认协议、
  角色隔离、旧真相源保留和一次性环境模板发生漂移。
- `docs/document/TECH_管理员企业配置与Skill共享治理.md`：定义平台、企业、个人配置
  与 Skill 共享边界，以及迁移 156–163 的实施顺序。
- `docs/document/TECH_统一配置与Secret控制平面.md`：取代原 158 以后配置存储设计，
  统一定义 platform/org/user 继承、信封加密、命名 Bundle、Worker 两阶段 Scope、
  ERP Token CAS、Kuaimai Cookie 迁移和 Skill SecretRef。
- `docs/document/TECH_统一配置与Secret控制平面_迁移附录.md`：保存 158–165 实施顺序、
  生产只读审计结论与 161 分阶段验证证据，主设计文档保持在 500 行以内。
- `backend/migrations/150_agent_runtime_tenant_defense.sql`：为 Runtime 基础设施首组 13 表
  创建失败关闭的租户身份辅助函数和 `USING + WITH CHECK` policy；仅 ENABLE，不 FORCE。
- `backend/migrations/rollback/150_agent_runtime_tenant_defense_rollback.sql`：移除首组 policy
  与辅助函数，并仅对迁移前未启用 RLS 的 6 张表恢复 DISABLE 状态。
- `backend/tests/test_agent_runtime_rls_migration.py`：静态约束 policy 表集合、个人与 Channel
  语义、连接角色/Scope 匹配、停用员工失权、函数公开权限及回滚对称性。
- `backend/migrations/151_agent_runtime_role_grants.sql`：按 runtime/worker 真实操作拆分首组
  表权限，授予租户辅助函数和公开资产登记入口，并仅让资产 policy 接受 owner 执行链。
- `backend/migrations/rollback/151_agent_runtime_role_grants_rollback.sql`：撤销首组表/function
  权限，并恢复资产 policy 的普通角色集合。
- `backend/tests/test_agent_runtime_role_grants_migration.py`：覆盖无 DELETE、无资产表直权、
  内部资产函数不公开、辅助函数完整授权和精确回滚表集合。
- `backend/tests/test_backfill_generation_turns.py`：覆盖四级证据、冲突拒绝、条件更新、批次失败和 dry-run/apply 语义。

本轮企微上下文治理新增的核心服务：
- `backend/services/agent/file_analysis_service.py`：隔离表格分析的路径授权、格式转换、结构化错误与缓存登记。
- `backend/services/handlers/chat_tool_result_mixin.py`：统一 Chat 工具结果分类、WebSocket 投递与审计。
- `backend/services/assets/file_identity.py`：按解密后内容统一识别文件类型、规范名称与内容摘要。
- `backend/services/assets/asset_identity.py`：按真实 Workspace/OSS 对象解析 canonical
  存储身份，统一个人、企业和企微群聊 owner key，并执行精确 HTTPS 主机与路径安全校验。
- `backend/services/assets/asset_registry.py`：构造 canonical 资产与来源 Draft，并通过
  `register_user_asset` RPC 原子登记；在线失败保持原业务结果并输出结构化日志。

统一用户资产索引的已确认技术设计：
- `docs/document/TECH_统一用户资产索引与管理员图片空间重构.md`：统一资产登记、
  管理端游标分页、历史回填、短维护窗口直接切换与回滚边界；聊天展示保持独立。
- `frontend/src/services/__tests__/adminUserAssets.test.ts`：统一资产游标请求、取消信号与
  仅资产 ID ZIP 协议测试。
- `frontend/src/components/admin/userDetail/__tests__/AssetSpaceTab.test.tsx`：管理员资产
  空间来源切换、资产 ID 选择和前后游标分页测试。

本轮图形渲染治理新增的核心模块：
- `backend/config/image_agent_prompt.py`：从主工具配置中拆出的电商图片提示词片段，保持 `chat_tools.py` 满足文件长度阈值。
- `frontend/src/components/chat/message/useEChartsRender.ts`：封装 ECharts Runtime 动态加载、初始化、重试、卸载清理和 ResizeObserver 生命周期。
- `frontend/src/components/chat/message/echartsRuntime.ts`：集中具名注册项目支持的 ECharts 图表、组件、Canvas 渲染器和主题，作为图表触发后的独立加载边界。

本轮 Agent Runtime 全项目对标新增的架构研究文档：
- `docs/document/TECH_AGENT_RUNTIME_AR-00技术基线与迁移边界.md`：冻结现有与目标能力
  边界、唯一 `backend/services/agent/runtime/` 目录、企业/企业员工/散客三级隔离、
  全局/企业/个人/Session 配置继承、Conversation/Task/Message 新旧映射、后续 Runtime
  RPC 合同，以及 AR-01～AR-04 各自的入口、Owner、文件、权限和迁移门禁。
- `backend/migrations/209_worker_active_organization_capability.sql`：仅允许 actorless
  Worker Scope 枚举 active 企业 ID；后台模型评分和一致性检查不再直读组织表。
- `backend/migrations/210_worker_orphan_task_recovery_capability.sql`：以
  claim/complete/fail、lease 和 fencing 原子恢复非 Actor 孤儿任务，消息回写、任务终态
  与退款均由 Owner 能力提交，Redis 只保留启动调度锁。
- `backend/migrations/211_worker_global_knowledge_seed_capability.sql`：校验受限 Seed
  Snapshot，通过 Owner RPC 原子替换全局节点、1024 维 Embedding 和关系边，不开放
  Worker 对知识表的直接权限。
- `backend/services/web_database_runtime.py`、`backend/core/tenant_registry.py`：移除迁移
  112 已删除的 `pending_interaction` 启动清理和 Registry 残留；未来持久 Interaction
  仍由正式 Agent Runtime 任务实现。
- `docs/document/TECH_Grok式通用记忆运行时重构.md`：将现有业务硬编码的 L1/L2/L3 记忆管道收口为 Grok 式通用 Session Flush、Session Memory、Consolidation、Curated Memory 与 Search/Get 生命周期；领域差异仅允许通过受限 Skill Profile 提供。
- `backend/services/memory/contracts.py`、`candidate_validator.py`：Grok 式通用记忆候选协议与 fail-closed 原文证据门禁；首期只建立契约和误提取基线，尚未切换生产写入。
- `backend/tests/test_l1_generic_memory.py`：通用 `NO_MEMORY/CANDIDATES` 解析、精确用户证据、整批拒绝、去重失败关闭及真实消息 ID 传递回归测试。
- `backend/migrations/140_generic_memory_session_runtime.sql`：通用 Session Memory 日志、固定 revision 幂等约束，以及 `memory_atoms` 生命周期、溯源、时效和召回兼容字段；rollback 停止写入但保留已提交事实。
- `backend/services/memory/session_flush.py`、`backend/migrations/141_memory_session_flush_cas.sql`：从闭合 revision 增量读取最多 20 条消息，区分 `NO_MEMORY` 与非法模型输出，并通过数据库行锁与 cursor CAS 原子提交 Session Log；应用回滚保留 cursor 和历史日志。
- `backend/services/memory/embedding.py`：记忆提取、Session semantic dedup 与旧检索共用的批量 embedding 边界；批次缺项或 Provider 异常时 fail-closed。
- `backend/tests/test_memory_session_flush.py`、`test_memory_shadow.py`、`test_memory_embedding.py`：Session single-flight/CAS、完整 revision 窗口、exact/semantic 去重、Embedding 失败关闭和 PromptBuilder shadow 非注入回归。
- `backend/migrations/142_memory_consolidation_runtime.sql`：Grok Dream 式 Consolidation Run、至少 3 份/至多 25 份 Session 来源约束、幂等 source hash、终态 Receipt，以及 Session Log 的一致消费标记；rollback 仅停止写入并保留来源链。
- `backend/services/memory/consolidator.py`、`backend/migrations/143_memory_consolidation_commit.sql`：至少 3 份新 Session Log、完成间隔不少于 4 小时的通用巩固；模型仅判定 novel/duplicate/supersedes/conflicts 关系，候选原文重新过 Evidence Validator，embedding、Curated Atom、Run 与 Session 消费由单 RPC 原子提交。
- `backend/tests/test_memory_consolidator.py`、`test_memory_consolidation_migration.py`：覆盖最小来源数、显式性、证据复验、embedding 失败关闭、同用户 singleflight、受限关系输出及原子提交/保留数据回滚协议。
- `backend/services/memory/recall_policy.py`、`retrieval_pipeline.py`：通用 Curated Memory Search/Get；只读取 active、未删除且处于有效期内的事实，以向量/BM25 融合、硬阈值、时间衰减和 MMR 控制相关性与重复，并在 Get 时重新执行组织、用户和生命周期校验。Runtime 只暴露通用 `kind`，不读取或注入历史 `type/scene_name`。
- `backend/tests/test_memory_recall_policy.py`：覆盖低分拒绝、时间衰减、MMR、多通道归一、生命周期 SQL、跨租户 Get 边界、溯源返回与数据库失败关闭。
- `backend/config/memory_tools.py`、`backend/services/agent/memory_tool_mixin.py`：个人上下文获准时向主 Agent 暴露只读 `memory_search/memory_get`；采用 `memory:<atom_id>` 稳定 ref，Search 最多 6 条，Get 再次执行用户、组织和生命周期校验。
- `backend/services/prompt_builder/builder.py`：首轮自动注入最多 3 条 Runtime 已筛选 Curated Memory；Context Compaction 后先删除旧会话缓存再重新检索，刷新失败时不复用压缩前记忆。
- `backend/tests/test_memory_tools.py`、`test_memory_prompt_refresh.py`：覆盖工具 schema、个人上下文隔离、稳定 ref、来源返回、无 scope 失败关闭，以及压缩后缓存失效/重新检索/失败不回退。
- `backend/services/memory/pipeline_scheduler.py`：生产调度只接受闭合 revision 并执行 Session Flush → Consolidation；缺失 revision 时不读取或更新 pipeline state，历史 Scene/L2/L3 状态字段不再由 Runtime 维护。
- `backend/services/memory/l1_extractor.py`：仅生成和验证无数据库副作用的通用候选；旧 L1 直写、Dedup 服务及其 Prompt 已删除。
- `backend/services/memory/memory_service_v2.py`：仅提供通用召回、闭合 revision 调度、压缩及历史管理读取；零调用的旧 Atom 手动 CRUD 已删除，公共手动记忆仍由 Phase 4.3 迁移。
- `backend/services/memory/config.py`：仅保留通用提取、Consolidation、调度、召回、压缩与 Embedding 配置；停用的旧 Dedup、L2 Scene/L3 Persona 配置已物理删除。
- `backend/tests/test_memory_legacy_exit.py`：固定 Scheduler 不再暴露 L2/L3 运行入口、无 revision 不触碰数据库、旧 Redis Session cache 中的 Persona 不能再次进入 Prompt。
- `backend/migrations/144_manual_curated_memory.sql`：为通用 Curated Memory 增加个人 `org_id=NULL` scope、来源标记和仅限 `service_role` 的手动创建、更新、软删除、清空 RPC；rollback 停止写入但保留个人 scope 与来源事实。
- `backend/migrations/220_runtime_manual_memory_capabilities.sql`：角色隔离后提供仅授权
  `everydayai_runtime` 的 owner-definer 手动记忆写能力；每次写入验证可信 Actor、
  组织 Scope、active 用户与成员关系，不向 Runtime 开放 `memory_atoms` 表权限。
- `backend/tests/test_manual_curated_memory_migration.py`：固定个人/组织 scope、并发锁、容量与内容边界、去重、手动来源限制、软删除、RPC 权限和保留数据回滚协议。
- `backend/services/memory/manual_memory_service.py`：手动 Curated Memory 的唯一服务边界；原文经过通用 embedding 后调用原子 RPC，统一处理个人/组织 scope、容量、重复、跨 scope 隐藏与失败关闭。
- `backend/services/memory/retrieval_pipeline.py`、`memory_service_v2.py`、`backend/services/agent/memory_tool_mixin.py`：Search/Get、Prompt 自动注入和 Agent 只读工具使用同一 NULL-safe scope，个人用户无需虚拟组织 ID 即可召回。
- `backend/tests/test_manual_memory_service.py`：覆盖手动原文写入、embedding 失败关闭、上限、个人列表、跨 scope 隐藏、软删除/清空 RPC 映射和数据库异常。
- `backend/api/routes/memory.py`、`backend/services/wecom/command_handler.py`、`card_event_handler.py`：Web CRUD/设置与企微查看/清空入口统一依赖 `ManualMemoryService`，保持原 API 和卡片协议不变。
- `backend/services/memory_settings.py`：记忆开关只由用户设置决定，不再因旧 Mem0 Provider 不可用而关闭通用 Curated Memory Runtime。
- 旧 `backend/services/memory_service.py`、`memory_config.py`、`memory_filter.py`、启动预热与 `mem0ai` 依赖已删除；生产运行时不再初始化或调用 Mem0。
- `docs/document/TECH_长期单会话上下文与Token治理.md`、`TECH_长期单会话上下文与Token治理_实施附录.md`：面向长期单会话体验收口 Grok Build 对标、跨 Turn Evidence、活动工作集、模型能力预算、结构化摘要、Search/Get、Prompt Cache 与 ContextReceipt。
- `docs/document/TECH_统一会话上下文Artifact与Compaction引擎.md`：方案 B 的实施合同，统一 ConversationItem、通用工具 Artifact、跨 Turn 召回、模型能力预算、结构化 Compaction、原子提交、历史回填和直接切换流程。
- `backend/services/agent/runtime/artifacts/`：通用工具结果规范化、Run 内完整事实存储、40KB 模型视图、稳定引用和 UTF-8 游标分页读取。
- `backend/services/agent/runtime/validation/`：统一 Tool 终态协议、结构化结果归一、失败指纹追踪、有界恢复纯决策和 Validation Receipt；主Chat已接入fail-open观察模式，尚未接管循环控制。
- `backend/services/agent/artifact_tool_mixin.py`、`backend/config/artifact_tools.py`：所有聊天入口共用的只读 `artifact_search/get/read` 执行与工具协议。
- `backend/services/agent/runtime/artifacts/persistence.py`：Actor 提交前将小 Artifact 内联、大 Artifact 上传租户隔离 OSS，并生成迁移 138 的提交参数。
- `backend/migrations/139_actor_artifact_terminal_integrity.sql`：在数据库边界归一化 Artifact 互斥存储字段，并保证 Actor 重试耗尽后 assistant 消息不残留 streaming。
- `backend/services/agent/runtime/artifacts/repository.py`：按会话、组织和固定 revision 跨轮检索、获取、分页读取 inline/OSS/message_slice Artifact。
- `backend/services/agent/runtime/context/assembler.py`：模型能力预算驱动的 HistoryAssemblyPlan、结构化双模型压缩、确定性降级、工具组/最近 Turn 保护与硬上限门禁。
- `backend/services/agent/runtime/context/receipt.py`：生成不含正文的 ContextReceipt、ContextEpoch 和 CacheIdentity，分别归因稳定前缀、动态后缀与 Tool Schema。
- `backend/services/agent/runtime/context/provider_plan.py`：冻结单个 ModelStep 的完整 ProviderContextPlan，并作为 Provider messages/tools 的唯一投影来源；构建或非无损投影失败时发送前终止。
- `backend/services/agent/runtime/context/pruning.py`：统一 Provider 前确定性 ToolResult Pruning；在可用输入达到 50% 后只处理最近 3 个用户 Turn 之前的完整工具组，孤立/缺失工具结果不裁剪，并生成无正文回执。
- `backend/services/agent/runtime/context/compaction.py`：当前 Run 唯一 85% LLM Compaction 合同；统一主/备摘要、prefix single-flight、失败 suppression、过期结果拒绝和无正文 CompactionReceipt，旧 `handlers/context_compressor/summary.py` 已删除。
- `backend/tests/test_context_compressor.py`、`test_runtime_context_compaction.py`、`test_context_compressor_budgets.py`：分别覆盖基础 Token/归档、Runtime LLM Compaction、旧预算兜底；原 708 行单体测试已按职责拆分且各文件低于 500 行。
- `backend/migrations/147_context_receipt_cache_identity.sql`：持久 Context Epoch、CacheIdentity 和单个 ModelStep Provider 用量，通过 v2 包装 RPC 与原生成提交保持同一事务。
- `backend/services/handlers/chat_context/unified_history_loader.py`：将持久 ConversationItem/Compaction 重建为唯一模型历史；既有 revision 缺少投影时失败关闭。
- `backend/services/agent/runtime/context/items.py`、`provider_receipt.py`：构建本 Turn 的原子 ConversationItem 组，并在每次真实 Provider 请求前登记无正文 ContextReceipt。

Agent Runtime AR-05～AR-09 基础实现：

- `backend/services/agent/runtime/domain/`：Session、Run、ModelStep、Action、Event、
  Scope、lease、fencing 和幂等的框架无关领域单一类型来源。
- `backend/services/agent/runtime/ports/`：Repository、Model、Executor、Event 和
  Projection 的基础设施反转边界；本阶段不包含具体 Provider、Tool 或数据库适配器。
- `backend/migrations/212_agent_runtime_core_foundation.sql`～`215_agent_runtime_model_event_projection_rpcs.sql`：
  建立七张 FORCE RLS 核心表及 Session/Command/Run/ModelStep/Projection 窄 RPC；
  AR-08 再追加只读能力迁移 216；应用顺序固定为 212→216，rollback 固定逆序执行。
- `backend/tests/agent_runtime/` 与 `backend/tests/fixtures/agent_runtime/`：可复用 Trace
  schema、确定性 Replay、Projection 重放和 single-owner/fencing/Scope/幂等断言。
- `backend/services/agent/runtime/infrastructure/postgres/`：将 Session、Command、Run、
  ModelStep、严格 Event Replay 和 Projection Outbox ports 映射到 212～216 的窄 RPC；
  RPC 响应按闭合 outcome、UUID、枚举、时间戳和 JSON 合同失败关闭。
- `backend/migrations/216_agent_runtime_read_projection_capabilities.sql`：增加受 Scope
  约束的 Session/Event 读取、Run claim readback 和 Projection Event envelope；
  Runtime/WeCom 不能读取 system Scope，Worker 不获得核心表直权。
- `backend/services/agent/runtime/infrastructure/model/`：将冻结的 ModelStepRequest、
  ProviderContextPlan、模型 revision 和脱敏 receipt 投影到现有 Provider adapter；
  当前只有明确拒绝的 429 可在同一 ModelStep 内重试，502/503/504 和 timeout 进入
  unknown，adapter cleanup 有界且不能覆盖主要结果或取消。
- 当前实现为 additive foundation，尚未接管 Web、企微或 Conversation Actor 的生产
  Owner；AR-08/09 只提供基础设施 adapter，生产调用方切换属于后续任务。
- `backend/services/handlers/chat/execution_result.py`：Chat 纯执行结果协议，携带 Artifact drafts 与 ContextReceipt，不产生数据库副作用。
- `docs/document/TECH_AGENT_RUNTIME全项目对标总纲.md`：固定 Grok Build 全项目对标范围、逐板块研究模板、证据要求、文档索引和阶段门禁。
- `docs/document/TECH_SESSION_RUNTIME多租户通用Agent架构.md`：在现有 Conversation Actor 和既有
  `agent_*` Runtime 设计上冻结方案 A；补充全局管理员、企业、个人、Session 四层配置与策略继承，
  企业 Skill 共享、系统推荐/自动/强制安装，以及 MCP/Goal 第一阶段边界，禁止另建平行 Runtime 表。
- `docs/document/TECH_AGENT_RUNTIME统一Session运行时与上下文加载合同.md`：以 Grok Build 最新源码为基线，定义统一 Session 状态推进、Context Epoch、ModelStep、首轮/多轮/工具循环/Compaction/冷恢复加载合同，以及 A+ 分波次迁移、完整切换和回滚边界。
- `docs/document/TECH_通用任务交付运行时与跨Turn数据证据.md`：统一单 Run 交付治理与跨 Turn 业务数据证据；Runtime 保留原模型/工具消费方式，只在工具和结构化产物边界执行确定性校验。
- `docs/document/TECH_统一Validation与Recovery运行时.md`及实施附录：参考 Grok Build 的 typed result、结构化错误回填、有界恢复和 Completion Requirement，规划全项目唯一的通用校验与恢复内核；不包含 Skill 或业务专属校验。
- `docs/document/research/AGENT_01_项目全景与组件装配.md`：对照 Grok Build 与 EVERYDAYAIONE 的启动入口、运行模式、进程/线程边界、装配参数和关闭恢复语义。
- `docs/document/research/AGENT_02_Session_Actor与持久执行.md`：对照 Session 命令、Prompt 队列、send-now、Claim/Lease/Fencing、取消竞态、等待交互和恢复语义。
- `docs/document/research/AGENT_03_Agent定义与能力装配.md`：对照 AgentDefinition、Session-bound Agent、PromptBuilder、权限/工具/预算装配和专业 Agent 分流。
- `docs/document/research/AGENT_04_Model_Loop模型循环.md`：对照模型采样、流式事件、工具并发、停止、重试、取消、预算和完成验证。
- `docs/document/research/AGENT_05_Policy权限成本与副作用治理.md`：对照 Hooks、用户授权、租户权限、积分事务、沙盒和副作用工具策略。
- `docs/document/research/AGENT_06_ToolBridge工具桥接与结构化输出.md`：对照工具注册、会话能力冻结、参数规范化、动态发现、执行桥接、流式终态和结构化结果分层。
- `docs/document/research/AGENT_07_Tool_Executors专业执行链.md`：对照即时查询、Web、文件、沙盒、ERP、图片/视频异步任务的参数、超时、并发、幂等、取消、计费和产物语义。
- `docs/document/research/AGENT_08_Goal_Orchestrator持续目标编排.md`：对照 Goal 状态机、验收契约、Planner、Completion Verifier、停滞策略、续跑、预算和跨 Worker 恢复。
- `docs/document/research/AGENT_09_Context_Engineering上下文工程.md`：对照上下文信息分层、模型预算、压缩抑制、摘要、长期记忆、按需检索、引用恢复和组装回执。
- `docs/document/research/AGENT_10_Skills_Runtime技能运行时.md`：对照 Skill 发现、选择、渐进加载、指令与工作流双模式、工具权限求交、资源隔离、版本固定和步骤恢复。
- `docs/document/research/AGENT_11_MCP_Plugins_Hooks扩展运行时.md`：对照 MCP 外部能力协议、Plugin 安装信任单元、Hook 生命周期拦截、Gateway 隔离、认证、目录热更新和故障恢复。
- `docs/document/research/AGENT_12_Subagents_Background子代理与后台任务.md`：对照子 Agent 独立上下文、能力继承、委派合同、并发、后台化、取消、结果回传、Workspace 隔离和持久恢复。
- `docs/document/research/AGENT_13_Persistence持久化与恢复.md`：对照状态表、追加事件、事务 Outbox、Artifact、Checkpoint、lease/fencing、幂等、重放、分支和 Schema 迁移。
- `docs/document/research/AGENT_14_Protocol_UI协议与交互投影.md`：对照运行时事件信封、高频流合并、顺序去重、Snapshot/Replay、持久交互、UI Projection、Artifact 展示和多通道降级。
- `docs/document/research/AGENT_14_Protocol_UI参数与迁移附录.md`：记录 UI Projection、通道能力、协议参数、边界场景、差距矩阵、迁移顺序和验证清单。
- `docs/document/research/AGENT_15_Observability_Config可观测性与配置运行时.md`：对照结构化日志、Trace、Metrics、Usage/成本、脱敏、告警、反馈、配置优先级、热更新、last-known-good 和运行快照。
- `docs/document/research/AGENT_15_Config_Feedback配置与反馈附录.md`：记录 Config Catalog、配置快照、生效模式、反馈闭环、风险、差距矩阵和实施顺序。
- `docs/document/research/AGENT_16_Testing_Operations测试与生产运维.md`：对照状态机测试、确定性 Trace 回放、真实依赖契约、故障注入、CI 门禁、Release Manifest、迁移、Actor 排空、灰度和回滚。
- `docs/document/research/AGENT_17_端到端链路与运行时收口.md`：串联 Grok 与本项目的 Session、Model、Tool、Skill、Goal、Subagent、MCP、媒体、文件、ERP、企微、持久化、恢复和展示完整路径。
- `docs/document/research/AGENT_17_全项目差距矩阵与优先级.md`：汇总 17 层架构与业务能力差距，冻结 P0/P1/P2 优先级、必须保留的项目优势、禁止方案和九个迁移波次。
- `docs/document/TECH_AGENT_RUNTIME目标架构与模块边界.md`：定义模块化单体 Runtime、PostgreSQL Actor、专业 Executor、多通道 Projection 的目标模块图、职责、依赖规则、进程边界和现有能力迁移归属。
- `docs/document/TECH_AGENT_RUNTIME核心状态机.md`：定义 Run、ModelStep、Action、ActionAttempt 的状态、合法转移、终态所有权、取消、重试、Unknown 对账和现有 task 映射。
- `docs/document/TECH_AGENT_RUNTIME_Action恢复与不变量附录.md`：记录 ActionAttempt、重试分类、幂等、single terminal owner、恢复参数、旧状态映射、边界和数据库不变量。
- `docs/document/TECH_AGENT_RUNTIME交互与Goal状态机附录.md`：定义 Interaction、AuthorizationGrant、Goal、Continuation Controller 与 SubRun 父子关系及恢复规则。
- `docs/document/TECH_AGENT_RUNTIME数据库模型.md`：定义 `agent_*` 状态表、完整字段/约束/索引、租户范围、Artifact、Usage、旧表映射与保留策略。
- `docs/document/TECH_AGENT_RUNTIME数据库RPC与原子边界.md`：定义锁顺序、Run/ModelStep/Action/Interaction/Goal 原子 RPC、Callback Inbox、事件追加、兼容迁移和回滚边界。
- `docs/document/TECH_AGENT_RUNTIME事件存储与保留附录.md`：定义 RuntimeEvent 信封、Projection Outbox、Snapshot/Replay、流事件合并、索引、分区触发条件、隐私与保留参数。
- `docs/document/TECH_AGENT_RUNTIME_Policy授权成本与副作用.md`：定义统一 Policy Gate、用户意图授权、工具风险元数据、组织权限、数据范围、成本预留、批量 Action、持久 Interaction 与扩展能力不扩权原则。
- `docs/document/TECH_AGENT_RUNTIME_Context分层额度与召回.md`：定义事实层、ContextPlan/Block/Receipt、模型能力派生预算、渐进式 Search/Get、结构化摘要、压缩抑制及 Skill/MCP/Subagent 隔离上下文。
- `docs/document/TECH_AGENT_RUNTIME_Context回执边界与迁移附录.md`：记录 ContextReceipt、边界场景、方案比较、架构影响、计划接口、迁移顺序和验收门禁。
- `docs/document/TECH_AGENT_RUNTIME_Executor_SPI与专业执行链.md`：定义统一 Executor SPI、Descriptor、四种提交结果、TaskRef、Worker/Reconciler、四视图 ActionResult，以及媒体、ERP、文件、Sandbox、MCP 和子 Agent 专业执行边界。
- `docs/document/TECH_AGENT_RUNTIME_Executor并发边界与迁移附录.md`：记录 Executor 并发池、资源冲突、失败场景、方案比较、架构影响、计划文件、迁移顺序和验收门禁。
- `docs/document/TECH_AGENT_RUNTIME_扩展运行时Skill_MCP_Plugin_Hook.md`：定义 Extension Registry、Skill 双模式、MCP Gateway、Plugin 信任与版本、Runtime Hook 分类及多租户安全边界。
- `docs/document/TECH_AGENT_RUNTIME_扩展运行时迁移附录.md`：记录扩展层架构影响、计划目录、渐进迁移顺序和安全验收门禁。
- `docs/document/TECH_AGENT_RUNTIME_Subagent与后台任务.md`：定义受限 Child Run、委派合同、隔离 Context/Capability、预算、Workspace isolation、父级唤醒及 Background Action 分界。
- `docs/document/TECH_AGENT_RUNTIME_多通道Projection与交互协议.md`：定义 RuntimeEvent 有序信封、Snapshot/Replay、Projection reducer、持久 Interaction、ChannelCapability 及 Web/企微确定性降级。
- `docs/document/TECH_AGENT_RUNTIME_测试灰度发布与回滚.md`：保留历史文件名，正文定义状态机/真实依赖/Trace/E2E/Eval 测试体系、ReleaseManifest、Actor drain、无副作用 shadow 对账、完整切换门禁与回滚；不采用租户、用户或流量 Canary。
```
EVERYDAYAIONE/
├── .cursorrules              # AI开发执行核心规则
├── .claude/skills/everydayai-test-coverage/SKILL.md # Claude 按需加载测试规则的轻量入口
├── scripts/run_tests.sh      # 后端 target/fast/pr/full/large/external 分层测试入口
├── scripts/run_redis_contract_tests.sh # 临时 localhost Redis Standalone 外部合同测试入口
├── CLAUDE.md                 # Claude Code 开发规则
├── .env                      # 环境变量（本地）
├── docs/                     # 项目文档
│   ├── PROJECT_OVERVIEW.md       # 项目概览（本文档）
│   ├── FLOW_DIAGRAMS.md          # 程序流转图（架构/组件/流程）
│   ├── FUNCTION_INDEX.md         # 函数索引
│   ├── CURRENT_ISSUES.md         # 当前问题
│   ├── document/TECH_Conversation_Actor持久执行架构.md # Chat 持久队列、fencing、原子完成与恢复设计
│   ├── document/TECH_Conversation_Actor实施与验收附录.md # Actor 观测、发布、回滚与测试矩阵
│   ├── API_REFERENCE.md          # API 接口文档
│   ├── database/
│   │   ├── DATABASE_GUIDE.md     # 数据库使用指南
│   │   ├── MIGRATION_GUIDE.md    # 迁移指南
│   │   ├── supabase_init.sql     # Supabase 建表脚本（PostgreSQL）
│   │   └── migrations/           # 数据库迁移脚本
│   │       ├── 001_add_image_url_to_messages.sql
│   │       ├── 002_add_video_url_to_messages.sql
│   │       ├── 003_change_model_id_to_varchar.sql
│   │       ├── 004_add_is_error_to_messages.sql
│   │       ├── 005_add_video_cost_enum.sql
│   │       ├── 006_add_tasks_table.sql
│   │       ├── 007_add_credit_transactions.sql
│   │       └── 015_add_chat_task_fields.sql  # chat 任务字段
│   └── document/
│       ├── TECH_ARCHITECTURE.md      # 技术架构
│       ├── PAGE_DESIGN.md            # 页面设计
│       ├── UI_主图详情制作页面.md      # 独立主图/详情图五步制作页面 UI 设计
│       ├── TECH_主图详情制作页面_UI第一阶段.md # 第一阶段 UI+Mock 技术设计
│       ├── TECH_主图详情页真实上传与草稿恢复.md # 第二阶段真实上传与草稿恢复设计
│       ├── TECH_工作区图片插入与聊天附件标准化.md # 工作区图片正确渲染与聊天附件提交标准化
│       ├── TECH_AI帮写通用创作简报.md # 电商图三套通用简报与共享入口适配架构
│       ├── OSS_CDN_DESIGN.md         # OSS/CDN 设计
│       ├── KIE_INTEGRATION_DESIGN.md # KIE API 集成设计
│       ├── SUPER_ADMIN_FEATURES.md   # 超级管理员功能
│       └── 聊天任务恢复方案.md       # 聊天任务刷新恢复方案
│
├── backend/                  # 后端代码（Python/FastAPI）
│   ├── venv/                      # Python 虚拟环境（git 忽略）
│   ├── requirements.txt          # Python依赖（精确版本锁定）
│   ├── .env                      # 后端环境变量（git 忽略）
│   ├── .env.example              # 环境变量模板
│   ├── main.py                   # FastAPI 应用入口
│   ├── conversation_worker_main.py # Conversation Actor 独立 Worker 入口
│   ├── migrations/146_admin_user_assets_query.sql # 管理资产 ref 过滤、代表来源与稳定游标 RPC
│   ├── migrations/209_platform_admin_user_assets_capability.sql # Runtime 数据库验权的超管资产列表与 ZIP 解析门面
│   ├── scripts/backfill_user_assets.py # 五类历史资产 dry-run/apply、checkpoint 与对账
│   ├── scripts/backfill_user_assets_sql.py # 历史资产回填固定 keyset/RPC SQL
│   ├── core/                     # 核心模块
│   │   ├── config.py                 # 配置管理（pydantic-settings）
│   │   ├── database.py               # Supabase 客户端
│   │   ├── security.py               # JWT/密码处理
│   │   ├── exceptions.py             # 自定义异常
│   │   ├── redis.py                  # Redis 客户端
│   │   ├── message_idempotency_cleanup.py # 消息幂等记录 24 小时 TTL 清理循环
│   │   └── limiter.py                # 频率限制器
│   ├── api/                      # API 层
│   │   └── routes/ecom_requirement.py # 电商图 AI 帮写三方案薄路由
│   │   ├── deps.py                   # 依赖注入
│   │   └── routes/                   # 路由模块
│   │       ├── auth.py                   # 认证路由
│   │       ├── wecom_auth.py                # 企微 OAuth 路由（扫码URL、回调、绑定/解绑）
│   │       ├── health.py                 # 健康检查
│   │       ├── conversation.py           # 对话路由
│   │       ├── message.py                # 统一消息路由（/generate）
│   │       ├── message_request_preparation.py # 消息生成前权限、积分与上下文准备
│   │       ├── message_turn_anchors.py # retry/send 的 Turn 输入锚点解析与消息关系写入
│   │       ├── image.py                  # 图像上传路由
│   │       ├── admin_user_assets.py      # 超管统一用户资产复合游标查询
│   │       ├── detail_project.py         # 主图详情页草稿恢复与图片关联路由
│   │       ├── audio.py                  # 音频上传路由
│   │       ├── task.py                   # 任务管理路由
│   │       ├── webhook.py                # Webhook 回调路由（多 Provider 分发）
│   │       └── ws.py                     # WebSocket 路由
│   ├── schemas/                  # 请求/响应模型
│   │   ├── chart.py                  # ECharts正式协议与历史图表格式兼容
│   │   ├── content_part_contract.py  # ContentPart 跨语言协议 artifact 生成
│   │   ├── contracts/content_part.v1.json # 后端权威 Schema 与前端契约样例
│   │   ├── diagram.py                # Mermaid 逻辑关系图 ContentPart 协议
│   │   └── ecom_requirement.py       # 电商图 AI 帮写请求、标准输入与响应协议
│   │   ├── auth.py                   # 认证相关 Schema
│   │   ├── conversation.py           # 对话相关 Schema
│   │   ├── message.py                # 消息相关 Schema
│   │   ├── media_parts.py            # 文本/图片/视频/音频/文件 ContentPart
│   │   ├── structured_parts.py       # 表格/中断锚点等非媒体 ContentPart
│   │   ├── image.py                  # 图像上传 Schema
│   │   ├── detail_project.py         # 主图详情页请求与统一响应 Schema
│   │   └── websocket.py              # WebSocket 消息 Schema
│   ├── migrations/               # 数据库增量迁移
│   │   ├── 120_turn_revision_foundation.sql # Turn/revision 字段、索引与事务 RPC
│   │   ├── 121_conversation_actor_queue.sql # Actor 队列字段、索引与执行权 RPC
│   │   ├── 122_conversation_actor_terminal.sql # Actor 原子完成、失败与取消 RPC
│   │   ├── 123_conversation_actor_progress.sql # Actor fencing 临时进度 RPC
│   │   ├── 124_conversation_delivery_outbox.sql # Actor 企微终态事务 Outbox 与投递租约 RPC
│   │   ├── 125_wecom_actor_enqueue.sql # 企微消息与 Actor task 原子幂等入队
│   │   ├── 126_wecom_conversation_settings.sql # 企微模型/思考模式按租户原子持久化
│   │   ├── 127_actor_tenant_rpc_contract.sql # Actor 租户 RPC 门面及 org 强校验
│   │   ├── 128_wecom_channel_conversations.sql # 企微渠道会话稳定绑定与群共享 scope
│   │   ├── 129_conversation_attachments.sql # 会话附件状态机与企微 FILE 原子暂存
│   │   ├── 131_attachment_asset_lifecycle.sql # 资产身份、附件集合和 task 不可变引用
│   │   ├── 132_wecom_channel_task_enqueue.sql # 企微 user/channel Actor task 写入
│   │   ├── 133_wecom_attachment_single_consumption.sql # 企微当前附件绑定后转历史资源
│   │   ├── 134_web_user_wecom_delivery.sql # Web 用户输入按真实企微绑定写入事务 Outbox
│   │   ├── 136_conversation_evidence_model_view.sql # Evidence 分级模型视图、hash、大小与过期字段
│   │   ├── 137_context_summary_revision_rpc.sql # 连续闭合 Turn 摘要的 revision CAS 原子提交
│   │   ├── 150_agent_runtime_tenant_defense.sql # Runtime 基础设施首组 13 表租户 RLS policy
│   │   ├── 151_agent_runtime_role_grants.sql # Agent Runtime 首组最小角色授权
│   │   ├── 163_conversation_actor_worker_discovery.sql # Actor 无租户发现与任务级 Worker Facade
│   │   ├── 164_actor_task_execution_capabilities.sql # Actor 任务级执行权限与终态 Facade
│   │   ├── 165_memory_runtime_tenant_boundary.sql # Memory 四表 FORCE RLS 与最小角色能力
│   │   ├── 166_wecom_worker_discovery.sql # WeCom 无 Secret Worker 发现能力
│   │   └── rollback/              # 数据库迁移回滚脚本
│   │       ├── 120_turn_revision_foundation_rollback.sql
│   │       ├── 121_conversation_actor_queue_rollback.sql
│   │       ├── 122_conversation_actor_terminal_rollback.sql
│   │       ├── 123_conversation_actor_progress_rollback.sql
│   │       ├── 124_conversation_delivery_outbox_rollback.sql
│   │       ├── 125_wecom_actor_enqueue_rollback.sql
│   │       ├── 126_wecom_conversation_settings_rollback.sql
│   │       ├── 127_actor_tenant_rpc_contract_rollback.sql
│   │       ├── 128_wecom_channel_conversations_rollback.sql
│   │       ├── 129_conversation_attachments_rollback.sql
│   │       ├── 163_conversation_actor_worker_discovery_rollback.sql
│   │       ├── 164_actor_task_execution_capabilities_rollback.sql
│   │       ├── 165_memory_runtime_tenant_boundary_rollback.sql
│   │       ├── 166_wecom_worker_discovery_rollback.sql
│   │       ├── 131_attachment_asset_lifecycle_rollback.sql
│   │       ├── 132_wecom_channel_task_enqueue_rollback.sql
│   │       ├── 133_wecom_attachment_single_consumption_rollback.sql
│   │       ├── 150_agent_runtime_tenant_defense_rollback.sql
│   │       └── 151_agent_runtime_role_grants_rollback.sql
│   ├── scripts/
│   │   └── reconcile_wecom_attachments.py # 历史企微附件 dry-run/事务调和
│   ├── services/                 # 业务逻辑层
│   │   ├── auth_service.py           # 认证服务
│   │   ├── conversation_service.py   # 对话服务
│   │   ├── conversation_execution.py # Actor claim、租约、执行器与原子终态协调
│   │   ├── conversation_delivery.py  # Actor 数据库终态后的 WS 投递与槽位释放
│   │   ├── conversation_worker.py    # Actor 数据库扫描、并发调度与 Redis 唤醒
│   │   ├── conversation_runtime.py   # Actor 独立进程装配与 Kernel/Worker 生命周期
│   │   ├── conversation_task.py      # Actor 任务识别与原子取消入口
│   │   ├── assets/file_identity.py    # 内容优先的统一文件资产身份识别
│   │   ├── handlers/resource_manifest.py # task/input 冻结的当前资源权限清单
│   │   ├── wecom/actor_enqueue.py    # 企微稳定 ID 与 Actor 原子入队适配
│   │   ├── wecom/message_normalizer.py # 企微回调身份与媒体字段统一规范化
│   │   ├── wecom/channel_conversation.py # 企微外部 chatid 到内部 conversation 解析
│   │   ├── wecom/attachment_service.py # 企微 FILE 幂等暂存与附件引用
│   │   ├── wecom/conversation_settings.py # 企微对话设置数据库事实源
│   │   ├── wecom/delivery_sender.py  # 企微 Outbox 稳定分项与双通道发送适配
│   │   ├── wecom/delivery_worker.py  # 企微 Outbox 租约、检查点、重试与 dead 消费
│   │   ├── wecom/wecom_ingress_mixin.py # 企微 Actor 灰度与旧链路入站分发
│   │   ├── wecom/wecom_reply_mixin.py # 企微结果格式化与双通道回复职责
│   │   ├── handlers/chat/            # Chat 流式与无头执行内核
│   │   │   ├── execution_engine.py   # 通道无关模型流、工具循环、预算与结果构造
│   │   │   ├── execution_sink.py     # 通道过程事件协议与无副作用收集器
│   │   │   ├── actor_sink.py         # Actor fencing 进度持久化与 Web/无头 Sink
│   │   │   ├── actor_enqueue.py      # Web Chat 稳定幂等 enqueue 与 Redis 唤醒
│   │   │   └── executor.py           # Actor GenerationExecutor 实现与多模态输入恢复
│   │   ├── message_service.py        # 消息服务（CRUD）
│   │   ├── message_idempotency_service.py # 消息生成幂等抢占、指纹与响应重放
│   │   ├── message_utils.py          # 消息工具函数
│   │   ├── turn_binding.py           # task 插入绑定与 Turn 关闭的统一 RPC 出口
│   │   ├── message_ai_helpers.py     # AI 调用辅助函数
│   │   ├── audio_service.py          # 音频处理服务
│   │   ├── storage_service.py        # 文件存储服务
│   │   ├── detail_project_service.py # 主图详情页草稿恢复与工作区图片关联
│   │   ├── oss_service.py            # OSS 存储服务
│   │   ├── sms_service.py            # 短信服务
│   │   ├── credit_service.py         # 积分服务
│   │   ├── user_activity_service.py  # 用户活跃事件记录（失败不阻断主流程）
│   │   ├── task_limit_service.py     # 任务限制服务
│   │   ├── background_task_worker.py # 后台任务轮询器（兜底模式，120s 间隔）
│   │   ├── background_periodic_tasks.py # 模型评分与企微巡检跨进程周期执行
│   │   ├── periodic_job_gate.py # Worker 数据库周期租约领取、续期与提交
│   │   ├── task_completion_service.py # 统一任务完成处理服务（Webhook/轮询共用）
│   │   ├── batch_completion_service.py # 图片批次任务终态、积分与 partial update 协调
│   │   ├── batch_message_finalizer.py # 图片批次/单图重生的最终消息落库与通知
│   │   ├── websocket_manager.py      # WebSocket 连接管理
│   │   ├── websocket_auth.py         # Token 解析与握手拒绝业务关闭码
│   │   ├── websocket_interactions.py # Tool Confirm/Steer 用户与企业复合等待键
│   │   ├── websocket_task_scope.py   # WebSocket 任务订阅的用户/企业精确边界查询
│   │   ├── websocket_task_completion.py # 连接建立较晚时按租户 Scope 补发任务终态
│   │   ├── intent_router.py         # 智能意图路由器（千问 Function Calling）
│   │   ├── memory/                  # 通用提取、巩固、Curated Search/Get 与手动记忆
│   │   ├── memory_settings.py       # 通用记忆开关与保留设置
│   │   ├── agent/runtime/context/    # 全通道共享模型预算、Evidence/Receipt，以及 Run/跨 Worker 压缩协调
│   │   ├── agent/evidence_tool_mixin.py # 固定 conversation/revision 的 Evidence Search/Get
│   │   ├── wecom_oauth_service.py  # 企微 OAuth state、code exchange、二维码与 Redis 一次性交接
│   │   ├── wecom/oauth_identity_service.py # 迁移 155 scoped OAuth 身份能力客户端
│   │   ├── handlers/                 # 统一消息处理器
│   │   │   ├── __init__.py               # Handler 工厂
│   │   │   ├── base.py                   # Handler 基类
│   │   │   ├── retry_knowledge_mixin.py  # Handler 智能重试、WS 重试通知与知识钩子
│   │   │   ├── mixins/message_mixin.py   # 消息完成/失败生命周期与租户投递
│   │   │   ├── mixins/message_persistence_mixin.py # 助手消息持久化与幂等检查
│   │   │   ├── chat_handler.py           # 聊天处理器（流式）
│   │   │   ├── context_snapshot.py       # 固定 task revision 的不可变上下文快照
│   │   │   ├── conversation_cache.py     # base revision + through-message 精确匹配的统一历史 v6 缓存
│   │   │   ├── context_compressor/        # 当前 Run 工具归档、临时循环摘要与 Token 预算
│   │   │   ├── emit_payloads.py           # 显式 emit payload → content block/ContentPart 转换
│   │   │   ├── chat/                      # Chat 流式执行内核
│   │   │   │   ├── outcome_builder.py     # 内容块收尾与 ContentPart 协议构造
│   │   │   │   ├── stream_finalize.py     # 预算合成、结果收割与 stream_end
│   │   │   │   ├── stream_setup.py        # Context/Provider/权限/预算执行前准备
│   │   │   │   ├── stream_session.py      # 单轮 Provider 流读取与请求累积状态
│   │   │   │   └── tool_loop.py           # 工具轮次、emit/form 与上下文压缩
│   │   │   ├── image_handler.py          # 图片生成处理器
│   │   │   ├── image_request_settings.py # 图片提交与计费参数解析
│   │   │   └── video_handler.py          # 视频生成处理器
│   │   ├── wecom/                   # 企业微信服务
│   │   │   ├── wecom_message_service.py # 企微消息处理核心（继承 WecomAIMixin）
│   │   │   ├── wecom_file_mixin.py     # 企微原始文件稳定落 Workspace/OSS 并构造 FilePart
│   │   │   ├── turn_lifecycle.py      # 企微同步生成的 task/Turn 生命周期适配
│   │   │   ├── wecom_ai_mixin.py        # AI 路由 + 生成能力 Mixin
│   │   │   ├── app_message_sender.py    # 自建应用消息发送（文本/图片/视频）
│   │   │   ├── app_outbound.py          # 自建应用 HTTP typed 回执与进程内请求关联边界
│   │   │   ├── ws_client.py             # 智能机器人 WebSocket 客户端
│   │   │   ├── ws_outbound.py           # 智能机器人 legacy 发送与 typed ACK transport
│   │   │   ├── access_token_manager.py  # access_token 管理
│   │   │   └── user_mapping_service.py  # 企微用户 → 系统用户映射
│   │   ├── kuaimai/                  # 快麦 ERP 集成
│   │   │   ├── erp_unified_query.py     # 统一查询引擎（Filter DSL → SQL）
│   │   │   ├── erp_unified_schema.py    # 列白名单 + 常量 + 格式化
│   │   │   ├── erp_local_query.py       # 专用工具（库存/平台映射/店铺/仓库）
│   │   │   ├── erp_local_compare_stats.py # 同比/环比对比
│   │   │   ├── erp_local_identify.py    # 商品编码识别
│   │   │   ├── erp_local_helpers.py     # 共享工具（健康检查/时区）
│   │   │   ├── erp_sync_service.py      # 数据同步服务
│   │   │   ├── erp_sync_handlers.py     # 同步处理器（6种单据）
│   │   │   ├── client.py                # 快麦 API 客户端
│   │   │   └── dispatcher.py            # API 调度器
│   │   ├── agent/                    # Agent 架构层（多Agent单一职责）
│   │   │   ├── runtime/              # Run合同、证据账本、工具产物策略与完成门
│   │   │   │   ├── credential_broker.py     # tenant-bound opaque handle→lease→controlled consumer
│   │   │   │   └── wecom_app_credentials.py # 企微 App lease→token exchange 端口与 typed transport 组装
│   │   │   ├── image/requirement_assist_prompts.py # AI 帮写事实边界与多模态 Prompt
│   │   │   ├── image/input_adapters.py # 详情项目到共享 AI 帮写输入的安全适配器
│   │   │   ├── image/requirement_assist_service.py # 三方案模型调用、降级、校验与事实冲突闸门
│   │   │   ├── image/requirement_assist_rate_limiter.py # Redis 跨进程用户级 AI 帮写限流
│   │   │   ├── erp_agent.py              # ERP 独立 Agent（路由层）
│   │   │   ├── tool_executor.py          # 同步工具执行器与 handler 注册
│   │   │   ├── knowledge_tool_mixin.py   # 租户范围知识库工具执行
│   │   ├── intent_router_runtime_mixin.py # 路由知识增强、HTTP 客户端与观测
│   │   ├── knowledge_seed_service.py      # 种子知识导入与关系边重建
│   │   │   ├── org_invitation_mixin.py    # 企业邀请创建与接受
│   │   │   ├── tool_loop_executor.py     # LLM 工具循环引擎
│   │   │   ├── tool_loop_execution.py    # 工具执行阶段与统一Validation旁路观察
│   │   │   ├── tool_output.py            # 结构化工具输出协议（ToolOutput）
│   │   │   ├── session_file_registry.py  # 会话级文件注册表
│   │   │   ├── department_agent.py       # 部门Agent基类
│   │   │   ├── department_types.py       # 部门Agent类型（ValidationResult）
│   │   │   ├── compute_agent.py          # 独立计算Agent
│   │   │   ├── compute_types.py          # 计算Agent类型（ComputeTask/Result）
│   │   │   ├── experience_recorder.py    # Agent经验记录器
│   │   │   ├── execution_plan.py         # DAG执行计划（ExecutionPlan/Round）
│   │   │   ├── plan_builder.py           # 意图分析→执行计划构建器
│   │   │   ├── dag_executor.py           # DAG编排执行引擎
│   │   │   ├── data_query_cache.py       # Excel→Parquet 缓存（双重检查锁+快照校验）
│   │   │   ├── data_query_executor.py    # DuckDB 查询执行器（file_analyze 转 Parquet 后用）
│   │   │   ├── excel_reader.py           # ★ Excel 结构化读取（公式+编号，file_analyze 入口）
│   │   │   ├── excel_cleaner.py          # Excel 三层清洗（结构检测/智能清洗/质量校验）
│   │   │   └── departments/              # 部门Agent实现
│   │   │       ├── warehouse_agent.py        # 仓储Agent
│   │   │       ├── purchase_agent.py         # 采购Agent
│   │   │       ├── trade_agent.py            # 订单Agent
│   │   │       └── aftersale_agent.py        # 售后Agent
│   │   ├── file_executor.py          # 文件操作执行器（安全路径校验 + Query/Write Mixin 组合）
│   │   ├── file_query_extensions.py  # file_list/search/info/edit 扩展 Mixin
│   │   ├── file_write_extensions.py  # file_write/delete/mkdir/rename/move 扩展 Mixin
│   │   ├── file_upload.py            # 文件上传服务（upload_to_payload + download_url_to_workspace 远程URL落盘到「下载/AI图片」+ 双轨payload）
│   │   └── adapters/                 # AI 模型适配器
│   │       ├── __init__.py               # 适配器导出
│   │       ├── base.py                   # 适配器基类
│   │       ├── factory.py                # 适配器工厂
│   │       ├── kie/                      # KIE API 适配器
│   │       │   ├── client.py                 # HTTP 客户端
│   │       │   ├── models.py                 # 数据模型
│   │       │   ├── chat_adapter.py           # 聊天适配器
│   │       │   ├── image_adapter.py          # 图片生成适配器
│   │       │   └── video_adapter.py          # 视频生成适配器
│   │       └── google/                   # Google API 适配器
│   │           └── image_adapter.py          # Imagen 图片适配器
│   ├── config/                   # 配置文件
│   │   └── kie_models.py             # KIE 模型配置
│   ├── scripts/                  # 运维/数据修复与隔离 POC 脚本
│   │   ├── migration_runner.py   # PostgreSQL 迁移身份/checksum/baseline/事务执行门禁
│   │   ├── backfill_media_asset_urls.py # 历史图片 original_url/thumbnail_url 回填脚本
│   │   ├── backfill_conversation_context_items.py # 历史消息到 ConversationItem/Artifact 的幂等回填
│   │   ├── verify_conversation_context_backfill.py # 统一上下文回填完整性的只读硬切换门禁
│   │   └── poc_ecom_requirement_assist.py # 主图/详情图 AI 帮写三方案多模态 POC（不写业务数据）
│   └── migrations/              # 数据库迁移脚本
│       ├── 000_migration_ledger.sql # 完整文件名身份与 SHA-256 权威迁移账本
│       └── 034_wecom_oauth_support.sql  # 企微 OAuth 数据库迁移
│
└── frontend/                 # 前端代码（React/TypeScript）
    ├── package.json              # 前端依赖
    ├── vite.config.ts            # Vite 配置
    ├── tsconfig.json             # TypeScript 配置
    ├── index.html                # 入口 HTML
    └── src/
        ├── main.tsx                  # 应用入口
        ├── App.tsx                   # 根组件（路由配置）
        ├── index.css                 # 全局样式（TailwindCSS）
        ├── pages/                    # 页面组件
        │   ├── Home.tsx                  # 首页（含认证弹窗入口）
        │   ├── ForgotPassword.tsx        # 忘记密码页
        │   ├── Chat.tsx                  # 聊天页（主功能页）
        │   ├── DetailPage.tsx            # 主图/详情图独立五步制作页
        │   └── WecomCallback.tsx         # 企微 OAuth 回调着陆页
        ├── components/               # 组件
        │   ├── common/                   # 通用组件
        │   │   └── Modal.tsx                 # 通用弹窗组件（动画、ESC关闭、遮罩层）
        │   ├── ui/                       # 表单与基础 UI 组件
        │   │   └── Select.tsx                # 锚定式自定义下拉选择器
        │   ├── auth/                     # 认证相关组件
        │   │   ├── AuthModal.tsx             # 认证弹窗容器（登录/注册切换）
        │   │   ├── LoginForm.tsx             # 登录表单（密码/验证码双模式）
        │   │   ├── RegisterForm.tsx          # 注册表单（手机号+验证码）
        │   │   ├── WecomQrLogin.tsx          # 企微二维码扫码登录组件
        │   │   └── ProtectedRoute.tsx        # 路由守卫组件
        │   ├── detail-page/              # 主图详情制作页组件
        │   │   └── RequirementAssistModal.tsx # AI 帮写三方案选择、编辑与冲突提示弹窗
        │   │   ├── DetailPageHeader.tsx      # 顶部导航
        │   │   ├── StepBar.tsx               # 五步进度条
        │   │   ├── ProductImageSection.tsx   # 产品图/参考图选择器
        │   │   ├── GenerationSettings.tsx    # Step 1生成设置
        │   │   ├── AnalyzingPanel.tsx         # Step 2分析进度
        │   │   ├── PlanReviewPanel.tsx        # Step 3规划确认
        │   │   ├── PlanCard.tsx               # 单张规划编辑
        │   │   ├── GenerationProgress.tsx     # Step 4生成进度
        │   │   ├── GenerationCard.tsx         # 单张生成状态
        │   │   └── ResultGallery.tsx           # Step 5结果画廊
        │   ├── workspace/                # 工作区页面、文件区域与交互 Hooks
        │   │   ├── WorkspaceView.tsx
        │   │   ├── WorkspaceFileArea.tsx
        │   │   ├── WorkspaceDeleteDialog.tsx
        │   │   ├── useWorkspaceItemActions.ts
        │   │   ├── useWorkspaceSelectionActions.ts
        │   │   └── useWorkspaceKeyboard.ts
        │   └── chat/                     # 聊天相关组件
        │       ├── Sidebar.tsx               # 左侧栏（对话列表、用户菜单）
        │       ├── ConversationList.tsx      # 对话列表（按日期分组，302行）
        │       ├── ConversationItem.tsx      # 对话项组件
        │       ├── ContextMenu.tsx           # 右键菜单组件
        │       ├── DeleteConfirmModal.tsx    # 对话删除确认弹框
        │       ├── conversationUtils.ts      # 对话列表工具函数
        │       ├── MessageArea.tsx           # 消息区域
        │       ├── message/                  # 消息渲染组件（主项、气泡内容、媒体内容块）
        │       │   ├── MessageItem.tsx       # 单条消息编排（预览、工具栏、删除）
        │       │   ├── MessageBubbleContent.tsx # 气泡内容状态分发
        │       │   ├── MessageContentBlocks.tsx # AI 多内容块渲染
        │       │   ├── MarkdownRenderer.tsx  # 纯文本快速入口与富文本按需加载边界
        │       │   ├── RichMarkdownRenderer.tsx # Markdown/KaTeX/高亮重型渲染实现
        │       │   ├── DiagramBlock.tsx      # 结构化 Mermaid 关系图正式入口
        │       │   ├── MermaidRenderer.tsx   # Mermaid 按需加载、安全清理、缓存与源码降级
        │       │   ├── EChartsRenderer.tsx   # ECharts按需加载、状态机、重试与数据降级
        │       │   ├── echartsRuntime.ts     # ECharts具名注册与动态加载边界
        │       │   ├── MessageMedia.tsx      # 消息媒体容器（图片、视频、文件）
        │       │   ├── FormBlockContent.tsx  # 聊天表单活动态展示外壳与操作栏
        │       │   ├── MessageImageBlocks.tsx # 图片块渲染（缩略图展示、原图下载）
        │       │   └── InlineChartImage.tsx  # 内容块内联图片
        │       ├── MessageActions.tsx        # 消息操作工具栏
        │       ├── MessageToolbar.tsx        # 消息工具栏（旧版，待删除）
        │       ├── attachments/              # 聊天草稿附件统一领域层
        │       │   ├── ChatAttachment.types.ts # 统一图片/文件附件类型
        │       │   ├── attachmentAdapters.ts # 上传、引用、工作区来源适配
        │       │   ├── attachmentSubmission.ts # 原图与文件提交快照转换
        │       │   ├── useChatAttachments.ts # 统一添加、删除、状态与草稿事务
        │       │   └── ChatAttachmentPreview.tsx # 统一缩略图/文件预览
        │       ├── InputArea.tsx             # 输入区域（组合 InputControls 和工具栏）
        │       ├── useInputSubmission.ts     # 输入提交与草稿事务结算
        │       ├── useInputDraftTransaction.ts # 文本草稿移出与合并恢复
        │       ├── useInputTaskControls.ts   # 停止、ESC 中断与 steer 控制
        │       ├── useInputExternalEvents.ts # 电商确认与建议发送事件监听
        │       ├── inputCompletions.ts       # 电商模式 Tab 补全词典
        │       ├── InputControls.tsx         # 输入控制（文本框、按钮、上传）
        │       ├── InputControls.types.ts   # 输入控制 Props 类型边界
        │       ├── ModelSelector.tsx         # 模型选择器
        │       ├── AdvancedSettingsMenu.tsx  # 高级设置菜单（图像/视频/推理参数）
        │       ├── SettingsModal.tsx         # 个人设置弹框
        │       ├── UploadMenu.tsx            # 上传菜单
        │       ├── ImageContextMenu.tsx       # 图片右键上下文菜单（引用/复制/下载）
        │       ├── ImagePreviewModal.tsx     # 图片预览弹窗（全屏缩放下载）
        │       ├── LoadingPlaceholder.tsx    # 统一加载占位符（文字 + 跳动圆点）
        │       ├── MediaPlaceholder.tsx      # 统一媒体占位符（灰色框 + 图标）
        │       ├── __tests__/MediaPlaceholder.test.tsx # 媒体失败占位符（积分不足/普通失败）回归测试
        │       ├── AudioPreview.tsx          # 音频预览
        │       ├── AudioRecorder.tsx         # 录音组件
        │       ├── ConflictAlert.tsx         # 模型冲突提示
        │       ├── EmptyState.tsx            # 空状态提示
        │       ├── LoadingSkeleton.tsx       # 加载骨架屏
        │       └── DeleteMessageModal.tsx    # 删除消息确认弹框
        ├── stores/                   # 状态管理（Zustand）
        │   ├── useAuthStore.ts           # 认证状态（用户信息、Token）
        │   ├── useAuthModalStore.ts      # 认证弹窗状态（开关、模式切换）
        │   ├── sessionStoreResetRegistry.ts # 已加载会话 Store 的同步清理注册表
        │   ├── useMessageStore.ts        # 统一消息 Store（消息、任务、缓存）
        │   ├── useDetailPageStore.ts     # 主图详情制作页专用状态
        │   └── useTaskRestorationStore.ts # 任务恢复状态
        │   └── slices/                  # Store slice 与按职责拆分的 action factories
        │       ├── streamingLifecycleActions.ts # 流式消息启动、注册与完成
        │       ├── optimisticMessageActions.ts  # 乐观消息增删改与错误替换
        │       └── streamingUiActions.ts        # 思考、提示、建议与工具确认状态
        ├── services/                 # API 调用
        │   └── ecomRequirement.ts        # AI 帮写请求快照与可取消长请求
        │   ├── api.ts                    # Axios 配置
        │   ├── auth.ts                   # 认证 API
        │   ├── conversation.ts           # 对话 API
        │   ├── message.ts                # 消息 API
        │   ├── messageSender.ts          # 统一消息发送器（chat/image/video）
        │   ├── messageSendLifecycle.ts   # 消息乐观更新、API 响应替换与错误回滚
        │   ├── upload.ts                 # 文件上传服务
        │   ├── detailProject.ts          # 主图详情页草稿读取、关联与设置 API
        │   └── audio.ts                  # 音频服务
        ├── types/                    # TypeScript 类型
        │   └── ecomRequirement.ts        # AI 帮写事实、参考图、冲突与三方案协议
        │   ├── auth.ts                   # 认证相关类型
        │   ├── message.ts                # 消息相关类型（ContentPart、Message、Task 等）
        │   ├── task.ts                   # 任务相关类型（兼容旧格式）
        │   └── websocket.ts              # WebSocket 消息类型
        ├── schemas/                  # 外部数据运行时协议
        │   └── messageProtocol.ts        # ContentPart Zod 校验、null 归一化与隔离日志
        ├── contexts/                  # React 上下文与 WebSocket 事件处理
        │   ├── WebSocketContext.tsx      # WebSocket 连接、订阅和 handler 依赖注入
        │   ├── wsMessageHandlers.ts      # WebSocket 事件工厂与流式/通知事件
        │   ├── wsMessageHandlerShared.ts # handler 共享类型、订阅清理与 chunk flush
        │   └── wsTaskMessageHandlers.ts  # 任务完成/失败与图片 partial update
        │   └── wsRoutingCompleteHandler.ts # 路由完成后的媒体占位符与聊天参数更新
        ├── hooks/                    # 自定义 Hooks
        │   ├── useDetailRequirementAssist.ts # AI 帮写弹窗请求、竞态和三方案编辑状态
        │   ├── useThumbnailFallback.ts # 小图 thumbnail→original→failed 加载状态机
        │   ├── useImageUpload.ts         # 图片上传逻辑
        │   ├── workspace/                # 工作区浏览、上传、变更和视图状态子 Hooks
        │   │   ├── useWorkspaceBrowser.ts
        │   │   ├── useWorkspaceUpload.ts
        │   │   ├── useWorkspaceMutations.ts
        │   │   └── useWorkspaceViewState.ts
        │   ├── useAudioRecording.ts      # 录音逻辑
        │   ├── useDragDropUpload.ts      # 拖拽上传逻辑
        │   ├── useMessageLoader.ts       # 消息加载逻辑（含缓存）
        │   ├── useMessageHandlers.ts     # 消息发送处理逻辑（组合器）
        │   ├── useRegenerateHandlers.ts  # 消息重新生成逻辑
        │   ├── useModelSelection.ts      # 模型选择逻辑（含用户选择保护）
        │   ├── useWorkspace.ts           # 工作区状态组合公共 Hook
        │   ├── __tests__/useWorkspace.test.ts # 工作区切换、取消和竞态回归测试
        │   ├── useVirtuaScroll.ts        # Virtua 滚动管理（统一入口）
        │   ├── useUnifiedMessages.ts     # 统一消息读取（合并持久化+临时消息）
        │   ├── __tests__/useUnifiedMessages.test.ts # 异步任务乱序完成的稳定顺序回归
        │   ├── useClickOutside.ts        # 点击外部关闭逻辑
        │   └── handlers/                 # 消息处理器子模块
        │       ├── useTextMessageHandler.ts   # 文本消息处理
        │       ├── useMediaMessageHandler.ts  # 统一媒体消息处理（图片/视频）
        │       └── __tests__/messageHandlers.test.tsx # 发送异常向上传播回归测试
        ├── constants/                # 常量配置
        │   ├── models.ts                 # 模型配置（UnifiedModel）
        │   ├── placeholder.ts            # 占位符常量（PLACEHOLDER_TEXT）
        │   └── echartsThemes.ts          # ECharts 6 套主题配置（classic/claude/linear × light/dark）
        └── utils/                    # 工具函数
            ├── settingsStorage.ts        # 用户设置存储
            ├── modelConflict.ts          # 模型冲突检测
            ├── messageUtils.ts           # 消息工具函数（getTextContent、normalizeMessage）
            ├── displayValue.ts           # 结构化值安全展示与表单标量适配
            ├── imageUrlRules.ts          # 图片 URL 规则（原图/缩略图语义入口）
            ├── messageCoordinator.ts     # 消息协调器
            ├── mergeOptimisticMessages.ts # 合并乐观更新消息（去重逻辑）
            ├── imageUtils.ts             # 图片URL工具
            ├── logger.ts                 # 统一日志工具
            ├── taskNotification.ts       # 任务通知工具
            ├── taskRestoration.ts        # 任务恢复工具（WebSocket 恢复）
            └── tabSync.ts                # 跨标签页同步（BroadcastChannel）
        ├── preview/adapters/          # 文件预览适配器
        │   ├── PdfAdapter.tsx            # PDF 轻量匹配入口与按需加载壳
        │   ├── PdfPreview.tsx            # PDF.js 重型渲染实现
        │   ├── PdfPreviewControls.tsx    # PDF/Office 共用翻页缩放工具栏
        │   ├── PptxAdapter.tsx           # Office 轻量匹配入口与按需加载壳
        │   ├── PptxPreview.tsx           # Office 转 PDF 后的重型渲染实现
        │   ├── SpreadsheetPreview.tsx    # 电子表格加载、Sheet 状态与取消清理
        │   ├── SpreadsheetTable.tsx      # 电子表格纯展示与 Sheet Tabs
        │   └── spreadsheetData.ts        # CSV/TSV 解析与合并单元格清理
│
└── tests/                    # 单元测试
    ├── __init__.py               # 测试模块标识
    ├── conftest.py               # pytest fixtures（mock 对象）
    ├── test_auth_service.py      # 认证短信与用户响应格式测试
    ├── test_auth_service_login.py # Web 注册、手机号/密码/企业登录 RPC 测试
    ├── test_auth_service_tokens.py # 密码重置、refresh 轮换与登出 RPC 测试
    ├── test_admin_user_activity_ordering.py # 管理员用户活跃时间排序契约测试
    ├── test_conversation_service.py  # 对话服务测试（11个用例）
    ├── test_message_service.py   # 消息服务测试（12个用例）
    ├── test_image_ecom_retry.py  # 电商图片失败占位原位替换、会话隔离与资产登记测试
    ├── test_admin_user_assets.py # 超管统一资产列表、复合游标与权限边界测试
    ├── test_admin_user_assets_role_matrix_external.py # 超管资产治理门面的真实 PostgreSQL 登录角色矩阵
    ├── test_admin_user_assets_rpc_contract.py # ZIP RPC 命名参数、Jsonb 绑定与迁移 JSONB 签名合同
    ├── test_admin_asset_routes.py # 统一资产 ZIP 治理 RPC、失败关闭、顺序与旧端点删除测试
    ├── test_admin_users_helpers.py # 管理员会话解析和通用 helper 测试
    ├── test_backfill_user_assets.py # 五类历史资产投影、checkpoint 与失败续跑测试
    ├── test_recent_tool_history.py # 最近 3 个用户回合的安全工具历史投影测试
    ├── test_legacy_summary_migration_retained.py # 旧摘要数据库回滚合同保留测试
    ├── test_file_browse.py       # 工作区空目录、无效目录和存储故障契约测试
    └── test_chat_payload_blocks.py # 聊天 emit_payload 图片 URL 字段保留测试
```

测试执行与 AI Token 控制规范见
`docs/document/TECH_TEST_EXECUTION.md`；详细流程按需加载
`.cursor/skills/everydayai-test-coverage/SKILL.md`。

## 开发规范
- 遵循 `.cursorrules` 中定义的所有规则
- 代码质量底线：文件≤500行、函数≤120行、圈复杂度≤15、嵌套≤4层
- 所有依赖必须使用精确版本号（==）
- 错误处理：try-except + loguru，日志需包含业务上下文
- 异步优先：耗时操作必须异步实现
- 并发限制：全局15个任务，单对话5个任务

## 核心架构设计

### 多任务并发架构
- **并发模型**：全局并行，允许多个对话同时执行任务
- **限流策略**：三层防护（前端、后端接口、积分系统）
- **超时策略**：HTTP 60秒超时，任务无强制超时，仅大模型返回失败时判定
- **积分锁定**：提交时锁定，成功扣除，失败/超时全额退回
- **实时通信**：Supabase Realtime监听数据库变化，自动推送任务进度
- **断线重连**：前端重连后自动拉取活跃任务并恢复订阅

### 数据存储架构
- **结构化数据**：Supabase PostgreSQL（用户、对话、消息、任务、积分记录、用户活跃事件）
- **文件存储**：阿里云OSS（图片、视频）
  - 前端直传OSS（使用STS临时凭证）
  - 生成URL后保存到Supabase
  - CDN加速访问
- **缓存层**：Redis（任务队列、频率限制）

### AI 模型接入架构
- **模型来源**：
  - KIE 代理：价格低 70-85%，统一 OpenAI 兼容接口
  - Google 官方 API：有免费额度（待开发）
- **模型类型**：
  - Chat：Gemini 3 Pro/Flash（KIE），Gemini 2.5/3 Flash Preview（Google 待开发）
  - Image：Nano Banana 系列（KIE）
  - Video：Sora 2 系列（KIE）
- **调用模式**：
  - Chat：同步流式（SSE → WebSocket 推送）
  - Image/Video：异步任务（Webhook 回调为主 + 轮询兜底 120s）
- **成本控制**：预扣费机制（Lock → Execute → Settle）
- **详细 API 文档**：见 `API_REFERENCE.md`

### UI展示规范
- **对话列表徽章**：🔄显示进行中任务数，✅显示已完成未查看任务数
- **消息气泡状态**：进度条展示任务进度，明确提示失败原因
- **消息工具栏**：复制、下载、编辑、删除（删除悬停显示）
- **分屏模式**：左侧40%对话区，右侧60%图片查看器

---

## 待开发功能规划

### 一、Google 官方 Gemini API 适配器
- **优先级**：中
- **类型**：新增功能（级别 A）
- **目标**：接入 Google 官方 API（有免费额度），支持 `Gemini 2.5 Flash Preview` 和 `Gemini 3 Flash Preview`

**文件清单**：
| 文件 | 操作 | 说明 |
|------|------|------|
| `requirements.txt` | 修改 | 追加 `google-genai==x.x.x` |
| `backend/services/adapters/google/__init__.py` | 新建 | 包初始化和导出 |
| `backend/services/adapters/google/client.py` | 新建 | Google API 客户端封装 |
| `backend/services/adapters/google/chat_adapter.py` | 新建 | 聊天适配器 |

**技术要点**：
- SDK：`google-genai`
- 环境变量：`GEMINI_API_KEY` 或 `GOOGLE_API_KEY`
- 接口风格：与现有 `KieChatAdapter` 对齐
- 支持流式/非流式输出、多轮对话

**官方文档**：
- API Key：https://ai.google.dev/gemini-api/docs/api-key
- SDK：https://ai.google.dev/gemini-api/docs/libraries
- 多轮对话：https://ai.google.dev/gemini-api/docs/interactions

---

### 二、KIE Gemini 调用优化（Gemini 3 新特性适配）
- **优先级**：中
- **类型**：功能增强（级别 A）
- **目标**：利用 Gemini 3 新特性提升 Chat 和图像生成的准确度

**参考文档**：
- Gemini 3 指南：https://ai.google.dev/gemini-api/docs/gemini-3?hl=zh-cn
- 图像生成指南：https://ai.google.dev/gemini-api/docs/image-generation?hl=zh-cn
- 文本生成指南：https://ai.google.dev/gemini-api/docs/text-generation?hl=zh-cn
- 函数调用指南：https://ai.google.dev/gemini-api/docs/function-calling?hl=zh-cn

**优化项一：Chat 准确度提升**

| 优化项 | 当前状态 | 目标状态 | 影响文件 |
|--------|---------|---------|---------|
| `thinking_level` 参数 | 仅 `LOW/HIGH` | 支持 `minimal/low/medium/high` | `models.py`, `chat_adapter.py` |
| `temperature` 控制 | 无 | 默认 `1.0`（官方强烈推荐） | `chat_adapter.py` |
| `media_resolution` 参数 | 无 | 图像 `high`(1120 tokens)、PDF `medium`(560 tokens)、视频 `low`(70 tokens/帧) | `models.py`, `chat_adapter.py` |
| Thought Signatures | 未处理 | 多轮对话/函数调用保留加密推理表示 | `chat_adapter.py`, `client.py` |
| 结构化输出 | 未支持 | `response_mime_type` + `response_json_schema` | `models.py`, `chat_adapter.py` |

**优化项二：图像生成准确度提升**

| 优化项 | 当前状态 | 目标状态 | 影响文件 |
|--------|---------|---------|---------|
| Google Search 接地 | 未启用 | `tools=[{"google_search": {}}]` 基于实时数据生成 | `image_adapter.py` |
| 多参考图片 | 最多 8 张 | 最多 14 张（6 物体 + 5 人物） | `image_adapter.py`, `models.py` |
| 多轮对话迭代 | 单次生成 | 使用 `chat.send_message()` 渐进式优化 | `image_adapter.py` |

**优化项三：函数调用最佳实践**

| 优化项 | 当前状态 | 目标状态 |
|--------|---------|---------|
| 函数数量 | 未限制 | 控制在 10-20 个以内 |
| 调用模式 | 未配置 | 支持 `AUTO/ANY/NONE/VALIDATED` |
| 并行调用 | 未支持 | 单响应可返回多个函数调用 |
| temperature | 默认 | 函数调用场景设为 `0` |

**文件清单**：
| 文件 | 操作 | 说明 |
|------|------|------|
| `backend/services/adapters/kie/models.py` | 修改 | 添加 `ThinkingLevel`、`MediaResolution` 枚举，更新请求模型 |
| `backend/services/adapters/kie/chat_adapter.py` | 修改 | 支持新参数，处理 Thought Signatures |
| `backend/services/adapters/kie/image_adapter.py` | 修改 | 支持 Google Search、多参考图片、多轮对话 |
| `backend/services/adapters/kie/client.py` | 修改 | 处理 Thought Signatures 的传递和保留 |

**代码示例参考**：

```python
# Chat - 推理深度控制
class ThinkingLevel(str, Enum):
    MINIMAL = "minimal"  # Flash 专用
    LOW = "low"
    MEDIUM = "medium"    # Flash 专用
    HIGH = "high"        # 默认

# Chat - 媒体分辨率控制
class MediaResolution(str, Enum):
    LOW = "low"      # 70 tokens/帧，适合视频
    MEDIUM = "medium"  # 560 tokens，适合 PDF
    HIGH = "high"    # 1120 tokens，适合图像

# 图像生成 - Google Search 接地
response = client.models.generate_content(
    model="gemini-3-pro-image-preview",
    contents=prompt,
    config=types.GenerateContentConfig(
        response_modalities=['TEXT', 'IMAGE'],
        tools=[{"google_search": {}}]
    )
)

# 图像生成 - 多轮对话迭代
chat = client.chats.create(
    model="gemini-3-pro-image-preview",
    config=types.GenerateContentConfig(
        response_modalities=['TEXT', 'IMAGE'],
    )
)
response = chat.send_message("生成一张...")
response = chat.send_message("把背景改成...")  # 迭代优化
```

---

### 三、KIE 成本优化功能
- **优先级**：低
- **类型**：功能增强（级别 A）
- **目标**：降低 API 调用成本

**参考文档**：
- 上下文缓存：https://ai.google.dev/gemini-api/docs/caching?hl=zh-cn
- 批量 API：https://ai.google.dev/gemini-api/docs/batch-api?hl=zh-cn
- Files API：https://ai.google.dev/gemini-api/docs/files?hl=zh-cn
- 长上下文：https://ai.google.dev/gemini-api/docs/long-context?hl=zh-cn

**优化项一：上下文缓存（Context Caching）**

| 项目 | 说明 |
|------|------|
| 功能 | 缓存重复使用的内容（系统指令、参考图、文档） |
| 收益 | 输入费用降低 **4 倍** |
| 最低阈值 | Gemini 3 Flash: 1024 tokens / Gemini 3 Pro: 4096 tokens |
| 有效期 | 可配置 TTL，默认 48 小时 |

**适用场景**：
- 固定系统指令的 Chat
- 重复分析同一批参考图
- 大型文档的多次查询

**代码示例**：
```python
cache = client.caches.create(
    model=model,
    config=types.CreateCachedContentConfig(
        system_instruction='指令内容',
        contents=[file_object],
        ttl="300s"
    )
)
```

**优化项二：批量 API（Batch API）**

| 项目 | 说明 |
|------|------|
| 功能 | 异步批量处理请求 |
| 收益 | 价格为标准费用的 **50%** |
| 限制 | 24 小时内处理完成 |
| 格式 | 内嵌请求（≤20MB）或 JSONL 文件（≤2GB） |

**适用场景**：
- 批量图像生成（非实时）
- 数据预处理、内容审核
- 离线评估任务

**优化项三：Files API**

| 项目 | 说明 |
|------|------|
| 功能 | 上传大文件供多次使用 |
| 限制 | 单文件 2GB，项目总量 20GB，保留 48 小时 |
| 收益 | 减少重复上传带宽，降低延迟 |

**适用场景**：
- 大型 PDF 文档分析
- 视频理解（上传后多次查询）
- 参考图片库（上传后复用）

**优化项四：长上下文最佳实践**

| 优化项 | 说明 |
|--------|------|
| 查询位置 | 将问题放在 prompt **末尾**，效果更好 |
| 成本优化 | 结合上下文缓存使用 |
| 准确率 | 单查询可达 99%，多信息检索会下降 |

---

## 更新记录

- **2026-07-17**：企微入站统一 Actor 与附件原子消费
  - 新增 `130_wecom_actor_attachment_consumption.sql` 及可独立执行的回滚脚本
  - TEXT/VOICE/IMAGE/MIXED 不再由企微同步旧链路处理
  - 下一条指令在数据库会话锁内原子消费 active 附件，重试不会误消费后续附件
  - 群聊 Actor 入队按 `conversation_channel_bindings` 校验 corp/chatid，并在消息上保留真实发送人
  - 删除 `conversation_actor_wecom_enabled`；企微生成入口不再存在运行时双轨切换
  - 删除企微旧同步生成与结果持久化尾链；新增
    `backend/tests/test_wecom_reply_and_media.py`，将超限测试按职责拆分
  - 新增 `services/handlers/chat/execution_scope.py`，群聊执行分离操作者与资源 owner
  - 新增 `chat_tool_helpers.py`、`conversation_tool_mixin.py`、
    `file_describe_mixin.py`、`erp_child_factory_mixin.py`，使受影响工具文件均低于 500 行
  - 群聊不读取个人 Memory/偏好/位置，不开放个人数据及定时任务工具；
    文件、Sandbox、ERP 与图片产物统一进入 channel Workspace
- **2026-07-17**：企微 FILE 统一为原始资产 `FilePart` 后，删除已无生产调用的
  `services/wecom/file_parser.py` 及其孤立测试；文件内容理解统一由标准工具链按需完成。
- **2026-07-17**：企业微信图表能力回退（已被 2026-07-18 文本降级策略取代）
  - Web 继续渲染统一 `ChartPart`，支持 ECharts、Plotly 和 Vega-Lite
  - 企微通道不运行浏览器图形渲染器；当前 chart 降级为格式化 JSON，diagram 降级为原始 Mermaid 源码
  - Outbox 保留原始 content index 检查点，并为结构化图形产生稳定文本投递项
  - 删除企微 Playwright/Chromium/ECharts runtime 与部署安装链路
- **2026-07-16**：新增消息发送草稿事务与幂等协议技术设计
  - 统一文字、图片、视频和电商图的输入草稿提交时序
  - 设计 `Idempotency-Key`、请求指纹、响应重放和不确定结果安全重试
  - 规划 `message_generation_requests` 专用表、原子 claim RPC 与完整回滚路径
  - 详见 [TECH_消息发送草稿事务与幂等协议.md](document/TECH_消息发送草稿事务与幂等协议.md)
  - 前端发送协议已接入固定 request/task/message ID 与 `Idempotency-Key`
  - timeout、网络错误、无业务错误码的 502/503/504 最多使用同一请求安全重试 2 次
  - 结果未知时保留乐观消息和任务订阅，等待后续恢复；明确业务拒绝保持原回滚行为
- **2026-06-22**：工作区分类筛选 + 图片视频预览 + 批量下载 ZIP
  - 新增 `frontend/src/utils/fileCategory.ts`：扩展名白名单 + mime 兜底分类（image/video/document）
  - 新增 `frontend/src/components/workspace/WorkspaceCategoryTabs.tsx`：3 个 Tab（全部/文档/图片与视频）+ 蓝色下划线
  - 新增 `frontend/src/components/chat/media/VideoPreviewModal.tsx`：视频全屏 Modal（Portal + ESC + ←→ 切换）
  - `useWorkspace.ts`：加 `categoryFilter` 状态；默认排序改 `modified desc` 并持久化；images Tab 自动切 grid
  - `WorkspaceView.tsx`：接入 Tab + 客户端 filter + 双击图片/视频分发到对应 Modal（顺带修双击 PNG 走下载的 bug）
  - 后端新增 `POST /workspace/download_zip`（zipstream-ng 流式 + 500 文件/2GB 上限）
  - **后端 file.py 拆分**：原 790 行单文件按职责拆为 `file.py`（25 行聚合）+ `file_common.py` / `file_upload.py` / `file_browse.py` / `file_manage.py` / `file_download.py`，所有子模块 ≤251 行
  - 新增依赖 `zipstream-ng==1.7.1`（纯 Python 流式 ZIP，UTF-8 中文文件名）
  - 测试：前端新增 43 个用例（fileCategory），后端新增 14 个用例（test_workspace_zip）
  - 详见 [TECH_工作区分类与批量下载.md](document/TECH_工作区分类与批量下载.md)
- **2026-05-03**：交互式图表（ECharts 替代 matplotlib）
  - 新增 `ChartPart` content block 类型（后端 `schemas/message.py` + 前端 `types/message.ts`）
  - 沙盒 `.echart.json` 检测 → JSON 读取 → `_chart_options` 传播链（executor → tool_executor → chat_tool_mixin → chat_handler）
  - 前端 `ChartBlock.tsx` ECharts 按需动态加载 + 6 套主题跟随 + toolbox 全开
  - 前端 `echartsThemes.ts` 6 套主题配置（classic/claude/linear × light/dark）
  - 提示词改造：matplotlib → ECharts JSON 输出 + 图表选择参考 + 反模式护栏
  - 新增 10 个后端测试（`test_chart_block.py`）
- **2026-03-07**：记忆智能过滤功能
  - 新增 `memory_filter.py` 千问精排过滤器（降级链：turbo → plus → 跳过）
  - Mem0 search 加 `threshold=0.5` 相似度阈值初筛
  - `format_memory()` 保留 score 字段
  - `DASHSCOPE_BASE_URL` 统一提取到 `config.py`，消除跨文件重复
- **2026-03-01**：修复刷新恢复场景僵尸消息
  - `MessageResponse` 添加 `field_validator` 处理 Supabase JSONB 字符串 → dict 转换
  - `/tasks/pending` API 增加 `client_task_id` 返回字段
  - `taskRestoration.ts` WS 订阅优先使用 `client_task_id`（与后端推送 ID 一致）
  - 清理 `task_completion_service.py` 中遗留的 debug print
- **2026-02-08**：Webhook 回调改造（回调为主 + 轮询兜底 + 多 Provider 兼容）
  - 新增 `task_completion_service.py` 统一任务完成处理（幂等、OSS 上传、handler 分发）
  - 新增 `webhook.py` 多 Provider Webhook 路由（`/api/webhook/{provider}`）
  - 适配器基类新增 `parse_callback()` / `extract_task_id()` 抽象方法
  - KIE 图片/视频适配器实现回调解析
  - Handler 基类新增 `_build_callback_url()` 回调地址构建
  - `BackgroundTaskWorker` 轮询间隔从 30s 降级到 120s，仅作兜底
  - 消除双路径格式不一致问题（polling/handler 统一走 TaskCompletionService）
- **2026-03-23**：工具系统统一架构（v5.0）
  - 新增 `config/tool_registry.py`：统一工具注册表（ToolEntry + 26 工具 + 同义词表）
  - 新增 `services/tool_selector.py`：三级匹配（同义词+tags+qwen-turbo）+ action 筛选
  - 废弃 v1 Agent Loop：删除 `_execute_loop_v1`、`AGENT_TOOLS`、`AGENT_SYSTEM_PROMPT`、`model_search.py`
  - 提示词精简：ERP_ROUTING_PROMPT 105行→40行，LOCAL_ROUTING_PROMPT 删除
  - action description 内嵌 13 条危险模式警告（5 个 registry 文件）
  - 兜底扩充机制：ToolExpansionNeeded（工具/action 各最多补充 1 次）
- **2026-02-04**：完成聊天任务刷新恢复功能
  - 新增 `ChatStreamManager` 后台协程管理器，支持 SSE 断开后继续处理
  - 新增 `/tasks/{task_id}/stream` SSE 恢复端点，支持断点续传
  - 新增 `tabSync.ts` 跨标签页广播同步
  - 完善 `taskRestoration.ts` 任务恢复逻辑（chat/image/video）
  - 统一任务恢复入口：`onRehydrateStorage` → `restoreAllPendingTasks`
- **2026-02-03**：滚动系统从 Virtuoso 迁移到 Virtua
  - 使用 `useVirtuaScroll.ts` 统一入口，删除旧的 `useVirtuosoScroll.ts`
  - 移除 `react-virtuoso` 依赖，改用更轻量的 `virtua`（~3KB）
  - 更好的动态高度支持，解决消息闪烁问题
- **2026-02-02**：完成聊天系统综合重构阶段5-7（状态管理重设计、占位符持久化、性能优化）
  - 消息合并算法优化 O(n²) → O(n)，图片加载失败重试机制
- **2026-02-01**：完成聊天系统综合重构阶段0-4（34/35任务，97%进度）
  - 统一消息发送架构（mediaSender、mediaGenerationCore）
  - 统一缓存写入（setMessages 兼容层）
  - 统一占位符组件（LoadingPlaceholder、MediaPlaceholder）
- **2026-01-31**：完成登录/注册弹窗化重构（Modal、AuthModal、LoginForm、RegisterForm）
- **2026-01-24**：完成视频生成功能集成（Sora 2 系列 3 个模型）
- **2026-01-21**：完成基础架构搭建（FastAPI + React + Supabase）
- **2026-07-29**：Agent Runtime 生产 composition 已实现但默认关闭
  - API、Actor、WeCom 仅负责持久 ingress，不构造 Runtime 或 Sandbox Owner
  - Runtime、Projection、Authorization Recovery、Sandbox 使用独立进程、
    Linux 用户和最小权限数据库角色
  - Tool Confirmation V3 仅解决 PostgreSQL Authorization Interaction；
    PolicyReceipt 与 Dispatch Gate 是唯一执行门禁
  - Sandbox 固定 nsjail/rootfs/seccomp 哈希及 cgroup v2 上限，无裸 Python 降级
  - Sandbox Linux合同以真实非root `everydayai-sandbox` 身份运行；jail内
    65534:65534只映射到启动Worker的非root UID/GID，root启动失败关闭
  - migration 223 clean rollback通过有效权限矩阵验证PUBLIC、普通Runtime和旧Worker
    均不能取得compatibility projection mutation权限
  - 生产发布、灰度、告警和回滚合同见
    [AGENT_RUNTIME_PRODUCTION_RUNBOOK.md](AGENT_RUNTIME_PRODUCTION_RUNBOOK.md)
- **2026-08-04**：C5.2 增加 capability-scoped safe composition；仅接入 Runtime read（含 C2.1 org-scoped ERP read），模型/ActionLoop 通过显式注入接线，ERP write、Media、external specialist 及未启动 Worker/Projection/Authorization/Sandbox 保持 unavailable/disabled，整体 production readiness 仍为 false。
- **2026-08-05**：C7-B3.1 增加 code-owned production composition spine；Worker 不再接受动态 factory 或组件注入，真实安全服务未接线时返回强类型、结构化 NOT_READY，未启用任何 Provider 或生产能力。
- **2026-08-01**：AR-17.1 共享基础已在独立分支实现，默认仍关闭
  - 224 additive v2 ingress 在同一 PostgreSQL 事务中冻结 Session、Command、Run envelope、上下文锚点和 EffectiveToolset
  - AgentDefinition、Runtime Tool Catalog、Executor revision 与 Authorization 使用同一 code_execute-only 目录事实
  - v2 Context 严格绑定 Run/session/base revision/through message，并在后续 ModelStep 复现 tool_call 与 terminal result
  - 224 持久化不可变 AgentDefinition/Catalog/EffectiveToolset 文档，分离新 ingress enablement 与历史 Run recoverability；v1→v2 后旧 Run 仍按原 facts 恢复。`org_id=NULL` 个人 ingress 仍受组织 rollout 白名单限制，属于 AR-17 完成前阻塞项
  - Definition facts 冻结 prompt/model/context policy；Catalog 与 EffectiveToolset hash 覆盖完整执行安全语义，已提交 Command 在 gate 漂移后按原 envelope readback
  - AR-17.2～17.4、42 项专业工具、生产启动接线和生产验收未完成
- **2026-08-02**：AR-17.3 专业 Executor 与 226 additive lane 在独立 worktree 实施
  - 新增 23 项唯一 Descriptor、Remote Read/Artifact Job/Media/ERP Mutation/Sync/Workspace/Child Run/Scheduled Task 专业族
  - 新增 Provider reconcile、Callback Inbox、独立 Action Cost Ledger、Artifact lineage、Child Run 与资源 CAS 的 226_01～226_06 迁移及失败关闭 rollback
  - 非生产 Catalog 显式合并 18 个 AR-17.2 只读工具、code_execute 与 23 项新工具，生产 Catalog、EffectiveToolset、rollout 和 Owner 仍关闭
  - 真实 PostgreSQL 并发/RLS/权限和隔离 Provider 网络验收仍未执行；AR-17、AR-17.4 和生产启用仍未完成

- **2026-08-02**：AR-17.3 真实适配器修复批次继续实施（非生产）
  - 23 项工具逐项绑定 ERP、DashScope、Crawler、KIE、Artifact、Workspace/OSS、Scheduler 与 Child Run 端口
  - 八类 Executor 具备各自请求边界；严格传输层只允许登记的 provider/method/path
  - Callback 应用层 HMAC 验签后才写持久 inbox；Child Run 使用 SHA-256 并做幂等 readback
  - Workspace 删除前验证 OSS retention，恢复按稳定资源 ID 使用隔离暂存和原子 rename
  - ERP action 只接受 `services.kuaimai.registry.TOOL_REGISTRIES` 正式条目；`erp_api_search` 保持本地文档搜索
  - Provider status/cancel 无证明时保持 unknown；Callback 唯一入口拒绝旧 boolean 验签绕过

- **2026-08-09**：AR-18 B7-S1 Scheduler 控制面（仅非生产）
  - 新增 `backend/migrations/227_28_agent_runtime_scheduler_control.sql` 及精确 rollback：复用既有
    `scheduled_tasks`，以不可变 operation intent/receipt 和单一 Runtime worker RPC 原子完成
    create/update/pause/resume/delete，包含 tenant/session/run/action/attempt、PolicyReceipt、
    DispatchIntent、request hash、execution token、kill epoch/revision、幂等和 CAS 栅栏。
  - `PostgresSchedulerControlStore`、`ScheduledTaskService` 和 `PortBackedProvider` 接入
    readback/cancel/reconcile 与 committed/unknown/cancelled 状态映射；旧 Scanner、
    ScheduledTaskAgent、ToolLoop、投递/计费/Provider 业务链路未切换，production readiness 仍为 false。
  - 新入口沿用现有组织职位/部门权限和 push target 租户边界；未配置业务权限的 `other`
    部门失败关闭。调度定义变更必须原子携带已校验时区和重新计算的 `next_run_at`，避免
    Scanner 继续读取旧执行时间。
  - 资源操作范围与 `PermissionChecker` 保持一致：boss 全部、VP 按 data scope、manager
    同部门、deputy/member 仅本人。pause 原子清空 `next_run_at`；resume 先经窄 RPC 读取
    权威调度快照，再用既有 `calc_next_run` 计算并通过 schedule hash + CAS 原子提交；过期
    once 任务失败关闭，模型参数不能指定恢复时间。
  - Scheduler payload normalization 与 service orchestration 分别位于
    `scheduler_control_payload.py`、`scheduler_service_support.py`，沿用既有 cron/once 定义语义。

- **2026-08-09**：AR-18 B7-S2-A1 Scheduled Run Owner Facts（仅非生产）
  - 新增 `227_29_agent_runtime_scheduled_execution_owner.sql` 及精确 rollback，仅建立
    Runtime scheduled execution profile 与 per-run 单一 Owner binding，不提交 Runtime
    Command/Run，也不改变旧 Scanner、计费、终结或投递行为。
  - 仅没有 Runtime profile 的历史 run 可默认 legacy；Runtime profile 已存在但 run 尚未
    binding 时失败关闭。Profile 的 definition/model/toolset/scope/channel/budget 全部从来源
    Session/Command/Run/Action 与 227_28 receipt 派生；来源 Run 必须持有真实可调用
    `manage_scheduled_task` 的冻结 production toolset，目标 profile 再派生为 C7-B3.2-A
    9/17 项 safe-read 子集；`catalog/scheduled_toolset.py` 使用 Runtime canonical JSON 生成
    snapshot/hash，数据库逐字段重建来源子集并复算规范字节，不接受任意 Worker 快照。
    Owner 按 run → trigger → tenant/provider/capability gate
    固定锁序选择且不可切换。
  - Runtime Worker 仅获得 profile/readback/owner-select/runtime-binding/assert 窄 RPC；两张
    事实表 FORCE RLS 且无 Worker 直权；旧入口可在后续通过不可外调的 owner gate 包装。
    Runtime Command/Run 缺少专用 scheduler system context/envelope、原生 scheduled run kind，
    或绑定后 task/profile/gate epoch 已变化时拒绝绑定。存在事实时 rollback 失败关闭；
    production Scheduler capability 与 readiness 继续关闭。

- **2026-08-12**：S4-B 历史定时任务 Runtime adoption preflight（仅非生产）
  - 新增 `227_59_agent_runtime_scheduled_adoption_preflight.sql` 及精确 rollback，提供
    owner-only、只读的历史 `scheduled_tasks` 分类报告；仅返回状态、事实完整性、语义/投递
    target hash 和 adoption candidate，不读取或回显 prompt/target，不修改任务或 Runtime 数据。
    `safe_to_adopt_count` 固定为 0；缺少来源 Action/Attempt/Run 或 Runtime facts 的任务继续由旧
    Owner 保持运行/阻断，待后续事实重建与人工语义确认后再设计可回滚 adoption。

- **2026-08-09**：AR-18 B7-S2-A2 Scheduled Runtime Command Submission（仅 disposable）
  - 新增 `227_30_agent_runtime_scheduled_submission.sql` 及精确 rollback：到期扫描与立即执行
    通过同一原子合同创建 scheduled run、隐藏 scheduler conversation/message anchor、system-scoped
    Runtime Session、Command 和 A1 owner binding；按 scheduled time 或 manual request id 权威
    readback，响应丢失不会重复 Command。
  - Background Worker 仅获得 claim/readback/legacy-owner 窄 RPC，无 Runtime 表直权；没有 Runtime
    profile 的任务保持旧 Scanner/Executor 路径，存在 profile 的任务不会进入旧 credits、ToolLoop、
    stale recovery 或 completion owner。普通对话列表只展示 web/wecom source。
  - submission control 默认 `disabled`，仅 disposable 模式可构造 Runtime Command；本批不实现
    terminal finalizer、next-run 恢复、计费、Projection/Delivery，也不启用 production readiness。

- **2026-08-09**：AR-18 B7-S2-B1-A Scheduled Runtime Terminal Intent（仅事实捕获）
  - 新增 `227_31_agent_runtime_scheduled_terminal_intents.sql` 及精确 rollback：scheduled Runtime
    Run 首次进入 completed/failed/cancelled 时，由 `agent_runs` 同事务 trigger 校验 A2 binding 与
    scheduled run/task/tenant 身份，写入不可变 finalization intent，并把 binding 标记为
    `reconcile_required`；正常完成、失败、取消和 command attempts exhausted 共用同一事实入口。
  - Runtime Worker 仅通过 claim/readback 窄 RPC 获取脱敏终态、binding、schedule hash/snapshot、
    ModelResult、usage 与 cost 投影输入；claim 使用 `SKIP LOCKED`、token lease 和过期恢复。
    intent 表 FORCE RLS 且 Worker 无表直权，迁移回填和 rollback 对缺失、冲突及历史终态事实均
    失败关闭。scheduled task 终态、next-run/counters/result、credits 与投递留待 B1-B；生产开关不变。

- **2026-08-09**：AR-18 B7-S2-B1-B1 Scheduled Runtime Finalization Apply（仅数据库合同）
  - 新增 `227_32_agent_runtime_scheduled_finalization_apply.sql` 及精确 rollback：持有效 intent claim
    的 Runtime Worker 通过单一窄 RPC，按 intent、binding、Runtime Run、scheduled run、task 锁序，
    原子投影 completed/failed/cancelled，并保留不可变 request-id/application-hash receipt。
  - once/周期、快速重试和自动暂停沿用既有 scheduled task 状态；credits/tokens 仅从 Runtime
    Model facts 重算，绝不调用 legacy credit lock/settle 或写用户钱包。结果只持久化 ModelResult
    身份与 content hash，不复制正文或 Secret。Worker 主循环、cron 计算和消息投递留待 B1-B2。

- **2026-08-09**：AR-18 B7-S2-B1-B1.1 Scheduled Finalization Context Readback
  - 新增 `227_33_agent_runtime_scheduled_finalization_context.sql` 及精确 rollback：Runtime Worker
    只有持有未过期 finalization claim token 时，才能只读取得终态基准、冻结 schedule hash、
    task/intent 版本、retry count 与 consecutive failures；applied、not-found 和 fenced 明确区分。
  - Context RPC 在返回前重新校验 scheduled task/run、Runtime binding/Run、execution profile、
    冻结 epoch/revision，且不续租、不更新事实、不返回 prompt、push target、claim token、Secret、
    路径或 Provider payload。新增 apply v2 仅在 Runtime Run 已 terminal 后完成本地 scheduled facts
    收敛；后续 tenant/provider/capability kill 仍阻止新副作用，但不会把 terminal intent 永久卡在
    running/reconcile_required。v1 身份保持不变，Worker 循环与 next-run 规划仍留待 B1-B2。

- **2026-08-10**：AR-18 B7-S2-B1-B2 Scheduled Runtime Finalizer Worker Wiring
  - Runtime Worker 在既有 Command、Run、Action、Child Cancel 与 Reconcile 扫描后，每轮最多领取一个
    scheduled finalization intent；使用冻结的 terminal baseline 和现有 cron 语义确定 next run，再只调用
    `apply_agent_runtime_scheduled_finalization_v2` 原子收敛本地 scheduled facts。
  - PostgreSQL adapter 严格解析 claim/context/apply/readback；响应丢失时先只读确认状态，再以同一确定性
    request id 重放 v2 apply，只有数据库返回 `already_applied` 才确认完成。drain 不再领取新 intent，错误仅
    记录脱敏类型且不阻断后续 Runtime 扫描；未接 legacy Scheduler、钱包、Provider 或消息投递。

- **2026-08-10**：AR-18 B7-S2-B1-C Scheduled Runtime Credit Hard Budget
  - 新增 `227_34_agent_runtime_scheduled_run_credit_budget.sql` 及精确 rollback：Scheduled Run
    只从冻结 profile/run snapshot 取得 `max_credits`，在 Model Attempt prepare 前以 per-run 预算锁
    原子分配；预算耗尽返回类型化 `budget_exhausted`，ModelLoop 在 Provider/Gateway dispatch 前安全
    终结，不创建第二个外部副作用。
  - Scheduled late receipt 的用户扣费不超过本 Run 剩余硬上限；完整 Provider actual 与平台 overage
    写入不可变 reconciliation fact，`adjustment_pending` 重放不会在用户充值后自动补扣。普通非
    Scheduled Run 保留既有完整计费语义。预算、allocation 与 overage 表 FORCE RLS，Worker 无表直权，
    `production_ready=false` 保持。

- **2026-08-10**：AR-18 B7-S2-B1-D1-A Scheduled Delivery Snapshot 与 Intent（仅数据库合同）
  - 新增 `227_35_agent_runtime_scheduled_delivery_intents.sql` 及精确 rollback；不修改 227_28～227_34。
    现有 submission 事务通过 trigger 将经既有租户权限验证的 web、企微用户、企微群和 bounded multi
    规范化为最多 20 个稳定 target key/hash；multi 仅允许一层 leaf 且拒绝重复。web 冻结 org/user，
    企微用户冻结 mapping/corp/user/channel，企微群冻结 target/corp/chat/type，并在 Projection readback
    时重新验证成员、映射和目标仍存在且 active；撤销或重绑后返回 unavailable，不暴露可发送目标。
  - Runtime Run 绑定后追加不可变 binding fact；`apply_agent_runtime_scheduled_finalization_v2` 成功事务
    同步冻结最终 ModelResult identity 和经 run/action/attempt/conversation/org 严格校验的有序 Artifact
    manifest（仅 artifact/hash/role/materialize revision/status），再生成 per-target pending delivery intent；
    不复制结果正文、URL、storage ref、Prompt、Secret、路径或堆栈。Projection 仅获得
    tenant/run-scoped readback 窄 RPC；本批不 claim、不发送 Web/企微、不使用 Redis 作为事实源，legacy
    Run 不生成 Runtime intent，`production_ready=false` 保持。

- **2026-08-10**：AR-18 B7-S2-B1-D1-B Scheduled Runtime Web durable projection receipt + wakeup
  - 新增 `227_36_agent_runtime_scheduled_web_projection.sql` 与失败关闭 rollback；Projection Worker
    只 claim Web intent，以 lease token/state version 单赢家验证 org/user、target/content hash、
    scheduled/runtime/task/finalization 绑定、terminal run/task Web 投影及 active membership。WeCom intent
    保持 pending，receipt/attempt 表 FORCE RLS，Worker 仅有窄 RPC EXECUTE，生产 flag 默认关闭且
    `production_ready=false`。
  - apply 先写唯一 durable projection receipt，再调用现有 `ws_manager.send_to_user` 发送仅含刷新字段的
    completed/failed wakeup；无连接、Redis/WS 异常只写脱敏 attempt/result，不撤销 receipt。lease 过期可从
    projected receipt 恢复，允许崩溃窗口内重复 wakeup，但不会重复数据库投影，也不创建 conversation/message。
    前端按白名单采纳 DB `task_status` 后 `fetchRuns`，once task 不再被 completed handler 短暂硬编码为 active。

- **2026-08-10**：AR-18 B7-S2-B1-D2-A1 Scheduled Runtime WeCom delivery foundation
  - 新增 `227_37_agent_runtime_scheduled_wecom_delivery.sql` 与精确 rollback。D1-A 每个 WeCom intent
    在同一事务初始化一条 Runtime-owned delivery fact、至少一条 text identity item，并为 completed
    Artifact manifest 按 occurrence 逐项冻结 identity-only item；item key 绑定 intent/content、manifest
    ordinal/role 与 source identity/revision，保留同一 Artifact 的合法重复 occurrence，
    不复制正文、URL、object path、Secret 或 bytes。
  - delivery/item/dispatch attempt 三表冻结完整状态与 provider evidence 字段，identity 不可改，attempt
    仅允许 prepared→dispatch_started→accepted/rejected/unknown 及 unknown→accepted/rejected；typed receipt
    写入后不可改。三表 FORCE RLS、仅 owner policy，本批不向 WeCom/Runtime/Projection/legacy Worker
    授予表或函数权限，不实现 claim、reconcile、transport 或真实发送，`production_ready=false` 不变。

- **2026-08-10**：AR-18 B7-S2-B1-D2-A2a Scheduled Runtime WeCom live claim contract
  - 新增 `227_38_agent_runtime_scheduled_wecom_claim.sql` 与精确 rollback。WeCom delivery worker 以现有
    `everydayai_wecom_runtime` + `app.access_kind=worker` 调用窄 RPC，按 request/token/version fence 完成
    `SKIP LOCKED` claim、纯 claim readback、renew 和 lease-expiry takeover；仍无底表权限。
  - claim 与 dispatch gate 按 intent 独立复核 D1/A1、org/member 及 app/smart_robot user/group 当前身份。
    纯 claim readback 不续租或写版本；dispatch-context gate 若在尚无 attempt 时发现 target 失效，会将该
    delivery 标记 unavailable 并取消 pending/retry_wait items。返回值仅含安全身份与 hash，不读取 Secret，
    不创建 attempt、不发起 transport；rollback 允许保留 pristine A1 facts，但存在 A2a 状态即失败关闭。
  - `test_agent_runtime_scheduled_wecom_terminal_fence_postgres_external.py` 逐项验证 applied finalization
    application identity、binding/Run/scheduled-run terminal 漂移会阻断 dispatch，且不影响独立 valid intent。

- **2026-08-10**：AR-18 B7-S2-B1-D2-A2b1 Scheduled Runtime WeCom dispatch prepare/recovery
  - `227_39_agent_runtime_scheduled_wecom_dispatch_prepare.sql` 在 transport 前持久化稳定 provider request、
    idempotency 与 provider revision，并以 current delivery claim fence 原子推进 prepared→dispatch_started。
    prepared 后崩溃仅可由独立 recovery RPC 在原 lease 过期且无 started/unknown/receipt 证据时接管；
    append-only recovery request fact 永久绑定 request ID 与原 attempt/issued claim，重复接管不会改写副作用身份。
  - 新 recovery fact FORCE RLS、owner-only，WeCom worker 仅获窄 RPC EXECUTE 且无表权；精确 rollback
    在 attempt、dispatching 状态或 recovery request fact 存在时失败关闭。PG 测试覆盖 50 并发 recovery/start
    单赢家、旧 token fence、request response-loss/后续接管重放、NULL 零事实变更和 split identity 稳定冲突。

- **2026-08-10**：AR-18 B7-S2-B1-D2-A2b2a1 Scheduled Runtime WeCom dispatch outcomes
  - `227_40_agent_runtime_scheduled_wecom_dispatch_outcomes.sql` 新增 append-only outcome request ledger 与单一
    WeCom worker RPC，把 `dispatch_started` 原子推进为 accepted/rejected/unknown，并逐 ordinal 更新 item；仍有
    后续 pending/retry_wait 时保留 current claim，否则同事务聚合 completed/partial/failed，unknown 则进入
    ambiguous/unknown 并清 claim，不能被普通 claim 重派。
  - accepted/rejected receipt 仅接受 app/smart_robot 的平铺 typed metadata allowlist，数据库用带 domain、outcome、
    receipt type/code、provider identity/revision 的 canonical envelope 重算 SHA-256；unknown 不接受 receipt metadata。
    start 后 live context 或 lease 时钟变化不会丢弃已发生的外部事实，但 claim identity/version takeover 仍会 fence。
    FORCE RLS、无表权、精确 rollback；本批不接 transport、Secret、真实企微或 unknown reconcile/cancel。

- **2026-08-10**：AR-18 B7-S2-B1-D2-A2b2b1a Scheduled Runtime WeCom UNKNOWN reconcile claim
  - `227_41_agent_runtime_scheduled_wecom_reconcile_claim.sql` 新增 owner-only、FORCE RLS、append-only
    reconcile claim request ledger，并向 `everydayai_wecom_runtime` worker 仅开放 claim/renew/pure readback RPC。
    claim 只接管 delivery/item 均已到期的 unknown 或 reconcile_required 与 frozen unknown/ambiguous attempt，复用
    delivery 既有 reconcile lease 字段，不创建 attempt、不 dispatch、不修改 provider identity；过期 lease 可 takeover，
    旧 request 永久 readback 原 token/attempt 且返回 fenced。统一 global request lock/guard 使 reconcile ledger 与既有
    delivery claim、prepared recovery、outcome request namespace 双向并发互斥。精确 rollback 在 ledger 或 active reconcile fact 存在时
    失败关闭；本批不实现 accepted/rejected/still_unknown 结果、cancel、transport、Provider 或 Secret 访问。

- **2026-08-10**：AR-18 B7-S2-B1-D2-A2b2b1b-1 Scheduled Runtime WeCom initial/continuation claim v2
  - 新增 `227_42_agent_runtime_scheduled_wecom_continuation_claim.sql` 与精确 rollback，并以 worker-only
    `claim_agent_runtime_scheduled_wecom_delivery_v2` 取代 v1 的可执行权限。v2 同时覆盖互斥的 initial claim
    （delivery 尚无 attempt）与 continuation claim（历史 attempt 全部 terminal accepted/rejected，严格下一 due item
    尚无 attempt）；两者都只建立 delivery lease，不创建 dispatch attempt，不重建或 resubmit 历史 attempt。
  - 新 append-only continuation claim request ledger 永久绑定 request/intent/item、claim kind、worker、token、lease
    与 delivery/item versions，支持 response-loss readback；FORCE RLS、owner-only 底表和扩展后的 global request guards
    保证其 request UUID 与 227_38～227_41 delivery claim、prepared recovery、outcome、reconcile ledger 双向互斥。
    live target 失效时，initial 复用安全 unavailable cancellation；continuation 仅取消剩余未发送 item，再按包含
    cancelled item 的 227_40 规则聚合 completed/partial/failed，清除 claim 且不修改历史 attempt。
  - migration 撤销 `everydayai_wecom_runtime` 对 `claim_agent_runtime_scheduled_wecom_delivery_v1` 的 EXECUTE；rollback
    在不存在 v2 ledger facts 时删除本批对象、精确恢复 227_41 namespace guard 定义并恢复 v1 EXECUTE。本批不实现
    reconcile accepted/rejected/still_unknown result、transport、Provider、Secret、Redis、retry 或 resubmit。

- **2026-08-10**：AR-18 B7-S2-B1-D2-A2b2b1c Scheduled Runtime WeCom still_unknown reconcile result
  - 新增 `227_43_agent_runtime_scheduled_wecom_reconcile_still_unknown.sql`、精确 rollback、append-only
    reconcile result request ledger 与 worker-only result RPC。请求永久绑定 227_41 current claim、intent/item/attempt、
    reconcile token/worker、delivery/item versions 及 provider identity；仅接受 `still_unknown`，不实现 accepted/rejected。
  - readback type/code/metadata 复用 227_40 typed allowlist，禁止自由文本，并由数据库以独立 domain canonical envelope
    重算 SHA-256。成功后 attempt 继续保持 unknown/ambiguous、`unknown_at` 不变；item 与 delivery 原子变为
    `reconcile_required`，清 reconcile lease，并以同一时间设置 5～86400 秒后的 `next_attempt_at`，不创建 attempt、
    dispatch 或 resubmit。外部 readback 后 target drift 或 lease 刚过期仍记录，token/worker/version takeover 则 fence。
  - result request UUID 双向加入 227_41/42 全局 namespace guards；FORCE RLS、worker 无表权、response-loss readback
    与精确 rollback 均由 PG 契约覆盖。本批不包含 cancel、transport、Provider 调用或 accepted/rejected resolution。

- **2026-08-10**：AR-18 B7-S2-B1-D2-B1d2b1d Scheduled Runtime WeCom definitive reconcile result
  - 新增 `227_44_agent_runtime_scheduled_wecom_reconcile_definitive.sql`、精确 rollback、append-only
    definitive request ledger 与 worker-only result RPC。请求绑定 227_41 current claim、intent/item/attempt、
    reconcile token/worker、delivery/item versions 与 Provider identity，只接受 typed `accepted|rejected`；canonical
    readback hash 与 metadata allowlist 复用 227_43/227_40，底表 FORCE RLS 且 worker 无表权。
  - 成功 readback 将 frozen unknown/ambiguous attempt 终结为 accepted/rejected receipt_recorded，保留 `unknown_at` 并
    设置 receipt evidence/`resolved_at`；item 映射为 accepted/failed，清 ordinary/reconcile claims。存在后续
    pending/retry_wait item 时 delivery 回到 pending，由 227_42 v2 领取真实下一 item；否则按 accepted 与
    failed/cancelled 聚合 completed/partial/failed。readback 后 target/context drift 或 lease 过期仍记录，
    token/worker/version takeover fence；不包含 still_unknown、cancel、transport、Provider 调用、普通 retry/resubmit。
  - definitive request UUID 双向加入 227_41～43 与 legacy/continuation namespace guards；rollback 无事实时恢复
    227_43 guard 定义，有事实时失败关闭。并发 accepted/rejected、readback/conflict、NULL/hash/metadata 零事实、
    v2 continuation、ACL/RLS/search_path 与 rollback 均由 disposable PostgreSQL 契约覆盖。

- **2026-08-10**：AR-18 D2-C0a Scheduled Runtime WeCom authoritative dispatch version readback
  - 新增 additive `227_45_agent_runtime_scheduled_wecom_dispatch_version_readback.sql` 与无事实精确 rollback；不修改
    227_39/40 身份、状态机或持久事实。worker-only prepare/start/readback v2 在调用 v1 的同一事务内，以
    intent/item/attempt、Provider identity/revision 和 current claim/lease/worker 联合绑定的窄查询附加当前
    `delivery_state_version` / `item_state_version`，fenced/not_found 不返回版本或业务事实。
  - prepare v2 的版本可直接调用 start v2，start v2 的版本可直接调用 227_40 outcome v1；response-loss 重放返回
    同一 attempt 与当前权威版本。migration 撤销 WeCom runtime 对三个 v1 入口的 EXECUTE，仅开放 v2，仍无底表
    SELECT；rollback 删除 v2 并精确恢复 v1 权限。静态与 disposable PostgreSQL 测试覆盖闭环、identity drift、
    ACL/RLS/search_path、legacy 拒绝及 rollback/reapply/rollback。

- **2026-08-10**：AR-18 B7-S2 D2-C1a Scheduled Runtime WeCom typed repository boundary
  - 新增 frozen dataclass/enum port 与 `PostgresScheduledWecomDeliveryRepository`，以现有
    `everydayai_wecom_runtime`、`app.access_kind=worker` scoped client 仅调用窄 RPC。完整覆盖 delivery v2
    initial/continuation claim、prepared recovery、227_45 prepare/start/read v2、227_40 dispatch outcome v1，
    以及 227_41～44 reconcile claim/renew/read、still_unknown 和 definitive result；不接 transport、循环 Worker
    或 production composition。
  - 所有参数逐字段映射并保留 request/worker/lease/reconcile token、delivery/item version、Provider request、
    idempotency hash 与 revision。输出按 outcome 使用精确字段 allowlist 和 typed receipt metadata；缺字段、额外
    Secret/credential/payload/free-text 字段、非法 UUID/版本/状态均稳定失败关闭。prepare/start/read 只使用
    227_45 v2 权威版本，禁止表直读和本地版本推算；仅有 durable identity/readback 的写 RPC 在连接响应丢失后
    原参数重放，lease renew 不自动重放。

- **2026-08-10**：AR-18 D2-C0b Scheduled Runtime WeCom safe dispatch payload readback
  - 新增 additive `227_46_agent_runtime_scheduled_wecom_dispatch_payload.sql` 与仅删除该函数的精确 rollback。
    `read_agent_runtime_scheduled_wecom_dispatch_payload_v1` 使用当前 claim/lease/worker 与 delivery/item BIGINT
    version fence 调用既有 227_38 live dispatch context，再绑定同一 scheduled run、task、applied finalization、delivery
    content、intent、当前有序 item 与冻结 `agent_model_results` identity。completed text 的正文仍只返回
    `scheduled_task_runs.result_summary`，但 SQL 会从 content 的精确 `model_result_id` 在内部重算
    `_agent_runtime_scheduled_safe_summary` 并要求逐字相等；原始 model text/structured content 不进入响应或异常。
    RPC 也不返回 target snapshot、内部 mapping/user/target ID、Secret、URL、路径或 artifact bytes。
  - 成功响应仅含稳定内容/target hash、当前版本、`message_type=text`、bounded safe text 和确定性 payload hash；
    transport target 仅投影 App `org_id + corp_id + wecom_userid` 或 Smart Robot `org_id + chatid`，且经 live
    context 验证的 `org_id` 纳入 canonical payload hash。artifact identity、failed、
    cancelled 与其它 non-completed content 只返回固定 payload-free `unsupported`，不生成通知文案。RPC 会继承
    227_38 已批准的 pre-attempt unavailable cancellation，但不会合并或透传完整 context。
  - 本批仅建立 inactive database contract，不接 repository orchestration、transport、循环 Worker 或 production。
    D2-C0b 阶段曾因缺少 durable unsupported terminalization/no-claim policy 而阻塞 Worker 激活；后续 227_47
    已关闭该生命周期缺口，但不会追溯改变本批的 inactive 范围。
- **2026-08-10**：AR-18 D2-C0c Scheduled Runtime WeCom unsupported durable terminalization
  - 新增 additive `227_47_agent_runtime_scheduled_wecom_unsupported_terminalization.sql` 与精确 rollback。
    `terminalize_agent_runtime_scheduled_wecom_unsupported_item_v1` 仅在当前 227_42 v2 claim、item、lease、worker
    与 delivery/item version 全部匹配且 227_46 返回四种固定 `unsupported` reason 之一时，原子地把当前未发送 item
    标记为 `cancelled`；调用方不能提交 reason 或正文，也不会创建、重派或改写 dispatch attempt。
  - owner-only FORCE-RLS append-only ledger 冻结 request 与原 claim identity、server-derived reason、结果状态、权威
    version 和时间，支持响应丢失后的同参数 `readback`；同 request 异参冲突，且新 ledger 双向加入完整 Scheduled
    WeCom global request namespace。仍有 pending/retry item 时立即释放 delivery 为 `pending` 供 fresh strict continuation
    claim；否则按 accepted-count 既有语义聚合为 completed/partial/failed，避免 unsupported reclaim loop。
  - rollback 在 ledger fact 或带本契约固定 reason 的 cancelled item 存在时失败关闭，不删除事实；无事实时仅撤销
    227_47 对象并恢复 227_44 predecessor guards。本批仍不接 provider/transport、循环 Worker 或 production。
- **2026-08-10**：AR-18 D2-C1b1 Scheduled Runtime WeCom typed payload/unsupported repository
  - `ScheduledWecomDeliveryRepositoryPort` 与 PostgreSQL adapter 新增 frozen typed 227_46 payload readback 和 227_47
    unsupported terminalization receipt；仅通过 worker-scoped RPC 显式传递当前 claim/token/worker/delivery/item
    version fence，不读表、不推算版本、不调用 provider。payload parser 只接受 App `org_id/corp_id/wecom_userid`
    或 Smart Robot `org_id/chatid` 的精确 target，以及 bounded safe text、固定 revision 和 hash identities；unsupported、
    unavailable、not-found 与 fenced 不会被转换成 transport payload。
  - 227_47 durable mutation 仅在 psycopg connection response-loss 后原参数重放一次，并严格验证 request/intent/item、
    cancelled item、pending/partial/failed delivery 与权威结果版本；227_46 read 不自动重放。本批仍不新增 migration、
    Worker/orchestration/transport 或 production activation。
- **2026-08-10**：AR-18 D2-C0d Scheduled Runtime WeCom stale started recovery
  - 新增 additive `227_48_agent_runtime_scheduled_wecom_started_recovery.sql` 与精确 rollback。worker-only
    `recover_agent_runtime_scheduled_wecom_started_dispatch_v1` 只选择 lease 已过期、delivery/item 仍为 dispatching、
    attempt 仍为 `dispatch_started/external_request_started` 且无 receipt/unknown/resolved 的精确候选；同时验证原
    claim/token/worker、provider identity/revision 与 prepare→start 权威版本。external dispatch 一旦开始，后续 target/org
    drift 或 revocation 不能证明请求未发送，因此不会阻止 UNKNOWN 收敛。
  - RPC 在同一事务生成与 recovery request 不同的内部 outcome UUID，调用既有 227_40 UNKNOWN/no-evidence 状态机，
    再写 owner-only FORCE-RLS append-only ledger；响应丢失按 recovery request durable readback。成功后仅可进入 227_41
    reconcile，不可被 ordinary claim、prepare/start 或 transport 重派，也不创建第二 attempt。
  - 新 ledger 双向加入完整 Scheduled WeCom request namespace；rollback 有 recovery facts 时失败关闭，无事实时恢复
    精确 227_47 guards 并只删除 227_48 对象。本批不接 Worker/orchestration/provider/transport/production，flags 保持关闭。
- **2026-08-10**：AR-18 D2-C0e Scheduled Runtime WeCom UTF-8 payload hash
  - 新增 additive `227_49_agent_runtime_scheduled_wecom_unicode_payload.sql`，以同签名替换 227_46 read RPC 的成功 hash
    实现并返回 `payload_revision=2`。safe summary 与最小 transport target 先按 UTF-8 分别计算 SHA-256，随后仅将 64hex
    digest 和冻结 source/content/result/target、org/channel/provider、delivery/item/version identities 交给既有 ASCII-only
    canonical helper；不放宽全局 helper，也不返回 raw model、target snapshot、Secret 或路径。
  - rollback 恢复精确 227_46 revision 1 函数并删除 v2 helper；Python parser 为协调数据库回滚仅兼容 revision 1/2，
    其它 revision failure-closed。RPC ACL 仍仅开放给 `everydayai_wecom_runtime`，无新增表权限、Worker、transport、
    provider 或 production activation。
- **2026-08-11**：AR-18 D2-C1b2 Scheduled Runtime WeCom Smart Robot direct orchestration
  - 新增 Runtime-owned one-shot service，输入仅为 router 已取得的 typed `DeliveryClaim + DispatchPayload`；Smart channel、
    target 和 claim/payload intent/item/version 在任何 prepare/start/transport 前失败关闭。服务不拥有 global claim、payload
    read、unsupported terminalization、App dispatch、循环 Worker 或 production composition。
  - routed item/provider identity 在 await prepare 前建立同进程 single-flight，共享内部 task 完整持有
    prepare→start→send→outcome，所有调用方仅 shielded await；调用方取消不会取消 owner、释放 flight 或允许重复发送。
    仅 fresh prepare/start owner 可调用一次 `send_proactive_typed(markdown,{content: safe_text})`；readback 不发送。ACK/rejection 生成与 227_40 SQL
    canonical 完全一致的 allowlisted receipt，NOT_STARTED、UNKNOWN、异常或取消在 durable start 后均保守记录 UNKNOWN。
    transport/internal task 取消路径 best-effort shield 持久化后重抛，失败时保留 `dispatch_started` 供 227_48 恢复。
  - 本批不组合 prepared/started recovery；跨进程安全仍依赖 PostgreSQL fresh outcome fence，进程内 50-way duplicate 由
    single-flight 收敛为一次 transport。production flags 保持关闭，无 migration、provider credential 或真实外呼。
- **2026-08-11**：AR-18 D2-C1f.0 Scheduled Runtime WeCom Smart Robot tenant transport resolver
  - Smart dispatch 改为注入窄 `SmartRobotTransportResolverPort`，在 routed claim/payload/target 校验后、任何 prepare/start/facts
    写入前按 canonical `target.org_id` 解析 transport。解析异常、缺失、租户不匹配或断连均返回 typed `UNAVAILABLE`，零
    prepare/start/send/UNKNOWN 副作用；解析取消继续传播。解析在既有 item/provider single-flight owner 内执行，50-way duplicate
    只解析并发送一次，解析成功后的 post-start exception/cancellation→UNKNOWN 合同不变。
  - 新增 `backend/services/wecom/scheduled_smart_transport.py` 可信 adapter，仅调用注入的 `get_ws_client(org_id)`，并要求 client
    的 `org_id` 精确匹配、`is_connected is True` 且 typed sender callable；不读取 Secret、不缓存 client、不按 chatid 或全局默认
    选路。Router 将 Smart `UNAVAILABLE` 映射为 route `UNAVAILABLE`；本批不接 runner/composition、App、migration、环境或生产。
- **2026-08-11**：AR-18 D2-C1c Scheduled Runtime WeCom App direct orchestration
  - 新增 Runtime-owned one-shot App service；输入仅为 router 后续提供的 typed `DeliveryClaim + App DispatchPayload` 与
    显式注入的非敏感 `org_id/corp_id/positive agent_id + WecomAppOutbound-compatible transport` binding。payload target 的
    org/corp 在 prepare 前逐字匹配 binding；Smart、fence drift、无效 binding 均零持久化和零 HTTP 副作用。本批不新增
    config table/resolver、credential material、router/composition、Worker、migration 或 production flag。
  - App-specific provider identity 绑定冻结 item/payload/provider revision 及 UTF-8-safe tenant binding hash，并在 await
    prepare 前注册同实例 single-flight；共享内部 task 完整持有 fresh prepare→start→一次 `send_typed`→durable outcome，
    调用方取消不释放 flight。发送体固定为 `touser + msgtype=text + agentid + text.content`。
  - ACK 仅记录 allowlisted provider message id 与 errcode；provider rejection/partial rejection 仅记录 errcode；receipt hash
    与 227_40 SQL canonical 完全一致。DB start 后 NOT_STARTED、UNKNOWN、异常或 internal cancellation 均收敛 UNKNOWN；
    cancellation outcome 持久化失败时保留 `dispatch_started` 供 227_48。现有 tenant config binding composition 与全局
    router 仍属于后续批次，production 保持 inactive。
- **2026-08-11**：AR-18 D2-C1d Scheduled Runtime WeCom unified router
  - 新增 Runtime-owned 单次 router：同一 request 在进程内 single-flight 后只执行一次 delivery claim 与一次 safe payload
    read，并按 typed outcome 路由 unsupported、Smart Robot 或 App。unsupported 使用由 claim/item/reason 派生的稳定
    request UUID 调用既有 227_47 durable terminalization；unavailable、not-found、fence drift 与配置缺失均在
    prepare/start/transport 前返回 typed deferred 结果，不创建 attempt。
  - App 路由只依赖窄 `AppBindingResolverPort.resolve_app_binding(org_id, corp_id)`；返回值必须是已构造且不暴露 Secret 的
    `ScheduledWecomAppBinding`，并在 dispatch 前精确核对 org/corp、positive agent id 与 typed transport。Router 不读取
    配置、不解密 Secret、不接 legacy sender、不拥有 recovery/reconcile loop，也未加入 production composition。
  - concrete binding adapter、Worker composition、prepared/started recovery 与 227_41～44 reconcile loop 当时仍属于后续批次；
    production flags 保持关闭。
- **2026-08-11**：AR-18 D2-C1e-B Scheduled Runtime WeCom App tenant binding adapter
  - 新增可信 `ScheduledWecomAppBindingResolver`：在 actorless exact-org `WORKER` scope 下调用仅
    `everydayai_wecom_runtime` 可执行的 227_50 `wecom.app` façade，并通过现有 `AsyncSecretBundleResolver` 解密组织级
    `wecom.corp_id/oauth_agent_id/oauth_agent_secret`。UUID、expected corp、canonical positive agent id、exact secret payload、
    organization source 与 config versions 任一不合法均 failure-closed；取消保持传播。
  - Secret 仅进入不可序列化、不可通过 dataclass/`vars()` 展开的 slot-only 私有 material，并由五分钟有效的 exact-match
    credential backend 经现有 production-ready `CredentialBroker`/lease consumer 交给注入的 per-org access-token manager；
    opaque handle/revision 只散列 org/corp/agent/config versions，Runtime token cache 以 org+revision 隔离配置轮换，legacy 无
    revision 调用保持原 key/行为。构造器强制注入 async
    database、material service、token manager、共享 outbound HTTP client 与真实 credential audit sink，不提供 global/default、
    callback credential、no-op audit 或隐式 HTTP lifecycle。Router/composition/runner、migration、legacy worker 与 production
    flags 均未改动。
- **2026-08-11**：AR-18 D2-C1f.2c Scheduled Runtime WeCom PREPARED recovery router
  - `ScheduledWecomRouter.recover_prepared_once` 以独立 request single-flight 串联 durable prepared recovery、专用 frozen
    payload readback 与现有 Smart/App recovered dispatch；只接受原 attempt 的 payload versions 和完整 provider identity，
    同时保留 recovery current fence 作为 start 权限，不创建新 attempt、不走 fresh dispatch 或 started recovery。
  - unsupported、unavailable、fenced、payload/target/identity drift 与 App exact binding 缺失均在 start/send 前失败关闭；已进入
    dispatch service 后的 UNKNOWN、取消和持久化异常仍由原 service 合同负责，Router 不吞异常、不普通重派。本批新增独立
    Router 恢复测试，不新增 migration、Worker/composition、配置、Secret、transport 或 production activation。
- **2026-08-11**：AR-18 D2-C1f.2d Scheduled Runtime WeCom Worker 安全循环
  - 新增独立 `ScheduledRuntimeWecomWorker`，每个 pass 固定执行 started recovery → prepared recovery → fresh dispatch；任一阶段异常或
    非 EMPTY 恢复结果都会终止本轮，started recovery 永不发送，prepared recovery 仅沿原 attempt 恢复路径发送。
  - 每阶段使用独立 canonical request UUID；无 durable identity 的 UNAVAILABLE 返回未处理并进入有界 poll，带 identity 的
    unavailable/config 结果按已处理结束本轮。实例级 pass lock 与唯一 loop task/generation ownership 防止 public `run_once`、
    stop-during-pass 和并发 restart 形成重叠发送；`stop()` 会唤醒并等待所属 loop 安全退出。该 Worker 尚未接入旧
    `WecomDeliveryWorker`、runner、systemd、env 或 production composition。
- **2026-08-11**：AR-18 D2-C1f.2e Scheduled Runtime WeCom composition boundary
  - 新增非 owning composition builder：以 actorless/orgless `WORKER` scope 装配 Scheduled WeCom PostgreSQL Repository、
    exact-org Smart resolver、复用现有租户配置的 App binding resolver、Smart/App dispatch、Router 与安全优先级 Worker；
    App resolver 单独持有注入的 raw async database，以便按目标 org 建立 exact-tenant scope。
  - 默认凭证审计为严格白名单 journal sink，只接受 canonical tenant UUID、opaque `wecom-app` handle/revision、固定 provider/
    purpose、Broker outcome 与 aware timestamp；不记录 Secret、token、payload、路径或异常正文。Builder 不启动组件、不拥有或
    关闭 DB/HTTP/WS 资源，也未接入 runner、env、systemd、migration 或 production activation。
- **2026-08-11**：AR-18 D2-C1f.3a Scheduled Runtime WeCom reconcile tenant identity
  - 新增 additive `227_52_agent_runtime_scheduled_wecom_reconcile_org.sql`，只替换既有 reconcile JSON helper，令
    claim/read/renew 从已锁定并读取的 delivery 返回不可变 `org_id`；rollback 精确恢复 227_41 输出，不删除事实或改变
    RPC、ACL、RLS、角色与状态。Python claim/parser/repository 同步 canonical UUID 与跨租户 identity readback fence；未新增
    channel、reconcile service、配置、开关、Provider、Secret 或生产接线。
- **2026-08-11**：AR-18 D2-C1f.3c Scheduled Runtime WeCom reconcile worker
  - 新增最小 reconcile service，复用 227_41～227_44：Smart 仅按 claim `org_id` 读取 exact resolver 的内存 ACK cache，
    ACK/REJECTED 写 definitive，其余写固定 60 秒 still-unknown；App 不读取 credential/HTTP/transport，直接写 typed
    `readback_unsupported`，未知 provider identity 前缀失败关闭。Python readback hash 与 227_43 SQL canonical 字段一致，
    result request UUID 从 claim identity 稳定派生以支持响应丢失重放。
  - Worker 优先级调整为 started recovery → reconcile → prepared recovery → fresh dispatch；高优先级非空或异常均阻断
    下游普通派发。Composition 向 Worker 注入同一 Repository 与现有 Smart readback resolver；未修改 migration、表、RPC、
    Admin API、tenant config、App credential path 或 production activation。
AR-17.3 remediation adds a worker-scoped `PostgresSpecialistRepository` composition path. Durable provider, cost, callback, artifact, resource and Child Run facts are persisted before terminal results are exposed. Local data, file analysis and ERP pagination use separate services; isolated HTTP and disposable PostgreSQL harnesses exercise the non-production contracts. Production remains inactive.

The current AR-17.3 remediation adds additive 226_08–226_18 lanes for strict fact idempotency, application-owned atomic provider/cost/ActionResult finalization, non-terminal reconciliation lease release, Child Run v2 readback/terminal aggregation and ordinal idempotency, cancel parity, database-fact-based ERP sync recovery with durable submission identity, ownership/version fencing and same-phase conflict detection, and exact worker RPC numeric overloads. The isolated PostgreSQL harness now drives the formal ActionLoop/Resolver/SpecialistExecutor/Postgres repository chain and real 50-connection races. Production activation remains unchanged and AR-17.3 is not accepted until the complete end-to-end matrix is closed.

- **2026-08-04**：T17.3-A 新增 227_06 tenant/provider/capability kill control、owner fence 与不可变 kill audit facts；提供 tenant-scoped admin CAS/audit status RPC，以及仅按 execution token 读取 owner fence 的 Worker 窄 RPC。该批不接入 ingress、claim、dispatch 或 lease 行为，production flags 继续关闭；rollback 在任何新事实存在时失败关闭。
- **2026-08-04**：C4.1 新增 227_13 additive ingress compatibility lane；RuntimeIngress 在确认 v5 capability 后使用 v5，保留 v4/v3 fallback。v5 保留 tenant kill-epoch ingress fence、rollout、anchor、版本与 effective toolset facts，移除 42 binding ready 总门禁且不修改 binding facts；v5 rollback 仅撤销自身函数/权限，存在 ingress facts 时失败关闭。
- **2026-08-04**：C5.1-R 新增 227_14 owner transition additive lane；Web/WeCom Runtime ingress 在未 accepted 时通过受控 RPC 恢复 Actor owner，在 created/already_exists 时通过同事务 transition 保持 Runtime owner；不启用 Worker、provider binding、生产 flags，也不修改 227_01～227_13。
- **2026-08-05**：C6.2-A 新增 227_15 Owner RPC ACL 收口；Web 仅可调用原子 owner-transition ingress，WeCom 仅可调用 v6 enqueue，两者保留 capability readback，但不能直接调用 raw v5 或 restore/mark helper。Rollback 只恢复 227_14 后的 ACL，不修改 Runtime facts 或任务 Owner 状态。
- **2026-08-05**：C7-B1 完成 production flags-off 安装配置闭环：Runtime/WeCom v3
  模板与严格白名单校验、四 Worker release revision 一致性、仅四个新 unit + wrapper 的
  失败关闭安装，以及不迁移、不启停、不切 Owner 的互斥发布路径；production flags 仍关闭。
