# Agent 升级：安全审查与功能保留验收

## 当前确认范围（2026-10-31，372接续）

用户已明确：保留原ERP Agent，暂停新增ERP写操作适配，继续其它升级。原erp_agent入口、专业规划/部门Agent、查询和追问上下文、权限/预算/工作区/原结果协议保持；86项新增写入终态契约从本次实施及放行范围中移出，不再继续逐项扩展。已开发的ERP恢复和防重复代码/账本保留，冻结不等于已经验收或删除旧效果。原ERP功能兼容仍属本次回归范围；默认新任务总开关仍false，未作生产配置变更。

本次不新增共享群聊的进度读取权限。当前媒体/异步协议仅支持个人作用域，因此群聊新任务继续原Actor/Skill流程；个人新任务才可进入新版编排。已有_task_bootstrap/_task_execution优先按原恢复协议处理，不被新的准入条件删除或降级。新准入边界不影响原群聊功能，不表示群聊新版媒体编排已适配。

发布前仍需完成：非ERP范围最终调用链/迁移与在途恢复审查、原两个Skill在原素材上的真实供应商与最终页面验收、确定最终候选后的受控发布检查。复用已有效的本地与页面证据，不重复生成已有图片；付费前先给预计费用并取得用户确认，整套本次范围验证通过后才部署。


## 当前费用约束（用户于2026-10-30确认）

继续开发和本地验证；尽量复用既有日志、规划、真实验收及有效测试证据。暂停所有新的付费模型、图片和其他收费业务测试。任何需花钱的验证必须先给出预计费用与范围，并取得用户确认；旧有真实测试授权和opt-in环境变量不构成新的费用授权。保留原供应商，不自行换模型；即便额度恢复，也不自动重跑。整套验收通过前不部署。本地回归必须关闭live opt-in，不能把离线通过算成真实模型/供应商/UI通过。


日期：2026-10-29。状态：独立设计初审四项缺口已修订并经只读复核回应；本轮未发现新增阻塞级协议矛盾。详细契约、实现与发布验证均未完成，不代表升级安全审核全面通过。关联 [整体方案](TECH_Agent通用任务编排与执行闭环.md)。

目的：把“现有功能保留、升级有效”变为可追踪的交付条件。没有发现问题不等于证明全部安全，测试文件存在不等于行为通过。

## 1. 审查对象与证据规则

- 设计审查：验证职责、信任边界、状态一致性、兼容与回退是否闭合；未实现的设计缺口不能写成已存在的生产漏洞。
- 实现审查：对最终实际 diff 独立检查正确性、权限和变更影响，不由作者自评替代；只读审查不直接修改被审对象。
- 验证审查：核对真实行为与目标，重点覆盖跨模块和故障边界，不以单一覆盖率、测试数量或工具成功代替业务完成。
- 发布检查：绑定最终候选、配置、迁移与环境；仍有效的同内容检查可复用，受修改影响的证据必须重验，不能把旧 SHA 的无关检查拿来放行。
- 每项结果只允许“通过/失败/未验证/不适用并说明原因”。环境受限和跳过不得记为通过；已知基线失败与本次回归分开记录。
- 独立审查缺陷须记录位置、触发场景、影响、严重度、修正与复核证据。核心权限、数据一致性、重复副作用、历史恢复或关键功能退化未解决，不满足发布条件。

## 2. 功能保留账本

以下是当前已识别的保留面，不声称已覆盖全仓库。实施前按最终调用链补齐接口消费者及原行为样本；受影响能力必须有负责人和验证结果。既有测试仅为定位入口，仍需检查断言是否证明目标。

| 编号 | 保留的能力与约束 | 既有验证入口示例 | 新增交叉场景 | 本升级结果 |
|---|---|---|---|---|
| C1 | 历史、压缩、摘要、记忆及用户修订；工具调用/结果配对 | test_context_compressor_web、test_context_summarizer、test_session_memory | 长任务压缩后续接；新要求优先；旧工具历史协议一致 | 未验证 |
| C2 | 附件、文件引用、作用域与模板/知识访问 | test_resource_scope_reproduction、test_file_search_default_scope_08 | 前一步文件传下一步；过期/删去资源；跨组织引用拒绝 | 未验证 |
| C3 | 手选、自动发现、固定和计划任务 Skill、不可变版本 | test_skill_coordination、test_scheduled_skill_snapshots | 阶段交接不解除全局限制，原两个 Skill 完整规划后执行 | 未验证 |
| C4 | auto/plan/ask、组织/个人权限及现有确认 | test_tool_policy、test_permission_mode_e2e、test_execution_scope | 内部续接不自行批准；权限撤销；模型参数伪造身份拒绝 | 未验证 |
| C5 | Actor 单写入者、取消/暂停/恢复、租约和检查点 | test_conversation_actor_recovery_matrix、test_conversation_actor_resume_integration | 双执行者/重启/事务失败；逻辑任务和执行状态一致 | 未验证 |
| C6 | 工具与图片受理幂等、不确定副作用处理、计费 | test_tool_invocation_uncertain_integration、test_chat_image_request | 换 call_id 重规划；提交响应丢失；重复续接不重复付费 | 未验证 |
| C7 | 原生图片、聊天图片、原方案执行、比例数量、编辑、重生 | 图片生命周期与电商工作流测试族 | 等五张图全部终态再整理；只补失败项；原生媒体参数不被文字覆盖 | 未验证 |
| C8 | 工具目录、按需发现、安全并行和 ERP 专业能力 | test_tool_policy 及 ERP/工具循环测试族 | 无 Skill 多步执行；依赖不乱序；独立只读操作并行 | 未验证 |
| C9 | 定时任务确认/预检/快照、暂停/恢复、完成 gate、结果投递 | test_scheduled_task_creation_confirmation、test_scheduled_task_delivery_worker、test_scheduled_task_agent | 新编排不扩后台授权；重复触发/投递；旧配置继续使用 | 未验证 |
| C10 | web/企微、工作区身份、消息流与刷新恢复 | test_wecom_actor_enqueue、test_channel_context_isolation、前端消息块测试 | 内部续接正确会话/通道；断线继续；旧消息显示兼容 | 未验证 |
| C11 | 模型网关、调用预算、供应商协议和工具错误 | test_execution_budget、test_dashscope_responses、test_dashscope_chat_adapter | 跨回合预算不重置；总量收敛；长 ID 与配对历史 | 未验证 |
| C12 | 旧 checkpoint、已受理任务、迁移与可回退发布 | test_replay_checkpoint_integration、真实隔离 PostgreSQL | v1/v2恢复不升级；新协议关闭新建后在途仍可消费；历史结果不丢 | 未验证 |

