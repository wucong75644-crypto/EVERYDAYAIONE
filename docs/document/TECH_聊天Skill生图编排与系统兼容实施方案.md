# 聊天 Skill 生图编排与系统兼容实施方案

> 本任务只读复制自旧工作树 `20260905210932-generate-image-async` 的同名文档（2026-10-04）。
> 新任务基座为 `32e8ba0d2128be07856a509384f19ec6f446e493`。第十一章旧任务合入/保留修改指令不适用于本任务；阶段状态以同目录实施记录为准。

本方案把聊天模型的需求理解、提示词编排与参考图选择交给现有 Skill Runtime，把每张图片的执行交给现有图片任务体系。保留工具名 `generate_image`，一次调用只创建一张图的独立任务；不再使用聊天同步等待图片完成的旧实现，也不再让图片工具另做一次任务规划。

核心结论：现有软件有足够的主体能力，但不能只替换一段桥接代码。必须同时接好工具展示、可信素材解析、独立结果消息、提交快照、幂等与计费、重启恢复、完成收尾、重试入口，以及旧同步函数的 Skill 试运行调用方。以下给出具体改动范围、负面影响和发布门禁。尚未实现的区域编辑、原生参考权重等能力不能靠 Skill 文案伪装成已支持。

## 一 调研基准和交付状态

本轮状态为技术设计与影响审查，未修改功能代码，未执行数据库迁移、发布 Skill、提交、推送或部署。

- 调研日期：2026 年 10 月 4 日。
- 代码基准：最新拉取的 `origin/main`，提交 `32e8ba0d2128be07856a509384f19ec6f446e493`。本地主工作树落后，不能用它的文件内容作为最新实现。
- 最新代码的只读调研副本：`/private/tmp/everydayai-image-architecture.DFtwRy`。本文代码路径均相对于上述提交，不依赖临时副本长期存在；可用 `git show <基准提交>:<路径>` 复查。
- 保存位置：本对话原任务工作树，分支 `codex/task/20260905210932-generate-image-async`。该工作树尚未合入最新主线；保存设计不表示它可以直接发布。
- 原任务已有两处未提交修改，分别位于 `backend/services/media_tool_executor.py` 与 `backend/tests/test_media_tool_executor.py`，均为 `_org_id` 透传相关；本轮保留原样。

“不破坏系统”是实施与发布的验收要求，不是只读调研可以提前证明的结论。本方案给出防止破坏的边界、故障处理和验收证据要求；修改后必须补足这些证据才能发布。

## 二 已确认的产品行为

| 编号 | 行为 | 可验证结果 |
| --- | --- | --- |
| R01 | 自动选择并激活生图编排 Skill | 有生图或提示词编排意图时能激活；仅讨论方法时不付费生图 |
| R02 | 一次工具调用一个图片任务，多图由模型多次调用 | 每张拥有独立 task、结果消息、状态和账单；允许同提示词生成多个变体 |
| R03 | 输入顺序可以打乱 | 先提示词后图片、先分析图后生成参考图、混用历史素材均可 |
| R04 | 精确区分文生图与图生图 | 每个图片任务单独确定模式；不能因为聊天中有上传图片就默认图生图 |
| R05 | 多参考图和来源准确 | 能选当前、历史、生成结果；分析图不自动成为生成参考图；不猜 URL |
| R06 | 提交后输入冻结 | 后续换图、改提示词只影响新任务；重试可找到原始执行输入 |
| R07 | 异步显示与恢复 | 接受任务后显示独立占位；刷新、断线、乱序完成不覆盖聊天或其他图片 |
| R08 | 部分执行、先样张、选择版本 | 只执行用户选中的项目；样张确认前不自动提交其余项目 |
| R09 | 成本和停止可控 | 服务端限制张数与预算；停止未提交项；已受理项按真实能力处理 |
| R10 | 扩展功能全纳入范围 | 展示真实输入、版本与系列一致性、参考用途、规格与 ZIP、配方、区域编辑、反馈均有落地边界 |

### 模式判定规则

模式表达用户希望如何生成，而不是上传历史或模型名称。Skill 理解语义，服务器检查实际引用与模型能力，两层共同约束。

| 场景 | 执行模式 | 真正传给供应商的图片 |
| --- | --- | --- |
| 只写一段提示词，要求生成新图 | `text_to_image` | 无 |
| 分析 A 提取风格词，要求仅按文字生成 | `text_to_image` | 无；A 是分析资料 |
| 分析 A 得到提示词，后上传 B 要用 B 生成 | `image_to_image` | B；除非用户另说，不能带 A |
| 使用人物 A、产品 B、风格 C 合成 | `image_to_image` | A、B、C，各有明确用途 |
| 先写提示词 P，后上传 X 说用刚才提示词 | `image_to_image` | X；提示词为 P 的准确版本 |
| 从历史生成结果 V2 继续修改 | `image_to_image` | V2 原图，不是最近一次结果或缩略图 |
| 只要求拆解、比较或修改提示词 | 不执行生成 | 不创建付费任务 |

显式文生图却携带生成引用、显式图生图却没有有效引用、指定不支持的模型或规格，都应返回可修正的校验错误。不能默默删图、换模式或降规格。未显式指定模型时，服务器按现有默认模型和已验证能力映射选择；保留用户明确选择的模型与预算边界。整个对话仍是聊天，不切换用户的整块输入界面。

## 三 当前软件能力和需要保持的边界

| 现有机制 | 代码证据 | 本方案使用方式 |
| --- | --- | --- |
| Skill 目录、懒加载、版本锁定、激活屏障 | `services/skills/runtime.py`、`runtime_source.py`、`context.py` | 直接复用，不新增技能运行平台 |
| 统一工具定义和权限策略 | `services/tools/definitions/*`、`registry.py`、`policy.py`、`runtime.py` | 只改声明和必要展示，所有执行仍经过现有 Policy |
| 异步图片执行与单图批次 | `services/handlers/image_handler.py` | 复用真实提交、适配器、回调与每图结算；聊天固定一张 |
| 公共参数整理与价格计算 | `handlers/image_request_settings.py`、`config/kie_models.py` | 复用并补显式模式校验；不新增第二套价格表 |
| 持久化任务、回调加轮询 | `task_completion_service.py`、`background_task_worker.py` | 补故障窗口和收尾对账，不新建任务表或队列服务 |
| 副作用调用记录 | `tool_invocation_store.py`、`handlers/chat/tool_lifecycle.py` | 复用调用身份、结果回放及 fencing，不用图片 URL 当幂等键 |
| 当前附件清单、历史文件检索 | `handlers/resource_manifest.py`、`file_resources.py`、`tools/resource_access.py` | 复用当前清单、`file_search`、资源版本和权限；当前清单不冒充全会话资产库 |
| 固定上下文基线 | `handlers/chat_context/history_loader.py`、`turn_binding.py`、迁移 120 | 保持已开始聊天的快照不变；晚到结果以新 revision 发布 |
| 工作区原图、缩略图、资产来源 | `file_upload.py`、`assets/asset_registry.py` | 补来源与版本关联，保留 NAS、OSS 和既有原图规则 |
| 原生多图与电商两阶段流程 | `handlers/ecom_image_handler.py`、`image_handler.py` | 保留独立界面行为；不删除 `_batch_prompts` 等现有消费者 |
| 图片引用、图片网格、恢复、ZIP | 前端图片菜单、`AiImageGrid`、`taskRestoration`、工作区服务 | 复用 UI 和下载能力，不重建上传、图库、ZIP 系统 |

