# Agent 通用任务编排：开发记录

## 当前确认范围（2026-10-31，372接续）

用户已明确：保留原ERP Agent，暂停新增ERP写操作适配，继续其它升级。原erp_agent入口、专业规划/部门Agent、查询和追问上下文、权限/预算/工作区/原结果协议保持；86项新增写入终态契约从本次实施及放行范围中移出，不再继续逐项扩展。已开发的ERP恢复和防重复代码/账本保留，冻结不等于已经验收或删除旧效果。原ERP功能兼容仍属本次回归范围；默认新任务总开关仍false，未作生产配置变更。

本次不新增共享群聊的进度读取权限。当前媒体/异步协议仅支持个人作用域，因此群聊新任务继续原Actor/Skill流程；个人新任务才可进入新版编排。已有_task_bootstrap/_task_execution优先按原恢复协议处理，不被新的准入条件删除或降级。新准入边界不影响原群聊功能，不表示群聊新版媒体编排已适配。

发布前仍需完成：非ERP范围最终调用链/迁移与在途恢复审查、原两个Skill在原素材上的真实供应商与最终页面验收、确定最终候选后的受控发布检查。复用已有效的本地与页面证据，不重复生成已有图片；付费前先给预计费用并取得用户确认，整套本次范围验证通过后才部署。


日期：2026-10-30。状态：基础状态/检查点、主循环控制、方法交接、canonical图片原子调用与自动续接已实现并做定向验证，整体仍在开发；未提交、未部署。不得用本文的局部测试宣布自主任务编排已交付。

设计：[整体方案](TECH_Agent通用任务编排与执行闭环.md)。安全要求：[验收与审查记录](VERIFY_Agent升级安全与功能保留.md)。工作分支沿用 `codex/task/20261029174537-skill-coordination-design`，未改主工作树。

## 已实现

- `services/task_execution/contracts.py`：逻辑任务、可信来源要求、工作计划/DAG、阶段 gate、成果、执行身份、operation 和 continuation 输入。模型工作计划不包含要求/权限/validator 写字段。
- `reducer.py`：步骤依赖就绪、完整要求与多步单项贡献分别验证、真实成果完成、完成检查；已有成果/操作仅锁相关步骤，允许修订尚未执行的步骤。
- 稳定副作用身份与状态：新 call_id 不创造第二派发权，未解决旧效果不能靠增加 version 重发；增加版本需可信 requirements_revision，普通 checkpoint 不得修改该事实。这里是状态归约，实际业务派发适配尚未接入。
- `controller.py`：版本校验、计划更新、文本成果保存、步骤完成和原子提交后 adopt。纯文本保存不能伪装文件或图片；模型不能给自己填写领域验证结论。
- `model_bridge.py` 与现有聊天主循环：已有可信 TaskState 时提供控制 schema 和任务事实；每批一个控制操作，混合业务调用配对返回屏障结果而不派发。状态、assistant/tool 配对及文本成果同检查点保存后才交付。没有新状态的旧任务不增加控制工具。新任务初始化入口尚未启用，因此生产默认行为未改变。
- 主循环按将要保存的 candidate 生成同版本事实，保存累计模型轮次后才采样；无工具输出时核对真实成果，有限纠正，耗尽为 partial 并向用户交付待完成项。收敛时将逻辑终态与 commit_ready 结果同事务保存；中间终态恢复也不重新采样。快知识路线暂保留原受限工具和引用验证，一次答复未有可信成果时如实 partial，尚未完成知识成果 adapter。
- 控制批次/完成检查透传原防重复调用 guard。Runtime 以确认保存序号区分未保存与保存后 pause/cancel，后者保留已保存配对及状态，包括无状态变化的错误控制。
- `continuation_context.py`：独立续接上下文投影，检查任务/revision/分支/身份/当前作用域；保留原始目标并通过现有严格闭合历史加载器读取，内部事件不伪造成 user 消息。图片消费者现已接入（见343增量）；完整跨入口资源/摘要和用户修订兼容仍不能由该基础模块代替验收。
- `store.py` 与迁移 340：新状态与现有 Actor checkpoint 同事务写入，校验 fencing、租约、作用域、CAS 和不可变来源。数据库延期一致性约束覆盖旧 RPC 的防降级/旁路写入，拒绝留下不一致的逻辑状态和 checkpoint。
- `ConversationTurnRuntime`、`DatabaseReplayCheckpointStore`、`ChatGenerationExecutor`：新状态存在时走新事务并在恢复时核对真实 ExecutionScope；无新字段时原路径不变；保存确认丢失只在完整 checkpoint 相同后认可。
- 迁移/回滚按现有 checkpoint 表的实际受控 owner 执行触发器 DDL，验证 everydayai 与 everydayai_owner 两种情况。已有新逻辑状态时拒绝直接回滚撤除支持。
- 从原能力目录生成 [46项基线库存](AGENT_CAPABILITY_INVENTORY.json)，全部暂标 legacy_only，未冒充业务 adapter 已验证。原目录、handler、权限和模式未被替换。

## 本轮新增：真实输入初始化与方法阶段

- `bootstrap.py` 由 Actor 已校验的真实输入/ContextAnchor/ExecutionScope 创建；模型提案不能写 actor、source、validator、gate。保留原始目标，模型计划不能修改已保存要求。`agent_task_execution_enabled` 默认 false，只控制新任务准入；旧 checkpoint 不升级，已保存 bootstrap 在关闭开关后仍可恢复并完整核对来源。
- `start_task_execution` 与业务调用是批次屏障；重复初始化不覆盖要求，保存失败恢复未初始化状态。初始化轮次采用可回放 model_round，不能因重启后的尝试预算归零而丢失累计调用。
- 首次提取的 action/适用方法是模型语义提案，不是权限或豁免。原始目标保留，初次提取仍需真实样本评估，不能宣称结构化字段能理解所有语义。
- `methods.py` 以原两个已审阅发布版本的精确 package/revision/body hash 登记阶段语义；未知版本不自动解释成阶段限制。初始化根据真实已激活方法与图片交付补入宿主规划要求；模型漏填动作/方法 ID 不能豁免已采用方法的前置要求。不相关文件/解释任务不强加图片规划。
- 规划文档保留参考判断、商品分析、整套视觉定位、页面衔接，以及逐图布局、完整提示词、负面提示词和比例。结构验证排除空白、无效比例及仅空白差异凑数量；这仍是结构/数量证据，不冒充语义或视觉质量保证。完整内容以中文章节展示，canonical JSON 和摘要保留在成果记录。
- 新任务初始化前/后，工具披露和真实派发都根据 Registry 的实际图片 capability 检查前置要求。不能通过省略 start、忽略广告、给计划步骤换 capability 标签来直接生图；被拒调用不改变当前 Skill 动作。
- 参考图提示词阶段有真实成果且完成步骤后才退出当前参与；manual/session 身份保留，其他方法和宿主上限继续生效。完整融合方法保留后续生图职责，不提前退出。阶段退出与 TaskState、composition、成对回执同事务保存；失败回滚原对象。恢复时连已退出方法也重新核对原版本、正文 hash 与当前来源权限。
- `SkillRuntime.restrict_tools` 即使阶段工具集合为空，也记录宿主进一步收窄；adopt/checkpoint/restore 保持单调收窄，阶段退出不能恢复被宿主排除的工具。

未闭合的边界：不适用阶段暂停、真实用户要求修订、旧纯文本最终提示词/旧业务方案的可信导入仍未实现。当前不把 execute_prompt 标签当作证据，可能继续要求规划；因此不能启用生产开关，也不能说这组兼容流程已全部完成。

## 动态方法、真实文本复用与通用依赖派发

- 方法可沿已有作者元数据声明 `task_stage`（限定两种已实现成果协议及 phase/task 限制范围），继续经过原草稿、审核、发布、不可变版本和 NAS 校验链；未声明字段不进入旧序列化结果。不增建生产测试 Skill，不用名称猜阶段语义。尚无前端阶段编辑器。
- 真实成功的 Skill 激活可追加方法约束，与用户要求、步骤、证据及其他字段隔离。PostgreSQL 只允许匹配当前激活来源/固定发布版本的追加；要求、gate、profile、retire 任一不匹配均拒绝。纯文本规划在任务开始后才激活方法也正确绑定；仅预加载而未选择的无关方法不强加规划。
- 区分独立成果数量 minimum 与成果内部条目数 item_count：五条提示词保存在一个文本成果中，仍按五条校验，不误用参考方法默认六条；五张图片仍需五个真实图片成果。
- `find_task_text/reuse_task_text` 只查同用户/组织/会话/分支/执行身份和冻结上下文边界内，已完成步骤及已关闭消息交付的 canonical 文本。保留完整原文及原始来源，按当前方法重新验证，不复制旧验证标签、不重复展示整篇。旧普通聊天文本、用户直接给定完整提示词及旧电商 ready 计划尚无可信导入适配。
- 区分成果原始 source 与本次 delivery/completed 所有者；SQL 阻止追溯改写。A 成果经 B 复用后，C 不能凭 A 已关闭而读到在 C 历史边界之后才完成的 B 目标/成果身份。失去租约、错误摘要、错会话/未来历史及未关闭消息均拒绝。
- 已声明业务 capability 的 DAG 就绪状态同时约束模型工具披露和实际聊天派发；工具按真实 Registry capability 匹配。未完成依赖返回可纠正回执，原生业务 handler 不执行；前置完成后恢复原 ToolPolicy 路径。工具描述仅给已披露工具追加准确 capability 标识；保存计划时拒绝未知非空标识，避免拼错名称使门禁失配。
- 此门禁只覆盖已声明 capability 的就绪性。未规划的辅助工具继续走原权限路径；同能力多步骤的具体输入/输出槽绑定、稳定 operation/调用账本映射及业务成果 adapter 尚未完成，不能用这一层宣称完整调度或副作用防重已接通。

阶段声明兼容边界：旧版本没有字段时保持原序列化；发布非空 task_stage 后，旧二进制不理解该字段。回退必须保留兼容读取器或先核验/停止相关版本参与，不能只关闭新任务开关后直接降级。迁移340的在途状态回滚守卫不等于已处理所有 Skill 元数据降级。

## 稳定 operation 与原调用账本的事务桥

- 迁移341提供内部 `begin_agent_task_operation/complete_agent_task_operation`。不改原 `begin_tool_invocation/complete_tool_invocation`，不执行外部业务、不另扣费。先锁当前 Actor，再锁逻辑任务；核对真实 actor/org/当前 owner、token、租约、状态及 revision，保留整个已有 checkpoint 其他字段。
- 新预留以 logical root/effect_key/version 形成固定 identity；同一事务写入 operation、Actor checkpoint 及原 tool_invocations。只有三者成功才返回 execute。原 native call 冲突或账本写入失败时整个事务回滚，不留下孤立预留或两份不同状态。
- Operation 保留原 owner task/turn、native tool/call 和真实参数摘要。后续新 call_id 只能读原调用的 replay/reconcile；running/uncertain 不授予新派发权，未解决旧版本也不能换效果版本重发。受理 accepted 与真实交付 completed 分开，accepted 不产生图片成果或完成步骤。
- 初次 native 回执通过同一事务写入 operation 与原完整调用结果；相同回执可幂等确认。业务返回错误不能因 handler 返回就当作完成。uncertain 不能用这个初次写入接口改成成功，须另走业务核实协议。已受理子任务的后续完成也不通过该接口伪装成初次 native 返回。
- `operation_store.py` 仅内部使用：从实际 Registry/native call 构造预期状态，逐项核对回传完整 TaskState/Operation，完成写入核对 ToolResult 的真实 actor/workspace/org/conversation/task/tool/call/args，并用原 encode_result 保留无损载荷。结果不明或回传不一致不允许派发；不把数据库响应丢失变成可重发的普通计划错误。
- 回退341检查所有租户是否仍有 operation。NO FORCE、检查和恢复 FORCE RLS 在同一个 DO 语句内，拒绝回退自动还原权限状态；不能依赖发布会话的 actor 可见范围。

范围边界：此基础桥随后已接入下节的 canonical 方法图片 native 路径；没有把任意 effect_key 提供给模型。旧普通提示词、旧 plan_source、其他能力与通用异步消费者仍未完成（图片消费者见343增量），不能将局部路由适配标为整个 generate_image 或46项能力已经 verified。

接业务层前必须完成的具体边界：

1. 对原图片完整规划，用真实 canonical 文档/逐图索引与图片交付 requirement 的槽位建立映射；实际 native prompt/负面内容/比例及获准参考资料必须匹配。仅 capability 就绪不足以证明参数正确。
2. 原 generate_image 的 `accepted=false, completed=false, submission_state=not_accepted` 已接入可信 failed_before_dispatch 纠正协议。只有原 handler 正常返回、无缓存/回放/取消、没有 child/message 身份、审计身份与参数均匹配才可解锁；同效果/版本/步骤/能力下换新 native callId 纠错，保留最多8条原尝试映射及全部旧账本。已受理或未知结果仍禁止重发。
3. 图片已受理但 native 回执未保存时，核对原 tasks 中 `_media_request_v1.origin.parent_task_id/tool_call_id` 的唯一受理身份及原 ecom 受理协议；不可通过再次生图来核实。原受理事实才是收费与子任务身份权威。
4. 子任务逻辑关联须在真实受理事务内建立，不能只在受理后另发 register 请求。持久终态事件、内部原子入队、消费者、冻结上下文、取消/新消息竞争及 pending business batch 的成对回放仍未接通；事件等待不能被旧父任务终态当 ignored 丢弃。

## canonical 图片实际调用、恢复及持久子任务关联

- `image_binding.py` 从已完成方法步骤的真实 canonical 文本匹配完整 prompt+negative、比例及用户图片 requirement/序号。effectKey 只由 requirementId/序号导出；改步骤名、工具名或 callId 不创造新交付槽位。拆分图片步骤可用 image_item_index/contributes_to；越界、重复槽位及单项冒充整套在真实控制器采用计划前拒绝。完整 source 读取与参考图版本/权限继续由原 ToolRuntime/ImageHandler 核对，不在这一层重建资源授权。
- `image_operations.py` 已经接到 `_execute_tools→ChatToolMixin→ToolRuntime→ActorToolLifecycle` 的 native 生命周期。仅对已有 method_bindings 的新 TaskState/canonical generate_image 路由参与；其他请求仍走原路径。当前 Policy/资源核对之后才 replay，新的执行仍在原审批/Policy 边界之后原子 begin。runtime 共享锁只串行预留/回执事实，不持锁跑外部业务。确认后同步已提交 revision/sequence。
- 初次回执 accepted 不生成图片成果、不完成图片步骤。各图片槽位的真实状态会进入模型事实；只提交1/5时继续剩余4项，全部受理且没有其他就绪工作才将 closed transcript 与 waiting_dependencies 一起保存。不能沿用旧“本批全是图片就终止”的数量缺口。
- 独立审查发现并推动修复 native accepted 已落账但 CP 仍有孤立 assistant.tool_calls 的 P1 窗口。`pending_batch.py` 在真实 `_run_loop` 采样前闭合原 missing calls，不新增第二个 assistant；已受理图片读原 ledger，none 辅助读可恢复，未有原记录的未知副作用不派发。已闭合部分不重复，持久原调用恢复不重新消耗旧 paid correction reserve。canonical 操作若两份原子账本均不存在，当前权限下可按原 call 开始原来尚未派发的操作；不能笼统声称所有恢复都不执行新 IO。
- 审查又发现仅辅助读缺回执时，等待判断不能依赖本批存在图片调用；已统一按当前 TaskState 收敛。真实 `_run_loop` 验证1/5、混合、5/5及图片已配对只缺read四种场景，模型不会看到未配对调用，已受理业务 handler 次数不增加。
- 真实 Actor 使用原始 AsyncLocalDBClient；此前已 scope 的 PG fixture 掩盖了新 SQL 严格身份与真实池不匹配的问题。`database_scope.py` 现在为真实原池按已验证 TaskState 建事务级 AsyncScopedDatabaseClient，已存在身份不符则拒绝；checkpoint/operation/artifact 三 store 共用。真实应用角色单连接池验证状态写入、原子预留、连接身份无泄漏及错身份拒绝。
- 迁移342在原 native image acceptance 的 tasks INSERT 事务内关联唯一 reserved operation，写入 agent_task_events.child_task_id/operation_id。绑定失败时原 child/message/预算同时回滚；旧无方法流程不变。父聊天关闭后事件仍 pending，worker 仅可读取同组织、实际 terminal 的原 child 依赖；消费者仍须核对原发布状态与成果。关联身份不可改写，回退检查全部租户。

**342之后的接续**：原子入队、消费者、已发布图片登记和步骤推进已在343增量接通，详见下节。跨入口完整取消/暂停/用户修订竞争、资源装配验收、原受理丢确认核实、旧 plan_source 与普通最终提示词导入仍是发布阻塞，不以新路由替换已有功能。

## 独立审查与修正

独立只读代理检查了新模块与现有 checkpoint/上下文/权限依赖，发现并推动修正：

1. 前置步骤可用不匹配的 receipt 完成；补充真实交付检查及宿主固定 capability gate。
2. 将任务总数量施加到每个并行步骤；区分完整交付与单项贡献，并验证五步各一张累计五张。
3. 任意 operation 冻结全部后续计划；改为只锁已产生成果/效果的具体步骤。
4. 未解决副作用可通过新 effect_version 再预留；阻断未解决旧版本，绑定可信修订。
5. 历史截断后原目标可能丢失；续接投影显式保留 original_goal。
6. 旧 checkpoint 写入口可丢弃或旁路新状态；数据库延期约束覆盖所有写入口，并验证拒绝后两份数据不变。
7. 主循环预算保存后模型看到旧 revision；改为 candidate 同版本事实，并以真实 `_run_loop` 读取上下文提交控制验证。
8. 工具末轮及外层预算耗尽缺少收尾、模型虚报完成不展示缺项；统一有界 partial 和已保存宿主说明。
9. 控制/完成保存漏传原防重复 guard；透传并验证保存/恢复不重置。
10. 逻辑终态到外层 commit_ready 之间崩溃可能重采样、过早 ready 又遗漏文件/知识后处理；共用原最终化流程后保存 ready，并验证 pending 文件 emit 在正常 parts、检查点和真实 Executor 恢复结果一致。终态中间检查点也不重新采样。
11. bootstrap-only 丢确认未重读、空白规划可取验证标签、空工具阶段丢失宿主收窄、加载即强加规划、初始化字段省略/不调用start绕过前置要求；分别补完整检查点核对、规范化结构验证、宿主上限持久化、适用性判断与图片真实派发门禁。独立复核当前修订未发现新增高置信阻塞，结论仅覆盖该批次。
12. 动态追加可把合法旧 pin 错配到另一 profile/退场语义；SQL 校验精确六元绑定。成果复用只看原始 source 会泄露后续复用任务的未来目标/身份；分离 delivery/completed 所有者，冻结两次交付的历史边界，并防止原始 RPC 追溯改写。
13. 通用依赖门禁按目录 capability 匹配，但计划原先允许拼错标识而静默失配；工具描述披露准确标识，真正采用计划前拒绝未知非空 capability。独立复核该修正关闭缺口，定向17项通过，未发现本批新增高置信阻塞；不代表参数或 operation 绑定审查通过。

上述初读缺口均有定向回归。最近独立复核已确认防降级修正关闭原阻塞，未在该修正范围发现新的高置信问题；不等于全系统审查通过。

## 验证证据

- Actor/作用域/上下文/Skill手选与恢复/工具权限相关集合：769通过、3跳过。跳过为未配置专用 Actor 集成测试数据库的恢复/续租用例，未记作通过。
- 最终新基础模块、控制器、恢复适配及真实 PostgreSQL/原339回归：72通过，日志 `/private/tmp/agent-task-foundation-final.log`。此集合与769项集合有重叠，不相加为去重总数。
- PostgreSQL 已验证两种实际表 owner 下的新状态保存、并发 CAS、租约/旧执行者、事务回滚、要求不可变、来源/跨用户隔离、旧RPC防降级/旁路、多次同事务更新、删除孤立保护及回滚守卫；单独集合32通过（含原339的8项）。
- 本地隔离 Unix socket PostgreSQL，未加载生产配置执行测试，未写生产数据，未新建/修改生产 Skill、未进行付费生图。
- 本轮主循环接入、控制、上下文、检查点、归约及原聊天/Actor手选相关集合：125通过，日志 `/private/tmp/agent-task-model-bridge-final.log`。其中新增控制桥接20项；随后补充实际 Executor 读取同一文件 emit 成果的恢复断言，20项仍通过。集合有重叠，不与前述数字相加。
- 实际聊天控制批次通过受控应用角色写入隔离 PostgreSQL，验证文本正文、artifact引用、配对回执和逻辑状态同事务持久化，两种受控 checkpoint owner 均通过。含原339回归共34通过，日志 `/private/tmp/agent-task-model-bridge-postgres.log`。本任务临时测试集群已停止。
- 共用最终化流程的受影响输出/Actor sink、知识引用与回答规划、消息产物和预算回归96通过，日志 `/private/tmp/agent-task-output-compatibility.log`。此结果不证明知识成果 adapter 已接入。
- 本轮阶段交接真实 PostgreSQL 集合36通过，日志 `/private/tmp/agent-task-phase-postgres.log`：包括两种受控 owner 下，完整规划与阶段退出、Skill composition、调用配对及工具上限同事务持久化。沿用隔离 Unix socket 测试库，集群已停止；未写生产或创建生产测试 Skill。本地 Source fixtures 仅验证机制，不替代原两个方法的真实验收。
- 本轮最终初始化/阶段/控制/恢复/旧 Skill、Actor、工具权限与消息出口集合778通过，日志 `/private/tmp/agent-task-bootstrap-phase-final.log`。独立审查代理另行定向23通过，并复核此前775与PG36日志；之后补空白说明、自动加载方法及重放累计轮次用例，最终集合为778，集合重叠不累加。`git diff --check` 通过。
- 后续动态方法、文本复用、领域数量与原 Skill 作者/目录/聊天创建受影响集合211通过，日志 `/private/tmp/agent-task-dynamic-reuse-unit.log`。真实 PostgreSQL 动态追加/不可变协议、文本复用/未来历史与回溯保护、原339集合70通过；加入纯文本五条数量正例后，与动态派发集合合计79通过，日志 `/private/tmp/agent-task-dynamic-text-pg.log`。
- 原 Skill 草稿审核/不可变发布/NAS 校验/目录真实 PostgreSQL 回归68通过，日志 `/private/tmp/agent-task-stage-publication-pg.log`；包括新增阶段声明只能随新审核版本发布、未发布草稿不生效、历史原版本不被改写。
- 本轮 capability 广告/实际派发/错误标识/真实控制批次及阶段相关定向集合63通过，日志 `/private/tmp/agent-task-dispatch-gate.log`。最初 fixture 猜错了 file_search 的 capability 并导入错误，五项失败已按实际目录 `workspace.file.search` 和原上下文入口修正，未改工具合同来迁就测试。
- 当前扩大后的新任务全部模块、真实PG、原 Skill/Actor/chat 执行及工具权限/作者路径集合1010通过，日志 `/private/tmp/agent-task-dispatch-compatibility.log`。该集合与前述数字有重叠，不累加为去重总数。无付费模型/生图，无生产 Skill/数据写入；总体验收账本仍未通过。
- 稳定 operation 内部事务桥扩大兼容集合1064通过，日志 `/private/tmp/agent-task-operation-compatibility.log`。加入 Actor 标记为空时的预留/完成拒绝负例后，最新该批定向集合56通过（14单元、42真实PG，覆盖两种 checkpoint owner），日志 `/private/tmp/agent-task-operation-bridge-pg.log`；两集合有重叠不相加。独立只读复核此前该批54项通过，确认候选状态/原调用核验、事务回滚及全租户回退检查无新增高置信阻塞；新补空 actor 负例同样通过。未调用真实模型或生产服务。
- 安全纠错归约/typed store/真实PG集合85通过（其中PG52），日志 `/private/tmp/agent-task-safe-retry.log`，独立同组85通过；后续补实际原始异步池的应用角色身份事务用例，PG集合54通过，日志 `/private/tmp/agent-task-real-pool-scope.log`。完整规划 native 参数绑定10项与实际 Policy/生命周期4项通过，见 `/private/tmp/agent-task-image-binding.log`、`/private/tmp/agent-task-image-lifecycle.log`。
- 原批次配对恢复最终9项通过，日志 `/private/tmp/agent-task-original-batch-recovery.log`，包括真实 `_run_loop` 单/混合/部分配对与实际 ToolRuntime 未知副作用恢复拒绝。342 原生受理事务/持久 child 关联两种 owner 共8项真实PG通过，日志 `/private/tmp/agent-task-child-dependencies.log`。独立复核这组增量71通过（9+54+8），原P1/P2均确认闭合，未发现新增高置信阻塞；没有将未开发的事件消费/入队视为通过。
- 该轮最终新任务全部模块/原 Skill runtime/Actor/chat/工具权限与真实工具执行集合1007通过，日志 `/private/tmp/agent-task-binding-recovery-final.log`。与前述1064所选模块不同（本轮加原工具执行，未重跑未改动的Skill作者集合），不可用数量比较覆盖，也不与各子集合相加。`git diff --check` 通过；只用本任务隔离 Unix socket 测试集群，无生产数据/Skill/真实模型/付费生成写入。

