EVERYDAYAI：从聊天创建 Skill — 已确认的实施方案
设计基线：origin/main f352959d48a4f913ef66876bc69056951292aa05（本次读取时的已验收稳定提交）。
状态：方案与预览格式已获用户认可；本任务已在独立工作树实施，定向验证进行中。当前仅为代码候选，尚未部署。未经本任务新的“提交部署”授权不部署。

一、产品目标与第一版范围
用户在智能聊天说“把刚才这套方法保存成 Skill”，AI 整理方法并生成可编辑候选；用户点击“创建草稿”后，系统才创建真实 Skill 草稿。发布仍使用现有审核与发布流程。“候选已生成”“草稿已创建”“已发布可用”分别展示，不混用成功状态。
第一版结构化创建和更新面向当前组织 owner/admin。普通成员保持现有权限，可以让 AI 整理文字，不能保存、更新或发布组织 Skill。本期不开放个人 Skill 存储，也不增加普通成员提交组织草稿的权限。若用户希望所有普通成员都能保存个人 Skill，需要单独扩展所有权、存储、目录、审核与删除机制。
第一版自然语言入口在智能聊天；图片、视频、电商任务结果通过消息“更多 → 整理为 Skill”入口进入相同流程，保留原媒体模式及生成参数，避免将创建指令当成图片提示词。输入区附件、Skill 标签、范围图标、弹框锚点保持既有位置。
第一版包括：创建新 Skill、自然语言修改候选、确认保存、明确指定已有 Skill 的草稿更新、文本与直接图片草稿试用。电商/视频方法可创建，但草稿真实执行试用留到后续；发布后沿用已有正式执行链路。
不包含：每轮聊天自动扫描与创建、自动发布、自动启用或会话固定、自动生成可执行脚本、整包跨产品格式导入导出、向量检索、模型新增工具权限。

二、用户流程
1. 用户说创建指令，或点击某条已完成消息的更多菜单。
2. 从消息“更多”入口发起时，整理指令带有所选助手消息 ID，模型将它回传给候选工具；服务端再验证它属于当前用户/组织会话，并只把同一轮关联消息的 ID、角色和内容哈希写入审计，不复制正文。自然语言入口默认记录当前会话最近最多 40 条用户/助手消息作为来源范围。候选由当前聊天模型直接整理，不另起独立提炼调用；模型发现多个无关任务或范围不清时应先追问。来源消息、文件和模型输出都不构成保存或发布授权，必须由用户在卡片明确确认。
3. AI 提炼名称、简短用途、适用模式、必要输入、具体步骤、输出标准、缺项处理和常见限制。关键内容不明确时追问；未解决项标记“待确认”，不伪装成已确认规则。
4. 聊天中展示候选卡片：名称、用途、输入、模式、保存到的组织、待确认项。完整提示词可展开、编辑。支持“名字改短一点”“保留包装文字”等自然语言修改；新版本明确展示变化。
5. 用户可选“试用”。试用不是保存，也不是审核通过。图片执行前显示参数与预计费用，用户点击试用才请求 Provider。
6. 用户点击“创建草稿”或“保存到现有草稿”。服务器提交已确认的冻结候选，显示真实 package_id 对应的结果与“前往审核发布”入口。
7. 复用现有草稿 → 提交审核 → 审核通过 → 发布流程。发布后目录按组织授权和模式显示；不自动选择或绑定。创建阶段的用户确认不替代发布审核。

