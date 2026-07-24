# AGENT 11：MCP / Plugins / Hooks 扩展运行时

> 状态：第一轮源码对标完成
> 日期：2026-07-18
> Grok Build 基线提交：`c68e39f60462f28d9be5e683d9cbe2c57b1a5027`
> 研究边界：MCP、Plugin、Hook 的发现、认证、生命周期、隔离和治理
> 后续专项：Subagents、Persistence、Protocol/UI、Observability 继续核验

## 1. 结论摘要

三种扩展机制必须分开：

| 机制 | 本质 | 解决的问题 |
|---|---|---|
| MCP | 外部能力协议 | 如何发现和调用远程 Tool/Resource |
| Plugin | 安装与版本单元 | 如何打包 Skill、Agent、Hook、MCP 配置 |
| Hook | 生命周期拦截点 | 如何在 Session/Model/Tool 前后执行策略或观察 |

它们进入 Agent Runtime 的正确位置：

```text
Plugin Registry ──提供──> Skills / Agent defs / MCP configs / Hooks
                              ↓
MCP Connection Manager → External Capability Catalog
                              ↓
Tool Catalog → EffectiveToolset → ToolBridge
                              ↓
             Pre Hooks → Policy → Executor → Post Hooks
```

Plugin 不是模型直接调用的对象；MCP Server 不是自动可信工具；Hook 也不能取代核心
Policy。真正执行仍必须落回统一的 Action、Policy、ToolRunResult、Artifact 和审计链。

Grok Build 已把三类扩展做得相当完整，但其默认面向本地开发机。EVERYDAYAIONE 是
多租户 SaaS，不能照搬“每个会话启动任意 stdio 进程、项目目录自动提供插件”的信任
模型。推荐采用：

- 平台/租户管理的 Plugin Registry。
- 独立 MCP Gateway/Connection Manager。
- 内部强类型 Runtime Hooks。
- 外部 Hook 仅作为受限 Extension Hook。

## 2. Grok MCP 架构

### 2.1 配置与传输

Grok 支持：

- 本地 `stdio`：command、args、env。
- HTTP/SSE：url、headers。
- Streamable HTTP：可带动态 session ID header。
- OAuth：浏览器授权、动态客户端注册或自带 client credentials。

主要配置：

| 参数 | 默认值 |
|---|---:|
| `enabled` | true |
| `startup_timeout_sec` | 30 秒 |
| `tool_timeout_sec` | 6000 秒 |
| `tool_timeouts.<tool>` | 可逐工具覆盖 |
| MCP inline output | 20,000 bytes |

6000 秒适合作为兼容性兜底，不适合作为本项目通用工具超时。SaaS Worker 被单个远程
调用占用近两小时不可接受；长任务应返回 `Accepted + external_task_id`，由异步任务
链继续。

配置按 user、repo root、cwd 逐层覆盖，同名 Server 是整体替换而非字段合并。还兼容
Claude、Cursor 和标准 `.mcp.json`，高优先级来源胜出。

### 2.2 工具目录

MCP 工具命名为：

```text
server__tool
```

避免不同 Server 同名冲突。Grok 没把全部外部 schema 无条件塞进模型，而是提供：

- `search_tool`：搜索已启用 MCP 能力。
- `use_tool`：调用限定名工具。

资源另有 list/read 路径。服务端发送 `tools/list_changed` 或
`resources/list_changed` 时，Session 重建快照。

这与本项目目标 Tool Catalog 一致：外部能力先作为目录项，模型命中后再取完整
schema；不能因为接入 MCP 就让所有 schema 常驻 Context。

### 2.3 输出控制

MCP ToolOutput 超过 20,000 bytes 时：

1. 上下文只保留截断内容和提示。
2. 完整 payload 落入 Session `mcp/` 目录。
3. 模型可依据引用继续读取。

长行阈值为 2,000 bytes。限制优先级支持 requirements、环境变量、repo config、
user config 和默认值。

本项目应沿用“inline preview + durable ref”，但完整输出进入 Artifact/ToolOutput
Store，而不是 Worker 本地目录。

### 2.4 健康和恢复

Grok 每 500ms 检查 Ready Client 的 transport 状态，只在
`Ready + transport closed` 时发一次事件。Session Dispatcher 以 50ms 窗口合并
重复状态。

stdio 重启退避：

```text
1s → 4s → 16s
累计 21s 后 parked
```

HTTP 首次立即恢复，随后：

```text
1s → 4s → 16s → 30s → 30s → 30s → 30s
共 8 次尝试，约 2.5 分钟
```