先前测试曾暴露 fixture 中 checkpoint 表 owner/ACL 与受控运行角色不匹配；已修正 fixture 并实际覆盖两种 owner，不用 superuser 代替应用角色通过核心用例。

## 尚未完成，仍属同一整体目标

1. 新任务初始化已接入默认关闭的主循环；尚需用户要求修订、初次语义提取评估、统一能力入口核验及全链路启用验证。
2. 登记方法阶段退场、权限上限、动态追加/作者声明、resident依赖、旧提示词/方案导入及canonical文本复用已接入；原两个Skill的真实模型规划已验证，完整实图/界面流程仍未验收。
3. 方法及真实旧来源图片的交付槽位/完整提示词/比例已接实际native生命周期与原子调用账本，明确未受理可安全纠错。原图绑定与受理响应丢失后的原生正证据核实已接入并有组件/真实PG验证；仍缺其他路由/业务工具及真实供应商/页面全链验收。46项库存不代表完成接入。
4. 方法/真实旧来源图片已接内部入队、消费者、成果推进和去重；关闭父窗口、queued及已apply owner的暂停/恢复/取消已接。用户修订、组织与所有入口资源边界仍未完整验收；新普通消息遵守现有串行FIFO，不能声称可抢占或自动修订原任务。
5. 续接上下文的完整摘要、资源清单和当前权限装配；计划任务与其他入口适配。
6. 前端类型化进度与成果交付、原流程功能回归、真实模型/界面验收、成本/延迟基准和完整独立审查。

因此尚不具备整套发布条件；现阶段不启用新任务入口、不改默认提示策略，避免以未完成链路替换已有用户功能。

## 接续定位

- 下一项是初始化/最终目标覆盖完善、原两方法实图和界面、完整资源事实、用户修订及其他业务adapter；受理丢确认核实与多入口权限/资源验收仍不可跳过，不能先打开默认开关。
- 主模型控制桥接在 `services/task_execution/model_bridge.py`；生产路径只对已有并经 scope 验证的 TaskState 生效。没有新状态的旧请求和旧检查点保持原协议。
- 原两个 Skill、原两份素材和原始请求继续作为业务验收样本；不得新建简化测试 Skill 代替。
- 总体验收账本仍为未完成。真实模型、异步事件与完整功能保留验证不能由本轮单元/数据库测试替代。


## 2026-10-30 增量：图片自动续接343（未发布）

- `continuation_consumer.py` 接原 ConversationWorker 扫描。原生受理事务写入轻量 child hint；发现使用原 worker 读身份，逻辑状态和入队逐真实 child actor/org 进入独立事务。持久 event 是权威，hint/内存游标仅作扫描加速；标记丢失或重复扫描不重复创建任务。不扩大 event RLS，也不修改共享连接身份。
- 343 按原 goal/revision 在同一事务创建唯一内部 chat Task、新 assistant 和 queued 事件。引用原 input/turn、复制原 task_attachment_refs、预算和选择身份，去除旧 `_image_sources_v1`，让原277 claim为新 Task 重新冻结正确资源边界。没有新建假 user、没有改旧 user/turn/anchor。这里只支持已校验的个人会话 canonical 图片路线，不宣称渠道全覆盖。
- `image_continuation.py` 深拷贝原 checkpoint 全部消息（保留摘要、记忆、规划和成对工具历史），加入内部事实消息。先核实原 child作用域、origin/receipt、已发布媒体阶段、真实完成消息、发布内容和新claim冻结revision，再登记图片 Artifact、结算 operation、推进图片步骤；失败为 `failed_after_dispatch`，不计交付、不盲目重发。图片 checksum 指原发布 JSONB part 快照校验和，不能说成像素文件哈希。
- apply事务同存 TaskState/原 Actor checkpoint 并转移 owner/应用事件。SQL复核来源、冻结base/through、执行身份、原消息前缀顺序和图片证明。回复丢确认通过现有 DatabaseReplayCheckpointStore 重读**整个预期检查点**，不重复业务调用。完成目标走真实主循环终态guard和原commit入口；尚有下游要求则恢复active交给原循环，完整下游工具验收仍待完成。
- 独立审查发现原close三参函数拒绝已关闭输入的P1（已复核修复）：343只为已applied/current logical owner的内部续接增加关闭新assistant分支，保留普通user关闭分支和原终态/计费入口。迁移及回退校验原prosrc hash/owner，未知修改拒绝覆盖；回退先检查所有租户在途引用。
- 新消息边界：**入队前**发现新的用户消息/任务就保留pending并延后，不将旧事件解释为新目标授权；**成功入队后**沿用原Actor串行FIFO，新输入排后且不会被追加到旧CP，普通新消息不抢占已入队工作。专门取消/暂停命令仍走原机制；新逻辑任务的整套取消/恢复与目标修订尚需验收，不能声称全面解决。
- 隔离真实PG定向48项通过，两种checkpoint owner均覆盖；日志 `/private/tmp/agent-task-continuation-executor.log`。包含五张图原生claim/派发登记/模拟供应商结果/真实发布与计费事务、内部原子入队、原277 claim、ChatGenerationExecutor、真实检查点与commit122；execute_chat装配使用测试替身，并调用真实_run_loop终态guard，**不等于真实模型/页面验收**。另覆盖apply丢确认、commit_ready重启、并发入队、消费者重启与数据库身份不泄漏、伪造来源/哈希/receipt拒绝、租约/令牌/权限变更和新输入顺序。
- 尚未提交或部署，新入口默认关闭。C1–C12保持完整升级未验收，不用这些定向证据替代原两个Skill、上下文压缩/新要求、资源与多入口的最终用户验收。


343后续加固：独立审核复现SQL数组包含校验可追加伪造user的P2，现要求原历史逐项前缀完全相同（包括重复项）、只增加一条受控system，并独立重建其facts和event_artifact_ids。独立44项PG复核通过；随后追加source CP consumed、重复历史删除、五图后仍有说明步骤的48项定向通过。实际source CP已consumed不影响新内部owner读取，五图只完成图片步骤，下游仍active且要求/来源不变。

新续接不重放旧输出块，因此发现原激活入口依赖UI块会重启已完成提示词阶段。现仅在原pin/哈希和完成事实验证后，跳过对应完成phase的required/manual/session重激活，选中身份和宿主上限保留；新增真实execute_chat准备路径验证。**不改变旧coordination.requires的常驻依赖语义**，不把“已运行过”当作active。依赖该常驻方法的固定组合仍须单独处理其退场/调度，当前不以放宽requires放行；这是发布前待办。相关Skill/method回归80通过，日志 `/private/tmp/agent-task-continuation-phase.log`。

扩大兼容中间证据2160通过、42跳过（最终SQL/阶段加固前）；最终聚焦Task/Actor/Skill/执行器集合421通过、无跳过，日志 `/private/tmp/agent-task-continuation-final.log`，其后新增负例与下游步骤定向已补。集合有重叠不相加。扩大集合部分旧API测试退出时有后台反馈任务/连接池未关闭诊断，不能视为无运行时告警的完整测试。最终变更扩大集合正在复验，以后续记录为准。


### 343最终增量验证与接续位置

- 最终变更扩大集合 **2175通过、42跳过**，`/private/tmp/agent-task-continuation-final-compatibility.log`。与中间2160/聚焦421/定向48、80都有重叠，不相加。
- 42跳过中，29个Redis依赖场景已用本机自建、无TCP端口的独立Unix socket补测：图片生命周期/槽位恢复/来源的完整组 **79通过、无跳过**，`/private/tmp/agent-task-continuation-redis-pg.log`。原业务Redis没有被访问。
- 另外7个旧Actor opt-in PG测试，经可重复的 `test_task_execution_legacy_actor_postgres.py` 补测：两种checkpoint owner各跑原7项（外层2通过，内层各7通过、无跳过），`/private/tmp/agent-task-continuation-legacy-actor.log`。隔离fixture补足原生产已有基础列，装配原123/239/240/241/248及当前339；343新的close分支保持安装。仅验证旧协议兼容，不代表新逻辑任务整套暂停/取消已经实现。
- 上下文压缩/摘要/记忆、权限模式、附件/资源范围、定时Skill快照与DashScope两种协议补充组 **279通过**，`/private/tmp/agent-task-continuation-context-policy.log`。不把旧模块回归当成所有新入口的端到端验收。
- 6个真实provider/model验收仍未执行；没有调用付费模型、生图服务或写生产。大集合旧API反馈后台任务/池清理诊断保留，不以测试通过掩盖该日志。
- 最新独立复核关闭本轮SQL新增user P2及空UI阶段重激活相关问题，独立44 PG/80 Skill-method通过。旧requires保持常驻，不能声称已完成“过去运行过即可满足常驻依赖”的能力。
- 本轮生产代码/迁移在任务树；未提交、未部署，`agent_task_execution_enabled=False`。本机测试服务只由本任务启动/关闭；临时文件不提交。

接续必须保留上述证据与范围，不重新创建测试Skill替代原两个发布版本。优先处理：原受理响应丢失后通过真实child/event/原调用账本核实（目前reserved/uncertain安全停下，不能盲重发）；旧普通最终提示词与plan_source导入；常驻requires组合与不适用方法的阶段调度；新逻辑任务跨取消/暂停/用户修订的稳定状态和资源/多入口验收。之后完成C1–C12和原两个Skill/原素材的真实模型/页面验收，全部通过才走本地release发布。不得用当前局部通过提前启用入口或宣布功能全部保留。

## 2026-10-30 接续：常驻阶段、旧方案和用户控制（仍未发布）

本轮沿用任务工作树与原分支，无生产迁移或业务写入，agent_task_execution_enabled仍为false。

- Skill阶段完成后移入resident，保留实际版本、正文、附件、手选/会话身份以满足requires；只退出已完成阶段的当前职责和阶段上限。active≤4、resident≤4，沿用合计字节预算。恢复仍实际读原存储、校验当前权限、哈希和宿主TaskState完成证明，不能凭“运行过”伪造依赖。阶段/Skill初组116通过，后续原包及回归见下。
- 344旧来源RPC按实际actor/org、当前fence/lease、同会话已关闭且不超过冻结base的来源读取。发现旧助手文本及ready电商方案，导入保留真实阶段原文、逐图prompt/negative/ratio、revision和plan_source；普通原提示词按原消息文本片段与sha选择，不伪造专业分析。read_task_text支持压缩后读完整已存正文；当前任务已完成的文本亦可原样复用到下一方法步骤。legacy发现/导入PG14通过，前期旧导入相关集合39通过。
- 345逻辑暂停/恢复/取消连接原控制入口，已关闭父窗口、尚未apply的queued窗口、已apply的新owner均有路径；保留原用户、消息全文、要求和已有成果。取消不撤销已实际付费效果，不自动重发。346保持原Actor pause/cancel函数body/OID/owner/ACL，仅由外层统一conversation→task锁序；回滚有hash守卫。新实际应用角色控制+旧Actor协议16通过，包含两owner下旧7项分别通过；不是仅superuser验证。
- 原Service终态RPC使用数据库真实任务生成actor/org事务scope。原上下文不伪造user。修复原来源交接method_bindings为空时engine仍漏存waiting_dependencies的P1；source-only与原方法五图原生受理、真实claim/Executor、apply丢确认/commit_ready重启/Service commit及真实_run_loop共63通过（供应商结果模拟，无真实图片服务调用）。独立该组及原runtime复核99通过。
- API追加completed父窗口时，普通旧消息在进入任何中断落锚前过滤，避免把旧completed对话改成interrupted；实际逻辑控制委托返回真实续接窗口的id/client/assistant/slot，推送闸门和释放槽位不再沿用已关闭旧窗口。控制/接口/槽位定向46通过。
- 原两个Skill从生产只读导出，原package/revision/正文hash及附件经过原SkillStorage验证，没有新建测试Skill。原包输入集成及原runtime等91通过，其中原两包2项；修复restore保留manual_id却没有还原typed manual_selection，按已验证目录同一pin恢复。此组没有模型/图片服务，不能当完整真实验收。
- 扩大旧功能集合一度3762通过55跳过6失败，日志 `/private/tmp/agent-task-resident-source-controls-compatibility.log`。其中旧mock runtime未声明新可选状态，另旧无新状态恢复不应提前访问新scope字段；修复后gateway/真实工具路径/slot/dispatch定向159通过，不能把失败的旧扩大日志当最终全绿。后续新代码仍需重新验证受影响集合。

### 原模型验收新证据与覆盖审查（正在验证）

原两图、原请求、原两Skill及真实qwen3.8-max，仅隔离业务工具的规划测试发现：P可以完成五图完整规划保存及阶段交接；E会把实际五张图提成只有text。另错误能力名反馈只给错误码导致重复猜测。现在上下文公开真实capability_gates，错误时只提示已有门槛的标识与纯文本无capability规则，仍不授予执行权限。

已开发宿主初始化覆盖阶段（详见TECH末段）：新Executor bootstrap启用；旧bootstrap省略字段保持旧协议。start必须有明确action/method_ids及真实用户原文evidence，先同CP存pending提案，独立正常模型轮隔离Skill正文和工具核对，covered才冻结TaskState。missing/uncertain有界修订。审查未完成时广告和实际派发共同挡住所有业务/Skill切换旁路。审查返回后的保存先于命令归约，防止pause后重复审查。恢复核对真实root/actor/conv/source/goal、提案hash及已冻结用户要求，不以执行模型自填coverage工具当证明。

- 最初覆盖阶段及旧兼容183通过；实际loop/PG初组15通过（13单元+两owner PG）。独立初组33通过后发现的审查旁路P1、结果返回后pause窗口P2均已修改，新增18项定向通过；还需独立复核及扩大验证。
- 真实新覆盖流程已看到P独立核对covered并继续规划。E重试暴露evidence超过schema上限但错误缺路径反馈，已补无原始输入的字段错误信息；同时发现逐张独立image要求应按总数规划，不能用max取数量。已改为sum，多组全局提示词序号与各组稳定交付槽位分开绑定，正在验证。尚未宣称两个Skill真实端到端通过。
- 私有模型/原输入证据位于 `/private/tmp/skill-coordination-live/`，不提交原用户图片/Skill正文或凭据。真实模型日志 `/private/tmp/agent-task-original-live-coverage-planning.log`（后续失败/修复日志需分别保留，不能覆盖成全绿）。该脚本使用真实provider和当前控制桥、隔离业务调用，不是完整Actor/UI/供应商生成验收。

### 仍不能部署的边界

整体C1–C12尚未通过。仍需覆盖审查的完整预算/usage持久归属、恢复/取消及最终目标覆盖验收，原两Skill真实生成与界面验收；受理丢确认后核实、非方法/其他路由的真实业务adapter、用户要求修订、其他入口/资源与上下文边界也不能按局部单元通过追认为完成。只有整套验收通过才走本地release.sh；当前不提交部署、不打开默认入口。

### 本轮后续加固与当前验证结论（未发布）

- 独立审查实际复现“按可变邻居排名计算局部槽位”可重发已受理图片P1。已改为 `(requirement_id, 原规划global序号)` 稳定身份，image_slots同源；不能因移动另一未执行步骤而为同一已受理prompt生成新effect。实际ToolExecutionService/lifecycle受理→移动其他未执行步骤→再次提议，只回放原回执，IO保持一次，binding/lifecycle17通过；独立原样复现返回replay、操作仍一条，并定向37通过。
- 新任务usage随完整CP绑定实际owner保存/恢复；内部续接清除已结算源owner的usage，避免再次结算。审查返回后暂停先保存verdict、model_round及usage，再归约命令；审查未完成同时限制工具披露与真实派发。核对/usage/真实PG/续接76通过，日志 `/private/tmp/agent-task-coverage-usage-proof.log`。
- 真实原两Skill/qwen3.8-max规划均产出五条完整规划并完成阶段：P为原参考画布1:1、E为详情3:4，保留各方法原规格逻辑。P日志 `/private/tmp/agent-task-original-live-coverage-planning-before-field-errors.log`，E日志 `/private/tmp/agent-task-original-live-ecom-coverage-planning.log`。E真实模型先自行修正范围/证据提案，经独立覆盖核对后保存13207字符规划并完成步骤；仍是当前控制桥真实provider规划，业务工具隔离，不能说已经生成真实五图或UI通过。
- 新任务、原Skill/Actor/chat/工具/控制/槽位扩大3811通过55跳过，日志 `/private/tmp/agent-task-resident-source-review-final-regression.log`。其后只补隔离核对保留既有host历史摘要（摘要仍为资料、Skill正文不进入核对器），对应新回归及本任务独立Redis/真实PG图片生命周期77通过，日志 `/private/tmp/agent-task-review-context-redis.log`。与前述集合重叠不累加；55跳过包含外部/opt-in条件，不能解释为全功能验收通过。
- 核对器仅判断最终交付范围，不审查未向其提供的附件中的材质/卖点；`has_user_image_references`仅是宿主附件存在事实，不授予权限、不证明产品事实。原两个Skill在真实模型中保存规划的证据与供应商/UI验收必须区分。当前整体仍不部署、开关关闭。

### 初始化核对职责反例补验

当前独立核对器又用真实qwen四例验证：原P和原E提案都列实际五张image→covered；把实际图片遗漏成只有五条提示词→missing；用户明确只要提示词→covered且不增加image。日志 `/private/tmp/agent-task-current-coverage-matrix.log`。初次此矩阵曾把“初始化提案”误当“已完成成果”，并凭提示词Skill名称否定后续生图，失败证据保留在 `...-before-role-boundary.log`；已明确核对器只核交付覆盖、不兼任完成验收或工具可用性判断，不将阶段方法当作整个任务限制。不是通过改预期掩盖失败。可重复的opt-in provider用例为 `tests/test_task_execution_coverage_model_acceptance.py`，需显式原包导出、原输入与真实provider代理；不持有或提交生产凭据。此4例仍不等于供应商/UI或整套C1–C12验收。

最终补充：最新核对/初始化/CP/真实PG续接与控制定向111通过4跳过，日志 `/private/tmp/agent-task-coverage-final-targeted.log`；其中4项provider opt-in随后用原包导出、原输入、真实代理显式开启，实际4通过（9.76秒），日志 `/private/tmp/agent-task-original-live-coverage-pytest.log`。这些集合不累加为去重总数。本任务自有临时PostgreSQL与Redis均已停止，私有证据保留。当前所有升级仍在同一任务分支未提交、未部署，默认开关false，不能把记录中的规划/结构验证改称原两Skill完整实图或全部业务闭环验收。


## 2026-10-30 原图、受理核实与真实模型发现的初始化缺口

本批继续同一任务树，未提交、未部署，默认开关仍关闭。没有创建/修改生产测试Skill，继续核验原两个发布版本。

- 新初始化采用image_binding_version=2；旧v1序列化不新增字段，旧检查点保留原协议。新图片任务必须有完整规划（原方法规划或host.image_plan），逐张由bind_task_image_sources调用原ChatImageInputResolver解析真实来源、角色顺序、文件版本和字节。模型只提交定位，不提供可信哈希/路径。新绑定证据为独立host artifact；受理前原ImageHandler再次解析并与规划、实际尺寸、绑定资源核对。原ready方案继续沿用native plan_source冻结校验。
- 原图绑定可直接作为图片步骤的准备控制；若模型计划已有独立准备步骤，可显式传step_id，要求该步骤就绪、无capability、无用户交付目标。绑定证据归该步骤，完成后解锁后续图片；不把绑定算作一张图或一份用户分析。绑定后预留/未知/已受理的槽位不能换资源。
- 新347 RPC只凭原生已提交child/event、原call/turn及ledger hash、同Actor/org/conversation和assistant关联正证据恢复accepted。未找到child仍未知，不授予重发。保留原invocation未知结果，不伪装原handler成功返回。普通handler缺回执先记录uncertain，再立即查原账本，不依赖模型再调用一次；崩溃后的同槽位重放走相同核实。
- 运行中的任务从可信owner._task_image_operations识别；关闭新入口开关后仍执行原图复核。不存在原先误设想的_agent_task_root请求参数标记，不依赖模型字段。
- 已有服务器电商ready方案可按原plan_id/revision导入当前父任务，无须重写三阶段成果。历史方案仍受闭合消息和固定历史边界约束；加入当前/历史两种PG用例。

真实原P模型测试暴露了单纯binary coverage的缺陷：提案仅保留提示词，隔离核对仍回covered；实际图片缺项随后被槽位校验拦住。另发现execute_prompt与本次规划方法同时选择，以及控制准备和实际产图混淆。本批没有把这些失败记作验收通过。

因此新RequirementReview采用version=2：核对模型不见提案、Skill名称或正文，独立提取当前用户的交付及动作；宿主比对种类、数量、原文证据与执行动作，按每个原文交付义务分别核对内部条数。多用途宽泛引用不能唯一配对则uncertain，不能合并数量冒充通过。记录原始observation，恢复时重算比较结果；旧v1保持原binary协议和字节形状。该观察仍是有误差的语义提取，不等于保证永不漏解自然语言，也不授予任何业务权限。

真实测试还表明泛化错误码不足以让模型纠正计划。对纯文本步骤误标生图、重复图片槽位、单图宣称整套完成，控制回执现在提供对应步骤/可修正说明，保持状态不变，不自动改写用户目标。

验证：本批扩大回归3856通过、55跳过（/private/tmp/agent-task-blind-source-native-final-regression.log），之后按义务配对及准备步骤的增量继续定向验证；集合重叠不相加。v2原供应商隔离核对5项通过，包括原两Skill背景、遗漏图片负例及仅提示词请求（/private/tmp/agent-task-blind-extraction-original-live.log）。原完整模型规划+资源绑定仍在重跑；没有真实供应商图片或页面验收证据。供应商超时、错误动作和重复无效计划的私有日志保留，未当通过。

剩余：完整原两Skill的原图→真实规划→原生受理→供应商产图→自动续接→页面交付；真实取消/恢复与断线场景；其他业务结果/参数/回执adapter、用户目标修订及多入口上下文权限验收。没有整套发布条件，不能开启新默认入口。


本批追加结论：逐义务配对/计数集合保持/准备步骤与原生复核最后定向110通过，日志/private/tmp/agent-task-per-obligation-stage-final.log。独立复核33项通过，关闭多text条数与冻结后方法数量回落两个P2。该结论不扩展为全系统验收。

