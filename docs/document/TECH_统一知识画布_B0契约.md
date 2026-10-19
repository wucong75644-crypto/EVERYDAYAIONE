# 统一知识画布 B0：协议、数据约束与交互验证

日期：2026-10-19（Asia/Shanghai）。基线：`d597ac35cb7e7d85f09ce681c8e99571ed83f7ae`。
状态：B0 设计与独立原型；不包含业务接口实现、可执行迁移或真实模型/企微接入。
需求来源：[主实施方案](./TECH_统一知识画布实施方案.md) KB-R01—KB-R18。

本轮范围已按用户最新决定收敛：画布页面不规划聊天、分支对话或项目群聊天入口；仅在既有主对话区 @ 总分支引用知识。分支右键提供定时配置，选择执行内容、时间及已有权限的企业微信个人/群目标。

本文件细化主方案，发生冲突时以本文件中标明的 B0 契约为准。正式实现从 B1 开始；新增依赖、生产规则和未明确的持续费用仍遵循主方案的限制。

配套产物：

- [API 与模型工具契约](./knowledge-canvas-b0/contracts.json)：OpenAPI 3.1、消息引用 schema、四个只读模型工具及确定性反例。
- [逐表数据约束](./knowledge-canvas-b0/schema-contract.json)：字段、主外键、唯一约束、索引、RLS 和事务不变量。
- [交互原型](./knowledge-canvas-b0/prototype.html)：单文件本地假数据演示，可直接用浏览器打开。
- [验证记录](./knowledge-canvas-b0/validation.md)：静态检查、原型场景与未执行项。

## 1. 本次锁定的局部设计

| 决定 | 契约 |
|---|---|
| B0-D01 用户入口 | 用户仅 @ 总分支；根目录或没有父节点的主题可作为入口；不列举子节点，不让用户设置读取范围 |
| B0-D02 引用传输 | `knowledge_refs` 为生成请求和消息响应的独立字段，不冒充 file/image，也不只保存 @ 文本 |
| B0-D03 稳定身份 | RootRef 为 object/container 二选一，使用 UUID；显示名称仅供呈现，不参与授权或身份匹配 |
| B0-D04 一轮一致性 | 每条新消息使用最新已保存知识建立读取快照；同一轮导航和读取固定这些版本，避免边读边变 |
| B0-D05 读取方式 | 先总纲/规则，再目录导航、检索和正文读取；分页、缺失、未解析和读取覆盖均有明确状态 |
| B0-D06 对话边界 | 只扩展既有主对话区的知识引用；不增加画布聊天、分支会话或 knowledge 会话作用域；保留原会话身份校验 |
| B0-D07 文件隔离 | 知识工具走资产绑定授权；需要沙盒时只准备获授权的有限副本，不能因引用知识开放他人的个人 Workspace |
| B0-D08 父子结构 | 层级真相收敛到 kb_objects.parent_id；导航树与 contains 连线从它派生，不再双写两套层级关系 |
| B0-D09 保存即调用 | 内容保存事务完成即能从目录/正文接口读取；搜索解析可以异步，不能作为可调用性的唯一前提 |
| B0-D10 实施范围 | B0 只写设计、契约和独立演示文件，不注册生产路由、不运行 DDL、不修改权限行为 |

## 2. 根引用与消息生命周期

### 2.1 请求与响应

原接口 `POST /api/conversations/{conversation_id}/messages/generate` 增量增加：

```json
{
  "operation": "send",
  "content": [{"type": "text", "text": "按照这里的流程处理客户退货"}],
  "knowledge_refs": [{
    "root": {"kind": "object", "id": "10000000-0000-4000-8000-000000000001"},
    "policy": "latest_saved"
  }],
  "client_request_id": "message-command-uuid"
}
```

