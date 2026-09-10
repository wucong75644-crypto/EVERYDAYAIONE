# 工作区资源选择与授权衔接（已实施，待部署/用户验收）

日期：2026-09-10。基于 bb4449a0 与 [生产诊断](FILE_WORKSPACE_SCOPE_DIAGNOSIS_20260910.md)。用户明确“开始开发吧”后已实施；2960 项相关测试通过，2 项既有真实模型测试未验证。当前代码为原任务未提交差异，尚未部署；完整版本与 A/G 证据见 [最新验收记录](TOOL_UNIFICATION_ACCEPTANCE_04.md)。

## 实施记录：实际接口与边界

以下优先于后文设计阶段的拟定名称；没有新增服务、数据库或授权 UI。

| 设计责任 | 实际实现 |
|---|---|
| 可信授权上界 | `tools/resource_access.py::ResourceAccessBoundary` 包含不可变 rules/source/known/expires_at，`ResourceRule` 指定 list/read/delete/restore 与相对 paths/directories；身份/组织/任务继续保留在原 ToolContext，resource_access 仅为内部不可变快照 |
| 选择线索 | `ResourceSelections` 记录已获准成功搜索的 (scope, directory) 集合；`scope` 根据显式值、已核验签名引用、清单内精确目标或唯一浏览范围解析；每次 execute 首个 await 前固定快照 |
| 已绑定操作 | 沿用 `PreparedFileCall`，增加 browse_directory；原路径/版本/确认/锁内复核复用，不新建 PreparedResourceOperation 类 |
| 同步权限检查 | `resource_boundary` 构建入口上界；`runtime_context` 刷新；Policy 确认绑定 resource_access；`FileTargetResolver` 对具体路径检查 action，列举/内容过滤发生在结果计数及内容读取前 |
| 错误与止损 | `ResourceAccessError`/`FileTargetError` 的 code/recovery/scope 进入现有 ToolError.retry_context/旧文本投影；Chat 与 ToolLoop 消费 Runtime 的终止原因，不写新持久化载荷 |

**权限来源保持明确**：普通交互沿用当前 owner 工作区访问资格，并与既有身份/组织/mode/domain/业务授权/危险确认共同限制；内部入口可传可信有限上界。未新增从自然语言生成文件授权的机制，模型自称“获准测试文件”无授权效力。定时入口仅把现有 `template_file.path/name` 转为精确只读 ResourceManifest；没有现成清单时明确未知并拒绝文件调用，不因为 allowed_tools 包含 file_search 就开放整个 owner 工作区。指定工作区是选择请求，不是权限授予。

Actor 恢复仅采用本任务既有 completed file_search 工具块中的显式 scope 输入，绝不解析结果文本。旧 checkpoint 不包含隐式调用的规范 scope，恢复时不按完成顺序猜测；这类历史通过已签名引用或显式 scope 重新选择，权限照常重验，无业务重放。

前置与 Handler 共用 `manifest_matches`，使部分名称匹配单图也先检查 read，目录 `.`/`uploads/` 即使只含一张图也只列举。有限授权根目录列表在原隐藏/staging 过滤之后做资源过滤，避免误拒绝合法文件；原查询遍历上限保留。

本次契约变化是有依据的省略 scope 定位、动作范围限制和无法恢复错误止损。原工具名/参数别名及写调用 ledger hash 不变，WS/结果投影/持久化格式不变。长引用、分页、结构化总数和新 UI 授权管理未实施，不属于本次根因衔接修复。

## 1. 设计依据

没有一个统一的“大厂标准”规定 Agent 文件流程。下面引用的是具体规范或官方产品机制，并据此提出项目设计，不引入这些厂商服务。

