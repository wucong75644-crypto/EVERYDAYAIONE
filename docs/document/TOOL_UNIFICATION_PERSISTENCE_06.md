# 板块 06：ToolResult 持久化、缓存与审计契约

> **2026-09-11 写入阶段更新**：兼容 reader 版本 `e097e392fd8229cffda363fc85113c31fb995179` 已先部署并完成用户回归；本次第二阶段默认写入切换为 **1**，显式设置 0 可停止新写入且继续读取 v1。当前有效发布顺序、验证和回退依据见 [06 写入阶段验收补充](TOOL_UNIFICATION_06_WRITER_ROLLOUT.md)。最终候选及关闭结果以受控发布/关闭交付消息为准。

以下为首次兼容读取阶段的验收和接口快照；其中“默认 0”“待部署”等时态不覆盖上述第二阶段更新。

日期：2026-09-11。技术实现完成，待提交部署及用户验收。验收及精确被测版本见 [06 验收记录](TOOL_UNIFICATION_ACCEPTANCE_06.md)。

## 1. 版本和发布顺序

当前基座为 `cdba58f9018ff45be2ebde0b802471ca634d8727`，与板块 05 最终提交 `87b07718` 代码树一致。本文称它为 **R0**。本块兼容读取版本称为 **R1**：基座加 [逐文件指纹](tool-unification-evidence/06-source-checks.json) 中的源码；尚未提交，不虚构发布 SHA。提交部署时必须在交付消息填入 R1 的实际候选 SHA，并核对源指纹。

新增设置 `TOOL_RESULT_PAYLOAD_WRITE_VERSION`（Settings.tool_result_payload_write_version），只接受 0 或 1，默认 **0**。reader 不受此开关影响，始终读旧载荷及 v1。不能把默认配置下的部署描述成新载荷已经启用。

发布顺序：

1. 发布 R1，保持写入版本 0；先让所有处理 invocation 的 Web/Actor worker 使用兼容 reader，确认具体镜像/提交版本与进程已经替换。此时仍写旧 kind 和旧投影，采用相同安全边界剔除 runtime metadata。
2. 在隔离会话完成本文恢复演练，并记录 R1 实际 SHA 后，才在受控后续发布中将设置改为 1。测试中两个阶段均已执行；本任务未操作任何生产配置、进程或数据库。
3. 停止新写入时设回 0，**保留 R1 reader**，新旧历史记录继续可读。不要清空 invocation、把状态改回 running 或重新执行业务作为迁移手段。
4. 启用 v1 后，完整信息恢复的回退目标是同一 R1 候选（或已证明含同一 reader 的后续版本），写开关设回 0。R1 的代码身份已在上述指纹中固定，正式提交号由部署阶段记录。

**R0 只支持降级读取，不是 v1 的完整回退目标。** 实际执行 R0 的 writer/reader AST 完成了跨版本演练：R0 reader 能读 v1 外壳的摘要、业务状态、错误和 emit；结构化 FileRef/图片注入/表单、metadata/retry、token/thinking 等会退化到旧能力。没有把此结果称作无损回滚。不能在承诺恢复完整信息时直接回退至 R0。尚未产生 v1 的 reader-first 阶段，可按旧格式回退 R0；R0 本身不含本块缓存和审计修复。

## 2. 载荷布局和边界

新文件 `backend/services/tools/result_payload.py` 提供：

- `encode_result(ToolResult) -> dict`：只编码受支持值对象，产出 v1 + 旧兼容外壳。
- `extension_of / validate_payload`：有界验证；未知版本、坏结构不会退回外壳并伪装成功。
- `decode_raw`：仅构造 AgentResult、FileReadResult、FormBlockResult、字符串等已知类型，不动态导入载荷指定的类。
- `restore_result(payload, *, call, context, decision)`：从已授权的当前调用重建 ToolResult。历史信息保存在 `audit.origin`，当前身份、参数、Policy decision 由调用方提供，载荷不能恢复权限。

示意（省略字段内容）：

