# Agent Runtime 扩展运行时：Skill、MCP、Plugin 与 Hook

> 状态：总体设计 / 第一轮冻结
> 日期：2026-07-18
> 范围：可扩展能力如何安装、发现、加载、授权、执行、升级和审计
> 对标：Grok Build `c68e39f60462f28d9be5e683d9cbe2c57b1a5027`

## 1. 结论

四种扩展必须分开建模：

| 机制 | 本质 | 不是什么 |
|---|---|---|
| Skill | 可发现的指令、资源与工作流说明 | 不是授权，也不是 Executor |
| MCP | 外部 Tool/Resource/Prompt 的协议 | 不是自动可信能力 |
| Plugin | Skill、Agent、MCP 配置、Hook 的安装版本单元 | 不是模型工具 |
| Hook | Runtime 生命周期拦截和观察 | 不是核心 Policy |

统一接入：

```text
Extension Sources
  -> Extension Registry
  -> Tenant Enablement + Trust
  -> Capability Catalog
  -> Context progressive disclosure
  -> EffectiveToolset / Policy
  -> Action / Executor
  -> Artifact / RuntimeEvent / Audit
```

核心规则：

1. 扩展只能提供“可请求能力”，不能扩大用户、组织、通道或父 Run 权限。
2. 安装、启用、升级、授权是管理动作，模型和扩展不能自行完成。
3. 每个 Run 固定 Extension/Skill/MCP schema revision 和内容 hash。
4. Skill 指令、MCP Prompt、ToolOutput 和 Plugin 内容均视为不可信输入。
5. 外部执行必须进入 Action、Policy、Executor 和 Artifact 主链。
6. 平台强策略不能由 Hook 替换、跳过或降级。
7. 产品扩展与开发代理 `.cursor/skills` 物理、逻辑隔离。

## 2. 项目上下文

### 2.1 架构现状

产品侧没有 Skill Registry、MCP、Plugin 或通用 Extension Registry。`backend/skills/data-usage.md` 和 `doc-usage.md` 只是只读挂载到 Sandbox 的操作指南；没有 manifest、触发、版本或 Policy。`.cursor/skills` 服务开发代理，信任域完全不同。现有 `LoopHook` 有进度、审计、时间校验、失败反思和歧义检测，但只覆盖部分 ToolLoop 生命周期，且部分 Hook 失败采用各自处理。

### 2.2 可复用能力

- Agent/Tool Catalog 和 EffectiveToolset。
- Policy Gate、AuthorizationGrant 和 CapabilityEnvelope。
- ContextPlan 的 Skill/MCP 目录预算和按需 Get。
- Executor SPI 与 MCP Gateway Executor。
- Goal、SkillRun、SubRun、Artifact 和 RuntimeEvent。
- 现有 LoopHook 的业务逻辑可迁移为内部 Runtime Hook。
- ResourceManifest、Sandbox 和 Workspace 隔离资源/脚本。

### 2.3 设计约束

- 多租户 SaaS 不允许任意用户在主 Worker 启动 stdio 进程。
- Secret 只通过 SecretRef/Credential Handle 使用，不进 Prompt、日志或 manifest。
- 动态 schema 变化必须生成新 revision，不能覆盖活跃 Run。
- Extension 失败不能破坏 Core 状态机和数据库终态。
- 不把所有 Skill 编译成状态机；短流程保持轻量。

### 2.4 潜在冲突

- 开发 Skill 和产品 Skill 同名但用途、权限完全不同。
- MCP 工具目录可能远超上下文额度。
- Plugin 升级可能改变 Skill、Tool schema、Hook 和数据处理范围。
- 当前 Hook 可以改消息文本，未来必须限制可变字段。
- 外部 MCP 长调用若照搬 Grok 6000 秒会占用 SaaS Worker。

## 3. Extension Registry

### 3.1 聚合根

```json
{
  "extension_id": "uuid",
  "kind": "plugin",
  "qualified_name": "vendor:package",
  "version": "1.2.3",
  "source": "platform_marketplace",
  "trust_level": "signed_reviewed",
  "manifest_hash": "sha256:...",
  "publisher_id": "uuid",
  "signature": {},
  "status": "active",
  "installed_at": "timestamp"
}
```

Registry 保存版本事实；租户启用单独保存：

```text
org_id / extension_id / version
enabled / approved_capabilities[]
credential_binding_refs[]
config_revision / policy_overrides
enabled_by / enabled_at
```

