# AGENT 05：Policy、权限、成本与副作用治理

> 状态：第一轮源码对标完成
> 日期：2026-07-18
> Grok Build 基线提交：`c68e39f60462f28d9be5e683d9cbe2c57b1a5027`
> 研究边界：Hooks、权限模式、用户授权、成本/积分、沙盒、资源范围和副作用控制
> 后续专项：ToolBridge、专业 Executor、Persistence、Skills/MCP 和端到端链路继续核验

## 1. 结论摘要

Policy 层不是一个“是否弹窗”的开关，而是每个 Action 在执行前后的确定性决策链：

```text
模型提出 Action
→ Schema 与能力校验
→ 用户意图授权范围
→ 租户 / 身份 / 数据范围
→ 风险与副作用
→ 成本与配额
→ Hook / 管理策略
→ Allow | Ask | Deny | Wait
→ 执行
→ 结果、成本和审计结算
```

Grok Build 强项是文件、命令、MCP 和 Web 工具的规则化 Permission Manager，以及完整 Pre/Post Hook 生命周期；但它不解决本项目图片、视频、定时任务等付费 SaaS 的积分事务。

EVERYDAYAIONE 强项是媒体成本计算、预扣/确认/退款、组织权限、数据范围和 nsjail 沙盒。但当前 Policy 分散在 Tool Schema、ChatToolMixin、各 Executor、CreditMixin、专业 Handler 和 API 权限服务中，没有统一 `PolicyDecision`。

第一轮结论为“融合升级”：

1. 用户明确说“生成/执行”可作为本轮授权，不额外弹窗。
2. 授权必须形成结构化 `AuthorizationGrant`，绑定原始用户消息、Action 类型、数量、成本上限和资源范围。
3. 一段 Prompt 或 AI 普通文本永远不能作为执行授权。
4. 同一授权内可拆成多次工具调用，但累计数量、成本和资源必须受 Grant 约束；超出时重新确认。
5. 保留媒体积分事务、组织权限和沙盒；统一收口为执行前 Policy Gate 与执行后 Settlement。
6. 安全、计费和租户隔离类 Policy 必须 fail closed；扩展型 Hook 可按用途选择 fail open。

## 2. Grok Build Permission 架构

### 2.1 权限模式

源码：`xai-grok-agent/src/config.rs::PermissionMode`

定义：

- `default`
- `acceptEdits`
- `auto`
- `dontAsk`
- `bypassPermissions`
- `plan`

源码注明当前只有 `BypassPermissions` 直接从 Agent Definition 接到 Spawn，其他模式仍有 forward-compat 成分。运行期实际模式还受 Session YOLO、Auto Mode、Plan Mode 和配置规则共同控制。

这说明不能仅凭枚举存在就判断能力完整，必须追踪最终 Permission Manager。

### 2.2 Permission Manager 装配

源码：`acp_session_impl/spawn.rs`

装配顺序：

```text
继承父 Session PermissionHandle
或
从工作区解析 Permission Config
→ 读取 Managed Policy 是否禁用 YOLO
→ 丢弃被 Managed Policy 禁止的 CLI catch-all allow
→ 合并 CLI Rules 与文件 Rules
→ 提取 deny-read globs
→ 连接远程 HITL Permission Transport，失败则本地询问
→ 创建 Permission Manager
→ 按 Session 开关启用 Auto Classifier
→ 注入最近对话作为分类依据
```

若子 Agent 继承父 PermissionHandle，它共享父权限管理器而不重复创建。这避免子 Agent 自行扩大权限。

### 2.3 规则与优先级

Grok Workspace 权限规则支持：

- `Allow`
- `Ask`
- `Deny`

规则可针对：

- Read/Edit/Grep 路径。
- Bash 命令模式。
- WebFetch 域名。
- MCP Tool/Server。

关键语义：