三、内容提炼原则
来源是服务端读取且用户有权访问的原消息，保留 role、message_id、顺序和边界。用户最新明确修正优先于被否定旧方案；模型意见、工具数据、文件正文和网页引用分别标记为资料，不作为发布或权限授权。
保存“怎么完成同类任务”的方法，把商品、日期、订单编号、单次文件等留作每次输入；不把单次结果、账号标识、密钥、绝对私有路径或整段聊天写入组织 Skill。自动敏感信息检查不宣称能发现全部敏感数据，预览中提示组织保存范围。
用户文件不自动复制进 Skill。第一版自动提炼不新增附件资产；有需要时用户通过现有后台附件流程明确添加。样例图片仅用于试用，不固化成通用资料。
业务输入用正文描述，不冒充现有 template_variables。当前变量机制只接受既有服务端来源，模型不能自创变量来源。
生成结果只允许名称、用途、正文、建议适用模式、必要输入与待确认项等有界字段；模型不得填写组织、权限、NAS 路径、发布状态、审核者、版本哈希或工具授权字段。推荐模式由用户预览确认，权限由服务器设置。
新 Skill 默认 model_selectable=false；执行范围先为 interactive。工具策略沿用平台现有默认与实际权限上限，不由模型扩权。计划任务适用范围、ERP 业务域及成员限制仍需在现有高级设置明确配置并审核。

四、架构与复用
智能聊天 Actor / 消息更多入口
  → 聊天模型在用户明确请求后调用 prepare_skill_draft，提交有界候选字段
  → Skill 创建服务验证管理员、会话来源和字段白名单并规范化 DraftContent
  → 现有 ChangeSet 候选状态、审计与聊天引用
  → 用户确认冻结候选
  → Skill 草稿适配器调用 SkillAuthoring 事务
  → 既有审核、NAS 不可变发布、目录与 activate_skill

增加模型工具 prepare_skill_draft，仅负责生成/修改候选，不保存真实 Skill，不提供 publish/approve 操作。模型侧工具走现有 ToolSpec/Registry/Policy 和 replay_requirement=record_required；仅当前组织管理员、interactive 且功能开关开启时开放。不能绕过已激活 Skill 的工具上限；工具不可用时可用显式界面入口，不偷偷放宽工具列表。
第一版由当前聊天模型整理候选；服务端不会把模型输出当作保存或发布授权。工具只建立待确认 ChangeSet，卡片用户确认后才写草稿。源消息正文不进入新审计表，候选哈希及来源消息 ID/哈希用于追溯。文本和图片试用另用隔离的 ModelGateway 请求，不带工具、原聊天历史或会话 Skill 绑定。只在用户发起创建或修改时整理候选，不增加每条消息的后台调用。
ChangeSet 新增业务 resource_type=skill_draft，operations=create/update；不新建通用候选数据库。proposed_snapshot 保存规范化后的 Skill 候选；audit_subject 保存来源会话与消息 ID、来源摘要哈希、提炼模型及提示词版本，不能保存整段来源正文。
通用 ChangeSet 当前 confirm 和 approval action 实际绑定 scheduled_task，需要增加按 resource_type 显式选择适配器及动作投影；不得把任何资源误交给定时任务适配器，不能根据模型 JSON 动态执行任意函数。
候选在待确认阶段冻结。编辑创建新候选并取消旧候选；confirm 必须校验当前候选 ID、版本、规范化内容哈希。旧卡片、迟到模型结果和旧组织页面不能保存新内容。确认必须来自认证用户的界面提交，模型工具无确认提交能力。

五、草稿提交、幂等与数据库变化
现有 SkillAuthoring.create/save 没有独立的聊天提交幂等回执；ChangeSet 与业务草稿是两段提交，必须处理业务已写入但候选状态尚未更新的崩溃窗口。
新增小型 skill_authoring_receipts 表，change_set_id 唯一，记录 org_id、发起用户、package_id、规范化内容哈希、最终草稿 revision/version、operation 与时间。回执与草稿创建/保存及现有审计在同一数据库事务提交；不重复保存正文。采用符合现有组织隔离的 RLS/权限，限制为受控 authoring 写入。
同一 ChangeSet 重复确认或崩溃恢复先读取回执，返回同一结果；不能以同名 Skill 或普通成功话术猜测成功。新建预分配服务端 package_id，业务事务确认标识、唯一 skill_key 和权限；同名冲突返回可选择项，不覆盖已有 Skill。
更新只根据用户明确说出的 Skill 名称精确匹配当前组织内唯一、可编辑的草稿；服务端解析 package_id 和 expected_version。没有匹配或出现同名多项时先追问用户，不让模型猜目标 ID。已有未保存草稿、in_review、disabled、deprecated 状态不能被 AI 静默覆盖或强行转状态。对已发布项明确提示新草稿版本、当前发布版保持有效；基线变化返回冲突并保留候选。实际写入前重新读取组织、成员状态和 owner/admin 权限。
迁移只追加新表，不修改历史迁移、Skill revision、已发布文件和现有绑定。迁移编号在实施时按最新 main 分配。