平台发布、租户安装、用户可见、Agent 可选择是四个不同状态。

### 3.2 信任等级

| 等级 | 自动选择 | 脚本 | 外部副作用 |
|---|---:|---:|---:|
| `platform_trusted` | 可 | Sandbox | 仍走 Policy |
| `signed_reviewed` | 租户允许后 | Sandbox | 仍走 Policy |
| `tenant_managed` | 管理员允许后 | Sandbox | 严格范围 |
| `project_local` | 当前项目允许 | Sandbox | 默认禁止 |
| `untrusted_import` | 否 | 否 | 禁止 |

信任只影响“是否可加载/自动选择”，不代表数据或副作用授权。

## 4. Plugin

### 4.1 PluginManifest

```yaml
id: vendor.package
version: 1.2.3
min_runtime_version: 1
publisher: vendor
components:
  skills: [skills/report/SKILL.md]
  agents: [agents/research.yaml]
  mcp_servers: [mcp/search.yaml]
  hooks: [hooks/audit.yaml]
permissions:
  requested_capabilities: [network.public, artifact.write]
data:
  retention: none
  egress_domains: [api.vendor.com]
integrity:
  files_sha256: manifest.lock
```

安装校验：

- SemVer 精确版本，不允许运行时浮动 latest。
- 所有文件必须在 lock 清单，路径 canonicalize，拒绝 symlink 越根。
- 校验签名、publisher、runtime compatibility 和许可。
- 静态扫描脚本、MCP 配置、网络域、SecretRef 和 Hook 类型。
- 安装只写 Registry，不立即进入任何 Agent。

### 4.2 生命周期

```text
discovered -> verified -> installed -> enabled
-> disabled -> upgrade_pending -> enabled(new revision)
-> uninstall_pending -> uninstalled
```

活跃 Run 固定旧 revision。禁用阻止新 Run 使用，但不中途偷换；发现紧急安全问题可由平台 revoke，此时活跃 Run 暂停或失败关闭。

卸载不得直接删除仍被 Run、Artifact lineage 或审计引用的版本；先 logical disable，再按 retention 清理。

## 5. Skill Runtime

### 5.1 SkillManifest

```yaml
name: batch-image
version: 1.0.0
description: 按多段提示词生成多张图片
when_to_use: 用户明确要求分别生成
user_invocable: true
model_invocable: true
execution_mode: workflow
requested_tools: [generate_image]
input_schema: schemas/input.json
resources: [references/style.md]
model_policy: inherit
```

内部字段还包括：

- `skill_id/qualified_name/source/trust_level`
- `content_hash/manifest_hash`
- `required_capabilities`
- `argument_hint`
- `paths`
- `compatibility/license`
- `resource_hashes`

兼容 `allowed-tools` 时内部改名 `requested_tools`，避免误读为授权。

### 5.2 发现与命名

产品来源优先级：

```text
tenant_managed
> project_local
> platform_bundled
> plugin qualified
```

Plugin 不抢占裸名，使用 `plugin-name:skill-name`。冲突项都保留 qualified name；裸名指向当前租户允许的最高优先级版本。

首期限制：

| 参数 | 值 |
|---|---:|
| name 最大长度 | 64 字符 |
| description 最大长度 | 1024 字符 |
| frontmatter 最大读取 | 4096 bytes |
| 目录扫描深度 | 5 |
| 单 Run 自动选择 Skill | 3 |
| Skill 嵌套深度 | 3 |

产品 Registry 主要从数据库和已验证包加载，不在每次聊天递归扫描任意磁盘。

### 5.3 Progressive Disclosure

目录阶段只放：

- qualified name；
- 200～400 字符描述和 when-to-use；
- requested capability 摘要；
- trust/source 标签。

Catalog 总预算最多占动态 Context 的 5%，且先按 Agent、租户、通道、意图、文件类型筛选。不能照搬 Grok 的 50%。

加载阶段：

- 用户显式调用可在首个 ModelStep 前加载；
- 模型自动选择通过结构化 `load_skill` Action；
- 固定 `{skill_id, version, content_hash}`；
- 完整读取 SKILL.md，references/scripts/assets 按需读取；
- body 装不下时拒绝或目录化分段，禁止静默截掉步骤；
- 参数变量使用结构化模板，不做未转义字符串替换。

### 5.4 Instruction 与 Workflow

Instruction Skill：

- 一次 Run 内完成；
- 正文进入 `skill_instruction` ContextBlock；
- 模型继续普通 Tool Loop；
- 不建立步骤状态机。