退避期间持续检查 Server 是否仍配置、启用以及 Session 是否关闭。禁用后立即取消，
旧 Client 的迟到 liveness 事件不能重启新连接。

### 2.5 调用恢复

HTTP 调用遇到可恢复 transport error 时可重连后重试一次；invalid params 不重试；
外层超时会重置 transport，但不会盲目重复有副作用调用。

这是关键边界：网络断开并不等于服务端没有执行。外部写操作只有在 Provider/MCP
提供幂等键或可查询 Action 状态时才能自动重试，否则结果必须是 `Unknown`。

## 3. Grok Plugin 架构

### 3.1 打包内容

Plugin 可同时包含：

- `skills/`
- `commands/`
- `agents/`
- `hooks/hooks.json`
- `.mcp.json`
- `.lsp.json`

`plugin.json` 可覆盖默认路径并声明 name、version、description、author、repository、
license 等。Manifest 未知字段被忽略以保持前向兼容；名称最多 64 字符，要求小写
字母、数字和连字符。

组件路径相对 Plugin root 解析，canonicalize 后必须仍位于 root 内，阻止 `..` 和
symlink 逃逸。

### 3.2 发现、安装与更新

来源包括 Session 注入、CLI、项目、用户、自定义路径和 Marketplace。支持 Git URL、
GitHub shorthand、ref pin 和子目录。Plugin 可以 enable/disable/uninstall/update，
Marketplace catalog 当前格式版本为 1。

Plugin 同名时按 scope 优先；组件保留 Plugin namespace。更新不是简单覆盖运行中
事实：活跃 Session/SkillRun 必须继续使用已固定的版本和 hash。

### 3.3 信任分层

Grok 把“启用”和“信任”分开：

- 启用后 Skill/Agent 可被发现。
- 未信任时 Hook、MCP、LSP 等可执行组件保持 inactive。
- 项目 Plugin 需要显式信任。
- 用户目录和 Session/CLI 明确注入的 Plugin 自动信任。

安装命令若没有 `--trust` 会展示风险并停止，不会边安装边执行。

本项目不能使用“用户目录即可信”。SaaS 中信任必须绑定组织、发布者、版本、内容
hash、审核记录和权限声明；普通成员安装也不能自动获得组织数据权限。

## 4. Grok Hooks 架构

### 4.1 事件

Grok 支持：

- SessionStart / SessionEnd
- UserPromptSubmit
- PreToolUse
- PostToolUse / PostToolUseFailure
- PermissionDenied
- Stop / StopFailure
- Notification
- SubagentStart / SubagentStop
- PreCompact / PostCompact

只有 `PreToolUse` 可以阻止执行，其余为被动事件。

### 4.2 Handler

Hook 可执行 command 或 HTTP POST，默认超时 5 秒。matcher 对工具真实名称做正则
匹配；MCP 调用使用 `server__tool`，不是内部 `use_tool`。

PreToolUse 输出：

```json
{"decision":"allow"}
{"decision":"deny","reason":"..."}
```

多个 Hook 按配置顺序串行；任一明确 deny 即短路。异常、超时、崩溃、命令不存在或
输出格式错误全部 fail-open，只有明确 deny 才阻断。

### 4.3 安全边界

Hook 进程获得 Session、Workspace、Plugin root/data 等环境变量；保留变量不能被
用户 env 覆盖。显示和日志使用未展开的 command/url，避免 secret 展开值泄漏。

项目 Hook 与项目 MCP 共用 folder trust。Plugin Hook 只有 Plugin trusted 才运行。

Grok 的 fail-open 适合可选自动化，不适合财务、删除、部署、ERP 写入等强安全策略。
核心 Policy 必须 fail-closed；外部 Hook 只能进一步 deny，不能放宽核心 Policy。

## 5. EVERYDAYAIONE 现状

### 5.1 MCP / Plugin

产品 Runtime 当前没有：

- MCP Server 配置与连接管理。
- 外部 Tool/Resource discovery。
- OAuth credential lifecycle。
- Plugin manifest、安装、版本、信任和租户范围。
- MCP health/reconnect 或 Tool catalog reconciliation。

现有 Provider、ERP、搜索和文件能力都是项目内直接集成，不能被误称为 MCP。

### 5.2 Hooks

项目已有内部 `LoopHook`：

- `on_turn_start`
- `on_tool_start`
- `on_tool_end`
- `on_text_synthesis`

实现包含 Progress、Audit、TemporalValidator、FailureReflection 和
AmbiguityDetection。它们由 `ToolLoopExecutor` 串行调用，适合作为内部 Runtime
Hook 雏形。