```json
{
  "kind": "agent_result",
  "summary": "旧 reader 的摘要投影",
  "status": "error",
  "error_message": "业务错误",
  "emit_payloads": [],
  "tool_result": {
    "version": 1,
    "kind": "agent",
    "status": "error",
    "raw": {},
    "execution": {},
    "audit": {},
    "error": {},
    "model_overrides": {},
    "omitted_metadata": []
  }
}
```

string 使用 scalar/value 外壳。新 image/form 使用可读的 agent_result 摘要外壳，完整类型只在扩展中。异常仅保存 ToolError 的文本、类型标识及重试描述，不保存异常对象/traceback；恢复时只创建安全内建异常。取消在新 reader 中继续传播，旧外壳是 error，不能成为普通成功。

| 内容 | v1 处理 |
|---|---|
| AgentResult | summary/status/format/source/error_message、受限 metadata、emit、tokens_used/confidence/insights/follow_up/thinking_text 全部保留；不保存 `_valid_cache` |
| FileRef/ColumnMeta | 所有明确字段保留，含 path/filename/columns/preview/id/mime/created_by/created_at/ttl/derived_from；恢复 tuple 语义 |
| 小数据 | 最多 200 行内联；Decimal→有限 float，日期→ISO 字符串，UUID→字符串，与原模型 JSON 投影相同 |
| 图片/表单 | FileRead 的 type/text/image_url；Form 的 form/llm_hint，保留图片注入和表单终止 |
| 错误/重试 | message/kind/retryable/safe_to_retry/retry_context；恢复的 safe_to_retry 还受当前 effects 和 uncertain/cancelled 约束，不授权自动重试 |
| 审计事实 | 工具/调用/任务/会话/操作者/owner/org、业务状态、耗时、长度、截断、source/token、参数 hash；审计层不保存调用参数明文、Policy/Confirmation/预算/锁/数据库等上下文（业务 retry_context 保留必要参数） |
| runtime metadata | dict 中的非值对象（数据库、锁、异常等）省略，路径记入 `omitted_metadata`；不调用其 repr/str。重试参数、产物和数据格中的非法对象使整个存储失败，不能悄悄丢失必要字段 |
| 大小 | JSON UTF-8 总量最多 512 KiB（包含兼容外壳），单字符串最多 128 Ki 字符，深度 16，访问节点 30000，key 256 字符，整数 256 bit；另有保守的遍历内存预算，防止构建巨型 JSON 才发现超限 |

超限、循环引用、非有限数、未知类型明确抛出 `ToolPayloadError`，不截断产物或伪造一个可完整回放的成功结果。大数据应使用已有 FileRef；本块不新增自动 staging、文件上传或新基础设施。数据库写入和缓存写入分别由既有完成边界捕获并记录故障，业务返回本身不重做。载荷写失败时 ledger 保留 running，恢复继续按原 running/uncertain 保护，不能宣称该条仍可完整恢复。

旧载荷 reader 保留 agent_result/scalar/json 规则。缺失 tokens/thinking/metadata/file_ref 按旧类型缺省 0/空串/空字典/None，retryable 缺省 None；这些是兼容缺省，**不是历史耗费为零或历史置信度已知的证据**。不解析旧 Form/FileRead 的对象字符串去猜丢失字段。旧载荷没有 `audit.origin`，回放也不伪造它。原直接 raw serializer/reader API 保留受支持类型和合法投影；它们也应用上述边界，去掉旧 writer 对任意对象的 str 兜底，只有已验证字段的 FileRead/Form 保留旧对象字符串投影。旧历史字符串仍照常读取。生产生命周期直接传 ToolResult。

## 3. Actor 和缓存调用链

`ChatToolMixin._execute_single_tool → ToolRuntime.execute → ActorToolLifecycle.replay/begin/complete`。

先运行当前 Policy、身份/组织/资源范围检查，再 lookup/取缓存；`check_result_resources` 在反序列化对象前检查 v1 raw 和旧 emit 的 workspace_path，以及新增 FileRef.path 的 owner 根目录包含关系（含路径逃逸和 symlink 解析）。不读取 FileRef 的文件内容，不把检查通过视为重新授权业务。

原 invocation lookup、args_hash、mark_stale、begin/complete RPC、lease/fencing、数据库状态枚举保持原样。`succeeded` 表示 Handler 返回了可回放结果，AgentResult.error/timeout 同样可以完成。业务抛异常为原 uncertain 路径，策略拒绝没有新 invocation；running/in_progress/uncertain 禁止重做。完成写入与展示、审计投递分离。