实施时按最终 diff 复核是否还影响视频/音频、代码执行、MCP、知识回答验证、资源生命周期等消费者；不能仅因表中没列名字就跳过已受影响功能。

## 3. 升级有效性的验收

- 原两个 Skill 使用原素材与原请求验证，不新建测试 Skill 代替业务复现。
- 无 Skill 的搜索→来源读取→分析→文件，及文件→计算→报告，必须完成全部交付。
- 图片→等待真实结果→整理文件，页面断线后仍可继续；重复/乱序事件不重复整理或收费。
- 仅提示词、普通问答、一步查询和已有最终提示词执行保持直接体验；不能全都套入多步计划。
- 真实缺项只问必要问题，正常授权内接续不重复确认；已规定的外部写入审批继续生效。
- 必需交付缺失不能标整体成功，部分失败必须展示真实缺口和可用成果。

## 4. 验证顺序与放行条件

1. 设计缺口先闭合：功能保留账本、权限语义、事件所有权、上下文和回退契约有可实现定义。
2. 实现者定向验证状态归约、权限、依赖和错误行为；独立审查最终 diff，修正后复核受影响项。
3. 真实隔离 PostgreSQL 验证 RLS、函数角色、CAS、租约、事务回滚及并发/重复事件；核心状态与安全边界不能仅 mock。
4. 受影响模块回归及前后端构建；按最终影响范围和发布入口要求扩大验证，不为数字默认重复全量。
5. 同样例基准及真实模型/界面验证；记录成功率、人工接管、漏步骤、追问、首字/总延迟、模型轮次/Token/积分与证据。
6. 整体候选才进入受控发布：无未解决的阻塞缺陷、关键保留项及新流程通过、迁移/在途/回退验证齐全。生产测试和用户验收按项目 release.sh 流程执行。

当前没有基准数字，不能声称延迟/成本已达标；阈值在基线建立后写明，再评估候选。安全/幂等类测试一旦发现越权或重复副作用即失败，不用平均通过率抵消。

每条证据记录行为编号、实现版本/候选、环境、命令或实际操作、结果、日志位置、限制及复核者。文档通过不能代替实现通过，内部测试不能代替真实用户验收。

## 5. 当前结论

基础状态、Actor 检查点及主循环控制接入已开发并做定向验证，整体未完成；进度与代码审查修正见 [开发记录](IMPL_Agent通用任务编排与执行闭环.md)。仍不具备“整套代码审查通过”“功能全部保留”或“升级成功”的结论。上表“未验证”指对应完整升级流程，不能用基础模块通过替代端到端结果。

本轮独立实现复核发现并修正主循环 revision 偏移、预算工具末轮异常退出、缺项未向用户交付、防重复 guard 丢失、逻辑终态恢复及过早 ready 遗漏后处理等问题。复核确认当前控制适配与最终化/恢复边界无新的高置信阻塞；限定范围结论，不是整体批准。125项定向集合、文件 emit 正常/检查点/实际 Executor 恢复等价，以及34项真实 PostgreSQL 集合见开发记录。快知识尚缺可信成果 adapter，当前只阻止无工具空转，不记为知识闭环通过。

后续初始化/阶段批次独立审查又发现并修正 bootstrap 丢确认、空白成果、宿主上限丢失、方法适用性及省略初始化绕过等问题。当前最终定向集合778通过、真实 PostgreSQL 36通过；独立代理针对实际派发与证据边界运行23项通过。结论仍只覆盖已实现批次；现成提示词、动态方法及异步等未闭合，默认入口继续关闭。详细证据与限制见开发记录。

最新增量已实现动态方法追加、作者阶段声明、canonical 文本复用及已声明 capability 的通用依赖派发门禁。独立审查发现的 pin/profile 错配、复用任务未来历史泄露及未知 capability 静默失配均已修正并复核；最新扩大集合1010通过，原 Skill 审核发布/目录真实PG另组68通过，集合有重叠不相加。仍未接通旧普通提示词/旧方案导入、稳定业务调用账本和异步接续；此证据不能使 C1–C12 完整升级验收转为通过，默认开关仍关闭。

稳定 operation 与原 native 调用账本的内部事务桥已完成定向开发：扩大回归1064通过，最新定向56通过（含42真实PG）；确认新call ID不获得二次派发权、uncertain不重发、回执/两份状态同事务、失败回滚、跨actor/空Actor拒绝及全租户回退守卫。独立复核该批54项通过，未发现高置信阻塞。以上范围不含真实 ToolRuntime/图片 adapter 与异步消费者接入，也不等于已完成所有副作用防重或用户端验收；见开发记录具体待办，C1–C12 保持未验证。

后续 canonical 方法图片路由已接实际 native 生命周期、完整参数/交付槽位和明确未受理纠错；修复审查发现的原调用未配对恢复P1及只剩辅助查询时不进入等待P2。并修复真实 Actor 原始异步池缺少任务级数据库身份的集成差异。342 在原生受理事务内登记持久 child 依赖，父聊天关闭不丢。该轮扩大1007通过，独立增量71通过；均未进行付费生成/真实模型/生产写入。事件消费、内部续接任务、真实图片成果/后续步骤与全部旧路由尚未闭合，C1–C12 完整升级验收仍未通过，不部署。

## 6. 独立设计初审与修订

独立只读代理 `architecture_safety_audit` 检查方案及上下文、入队、作用域、权限、资源版本与调用账本。未运行实现测试、未访问生产。以下为设计缺口，不是已验证的生产漏洞：