Workflow Skill：

- 包含异步、付费、Interaction、多个 Artifact 或跨 Worker 恢复；
- 创建持久 SkillRun；
- step 有稳定 ID、输入 hash、Action refs、状态和证据；
- Goal/Continuation 决定等待和继续。

多提示词生图应使用 Workflow SkillRun + N 个图片 Action；Skill 负责拆解和完成条件，Image Executor 负责 Provider、积分与 Artifact。

### 5.5 权限

```text
EffectiveSkillTools
= Agent EffectiveToolset
 ∩ Skill requested_tools（若声明）
 ∩ Tenant/Channel capabilities
 ∩ Parent Run capabilities
 ∩ Policy
```

Skill 没声明 requested_tools 只表示不额外收窄，不表示获得全部工具。Skill 的 model/effort 也是请求，受套餐、预算和 ModelPolicy 限制。

## 6. MCP Runtime

### 6.1 架构

```text
Tenant MCP Config
  -> Credential Broker
  -> MCP Gateway Connection Manager
  -> Server Session
  -> Tool/Resource/Prompt Catalog
  -> Core EffectiveToolset
  -> MCP Executor Adapter
```

主 API/Actor Worker 不启动任意 stdio。平台内置 Server 可作为受控容器运行；第三方租户连接首期只允许 HTTPS Streamable HTTP/SSE，域名经管理员和 Platform allowlist。

### 6.2 MCPServerConfig

```json
{
  "server_id": "uuid",
  "org_id": "uuid",
  "name": "crm",
  "transport": "streamable_http",
  "endpoint": "https://mcp.vendor.com",
  "credential_ref": "secret:uuid",
  "enabled": true,
  "startup_timeout_seconds": 30,
  "call_timeout_seconds": 30,
  "catalog_ttl_seconds": 300,
  "max_inline_output_bytes": 20000,
  "allowed_domains": ["mcp.vendor.com"]
}
```

参数：

| 参数 | 初值 |
|---|---:|
| startup timeout | 30 秒 |
| 普通 tool timeout | 30 秒 |
| 单工具最大同步 timeout | 120 秒 |
| catalog TTL | 300 秒 |
| inline output | 20 KB |
| 单 Server 并发 | 4 |
| 每组织 MCP 并发 | 16 |
| reconnect backoff | 1、2、4…60 秒 + jitter |
| OAuth state TTL | 10 分钟 |

长于 120 秒的能力必须提供异步 TaskRef/query/callback；否则超时进入 Unknown，不占用 6000 秒 Worker。

### 6.3 Catalog

命名：

```text
mcp:<server-qualified-name>:<tool-name>
```

Catalog 保存 server revision、tool schema hash、annotations、input/output schema 和平台 ToolPolicyMetadata。模型先用 capability search 找工具；只有 EffectiveToolset 中的 schema 进入 Context。

动态 schema 更新：

- Connection Manager 创建新 catalog revision；
- 活跃 Run 继续旧 snapshot；
- 新 Action 参数按旧 schema 校验；
- Server 拒绝旧 schema时返回 revision conflict，Run 重新规划；
- 不在执行中静默替换。

### 6.4 Tool、Resource、Prompt

Tool：

- 每次调用创建标准 Action；
- Gateway 转成 SubmissionOutcome；
- 输出进入 ActionResult/Artifact。

Resource：

- 统一 `mcp_resource_search/read`；
- read 重新执行 Scope、大小、MIME、敏感数据和 egress 策略；
- 大内容进入 Artifact，不全量注入。

Prompt：

- 作为外部模板/数据块，优先级不高于 Skill；
- 不能成为 system Policy 或授权；
- 模板变量结构化校验；
- 来源、revision、hash 进入 ContextReceipt。

### 6.5 认证与隔离

- OAuth token、API key、header 只存 Secret Store。
- Gateway 按 org + server 建隔离连接池。
- ToolOutput、日志、错误移除 header、URL query secret 和 OAuth body。
- Server 端返回的 Tool annotations 不决定最终风险。
- 网络层阻止私网、link-local、metadata IP 和未批准重定向。
- OAuth redirect 绑定 org、user、server、nonce 和 PKCE。

## 7. Runtime Hook

### 7.1 Hook 类型

Core Hook 点：

```text
session_created
run_started
before_model_step / after_model_step
before_context_assemble / after_context_assemble
action_requested
before_policy / after_policy
before_executor / after_executor
artifact_created
run_waiting / run_terminal
```