### 不能当作已完成能力的部分

1. `generate_image` 已注册但不是 core 工具；Skill 激活收窄授权集合，不会自动把它的 schema 加入展示集合。允许调用和模型看得到是两件事。
2. 最新主线的 `_generate_image` 仍同步等待结果；原任务的早期异步改法基于旧基座，不能整文件覆盖新主线。
3. `conversation_subtask_links` 只允许父子均为 chat Actor，不能直接注册 image 子任务，也不能让 image 子任务关闭父 Turn。
4. `get_conversation_context` 目前是内部工具、没有公共 schema，只返回近期文本；不是准确的历史提示词和版本读取接口。
5. 参数 `output_format=png` 不代表透明背景；`remove_background` 虽存在，但未接入当前主生成链，而且失败会返回原图。
6. 当前图片适配器没有 mask、原生参考权重、seed、透明背景参数合同；不能承诺传这些字段就有效。
7. 资产登记与工作区持久化有尽力而为的降级；不能假设每张图都有 asset_id 或永不过期的原图地址。全仓检索还确认，`register_task_media_best_effort` 与 `register_message_media_best_effort` 目前仅有定义/导出，未接到普通生成完成调用方；资产平台存在不等于该链路已经登记。
8. Skill 草稿图片试运行直接调用旧同步 `_generate_image`，且界面期待立即完成结果；必须一起迁移。

## 四 影响审查的新发现

以下是本方案必须消除的现有断点，不代表生产已经发生同类事故。

| 编号 | 已核实机制 | 新链路直接接入的负面影响 | 处理要求 |
| --- | --- | --- | --- |
| F01 | `finalize_batch` 直接 upsert 目标消息全部 content | 若沿用聊天消息 ID，图片回调覆盖正文和工具步骤；多个调用互相覆盖 | 每图独立结果消息，绝不把父消息作为输出目标 |
| F02 | 完成服务写 task 终态后再推送和 finalize；再到回调会跳过终态 | 中途异常留下 pending 消息、缺结果或未发表到历史 | 持久化结算与发布进度，终态仍允许重做未完成的收尾 |
| F03 | `recover_orphan_tasks` 扫描所有 pending/running；普通媒体无流式内容便失败并退费 | 部署重启会终止供应商侧仍运行的普通图片任务 | 流式孤儿恢复只处理 chat；媒体按外部受理和提交阶段恢复 |
| F04 | `_RepeatedToolCallGuard` 仅比较工具与参数，不看 call_id | 连续同提示词不同变体第三次被当死循环阻止 | 每项带计划内稳定 variant 身份；保留对真正重复调用的防护 |
| F05 | 通用取消直接标终态、关闭 WS、释放槽位；没有媒体供应商取消和完整结算 | 已受理图仍继续生成，回调被跳过，预扣与页面不一致 | 排队停止和已受理停止分开；已受理无法取消时继续结算并显示事实 |
| F06 | 原生重生前端取邻近 `userMessage.content` | 跨轮组合任务丢提示词或带错参考图 | 新图片任务重试从服务器快照取输入；成功重生成新版本 |
| F07 | `_lock_credits` 分别更新余额和插入事务 | 在事务记录写入前异常，可扣余额却没有可退款记录；该函数本身不按 task 去重 | 新链路预扣、事务记录、任务绑定需同一 DB 事务，复用账本而非复制金融系统 |
| F08 | KIE `create_task` 在网络超时或网络错误时自动最多重试三次 | 已受理但响应丢失时可能重复创建供应商任务；本地幂等无法撤回已发生的重复 | 新受控图片路径复用 `create_task_once`，保留 shadow 上传；不确定不盲目重发 |
| F09 | `TaskLimitService` 先读 SET 数量再写入，异常时降级放行 | 并发超限；图片若复用父槽位则错误释放，Redis 故障时缺少付费硬限制 | 图片独立槽位，原子检查并占用；服务端预算 DB 硬约束，不能只依赖 Redis |
| F10 | 现有 `message_start` 会绑定对话唯一 streaming 槽 | 用该事件创建图片占位会抢聊天流，影响输入框、停止和发送状态 | 媒体 pending 快照走独立分支，不 registerStreamingId |
| F11 | finalize 重新构造少量 generation_params；参数还有 JSONB 大小上限 | 丢来源、计划和版本信息；把全文塞进去可能截断或拒绝落库 | 全输入存 task.request_params，消息只保存短引用和必要渲染字段 |
| F12 | native Skill 会再调用模型整理 prompt | 聊天 Skill 已编排的 prompt 被二次改写、延迟和重复消耗 | 可信聊天提交入口跳过二次整理；原生显式 Skill 行为保持 |
| F13 | 默认history查询只取completed/interrupted；资产生成登记函数尚无调用方 | 图片失败后下一轮缺结果事实；版本继续编辑缺可靠来源关联 | 精确纳入已发布的图片失败事实；在完成链路调用现有登记函数并补lineage |

本轮用无真实 DB、无供应商调用的 Mock 探针复现了 F02、F03：普通已受理图片在重启恢复中被写 failed 并调用退款；模拟 finalize 异常前，图片 task 已写 completed。F01、F05、F07、F08 等由完整调用链与代码分支确认。不存在供应商幂等合同的证据时，不承诺外部请求的严格 exactly once；采用本地单次提交、受理不确定停住与对账来避免盲目重复。

## 五 完整目标流程

```text
用户任意顺序提供文字和图片
  → 现有聊天模型选择并 activate_skill
  → Skill 指导取历史原文、选参考、拆成单图任务
  → 每张各调用一次 generate_image
  → 现有 Policy 与素材权限校验
  → 服务端归一化模式、规格、价格和输入快照
  → 原子接受独立任务与结果占位，返回任务身份
  → 现有后台 Worker 按槽位领取并提交，记录供应商身份
  → 回调或轮询持久化结果并完成独立结算
  → 发布独立图片消息，幂等推进新的会话 revision
  → 前端更新该卡片；父聊天可已完成或仍在回复
```