但它只覆盖 ERP/ScheduledTask 共用循环，不覆盖统一 `execute_chat` 的全部 Action、
Policy、异步媒体、MCP 和 Goal 生命周期。部分 Hook 会直接 mutate messages，缺少
统一结构化 HookDecision。

沙盒 `emit_auto_hooks.py` 是运行库拦截，不属于 Agent 生命周期 Hook；Provider
Webhook 是异步回调入口，也不是 Hook。命名必须澄清。

## 6. 目标扩展架构

```text
Extension Registry
  ├─ Plugin Packages
  ├─ MCP Connections
  └─ Hook Registrations
          ↓
Capability Reconciler
          ↓
Tool / Skill / Resource / Agent Catalog
          ↓
EffectiveCapabilities
          ↓
Runtime Hook Pipeline
  BeforeRun
  BeforeModel
  BeforeAction → Core Policy → Extension Deny Hooks
  AfterAction
  AfterArtifact
  AfterRun
```

### 6.1 Extension Registry

记录：

```text
extension_id / org_id / owner_id
type: plugin | mcp | hook
source / publisher / version / content_hash
enabled / trust_level / review_status
declared_capabilities[]
granted_scopes[]
config_ref / credential_ref
installed_at / updated_at
```

Secret 只通过 Credential Vault 引用，禁止进入普通 JSON config、日志、Prompt 或
Plugin data。

### 6.2 MCP Gateway

SaaS 推荐独立 Gateway，而不是 Web/Worker 每进程各自连接：

- 按 org/user credential 建立隔离连接。
- 统一 OAuth refresh、限流、超时、熔断和 health。
- Tool schema/version 形成 Catalog snapshot。
- Tool call 注入 tenant/run/action/idempotency metadata。
- 大输出落 Artifact Store。
- Worker 重启不丢连接状态和调用审计。

本地 stdio MCP 第一阶段只允许平台运维配置并运行在隔离 Runner，禁止租户提交任意
command。远程 HTTP MCP 优先。

### 6.3 MCP ToolDescriptor

```text
tool_id: mcp:{connection_id}:{server_tool_name}
display_name / description / input_schema
schema_hash / catalog_revision
risk_level / side_effect
supports_idempotency / supports_async
default_timeout / max_timeout
required_scopes[]
availability
```

模型看到稳定逻辑 ID；真实 endpoint、token 和内部 connection ID 不进入 Prompt。

### 6.4 Runtime Hook

内部 Hook 使用强类型接口：

```text
BeforeActionResult
  allow | deny | require_confirmation
  reason_code
  annotations

AfterActionResult
  annotations
  emitted_events[]
```

核心 Policy 先决定最大权限，Extension Pre Hook 只能保持或收紧：

```text
FinalDecision = CorePolicy ∩ ExtensionHooks
```

审计、指标和 UI Hook 异步执行；安全 Hook 有短超时且必须本地/平台托管。不能让未知
HTTP Hook 决定高风险动作。

## 7. MCP 调用链

```text
模型选择 catalog tool
→ ToolBridge 解析逻辑 ID
→ Core Policy 检查用户授权、风险、数据范围
→ Extension deny hooks
→ MCP Gateway 读取 credential ref
→ schema hash 校验并调用
→ Completed / Rejected / Unknown / Accepted
→ 大结果 Artifact 化
→ ToolRunResult 回模型
→ AfterAction hooks + audit
```

`Accepted` 必须包含外部 operation ID 和查询/回调策略。连接中断且无法确认服务端是否
执行时返回 `Unknown`，禁止自动重复写操作。

## 8. Catalog 与 Context

MCP 接入后最大的隐性成本是 Tool schema：

1. Context 常驻仅放 Server/Plugin 能力摘要。
2. 模型通过 catalog search 找候选。
3. 只把本 Run 候选的完整 schema 加入 EffectiveToolset。
4. `tools/list_changed` 生成新 catalog revision，不修改进行中 Run 的 snapshot。
5. 调用时校验 schema hash；变化则拒绝旧调用并让模型重新规划。

Resource 使用 Search/List → Read 两段式；大 Resource 进入 Artifact/Knowledge 索引，
不直接整块注入。

## 9. 权限与信任矩阵

