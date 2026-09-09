# 工具统一 03：执行入口、结果字段与兼容出口

本块仅提供隔离基础，生产调用方仍使用旧 ToolExecutor。对应 [03 验收记录](TOOL_UNIFICATION_ACCEPTANCE_03.md)。基准为 `8e74f57d`，实际符号以本任务代码为准。

## 1. 调用契约

```python
from services.tools import (
    ToolCall, ToolDispatcher, ToolExecutionService, ToolConfirmation,
    build_legacy_catalog, build_legacy_handlers,
)

# 服务端先构造同一请求的 legacy_executor 和 trusted_context。
registry = build_legacy_catalog()
dispatcher = ToolDispatcher(build_legacy_handlers(legacy_executor))
execution = ToolExecutionService(registry, dispatcher)  # 请求内复用，不能每次批准后重建
call = ToolCall(call_id, tool_name, normalized_and_resolved_arguments)
result = await execution.execute(call, trusted_context)

if result.status == "confirmation_required":
    # 板块 04 的可信适配器保存绑定、使用既有确认通道并认证响应。
    # 等待后重新取得上下文/权限/所有权，不能从模型 JSON 构造 approved。
    confirmation = ToolConfirmation(result.decision.confirmation_binding, authenticated_status)
    result = await execution.execute(call, refreshed_context, confirmation=confirmation)

# 返回同一原始对象；若原来为异常或未获准，则抛出相应异常。
legacy_value = result.to_legacy()
# 也可直接 await execution.execute_legacy(call, trusted_context, confirmation=confirmation)
```

调用链为 ToolSpec → ToolRegistry.check_access → ToolPolicy.decide → ToolDispatcher.dispatch → LegacyToolHandler → ToolResult。`ToolPolicy.decide` 内部调用 Registry 共享访问检查；不能用名称级 resolution.allowed 或 advertised 替代执行批准。显示列表仍通过 `registry.resolve(..., policy=execution.policy, ...)` 取得。

`execute` 每次都用 canonical Registry 的 Policy 重新判断，调用 ID 取自 ToolCall（覆盖 Context 的本轮 call_id）。拒绝/待确认直接返回未执行信封；未知工具的兼容出口为原 `ValueError("Unknown sync tool: <name>")`。其他策略拒绝/待确认的兼容出口为 `PermissionError`，携带 Policy reason。不存在拒绝后执行旧 Handler 的兜底。

`_ApprovedCall` 是内部不可变调用/定义/决定及一次性分发状态，不对外导出。由入口在 allow 后通过 Dispatcher 私有方法签发，Dispatcher 检查本实例签发、allow、未消费，按 `(executor_type, handler_key)` 找 Handler。普通 ToolCall、ToolDecision、JSON 字典及其他 Dispatcher 的许可均不能直接分发。它是进程内 API 边界，不是密码学凭据；受信 Python 代码不得调用 `_approve` 绕过入口，更不能反序列化外部许可。

`build_legacy_handlers(executor)` 为 executor 当时的 `_handlers` 逐项创建 LegacyToolHandler，保存原 callable 本身。后续执行只 `await self._handler(arguments)`，不调用公共 execute，不重写 ERP/File/Sandbox/Media 实现。装配后不动态替换这份 Handler 快照；新绑定需要新的请求装配。缺少注册或不同 executor_type 不按名字兜底，返回既有 ValueError。没有新增 executor_type；新显式 Spec 与 legacy Spec 都可通过 handler_key 绑定原异步函数，遵守同一策略。

ToolCall 在批准前冻结 JSON 参数，分发时复制为旧 Handler 可消费的 dict。保留 `files`/`file_ids` 等原参数，并保留 Handler 原来的解析行为；本层不代替旧参数校验/路径解析。危险目标必须由可信上游在 Policy 之前稳定解析，批准后不能换目标。

## 2. 执行状态、异常、调用次数