真实原P规划诊断已推进到规划保存和方法完成，但产出的商品事实把原图“8入”写为“8寸”，并包含未核实材质。该业务规划未通过人工验收，已停止该次只读诊断，不继续供应商产图；没有图片业务工具派发。日志/private/tmp/agent-task-original-live-per-obligation-binding.log保留。当前核对层只证明交付协议，不证明商品事实真实；后续须把专业事实/成品质量核验接在方法完成与生图前，并处理需修正规划的阶段状态，不能把结构通过当专业通过。原E完整链仍未运行完成。另多个独立计数提示词集合与方法数量的整体映射仍需专门验收，不能以单集合五条正例代替。


## 2026-10-30 规划事实核验与原生只读成果批次

本段更新前述v2状态：新初始化使用image_binding_version=3；旧v1/v2检查点保持原行为。默认入口继续关闭，未提交、未部署。用户已明确暂停付费测试，供应商恢复额度也不自动重试。

- 规划保存后先是草稿；完成规划步骤前，捕获原生图片字节/版本/来源角色、用户原文及已导入真实查询数据，进入当前Actor主循环的独立事实核验。核验轮不加载Skill正文和业务工具，沿用原模型预算、usage、取消安全点与CP保存。核验失败保留草稿与未完成阶段，模型在同一步写修正版；每步最多三次，换草稿不能重置上限。
- 宿主事实证明绑定任务身份、原目标、原始消息、分支、具体规划SHA和来源快照。恢复重新验证；绑定图片和原生受理前重新读取来源，拒绝原图/事实变化。review_task_plan允许对已保存规划重核，不修改原目标/既有成果。事实核验本身仍可能误判，不作永久正确承诺。
- native_results使用现有Actor tool_invocations和348受限RPC导入实际只读text/data成果。仅当前Policy允许的显式只读工具/动作参与；同原任务/turn的规范参数由CP保存哈希见证，压缩后无需模型猜回参数。缓存消费记录cached与handler_started=False，不重复查询和计费。未知/失败结果、能力不匹配、撤权、跨Actor/作用域及修改后的回执不能充当完成证据。
- 原生只读数据可作为规划核验引用的tool证据，数据不是新指令或授权。恢复读取同时核对原生状态、数据/文本投影和身份，避免只验证旧inline_text副本。
- 原生图片handler识别可信v3 TaskState，即使新入口关闭、当前没有图片coordinator，也执行既有任务证明核验。v1/v2不改变旧协议。

本地证据：事实/阶段真实PG定向39通过；只读成果/真实PG/来源检查最新37通过（/private/tmp/agent-task-read-integrity-final.log）；原生v2/v3受理与来源检查18通过（/private/tmp/agent-task-facts-native-v3.log，随后追加无coordinator场景）；旧工具执行回归161通过（/private/tmp/agent-task-read-hook-clean-order.log）。集合有重叠，不累加。旧工具测试有两处mock改到实际兼容导入路径，消除测试间模块缓存污染，未降低生产断言。

真实供应商事实核验因DashScope欠费/访问拒绝没有通过；本地模型判断使用模拟输出，仅验证宿主/数据库机制。原两Skill的已生成规划和原始输入继续复用，但其中8入误读8寸/未核实材质的问题仍需经真实核验修正，不可直接送付费生图。

剩余整套范围：文件/写回执/异步视频/计划任务/MCP等领域成果适配、用户目标修订、多入口权限与上下文、前端进度及原两Skill真实产图/页面验收。只读适配不代表所有业务适配完成；旧入口仍保留，不给未适配能力增加跨回合自动执行权。

本批扩大回归3922通过、64跳过，89.73秒，日志/private/tmp/agent-task-facts-reads-regression.log；模块清单保存在同前缀-files.txt。该结果替代之前代码迭代的兼容证据，不与旧计数累加。能力库存仅将明确只读分支标为adapted，所有领域均未因此提升为verified。

独立复核发现并已修复合法empty查询不兼容P2：原工具成功但无匹配数据现在保留empty状态，可导入真实空data/文本；宿主empty标签锁定原状态，恢复后不能将empty篡改为success。原生执行/缓存、真实PG完成与压缩恢复及状态篡改负例定向58通过（/private/tmp/agent-task-native-empty-compatibility.log），末次缓存导入参数化另有局部验证。先前3922回归在此小修之前取得，其无关兼容证据复用，不能冒称已经对最终小修重复全套。新增可信TaskState且无图片coordinator的原生受理定向12通过（/private/tmp/agent-task-native-state-marker-final.log），含入口关闭后的受理校验及拒绝未规划提示词，未调用图片供应商。


### 本地续接：原生只读导出文件

import_task_result扩展file，仅接受已登记只读结果中的真实FileRef；没有按模型文字生成文件/任意路径导入。保留原生staging结果消费规则，不开放用户文件工具的staging输入访问。核验当前Actor/工作区/会话/作用域/读取授权、精确sandbox_ref、TTL、大小、全部文件字节和原生元数据；导入和恢复均核验，完成步骤前再次读取真实ledger和当前文件。文件IO放到线程，控制CP继续原子保存。成果引用仍绑定原invocation，原查询不重复执行。

实际文件字节+原工具执行/缓存+真实PG导入与压缩恢复+控制器/模型桥接/事实源回归99通过（/private/tmp/agent-task-native-files-compatibility.log，1.83秒）。11项文件用例覆盖删除、过期、内容/元数据修改、越界symlink、错误locator、撤权和身份变化。此处只适配已有只读查询导出；code_execute产物、打包交付、外部写回执和异步领域仍待接入，不把file支持扩展为全部文件能力已完成。此前3922扩大回归未涵盖此后文件增量，按受影响范围定向验证，不冒称重跑全套。


本轮最终任务集合：544通过、7跳过，21.64秒，/private/tmp/agent-task-local-current-all.log；启用的是私有原包本地导出读取，原两发布包集成通过，TASK_PLAN_FACT_LIVE和TASK_COVERAGE_LIVE均为0，不运行真实供应商。此前3922扩大回归按未受后续file/empty修改影响的兼容范围复用，两组不累加。独立复核关闭empty P2，定向33通过；文件边界独立25通过，未发现高置信阻塞。末次缓存empty导入2通过/private/tmp/agent-task-empty-cache-import-final.log。git diff --check通过。

接续开发仍需完成：外部写入回执与稳定operation绑定、代码产物/打包及交付、异步视频/计划任务/MCP等领域适配、用户目标修订/多入口/前端进度与上下文整体验收。当前本地任务集合通过不等于上述已实现，更不等于原两Skill真实产图/页面验收通过。付费验证必须先估价并获本轮新增确认；不得以余额恢复、旧授权或opt-in配置自动执行。继续保持默认入口关闭和不部署。


## 2026-10-30 接续：原生Reach写操作及真实回执

本批继续原任务，使用implementation流程；未提交、未部署，默认入口关闭，不修改原生产Skill。用户暂停付费测试的约束继续有效，本轮没有外部平台/供应商调用。

任务地图：现有ERP/文件删除主要返回说明，不能据此泛化认定写成功。先适配已有reach_operations持久领域回执的Agent Reach同步写；新组件仅在v3 ManagedReadLifecycle的显式business_write分支启用。原ToolRuntime的当前Policy、资源复核和最终内容确认仍在预留之前；旧v1/v2与其他领域继续原路径。

- reach_operations.py把一个就绪写步骤绑定到唯一原始receipt要求（minimum=1），effect_key由capability+不可变requirement ID生成；复用341原子保存逻辑operation和139 native invocation，不新建业务执行器。新call_id、步骤换名或参数变化不增加第二次派发权。当前不支持一项receipt minimum>1或多个同时可匹配的写步骤；模型须明确交付拆分，不能静默任选步骤。
- 原handler返回后，经349只读RPC核对当前Actor token/lease/revision/member，以及原operation/native call/turn/hash/任务/会话/组织和真实Reach行。只有平台成功回执与原ToolResult一致才标completed；普通成功文字、缺失/错误/partial结果和平台提交待审核均保留uncertain。没有正证据时不自动重发，也不宣称所有丢回执都可自动恢复。
- import_task_receipt只导入已完成operation的真实领域回执，复核原native完整payload、执行状态、audit身份、参数hash及平台回执，生成receipt成果，再由原步骤完成检查消费。模型不能写入原生回执或标记自己完成。349只读受限函数不返回账号凭据、不赋予重试权。
- recovery_only原pending batch不能为未持久预留的写创建新派发。数据库预留/完成无法确认时停止当前owner路径，保留原账本；不把保存不明转成可纠正参数错误。已完成结果可用新call_id重放，不重复业务IO/使用量。

验证：Reach单元12通过；真实原291/294+139/341/348/349 PostgreSQL4通过，覆盖两种CP表owner、原上下文保留、状态恢复、真实receipt导入→完成、未知回执拒绝新ID、只保留一个native与一个Reach操作、错token/错revision/跨Actor拒绝。最初真实PG失败因最小图片fixture缺失原Reach membership helper的身份表读取权限；仅补齐fixture的既有角色权限，未放宽生产RLS或改断言。

最终受影响集合774通过、7跳过，25.23秒，/private/tmp/agent-task-reach-current-local-regression.log；包含所有task_execution模块、旧ChatToolMixin/工具生产集成及Agent Reach已有用例。原包本地导出参与验证，真实模型/图片opt-in均关闭。此前扩大3922的未受影响范围继续复用，两集合重叠不相加。独立窄复核16通过，未发现高置信新阻塞；git diff --check通过。

剩余：已知not_dispatched的Reach有界纠错重试及未知回执正证据恢复，其他ERP/文件动作领域回执与稳定参数绑定，代码产物/打包、异步视频/计划任务/MCP，用户目标修订/多入口/前端进度及整体验收。原两Skill真实事实纠错、供应商产图和页面验收仍须先估价并取得新增付费确认。不能把本批通过当作全部业务已升级或整套发布条件。


## 2026-10-30 接续：正证据恢复、安全重试和其余只读成果

状态：同任务树继续，未提交/未部署，默认新入口关闭。用户禁止新增未确认费用的测试，本批没有模型/图片/平台付费或外部调用；此前真实原两Skill规划及原包资料复用，不重新创建Skill。

完成的连贯批次：

1. 350新增Reach到原native invocation的可空关联，保留legacy数据和原保留期删除语义（ON DELETE SET NULL）。v3在原Connections.reserve事务内先按原Actor token/lease/revision/成员/原reserved操作/原call+turn+hash取得native UUID，然后保存域记录关联；仅服务端typed TaskState可启用。域call_id使用native UUID，避免不同任务复用供应商call_id时撞到旧操作。没有当前关联的legacy行不能被重新认领；原legacy调用仍走原SQL，不依赖新列。
2. 正证据reconcile把原关联、平台succeeded、明确回执与当前逻辑槽位对应后只推进逻辑状态与原CP。保留原running/uncertain native返回，不伪造收到过成功响应、不新派发。handler异常/返回缺receipt可即时核实，恢复后重复读取仍不产生第二次IO；partial、待审核、空/无明确回执及未关联旧记录均拒绝当作完成。
3. 341共享拒绝核验扩为显式领域适配：图片原not_accepted合同不变；Reach必须有真实关联且原域账本failed/not_dispatched/code与原返回一致，才开放有界修正。原Policy/最终内容确认仍先于新预留；新attempt ID及最多8个历史尝试；unknown/failed-after-dispatch不能重发。ACCESS_BUDGET_WAIT未到原native next_eligible_at（或无可信时间）返回retry_wait，在新native/域记录产生前停止轮询。341仍为本任务未发布的迁移源，本次未改已发布迁移。
4. 回退350新增全租户同事务drain guard：存在native关联时拒绝删除；失败恢复FORCE RLS且保留列/函数。独立审查发现的原回退丢唯一恢复关联P2已关闭。
5. 原9个local查询与5个knowledge读取显式接入原生text/data/FileRef成果协议；当前Policy必须allow/read，不扩入trigger_erp_sync、ERP委托、code_execute或其他写能力。原结果/empty/缓存/当前权限和参数见证沿用前批，不重造查询结果。
6. 当前唯一reviewed MCP合成只读Connector接原生text成果：完整ToolSpec严格相等、effects none、record_required才适配；导入和重读核connector/capability/remote身份及当前actor/org/workspace/conversation、原参数hash和当前Policy。不是任意MCP或业务写入适配。read_task_text/complete_task_step同样走当前native证据校验，避免正式入口不一致；同root reuse保留验证tags并重核，跨root不剥离标记降为普通文本。普通手写分析/提示词的原复用逻辑保持。

本地验证（重叠集合不累加）：扩大931通过、16跳过，30.99秒，/private/tmp/agent-task-recovery-business-local-regression.log；包括原包本地集成、所有task_execution、旧聊天工具/ERP本地查询/知识库及Reach用例。此后MCP与相应原回归130通过，/private/tmp/agent-task-reviewed-mcp-results.log；正式文本入口与旧复用/FileRef/controller/model_bridge/MCP定向131通过，/private/tmp/agent-task-connector-text-boundary-final.log。最后预算等待门禁+恢复/341基础104通过，随后UUID身份冲突真实PG集合80通过，/private/tmp/agent-task-reach-native-identity-final.log（其中26 Reach PG，54原共享operation PG）。这些后续增量按影响范围验证，不冒称对最终源码重复整个931集合。git diff --check通过。

独立审查：Reach恢复/重试30通过，另缺receipt模拟2通过，指出并关闭回退P2；MCP/只读身份窄审130通过。后续UID/等待时间/正式文本入口小增量的代码和日志另给独立核对，不把局部审查当整套C1-C12通过。

下一批范围仍未闭合：ERP/文件动作等其他写回执与参数绑定，代码产物/打包交付，真实视频处理/后台入口，计划任务、用户目标修订、多入口权限与前端进度，以及原两Skill真实纠错→供应商产图→自动续接→页面交付。查明现generate_video工具实际同步等待adapter，和其旧schema“异步返回task_id”描述不同；后续应适配真实流程及原计费事务，不能照描述虚构子任务或把返回URL当完整持久证明。以上未完成项继续阻止整套部署。付费测试须先报价并取得新确认，恢复供应商余额不是授权。

最后补验：MCP跨root原生文本不丢验证身份，并返回当前获准导入的明确修正说明；MCP14+原普通文本复用6共20通过（/private/tmp/agent-task-connector-reuse-final.log）。正式文本控制入口增量覆盖read/complete/reuse，撤权时状态和commit不变；同root复用保留原tags，普通分析/提示词旧协议不受该分支影响。最终几处窄diff仅请求独立只读核对，没有重复要求运行已通过集合。

本轮关闭：最后预算门禁/native UUID身份/正式MCP文本入口已由独立审查只读对照关闭，未发现可复现高风险缺口；该次只核对代码和既有80 PG/20复用日志，未冒称独立重跑。自有临时PostgreSQL已停止，私有证据和任务工作树保留。所有代码仍未提交/部署，默认新入口false；后续继续上述剩余范围，付费测试仍须先估价并获新增确认。


## 2026-10-30 接续：代码成果、计划任务原生身份与事务回执

继续既有implementation流程，同一任务树，未提交/未部署，新入口仍默认false。本批完全本地：不调用模型、图片、视频或外部平台；恢复供应商余额不构成付费授权。现有原包/原输入和已通过证据复用，未新建Skill。

代码成果：v3 code_execute在341/139原事务预留稳定effect，输入为原code字节SHA；未确认执行时也阻止换代码绕过。KernelManager仅为managed任务传每次调用独立observer，验证本次request ID与reply形状；匹配ok/error才证明代码已执行完，正常Python错误不等于用户成果完成。AST拒绝/内核尚未提交可在原Policy下有界纠错；提交后crash/timeout/无ACK为unknown，不重跑。旧调用保留原签名与旧重建机制。

351只读原native执行结果。read_task_code_result恢复原stdout/真实emit列表，import_task_code_result只导入宿主真实返回文件或文本，不按stdout编造文件。managed宿主在原工作区锁内、返回原生ledger之前捕获产物SHA/版本；导入及完成时核当前Actor/工作区/会话/作用域/权限、真实字节和原版本，首次导入之前同大小替换也会拒绝。ZIP是实际代码emit产物，不增加另一个打包执行器。已完成代码换call_id只复用，不重复worker/业务IO。独立审查关闭读取分支遗漏冻结personal/context scope的P2；后续只读核对未发现新阻塞。

计划任务：沿用原ChatTaskManager、可编辑确认表单、ChangeSet检查/审批与249原生业务提交回执。v3唯一就绪receipt minimum=1步骤绑定原requirement稳定effect；原Policy/确认仍先于341预留，不从模型新call_id获得第二次创建权。352最小关联表在manager业务IO前用当前Actor token/lease/原running native UUID+reserved op+参数hash绑定operation/action。只保存身份关联，不新建业务任务、不授予权限。原description仍由真实用户文字补齐；hash对照补齐之前的实际dispatch参数。

直接变更沿用原服务的稳定idempotency key；表单只替换确定性form_id，原字段、编辑内容、确认方式和原WS鉴权/状态迁移不变，仍由chat-form:conversation:original_assistant:form_id提交。read_task_scheduled_result只核原关联、精确source scope、同Actor/org/action/resource，稳定key与表单key存在多个候选则拒绝认领。表单/ChangeSet受理为accepted，CS.status单独为applied也不等于交付；只有249原业务事务receipt可标completed，返回丢失/后续CS状态落失败可由正回执恢复。没有回执不重发；import_task_receipt导入原receipt，complete前复核。控制推进通过原model_bridge私有staging与assistant/tool完整pairs同一CP保存；未新增控制RPC绕过该事务。

352回退同事务临时NO FORCE检查所有租户，存在绑定拒绝回退并保留关联，避免丢失唯一恢复线索。旧无TaskState或v1/v2路径不依赖新表/RPC。独立窄审只读核对352绑定/回退及既有30项日志，无新高置信阻塞；不冒称独立重跑。

验证证据（重叠不相加）：代码最终扩大1023通过/7跳过、83.09秒，/private/tmp/agent-task-code-current-regression.log；含原kernel/worker、factory、mixin及全部task_execution/旧工具兼容，真实worker/OSS为本地double。随后计划任务最终受影响集合953通过/7跳过、32.52秒，/private/tmp/agent-task-scheduled-current-regression.log；其中352真实PG18项（两种CP owner）核原248/249领域schema+139/341/348/351/352，原业务提交以fixture写入真实receipt表模拟，未运行后台Planner/真实调度提交。新增manager真实适配与CP丢确认定向16通过，/private/tmp/agent-task-scheduled-manager-final.log；PG+最初unit30通过，/private/tmp/agent-task-scheduled-binding-postgres.log。最新953不包含之前1023的全部旧sandbox/worker集合，按未变化范围复用，而非互相替代或累加。

明确剩余：计划任务确认/取消/失败后的后台自动消费、内部续接/原目标修订；异步视频原生受理、计费/回调/真实发布成果；ERP、删除/恢复等其他写操作的原生身份和可靠领域回执；多入口/前端进度及整套原两Skill真实页面验收。当前代码成果及receipt读取适配不代表这些闭环已完成，不满足整套部署条件。下一批必须复用原Actor队列/事件/上下文锚点，不能把表单受理算完成、把URL当视频持久证明，或在未知提交后自动重发。付费验收仍需先报预计范围/成本并等待新增确认。


本批末次窄验：352真实PG18项在两种CP owner下再次通过（2.25秒，/private/tmp/agent-task-scheduled-rollback-final.log），补充验证错误action/hash无法改绑、跨租户不可见时回退仍能发现绑定并拒绝，失败后FORCE RLS恢复。该次仅补齐新回退/身份负例，不重复953已通过集合。

接续任务地图：先将原343图片自动续接机制扩为可信领域来源适配，再接计划任务原表单取消/ChangeSet确认或失败事件；必须在原完整assistant/tool与表单展示CP落库后进入waiting，防止保存受理后、UI尚未交付时崩溃导致确认入口丢失。回执核实需支持原owner已完成，不得放宽活跃Actor读RPC租约来代替后台来源校验。继续使用原队列、事件去重、原input_message_id/turn_id和冻结完整历史；更新原任务目标的用户表单编辑与取消不能被当作模型自授权限。正回执、负终态、暂停/取消恢复、新用户消息优先、多个异步依赖/领域混合均需验证。

视频方面，已确认旧chat generate_video实际上同步wait_for_result=True，而VideoHandler.start才是原生异步任务；原异步入口在provider之前保存本地task/锁积分，之后回填external ID，旧Smart重试/refund逻辑不能直接套在managed未知受理上。接入前须核callback最终VideoPart与真实message/context_revision/计费状态；不能照旧schema“异步task_id”编造子任务，也不能把URL当持久成果。

文件删除现按os.remove后finally记录deleted_files，记录错误可能被原逻辑吸收；恢复先原子link，再标purged，标记失败亦可能被吸收。故普通成功文字/文件不存在不足以建立通用写回执，后续需要原来源身份+实际文件版本/恢复备份与未知提交边界，不能仅加success标签。ERP远程写同样须按实际API原生回执适配。

原两Skill真实验收仍复用/private/tmp/skill-coordination-live/的原包/原图/原规划结果；原规划存在8入误写8寸、无依据防水PP/8层等事实问题，不能直接复用为通过产图输入。真实fact review/产图/页面验收必须先估价并得到新增付费确认；当前均未执行。不因余额恢复自动重试原DashScope。


## 2026-10-30 接续：真实领域事件进入原Actor续接

同一任务树继续，已实现353+352被动fact/343公共enqueue/image wrapper/345等待审批控制，新增receipt_continuation与共享continuation_context、原worker消费者组合和executor源分派。没有第二套队列，没有fake用户消息，没有放宽351活跃Actor租约。源owner已关闭时，在当前租户/成员/原native绑定下核实原表单取消或CS负终态、真实249回执，创建事件后按原队列恢复。只恢复active和原上下文，随后原模型control核实/导入，不自动标业务成功。负信号不能永久抹掉之后的真实正回执，分别dedupe。源kind在再次image入队前移除继承值，避免混合阶段错分派。暂停/取消/新用户输入优先仍由原控制/队列处理，原已发生业务不回滚。

证据：初始18新PG+原image/scheduled/modelbridge共126通过/12.15秒（/private/tmp/agent-task-receipt-continuation-current.log）；此后opaque CP篡改拒绝+真实pool消费者重启去重20通过/2.73秒（/private/tmp/agent-task-receipt-consumer-final.log）；原345/346真实控制路径与新续接42通过/5.69秒（/private/tmp/agent-task-receipt-controls-final.log），包括入队前后暂停/恢复/取消；实际engine保存原表单与完整pairs后进入waiting，等待CP丢确认恢复active定向2通过（/private/tmp/agent-task-receipt-ui-wait.log）。这几组重叠不累加；最后image source_kind继承修正尚需后续兼容集合包含。独立只读窄审核当前代码和20 PG日志，未发现高置信阻塞；审查工具首次403后已恢复，本轮未重跑测试/外部调用，不冒称整套验收。

所有供应商、平台及付费验收仍暂停；没有生产操作。临时PG本批为本地验证启动，当前运行PID16069，目录/private/tmp/skill-coordination-pg.aToMC9，仅Unix socket /private/tmp:55439。未提交、未部署。剩余视频、ERP/文件真实写回执、目标修订、多入口/前端及原两Skill整体验收继续阻止发布。


## 2026-10-30 接续：ERP journal、单次发送和原上下文回归

同一任务树继续implementation，无付费、模型、图片、视频或真实ERP写入，未提交/部署。上批临时PG目录已不在，重新initdb本任务全新Unix-socket临时库/private/tmp/agent-orchestration-pg.9yUXZd（/private/tmp:55439，禁TCP），没有连接生产。

354+native_writes接通原ToolExecutor/ErpDispatcher/KuaiMaiClient。原341/139稳定slot预留前继续原Policy/确认，绑定真实原native UUID/hash及私有nonce；派发前再次核Actor token/lease/member/active，nonce只允许一次派发。实际HTTP响应先于格式化保存，格式化丢返回可只读核实/import/complete，原139的失败不被改写成虚构成功。未知结果换call_id也不能再发。SDK原request带第二层tenacity重试，managed必须使用无重试request_once；原未管理路径和读取保留原重试。原Redis锁/去重/释放/close保持；managed参数验证明确未派发时不写10分钟done，允许原审批下的有界参数纠错。

