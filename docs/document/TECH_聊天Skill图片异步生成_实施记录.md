# 聊天 Skill 图片异步生成实施记录

> 2026-10-05 更新：个人共同规则已撤销 AOCI。以下历史记录中的 MCP 接入、索引维护和 aligned 发布要求不再适用；后续直接使用当前受控发布入口。

更新：2026-10-05。此文档记录实际实现与证据；[原设计](TECH_聊天Skill生图编排与系统兼容实施方案.md)中的编号继续用于追溯，旧任务第十一章不适用。最新来源与文件ID修复见文末；本地候选未提交部署，生产仍为95a18e18。

## 任务与最新基座核对

- 分支：`codex/task/20261004141838-chat-skill-image-async`。
- 工作树：`/Users/wucong/EVERYDAYAIONE/.worktrees/chat-skill-image-async`；按 `scripts/task-worktree.sh start` 从最新 `origin/main` 创建，基座 `32e8ba0d2128be07856a509384f19ec6f446e493`。
- 基座与方案所述主线相同；仍逐一检索消费者和实际符号，没有合并旧分支或复制旧版代码。旧工作树仅复制方案文档并注明来源；原有改动未触碰。
- 最新代码已有 `create_task_once`、适配器、资产 RPC 和 Skill Runtime。复用这些能力，不另建图片任务表、通用 Runtime、资源编号或价格副本。
- `_RepeatedToolCallGuard` 已按完整参数计算 fingerprint：稳定 `variant_id` 即可区分同提示词变体，无须改造通用循环保护；随机变体受数据库硬预算约束。
- 原设计中的同步 `_run_image_generation`、内部历史工具、同步图片 trial、聊天孤儿恢复和父流式槽位断点仍存在；本任务已迁移或修复。复检旧同步图片符号已无消费者及实现。
- 最初仅开发、测试与文档；后续用户明确授权提交部署、首轮最多2张/24积分的生产验证及管理员个人 Skill 发布。未合并main、清理工作树或向组织/平台分配 Skill。

## 执行顺序和阶段状态

依赖顺序：协议/资源/事务 → Worker/结算/恢复 → Skill/精确历史 → 前端/trial/同步删除 → 产品控制 → 进阶能力逐项核验。全程在同一任务工作树；独立代理仅只读审查数据库、账本和恢复风险。

阶段1–5实现与对应隔离验证已完成；阶段6部分实现且关闭。用户后续明确授权管理员账号生产小范围验证及个人Skill发布。生产已完成单张文生图样张和引用生成原图的图生图编辑，共2张/12积分，真实模型调用、供应商受理、NAS/资产保存、唯一结算及浏览器基本流程通过；完整schema重建、生产故障注入、多图/多参考图组合、trial与其他原生入口的真实供应商回归仍未验证。

| 阶段 | M / F / T | 实际完成 | 证据限制 |
| --- | --- | --- | --- |
| 1 协议与安全 | M01/M06/M07/M08/M18/M20/M29；F06/F07/F09/F11/F12；T02/T03/T08/T09/T15 | 单图显式模式、实际模型规格/价格、可信原图/顺序/用途、完整版本化快照；273原子接受、硬预算、领取/预扣/账本绑定；独立Redis槽位；默认关闭 | 隔离PG真实事务与测试RLS，不是完整部署权限副本 |
| 2 异步闭环 | M09/M10/M11/M12/M19/M20/M32；F01/F02/F03/F07/F08；T04–T07/T10/T16 | 有界Worker领取、发送前持久标记、单次HTTP提交、外部ID绑定、回调/轮询、未知受理期限、结算/持久化/资产/历史发布/投递恢复；关闭接受仍收尾 | HTTP协议使用MockTransport；本地真实PNG和真实资产RPC；供应商与NAS/OSS未调用 |
| 3 Skill编排 | M02/M03/M04/M14/M15/M30；F04/F12；T01–T03/T08/T12/T15 | 自动/手动激活工具展示取Policy交集；能力摘要、精确历史原文/hash、Skill正文与版本事实；合法变体保护；聊天最终prompt不经过native prepare | 隔离发布/分配/hash与运行时测试通过；真实模型A→B意图及样张行为未验收 |
| 4 前端与迁移 | M05/M13/M16/M21–M26/M28/M31；F05/F06/F10/F13；T04/T06/T07/T11–T16 | 独立pending/终态、刷新断线恢复、来源贯穿引用、原快照重试/新版本；trial异步GET结果不入聊天；文字trial保持；旧同步图片实现删除 | React/接口合同与类型构建通过，未做真实浏览器端到端 |
| 5 产品完善 | M16/M17/M24/M27/M29/M30；T08–T12/T14 | 选中项/样张指导、服务器真实输入与原图预览、动态成本预览、硬预算、排队停止/提交后说明、版本比较、系列约束、复制配方、反馈、既有下载/ZIP | 样张及系列约束依赖真实模型执行Skill；无新计划调度平台 |
| 6 进阶 | M07/M19/M26/M29/M30；T17 | Flare真实背景参数与保存结果alpha检查已接线、默认关闭；本地像素/格式/协议验证通过 | 数值权重/mask当前已接适配器合同无支持字段；语义质量检查未新增；透明真实输出及无额外成本未验证 |

## 关键实现与兼容

- `chat_image_request.py` 统一校验、原图定位、精确来源与快照。分析图不会自动成为生成参考图；用户指定的当前/历史/生成原图独立解析，接受/提交/重试校验版本和字节digest。详情预览校验原权限与版本后临时签址，不改写快照。
- `ImageHandler.accept_chat_image` 只持久化接受，`MediaToolMixin._generate_image` 只走此出口。旧prompt-only归一为文生图；旧URL仅可映射当前可信manifest原图，不能任意URL生成。删除 `_run_image_generation` 和旧同步失败辅助实现；工具名不变。
- 快照位于 `tasks.request_params._media_request_v1`，细状态位于 `_media_lifecycle_v1`。每张独立task/message/slot，父chat/Turn/token/流式状态不作图片输出容器。
- 273–276加性迁移使用现有tasks、messages、credit_transactions、Skill trial。新RPC为SECURITY INVOKER，撤销PUBLIC权限；trial Worker策略限定已关联图片的原trial/actor/org。资产登记复用145既有RPC，不改变其权限合同。
- `chat_image_lifecycle.py` 和既有Worker/Completion处理全部恢复。供应商结果先持久化，保存或资产登记失败只重做保存/登记；不会重生。终态投递仍可恢复。资源暂不可访问不会误报透明合同失败。
- 新版停排队按数据库阶段CAS；一经领取/提交，界面说明无法保证撤回，继续核实结算。通用cancel/fail识别新版图片，不能关闭父任务或跳过结算。
- `get_conversation_context` 公开精确历史schema，按当前用户/会话revision返回实际文本和来源hash；不从摘要猜提示词。已发布图片失败事实在分页前定向纳入，普通失败聊天不扩大注入。
- 图片trial冻结草稿/revision/hash/原参考；接受返回running和task_id，完成写trial，不创建普通聊天消息或revision。已接受trial即使候选过期仍可读结果；新运行仍拒绝过期候选。文字trial准备与执行语义保留。
- 新版消息pending和done/error只更新子图片；刷新恢复排队/不确定/已完成状态，不触碰父streaming。重试只提交持久request UUID，服务器取原快照；成功再生成保留旧版，旧native分支语义保留。
- 原生ImageHandler参数准备、原生文/图生图和电商批次、视频、文字trial、文件、ERP/MCP与scheduled通用权限边界未迁入此路径；有针对性回归，不宣称全系统无回归。

## 已确认的平台承担政策

用户2026-10-04授权“平台承担，并统计承担积分供管理员调整”。受理不确定到期后退还用户，不盲目重发；可能发生的供应商成本由平台承担。默认排队期限600秒，提交lease60秒，不确定核实期限900秒；已接受任务的供应商超时转入核实。关闭新接受不停止恢复/退款。