| 发现 | 影响与修订 | 当前证据 |
|---|---|---|
| P1 模型可修改完成检查所依赖的交付要求 | 分离用户 requirement 与工作计划；绑定真实修订消息，禁止模型自行降级必要交付；增加删步骤/改optional负例 | 方案 4.1/4.3 已补充，未实现 |
| P1 内部续接未定义不可变上下文锚点协议 | 独立可信 continuation 输入/适配器与 context builder；固定分支/revision/历史边界；不伪造用户、不修改旧anchor | 方案 4.4 已补充，未实现 |
| P1 新 generation 可产生新副作用调用身份 | 稳定 operation identity、事务预留与原账本映射；跨call_id回放、uncertain禁止自动重发、修订/取消竞争检查 | 方案 4.4 已补充，未实现 |
| P2 工具逐项接入资格与未支持行为不明确 | 建立按能力/action/入口库存和 legacy_only/adapted/verified 状态，未适配保留原路径但不冒充新闭环支持 | 方案 6.1 已补充；完整库存仍为实施前要求 |

补充验收：模型删必要交付；续接时新消息/并行分支/压缩/权限及通道变化；新旧续接同时派发、响应丢失、取消与派发竞争；未适配工具不丢原能力也不被错误标为新编排已支持。

独立复核结论：四项初审问题在设计层得到实质回应，可以进入详细契约与实施准备。实施前须固化 requirement 修订、continuation/context anchor、operation 预留与事件的 schema、唯一约束和 RPC 事务边界，以及原账本映射/恢复状态表和真实能力库存。测试必须覆盖实际竞争和失败行为，不能只检查字段存在或 mock 成功。复核未运行实现测试、未访问生产。


2026-10-30：343图片消费者/原子内部入队/真实发布成果推进已开发，隔离PG定向48通过；五图链路覆盖真实原生受理/发布与计费事务、原Actor claim、Executor、检查点与最终commit，以及重启/丢确认/并发/权限负例。供应商结果为测试模拟，execute_chat装配为测试替身，实际调用主循环终态guard；没有付费或页面验收。独立审查发现的已关闭原输入无法收尾P1已修复且4项独立PG复验通过，新增范围还在复核。原两个Skill完整用户流程、跨入口取消/暂停/目标修订和资源/上下文全链仍未验收，C1–C12不改为通过，默认不启用、不部署。详见开发记录343增量。

343后续独立P2（可额外插入伪造user）已修复并独立44项PG复验；重复历史/source CP consumed/下游说明步骤追加后定向48通过。阶段恢复不再依赖旧UI块来决定已完成方法是否重激活；方法/Skill定向80通过。旧requires常驻约束不放宽，其与阶段退场的组合仍待处理/验收。扩大回归2160通过42跳过为最终加固前证据，最终聚焦421通过为后续证据，集合有重叠不能相加。所有证据均不把C1–C12改为整套通过。

343最终变更扩大2175通过42跳过；跳过中的29项Redis已通过独立本机Unix socket补测（对应整组79通过），7项旧Actor opt-in PG测试已在两种owner下分别7通过（外层2通过）。上下文/记忆/权限/附件/定时Skill/DashScope补充279通过。仍有6项真实模型验收未执行，日志/重叠范围/旧后台任务清理诊断见开发记录。不能将这些旧功能回归覆盖换算成C1–C12完整新链路通过，仍不部署。

2026-10-30阶段/旧方案/控制增量：resident真实依赖与恢复证明、344真实旧来源、345逻辑控制及346保留原Actor协议锁序已接入；source-only等待遗漏P1已修并独立复核，新实际loop/原生受理/claim/Executor/commit集合63通过。原两个已发布包只读取回并做原包集成2项通过，不能替代真实模型/真实图片/页面验收。扩大集合曾有6失败，修复后定向159通过；该旧扩大日志不标全绿。真实qwen原样本发现E把实际图片漏提为text，宿主覆盖审查增量正在验证，独立初审指出的旁路与pause窗口已修改但未完成总审。当前C1–C12不改变为通过，开关关闭，不部署；细节和最新证据见IMPL本日接续段。

本轮加固后：扩大3811通过55跳过；保留host历史摘要增量及独立Redis/真实PG图片生命周期77通过（集合重叠不相加）。多组effect排名P1已用实际无二次IO回归和独立原样复现确认关闭；两原Skill真实provider均完成五图规划与阶段交接。新任务usage绑定实际owner保存，续接不复用已结算源用量。以上仍不覆盖真实供应商产图/UI、所有业务adapter、用户修订及完整资源事实边界，不将C1–C12改为全通过，不部署。证据日志与不同代码迭代边界见IMPL本轮末段。

最新独立范围核对真实provider opt-in四例实际4通过，含两原方法实际图片要求、遗漏图片反例及明确prompt-only；定向111通过4跳过的provider四例已用本轮独立日志补证，不能累计或换算成整体端到端通过。自有临时PG/Redis已停止，当前不发布。


## 2026-10-30 当前批次验收边界

- 已接：v2逐张原图绑定及native受理复核；原资源权限透传；新入口关闭后的在途校验；347原生正证据回执核实（含handler缺回执立即核实）；原ready方案当前/历史导入；v2盲提取+host逐交付义务比较。
- 扩大集合3856通过、55跳过，日志/private/tmp/agent-task-blind-source-native-final-regression.log；此后逐义务配对/独立准备步骤等增量有单独定向集合，不能以较早全量结果替代其验证。独立审查曾指出多text内部条数漏验P2，已改逐义务比较并新增负例，待复核结论。
- 原供应商隔离核对5通过，含原P/E背景、丢image负例和只要提示词。原两Skill真实完整规划+资源绑定仍重跑；此前实际发现的binary误判、动作矛盾、错误能力标记和供应商超时全部保留为失败证据，没有计为全链通过。
- 实际ImageHandler+scoped PostgreSQL4通过：开关关闭后在途v2仍检查、非规划提示词拒绝、精确已存snapshot重放只保留一个子任务/预算一次；该集合未调用图片供应商，不代表最终图片或页面成功。
- 尚无整套原两Skill真实产图和页面验收；其他业务adapter、用户修订及多入口资源/上下文功能账本未闭合。本任务未提交、未部署、开关默认关闭，不得宣称所有功能零损失或可永久避免所有冲突。