完成语义收紧为按方法准入：目前仅erp.item.tag.add，其他原ERP写方法仍可按原权限执行并有原回执，但通用success最多accepted；显式异步/部分失败不能完成用户交付。库存保留erp_execute整体legacy_only，并单列partially_adapted新路径，未把单方法测试冒充全部ERP业务验收。ERP异步最终核实/后台事件、视频计费/受理/回调/发布、文件删除/恢复可靠回执、目标修订及多入口/UI和原两Skill整体验收仍未闭合。

扩大task_execution+旧chat loop/ERP SDK/mixin回归1188通过、9跳过，27.89秒，/private/tmp/agent-task-receipt-erp-current-regression.log；含本轮353/原image续接及原context接口。此前提取时覆盖过原continuation_context的错误已从本会话原始patch精确恢复，新的共享Actor CP helper独立为continuation_checkpoint；旧10项context及全部相关集合通过，不遗漏原ContinuationSnapshot/validate_continuation/build_continuation_snapshot协议。

随后方法准入收紧、Redis及原子CP/回执再验证的窄增量83通过，2.05秒，/private/tmp/agent-task-erp-checkpoint-final.log，组成22 ERP单元+18真实PG（两种checkpoint owner）+原20 model_bridge+10 context+2 engine wait+11 ERPmixin；与1188重叠不相加。PG用真实139/341/348/351/354，业务HTTP为本地AsyncMock，覆盖原始回执/lost format/timeout/no-send、当前scope、一次性nonce、取消/过期、旧nonce失效、原审批参数纠错、全租户回退drain与原消息/opaque context不变。测试期发现一处用created_at查询旧139表的测试错误，改为真实started_at后通过；未放宽生产断言。尚无整套发布条件。

本批收尾：独立只读窄审最新allowlist、Redis、CP和receipt变更断言无新增高置信问题，未重跑测试。git diff --check通过。自有临时PG PID19124已fast stop；保留目录/private/tmp/agent-orchestration-pg.9yUXZd（后续如仍存在可在同一Unix socket重启），无Redis或生产服务变动。当前任务仍未提交/部署，不清理工作树。

接续位置：先补ERP逐方法最终状态事实适配（其余success现在只accepted）；文件write需沿用PreparedFileCall/workspace lease、原备份/30天恢复协议，防止os.remove后fire-and-forget记录丢失及同路径新文件被旧重试误删；视频接原VideoHandler计费/任务/回调/VideoPart发布，解决原用户turn已关闭后的真实产物消费，不能重发unknown或用URL自证。当前没有这些领域的占位“成功”实现。继续保持原history、frozen method、用户原目标及原Actor队列边界；原两Skill付费真实验收须先报价和新增确认。广泛集合已通过部分按未变化范围复用，禁止把83与1188累加或宣称完整业务验收。


## 2026-10-30 接续：原生文件恢复、丢确认和文件交付

同一任务树，继续implementation与review，无付费/外部业务调用，未提交部署。复用前批ERP/后台续接证据，新增355/rollback、file_restores.py和真实PG测试；354 journal file域采用专属settlement，341原negative证据支持真实prepared恢复。小范围bind/proof_phase钩子复用生命周期，ERP默认约束与原重试证据保留。原FileDeleteMixin只在v3 prepared restore分支调用新宿主适配；file_delete物理删除流程尚未变更。FileExecutor保留内部snapshot命名空间，禁止公共文件工具访问。

实际恢复下载沿原OSS If-Match及PreparedFileCall核验，在原工作区锁内保存private snapshot并通过原link发布。原native UUID/hash/记录ID/备份版本绑定；no-clobber、实际inode+size+mtime及全SHA核验后，原deleted_files.purged与native returned同事务。丢确认仅补记真实已发布文件的领域事实，原139 payload不改写成功；同名同字节不同inode及后来内容变化均拒绝认领/完成，不重复下载或覆盖。文件型原目标保持file种类并额外核当前read授权，未强改为receipt。

本地验证：原完整文件入口/边界/执行器+新增restore/ERP集合240通过6.21s，/private/tmp/agent-task-file-restore-current.log；原先从仓库root跑该集合时跨进程锁测试子进程无法import services（239通过/1失败），改在backend按原测试入口执行后全通过，没有改锁或断言。file型交付新增后恢复+ERP52通过2.99s，/private/tmp/agent-task-file-restore-delivery.log。下载失败纠错、已有目标零IO、文件读取撤权/内容变化、domain-only settlement及355全租户回退、原model_bridge原子CP成功/失败：24 PG通过2.38s，/private/tmp/agent-task-file-restore-recovery.log。

最终扩大1051通过、9跳过，32.48s，/private/tmp/agent-task-file-restore-final-regression.log，涵盖所有task_execution、原file_target_execution/file_tool_boundaries/file_executor/file_tool_mixin/file_identity/resource_scope_continuity；不是前批1188的超集，按未变范围复用ERP SDK/旧chat证据，不相加。随后controller先验证proof再读取intent，并补实际下载后取消/lease过期不发布：65通过2.86s，/private/tmp/agent-task-file-restore-fence-final.log（28真实PG+7 controller+20原bridge+10旧context）。SQL使用原111/139/341/348/351/354/355，OSS仅本地Mock写真实测试文件，没有外部访问。真实PG两种checkpoint owner均通过；原消息/opaque CP、原native错误结果均保留。

独立只读审查先后核原生恢复边界及filekind/read权限增量，无新增高置信问题，核对24通过日志但未重跑或外部调用。整套仍不具备部署条件：删除及可靠备份/逐项回执、其余ERP方法与异步核实、视频计费/回调/发布、目标修订/多入口/UI和原两Skill真实验收仍未完成；未发布新Skill。新入口默认false。实际dispatch但未发布的未知恢复不自动重做，保留snapshot等待核实；不把该边界冒充所有恢复场景完成。

本批关闭本地资源：自有临时PG目录/private/tmp/agent-orchestration-pg.9yUXZd、PID42367已fast stop，代码与私有验证日志保留。git diff --check和新模块AST解析通过，无提交/部署、无工作树清理。下一批应先落完整file_delete逐项领域证据/可靠备份方案，包含删除后DB丢确认、批量部分完成、同路径新文件和备份清理/保留；不能将prepare/已备份/路径不存在当作已删除证明。然后接原VideoHandler的受理、计费/回调与真实VideoPart发布及通用consumer。真实付费原两Skill产图/页面验收仍需先估价并获新增确认，原模型供应商充值不等于授权。


## 接续开发：逐文件删除领域事实与视频单次提交（整套仍未发布）

用户最新明确要求优先完成剩余开发，不再反复运行已通过的大集合；本批仅新增代码的必要定向验证，付费/外部调用仍禁止，部署仍待全范围通过。继续同一工作树；主工作树不动。

356新增agent_task_file_deletions，沿原341/139稳定effect与354 native journal，不另造文件执行器。原PreparedFileCall/workspace写锁/当前确认保持。每个文件先在原deleted_files分配不可覆盖的独立OSS地址，native_status=allocated隐藏于恢复查询，但原30天purger照常清理（包括PUT返回丢失和未使用备份）；所有文件备份上传、meta ETag与原SHA确认后才armed/dispatch。逐文件再次核当前Actor/token/lease/原reserved native与原文件version，原inode原子rename到内部quarantine；真实identity+SHA后，逐项完成原恢复记录与领域事实。finally不再fire-and-forget新域记录。部分完成只核实实际quarantine并补事实，不自动再删未完成文件或同路径新文件，不把partial当整批交付。

逐项证据保留独立native/operation identifiers、settlement_token和armed状态，不以FK阻断原task/会话删除；来源消失后仅armed且匹配原事实的延迟回执可完成原恢复记录，不能获得新发送权限。正常Task删除和后续原工具恢复实际通过。356允许deleted_files.org_id NULL，原getter/355使用IS NOT DISTINCT和native_status JSON兼容读取，个人工作区删除及原恢复路径通过；已发布原111未修改。356回退全租户drain，保留private证据与清理地址，拒绝抹掉个人/null-org数据。

必要本地证据：首10新PG通过2.07s（/private/tmp/agent-task-file-delete-development.log）；后18 PG+7视频单元+32原filemixin共57通过2.39s（/private/tmp/agent-task-file-delete-video-targeted.log），含丢确认/丢返回、partial/new same-name file、备份失败原文件完整及每次唯一purge地址、源Task删除后仅事实settle、两文件间取消不再删、copy伪造不认领、跨租户回退FORCE RLS保留。随后个人2 PG及视频模型/价格时长冻结边界共11通过0.96s（/private/tmp/agent-task-personal-video-incremental.log，18旧已通过删除用例明确deselected，不重复）。重叠不累加。OSS仅本地Mock承载真实测试字节，未发送外部/付费请求。独立窄审只读代码和57日志，无新增高置信具体问题，未独立重跑。

视频已开发KieVideoAdapter的host opt-in单次提交接口：_chat_video_single_submit只异步，原create_task_once单post，dispatch/accepted observer前后衔接持久化；未知transport/ledger异常保留，不进旧重试包装；snapshot model/实际帧时长与计价时长一致才允许IO；旧generate/create/create-and-wait路径保留。当前这只是完成的SDK衔接段，尚未接通Task队列/信用事务/真实VideoPart发布，不能宣称视频闭环。原VideoHandler.start仍有unknown退款+Smart retry，旧TaskCompletion视频允许OSS失败临时URL降级，_close_task_turn也不能用来关闭已关闭的原用户turn，必须在managed路径保留不重复提交、原生账本和独立媒体发布边界。

继续开发位置：复用原tasks/messages/credit_transactions和现有BackgroundTaskWorker/FairBackgroundJobs，新增native视频受理/worker claim单次锁积分/dispatch/ACK绑定/实际供应商结果保存，再接严格持久视频成果发布（原Input/Turn不变）和原343/353同源自动续接，不能把任务号/临时URL当用户成果。当前新全局入口仍false、未提交部署。临时PG /private/tmp/agent-orchestration-pg.9yUXZd 仅本地Unix socket55439正在运行；本次未结束资源，不运行生产迁移。


本批资源关闭记录：自有PG /private/tmp/agent-orchestration-pg.9yUXZd（PID63146）已fast stop；前段“正在运行”仅为当时过程记录。无后台付费工作，未提交或部署；新模块AST与git diff --check通过，任务源码/私有日志保留。接续优先写357视频原生队列/信用事务及BackgroundTaskWorker实际衔接，随后VideoPart严格持久发布与原343/353来源续接。上批大集合不重跑；只有本轮更改边界需要定向补证。文件partial的剩余原项目接续仍未完成，当前只认事实不自动二次删除；不把SDK observer当作已完成视频Host队列。源模型供应商恢复额度仍不等于付费验收批准。


## 视频原生队列、持久成果与原 Actor 续接（本地开发增量）

本批继续原任务与implementation，不启动付费/外部供应商、OSS或ERP调用，不提交部署；新任务开关仍为false。357/358和Python适配已接入：实际generate_video的v3受理在原Actor/139/341 operation同一身份下冻结prompt/model/duration/价格/原turn/input/context revision，创建独立视频占位消息及原tasks子任务；受理仅accepted。子任务有宿主专属delivery_context标记，普通request_params不能冒充。原无状态/v1/v2视频入口不变。

原BackgroundTaskWorker/FairBackgroundJobs驱动队列，原media并发配额以child ID领取。claim原事务只扣一次积分并保存原credit_transactions pending、无自动过期；租约过期且没有dispatch marker才可回收同一领取、复用原账本。dispatch在原conversation控制锁下再核当前root状态、用户/组织/成员，marker仅一次；原SDK create_task_once仅单POST。暂停前不占并发；claim后暂停、尚未派发时释放并发且保留原pending账本供同一任务恢复，不误报失败或重扣积分。未知受理不调用旧Smart retry/create包装。

ACK丢失可由原已鉴权callback携带本地child+claim nonce绑定实际externalID，再沿原parser/TaskCompletionService核实。accepted超时转uncertain，沿原媒体平台承担未知费用政策，固定deadline到期后原发布事务仅退款一次，并保存_media_platform_cost_v1；不因退款获得第二次发送权。既有poller可核实已绑定的供应商任务。原stale清理跳过managed video，防止进入旧重复退款/重试路径。

供应商完成回执先落原task.result，不能以临时URL自称交付。strict owned storage固定用户/组织/child key、MP4字节校验、完整SHA、禁止覆盖、HEAD核对source task/etag/size；上传丢确认只认原对象不重复下载/覆盖。必须登记原ready user_asset及真实task/message ref后，原发布事务核资产身份/SHA、确认或退款原积分、保存VideoPart与原conversation revision；不改原input/user Turn/上下文锚点或侧栏。控制器read/import/complete重新核原proof、当前scope/Policy与owned对象meta；目标种类保持file或receipt。持续存储失败尚需保留settling核实，不能退化为临时URL成功。

358视频事实复用343原内部Actor队列及353完整CP交接；后台consumer实际用原AsyncLocalDBClient单连接池重新绑定每个源任务actor/org，重复扫描去重，事务结束身份清空。原历史、opaque字段、要求/目标/冻结执行身份保留，不加假user输入。paused/cancelled/new input均不强行续接；事件只提示核实，不自授成功成果或发送权限。

证据：首12项SQL边界通过（/private/tmp/agent-task-video-queue-development.log）；新增视频定向初35项2.12s，后39项2.29s（/private/tmp/agent-task-video-native-targeted.log），最终新增两项worker真实SDK本地替身边界及修订的原权限模式断言后26 PG+6 storage/worker=32项2.23s通过（/private/tmp/agent-task-video-incremental-final.log）；SDK9项沿未变代码复用。实际原连接池consumer替换手工enqueue后的8项1.40s通过，18项明确deselected（/private/tmp/agent-task-video-consumer-final.log）。视频用例去重为41项，包含两种checkpoint owner；不是41次外部业务验收。

受影响兼容性集合共195项运行，193通过/2个新视频测试断言失败（/private/tmp/agent-task-video-current-regression.log，5.29s）；失败是测试错误假设ask模式对generation必然另弹确认。原ToolPolicy允许此类已请求生成；生产权限逻辑未改为满足测试，改用原plan模式禁止生成的正确断言后上述32项通过。该批其余156项旧路径全部通过：scheduled原生续接、等待CP、ERP生命周期、旧bridge/context、webhook、video retry、completion routing与background worker。与新用例集合重叠不相加，不重跑无变化大集合。

独立只读窄审发现1项P2：claim后组织被停用时dispatch漏核organizations.status；现与claim同样核组织active，并有真实PG负例证明无marker、未发送路径退款一次。其余暂未发现高置信问题；审查未独立跑测试或外部验收。早期本地验证也修正357 trigger/rollback实际表owner权限及PL/pgSQL变量歧义，未扩大运行时权限；asset fixture对齐已登记bootstrap实际owner/RLS，未添加测试专用运行时越权grant。

整套仍未完成：文件partial剩余项的原授权接续、其余ERP端点/异步及部分失败语义、用户要求修订/多入口/UI和原两Skill真实产图/页面验收仍需开发或验证。只有整套通过才部署；原供应商充值不等于付费授权，收费前仍先报预计费用并等用户确认。下一步优先完成partial剩余项接续与ERP逐方法结果协议，不反复跑上述旧集合。

本批结束资源记录：自有临时PG /private/tmp/agent-orchestration-pg.9yUXZd 已fast stop，保留源码及私有日志；未改生产服务。新视频模块AST及git diff --check通过。继续位置：文件partial原授权剩余项接续、ERP逐方法最终/异步协议，再做目标修订/多入口/UI和原两Skill整体验收；禁止自动收费或提前部署。


## 原文件批次剩余项续接与ERP嵌套回执（2026-10-31）

本批继续原任务，未提交部署、未调用模型/图片/视频/ERP/OSS外部服务。359与file_delete_resume.py补齐356 partial剩余原版本的正常工具续接：同一逻辑effect保留原139失败/uncertain结果和原领域回执；新call必须与原完整规范化参数hash相同，继续沿原ToolRuntime当前身份/Policy/资源解析/确认/workspace写锁，再登记一条真实139调用和宿主内部关联。不把read control变成写操作，不重造原调用成功或第二套文件执行器。

已完成项先只核实原quarantine/原inode/SHA并补原事实，从实际prepared targets排除；同路径新文件、已恢复的原文件均不再次删除。剩余项必须是原manifest完整version+SHA，原OSS备份当前ETag相同、原恢复记录未purged/未过期。实际执行前逐项SQL重核当前Actor/task/root/revision/token/租约/成员与组织、原uncertain effect、新running native和原armed备份；只rename确实还在原位置的原版本到私有quarantine，然后沿原独立settlement记录事实。无再次备份上传、无猜测路径不存在即删除成功。原命名空间空目录被已完成事实清理后，剩余项执行在原写锁下安全重建原private目录。

同一续接call已保存succeeded时直接重放，不重新确认/删除；running/uncertain不重派。pending batch的recovery_only允许重放已记录成功的新续接call，但notfound拒绝新发送。新call保存结果与原engine历史成对交接保持；原TaskState仍由随后read_task_write_result读取实际全批回执、import及complete_step推进，不篡改旧native。359关联表跟随新原生调用删除，不阻断原Task/会话删除；356原备份/领域事实独立于Actor生命周期继续保持。

read_task_write_result在部分完成时给出无private token的resume说明、原call/owner和剩余路径；模型应沿原完整参数用新call_id继续，宿主仍会重新校验。缺失原参数不能靠拼目标或忽略args hash授权。确认UI使用resource_versions实际剩余文件展示数量/路径；canonical call和完整confirmation binding仍保持原参数与实际准备版本。用户看到的是将真正执行的剩余目标，已完成位置的新文件不显示成待删对象。普通文件删除仍显示原完整目标，无新UI确认协议。

验证：新18 PG通过2.29s（/private/tmp/agent-task-file-resume-development.log）；相关新旧文件恢复/删除、原filemixin/context/model bridge 128通过6.51s（/private/tmp/agent-task-file-resume-current-regression.log）。随后原deletion_db真实安装359、旧partial断言升级为允许正常新call续原剩余版本且保留同名新文件，新旧38项通过4.26s（/private/tmp/agent-task-file-resume-final.log）。最后确认中改文件、实际异步store begin之后撤member/过期、扩参数以及原partial指示/secret不泄漏共10通过1.87s（/private/tmp/agent-task-file-resume-fence-final.log，36 deselected）；pending batch重放2 PG通过0.99s（/private/tmp/agent-task-file-resume-pending-batch.log，26 deselected）；实际WS确认只展示剩余目标2 PG通过0.95s（/private/tmp/agent-task-file-resume-confirmation.log，28 deselected）。两种CP owner均覆盖，集合重叠不相加，不代表外部NAS/OSS或整套验收。

测试期间修正两处fixture断言/注入：expired应保持原ConversationStopRequested，不强行包装普通错误；派发后竞态须注入实际Async store而非未被该RPC使用的同步executor DB。新pending replay位置问题已修正后两种owner通过。新增原目录存在性恢复修复没有改变文件版本断言或放宽授权。最新普通确认/文件边界回归另见/private/tmp/agent-task-file-resume-original-confirmation.log。

ERP本批继续收紧真实原始回执解析：receipt_needs_follow_up在有界遍历中识别嵌套批次失败/异步task、字符串非零或未知failCount、partial及显式子项失败，避免顶层success掩盖部分失败。只阻止提前completed，不授予重发、不把异常计数推断成成功。已准入方法的正常同步回执/零失败计数保持原完成行为，其余方法仍仅accepted，逐方法最终/异步核实协议尚未全部接好。ERP38单元+18原生PG=56通过1.73s（/private/tmp/agent-task-erp-receipt-shapes.log），HTTP均本地替身。

独立只读窄审先后核359核心及最后recovery/确认显示/ERP防提前完成增量，未发现新增高置信漏洞；未独立重跑或外部调用。新全局入口仍false。整套尚未完成：其余ERP方法最终/异步业务适配、用户要求修订/多入口/UI整体验收，以及原两个已发布Skill真实产图/页面验收。付费测试仍先估价等确认，整个范围通过后才部署；不发布新测试Skill。

本批收尾：原file_target_execution/file_tool_boundaries/chat_tool_mixin共141通过4.24s（/private/tmp/agent-task-file-resume-original-confirmation.log），只因本轮确认展示接入补此受影响集合，不重复千项旧验证。新模块AST、库存JSON与git diff --check通过。自有PG /private/tmp/agent-orchestration-pg.9yUXZd已fast stop。未提交/部署/清理工作树。接续优先逐方法ERP终态/异步核实适配；completion allowlist仍只有erp.item.tag.add，不能把本批回执形状防护冒充全部ERP闭环；随后继续目标修订、多入口/UI及原两Skill真实验收。付费仍先估价等确认。


## 360 ERP写后核实与异步触发分类（2026-10-31）

继续原任务树，复用此前文件/视频/续接证据，本批未跑付费测试、未调用外部ERP/OSS/模型、未提交部署。新增erp_postconditions.py及360/rollback；沿原139真实只读调用、354原派发/回执和原TaskController/CP闭环，不新建ERP客户端、调用队列或单独状态平台。

新完成协议仅准入item.general.addorupdate的纯商品标题场景：原参数必须恰为outer_id/title，原dispatcher映射后的业务字段也只能是outerId/title，值完全对应（既有mapper固定添加的分页字段不算新增业务字段）。有SKU、价格、其它字段、未知被忽略字段或不同别名不据此完成整项写入。宿主在原受控dispatch阶段固定product.title期待；原API返回只accepted，不因success扩充COMPLETION_METHODS。

v3原ERP只读dispatcher对真实精确item.single.get响应投影outerId/title及来源指纹，进入原ToolOutput/139不可变结果。沿既有single_code_only detail分支，只接受一个原编码/一个outerId查询，不把宽泛、多目标、分页拼接或不明嵌套包裹当该协议。旧非v3/非opt-in结果metadata不加字段。原始响应中的partial、错误计数、异步等仍不能完成期待。

360读取当前fence下原ERP proof，再选同actor/org/conversation、原root或同源continuation的真实读取账本：读取started严格晚于原write returned，原native succeeded、实际handler_started且非cached/nonreplayed/cancelled，取最新同商品查询。Python再核原audit调用ID/hash/actor/workspace/org/conv/时间和精确标题；读写两侧当前ToolPolicy均重新检查。read_task_write_result给出真实详情查询follow_up；有pending ERP effect时该product_detail调用只绕过结果cache，原权限/资源/调用账本重放仍保持，避免把写前旧缓存当新核实。没有自动后台额外查询，仍是模型按原工具执行的正常受控只读调用。

实际匹配后，控制器沿原record_operation/receipt import/complete_step及CP事务推进。receipt hash纳入真实readback证据，导入和完成重新取证；较新真实读取已变化则不能完成旧证据。原写HTTP只有一次；原139成功/失败/uncertain payload、历史和opaque上下文均不改写。写已受理但格式化返回丢失可通过原回执+真实后续读取推进；完全没有受理回执的unknown，即使后来商品恰好同标题，仍不被此协议假定已受理或允许重发。

来源边界：实际client的org/appKey/appSecret/current accessToken/baseURL只参与SHA256，持久proof/投影仅保存64hex指纹，不保存凭据原值。写前指纹与真实读返回后指纹必须一致；账户切换或session刷新不能串回执。原自动token刷新代码不变；在尚无稳定ERP账户身份协议时，跨refresh保守保持accepted，属于新自动完成能力的明确限制。检测到指纹变化会返回requires_connection_verification说明，提示停止盲目反复查询/写入、保留原受理事实等待身份核实；不伪造一次账户切换授权，也不阻断用户独立获准的普通读取。后续需稳定账户标识才能安全放开跨session认领。