- `knowledge_refs` 最多 8 项，去重后保留用户顺序；旧请求省略时按空数组处理。
- 每个引用必须为 RootReference schema；object 根必须是无父 topic，其他独立文档仍可由容器根读取；子节点 ID、无权对象、其他组织根一律资源不可用，不静默去掉。
- 客户端不能设置 actor、org、ACL、snapshot_id 或“已授权”标记。这些保留字段出现时明确拒绝，不按可信上下文接收；引用元素采用严格 schema，不为此一并改变旧请求其他无关扩展字段的兼容性。
- 用户消息及响应持久保留原始根引用；服务端另保存该次 run 的 snapshot 记录。显示标签可更新名称，审计保留发送时名称。
- Pydantic GenerateRequest、消息 DTO、前端 SendOptions、GenerateRequest、乐观消息、发送器、服务端序列化和历史消息投影同步演进。
- 不能只给模型拼接“@售后处理”文字；若 schema 不识别引用，应明确报错而非静默忽略。

### 2.2 重试、重新生成与刷新

| 场景 | 行为 |
|---|---|
| 同幂等键网络重试 | 原请求摘要包含 knowledge_refs；回放原消息、任务及快照，不读取新版本 |
| 同键但改根引用 | 409 IDEMPOTENCY_PAYLOAD_CONFLICT |
| 恢复未完成的同一任务 | 沿用原快照并重新鉴权；权限撤销则停止，不能靠快照继续访问 |
| 手动重试失败任务 | 默认沿用该次输入引用和已取得快照；若尚无快照，则第一次建立；界面如实显示使用原资料版本 |
| 重新生成 | 创建新尝试，以同一根引用读取最新已保存版本；记录新的快照；不改原尝试依据 |
| 新一条消息 | 用户标签仍在时沿用根身份，重新按 latest_saved 建快照；不把上一轮展开过的全量资料永久塞入提示词 |
| 刷新/跨端 | 从消息 knowledge_refs 和运行快照恢复；不能只从前端内存找标签 |

已有消息省略字段保持可读。不能把结构化引用降级成默认开放的普通附件；历史会话仍按原身份与资源策略访问。

### 2.3 对照现有文件

- 前端：useFileMention/InputArea 的文件提及行为保留；新增根引用候选适配和独立标签状态；文件名搜索不用于找知识根。
- messageSender/messageSendLifecycle/useMessageStore：将引用纳入请求、乐观消息、幂等摘要、失败恢复和消息响应。
- backend/schemas/message.py、message_request_preparation.py、message_service.py：验证并原子保存引用；不是仅放进 params 后期待下游自动处理。
- Actor enqueue/恢复：根引用及快照 ID 进入持久运行参数，按同一输入消息恢复。附件清单仍服务文件，不强塞整个知识图。
- 相关历史缓存、摘要、WS 和前端投影只保存允许展示的标签/引用，不把权限状态缓存成永久授权。

## 3. 读取快照与智能体导航

### 3.1 建立快照

服务端在获得真实 actor/org 后：

1. 验证会话、根类型和当前容器权限，检查根确实为容器或无父主题。
2. 在可重复读事务中锁定涉及容器的结构版本，枚举授权后代的对象 ID、版本和层级。只冻结元数据，不向模型注入所有正文。
3. 将请求的根与已持久化输入消息核对一致，记录 snapshot、items、当次附件不可变版本、规则版本与 source revision；释放锁后才做模型调用。
4. 初次上下文注入根说明、全局适用规则、目录概要和读取状态。
5. 每次工具调用再次校验实时权限与源状态；版本固定不等于授权固定。

所有容器内容/层级/附件/规则更新都在同一容器锁协议下递增 revision；事务按容器 UUID 排序锁定，避免跨根死锁。快照不调用外部模型或文件转换，不长时间占用写锁。

重命名或移动不破坏引用 ID；根被移动成子节点后，已有引用可定位其身份但新调用返回 ROOT_NO_LONGER_TOP_LEVEL，提示选择其当前总分支，不把范围静默扩张。