六、接口建议（命名待实施时按现有路由风格落地）
POST /skills/authoring/proposals
  参数：conversation_id、来源消息 ID/边界、可选目标 package_id、用户创建说明、幂等键。
  服务端校验会话归属和组织；不接受任意历史正文作为已认证来源。
PUT /skills/authoring/proposals/{id}/revision
  参数：expected_revision、用户修改说明或白名单编辑字段。生成替代候选并使旧候选失效。
GET /change-sets/{id}
  复用候选状态查询，补充 Skill 资源权限与状态展示。
POST /change-sets/{id}/confirm
  Skill 资源要求 expected_revision/content_sha256；旧资源保持原兼容行为。只提交草稿，不发布。
GET /skills/authoring/proposals/{id}/trials/estimate
POST /skills/authoring/proposals/{id}/trials
  参数：候选版本、试用输入、明确的媒体参数及请求幂等键。
GET /skills/authoring/proposals/{id}/trials
PUT /skills/authoring/proposals/trials/{trial_id}/feedback
POST /change-sets/{id}/cancel
  复用取消入口。取消不删除正式 Skill、历史记录或已经明确提交的试用任务。
正式审核与发布保持现有 /skills/admin/orgs/{org_id}/{package_id}/transitions。

七、试用边界
未发布候选不进入正式目录，不通过 activate_skill 加载。试用服务从冻结候选获取正文，并在独立试用会话中执行；不继承原对话历史、固定 Skill 或其他方法。
文本试用只验证给定资料上的输出，不调用外部业务写操作；界面不能把内容示例测试显示成“完整业务执行已验证”。
图片试用先将候选和用户资料准备为生成提示词，再复用现有图片 Handler 的参数、积分、Provider、任务和回调机制。任务额外保存 trial=true、候选 ID/版本/内容哈希；不能伪装成正式 Skill revision。客户端不得自行提交内部试用标识来跳过正式权限检查。
媒体执行仍须用户明确点试用；第一版每次生成一张，用户可选比例和当前会话中本人上传的参考图。无参考图使用现有默认文生图模型，有参考图使用现有图生图模型。新试用会话防止既有绑定混入。采用同一请求幂等键避免重复点击重复计费，生成中的不确定状态先查现有任务，不盲目再提交供应商。
测试记录分别说明实际执行完成、输入缺项处理、用户评价与可验证的效果标准；例如白底图背景的像素检查和文字保真不得用“任务 completed”替代。

八、状态和恢复
用户可见状态：整理中、需要补充、待确认、试用中、试用完成/失败、保存中、草稿已创建、保存冲突、已取消。
整理失败不创建 Skill，用户可重试；网络回执不确定先查候选/业务回执；刷新重建服务器真实状态，已提交卡片不能再变为可创建。
组织切换、权限撤销、来源会话删除/不可访问、候选过期：停止依赖操作，不用其他会话或组织兜底。
取消、关闭编辑和异步返回：不得覆盖用户正在编辑的内容或使旧卡片再次可提交。

