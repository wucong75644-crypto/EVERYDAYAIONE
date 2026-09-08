# T5：ModelGateway 错误、retry 与 fallback 收口

## 范围与基座

基座为 `44ea7dc9`，包含 T1 模型入口、T2 生产 Chat 调用迁移、T3 request/attempt 采样和 T4 deadline/cancel。
本次只升级主 Chat 的 Web/Actor 共用模型会话；图片、视频、ERP 工具、调度器等未注入 Chat retry policy 的辅助调用保留原有执行策略与异常契约。没有新增数据库迁移、重试计数器或重试配置。

## 修改前的事实

- `core/error_classifier.py` 是分类入口，输出 category、is_retryable、is_transient、should_refund、should_record_breaker、error_code。
- `RetryContext` 保存 smart 标记、原请求、生成类型和失败模型列表。`max_retries=2`，每次先 add_failure，再判 `len(failed_attempts) < max_retries`，因此实际是 **总共两次模型尝试（一次换模）**，不是两次额外重试。
- `BaseHandler._build_retry_context()` 只为 `_is_smart_mode=True` 建立或续用上下文；手选模型不自动重试。`_route_retry()` 调用 IntentRouter，后者依次走路由主模型、备用模型、配置中的确定性候选；熔断器和模型配置过滤候选，工厂也检查 Provider 是否可用。
- Web 的 `handle_stream_error()` 分类并记录熔断，`_handle_stream_failure()` 调 `_attempt_chat_retry()`。后者通知、更新模型、递归调用 `_stream_generate()`，会重建上下文与预算；没有检查此前是否已交付文本、思考或工具片段。
- Actor 的 `ChatGenerationExecutor` 使用同一 `execute_chat()`，但没有 Web 的模型 retry。异常交给 `ConversationExecutionService._fail()`；租约回收/重新领取由队列 RPC 的 execution_attempt/max_attempts（默认 3）控制，这不是模型 retry。
- T4 会把包括显式 cause 在内的 Provider timeout 归一为 `ModelGatewayTimeoutError`，分类为 `MODEL_TIMEOUT`、不可模型 retry。保留此语义。
- KIE Chat 的 HTTP/SSE 错误转成 KieAPIError 子类；原通用 KieAPIError 分类可重试，部分业务 4xx 因而也会重试。DashScope/OpenRouter/Google 异常不继承公共 AIModelError，多数落到 UNKNOWN；HTTP 状态与网络 cause 未被充分使用。
- KIE 图片/视频 create/query 有独立 tenacity 网络退避；Google 客户端另有 generate_content 的重试声明；本次不修改这些 Provider 实现或非主 Chat 链路。
- Chat 无预扣积分，失败退款 hook 是空操作；成功时 `_calculate_credits()` 优先取 API 积分 `ceil(api_credits)+1`，否则用 adapter 的既有价格估算。Web 原完成流程有任务幂等检查，Actor `commit_generation_turn` 在事务内处理幂等、扣分、usage 与终态；失败不扣分。

## 分类与执行规则

| 情况 | 既有分类/Chat 补全 | Gateway 动作 |
| --- | --- | --- |
| 网络连接、断连、已包装的网络 cause | TRANSIENT / NETWORK_ERROR，retryable | smart 模式且尚未交付任何 chunk 时，可请 IntentRouter 换模 |
| Provider 429，含 KIE/DashScope/OpenRouter/Google | TRANSIENT / RATE_LIMIT 或 KIE_RATE_LIMIT | 同上；保留原有换模上限，不新增同模型循环或固定退避；should_refund=False |
| Provider 5xx | MODEL / MODEL_ERROR（KIE 原通用错误仍兼容） | 同上 |
| ProviderUnavailableError | MODEL / PROVIDER_UNAVAILABLE | 可换模；已 OPEN，不再记一次 breaker failure |
| Gateway deadline、已包装的 Provider timeout、Chat HTTP 408 | TRANSIENT / MODEL_TIMEOUT | 终止请求；保留 T4 的不换模语义 |
| 积分不足 | BUSINESS / INSUFFICIENT_CREDITS | 不重试、不新增退款 |
| 权限、验证、队列满、KIE 余额不足、Provider 非 408/429 的 4xx、content_filter | BUSINESS / 对应既有码 | 不重试、不记熔断；仍由业务完成/失败流程处理积分 |
| 数据库错误、快麦 token 失效 | INFRA | 不换模型；不接管基础设施恢复 |
| 快麦限流等 transient=True、retryable=False | 仅同操作瞬态恢复提示 | 不转为模型重试，原调用方拥有短暂退避 |
| cancel、Actor 失去租约/执行权、asyncio task cancel | 原有取消控制流 | 关闭 Provider/路由协程，绝不进入下一 attempt |
| 已交付 chunk 后失败 | 保留原分类，最终标记 partial | 不重试、不拼接第二次生成 |
| 未识别异常、无结构化依据的 Google/DashScope/OpenRouter 错误、Provider 包装的编程错误 | UNKNOWN | 不重试 |

Chat 补全通过 `classify_error(error, model_call=True)` 实现。默认调用不变，尤其不改变图片/视频的 KIE 错误、重试与退款策略。分类依据是异常类型、HTTP 状态和显式 cause，不通过任意错误文本猜测重试。

## Gateway 的职责

`ModelRetryPolicy` 仅注入既有 BaseHandler 建立上下文、IntentRouter 路由、ChatStreamSupportMixin 熔断记录及调用方通知 hook。失败历史和次数仍只有 RetryContext 一份。

`ModelGatewaySession.stream_chat()` 拥有 attempt 循环；`_stream_attempt()` 保留 T3/T4 单次 Provider 生命周期、deadline 与取消实现。

