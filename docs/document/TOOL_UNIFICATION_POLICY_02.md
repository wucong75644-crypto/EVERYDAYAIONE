# 工具统一 02：权限矩阵与决策接口

本文件描述隔离的新 Policy；生产入口尚未使用。旧循环的危险确认故障行为不会因本块自动改变，完整接入属于板块 04。

## 1. 用户核对矩阵

允许 = `allow`；需确认 = `require_confirmation`；拒绝 = `deny`。下表默认工具在目录、组织、域、功能开关和可信授权范围内，危险调用有完整作用域及可用确认设施，但尚未批准。

| 行为 / 代表工具 | ask | auto | plan | 说明 |
|---|---|---|---|---|
| 普通读取 / file_search | 允许 | 允许 | 允许 | 仍受资源权限和原 Handler 约束 |
| 辅助计算 / code_execute | 允许并通知资源消耗 | 同左 | 同左 | 保留沙盒计算、状态和本地产物；不承诺任意 Python 严格只读 |
| 危险业务写入 / file_delete、erp_execute | 需确认 | 需确认 | 拒绝 | auto 不免确认；ERP 先检查 action |
| 既有 safe 写入 / restore_file | 允许 | 允许 | 拒绝 | 继承已确认兼容约束，不擅自升级风险；仍串行 |
| 生成 / image_agent、generate_image、generate_video | 允许并通知资源消耗 | 同左 | 拒绝 | `confirm` 风险仍是通知，不新增第二类批准弹窗 |
| 任务表单 / create、update | 允许 | 允许 | 允许 | 只生成表单，未创建/修改正式任务 |
| 任务变更提案 / pause、resume、delete | 允许 | 允许 | 允许 | 只生成 ChangeSet；实际业务变更仍由原确认流程处理 |
| 任务列表 / list | 允许 | 允许 | 允许 | 同一工具的动作按实际语义区分 |
| erp_agent、social_crawler | 允许 | 允许 | 拒绝 | 保留原 plan 的特定边界；ERP 分析使用 erp_analyze |

场景限制与上表取交集，不用 scheduled 或 preflight 替换 ask/auto/plan：

| 工具类别 | scheduled | preflight |
|---|---|---|
| 当前核心读取/辅助计算 | 可信任务范围内允许；plan 限制仍有效 | 可信预检范围内允许；plan 限制仍有效 |
| 危险业务写入 | 拒绝；现有名字快照不表达危险目标授权，即使传入交互批准也不能扩大能力 | 拒绝 |
| restore_file | 当前核心及固化范围内允许；保留 safe 语义 | 拒绝实际工作区写入 |
| image_agent | 当前核心及固化范围内允许；plan 拒绝 | 拒绝生成 |
| generate_image/video、内部 ERP function | 拒绝；不属于当前 scheduled 核心能力，名单不能扩张 | 拒绝 |
| manage_scheduled_task | 当前核心及固化范围内允许；仍仅返回列表/表单/提案，不代替业务批准 | 沿用整个工具不可用于预检的边界，含 list |

scheduled/preflight 的可信输入必须同时有 `task_id`、显式 `authorized_tool_names` 和 `authorization_snapshot.allowed_tools` 数组。空范围拒绝；缺少任一项或快照格式错误拒绝。Context 是服务端适配后的事实，不认证该快照本身。`permissions` 映射可携带已有 PermissionChecker 得出的权限点布尔值；声明 `required_permissions` 的 Spec 只接受字面 True。没有新增 ERP 角色映射或任务权限语义，路径/资源所有权仍由现有业务层与板块 04 可信适配器落实。

### 完整自动断言表

每个单元格固定按 **ask / auto / plan** 排列；A=允许、C=需确认、D=拒绝。非交互行使用完整可信范围。`test_mode_authorization_matrix` 对以下 81 个组合逐行断言，并同时验证 Registry allowed/advertised；`test_equivalent_new_and_legacy_specs_share_all_matrix_rows` 对 explicit/legacy 分别重验全部组合。

| 代表调用 | interactive | scheduled | preflight |
|---|---|---|---|
| file_search | A/A/A | A/A/A | A/A/A |
| restore_file | A/A/D | A/A/D | D/D/D |
| file_delete | C/C/D | D/D/D | D/D/D |
| image_agent | A/A/D | A/A/D | D/D/D |
| manage_scheduled_task(create) | A/A/A | A/A/A | D/D/D |
| manage_scheduled_task(list) | A/A/A | A/A/A | D/D/D |
| code_execute | A/A/A | A/A/A | A/A/A |
| erp_agent | A/A/D | A/A/D | A/A/D |
| social_crawler | A/A/D | A/A/D | A/A/D |

批准后危险调用在 ask/auto 可允许，但计划/场景/目录拒绝优先。未知工具、未知动作、未知 ERP category、query 携带写 action、erp_execute 携带只读 action 均拒绝；有效确认也不能绕过这些拒绝。

## 2. 对外接口与事实来源

代码均在 `backend/services/tools/`；从 `services.tools` 导入：

```python
registry = build_legacy_catalog()
policy = ToolPolicy(registry)
resolution = registry.resolve(
    trusted_context, policy=policy, advertisement=LegacyAdvertisement(),
)
decision = policy.decide(tool_name, trusted_context, normalized_arguments)
batches = policy.plan_batches(normalized_calls, trusted_context, confirmations=trusted_receipts)
```