本批追加结论：逐义务配对/计数集合保持/准备步骤与原生复核最后定向110通过，日志/private/tmp/agent-task-per-obligation-stage-final.log。独立复核33项通过，关闭多text条数与冻结后方法数量回落两个P2。该结论不扩展为全系统验收。

真实原P规划诊断已推进到规划保存和方法完成，但产出的商品事实把原图“8入”写为“8寸”，并包含未核实材质。该业务规划未通过人工验收，已停止该次只读诊断，不继续供应商产图；没有图片业务工具派发。日志/private/tmp/agent-task-original-live-per-obligation-binding.log保留。当前核对层只证明交付协议，不证明商品事实真实；后续须把专业事实/成品质量核验接在方法完成与生图前，并处理需修正规划的阶段状态，不能把结构通过当专业通过。原E完整链仍未运行完成。另多个独立计数提示词集合与方法数量的整体映射仍需专门验收，不能以单集合五条正例代替。


## 2026-10-30 v3事实与原生只读成果的当前证据

已完成本地：规划核验拒绝→修订→批准→真实PG保存/阶段交接；来源字节、角色、用户原文/引用数据快照核对；旧协议恢复；原生只读执行及缓存消费→原账本→受限RPC→成果导入/压缩恢复；未知结果/撤权/跨身份/回执变化拒绝。原生v3受理采用实际ImageHandler/受限PostgreSQL，模型核验输出为模拟，不调用图片供应商。

最新定向：37通过（/private/tmp/agent-task-read-integrity-final.log），18通过（/private/tmp/agent-task-facts-native-v3.log）；此前39与161通过按原范围复用。集合不累加。扩大回归3922通过、64跳过（89.73秒），日志/private/tmp/agent-task-facts-reads-regression.log，文件清单/private/tmp/agent-task-facts-reads-regression-files.txt。跳过项不是验收通过；本批不重复收费opt-in。

尚未完成：原两Skill真实事实纠错/供应商产图/自动续接后页面交付，以及其余领域adapter、多入口和目标修订整体验收。DashScope欠费阻塞过真实核验；当前用户又明确禁止未确认费用的测试，所以不自动重试。没有整套部署条件，保持默认开关false。

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

末次352真实PG18通过（/private/tmp/agent-task-scheduled-rollback-final.log），包含跨租户drain guard、错误action/hash改绑拒绝与失败后FORCE RLS恢复。未重复953未变化范围。自有临时PostgreSQL在本批结束时停止，保留私有日志/原输入/任务树；不清理工作树、不提交或部署。


## 2026-10-30 增量：后台回执续接及 ERP 发送记录

本地扩大回归1188通过/9跳过（27.89s，/private/tmp/agent-task-receipt-erp-current-regression.log），覆盖全部task_execution、原chat loop及ERP SDK/mixin。未执行live flags，未消费模型/图片/视频或外部ERP。新共享Actor CP helper为continuation_checkpoint；原continuation_context曾误覆盖，已按本会话原始patch恢复完整旧接口，原10项context及扩大回归通过。

之后明确方法级完成准入与managed Redis no-send规则，并补正式model_bridge CP丢确认和完成前回执变更拒绝：83通过（2.05s，/private/tmp/agent-task-erp-checkpoint-final.log）；22 ERP单元、18 PG、20原bridge、10旧context、2 wait、11旧mixin。此组与1188不累加，仅覆盖后续窄改动。原SDK已有tenacity，单测确认managed request_once只有一次mock HTTP post；旧SDK/mixin原测试通过。实际原ToolExecutor分支验证Redis锁/释放/close和未发送参数错误不写done。PG确认341/139与原CP仍一致、原messages/opaque值保留、格式化丢返回的正回执恢复不改原139 payload、新call_id未知不重发、参数纠错必须新确认/新native identity/旋转nonce、旧nonce无效、过期/取消在发送前拒绝、全租户非空回退拒绝并保留FORCE RLS。

独立窄审只读354/341/控制/SDK/dispatch，未发现可证越权或重复发送。审查提出不能用tag_add测试覆盖全部ERP端点；实现已限制COMPLETION_METHODS={erp.item.tag.add}，未审查method仅accepted，明确异步/部分失败不算交付。最新Redis与方法准入窄diff另请只读核对，未称独立重跑。

仍未验收：ERP其他方法的完成/部分失败协议及异步后续，文件删除/恢复和真实备份版本，视频原计费/回调/发布，目标修订/多入口/前端及原两Skill真实产图/页面。默认agent_task_execution_enabled=false；没有提交、部署、外部业务写入或付费调用。必须在整个范围通过后再发布，付费测试仍先报价并获新增确认。

末次独立只读核对已完成：方法allowlist、session.dispatched与原Redis/锁/close分支、原子CP失败与receipt变更拒绝断言均无新增高置信问题；未另跑测试，不代表全项目审查或外部验收。当前新鲜临时PG仅用于本地fixture，结束时停止，任务源码和证据保留。


## 2026-10-30 文件恢复增量

1051通过/9跳过（32.48s，/private/tmp/agent-task-file-restore-final-regression.log），所有task_execution与原文件入口/边界/执行器/mixin/身份/资源连续性；后续proof验证顺序及实际下载后取消/lease过期不发布的窄验证65通过（2.86s，/private/tmp/agent-task-file-restore-fence-final.log，28 PG+7 controller+20 bridge+10 context）。不同集合重叠不累加，前批未变化的SDK/旧chat证据复用。原跨进程锁用例曾因从repo root启动而子进程不能import services，backend目录原240集合6.21s通过，没有修改生产锁或放宽测试。

新增PG沿真实原ToolRuntime/PreparedFileCall/原FileDeleteMixin恢复路径/139/341/348/351/354/355，OSS返回为本地替身，非外部验收。核已恢复文件的inode+size/mtime和全SHA；同名同SHA不同inode不认领，新目标不覆盖；原If-Match、原记录/期限制约、workspace写锁、当前scope/restore/read权限保持。领域settlement与原deleted_files更新同事务；返回丢失只核实原文件并补事实，不再download/link，不改原139失败结果。文件目标保留file，receipt目标保留receipt；read授权缺失或文件变化拒绝导入/完成。CP保存失败仍保留完整原消息边界和初始状态。原通用returned不允许绕过file domain settlement；355 rollback跨租户drain失败仍保留FORCE RLS/领域证明。原call ID仅重放，未发布下载错误可用新native attempt有界纠错，取消/过期后不发布。