- Deny 优先于 Allow。
- Managed Policy 可禁止 bypass permissions。
- `dontAsk` 对未预批准工具静默拒绝。
- 无法可靠解析的敏感路径、符号链接和 Shell Operand 升级为 Ask。
- 对 `.env`、密钥、云配置、SSH、Terraform State 等提供 deny 模式。

这是参数级授权，而不是只按工具名分级。

### 2.4 Auto Permission

Auto Mode 会把最近对话送入后台分类器，判断工具是否符合用户请求。每次 Permission 请求前可刷新 transcript。

它改善交互流畅度，但分类器不是最终安全边界：

- Managed Deny 仍优先。
- YOLO 可被组织策略禁用。
- Permission Manager 仍产生明确 Decision 和来源。

本项目可以借鉴“对话授权分类器”，但只能生成授权候选；付费和外部副作用仍需确定性规则验证数量、成本与范围。

### 2.5 Tool Preflight 顺序

源码：`acp_session_impl/tool_calls.rs::prepare_tool_call`

实际顺序：

```text
登记 Pending Tool Call
→ 确认 MCP 是否可用
→ 规范化并解析 JSON
→ ToolBridge Schema 解析
→ 推导 AccessKind
→ Plan Mode Gate
→ PreToolUse Hooks
→ Permission Manager
→ 构造 PreparedToolCall
```

Permission Decision 包含：

- `Allow`
- `Ask`
- `Reject(reason)`
- `PolicyDeny(reason)`
- `Cancelled`
- `FollowupMessage`

拒绝或取消时仍写入对应 Tool Result，保证模型协议配对。PolicyDeny 可以回填模型继续选择其他方案；用户明确拒绝可终止当前 Turn。

### 2.6 Hooks

Grok Hook 事件包括：

- SessionStart/SessionEnd
- PreToolUse
- PostToolUse
- PostToolUseFailure
- PermissionDenied
- Notification
- 其他 Agent/Turn 事件

`PreToolUse`：

- 按配置顺序串行运行。
- Matcher 可按工具过滤。
- 只有显式 Deny 阻止执行。
- 超时、崩溃、命令不存在和畸形输出均 fail open。

其源码理由是运行在受保护环境中，Hook 故障不属于攻击模型。这一假设不适合多租户 SaaS 的安全、计费和合规 Hook。

因此目标架构必须区分：

| Hook 类型 | 失败策略 |
|---|---|
| 安全/租户/成本/合规 Policy | fail closed |
| 审计、指标、通知 | fail open，进入补偿队列 |
| 用户自定义扩展 Hook | 按声明决定，默认不得扩大权限 |

## 3. EVERYDAYAIONE 当前 Policy

### 3.1 三种 SafetyLevel

源码：`backend/config/chat_tools.py`

```text
SAFE       直接执行
CONFIRM    注释称“通知用户后执行”
DANGEROUS  必须用户确认
```

当前映射：

| 级别 | 工具 |
|---|---|
| CONFIRM | `generate_image`、`generate_video`、`image_agent`、`code_execute` |
| DANGEROUS | `erp_execute`、`trigger_erp_sync`、`file_delete` |
| SAFE | 其他所有未声明工具 |

运行事实与命名存在偏差：

- `CONFIRM` 只记录日志，随后立即执行，不发送通知，也不等待确认。
- 只有 `DANGEROUS` 才发送 WebSocket 确认并最多等待 60 秒。
- 未注册的新工具默认 SAFE，属于 allow-by-default。

所以 `CONFIRM` 实际语义更接近“已由明确用户意图授权、执行前展示成本状态”，而不是确认。

### 3.2 当前产品原则的真实实现

用户明确要求图片生成时，模型选择 `generate_image/image_agent`，工具会直接执行并扣费。这符合用户期望的顺畅体验。

但系统当前没有结构化证明：

- 哪条用户消息授权了生成。
- 授权的是 1 张还是多张。
- 多段 Prompt 能否拆成几次生成。
- 用户接受的最高积分是多少。
- 重试和替代模型是否仍属于同一授权。
- 模型在后续轮次主动再生成一张是否越权。

