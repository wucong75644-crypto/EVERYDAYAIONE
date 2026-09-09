# 工具统一：共同约束与板块交接

更新日期：2026-09-09。当前板块 03：**技术验收通过，待部署/用户验收**。本块未提交、未推送、未部署、未合并；不能启动板块 04。

- 板块 01–02 前置已核验：用户明确确认已验收进入 main。最新 origin/main `8e74f57de1073cbef8b3b8c8256e4409d60c506b` 合入板块 02 `4084db4e`，二者 tree 同为 `81f34e6dc3bee23db8499bcceb9b67f91257e9ad`；板块 01 `b4c854ac` 也在其祖先链，合并 `2e8fdb2d` 与候选 tree 一致。已关闭的 01/02 工作树不在当前工作树清单；历史验收记录中的提交前状态保留，不替代当前 Git 与用户验收事实。
- 当前分支：`codex/task/20260909195904-tool-unification-03`
- 当前工作树：`/Users/wucong/EVERYDAYAIONE/worktrees/tool-unification-03`
- 当前基准及 HEAD：`8e74f57de1073cbef8b3b8c8256e4409d60c506b`；实现为该基准上未提交差异，HEAD 不是板块 03 已测候选。
- [板块 03 验收记录](TOOL_UNIFICATION_ACCEPTANCE_03.md) / [执行与结果接口](TOOL_UNIFICATION_EXECUTION_03.md) / [代码指纹及结构检查](tool-unification-evidence/03-source-checks.txt)
- 历史：[板块 02 验收记录](TOOL_UNIFICATION_ACCEPTANCE_02.md) / [Policy 接口](TOOL_UNIFICATION_POLICY_02.md) / [板块 01 验收记录](TOOL_UNIFICATION_ACCEPTANCE_01.md) / [目录与代表契约](TOOL_UNIFICATION_CATALOG_01.md)。01/02 的记录是各自交付时的快照。

## 总体目标与顺序

渐进建立 ToolSpec → ToolRegistry → ToolPolicy → ToolDispatcher → ToolHandler → ToolResult，统一公共工具调用外层，不重写业务引擎。编号顺序固定：

1. 工具定义和注册表（已进入 main）。
2. Policy：模式、业务权限快照、参数级风险、确认与分批（已进入 main）。
3. Dispatcher / Legacy Handler / ToolResult 基础（当前本块）。
4. Chat、scheduled ToolLoop、旧 execute 实际入口完整切换。
5. 实时结果与展示。
6. 回放、缓存、审计及新持久化载荷。
7. 剩余定义所有权收拢。
8. 跨板块整体验收。

每块顺序为核验最新代码/前置 → 实现 → 验收矩阵 → 修复复验 → 技术记录 → 用户指令提交部署 → 用户验证 → 用户指令清理工作树 → 核验 main 已包含成果 → 下一块。不能因接口已经存在或单元测试通过就启动下一块。

每个新对话由 `scripts/task-worktree.sh start` 从最新 origin/main 建立自己的 `codex/task/*` 工作树；不得复制其他任务未提交文件。用户“提交部署”才按 release 入口提交推送并部署确定任务候选，保留工作树，不合并 main；用户“清理工作树”才按 `deploy/release.sh --accept-and-close` 验收合并并清理，不重复部署，最终 main 代码树须与已测试候选一致。禁止直接 deploy/deploy.sh，发布/验收遇到被禁旧 Runtime 平台路径须停止。

## 持续有效的共同边界