### 3.2 四个只读工具

| 工具 | 输入 | 返回及边界 |
|---|---|---|
| knowledge_outline | root_handle、parent_handle、cursor、limit | 固定快照内的子目录、关系摘要、处理状态及下一页；返回的 opaque handle 才能用于后续读取 |
| knowledge_search | root_handle、query、cursor、limit | 仅该快照授权内容；含来源版本及命中定位；未索引内容数量/状态明示 |
| knowledge_read | root_handle、item_handles、cursor | 正文或已解析附件内容、适用规则和来源；明确 partial/complete/processing/unavailable |
| knowledge_follow_reference | root_handle、link_handle | 只跟随已读范围内真实存在的引用；核验目标实时权限，记录来源与版本，不允许任意目标 UUID |

root_handle 为服务端绑定当前任务、actor、根和 snapshot 的不透明标识；它不是 bearer 授权凭证，盗用别人的 handle 不能访问内容。模型不能通过输入 schema 选择 actor/org 或随意更换快照。

limit 默认 30、最大 100；read 单次最多 10 个项，响应按模型上下文预算分页。游标绑定 root/snapshot/查询摘要和排序，不能跨任务或跨根重用。

follow_reference 不把另一知识库整个根自动变成授权范围。只读被引用对象的当前获准版本；后续继续跟随必须由真实引用链支持，使用 visited 集合、深度和预算限制防止循环。未知或未授权引用不返回标题、路径和数量等隐私信息。

现有 search_knowledge 保持 Agent 经验查询用途，新工具接入统一 Registry/Policy/Dispatcher，使用新的 capability 名称 knowledge_workspace.outline/search/read/follow_reference，避免与既有 knowledge.search 混淆。interactive、scheduled、preflight 均执行相同资源上界；写入结果沿用知识命令/现有 ChangeSet，不在只读工具内暗中执行。

### 3.3 规则与读取覆盖

- 规则必须为用户明确创建的 rule 类型内容。根规则、祖先规则与局部适用规则在相关正文前一同返回；附件文本和引用网页不会自动升级为规则。
- 具体业务条件和例外可缩小一般规则的适用范围，但不能扩大操作权限。规则冲突时返回 rule_conflicts，不伪造“已按规则完成”。
- 模型自己选择相关子分支，不要求用户代为定位；问题不明确、资料缺失或业务规则矛盾时才追问。
- 读取记录分别保存 outline_seen、snippet_read、body_partial、body_complete；看到目录或搜索片段不等于读过正文。
- 全框架任务只有遍历完成且必要资料读取完毕才可标记 full；否则返回 partial 和 missing_sources。普通定向问答记录 relevant_scope，不声称阅读了所有附件。
- 模型最终回答含 source refs；请求的工作实际执行成功必须来自真实工具结果，不能由模型一句话决定。

## 4. 主对话区引用和文件运行时

### 4.1 沿用既有会话作用域

本轮不新增画布聊天、分支对话、项目群聊或 `scope_type=knowledge`，不建设 kb_conversation_links。知识画布只负责内容组织、预览编辑、导航和分支定时配置。

用户在既有主对话区输入 @，选择有权访问的总分支；消息持久保存 knowledge_refs，由当前真实 actor/org 和会话授权构造可信 knowledge_context。智能体自行导航子分支、读取资料和规则，并返回可定位来源，不需要画布页面提供输入框。

现有个人主会话维持原 user 所有权与个人资源行为；知识读取额外受根引用和实时内容 ACL 限制。不得因引用共享知识而开放上传者私人 Workspace，也不得因能读某个根就放开其他项目。已有 channel 维持原通道隔离，未经对应适配不能自动开放私人知识或新增共享群聊能力。

用户失去容器访问权时，拒绝新知识读取和引用解析；历史标签显示不可用，历史记录按原会话规则处理。knowledge_context 是运行时资源边界，不是新的会话 scope。

