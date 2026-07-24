# AGENT 10：Skills Runtime 技能运行时

> 状态：第一轮源码对标完成
> 日期：2026-07-18
> Grok Build 基线提交：`c68e39f60462f28d9be5e683d9cbe2c57b1a5027`
> 研究边界：发现、选择、加载、注入、工具约束、资源、版本和恢复
> 后续专项：MCP / Plugins / Hooks、Subagents、Persistence、UI Event 继续核验

## 1. 结论摘要

行业里 Skill 的准确定位不是“新的工具”，也不是“自动执行脚本”，而是：

```text
Skill = 可发现的能力说明
      + 按需加载的工作指令
      + 可选资源 / 脚本
      + 期望使用的工具声明
      + 触发和适用条件
```

Agent 执行 Skill 的正确链路：

```text
发现元数据
→ 用户显式指定或模型选择
→ Policy 判断是否允许加载
→ 固定 Skill 版本
→ 读取完整 SKILL.md
→ 按指令逐步推理和调用工具
→ 每个 ToolCall 仍独立经过 Policy
→ 记录步骤、产物和完成证据
```

Skill 不拥有执行权。它可以建议“调用生图三次”“读取报表后导出 Excel”，但不能因为
正文写了这句话就绕过积分、权限、确认、租户范围或工具参数校验。

Grok Build 的优点是 Skill discovery 和 progressive disclosure 做得很完整：启动只
注入名称、描述和绝对路径，真正调用时才加载正文；工作中访问新目录还能动态发现
附近 Skill。它的不足也很明确：Skill 仍主要是 prompt package，步骤没有持久状态；
`allowed-tools` 已解析，但在当前基线源码中没有形成执行期强制权限边界。

EVERYDAYAIONE 目前有两类“技能”：

- `.cursor/skills/*/SKILL.md`：开发代理使用，不进入产品 Agent Runtime。
- `backend/skills/data-usage.md`、`doc-usage.md`：沙盒只读操作指南，不具备发现、选择、
  版本、触发、步骤或 Policy 协议。

因此产品运行时实际上还没有统一 Skills Runtime。这正是用户感受到“工具都能用，
但彼此断层”的一部分原因。

## 2. Grok Skill 数据模型

### 2.1 文件结构

标准形式：

```text
skill-name/
  SKILL.md
  scripts/       # 可选
  references/    # 可选
  assets/        # 可选
```

`SKILL.md` 由 YAML frontmatter 和 Markdown body 构成。核心字段：

| 字段 | 作用 |
|---|---|
| `name` | 命令身份和去重键 |
| `description` | 能力说明，也是自动选择依据 |
| `when-to-use` | 单独的触发语义 |
| `allowed-tools` | 预期工具列表 |
| `argument-hint` | 用户参数提示 |
| `user-invocable` | 是否显示为用户命令，默认 true |
| `disable-model-invocation` | 是否禁止模型自动调用，默认 false |
| `model` / `effort` | 模型与推理强度覆盖 |
| `compatibility` / `license` | 环境和许可信息 |
| `metadata` | 作者、短描述等扩展信息 |
| `paths` | 触碰匹配文件后才激活 |

Grok 的解析限制：

- 名称最长 64 字符。
- 描述最长 1024 字符。
- frontmatter 最多读取 4096 bytes。
- 从正文推导描述时最多 peek 2048 bytes。
- 目录递归深度最多 5。
- 遍历按字典序，避免同名 first-seen-wins 受文件系统顺序影响。

这些限制不仅是性能参数，也是对恶意或异常 Skill 的输入边界。

### 2.2 SkillInfo

运行时元数据还记录：

- scope、绝对路径、配置来源。
- plugin name/version/root/data。
- 是否 enabled。
- display name 与 dedup key。
- body 是否已加载。

Plugin Skill 使用 `plugin-name:skill-name`；其他同名项可用
`local:`、`repo:`、`user:` 限定，避免覆盖后无法显式选择。

## 3. 发现与优先级

### 3.1 搜索范围

Grok 从当前工作目录向 Git 根逐层扫描：

- `.grok/skills`
- `.agents/skills`
- 可配置兼容的 `.claude/skills`
- 可配置兼容的 `.cursor/skills`

再加入用户目录、配置额外路径、Server、Bundled 和 Plugin。优先级：

```text
Local → Repo → User → Server → Bundled → Plugin
```

原生高优先级 Skill 覆盖低优先级同名项；Plugin 不抢占裸名，保留限定名。