1. 每个逻辑请求分配 request_id；各 attempt 分配独立 attempt_id。
2. 捕获工厂/Provider 失败；工厂首次失败延后归入首个实际消费的 attempt，不重复建同一模型、不重复计数。
3. 分类、记录符合条件的 breaker 结果，检查 cancel、partial、smart 和 RetryContext 上限，再调用既有路由。
4. 旧 adapter 先关闭，再路由并创建下一 adapter。Provider 专有 Google Search 工具按实际模型重新装配，权限、思考参数、冻结上下文和执行预算保持同一次 Chat 请求。
5. retry_started 关联 previous_attempt_id，同一请求所有 attempt 使用相同 request_id/request_index。后续正常工具回合获得新的 request_id。
6. `last_result: ModelCallResult` 给出 completed/failed/cancelled、最终 model、各 attempt 的关联与 usage、分类、停止原因和 partial 标志。主 Chat 的失败通过携带该结果的 ModelGatewayError 返回；cancel 仍抛原有 CancelledError。上层没有第二层模型 retry。

路由返回空候选、失败模型或未注册模型时终止；不能让 adapter factory 的默认模型回退绕过失败模型排除。

## 流式安全边界

采用保守边界：同一 Chat 会话 **任何 chunk 一经交付**（文本、思考、tool delta、usage 或空 chunk），自动模型 retry 窗口就关闭，包括后续工具回合失败。这样既不会重放工具/文本，也不会把已有模型的累计 usage 按另一个模型计价。

partial 失败不会转换为成功；最终错误为 MODEL_PARTIAL_OUTPUT（timeout 仍为 MODEL_TIMEOUT）。Web/Actor 在失败终态前通过已有 sink 保存未到批次阈值的 partial 进度。消息失败展示、Actor 快照与恢复仍由原有机制负责。原工具结果为空时的继续整理流程不是 Provider 错误 retry，保持不变。

## Web、Actor 和计费边界

- 移除 Web `_attempt_chat_retry()` 的递归模型执行。保留 ChatStreamSupportMixin 建立策略、通知、知识指标与最终失败处理。
- Web 和 Actor 都在 `prepare_chat_stream()` 注入同一策略，使用同一 Gateway；Web 只在最终完成时调用一次 on_complete，且知识指标使用实际成功模型和同一 RetryContext。
- Actor 的换模元数据更新在 ChatGenerationExecutor 内执行，条件包含 task id、execution_token、running 和未过期租约；无更新行即失去所有权，不创建下一 Provider。Gateway 无数据库写入。Actor 的领取、续租、任务次数、checkpoint、commit/fail/cancel 均由原有机制负责。
- 一个失败 attempt 的 Provider 成本只进入已有 T3 采样记录与 attempt result，不预扣/退款，也不累加到成功结算。已输出 usage 后失败不可重试，最终仍按现有 Chat 失败规则不扣分。
- 成功结果只由原有 `_calculate_credits()` 计算一次；首 chunk 前切换没有任何失败 usage 被交付，最终累计用量属于成功模型，API 积分及本地估算公式保持不变。
- Actor 仍返回 GenerationOutcome，由原 `commit_generation_turn` 原子提交 usage 与 credits；Gateway 不调用积分函数，也不提交任务终态。持久化失败不重新调用 Provider。

## 验证

- 新增 Gateway 错误/attempt/取消/partial 测试，以及 Web/Actor 经真实共享内核的 retry、usage、credits 与 ownership 集成测试。
- 原有 Chat retry 测试改为验证 BaseHandler 策略复用和通道 hook；保留 RetryContext 原始上限断言。
- 最终执行 Chat、Actor/Conversation、retry、分类器、IntentRouter、熔断器、adapter factory、DashScope、OpenRouter、KIE、图片/视频 retry 和 usage 回归：**920 passed，13 skipped（5.58 秒）**。13 个跳过项是仓库明确退役的 V1 Mem0/gather 测试，不是 T5 未执行项。
- 使用仓库既有 Python 3.12 虚拟环境；测试以本地假配置启动，不读取生产凭据、不发送真实 Provider 请求。

- 其中 11 项在新建临时 PostgreSQL（只监听独立 Unix socket）上执行，加载仓库原有 Actor、credits SQL 与最小测试 schema：覆盖租约回收、暂停、恢复、交付，以及重复 commit 只扣一次积分、最终 usage/模型一致、四类最终失败无积分流水。
- 数据库回归发现旧交付测试仍调用迁移前四参数 RPC；当前生产调用方与 248 迁移均为包含 event_id 的五参数。已仅更新测试到现有契约，并新增重复 event_id 不增长序号的断言，未改交付实现或迁移。
- `git diff --check` 通过。Provider 测试使用可控模拟流和真实 Gateway/RetryContext；未调用付费 Provider，未部署或进行生产验收。

## 验收结论

T5 的代码和自动化验收标准已满足：复用指定组件，Gateway 唯一执行模型 attempt，Web/Actor 共用判断，取消/业务/未知错误不重试，partial 不拼接，原 retry 次数与计费公式保留，attempt/usage 可追踪且终态只结算一次。生产测试须在用户另行提交部署后进行。

## 提交部署准备

用户授权提交部署后，检查发现当前 main 与任务分支仍使用旧发布协议。按部署技能要求，仅复用已审查的公共发布兼容实现（release.sh、release-coordination.sh、deploy.sh 及对应测试），补齐协议 2 的生产锁、发布前候选失效、完整成功后重建候选与不确定操作保留锁。未移植来源任务的产品代码或工作树取消功能。

本地伪 SSH / 临时 Git 验证：发布协调 10 项通过，发布与验收生命周期脚本通过；这些验证不访问生产。任务部署默认包含前端与后端，完成后保留工作树等待用户验收。