- 保留名称、合法 JSON 参数、参数别名及规范化、旧导入与 ToolExecutor.execute 门面、AgentResult/ToolOutput 兼容和 WS content blocks。schema 复用工厂，不复制参数定义，不引入循环导入。
- 1–3 不接生产；4 必须一次完成可信上下文、授权、确认、分批与新旧入口统一。禁止拒绝后绕回旧 handler，也禁止先执行再补权限。
- ERPAgent 当前走计划提取 → 部门 Agent → 查询引擎；ToolLoopExecutor 的当前生产创建者为 ScheduledTaskAgent。部门内部 SQL/IO、ERP 查询引擎/幂等锁、沙盒内核锁、媒体计费/重试/退款不属于外层重写范围。
- actor_user_id 与 workspace_owner_id 分离；群工具不能读取操作者个人上下文。身份、组织、授权、资源来自可信服务端，模型参数不能创建权限。
- allowed 与 advertised 分离；动态发现只选择展示，不能扩权。调用批准、资源检查须先于缓存/回放和业务执行。
- ask/auto 都不豁免危险确认；CONFIRM 仍为资源消耗通知，不新增第二类弹窗。plan 保留已明确的分析/检索/辅助计算能力，阻止业务写入及生成；不新增自然语言批准识别。沙盒辅助计算不等于任意 Python 严格只读。
- interactive/scheduled/preflight 与 ask/auto/plan 是两个维度；仅有 allowed_tools 名单、task_id 或没有界面均不能充当参数级危险授权，也不扩大定时任务既有写能力。
- ERP query function 携带写 action 必须拒绝并继续要求 erp_execute；风险取 ApiEntry.is_write，保留 execute_raw 读保护，不能把查询入口升级为确认后写入口。
- manage_scheduled_task 有些 action 返回表单/变更提案，保留既有确认流程；file_search 目前不自动治理数据文件；generate_video 当前 handler 等待结果，不改成新异步协议。
- 风险、并行、缓存、副作用分别声明；保留未迁移工具的旧标记。code_execute 缓存、restore_file 风险/回放等语义变更不能隐藏在 metadata 迁移里。
- ToolResult 包装 AgentResult，不全仓替换。Chat 的 to_message_content 与 ToolLoop 的 to_tool_content 投影分别保留；FileReadResult 图片注入、表单终止、ERP 交互 TABLE 避免重复显示、定时 TABLE 收集都须保留。
- invocation succeeded 表示调用完成可回放，不等于业务成功；uncertain 表示外部效果未知。策略拒绝不记为 uncertain；回放或投递失败不得重做业务。取消继续传播，不新增统一自动重试器。
- 审计复用现有写入器，保证字段透传、正确状态和不重复记录，不增加可靠投递基础设施，不宣称数据库故障下零丢失。
- 5 在新协议未上线时只写旧格式兼容投影；6 才切已验证版本载荷。涉及持久化的回退需证明旧 reader 兼容，本块无持久化变更。

## 板块 01 实际接口

代码位于 `backend/services/tools/`，生产调用方仍只引用旧路径。以下为板块 01 原接口，板块 02 增量见后文。

| 文件 | 已实现接口 / 责任 |
|---|---|
| spec.py | ToolSpec、ToolAvailability、Exposure；定义验证、不可变 JSON 快照及导出副本 |
| context.py | ToolContext；显式可信身份与请求快照，禁止不一致的个人/群工作区组合 |
| registry.py | ToolRegistry、ResolvedTools、ToolAccessPolicy、ToolAccessDecision、ToolAdvertisement；目录资格与展示交集 |
| legacy.py | build_legacy_catalog、LegacyAdvertisement、validate_legacy_coverage；读取旧工厂/旧 metadata，覆盖当前完整目录及内部 handler |
| __init__.py | 上述公开 Python API 的集中导出；模块导入不初始化 catalog 或业务 executor |
| tests/test_tool_registry.py | 本块 98 个隔离用例；契约/目录/允许拒绝/快照/动态发现/接口边界 |

### Spec

`ToolSpec` 使用 keyword-only 构造。必填 name / schema / domain / availability / risk_level / parallelizable / cacheable / effects / executor_type / handler_key / exposure / source；definition_kind 为 explicit 或 legacy；compatibility_notes 记录旧语义；legacy_validation_schema 保留旧部分校验表。