独立只读审查最新恢复机制与filekind/权限及新增断言，未发现新增高置信问题；没有独立重跑，结论仅覆盖本轮。文件删除、其他ERP方法及异步后续、视频、目标修订/多入口/UI和原两Skill整体验收仍未闭合。snapshot在未知时保留、确认后清理；dispatch后无可靠发布事实不自动重做。默认新入口仍false，未提交部署；没有模型/图片/视频/外部ERP或OSS测试收费。


## 增量开发：逐文件删除与视频单次提交

按用户最新要求优先剩余开发，未重跑已通过大集合。新删除域18 PG、视频7单元及原filemixin32共57通过2.39s（/private/tmp/agent-task-file-delete-video-targeted.log）；随后只选个人2 PG+视频9单元共11通过0.96s，18个旧删除用例明确deselected（/private/tmp/agent-task-personal-video-incremental.log）。集合重叠不累加。无模型/图片/视频/ERP/OSS外部或付费请求。

356每项备份地址在OSS IO前进入原deleted_files expiry账本，allocated不表示已删除，不暴露给restore，但原30天purger可清理unused或PUT丢确认对象。全批原版本备份验证后才armed/dispatch，逐项当前Actor/token/lease/running native/reserved operation和原文件version再次检查。实际rename原inode+SHA为删除事实；每项独立恢复记录与领域回执同事务，失败后只认已发生事实，不删未完成或后来同名文件。新域不走原fire-and-forget路径。原普通文件工具保持旧流程。

验证涵盖丢提交确认/丢返回、原生账本和opaque/原消息不变、单批只PUT一次、原restore消费独立备份、partial只归并真实quarantine且新文件和未完成文件保持、备份失败完整保留原文件及每次唯一expiry地址、源Task删除不被FK阻断且armed领域事实可延迟settle、两文件之间取消不再删、复制假quarantine拒绝、跨租户drain拒绝并保留FORCE RLS、个人org=NULL删除及原恢复。未修改已发布111；356新增native_status并允许个人orgNULL，原getter/355兼容JSON读取与IS NOT DISTINCT。

独立只读审查本批代码与57日志未发现新增高置信问题，未重跑。最后个人fixture与视频snapshot模型/计价时长校验是有定向证据的小增量，未声称独立重跑或完整业务验收。video_adapter当前只开发host opt-in异步单POST与dispatch/accepted observer、unknown不重试和完整snapshot时长/模型校验，旧normal/video同步等待分支保持；尚未接完整视频Task/积分/发布/自动续接，不宣称视频完成。

当前边界：部分删除若剩余原文件未被操作，read control不会替模型再发删除；恢复剩余操作的授权/接续尚需闭合，不能用改call_id绕过。视频原生队列、严格持久成果/原user turn不重关、积分结算和自动续接继续开发；其他ERP方法/多入口/UI/目标修订和原两Skill整体验收仍阻止发布。默认agent_task_execution_enabled=false，未提交部署、未执行任何付费测试。


## 视频队列及原上下文续接增量

本地新增视频用例去重41项通过：26真实PG（两checkpoint owner）、9 SDK、6 owned storage/worker，证据/private/tmp/agent-task-video-native-targeted.log及/private/tmp/agent-task-video-incremental-final.log。后台实际AsyncLocalDBClient consumer/原队列/冻结CP替换手动enqueue后8项通过1.40s，18项deselected，/private/tmp/agent-task-video-consumer-final.log。旧路径156项通过，见/private/tmp/agent-task-video-current-regression.log；同一195项run内2个新测试对ask-generation的错误预期已修订为原plan禁止生成断言，并在后续32项增量通过。未重复无变化的大集合、不相加重叠计数。

覆盖one-shot扣分/派发、无marker租约回收复用原账本、ACK丢失按原nonce绑定、不明受理固定deadline/平台承担/单次退款、真实资产及原ref才能完成、临时URL拒绝、MP4/SHA/etag/源task变化拒绝、上传ACK丢失不重下载、暂停竞态零POST并保留原claim/释放并发、原generation/plan权限、重复call重放不新建child、原user/input/turn/context revision不改写、实际pool Actor身份不泄漏、pause/cancel/newinput不强行续接、全租户回退drain/owner权限。所有HTTP/OSS为本地替身；没有真实视频收费或供应商/UI验收。

独立审查1项P2（dispatch漏org.active）已修复并由PG负例覆盖。其余无新增高置信问题不代表全项目安全保证。原两个发布Skill的真实端到端验收仍须先报价、用户确认后进行；当前全局开关false、无提交部署。剩余文件partial/ERP方法/目标修订/多入口/UI不得靠已有通过结果宣称完成。


## 359文件剩余项接续与ERP嵌套回执增量

原ToolRuntime到真实139/341/354/356/359的正常续接验证：旧native结果/history/opaque保持、新139续接成功后只经原控制导入实际全批回执；原位置新文件保留；无重PUT；剩余版本/备份/current资源/拒绝确认/暂停/租约撤销/实际begin后撤member或过期均阻止发送；扩参数不授予新目标；recovery_only只重放已有成功；WS确认展示实际剩余目标且保留canonical approval binding；rollback全租户drain保留FORCE RLS。证据128原相关集合、38新旧PG、10竞态/partial增量、2 pending recovery、2确认展示，详见IMPL日志索引，重叠不相加。原expiry停止和Async RPC注入测试修订不改变生产断言。

ERP56（38单元+18原生PG）通过，嵌套批次/字符串失败计数/async/partial不能靠顶层success提前完成，普通已准入同步零失败回执保持。其它method最终状态仍待领域协议，未冒充已全部适配。所有IO为本地替身；独立只读窄审无新增高置信问题，不代表外部业务/整套验收。默认入口false、无收费/提交/部署。


## 360 ERP后置条件与异步分类验证