### 4.2 文件与沙盒

knowledge_read 通过 kb_object_assets 和不可变 asset version 读取，不依赖上传者的私人路径。原 user_assets 中的用户/通道存储身份保留；内容共享由绑定授权表达，不通过伪装上传者解决。

需要文件工具或沙盒时，在该 actor 的独立任务 staging 中准备获授权副本，知识引用不扩大原会话的个人资源权限。ResourceManifest 仅列这些副本及明确输出目录，ResourceAccessBoundary 禁止读取其他个人目录；每次物化前复核原绑定与版本。跨成员不得直接把原路径传给个人 FileExecutor。

工具的资产 ID 必须标明 canonical_asset_id 与 attachment_ref_id 的命名空间；不能把 conversation_attachment_refs.id 与 user_assets.id 混为同一个资产身份。原文件附件协议继续兼容。

## 5. 数据约束与事务

[schema-contract.json](./knowledge-canvas-b0/schema-contract.json) 是 B1 的逐表设计输入，不是已运行的数据库结构。字段类型、约束和动作策略在文件中列明。

21 张表是核心领域的分批清单，不是要求 B1 一次建设：B1 先做容器、项目、成员、对象、版本、布局、关系、回执、事件 9 张表；B2 再接资料与检索，B4 接读取上下文，B5 接调度绑定。协作 CRDT 持久化随 B3 服务选型确定，不把候选服务的存储表提前硬塞入第一批。

### 5.1 权限函数语义

`kb_can(container_id, action, actor, org)` 必须由事务级 DatabaseScope 取得 actor/org，客户端不能覆盖：

- actor 为有效用户；org 非空时还必须是该组织有效成员，且容器 org 与请求上下文一致。
- personal：仅 owner，可读写/管理；组织管理员不自动获得读取。
- project：有效 kb_container_members 的 viewer/editor/manager 决定读/写/管理；创建人同事务成为 manager。
- team：组织有效成员默认可读；编辑和内容管理要求显式 container member/editor/manager，组织 owner/admin 可管理团队空间。该例外只适用于 team。
- 子表从父容器授权；跨对象关系读取要求两端可读；跨容器发布分别检查来源读取/分发与目标写入。
- 数据库服务账号不因 access_kind=worker 获得读取所有私人内容的通行证；任务带真实执行 actor，定时执行与读模型保持相同边界。

RLS 使用现有 app.actor_user_id/app.org_id；新表启用并强制行级策略。辅助授权函数以受控数据库角色读取成员元数据、固定 search_path、禁止 PUBLIC 调用/任意 SQL；不能因成员表自身 RLS 递归而退回无过滤查询。成员写入、角色提升、归属变更仅经受控事务函数。

### 5.2 五个关键事务

| 事务 | 锁、检查和提交结果 |
|---|---|
| create_object / create_project | 验证 actor/容器；锁容器；命令去重；父节点存在、同容器；同事务写对象/初始版本/布局/事件/回执；项目创建同时写 manager 成员 |
| reparent / trash / restore | 锁容器；验证版本与子树；递归拒绝环；写新版本/墓碑和事件；引用或任务不静默失联；恢复重新鉴权 |
| bind_asset / save_document | 确认可靠资产和不可变版本；CAS 当前版本；写 object version、version assets、索引任务、事件；索引失败不丢原内容 |
| accept_message / resolve_context | 原幂等事务原子保存用户输入、knowledge_refs、任务意图；任务开始前创建一次版本快照；恢复不重复创建，也不采用新版本 |
| complete_run / publish_result | 原运行事务扩展：run_id+output_key 幂等写知识对象/版本/事件及原投递事实；失败全部回滚；外部文件先持久化再挂引用 |