因此当前链路是“Tool Call 即视为授权”，而不是“Tool Call 必须落在用户 Grant 内”。

### 3.3 推荐 AuthorizationGrant

目标结构示意：

```text
AuthorizationGrant
├── grant_id
├── source_message_id
├── actor_user_id / org_id
├── action_kind
├── allowed_count
├── max_total_cost
├── resource_scope
├── parameter_constraints
├── valid_until / valid_for_run
└── status
```

例子：

用户说“用这三段提示词分别生成图片”，可形成：

```text
action_kind = image.generate
allowed_count = 3
max_total_cost = 当前模型三张预估值
valid_for_run = 当前 Run
```

Agent 可拆成三个 Tool Call，不再逐个弹窗。第四个 Tool Call、提高分辨率导致超过总成本、或改为视频时必须请求新授权。

用户只说“写三段提示词”时不产生 Grant，模型即使输出完整 Prompt 也不能执行。

### 3.4 工具确认当前依赖进程内状态

`ChatToolMixin` 使用全局 `ws_manager`：

```text
send confirm request
→ wait_for_confirm(tool_call_id, 60s)
→ approved / rejected / timeout
```

问题：

- Pending Confirm 保存在进程内映射。
- API 与独立 Actor Worker 跨进程时未证明可贯通。
- 重启后无法恢复。
- ToolLoopExecutor 的 headless 模式没有 task_id 时直接放行危险操作。
- ToolLoopExecutor 捕获确认机制异常后 fail open。

后两项对外部副作用属于高风险语义。目标系统中：

- 无交互渠道不能默认放行 Ask 类动作。
- 确认系统异常必须进入 `waiting_policy` 或 Deny。
- Pending Interaction 必须持久化。

### 3.5 权限模式

主 Chat 的 `auto/ask/plan` 同时控制 Prompt 和可见工具：

- Plan 移除部分执行工具。
- Auto 提示模型自主执行。
- Ask 当前没有统一“所有副作用都询问”的执行层语义。
- 非法 mode 静默降级为 Auto。

这会把产品交互模式和安全授权模式混为一体。

目标应拆分：

```text
InteractionMode = auto | ask | plan
RiskDecision = allow | ask | deny
```

即使 InteractionMode=auto，Managed Policy、成本上限和不可逆动作仍可 Deny/Ask。非法模式必须安全降级为 Ask/Plan，而不是 Auto。

### 3.6 组织权限和数据范围

`services/permissions/` 已有：

- Boss/VP/Manager/Deputy/Member 职位权限。
- 部门权限点。
- 资源创建者范围。
- 查询前注入数据范围，而不是查询后过滤。
- 无任职时限制为本人。

这是本项目相对 Grok 的 SaaS 优势，应进入统一 Policy Context。

当前断层：

- 业务 API 如定时任务显式调用 `check_permission()`。
- 主 Agent ToolExecutor 的通用工具分发没有统一调用 PermissionChecker。
- ERP 内部可能另有组织过滤，但尚未形成每个工具的 `required_permission` 元数据。
- `apply_data_scope()` 参数 `permission_code` 当前标记为预留、未实际校验。
- 部门子树仍为简化直接 ID 匹配。

### 3.7 媒体成本与积分事务

同步 Agent Tool 路径为：计算成本 → `_lock_credits()` → Provider 同步等待 → 成功 confirm / 失败 refund → OSS/Workspace Artifact。

参数：

- 图片等待最多 90 秒，轮询 2 秒。
- 视频固定 10 秒内容，等待最多 300 秒，轮询 5 秒。
- 每次 Tool Call 生成新的随机 task_id。

异步 Image/Video Handler 路径为：余额预检 → 每任务独立锁积分 → 创建 Provider Task → 持久化 Task/transaction_id → Webhook 成功确认或失败退款。

