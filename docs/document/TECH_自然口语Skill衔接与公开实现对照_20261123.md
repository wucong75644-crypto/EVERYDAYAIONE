# 自然口语 Skill 衔接：公开实现对照及优化方案

状态：已按真实响应证据实施局部修复并进行复测，尚未部署。以下保留公开实现对照与原方案；实施修订和验证见文末。不将此前专业验收话术的成功外推到普通聊天。

## 推荐结论

保留当前聊天、Skill、工具执行器、策划子Agent和图片生命周期。补齐三个局部边界：**被延迟展示的模型响应留证；已确定业务阶段的工具衔接；无工具调用时的结束判定。** 普通聊天仍可直接回答；手动选择Skill本身不等于授权生图。已确认进入服务器策划流程后，客户不应再通过“调用策划器、按原文保存”等话术帮助系统启动流程。

先取得真实响应证据，再决定修复解析还是调度。不要根据缺少调用的表象，直接认定唯一根因是模型不听话或提示词不够长。

## 本次已确认的事实与尚未确认的原因

真实输入为“参考这个产品，帮我想3个新款，把提示词写出来，我先看看。”，运行中追加图片及“这个也给你参考，背景用浅米灰色。”。

原测试曾将main_images视为正确，现已被原始响应及Skill适用范围证据推翻：当时路由为main_images、Skill已激活、plan_ecommerce_images工具及工作流说明已提供；追加回执enqueued、检查点delivered、重建后的请求含追加原话和图片。执行层最终没有获得有效的策划调用，计划记录0、图片供应商提交0。后台向页面发布停止提示并提交本轮，用户没有得到方案。

原诊断阶段尚未确认：供应商是否返回了普通文字、工具调用是否在解析中丢失、是否出现空输出或指令冲突。原始回复未被这次采集保留下来，因此不能把“解析器有宽容跳过JSON的代码”直接认定为本次根因；也不能证明所有自然口语都会失败。应使用该原始输入及追加时序重放，采集原始返回与规范化结果进行区分。

## 已核对的公开实现

以下是公开代码/官方机制，不冒充大厂线上未公开的内部全部实现。Grok Build固定到官方main提交 `2bdd1d6a6369de0e8c68132ea4539e9abd9e14a8`；OpenAI Agents SDK固定到 `6c4e4a29cd08eef454db6980d74da6afc8939d24`。Grok本文引用的5个源码文件逐一核对Git blob SHA，私有证据清单位于 `/private/tmp/grok-flow-audit-20261123/manifest.json`。

### 1. Skill加载与业务执行是不同职责