- `schema` 是原 OpenAI function 完整 schema 的不可变快照；`to_schema()` 返回独立 dict 副本，不修正描述、参数、required、enum 或嵌套字段。
- 公开项必须有 schema。fetch_all_pages 读取已有内部完整工厂；get_conversation_context 没有模型 schema，schema=None，保留旧 limit 部分校验条目于 legacy_validation_schema，`to_legacy_validation_schema()` 返回副本。
- domain 只用当前 general / erp / shared。get_conversation_context 在旧域表中无条目，本块仅给其内部入口 general 域，仍不对模型开放。
- availability 只声明目录事实：requires_org、requires_personal_context、feature_flags；不承担 ERP action 写判定、业务 RBAC 或确认政策。
- risk_level 保留 safe / confirm / dangerous；executor_type 当前仅 legacy，handler_key 指向现有 ToolExecutor._handlers 的 key，无新执行器或 Dispatcher。
- 3 个显式 Spec 为 search_knowledge、file_search、file_delete；其余是 Legacy Catalog 适配。未独立审定的副作用显式 unknown，不由 safe、parallelizable 或 cacheable 推断。

### Context

必填 actor_user_id、workspace_owner_id、org_id、context_scope(user/channel)、personal_context_allowed、agent_domain(general/erp)、permission_mode(ask/auto/plan)、execution_mode(interactive/scheduled/preflight)。

可选 authorized_tool_names（None=本字段不缩小范围，空集=全部拒绝）、authorization_snapshot、feature_flags、resource_manifest、entrypoint(model/legacy_internal)、conversation_id/task_id/call_id、budget/cancellation。数据字典与列表递归冻结；运行引用仍由请求拥有；Registry 不存这些引用。不同请求每次重新解析，不缓存用户/组织结论。

Context 构造器不认证用户、不查询组织成员权限。板块 04 的可信适配器须从已解析 ExecutionScope 等真实来源取值；resource_manifest 为经过适配的 JSON 快照，budget/cancellation 为显式运行句柄。**不要从模型 args 构造 Context 或把一个客户端传入的 allowed_tools 当成授权。**

### Registry / Policy 衔接

```python
catalog = build_legacy_catalog()
resolution = catalog.resolve(
    trusted_context,
    policy=policy,                         # 必需；板块 02 实现
    advertisement=LegacyAdvertisement(),  # ERP/定时可传现有初始名称集合
    discovered_names=discovered_names,
)
# resolution.allowed / advertised: 只读 name -> ToolSpec 映射
# resolution.denied: name -> reason 映射
# resolution.advertised_schemas(): 完整原 schema 的新副本列表
```

`ToolAccessPolicy.resolve_access(spec, context) -> ToolAccessDecision(allowed: bool, reason: str)` 为当前唯一名称级权限决策扩展点；同步消费可信快照，有 IO 的身份/权限解析由可信适配器预先完成。Registry 在调用此接口前执行目录事实筛选：内部入口、domain、org、个人上下文、功能开关、授权名称上界。Policy 不必复制这些规则；板块 02 增加 `check_access` 供 resolve 和调用决策共用，后续执行入口使用同一 Policy 参数级判断。

Policy 负责模式/场景、既有授权快照、可表达的业务权限，板块 02 已增加规范化参数级 allow / require_confirmation / deny、实际风险与分批。当前名称级 allowed **不是执行许可或危险确认凭据**；板块 01 没有实际 Policy，板块 02 已增加隔离的纯 Policy，但仍无生产接入或 UI 操作。缺少 policy 为调用错误；policy 报错传播，没有放行兜底。测试中的 StubPolicy 仅是验证衔接点的假实现，不是未来政策源。

`advertised = PUBLIC ∩ allowed ∩ 当前展示选择`，有稳定名称顺序。LegacyAdvertisement 复用 get_tools_for_mode 的原核心/plan 展示事实；接受既有 ERP 初始集合；scheduled/preflight 不扩展 discovered_names。自定义展示器即使输出未知或内部名称也不能扩权。内部 exposure 在 legacy_internal 入口可属于 allowed，但永不 advertised。

