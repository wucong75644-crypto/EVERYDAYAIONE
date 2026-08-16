# Runtime v3 兼容迁移与 Skill 接入总计划

> 版本：v1.0
> 日期：2026-08-16
> 状态：计划已建立，按板块逐项确认后实施
> 原则：每个板块独立调查、实现、验证、提交、部署；用户确认通过后才进入下一板块

## 1. 总目标

在不破坏旧链路能力和用户工作模式的前提下，把主 Agent 的执行入口逐步迁移到 Runtime v3：

```text
用户请求
→ Ingress
→ Session / Run
→ 模式与 Skill 上下文
→ Runtime Catalog / Effective Toolset
→ ModelStep
→ Action
→ Policy / Authorization
→ Action Worker / Executor
→ 结果投影
→ 下一轮 ModelStep 或 Run 结束
```

旧 ToolLoop 在迁移期间继续作为兼容链路存在，但同一个 Run 只能有一个执行所有者：

```text
旧链路：Model → ToolLoop → 直接执行
Runtime：ModelStep → Action → Worker → Executor
```

## 2. 已确认的架构原则

- Runtime v3 是新的执行方式，不只是新的工具清单。
- 旧链路的外部能力、工作模式、提示词语义和结果表现必须对齐。
- 内部实现不要求相同；Runtime 的 Action、授权、Lease、幂等、恢复和投影底座直接复用。
- 旧工具通过 Compatibility Adapter 接入 Runtime Executor，不复制第二套业务实现。
- Skill 是主 Agent 执行前的上下文增强层，不是独立 Agent、独立编排器或独立执行器。
- Skill 不授予权限；工具可见性和执行权限仍由 Catalog、EffectiveToolset、Policy 和 Action 决定。
- 当前 Run 固定模式、Skill 内容哈希、工具 Catalog 和 EffectiveToolset；中途修改不热更新。
- 未完成契约对比、定向测试和回滚验证前，不恢复 Runtime v3 生产入口。

## 3. 迁移板块

### 板块 0：基线与差异清单

目标：冻结旧链路和 Runtime v3 的真实行为基线。

核对内容：

- 旧链路初始工具、动态工具和完整工具清单；
- Runtime v3 Catalog、EffectiveToolset 和 Executor family；
- Auto / Ask / Plan 模式；
- 工具描述、参数 Schema、作用域和权限；
- ToolLoop 与 Action 执行流程；
- Skill 当前接入位置和 Runtime 缺口。

当前结果：已完成只读对比，发现工具名称基本覆盖，但存在作用域、Schema 和工具描述差异；生产 v3 仍未恢复。

验收：形成差异表，并将每项差异归类为兼容、需适配、需用户确认或阻断。

### 板块 1：Auto / Ask / Plan 工作模式与提示词

目标：让 Runtime v3 真正继承现有三种工作模式，而不是只在 Policy 层保留枚举。

目标行为：

- `auto`：自主推进；只读工具直接执行；危险工具仍受授权/确认保护；
- `ask`：只读工具直接执行；危险工具生成待确认 Action；
- `plan`：只分析和读取，展示计划后停止；用户确认后恢复进入前的模式；
- 模式提示词进入 Runtime Context，不能只依赖固定 `AgentDefinition.system_prompt`；
- 模式同时影响提示词、EffectiveToolset 和 Policy，三层规则必须一致；
- Plan 模式下不得创建未授权的副作用 Action，Policy 仍作为第二道防线。

主要位置：

- `backend/services/handlers/permission_mode.py`
- `backend/services/prompt_builder/templates/modes.md`
- `backend/services/agent/runtime/agents/definition.py`
- `backend/services/agent/runtime/catalog/effective_toolset.py`
- `backend/services/agent/runtime/production_model.py`
- `backend/services/agent/runtime/policy/evaluator.py`

状态：第一批实现完成，定向测试通过，已提交；待部署验证。

验收重点：三种模式可进入 Runtime Run；模型能看到当前模式；Plan 不产生副作用 Action；Ask 确认后可继续；Plan 退出可恢复原模式。

### 板块 2：Skill 目录发现与模式上下文合并

目标：在 ModelStep 前按需发现并加载 Skill，同时保持当前 Run 的上下文快照。

接入方式：

```text
模式上下文
→ Skill 目录元数据
→ 匹配候选 Skill
→ 按需加载 SKILL.md
→ Runtime Context / ContextPlan
→ ModelStep
```

原则：

- 目录先发现，正文按需加载，不全量塞入上下文；
- Skill 不直接执行工具；
- 多步骤 Skill 复用 Run / Action / Executor；
- AI 创建和修改 Skill 复用现有工作区读写能力；
- 当前 Run 固定 Skill 内容哈希，下一 Run 才使用修改后的内容。

详细阶段以 [`PLAN_AGENT_RUNTIME_Skill系统分阶段实施.md`](./PLAN_AGENT_RUNTIME_Skill系统分阶段实施.md) 为准。