| 情形 | Handler 次数 | ToolResult.status | execution.status | 兼容出口 |
|---|---:|---|---|---|
| 允许，返回正常结果 | 1 | 原业务状态（str/File/Form 为 success） | succeeded | 同一原对象 |
| 返回 AgentResult error/timeout | 1 | error/timeout | succeeded | 原错误对象及原 retryable；调用完成不代表业务成功 |
| require_confirmation | 0 | confirmation_required | not_started | PermissionError，保留 binding 供可信适配器使用 |
| deny | 0 | denied | not_started | PermissionError；未知工具为 ValueError |
| 缺 Handler | 0 | error | not_started | ValueError，原 Unknown sync tool 文本 |
| 原 Handler 抛普通异常 | 1 | error/timeout | effects 恰为 `(none,)` 时 failed，否则 uncertain | 同一异常对象（类型、消息及已有 traceback 保留） |
| 执行前取消 | 0 | 不返回成功/错误信封 | 不执行 | CancelledError 直接传播 |
| 执行中/任务取消 | 1 | 不返回成功/错误信封 | 不自动写状态 | CancelledError 直接传播，不重试 |
| 同一请求服务重复提交已分发 ID | 不增加 | error | not_started | PermissionError: already dispatched |

`execution.status` 是内存状态，不是新增数据库枚举；`failed` 不写入 invocation 表。取消的可选 `ToolResult.from_exception(CancelledError, handler_started=...)` 可供未来外层已有 catch/finally 取得字段，`to_legacy()` 仍抛取消；本块执行入口本身不捕获并归一化取消。外部效果未知必须由后续 invocation 适配器保守记录，不能因捕获/投递失败重做业务。

入口在第一次 await 前按 actor/workspace/org/conversation/task/call ID 预占已分发集合，批准的并发重复提交也只调用一次。待确认/拒绝不消费，允许等待批准后重进；分发后异常/取消仍消费。此集合只约束当前 ToolExecutionService 生命周期，**不是跨请求、进程或持久化幂等保证**；不可在批准后/每个工具调用时重建服务。板块 04 保留其执行作用域；板块 06 继续复用原 invocation 存储。

Policy 异常直接传播，Handler 零执行。取消句柄采用现有 `asyncio.Event.is_set()` 约定，入口检查 Policy 前/后两次；进入 Handler 后继续依靠既有任务取消/Handler 取消句柄，不新增后台取消监控或重试器。

## 3. 字段级结果映射

ToolResult 为运行内信封；raw 原对象及其 runtime metadata 不做 JSON 序列化、文件校验、拷贝或截断。模型、展示和产物字段是消费投影，不产生 WS、staging 文件、audit 写入、缓存或 invocation 记录。

| 原字段/行为 | 新入口/字段 | 保留方式与边界 |
|---|---|---|
| AgentResult 全部 dataclass 字段（含 `_valid_cache`） | raw / to_legacy() | 原对象 identity；ToolOutput 仍是 AgentResult 别名 |
| status / is_failure | status / is_failure | 复用原业务失败语义；empty/partial/plan/rejected 不自行改成失败 |
| to_message_content() | model_content("chat") | 原结构化 blocks，保留本地化列、Decimal/date/UUID 与文件引用格式 |
| to_tool_content() | model_content("tool_loop") | 原文本及 DATA_REF；不与 Chat 投影合并 |
| summary / format / thinking_text | display | 原展示文本、格式、思考文本；未直接渲染 UI |
| source/tokens_used/confidence/insights/follow_up/thinking_text | agent_context | 全字段原值；source、tokens_used 同时进入 audit |
| metadata（含业务扩展及 runtime-only 对象） | metadata / audit.metadata | 原引用；不遗漏、不假装它已 JSON-safe |
| error_message / metadata.retryable / metadata.retry_context | error.message / retryable / retry_context | 原提示保留；布尔以外值不解释为已声明 retryable，原值仍在 metadata |
| 副作用与安全重试 | decision.effects / error.safe_to_retry | 仅原 retryable=True 且 effects 恰为 `(none,)` 的已返回业务错误可标安全；文件索引、写入、unknown 均不推断安全。异常/取消从不新增安全重试结论 |
| file_ref（含 ID/MIME/TTL/血缘）、data、columns | artifacts.file_ref/data/columns | 原对象/引用；不读文件、不产生额外 TABLE |
| emit_payloads（file/image/失败卡/图表/diagram/table） | artifacts.emit_payloads | 原列表；URL/workspace_path 双轨、失败 error/retry_context 不丢失 |
| FileReadResult type/text/image_url | raw；model_content("chat")；model_image_blocks；display/artifacts | Chat 返回原 text，并单独携带仅 type=image 且有 URL 的 image_url block；旧 Chat 经 to_legacy 后仍自动注入图片 |
| FormBlockResult form/llm_hint | artifacts.form；display.form/terminal_form；model_content("chat") | 原表单；Chat 模型用 llm_hint，展示为“表单已展示”；旧消费者经 to_legacy 后仍设置终止标志 |
| str | raw / 两种 model_content / display.text | 原字符串，含空串和长文本；不按“错误”字样推断失败；截断/落盘仍由原消费者处理 |
| 普通异常、TimeoutError、CancelledError | exception / error / to_legacy() | 原异常对象；投影重新抛同一异常。取消不转成功 |
| 请求与执行事实 | audit_fields() | tool_name/call ID、actor/workspace/org/conversation/task、冻结 args、原 status、elapsed_ms、result_length、tokens/source/metadata、cached=False、truncated=False |
| 执行事实 | execution | handler_started、attempts、elapsed_ms、effects、status、cancelled、cached=False、replayed=False |