当前旧 plan 列表仍含 file_delete/restore_file/manage_scheduled_task；本块保留这份展示规则，与真正的执行权限分离。板块 02 通过同一 Policy 收紧允许集合，不在 Registry 再抄一份 plan/preflight 工具名单。板块 01 不声称完成目标 Policy 护栏。

`validate_legacy_coverage(catalog, public_schemas=完整组织目录, handler_names=真实组织 executor._handlers)` 返回明确问题码 tuple；发现重复 schema、缺失/额外公开项、完整 schema 漂移、缺失绑定/内部 Spec。它不构造业务对象；测试用真实 executor 构造目录，未执行业务。未知工具 require 抛 KeyError，get 返回 None。

## 兼容、验证与问题记录

生产 get_chat_tools/get_core_tools/get_tools_for_mode、ToolExecutor.execute、Chat 和 scheduled 循环及旧 re-export 文件没有修改，也没有引用 services.tools。新旧 schema 完整深比较、部分校验表逐工具对照；目录验证覆盖 35 个绑定。返回投影、WS、持久化没有变化；相应既有测试通过。

最终验证：98 新隔离 + 603 既有主组 + 11 既有 ERP 组，共 712 passed，0 failed/error/skipped。保存命令、每个用例日志及基准复现于 [验收记录](TOOL_UNIFICATION_ACCEPTANCE_01.md)。联合收集旧 ERP Mixin 测试会污染 sys.modules，已在基准复现；使用分进程完整复验，无修改/跳过/削弱旧断言。该旧测试隔离问题仍在旧文件中，运行方式与证据已记录。

限制：没有连接真实 ERP/数据库、执行真实删除或付费生成，没有部署或用户生产验证。功能开关/授权和业务测试使用测试快照及 mock；不能将测试成功称为生产效果。

回退：本块仅新增目录、测试与文档；撤回这些新增项即回到基准 `051b24c5` 的生产代码树，没有数据迁移或新载荷，无需新增开关或生产回退演练。

## 板块 02 实际增量

- 新增 `policy.py`：ToolPolicy、ToolDecision、ConfirmationBinding、ToolConfirmation、ToolCall、PlannedToolCall；`decide` 纯决策，`plan_batches` 连续合批且保留串行屏障。
- 新增 `action_rules.py`：同步 ERP ApiEntry.is_write 判定（query/raw 写 action 拒绝、execute category/动作校验）及任务列表/提案分类，无业务调用。
- 新增 `legacy_policy.py`：本目录的唯一 Policy 声明，scheduled 能力上界复用现有核心工具工厂，不复制核心/预检阻止名单。新旧 Spec 使用同一规则引擎。
- `ToolSpec` 增加不可变 `ToolPolicyRules` 和默认 unspecified 的 `replay_requirement`；原 schema/旧验证表及 metadata 保留。`ToolContext` 增加可信 `confirmation_available=False`，验证可选调用/会话/任务 ID；确认凭据通过独立参数输入。
- `ToolRegistry.check_access(name, context, *, policy)` 把已有可用性规则公开为单一调用点，供 resolve 与 decide 共用；没有额外可用性名单。
- 决策返回明确 outcome/reason、实际 risk/operation、分批资格及原 cacheable/effects/replay 元数据。缓存不从并发资格推导；restore_file 的 safe 风险和 code_execute 缓存资格保持原样。
- 模式/场景矩阵、确认绑定范围、可信适配器职责、参数规范化前置和消费示例详见 [02 接口文档](TOOL_UNIFICATION_POLICY_02.md)。批准值不是签名凭据；适配器不能信任模型 JSON、客户端提供的 binding/approved 或仅有工具名字的危险授权。
- 新增 `backend/tests/test_tool_policy.py`。本块最终：552 个新用例 + 98 个 Registry 回归 + 516 个相关旧入口回归 + 11 个独立 ERP 回归，共 **1177 passed，0 failed/error/skipped**。命令、逐用例日志、结构检查与问题记录见 [02 验收记录](TOOL_UNIFICATION_ACCEPTANCE_02.md)。
- 未调用真实 ERP/删除/生成；未操作 WS/业务 Handler/缓存；未新增重试或生产导入。真实确认超时/断连、运行并发、业务授权源适配仍由板块 04 验证，不以本块测试代替。
- 回退：撤回本块新增文件并把 services/tools 的五个增量文件恢复到 `2e8fdb2d`，保留板块 01。无持久化/数据库/新协议载荷，不需迁移或回放兼容处理；生产入口无需开关。