### 为什么保留现有 Worker 的排队阶段

一次工具调用创建一个执行单元，并不意味着一定要在工具调用栈里完成供应商的创建请求。独立图片任务可能超过并发槽位，也可能在提交时断线或服务重启。使用现有 `tasks` 加 `BackgroundTaskWorker` 的短领取分支，可以让未提交项恢复、分批执行、停止和预算控制都基于持久化事实。

不新增 Celery、Redis 队列、独立调度平台或第二张图片任务表。这里新增的是现有图片任务的受控提交阶段；每个 Worker 领取必须有 DB lease/CAS，不能只有进程内 `create_task`。原生图片入口仍使用现有 `ImageHandler.start`，但实际单图提交复用相同低层方法，不复制适配器调用或计费规则。

现有结果轮询有较长间隔和抖动，不能把“提交新任务”也丢进同一个供应商查询抖动队列。Worker接受新任务的短领取检查应与结果轮询周期分开，可增加轻量进程内通知加速，但数据库仍是恢复事实；不把chat专用Actor唤醒协议直接当图片队列。领取检查有界、不启动每图永久协程，不把全部供应商结果轮询提速；验收分别测占位出现延迟、排队等待和真正提交延迟。

工具返回的 `submitted` 表示本软件已持久化接受该任务，不表示供应商已受理或图片已完成；同时返回 `submission_state=queued|accepted|uncertain`。界面文案相应显示“排队中／生成中／提交待核实”。这一区分必须写入工具说明和模型结果，避免模型提前声称已生成。

## 六 Skill 与工具合同

### Skill 的职责

通过已有审核发布流程发布一个面向 `smart` 聊天、可由模型选择的生图编排 Skill。初始发布沿用个人会话 `conversation_scopes=user` 的既有可选Skill边界，不因本任务自动向channel开放个人历史或Skill；频道等上下文保持既有授权/拒绝规则。正文必须包含以下方法，不把方法写成固定顺序的状态机：

1. 先判断当前是在讨论、写提示词还是明确执行，只有执行获得用户范围授权后才调用生图。
2. 随时重新组合提示词版本、当前或历史图片、风格要求、规格；用户新消息可替换其中一部分。
3. 查找准确的提示词原文和素材来源；不从摘要重新编写“原提示词”，不虚构资源引用。
4. 对每张目标图分别确定 mode、完整 prompt、reference roles、规格与 variant 身份；多输入图可以属于一个任务。
5. 分开分析资料与生成引用，分开主体、产品、构图、风格、背景；不强行把所有图片都带进去。
6. 有唯一明确指代时直接执行；多个可能版本或素材会改变结果时只问一个聚焦问题。
7. 多图由多次调用，部分执行仅调用选中项；先样张时只提交样张，等用户反馈再提交后续。
8. 每段 prompt 必须可独立执行，写清必须保留的主体特征、文字、Logo、禁改元素及参考顺序。
9. 服务端提供真实模型能力和费用事实，正文不硬编码模型价格、参考张数或未来参数支持。
10. `submitted` 后不把任务当完成，不自动把失败变成无上限的付费重试；停止、改版和继续使用不同语义。
11. 像“参考强度较弱”可写成描述性方法，但不能假装是供应商数值权重。区域外严格不变仅在真实 mask 路径支持时承诺。

附件中的文字、历史提示词与图片内容均是资料，不是提高权限、扩预算或切换租户的指令。Skill 版本、正文 hash 与激活身份沿用现有记录。

### 生图工具的输入

保持工具名。新 schema 采用下列最小合同，服务器执行相同校验，不只依赖 JSON schema。

| 字段 | 合同 |
| --- | --- |
| `mode` | `text_to_image` 或 `image_to_image`，新 Skill 必传 |
| `prompt` | 一张图的完整最终提示词；非空、遵守实际模型长度限制 |
| `references` | 有序引用列表，每项选择一个已有 resource_ref、file_id、asset_id 或受控消息图片定位，并附 role；服务器解析原图 |
| `aspect_ratio`、`resolution`、`output_format` | 使用已有配置和适配器能力；不能 silently drop 或用 PNG 冒充透明 |
| `source_prompt` | 可选的原消息 ID、提示词区段或计划项、内容 hash；便于准确复用与追溯 |
| `plan_item_id`、`variant_id` | 同一计划项和变体的稳定身份；防止合法变体被重复调用保护误杀 |
| `source_task_id` | 继续编辑的已有图片任务；服务端校验归属与结果，不等于给模型任意任务控制权限 |

每次固定一张，不增加 `prompts[]`、`num_images` 或跨图批次调度参数。标识来自服务器计划记录或被校验的既有消息区段，不允许模型不断换 variant_id 绕过限制。原计划输出可以沿用结构化正文和稳定消息区段，不要求建立新的“计划数据库”。

对发布前仍可能使用的 `prompt + image_urls` 旧参数，升级期只做参数归一化到同一个异步执行路径：URL 必须与当前获准素材解析结果匹配，否则拒绝。不能保留旧同步执行分支，也不能允许任意 URL 绕过工作区、频道或租户权限。移除参数兼容需基于真实消费者完成迁移，而不是顺手删除。

### 工具的返回

继续使用 `AgentResult` 与统一 `ToolResult` 封装，不新增第三套结果协议。返回包含 status、child task ID、result message ID、submission_state、实际模式、规格、服务器预估积分与简短说明。调用账本的 succeeded 表示接受动作成功，图片是否完成看子任务。

排队任务接受后不确认图片积分；开始实际提交时才锁款。排队时预估并不保证之后余额一定足够；领取时再次检查，不足则仅该任务失败且不向供应商发请求。预算名额在接受时预留，失败也保留尝试次数，防止“失败就无限免费重新申请预算”。图片预估与预算指图片执行费用，聊天模型和可选质量检查的费用另按现有规则展示，不能把图片预估冒充整个会话总价。

## 七 数据安全与任务生命周期

### 使用已有数据结构

不新增图片任务表、图片资源编号系统、通用 Agent 或通用子任务平台。任务输入快照放在现有 `tasks.request_params` 中的版本化命名空间，例如 `_media_request_v1`；消息的 `generation_params` 只放短 task/plan/lineage 引用、实际模式和渲染规格。

快照包括：最终 prompt 及 hash、提示词来源、模式、模型、规格、排序后的参考图身份与版本、原图存储定位、reference roles、父任务与父工具调用身份、Skill 版本、计划项与变体、来源图片任务、预算与预估费用事实。URL 只作为当次执行地址，不作为唯一资产身份；重试时重新签署可访问地址并校验原素材版本。