`ignore` 完全隐藏；`disabled` 仍可管理和展示，但不进入模型列表或调用工具。
Skill discovery 不服从 `.gitignore`，隐藏必须显式配置。

### 3.2 动态发现

Grok 在 read/list/edit/apply_patch 成功后：

1. 从工具输出提取实际访问或修改的路径。
2. 激活 `paths:` 匹配的条件 Skill。
3. 从访问位置向上查找新的技能目录。
4. 文件系统 I/O 在共享锁之外执行。
5. 用 canonical path 去重并记录 checked directories。
6. 会话稍后统一发送 reconciliation，而非在 ToolOutput 中混入公告。

限制：Bash/grep 路径不参与激活，因为输出难以可靠解析；concise mode 关闭 Reminder
后，V1 动态发现也会失效。

这一设计说明 Skill discovery 应属于独立运行时 Hook，而不是某个文件工具的特殊
返回文本。

## 4. Progressive Disclosure

### 4.1 列表阶段

模型常驻的不是所有 Skill 正文，而是：

```text
name + description + when-to-use + absolute path
```

Grok 将 Skill 列表预算设为模型上下文的 50%，未知窗口按
`200K tokens × 4 bytes × 50% = 400K chars`。每条 description 与 when-to-use 合计
最多 400 bytes；超预算时依次降级：

1. 完整但单条截断的描述。
2. 按条目均分后缩短描述。
3. 仅名称并显示溢出提示。

50% 对大型 Skill Store 仍然过高，不适合本项目直接照搬。Skill Catalog 应先按
Agent、租户、通道、文件类型和当前意图预筛，再使用 Context 预算。

### 4.2 加载阶段

用户 `/skill args` 可以在第一次模型调用前直接展开，实现 zero-round-trip；模型自动
选择则调用 Skill 工具，返回完整正文。统一格式：

```xml
<skill name="..." description="..." path="...">
  Markdown body
</skill>
```

显式调用支持 `$ARGUMENTS`、`$ARGUMENTS[N]`、`$N`、`${SKILL_DIR}`、
`${SESSION_ID}` 和 Plugin root/data 变量替换。未知变量保留；参数不存在时替换为空。

加载后 Skill 只是附加指令，模型继续进行普通 Tool Loop。它不是一次 RPC 执行。

### 4.3 Agent 预加载

AgentDefinition 可以声明固定 `skills:`。构建 Agent 时解析名字并加载 body，使专用
Agent 天生携带特定流程。预加载适合少量核心规范，不适合把所有业务 Skill 常驻。

## 5. Grok 当前边界与风险

### 5.1 `allowed-tools` 不是授权

在本次基线源码中，`allowed-tools` 被解析进 `SkillInfo` 并用于详情展示；没有发现它
在 Skill 激活后收窄 Agent EffectiveToolset 或在执行期成为强制 allowlist。

因此必须区分：

- `requested_tools`：Skill 作者声明工作流可能需要。
- `effective_tools`：Agent ∩ 用户/租户权限 ∩ 通道能力 ∩ Policy 后的实际集合。

Skill 声明绝不能扩大 EffectiveToolset。

### 5.2 Prompt 注入风险

Skill 是本地或 Plugin 提供的长指令，可能包含：

- 要求忽略用户或系统约束。
- 请求读取凭据、越权路径或上传数据。
- 诱导自动确认付费/删除/部署。
- 通过变量或 args 拼入未转义内容。
- 引用被替换的脚本或资源。

“文件在项目里”不等于可信。Skill 的信任来源、内容 hash 和依赖资源必须在 Run 开始
时冻结。

### 5.3 没有步骤状态机

Grok Skill body 可以写步骤，但运行时不理解“当前第几步”。中断、异步等待或 Worker
重启后，只能依靠对话和 Goal 摘要恢复。对短流程足够，对跨分钟媒体、多次生成、
审批等待和跨系统任务不够。

## 6. EVERYDAYAIONE 现状

### 6.1 架构现状

产品 Agent 目前没有 Skill Registry、Skill Selector 或 Skill Tool。沙盒启动时把
`backend/skills` 只读挂载为 `/skills`，其中只有数据和文档使用指南；这些 Markdown
需要执行代码主动读取，没有 frontmatter、触发或加载协议。

项目开发目录的 `.cursor/skills` 服务于开发协作规则，不能直接暴露给产品用户或生产
Agent。两者必须物理和逻辑隔离。

