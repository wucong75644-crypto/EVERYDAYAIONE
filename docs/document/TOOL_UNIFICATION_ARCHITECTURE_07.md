# 工具统一：01–07 最终代码架构

更新：2026-09-11。代码基准 `ca4c3d7e6a89ef34412cf405efc2d4fb0c1350e2` 加板块 07 当前差异；状态为**技术验收通过，待提交部署及用户验收**。这份文档描述实际代码，不代表板块 08 整体验收已执行。

配套：[完整目录](TOOL_UNIFICATION_CATALOG_07.md)、[逐项验收](TOOL_UNIFICATION_ACCEPTANCE_07.md)、[01–07 交接](TOOL_UNIFICATION_HANDOFF.md)、[源码与入口证据](tool-unification-evidence/07-source-checks.json)。

## 定义与执行

```text
definitions/{erp,file_sandbox,media,task,general}.build_specs
  ├─ 原 schema 资源/ERP ApiEntry/ERPAgent 描述（只读取领域事实）
  └─ 不可变 ToolSpec（完整 schema、兼容视图、域、资格、风险、并发、缓存、PolicyRules）
                 ↓
        catalog.build_tool_catalog → ToolRegistry
           ├─ config.* 旧工厂/字典/helper 的兼容投影
           ├─ Planner CapabilityRegistry 的描述投影
           └─ 显式 ToolContext + ToolPolicy → allowed / advertised / call decision
                 ↓
        ToolRuntime → ToolExecutionService → ToolDispatcher
                 ↓
        LegacyToolHandler → 当前 executor._handlers[handler_key]
                 ↓
        ToolResult → Chat / ToolLoop / 审计 / cache / Actor replay
```

35 个运行工具全部为 `definition_kind="explicit"`；33 个公开 schema，2 个 handler-only。`executor_type="legacy"` 表示仍绑定已有业务实现，**不表示存在 legacy 风险表或第二条授权执行路径**。新旧入口最终使用相同 Registry、Policy、Dispatcher。ERP 内部仍是计划提取、部门 Agent、查询引擎；本块未注册每次 SQL/IO，也未改写其实现。

## 实际接口与所有权

| 接口/文件（相对 backend） | 当前责任 |
|---|---|
| `services/tools/definitions/*.py` | 五组显式 Spec；原工厂代码移入同目录 `*_schemas.py`。ERP action 描述/读写继续读取 ApiEntry；ERPAgent 描述继续从原领域 manifest 生成 |
| `ToolSpec.schema` / `to_schema(variant=None)` | 原公开 schema 的不可变快照；每次返回独立 dict。`erp_api_search` 的旧 Phase 描述保存在 `schema_variants["erp_search"]`，不是第二个工具 |
| `ToolSpec.legacy_validation_schema` | 同一 Spec 持有原部分参数校验契约；保留它与模型完整 schema 的原有差异，不增加必填参数、不把旧别名删掉 |
| `ToolSpec.catalog_order/catalog_groups/core/legacy_plan_visible` | 保留旧工厂分组、顺序、核心与旧 plan helper 的展示契约；不是权限字段，也不用于推算风险/缓存 |
| `ToolSpec.domain/availability/policy_rules` | general/erp/shared、组织/个人上下文/功能标志、操作类别及模式、action resolver 与已有权限要求。Policy 的参数级检查和实时权限刷新仍必需 |
| `ToolSpec.risk_level/parallelizable/cacheable/effects/replay_requirement` | 各自独立；不从并行推断缓存，不从 safe 推断无副作用。本块逐字段与基准完全一致，原未评审的 effects=`unknown` 不伪称已完成副作用分类 |
| `catalog.build_tool_catalog()` / `definition_registry()` | 每次返回新 Registry，Spec 深层冻结。只持有定义，不持有用户、组织、资源授权、DB、Handler、锁或取消事件 |
| `build_legacy_catalog()` | 保留原导入与调用签名，委托 `build_tool_catalog()`。`validate_legacy_coverage` 保留公开 schema 全字段/handler 绑定校验 |
| `config.chat_tools` | 原工厂 re-export、SafetyLevel/ToolGroup、工具目录、core/plan/by-names/正则 helper 保留；风险、并发、目录均从 Registry 派生。旧字典只为兼容读取 |
| `config.{common,erp,erp_local,file,code,crawler}_tools` | 原 build 工厂、名字集合、部分校验表、辅助函数导入保留；公开 schema/校验表读取 Registry，路由提示词保留原文 |
| `config.tool_domains` | 域字典及 can_access/filter/validate API 保留，运行工具域取自 Spec。`get_conversation_context` 在旧域 helper 中原本未列出，仍保持；`route_to_chat` 只保留旧 Phase 出口分类 |
| `config.agent_tools` | INFO/ROUTING/SYNC 等分类和 validate API 保留；运行工具部分校验表来自 Spec；三个 route 出口的旧校验条目属于独立控制兼容接口 |
| `ToolResultCache.is_cacheable` | 读取 Spec.cacheable；TTL、容量、快照、安全载荷、用户/域隔离 key 和 get/put 行为保留 |
| `CapabilityRegistry.from_specs/from_tool_schemas/from_names` | 注册工具从 Spec 生成 CapabilityDescriptor。原 `capability.v1`、Planner/PlanRelease、execution_policy 的字段/版本不变；保留通用 Planner 的自定义构造 API，但它不能向执行 Registry 注册工具或授权 Handler |