## 板块 03 实际增量

- 新增 `dispatcher.py`：ToolHandler 协议、ToolDispatcher，只分发入口签发的 allow 调用，按 executor_type/handler_key 定位；无 UI、业务实现、批次或重试。未知/缺失绑定仍为 ValueError。
- 新增 `legacy_handler.py`：LegacyToolHandler、build_legacy_handlers，保留原 `_handlers` callable 并调用；不回调公共 execute。新显式/legacy Spec 都能绑定同一原业务函数。
- 新增 `execution.py`：ToolExecutionService.execute 逐次执行 Registry/Policy 检查、取消检查、请求内一次性预占与分发；拒绝/待确认 Handler 为 0，批准后为 1。普通异常包装，取消继续传播；execute_legacy 返回原类型/抛原异常。
- 新增 `result.py`：ToolResult、ToolError、ToolArtifacts、ToolExecutionMetadata。原对象及 runtime metadata 无损保留；Chat/ToolLoop 惰性旧投影、图片注入字段、表单终止字段、错误/重试、文件/emit、audit/执行状态映射齐备。无序列化器、审计写入或协议切换。
- `__init__.py` 仅增加公开导出；生产 ToolExecutor、各 Mixin、Chat/ToolLoop、WS、invocation 与返回类型源码均未改动。
- 新增 `test_tool_execution.py`（41 passed）和 `test_tool_result.py`（49 passed）。最终 90 新用例 + 650 Registry/Policy + 720 相关旧入口/结果 + 11 独立 ERP = **1471 passed，0 failed/error/skipped**。初轮 1 项新测试符号误写已修复并复验，详细记录见 [03 验收](TOOL_UNIFICATION_ACCEPTANCE_03.md)。
- 三代表测试实际经过原 `_search_knowledge` 或原文件 `_handlers` 闭包 → `_file_dispatch`，仅外部 IO/删除实现为 mock；公共 execute 递归陷阱零调用。无真实双执行、删除或付费生成。
- 接口、各字段与旧结果逐项对照、请求内一次性边界、异常行为与板块 04 的可信装配责任见 [03 接口](TOOL_UNIFICATION_EXECUTION_03.md)。特别注意：ToolResult 是内存信封，不是新持久化 payload；ToolLoop 非 AgentResult 的现有消费限制没有被本块改写。
- 回退：撤回本块四个新模块、两项测试及文档增量，将 tools/__init__.py 恢复到 `8e74f57d`；保留 01–02，无数据迁移、生产开关或 WS 新格式。

## 下一板块前置

板块 04 的代码前置已具备：Spec/Registry/Policy、仅允许分发、原业务 Handler 复用及 ToolResult/旧兼容出口已在隔离环境贯通。后续必须完整接入可信 Context/Executor 身份配对、参数/资源解析、确认、分批、缓存/回放之前的权限边界和旧 execute 门面；不能先执行再补策略。实际接入契约见 03 接口文档第 4 节。

流程前置仍缺：板块 03 用户明确提交部署确定候选 → 用户核对调用计数/结果对照及原有只读行为 → 用户明确验收关闭 → 受控入口确认 main 包含本块。**本任务止于板块 03；用户验收关闭前不能启动板块 04。** 最终候选 SHA 在实际提交部署后记录，并核对被测代码指纹。