audit_fields 是**投递前**事实。Agent 长度取原 summary，File 取 text，str 取原长度，Form 调用该方法时才按原 json.dumps(form) 计长（避免包装阶段新增序列化错误）。若下游做截断/缓存/回放，应在相应阶段更新事实再复用已有写入器；本块不声称已经完成审计接入/可靠投递。turn/message_id 等消费层事实由后续适配器提供，不虚构 Context 中不存在的值。

旧 ToolLoop 对非 AgentResult 先使用 `result or ""`，本块 `model_content("tool_loop")` 同样保留这一预处理值，File/Form 仍是原对象。本块不声称旧 ToolLoop 已支持这两类的完整后处理；扩展实时消费属于板块 05。默认兼容出口应传 `to_legacy()` 给原消费者，不直接传 ToolResult。

Agent 产物仍由旧 `_collect_interactive_agent_payloads` / `collect_agent_result_payloads` 消费：交互 ERP TABLE 不重复展示，定时 TABLE 继续收集。ToolResult 本身不合成或二次推送表格。投影为惰性，原模型序列化可能抛的错误仍只在原投影调用时抛出，不影响旧对象返回。

## 4. 板块 04 接入前置与责任

1. 从实际 ExecutionScope/请求/定时授权快照装配同一请求的 ToolExecutor 与 ToolContext，严格对应 actor_user_id、workspace_owner_id、org、conversation、execution_mode、授权上界/快照、资源、budget/cancellation。Context 与绑定 executor 的身份配对由可信服务端负责，本层不认证请求、查询权限或从模型参数推导身份。
2. 复用旧参数合法性/别名/资源路径解析；确认之前稳定危险目标。使用现有确认通道保存 server-side binding，认证实际回复；超时/断连/拒绝均不能转为 approved，等待后刷新所有权和权限并重新进入 execute。
3. 同一次完整切换中让 Chat、scheduled ToolLoop、旧公共 execute 门面都进入本服务；兼容门面返回 execute_legacy 的旧类型/异常。不得从 LegacyToolHandler 回调公共 execute，禁止先接执行再补策略。
4. 服务每请求/执行作用域复用；调用者按 Policy.plan_batches 实施顺序与并行屏障。Dispatcher 不负责批次、确认、budget timeout、Actor lease/safe point、审计、缓存或 replay。本块未证明运行批次并行已经接入生产。
5. 权限批准先于缓存/回放与 invocation begin；将准备获准调用和 invocation 生命周期衔接到同一完整入口，不能在回放/投递失败后绕回 Handler。当前入口没有这些 hooks；板块 04/06 在原编排边界接入时补齐适用衔接。
6. 旧返回/WS/持久化消费者先继续使用兼容出口；板块 05 接入实时结果，板块 06 才切版本化持久化载荷。本信封无 serializer，不能 dataclasses.asdict 后直接持久化。

回退：撤回本块新 execution/dispatcher/legacy_handler/result、测试与文档增量，并把 services/tools/__init__.py 恢复到 `8e74f57d`。没有生产导入、配置开关、WS/数据库/持久化格式变化，不涉及数据迁移；保留已验收板块 01–02。
