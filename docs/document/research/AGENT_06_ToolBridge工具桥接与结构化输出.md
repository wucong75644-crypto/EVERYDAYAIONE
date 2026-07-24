# AGENT 06：ToolBridge、工具注册与结构化输出

> 状态：第一轮源码对标完成
> 日期：2026-07-18
> Grok Build 基线提交：`c68e39f60462f28d9be5e683d9cbe2c57b1a5027`
> 研究边界：工具注册、能力暴露、动态发现、参数解析、执行分发、进度和 ToolOutput
> 后续专项：各专业 Executor、Skills、MCP/Plugins、Context、Persistence 和 UI Event 继续核验

## 1. 结论摘要

ToolBridge 不是一个包含全部业务逻辑的“万能执行器”，而是 Agent Runtime 与专业能力之间的稳定协议边界：

```text
Agent 看到的 ToolDefinition
→ ToolCall
→ canonical name / canonical arguments
→ PolicyDecision
→ ToolBridge dispatch
→ 专业 Executor
→ progress events
→ structured ToolOutput
→ model-facing observation + UI/persistence payload
```

Grok Build 的核心做法是：

1. `ToolRegistryBuilder` 在编译期注册工具类型、参数 Schema、输出转换和 Requirement。
2. 会话创建时使用 `ToolServerConfig + SessionContext` 冻结为 `FinalizedToolset`。
3. `ToolBridge` 只暴露定义、解析、调用、动态 MCP 注册和会话资源操作。
4. `ToolRunResult` 同时保留不可变结构化 `output` 与供模型读取的 `prompt_text`。
5. 同步调用和流式调用共用同一个 `finalize_output`，避免终态语义漂移。

EVERYDAYAIONE 已有工具选择、参数清洗、并发执行、成本与权限、专业执行器、`AgentResult`、结构化产物和大结果 staging，能力并不弱。真正的断层是工具事实分布在多处：

- Schema 由多个 `build_*_tools()` 生成。
- 搜索元数据由 `config/tool_registry.py` 保存。
- 风险等级由 `_SAFETY_LEVELS` 保存。
- 执行绑定由 `ToolExecutor._handlers` 保存。
- 参数校验从当前 `selected_tools` 反查。
- 输出还存在 `str`、`AgentResult` 和兼容别名。

因此同一个工具可能“模型可见但不可执行”“可执行但没有 Schema”“动态调用时跳过校验”“新增工具忘记风险登记而默认 SAFE”。推荐结论是“融合升级”：保留专业 Executor 和现有 SaaS 优势，新增一个真正的 Tool Catalog 单一事实源，并让 ToolBridge 成为薄边界。

## 2. Grok Build 的注册与冻结

### 2.1 编译期 Tool Registry

源码：

- `crates/codegen/xai-grok-tools/src/registry/mod.rs`
- `crates/codegen/xai-grok-tools/src/registry/types.rs::ToolRegistryBuilder`

`ToolRegistryBuilder::register<T>()` 要求工具同时提供：

- `Tool`：工具 ID、输入和输出类型。
- `ToolMetadata`：namespace、kind、requirements 和说明。
- `T::Args: DeserializeOwned + JsonSchema + Into<ToolInput>`。
- `T::Output: Serialize + DeserializeOwned + Into<ToolOutput>`。

注册时一次性产生完整 `namespace:id`、`ToolKind`、Requirement、输入 JSON
Schema、类型化输入/输出转换、配置校验、Resource 注入和 Local Registry
注册函数。这使 Schema、解析、执行和输出源自同一个工具类型。

### 2.2 外部 Tool Pack

`register_tool_pack()` 可在首次创建 Builder 前注册进程级扩展包；后注册不会影响
已有 Builder，重复注册的幂等由调用方负责。它适合受信任的启动期扩展，不等于
运行期用户插件，必须与 MCP/远程插件分开治理。

### 2.3 会话级配置

源码：`registry/mod.rs::ToolServerConfig`

每个 `ToolConfig` 支持：

- `id`
- `params`
- `name_override`
- `params_name_overrides`
- `description_override`
- `behavior_version`
- `kind`

`ToolServerConfig` 还支持全局 `behavior_preset`，因此同一实现可按 Agent 改变启用
状态、模型侧名称、参数名、描述和兼容行为版本，无需复制多套 Schema。

### 2.4 SessionContext

源码：`registry/mod.rs::SessionContext`