274将用户退款、task/message终态发布及 `_media_platform_cost_v1` 置于同一事务，重复通知/退款只重放。两类登记：

| reason / evidence | 含义 |
| --- | --- |
| `submission_uncertain_expired` / `unconfirmed` | 发送可能已受理，但期限内不能确认；供应商实际支出未知 |
| `image_output_contract_failure` / `provider_success_unbilled` | 供应商报成功但单图/透明输出合同不满足；用户未获合格结果，退款并登记平台估算；未对供应商账单核销 |

管理员现有错误监控面板显示事件数、`refunded_user_credits`、`estimated_provider_credits`及分页明细；接口 `/api/error-monitor/image-platform-costs` 与DB均检查有效超级管理员身份。汇总涵盖完整时间范围，非当前页抽样；没有对外发送消息。

供应商估算取接受时既有 `calculate_image_cost(...).kie_cost`。当前Flare 1K/2K/4K每张约6/10/16供应商积分；例如100次1K不确定受理，记录估算600，实际支出可能0至600，须账单核实。用户积分与供应商积分不是同一账目，分别展示。未读取生产数据，无法报告真实发生数量。

## 最终测试证据

使用占位 `DATABASE_URL`/JWT；没有读取或输出密钥。PG仅 `/private/tmp` Unix socket/55439，Redis仅专用Unix socket，无应用Redis或生产数据库。专用随机测试DB创建后销毁；未安装依赖。

| 验证 | 结果 | 证明范围 |
| --- | --- | --- |
| 后端30个定向文件 | **1500通过、3跳过**，9.43秒 | 输入合同、native图片/电商/视频、Worker/Completion/recovery、Tool Policy/Skill/Actor/历史、文件/MCP/ERP工具与scheduled回归；3项为既有废弃V1 gather测试显式skip，不是新实现通过 |
| PostgreSQL生命周期 | **45通过** | 真实事务/并发/故障回滚/fencing/NULL actor/RLS角色拒绝、退款与平台统计、重启状态恢复、乱序部分失败、trial、快照重放、真实隔离Skill发布分配；真实资产RPC登记唯一性与来源/hash |
| Redis子槽位 | **2通过** | 实际Lua并发、独立父子槽位、集合部分丢失恢复、故障关闭 |
| 前端13个定向文件 | **185通过** | WebSocket子图隔离、恢复、引用/上传/发送、重试、trial/详情、管理员统计、消息协议、下载 |
| 附件队列补充3文件 | **16通过** | 有序附件hook/context/preview回归；与185项不重复 |
| 前端app/node TypeScript | **通过** | 新API/协议/来源与界面类型 |
| Vite构建 | **通过**，12.09秒 | 6272模块；既有大chunk提示，未部署 |

隔离工具→真实接受RPC→KIE单次HTTP JSON合同→真实Worker/Completion→真实结算/资产登记→FastAPI详情/成本/replay重复请求的闭环通过。供应商HTTP为MockTransport，保存到临时PNG文件；认证身份及WebSocket为桩，不能算真实供应商/存储/浏览器端到端。

PG使用最小生产字段合同、明确测试RLS及实际040/145/256/257/273–276和266 trial定义；145测试授予登记RPC执行权限，其无关FK目标简化为ID。**并非完整部署schema、函数owner或生产RLS副本**，不证明实际应用/Worker生产角色权限。

独立只读审查发现并修复：NULL actor允许风险、精确资源路径误回退、陈旧queued失败覆盖已发送任务、缓存结果丢失/暂时读错处理。相应真实PG/资源故障回归通过；最后审查未发现新的高置信缺陷，环境限制仍保留。

日志：`/private/tmp/everydayai-chat-image-{backend,postgres,frontend,attachment,build}-final.log`。复现命令与隔离验收见[发布与回滚说明](RELEASE_聊天Skill图片异步任务.md)。

## T01–T17验收映射

“已覆盖”指上述实际测试范围；表中未验证项是启用前的证据缺口，不能用基线测试代替。

| T | 本次已覆盖 | 未验证/限制 |
| --- | --- | --- |
| T01 | 自动/手动/恢复展示、收窄Policy、隔离published/assignment/hash；生产新对话模型激活与正文hash核验（见下） | 自动激活后真实生图、无方法提示的生成意图命中率、撤权交互 |
| T02 | mode/模型/规格错配拒绝、单图多参考顺序 | 真实供应商执行效果 |
| T03 | 当前/历史/生成原图定位、版本/digest拒绝、精确提示词来源 | 真实模型A分析+B生成、歧义询问 |
| T04 | 接受前无provider调用、独立task/message/slot、子事件不结束父流式 | 浏览器实际并行流式 |
| T05 | 接受/预扣回滚、崩溃、未知受理/ID绑定丢失、重复回调、一次HTTP | 真实网络丢响应与供应商侧账单 |
| T06 | 新实例恢复queued/lease/uncertain/settling、终态投递重试、前端刷新数据 | 真实服务重启、多标签断线 |
| T07 | 乱序/部分失败、唯一账本、保存失败重试、资产登记、WS失败恢复 | 真实NAS/OSS故障、浏览器最终一致 |
| T08 | 稳定同prompt变体、不变call重放、异输入冲突、随机变体硬配额 | 模型实际多次调用行为 |
| T09 | 真DB预算/余额竞争、严格Redis、原始价格再校验 | 实际负载规模 |
| T10 | stop/claim并发CAS、queued不提交、accepted准确说明 | 不承诺供应商取消 |
| T11 | 原快照retry、新版本保留、原图权限重核、native分支回归 | 原生/编辑供应商效果 |
| T12 | 原文/hash/revision、105条无关失败前分页过滤、历史与晚到独立发布 | 浏览器侧栏实测 |
| T13 | 图片trial接受/结果隔离、文字不变、候选变更/过期GET、前端有界刷新 | 真实模型trial与完整部署trial RLS |
| T14 | 受影响native/电商/视频/chat/文件/ERP/MCP/scheduled定向回归与构建 | 未跑全部系统端到端；不是绝对无回归 |
| T15 | 个人/企业/频道拒绝边界、角色/成员/NULL actor/资源/路径/来源校验 | 完整部署FORCE RLS/ACL/函数owner验收 |
| T16 | 加性迁移、关接受继续收尾、closed-gate receipt、隔离Skill真实发布 | 完整迁移链、读写错峰/应用回滚演练；生产发布未授权 |
| T17 | Flare背景参数冻结/协议、实际PNG alpha检查、失败退款/平台登记 | 透明供应商输出/价格未验；数值权重、精准mask与语义质量检查未实现 |

## 后续授权与交付边界

没有新供应商/依赖/持续付费能力。透明功能保持 `CHAT_IMAGE_TRANSPARENT_ENABLED=false`；现有适配器没有数值权重/区域mask合同，不以文字强度或普通改图替代。可选语义质量检查会新增模型调用费用，须另行决定与授权。

首轮记录的默认 `CHAT_IMAGE_ASYNC_ENABLED=false` 保留。后续用户指示直接生产验证，使用受控发布入口准备任务候选，不合并main或清理任务。新增 `CHAT_IMAGE_ALLOWED_USER_IDS` 账号范围：仅限制新工具/trial/快照版本接受及展示，读取、既有回执、Worker完成/恢复不受影响；空串在总开关开启时代表全用户，生产验收必须明确配置管理员UUID。

本轮生产只读preflight：受保护数据库身份一致，所需列全部存在，everydayai/worker/owner均无superuser或bypassrls，现有register_user_asset可由everydayai执行；tasks/消息/账本采用当前生产既有无RLS合同，trial与change_sets保持FORCE RLS。没有关闭RLS、扩大表授权、读取密钥或故障注入。账号门对应新增3项及相关645项定向测试通过；原1500项作为首轮证据，不冒充新增门后的全量复验。

受控发布准备完成，拟限管理员账号、每轮2张/24用户积分、透明关闭。此前自动审批拒绝上传生产开关配置，命令未执行。用户随后明确授权“提交部署，并允许上述范围的生产生图验证”；按受控入口执行，首轮真实验证总计最多2张/24用户积分，不合并main或清理任务。发布与生产验证结果另行按实际证据记录。