### 工具接入总流程

工具接入不是只增加 Runtime Schema，而是必须完成以下完整迁移链：

```text
旧工具清单与动态发现
→ 工具能力盘点
→ Runtime Canonical Contract
→ description / parameters / result schema
→ Tool Group / Scope / Entitlement
→ EffectiveToolset
→ Provider / Adapter
→ Runtime Executor
→ Action / Policy / Recovery
→ 旧链路对比测试
```

工具接入必须逐项建立映射，禁止只对比工具名称：

| 旧工具能力 | Runtime v3 接入位置 | 迁移方式 |
|---|---|---|
| 本地 ERP 查询 | `read_registry.py`、`ErpLocalReadCapability` | 复用查询能力，重新确认 user/channel scope |
| 远程 ERP 查询 | `specialist_registry.py`、`ERPQueryProvider` | 接入 `remote_read` family |
| ERP API 文档搜索 | `erp_catalog`、`ErpApiSearchProvider` | 保留 action/params 发现能力 |
| ERP 写入 | `erp_mutation`、`ERPQueryProvider(write=True)` | Action + Policy + 用户确认 |
| ERP 同步 | `erp_sync`、`SyncExecutor` | 异步 Action、幂等和恢复 |
| 文件搜索/分析 | `workspace`、`artifact_job` | 复用工作区权限和文件能力 |
| 文件删除/恢复 | `workspace_mutation` | 危险 Action，不允许 ToolLoop 直接执行 |
| 知识库、记忆、证据、Artifact | `runtime_read` registry | 只读能力，按作用域过滤 |
| 图片/视频生成 | `media_generation` | 异步 Action、确认、回调和结果投影 |
| 代码执行 | `sandbox_job` | 沙盒、预算、授权和执行结果回写 |
| 子 Agent 工具 | `child_run` | 复用 ChildRun，不新增 SkillRun |
| 定时任务 | `scheduled_task` | CAS、版本校验、确认和恢复 |

工具接入的完整验收矩阵必须覆盖：

```text
工具名称
× 工具描述
× 参数 Schema
× 结果 Schema
× Tool Group
× user/channel Scope
× web/wecom Channel
× auto/ask/plan 模式
× safe/confirm/dangerous 安全级别
× Provider / Executor
× Action 状态
× 错误、重试、取消、恢复
```

只有矩阵全部通过，Runtime v3 才能替换旧工具出口；任何工具缺失、描述缺失、参数不兼容或执行器未接入，都必须阻断切换。

### 板块 3：Runtime Tool Catalog、描述和参数契约

目标：使 Runtime v3 模型看到的工具与旧链路能力兼容。

实施内容：

- 建立 Runtime 工具的权威 Contract；
- 保留工具名称、用途和参数说明；
- `provider_tools()` 下发工具级和参数级 description；
- 处理旧参数与 Runtime 参数的兼容映射；
- 明确 `additionalProperties`、必填字段和结果 Schema；
- 为每个工具保存 contract revision 和 schema hash；
- 旧 `chat_tools.py` 作为迁移输入和兼容来源，不再作为 Runtime 模型出口。

重点工具：`erp_agent`、`erp_analyze`、`local_data`、`file_analyze`、`image_agent`、`social_crawler`、`manage_scheduled_task`、`restore_file`、`trigger_erp_sync`。

主要位置：

- `backend/config/chat_tools.py`
- `backend/config/tool_registry.py`
- `backend/services/agent/runtime/catalog/types.py`
- `backend/services/agent/runtime/catalog/specialist_schemas.py`
- `backend/services/agent/runtime/catalog/effective_toolset.py`
- 新增 Runtime Compatibility Adapter 层

验收：旧完整工具与 Runtime Contract 逐项对比；描述完整；旧参数可兼容；不允许出现模型看得到但执行器无法处理的工具。

### 板块 4：作用域、工具可见性和权限模式

目标：解决同一工具在旧链路和 Runtime v3 中可见范围不同的问题。

实施内容：

- 对齐 `user / channel` scope；
- 解决 Web 用户作用域下 8 个本地 ERP 工具不可见问题；
- 对齐组织、用户和工作区权限；
- 统一 entitlement、authorized names 和 channel 过滤；
- 确认 Auto / Ask / Plan 下的工具可见性和 Policy 结果；
- 工具不可见、无权限和需要确认分别返回稳定原因码。

主要位置：

- `backend/services/agent/runtime/catalog/effective_toolset.py`
- `backend/services/agent/runtime/executors/read_registry.py`
- `backend/services/agent/runtime/policy/evaluator.py`
- Runtime ingress scope 解析与数据库事实生成

验收：每个模式、每个 scope、每个 channel 都能生成预期工具集，并且与旧链路能力矩阵一致。

