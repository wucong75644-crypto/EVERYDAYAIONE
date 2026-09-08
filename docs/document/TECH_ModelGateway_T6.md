# T6：ModelGateway 并发控制与生产收口

## 基座与范围

任务分支 `codex/task/20260908190647-model-gateway-t6`，基座 `c789a6ccca624571b569aefea8f811165be627a0`。
按用户提供的 T1～T5 已验收前提执行；最新 `origin/main` 包含以下实现：

| 阶段 | 主要提交 | 已复核的实现和测试 |
| --- | --- | --- |
| T1 | `b07f8cf1` | ModelCallRequest、进程内 Gateway、Web/Actor 共享 Chat 内核 |
| T2 | `c55bb414` | 生产辅助 Chat 调用迁移到 Gateway，保留 adapter 工厂和兼容导出 |
| T3 | `0a1b5dfa` | request/attempt 生命周期、日志与 Langfuse 采样 |
| T4 | `711903c2`、`fe9f6e78` | deadline、cancel token、pending Provider task 清理 |
| T5 | `e894ca8a` | Gateway 唯一执行 retry/fallback；Actor 和 Web 保留原有终态、usage 和计费职责 |

本任务约束 Gateway 管理的 Chat stream（包括通过 Chat 生成计划、摘要等辅助调用）。图片/视频生成、Embedding、ERP API 和其他外部请求保留原有机制。未新增 Gateway 进程、Redis 模型调度器、SamplerActor 或数据库调度。

## 修改前的并发机制及证据

| 层级 | 当前机制 | 证据 |
| --- | --- | --- |
| 入口任务额度 | Redis SET 按企业/用户限制活跃任务 15、单对话 5；这是任务额度，名称中的 global 仍按用户分键，不是全站模型配额 | `api/routes/message.py`、`services/task_limit_service.py`、`core/config.py` |
| Conversation Worker | 默认最多 5 个 execution task；serial 按 conversation 去重，branch 按 task 去重；每次扫描最多 100 条；默认 10 秒关闭排空 | `services/conversation_worker.py` |
| Actor 执行权 | DB claim RPC、execution_token、90 秒租约、最多 3 次任务领取；运行时每 5 秒续租 | `services/conversation_execution.py`、`services/conversation_runtime.py` |
| execution_task | 模型/工具执行与 ownership_lost 等待协程竞争；失去所有权取消 execution_task；续租协程独立运行 | `ConversationExecutionService._execute_until_lost()`、`_renew_loop()` |
| 定时任务 | DB `claim_due_tasks`，每批 5；Scanner 的 Semaphore(3) 控制任务执行；后台 create_task 不阻塞扫描 | `services/scheduler/scanner.py` |
| 其他 asyncio 工作 | Chat 读取 chunk、工具并行、后置任务各有独立协程；ERP 分页 sandbox_api_concurrency=10、KIE 媒体轮询限制只控制对应外部工作 | `services/handlers/chat/execution_engine.py`、`services/agent/tool_executor.py`、`services/background_task_worker.py` |
| Provider client | DashScope/OpenRouter/KIE 按 adapter 懒建 httpx client，Google 使用现有 SDK/client；这些 client 的超时/连接池不能合并限制不同会话的模型请求 | `services/adapters/{dashscope,openrouter,kie,google}` |
| 熔断 | 已有 Provider 级进程内 CLOSED/OPEN/HALF_OPEN；60 秒窗口失败 3 次打开 30 秒；工厂/路由过滤不可用 Provider，但预先创建的 adapter 缺少 dispatch 时复查 | `services/circuit_breaker.py`、`services/adapters/factory.py` |
| 服务进程 | API uvicorn 2 workers；Conversation Actor 1 个 Python 进程；企微连接服务 1 个 Python 进程 | `deploy/everydayai-backend.service`、`everydayai-conversation-actor.service`、`everydayai-wecom.service` |

未读取生产凭据或以生产流量压测。进程数量是仓库 service unit 的部署契约，不声称是本次实测的生产进程快照。

## 最终并发边界及配置