父关联取自服务器上下文：actor、workspace owner、org、conversation、parent task/message、turn、当前 dispatch call_id 和执行 fencing。复用 `current_dispatch_call_id()`，不在模型输入里新增可信 `org_id` 或父任务字段。保留企业、散客及频道的实际资源边界；保存结果与资产登记也必须使用可信storage scope/owner，不能在完成阶段又把workspace owner默认为扣费actor。

图片任务不复制父 `_task_slot_id`、client_task_id、assistant_message_id，也不冒充 Actor。父关联存来源命名空间；不调用 `bind_generation_turn` 重绑同一用户输入，不调用 `close_generation_turn` 关闭已经结束的父 Turn。

### 原子接受、领取和结算

需要小范围新增 DB 事务/RPC与必要索引，而不是只加几个 Python 字段。推荐职责如下，具体函数名可沿用仓库命名习惯：

| 事务职责 | 必须原子保持的约束 |
| --- | --- |
| 接受图片请求 | 检查可信执行身份、计划选择、停止状态和额度；校验 canonical 参数 hash；按稳定调用身份创建一条子任务和独立 pending 消息；同身份同输入返回原任务，异输入冲突 |
| 领取提交 | 多 Worker 只有一个获得提交 lease；确认未停止且能力仍有效；预扣余额、创建 pending credit transaction、绑定到任务必须同一事务；失败不出现无账可退的扣款 |
| 完成结算和发布 | 已有账本 confirm/refund 语义仍有效；保存结果、幂等更新目标消息或 trial 结果、记录发布阶段；晚到结果分配新 revision，不改旧输入基线 |

能用同一数据库事务与现有退款 RPC时直接复用，不另建金融账本。现有CreditService的lock也采用先改余额、后插事务的多语句流程，不能仅换用它便宣称已经原子。RPC须使用当前 DB 的权限模型，限制调用角色并校验真实 scope、owner、租户成员身份和 fencing；不得为 Worker方便而给客户端或普通 DB角色写全租户任务的权限。异步 Skill 试运行的完成写入还必须满足现有 `skill_draft_trial_runs` 的 actor/admin RLS，需专用受控完成函数，不扩大表级授权。

新增事务采用一致锁顺序，并以真实数据库并发测试验证与聊天关闭、取消、退款、Actor领取没有死锁。父行预算更新只能合并自己的命名空间，不能整块覆盖 Actor 的 request_params/checkpoint。持久化预算计数和任务登记共同提交，Redis故障不能让付费硬限制失效。

### 提交阶段

保持现有 task.status 枚举，在任务自己的提交 metadata 中表示细阶段：

| 阶段 | 恢复与计费 |
| --- | --- |
| `queued` | 未发供应商请求，未锁图片款；可停止。排队截止时间与供应商生成超时分开 |
| `submitting` | 已领取且锁款；持久化 lease、尝试号和参数快照。重启或 lease 过期不能直接重发可能已经发出的请求 |
| `accepted` | 有外部 task_id，回调和轮询继续；父聊天退出或服务重启不取消图片 |
| `uncertain` | 请求可能已受理但确认丢失，或外部身份绑定失败；不自动退款后立即重发，不发新的付费任务 |
| `settling` | 供应商终态已知，重做尚未成功的持久化、结算和发布，不再次生图 |
| `published` | 结果或明确失败已写入独立目标并可被恢复查询；后续重复事件只回放 |

未知受理是有界待核实状态，不永久占着前端转圈。记录截止时间、可安全查询的外部身份、可操作错误与人工核对入口；到期后的退款或损失处理必须符合已批准的产品计费政策。若供应商既无幂等创建键又无法找回任务身份，就不能技术性保证“找回那张已经受理但丢 ID 的图”。本方案能保证不盲目再收费再创建；该能力限制必须在界面说明。

对本轮新增聊天图片路径，不默认沿用 `_is_smart_mode` 的任意备用模型重试。允许的fallback必须事先校验相同mode、参考图能力、完整规格和费用不超授权范围；否则返回需要选择，不静默换模型。现有KIE图片取图旁路的原请求快照重放仍复用，其作用只是修正可访问地址，不能重新编排prompt或更换素材。回调早于外部ID绑定时，以之后的已有轮询补齐；ID绑定失败归入uncertain，不误判为明确未受理。

### 回调和消息收尾

保留完成服务现有 Redis 锁与 task.version CAS，另外补“结算与发布已完成”的持久化事实。终态跳过不能跳过未完成的收尾。后台只扫描有未完成收尾标记的任务，分页、有索引、有限重试与告警，不全表无限重传。

每次完成只写目标 child message，保留它的来源和 lineage 字段；native 批次消息仍使用旧聚合展示规则。新聊天图片一个 batch_size=1，所以不会改变 native batch_size=2..4、电商内部最多 8 项的行为。

原图下载、OSS或 NAS 持久化失败不能伪装成供应商生成失败然后再生成一张。复用结果保存重试并展示“结果保存中/保存失败”；临时供应商 URL 降级必须注明，并不能作为长期版本资产的唯一凭据。不得将 prompt、签名 URL、Token 或参考图完整请求写到新增日志里。

### 会话 revision 和历史

新增的媒体结果发布事务只把新图片输出归入 conversation 并分配新的 revision，校验目标消息和 origin，不修改已关闭用户消息，不改变已启动任务的 base_context_revision。重复发布返回原 revision。

该更新也覆盖失败结果的交付事实，便于下一轮知道哪张成功/失败；不把 pending 图当完成图注入视觉模型。当前history_loader只查询completed/interrupted，因此要有界增加“已发布的聊天图片failed/cancelled结果”读取与投影，不能直接把所有失败chat和未闭合工具协议都放进历史。更新预览要按消息时间或现有最新消息条件，不能晚到旧图片把用户随后聊天的侧栏预览覆盖掉。版本历史可以按提交顺序展示、按完成顺序入 revision，不能混淆两种顺序。

精确历史读取只能取当前已授权会话、已允许基线及当前轮已知输入，返回消息 ID、区段、原文、hash、图片定位及受控任务摘要。不能通过公共历史工具绕过固定基线读取未来其他并发轮次，也不能读取其他用户、其他会话、ERP专家/频道不获准的个人上下文。当前 Skill 创建中的 source-message 校验可以复用规则，不能直接当已存在完整历史工具。

## 八 逐位置修改清单与负面影响

下表是最新基准的实际文件和符号。一个位置只做所列职责，不要求重写整个文件。未列为“修改”的共享实现以复用与回归为主。