首次候选 `ea59610a` 已受控提交/推送；前后端构建、文件同步完成，273在事务内创建tasks索引时因owner不符失败，273–276均未记入迁移账本、接受RPC不存在，服务尚未重启，完整发布候选失效，锁保留。只读核验确认发布/迁移均已停止且原锁owner不变。兼容修复沿用271模式：索引与trial policy DDL局部使用既有表owner `everydayai`，函数部分恢复 `everydayai_owner`，保持INVOKER及原ACL；不转移表owner、不关闭RLS、不扩大生产授权。生产两角色已有public USAGE/CREATE。原PG测试说明中的16为记录错误，临时cluster实际为已安装PG17；修正启动参数后本机仅Unix socket。按真实表owner与发布role wrapper复验 **46项通过/0跳过**（1.90秒，`/private/tmp/chat-image-migration-owner-test.log`），包含角色恢复、函数owner/INVOKER/PUBLIC拒绝及Worker/账本/RLS回归。独立只读审查无新的高置信阻塞；下一步按原所有者恢复锁并受控重新发布修复候选。

API/状态见[接口文档](API_聊天Skill图片异步任务.md)，迁移顺序与保留型回滚见[发布文档](RELEASE_聊天Skill图片异步任务.md)。生产实测结果按实际阶段继续记录。

## 2026-10-04 生产小范围验证

- 受控重新发布成功：`54298ebfea059772ff1760812a019084c8fa6135`，前后端范围，构建/273–276迁移/服务健康通过，`DEPLOYED_PENDING_ACCEPTANCE`。最新origin/main仍为32e8ba0d；没有合并main或清理工作树。发布锁按已核验的原所有者恢复后重新获取，完整成功后由入口释放。
- 管理员浏览器已有登录会话：系统监控新增承担统计成功读取，近7天承担任务数/已退用户积分/供应商估算积分均为0，没有模拟生产失败或制造退款。
- 独立验证对话 `6046e5f9-13a7-480a-8a43-7e0aad2d0c6d`：普通聊天模型文字误称已提交，真实DB只有已完成chat、无图片子任务；手动选择现有“参考图多方案提示词”后，模型如实说明该Skill仅生成文字提示词、无生图工具。两轮均无图片扣费，余额1278未变；不把文字声称成功作为图片任务接受证据。
- “聊天图片编排”源文件已随代码部署，但尚未发布至生产Skill目录。为遵守原先生产发布/分配授权边界，已请求仅在管理员个人目录发布这一明确源文件以继续最多2张/24积分验证；等待该项授权。尚未调用真实生图供应商，也未进行图生图、刷新恢复、快照新版本或trial真实供应商验收。
- 上述为54298ebf部署后的原始证据；本次继续发布包含以下真实用户验证记录与余额刷新修复，最终生产候选以受控入口的RELEASE_RESULT为准。

### 生产 Skill 发布与真实用户流程

- 用户明确授权“直接整个发布上去。然后开始模拟真实用户使用测试”。通过现有 SkillAuthoring 控制面服务，使用实际 active super_admin 身份和个人 RLS scope 发布完整“聊天图片编排”，不改变已有 Skill、组织分配或账户权限。package_id 为 `e118d32b-75ba-44c8-8b11-f8741bad053b`，revision 为 `v533471bf144641bab38f369d9e5333c1`；已核对源文件 SHA256、完整正文、三个限制工具和 NAS 回读。管理员个人目录和聊天选择器实际可见。
- 新建真实浏览器验证对话 `4e5aa228-9403-4af7-9679-fec008f1617a`，固定此 Skill。先仅保存提示词原文：只有已完成 chat，无图片任务、无扣费，余额1278。
- 随后自然语言要求先出1张样张：真实 generate_image 回执674ms，独立任务 `d21eea4b-faff-4350-a56e-7813ea014672`、独立 pending 消息，父 chat 已完成。数据库快照为 text_to_image / Flare / 1K，prompt_exact=true，references=[]，预估与预扣6积分、pending账本已绑定，外部受理ID已绑定，余额1272。受理不等于图片完成；尚未记录完成验收。
- 服务器输入详情实际显示原文及真实模式规格与本轮2张/24积分上限。生成中刷新浏览器显示“正在恢复1个任务”；继续核验完成、持久化和刷新后的最终结果。
- 文生图与图生图最终均completed/published、delivery_pending=false，每张独立消息和一条confirmed的6积分账本、一个资产登记，NAS实际存在1024×1024 PNG。余额1278→1266，共12积分；图生图唯一参考的content_sha256与第一张保存原图字节完全一致，旧图未覆盖。浏览器实际预览蓝色和橙色版本，主造型、姿势、背景与构图保持；这是普通提示词编辑，不宣称精准遮罩效果。
- 图片运行期间继续纯文字讨论配色，只新增chat，无第三张图，已提交快照未改变。本轮覆盖T01/T02/T03/T04/T06/T07/T11/T12/T16的基本真实路径，不能替代这些编号中的所有边界与故障场景；尤其多Worker、丢响应、退款失败等仍以此前隔离证据为限。真实图片trial、压缩后原文检索、多参考图顺序与A分析/B执行、快照replay、停止竞态、ZIP、原生/电商/视频等真实回归未做。没有生产故障注入或额外付费调用。
- 本轮发现并修复前端余额滞后（M05/M13，F05/F10，T04/T06/T07/T14）：独立子任务没有原输入operation callback，完成/退款事件未触发用户余额刷新；浏览器重连亦仅恢复任务。新消费逻辑在图片终态及WS连接时读取现有用户信息接口，不改账本、父聊天或流式槽位。回归先复现缺少刷新，再修复；5个相关前端文件共161项通过，app/node TypeScript检查通过。同步修正订阅清理测试按实际注册数检查，避免新增message_pending后旧固定数字失效。

### 发布确认与自动激活补验

- 受控入口返回 `RELEASE_RESULT status=success commit=731f8ba3433938124a04720c6e6890352081e707 mode=preview status_after=DEPLOYED_PENDING_ACCEPTANCE acceptance_candidate=true`，前后端完整发布。浏览器刷新后余额1266，两张完成图片与历史消息仍在；没有合并main或清理工作树。
- 用户指出此前两张图使用手动选择并固定 Skill。这一证据只证明手动路径，不能宣称已验证模型自主激活。阶段3补验关联 M02/M03/M30、T01/T15，不改授权或业务代码。
- 新对话 `29dfa5f3-28af-448b-b167-271a57ae1470` 全程未点击 Skill 选择器。第一轮只讨论缺失需求，模型未激活；第二轮要求使用当前可用的专门图片编排方法整理样张提示词及执行顺序，未指定 Skill 名称或工具名，且明确禁止提交生图。模型自行激活“聊天图片编排”，前端出现真实 skill_step，而非文字模拟调用。
- 生产只读核验：conversation_skill_bindings 为0；两条 chat 的 `_selected_skill` 均不存在，checkpoint.manual_skill_id=null、session_skill_ids=[]。第二条任务 `fd62b052-398a-528a-8ce0-1955807cf27f` 的激活 step_id 为模型 actor-call，不是 manual-skill 或 session-skill；checkpoint.active 含已发布版本，完整 rendered 与正文 SHA256 均为 `ef6135f24995b860e7775948a4a4ce547f36da91591ac9a33e7784fed45fcb91` 且实算hash一致。effective_allowed_tool_names 为 file_search/generate_image/get_conversation_context；现有执行链在激活后取实际 Registry/Policy 过滤展示，未扩权。
- 新对话仅2条已完成 chat，无图片任务，余额仍1266。已证明模型选择→正文加载→当前轮工具范围，不证明自动激活后的真实生成；没有新增付费图片。未测试无方法提示的实际生成命中率或各模型稳定性。模型准备文字中的价格/规格建议未作为服务器成本证据；以后实际执行仍须服务器校验与成本预览。

### 默认模型收敛与用户确认回归