修正既有分类：purchase_strategy_calculate原描述明确触发异步计算但is_write缺省false。现登记is_write=True，原method/参数功能保留，改从erp_execute category=purchase/action=purchase_strategy_calculate经原危险操作确认、nonce和单次写发送；纯erp_purchase_query、execute_raw/沙盒不能再触发。旧只读调用的拒绝是有意的权限收紧，描述明确新入口。purchase_progress和purchase_strategy仍是原查询；没有可关联当前计算实例的原始终态协议时不把全局进度猜成某次成功，不宣称该异步终态适配已完成。

验证：首29项2.57s通过（/private/tmp/agent-task-erp-postcondition-targeted.log），连接指纹加入后29项2.57s通过（/private/tmp/agent-task-erp-postcondition-source.log），新增源切换/参数/导入后变化等39项3.12s通过（/private/tmp/agent-task-erp-postcondition-final.log）。新旧受影响组合295项中294通过6.44s，唯一新采购route测试错误使用旧context.call_id生成确认绑定；生产原确认逻辑未改，修正测试同call ID后1项0.45s通过（/private/tmp/agent-task-erp-calculation-route.log）。该组合的255项旧读/写生命周期、原真实PG、ERP mixin及生产多入口回归均通过，未重复跑千项旧集合。

随后真实写受理但格式化丢失、完全unknown不误认领，共6 PG通过1.31s（/private/tmp/agent-task-erp-postcondition-recovery.log，37 deselected）。最后source_changed不建议重复IO及正常/格式化丢失，共6 PG通过1.31s（/private/tmp/agent-task-erp-postcondition-source-final.log，18 deselected）。原采购参数/全注册表结构19项0.43s通过（/private/tmp/agent-task-erp-registry-compatibility.log）。新协议去重44项（20单元+24 PG、两checkpoint owner）分增量通过；集合重叠不累加，不等于44次外部验收。测试中domain须真实erp、撤member保持原persistence-stop异常；均是fixture修正，不放宽生产身份检查。参数知识提取代码已永久retired=True，新增未知参数用例另显式替换记录器，避免任何后台提取；无模型请求。

独立只读窄审先后核360核心及源身份/采购分类，未发现高置信泄密、跨账号认领或权限绕过。未独立重跑、未访问外部。新版入口仍false，整套仍未完成：剩余ERP逐方法/批量/异步实例终态与背景唤醒、稳定ERP账户身份、用户目标修订、多入口/UI及原两个已发布Skill真实产图/页面验收。付费仍先估价等确认，整套通过后才部署，不创建新的测试Skill。

本批收尾：自有临时PG /private/tmp/agent-orchestration-pg.9yUXZd已fast stop；新模块AST、库存JSON及git diff --check通过。原模型/图片/视频/ERP/OSS没有外部请求或收费；未提交/部署/清理工作树。接续优先解决稳定ERP账户身份及其它逐方法/异步实例最终回执，不能靠新增COMPLETION_METHODS放行未核实接口；继续补用户目标修订与多入口/UI/原两Skill整体验收。最新source_changed提示只是可信host跟进说明，原用户独立授权的查询权限不被篡改；普通循环仍受原任务预算限制。收费验收需先报价等用户确认。


## 361 ERP正常刷新后的原任务核验（2026-10-31）

本批延续360原后置核验链路，未新建客户端体系或业务调用。原KuaiMaiClient._do_refresh_token可选接收token_rotation_recorder，在原provider refresh、cache及token持久化后，只有success严格为布尔True、新accessToken为非空str且前后实际来源指纹不同才记录关系。SDK原宽松返回兼容、刷新锁、缓存加载、token persister保持；观察记录失败/两秒超时只留下不带凭据的错误类型，不把原刷新改为失败，也不自动补发业务。原已有scope客户端与池复用，不新建生产连接。未记录/缺链时只能待核实，不能推断账户相同。

361新增按org的不可更新old/new SHA256关系表，原始凭据和供应商响应不落表。RLS限当前active组织和原runtime成员或原worker范围；会话记录RPC只允许应用数据库身份同租户调用，重复同边不刷新时间，分叉拒绝。只跟随最多32条时间不逆序、不晚于实际读回执完成的无环关系。360只读RPC增加source_rotations，Python逐边再核tenant/指纹/时间/终点；只有非空关系才纳入receipt hash，原同session回执格式不变。import/complete仍通过原真实readback、当前读写Policy、Actor fence及CP事务复核，旧139业务回执/历史/opaque保持。

接入当前聊天ERP、单worker、pool、dead-letter和连接测试的原org客户端工厂。回调只接受已知同步/异步本地数据库client或对应scope门面；不更换runtime actor/org，只有原tenant-neutral actorless worker能收窄到已加载org。个人客户端、未知transport和dedicated SYNC scope不伪造观察身份；这些未支持场景、跨手工换号/缺失记录/超32跳仍保持待核实，不能宣称已取得供应商稳定账户ID。连接测试沿原owner/admin检查；正常FastAPI依赖仍原样注入，显式旧函数调用缺可选resolver时不影响原测试功能。

验证证据：新SDK/原生PG首23通过2.68s（/private/tmp/agent-task-erp-session-rotation-development.log）；收紧成功判断后，23新用例+360原44项合计67通过5.32s（/private/tmp/agent-task-erp-session-rotation-final.log）；随后新增边界23通过0.95s（/private/tmp/agent-task-erp-session-rotation-boundaries.log，23 deselected）。361去重47项：21单元+26真实PG，覆盖两checkpoint owner、1/2次真实SDK模拟刷新、后台async scope到另一连接读取、手工换号/错租户/未来记录/权限撤销/replayed/gap/cycle/超过32跳/逆序拒绝、同边幂等/不可改/分叉拒绝与跨租户回退drain。所有供应商HTTP/Redis/凭据存储均本地替身，不是外部业务验收。

原SDK刷新/多租户不变量、pool及dead-letter客户端28通过0.53s（/private/tmp/agent-task-erp-session-rotation-sdk-compatibility.log）；原连接测试/owner-admin拒绝4通过0.47s（/private/tmp/agent-task-erp-session-rotation-route-compatibility.log）。首次混跑因既有erp_tool_mixin_unit模块级sys.modules替身污染org resolver，collection中止；拆进程后SDK及真实route各通过，不修改原生产resolver。修复新增聊天工厂未定义org_id为self.org_id，连接测试可选observer对未显式注入旧dependency兼容。独立窄审发现P2 truthy-success可能伪造lineage，已只收紧新证据准入并补5负例，不改变原SDK返回规则。

剩余范围：其它ERP逐方法/批量/异步实例最终回执及背景唤醒、用户目标修订、多入口/UI及原两个已发布Skill真实产图/页面验收。现有采购进度没有当前写入实例ID契约，不拿全局progress冒充实例完成；不扩COMPLETION_METHODS放行未知API。整套未完成；全局入口仍false，未提交部署、不发布测试Skill。本批无付费/外部模型、图片、视频、ERP、OSS请求。收费仍先估价等确认，整套通过后才部署。


361收尾补充：增加原聊天org工厂真实成功分支测试，确保实际self.org_id、原token_persister与scoped observer同时保留。session_rotations可读性整理和未知响应类型检查后，原真实1/2跳、后台async及工厂/scope共8项通过1.25s（/private/tmp/agent-task-erp-session-rotation-factory-final.log，39 deselected）；新增总数47（21 unit+26 PG），只复核受影响集合，不重跑千项。独立最后复核关闭truthy-success P2并确认factory/route兼容，无新增高置信发现；未独立重跑外部验收。150个本任务新/改Python文件AST、库存JSON与git diff --check通过，不代表整套运行时验收。剩余工作和付费限制按上节；工作保留原任务树，无提交/部署/主树修改。

本批自有PG /private/tmp/agent-orchestration-pg.9yUXZd已fast stop。接续从本记录361后的剩余范围推进，复用有效旧证据；不要再次构建/发布测试Skill，不在缺实例协议时猜ERP终态，不擅自恢复收费验收。


## 362真实任务进度展示与恢复读取（2026-10-31）

本批先核原ERP注册表：当前采购计算只提供progressType全局进度，没有同次写入实例ID/最终回执契约；不把其它任务的完成套用到本次受理，也不新造自动轮询或扩大COMPLETION_METHODS。该部分继续保留accepted/uncertain及原业务功能，不能称全部ERP终态已适配。随后推进已规划的用户可见进度批次，付费验收暂停、默认入口false、无提交部署。

新增progress.py从原TaskState生成白名单投影：root/revision/逻辑status、步骤id/title/status、实际完成步骤/交付数量、accepted及reserved/uncertain计数；不返回原上下文、完整goal、args、invocation、receipt、资源、模型私有推理或执行token。原step结果和真实效果状态决定显示：accepted仅“已受理，等待结果”，reserved/uncertain仅“结果待核实”；助手消息completed不等于逻辑任务completed，不计算虚假完成百分比。

362只新增read_agent_task_progress SECURITY INVOKER只读函数，无新状态/队列/派发权。限定当前应用runtime actor/org、active用户/成员/组织、原user会话及原workspace owner；参数assistant消息必须由原root/当前owner或同逻辑根的真实续接chat Task关联。用户消息、同会话另一root助手、错租户/actor、撤权和channel scope拒绝。读取不要求已经结束的Actor窗口继续持有lease，不改变原CP/state/revision。回退直接删除只读函数，无写债务/迁移状态。真实DB/RLS仍为授权事实，marker不是授权。

原完整CP里保存最小task_progress discovery marker（只有root UUID），在新v3 coverage核对通过或原控制批次中随历史/状态一次保存，成功后才经原sink发布。保存失败恢复原blocks/messages/state且不发marker；恢复不改旧ContextAnchor。新ContentPart贯穿后端typed outcome/序列化、原WS事件与前端历史协议；前端丢弃marker中夹带的status/state等字段，user上传的marker不显示。旧v1/v2消息和channel业务不新增此个人UI标记；channel专用展示仍未完成，不把channel身份降为user。

新GET /tasks/{root}/execution?message_id=...沿原OrgCtx/ScopedDB认证，HTTP no-store，只返回投影。TaskProgressCard复用原消息布局显示整体状态、实际步骤/交付计数和可展开步骤，不新增确认/执行按钮；原暂停/取消/继续不改。只在进入视口且页面可见时读取，active每5秒、其它待办每30秒；全部完成/失败/取消停止定时，401/403/404明确拒绝停止自动重试。暂时网络错误清除旧成功且有界间隔再读，用户可手动刷新；没有模型或收费后台请求。局部AbortController+清理标记拒绝旧响应回写，keyed child按root/message切换清旧状态，revision不倒退。

验证：首新7unit+16真实PG后，加原model bridge/coverage/outcome/message schema，99项通过1.35s（/private/tmp/agent-task-progress-backend-regression.log）；随后新8unit+28PG共36通过1.08s（/private/tmp/agent-task-progress-boundaries.log），覆盖真实org撤member/停组织、runtime/worker scope、同消息与不同root绑定、白名单、覆盖审批保存/原历史保持。实际HTTP到真实本地PG两CP owner的2项通过0.64s（/private/tmp/agent-task-progress-http.log，28 deselected），验证200 no-store/无state原文和错误消息404。362新增去重38项（8unit+30PG），分增量验证，未重复千项旧集合。

前端首57（新card5+原MessageItem/ContentBlocks/protocol）通过1.86s（/private/tmp/agent-task-progress-frontend.log）；新增protocol白名单/非法ID后20通过（/private/tmp/agent-task-progress-protocol-final.log）；消息历史/assistant派发与user marker拒绝14通过1.09s（/private/tmp/agent-task-progress-message-integration.log）。新Card覆盖受理非完成、后续真实完成与停止轮询、撤权清旧状态、离屏/隐藏页面不查询、旧异步响应丢弃；最后keyed child改后5通过0.38s（/private/tmp/agent-task-progress-card-final.log），新增明确404停止自动重试/消息切换1通过0.40s（/private/tmp/agent-task-progress-denial-final.log）。前端新9项分增量通过，旧集合重叠不相加，仍非浏览器视觉/供应商验收。

首PG channel负例直接改state触发原CP一致性guard，测试修正为同事务同步原CP，生产guard不放宽；新增coverage测试误引不存在的setup，改用原staged fixture后通过。ESLint发现新card同步effect setState，已改为按来源key卸载旧子组件，零error；仅保留原MessageItem genParams依赖warning。TypeScript与生产build/postbuild通过（/private/tmp/agent-task-progress-build-final.log，Vite11.14s）；仍有既有大chunk提示，未改包依赖或为消警扩大重构。独立只读核对本批身份/消息关联/保存后发布/前端竞态未发现高置信问题，不替代真实供应商或整个系统安全验收。

剩余：其余ERP逐方法/批量/异步实例最终契约与背景唤醒、可信后续用户目标修订、channel与其它多入口/视觉UI验收、原两个已发布Skill真实产图与页面验收。新progress UI只完成个人聊天路径本地机制，不宣称完整产品已完成。继续复用有效旧结果，不发布新测试Skill，收费先估价等确认，整套通过后部署。

362收尾：独立最后只读复核关闭keyed child/明确拒绝停止/marker scope增量，无新增高置信问题；未独立重跑或外部调用。155个任务新/改Python AST、库存JSON、git diff --check通过。自有PG /private/tmp/agent-orchestration-pg.9yUXZd已fast stop，未启动Redis/生产进程，未修改主树或包依赖。接续优先可信后续用户要求修订（须保留旧要求/成果/效果并拒绝模型自授权新版本），同时继续补有真实实例契约的业务和多入口；缺采购实例ID不能靠全局progress假定完成。当前无提交/部署，收费仍需先报价等确认。


## 363可信追加要求的持久输入与交接基础（2026-10-31）

用户授权继续优先推进“中途修改要求”。本批查明原Actor STEER只将字符串加入内存队列，随后确认原conversation_control_events；原文尚未进入下一份CP时若重启，或父owner闭合后交给continuation，该输入可能已ACK但未交给模型。原通道242和真实事件复用，不新建用户任务/消息队列、不将附件/模型输出当用户修订。此批仅关闭可信输入保存/恢复缺口；语义审查、要求版本应用和旧派发拦截尚未完成，不能称中途修改付费计划已经安全可用，不部署。

新增user_changes.py typed inbox：root固定，每项原event/owner/message/SHA和delivered；原文最多20k字符、32事件/256k UTF8；同事件不能改正文，delivered只能单向。保留真实原多行文本及digest，不把普通换行缩进当无效新指令。delivered只表示交过原模型上下文，不表示核验通过/允许改要求或增加操作版本。无新要求写控制工具。

363 capture RPC以原task行锁、当前execution_token/lease、runtime actor/org、active用户/成员/组织核对；从真实同task/conv/turn的steer事件取原文。将最小inbox写入原完整conversation_turn_checkpoints，同时标原事件applied/原ACK字段，单事务提交；写失败则事件仍pending，丢返回可按原event重取。保留原CP全部messages、opaque及_task_execution，requirement_revision和goal/效果不动。新checkpoint trigger对所有原写入口检查来源真实、root/owner关联和旧项prefix不可删改、delivered不能回退。源NULL/缺失消息显式拒绝，不能被SQL三值逻辑放行。触发器按原CP受控owner安装，guard execute只授app和原owner，覆盖两种owner。回退须全租户排空含inbox的CP，失败不改变原FORCE RLS；不删除已ACK来源。

ConversationTurnRuntime仅新v3或新coverage bootstrap走捕获路径；capture成功后才把真实canonical来源加入原steer内存队列，原事件不二次ACK。consume沿用原模型user文本注入，设置delivered并随下一份完整CP保存；多事件按原到达顺序，同事件传输重放不重复注入。旧v1/v2/普通运行时继续原通道，无新增metadata。capture失败/无可信source不ACK、不当作原要求已经处理。

Executor恢复inbox并重新投递undelivered，既有ContextAnchor/输入消息/权限/预算不变。普通恢复默认拒绝跨owner未交付；只有当前真实task.delivery_context标为同root agent_task_continuation时，才允许继承旧owner的真实来源，不篡改owner/event/delivered。独立窄审发现这一P2已修：旧owner末安全点捕获新文字后闭合，新continuation不得因owner不同丢输入。shared build_context保留inbox；真实图片齐全但输入尚未交付时，保留active让模型读取。原commit_ready恢复有未交付输入时也不快返旧结果，_run_loop只重开delivery窗口的status/revision，不修改要求、预算、操作、成果或原账本。

本地验证：初新3unit+14PG=17通过0.98s（/private/tmp/agent-task-user-changes-development.log）。真实原343图片等待→末安全点原242 STEER→capture/ACK→原owner闭合→enqueue/claim/load/apply→同root新owner原文一次consume，两CP owner通过；实际AsyncLocalDBClient scoped capture丢返回重取、跨租户rollback排空保护共6 PG通过0.98s（/private/tmp/agent-task-user-changes-recovery.log，14 deselected）。未创建第二个image task、未重发图片。原文单次交付指已保存检查点恢复语义，不承诺模型网络采样在所有崩溃中不会重新计费。

原Executor/命令/Store/CP/bootstrap/model bridge加新unit79项通过0.94s（/private/tmp/agent-task-user-changes-original-regression.log）。后续新6unit+26PG及原图片54/原receipt28续接共114通过9.46s（/private/tmp/agent-task-user-changes-final.log），覆盖bootstrap尚未冻结要求、真实组织撤member不ACK、两CP owner、新旧continuation完整历史/成果保持。最后多行原文digest兼容新增后7 unit通过（/private/tmp/agent-task-user-changes-text-final.log）。363新增去重33（7unit+26PG）分增量通过，重复集合不累加，不是外部模型/供应商或完整项目验收。

测试问题如实保留：初PG原138表定义需uuid-ossp，给本任务新隔离库安装本地extension后通过；源NULL负例暴露SQL三值遗漏，修复生产guard并确认失败不ACK；teardown须先删除测试CP+对应state并结算原deferred一致性事件，再执行rollback，不放宽生产guard。恢复测试误用CP status=active，按原契约改ready，不改变产品状态机。独立最后复核关闭跨owner P2，无新增高置信恢复缺陷；没有运行付费或外部调用。

本批不是完整用户要求修订：尚缺隔离的continue/amend/unrelated关系核验、可信新要求及旧/新版本保存、过期规划/事实审查/阶段交接失效、派发前新输入的事务阻断，以及新用户Turn接续等。尤其不能把steer event UUID塞进source_message_ids冒充真实messages，不得简单提升requirements_revision或放宽旧CP不可变检查授予新效果；后续须绑定真实事件来源并保留所有旧成果/受理/uncertain效果。先完成这些，再宣称能安全按新要求修改执行计划。其它ERP终态/多入口/原两Skill真实验收继续按前记录，默认入口false、无提交部署，收费须先报价等确认。

363收尾：本任务158个新/改Python AST、库存JSON与git diff --check通过；自有PG /private/tmp/agent-orchestration-pg.9yUXZd已fast stop。未改主树、未提交/部署/收费。下一批优先继续真实追加要求关系核验和要求版本/派发屏障；保留本批来源及待交付标识，不能把delivered解释为已应用要求变更。

## 364/365 要求修订与派发保护增量（2026-10-31）

本轮继续原任务树，新增requirement_changes语义提案/隔离观察/有界修复和364/365迁移，具体契约见TECH同节。继续与旁支不增加要求版本；amend才归档原要求和计划并生成rN当前要求。旧成果、真实原文来源、效果/受理/unknown记录、预算和身份不删。原CP保留messages及opaque extensions；清除旧commit_ready完成字段，delivered但未review的输入也不能走Executor旧结果快返。保存失败还原内存state/Skill/审核/messages，不用未确认状态派发。原阶段只重开本次适用、手选/固定方法，容量不足进入原Skill控制协调；不移除受保护来源或增加容量。新来源能重新核验，原来源三次失败持久化waiting_input，不空转耗剩余模型预算。

独立审查发现并修复：SQL payload夹带未提案图片要求；365视频logical→conversation锁序倒置；365回退漏drain；修订时active/runtime/手选来源可被偷偷删空。最终协议按真实proposal/pin重建精确集合并校验观察种类/数量/来源；original两发布版本PROFILES的Python/SQL投影与篡改负例在本地验证，不新建生产Skill。另由原video marker实际测试查明operation_id保存在delivery_context，已按357真实结构修正。原动态方法扩展在rN后沿原固定profile guard验证，旧图/视频结果在当前交付完成后仍走原consumer，不把历史pending抹为已完成。

验证逐次结果不累加：302项受影响原unit/Actor/CP/Skill/Reach/工具引擎集合通过3.93s（/private/tmp/agent-task-revision-original-regression.log）。随后新版本方法扩展及原两pin对照、当前新增和原方法/输入共95通过3.32s（/private/tmp/agent-task-revision-final-targeted.log）；这两个集合重叠，不称397去重。后补旧媒体收尾分支与原image/model bridge/video单元共59通过1.05s（/private/tmp/agent-task-revision-media-settlement.log）。逐项原版本续删在新要求到达最后authorizer时拒绝，无第二次备份或删除，4个两CP owner用例通过1.20s（/private/tmp/agent-task-revision-file-resume.log）。最新PG结果见本节收尾。

真实本地PG采用两种原checkpoint owner，保留原242 STEER、139 invocation、343续接及原视频/文件账本；双连接测试实际等worker阻塞于conversation后再锁logical并写STEER，无死锁/无dispatch marker。组织child gate正常及错误返回后scope不变。文件首项已完成后到达新要求，第二项保留原字节，旧settlement及备份仍保留。新SQL/rollback在本任务隔离库执行；没有供应商、模型、OSS、ERP或生产数据库调用。

尚未完成：新用户Turn交接、历史原图片绑定到修订后交付的受控复用及完整恢复端到端验收、其余ERP逐实例终态/多入口、原两Skill真实产图/浏览器验收。不得因本批通过宣称整套完成或保零故障；不启用默认开关、不提交部署。继续复用旧证据，收费先报估价等待确认。

364/365收尾：最终SQL数量/动作/引用复核与全部新native gates组合54个真实隔离PG用例通过3.76s（/private/tmp/agent-task-revision-persistence-final.log），两CP owner；覆盖原两pin投影与动态新增方法、extra delivery/observer/pin/manual/runtime负例、图片/视频marker及scope/锁序、文件每项/原版本恢复续删、回退排空。补充单元59通过日志见上，新增去重17unit+54PG=71，本轮既有/新混合集合不累加。166个任务新/改Python AST、库存JSON与git diff --check通过；自有PG /private/tmp/agent-orchestration-pg.9yUXZd已fast stop。没有修改主树或包依赖，无付费/外部业务请求/提交部署。

下一批应优先接续新用户Turn和历史真实图片对修订后交付的受控复用，再验证“原两Skill规划→真实产图→用户改要求→仅补所需成果→原上下文继续”的完整原入口恢复链路；未获费用确认前只做本地开发/替身传输验证，不调用原供应商。其余ERP缺实例ID协议保持待核实，不为缩短待办把全局进度当单次操作完成。默认入口仍false，整套未通过，不得自动发布。


## 366/367 新用户Turn接续与真实成图复用（2026-10-31）

本批沿原Executor/Actor/完整CP接新真实用户Turn，不新建编排系统。只读取同会话、同actor/org的最近可接续个人serial v3任务；分支、暂停/取消/失败、无剩余模型预算、已排队续接或不同明确手选Skill不抢占。先用原模型轮做无工具的continue/amend/unrelated/uncertain观察，逐字证据须出自真实新文字；不相关/不明确保留独立任务。该轮会使用原模型预算，不能将“图片未收费”解释为整轮免费。本轮测试观察均为本地替身，无模型请求。

367以真实输入消息、source CP hash/revision和当前lease作CAS，conversation→current/source owner→logical锁序，通过原save_agent_task_checkpoint转交同root owner；只重开交付窗口并增加实际模型轮，不改原goal/要求/预算上限/成果/效果。原messages与新窗口messages均精确保留；opaque字段保留，之前的新窗口归档。原手选/session固定包和版本不换，仅重绑定turn/composition窗口。新输入同事务进入原363真实STEER inbox，仍须364要求审查，未核验不能沿旧派发。丢确认仅重试本地收养事务，幂等返回，不重发业务。真实Executor测试覆盖重新prepare、当前ContextAnchor/Policy与旧SkillRuntime恢复。