- 新增唯一配置 `MODEL_GATEWAY_MAX_CONCURRENCY=5`，必须为正整数；未配置即启用默认值，非法值由 Settings 拒绝。
- 边界是**单个服务进程/事件循环中的共享 Gateway**，所有生产调用通过 `get_model_gateway()` 使用同一准入器。不同会话、Provider、模型、企业和任务共用这 5 个槽。
- 默认 5 对齐现有 Actor Worker 的任务并发，作为保守的本地保护值；它不是 Provider 账号配额或压测容量结论。原先没有此限制的 API 辅助调用在高峰时会开始排队。
- 没有单 Provider 或单模型独立上限：没有账号配额或负载证据支持这些规则。Provider 保留原有熔断器和模型选择策略，HALF_OPEN 也保留既有语义。
- API 两个 worker 各有 5 槽，所以该 unit 理论上可有 10 个 Gateway stream；Actor 和企微各自有独立的 5 槽。增加进程会增加总体上限，不能把配置解读成跨进程或跨机器上限。
- 超额请求由 asyncio.Semaphore 按等待顺序准入；不新增持久排队表或额外队列容量配置。所有等待有 deadline，等待项支持直接取消。
- 仅实际 Provider stream 持有槽位。创建会话、工具执行、retry 路由不持有槽；每个后续模型 attempt/工具回合重新准入。同一会话不允许同时消费两个请求，明确返回 `MODEL_GATEWAY_SESSION_BUSY`，避免共享结果状态被覆盖。

## 排队、取消、timeout 与释放

1. 请求 attempt 的 deadline 在入队前建立，**排队和 Provider stream 共用这次 deadline**；不会获槽后重新获得完整 timeout。继续取执行预算 remaining 与 deadline 的较小值。未显式设置时复用现有模型超时解析（普通 Chat 60 秒、专用推理模型 120 秒，均可由原配置覆盖）。
2. 排队到期返回 `ModelGatewayTimeoutError(phase="queue")`，对外仍为 `MODEL_TIMEOUT`，不换模、不记 Provider 失败；没有真正调用 Provider。执行阶段 timeout 保留 T4 的 first_chunk/stream/provider 分类及原熔断记录语义。
3. 支持 Task.cancel、asyncio.Event、现有轮询 cancel token、session.close；取消优先于同时完成的准入或已就绪 chunk，不向调用方交付取消后的数据。
4. 等待的 acquire task 总会被取消并收尾。若取消与获槽同时完成，取得的 lease 在 finally 中归还；清理期间再次取消也不能跳过归还。lease.release 幂等，避免关闭与 stream finally 重复增加 semaphore 容量。
5. 正常结束、Provider error、timeout、cancel 和迭代器提前关闭均在 finally 释放槽位；关闭时先停止在途 `__anext__` 并关闭 Provider iterator/client。即使消费者停在 yield 后，服务关闭也会关闭 Provider 并归还槽位。
6. Gateway.close 拒绝新会话，关闭已登记的会话，取消等待准入和 retry 路由；API lifespan、Actor 进程 finally、企微关闭流程均接入。任务终态、租约回收、Worker 排空和积分继续由原所有者处理。
7. Python 协程和现有 Provider client 按 asyncio 取消契约收尾。没有宣称可以中断任意不响应取消的第三方代码；systemd 原关闭时限继续作为进程级兜底。

## 熔断与 T3 事件