- 当前阶段3/4修正：M01/M02/M03/M07/M08/M30，F04/F08/F12，T01/T02/T04/T05/T15。用户明确要求简化为平台默认模型，不向AI提供生图模型选择。工具schema移除model，仅展示默认文生图/图生图配对能力；接受端拒绝model/model_name及错误字段，现有模型注册表、原生入口、已冻结任务及快照重试保持兼容。
- 用户在上述自动激活对话第三轮输入“确认”，任务 `523de9b2-54b6-5108-bc13-575bb5cacad5` 实际使用model_name/size/format且缺mode，接受前被 `IMAGE_REQUEST_FIELDS_INVALID` 拒绝，未产生图片任务，余额1266未变。该轮checkpoint.active为空，上一轮自动激活没有在本轮重新加载；全程无手动绑定。此前两轮的准备验证不足以发现这个续轮问题。
- 修正工具说明、共享聊天说明及Skill正文为当前字段/default规则，并明确自动Skill只作用于当前轮：确认/继续时需按当前目录重新activate，不能沿用历史参数。接受RPC前确定的输入错误返回not_accepted，不再误报“外部执行结果尚未确定”；RPC抛错/回执丢失继续按不确定处理，不盲目重发。
- 定向验证：默认模型/覆盖拒绝、原模型快照兼容、真实handler接受前准备与RPC错误边界、Skill Actor恢复，共74项通过（Python3.12.12既有backend/venv，日志 `/private/tmp/chat-image-default-model-test.log`）。这些handler测试使用mock数据库边界，不新增真实事务/RLS通过声明。首次使用不完整Python3.14环境缺yaml，4项新测试UUID fixture无效；换用已有完整环境并修正UUID后通过，未安装依赖。
- 仍待发布后核验新Skill正文/自动激活与续轮；不会追加付费图片请求。本次无数据库迁移，不改变管理员灰度范围、预算或平台承担政策。
- 最终局部回归1116项通过，涵盖默认模型、接受边界、Actor/Skill恢复、工具执行/Policy、旧工具完整合同和三种执行入口（日志 `/private/tmp/chat-image-default-model-final-test.log`）。旧合同快照仅增加已授权的异步图片/精确历史差异及图片说明，其余工具/schema/权限矩阵继续与原基线精确比较；没有通过回退同步协议或关闭断言制造通过。
- `0be105ad23749256a312a26b52326b8f96bda6bb` 前后端发布成功，状态DEPLOYED_PENDING_ACCEPTANCE。首次GitHub 22端口拉取超时在应用更新前停止，经同主机密钥校验的SSH443重试成功。线上只读核验工具无model/model_name/size/format参数、默认Flare文/图配对、预算2张/24积分，1K/2K/4K实际单张价格6/10/16；零数据库/供应商调用。
- 个人包 `e118d32b-75ba-44c8-8b11-f8741bad053b` 已通过既有控制面发布新版 `v0b89c04f1df64626ae86000cc4961a63`，正文SHA256 `83b64d96da3cc0802f0dda6e5255d208197bdc1b991e661be19a33c138cb3764`，NAS读回及权限集合核验成功；目录model_selectable保留true表示AI可以激活Skill，不表示选择生图模型。
- 生产不付费续轮补验：任务 `7c48aa95-65ea-5dbd-afae-b7c3405d368c` 自动激活新版Skill，checkpoint/rendered/hash一致，正确说明默认模型和6积分；下一轮 `5c56d74a-af75-562e-86ea-8a9a51b3f1a1` 没重新激活，文字示例仍写format。五轮均无手动选择/固定绑定，无图片任务，余额1266。不能把第一轮成功加载冒充续轮合同通过。
- 进一步修正已有SkillRuntime的请求投影：无active时此前直接返回leading目录及历史，缺少当前请求旁的选择状态。只有实际提供activate_skill且目录有获准可自动激活项时，把有界当前目录和“本轮未激活、确认/继续先加载、按当前schema准备/执行”的事实放在当前用户消息前；不继承历史绑定、不预加载正文、不改变授权。context_version=1、计划任务及无控制工具场景保持原投影。
- 该修正的129项定向测试通过（Actor真实provider调用参数、lazy加载、旧消息不变、legacy/无控制工具、目录预算、恢复、推荐、手动/固定与计划快照；日志 `/private/tmp/chat-image-default-confirm-test.log`）。仍需线上续轮验证后判断模型行为，不追加付费图片请求。

### 2026-10-04 参数可靠性机制（本批开发，未提交部署）

阶段3/4补齐，关联 M01/M02/M03/M08/M20/M29/M30、F01/F04/F08/F12、T01/T02/T05/T08/T09/T15/T16。用户确认增加机制解决模型填错字段，继续默认模型；本批基于075726fc，在同一任务工作树实施，没有更改Skill正文、生产Skill目录、接受开关或数据库迁移。

- 共享ToolRuntime/ToolExecutionService使用实际ToolSpec完整校验，错误在确认/调用登记/图片接受前返回明确未受理事实；ChatToolMixin覆盖坏JSON、数组、重复键和非有限数值。通用参数清洗器仅对图片采用同合同严格拒绝，其他工具兼容行为保留。
- 当前真实聊天Actor实现一次纠错：保护完整prompt、原图顺序/用途、已确认规格、稳定变体，只有首次明确的shape rejection可打开额度。无法确认语义、纠错改变输入、再次错误、受理不确定均停止；修正结果用真实工具回执交付，不加解释性模型轮次。多张仍由多次单图调用，原预算/幂等不变。
- 恢复状态复用tool_step和239 checkpoint RPC，新增应用层before_tool边界及完整调用hash记录，没有新表。修正模型之前记录used，整批工具执行之前保存dispatch_reserved；缺回调、disabled/ignored或保存异常都阻止IO。从used/dispatch_reserved恢复停止，从done恢复原回执及统计，避免改变批次index/随机variant_id后重复付费。
- 管理员错误监控新增参数错误率、纠正成功/未纠正、额外Token及估算聊天积分。只查已完成chat的统计JSON，近1..30天、最多5000条并注明截断。估算使用已有模型定价，不是新增扣款或供应商账单；无usage/未完成聊天的成本不宣称覆盖。已有图片平台承担、原生图片/电商/视频/trial执行及账本未改。
- 独立只读审查发现并修复：恢复纠错可换index重复受理、解析错误绕过限制、覆盖受理不确定提示。复审三项关闭；进一步保守关闭已used纠错模型的重启重试。数据库真实保存与测试RLS见新增临时PG验证，不等同生产ACL。

验证记录：原基线与此前生产两张样本不能替代本批验证。实际模型选择/确认续轮的线上纠错仍待发布后验收，本批无付费调用、生产数据库/控制面写入或部署。

| 本批实际验证 | 结果 | 范围与限制 |
| --- | --- | --- |
| 后端27个相关测试文件 | **934 passed**，8.46秒；`/private/tmp/image-argument-final-backend-tests.log` | 真Actor/ToolRuntime执行链配合脚本化模型和业务double，验证自动激活→坏字段→一次修正、完整合同、输入保护、旧回执/旧内部调用、权限、未保存0调用、受理后故障恢复0模型/0调用、used恢复、uncertain提示、统计API与5000条截断；原生图片/电商/媒体、Worker、Actor/Skill和其他工具回归。不是实际Qwen或供应商验证 |
| 新建临时PG17 Unix socket库 | **3 passed / 0 skip**，0.41秒；`/private/tmp/image-argument-postgres-tests.log` | 使用现有239实际RPC跨连接提交/重载before_tool、旧token拒绝、过期租约拒绝、测试RLS隔离其他actor及实际JSONB统计投影；最小schema/测试RLS不是完整生产权限副本。首次系统PG16启动被沙箱共享内存阻止，改用已有PG17并获工具许可后验证；没有安装依赖。临时集群已停止并删除 |
| 管理员前端 | **15 passed**；`/private/tmp/image-argument-frontend-tests.log` | 指标展示、读取失败降级与既有操作回归；日志有React act warning，无断言失败 |
| 类型/构建/静态 | `npm run build`（含tsc）通过，生产TS文件定向ESLint及git diff --check通过 | 构建保留大chunk警告。完整定向lint的测试文件有原有motion mock析构变量5项unused错误，已与HEAD相同位置核验，本批未改该mock；不把它说成全量lint通过 |