366沿原347核实受理、原343图片consumer结算：原图在新消息前闭合可在当前冻结历史内收取；晚于新消息才完成则等原consumer发布/唤醒，不跨ContextAnchor提前读结果，也不购买替代图。独立文字/只读可继续；被未决旧效果挡住的付费工作等原结果。适用的历史video/scheduled依赖沿原队列等待，不伪造完成。

新增reuse_task_image及generate_image预留前自动检查，先走原参数schema、原尺寸/当前文字意图、当前完整规划和原图资源绑定。匹配真实已发布child、完整正负提示词、比例/模式/像素约束、引用字节hash和角色顺序；显式variant须一致。实际旧图经当前权限和原内容定位重新解析，SQL核验消息已发布、冻结历史、原child/result/hash、当前要求/规划/slot/lease；不能把一个旧child凑多个当前slot，已完成slot不能再预留。成功保存当前typed reuse proof后才发布ImagePart，不新增image task/effect/图片费用。未知/失权不能自动花钱买替代；真正不匹配才保留原原生生成路径。原未绑定版本和不支持协议不被替换。

范围限制：当前直接canonical generate_image匹配支持同一稳定variant；source_task_id再生成、legacy ready-plan/final-prompt/source协议尚不走此复用适配。原legacy_message/ecom_plan导入和原执行保持，但原Task结构化规划跨阶段精确选取、旧方案逐项成图复用仍需后续针对原两Skill链路验收，不能宣称所有入口已免重复生成。语义观察为模型判断，不保证永不误判；确定有来源、版本和副作用边界。

本地结果（重叠集合不累加）：受影响回归203项通过10.10s，/private/tmp/agent-task-followup-reuse-validation.log；其后补原schema、variant/current-slot约束后最新23unit+42PG=65项通过2.78s，/private/tmp/agent-task-followup-reuse-contract-final.log。PG两种原CP owner，覆盖原child晚到→原343 enqueue/claim/load/apply→保留新目标版本→复用、真实Executor丢确认重启、来源篡改/旁支/手选pin/重复slot/尺寸拒绝/权限与冻结历史/rollback drain。供应商结果与资源解析为本地替身，不是原两Skill真实产图或页面验收。独立窄审发现SQL JSON运算优先级、追加消息精确集合和精确像素绕过，均修复并补负例；末次复核无新增高置信问题，不代表整套完成。

本轮未收费、未提交/部署、未修改默认入口false；后续仍需剩余ERP逐实例终态、多入口/页面、原两个Skill全链路恢复和真实供应商验收。付费先报价等确认，整套通过再部署。

366/367收尾：170个任务新/改Python AST、库存JSON和git diff --check通过；自有隔离PG /private/tmp/agent-orchestration-pg.9yUXZd已fast stop。未改主树、未启动Redis/生产进程、未调用付费模型/图片/视频/OSS/ERP、未提交部署。接续以原Task规划跨阶段精确选取/旧方案逐项成图复用及原两Skill完整原入口本地恢复链为优先，避免将当前canonical覆盖误报为所有协议完成；付费前仍须报价等确认。


## 368 旧方案／最终提示词成图复用与原两Skill本地完整流程（2026-10-31）

本轮接续366/367，未另起工作树、未改主树或默认false入口。原ImageHandler受理前的ready方案展开、源提示词/原任务核对、尺寸及原图版本核验提取到chat_image_preparation.prepare_input；生成和复用共同使用，原ToolRuntime/确认、受理、账本、provider与结算仍在各自原位置。原ECOM plan_source早拒绝用共享validate_entry保持在构造resolver前，避免把原应当返回的计划错误变成缺字段异常。native create_resolver.prepare_request同时重新verify_native_resources、事实来源、原图字节与文件权限，再应用当前adapter默认参数；不是复制旧URL直接认完成。

复用现覆盖原plan_source、最终source_prompt及带稳定variant的原任务重试。ready方案保留原plan_id/revision/item/request_text_sha，当前尺寸变更、ready状态/来源/原图改变均拒绝；最终提示词经原resolver检查精确消息片段/hash，保留原plan_item_id与原文，不重写。stable variant再生成只找相同source_task_id、相同variant的既有子结果，不能拿source本图充新结果；无variant的明确再生成保持原生新图语义，不能为省钱吞掉用户重做要求。图片任务retry/原consumer的既有幂等继续保留。真正不匹配才可沿原付费派发路径，失权/读取不确定不能自动买替代。

原Task规划跨阶段沿find_task_text/reuse_task_text，不新增模型可传正文的来源工具。已提交结构化原稿可导入当前host.image_source要求，复制原完整正文及SHA/source身份，赋task-source真实定位；不继承旧事实审核，当前complete仍走原核验和原图绑定。SavedPrompt/legacy-plan原身份保持不变。368 SQL核查同root旧已完成结果，或通过原340固定历史/同会话/执行身份/branch的读取接口取得外root真实来源；检查真实来源、正文/hash和source_call/task/current交付owner/要求步骤。不能将临时未完成文字、伪造source或改变正文当原稿；rollback全租户排空task-source，不拆仍被恢复引用的协议。

366 guard扩展保存SavedPrompt和ready plan复用，重新核实当前真实ready行/原item SHA/原input_snapshot引用/真实发布child；没有新图片受理或费用。新增真实PG覆盖原生accept→claim→mark dispatch→provider结果替身→publish→当前CP复用，图片任务数和余额不再变化；变造结果拒绝。跨真实新Turn→原要求修订→host.image_source→reuse_task_text保存，旧正文和待核实效果保留；rollback用无租户scope的连接证明全租户drain拒绝。

原两Skill证据：原临时完整export已丢失，未连接生产重新导出。仓库两份v3正文精确SHA分别等于PROFILES已登记的原发布body；使用原body及本地验证frontmatter/asset，固定原package/key/发布revision，未新建/发布测试Skill。两条实际SkillRuntime/control链完成：手选→完整逐图规划→原事实核验保存→phase/resident交接→Skill恢复→新execute_ready要求版本→压缩后读取已存原稿→重新当前事实核验/原图绑定→五个真实来源替身的自动复用→当前交付完成→再次恢复。保留原摘要、手选身份、历史要求/完整旧成果；业务执行器未调用、无image operation。该证明覆盖原正文和本地协议，不冒称已拿到原生产完整frontmatter/export、真实模型推理效果、供应商产图或浏览器验收。

验证：受影响原生图片/工具路由/ECOM/旧来源/原图绑定/修订/新Turn集合276通过4.79s（/private/tmp/agent-saved-source-regression.log）。最新新unit23通过0.73s（/private/tmp/agent-saved-source-callback.log），含实际native prepare callback、真实临时图片字节变更、两原正文完整本地流程及SavedPrompt跨阶段复用。最后组合结果见收尾；集合重叠不相加。真实PG新协议4项与跨阶段2项覆盖两CP owner，均已定向通过。没有付费请求或生产数据库操作。

失败证据与修复：首轮错误测试路径未运行即退出，改为真实存在用例；fixture替换误改原rollback负例造成后续级联，恢复原负例后138通过。新来源SQL真实PG暴露JSONB箭头/拼接优先级，368及366目标归属改显式括号，未放宽条件；合规source和变造负例通过。原ECOM早拒绝移动造成简化fixture KeyError，恢复原时机并共享guard。真实callback测试原稿3:4与原4×4照片默认继承1:1不符，明确当前用户文字3:4后通过，原尺寸约束未放宽。独立窄审无新增高置信问题，后补修正复核见收尾。

本批本地流程完成不等于整个升级可发布；其余ERP逐实例终态、多入口/channel/完整页面及原两Skill供应商验收仍未全部闭合。保持无付费、无提交部署，收费先报价等确认，整套验证通过后才发布。

368收尾：最终原343续接54PG＋新23unit＋新旧followup/reuse48PG组合125项通过10.76s（/private/tmp/agent-saved-source-final.log）；受影响集合276通过见上，集合重叠不累加。独立只读末复核确认task-source含冒号Key/空来源拒绝、366/368数组合并及原ECOM早拒绝/尺寸/确认时机保持，无新增高置信发现；未独立重跑/外部调用。172个任务新/改Python AST、库存JSON与git diff --check通过。自有PG /private/tmp/agent-orchestration-pg.9yUXZd已fast stop；默认admission=false，无主树修改、付费/供应商/生产数据库调用、提交或部署。

后续接续：本批旧方案/最终提示词/结构化原稿与稳定重试的本地复用和原两个已核对正文的完整control流程已有证据，不重建测试Skill或重跑未受影响结果。整套仍缺剩余ERP逐方法/批量/异步实例终态、多入口/channel及完整页面，原两Skill真实供应商产图验收；生产完整export/frontmatter复核需合法原来源，不以本地body证明替代。先完成可离线的剩余适配；需要任何模型/图片/视频等付费测试时先报价等用户确认，整套验证通过后再部署。


## 369 ERP批量回执与丢确认恢复事实（2026-10-31）

本批继续原任务树，先补ERP领域账本的恢复信息，不猜未知供应商终态。原NativeWriteSession.dispatched现在在原参数hash旁保存request_evidence：来自真实dispatcher映射后的实际参数，保留批量对象和顺序；凭据键及嵌套/JSON编码凭据脱敏，原完整参数hash不变，投影独立hash；有界遍历/体积超限显式incomplete，不改变原写参数或为扩大快照另发请求。仍是原354领域账本JSON，无新队列、第二套执行器或模型可传正文的证明接口。旧行无snapshot明确返回None，兼容读取，不凭摘要重构。

read_task_write_result在原身份/Policy核验之后返回recovery事实：实际派发快照、原响应中job/task字段定位、逐项identity与reported_success/reported_failure/unverified，截断标记及下一步。标记只是原接口观察，不将一个成功子项或全局success推断成整批完成，不根据taskId猜新查询API。未知方法/批量/异步没有已验证终态协议时，明确requires_method_specific_verification；无自动续发剩余项、不替换原confirmation或nonce。当前有异步/部分/失败标记的标题回执也不再建议反复查普通商品详情，保留原实例/逐项信息等待方法终态。原COMPLETION_METHODS未扩大，已有tag.add和有ACK的纯标题postcondition完成路径保持。

369 reader在原360/361受限读取基础上，允许查看HTTP确认丢失但本地原调用已闭合之后的真实商品详情观察。只支持原精确title-only expectation，真实139查询须晚于原native invocation completed_at，未闭合/过早/缓存/重放/跨actor/org/错误商品/不同session或无已核实刷新链不得认领。保留原source rotation、原owner/lease/成员scope。此处只是当前目标状态观察，不证明供应商原写已经结束。

独立窄审识别并关闭一项P2：本地timeout结束不等于供应商结束；若原标题本来X、原设X仍在供应商排队，读X后清掉unknown再设Y，迟到X可能覆盖Y。初候选按matching read标completed已撤销。最终validate_proof仍仅按原returned/accepted协议结算；dispatched/unknown即便target_state_observed=true，也保持uncertain、effect_terminal=false、completion_basis=None，不能导入完成回执，不能解除原冲突/版本写屏障。指引await_provider_terminal_evidence并停止重复读取，等待可关联原请求的终态或因果证明。不声称“查询符合就写成功”，不为省事自动重发。原journal.phase/response、139失败结果和完整CP/opaque历史均不改。

验证：新8unit+原native/postcondition共66通过1.01s（/private/tmp/agent-erp-recovery-unit.log，后补2unit见最终）；最终新18真实PG通过2.74s（/private/tmp/agent-erp-recovery-pg.log），两CP owner，实际原dispatcher/SDK HTTP为本地替身。覆盖丢确认观察但仍uncertain、相同/不同标题后续调用不再发单、原文/上下文/原失败结果保持、过早/未闭合/cached/wrong actor/source拒绝、batch部分响应和原对象在压缩后从真实账本恢复、import拒绝。接口格式化不参与成功判定，无外部ERP/模型/OSS请求。

新旧ERP/postcondition/session刷新及PG受影响集合155通过8.10s（/private/tmp/agent-erp-recovery-regression.log）；最后补async/partial停止无效详情查询、新旧controller/model bridge共95通过1.09s（/private/tmp/agent-erp-recovery-final-unit.log）。集合重叠不累加。早期PG用例在已完成步骤后强求replay而触发原TASK_WRITE_RECEIPT_STEP_REQUIRED，已改为验证最终安全语义：本批不再完成unknown，不导入回执，原uncertain重放/变参调用拒绝；不是放宽原边界。独立最后复核确认P2已关闭，无新增高置信发现；不替代整套或供应商验收。

rollback369仅恢复原361 reader；新观察不产生新的完成状态/成果类型，不需删数据或解除unknown。原dispatch JSON快照完整保留，旧代码忽略新字段。默认入口仍false，不提交部署，不跑付费验收。

仍未完成：其余ERP逐方法/批量/异步供应商实例终态协议与后台唤醒。采购计算现仅progressType全局进度，不能安全绑定本次请求；本批没有虚构实例协议、自动轮询或把“待核实”标已完成。此类入口还需要可信原始方法契约/实例终态，才可新增自动完成适配。后续多入口/channel/完整页面与原两Skill真实供应商产图也未完成。继续复用原证据，付费先报价获确认，整套通过后再部署。

369收尾：175个本任务新/改Python AST、库存JSON与git diff --check通过。自有PG /private/tmp/agent-orchestration-pg.9yUXZd已fast stop；没有Redis/生产服务/主树变更，无付费或外部HTTP/ERP/模型调用，无提交部署，admission=false保留。后续优先核实可关联本次原请求的ERP逐方法/批次/异步终态契约；缺契约的状态不得改completed，不得清unknown继续冲突新写。已完成的snapshot/观察恢复不重新开发，原有155和最终95定向证据按影响复用。


## 370 商品稳定字符串字段逐项完成核验（2026-10-31）

本轮继续ERP可在本地有证据适配的范围。由现有registry.product的product_add_update_simple映射和formatters.product详情原字段确认：标题之外的barcode、unit、remark、shortTitle有相同原生字段名可精确读取。新增product.fields v2后置条件覆盖title加这些字符串字段的组合；outerId和title仍必需，标题/编码边界沿旧规则，新增长度仅为本地自动核验的保守上限，不声明为供应商API限制。未知/数值/嵌套/过长字段不取得自动完成契约，原写功能和确认仍按原入口执行；不转换条码数字、不删前导零、不将NULL/缺省当清空、不换算单位/价格。SKU、库存、价格、brand/ID/status及其它方法不在本批新增完成范围。

宿主同时核对原模型params（只接受明确友好字段）与真实mapped参数的精确期望相等；被mapper忽略的未知编辑、None或类型变化不能洗成纯标题许可。纯标题继续返回原product.title v1形状，旧checkpoint/hash/解释不改。managed exact item.single.get新增独立_erp_readback_fields_v1，旧_erp_readback_v1仍原形保留；普通非managed、fetch-all、宽泛/多目标读取不增加证明。新投影逐项保留原字符串，全部请求字段在同一真实详情回执中一致才允许核验通过，标题相同不覆盖其它缺失/不同字段。

370 RPC以postcondition.kind选择原139 metadata投影，保持原owner/token/lease/成员/同会话/logical来源、查询晚于原写边界、非cache/replay及原source fingerprint/已观察refresh链。原已ACK同步写可在此后置核验完成后导入回执；HTTP/格式化丢失但原returned记录确实保存时沿既有核实路径恢复，不重写。未知dispatched即便全部字段已观察匹配仍uncertain，target_state_observed与effect_terminal分开，不导入完成回执/不解除旧效果屏障。async/partial ACK或被忽略的原编辑即使详情全匹配，也保持待核实，不能普通详情查询冒充整批/异步终态。

新真实PG流程通过：完整指定字段→原dispatcher/SDK一次模拟HTTP→持久原账本→真实非缓存read→read_task_write_result→import_task_receipt→complete_task_step→finish。包含format_loss、有组织的实际session刷新链、逐字段不同/缺失/NULL/数字条码、缓存/早读/源变化/撤权、unknown匹配不完成、导入后真实字段再次改变拒绝旧回执。原messages/opaque、journal.response及模型原输入保持，原参数/nonce/确认/计费入口不变。rollback370由实际journal owner全租户检查product.fields排空，再恢复369 reader，不能拆仍被在途状态引用的新契约。

本地证据：新27unit加旧postcondition/recovery共57通过1.01s（/private/tmp/agent-product-fields-unit.log）；新32 PG通过4.59s（/private/tmp/agent-product-fields-pg.log，两CP owner）。受影响新旧ERP/会话刷新/原工厂与PG/旧mixin合跑227通过11.69s（/private/tmp/agent-product-fields-regression-final.log）。最后增补async ACK/partial ACK/mapper忽略字段的真实dispatcher+PG负例6通过1.59s（/private/tmp/agent-product-fields-partial-final.log，32 deselected）。新增去重27unit+38PG=65，组合重叠不累加。所有HTTP与provider结果为本地替身，没有外部ERP、模型、图片、视频、OSS或生产数据调用。

首个扩大集合226通过1失败，发现旧test_erp_tool_mixin_unit在collection阶段全局sys.modules预注入services.org空模块，污染真实工厂测试；移除这两个过期全局替身后原组织配置使用真实模块，合跑227通过，没有为通过测试放宽产品权限。prepared测试fixture仅增可选parameters，旧默认纯标题完全保留。独立窄审无新增高置信字段漏验、越权或unknown清除问题；未独立运行外部测试，不代表供应商/UI整体验收。

本批完成稳定商品文字属性适配，不宣称所有ERP方法/批量/异步都已完成。原COMPLETION_METHODS未扩；仍缺其它方法及可关联本次请求的实例终态/后台唤醒。保留原369批次事实与unknown屏障；采购全局progress仍不能冒充本次完成。多入口/channel/页面与原两Skill真实供应商验收继续待完成。默认入口false，无提交/部署，任何付费仍须先报价获确认，整套通过后再部署。

370收尾：旧ERP mixin单独11项通过0.56s（/private/tmp/agent-product-fields-mixin-isolated.log），证明移除全局org替身后独立运行和合跑均可用；不为此重跑已通过集合。178个本任务新/改Python AST、库存JSON与git diff --check通过。自有PG /private/tmp/agent-orchestration-pg.9yUXZd已fast stop；未修改主树/依赖/默认admission=false，没有任何付费、外部ERP/模型/图片/OSS/生产数据调用或提交部署。

下一接续：稳定商品文字字段本批已完成，不重复开发；其余ERP方法尤其批量/异步终态须找到与本次原请求绑定的可信方法契约，不能从全局进度/当前目标匹配推出旧effect已结束。可并行推进不依赖供应商的多入口/channel和页面恢复验收，复用未受本批影响的原两Skill本地证据；需要真实模型/产图等付费先报价等用户确认，整套验证通过后才发布。


## 371 原消息接续控制、多入口回归与本地页面验收（2026-10-31）

复用原任务树、原343消费者、345/346控制入口和362进度读取，不增加另一套执行器或权限。MessageArea沿原resume-by-message接收resumed/already_pending/enqueued/already_enqueued，逻辑续接不再对已完成source执行beginResumedStreaming而清空旧内容；有真实新message才订阅新窗口，否则刷新原历史等待原消费者。沿用client/external/native task_id，保留旧同窗口恢复行为；受理后历史刷新失败不建议用户重发。每消息和进度卡片均限制并发点击。

TaskProgressCard经MessageItem真实回调链沿原接口暂停、继续、停止；只显示请求已提交，最终状态来自原持久进度。原成果与已派发不确定操作保留，停止不等于撤回供应商写入。权限失败清除进度/按钮、停止自动重试；拒绝较旧revision倒灌，已结束窗口低频重查以发现真实新用户接续，隐藏/视口外不轮询。真实390px浏览器发现旧InlineChartImage固定500px溢出，限制容器最大宽度并保留原比例/资源/点击语义后通过。

ERP accepted不是可保证自动返回的媒体队列，公开进度将其归为needs_verification/unconfirmed，不再错误计入pending_results。erp_contracts是只读契约目录，恢复事实仅附加verification_contract，不参与派发/完成判定。ERP_METHOD_FINALITY_INVENTORY.json覆盖实际7类registry的88个写操作：1同步回执、1有条件商品精确读取、1采购实例协议缺失、85其它方法协议缺失。后86项均不能借generic success、目标状态匹配或全局progress声明旧effect已结束，不能重发。370原字段核验、unknown屏障和原业务执行能力不变；未声称所有ERP已完成。

本地验证：新增真实HTTP+PG8场景（两CP owner，原窗口完成前后队列阶段×继续完成/取消），保留原checkpoint、消息内容、计划、操作及原生已发布图片；原进度PG/ERP恢复PG/企业微信Actor入队与WS runner/会话投递组合85通过5.05s（agent-task-page-backend-regression.log）。契约目录3+ERP恢复10+进度9共22通过0.78s（agent-task-page-erp.log）。前端原MessageItem/WS/加载/思考/接续组合164通过1.51s；停止控制增量后卡片+接续22通过693ms（集合重叠不相加）。tsc/Vite/postbuild PDF检查通过13.35s。定向ESLint0错误，仅原MessageItem genParams Hook依赖1条既有警告。

新增版本化frontend/scripts/task-page-acceptance.mjs与tests/browser/task-progress.fixture.jsx，用已安装Playwright/Chrome启动自有loopback Vite并在finally关闭；所有API替身，非本地请求拦截并失败，不安装依赖、不连接backend/供应商/生产。真实MessageItem检查续接仅一次、原规划+两张本地SVG资源保留、暂停/停止各一次、390px无溢出、已结束任务接续轮询、权限失效清控件。12本地请求、0外部请求/页面错误；桌面和手机截图已目视检查。此证据不代表原两个Skill的真实产图/生产页面验收。证据持久保存/Users/wucong/.codex/task-evidence/skill-coordination-design/371；临时日志已复制，不把重叠测试求和。

复现浏览器：在任务根设置TASK_PAGE_PLAYWRIGHT_MODULE为已安装playwright/index.mjs绝对路径，TASK_PAGE_BROWSER_EXECUTABLE为已安装Chrome绝对路径（可选，缺省用Playwright浏览器），TASK_PAGE_ACCEPTANCE_OUTPUT为私有证据目录，运行node frontend/scripts/task-page-acceptance.mjs。仅使用已有运行时；本任务没有新增安装或付费。

剩余：86个ERP方法/实例终态契约及对应可信后台唤醒、channel专属进度访问/展示、原两个Skill真实供应商/原产品图完整验收与全项目最终发布验证仍未完成。既有channel入队/投递回归不是新增channel进度授权。默认admission=false，未提交部署，整套通过后再发布；任何收费先报价等用户确认。

371收尾：181个本任务新/改Python AST、两个库存JSON、版本化浏览器脚本语法与git diff --check通过。版本化脚本最终重跑12本地请求/0外部/0页面错误通过，已拦截且断言不允许任何非本地请求。自有PG已fast stop；自有Vite/Chrome关闭，5179无监听。未操作主树、生产凭据或部署入口，没有新增依赖或收费。下一接续从本节明确的86项终态缺口及channel专属范围推进，不重复已完成的原消息续接/页面修复，不重新生成已有图片。


## 372 原ERP Agent保留与多入口准入保护（2026-10-31）

按用户新范围冻结新增ERP写适配，不删除原ERP Agent，不把它替换为88方法新编排。原erp_agent注册仍为legacy read；真实ToolExecutor handler兼容task/query，原追问条件、actor/org/conversation/workspace、request context、共享预算、取消信号、超时和staging结果注册保持。新5项本地回归验证个人/群工作区×两种参数入口及注册；专业Agent结果以原对象返回，没有外部ERP调用。不是新增原ERP Agent adapter。