| 来源 | 默认发现 | 默认执行 | 要求 |
|---|---:|---:|---|
| 平台内置 | 是 | 按 Policy | 平台发布审核 |
| 租户管理员 Plugin | 是 | 按授权 | 版本/hash 审核 |
| 普通用户 Plugin | 私有可见 | 否 | 管理员批准可执行组件 |
| 远程 HTTP MCP | 配置后 | 否 | OAuth/scope + tool risk |
| stdio MCP | 否 | 否 | 平台隔离 Runner 白名单 |
| 外部 HTTP Hook | 配置后 | 被动事件 | SSRF、签名、出站域名控制 |
| Project/local Hook | SaaS 不适用 | 否 | 转平台托管 Hook |

Plugin trust 只代表“允许加载代码”，不代表其所有 ToolCall 自动授权。

## 10. 关键参数建议

| 参数 | 初始建议 |
|---|---:|
| MCP connection startup | 30 秒 |
| 普通查询 Tool timeout | 30 秒 |
| 较慢同步 Tool timeout | 120 秒 |
| 超过 120 秒的操作 | 必须 Accepted 异步化 |
| inline ToolOutput | 20 KB 起步 |
| health poll | Gateway 5～15 秒，非每会话 500ms |
| HTTP reconnect | 1/4/16/30 秒后熔断 |
| stdio restart | 1/4/16 秒，平台 Runner 内 |
| Extension Hook timeout | 2 秒；通知型异步 |
| Hook payload inline | 8 KB，其余使用 ref |
| Catalog snapshot TTL | 5 分钟并响应 list_changed 主动失效 |

500ms 在本地 Client 成本很低，但 SaaS 若每租户、每 Session 轮询会放大；应由共享
Gateway 维护连接级 health。

## 11. 边界场景

| 场景 | 处理 |
|---|---|
| MCP 启动失败 | 标记 unavailable，不阻塞无关工具 |
| OAuth 过期 | refresh；失败转 auth_required，不把登录页当 ToolOutput |
| schema 热更新 | 新 revision 只作用新 Run，旧调用 hash 不匹配则拒绝 |
| 同名工具 | 全部使用逻辑 namespace |
| 输出超限 | preview + Artifact ref |
| 写操作超时 | Unknown，先查询状态，不直接重试 |
| Server 反复断线 | 熔断并从候选目录降权/移除 |
| Plugin 更新中有活跃 Run | 固定旧版本，更新原子切换新版本 |
| Plugin 被禁用 | 禁止新调用；活跃外部等待可收结果但不得发起下一步 |
| Hook 超时 | 可选 Hook fail-open；核心安全策略不依赖它 |
| 多 Hook 冲突 | deny/confirm 比 allow 严格，确定性聚合 |
| Hook 修改参数 | 第一阶段禁止；后续只能生成新 Action 并重新过 Policy |
| SSRF/恶意 endpoint | 出站 allowlist、DNS/IP 重绑定检查、私网默认拒绝 |
| Credential 泄漏 | Vault 引用，日志与模型只见 credential alias |

## 12. 与 Grok 的取舍

直接采用：

- MCP namespace、search/use 渐进披露。
- Tool/Resource list_changed reconciliation。
- 大结果截断并保留完整引用。
- liveness、取消感知退避和调用错误分类。
- Plugin enable 与 trust 分离。
- Plugin root containment。
- Hook 生命周期和明确 deny 语义。

调整后采用：

- Grok 本地配置优先级改为平台/租户/用户数据库配置。
- per-session Client 改为共享隔离 MCP Gateway。
- folder trust 改为组织审核、版本/hash 和 capability grant。
- Hook fail-open 仅用于非核心扩展。

不采用：

- 租户任意 stdio command。
- 6000 秒同步 Tool timeout。
- 将 project/user local directory 自动视为 SaaS Plugin 来源。
- Plugin trust 自动等价为数据和副作用授权。

## 13. 分阶段落地边界

本轮只形成设计，不修改运行代码、数据库或 API。总体重构阶段建议：

1. 先定义 Extension/MCP Descriptor，接入 Tool Catalog 但不允许调用。
2. 建立平台级 HTTP MCP Gateway 和 Credential Vault 接口。
3. 加入只读 MCP 工具试点，验证 schema、Context 和 Artifact。
4. 接入 Core Policy、审计、Unknown/Accepted 和幂等协议。
5. 再建设 Plugin Registry，以固定版本提供 Skill/MCP config。
6. 将现有 LoopHook 收口为内部 Runtime Hook，并覆盖统一 execute_chat。
7. 最后开放租户管理界面和受限 Extension Hooks。

下一层进入 Subagents / Background，核验子 Agent 的上下文、工具、权限、并发、结果
回传、后台生命周期，以及它与 Goal、SkillRun、MCP 的关系。