| 编号 | 必须动的位置 | 要改什么 | 可能负面影响及防护 | 对应验证 |
| --- | --- | --- | --- | --- |
| M01 | `backend/services/tools/definitions/media.py`，`_schema_generate_image`、`build_specs` | 声明显式模式、资源引用、单图、变体与 async 返回；消除“上传必带图”和“电商只能另一工具”的聊天冲突 | 旧工具 JSON、工具目录快照漂移；升级期归一旧参数但只有一个执行出口 | T01、T02、T14 |
| M02 | `backend/services/handlers/chat/execution_engine.py`，`_apply_skill_context` | 把已激活 Skill 所需且实际获准的工具加入下一轮展示 | Skill误增权限、scheduled动态扩工具；展示仍取 Registry/Policy交集、仅interactive | T01、T15 |
| M03 | `backend/services/handlers/chat/tool_loop.py`，`prepare_tool_turn`；`tools/legacy.py`，`LegacyAdvertisement.names` | 使Skill工具展示跨轮、恢复后稳定；接同一个展示来源 | 非Skill聊天、MCP、ERP工具丢失或重复；保留core/discovery语义，不全局开放全部schema | T01、T14、T15 |
| M04 | `backend/services/handlers/chat_tool_mixin.py`；`backend/services/agent/tool_executor.py` | 透传可信图片提交上下文、停止与预算事实 | 复用executor导致跨轮串上下文、org遗漏、混淆actor和workspace owner；沿现有scope刷新 | T03、T08、T15 |
| M05 | `backend/services/media_tool_executor.py`，`_generate_image` | 删除同步等待与独立适配器结算；调用现有ImageHandler的受控接受入口 | Skill试用调用签名断裂、视频被误删；M13完成后删除旧图片助手，视频逻辑不动 | T04、T13、T14 |
| M06 | `backend/services/tools/runtime.py`，资源准备/dispatch之前的校验 | 对生图资源进行可信解析和版本复核，使用既有FileTargetResolver/Boundary | 任意URL泄露私有素材、路径越界、权限变更后仍执行；准备与最终提交均校验 | T03、T15 |
| M07 | `backend/services/handlers/image_request_settings.py` | 集中验证mode、模型配对、参数、价格；严格新合同保留native旧归一化 | native分辨率被改变、规格静默丢失、价格不同源；区分新显式合同与原生既有规则 | T02、T09、T14 |
| M08 | `backend/services/handlers/image_handler.py`，受控接受/提交入口、`_create_single_task`、`_save_task` | 共用一张图低层提交；新任务排队和快照；可信chat prompt不再次prepare；支持已登记local task | 重复task、重扣费、双规划、native批次错误、备用模型擅自更改模式或预算；低层复用且严格校验fallback | T04、T05、T08、T09、T14 |
| M09 | `backend/services/background_task_worker.py`，`poll_pending_tasks`、`cleanup_stale_tasks`、`_handle_timeout` | 领取新排队任务、补未完成收尾、分别处理queued/submitting/accepted超时 | 多Worker双提交、队列饥饿、清理任务互抢；DB lease/fence、分页、独立deadline；旧image/video轮询保持 | T05、T06、T10、T14 |
| M10 | `backend/services/task_recovery.py`，`recover_orphan_tasks` | 流式孤儿恢复仅处理chat；媒体不因无流式文本而失败 | chat中断内容恢复丢失；旧流式逻辑与Actor跳过保持，媒体留到统一恢复 | T06、T14 |
| M11 | `backend/services/task_completion_service.py`，`_process_result_locked`、`_handle_success`、`_handle_failure`、`_build_content_parts` | 终态未收尾重做、metadata/lineage保存、trial受控完成出口 | 重复结算、视频回归、存储失败触发再生图；只重做确定结果的后处理 | T05、T06、T07、T13、T14 |
| M12 | `backend/services/batch_completion_service.py`、`backend/services/batch_message_finalizer.py` | 结算不吞失败后宣称完成；保留来源字段；独立结果发布及幂等恢复 | native批次聚合/单图替换被破坏；新任务destination明确，原生operation分支保持 | T05、T07、T11、T14 |
| M13 | `backend/services/skills/trials.py`；`backend/api/routes/skill_creation.py` | 图片试运行迁移同一受控图片执行；POST返回running身份，GET恢复；文字试运行不变 | 新响应被当成功、候选更新覆盖旧结果、RLS导致Worker不能完成；保留hash/幂等，用专用完成函数 | T13、T15 |
| M14 | `backend/services/agent/conversation_tool_mixin.py`；`backend/services/tools/definitions/general.py` | 扩展既有历史读取为受控公共schema，支持有界原文和来源定位 | 泄露个人历史、扩大专家权限、无界上下文；默认旧limit语义可兼容，新增读取限定基线和scope | T03、T12、T15 |
| M15 | `backend/services/handlers/chat/execution_engine.py`，`_RepeatedToolCallGuard` | 用受校验variant区分合法变体，不解除全局重复保护 | 随机variant绕过guard导致付费循环；服务端计划/预算约束优先 | T08、T09 |
| M16 | `backend/api/routes/task.py`，cancel/fail/pending/result查询 | 媒体走自己的停止与结算；提供只读快照、身份与排队阶段；trial不混入聊天恢复 | 任意ID越权、取消图变取消chat、终态旧接口留锁款；服务器鉴权并保留chat控制分支 | T07、T10、T11、T13、T15 |
| M17 | `backend/api/routes/message_generation_helpers.py`；相关request preparation | 新图片retry/regenerate仅从服务端快照恢复，忽略客户端伪造内部标记 | 原生消息发送与重试回归、错误重绑Turn；按可信任务来源选择，旧native合同保留 | T11、T14、T15 |
| M18 | `backend/services/task_limit_service.py`，图片领取的check-and-acquire | 原子获取子任务独立槽位；服务失效时不突破DB图片预算 | 所有入口限流语义突然改变；优先提供图片严格入口，旧chat/video降级不顺带重写 | T09、T10、T14 |
| M19 | `backend/services/adapters/kie/image_adapter.py`；必要时`kie/client.py`单次提交出口 | 新可信路径单次创建、区分明确拒绝与受理不确定，沿用shadow行为 | 原生图片/视频重试变化、海外旁路丢失；新模式受控，默认旧消费者不变 | T05、T06、T14 |
| M20 | `backend/migrations/<实施时新编号>_chat_image_lifecycle.sql`及rollback | 增量接受、领取/预扣、结果发布、预算/停止和trial完成事务；必要的定位/收尾索引 | 锁、RLS、迁移耗时、回滚丢进行中任务；必须先reader后writer，可保留schema回滚应用，不删数据 | T05、T07、T08、T09、T13、T15、T16 |
| M21 | `backend/schemas/websocket_builders.py`；`backend/schemas/websocket.py`；前端WS事件类型 | 复用消息事件增加可信媒体pending快照语义，不发聊天stream start | 多版本前后端丢事件、占位重复；加性合同与快照查询兜底 | T04、T06、T07、T14 |
| M22 | `frontend/src/contexts/wsMessageHandlers.ts`、`wsTaskMessageHandlers.ts`、必要的`WebSocketContext.tsx` | 媒体pending按task/message入store并订阅；完成只清理对应任务 | 抢聊天streaming、子图完成误关发送态、多标签重复；保持按message所有权检查，校正会话完成标记 | T04、T06、T07、T14 |
| M23 | `frontend/src/utils/taskRestoration.ts`；必要的task store类型 | 恢复queued、accepted、uncertain和来源，独立映射多个图片 | 刷新重复占位、误按排队时间认为生成超时、trial卡混入 | T06、T10、T13、T14 |
| M24 | `frontend/src/hooks/useRegenerateHandlers.ts` | 新聊天图片按task快照重试；成功再生保留旧版，新message/task | 邻近userMessage错误；native单图替换误升级；按origin分支不全局改语义 | T11、T14 |
| M25 | `frontend/src/components/chat/media/ImageContextMenu.tsx`、附件types、`useImageUpload.ts`、`useChatAttachments.ts`、`messageSender.ts` | 引用携带source message/content index/task/asset信息并保持原图URL | 引用顺序错、字段途中丢失、前端来源被当授权；传递用于定位，服务端重核归属 | T03、T11、T15 |
| M26 | `backend/schemas/media_parts.py`；`frontend/src/types/message.ts`、`schemas/messageProtocol.ts` | 加性来源/任务定位类型，严格解析实际新增字段 | 后端字段前端丢弃、旧消息不兼容；新字段可选，旧内容照常解析 | T03、T04、T14 |
| M27 | `frontend/src/components/chat/media/AiImageGrid.tsx`及图片消息渲染入口 | 展示阶段和真实任务输入入口，串联版本/选择；复用现有失败、下载与图片网格 | 图片成功却被渲染pending、长prompt撑布局、多图单槽混淆；全文按需读快照 | T04、T07、T11、T14 |
| M28 | `frontend/src/services/skillCreation.ts`、`components/chat/message/SkillDraftCard.tsx` | trial接受与完成分离，按已有GET trials恢复；仅有运行项时有界查询 | 页面一直忙、重复按钮重扣、旧候选反馈串到新版；trial_id/hash隔离，文字trial不变 | T13、T15 |
| M29 | `backend/core/config.py`及现有工具上下文事实注入 | 小范围写入新链路开关、图片张数/预算上限和能力摘要；不写价格副本 | 开关关闭把进行中任务遗弃；开关只关新接受，读/完成/对账始终兼容 | T09、T16 |
| M30 | Skill正文与发布材料、相关TECH/API/前端状态文档 | 经既有审核发布并分配正确作用域；文档同步新参数、返回和计费语义 | 文件存在但Runtime没发布/没分配，旧文案诱导错误；以真实published目录和hash验收 | T01、T16 |
| M31 | `backend/services/handlers/chat_context/history_loader.py`、`history_outcomes.py` | 只纳入已发布图片成功/失败/取消交付事实，投影准确task/版本/素材定位 | 全部失败chat被注入、工具协议失配、晚到图片改旧基线；保留原快照和token预算，只加指定来源的结果 | T03、T07、T12、T14、T15 |
| M32 | `backend/services/assets/asset_registry.py`，`register_task_media_best_effort`、`_task_asset_metadata`；M11中的调用点 | 接现有登记函数，补可信storage owner、task/message来源和lineage；把登记得到的asset身份回填结果 | 私有资产错归actor、登记失败变重复生成、来源被外部字段污染；登记按现有稳定ref_key幂等，失败保留task定位并有限补登 | T03、T07、T11、T15 |