冻结 Toolset 时显式注入 Terminal、文件系统、cwd、session folder、环境变量、
owner session ID、流式通知、Skills、state path、Memory、Web、LSP、媒体、部署和
Scheduler 等资源。Tool 实现依赖会话资源协议，不直接依赖 Session Actor 或 UI。

### 2.5 FinalizedToolset

源码：`registry/types.rs::FinalizedToolset`

工具表使用 `RwLock`，只在查找时持有读锁且不跨 `.await`；会话资源使用异步
Mutex，Local Registry 与 Renderer 冻结后复用。冻结先建立稳定能力集，仍允许
通过受控写入口增删 MCP 工具。

## 3. Grok Build 的 ToolBridge

### 3.1 Bridge 职责

源码：`crates/codegen/xai-grok-tools/src/bridge.rs::ToolBridge`

`ToolBridge` 仅保存：

- `Arc<FinalizedToolset>`
- 独立保存的可选 Terminal backend

它负责 Builder 冻结、Definition 查询、按 Kind 解析工具名、Prompt 渲染、MCP
增删、`try_parse()`、`call()` 及少量会话状态操作，不合并图片、命令、文件或
MCP 的业务实现。

### 3.2 为什么 Terminal 单独保存

代码注释给出的原因是取消安全：执行 Bash 时 Registry 调用可能持有相关运行资源，取消动作不能等待正在执行的命令先释放锁。Bridge 单独保存 Terminal backend，使 `kill_foreground_commands()` 可直接中断。

本项目未来的 ToolBridge 也需要把“执行资源”与“取消控制面”分开，尤其是：

- 沙盒子进程。
- 图片/视频 Provider 任务。
- MCP 长连接。
- 子 Agent 和后台任务。

### 3.3 名称与参数规范化

Grok 同时保留：

- Registry canonical ID。
- Client-facing name。
- Canonical argument name。
- Client-facing argument name。
- `ToolKind`。

`try_parse()` 和 `prepare_dispatch()` 会先反向映射客户端参数，再交给类型化解析器。未知工具明确返回 `not_found`，不会回退为默认安全工具。

### 3.4 执行路径

`call()` 只是消费 `call_streaming()`，跳过 Progress，取得唯一 Terminal：

```text
prepare_dispatch
→ 查找工具并释放 RwLock
→ 参数反向映射
→ 建立 ToolCallContext
→ 注入 Resources / Renderer / cwd / behavior version
→ LocalRegistry.execute
→ Progress*
→ Terminal
→ finalize_output
```

若流结束但没有 Terminal，会返回明确错误。同步和流式路径不会分别拼装终态。

### 3.5 Meta Tool

`use_tool` 可把一次请求转发给实际动态工具。`ToolRunResult.effective_tool_name` 保存真正执行的工具名，解决审计、展示和权限不能只记录元工具名的问题。

内部转发使用 `call_raw()`：

- 复用父 call ID、cwd 和资源。
- 移除 `InnerDispatch`，避免递归。
- 不重复执行 Reminder 和持久化。
- 外层调用统一做一次终态后处理。

未来若本项目使用 `tool_search → use_tool`、插件代理或子 Agent 代理，必须同时记录 requested tool 与 effective tool。

## 4. Grok Build 的结构化 ToolOutput

### 4.1 双通道结果

源码：`types/output.rs::ToolRunResult`

字段：

- `output: ToolOutput`
- `prompt_text: String`
- `effective_tool_name: Option<String>`

`output` 不被 Reminder 或 Prompt 文案污染，用于序列化、ACP/UI 转换、hunk
跟踪和类型判断；`prompt_text` 由 Output 渲染并追加 Reminder，只进入模型上下文。

这是本层最重要的协议：模型 Observation 与系统事实使用同一个结果来源，但不是同一个字符串。

### 4.2 ToolOutput 类型

`ToolOutput` 是带 `type` 标签的枚举，覆盖命令与后台任务、文件、搜索、Web、
MCP、Skill、Subagent、交互、Plan/Goal、Scheduler、媒体、Dynamic 和通用 Text。

`is_error()` 根据具体输出变体判断逻辑失败，不只依赖异常。比如 Bash 非零退出码、MCP `is_error`、文件不存在或 Patch 失败都成为可观察失败。

### 4.3 输出后处理

`finalize_output()` 是唯一终态构造点：