- `ToolSpec.policy_rules: ToolPolicyRules` 声明 `operation`、`plan_allowed`、`execution_modes`、`action_rule`、`required_permissions`。旧构造签名新增有默认值字段，未审定 operation 默认为 unknown，由真实 Policy 拒绝；不是默认只读。旧目录全部逐名补齐声明，新旧不按 definition_kind 分流。
- `legacy_policy.py` 是既有工具的单一 Policy 声明源；scheduled 能力上界读取当前 `get_core_tools`，没有再抄一套核心名单。schema、风险、并发、缓存、effects 的原来源不改；未审定 effects 仍为 unknown。原 plan/preflight 散表仍服务尚未切换的旧入口，本块没有改写生产行为；后续由板块 04/07 接入/收拢。
- `ToolRegistry.check_access(name, context, *, policy)` 是名称资格统一入口：查询规范 Spec、目录可用性、Policy 模式/场景/授权。`resolve()` 与 `ToolPolicy.decide()` 都调用它；org、domain、personal、flag、授权名称范围只在 Registry 一处实现。`ToolAccessDecision` 仍只是名称资格，不是调用批准。
- `ToolPolicy.decide(name, context, normalized_arguments, *, confirmation=None) -> ToolDecision` 从 Registry 取权威 Spec，避免调用者传入一个更宽松 Spec。模型 JSON 中的身份、模式、权限和 approved 字段不能覆盖独立 Context/确认参数。此接口不执行通用 JSON schema 校验或目标解析；调用者须先按旧契约规范化参数、固定危险目标，不能在获准后静默改目标。
- `ToolDecision` 含 outcome、reason、risk_level、operation、parallelizable、cacheable、effects、replay_requirement、可选 confirmation_binding。拒绝/待确认均不能执行；不是 ToolResult，也不产生 uncertain、审计、业务错误或自动重试。
- `action_rules.resolve_action()` 仅同步读取领域数据：ERP query/raw 读 `TOOL_REGISTRIES`；erp_execute 按实际 category 选对应 registry。使用 `ApiEntry.is_write`，未复制读写 action 名单，Handler 的 execute_raw 写保护原样保留。任务 action 的 list / proposal 分类对应 ChatTaskManager 现有实现，新增未知动作不会自动归为安全提案。
- `ToolCall(call_id, name, arguments)` 冻结规范化参数；`plan_batches(...) -> tuple[tuple[PlannedToolCall, ...], ...]` 保持调用顺序。只合并连续 allow 且 Spec.parallelizable 且为读取/分析的调用；业务写入、生成、提案、不可并行、未知、拒绝和待确认均单独成批。重复 call_id 拒绝，空输入返回空 tuple。批次本身不表示批准，消费方必须检查 decision。
- `ToolSpec.replay_requirement` 当前默认 unspecified，仅透传为后续接口。未改变 restore_file 回放语义，没有缓存或持久化接入。cacheable、effects 直接来自 Spec，不从分批结果或 parallelizable 推导；code_execute 旧缓存资格保留。

## 3. 确认约束

`ConfirmationBinding(tool_name, call_id, arguments_digest, scope_digest, spec_digest)` 是不可变比较值；`ToolConfirmation(binding, status)` 仅接受 approved/rejected。它们**不是签名凭据，也不自行认证用户**。服务端适配器保存原 binding，经真实确认通道核验批准者后构造 ToolConfirmation；不可从模型 args 或客户端回传的 binding/approved 直接构造授权。

绑定包含：规范化参数的稳定 JSON SHA256；真实 actor、workspace owner、org、个人/群作用域、个人上下文许可、conversation/task、domain、两类模式、入口类型、授权快照及名称范围、功能开关和资源快照；另含 schema、handler/executor、risk、effects、回放及 Policy 规则的定义摘要。参数对象键顺序不影响摘要，参数值/列表目标、调用、用户、组织、工作区或上述作用域变化均不能复用批准。

| 情形 | 决策 |
|---|---|
| 危险调用有完整作用域、可用设施、未批准 | require_confirmation，附 binding |
| 缺 call_id 或 conversation/task 均缺 | deny: confirmation_scope_required |
| confirmation_available=False，含设施未建立/失败 | deny: confirmation_unavailable；即使传入旧 receipt 也不放行 |
| JSON/布尔值伪装 receipt | deny: invalid_confirmation |
| binding 不一致 | deny: confirmation_binding_mismatch |
| 用户拒绝 | deny: confirmation_rejected |
| 当前规则仍允许且准确绑定批准 | allow: confirmed |
| 确认前后 Policy/领域解析出错 | 异常传播；无异常放行或 legacy 兜底 |

Policy 不保存全局 approved、不生成 nonce、不操作 WS、不等待或计时、不消耗 receipt。板块 04 负责真实事件认证、确认生命周期/一次调用内消费、取消/超时/断连，以及等待后重取权限/作用域并再次 decide。预算与取消引用不被 Policy 调用。重复查询同一个纯决策不是业务重试；实际执行去重继续由后续编排和原 Handler 保证。

## 4. 分批示例与后续边界

输入“读 A、读 B、写 C、读 D、写 E”，结果严格为 `[A,B] → [C] → [D] → [E]`。即使写 Spec 错将 parallelizable 标为 True，已声明的 business_write 仍作为屏障；名字像读取但 parallelizable=False 也单独成批。只证明本块的纯分组与顺序，没有声称真实运行已并行；循环落实和事件屏障集成验证在板块 04。

板块 03 可消费决策类型与规范 Spec，继续只建立隔离 Dispatcher/Handler/ToolResult；不得切生产或把拒绝调用交给原 Handler。板块 04 的完整切换、板块 06 的缓存/回放/审计载荷和板块 07 的定义收拢不属于本块。实际测试证据与用户验证步骤见 [验收记录](TOOL_UNIFICATION_ACCEPTANCE_02.md)。