M06的图片资源准备如需要拆出代码，最多增加一个媒体输入解析模块，复用现有解析器与资源边界；不创建新的Resource SDK。M20所列函数可以按同一事务的职责合并，不按表格机械创建一套服务框架。新的细阶段写入必须在所有读/完成分支准备好后才开启。

### 明确复用为主的位置

`tools/registry.py`、`policy.py`、`dispatcher.py`、`tool_invocation_store.py`、Skill目录/版本/资产存储、原图持久化和AssetRegistry、`turn_binding.py`与旧Turn RPC、`conversation_subtasks.py`、电商需求与计划接口、视频与音频、ERP/MCP、计划任务调度均不应整体改造。新路径对这些机制需要定向验证，但没有证据不修改。旧 `_generate_image` 的全仓调用已定位到工具handler和Skill trial；实施删除前仍需重新检索，防并行任务新增调用方。

`config/kie_models.py`与适配器的`kie/configs.py`已有配置投影可能不同：价格以`calculate_image_cost`为准，参数以实际适配器接受能力为准。能力摘要应由这些既有来源生成并测试一致性，不新增一张静态价格/能力表，不趁本任务重构全部模型目录。

## 九 已纳入的补充能力和新增边界

| 补充项 | 第一阶段可以复用和接线的部分 | 完整能力增加的位置与风险 |
| --- | --- | --- |
| A 部分执行与先样张 | Skill只调用选中项；保存提示词来源与计划项 | 样张需要用户反馈时不能自动跑剩余项；剩余项不锁款，不新增计划调度平台 |
| B 展示真实输入 | 独立图片卡提供任务详情，读服务端快照 | M16/M27补prompt、参考缩略图/角色、实际规格；不能展示模型虚构的摘要当真实执行输入 |
| C 成本与预算 | 沿用服务器价格与账本，新增可信单轮上限 | M07/M20/M29持久化预留与原子限制；普通用户明确请求在预算内可执行，超出再询问，不为每张重复确认 |
| D 停止 | chat停止继续沿用Actor；图片有独立取消身份 | M16/M20区分queued与accepted；无法supplier cancel时明确“不能撤回已提交”，继续闭合账单 |
| E 版本和比较 | 已有生成资产与任务结果可继续引用 | M25/M27及asset source_refs补lineage；并列比较复用现有图片展示，成功重生新版本不覆盖旧图 |
| F 系列一致性 | Skill完整独立prompt、固定reference角色；复用电商方法与style directive知识 | 角色一致/Logo文字精确度不是确定保证；必要时显示限制、后续反馈修正，不悄悄无限重生 |
| G 参考用途与强度 | 多图、排序、role与描述性强弱 | 真正数值权重需适配器配置及供应商接口支持；当前schema不开放无效native weight字段 |
| H 规格原图与ZIP | 复用ratio/resolution/format验证、下载与工作区ZIP | 透明背景需实际alpha检查及能力路线；现有rembg不作为默认可用。依赖或额外后处理成本查清前不开启 |
| I 可复用配方 | 用已实现Skill创建/编辑保存方法、JSON/text模板与workspace资源引用 | 参考原图仍保留私有资产权限；不将图片二进制塞入Skill assets，不把共享Skill当共享私有图授权 |
| J 真正区域编辑 | 普通提示词改图可用，但不能当精准mask编辑 | 新区域选择UI/遮罩资产、media_parts与工具schema、适配器base/KIE输入模型及配置、提交/保存/重试mask快照；供应商支持与实际费用核实后单独实施，不在目前普通生图路径假装支持 |
| K 反馈与质量检查 | 复用已有反馈方法和Skill试用feedback | 生成任务rating、与prompt/ref/版本关联的统计需接现有日志/资产metadata与受控API；质量检查为有界可选模型任务，需单独预算和用户可见，不能把AgentResult.confidence当画质分数 |