1. 动态值转换为类型化 `ToolOutput`。
2. 收集跨工具 Reminder。
3. 生成模型 Prompt 文本。
4. 持久化会话 Resources 状态。
5. 返回 `ToolRunResult`。

需要注意：Resources 持久化是本地 Session state，不等价于本项目数据库事务、消息持久化和计费结算。

### 4.4 截断参数

源码：`types/context.rs::TruncationConfig`

关键默认与优先级：

- `read_file` 默认最多 1000 行。
- 全局工具输出默认约 40 KB，具体工具还可有内建值。
- 普通工具：per-tool override > default override > builtin default。
- MCP：per-tool > MCP-specific > default > builtin。
- 不按单行长度静默裁剪，避免破坏单行 JSON 或数据。
- Skill 文件明确豁免读取限制。

本项目后续 Context 板块要评估是否继续按字符预算，还是切换 token/byte 与结果类型联合预算。

## 5. EVERYDAYAIONE 当前实现

### 5.1 工具 Schema

入口：`backend/config/chat_tools.py::get_chat_tools`

工具 Schema 分别来自 ERP、Crawler、File、Code 和 Common 的 `build_*_tools()`。

`get_core_tools()` 用 `_CORE_TOOLS` 建立常驻集合，`get_tools_for_mode()` 对 plan 模式移除执行型工具，并按工具名和 JSON key 排序以稳定 Prompt Cache。

优点是已有 ERP 租户过滤、Agent domain 隔离、Prompt Cache 字节稳定，以及
核心/全量工具分层。局限是 Schema 不是由 handler 或元数据注册表派生。

### 5.2 选择用 Registry

`ToolEntry` 只有 name、domain、description、tags、priority、always_include 和
has_actions，用于 Selector；没有输入/输出 Schema、Executor、Safety/Cost、
timeout/retry、幂等、资源锁、权限或版本。

因此名字叫 Registry，但还不是运行时 Tool Catalog。

### 5.3 执行绑定

`backend/services/agent/tool_executor.py::ToolExecutor` 在构造函数中手工建立 `_handlers`：

- 通用查询和搜索。
- code、media、ERP Agent。
- 文件工具循环注册。
- 企业用户动态加入 ERP。
- 群聊/Workspace scope 再移除个人工具。

优点是专业实现继续保留在各 Mixin/Executor，不需要改成万能执行器。

问题是 handler 可用性与 Schema、Policy、Selector 分别维护。项目已有 `has_handler()` 做兜底，但它只能在运行期发现断层。

### 5.4 参数校验

`backend/services/agent/tool_args_validator.py::validate_tool_args`：

- 从本轮 `selected_tools` 反查 Schema。
- 删除模型幻觉字段。
- 修复 object/integer/boolean 常见类型偏差。
- 检查必填参数。

这是有价值的容错层，但有两个协议风险：

1. 工具不在 `selected_tools` 时直接跳过校验。
2. 校验器只实现部分 JSON Schema 语义，不能证明 enum、范围、组合 Schema 等全部有效。

目标架构应从 Catalog 取 canonical Schema，无论工具当前是否对模型可见都必须校验；兼容型 coercion 作为明确策略，而不是替代完整校验。

### 5.5 动态发现

当前 `extract_tool_names_from_result()` 使用正则从 `erp_api_search` 文本中提取工具名，再与全量工具白名单和 domain 权限求交集。`ToolLoopContext` 累积结果，`inject_tool()` 再加入隐藏工具 Schema。

已具备动态扩展思想，但文本扫描不应成为通用插件协议：

- 工具改名或输出措辞变化会失效。
- 无法携带版本、来源、Schema 哈希和风险。
- “发现”与“授权执行”边界不清晰。

未来搜索结果应返回结构化 `ToolRef[]`，再由 Catalog 解析完整 descriptor；普通 ToolOutput 文本不能隐式注册工具。

### 5.6 并发与调用

Web Chat 的 `ChatToolMixin`：

- 每轮构造 `ToolExecutor`。
- 按并发安全性分批。
- 只读批次 `asyncio.gather()`。
- 写操作串行。

`ToolLoopExecutor` 的专业 Agent 循环也会：

- 预处理所有调用。
- 执行前 Hook。
- 单工具快路径或多工具 gather。
- 按原始顺序回填结果。
- 执行后 Hook、steer 和动态扩展。

问题是两条循环的参数、确认、并发和结果后处理没有统一从一个 Bridge 调用，因此行为可能继续漂移。ToolBridge 应统一单次工具调用语义，Model Loop 决定批次与依赖调度。