分类：

- `policy_hook`：平台强制规则，只能内部注册，fail closed。
- `transform_hook`：允许修改白名单字段，内部或审核 Plugin。
- `observer_hook`：指标/审计/通知，只读，Outbox 异步。

Core Policy、状态 CAS、Cost 和 Artifact commit 不是 Hook。

### 7.2 HookReceipt

```json
{
  "hook_id": "uuid",
  "hook_revision": 2,
  "event": "before_executor",
  "decision": "continue",
  "added_obligations": ["redact_result"],
  "changed_fields": [],
  "duration_ms": 12,
  "outcome": "success"
}
```

规则：

- 固定顺序：platform policy → tenant policy → transform → observer enqueue。
- Hook 可拒绝或收窄，不能把 deny 改 allow。
- transform 只能改 manifest 声明的 JSON Pointer 白名单。
- 同 priority 按 qualified name 稳定排序。
- Hook 不能直接调用 Tool；需要动作时提交普通 Action。
- 外部 Hook 只消费脱敏 RuntimeEvent，不进入数据库事务。

超时：

| 类型 | timeout | 失败策略 |
|---|---:|---|
| platform policy | 500 ms | fail closed |
| tenant policy | 500 ms | fail closed |
| internal transform | 500 ms | 按 manifest |
| observer enqueue | 100 ms | Outbox 重试 |
| external observer | 2 秒/异步 | 不阻塞 Runtime |

现有 ProgressNotify、ToolAudit、TemporalValidator、FailureReflection、AmbiguityDetection 分别迁移为 observer、observer、transform、transform、transform；需逐项限制可写字段。

## 8. 安全不变量

- Skill/MCP/Plugin/Hook 内容不能产生 AuthorizationGrant。
- Plugin trust 不等于 Tool trust；每个 Tool 有独立 PolicyMetadata。
- Extension 不能读取未列入 CapabilityEnvelope 的文件、Memory 或 Secret。
- 外部输出永远不作为 system instruction。
- 活跃 Run 不自动升级扩展 revision。
- Extension 删除不破坏历史 Artifact lineage 和审计。
- Hook 不持有数据库终态所有权。
- MCP Server 不决定计费、组织权限或用户数据范围。
- Plugin 升级新增能力必须重新管理员批准。

## 9. 失败与边界场景

| 场景 | 处理 |
|---|---|
| Skill 不存在 | 显式调用返回 typed not_found；自动选择跳过 |
| Skill 热更新 | 活跃 Run 固定旧 hash |
| Skill 请求不可用工具 | 加载失败或降级计划，不扩权 |
| Skill A/B 循环 | 调用栈检测，深度 3，拒绝环 |
| Plugin 被禁用 | 新 Run 不可用，活跃 Run按安全策略继续/暂停 |
| Plugin 被平台 revoke | 活跃 Run fail closed |
| MCP 连接断开 | 重连；进行中 Action query/unknown |
| MCP schema 漂移 | 新 revision；旧 Run不静默替换 |
| OAuth 过期 | Interaction 重新授权，不把登录错误交给模型猜 |
| MCP 返回超大数据 | Artifact 化 + ref |
| Hook timeout | 按 Hook class 的 fail 策略 |
| Observer 故障 | Outbox 重试，不回滚业务终态 |
| Plugin 卸载仍有引用 | logical disable，保留版本事实 |

## 10. 方案比较

| 维度 | A：目录/Prompt 直连 | B：照搬本地 Grok | C：Registry + Gateway + Core Runtime |
|---|---|---|---|
| 开发量 | 低 | 中 | 中高 |
| 多租户隔离 | 弱 | 弱 | 强 |
| 版本恢复 | 弱 | 中 | 强 |
| 权限治理 | 分散 | 本地确认 | Core Policy |
| MCP 扩展 | 可 | 强 | 强且隔离 |
| Plugin 信任 | 无 | 项目目录 | 签名/租户启用 |
| 运维 | 简单但失控 | 进程膨胀 | Gateway 可观测 |

推荐 C。保留 Grok 的渐进披露、限定名、版本固定和统一工具目录，但使用 SaaS Registry、Gateway、Secret Broker 和 Policy。

架构影响、计划路径、迁移和验收见
`TECH_AGENT_RUNTIME_扩展运行时迁移附录.md`；Subagent/Background 见
`TECH_AGENT_RUNTIME_Subagent与后台任务.md`。