缓存资格仍同时使用原 decision.cacheable 与 ToolResultCache.is_cacheable，没有修改目录、code_execute 的资格、restore_file 的风险或 Actor qualification。生产缓存保存有界 v1 快照（进程内缓存无需滚动 reader 协议），旧直接 put/get AgentResult/str API 保留。容量仍 50、TTL 300 秒、单条 8000 字符；现在大小检查包括产物和 metadata。命中恢复业务状态、重试及产物；不引用可被调用方修改的原结果对象。

缓存 key 增加 actor/owner/org/conversation/task/scope/execution_mode/domain 身份维度，保留文件版本维度；同名同参数不能跨这些边界取用旧内容。缓存读异常、损坏/未知版本载荷是明确失败，不能回退执行 Handler；缓存写失败记录 warning 后仍交付已经完成的业务结果。

`ToolResult.reused` 将当前尝试标记为 cached 或 replayed、handler_started=False、attempts=0；仍保存原结果 token/thinking。`chargeable_tokens` 在复用时为 0，Chat 原 `_erp_agent_tokens` 累计改用此字段，不改变任何 Handler 内部扣费/退款。

## 4. 审计、投递和实际限制

继续使用 `ToolAuditEntry / record_tool_audit`。表字段、状态列、数据库枚举、索引和迁移文件均未改变。

| 场景 | 原表字段 | 关联执行事实/次数 |
|---|---|---|
| 正常/业务失败 | status=原业务状态，is_cached=False，原 task/call/user/args_hash/length/time | 每次消费一次审计；execution.status=succeeded，Handler=1 |
| 策略拒绝 | status=denied 或 confirmation_required，is_cached=False | execution.status=not_started，attempts=0，Handler/新 invocation=0 |
| 缓存命中 | 保持原业务 status，is_cached=True | cached=True、replayed=False、Handler/新 chargeable tokens=0 |
| Actor 回放 | 保持原业务 status，is_cached=False，不谎报为缓存 | replayed=True、cached=False、Handler/新 chargeable tokens=0；同 call 的回放消费是一条可区分的恢复观察，不是第二笔业务执行 |
| uncertain | 原 error/timeout 状态 | execution.status=uncertain；禁止重试，不能写成业务成功 |
| 取消 | 保持原取消传播，不新增普通成功审计 | 若 Handler 中取消，原 running 恢复保护仍生效 |

原审计表没有 replay/effects/source/子 Agent token 列。因此新增的 `ToolAuditEntry.execution` **只由同一个原写入器写入现有结构化应用日志**（`Tool audit execution`，关联 task_id/tool_call_id/trace_id），显式从数据库 insert 的 row 移除；它包含执行状态、cached/replayed/cancelled、attempts、effects、source、original_tokens/chargeable_tokens，以及恢复载荷版本。业务结果的必要 metadata 则保存在 v1 ledger。查询回放区别需要将表记录与关联日志一起检查，不能声称表里已有 replay 列，也不能承诺日志保留期等同数据库。

ToolLoop 的 prompt/completion 是本轮模型 token，只归属本批第一条审计，后续工具记 0，避免多工具重复累计；cache 命中本轮如果模型实际又调用了一次，仍保留这次真实模型花费。它不代表重新计入缓存结果的历史 token。

Chat 审计调度异常记录 `Tool audit dispatch failed`，原数据库失败记录 `Tool audit write failed`，不阻止已经完成的结果展示。Chat/ToolLoop 展示异常记录 `tool_result_delivery_failed` 并继续传播错误；ToolLoop 为本批尚未记审计的已完成结果补交一次原审计，已尝试过的审计不重试。没有第二次 Handler 分发。

仍然是 best-effort 审计和原 ledger 持久化。进程在提交审计任务前退出、数据库失败、日志保留到期等可能导致记录缺失；没有 outbox、投递重试设施、跨进程审计去重表或零丢失保障。FileRef 恢复只恢复引用，不保证文件/URL 永久存在；保留原 TTL/有效性检查，不通过重做业务来修复过期产物。