新校验文件、chat/image_argument_correction、execution_engine和现有checkpoint适配器为关键后端位置；统计在error_monitor路由及ErrorMonitorPanel。API/状态、发布顺序/兼容回滚和CURRENT_ISSUES同步完成。本批T01/T02/T05/T08/T09/T15/T16相关分支已有上述证据，其余T编号沿用前文原实现证据，不将它们或T17宣布为本批全量重测。仍待受控部署后真实模型自动激活/确认续轮验收、生产checkpoint/统计权限核验；追加供应商测试须另定额度。本批具备本地候选验证证据，未提交、推送、部署、合并main或清理任务工作树。

用户随后明确授权“提交部署”。提交前只读生产预检通过：数据库身份与实际checkpoint RPC/非枚举safe_point兼容；pending/running chat及在途图片均为0，历史paused chat为43，发布锁空闲。最新origin/main仍为32e8ba0d，当前候选已包含。发布前后端并复用以上定向测试，保留构建、迁移账本及服务readiness检查；最终发布状态以本次受控入口结构化回执为准。没有增加生图额度、发布Skill、改生产配置、合并main或授权清理。

### 两张图片自然语言生产测试阻塞（2026-10-04）

- 受控入口已返回完整发布成功：`0e1625150ce967a7e03c24ccb5c73065925e9169`，前后端、构建及服务readiness通过，`DEPLOYED_PENDING_ACCEPTANCE`。已登录管理员系统监控中的新增纠错统计成功读取，无新样本；工作树保留、main未合并。
- 用户随后明确授权继续真实流程“从打磨提示词到同时生成两张”。浏览器新建普通聊天`4ea3d8b7-bf2b-4b1f-82e2-f0c39f3919a7`，全程没有选择/固定Skill，生产bindings=0，三条chat的manual_selection为空。两轮分别准备挥手/看书两张1K方图PNG并调整宝蓝色、磨砂塑料、黑色圆眼、无嘴巴等特征；仅chat完成，无图片任务或扣费，起始余额1266。
- 第三轮自然确认原文生成、仅两张、最多12积分、失败不重试。父task`d30f6119-c131-513f-a2a6-9391e63d7475`的checkpoint.active为空，真实两个工具step均为`image_agent`（round 0/1，各index 0），返回账本task_id的UUID类型错误。没有调用generate_image，没有触发本批参数校验/一次纠错机制。不能把测试结果写成自动Skill或两张异步生成通过。
- 已只读确认生产credit_transactions.task_id为uuid，22:45:58–22:46:11测试窗口内该账号没有credit_transactions或credits_history记录，本测试会话image task为0；余额1266→1254，两个失败调用各造成6积分无账本扣减。随后余额1248对应22:50:49另一会话的已完成原生图片任务，不把它混入本测试误扣范围。
- 根因证据：ImageAgent.execute生成`img_ecom_{uuid4().hex[:8]}`传入CreditMixin._lock_credits；后者先独立提交users余额更新，再插入UUID账本，插入异常时前一步未回滚，且未返回transaction_id，普通退款入口无法按账本回退。两份文件blob与origin/main一致；主线init-database.sql已定义该字段UUID，本任务273–276没有改变该类型。新品异步链claim_chat_image_submission把真实UUID task、预扣和账本绑定放在同一数据库事务中，本次未走该链。
- 当前阶段3/4真实验收受阻，关联M01/M03/M08/M30、F04/F05/F08、T01/T04/T09/T14–T16。原回归漏掉自然请求被电商入口误选及此入口真实UUID/扣费失败边界。已停止新增生图，没有修改生产积分、修复代码或再次部署；12积分尚未补回。需要先处理误扣、旧入口账本原子性及错误路由，再继续两张测试；后续账单修复须真实数据库验证和独立审查。

### 聊天图片统一工具路由修复（2026-10-04）

阶段3/4，M01/M03/M08/M30、F04/F05/F08、T01/T04/T09/T14–T16。用户确认普通聊天及图片Skill固定 `generate_image`，电商专用内部实现保留；本批在同一任务分支0e162515上开发，没有提交/推送/部署、生产写入或新增付费生图。

- 真实调用方复检：普通聊天原核心目录和两套系统提示词同时引导image_agent；EcomImageHandler及原生电商确认事件直接走image_ecom/ImageHandler批次，专用retry直接调用内部ImageAgent，不依赖公共工具展示。保留这些内部绑定，没有删除或复制电商实现。
- `services/tools/definitions/media.py` 将generate_image纳入初始核心候选并保持plan不可见；image_agent改为legacy_internal、移出common_tools/core。现有Registry检查同时管展示和执行：历史模型调用、discovered名单、Skill名单均不能绕过，明确not_started且不刷新身份/重放invocation/调用账本或供应商。开关、账号灰度、个人空间与授权交集保持；关闭新入口不回退旧工具。
- `config/chat_tools.py` 和 `prompt_builder/templates/tool_strategy.md` 移除旧电商工具引导，统一当前schema/默认模型、匹配Skill当前轮激活、两张两次接受与真实task_id后告知提交；视频说明同步移除旧替代工具名。Skill正文/控制面未改，也没有手动固定或预加载Skill。
- 内部诊断来源投影兼容新内部Spec；旧工具完整合同测试保留冻结07基线，只显式记录已批准的路由/说明差异。原生ImageAgent/CreditMixin、数据库、Worker/快照/预算和积分结算未改；路由收口不能被解释为内部账本原子性问题已解决或12积分已退回。

验证：新增16项真实Registry/ToolRuntime/Actor回归修改前14失败/2通过，修改后16通过，稳定复现旧公共路由。覆盖初始与发现展示、关闭/灰度/plan/个人空间/授权交集、旧模型调用执行前拒绝、Skill不能复活旧工具、可信内部兼容，以及脚本化模型自动activate→正文注入→同轮两次单图接受、完整提示词逐字保持和两个子身份。模型/业务/身份DB使用隔离double，不代表实际模型质量、真实积分事务或供应商完成。

- 最终20个相关文件 **1562 passed / 0 skip / 0 failed**，10.08秒；日志 `/private/tmp/chat-image-routing-final-tests.log`。包括共享工具合同/三入口/权限、Actor/Skill、一次参数纠错、原生图片/电商/多模式Skill、接受规格及静态提示词回归。第一次扩大验证的3项失败来自过期目录预期，已将旧内部名单及开关前后的实际展示预期显式更新；未关闭权限断言或替换业务执行机制。
- 修改Python文件编译检查与 `git diff --check` 通过。无前端/SQL改动，不重复此前构建、真实PG/Redis验证，也不把它们称为本批重测。
- API、发布状态、收口发布顺序和保留型回滚已同步。后续部署需重启整个后端/Actor统一目录；回滚保留旧工具内部可见性，防止重新开放已知误扣路径。

剩余：本批尚未部署，生产普通聊天仍为0e162515；真实自然语言自动Skill、确认续轮、两次任务最终图片与唯一扣款尚未复验。保留内部电商账本缺陷及12积分误扣待处理；未做生产故障注入、历史计划快照/图片trial的生产回归。新的模型入口会拒绝历史image_agent调用，不将旧工具授权转为generate_image权限；如有旧计划快照依赖该公共工具，发布前需识别并迁移，不能自动切换成另一付费调用。没有宣称整个系统无回归，T17没有本批新验证。