| 外部依据 | 已核验的机制 | 本项目采用的原则 |
| --- | --- | --- |
| [AWS AgentCore / Cedar](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/policy-understanding-cedar.html)、[Cedar 安全要求](https://docs.cedarpolicy.com/other/security.html) | principal/action/resource/context 决策；默认拒绝，显式禁止优先；授权输入必须可信 | 模型提出操作；身份、资源归属和权限由服务端核验 |
| [Google Cloud Credential Access Boundaries](https://cloud.google.com/iam/docs/downscoping-short-lived-credentials) | 限定资源及权限上界，该产品能力限 Cloud Storage | 本轮/定时限制只缩小既有权限，发现资源不能新增权限；不照搬云产品 |
| [MCP 2025-11-25 Tools](https://modelcontextprotocol.io/specification/2025-11-25/server/tools) | 输出 schema、结构化结果、可纠正的执行错误、兼容文本 | 工具应提供机器可判定的范围和失败事实；当前仍投影到旧消费者 |
| [Google AIP-158](https://google.aip.dev/158) | 不透明分页游标、分页请求重新鉴权；引入分页存在行为兼容影响 | 返回数量不等于总数；分页/短引用不能冒充授权；后续契约改造须迁移 |

这些依据没有要求新增微服务、策略引擎或专用数据库。当前先复用 Registry、ToolPolicy、ToolRuntime、PreparedFileCall、Actor 和现有存储。

## 2. 恢复的系统约束

1. 资源身份、浏览/选择范围、执行授权分离。current/workspace 是定位及查询语义，不能直接成为跳过资源授权检查的开关。
2. 一次获准搜索返回的目标可稳定用于后续调用，模型不必重复拼写身份/归属；漏参不能静默变成另一个数据集合。
3. 可以看到文件名不代表可以读取其内容，可以读取不代表可以删除。动作权限单独判定。
4. 本轮状态不跨任务、跨用户、跨组织、跨频道传播；并行调用不能互相改变默认目录或范围。
5. Actor 重启仍可核验相同资源及当前权限，确认拒绝/失效、权限撤回、预算耗尽和取消均不会因恢复而失效。

## 3. 三个独立对象

本节保留设计时的责任划分；实际类名和字段以上方实施记录为准。

**ResourceAccessBoundary（授权上界）**：真实 actor、workspace owner、org/channel、任务标识、资源限制、允许的动作、有效期/授权版本/来源。由认证入口、现有权限数据、附件清单、已有明确批准和定时授权构建。模型传入的 scope、工具输出、自称“获准”及聊天历史都不能自行生成授权。

附件清单保持不可变；搜索命中不追加到 ResourceManifest.allowed_paths。没有资源限制与无法加载限制是不同状态，不能都用 None 表示并放行。有效权限由身份权限、任务限制、mode/domain、定时快照等共同限制，任一未知或禁止都拒绝。

**ResourceSelection（资源选择）**：规范资源标识、相对路径、类型、版本、owner/org、定位来源，以及关联的请求/调用。记录“选中了哪里”，不携带可绕过 Policy 的执行许可。目录浏览和文件操作共享解析规则。沿用现有 fref1 身份与版本核验；完整路径/fid 通过同一解析器适配。

**PreparedResourceOperation（已解析操作）**：在当前 PreparedFileCall 上演进，绑定规范化动作、确定目标集合、当前授权依据、版本/内容摘要与确认绑定。准备后不再从原名字重新选择文件。缓存、invocation 和 Handler 使用同一个绑定对象；授权快照变动后重新判断。

## 4. scope 与调用兼容

模型仍可表达“搜索工作区”“只查本轮附件”，但这些都是待验证的查询选择。服务端先判定可访问范围，再解析目标。

| 输入 | 推荐行为 |
| --- | --- |
| 明确 current | 限定当前附件；不因找不到自动改 workspace |
| 明确 workspace | 请求工作区选择，仍要与当前动作的授权上界求交 |
| 已验证的资源引用，省略 scope | 从资源身份恢复位置并检查当前授权；不能机械套用 current 造成假拒绝，也不能凭引用扩大授权 |
| 完整路径/fid，省略 scope | 使用本轮唯一且可验证的选择依据；歧义返回要求限定范围，不取最近一次 scope、不按 basename 跨目录回退 |
| 后续目录浏览省略 scope | 绑定到同一浏览请求的明确范围；不能依赖共享“最后目录”。同时存在 current/workspace 两个浏览上下文而无法确定来源时明确要求选择 |
| 无范围、无选择依据 | 保持旧 current 语义，明确说明检索的是当前附件 |
| scope 与绑定目标冲突 | 拒绝并解释冲突，不静默覆盖显式范围 |

不得建立会话级 last_scope=workspace。并行浏览不同目录、下一轮切换附件、定时执行、Actor 恢复会使这种办法串范围。

现有路径/fid/fref/tool name 保留；对“省略 scope 且有可靠已选资源”的行为调整需要作为明确批准的契约增量验收，不能冒充完全无行为变化。旧 execute 也进入同一适配器，不能独立采用宽松默认值。

## 5. 正常、确认与恢复流程

认证入口构建授权上界 → 模型提出查询 → Policy 校验查询动作及范围 → 搜索返回资源身份 → 统一解析器绑定选中资源 → Policy 校验具体动作/资源 → 准备版本 → 必要时沿用现有确认 → 刷新授权及锁内版本复核 → 缓存/幂等判断 → Dispatcher/原 Handler → 旧兼容投影。

危险确认展示准确目标；批准只绑定该动作、参数、资源版本和权限依据。拒绝、超时、断连、异常不执行；批准后目标/范围变化不复用。保留原资源通知和 ChangeSet 提案机制，不新增第二次弹窗。

Actor 恢复重新验证引用、当前身份/范围及已有确认。签名只证明引用未被篡改，当前授权才决定是否可用。当前块不引入只靠进程内字典才能解析的短 ID，也不写新 replay 载荷。

浏览来源若需恢复，只允许使用既有 checkpoint 中结构化工具调用参数及其已完成记录，作为查询选择的线索，并重新鉴权；不能把模型叙述或人类可读结果文本解析成权限/身份。无法确定唯一浏览来源时返回显式未执行结果。已完成业务的回放仅检查当前资源权限，不能重做删除、恢复或文件分析来重建状态。

## 6. 失败必须指导正确下一步

拟在当前内部错误对象中保留 code、execution_state、requested/effective_scope、recovery_action；当前块通过旧文本/AgentResult 投影输出，不新增 WS 或 ledger 格式。

| 失败 | 明确结果 | 合法恢复 |
| --- | --- | --- |
| 空集合 | “当前附件范围未找到”，区别于工作区目录为空 | 有当前授权时选择相应工作区浏览上下文 |
| 范围缺失/歧义 | 未执行，缺少哪一项选择依据 | 选择上下文或具体资源 |
| 资源不允许当前动作 | 未执行、没有权限 | 停止，按既有授权渠道处理；不能建议改 scope 绕过 |
| 引用未知/过期 | 未执行 | 在现有授权范围重新发现 |
| 资源变化 | 未执行，旧确认失效 | 重新选择/准备，危险动作重新确认 |
| 权限服务不可用 | 未执行，权限不可验证 | 可提示稍后再试；不得默认允许 |
| 副作用不确定 | uncertain | 查询已有 ledger/人工核验，不自动重试业务 |

这里设计的是纠正输入，不增加统一自动重试器。每次纠正仍消耗原预算、经过取消和 Policy；相同错误且没有新选择/授权事实时停止无效重试。模型的最终语言仍有出错可能，必须通过真实模型评测衡量，不能承诺措辞绝对正确。

## 7. 搜索量与引用成本

关联问题的目标契约：entries、returned_count、total_count（未知为 null）、has_more、next_cursor。建议交互默认小页如 20 项，并设置结果 token 预算；达到预算允许更少条，不截断单个身份引用。exact 总数仅在确实完成计数时返回。游标绑定查询/排序/范围指纹，下一页重新鉴权；目录变化须采用明确的一致性语义或拒绝旧游标，不能承诺任意 NAS 变动下无重复/无遗漏。

短引用应是服务端可恢复、不可篡改或不可猜测的资源定位符，绑定正确命名空间和生命周期；它不是 bearer grant。缓存失效时明确失效/重新发现，不能按名字猜回目标。既有 fref1 长引用仍可兼容。短引用方案必须覆盖 worker 切换、Actor 恢复和旧记录回放，不能只加内存映射。

这是结果消费/持久化演进的设计方向，涉及原板块 05/06 及 schema 行为兼容。当前 04 只修可靠选择、授权与原投影中的必要错误反馈；不在本次设计请求下直接开发分页、短引用存储或新版结果 UI/replay。

## 8. 当前需要明确的产品语义

建议维持“已有授权的普通读取无需额外弹窗；删除沿用原确认”。但必须明确这些授权从哪里来，不能凭模型声称“用户允许”构建 ResourceAccessBoundary。

本次输入“读取一个获准测试文件”没有标识具体测试文件。若系统已有用户选择的测试文件/目录，按该集合执行；若没有，列出候选请用户指定一次。模型不能任意挑业务文件并称其获准。“搜索整个工作区”不自动代表已批准每种动作。

自然语言可用于提出目标及查询条件，不应成为新增安全权限的唯一事实。现有已认证的产品权限、结构化资源选择、真实确认事件、定时快照是可靠来源。若产品希望以后通过自由文本改变持久授权，需要另外设计明确的批准及持久化流程；本修复不引入。

## 9. 代码职责与同类入口

| 位置 | 推荐职责 |
| --- | --- |
| services/tools/context.py、runtime_context.py | 明确授权上界及未知状态；刷新身份/范围；不让 model args 构造可信对象 |
| services/file_resources.py、tools/file_calls.py | 统一目录/文件选择、引用/旧参数解析及冲突；准备对象绑定 |
| services/tools/policy.py、runtime.py | 动作与具体资源的统一决策，确认前后复核，拒绝先于缓存/Handler/invocation |
| services/agent/file_tool_mixin.py、file_analysis_service.py | 消费绑定对象，不自行重设 scope 默认值；原分析实现保持 |
| services/handlers/chat/execution_engine.py、chat_tool_result_mixin.py | 本轮选择上下文和可纠正错误衔接；保持 Actor 安全点、原投影 |
| services/agent/scheduled_task_agent.py、tool_executor.py | 定时资源上界和旧入口一致；allowed_tools 不是文件授权。当前 scheduled 的 resource_manifest=None 需要显式审计，不能当成全盘放行 |
| tests/test_file_target_execution.py、test_tool_production_integration.py 等 | 生产轨迹固化、多入口遗漏/冲突/恢复测试 |

同类排查覆盖搜索→分析/删除/恢复、目录→子目录、附件/历史文件选择、分析产物→消费的来源绑定、定时模板/文件、ERP action 与实际对象。复用共同入口，不改 ERP 内部编排或把任意 sandbox Python 宣称为已具备逐文件权限控制；后者是独立执行环境边界。

## 10. 验证与发布

- 固化本次真实 10 步轨迹；引用省略 scope 可在已有授权内读取，缺权保持 Handler=0，目录查找不静默切换集合。
- 参数矩阵：path/fid/fref、current/workspace/省略/冲突；不完整名字、多候选、伪造/旧/跨主体引用。
- 各入口：Chat、Actor、ToolLoop、旧 execute；群 actor/owner、跨 org/channel、定时资源范围、权限服务失败。
- 生命周期：Actor 冷恢复、确认拒绝/批准/超时/断连/异常/改参数/改版本、取消、预算、并行浏览无污染、幂等/缓存拒绝顺序。
- 测试同时覆盖正确调用与模型漏参/误参，不以完美 mock 参数证明可用。真实模型在临时、获准只读资源上重复评测，记录模型版本、候选 SHA、成功率、无进展调用数和 tokens；真实写入另须明确授权。
- 04 核心链作为一个完整变更验证后再部署，不上线“自动承接选择但 Policy 尚未接资源约束”的中间态。运行受影响既有测试并对照 A-04/G 项补证。
- 生产遥测复用既有日志/审计，关联 task/call、请求范围/有效范围、拒绝码、Handler 是否开始；不记录文件正文或完整敏感引用，不增设可靠投递基础设施。
- 该核心方案不改持久化格式，可按受控发布回退业务代码；不能保证撤权规则放宽后的回滚具有相同安全行为，应在发布前比较两版策略。分页/短引用后续若涉及状态或载荷，另附迁移及回滚方案。

结论：推荐以“可信授权上界 + 明确资源选择 + 已绑定操作”完善现有统一执行链。当前仍处于设计建议阶段，原生产问题尚未修复，04 验收保持未通过。