- 入队前及获槽后复查现有 Provider 熔断器；排队期间每 50ms 检查一次本地状态，Provider 变为 OPEN 时及时以 `ProviderUnavailableError` 退出，不等待其他 Provider 的长流结束。
- 主 Chat 继续使用 T5 的 record_breaker hook，每次 attempt 只记一次；辅助模型调用也向同一个已有 Provider breaker 记录成功及可记录的失败。队列等待超时、取消、业务错误、已 OPEN 的拒绝均不增加 Provider 失败计数。
- 原 STARTED/FIRST_CHUNK/COMPLETED/FAILED/CANCELLED/RETRY_STARTED 保留。attempt 事件增加 `queue_wait_ms`，拒绝时增加 `rejection_reason`。
- `concurrency_rejected`：队列等待到期，reason=queue_timeout。
- `provider_rejected`：reason=circuit_open；后续是否换模仍由原 T5 策略决定。
- `provider_overloaded`：基于已有分类或结构化 HTTP 429/503/529；不匹配异常正文。
- `request_failed`：消费中的逻辑请求最终失败，记录标准化 error_code 和 stop_reason，区别于可继续 retry 的中间 attempt FAILED。该事件不重复创建 Langfuse generation 或计费记录。同步 factory 失败继续保留原 STARTED/FAILED 契约。
- 所有新增字段都是时间、关联标识、错误码或固定原因；不记录 prompt、消息、响应内容、API key 或原始异常正文。沿用既有异步 best-effort 采样通道。
- 事件消息采用 `ModelGateway sampling event {JSON}`，JSON 只包含 `log_fields()` 的安全字段，同时保留 bind 字段供已有结构化 sink 使用。这样现有仅输出 message 的文件/控制台格式也能查询事件，不需要修改全局日志格式或展开任意上下文 extra。仅 COMPLETED/FAILED/CANCELLED 创建 Langfuse generation，重试、拒绝和最终请求失败不重复创建。

### 日志落盘缺口与补测（2026-09-08）

首次部署 `9f8fdc81` 后核对人工验收入口时，发现原发布器仅 `logger.bind(**fields).info("ModelGateway sampling event")`，而生产 `setup_logging()` 格式不包含 extra。此前测试验证了事件对象、脱敏和异步行为，没有检查最终文件，因此普通日志只显示固定消息，无法查询等待时间和拒绝等字段。此前“全部达到验收标准”的判断不完整。

本次仅修复采样发布器的消息序列化。新回归直接运行生产 `setup_logging()`，仅将日志目录移到临时目录，并读取生成的 app 日志；修复前稳定复现 JSON 字段缺失，修复后覆盖全部 10 种事件、真实 Gateway 排队超时/成功、过载后重试及最终失败。Langfuse 使用测试替身，日志发布器和文件 sink 使用真实实现。测试同时验证 request/attempt 关联、终态 generation 不重复，以及 prompt、响应、key、Provider 异常正文和上下文任意 extra 不进入事件输出。

重新部署后，可在现有 `backend/logs/app_YYYY-MM-DD.log` 或服务控制台日志中搜索 `ModelGateway sampling event`，按 JSON 的 task_id/request_id/attempt_id 关联；异步事件不能依赖文件行顺序。等待时间见 attempt 事件的 queue_wait_ms；队列超时见 concurrency_rejected + queue_timeout；熔断见 provider_rejected + circuit_open；过载见 provider_overloaded；超时/最终失败见 request_failed 的 error_code/stop_reason；retry_started 的 previous_attempt_id 连接上一次 attempt。旧版本已丢弃的日志字段无法补回。

## direct call 清理及保留理由

T2 已清理 `backend/services` 和 `backend/api` 里的业务 direct stream call。T6 再次扫描，并将以下 3 个基准脚本的 `adapter.stream_chat()` 改为真正的 Gateway session 调用，关闭流程也交给 session；各脚本均补充可控 Provider 的行为测试：

- `backend/scripts/benchmark_direct_vs_agent.py`
- `backend/scripts/test_erp_agent_benchmark.py`
- `backend/scripts/test_tool_loop_benchmark.py`

最终 `adapter.stream_chat(` 文本扫描在上述三个目录中只剩 Gateway 内部唯一 dispatch。AST 回归同时禁止 service/API 直接调用 create_chat_adapter，并检查业务及脚本的 stream 接收者。

保留以下入口，没有删除仍有用途的兼容代码：

- Provider adapter 内部 stream_chat/chat_sync/chat 实现及内部调用；它们负责 HTTP/SSE 协议，必须由 Gateway 调用。
- adapter 工厂与公开导出；Gateway 本身、Provider 测试、离线协议实验仍在使用。
- 旧 `poc_*.py` 中的 chat_sync 调用属于离线 Provider/提示词实验，不由生产 service/API 导入；本次不扩张为所有离线实验的改造。它们不是已收口的业务 stream 入口，不能当作 Gateway 并发能力的验收工具。
- `PreparedChatStream.adapter`、Chat 内核的 adapter-shaped 注入、企微共享内核入口、ToolLoopExecutor 的兼容参数和 T3 公开关联接口；它们仍用于现有调用或测试契约，实际生产 Chat 模型流走 Gateway。
- 测试 fake 和 manual Provider 测试保留，可独立验证 adapter 协议。