精确纯标题写入期待、真实后置详情读取、同租户/Actor/workspace/原root、started晚于受理、cache/replay拒绝、最新标题变化、撤member、不可忽略额外写字段、源session不匹配、原native结果/CP保持和rollback全租户drain均有本地证据。44新协议用例分增量通过（20 unit+24真实PG，两CP owner），日志及fixture修正见IMPL。受影响295项集合中255旧路径通过；唯一新route确认call ID测试已按原规则修正并通过，不修改生产确认规则。原注册表19项通过。不同集合重叠不累加，未跑live/付费ERP或模型。

独立只读核无新增高置信漏洞，不代表外部接口验收。采购计算由只读改为写入并保留原erp_execute入口是有意权限收紧，旧query不能继续触发。新完成协议只认精确同源session，token自动刷新保持但跨session自动完成仍待稳定账户身份适配。默认全局入口false，无提交部署。尚缺其它ERP/异步终态/目标修订/多入口/UI/原两Skill真实验收。


## 361 ERP刷新连续性增量

361去重47项（21单元+26真实PG）分增量通过：原SDK模拟refresh一次/两次后可核实原写入，两CP owner均保留原139业务结果/messages/opaque、原写HTTP只有一次；异步worker记录由原actor另一连接读取。手工换号、缺链/环/逆序/未来/超32跳、错租户、撤member/replayed读不能完成或重发。关系幂等保留原时间、UPDATE/分叉拒绝、全租户rollback排空保持FORCE RLS。异常非布尔success/非str token不会生成新可信关系，原SDK兼容返回保持。原SDK和worker工厂28项、连接测试4项通过，日志索引和首次collection污染说明见IMPL361；不重复或累加此前重叠证据。

独立审查发现并修复1项P2：旧truthy成功字段不能用于新来源证明。全部供应商/Redis/存储为本地替身，不代表外部ERP账户验收。当前dedicated SYNC/未知transport没有可信观察，缺链仅待核实；没有稳定账户ID也不能跨手工换号证明。开关false、无收费/提交/部署，剩余ERP异步/目标修订/多入口/UI/原两Skill真实验收继续阻止整套发布。


## 362真实进度与前后端协议

新38后端用例（8unit+30真实PG，两CP owner）分增量通过，含真实HTTP200 no-store/错误消息404、无状态原文/参数/回执返回、原Actor已完成仍可读真实逻辑任务、active org/current member/runtime与原root消息绑定、channel/错租户/错user/同conv其它root/user消息拒绝。保存失败无marker发布，成功后完整CP/history保留；原受影响99项通过。前端新9项及原相关57/14等分增量通过：历史/WS parser白名单、不信任嵌入完成状态、受理非完成、真实完成停轮询、隐藏/离屏不查、网络错误清旧成功、旧响应/source变更隔离、明确拒绝停止自动重试。集合交叉不相加，日志见IMPL362。

TypeScript/production build/postbuild、定向ESLint零error（1条原MessageItem依赖warning）通过。独立只读核未发现高置信越权/提前发布问题。没有真实浏览器视觉或供应商/原两Skill产图验收；新个人UI不覆盖channel，其原业务仍保留。默认开关false，无收费/提交/部署，剩余用户目标修订、ERP终态及多入口整体验收继续阻止整套发布。


## 363追加输入与恢复验证（要求版本尚未完成）

新33用例分增量通过：7unit+26真实PG，两CP owner。覆盖原242输入→同事务完整CP/ACK，幂等/丢返回、不丢messages/opaque/要求、错误event/token/org/actor/lease或撤成员不ACK、旧项不可删除/改写、delivered不可回退、bootstrap输入未冻结时保存、多事件顺序和传输重放、原文多行SHA。真实343等待末安全点输入→闭合→enqueue/claim/load/apply→新owner继承原来源一次消费，无新image task。实际Executor commit_ready有待交付时进入原模型路径，不快返旧成果。原79 Actor/CP相关、114新旧图片/receipt续接组合通过；集合交叉不相加，日志见IMPL363。

独立发现的跨owner undelivered恢复P2已修并复核。仍没有验证要求变更后的安全执行：语义核验、旧/新要求版本与派发事务屏障尚未实现，此批只证明输入可靠交付。没有供应商/真实模型收费请求、未启用默认开关/提交/部署，剩余整体范围继续阻止发布。

## 364/365 修订版本与旧派发增量

继续/旁支不修改要求；真实amend经原模型盲提取及宿主数量/证据核对，专用SQL按proposal和真实固定Skill重建精确要求/gates/bindings。伪造event、改原goal/预算/旧成果、丢历史、改上下文、额外付费要求、缺host gate、替换profile/pin、删除manual/session/runtime、错误观察种类/动作/引用均不得确认修订。失败原文仍待核验，保存失败不推进内存。delivered但未核验输入不能复用旧commit_ready；未知来源/三次失败不能派发旧业务。原上下文及opaque字段保持；旧完成字段清除。

302项受影响集合和后续95项定向集合分增量通过，重叠不相加；原two PROFILES只做精确原pin本地协议对照，不是供应商验收。视频/图片最后marker、原文件每项和续删authorizer、ERP原journal及Reach/sandbox原派发入口均接保护。双连接video conversation/state锁序、worker scope恢复、跨租户回退拒绝、首文件已结算后新要求挡第二项有真实本地PG证据。旧媒体accepted结果仍须原consumer核实，当前交付覆盖不能抹旧unknown；旧成果也不自动冒充新要求完成。最新结果及未执行范围见IMPL364/365。

本批只完成运行中真实STEER修订的本地机制；新Turn接续、修订成果受控复用/全恢复、其余业务终态/多入口及原两Skill真实页面产图仍未验收。默认入口false、无付费测试/提交/部署。用户费用限制持续有效，整套通过后才能部署。


## 366/367 新Turn/旧图复用证据

最新定向65项（23unit+42真实本地PG，两CP owner）通过2.78s，日志/private/tmp/agent-task-followup-reuse-contract-final.log；此前新旧image continuation/尺寸/资源/scheduled/video/要求修订集合203项通过10.10s，/private/tmp/agent-task-followup-reuse-validation.log。集合重叠不相加。测试不调用模型、供应商、OSS或ERP；图像结果及资源为本地协议替身，不是假称真实产图。