九、文件职责（现有路径均已通过 origin/main 查明；新增名称为建议）
backend/services/skills/creation.py（新）：创建流程、来源检查、提炼与候选协调。
backend/services/skills/creation_contracts.py（新）：有界输入输出与候选契约。
backend/services/skills/change_adapter.py（新）：Skill 草稿 ChangeSet 业务适配器、提交校验。
backend/services/skills/authoring.py：最小增加事务内聊天回执与可复用的创建/保存写入方法。
backend/services/skills/authority.py（新）：复用现有管理员核验，供 HTTP、模型工具及提交阶段使用。
backend/api/routes/skill_creation.py（新）：候选、修改、试用接口。
backend/api/routes/change_sets.py：按资源选择适配器，Skill 确认版本核验。
backend/services/tools/definitions/skills.py（新）与工具定义加载/既有分发接线：仅候选工具注册，不在 config/tool_registry.py 单独注册后就视为已拥有执行能力。
backend/services/skills/trials.py（新）：独立试用服务、冻结候选与正式执行隔离。
backend/migrations/<新编号>_skill_authoring_receipts.sql：幂等回执与组织隔离。
frontend/src/components/chat/message/MessageActions.tsx：更多菜单入口，不挪动输入区。
frontend/src/components/chat/message/SkillDraftCard.tsx（新）：候选详情、编辑、试用、保存与状态。
frontend/src/components/chat/message/MessageContentBlocks.tsx：沿用 changeset 引用，分派 Skill 候选展示。
frontend/src/components/chat/message/changeSetResourceAdapters.ts：Skill 资源展示适配，保持定时任务兼容。
frontend/src/services/skillCreation.ts（新）：候选与试用客户端。
前后端开关配置：新增独立聊天创建和草稿试用开关，默认关闭，兼容现有 Skill 开关。

十、实施顺序与验收
A. 服务端来源读取、提炼契约、管理员权限和候选，先验证不会保存发布内容。
B. 聊天工具/更多入口、候选卡片、自然语言和直接编辑、旧卡片失效。
C. ChangeSet 草稿适配器、事务幂等回执、管理员后台关联、现有 Skill 更新。
D. 文本与图片独立试用、费用与任务状态、历史刷新恢复。
E. 定向测试与授权后的生产真测；不自行部署，用户明确“提交部署”后使用 deploy/release.sh。
测试至少覆盖：一句话生成候选且正式目录无新增；确认只创建一个草稿；重复确认及崩溃窗口回放；撤权/跨组织/普通成员/过期候选拒绝；文件网页和助手内容无法授权发布；被否定方案不固化；多任务歧义和缺项处理；旧候选/旧响应不覆盖新版本；原 Skill 发布版和重试版本不受影响；模板来源校验；未发布试用不进目录且不继承绑定；媒体参数/积分/幂等；关闭开关保持原聊天和正式 Skill 可用；定时任务 ChangeSet 回归。
真实验收至少使用两套不同资料复测可复用性。内容与效果的模型测试不要求随机文案逐字相同，验证明确规则、必要输入、真实回执及可测的结果。

十一、回滚
关闭聊天创建/试用开关阻止新请求；已明确提交的任务按原任务机制核对状态，不直接丢弃。保留草稿、回执和审计，新表不做破坏性回滚。已经发布的 Skill 沿用现有停用/版本流程。代码回退只撤回本功能，不重部署旧 main 覆盖其他已验收更新。

十二、已确认的第一版范围
推荐第一版：组织管理员创建和更新；智能聊天一句话入口与所有结果消息的更多菜单入口；创建后为草稿、审核发布后可用；文本与直接图片试用。个人 Skill、普通成员保存权限、自动建议保存、电商/视频草稿真实试用延后。
用户已认可本方案；开发安排在当前对话内分批推进，部署仍须独立授权。支持从纯聊天打磨的提示词创建，不要求先执行真实任务。预览格式：名称、用途、适用模式、必要输入、保存组织；完整正文按目标、输入、步骤、约束、输出、缺项处理展开。

研究依据（官方产品机制，不代表其内部提示词）
Claude 对话创建、追问和测试：https://academy.claude.com/tutorials/how-to-create-a-skill-with-claude-through-conversation
Gemini 聊天创建与聊天修改：https://support.google.com/gemini/answer/17094296?hl=en
OpenAI Plugin Creator 的对话描述、检查和私有测试：https://learn.chatgpt.com/docs/build-plugins
微软自然语言创建 Skill、查看细节与试用：https://learn.microsoft.com/en-us/microsoft-365/copilot/extensibility/agent-builder-add-skills