## 验证记录

新增行为覆盖：1/3/5 槽上限与 FIFO、排队、4 种排队取消、执行中取消、取消与获槽竞争、first_chunk/stream timeout、队列 timeout/预算到期、Provider 异常、429/503/529 过载、关闭运行/等待/暂停消费的流、关闭与就绪 chunk 竞争、同会话并发拒绝、预建会话与等待期间熔断、HALF_OPEN 恢复、retry 路由释放槽、多个 Actor 领取/续租/丢失执行权、Web 与真实 ScheduledTaskAgent 并发，以及 3 个脚本的 Gateway 行为。

相关回归清单见 `docs/testing/model-gateway-t6-regression.txt`。采用仓库既有 Python 3.12 venv、假配置和本地临时工作区：

- 主回归（排除另行隔离执行的模块）：**1389 passed，13 skipped**。
- ERP Agent 与 ToolLoopContext 两个模块隔离进程回归：**158 passed**。
- 独立临时 PostgreSQL，加载仓库既有 Actor/credits SQL：**11 passed**，覆盖暂停、恢复、租约回收、交付、重复 commit 只扣一次积分，以及最终失败不产生积分流水。该测试服务已停止。
- 总计 **1558 passed，13 skipped**；13 项是仓库明确退役的 V1 Mem0/gather 测试。另有最终定向行为测试 **122 passed**，不重复计入总数。
- 最初组合运行的 9 个失败来自既有测试先 patch canonical ToolExecutor，再导入 re-export 模块造成 Mock 缓存。用最小测试顺序复现后分进程执行上述两个模块，未改业务实现或削弱断言。
- 最小临时 DB schema 缺少生产已有的 fail_code，补齐临时库后原测试全部通过；没有修改仓库迁移。
- 新模块在 Python 3.11.15 上实际执行 semaphore 取消/幂等归还 smoke；生产修改文件通过 Python 3.11 语法解析。完整依赖测试使用现有 3.12 venv，不冒充完整 3.11 环境回归。
- AST direct-call 扫描、Python 编译检查、git diff --check 通过；环境没有 ruff，未安装额外 lint 依赖。
- 日志落盘补丁：新增 **12 项行为测试**；连同 Gateway、并发、Actor/Web/定时任务集成、retry 及 Web/Actor retry 集成，共 **134 passed**。继续覆盖采样 I/O 不阻塞首 chunk；本次没有改变并发/任务机制，不重复运行与日志无关的数据库或前端回归。

## 部署、回滚和验收状态

- service unit、worker 数、发布脚本、依赖和数据库迁移均不需要修改；现有受控发布入口继续适用。
- 可选在现有 backend `.env` 增加 `MODEL_GATEWAY_MAX_CONCURRENCY=5`；不写也启用默认保护。变更需重启对应现有服务进程，配置不跨进程共享。
- 可以回滚到 T5 候选 `e894ca8a` 或本任务基座 `c789a6cc` 的代码树；没有不可逆存储变更。回滚将恢复原来的无 Gateway 总并发限制行为；新增配置不再生效，必要时可移除。应通过项目受控发布流程操作。
- 待交付差异未增加旧 Runtime 平台文件或无关部署变更；Actor claim/任务状态机/持久队列、计费公式和提交 RPC 均未修改。
- 首次受控部署 `9f8fdc81de28ded9c9c53bae2e5449062c4750b0` 成功，前端 **1306 passed**、后端 **8009 passed / 37 skipped / 4 xfailed**，状态 `DEPLOYED_PENDING_ACCEPTANCE`。该版本仍有上述日志字段缺口。
- 日志补丁已完成本地实现和行为验证；本节为补丁提交部署前的记录，发布状态以受控入口的 `RELEASE_RESULT` 为准，**T6 最终生产验收仍待完成**。补丁不新增配置、依赖、迁移或 service unit 修改；回滚补丁会恢复字段缺失，但不改变已部署的并发行为。发布后核对生产日志字段和用户业务验收；保留当前工作树。