用户随后明确授权本批“提交部署”。本次发布前只读检查正式数据库身份匹配，pending/running聊天和在途图片均0，paused历史43；实际scheduled_tasks与scheduled_task_drafts的计划/权限/Skill快照没有image_agent依赖，无需生产计划迁移。最新origin/main仍32e8ba0d，候选已包含。按release.sh显式16个任务文件提交推送并完整发布前后端，复用1562项回归、不跳过构建/迁移账本/健康检查。自动验证仅无业务副作用的目录与拒绝边界，不补扣/退款、不发起付费供应商或生产Skill发布；最终发布SHA和DEPLOYED_PENDING_ACCEPTANCE状态以受控入口回执为准。工作树保留，main不合并。

### 普通聊天双图生产复验（2026-10-04）

当前阶段3/4，关联M01/M03/M05/M08/M13/M30、F04/F05/F08/F10、T01/T04/T05/T09/T11/T14–T16。本批受控发布实际成功：`95a18e188a34198cb76051a32b5a10a93c8906c4`，`mode=preview`、`DEPLOYED_PENDING_ACCEPTANCE`、`acceptance_candidate=true`；前后端构建、273–276迁移账本核验、服务与readiness通过。用户随后要求“现在重新帮我测试。还是生成两张图”，本轮只执行获准真实UI验证和只读取证，不再部署或更改生产配置、Skill、账本。

- 新管理员普通聊天 `52c764f0-772e-44f1-9dc9-c2a4c99f856f`，智能模型/自动模式，未点击Skill选择器。第一轮打磨站立挥手、坐姿读书两条提示词；第二轮统一宝蓝、磨砂、大头小身体、黑色圆眼/无嘴、空白书页；两轮都明确暂不生图。服务器只有两条completed chat，图片任务和积分账本为0，余额1248。
- 第三轮自然语言确认原文、默认模型、1K/1:1/PNG、无参考图、最多12积分、只两张且失败不自动重试。真实父chat `5791650c-2016-502b-a94b-3363aac808c7` 在23:37:50完成，checkpoint恰有两次`generate_image`（round0、round1）；工具输入没有model/model_name、旧size/format或prompts数组，无image_agent调用或参数错误。
- 两次接受创建独立子任务、消息和资产；前端工具回执616ms/63ms后显示pending，父聊天先完成。第一张 `9456f659-83de-491f-9b7a-bea6a2e6cc5a` 于23:38:44完成，第二张 `15c2f4df-51f1-4548-b6fb-a7931f24901a` 于23:38:53完成；均`completed/published`、`delivery_pending=false`、各一张结果。冻结模型均为`gpt-image-2-5-flare-text-to-image`、模式`text_to_image`、references=[]；快照prompt与UI最后展示的原文分别逐字一致，没有prepare重复改写。
- 图片消息分别为 `ec043dc0-9b8d-487f-b463-79c8f52a5b7f`、`d1d2258b-f051-456b-97c7-54530015edf3`，均completed/credits_cost=6，context_revision分别4/5，content.task_id正确。资产分别 `31639c7f-5e36-476f-a0a3-64bd1d3242c3`、`e98dbbc9-4fa0-4126-82fc-ca23299dd158`，均ready且各有一条本对话任务/消息引用。
- 各任务恰一条confirmed的lock账本，分别 `5efbd47e-4492-45fe-9e77-52d5c83cbec9`、`c870d92e-124d-47c0-a90e-270e6ec2a8fa`，各6积分；三条chat credits_used=0，余额1248→1236，总12。刷新页面，两张完成图片、历史和余额仍在；只读复扫仍只有2个图片任务，没有重复扣费。此前另一次测试的12积分误扣没有退款，不能将本轮正常账本解释为旧问题已解决。
- 使用受保护数据库身份、只读事务/10秒statement timeout取证；经现有FileTargetResolver权限/路径检查读取已保存原图，无新供应商调用。两张文件都能由Pillow完整verify，实际均 **1254×1254 PNG/RGB**，大小分别1054950/1219061字节。1K请求参数已验证，但不宣称精确1024像素输出或透明背景；UI360×360为缩略图。实际画面动作、白背景、无嘴、空白书页符合基本请求，机器人的头型和天线有差异，尚不能保证严格角色一致。
- **自动Skill仍未通过本轮**：conversation_skill_bindings=0，三条chat均manual_selection=null；checkpoint.manual_skill_id=null、session_skill_ids=[]、active_skills=[]，快照Skill来源亦为空。此次通过的是普通聊天→两次单图异步任务→预扣结算→独立发布→刷新恢复的真实路径，不能计为T01自动激活及完整Skill编排通过。

证据：`/private/tmp/chat-two-image-retest-before-submit.json`、`/private/tmp/chat-two-image-retest-prompts.json`、`/private/tmp/chat-two-image-retest-accepted.json`、`/private/tmp/chat-two-image-retest-final.json`；UI截图 `/private/tmp/chat-two-image-retest-success.jpg`。本轮补足T04/T09基本路径、T11完成后刷新；T05仅参数/文件格式与实测尺寸，未完成精确尺寸合同验证；T01自动激活、生产trial、图生图/多参考图、并发故障注入、T17和全部系统回归仍未验证。没有新增代码或测试用例；上述1562项为部署前回归，不冒充本轮新运行的全量测试。main未合并、工作树保留，新增测试记录尚未提交。

### 历史参考图跨轮定位失败（2026-10-05）

阶段3/4，关联M01/M05/M06/M08/M30/M31，F06/F13相关跨轮来源风险，T03/T12/T14/T15。本次是新增真实触发证据，不以原F编号替代具体根因。继续同一任务工作树；生产候选仍95a18e18。本轮开发、只读取证及本地测试，没有提交、推送、部署、Skill发布、账本写入或额外供应商调用。

**生产证据**：用户截图对应对话`ea51b1d2-da12-428e-9471-6d703ad32561`，23:53:40创建父chat `3b3feeb8-6fd8-52df-9a77-b2021af0ba0a`，用户输入消息`a44d7fa3-2cb2-4e2d-9dc4-269c5701c9d0`仅为“没问题 帮我生成图片”。此前上传/引用原图所在消息`d91e017f-a990-4716-aad8-1a8b22ea0aef`已闭合revision1；本次父任务base_context_revision=1，mode=image_to_image、resolution=1K、aspect_ratio=1:1、output_format=png均合法。唯一generate_image调用引用fid_2a8b9c3d，接受前失败。失败后该对话仍仅3个既有原生图片任务/各一条历史confirmed账本，没有本次新增图片任务或账本，管理员余额1236未变。

**根因链**：只读身份保护事务及现有FileTargetResolver核验原JPEG真实存在、1080×1080且Pillow.verify通过，SHA256=`5d344989e500919e83613757b231b04e559ff6bceddd07f5a3d895d32b5e389f`。真实org/path确定性编号为fid_1bcc70bb，旧None-org编号也为fid_6f31e111，均不是调用中的fid_2a8b9c3d。生产checkpoint.messages在工具调用前没有任何fid；历史投影只把上传图片转成image_url，没有附带消息/资源定位。模型看到图和上轮完整方案，却不能复制原图身份，编造了编号。FileTargetResolver按原有唯一性/权限规则拒绝不存在的编号；ImageHandler将含说明的FileTargetError字符串归一为IMAGE_INPUT_UNAVAILABLE，MediaToolMixin对所有拒绝附字段提示，ImageArgumentCorrection又对所有accepted=false使用“参数自动纠正”停止文案。故障既有定位连续性缺口，也有错误分类/传播误导；供应商并未收到本次请求。

**最小修复**：

- M31历史query加id，历史投影为持久化的user/assistant图片附真实message_id/content_index/name，按原content数组索引保留多图顺序，兼容JSON字符串。视觉图片预算耗尽仍保留定位提示；失败/无workspace_path图片不广告生成定位。元数据不是自动选图或权限授予，不新建编号体系、不传历史路径/签署URL、不改变闭合revision。
- 闭合缓存仅切换到outcomes-v2投影命名空间，原schema2/revision/through_message_id严格合同保持；不使用缺定位的旧缓存，不删除生产缓存、回填历史或重写进行中任务。
- M08接受前保留FileTargetError/ResourceAccessError的安全代码；ValueError/OSError输入读取拒绝明确not_accepted，RPC异常仍在该catch外并保留受理不确定。M05按资源/字段错误分别给出恢复说明；非schema拒绝不声称自动参数纠错，不开启新纠错额度或自动替换参考图/模式/提示词。
- M01工具说明明确复制已有定位，缺定位时先通过获准历史工具读取，不编造file_id。Skill正文及生产版本未改。冻结07基线仍保留，仅更新已授权异步schema升级fixture的说明文字，参数类型/权限/工具合同断言未弱化。