### 板块 5：旧业务能力接入 Runtime Executor

目标：复用旧 ERP、文件、搜索、媒体和代码能力，但由 Runtime Action 成为统一执行所有者。

接入原则：

- 不复制旧业务实现；
- 通过 Provider / Adapter 接入 Runtime family executor；
- 统一输入转换、输出转换和错误转换；
- 旧链路继续使用旧执行入口，直到对应工具完成灰度；
- Runtime Action 不回调旧 ToolLoop。

主要位置：

- `backend/services/agent/runtime/production_composition.py`
- `backend/services/agent/runtime/executors/family_executors.py`
- `backend/services/agent/runtime/executors/real_specialist_composition.py`
- 现有 ERP、文件、媒体和代码 Provider

验收：每个工具至少覆盖成功、参数错误、权限拒绝、Provider 失败、超时和重复调用场景。

### 板块 6：ModelStep、Action、多步骤和恢复

目标：确认 Runtime v3 的多步骤执行与旧 ToolLoop 的用户可见行为兼容。

实施内容：

- ModelStep 产生 Action；
- Action 经过授权、Claim、Lease 和 Executor；
- 结果通过 Projection 回到下一轮上下文；
- 支持并行只读、串行副作用、异步媒体和 ChildRun；
- 统一重试、幂等、取消、unknown 和 reconcile；
- 禁止旧 ToolLoop 与 Runtime Action 双重执行。

主要位置：

- `backend/services/agent/runtime/application/model_loop.py`
- `backend/services/agent/runtime/application/action_loop.py`
- `backend/services/agent/runtime/application/authorization_recovery.py`
- Runtime projection、recovery 和 coordinator

验收：单步、连续多步、暂停恢复、失败重试、重复回调、取消和迟到结果均有确定终态。

### 板块 7：结果、前端状态和用户可见行为

目标：让用户看到的状态与旧链路兼容，同时反映 Runtime 的真实 Action 状态。

实施内容：

- 映射 Run、ModelStep、Action、Attempt 到前端消息状态；
- 区分执行中、等待确认、等待外部结果、失败、已完成和已取消；
- 保留旧聊天消息、tool result 和图片/文件结果兼容表现；
- 对 Runtime 阻断提供可理解提示，不显示内部 receipt、路径和策略事实。

验收：用户能够理解每个状态，并能继续确认、取消或重试；旧历史消息仍可恢复上下文。

### 板块 8：灰度、生产恢复和回滚

目标：在不影响生产旧链路的情况下逐步开启 Runtime v3。

顺序：

```text
本地契约测试
→ Runtime 非生产 Run
→ 只读工具灰度
→ Ask / Plan 灰度
→ 副作用工具灰度
→ 全量 Runtime v3
```

门禁：

- v3 Catalog fact 已启用且 revision 可验证；
- 工具、模式、提示词和 scope 对比全部通过；
- Runtime worker、projection、authorization 健康；
- 旧入口仍可回滚；
- 健康检查和人工业务验收通过；
- 未确认前不修改生产业务状态。

## 4. 每个板块固定交付模板

每个板块都必须保存一份短报告，包含：

1. 当前旧行为；
2. Runtime v3 目标行为；
3. 需要接入的文件和职责；
4. 提示词、状态和接口契约；
5. 不修改范围；
6. 失败、恢复和回滚；
7. 定向测试和验收用例；
8. commit、部署和健康检查结果；
9. 用户确认记录。

固定流程：

```text
板块计划确认
→ 只读调查
→ 对齐提示词/流程/契约
→ 用户确认
→ 最小实现
→ 定向测试
→ 独立提交
→ 按既有流程部署
→ 验证通过
→ 用户确认进入下一板块
```

## 5. 当前进度

| 板块 | 状态 |
|---|---|
| 板块 0：基线与差异清单 | 已完成只读调查 |
| 板块 1：三种模式与提示词 | 第一批实现完成，定向测试通过，已提交，待部署验证 |
| 板块 2：Skill 目录与上下文 | 已有分阶段计划，待板块 1 完成后接入 |
| 板块 3：Tool Catalog 与契约 | 待板块 1、2 的上下文边界确认 |
| 板块 4：Scope、可见性和权限 | 待板块 3 |
| 板块 5：Executor Adapter | 待板块 3、4 |
| 板块 6：多步骤和恢复 | Runtime 底座已有，待兼容验证 |
| 板块 7：结果和前端状态 | 待板块 6 |
| 板块 8：灰度和生产恢复 | 所有前置板块通过后 |

## 6. 变更记录

- 2026-08-16：建立 Runtime v3 兼容迁移与 Skill 接入总计划；确定每个板块逐项确认、验证、提交、部署后再推进。
- 2026-08-16：完成板块 1 第一批实现：Runtime v3 读取并规范化 `permission_mode`，将共享模式规则和当前模式注入 Runtime Context，并记录模式事实。