### 6.2 可复用模块

- Agent/Tool Catalog：用于计算 Skill 实际可用工具。
- Policy Gate：逐 ToolCall 检查授权、成本和副作用。
- ResourceManifest：限定 Skill 可见的输入资产和 Workspace 路径。
- ContextPlan：目录元数据与已加载正文分别计入预算。
- Goal Orchestrator：长 Skill 的步骤、等待和验收恢复。
- Sandbox：运行可信度较低的辅助脚本，继续只读挂载 Skill 资源。
- Artifact 协议：脚本输出、文件、图片和报告统一形成 Artifact。

### 6.3 潜在冲突

1. `.cursor/skills` 与未来产品 Skill 同名但信任域不同。
2. 当前 `backend/skills/*.md` 不是标准目录结构，迁移时需保留沙盒路径兼容。
3. Skill 可能要求当前 Agent 没有的工具，不能临时越权补工具。
4. `model/effort` 覆盖可能突破租户套餐或成本策略。
5. 多段提示词批量生成需要步骤实例和幂等键，单纯 Skill 文本无法保证不重复扣费。

## 7. 目标架构

```text
Skill Sources
  ↓
Skill Registry
  ↓ metadata/filter
Skill Catalog View
  ↓ explicit invoke / model selection / path trigger
Skill Resolver + Policy
  ↓ pin version/hash
Skill Instance
  ↓ instructions/resources/requested tools
Agent Context + EffectiveToolset
  ↓
Model Loop → ToolBridge → Executors
  ↓
Skill Step State / Artifact / Evidence
```

### 7.1 SkillManifest

建议内部规范：

```text
skill_id / qualified_name / version
name / description / when_to_use
source / trust_level / content_hash
user_invocable / model_invocable
requested_tools[]
required_capabilities[]
input_schema
model_policy / effort_policy
resource_manifest[]
execution_mode: instruction | workflow
```

`allowed-tools` 对外兼容，但内部改名 `requested_tools`，避免被误解为授权。

### 7.2 两种执行模式

**Instruction Skill**

- 短流程、一次 Run 内完成。
- 加载正文后由模型普通 Tool Loop 执行。
- 不持久化内部步骤。
- 示例：格式化数据、代码评审规范、提示词改写。

**Workflow Skill**

- 多步骤、包含付费/异步/等待/多个 Artifact。
- 激活时创建 `SkillRun`，必要时绑定 Goal。
- 每步有稳定 `step_id`、输入、状态、attempt、Action refs 和 evidence。
- 示例：多提示词批量生图、视频生产、ERP 查询后生成报告并发送。

不要把所有 Skill 都升级为工作流。只有需要恢复和幂等的流程才持久化。

### 7.3 SkillRun

```text
skill_run_id / run_id / optional goal_id
skill_id / version / content_hash
invoked_by: user | model | agent_preload | path_trigger
arguments
effective_tools[]
current_step
status: active | waiting | paused | completed | failed | cancelled
step_records[]
artifacts[] / evidence[]
```

模型不能自由改写已固定的 Skill 版本；热更新只影响新 SkillRun。

## 8. 选择、冲突和权限规则

### 8.1 选择优先级

```text
用户显式限定名
> 用户显式裸名
> AgentDefinition 预加载
> 当前资源/路径条件 Skill
> 模型按意图自动选择
```

同名裸 Skill 只选择当前租户和项目定义的最高优先级项，同时把限定名返回 UI。不同
Skill 描述同时匹配时，模型最多选择必要集合；存在互斥执行规则时请求用户或由确定性
优先级解决，不能静默合并冲突指令。

### 8.2 权限求交

```text
EffectiveSkillTools
= Agent EffectiveToolset
∩ Skill requested_tools（若声明）
∩ Session/Channel capabilities
∩ Tenant plan
∩ Policy
```

未声明 requested tools 时不代表全工具授权，只代表不额外收窄。每次实际 ToolCall
仍走 Policy。Skill model override 同样只是请求，最终受 ModelPolicy 与成本上限约束。

### 8.3 信任等级

建议至少：

- `platform_trusted`：平台随版本发布。
- `tenant_managed`：组织管理员审核。
- `project_local`：项目内 Skill。
- `plugin_signed`：已安装并固定来源的 Plugin。
- `untrusted_import`：导入但未审核。

未审核 Skill 默认不能自动调用、不能请求外部副作用工具、不能运行脚本。安装和启用
属于管理动作，不应由 Skill 自己完成。