它支持 1~4 张图片、每张独立 Prompt、失败替代模型和 OSS 持久化，是必须保留的专业执行器。

### 3.8 积分事务风险

`CreditMixin` 和 `CreditService.lock_credits()` 当前都采用：

```text
读取余额
→ 乐观锁 UPDATE users.credits
→ INSERT credit_transactions
```

余额扣减和 Transaction INSERT 不是同一个数据库事务。若第二步成功、第三步失败，用户积分已经减少，但没有 transaction_id 可确认或退款。

其他风险：

- `task_id` 文档称幂等键，但 `transaction_id` 每次随机生成，当前代码未证明数据库对 task_id 有唯一约束。
- 同步 Agent 媒体 Tool 每次模型重试都创建新 task_id，无法天然去重。
- `_confirm_deduct()` 只是按 ID 更新为 confirmed，没有限制原状态必须 pending。
- `CreditMixin` 乐观锁只重试一次；`CreditService` 最多重试 3 次，两套行为不同。
- Agent 同步媒体和异步 Handler 使用两套计费封装。

退款使用 `atomic_refund_credits`，具备 CAS 和幂等语义，相对更强。目标应把 lock/confirm/refund 全部收口为数据库事务 RPC，并以 Action Idempotency Key 为业务唯一键。

### 3.9 定时任务预算

ScheduledTask 在执行前锁定 `max_credits`，范围为 1~1000；成功后按实际 Token 计算，最低 1 分、最高不超过 max_credits，差额退款。

该模型允许用户预先授权最大成本、Runtime 在范围内自主规划、最终按实际用量结算，应成为未来 Goal Run CostBudget 的主要参考，而不是每一个工具都弹窗。

### 3.10 沙盒

现有沙盒组合 AST 黑名单、受限 imports/builtins、scoped os/shutil/pathlib、删除转 `file_delete`、子进程/Kernel 超时与限额，以及 nsjail bind mount、网络隔离和 cgroup；DuckDB 显式限制为 3GB。

值得保留的原则是：Python 检查用于错误提示，nsjail 才是安全边界。

风险：

- `scoped_os._check_path()` 不做 workspace 边界校验，本地开发依赖“信任”，不能作为生产等价环境。
- `code_execute` 被标为 CONFIRM 但实际直接执行。
- 沙盒工具预算最少给 5 秒，即使父预算即将耗尽。
- `code_execute` 被标记 concurrency-safe，多次调用可并行占用 Kernel/资源；需要全局及租户级并发配额。

## 4. 统一 Policy 模型

### 4.1 ActionPolicyContext

`ActionPolicyContext` 包含 user/org/channel/session/run、source message、AuthorizationGrant、AgentDefinition、EffectiveCapabilities、工具及参数、资源范围与冲突键、成本预算、交互模式、Managed Policy、历史尝试和幂等键。

### 4.2 PolicyDecision

`PolicyDecision` 包含 `allow|ask|deny|wait`、reason code、policy source、有效参数、授权成本、幂等键、审计字段和过期时间。

`allow` 不是模型决定，而是所有强制 Policy 的交集。

### 4.3 推荐执行顺序

```text
1 Schema / Tool Existence
2 Agent Capability
3 Managed Deny
4 Tenant / Role / Data Scope
5 Resource Boundary
6 User Authorization Grant
7 Risk / Side Effect
8 Cost Estimate / Budget Reserve
9 Blocking Extension Hooks
10 Idempotency Claim
11 Execute
12 Validate Result
13 Settle Cost
14 Persist Audit / Artifact / Event
15 Non-blocking Hooks
```

成本 Reserve 必须晚于所有廉价静态校验，早于外部 Provider 调用。Idempotency Claim 必须在副作用前完成。

## 5. 工具风险分类建议