`config.tool_registry.ToolEntry.domain` 是语义选择分组（含 computer/common/crawler），与 ToolSpec 权限域不同。tags、synonyms、priority、always_include 及 `tool_selector` 排序/筛选算法保持独立，AST 对照未变。它们只能选择候选，不能扩大实际访问范围。

## Planner 对照：描述跟随已有执行边界

旧 `from_tool_schemas` 只按 dangerous 与否推断执行模式，因此把多数工具统一标成 interactive/scheduled/preflight。新投影从 Spec 的既有 `execution_modes` 读取；这使 Planner 提前反映当前运行链早已存在的限制。

| 工具 | 旧描述 → 新描述 | 当前实际执行权限 |
|---|---|---|
| 7 个 ERP query、9 个 local 查询、erp_api_search | 三模式 → interactive；readonly_preflight true → false | 原本仅 interactive；无变化 |
| erp_execute、trigger_erp_sync | interactive/scheduled → interactive | 原本仅 interactive；无变化 |
| generate_image、generate_video | 三模式 → interactive；readonly_preflight false | 原本仅 interactive；无变化 |
| image_agent、manage_scheduled_task、restore_file | 三模式 → interactive/scheduled；readonly_preflight false | 原本禁止 preflight；无变化 |

完整 24 项差异见源码报告的 `planner_descriptor_differences`。除此之外，已有描述的风险、read/write 属性、输入 schema、输出 schema、权限、版本均通过基准对照。`safe/confirm → low`、`dangerous → high` 的旧 capability 标签保留；read_attributes 不能用来推算真实副作用。模式列表也是能力描述，scheduled 的危险操作及参数授权仍由 Policy 拒绝。旧快照不迁移、不追加权限，创建/提交/预检流程函数保持原样。

## 生产入口与请求状态

| 实际生产路径 | 确认与权限入口 |
|---|---|
| Web/Actor → 共享 chat execution_engine → ChatToolMixin | 创建 ToolExecutor；每轮可信上下文进入 ToolRuntime；保留 WS/Actor 确认与 lease 生命周期 |
| ScheduledTaskAgent → ToolLoopExecutor | 显式 scheduled/preflight、已有授权快照、组织/资源范围；不通过名字白名单扩大危险写权限 |
| 旧 `services.tool_executor.ToolExecutor.execute` | 原返回/异常投影保留；委托同一 runtime.execute 后 to_legacy，没有直接调用业务的兜底 |

生产源码只有两处 ToolExecutor 构造、一处 ToolLoopExecutor 构造，详见源码报告的路径与行号。`LegacyToolHandler` 只绑定请求内 `_handlers`，不回调公共 execute。旧 `legacy_policy_rules(name, scheduled_names)` 仅保留描述 API，规则来自 Spec，没有生产消费；旧 `validate_runtime_tool` 为通用 Planner 快照检查 API，不调用 Handler。

Registry 定义不缓存请求状态。用户/工作区 owner/组织/任务/资源边界仍在 ToolContext 与请求内 ToolRuntime；执行服务的调用预占、确认和资源选择也在请求内。测试覆盖 144 组完整目录上下文对照、同 call_id 不同用户并发执行、返回 schema 深拷贝、旧表修改无法覆盖运行风险/域、9 种全新进程导入顺序。

## 保留的业务适配与已知边界

- ERP：原部门/查询引擎、ApiEntry.is_write、query 写 action 拒绝、execute_raw 只读保护和写幂等锁保留；无组织 erp_agent/erp_analyze 的目录及原业务返回保持原样。
- 文件/沙盒：fid/fref/path、授权 scope、目标版本与确认绑定、restore record/no-clobber、内核锁及取消/内部重试保留。code_execute 缓存资格仍为 true；restore_file 仍 safe、串行、不可缓存，未改 invocation/replay 资格。
- 媒体：沿用图片注入、Provider、扣费/退款和错误卡片；generate_video 原 schema 的历史“异步 task_id”措辞保留，而 Handler 仍同步等待并返回原结果。修正文案/协议需独立授权，不混入定义迁移。
- 任务：管理工具仍返回表单/变更提案；不会因为 Spec 迁移新增重复确认。任务删除、提交、ChangeSet 与结算实现不变。
- 结果：保留 06 的 v1 writer/旧 reader 兼容、两种模型投影、WS content blocks、回放/缓存权限顺序及 best-effort 审计；未新增可靠投递基础设施。
- 独立通道：conversation_control、route_to_chat 及仅有旧校验条目的 route_to_image/route_to_video 不新增到运行 Registry。Provider 内置 Google Search 与沙盒 emit/read_file 也不作为新的公共 Handler 注册。见目录排除映射。

## 回退与板块 08 前置

本块未修改任何持久化格式、数据库迁移、result_payload、writer/reader、WS 或 invocation 状态。可回退到本块基准 `ca4c3d7e`，它已包含 06 的 v1 兼容 reader/writer；不回退到 06 reader 上线之前。源码逐字相同与回放回归均有证据，旧确认 binding 的 schema/规则摘要也保持一致。

08 的确定代码前置为：01–06 已在 main，本块 35 个显式 Spec、兼容投影、Planner 派生与全链回归齐备。流程前置仍需用户指令提交部署、用户验证确定候选、用户指令验收关闭，再核验 main 代码树与候选相同。本任务不执行 08。