验证新真实message SHA、源CP CAS、旁支独立、同手选pin恢复、丢ACK幂等收养、原上下文/opaque/旧成果保留、新输入进原审查；伪造旧/新消息、pin、owner、source hash不允许。真实晚到child经原343队列/claim/checkpoint/apply保留新要求版本并复用，未新增图片任务；冻结历史前不可提前借图。当前规划/原schema/精确尺寸/资源权限、variant、重复child/slot及回退排空均有负例。精确像素不支持时无复用、无原生成调用。

独立窄审三处发现已关闭：367 JSON减键表达式加括号；消息精确前缀/长度及固定后缀核对；复用走原原生尺寸解析且包含用户文字意图。未宣称外部供应商验收或整套无回归。legacy ready-plan/final-prompt/source再生成复用及原Task规划跨阶段精确选取、其余ERP终态、多入口/真实页面/原两Skill仍待完成。新入口保持false，本轮无付费、提交或部署；用户费用确认与整套发布条件不变。


## 368 旧方案复用与原两个正文的本地全流程

本轮无外部模型/图片/视频/OSS/ERP请求。276受影响用例通过（/private/tmp/agent-saved-source-regression.log）；最新23新unit通过（/private/tmp/agent-saved-source-callback.log），集合重叠。旧ready plan/source_prompt实际原生发布后的复用4PG、原Task结构化规划跨真实新Turn/要求版本的受限导入及篡改/全租户rollback2PG均通过，两CP owner；最终原续接组合见IMPL收尾。

原两个仓库v3正文精确body SHA等于原发布PROFILES，按原package/key/revision固定，正文/资产本地校验。两条实际SkillRuntime及control流程完成规划、阶段/恢复、要求修订后读取原稿、当前事实重核、逐图绑定、五图自动复用、最终完成/恢复；原摘要、手选、历史成果和要求保留，未调用业务执行器。审核观察和已发布图像资源为本地替身，前端/真实provider未调用；缺失的原生产完整export未被本地frontmatter冒充。

新增共享原生输入校验、精确saved source/native plan SQL校验、当前资源字节/尺寸、稳定再生成variant及不吞无variant用户重做意图的覆盖。SQL拼接优先级与ECOM早拒绝回归均已修复并定向重验；真实原图callback改变字节会拒绝。旧事实核验标签不继承、变造source正文和失权不买替代。部署仍受整套剩余业务/多入口/页面和真实供应商验收阻挡，默认入口false；收费前报价等确认。


## 369 ERP批量与丢确认恢复

新18真实本地PG（两CP owner）通过：原SDK/dispatcher HTTP替身一次派发、丢确认后真实原139详情观察、读到目标但仍uncertain/无完成artifact、同call及改title不能重发、原CP/messages/opaque/139失败结果与journal.phase/response不改。过早/未闭合/缓存/错actor/session不能认领；批量原对象与部分成功/失败和job handle压缩后从原领域账本恢复，不自动续发失败项或假定全批完成。日志/private/tmp/agent-erp-recovery-pg.log。

新旧ERP/session/postcondition集合155通过，最后controller/model bridge/新脱敏与async停止无效查询集合95通过；重叠不相加。所有HTTP为本地替身，无外部ERP、模型、图片或OSS请求。

独立审查P2已关闭：本地超时结束不证明供应商请求终态。最终unknown不得因matching read变completed，target_state_observed只是事实；effect_terminal=false/completion_basis=None，保留旧effect屏障。未新增COMPLETION_METHODS或猜测全局progress为实例完成。未知方法终态、后台唤醒、多入口和原两Skill供应商/页面仍未验收，默认入口false、无提交部署，收费先报价等确认。


## 370 商品字段完整核验

新27unit+38真实本地PG分别通过（两CP owner）：原参数/mapped一致、标题之外所有指定字符串字段逐项读取、缺失/NULL/数值条码/不同字段拒绝、原139非cache/早读与scope、source/session真实刷新、原上下文保持、format_loss恢复与导入后变化重核。HTTP全部本地替身。原纯标题v1和新字段metadata并存，原原生工厂/ERP确认与mixin合跑227通过；最后async/partial/忽略参数6 PG增量通过，不重跑有效旧证据。日志见IMPL370，集合重叠不累加。

未知HTTP确认时，全部目标字段匹配仍不产生完成artifact，保留uncertain；异步/部分ACK和未知参数不得靠title或全部字段匹配完成。370回退全租户排空保护通过。首轮组合失败为旧全局services.org替身污染真实工厂，删除过期替身后合跑全绿；没有改产品授权边界或降低断言。独立只读审未发现新增高置信缺陷。

仅上述稳定商品文字字段自动核实完成，SKU/库存/价格/其它ERP方法与异步实例终态仍未全部适配，原两Skill供应商与页面尚未验收。默认false、无收费/提交部署；付费前报价等确认，整套通过后发布。


## 371 原消息继续、停止与页面证据

真实本地PG+实际受权HTTP路由8场景覆盖原窗口完成前/后、队列前/后、继续完成/取消和两个CP owner；不是只mock路由返回。受影响原进度/ERP恢复/企业微信入队及WS/会话投递合跑85通过；ERP目录/恢复/公开进度22通过，accepted ERP不报完成或保证未来返回。前端受影响组合164通过，增量停止后控制/接续22通过；不对重叠组合累计。最终tsc/Vite/PDF检查通过，定向lint0错误1既有warning。沿用未失效的370及原两Skill本地证据，未再次生成图片。

版本化task-page-acceptance脚本用真实Chrome+原MessageItem、fixture API和本地SVG图片通过：12本地请求、0外部请求、0页面错误；继续/暂停/停止各1次，旧文本与两张资源保留，390px无溢出，闭合任务发现新接续，撤权清控件且不删旧图。桌面/手机截图目视通过。证据在/Users/wucong/.codex/task-evidence/skill-coordination-design/371；脚本自有Vite/浏览器finally关闭，无生产用户登录或真实供应商请求。不得据此宣称原两个Skill真实产图或整套系统已验收。