| 类别 | 示例 | 默认策略 |
|---|---|---|
| Pure | Mermaid、ECharts、本地格式化 | Allow |
| Read | 天气、搜索、授权范围内 ERP 查询 | Allow + Scope |
| Compute | 沙盒计算、文件分析 | Allow + Sandbox + Quota |
| Paid Generate | 图片、视频 | Grant + Cost Reserve |
| Reversible Write | 文件生成、草稿更新 | Grant 或 Ask + Audit |
| Destructive | 删除、覆盖、ERP 写入 | Explicit Ask + Idempotency |
| External Communication | 发消息、推送第三方 | Recipient/Content Scope + Ask |
| Deployment/Admin | 部署、权限调整、生产变更 | 强确认 + Managed Policy |

风险必须由 Tool Metadata 显式声明；未知工具默认 Deny/Ask，不能默认 SAFE。

## 6. 边界与验收场景

| 场景 | 预期 |
|---|---|
| “写三段生图提示词” | 无 Grant，不执行 |
| “按这三段分别生成” | Grant count=3，可拆三次调用 |
| 模型请求第 4 张 | Ask 或 Deny，不扣费 |
| 模型提高分辨率超预算 | 重新确认成本 |
| 相同 Tool Call 重放 | 命中幂等结果，不重复扣费 |
| Provider 已创建任务但 DB 保存失败 | 可调和，不只退款后丢失外部任务 |
| 积分锁定事务记录失败 | 整体回滚 |
| 多并发 Action 争抢余额 | 数据库原子串行，不能超扣 |
| 用户拒绝危险操作 | 持久化 rejected，Tool Result 配对 |
| Confirm 服务重启 | 恢复 waiting_policy，不放行 |
| Headless 遇到 Ask | 保持等待或按预授权 Grant；不得默认放行 |
| Managed Deny 与用户 Allow 冲突 | Deny 胜出 |
| Hook 超时 | 安全/成本 Hook Deny；审计 Hook 异步补偿 |
| 无组织任职 | Agent 工具与 API 一样 fail closed |
| 查询他人数据 | 查询前注入 Scope |
| 本地开发未启 nsjail | 明确标记非安全环境，禁止当生产验收 |
| Sandbox 并发过高 | 租户/用户/全局配额限流 |
| 退款失败 | 进入调和队列并告警，不只写日志 |

## 7. 候选影响范围

- `backend/config/chat_tools.py`
- `backend/services/handlers/chat_tool_mixin.py`
- `backend/services/handlers/permission_mode.py`
- `backend/services/agent/tool_loop_executor.py`
- `backend/services/agent/tool_executor.py`
- `backend/services/media_tool_executor.py`
- `backend/services/handlers/image_handler.py`
- `backend/services/handlers/video_handler.py`
- `backend/services/handlers/mixins/credit_mixin.py`
- `backend/services/credit_service.py`
- `backend/services/permissions/`
- `backend/services/sandbox/`
- Authorization、Policy Decision、Cost Budget、Pending Interaction 和幂等持久化协议

当前不修改上述实现。数据库 RPC、唯一约束和迁移方案必须等 Persistence 与 Tool Executor 板块核验后统一设计。

## 8. 第一轮证据与风险等级

高风险候选：

1. 积分余额扣减与 transaction INSERT 非同一事务。
2. 危险工具在 headless 或确认机制异常时存在 fail-open 路径。
3. Pending Confirm 依赖进程内状态，独立 Actor Worker 跨进程恢复未闭环。
4. 新工具未登记 SafetyLevel 时默认 SAFE。

中风险候选：

1. CONFIRM 命名与真实行为不一致。
2. 用户授权没有结构化数量和成本范围。
3. 主 Agent 工具未统一接入组织 PermissionChecker。
4. InteractionMode 与 RiskDecision 混合。
5. 沙盒本地开发与生产安全边界不等价。

本阶段只记录和确定目标边界，不修改业务代码。后续 ToolBridge、Executors、Persistence 和端到端板块必须复核上述风险是否已有其他层补偿。