**验证**：新增连续性回归初版8项在修复前7失败/1通过，分别暴露定位缺失、资源代码被抹、错误纠错文案和旧缓存复用（`/private/tmp/chat-image-reference-before.log`）。最终13项覆盖上传A/B→打磨→下一轮只选B原图、原content索引/顺序与digest、JSON输入、user/assistant及视觉额度耗尽、失败/无原图、未来revision/跨对话/跨org拒绝、当前read权限拒绝、未知fid/损坏原图在接受RPC前停止、资源错误不启纠错、旧缓存不复用。测试使用真实临时图片/FileExecutor/Resolver，DB与provider入口为隔离double，仅证明行为边界，不宣称生产事务/RLS验收。

16个相关测试文件最终 **774 passed / 3 skipped / 0 failed**，11.29秒（`/private/tmp/chat-image-reference-regression-final.log`）。3项skip是既有已由PromptBuilder替代的V1 gather编排测试；没有新skip。覆盖历史/cache/snapshot/当前附件、图像接受/一次纠错、工具合同/路由、频道及企微、资源权限/版本执行。第一次扩回归的失败来自旧SELECT断言及异步schema说明fixture/JSON字段顺序，已显式同步新增id与文字，保留原权限、字段和错误合同断言。前端/SQL/账本/Worker未改，不重复原构建/PG并发测试，也不将774项称为全系统无回归。

证据：`/private/tmp/chat-image-reference-failure.json`、`/private/tmp/chat-image-reference-identity.json`。此前普通聊天文生图双图成功只覆盖无参考图路径，未覆盖本次图生图跨轮原图定位；这一真实缺口已记录并补回归。剩余：修复未部署、真实模型读取新定位并接受/完成图生图未复验，Skill自动激活稳定性、精确输出尺寸和旧电商账本/误扣退款仍未处理；生产每轮2张/24积分限制未扩大，截图六个方案需遵守分批限制。发布/回滚顺序见RELEASE增补；任务工作树保留，等待用户后续提交部署指令。

**多轮全量编号复核（用户指出不能仅核对一张原图，2026-10-05）**：前一轮“编号不存在”的表述应限定为当前资源范围可解析性，不能由它不等于一张原图的fid直接推断整个对话/所有历史缓存都从未存在该编号。新增只读完整审计对话10条消息、7个图片块（用户4/assistant3，因重引用实际4张不同图片）；逐项核对当前org、旧None-org、消息org与相对/绝对路径、basename、记录name的确定性fid，没有匹配fid_2a8b9c3d。7个图片块的文件均存在，图片并未丢失；该对话没有user_asset_refs登记记录，未使用资产表缺行推断文件不存在。当前授权workspace完整枚举1609个文件也没有匹配；按原对话各精确路径/名称重建临时查找提示后，实际FileTargetResolver仍返回RESOURCE_NOT_FOUND。未读取或修改运行中Actor的内存缓存，不能证明所有过去的临时别名从未存在。

失败checkpoint在第一个generate_image前没有fid；只有1个视觉image_url，匹配原上传JPEG在两条user消息中的重复引用（最初消息与revision1再次引用消息），并非已经提供7张图的可执行定位后选错一项。fid_2a8b9c3d第一次出现在模型工具参数。结论：当前输入没有可复制的资源身份，模型填了无法解析的编号；根因是跨轮来源定位信息丢失，尚无证据证明它是另一张有效图片的编号。修复必须保持逐消息/原content_index身份及来源，不能以“最近图/第一张图”代替用户指定素材。

另一个明确兼容限制：该对话以前3条原生生成图片及较早用户引用消息的context_revision均为NULL，现有固定revision历史没有纳入它们；本轮只有revision1的用户上传图进入视觉上下文。这是旧图片历史边界/发现能力的后续验证缺口，本次未回填revision、扩大消息权限或声明旧原生生成图跨轮定位已经全部通过。完整证据`/private/tmp/chat-image-conversation-id-audit.json`；没有新增付费请求、扣款、代码变更或重复测试。


### 图片来源与文件ID复用实施（2026-10-05，本地完成未部署）

当前阶段：阶段1安全基础、阶段3/4跨轮来源、阶段5输入详情的增补D01–D06均本地完成。关联M01/M06/M08/M14/M20/M25/M27/M31、F06/F11/F13及T03/T05/T06/T07/T08/T09/T11/T12/T14/T15/T16；原账本原子性及不确定提交约束继续沿F07/F08，未重写。生产仍为`95a18e188a34198cb76051a32b5a10a93c8906c4`，同一任务工作树继续，没有提交、推送、部署、合并main、工作树清理、生产Skill/配置写入或付费调用。

**实现**：

- D01：新增`handlers/chat_context/image_sources.py`，当前/历史/精确读取共同投影现有fid、原消息/图片块位置、来源、可用性与视觉对应；当前附件保留来源字段并服务端核验。正常历史全部调用传实际org，图片视觉预算耗尽仍保留定位，不将分析图自动选作生成参考。缓存升级`conv:msgs:outcomes-v3`，无批量清理。
- D02：迁移277在原串行/分支Actor claim同一事务冻结`_image_sources_v1`，包括原始content及固定base/through；首次覆盖伪造私有字段，之后目录受触发器保护不可改写。只兼容本个人会话的终态旧NULL revision图，支持终态非Actor原生任务已有Turn的旧结果，排除在途Actor用户输入/结果及跨组织/会话。旧已有attempt无目录则冻结空目录，不扩大过去的上下文。超过100条/120000字节冻空目录及明确原因，让正常聊天继续；展示上限20个来源仅限制展示，不截断权威目录。
- D03/D04：裸fid绑定固定历史/当前输入或真正获准file_search返回的签名ref，不能扫描工作区猜匹配。碰撞不同原图拒绝；同一原图多处出现保留occurrences，并剔除错误的客户端引用声明。精确读取保留完整提示词空白/hash；所选旧消息变动拒绝，重启不扩大目录。既有file_search checkpoint恢复只恢复已验证签名身份，不授予权限。资源错误与字段错误分离，受理不确定仍禁止重新付费提交。
- D05：新快照包含服务器所选来源及旧来源证明，request_hash绑定；已有Worker/详情/replay调用同一解析器，重新核验真实原图版本/digest与权限。旧已接受快照继续兼容，没有第二条同步链。来源变化或原图删除时真实Worker确定失败收尾，无供应商调用/账本预扣。
- D06：复用前端现有引用原图和来源链；详情展示冻结消息位置、用途和引用原来源。配方移除模型选择，并优先复制精确消息位置，只包含公共工具输入。URL-only图片仅通过现有canonical资产登记找到同owner/org/scope的工作区原图；验证URL签名变化不影响身份，找不到时明确不可用，不新增任意下载恢复。

独立只读数据库/恢复审查发现并修复：超限目录导致队首claim反复失败、无关变化图片阻断有效选择、运行中Actor输入被误纳旧历史、有Turn的旧原生结果被误排除、URL-only来源展示/解析不一致，以及URL-only带真实资产时引用校验错误。最终复审无剩余高置信阻断项；复审未执行生产验证或代替本地测试。最后同文件错误引用的可用性和配方修正通过专项回归。

**本批实际验证（不复用上一批774项冒充本次证据）**：