Grok的Skill实现把方法正文格式化后注入模型，用户显式引用也会展开正文。它并不在这个加载函数里替所有Skill直接执行固定业务。[Skill代码](https://github.com/xai-org/grok-build/blob/2bdd1d6a6369de0e8c68132ea4539e9abd9e14a8/crates/codegen/xai-grok-tools/src/implementations/skills/skill.rs#L30)、[显式Skill展开位置](https://github.com/xai-org/grok-build/blob/2bdd1d6a6369de0e8c68132ea4539e9abd9e14a8/crates/codegen/xai-grok-shell/src/session/acp_session_impl/turn.rs#L821)。

Anthropic公开区分按代码预定义步骤编排的workflow与由模型动态决定执行步骤的agent；这是架构模式，不是强制行业标准。我们已绑定的服务器三阶段策划属于前者的特征，普通聊天更接近后者，不能既按固定流程拒绝普通回复，又把启动固定流程全部交给一次自由工具选择。[官方文章](https://www.anthropic.com/research/building-effective-agents)

### 2. 工具可用，不等于模型会调用

OpenAI SDK明确提供auto、required和指定工具，并强调按当前请求开放合适工具；调用后默认重置tool_choice，避免反复强制执行。同一API机制不能当所有模型的统一保证。[SDK文档](https://openai.github.io/openai-agents-python/agents/#forcing-tool-use)、[固定版本代码](https://github.com/openai/openai-agents-python/blob/6c4e4a29cd08eef454db6980d74da6afc8939d24/src/agents/agent.py#L400)。xAI也提供自动与指定函数选择。[xAI工具调用](https://docs.x.ai/developers/tools/function-calling#tool-choice)

**与本项目直接相关的限制：** 百炼Chat文档明确Qwen的required不能当必定调用保证，思考模式不支持强制指定函数；Function Calling文档也要求按具体模型支持情况使用。因此不能只照抄一行tool_choice=required，也不能为了强制工具擅自关闭用户打开的思考。[百炼Chat参数](https://help.aliyun.com/zh/model-studio/qwen-api-via-openai-chat-completions)、[百炼函数调用](https://help.aliyun.com/zh/model-studio/qwen-function-calling)

### 3. 正常结束与异常结束分开处理

Grok在收到响应后分别读取工具调用、是否为空、停止原因和拒绝信息。无工具调用时，会处理截断、可选待办接续，并在结束前消费追加输入；普通文本仍可能合法结束。它并没有“所有回复都必须调用工具”的普遍规则。[主循环](https://github.com/xai-org/grok-build/blob/2bdd1d6a6369de0e8c68132ea4539e9abd9e14a8/crates/codegen/xai-grok-shell/src/session/acp_session_impl/turn.rs#L3470)、[结束前追加检查](https://github.com/xai-org/grok-build/blob/2bdd1d6a6369de0e8c68132ea4539e9abd9e14a8/crates/codegen/xai-grok-shell/src/session/acp_session_impl/turn.rs#L3623)。

Grok另有可配置Stop hook：未启用时直接允许结束，启用后也有续接上限。这说明接续有范围和预算，不意味着我们应新建一个全局停止审核系统。[Stop gate代码](https://github.com/xai-org/grok-build/blob/2bdd1d6a6369de0e8c68132ea4539e9abd9e14a8/crates/codegen/xai-grok-shell/src/session/acp_session_impl/stop_gate.rs#L234)

### 4. 输出证据独立于最终页面展示

Grok有与模型上下文分开的流式捕获，用于取消或异常终止、尚未形成正式assistant消息的场景，并有内存上限。OpenAI SDK也保留每次模型返回的ModelResponse，提供raw_responses及可恢复状态；这里不是声称其全部保存原始HTTP字节。[Grok捕获代码](https://github.com/xai-org/grok-build/blob/2bdd1d6a6369de0e8c68132ea4539e9abd9e14a8/crates/codegen/xai-grok-shell/src/session/streaming_capture.rs#L1)、[SDK结果说明](https://github.com/openai/openai-agents-python/blob/6c4e4a29cd08eef454db6980d74da6afc8939d24/docs/results.md#L203)

我们需要采用的是“展示策略不能让诊断证据消失”，不照搬它的思维链采集、8MB上限或完整遥测平台。

## 我们的对应代码与缺口

| 位置 | 当前事实 | 对应缺口 |
|---|---|---|
| [workflow.py](/Users/wucong/EVERYDAYAIONE-worktrees/20261121005427-skill-runtime-performance/backend/services/agent/image/ecommerce_planner/workflow.py:168) initialize/coordinate | 已区分主图、普通图片及既有ready计划，并绑定Skill/action | 应复用这个决定，不再增加并行意图路由；必须识别用户改口和新素材修订 |
| [workflow.py](/Users/wucong/EVERYDAYAIONE-worktrees/20261121005427-skill-runtime-performance/backend/services/agent/image/ecommerce_planner/workflow.py:390) needs_planner/context_message | 检查策划完成回执，给主模型发“调用策划器”的文字指令 | 确定的业务阶段主要靠提示语衔接，缺少按接口能力实际控制工具选择的策略 |
| [execution_engine.py](/Users/wucong/EVERYDAYAIONE-worktrees/20261121005427-skill-runtime-performance/backend/services/handlers/chat/execution_engine.py:657) | 每轮计算defer_ecom_narrative | 已有天然作用域，可限定控制，没必要影响全部聊天 |
| [execution_engine.py](/Users/wucong/EVERYDAYAIONE-worktrees/20261121005427-skill-runtime-performance/backend/services/handlers/chat/execution_engine.py:1413) 流式返回 | 在服务器策划前延迟正文展示；局部变量仍接收文字/工具增量 | 页面不展示可以合理，但不能因此丢失原始响应及解析依据 |
| [execution_engine.py](/Users/wucong/EVERYDAYAIONE-worktrees/20261121005427-skill-runtime-performance/backend/services/handlers/chat/execution_engine.py:1201) | 延迟正文且无有效工具调用时，直接发固定停止提示并return | 未区分真实澄清、普通草稿、拒绝、截断、解析失败、意外无调用；容易吞掉有用信息且无法继续定位 |
| [model_request.py](/Users/wucong/EVERYDAYAIONE-worktrees/20261121005427-skill-runtime-performance/backend/services/agent/observability/model_request.py:24) | 记录协议、参数、工具schema、预算和attempt等请求诊断 | 缺与响应正文/工具增量/解析结果相对应的证据，本次无法查透无调用原因 |
| [DashScope adapter](/Users/wucong/EVERYDAYAIONE-worktrees/20261121005427-skill-runtime-performance/backend/services/adapters/dashscope/chat_adapter.py:121) 两个协议入口 | tools已发送，当前没有序列化tool_choice | 上层即使增加意图字段也不能假定控制已经到达供应商；必须抓最终HTTP验证 |
| [工具定义](/Users/wucong/EVERYDAYAIONE-worktrees/20261121005427-skill-runtime-performance/backend/services/tools/definitions/ecommerce_planner.py:1)及动态工作流说明 | 工具固定文案要求先激活；动态文案说已经激活；固定文案混写ready后出图与只策划例外 | 入口/当前动作说明存在多处维护；收口有必要，但这些并未被证明是本次唯一原因 |
| [ToolExecutor](/Users/wucong/EVERYDAYAIONE-worktrees/20261121005427-skill-runtime-performance/backend/services/tools/execution.py:43)及[策划器](/Users/wucong/EVERYDAYAIONE-worktrees/20261121005427-skill-runtime-performance/backend/services/agent/image/ecommerce_planner/service.py:365) | 已有Policy、参数检查、派发占用、租约、版本、账本和恢复 | 必须保留并复用；不能直接调用service.run绕过这些边界 |

## 待实施方案：按证据分两批

### 第一批：补齐响应证据并确定分支

复用现有Gateway attempt、私有检查点和测试捕获入口，在模型返回后、任何展示过滤或固定提示覆盖之前保留：

- request/attempt/task/turn/model-round及输入修订的关联；
- 正文、工具名称/参数/调用ID、finish_reason、是否完整结束、取消原因；
- 规范化前后工具数量、解析异常及被忽略事件计数。

常规共享日志只记录安全摘要与哈希，完整响应只进同任务权限范围的私有证据，受现有预算约束；不记录认证头，也不把诊断内容、思维链或内部工具参数再传给图片模型。真实测试增加HTTP层原始SSE采集，才能区分供应商没发调用和适配器解析丢失。生产不新增逐token数据库写入。

用本次真实输入重放“无追加”和“运行中追加”两个文本场景。若原始返回已有工具调用，则先修解析；若是拒绝/截断，走其专属错误路径；若完整返回只是普通文字，才进入工具衔接优化。不要不看返回内容就把所有无调用当同一种错误。

### 第二批：在已有阶段内收口执行衔接

1. **只在既定策划动作内控制。** 复用server_selected、现有ActionContext及needs_planner，不新增Skill种类或流程引擎。仅选择Skill但用户问普通问题、取消、转聊天，不能因此强制策划；“先看看/写提示词”不能被当成生图授权。
2. **支持的接口使用阶段性指定工具。** 在用户确实要求策划、必要素材定位已可用且没有待消费追加时，关闭思考的已核验模型可在原有主模型轮次使用指定plan_ecommerce_images，让模型准备参数，而不是再次自由选择是否启动。只限制该阶段，不对所有工具使用全局required，不强制调用generate_image。模型、接口、思考模式的支持情况明确声明并由真实HTTP验收；不支持时明确回到auto的兼容路径，不擅自切换思考开关或假称已强制生效。
3. **还缺参数时保留准备和澄清。** 素材不在当前已授权目录时沿现有读取工具定位；真正含糊的用户要求允许询问。后台不猜商品身份、素材用途或张数。用户未给尺寸仍使用现有默认/参考图匹配，不能要求用户提供技术字段。
4. **收到策划回执立即解除阶段控制。** ready、needs_input、error各走现有回执逻辑；ready且用户只看方案时由后台自动保存和发布全文。下轮按编号直接读plan_source，不再次策划或润色，不把“ready就全部出图”写成无条件指令。
5. **意外无调用只允许有界接续。** 已确认还欠策划且不是取消、澄清、拒绝或截断时，可以在现有模型循环中做一次目标明确的接续，沿用当前输入修订和原预算。接续标记必须进入原检查点，恢复不能再拿一份额度。不是默认加一轮，正常调用不增加回合；不与现有空回复、参数纠错叠加成无限重试。
6. **仍未完成就如实交付未完成原因。** 保留原始响应和当前可恢复定位；必要澄清应展示给用户，系统异常不伪装成缺资料。不能继续只给“请重试主图请求”，也不能把模型自行写的草稿冒充已由服务器生成并保存的ready方案。沿现有不完整/失败表达和恢复机制处理，不为此改整套conversation状态机或新增数据库枚举。
7. **提示词职责单一。** 固定工具描述只讲能力、参数和回执；动态workflow context说明本轮是否已激活、是只写还是执行、是否已有计划。保留Skill专业方法，清理重复/矛盾的入口要求。用户说自然语言即可；新增内部调度指令仍只给主模型，不拼入生图prompt。

不建议本轮新建通用“后台替模型直接跑所有Skill”的框架。如果以后要让参数已齐备的固定流程完全由代码直接启动，仍须经过原ToolExecutor、相同Policy和账本，不得伪造模型调用或直接绕过service入口；这属于后续单独决定，不作为本次最小修复的前提。

## 完整链路与必须保持的机制

```text
用户原话 + 当前图片/页面引用
  → 现有意图与Skill协调（普通聊天 / 策划 / 执行已有稿）
  → 读取最新追加，核对当前输入修订
  → 模型准备参数（必要时仅在本策划阶段指定工具）
  → ToolExecutor：权限、参数、幂等占用、原派发账本
  → 原三阶段策划：商品分析 → 视觉方向 → 逐图执行稿
  → 原后台保存、全文投影、检查点恢复
  → 用户“第一、第三个做图”：读取原plan_source及原文
  → 原图片请求冻结：提示词 + 实际图片顺序 + 规格
  → Image2/KIE → 保存原文件并交付
  → 用户“再改”：引用最新已完成且被选择的实际结果
```

- 追加：派发前先消费已送达事件，新输入使旧的工具选择/准备失效；仍复用原safe_point和input revision。不能为继续而丢弃追加，也不能把一条“背景改色”当成独立生图授权。
- 已受理/未知受理：优先recover_pending_batch、原RPC/租约/回执，不开启新计划或新图片来替代未知结果。工具选择控制不能绕过原恢复路径。
- 权限：ToolExecutor现有检查必须继续先于任何业务IO。其当前model入口有专门参数验证，将来如引入host路径也不能遗漏等价校验。本方案优先保留原模型工具执行路径。
- 图像执行：本轮不改已验证的Image2输入正文、素材绑定、默认尺寸及比例匹配；不重新引入内部ID/大段规则拼接。KIE偶发拒绝保留供应商失败，不自动反复重发。
- 普通聊天：未绑定该业务阶段的问答直接回答，不要求工具、草稿结构或固定审批。

## 文件范围与实施顺序

| 批次 | 文件/职责 | 改动边界 |
|---|---|---|
| P0 | services/agent/observability/model_request.py；handlers/chat/execution_engine.py；对应真实测试捕获 | 复用现有诊断关联，增加响应证据；先定位，不改派发 |
| P1 | agent/image/ecommerce_planner/workflow.py | 当前阶段、输入修订、回执与有界接续；保留现有绑定与ready读取 |
| P1 | handlers/chat/execution_engine.py | 按能力传递阶段工具选择、保存/区分无调用返回、复用原工具执行和检查点 |
| P1 | tools/definitions/ecommerce_planner.py | 固定工具描述与动态说明分工，不更改业务三阶段方法 |
| P1（仅核验过的模型） | adapters/types.py、factory.py及dashscope/chat_adapter.py；需要时responses.py | 复用模型注册能力，正确序列化对应协议参数；不得静默丢弃，不套用到未知模型 |
| 验收 | manual_skill_formal_entry.py、test_ecommerce_workflow.py、test_chat_execution_engine.py及对应适配器/恢复测试 | 保留自然口语失败分支，补关键模型返回与分派反例 |

初始不需要迁移、新依赖、新Agent、新审批或全局审核器。若第一批证据表明是解析故障，按故障层收窄P1，不为了照搬方案而实施无关调度改动。旧检查点缺少新增诊断/接续标记时按既有状态恢复，已受理任务不重新准备或重发。回滚只撤销本次阶段控制和说明，不删除计划、检查点及图片记录；诊断可沿原模式关闭，恢复仍以旧账本为准。

## 验收：不能再只靠专业提醒

1. 真实失败原话与同一原图：手动Skill，只写三套；运行中追加原话，正文完整且不出图。
2. 同一句分别有/无追加，区分输入改动时序与自然语言的影响；用户取消、改聊天、撤回执行要求时不强制策划或生图。
3. 适配器：真实无工具文本、真实工具增量、多个增量、缺finish/截断、拒绝、解析错误；比较原始返回和内部调用，不能只测模型已提供完美JSON的情况。
4. 工具控制：auto用于普通聊天；指定工具仅在可用的当前阶段；Chat/Responses最终HTTP字段匹配协议，思考开/关分别测试，无法支持的组合不发送。
5. 未调用后的接续有预算、有输入修订、可恢复；崩溃重放不新增一次纠错、策划或图片派发。
6. 自然口语“第一个和第三个做成图吧”：原编号、原文哈希、原素材；无用户尺寸时默认1K并匹配真实来源比例，不要求补技术字段。
7. 页面引用后“这张背景换成浅蓝色”，再“再换成浅灰色”：每轮真实图片SHA链正确，不倒回旧输入；图片顺序、画布、部件实际效果人工核对。
8. 权限拒绝、跨会话/跨组织来源、暂停/取消、已受理未知恢复：不能为保证继续执行而绕过。
9. 最后从正式入口、真实模型和真实Image2供应商完整跑完两轮自然口语链路，保留所有失败及未执行阶段；流程、交付、视觉效果分开计数。KIE失败单列，不用修改断言凑绿，也不以分段结果冒充完整通过。

完成标准是原口语可交付、下一轮可准确引用、异常有真实证据且不会重复付费；不是每轮必定出图、每种文本都强制策划，或保证外部供应商和图像模型永不失败。


## 实施修订：原始返回推翻了“只需强制策划”的假设

`handoff-diag1-20261123`保留真实HTTP SSE后发现：模型完整返回三套新款提示词，没有tool_calls，停止原因为stop，事件JSON解析错误为0。已加载Skill正文同时明确排除产品表面设计。因此这不是已证实的解析丢调用，而是方法适用范围与后台绑定冲突。不能把“想新款”擅自改成“策划电商主图”。

三次只读供应商探针（原工具集、仅保留策划工具、策划参数根节点扁平化，均使用指定函数）都返回HTTP200但无工具调用。这不足以宣称该组合已可靠支持强制选择。本次没有新增模型能力标记，没有修改适配器、关闭用户思考或全局设置required。

实际修复采用原协调与工具执行链：

1. 手选方法的快速入口还必须有主图/详情页/电商套图领域证据。产品新款及其提示词仍走普通图片文字交付；路由不可用不再因手动选择就伪造main_images意图。已有服务器计划、重试窗口和ready编号引用保持原机制。
2. 补齐“把提示词写出来”等宾语在动词前的文字请求识别。复用原登记、范围和精确原文引用，不新增保存工具或二次策划。
3. 仅对真实适用且仍欠服务器策划回执的无调用stop返回，在Actor原预算内允许一次续接。标记绑定输入修订并进入原检查点；恢复不重获额度，新增输入不沿用旧提示。澄清候选、截断、非stop和预算不足不盲重试。
4. 保留首末规范化正文、结束原因、回合及哈希到同任务私有检查点，超长诊断明确标记截短。生产不记录思维链和认证头，诊断不进入普通历史或图片提示词。真实测试额外采集原始SSE作因果核验。
5. 仍无策划回执时不吞正文，也不冒充ready：展示已有正文并明确尚未登记；没有正文则报告未完成。工具描述区分已加载与待激活，ready后只按用户授权选图执行。

已通过两条真实文字复测：同一句新款请求含运行中追加，约22.30秒完成、保存三条、无图片任务；自然主图请求含追加，约85.05秒完成原三阶段、保存三套，模拟ready保存后进程丢失，恢复前后模型调用数均为5。以上是任务耗时；完整pytest分别27.30秒、88.50秒。它们不是完整真实出图验收。

完整真实Image2复测进行中；发现普通方案末尾的整组对比总结仍进入最后一个条目的执行正文，将单独修复展示/执行边界后重新验证，不把本轮供应商受理当成最终通过。
