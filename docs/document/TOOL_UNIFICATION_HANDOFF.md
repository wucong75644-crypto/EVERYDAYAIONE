# 工具统一：共同约束与板块交接

## 当前有效状态：创建表单生产复验修复（2026-09-12）

后续升级已以 `8773b8b77ed0932445b9ba8b724fa52498cdbd51` 完成受控生产发布，迁移 254/255 已应用。用户随后报告创建时表单缺失：后端已保存 form，前端运行时枚举拒绝 datetime-local 并剥离条件 not；另发现严格解析混用旧格式指令、模型改写原文。现已在同一任务增量修复，后端 675、前端 193 项复验及类型/构建通过，真实浏览器核验已生成表单可见。**修复是 8773b8b7 + 未提交差异，尚未重新发布，用户验收未关闭，不进入 08。**

[本次完整复验与用户验证单](SCHEDULED_TASK_FORM_FIX_ACCEPTANCE.md) 包含接口、源文件指纹、修复前失败、回退及生产限制。旧工具 schema、授权快照和业务链保持；直接创建保留当前 user 原文并要求补全明确的店铺模板标记，旧 proposal 模式保持。没有第二套执行策略。

## 上次升级开发快照（下列未部署状态为当时记录）

用户在诊断暂停异常、调研和代码方案后明确授权开始开发。已完成原系统 A–D 增量升级，本地技术验证通过：后端 1963 passed/1 opt-in skip（该原测试体另在临时数据库执行通过）、前端 122 passed、TypeScript/构建通过。**本次升级未提交部署、未合并关闭，不进入 08。** [逐项验收及生产验证单](SCHEDULED_TASK_UPGRADE_ACCEPTANCE.md)、[实际接口/迁移/回退](TECH_定时任务增量升级边界.md#8-已实现的接口与运行机制)。

当前分支仍为 `codex/task/20260911215117-tool-unification-07`，HEAD `75fced912ce1ee30668b1a588043b26120ee1e8d`；被测版本是 HEAD + 本次未提交差异，[源码指纹及完整 diff](scheduled-task-upgrade-evidence/source-checks.json) 可追溯。75fced91 的历史部署未取得完整受控完成回执，不等于本次升级已部署；生产状态须在下一次授权发布时重新核验。

35 项工具、别名和 handler-only 清单继续完整，Spec 唯一维护元数据，ERP/文件/沙盒/媒体原语义保持。任务工具 effects 显式增加 task_definition，可信交互的直接管理由统一 Policy 事先判断并禁用缓存；其他模式/旧 helper 保持提案语义。这是明确授权的行为变化，不覆盖原 07 “仅迁移定义”的历史证据。保留 legacy 的原因仍是业务适配、兼容入口和投影，没有第二条权限执行链。

发布本版必须排空在途任务并停止所有旧 Scheduler/HTTP Worker，再执行 254/255 后启动新代码；不能混跑旧调度器。停止当前运行未开放。08 仍须等待本升级的确定候选完成生产验证及用户验收关闭。

## 原 07 交付快照（2026-09-11，以下 SHA/未提交状态为当时记录）

用户本次明确确认 01–06 已验收进入 main。07 经受控 start 从最新 origin/main `ca4c3d7e6a89ef34412cf405efc2d4fb0c1350e2` 创建；下表各关闭合并均为该基准祖先，且代码树与其验收候选一致。[07 源码证据](tool-unification-evidence/07-source-checks.json) 保存完整 SHA 和核验结果。

| 板块 | main 关闭合并 | 已验收候选 | 记录 |
|---|---|---|---|
| 01 | 2e8fdb2d | b4c854ac | [01 验收](TOOL_UNIFICATION_ACCEPTANCE_01.md) |
| 02 | 8e74f57d | 4084db4e | [02 验收](TOOL_UNIFICATION_ACCEPTANCE_02.md) |
| 03 | 0f65d72d | 2ed4d783 | [03 验收及修复历史](TOOL_UNIFICATION_ACCEPTANCE_03.md) |
| 04 | 6c0737ab | 1f288018 | [04 验收](TOOL_UNIFICATION_ACCEPTANCE_04.md) |
| 05 | cdba58f9 | 87b07718 | [05 验收](TOOL_UNIFICATION_ACCEPTANCE_05.md)、[最终附件修复](TOOL_UNIFICATION_05_ATTACHMENT_BINDING.md) |
| 06 | ca4c3d7e | 6e72665b | [06 验收](TOOL_UNIFICATION_ACCEPTANCE_06.md)、[v1 writer 上线](TOOL_UNIFICATION_06_WRITER_ROLLOUT.md) |
| 07 | 尚未提交部署/关闭 | 尚无候选 SHA | [07 逐项验收](TOOL_UNIFICATION_ACCEPTANCE_07.md) |

07：**技术验收通过，待提交部署及用户验收**。任务工作树 `/Users/wucong/EVERYDAYAIONE/worktrees/tool-unification-07`，分支 `codex/task/20260911215117-tool-unification-07`；当前 HEAD 为上述基准，**被测版本为 HEAD 加未提交差异**，精确 backend 文件指纹见源码证据，不用基准冒充已测试发布候选。

- [最终架构及接口](TOOL_UNIFICATION_ARCHITECTURE_07.md)、[35 项工具/别名/独立通道目录](TOOL_UNIFICATION_CATALOG_07.md) 是 01–07 当前运行链的代码权威；下文旧板块交付时的“当前/未关闭/不能启动下一块”等保持为历史快照。
- 定义按 ERP 22 → 文件/沙盒 5 → 媒体 3 → 任务 1 顺序迁移、分别 147/296/173/159 项验证通过，再收拢通用/爬虫/内部上下文 4。33 public + 2 handler-only 全部为显式 ToolSpec，未注册/重复/缺 handler 均为 0。没有增加工具名称或扩大内部可见性。
- `services/tools/catalog.py::build_tool_catalog` 为规范工厂；`build_legacy_catalog` 原导入/签名保留并委托。原 schema 工厂资源进入 `definitions/*_schemas.py`，其 config build/集合/validator 与 `chat_tools` 风险/并发/目录、`tool_domains` 均为兼容投影。Spec 新增 catalog_order/catalog_groups/core/legacy_plan_visible/schema_variants，只承载原目录和视图差异。
- Planner CapabilityRegistry 新增 from_specs，原 from_tool_schemas/from_names 从注册 Spec 派生；旧 capability/Planner/执行授权快照字段和版本保持。24 个原描述的执行模式对齐既有 Spec，预检不再把媒体/恢复/任务提案标成只读可用；风险/read/write 标签及实际运行授权保持。通用自定义 Planner API 仍可用，但不能注册运行工具或执行 Handler。
- `config.tool_registry` 的 domain 为语义选择分组，保留 tags/priority/synonyms 和原算法；它不是权限域。legacy 现在只剩原业务 Handler、展示/旧 schema/API/通道适配和无生产执行消费的描述 helper，没有另一套执行策略。
- 原无组织 ERP 可见性与业务返回、code_execute 缓存资格、restore_file safe/串行/不可缓存及原 replay 资格保持。ERP 引擎、文件目标/内核、媒体结算、任务提交流程和实际执行 Policy/Dispatcher/Runtime 均未改写。
- 最终 **2479 passed、0 failed/error/xfail、2 个既有字体环境 skipped**；334 个新增必需场景无跳过。完整契约冻结基准重新采集字节一致；144 组上下文对照、9 种独立进程导入、两用户同 call_id、旧确认 binding 和 3 入口 × 5 代表工具回归通过。失败/fixture 修正、精确命令、全日志和 A-07/G 表见 07 验收。
- 无数据库/持久化/WS 新变更；06 v1 writer/reader 和调用 ledger 源码逐字保留。回退到 `ca4c3d7e` 即保留 06 读写兼容，不需要 payload 回迁；不得回退到不支持 06 v1 reader 的旧版本。
- **08 代码前置已具备，但启动仍需本块用户指令提交部署 → 确定候选验证 → 用户指令验收关闭 → main 一致性核验。本任务不进入 08，未推送/部署/合并/清理。** 真实外部业务/生产浏览器/付费生成与用户观感按 07 验证单待完成。

## 以下为 06 及更早板块的交付时快照


> **2026-09-11 写入阶段更新**：兼容 reader 版本 `e097e392fd8229cffda363fc85113c31fb995179` 已先部署并完成用户回归；本次第二阶段默认写入切换为 **1**，显式设置 0 可停止新写入且继续读取 v1。当前有效发布顺序、验证和回退依据见 [06 写入阶段验收补充](TOOL_UNIFICATION_06_WRITER_ROLLOUT.md)。最终候选及关闭结果以受控发布/关闭交付消息为准。

以下为首次兼容读取阶段的验收和接口快照；其中“默认 0”“待部署”等时态不覆盖上述第二阶段更新。

## 当前有效状态：板块 06（2026-09-11）

用户明确确认 01–05 已验收进入 main。本任务受控 start 基准为 `cdba58f9018ff45be2ebde0b802471ca634d8727`；其 tree 与 05 最终 `87b07718` 相同，01–05 提交祖先关系已核验。[06 源检查](tool-unification-evidence/06-source-checks.json) 保存证据。下方 05 的“未关闭/不能进入 06”均为当时快照，不覆盖本段与用户最新确认。

板块 06：**技术验收通过，待提交部署及用户验收**。分支 `codex/task/20260911165104-tool-unification-06`，工作树 `/Users/wucong/EVERYDAYAIONE/worktrees/tool-unification-06`；被测版本为上述 HEAD 加当前未提交差异，尚无发布候选 SHA。

- [06 逐项验收](TOOL_UNIFICATION_ACCEPTANCE_06.md)：A-06-01～06、G-01～05 均有证据，最终 2035 passed、0 failed/error/xfail、2 个原字体环境 skipped；76 个新增必需场景无跳过。原隔离 PostgreSQL opt-in 测试没有运行数据库验证，不计为通过。
- [06 载荷、缓存、审计和回退契约](TOOL_UNIFICATION_PERSISTENCE_06.md)：新增 result_payload.encode_result/restore_result；AgentResult/FileRead/Form、FileRef/emit、error/retry、token/thinking 和必要审计事实有界保存。未知版本/坏载荷不回退为成功，超限拒绝存储；runtime metadata 的数据库/锁/异常对象不会 stringify。
- `serialize_tool_result(ToolResult)` 按 `TOOL_RESULT_PAYLOAD_WRITE_VERSION=0/1` 写入；**默认 0，先部署所有兼容 reader，再受控启用 1**。reader 始终支持旧格式及 v1；回设 writer=0 仍可读已有 v1。没有数据库迁移，不清空历史记录。
- 回退边界：旧版本 `cdba58f9018ff45be2ebde0b802471ca634d8727` 的原 reader 已实际读取新载荷外壳，但会丢失结构化 FileRef/图片/表单、retry/token/thinking；只算有损降级读取。完整恢复要求保留本块 R1 兼容 reader（当前源码指纹可查，正式候选 SHA 在提交部署时记录），不能宣布启用 v1 后可直接无损回退旧版本。
- Actor runtime/lifecycle 在当前授权和资源包含检查后恢复，succeeded 仍表示调用完成，业务 error 可回放；running/uncertain 禁止重做。取消继续传播，策略拒绝不创建 uncertain。原 RPC、状态枚举、lease、资格、业务锁和 Handler 保留。
- 生产缓存改为有界快照，增加现有身份/作用域 key；命中保留真实业务状态。`ToolResult.chargeable_tokens` 在缓存/回放为 0，原 token/thinking 不丢失；未修改 code_execute 缓存资格、restore_file 风险/回放或目录定义。
- 复用 ToolAuditEntry/record_tool_audit；原表字段不变。execution 事实由同一 writer 写现有结构化日志，含 cached/replayed/status/attempts/original_tokens/chargeable_tokens。数据库没有新增 replay 列，需关联日志查看；不承诺 best-effort 写入零丢失。ToolLoop 同轮模型 token 只归属一条审计，投递异常时补交本批尚未审计的已完成结果，不重做 Handler。
- 下一块 07 的代码前置已具备；仍需用户指令提交部署 → 按兼容发布次序核验指定会话 → 用户明确验收关闭 → 核验 main。**本任务不进入 07，未推送/部署/合并/清理。**

## 以下为板块 05 及更早交付时快照

> 2026-09-11 最新生产复验：1df0e01c 的工作区插入与重新上传均出现模型未识别当前附件。已准备当前 user 消息绑定附件的候选，774 项回归及 6 次真实模型合成对照通过，首次部署被 5 项旧字符串断言拦截，修正后等待重新完整发布，原生产场景待复验；05 不得标记技术通过或进入 06。见 [附件绑定调查与增量验收](TOOL_UNIFICATION_05_ATTACHMENT_BINDING.md)。以下保留此前实施记录。

## 当前状态：板块 05

最新恢复阶段（2026-09-11）：用户已授权恢复上下文与状态逻辑，已完成 `history_outcomes` 独立事实投影、缓存 key 隔离、`ToolLoopContext.update_from_batch` 逐调用结果、唯一动态摘要及三条压缩路径的事实保留。相关 **986 passed、3 既有 skipped、0 failed**；原代码 44 项失败，当前全部通过。第 1、2 项继续保留。Actor 输入/lease、原工具结果投影、WS/delivery、旧 ledger/checkpoint 格式不变；未提交部署。[最新技术设计与逐项验收](TECH_05_上下文结果恢复.md) 为当前交接权威。真实模型目标误用及用户生产观感仍未验证，**整块技术验收未通过，06 前置未满足**。下方“上下文只调查”是此前阶段快照。

最新进展（2026-09-11）：用户授权第 1、2 项修复，已完成 253 删除外键迁移与附件/搜索/工具描述的共享文件调用契约，608 项相关自动化通过。上下文与结果状态仅调查：官方 Grok Build 保留独立工具结果；本项目 7 月历史正文压缩与 8 月表单终止叠加后丢失无正文回复。已撤回未验证的位置/目标提示补丁；没有改 Actor、历史投影、失败摘要或 ledger 格式。**05 整块技术验收未通过，目标误用与用户生产复验仍未关闭，不能启动 06。** 详见 [本轮实施、A/G 验收与 Actor 对照](TOOL_UNIFICATION_05_ITEMS12_AND_ACTOR_COMPARISON.md)。原 731 项是旧候选证据，不累计进本轮。以下首次实现记录仍保留供追溯。

2026-09-10 最新状态：05 的 `887b28ae` 已完整部署，用户生产验收发现任务删除失败、附件调用参数无效、读取请求扩展成历史导出任务。本地修复候选 **731 项通过、3 项既有旧流程跳过，但本轮验收尚未通过，待重新提交部署及用户复验**。未修改生产数据、未合并或清理，不启动 06。详见 [生产修复与逐项复验](TOOL_UNIFICATION_05_PRODUCTION_FIXES.md)。

- 验收修复新增 253 迁移：旧创建草稿的 confirmed_task_id 与既有 source_task_id 一样随任务级联清理；独立 ChangeSet 审计/回执保留。重新发布需要执行该迁移。它不改变 ToolResult ledger/checkpoint 格式，不属于板块 06。
- 当前附件/搜索分别提供只含合法 file_id/resource_ref 的可复制读取调用，工具描述共享同一说明；附件摘要位置与线上 HEAD 一致。保留非法引用拒绝及原模型工具投影。Actor 输入锚点、历史 revision 和 Provider 透传已核对正常，模型后续目标选择仍待复验。
- 以下基准、初次源指纹和原技术记录为首次实现证据；本轮修复指纹、命令与验证另见生产修复记录。

- 用户明确确认 01–04 已验收进入 main；本任务受控 start 基准为最新 origin/main `6c0737ab78f2d0cb3b2a8376498e7825b0829431`。其与 04 最终 `1f288018` 的 tree 都为 `5196ad3351ebaefa8e78a5c0215211018a2174db`，前置已核验。
- 分支 `codex/task/20260910221341-tool-unification-05`；工作树 `/Users/wucong/EVERYDAYAIONE/worktrees/tool-unification-05`。被测版本是当前 HEAD 加未提交差异，具体指纹见 [05 源检查](tool-unification-evidence/05-source-checks.json)。
- [05 逐项验收记录](TOOL_UNIFICATION_ACCEPTANCE_05.md) 包含 A-05/G 矩阵、模型/前端/审计/持久化四边界、失败复验、真实环境限制及用户验证单。
- 最终 1184 passed、0 failed/error/xfail、2 项既有 Linux 文泉驿字体环境测试 skipped（同 main 复现，未计为通过）；A-05 必需项均无跳过。新增 72 项真实消费集成，包含 18 份未修改 main 生成的 WS/delivery/checkpoint 全字段对照。
- 实时 Chat 与 ToolLoop 已直接消费 ToolResult；ledger/checkpoint 继续旧兼容投影，旧 reader 未改。不把旧载荷中原来就缺失的 tokens/thinking/metadata 或 FileRead/Form 字符串恢复声称为无损；新版回放由 06 负责。

以下 01–04 段落是对应板块关闭前的历史记录；其中“当前”“待部署”“不能启动 05”不覆盖以上最新 main 核验和本次状态。05 实际接口及 06 前置见本文末尾。

## 板块 04 历史交接（关闭前快照）


最新状态：资源范围衔接根因修复已完成，2960 passed、2 项既有真实模型 opt-in 测试未验证，源码入口/兼容检查通过。**技术验收通过，待提交部署及用户验收**；本轮没有新候选 SHA，也未部署。最后已部署版本为 bb4449a0；下方历史发布状态不代表本轮状态。

更新日期：2026-09-10。当前板块 04：用户在 bb4449a0 发现工作区搜索到分析的 scope 衔接失败后，本任务完成 [诊断](FILE_WORKSPACE_SCOPE_DIAGNOSIS_20260910.md)、[设计及实际接口](TECH_工作区资源选择与授权衔接.md) 和 [逐项复验](TOOL_UNIFICATION_ACCEPTANCE_04.md)。真实模型/生产用户复验未完成；分支和工作树保留，不进入 05。

### 本轮接入增量与恢复约束

- `tools/resource_access.py`：`ResourceRule`/`ResourceAccessBoundary` 是可信内部动作、资源及有效期上界；身份仍由 `ToolContext` 持有。普通 interactive 保留现有工作区资格，scheduled/preflight 仅从已有精确资源清单产生 list/read；无清单为未知，不能用 allowed_tools 替代。`scheduled_task_agent.py::_template_manifest` 只适配既有模板文件字段，不读取计划文本作为授权。
- `ResourceSelections` 是定位线索集合，运行时按 actor/owner/org/任务/domain 隔离，调用首次 await 前取快照；不是 last_scope。签名引用、确定清单目标、唯一浏览范围可以定位，显式 current 不被覆盖。搜索结果不扩写 manifest。
- `file_calls.py::PreparedFileCall` 保留原目标/版本/确认链，增加准备阶段的 browse_directory；原参数不被执行后重选。`FileTargetResolver` 与实际文件查询共享动作过滤；`manifest_matches` 使前置检查与 Handler 对“部分名称/目录/单图”的选择一致，list 权限不隐式读图片。
- 生产接入点：Chat/Actor 的 `chat/execution_engine.py` → `chat_tool_mixin.py`；定时 `ScheduledTaskAgent` → `ToolLoopExecutor`；兼容 `ToolExecutor.execute`。全部进入 `ToolRuntime` 和同一 Registry/Policy/Dispatcher，无旧执行/恢复宽松兜底。核心/动态目录仍使用 Registry。
- Actor 仅用同 task 的已完成、显式 scope 的既有 file_search 输入恢复浏览线索，不读展示文本、不重新搜索或执行业务。旧 checkpoint 未记录隐式调用的规范 scope，不能猜其并行完成顺序；此时用已签名引用或明确 scope 定位并重新鉴权。
- 确认包含资源授权依据；等待结束后刷新权限/有效期及目标版本，先于缓存、invocation 与 Handler。未知授权立即终止工具循环；同类资源错误在无新可信事实下再次出现也终止，保留原失败结果统计。取消继续抛出。
- `ToolError.retry_context` 复用现有字段携带恢复动作/有效范围；正常 ToolResult 投影、WS、ledger serializer/hash、ERP、Actor lease/安全点、业务锁无新格式或协议。

测试命令、基线四项失败/修复后成功、56 项新增场景及 A/G 完整矩阵见最新验收记录。回退无数据迁移：受控发布到 bb4449a0 可用旧 reader，但会恢复已知 scope 缺陷。长引用/分页/截断数量解释留在 05/06；本轮不承诺已解决。下一块必须等待新候选用户验证及受控验收关闭。

用户已授权 [文件目标解析与确认闭环实施方案](TECH_文件目标解析与确认闭环.md) 的完整范围：统一名字/路径/fid/fref 的当前范围解析、绑定确认对象版本、恢复记录与 no-clobber、全内容缓存与解析配置、任务短 ID 歧义、必要 writer/取消协调。详见 [最新 A/G 验收记录](TOOL_UNIFICATION_ACCEPTANCE_04.md)、[本轮源码/入口检查](tool-unification-evidence/04-rootfix-source-checks.txt)、[测试命令](tool-unification-evidence/run-04-rootfix.sh)。未获生产业务写入授权，未操作生产业务文件。

当前新增接口：

- `services/file_resources.py`：FileTargetResolver、FileReferenceCodec、FileTargetError、content_digest、source_snapshot。签名引用仅定位，不授权；旧 fid 在当前 owner 范围唯一反查，碰撞/不完整集合拒绝；明确路径不模糊回退。
- `services/tools/file_calls.py`：resolve_file_call、resolve_restore_record、PreparedFileCall.prepare/verify/activate。回放前只核验身份/权限；新执行先完整准备再确认，最终锁内验证后才访问缓存/invocation。Handler 消费已准备对象，不再重新猜选目标。
- `ToolContext.resource_versions`：冻结准备版本；Policy ConfirmationBinding 摘要和内部文件缓存键包含它。未写新 ledger、WS 或 ToolResult 持久化字段。
- `services/workspace_coordination.py`：workspace_lock（共享/排他）、workspace_lock_sync、finish_file_io/drain_file_task、receive_workspace_upload。当前单生产主机多 worker 契约；NFS local_lock=all 不支持跨主机排他。新增 writer 需参与同一 owner 协调，不能绕过。
- `file_search_entries` 的结构化 hits 是搜索引用来源；file_analyze 增可选 resource_ref、file_delete 增可选 resource_refs，restore_file 增可选 record_id。旧输入名与兼容返回不变；批量存在歧义/缺失整批拒绝，恢复不覆盖。这些变化已获用户批准。
- 分析数据缓存 v3.1 以全文件 SHA256 和解析格式/参数标识；转换读取稳定快照。同内容缓存不随调用者重写元数据，当前源路径仅绑定本次视图。没有改 code_execute 缓存资格或原 ToolResult replay。

已关闭的必需项：生产 NAS“不覆盖发布”独立临时探针已通过并清理，见 [NAS 验证记录及脚本](tool-unification-evidence/04-rootfix-nas.md)。旧恢复记录只有可变 OSS key；本轮固定准备时 ETag，不能保证历史备份未被覆盖。当前源版本删除保证依赖参与协调的同机应用写入，不承诺未受控外部或多主机写入下原子版本删除。

下方原 01–03 及 04 发布前版本状态是历史快照；当前版本/结论以上文和最新验收记录为准。

- 板块 01–03 前置已核验：用户明确确认已验收进入 main。最新 origin/main/本任务基准 `0f65d72dd00a0fce6885d4df0b7977454f666812` 与 03 最终候选（含附件/文件边界修复）`2ed4d783` 的 tree 均为 `c38a4f18c3ec0ae8193af3c7b9750877df6ae933`；01 `b4c854ac`、02 `4084db4e`、03 `e243ba2c` 和 `2ed4d783` 均是祖先。不是从其他任务复制未提交文件。
- 当前分支：`codex/task/20260909225830-tool-unification-04`
- 当前工作树：`/Users/wucong/EVERYDAYAIONE/worktrees/tool-unification-04`
- 原 04 基座：`0f65d72dd00a0fce6885d4df0b7977454f666812`；当前 HEAD/已部署旧候选：`20636929f7b78346991b357630240d932ed5772d`。本轮被测源码为 HEAD 加未提交差异，不能把它冒充已发布候选。
- 当前：[板块 04 验收记录](TOOL_UNIFICATION_ACCEPTANCE_04.md) / [逐文件指纹与调用点](tool-unification-evidence/04-source-checks.txt) / [最终测试命令](tool-unification-evidence/run-04.sh)。实际接口和后续前置见本文末尾“板块 04 实际接入”。
- 历史：[板块 03 验收记录](TOOL_UNIFICATION_ACCEPTANCE_03.md) / [执行与结果接口](TOOL_UNIFICATION_EXECUTION_03.md) / [板块 02 验收记录](TOOL_UNIFICATION_ACCEPTANCE_02.md) / [Policy 接口](TOOL_UNIFICATION_POLICY_02.md) / [板块 01 验收记录](TOOL_UNIFICATION_ACCEPTANCE_01.md) / [目录与代表契约](TOOL_UNIFICATION_CATALOG_01.md)。下方 01–03 的“尚未接生产/未部署”等描述均为各自交付时的快照，不替代这里的当前状态。

## 总体目标与顺序

渐进建立 ToolSpec → ToolRegistry → ToolPolicy → ToolDispatcher → ToolHandler → ToolResult，统一公共工具调用外层，不重写业务引擎。编号顺序固定：

1. 工具定义和注册表（已进入 main）。
2. Policy：模式、业务权限快照、参数级风险、确认与分批（已进入 main）。
3. Dispatcher / Legacy Handler / ToolResult 基础（已进入 main，包含后续文件身份与边界修复）。
4. Chat、scheduled ToolLoop、旧 execute 实际入口完整切换（当前本块，技术通过、待部署/用户验收）。
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

## 板块 03 附件修复历史（交付时快照）

以下原文保留历史问题、测试和发布过程；其中“未关闭/不能启动 04”的状态已由本文顶部的最新 main 核验更新，当前下一板块为 05。

### 2026-09-09 用户验证追加：工作区附件 ID 修复

用户继续询问其他工具逻辑后，路径模糊匹配回归已修正，身份测试现为 22 项；旧删除入口、stdout 和同类 staging 登记边界也已按用户授权修复。新增 41 个边界/兼容用例，保留原 OSS 副本/恢复记录、实际 Parquet/JSON 读取与旧返回对象。最新主组 1481 + 独立 ERP 11 = **1492 passed、4 原有 xfailed**。见 [边界修复与兼容验收](FILE_TOOL_BOUNDARY_REPAIR.md)，原发现快照见 [其他工具核验](OTHER_TOOL_LOGIC_AUDIT.md)。当前仍未提交部署或验收关闭，不能直接关闭任务。

首次板块 03 候选 `e243ba2c0d545d8afca3ae930a40389276b7c123` 已成功提交部署，保留本工作树、未合并。用户反馈“插入后读取文件失败”，并明确授权同工作树修复；本增量属于该 Bug 修复，不接入板块 04。

- 修复 `FilePathCache.register`：相同 workspace 路径才合并，每次补全键，完整相对路径与名字别名分离，唯一匹配及整体淘汰；`registered_paths()` 为 ID 解析提供精确键接口。
- `resolve_fid_to_workspace` 使用上述接口，多源哈希冲突返回 None；`compute_fid` 和 schema 不变。分析以绝对源路径写回状态；附件以 workspace_path 读取状态，避免不同目录同名文件串用。
- 新增 21 个用例：同一测试在旧候选全部失败、修复后全部通过。最终相关回归 **1111 passed、0 failed/error/skipped、4 原有 xfailed**；含原工具统一全组及文件、图片、上下文、旧 ToolExecutor。基准对照确认原 xfail，不隐去或放宽断言。
- [方案与验收记录](FILE_CACHE_ID_REPAIR.md) 包含 G-01～05、精确命令、日志、范围指纹、生产测试步骤及回退说明。当前代码仅本地修复、未提交部署；原先“生产源码无差异”仅适用于首次工具统一 03 增量。

板块 04 的代码前置已具备：Spec/Registry/Policy、仅允许分发、原业务 Handler 复用及 ToolResult/旧兼容出口已在隔离环境贯通。后续必须完整接入可信 Context/Executor 身份配对、参数/资源解析、确认、分批、缓存/回放之前的权限边界和旧 execute 门面；不能先执行再补策略。实际接入契约见 03 接口文档第 4 节。

流程前置仍缺：板块 03 用户明确提交部署确定候选 → 用户核对调用计数/结果对照及原有只读行为 → 用户明确验收关闭 → 受控入口确认 main 包含本块。**本任务止于板块 03；用户验收关闭前不能启动板块 04。** 最终候选 SHA 在实际提交部署后记录，并核对被测代码指纹。

## 板块 04 实际接入

### 入口与模块

| 实际文件 / 接口 | 当前责任与调用方 |
|---|---|
| tools/runtime.py：ToolRuntime.context / advertised / batches / execute；run_parallel | 每个 ToolExecutor 绑定一个 Registry、Policy、ToolExecutionService、原 Legacy Handler 集。执行前刷新权限/资源，等待既有确认，最终 allow 后才进入缓存/ledger/Handler；并发读失败取消同批未完成任务 |
| tools/runtime_context.py：chat_context / executor_context / refresh_context | 装配服务端 mode/domain、actor/workspace owner、org/task/conversation、个人/群边界、feature flags、授权快照/名称上界、资源清单、预算/取消。当前 organizations/org_members 与会话归属按现有身份规则读取；已有声明权限调用 PermissionChecker，不发明 ERP action RBAC |
| 同模块：resolve_resources / check_deferred_resources / check_result_resources | 复用 FileExecutor 路径保护；不 mkdir、不执行业务。文件名/ID 解析稳定目标，Actor 恢复可由当前 manifest 解析 fid；restore 目的地及缓存/旧 replay 显式 workspace 产物必须在当前 owner 根目录内 |
| ToolExecutor.tool_runtime / execute | 原 re-export 和 execute(name,args) 保留，可选 keyword-only call_id；旧 execute 必须经过 runtime，再 to_legacy。业务集合/旧投影保留；本轮文件业务消费统一准备目标；无直接执行兜底 |
| ChatToolMixin._execute_tool_calls / _execute_single_tool | 由共享 Chat execution_engine 调用，显式传本轮 mode/domain/budget/cancel，携带已解析 ExecutionScope、resource_manifest/loader。Policy 分批；同请求跨模型轮次保留服务及一次性预占，结束清理 |
| ChatToolMixin._confirm_tool_call / _wait_for_tool_confirmation | 复用原 WS UI 和 Actor 持久命令；原 tool_call_id 字段承载完整 ConfirmationBinding 摘要，含参数、作用域、资源版本和 Spec。先检查可恢复批准；等待失败/超时/断连拒绝，重新读取当前事实后才放行 |
| chat/tool_lifecycle.py：ActorToolLifecycle.replay / begin / complete | 授权/资源校验后才只读 lookup；名称/参数 hash 匹配后旧 payload 回放，0 Handler。执行前保留原 mark_stale/begin/fencing，业务后用原 serializer complete。业务 error 是调用 succeeded；业务异常才 uncertain；投递失败不改写完成状态或重试业务 |
| tool_invocation_store.DatabaseToolInvocationStore.lookup | 旧表按 task/conversation/turn/tool_call 查询 tool_name,args_hash,status,result；只读。原 begin/complete/mark_stale RPC、serializer/deserializer 未变，没有新 payload |
| ToolLoopExecutor.run / _execute_tools；invoke_tool_with_cache | 当前生产创建者仅 ScheduledTaskAgent。保持模型返回顺序，Policy 连续读合批/写屏障；缓存读取移入最终策略检查之后，旧结果/audit 消费保留。原确认 helper 无生产调用且失败不放行 |
| ScheduledTaskAgent.execute；scheduled_task_workflow | DB task 的执行策略先校验 version/名单；显式 scheduled/preflight 与 auto/general、actor=owner、预算/取消、授权快照。当前身份和任务要求权限在模板复制前再核验。核心/预检/动态展示由 Registry；定时名字授权不升级成危险 action 授权 |
| Chat stream_setup._prepare_permission_and_tools；chat.tool_loop.prepare_tool_turn | 核心与动态发现统一 Registry.resolve，允许集合与展示选择分离。PreparedChatStream.execution_context 供每轮上下文传递；兼容 helper 无执行授权能力 |
| api/routes/ws.py 确认响应 | approved 只接受字面 bool True；字段/builder/前端和 content blocks 原样 |

Web 的 run_legacy_chat_stream 与 Actor 的 ChatGenerationExecutor.execute 共用 execute_chat/_run_loop；ERPAgent 仍是计划提取→部门 Agent→查询引擎，不改成 ToolLoop。全部生产构造点、执行点与已消除旁路见 [04 验收入口表](TOOL_UNIFICATION_ACCEPTANCE_04.md#生产入口与已消除旁路) 和源检查日志。

### 运行与兼容契约

- 生产调用者从已认证的服务端事实创建 executor，不从模型 JSON 赋值 actor/owner/org。ToolExecutor 新增 permission_mode、agent_domain、task_id、context_scope、personal_context_allowed、execution_scope/channel_scope_id、tool_entrypoint、tool_confirmer、resource_manifest_loader；原参数与默认门面保留。旧调用缺可信身份会明确拒绝，不回退业务 Handler。
- 执行顺序是静态 Policy/action → 当前身份/声明权限/资源与目标检查 → 可用旧 replay → 文件版本准备 → 必需真实确认 → 当前事实/绑定再核验 → 工作区协调 → 统一服务 allow/预占/版本复核 → 旧缓存/Actor begin → Dispatcher/原 Handler → to_legacy/原 ledger 与消费者。拒绝路径没有缓存数据、业务或新 invocation，绝不登记成 uncertain。
- 原资源消耗 CONFIRM 只是通知；manage_scheduled_task 的提案/表单沿用原提交机制。auto/ask 的危险操作均要确认，plan 继续阻止写/生成；query function 的写 action 不能通过确认升级为写入口。
- 一次性 key 属于当前请求；Chat 跨轮复用服务，Actor 重启由原 ledger 保证不能重做已完成业务。已完成回放仍检查当前成员/资源和旧行 args_hash；running/uncertain 或哈希不一致明确拒绝。旧未绑定批准不能作为新批准，不改写旧行或 payload。
- ToolResult 仍是 03 的内存信封。原 Chat/ToolLoop 两种投影、FileReadResult 图片、表单终止、交互 ERP TABLE 与定时 TABLE、emit/audit 字段继续交原消费者。结果 writer/reader、WS content blocks 和模型循环/Actor lease/安全点/业务锁没有改造。
- cache.put 或完成结果投递故障不重试业务；完成写失败保留原 running/uncertain 恢复保护。取消继续传播，不增加统一自动重试；不宣称故障情况下审计零丢失。
- 保留 code_execute 原缓存资格、restore_file 原 safe 风险/原 invocation 资格。scheduled 无新的资源附件清单来源，显式 resource_manifest=None，继续使用任务 owner 的工作区/模板边界；不冒充已有逐资源危险写授权。

### 验证、限制与回退

[04 验收记录](TOOL_UNIFICATION_ACCEPTANCE_04.md) 按 A-04-01～06、G-01～05 提供逐项证据，技术均通过。最终 **858 核心/集成 + 1613 相关旧回归 + 11 独立 ERP = 2482 passed，0 failed/error/skipped/xfail**；新增 I 为 118 个场景。真实 Event/轨迹证明读 A/B 重叠、C/E 独占及 D 位于 C 后；确认、撤权、群隔离、定时范围、取消和 invocation 拒绝顺序均通过。执行命令、日志、变更指纹、旧目标冲突断言依据及复验记录已保存。

未连接真实业务服务/执行删除或付费生成，未提交/推送/部署/合并；用户生产验收为待完成。确认界面由真实服务端 WS 等待器与原 builder 在测试中贯通，真实浏览器/数据库部署验证仍需按验收单记录候选版本与用户结果。

回退基准为 `0f65d72dd00a0fce6885d4df0b7977454f666812`：撤销本任务 04 增量，保留 01–03 及附件/文件边界修复。原序列化/reader/payload 未变，无数据迁移或新协议回迁；跨版本批准/参数哈希不匹配时拒绝，不能借回退重做 uncertain 业务。

## 下一板块前置（05）

04 本轮代码与必需 NAS 发布实测已通过；用户已明确“提交部署”，受控发布仍需确定候选 SHA → 在获准资源完成只读、plan 拒写、危险拒绝/批准及定时范围验证 → 用户明确“清理工作树” → 受控关闭确认 main 包含相同代码树。没有真实写入授权时仅验证无副作用步骤，批准执行项保留未验证。**用户验收关闭后才允许开始 05；本任务不继续结果展示或 replay 格式改造。**


## 板块 05 实际增量与后续前置

| 文件/实际接口 | 责任与边界 |
|---|---|
| `tools/result.py` | `model_content(chat/tool_loop)` 分别调用两种原 AgentResult 投影；FileRead.text/图片 blocks、form.llm_hint/terminal_form、display/agent_context/metadata 保留。`with_model_content` 仅覆盖指定模型投影及分流标志；`collect_payloads` 复用原 emit 转换并按 Chat ERP/定时策略收集；`audit_fields` 保留原状态并读取实际 cached；`legacy_persistence_value` 为明确旧 writer 边界 |
| `handlers/chat_tool_mixin.py` / `chat_tool_result_mixin.py` | runtime 返回信封不再 to_legacy；统一提取状态/展示/审计，产物和原 token 累加来自 ToolResult。旧 raw 分类 helper 保持兼容。审计先于展示；展示异常不触发第二次业务或审计 |
| `handlers/chat_generate_mixin.py` / `chat/tool_loop.py` | model_content(chat) 回填；FileRead 图片通过原 append_tool_images 注入，旧类型入口仍受支持 |
| `agent/tool_loop_helpers.py` / `tool_loop_executor.py` | 生产采用 `invoke_tool_result_with_cache`；原 `invoke_tool_with_cache` API 保留旧返回。统一结果贯穿模型、artifact、hook 和停止分类；原 AgentResult.validate、to_tool_content、普通文本 staging 预算保留。steer 仍按原模型协议反馈跳过，但收齐已完成结果的产物和审计 |
| `agent/loop_hooks.py` / `stop_policy.py` | 原审计 writer 从 ToolResult 取真实状态/长度/cache/分流；其他 hook 提取原 display 文本。统一状态优先于旧 audit_status，保留业务 retryable=False；uncertain/cancelled 不走普通重试 |
| `tools/execution.py` / `chat/tool_lifecycle.py` | 取消仍抛出；取消异常上的内存执行状态仅供 wait_for 超时分类。`UncertainToolInvocationError` 标识原 running/in_progress/uncertain 副作用状态。lifecycle.complete 显式投影旧持久化值，无新 invocation 格式/资格/RPC |
| `chat/execution_engine.py` / `tool_invocation_store.py` | Chat 收到 uncertain 结束后续工具轮次并说明需核验；原表单终止保留。checkpoint 消费原模型消息/blocks；serializer/checkpoint 误收 ToolResult 时明确抛错，禁止 default=str 掩盖错误 |

模型和前端均不认识新的持久化信封。原 AgentResult/ToolOutput、schemas、emit payload builder、WebSocket/Actor sink、ToolRuntime/Registry/Policy、沙盒/ERP/媒体业务代码保持原样；原 `generate_video` Handler 同步等待结果并返回 summary URL，本块未扩展视频 block 或异步协议。完整边界对应与精确命令见 [05 验收记录](TOOL_UNIFICATION_ACCEPTANCE_05.md)。

旧 ledger 继续写 agent_result/scalar/json；保存内容与原 writer 相同，旧 reader AST 完全相同。原 AgentResult ledger 仍只恢复文本摘要、原业务状态/错误、emit；旧 FileRead/Form ledger 仍只有旧对象字符串。05 实时不丢字段，不能据此宣称旧 replay 也已无损。code_execute 缓存资格、restore_file 的资格/回放、Actor lease、可靠审计基础设施未扩展。

回退到 `6c0737ab` 无需载荷迁移；实际落盘/恢复对照及 writer/reader AST 证明旧读兼容。真实浏览器文件点击、表单提交、获准媒体样本/生产 ERP 及用户观感未完成，按验收单记录确定部署版本。

06 的代码前置：实时结果已统一且所有落盘边界明确；可据此独立设计新版本 replay/cache/audit 载荷、旧格式读取及跨版本回退。流程前置：用户“提交部署”确定候选 → 用户按 05 验证单验收 → 用户“清理工作树” → 受控关闭核验 main。完成之前不得启动 06。