### 5.7 AgentResult 与结果信封

`AgentResult` 已统一工具和子 Agent 结果，包含状态、摘要、表格/文件/数据、来源、
错误、metadata、emit payload、token、confidence 和 thinking，并提供消息、
工具循环与纯文本三种序列化。

`tool_result_envelope.py` 进一步把大字符串落入请求 staging：

- 主 Agent 默认 2000 字符。
- ERP 内部默认 3000。
- ERP Agent 返回默认 4000。
- code_execute 默认 30000。
- 完整结果使用内容哈希命名，模型只接收预览与相对路径。

这是本项目相对 Grok 更贴近数据型 SaaS 的优势，应保留。

不足是 Executor 仍返回 `str | AgentResult`，`ToolOutput` 只是延迟别名，
`emit_payloads` 为弱类型字典，且 AgentResult 同时承担执行、模型、UI 和 Agent
状态。staging 又是请求级 ContextVar，其崩溃恢复留待 Persistence 核验。

## 6. 差距矩阵

| 能力 | Grok Build | EVERYDAYAIONE | 决策 |
|---|---|---|---|
| 单一注册源 | 类型、Schema、转换、Requirement 同源 | Schema/元数据/风险/handler 分散 | 采用 Grok 思路 |
| 会话冻结 | `FinalizedToolset` | 每轮构造 Executor + 工具列表 | 融合升级 |
| 多租户范围 | 主要工作区会话资源 | org/user/channel Workspace 较强 | 保留现有 |
| 参数规范化 | 类型化反序列化和 rename | 部分 Schema 校验与 coercion | 融合升级 |
| 动态工具 | MCP 注册/注销、meta dispatch | 文本正则发现并注入 | 采用结构化发现 |
| 专业 Executor | Registry 后端工具实现 | Media/ERP/File/Sandbox 专业实现 | 保留现有 |
| 流式进度 | Progress* + 唯一 Terminal | WebSocket/媒体任务各自实现 | 统一协议 |
| 结果协议 | `ToolOutput + prompt_text` | `str | AgentResult` + envelope | 融合升级 |
| 大结果分流 | byte/line/MCP cap | staging + 数据引用更强 | 保留并统一预算 |
| 版本兼容 | behavior preset/version | Schema/handler 无统一版本 | 采用 |
| 取消控制面 | Terminal 独立于 Registry | sandbox/media/cancel 分散 | 后续 Executor 核验 |

## 7. 目标 ToolBridge 候选设计

### 7.1 ToolDescriptor 单一事实源

后续总体设计至少需要表达以下事实，字段名最终再定：

```text
identity:
  canonical_name
  version
  source
presentation:
  model_name
  description
contract:
  input_schema
  output_schema
capability:
  domain
  required_permissions
execution:
  executor_ref
  timeout
  retry_policy
  concurrency_mode
  resource_keys
policy:
  risk_class
  side_effect
  cost_policy
  idempotency_policy
result:
  artifact_types
  context_policy
```

这些字段不全部暴露给模型，只用于消除多张手工映射表。

### 7.2 会话能力快照

Run 开始前由 Catalog 结合 AgentDefinition、身份与 channel scope、PermissionMode、
已安装扩展、Provider/模型可用性、feature flags 和本轮授权生成
`EffectiveToolset`。

模型只看到允许其规划的 ToolDefinition；真正执行时仍重新经过 Policy Gate，防止会话期间权限或成本变化。

### 7.3 薄 ToolBridge

职责限制为 Definition 查询、ToolRef 解析、参数校验、ActionContext 分发、结果规范化
以及 cancel/progress/terminal 协议；图片 Provider、ERP、文件处理、积分事务、
Model Loop 和 Goal Planning 均不进入 Bridge。

### 7.4 结果分层

候选结果应明确分开：

- `output`：类型化执行事实。
- `observation`：给模型的摘要、引用和 Reminder。
- `artifacts`：图片、视频、文件、表格、图表和 Diagram。
- `events`：开始、进度、等待、完成和失败。
- `audit`：requested/effective tool、耗时、成本、重试、授权来源。

现有 `AgentResult` 和 `emit_payloads` 可以作为迁移来源，不应一次推倒重写。

## 8. 关键边界场景

### 8.1 未知与未注册工具