G/H/J涉及当前接口未实现的实际图像能力。具体新UI组件与供应商字段不能在没有真实合同的情况下写成已验证代码位置；以上列出了必经边界和不破坏现有路径的条件。完整产品范围保留，不能把这些条件项藏进“接线后全部已有”。需要新供应商、rembg运行依赖或额外持续费用时另行取得对应授权。

## 十 旧链路迁移和兼容矩阵

| 消费者 | 完成后的行为 | 不允许的破坏 |
| --- | --- | --- |
| 聊天generate_image | 唯一受控异步接受路径 | 不保留同名同步等待fallback；父正文不被覆盖 |
| 原生文生图与图生图 | 保持模型/参数/显式Skill与现有批次；共享低层执行 | 不自动把native变成聊天编排，不丢用户选择的模型 |
| 原生电商图 | 保持计划与二阶段界面和平台规格 | 不因为聊天统一generate_image而删掉电商界面、ImageAgent、内部批次 |
| Skill草稿文字试用 | 原样按文字结果返回 | 不改变候选hash、feedback及发布审批流程 |
| Skill草稿图片试用 | 相同执行低层，结果写trial记录；界面异步恢复 | 不用虚构conversation="skill-trial"创建UUID聊天任务，不混入普通聊天history，不期待submitted内有图片URL |
| generate_video及native视频 | 原行为 | 删除旧图片helper时不能删视频helper、改变供应商video create重试 |
| ERP、MCP、文件、计划任务 | 原权限/工具展示和执行模式 | Skill激活不扩大权限；scheduled/preflight不动态激活，不顺带开放普通生图 |
| 企业、散客、频道 | 按现有身份与上下文资源边界 | `_org_id`漏传、workspace owner=actor假设、公开历史工具绕过channel范围 |
| 部署时已有图片任务 | 旧params依然能完成、退款与恢复 | 不强制补填所有旧任务为新schema，不迁移结果覆盖原图 |

Skill图片trial无需新任务表。真实来源会话从现有change_set audit_subject读取并复核；任务destination为trial，消息目标可以为空，原tasks schema允许空关联。完成出口写trial而非普通batch消息，既有GET trials提供恢复。trial表status继续running/completed/failed；queued阶段作为result/task元数据，不为此修改trial状态枚举。

## 十一 实施批次和发布回滚

### 批次一 核心安全闭环

先把最新main合入本任务分支并处理差异，保留原有两处改动；不能把旧config/common_tools或整文件版本复制回来。核验无项目禁止的旧Runtime平台路径。完成M01至M32中核心合同、独立任务、资源边界、事务、Worker提交/恢复、收尾、trial迁移与读端支持，包含M31历史投影和M32资产来源接线；发布真实Skill材料。

只有文生图/图生图、三种跨轮组合、独立占位、同prompt变体、刷新/重启/回调失败、幂等与账单、trial兼容全部通过，才启用新接受开关并移除旧同步图片实现。没有“先删旧链路，出问题再加回同步分支”的中间发布。

### 批次二 用户控制与已有能力补齐

完善真实输入详情、成本预览、部分执行、排队停止、失败重试/成功新版本、版本比较、配方及反馈；复用现有下载和ZIP。核心快照与来源字段必须在批次一建立，不能到此才补救历史数据。

### 批次三 当前缺少的图像能力

按实际供应商能力单独加入原生参考权重、透明背景、区域编辑和可选质量检查。每项有独立能力开关、参数拒绝行为、费用与数据合同；不改变不使用该能力的普通生成。新增外部依赖或供应商写入属于新的执行动作，设计纳入不代表本轮已授权安装或付费测试。

### 发布顺序

1. 增量DB迁移与只读兼容能力，先在隔离数据库验证真实事务、RLS、回滚和执行计划；编号以实施时最新迁移为准。
2. 后端 reader、完成/恢复分支和前端新旧事件双读；新任务接受仍关闭。
3. Skill trial异步客户端与服务器合同一并发布；保留旧结果读取，不保留旧供应商同步执行。
4. 发布并分配Skill，启用限定范围的新接受开关，按验收场景测试。
5. 核验进行中任务恢复、账单、日志脱敏与既有入口，再扩大范围。提交推送部署必须等待用户相应指令，遵循`deploy/release.sh`受控入口。

### 回滚

关闭新请求接受，不关闭旧任务读/完成/对账。已有accepted/settling任务必须继续完成；queued可受控停止；uncertain按核实规则处理。可回滚到包含新任务读/完成支持的前一个兼容版本，不能直接退回完全不识别新阶段的旧版本。

新增RPC/索引是加性迁移，优先保留schema回滚应用；涉及pending credit或未发布结果时禁止drop函数/索引和删除任务数据。完整数据库rollback只在确认无新任务进行中、无未结算账单并备份后执行。原生工具与聊天权限变化可独立关闭，不自动恢复已被明确淘汰的同步图片执行链。

## 十二 回归和故障验收

以下为发布门禁，不以“现有测试全绿”代替。测试应验证相应代码的真实行为，数据库部分必须有实际事务/RLS测试，供应商部分用受控故障注入和获授权的少量真机生成补证据。