独立窄审复核接续原文保护、回调接线、重复点击、轮询竞态、ERP公开待核实状态，无新高置信阻塞发现；不代表全项目独立审查。未适配ERP终态目录显式86项；channel专属进度、真实原Skill/供应商验收、最终整体发布仍待完成。默认关闭、无付费/提交部署；价格确认后才能启动收费测试。


## 372 范围变更与多入口保留

原ERP功能继续作为兼容回归；新增写终态适配按用户指令延期，不作为本次缺失交付。ERP部分历史“86项未完成阻止发布”的表述由文首当前范围取代，不表示86项已适配。个人新版编排以明确宿主身份准入；群聊保留原流程和权限，既有bootstrap/state恢复不降级。实际Executor与原ERP handler通过47项，再与原ERP Agent/企微/scope/历史图复用/上下文压缩合跑219项通过（有1条测试模拟协程RuntimeWarning，日志保留）。没有新增channel共享进度授权，原两Skill供应商及最终整体验收仍未完成。371页面和构建证据复用；费用先报价等确认，整套本次范围通过后部署。


## 373 最终审查增量

独立审P1“非ERP启用仍误接入延期ERP写”已关闭：新域默认false，计划/披露/派发/预留前/原生发送前门禁，原ERP Agent、无状态旧路径和旧effect恢复保留。新10项关闭默认测试及两CP owner原PG关闭后恢复证明通过；非ERP文件恢复/删除/剩余项接续/异步视频当前真实迁移验证100+40 PG分集通过。111unit、79control组合与原两Skill本地协议/原Agent/上下文53项证据见IMPL373，集合重叠不累加。首轮46失败为旧文件fixture缺真实版本门禁迁移，安装当前迁移后最终通过，没有放松产品guard。独立窄复核无新高置信发现，不能推导全仓或真实供应商已通过。

只读后台预检确认原Skill及已发布第三版仍存在，但后台当前可用版本展示尚未启用；需在实际聊天作用域核对，不修改发布/assignment，不凭后台显示推断聊天权限。真实完整包/素材与供应商端到端仍待验收，收费前报价获确认；没有收费/提交部署。371页面/构建及372原ERP Agent回归按影响复用，默认两个新开关false。


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


## 379 原包和生产基线已核实，最终语义验收未通过（2026-11-01）

完整证据与费用事件见IMPL第379节，私有379目录。原两个实际发布包（含附件）已校验，原5张成图的发布/闭合/来源一致性均通过；模型选择属性、权限和版本未改。补真实JWT/租户/slot/HTTP幂等/PG入队/固定历史与生产3原样本journal/DB关联。最终免费239项通过；复用378前端和既有ERP/恢复证据，不累计重叠测试数。新修Qwen输出上限、同冻结请求blind extraction复用及自包含基本图片分析，后两者的上下文/引用/旧快照/指纹失效和实际流式边界通过。

正式发布仍不放行：原两Skill真实完整规划/事实核验/阶段交接未完成，安全返回不是完成。已批60积分（次数用户增至16），实际6次Max响应，token等价18.31393积分；预算台账保守占用27、下一长请求预留39超过剩余33而止，未生图/实扣生产平台积分，供应商账单未查。早前3次检索误走embedding调用路径，费用不明、不属于批准批次，已披露并补出站拦截，不能写0费用。修复后的语义效果尚无新实测通过记录；没有候选生产after或完整生产DOM首字数据。原ERP Agent、上下文和已有成果保留，未提交/部署。


## 380 免费原响应回放及方法工具一致性（2026-11-01）

最终225项定向通过，来源和范围见IMPL第380节；中间62/187组合重叠不累计。原已收响应离线重放必须拒绝全部socket，不新调用provider，仍拦截不完整提案并保留修正证据；完成断言未放松。原包正文各只出现1次，出站证明非重复方法注入。修复文字内容要求与文档份数的误导性反馈，不减内容要求。原电商inline方法不再披露只接受server_plan的planner；猜调用整批无派发、方法状态不变、反馈引导继续当前方法；恢复后同样过滤，普通工具和原ERP Agent保留，无owner/v1不误删工具。独立只读审查无阻塞发现。

本轮零新增付费/生产访问/生图/部署。原文、上下文和已有成果不变。上述结果不等同原两Skill真实完整规划通过，也不等同生产候选端到端性能after验收；原379未完成发布门仍保留，付费先报价确认。


## 381 实测未整体通过，次数耗尽停止；免费修复与精确复用通过（2026-11-01）

用户新批100积分/12次，实际新增12次全部received；149243 input/12372 output，分支token费率等价31.68103积分，保守账本51不是实扣；生产平台扣分0，供应商账单未查。无生图/业务派发/生产写入/部署。原ERP Agent、上下文、原包和旧图保留。

reference完整五条规划已保存，但facts review的claim概括非逐字原文，被正确拦截为uncertain，无proof，任务partial。ecommerce未新跑。本轮还暴露验收source factory被覆盖、任务随机身份使缓存失效而产生重复文字规划的夹具错误；已披露并免费修复，费用全入账不删。新固定诊断身份绑定goal/方法/素材，实际完整请求SHA免费回放复用已收规划，不以序号回放冒充精确复用。

主免费组合283通过；后续10项精确复用/预算组合通过，重叠不累计。共享原文用途映射采用独有证据及唯一约束，数量/漏项/宽引用歧义仍拒绝；事实核验输出协议错不会误指引重写商品事实或猜来源，同稿同源重审仍须校验通过才有proof。独立只读审查无阻塞发现。详见IMPL第381节，私有381/batch-summary.json和日志。

仍不得发布：修复后真实facts重审、原两个Skill完整语义闭环/阶段交接、页面及候选完整生产链性能证据尚未齐全。次数已用满停止付费，下次须报价确认，不用剩余金额绕过次数上限。


## 382 发布门禁实际执行

用户已明确授权带已知未验收项受控部署。首候选c0ef7dc3前端2731及构建通过，后端13811通过/11失败，入口failed_safe在生产更新前停止。旧测试夹具与既有ERP异步写安全分类契约修复后550项定向通过，原权限/确认/完整基线断言未删除；独立只读审查无阻塞发现，详见IMPL382。必须新候选重新通过发布门禁才可报告部署成功。业务验收仍未完成，两个新开关默认false保持；不运行已耗尽预算的付费验收。