- 模型调用不存在的工具：返回结构化 `not_found` 给模型，不执行。
- ToolDescriptor 有 Schema 无 Executor：会话冻结失败，不能等到运行期。
- Executor 有实现但未注册：模型不可见，内部调用也必须通过 canonical descriptor。

### 8.2 Schema 与版本

- 动态 MCP Schema 变化：记录 server/tool/version 或 Schema hash。
- 对话恢复时旧 ToolCall：按原 contract version 执行或明确拒绝迁移。
- 参数 rename：在 Bridge 统一转换，Policy 与审计使用 canonical 参数。
- coercion 后参数必须再次完整校验。

### 8.3 并发与资源

- 多个只读天气/搜索可以并行。
- 多图生成可并行，但受授权数量、积分上限和 Provider 并发限制。
- 同一文件写、删除和恢复必须按 resource key 串行。
- MCP 连接断开不能影响内置工具表。
- Registry 更新不能在持锁状态等待远程执行。

### 8.4 超时、重试和幂等

- Bridge 传递稳定 `action_id/tool_call_id`。
- 重试沿用同一幂等键，不重新生成媒体任务 ID。
- timeout 不能自动等价为失败；外部副作用可能已发生，需要 reconcile。
- 流式执行必须恰好产生一个 Terminal；无 Terminal 是协议错误。

### 8.5 输出与上下文

- 结构化 Output 永久保存，模型 Observation 可压缩或重建。
- 大结果落盘失败不得静默截断。
- staging 引用必须限制在当前 Workspace/Run。
- MCP 和网页返回中的指令视为不可信数据，不能转成授权或注册行为。
- Artifact 上传成功但消息提交失败时，Persistence 层必须能重放关联。

### 8.6 取消

- 取消控制面不能等待执行资源锁。
- 沙盒应终止子进程树。
- 媒体 Provider 若不可取消，标记 cancelling/reconciling，而不是伪报 cancelled。
- 已完成副作用不能因用户取消而丢失审计和结算。

## 9. 验收场景

1. 普通天气查询：Definition、Policy、Executor、Observation 和 UI 事件使用同一 canonical tool identity。
2. 用户提供三段图片 Prompt 并明确生成：形成三个 Action，授权数量和成本校验通过后并发执行，各自产生独立稳定 ID 和 Artifact。
3. 只要求优化三段 Prompt：没有任何 ToolCall 和费用。
4. 动态 MCP 工具上线：无需修改 Schema 表、Safety 表和 handler 表三处；注册后可发现、授权、调用和注销。
5. MCP 返回超大结果：完整结果可检索，模型只收到带引用的预算内 Observation。
6. 模型调用隐藏或未知工具：不会跳过 Schema/Policy 执行。
7. 同一文件的两个写操作：按资源串行；不同文件可并行。
8. 工具 Progress 后异常：事件流只有一个失败 Terminal，数据库可恢复。
9. Provider timeout 后迟到成功：幂等 reconcile 关联原 Action，不二次扣费。
10. Actor Worker 执行中重启：ToolCall identity、授权、输出和 Artifact 可从持久状态恢复。
11. 群聊调用个人定时任务工具：会话能力集不暴露，伪造调用也由 Policy 拒绝。
12. 旧版本 ToolCall 恢复：使用固定 contract version 或返回可解释的不兼容错误。

## 10. 对总体重构的输入

本板块确认以下候选项进入总体设计，尚未授权编码：

1. 将现有选择 Registry 升级或替换为统一 Tool Catalog。
2. 从 Descriptor 派生模型 Schema、参数校验、Executor binding、Policy metadata 和审计 identity。
3. 建立会话级 `EffectiveToolset`，不再每轮临时拼接互相独立的列表和 handler。
4. 建立薄 ToolBridge，保留 Media、ERP、File、Sandbox 等专业 Executor。
5. 统一 `ToolRunResult` 的结构化 output、模型 observation、artifacts、events 和 audit。
6. 动态发现改为结构化 ToolRef，文本输出不得隐式注册或授权工具。
7. 所有调用使用稳定 Action/ToolCall ID 和 contract version。
8. 保留 staging 和数据引用优势，后续 Context/Persistence 板块确定预算及恢复协议。
9. 迁移必须兼容现有 `AgentResult`、`ToolOutput` 别名、Web/企微和媒体任务，不一次重写。

下一板块进入 Tool Executors，逐一追踪内置查询、媒体、ERP、文件、搜索、沙盒和外部动作的真实执行链、参数、任务状态、取消、幂等与产物落点。