| 编号 | 必须验证的场景 | 核心断言 |
| --- | --- | --- |
| T01 | 自动/手动Skill激活、恢复、多Skill收窄、未启用、撤权 | 所需schema可见且获准；不额外开放工具；同轮激活屏障仍有效 |
| T02 | 无图、有分析图、有生成refs、mode错配、模型错配、规格不支持 | 路由精准；不静默换mode/规格；多refs按顺序且数量遵守实际模型 |
| T03 | 当前/历史/生成图，A分析+B执行、多素材编号歧义、源版本变化 | 准确原图定位；不带A；歧义问清；资源变化不执行旧快照 |
| T04 | 接受后立即占位，两个独立图，父聊天继续流式 | 每张任务/消息/slot独立；无供应商完成前的假成功；不抢父streaming槽 |
| T05 | DB失败、预扣中断、提交前崩溃、发送后丢响应、绑定ID失败、重复回调 | 不无账扣款、不重发受理不确定、不生成重复图、账本和结果可收尾 |
| T06 | 排队/accepted/uncertain/settling时刷新、断线、多Worker、服务重启 | 恢复真实阶段；普通媒体不被流式孤儿恢复失败；终态未收尾会对账 |
| T07 | 两张乱序、部分失败、重复finalize、失败退款异常、NAS/OSS/消息/WS异常 | 单图隔离、账单只一次、保存失败不重生；前端最终与DB一致 |
| T08 | 同prompt合法变体、相同call重放、相同call异参数、随机variant | 合法变体不误杀；重放回原任务；异输入冲突；预算不被绕过 |
| T09 | 同轮/多Worker额度竞争、Redis故障、槽位满、排队后余额不足、价格变化 | 原子上限；不超张数/预算、不借父slot；旧预计价变化需尊重授权上限 |
| T10 | 停止未提交、停止已受理、停止与领取/完成并发、超时清理 | 停止线性化；未提交不请求供应商；accepted不能伪装已撤回；账单闭合 |
| T11 | 跨轮组合失败retry、成功regenerate、旧版本继续编辑、native单图替换 | 新任务用原输入；成功保留旧版；native原语义不变；不误绑Turn |
| T12 | 历史提示词原文、压缩后取回、revision边界、晚到结果、重复发布 | 原文/hash可校验；新revision不改旧基线；提示词缺失不猜；侧栏预览不倒退 |
| T13 | 图片trial异步、文字trial、刷新、重放、候选变化、反馈、发布与撤权 | 不混聊天、不双扣、不把running当completed；Worker写trial受控 |
| T14 | 原生图片1..4张、电商计划/内部批次、retry、video、chat、文件、ERP、MCP、scheduled | 原有参数、权限、展示、恢复和返回结果不被本变更破坏 |
| T15 | 企业/个人/channel、被移除成员、错误workspace owner、伪造org/路径/任务/来源/内部标记 | 所有越权在付费IO前拒绝；Worker不能靠普通模型输入提高权限 |
| T16 | 迁移/读写错峰、开关切换、进行中任务应用回滚、真实Skill发布 | 旧任务可完成，新任务不被遗弃，旧前端可回查；无禁止Runtime路径 |
| T17 | 权重、透明、mask、质量检查的新增能力 | 参数真的传给支持的adapter，mask尺寸/角色正确，输出alpha与区域效果可检查，费用有限 |

优先复用的测试文件：`test_skill_runtime*`、`test_tool_policy.py`、`test_tool_registry.py`、`test_chat_execution_engine.py`、`test_media_tool_executor.py`、`test_image_handler_batch.py`、`test_batch_completion_service.py`、`test_task_completion_routing.py`、`test_task_recovery.py`、`test_background_task_worker.py`、`test_task_limit_service.py`、`test_context_snapshot.py`、`test_resource_manifest.py`、`test_file_search_protocol_08.py`、资源scope连续性测试、工具结果持久化测试、`test_kie_image_fallback.py`、`test_skill_chat_creation.py`。新增真实DB测试覆盖M20，不用Mock证明原子性或RLS。

前端复用WS、taskRestoration、useRegenerateHandlers、AiImageGrid、ImageContextMenu、附件顺序和SkillDraftCard测试；补父chat与多个child并行场景和新协议fixture。类型检查、构建及受影响接口契约测试必须针对最终修改提交，不在设计阶段提前宣称通过。

## 十三 已取得的验证证据与未验证项

| 验证组 | 结果 | 能证明什么 |
| --- | --- | --- |
| 前轮Skill、图片batch、完成、generate_image、file_search、invocation及相关Actor/迁移测试 | 9文件153条通过 | 既有主体机制可复用；不是新方案已经实现 |
| 本轮工具Policy/Registry、聊天引擎、slot、Worker、完成路由、Skill草稿、KIE旁路 | 8文件819条通过 | 受影响核心机制的既有测试基线 |
| 本轮恢复、context snapshot、manifest、结果封装/消费/持久化 | 6文件236条最终通过 | 既有恢复与新旧结果合同基线；持久化组依赖git历史 |
| 本轮前端WS、恢复、重生、图片网格、引用、Skill试用 | 6文件134条通过 | 既有UI与状态合同基线，使用本机已有依赖 |
| Mock故障探针 | 普通image重启被failed并退款；finalize异常前task已completed | F02/F03确有代码路径，需针对性修复和回归 |
| 前轮工具展示与同prompt诊断 | generate_image allowed但未advertised；连续同参为continue/nudge/stop | F04和工具展示缺口不是Skill文案能够独自解决 |

本轮后端合计1055条不同测试最终通过，前端134条。最初结果持久化的15条失败是只读副本不带git元数据导致旧版本`git show`无法读取；提供真实仓库的只读GIT_DIR后77条持久化测试全部通过，没有改测试或业务代码。前轮153条基线仍可复用，但不重复计作本轮运行。

所有后端验证使用测试占位配置，Mock DB/provider；没有读取生产密钥、调用真实供应商或消耗用户积分。前端测试未安装新依赖，没有运行最终实现的构建。测试数不等于覆盖率，也不证明整个系统所有功能无回归。

未验证且在相应实施/发布前必须补齐：生产Skill开关与published分配、生产schema/RLS和迁移版本、真实供应商受理不确定/取消能力、mask/权重/透明支持、真实费用与规格一致性、应用部署重启中的真实端到端恢复。不能把这些写成“已调研确认可以直接上线”。

## 十四 交付判断

方案可以进入核心实施，但实施必须从最新基座开始，按M01至M32的职责保持共享机制边界，先建立输入与账单安全闭环，再开启新接受。原有同步图片函数在所有已知消费者迁移后删除；不保留第二条聊天同步执行链。

完整产品范围已纳入，核心接线与当前缺少的供应商图像能力清楚分开。只要T01至T16中与实际发布范围对应的门禁未满足，就不能宣称不破坏系统或部署供验收；T17是进阶能力各自的上线条件。当前交付的是已保存的、可追溯的实施方案，而非已完成的功能改造。
