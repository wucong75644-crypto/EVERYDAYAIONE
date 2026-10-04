# 聊天 Skill 图片异步生成实施记录

更新：2026-10-04。此文档记录实际实现与证据；[原设计](TECH_聊天Skill生图编排与系统兼容实施方案.md)中的编号继续用于追溯，旧任务第十一章不适用。

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