## 9. 上下文和资源策略

目录阶段只进入：

- qualified name。
- 最多约 200～400 字符的描述/触发语义。
- requested capability 摘要。

加载阶段：

- 完整读取选中的 `SKILL.md`。
- `references/`、`scripts/`、`assets/` 不全量注入，按正文明确引用读取。
- 文件读取必须相对 Skill 根解析并 canonicalize，禁止 `..` 和 symlink 越界。
- 二进制资源只进入 ResourceManifest/Artifact ref。
- 同一 Run 固定 `{skill_id, version, hash}`，重试不重新解析漂移版本。

Skill body 超过预算时不能静默截断步骤；应拒绝加载或使用有目录的分段资源。正文被
截断却继续执行比显式失败更危险。

## 10. 多提示词批量生图示例

用户说“按刚才三段提示词分别生成”：

```text
Intent: execute
→ 选择 batch-image Skill
→ 从对话引用解析 3 个 prompt，冻结顺序和 hash
→ SkillRun 创建 step-1..3
→ Policy 计算总成本、授权范围和并发上限
→ 每步形成独立 ActionRequest + 幂等键
→ Image Executor 并发或限流提交
→ 每个 Accepted task 持久化并前端渲染
→ 异步完成逐项挂 Artifact
→ 全部完成或部分失败后 verifier 汇总
```

Skill 负责“怎么拆、什么顺序、什么完成标准”；Image Executor 负责 Provider、积分、
超时、退款和 OSS；Goal/SkillRun 负责跨等待恢复。职责不会再断层。

## 11. 边界场景

| 场景 | 处理 |
|---|---|
| 没有匹配 Skill | 正常使用 Agent，不强行调用 |
| Skill 文件缺失/格式错误 | 不进入目录；显式调用返回结构化错误 |
| 同名冲突 | 按 scope 选裸名，保留 qualified name |
| Skill 热更新 | 活跃 Run 固定旧 hash，新 Run 使用新版 |
| requested tool 不可用 | 加载前报告缺失能力；不得越权添加 |
| 模型重复调用同一 Skill | 同 Run + skill + args hash 幂等复用 |
| Skill A 调 Skill B | 记录调用栈和最大深度；禁止环 |
| 脚本超时/异常 | Sandbox 限时，产物未确认前不进入完成证据 |
| 用户中途修改目标 | 更新 Goal/Run 输入，受影响步骤失效而非整批重放 |
| Worker 重启 | Workflow Skill 从持久 step/action 状态恢复 |
| Plugin 被卸载 | 活跃 Run 暂停；不从同名其他来源偷换版本 |
| Skill 诱导副作用 | Policy 以用户授权和 Action 风险为准拒绝 |

推荐初始限制：Skill 嵌套深度 3、单次自动选择最多 3 个、动态目录扫描深度 5；这些
属于保护参数，后续用真实指标调整。

## 12. 方案选择

| 方案 | 说明 | 判断 |
|---|---|---|
| 全部 Skill 都是 Prompt | 实现最简单，但异步/付费流程无法恢复 | 不满足目标 |
| 全部 Skill 编译成状态机 | 强恢复，但作者成本和系统复杂度过高 | 过度设计 |
| Instruction + Workflow 双模式 | 简单任务保持轻量，长任务获得持久步骤 | 推荐 |

推荐“双模式 + 同一 SkillManifest”。第一阶段先做 Catalog、Resolver、按需加载和
Policy 求交；Workflow Skill 等 Goal/Persistence 层字段确认后再落最终协议。

## 13. 分阶段落地边界

本轮只形成研究结论，不修改运行代码、数据库或 API。后续重构阶段建议：

1. 将 `backend/skills` 标准化为平台 Skill Source，但保留 `/skills` 只读挂载。
2. 建立只读 Skill Registry 和 Catalog，不改变 Agent 行为。
3. 在 PromptBuilder/ContextPlan 注入经过筛选的元数据目录。
4. 增加结构化 Skill Tool，支持显式与模型调用、版本/hash 固定。
5. 将 requested tools 与 EffectiveToolset/Policy 求交。
6. 增加资源按需读取和路径隔离。
7. 最后为长流程引入 SkillRun，并与 Goal/Action/Artifact 绑定。

下一层进入 MCP / Plugins / Hooks：核验外部 Tool、Resource、Prompt 和 Skill 如何
安装、发现、认证、隔离、授权、断线恢复，以及它们如何进入同一 Tool Catalog。