| 检查 | 最终结果 | 证据及限制 |
| --- | --- | --- |
| 23个后端相关测试文件 | **654 passed / 3 skipped / 0 failed**，5.66秒 | `/private/tmp/image-source-backend-regression-final.log`；3项为既有已废弃V1编排测试。含19项新来源测试、精确原文/目录边界、当前/历史/cache、真Actor/ToolRuntime、Skill/工具合同、原生图片/电商/视频重试、文件权限及scheduled快照。模型/一般DB使用double，不代表模型理解质量或真实账本。 |
| 真实隔离PostgreSQL17/Redis、实际121/273–277及rollback | **58 passed / 0 skipped / 0 failed**，2.37秒 | `/private/tmp/image-source-postgres-final.log`；12项新来源/claim/回滚、46项既有图片闭环。真实角色无SUPERUSER/BYPASS_RLS，验证串行/分支原子目录、2线程并发claim、作用域RLS、私有目录保护、重领原边界、超限聊天继续、真实双子任务各一预扣账本、来源改变/删除无供应商或积分IO、rollback owner/ACL及目录数据保留。最小隔离schema和测试RLS，不等同生产完整schema/ACL。供应商替身，无付费调用。 |
| 前端来源详情、配方、原图引用、附件提交与messageSender | **70 passed / 0 failed** | `/private/tmp/image-source-frontend-tests-final.log`，4个实际文件；DOM/API替身验证，非真实浏览器/供应商端到端。 |
| TypeScript + Vite生产构建 | **通过**，Vite12.69秒 | `/private/tmp/image-source-frontend-build-final.log`；`npm run build`含tsc -b，保留既有大chunk提示。 |
| 改动Python编译及diff空白 | **23个文件通过**；`git diff --check`通过 | 无安装新依赖、产品环境配置或生产密钥读取。 |
| 额外scheduled Skill HTTP回归 | **7项环境阻塞** | `/private/tmp/image-source-extra-regression.log`；导入既有org_service时缺少`supabase`包，尚未进入HTTP断言。该次另外171项通过，已包含在最终定向证据中，不重复累计。未装新依赖/伪造模块，也不把该HTTP边界称为已验收。 |

V01/V02以真实原图字节、两份逐字prompt和两个独立任务验证选B及积分账本；模型为脚本替身，不宣称自然语言“确认”一定选对图。V03/V04覆盖上传/生成/引用投影、旧native Turn兼容、顺序、同文件多来源及模拟短ID碰撞。V05/V06通过私有cache隔离、检查点搜索恢复、重新构造Worker、DB重领及后到消息排除验证固定边界；没有杀真实生产进程。V07/V08覆盖所选消息变动、文件删除/损坏、权限/客户端伪造、原图与缩略图合同。V09重跑真实DB/Redis闭环故障：丢接受回执、提交前崩溃、发送丢响应/外部ID绑定失败、保存与投递故障、关接受后的恢复等，不重复生成。V10定向回归及构建完成，但上述7项HTTP环境阻塞及所有真实模型/生产组合未完成；T17本批未增加能力或验证。

发布候选已具备本地代码、迁移/rollback、独立审查、定向验证与文档证据。后续须按受控release入口核验生产完整schema/owner/ACL，停新接受并更新全部兼容读端/Actor/Worker后才恢复原灰度；不能只发布接受端。真实模型自动Skill/选图、A分析B两图、旧生成结果继续编辑、刷新恢复与实际供应商/账本验收仍待发布后完成，不能以95a18e18此前文生图样本代替。未支持无登记OSS-only原图自动恢复；旧内部电商误扣12积分及账本缺陷保持另项未处理。

本轮临时PostgreSQL/Redis服务结束后停止，保留专用测试目录和日志；不清理任务工作树。完整API及发布/回滚顺序已同步。用户后续“提交部署”执行当前任务候选发布，不合并main；本轮只开发测试。


### 来源修复提交部署前预检（2026-10-05）

用户明确授权提交部署。生产只读身份匹配，现有串行/分支claim的prosrc与迁移121一致，owner均everydayai、SECURITY INVOKER，runtime有EXECUTE、worker无；tasks/messages/conversations现有RLS均关闭、user_assets启用RLS但非FORCE，保持原样。本次没有为发布改变这些权限；初始预检误要求全部基础表FORCE而停止，随后按实际可信服务边界复核。独立只读审查未发现本批新增依赖基础表RLS的权限防线；公共工具不得开放任意SQL/RPC。

已新增真实隔离PG生产配置用例：临时事务关闭三个基础表RLS，277仍排除跨conv/org，私有目录更新/不匹配user拒绝；273错误actor/org/token全部拒绝，任务/账本/余额不变，事务回滚恢复测试配置。最终数据库/Redis **59 passed / 0 skipped**，2.45秒（`/private/tmp/image-source-release-schema-tests.log`）。第一次重新启动专用cluster遗漏端口参数，测试因专用socket不存在未连接任何数据库；已恢复55439/private/tmp、禁TCP并实际重跑通过，不将连接错误计作通过。

生产预检无pending/running父chat、在途image为0、277尚未应用。保留原灰度与预算，不新增生产Skill或付费生图；受控入口后续核验最新main、锁、正式数据库、迁移账本、完整前后端构建与四服务readiness。实际最终提交、发布与验收候选状态以本次RELEASE_RESULT为准。

远端稳定基座已前进到0065b47cb88ccd4a58bb3a516c05e8d631ec6f26，新增AOCI索引维护/发布核验；业务代码没有在这次main更新中变化。本旧工作树release入口按最新main的16行兼容补丁补齐提交前、main合入后、隔离候选和验收核验，不能从旧入口绕过新门禁。当前会话没有暴露AOCI MCP工具；若合入后Verify/Check未aligned，应保留已提交任务和生产现状，通过本工作树session准备独立配置并重新打开会话接入MCP，按live Guide维护后继续受控发布。不得使用CLI或手写语义/基线冒充MCP维护。

发布入口兼容验证：`bash -n deploy/release.sh`通过；`scripts/testing/test_release_coordination.py`的10项测试通过。生命周期测试旧fixture缺少既有生产数据库身份guard而停止；将最新main的12行fixture修正同步到本任务后，`scripts/testing/test_release_acceptance_lifecycle.sh`通过（`/private/tmp/image-sources-release-lifecycle.log`）。两处补丁与最新main逐字一致，未更改生产控制规则；所有fixture使用临时Git仓库及假SSH，不连接生产。

### 本次受控发布结果与续接（2026-10-05）

受控入口已提交、推送37个任务文件：`86554a0ada5e99cd309916429ca646f31e10aafc`。随后无冲突合入`origin/main`的`0065b47cb88ccd4a58bb3a516c05e8d631ec6f26`，本地HEAD为`f320d007faf3fa89c3723f188e1a5b4d4dccb801`；该合并尚未推送。业务backend/frontend与86554a0a逐字相同。本次在合入后AOCI核验返回未aligned而停止，未进入生产部署执行器、未应用277、未使生产候选失效，也未执行付费生图。

停止后只读核对生产候选：当前是另一已发布任务的`9e454496a24bf0e537c31720867dc9bfd90f4db7`，不是本修复；生产发布锁已释放。历史95a18e18记录只代表此前本图片任务的测试版本，不代表当前生产。任务工作树、提交及本轮结果记录保留，不合并任务到main、不清理任务。

已在实际任务根运行`python3 scripts/aoci-task.py session --repo "$PWD"`，退出2、`stable_update_pending=false`、`maintenance_required`，生成仅本机使用且忽略的`.codex/config.toml`，绑定`/Users/wucong/EVERYDAYAIONE/.worktrees/chat-skill-image-async`及AOCI 0.1.0-rc17。当前工具清单没有AOCI MCP；按AGENTS第132–135行，需要重新打开此工作树会话接入MCP，核对root、Rules/live Guide和完整Overview，再增量维护受影响索引及本轮Observe证据。Verify/Check aligned后，将正式资产及这些结果文档列入受控release提交，并推送包含最新基座的最终候选；不能用CLI、手写索引或删除baseline绕过。

后续无需重复请求用户发布授权。最终候选必要验证以变更范围决定；受控发布仍保留完整前后端构建、迁移及readiness。临时PG55439/Redis专用socket已停止，测试数据目录及日志保留。