调查发现全局新任务开关打开后，Executor虽限制followup个人会话，却无同等bootstrap限制。343续接及新版媒体数据库协议明确只接受user会话，群聊会被误切入不支持的新链。新增bootstrap.admits_new_task统一宿主准入：user、workspace owner=actor、允许个人上下文、无channel_scope_id；只用于新建/新followup，saved_bootstrap优先保留，已有state恢复不受影响。原群聊仍按resolve_execution_scope和原Actor/Skill/工具流程执行，不授予个人记忆或跨channel访问；不新增共享群聊UI权限。

实际Executor回归：新开关开/关群聊均不新建逻辑任务；已有群bootstrap在全局新建关闭时照常恢复，原群消息、已有内容块和执行身份逐项保留。个人新建/旧恢复/篡改拒绝继续通过。增补5个准入身份反例；这组47项通过0.83s。首轮43通过3失败发现原bootstrap测试缺少366新增followup的DB RPC替身，补充只接受确切read_agent_task_followup参数并返回not_found的窄替身后全绿，未放宽产品判断。

受影响9文件合跑219项通过1.69s（agent-entry-preservation-regression.log），含原ERP Agent全套、个人/群scope、企微入队、followup/历史成图复用、Web工具历史压缩；不与47项重叠累计。结束时有一条AsyncMock协程未await的RuntimeWarning，未归为产品异常，也未隐瞒为零警告；没有因此修改原Agent。保存日志在/Users/wucong/.codex/task-evidence/skill-coordination-design/372。未改前端，不重跑371有效构建和浏览器证据；无新迁移，不重跑未受影响PG集合。无付费/供应商调用/提交部署，默认新建开关false。

372收尾：183个本任务新/改Python AST、两个清单JSON及git diff --check通过；本轮没有启动数据库/Vite/Redis或浏览器进程，没有生产操作。后续从文首当前范围推进非ERP最终调用链/在途恢复审查与原Skill真实验收准备，保留371有效页面证据，不重做冻结ERP适配，不重新生成旧成果。


## 373 非ERP范围最终审查增量与延期ERP域准入（2026-10-31）

按review Skill对新建准入、原CP/完整上下文、共享预算、成果复用、副作用派发和在途恢复作定向审查，复用368/371/372仍有效证据。独立只读复核发现P1：全局编排开关打开后，v3混合任务仍无条件进入延期ERP写协议，仅改文档不足以隔离发布范围。已关闭：新增agent_task_erp_write_enabled=False独立域开关；新v3计划拒绝新增ERP写步骤，原工具投影和实际派发阻挡同源。NativeWriteLifecycle在无旧effect回放入口、failed_before_dispatch新call纠错及begin预留前拒绝；prepare_erp与原生observe dispatched再次检查。不能退回普通Actor生命周期绕过保护。

无TaskState及v1的原ERP路径不改，原erp_agent注册、调用及上下文不改。旧ERP operation仍可沿原key回放、读取和导入真实回执；unknown仍不完成、不重发、不解除冲突屏障。已有ERP op导致工具保留披露时，也不能新建另一effect或修正后重派。文件恢复/删除/视频共享基类只按call.name=='erp_execute'检查，ERP关闭不影响其执行；计划任务/Reach/代码产物沿原协议。新总开关与新ERP域开关均保持默认false，不修改生产配置。此处是延期范围隔离和旧数据保留，不是继续扩展ERP业务适配。

测试使用明确opt-in fixture验证冻结的旧ERP协议，与真实默认关闭分开：旧ERP协议相关8个测试模块显式启用本地测试开关；新增deferred_erp共10项始终关闭，覆盖默认配置、原Agent保留、伪造新调用不预留不IO、计划拒绝但原成果不改、回放至begin之间关闭、原prepared边界、旧completed/format_loss/timeout/failed_before_dispatch恢复和不重发。原native PG恢复另增close_new_domain参数，两CP owner验证关闭后真实原账本、CP、opaque上下文、原生结果及回执导入保持。

证据：新门禁/共享生命周期/计划任务/Reach/视频/代码unit组合111通过1.40s；新计划门禁与实际control/model bridge组合79通过0.96s（前两集合重叠不累加）。原两个固定body完整规划→phase交接→压缩恢复→五图来源替身复用、原ERP Agent、bootstrap和channel隔离组合53通过1.15s，包含两原Skill本地control链，不调用供应商。真实原生账本/文件恢复/删除/视频队列最终100 PG通过11.47s；原文件剩余项恢复与晚到要求拦截/异步视频修订再40 PG通过6.73s，均两CP owner。日志在/Users/wucong/.codex/task-evidence/skill-coordination-design/373，集合重叠不求和。

首轮扩大PG54通过46失败，诊断为原文件测试fixture未安装363–365真实要求版本门禁，导致check_agent_task_revision_gate不存在；不是放宽产品校验。file_restores/deletions本模块增加当前真实迁移安装/排空fixture，remaining-item resume显式复用；独立revision测试保留自身迁移层。加原operation存在诊断断言，48文件PG通过后最终100合跑通过。初始命令有一次文件名错误、0项运行，未当通过证据。审查独立复核P1关闭，未发现本轮新高置信预算/上下文/成果/域门禁问题；只读审不替代整个仓库或供应商验收。

免费浏览器预检：仅访问已有登录的EVERYDAYAI后台，没有发送聊天、重试、生图、保存/编辑/发布/启用Skill。平台列表包含原reference-image-prompts及ecommerce-image-prompts，参考图原第3版revision可见vf540a7eb0eff4862b924d9af0f7032bd并可只读查看；电商原第3版及原schema附件摘要仍存在。两详情“当前可用版本”显示“尚未启用”，而历史有已发布版本：只记录当前后台展示，不据此断言聊天作用域一定不可用，也未修改assignment。需在真实验收前核对同一聊天作用域的目录可用性与原完整export/素材。浏览器被用户切换时停止使用旧AX索引，没有试图绕过状态检查；本批后台只读预检不是新编排的生产页面验收。

剩余不冒称通过：原两Skill在当前真实聊天作用域的目录可用性/原完整包与素材复核，真实供应商语义规划/事实纠错/产图及最终页面端到端，最终候选发布检查。371本地真实Chrome页面与构建仍有效，本轮未改前端，不重复；当前没有任何新的模型、图片、视频、ERP/OSS外部写、安装、提交或部署。需要收费先按锁定请求清单报价等用户确认。按当前仓库qwen3.8-max计价参考：每百万输入170/输出510积分；10万输入+2万输出为27.2积分，仅预算测算，不是已批准额度、不含图片/供应商价格承诺。原请求与可用模型锁定前不启动付费、不将测试skip算通过。


## 374 原两Skill、原素材及收费预检（2026-10-31，只读未闭合）

用户要求先核对原两Skill当前可用性、完整发布包和原素材。本轮只读现有登录后台，没有发送聊天、生成/重试图片、编辑/保存/发布/启用/停用Skill或修改assignment。电商原第3版UI可见v5d3a01973e41404981324860769e3750，与原PROFILES登记一致；商品分析schema摘要5650字节。参考图原第3版vf540a7eb0eff4862b924d9af0f7032bd沿用373只读证据。两份仓库v3正文重算SHA仍等于已登记原发布body，商品schema本地5650字节/SHA256=6efc8a4f9181b567358f6d139ec9fedfd1a55d32bb004cbbc0ffb7228f7b9cf9。证据写入私有持久目录/Users/wucong/.codex/task-evidence/skill-coordination-design/374/original-skill-preflight.json；不重新运行53项有效本地协议证据。

重要区分：仓库frontmatter/正文/附件验证不等于线上完整原包raw_b64或storage内容hash已复核。当前后台详情“尚未启用”只能说明该后台available_revision为空，需要在原聊天实际目录核对，不能凭此推断整个Skill禁用或擅自调整assignment。更多操作菜单仅刷新/废弃/停用，无完整只读导出按钮，未点击任何状态写动作。原/private/tmp/skill-coordination-live及用户当时两个截图的临时本地文件均已不存在；它们不是可复用的原商品图来源，不用截图替代原图，需要从原会话核对资源身份/尺寸/原字节与已有成果。

Chrome原页面在动作间持续被切回另一个活跃任务，工具stale-index保护拒绝后不重复使用旧索引；一次Escape超时重置后重新读取，未触发Skill写入。已异步请用户短暂保持EVERYDAYAI页面以完成当前聊天可用性/版本/素材只读核对。此处是真实未闭合条件，不标通过，不以之前的Skill正文hash证明替代。

费用只作未批准估算：仓库DashScope qwen3.8-max输入170积分/百万Token、输出510积分/百万Token。先做交付覆盖/事实纠错等不产图的真实模型核验，预估10–30平台积分；示例总10万输入+2万输出为27.2积分。当前未锁定实际完整原请求、Token限额和模型，故不是已实施的硬预算或供应商账单，也没有获得付费授权。生图若使用nano-banana-pro 1K，仓库价25平台积分/张；五张全新图示例125积分，另加规划模型费用，仅用于预算参考，不代表需要重做五张。先核实已有成果的精确可复用清单，只对确实缺少的新结果另报正式报价。上述报价依据/文件SHA保存cost-estimate-unapproved.json；所有付费调用数0。

尚未完成：原聊天Skill实际可选状态、原线上完整包和原产品素材/成果清单、真实供应商端到端及最终发布。未收费、未提交部署，原ERP Agent及两个默认关闭开关保持，不改原功能/上下文/成果。


## 374补记与375行业研究（2026-10-31）

用户允许短暂不切换后完成原会话只读核对：fd4f8eeb-cfd6-4ab8-b81c-f846de4e64d4实际Skill选择器中，两原Skill均可选且详情版本精确匹配原vf540a7eb...和v5d3a0197...，没有点击选择/发消息。纠正此前后台“尚未启用”不等于当前聊天不可用。两张1024×1024原商品PNG从原预览下载恢复到私有持久374/original-inputs，记录原字节/尺寸/SHA；原请求同目录。另找到原截图同一成图会话9371b463-e8fc-4262-91e8-af4e23e2fbc3，原请求、原电商Skill版本、五张旧成图仍存在。第一张任务详情为已完成结算6积分、gpt-image-2-5-sunburst-image-to-image、1K、1024×1024；原参考消息e8500ebb-4ab6-412f-a7d2-f5a616689836及两原图字节与已恢复文件一致。这是历史账单，没有新模型/产图或额外扣费；尚未逐一核实五张复用资格，完整原包raw_b64/元数据导出仍未闭合。374的Nano Banana Pro25积分只是假定模型示例，不能当作原历史模型的正式新验收报价。

用户随后要求研究大厂的显式Skill、自动推荐、编排与纠错机制。本轮只研究/对照，不改运行逻辑。官方Codex/Claude/Gemini文档、Google公开源码、ADK和AWS幂等资料已核验；详见INDUSTRY_Skill显式选择与自动推荐及错误恢复.md。关键发现：无公开统一“手选独占且永不冲突”保证；Claude allowed-tools为预授权而非排他白名单，与本项目旧求交语义不同；现owners_for不按sources自动裁决冲突，原旧主图预处理失败在模型循环外抛异常，新bootstrap入口另有隔离。建议先评审来源/动作/阶段及权限语义差距，再决定修改；不据此另建调度器或放宽权限。默认两个新开关false，原ERP Agent保留，付费先报价，整套通过后部署约束不变。未收费、安装升级、提交或部署。


## 376 原两个Skill本地场景验收（2026-10-31）

新增 `backend/tests/test_task_execution_original_entry.py`：原两个精确发布身份/正文hash的仓库v3源，分别手选和不手选，覆盖原五张详情图请求、只要提示词、先规划再产图、执行已保存完整方案、执行最终提示词，共15个真实execute_chat入口/主循环场景。开启旧电商关键词条件且提供handler.db，以会立即失败的旧initialize监视器证明新bootstrap没有绕过测试条件进入旧强制流程。错误start参数返回TASK_CONTROL_INPUT_INVALID，下一轮修正进入独立要求核验并保存原始goal/source；规划方法仍绑定且图片工具未提前披露。直接执行已有来源不重复绑定规划方法，但保留宿主来源核验要求。原历史消息按原顺序完整保留，未调用业务执行器。

该入口测试明确停在初始化已核验边界，不伪称完成产图。完整规划、真实方法阶段切换、检查点恢复、压缩后重读原文本、五张已发表图复用由同一RepositoryOriginalSource的已有完整控制链测试验证。与要求核验、规划事实核验、要求修订、接续复用、旧来源、原图绑定、旧入口保留组合最终146通过/0失败/0跳过，日志与原图hash核对记录位于 `/Users/wucong/.codex/task-evidence/skill-coordination-design/376`。前序132与中间99通过组合重叠，不累加；中间另有2项真实完整原包测试因缺私有export而跳过，未删除或改为虚构export放行。两张原始素材持续存在且SHA与374一致。

本轮只新增测试/记录，无产品代码修改，无付费调用、生产写入、提交或部署。模型输出、语义复核和已有成图查询使用明确的本地替身；不能证明真实模型理解/供应商产图质量。两个原仓库方法的model_selectable=false保持原样，无手选只验证宿主自主规划入口，不偷改为自动激活这两个方法。复用371页面/构建、372原ERP/上下文、373真实PG/在途恢复证据，未因测试新增重跑无变化的整库或构建。

尚不能声称整套最终验收：原生产完整包字节/元数据export仍缺；真实模型选择、原素材供应商产图和最终原Skill页面验收仍待完成。先按实际模型/价格给出费用上限并取得用户确认，再运行必要收费样本；优先复用旧成果，不能以本地146通过自动批准付费或部署。


## 377 聊天流式、知识放行边界与终态对账（2026-10-31）

本批并入原任务和原工作树，不新增任务编排/交付链路。用户提供生产三次10–18秒、每次正文1块（一次3402字符）、grounding两次unrelated/一次other_tool_chain及接口首字0.6–0.9秒观察；本轮复核了代码因果，未重新读取这三次生产原始日志，不能冒充独立生产计时。原knowledge_grounding_run存在即阻止正文/思考增量，模型整轮结束后_verify_knowledge_answer放行一次；前端3秒补偿持续回读最近已终态会话并无成功去重/重叠请求合并。这些逻辑并不会随新版编排自动消失。

职责与原调用链：消息发送/幂等持久化和ContextAnchor → actor_enqueue.enqueue_generation_turn受理 → Redis唤醒/ConversationWorker及数据库claim → ChatGenerationExecutor恢复身份、输入和CP → stream_setup上下文/知识/资源/权限/预算 → 原Skill与任务控制循环 → ModelGateway增量 →（仅需要时）知识依据核验 → ActorWebSink先追加持久事件、再原WebSocket/Redis推送 → 前端delivery cursor、原store和MessageItem（首块直接投影、后续16ms批处理）→ 原完成/计费/成果保存 → refresh/reconnect/定时终态补偿。任务计划、成果齐全/finish、答案依据和可展示内容是不同职责；没有把所有请求强行排成规划+分类+评审门。

用户明确批准保守边界：宿主明确自包含可流式；显式知识引用、已有证据、企业规则和模糊继续核验。scope_selection新增宿主窄判定：寒暄/限定通用交互、带本条明确正文的转换、明确仅分析当前图片等；不是通用语义分类器，不能识别所有普通问题，未命中不视为无需知识。不接受模型自报豁免，不依据有无工具或任务复杂度放行。现有知识历史标记/引用、不同的最新user追加消息、required或非支持附件保守留在旧核验路径。显式refs从持久化消息读取；新自包含请求仅跳过默认快照准备，已有snapshot始终先核对并恢复；没有删除知识库、原引用或原校验。真实prepare明确禁止旧select再次自行豁免，防宿主拒绝后被旧转换规则选空范围。

所有STEER消费点（loop、工具、Skill及legacy reader）用原注入函数保留pending失效标识，下一模型边界重新进入原知识准备/使旧answer_plan失效；最新真实user正文用于知识范围/核验问题，原request/ContextAnchor不改。恢复也检查最新user，不能用原始寒暄把已追加企业要求重新豁免。工具后实际新知识run在下一模型边界进入核验。需要核验的轮次通过原tool_step事件展示“核对回答依据”的真实宿主阶段，只有核验结束后才释放允许正文；阶段卡片不假称已完成业务成果、不触发新业务/模型调用。

前端所有对账调用共享恢复队列，timer/reconnect的重叠默认fetch合并；显式恢复快照排队不丢。成功receipt绑定任务状态/目标消息/上下文版本和实际缓存对象，缓存卸载/替换、状态更新、新active或当前stream再次出现均可重新核对；失败不写成功receipt。image子成果也只合并自己的结果并保留父流，成功稳定对象不反复details。缓存各最多200项，仅内存引用不含凭据。保留原stream ID前后检查，另以immutable binding Map防同message ID暂停/继续时旧在途历史覆盖新投影；其它绑定变化最多推迟本轮，后续补偿仍有效。没有靠关轮询或假打字隐藏问题。

观测补充：原Actor sink标量append/send/progress/snapshot/persist与到达间隔诊断保留；新增模型边界first_body/块数/最大块间隔/elapsed及held/review标记、知识准备与核验耗时。只记录耗时和身份，不记录正文；诊断不加入计费usage。模型边界含Gateway等待和sink背压，不能解释为独立接口TTFT。原pre-stream日志仍为聚合上下文时间；受理/Actor纯排队及生产页面首字的统一跨进程关联测量尚未形成完整前后样本。

验证：后端232项组合通过（含本地真实PG知识/检索/依据检查、原两个Skill、实际读流边界和计费契约）；随后新增真实runtime+PG闭环23项通过，重叠不累加。前端最终150项组合通过，含断线/刷新/暂停/取消/继续的既有补偿、新消息与旧任务交错、失败重试、同ID恢复、终态缓存重建、图片子成果保留。构建/类型/PDF worker和定向lint通过，日志以377私有证据为准。独立审两处P2（模糊转换、提前消费STEER）已修；旧select二次豁免也同步封闭，窄复核无新增高置信问题。调试夹具曾有HMR重复createRoot警告，已加卸载处理，不把调试期说成全程零错误。

真实浏览器本地夹具走实际WS projection/store/MessageItem：任务仍在输出时首段出现、后续事件继续追加，核验阶段中文显示；生产传输/模型用模拟事件。一次首段事件5457ms、DOM帧5523ms，66ms只是本地事件至DOM帧，不是生产端到端延迟，人工事件间隔不能用于速度对比。夹具在frontend/tests/browser/chat-stream.*，私有证据在/Users/wucong/.codex/task-evidence/skill-coordination-design/377。

本轮0付费、未提交部署，原ERP Agent/新增ERP写延期、上下文、权限、确认、计费、防重复、原成果及旧任务进度保留。尚未完成完整验收：生产/等价真实链路的受理/排队/上下文/模型首正文/核验/Redis-WS/页面首字/总耗时前后关联样本；真实模型普通/图片分析/知识/多步场景；原两个Skill原包与供应商最终验收仍沿用376未决。不能以232/150或本地66ms替代这些证据，不能据此部署或宣称完全解决。下一轮先核对实际测试模型与平台现价，给出必要样本费用和上限，用户确认后才收费测试；不重新生已有图片。


## 378 关联耗时观测、真实千问小批与免费Worker/页面接续（2026-10-31）

沿377既有链增加被动诊断，没有新增编排/交付栈。后端chat_lifecycle用原task/turn/attempt关联受理、claim RPC、执行输入、上下文、answer准备、模型首正文/结束、依据核验、首事件持久化/推送及终态commit；标量日志有界、异常隔离，不保存正文或凭据，不写入计费usage。仅同一clock_domain/attempt相减；enqueue到claim包含调度及RPC，不称纯排队。sink推送返回不是浏览器ACK。前端复用原sender/WS handler/store/MessageItem测量请求、受理、实际接收、写入和正文DOM帧；排除thinking和恢复历史，无新网络埋点/打字动画；迟到ACK和旧attempt终态不能污染同ID新请求。生产尚未采集此新字段的前后样本。

本地协议夹具实际运行原ConversationWorker/Redis唤醒、数据库claim、ChatGenerationExecutor、ActorWebSink先落事件后推送、双WebSocketManager跨Redis订阅及原终态提交。上下文/历史/检索是明确夹具，初始消息种入隔离数据库；不代表生产HTTP鉴权/真实知识快照链。四场景×两检查点所有者的8项前后对照通过；before在同一候选复现旧整轮暂扣条件，不是旧生产二进制。每组2样本的Redis接收首正文中位毫秒：普通311.735→88.351，图片288.038→87.174，知识290.806→287.447，多步工具501.164→494.698。前两类1块变3块且在模型结束前到达；知识和本轮带依据的工具回答仍先校验，不以提速关闭保护。固定响应节奏/本机数据不作为生产SLA。细分受理/claim/模型/核验/落盘/总耗时见worker-report/comparison.json和csv，未知字段保持null。

用户批准本批3条千问真实任务、最多30积分，不生图。采用qwen3.8-flash，3条任务实际4次供应商调用（知识生成另加一次原依据审计），全部收到响应，输入1953/output399 token。普通：模型首正文813.744ms、Redis接收903.732ms、10块、commit1238.541ms；原图分析：1756.509/1799.363ms、250块、commit13119.791ms；知识：模型首正文772.467ms、核验1032.287ms、核验后Redis1964.446ms、1块、commit1976.100ms，grounding_outcome=passed。普通和图片均在模型结束前推送。原图读取374已核验SHA的蓝色荔枝文件袋PNG，没有生图。真实供应商探针直接通过显式provider seam，生产ModelGateway路由/素材解析/知识获取不是本次全链证明；native enqueue/claim/Actor/PG/Redis/commit为真实。并未以这些探针验收原两个Skill完整语义链。

预算台账在每次请求前锁定/fsync，5积分保留/调用、最多4调用/30上限、重复请求指纹和不确定受理拒绝重试。20积分为预算保留不是实际消费。按当前分支12/39每百万token价格折算0.038997平台积分，仅token费率等价，未含最低消费/舍入/服务规则；本地Actor测试计费为0，没有生产平台扣分；供应商现金账单未查询，不能报告现金结算金额。本批已用满授权任务/调用数量，不因余额尚余自动追加付费。保存响应+用量+原生事件，无密钥。

之后全部免费：以请求体SHA严格命中保存的真实千问响应与审计JSON，拒绝任何网络回退，原Worker完整续接6项通过；真实Worker→PG→Redis→本地ASGI WS→实际MessageItem页面1项通过，页面首接收99ms/首DOM101.8ms/终态154.9ms、10块、无页面error（缓存数据管道证明，不是模型性能）。另3种真实响应按原native事件节奏回放到页面，无新模型调用：普通首DOM922ms/终态1243.5，原图1811.5/13125，知识1976.8/1982.3；明确为保存事件回放而非同步生产采样。真实供应商分析本身存在秒级块间隔，不承诺持续均匀输出。

本轮证据分组不相加：核心/原Skill/依据164通过；真实Worker及worker单元19通过；原Actor/受理/持久化/Redis/预算24通过；上下文与终态126通过3跳过（原V1 gather被PromptBuilder替换的3项旧测试；另有既有_record_knowledge_metric待完成任务清理警告，非无警告运行）；复用响应Worker6通过，Worker页面1通过；真实付费3通过；最终前端定向133通过（此前更宽213通过组合重叠）；最终tsc/Vite/PDF worker通过，定向eslint0错误1条既有genParams依赖警告，构建有既有大chunk提示。diff检查无空白错误。完整私有证据在/Users/wucong/.codex/task-evidence/skill-coordination-design/378；测试日志与raw/paid/worker/replay分目录保留，不能合并来源冒充生产完整链。

保留原ERP Agent、延期新ERP写适配、原上下文/成果/权限/确认/计费/防重复与原任务进度。新编排及ERP写默认开关仍false。本轮不提交/不部署。仍未完成的发布门：原两个Skill完整生产包元数据/export及原会话最终语义/成果页面验收；生产或等价HTTP鉴权受理、真实历史/知识获取、实际Gateway/资源解析、Worker到页面同一请求的关联性能样本。本批没有授权多步真实模型或原Skill额外付费，需先报价；优先复用已有5张成图，不重复生成。不能以本地通过或3条接口边界探针宣称整套全部完成或生产10–18秒已消除。