空组织唯一键按现有 PostgreSQL 版本选用 NULLS NOT DISTINCT 或显式 COALESCE 索引，不能使用默认允许多 NULL 的唯一约束冒充“个人空间唯一”。parent_id 的同容器复合外键、树防环、命令键、输出键和文件版本身份都须有实际数据库约束或锁内验证。

### 5.3 迁移与回滚拆分

| 批次 | schema 与代码顺序 | 回滚条件 |
|---|---|---|
| M1 内容底座 | 新表、索引、RLS、受控命令 RPC；不启新 UI | 允许停入口并保留表；不清理原知识/项目表 |
| M2 文件/索引 | 绑定版本、可靠资产适配、生命周期检查、索引 Worker | 保留文件/快照；不得删仍被引用的历史版本 |
| M3 主对话引用 | messages.knowledge_refs、读取快照及原消息/Actor/WS投影兼容；不改 conversations scope 约束 | 先停新引用执行并排空相关任务；保留引用和审计快照，禁止旧服务静默忽略引用继续执行 |
| M4 自动化 | source_binding 进入 ChangeSet 指纹、版本快照和提交事务；结果投影接原 Outbox | 停领知识自动化，保留投递消费者；旧 Worker 不得忽略绑定执行 |

本轮不分配迁移编号、不执行 DDL。B1 编写 SQL 时必须以实际角色执行 RLS 集成测试；静态 schema 检查不能证明权限正确。

## 6. 原型与交互验收

原型沿用现有低饱和深色界面、清晰边界和信息层级。只模拟本地状态，不验证后端或智能体效果。

| 场景 | 用户操作与观察 |
|---|---|
| UI-01 三分类 | 切换顶部三项，只切换目录；画布视野不移动；右侧无创建按钮 |
| UI-02 画布创建 | 双击空白、选择内容类型和归属；保存后节点与目录同步；取消不留节点 |
| UI-03 思维导图 | Tab 子节点、Enter 同级、F2 标题；拖动节点；连接点拉到空白创建，拉到节点关联；Space 平移、滚轮缩放、撤销 |
| UI-04 资料 | 点击文档展开并编辑保存；从文件类型选择本地图片/视频/文本绑定并查看；不支持类型显示能力边界 |
| UI-05 导航搜索 | 选项目后搜索仅当前项目；点击结果定位高亮；清空恢复树；“返回”恢复上一个视野 |
| UI-06 页面边界与主区引用 | 画布无聊天框、聊天面板或创建对话项；总分支改名保留稳定 ID。主对话区 @ 由消息契约验证，本原型不模拟主对话页面，真实候选、发送、读取与来源定位在 B4 验收 |
| UI-07 定时配置 | 右键内容→设置定时；选择时间和示例群；保存为自动化节点；显示“本地演示，不发送消息” |
| UI-08 失败恢复 | 空搜索、取消菜单、空标题、循环关联、暂不支持文件等状态清楚；Esc 关闭；错误后能继续 |

原型中的“任务保存”仅保存本地状态，不使用模型、不触发后台调度；画布不包含模拟聊天或模型回答。UI-03 仅验证桌面交互；真实多人同步、IME组合输入完整兼容和移动端属于业务实现后的验收。

## 7. B0 交付与后续边界

B0 完成条件：JSON 契约可解析且 schema 引用闭合；请求/工具正反例验证通过；逐表约束和权限动作有对应项；原型覆盖 UI-01—UI-08 中的画布主路径并记录边界；UI-06 的真实主对话区引用留待 B4 集成验收；主方案指向本文件。

新知识接口沿用现有 success/data 成功包装和 error/code/message/details 错误包装，认证错误沿用既有入口行为；不引入与现有 API 客户端不兼容的另一套默认包装。

B1 可按本文件实现内容/权限底座。主对话区引用集成、协作依赖准入、CDN 实际鉴权、真实模型导航评测、纯提醒额度与企业微信通道实测仍在对应后续批次完成，不把本轮原型当作这些能力的上线证据。