## 379 原生产取证、完整原包与预算内真实语义试验（2026-10-31—2026-11-01）

继续同一工作树，不新建编排/交付链。本轮通过本地已有生产连接作只读取证，不运行发布/迁移。取得原两个发布包的实际package/revision、完整SKILL.md与1份附件；生产SkillStorage验证后导出至私有379。参考图原包全文SHA463647352f562e40ee731c13d38883f6176f824253b4fd460bc41d989095ea96，电商原包SHAa668059ba341fe801b96c5f80919594736cbbfbc97b4abf927cfc78bf45167a2；正文hash与374一致，全文不同于仓库v3副本。发布版本及model_selectable=false未改。原包并入真实入口/完整控制链测试参数，既测仓库体也测真实原包。原成功会话6个任务（1聊天+5图片）和原消息只读留存；五张图片均completed/published、闭合revision有效、message内容等于native result、taskID及输入actor/原会话/原素材来源吻合。只证明已有成果来源可用，不代表它们自动满足任意新方案或已执行复用。未新建Skill、未重复生图。

生产原三次样本由实际DB记录及同时间窗journal重新对上，模型均qwen3.8-max。created→started分别311.961/232.109/20.170ms（包含调度，不称纯队列）；created→completed分别10721.332/17184.859/18410.839ms。原pre-stream聚合context为1354/2488/1263ms；Gateway首任意增量817/996/1209ms、Gateway生成总6000/10178/14187ms，首任意事件可能含思考或工具，不能叫正文首字。Actor首正文距sink创建10210.661/16732.973/18280.377ms，正文均1块，245/3402/579字符；三个grounding_calls均1，分别unrelated/other_tool_chain/unrelated。append总11.888/36.045/22.077ms、send总0.878/2.072/0.989ms，不能把整段等待归咎于这些已测推送耗时。缺生产DOM首正文、细分首正文和全上下文准备阶段，未用相邻日志推算为不存在的字段，也没有候选生产after样本。

真实只读固定锚点历史/原资源与已有知识快照恢复也取得数据。最终加网络拦截的冷缓存读取为133.199/6.159/3.280ms，已有知识snapshot恢复/复核50.681/6.862/6.504ms，历史4/2/0条、资源0/0/1份。它们不包含PromptBuilder的记忆准备与完整查询链，不能替代原1.2–2.5秒context聚合。原请求“分析这张图片”漏在377窄识别之外；现增完整句匹配基本图片分析，需真实image附件且现有private/required/知识历史/当前user检查均通过，不匹配追加联网、价格判断、企业规则等复杂句。真实锚点输入/历史本地重评三条为None/None/user_images_only；前两条继续保守核验。旧输入已有知识snapshot仍优先恢复并核验，不以本次匹配丢弃原上下文。新请求可避开无关默认知识准备。最终只读取证的HTTP/Python socket出站尝试0，没有再触发embedding。

费用疏漏单列：较早一次只读Repository.search内部会走semantic.query_embedding，当时仅约束SQL写入，没有禁止HTTP，三个“退货”检索触发text-embedding-v3调用路径（生产开关和凭据已只读核实）。该次测得4030.649/1721.052/1662.452ms混合了可能的供应商嵌入，不能称纯数据库检索，也不算免费验收。未采集返回usage/结算，成功与现金金额不可确认；不得宣称0费用或追溯纳入后续60积分批准。已向用户当场披露并停止；补process-local诊断网络守卫和真实semantic.query_embedding拦截回归，后续诊断跳过该搜索。生产设置和应用检索能力没有关闭。LocalDBClient会覆盖conninfo options，因此最终只读脚本采用独立小连接池保留default_transaction_read_only=on并SHOW核验，同时关闭该进程的上下文Redis读写；仅用于本任务只读诊断。

用户另批原两Skill各1个文字规划样本：qwen3.8-max、累计60积分，初始最多8调用；用户随后明确同意保持60上限、累计次数放宽16。实际仅6次HTTP调用全部收到响应，prompt103544/completion1395 tokens，按分支170/510每百万费率折算18.313930平台积分；这是token费率等价，不是供应商账单。本地handler计费为0，没有生产平台扣分。预算守卫按保守上界留置及收到usage后结转，目前占用上界27；电商原包132439字节，下一长请求需保留39，高于剩余33，故在发出前停止；未花满60，也未为用满16追加请求。原响应/用量/指纹/包和原素材保存在私有目录；按完全一致请求hash复用已收到SSE，受理不确定不重试，未发送任何生图/其他业务写工具。

真实试验没有最终通过：两方法均能自主提出plan和选中原方法，随后出现evidence超过4条、交付提案与独立提取分组不一致；同一冻结原请求的blind extraction曾被重复请求，付费重复拦截器阻止再次发送。第一次试验2项失败，恢复试验中1项只证明安全返回、1项预算停止；安全返回时任务尚未初始化、没有完整规划，不能宣称一个Skill完成。现已收紧可选真实验收测试，必须有completed逻辑状态才放行，未付费重跑制造通过。正式原两个Skill真实文字规划/事实核验/交接及候选页面仍未验收。

三处产品修复：Qwen Max/Flash现将宿主明确max_tokens送到实际Chat Completions请求，未指定不发明默认；预检曾在发送前抓到该字段被丢弃，免费原生Gateway→Adapter模拟流证实修复。需求提案纠错明确每项evidence1–4条完整原句，仍逐项Pydantic/原文检查，不截掉证据或改数量。blind extraction只在同任务/actor/conversation/source、完整未截断上下文（包括图片）、同一核对指令/schema指纹、有效原文且无uncertainty时复用已保存typed observation；每个修正提案仍重新走宿主compare/apply，不重复模型调用、不增加usage/模型轮次。旧checkpoint无新指纹仍可恢复但不能命中缓存。超长新消息、image-only新消息、历史、指令版本改变均失效，不能用32k/20条投影裁切当缓存身份。反馈增加带来源数量的observed_requirements数据以便纠正，明确task_initialized=false时没有执行授权，blind reviewer仍不见原提案。未取消核验/权限/确认/真实成果检查。

最终免费定向239项通过、0失败/跳过，覆盖原包、原入口、完整本地阶段链/成图复用、真实JWT/OrgContext/任务slot/HTTP幂等/PG入队及固定历史、真实PG审阅保存/恢复、新缓存引擎分支只提取1次、超长/图片-only/新历史/指令变更失效、旧记录兼容、Qwen普通/stream与Responses回归、预算及诊断阻断、保守流式和知识snapshot显式引用/恢复。此前185/186/83/57等组合重叠不累加；其中引擎测试一度把当前已开始的第3主轮计数预期写成3，依原消耗语义校正为4，再全组通过，没有改产品轮次门槛。独立两次窄审未发现高置信阻塞，最终指纹修订也复核通过。前端复用378无相关后续改动的133定向/构建与213更宽证据，不重复构建或收费测试。

本批尚未提交/部署，未启动受控release或更改生产开关/锁/迁移。保留原ERP Agent、延期新增ERP写适配、上下文、权限、确认、计费、防重复、已有五张成图及原任务进度。剩余门禁是修复后原两Skill真实完整规划和原素材成果/页面接续、候选全生产HTTP/真实记忆知识/ModelGateway/Worker/WS同请求after计时。现有真实旧生产基线、隔离HTTP与检索/语义边界证据不能拼成候选生产全链成功；费用不足或不确定时停在已有授权边界，不自动增费，不提前放行部署。私有证据：/Users/wucong/.codex/task-evidence/skill-coordination-design/379。


## 380 原失败免费回放、上下文费用归因与方法工具一致性（2026-11-01）

本轮仅离线开发/验证，无生产读取、供应商调用、生图或部署。沿用原任务工作树；原ERP Agent、上下文、原包正文和已有5张图不变，新ERP写适配延期。按testing-bugfix复核，核心方法协议修改按review进行了独立只读审查。

新增recorded-output负向回归，MockTransport逐条重放379已经收到的Max响应，socket.connect/connect_ex均拒绝；验证旧坏提案仍未初始化任务、不派发业务、保留纠错证据，同一冻结来源的blind extraction只用一次。此回放按场景序号应用旧输出，不能证明新请求语义，也不能用旧response usage计算本轮费用。真实付费缓存仍必须请求SHA完全一致才复用。正式付费探针完成断言继续要求TaskState completed；安全返回不算完成。

内容不外泄的request_context_cost记录消息文字字节/工具参数字节/图片数/schema字节/原方法出现次数。380/failure-replay初始请求的body SHA均与379实际已收响应请求一致：reference正文14669字节、消息17814、工具2905；ecommerce正文131369、消息140815、工具26677。两原方法都只注入自身1次，另一方法0次，原电商正文占文字加schema约78.4%，工具约15.9%；字节比例不等于token归因。原第一电商请求实测40051 input tokens（其中2052 image），不是所有调用同价，缓存命中也不保证。不能擅自截短原方法换取成本下降。

既有覆盖比较把缺少两项文字内容称为“至少2份text”，容易诱导拆文档。只修正minimum schema/遗漏反馈表述：独立内容要求需分别声明，分析和五条提示词可由一份完整文本关联两个requirement IDs，item_count仍表示列表项数；仍拒绝漏分析/漏提示词，不放松图片份数。补结构回归，不声称仅凭文本元数据完成任意语义核验。

新发现实际方法/工具不一致：原电商image.planning=ecommerce.inline，服务器plan_ecommerce_images仅接受ecommerce.server_plan，误调用会要求activate竞争方法。沿现有workflow_contracts提供incompatible_protocol_tools，SkillRuntime在工具披露时只读投影未来plan action，兼容chat/bootstrap；未来CompositionConflict不阻断普通聊天，真正调用仍由原屏障处理。仅隐藏与实际inline owner不兼容的planner，不改变权限上限、不删普通工具、不改v1。prepare_tool_action在set_action前验证未来composition，拒绝猜调用并给出保留所选方法、直接策划/save_task_text的可纠正反馈，整批不派发、checkpoint不变。恢复后重新计算，不持久化过期工具列表；阶段驻留体不重新成为owner。

最终225项定向通过（/private/tmp/skill-final-regression-380.log），含原两Skill真实包、实际engine出站、旧入口、方法/恢复、Actor、覆盖/reducer等。187和62为重叠中间组合，不相加。原两个Skill出站实际回放都不含不兼容planner；电商保留erp_agent/file_search/web_search。电商schema26677→23863字节，正文完整保留；这不是实测token/耗时改善承诺。独立只读审查未发现阻塞问题。私有380/context-cost-comparison.json和verified-replay保留来源，无密钥。原378前端与379上下文/生产基线证据继续复用，本轮后端改动不重跑未受影响构建。

发布门仍未通过：原两个Skill修复后真实完整规划、事实核验与阶段交接/成果页面，以及候选完整生产链关联性能after证据仍缺。新编排默认开关未开启。下一批付费需重新报价等用户确认；不以225项负向/结构检查宣称完整实测成功或生产延迟已消除，不提交/部署。


## 381 新批100积分/12次实测止步事实核验，先完成免费修复（2026-11-01）

用户明确批准新增100积分/最多12次Max调用，不生图。本批单独账本，旧60批不重开。probe支持绑定(100,12)，新增预算/次数/旧账身份错配测试；旧收到响应仅完整request SHA一致时免费复用，无网络回退的验证模式单独提供。12次新增请求全部received，input149243/output12372；按分支170/510每百万token折算31.68103积分，保守settled_upper合计51不是实际账单，生产平台扣分0；供应商现金账单未查。旧批响应免费复用不计新增调用，不用包含缓存usage的Actor汇总计算本批费用。次数已耗尽，停止全部付费，未自动用余预算继续或扩大次数。

首轮新增3次后未初始化：模型第二提案已分别声明文字策划item_count1和五条提示词item_count5，但共享“仅文字不生图”的证据导致compare_observation用途映射歧义。免费实际响应复现后，优先使用独有原文证据；共享引用只在已唯一定位的冻结计数集合约束下消歧，仍检查独立义务、份数、item_count和集合唯一性，不按标签/顺序/数量猜用途。新正反顺序测试：真实正确分组covered，共享原话加数量仍uncertain，宽泛全文引用仍uncertain，错数量/漏内容仍missing。独立只读审查无阻塞发现。

第二次调用路径已写出并保存完整五条规划，格式profile通过；但验收夹具预设runtime.task_plan_source_provider被execute_chat原工厂覆盖，h.db占位object触发AttributeError。这是探针接线错误，不是生产数据库故障。修正真正create_source_provider seam，并以保存的实际调用序列免费重放、禁所有socket，验证到同SHA原图来源与待核验完整草稿；原生生产资源/权限路径仍依原有native测试证据，不用此seam冒充生产等价。

另一次重启时探针bootstrap随机生成新的root/actor/source标识，初始化后的schema上下文不再精确相同，造成规划后半段额外付费重跑。已向用户说明此前“无需重付费生成”的判断不准确。费用和调用全部纳入上述12次，不删账。新增pinned_probe_scope固定诊断bootstrap/anchor，绑定完整goal、原方法revision/bodySHA及原图SHA；变化时拒绝默默按新任务付费。保存prepared_messages、Skill checkpoint及plan reviews，且已把当前真实任务身份冻结。新增精确免费回放：实际请求SHA逐一命中381/379收到的响应，完整实际草稿保持同SHA，恰在变更后的纠错请求停止，禁全部socket，无新付费。旧序号回放仍只用于协议负向测试，不能代替此精确复用证明。

最终reference状态partial：五条完整规划已保存，但事实核验模型把claim概括改写，未逐字取plan_sections原文；evaluate拒绝为uncertain，无proof/阶段完成。随后模型把泛化“来源/原文/覆盖不能核实”当成素材问题，猜fact_references被拒绝。免费修复明确区分核验输出协议不合格与商品事实被否定：非逐字claim给具体节名及复制连续原文要求，仍None/uncertain；source_error=None时指引保留artifact_id/原图，对同稿重审而不重写草稿。沿既有complete_task_step→isolated review路径，未新建重试链。仅同artifact ID/SHA及来源SHA时，把上次uncertain协议诊断交给隔离核验器，作为非商品证据的host feedback。真正source_error和rejected事实处理不放宽。旧checkpoint字段结构不变，None observation的旧uncertain仍恢复。独立只读审查无阻塞发现。

免费最终主组合283项通过（含原方法/入口、匹配、来源、事实核验、controller和Actor）；随后精确复用及预算组合10项通过，与283重叠，不相加。diff检查通过。未受影响的前端/生产基线证据复用。本轮没有生产数据访问、生产变更、生图或业务派发。原ERP Agent、新ERP写延期、原上下文/权限/确认/计费/防重复机制和已有5张图片保留。两版已收到的文字草稿都保存至私有381/saved-plans，未覆盖旧图或上轮证据。

本批未整体成功：reference事实核验纠错后的新模型效果/最终完成/阶段交接未新实测；ecommerce本批未新启动；生产完整入口及页面关联性能after证据仍缺。12次用满，不再申请或执行隐式付费；下次如需付费需明确报价和确认。发布门未通过，未提交/部署。私有381/batch-summary.json、scope、全响应账本、过程失败证据和logs可追溯，不以免费回归通过冒充真实语义验收完成。


## 382 用户授权受控部署供生产验收（2026-11-01）

用户在已知381未完成验收、已停付费的前提下明确要求“提交部署”。本次按现有deploy/release.sh提交完整当前任务并发布前后端，执行原测试/构建、锁、数据库身份、迁移账本和readiness门禁；不以此将未完成业务验收改为成功。默认agent_task_execution_enabled=false和agent_task_erp_write_enabled=false保留，不自动更改生产配置、扩大编排受理范围或恢复延期ERP写功能。原ERP Agent保持原路径。付费批次381的12次已满，本轮发布/健康检查不运行任何付费模型或图片调用，不重复生图。发布结果以本次受控入口结构化结果为准，工作树待验收保留，不合入main或清理。


### 382 首候选发布检查安全拦截及测试契约修复

候选c0ef7dc318263702363c517428e2af0810aed1d1由受控入口提交推送。前端242文件/2731测试与构建通过；后端13811通过、1215跳过、4xfail、11失败，入口返回failed_safe，阶段仅preflight/tests_and_build，未同步生产文件、执行迁移或重启。未绕过检查或恢复锁。

11失败集中在5个旧测试文件：不安全目录mkdir权限受发布umask077掩盖（现显式chmod755后仍断言blocked）；worker.__new__夹具未初始化新增video loop/pool（现同时断言三路取消/关闭）；确认测试动态Mock把未配置prepare_file_resume当异步能力（显式None/boolean，真实确认断言保留）；旧Actor Mock动态生成新增reader/followup属性（改用真实ConversationTurnRuntime，仅safe_point为AsyncMock）；6项ERP契约期望尚未登记既有purchase_strategy_calculate从读改为异步写的安全修正。新增静态两schema升级，原不可变baseline不改，registry仅核对该条is_write/描述后还原两字段继续整表hash，其他接口/权限/确认契约不放松。原ERP Agent及延期ERP写开关未改。

550项定向回归通过，独立只读窄审无阻塞发现。修复仅测试/证据文档，不改应用行为；仍按新候选重新执行完整受控发布，不能复用失败结果冒充成功。不新增付费模型调用、生图或业务写验收。最终结果以新入口结构化RELEASE_RESULT为准。


### 382 同SHA续发的权限故障、受控恢复与生产Actor兼容

67b8c47bbd6eee7e210e6bbb7d53254f29716897已完成完整前端2731/后端13822通过（1215按条件跳过、4xfail）。首次远端写入后因操作调用继承umask077，使SQL新文件600，postgres无法读取340，事务未提交且340账本无记录。前端同样403，后端health正常；属本次调用错误，已明确向用户说明。核实无writer/原锁/健康后通过release.sh --recover-lock归档，不删锁、不伪造成功。正常022环境补795项权限及兼容检查通过，同一SHA沿原入口复用通过测试、保留构建/身份/账本/健康门。正常权限续发已使前端HTTP200、index644；340–345事务成功记账。

随后346精确hash检查拒绝原生产Actor定义，事务回滚，未建立wrapper/迁移账本；发布再次停止并保留锁，服务尚未重启。只读获取两函数定义/owner/ACL/definer，owner=everydayai、invoker、仅原执行权限。生产pause body c21ad0c393cf26acaa85375f757cfd19，cancel bd171df8d471c36ab93aa1a3d7383997；原346只接受repo239的4b315fa32edae89ca625d343cd51b20e/83bee41969f36ac93b23dad23f5c582b。生产pause原调用cancel_snapshot，repo239调用pause_snapshot。不得用本地正文覆盖生产现有上下文行为。

仅为尚未应用的346补精确已核实hash，原owner/invoker/alias/未知hash守卫不变，仍rename-delegate原体并保留owner/执行ACL；不改已成功340–345。新增原生产定义静态测试夹具，无用户数据。真实隔离PG在两种表owner下验证repo/production原体与wrapper、rollback owner/body/ACL/definer一致，有效ACL不扩大，未知body拒绝无部分alias。原7条Actor协议在repo版本全过；production原7条在包装前后均6过/1既有pause marker失败，未修改原断言。进一步固定该失败最终行231及归一化失败信息，防止仅凭longtrace关键词误放行；另独立SQL roundtrip验证暂停状态/owner token释放、最后文本保留、input/output修订均1、原marker分别user_pause/user_cancel、重复暂停幂等及取消胜出，比较包装前后完全相同。该既有生产marker问题未修，不称生产7全过。最终本地20项通过、独立审查P2已关闭。

已确认后端health正常、前端恢复200、无在途writer、346未记账，再由原入口恢复原锁并归档。后续新提交须以其发布结果为准，不绕过迁移或健康门，不自动启用新编排/ERP写、不追加付费验收。调用日志必须单独预建0600，不再向整个发布进程传播umask077；公共构建/迁移文件沿正常022权限。


## 383 用户明确授权开启新编排、ERP新增写继续关闭（2026-11-01）

用户指出关闭无法验收，明确要求新编排开启，用原两个Skill验收，新增ERP写继续关闭。此决定更新382保持默认关闭的操作范围；不重新设计编排或放松旧权限，不修改原Skill、上下文、既有成果或计费/幂等机制。

原release入口缺少此显式配置选项。本次仅扩展原normal-release参数--enable-agent-task-execution，拒绝frontend-only/rollback/recovery/accept，转交当前executor。默认不改任何flag。deploy_backend依赖/既有准备通过后，由既有env-patch.py按原lock token校验、私有备份和原子替换，精确写两键true/false，随后原四服务重启及readiness。不新增配置交付链或数据库/业务操作，配置失败在重启前停止。

新增实际shell函数本地IO替身检查prepare→patch→restart、默认不改、失败停止、精确两键及其他配置/备份600保留、错误mode在生产IO前拒绝；既有fake Git/SSH协调测试验证参数转发。旧preflight夹具缺既有invalidate IO stub，补齐并保留/增强构建先于失效及同步的断言。最终40项通过，独立只读审查无阻塞发现。应用/前端/迁移代码与已完整验证8d8e062e相同；补原包/上下文/ERP延期边界验证，受控发布复用未变应用的完整证据，仍构建、核验数据库/迁移、重启及健康检查，不执行任何付费模型/生图验收。正式启用结果以原入口结构化结果及生产只读开关检查为准。


## 384 生产问候误入知识审核、手选方法在盲提取中丢失（2026-11-01）

生产只读SQL按原Actor/org运行域与指定任务核对，未调用模型、embedding或生图。你好任务完成但默认知识准备active=true，功能介绍任务被答成资料不足。根因是self_contained_reason把整个messages序列中的knowledge_search/read/outline字符串视为检索依据；真实通用system工具说明本来含这些名称，窄单元输入未复现完整提示词。旧HEAD加真实TOOL_SYSTEM_PROMPT的问候路由为None，修复后self_contained_chat。新规则仅忽略通用system工具可用性说明，实际宿主知识包、历史工具调用/结果、摘要及引用仍要求校验；显式引用、已冻结快照和required仍由原KnowledgeRun.start恢复/复核优先。未知或企业私有请求不豁免，不关闭知识库，不新增固定分类模型。

原图+“要风景类型的”任务已加载用户手选reference-image-prompts原revision。主模型提交六套文字提示词，独立v2提取器看不到手选元数据，三次均提成image且数量不明，最终未初始化；不是此处又发生两个Skill同时覆盖。现将宿主冻结的user/session非resident方法id/名称/revision传给盲提取器，不传提案、Skill正文或模型推荐；文字默认条数不得授权新增成图。真不确定且来源/摘要指纹有效时，保存一次澄清问题，走原Actor最终交付与commit_ready恢复，不继续同输入反复改不可见提案。协议错误和无效引用仍保留原有限纠错与拒绝边界。

页面每轮“核对回答依据”过去在模型调用前创建，即使回合只选工具、根本未执行_verify_knowledge_answer也标绿。现仅在实际最终回答审核开始时展示该卡片；工具回合草稿与思考仍暂扣，最终审核后才发布正文。任务工具沿原占位文案映射显示整理要求/规划步骤等，不改变协议、账单、执行或检查结果。没有另建编排链，无DB迁移，原ERP Agent与新增ERP写关闭保持。

本地定向268项通过：真实PromptBuilder工具说明两种缓存布局、问候/功能介绍/图片分析、真实知识来源仍暂扣、真实增量先于生成完成、真实工具循环不假报审核且草稿不泄露、本地PG的默认知识跳过/显式快照恢复、原两Skill仓库与完整生产导出包入口、原手选+图片+风格短句的隔离元数据/文字初始化、纠错/交接/复用、澄清BEFORE_COMMIT保存与restore_review/commit_ready解码、后续短答的固定历史保留。该原Skill新增用例使用脚本模型观测宿主边界，不宣称真实千问语义已通过。独立只读审查无阻塞发现；未发付费请求、不重复生图。前端相关检查与完整受控发布结果另记，未以本地测试宣称所有任务性能或整套业务已通过。
