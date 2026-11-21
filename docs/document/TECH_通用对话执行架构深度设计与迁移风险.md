# 通用对话执行架构：Grok 源码对照、修改位置与迁移风险

> 后续实施以[最终改进方案与逐项风险关闭条件](TECH_通用对话执行架构最终改进方案.md)为准；本文保留为诊断依据。

2026-11-20。深度诊断补充；只形成方案，未修改业务代码、迁移数据库或部署。

阅读顺序：先看[总体方案](TECH_通用对话执行架构诊断与改进方案.md)，再用本文审查实现范围和风险。项目固定在 `4ca37b6703aef49147bec23a533ebb2acc769cc0`；Grok Build 固定在 `2bdd1d6a6369de0e8c68132ea4539e9abd9e14a8`。本文的“确定缺陷”有离线复现，“设计风险”由调用关系推导；没有把推导当作线上事故。

## 1 最终判断

现有程序已经有通用模型循环、权限、原文保存、版本、异步执行和恢复机制。应该修复的是这些机制交接时对模型暴露的合同和状态不一致，而不是另建一套执行平台。

最值得保留的是原生执行账本及图片受理链。最需要改变的是：自然语言正则参与素材绑定、首次工具说明与保存要求不一致、发现不稳定、不同入口采用不同校验/纠错规则，以及模型不容易区分当前原图、历史成图和提示词版本。

这会改变部分模型调用步骤，但不需要推翻产品流程。原来的“策划→保存→选择→执行”仍成立；即时编辑可以在后台完成同样的保存和受理动作。两种入口必须汇入同一套原生执行链，不能各自拥有一套计费、权限或恢复逻辑。

## 2 Grok 的实际完整流程

以下是公开 CLI/harness 的源码流程，不是对其托管产品内部的推测。

| 顺序 | 源码和真实行为 | 与我们的具体差别 |
|---|---|---|
| G1 会话调度 | `run_loop.rs::run_session` 接收 prompt、追加消息、关闭等命令；不是每来一句话就新建无状态工具执行器 | 我们已有 Conversation Actor、claim、执行 token 和固定上下文；应继续保留任务所有权与 fencing |
| G2 输入准备 | `turn.rs::handle_turn_input_inner` 处理输入、保存附件；同文件写入本轮 AttachedImages，将显示引用映射到真实资源 | 我们有 message/content index/file/asset/task 来源，跨轮关系更复杂；不足是统一模型视图，不是完全没有来源图 |
| G3 本轮合同 | `sampler_turn.rs::prepare_tool_definitions_timed` 更新工具集合，处理 MCP 配置/连接状态，取得 native/hosted 定义快照 | 我们先经 registry/policy，再经 Skill、场景和保存投影；需对“最后提供给模型的合同”统一校验，不能只验证初始 registry |
| G4 上下文与采样 | `turn.rs::process_conversation_turn_inner` 处理工具、提醒、模型切换和采样前压缩；另有采样/认证恢复 | 我们不能只复制其工具 Schema 而忽略历史、预算和恢复；也不需要为每轮另加一个规划模型 |
| G5 工具提取 | 同文件区分无调用和有调用；媒体批次超限可在执行前有限重采样；无工具时也存在结束条件、todo/结构输出检查与追加消息安全点 | 我们须明确哪些拦截发生在执行前、哪些必须恢复原批次；禁止已受理后用重采样替换原调用 |
| G6 准备调用 | `tool_calls.rs::prepare_tool_call` 查找工具、解析 JSON、转为具体 ToolBridge 参数；部分解析失败返回可见错误 | 我们业务工具和保存控制的校验不完全一致。需保留“工具不存在、参数错误、原稿不存在”的差别 |
| G7 有限兼容 | 同文件处理拼接 JSON 恢复；可选 MCP schema 参数 coercion；有明确适用范围、资源上限、模型说明，并会重新经过相关 gate | Grok 也会修复输入，并非全程一字不改；我们不能把静默删除未知字段称为等价修复，更不能任意修正查询范围或素材用途 |
| G8 权限与 hooks | pre-tool hooks 可影响参数；重写需要验证并防止重定向，再进行访问类型/权限处理。auto 模式还能安装独立 LLM 权限分类器 | 不能说 Grok 永远只使用主模型或没有专用分支。我们的授权依据是可信身份、范围、参数及资源版本；模型判断不能替代这些 |
| G9 批次执行 | `execute_tool_calls` 对 ExitPlan 等设置屏障；准备阶段出现终止原因时为剩余调用补齐取消结果；批次对写路径建立锁，使用 FuturesUnordered 调度 | 可借鉴精细并发与完整配对。我们已有 policy 批次、写屏障、workspace 锁和 pending batch，不宜简单全部改成并行 |
| G10 结果回写 | 工具结果关联 tool use ID，更新 UI、遥测、hooks 和聊天；ToolStream 协议要求唯一终态，缺终态是错误 | 我们还必须区分“工具提交完成”和“图片任务完成”；submitted 不能直接变成用户需求已完成 |
| G11 继续或结束 | PermissionReject/Cancelled/Followup 等分流；记录进展后继续采样；限制工具轮数、重复调用，必要时压缩，再检查中途新消息 | 我们的重复 guard 目前更偏整个参数批次签名；需要补充同一目标反复修错但无进展的预算 |

### 可直接核对的固定版本源码

- [会话调度](https://github.com/xai-org/grok-build/blob/2bdd1d6a6369de0e8c68132ea4539e9abd9e14a8/crates/codegen/xai-grok-shell/src/session/acp_session_impl/run_loop.rs)
- [输入、主循环及重复保护](https://github.com/xai-org/grok-build/blob/2bdd1d6a6369de0e8c68132ea4539e9abd9e14a8/crates/codegen/xai-grok-shell/src/session/acp_session_impl/turn.rs)
- [工具定义与认证恢复](https://github.com/xai-org/grok-build/blob/2bdd1d6a6369de0e8c68132ea4539e9abd9e14a8/crates/codegen/xai-grok-shell/src/session/acp_session_impl/sampler_turn.rs)
- [调用准备、修复、权限、锁与执行](https://github.com/xai-org/grok-build/blob/2bdd1d6a6369de0e8c68132ea4539e9abd9e14a8/crates/codegen/xai-grok-shell/src/session/acp_session_impl/tool_calls.rs)
- [Args 类型与调用前解码](https://github.com/xai-org/grok-build/blob/2bdd1d6a6369de0e8c68132ea4539e9abd9e14a8/crates/common/xai-tool-runtime/src/tool.rs)
- [搜索返回完整合同](https://github.com/xai-org/grok-build/blob/2bdd1d6a6369de0e8c68132ea4539e9abd9e14a8/crates/codegen/xai-grok-tools/src/implementations/search_tool/mod.rs)
- [图片编辑输入与引用解析](https://github.com/xai-org/grok-build/blob/2bdd1d6a6369de0e8c68132ea4539e9abd9e14a8/crates/codegen/xai-grok-tools/src/implementations/grok_build/image_edit/mod.rs)
- [压缩结果的文本检查](https://github.com/xai-org/grok-build/blob/2bdd1d6a6369de0e8c68132ea4539e9abd9e14a8/crates/common/xai-grok-compaction/src/history/validate.rs)

Grok 的搜索索引包含面向 MCP 集成的 BM25 检索，不是已经证明能理解所有中文任务的全局意图路由。它的本地重复保护仍使用步骤签名，区别 problematic tool 和 true noop；推理服务 doom-loop 信号又是一层，不能把二者合并成“万能无进展检测”。压缩文本非空、标签平衡也不等于证明摘要完整保留所有事实。

可借鉴的是合同可追溯、可操作错误、完整调用配对、输入修复后重新检查和生命周期边界。不能从公开 CLI 推导其托管图片业务的计费幂等性，也没有证据证明我们应该照搬其权限模式、搜索算法或全部框架。

## 3 我们真正需要修改的位置

下面路径相对仓库根目录。分成“改入口”“保留并验证”“后续扩展”，不把受影响文件全部列成必须重写。

| 位置/符号 | 建议变更 | 重要依赖和验证 |
|---|---|---|
| `backend/services/handlers/chat/content_tools.py::model_tools` | 保存说明与实际 materials 要求一致；补齐选择、继续执行含义；发现返回可用合同 | 继续执行是意向，不是自动生成。保持普通正文和保存正文只显示一次 |
| 同文件 `discover` | 精确名称优先；控制工具可自省；业务检索支持动作/对象同义表达，返回候选和就绪状态 | 仍先按可信权限过滤；不能让检索暴露原本不可用工具。先做轻量检索，不预设向量库 |
| 同文件语言辅助函数 | 从关键路径移除对固定措辞的依赖；表达识别仅辅助展示/召回 | `image_execution_request` 当前并非生成总开关；不能误删正常叙述控制 |
| `backend/services/task_execution/content_groups.py::bind_group` | 素材关联采用显式引用和可信来源；正则不能决定是否丢失当前上传图 | 多图角色不明就返回候选/追问，不能默认全部作为执行图 |
| `backend/services/task_execution/content_objects.py::ContentItem/SaveContent/save_parameters` | 先修说明、路径错误与示例；后续给即时任务提供明确模型输入适配 | 保留完整正文、materials 的 input/analysis、保留字段限制及选择器互斥 |
| 同文件 `save/catalog/compile_input` | 同一选择贯穿保存、条目选择和原生参数编译；向模型提供版本及来源身份 | 继续验证内容摘要、owner/scope、素材可用性和 source_prompt 证明；不能用当前最新稿代替冻结版本 |
| `backend/services/handlers/chat/execution_engine.py::_run_loop/_execute_tools_impl` | 接入统一合同/错误；保存出错给精确字段路径；通用无进展计数；逐步收敛 legacy text marker 分支 | 控制与业务调用仍有屏障；pending tool result 完整配对；不能在副作用后重采样整个批次 |
| 同文件 `_read_turn` | 验证输出投影即可，当前无需把普通正文改为整轮缓冲 | 当前非 legacy 前缀会恢复流式；此前不能据代码初始 probe 状态推断整轮都不流式 |
| `backend/services/agent/tool_args_validator.py::validate_tool_args` | 不再静默丢弃用户语义字段；按最终模型合同检查嵌套类型、未知字段和组合条件 | 旧调用兼容须明确等价；查询 domain/filter 等不能悄悄丢失；错误不是已执行结果 |
| `backend/services/agent/tool_loop_executor.py::_process_tool_turn` | 和 Actor 共享合同校验及错误语义，保持各自调用包装 | 保留 check_argument_scope、工具可见性、终止信号及真正 runtime 权限；不能形成旁路 |
| 建议新增 `backend/services/tools/model_contracts.py` | 从现有 ToolSpec 和控制类型生成本轮模型合同、编译器及错误定位 | 只作适配边界，不是第二份人工目录，不改变 policy 所绑定的 canonical spec |
| `backend/services/tools/spec.py`、`registry.py` 及现有工具定义 | 增加必要合同元信息/稳定投影接口，逐类接入 | 已存在定义是事实来源。先保持执行参数不变，避免一次修改全部处理器 |
| `backend/services/handlers/chat_context/image_sources.py` | 统一 available 与 visually_loaded、原图与成图、轮内短引用 | 引用仍绑定固定历史边界与实际资源；不能将历史目录全部下载/注入模型 |
| `backend/services/task_execution/direct_loop.py` | 输出一致的内容与操作状态投影；复用原生调用 staging | 保留可选计划；不强制所有问答先写计划，不放松 image/source guard |
| `backend/services/handlers/chat/executor.py` 与上下文快照入口 | 在现有 claim/snapshot 后构造统一模型视图 | continuation、followup、工作计划候选仍来自可信 DB，不信客户端 task 身份 |
| `backend/services/handlers/chat/image_argument_correction.py` | 与通用错误预算接轨，保留图片特有的受理/语义保护 | 已受理 sibling 不能再次发送；不得因改错字段顺便改原图、正文或付费规格 |
| `backend/services/tools/runtime.py::execute`、`execution.py` | 入口接线及回归，原则上保留执行职责 | 身份刷新、解析资源、恢复、审批、before_dispatch、实际执行的边界不能被新投影绕过 |
| `backend/services/tools/policy.py::decide/plan_batches` | 首批不改变授权/并发规则，只增加合同对照测试 | 确认摘要包含 schema/输入/范围/资源版本；改 spec 可能使旧确认失效 |
| `backend/services/task_execution/model_directed.py`、`pending_batch.py` | 验证新入口仍记录原始/解析调用关系、完整结果配对 | 先闭合旧 pending 批次再采样；已有 effect 的调用不得换 args/hash |
| `backend/services/handlers/chat/tool_lifecycle.py`、`task_execution/image_operations.py`、`operation_store.py` | 保留；新增入口必须复用，补充故障注入 | running/accepted/uncertain 不能重发；持久化失败不能伪装未受理 |
| `backend/services/handlers/image_handler.py`、`handlers/chat_image_request.py` | 复用资源验证、freeze 与 acceptance；首批不重写计费 | acceptance RPC 异常可能已提交；通知发送失败不能把已受理图判为失败 |
| `backend/services/task_execution/replay.py`、`contracts.py` | 只有持久化新字段时才扩展版本解码；先设计双版本读取 | TaskState extra forbid；未经版本设计不能随意追加 checkpoint 字段 |
| `backend/services/skills/context.py` | 如需调整，只统一方法提示与工具可用状态说明 | Skill 固定版本、退出和权限边界保留；不能让一次改图自动变整套策划 |
| `frontend/src/services/messageSender.ts`、`hooks/useImageUpload.ts` | 如界面确需显式选图，复用 source message/content/task 引用 | 客户端只提供选择线索，服务端再次校验；不新造一套不受控 URL 来源 |
| `frontend/src/components/chat/media/ChatImageControls.tsx` | 显示实际基图/配方及 pending/accepted 等状态；必要时加入歧义候选 | 重试、修改、停止不是同一动作；已受理后不能暗示已成功撤销 |
| `frontend/src/stores/slices/streamingSlice.ts`、`contexts/messageStreamProjection.ts`、`utils/taskRestoration.ts` | 验证新事件/内容版本的去重、排序和重放 | 保留 artifact/task/attempt 身份；刷新不能重复卡片，旧完成事件不覆盖当前新任务 |

前端路径在实施前以仓库实际文件为准；本轮只读检查，无浏览器行为验收。数据库受理、结算和 replay 迁移（273–278、299 等）是兼容检查对象，不是建议直接修改已执行历史迁移。

## 4 拟定的边界合同

### 4.1 同一工具有两个明确合同

模型合同描述“本轮允许模型提交什么”；原生合同描述“处理器实际执行什么”。二者可以不同，但必须有唯一可追溯编译关系。

新调用顺序：可信身份/本轮可见性 → 模型输入解析和合同校验 → 引用解析与确定性编译 → 原生输入校验 → 参数/来源证明及原有执行记录 → 当前权限和资源复核 → 原生执行与回执。

这不是重新排列现有恢复代码的指令。已有调用按其记录身份与冻结参数恢复；`runtime.execute` 现有 replay 分支不得被“使用今天最新 Schema 再校验一次”阻断。恢复只能检查已保存事实及当前必要权限/资源，不能重新让模型选图后重发。

建议本轮临时合同对象包含 tool_name、model_schema、canonical_tool、compiler_revision、schema_fingerprint 和错误定位器。首批仅内存使用，保留现有 invocation 原始参数摘要，不替换旧幂等键。需要跨重启保存新编译语义时，再增加明确协议版本/读取器；不能把扩展字段硬塞进严格 TaskState。

### 4.2 即时图片任务不再要求模型掌握内部登记顺序

推荐的模型语义输入示意：

```json
{
  "instruction": "保留红色福字主体，背景换成棕红色实木桌面",
  "inputs": [{"ref": "image_1", "usage": "input", "role": "edit_base"}],
  "options": {"aspect_ratio": "auto"}
}
```

这是待实施合同，不是当前工具已支持参数。实际模型、尺寸和默认项只使用现有能力支持值；不把 `auto` 或示意角色直接塞给不支持它的处理器。

`image_1` 必须由服务端本轮目录解析为 canonical ref。明确单图编辑由模型选择该图；图片本身不构成付费执行许可。多图用途或历史“第二张”不唯一则先 resolve/追问。后台保存完整正文与素材，生成现有 ContentRecord/证明，再进入原生图片提交。保存后受理前失败，保留可恢复草稿；不能声称 DB 保存与外部供应商提交是一个跨系统原子事务。

已有方案执行继续引用固定 content/item/revision，不从摘要重写完整正文。对成图修改时底图是选中成图；“按原图重做”才回到原图。已有 source_task_id/source_message_id/origin 关系复用，不新造平行来源图。

### 4.3 错误必须包含动作和真实状态

模型可见错误建议含 code、stage、path、简短原因、当前合法形态、recovery.action，以及可证实时的 operation_id/accepted/completed。内部堆栈、完整鉴权信息不下发。未确认状态使用 unknown/省略未知布尔值，不能默认 accepted=false。

| 错误情况 | 正确后续 |
|---|---|
| 未执行的字段/类型错误 | repair_arguments，给 `document.items[0].materials` 等精确路径及最小示例 |
| 来源版本失效或候选不唯一 | resolve_context 或 ask_user，不默认换成最新文件 |
| 确认/权限缺失 | 执行现有授权流程或停止；不通过改写参数绕过 |
| 已受理，后台仍运行 | wait/reconcile，引用原 operation/task；不重提付费请求 |
| 超时且是否受理不明 | reconcile 原请求；不能建议一律缩小参数重试 |
| 明确受理前暂时故障 | 由适配器按安全条件有限重试；不混入模型纠错预算 |
| 服务能力不支持 | stop 或提出真实替代，不能伪装同工具稍改字段就支持 |

完整 Schema 按需发现；日常错误返回相关字段和最小示例，避免每个错误塞全目录导致上下文膨胀。

### 4.4 纠错预算按目标进展判断

保留总轮数/用量上限，另外记录目标、输入版本、错误类别及阶段、实质进展。参数名从 attachments 改为 image_usage 不算进展；找到合法来源、拿到授权、受理或完成才算对应目标的进展。同批别的任务成功，不能无限刷新失败任务预算。

分页换游标、有限异步等待、新用户指令造成版本变化必须区别处理。具体次数先作为可配置值用回放和真实任务定标；不把每次签名不同都视为新目标。若预算需要跨重启生效，须纳入版本化状态，不能只用进程内计数器冒充持久防护。

## 5 修改可能造成的负面影响

以下覆盖已识别的主要风险，不宣称穷尽所有未知行为。P0 是不可放行的权限/重复副作用风险；P1 为任务与恢复正确性风险；P2 为体验和成本风险。

| 风险 | 触发方式/负面结果 | 预防、验证和停止条件 |
|---|---|---|
| P0 重复付费或业务写入 | 新入口绕过 lifecycle；超时当未受理；重采样已部分执行批次 | 未确认状态只核实；复用原操作键。注入 acceptance 已提交但响应丢失，确认只存在一个原生任务；出现重复提交立即停用新入口 |
| P0 授权对象改变 | 参数修复或别名解析发生在确认后；资源新版本继承旧确认 | 解析后再绑定授权；执行前再次验证。确认后换文件/范围必须失效，不能自动放行 |
| P0 越权来源暴露 | 统一图片目录跨会话/组织枚举；客户端伪造 ref | 目录与读取都基于 scope；模型 ref 只是选择线索。跨组织/已删除资源测试必须拒绝 |
| P1 旧任务不能恢复 | 严格 TaskState 新增字段、旧 reader 不认识；最新合同拒绝历史合法调用 | 版本化双读、新写只用于新任务；恢复用固定证明；旧 pending/accepted/uncertain 场景全部覆盖 |
| P1 回滚后状态不可读 | 新生产状态交给旧二进制；开关切回被误认为全部回滚 | 同次发布先就绪兼容 reader 再启用新写；回滚只停新入口，存量任务继续由兼容版本处理；无法证明可读就禁止直接回旧二进制 |
| P1 编辑错图/丢约束 | 去正则后默认所有图、默认最新成图；修改时模型缩写原文 | 显式选择+固定版本；保留未变约束与全文；多组第二张歧义测试必须追问 |
| P1 严格校验破坏旧能力 | 以前忽略的字段开始报错，供应商工具 Schema 支持不同 | 先在开发测试与离线回放中统计差异，验证后全量上线内测；明确兼容映射不改变语义，不降级删除过滤条件 |
| P1 原文/卡片重复 | 即时入口自动保存加上旧 legacy 保存；UI 按新身份生成第二张卡 | 单次保存绑定调用和版本；artifact 去重与刷新重放测试；双保存出现则停该入口 |
| P1 用户中途改意图仍执行旧稿 | 新编译/缓存忽略 before_dispatch 最新命令；旧结果覆盖新任务 | 保留执行前 gate 与冻结输入，旧完成事件保留所属任务；对新版本不覆盖不混计 |
| P1 工具对话配对破坏 | 屏障或错误提前返回，遗漏 sibling tool result | pending batch 原有恢复优先；每个 tool call 有对应结果，恢复前不继续采样 |
| P1 并发写冲突 | 认为统一循环等于全部并行；忽略同路径/共享 sandbox | 先保留现有 policy 和锁；按领域证明独立后再放开，不把 Grok 路径锁直接替换 DB fencing |
| P1 过早终止有效任务 | “无进展”将分页/异步等待/材料补齐判为重复 | 目标及输入版本分桶、真实进展判定；长分页与等待测试；正常完成率下降就回退新预算策略 |
| P2 发现噪声增加 | 同义词扩展召回过多、不相关工具进入模型 | 精确名优先、候选限量、相关性评测；别名和中文变体固定对照；不默认调用召回第一名 |
| P2 token/延迟/费用上涨 | 每轮全 Schema、全历史图片，重复检索或新增分类模型 | 按需工具合同/视觉加载；记录 token、首反馈、总耗时和每任务费用；阈值由同条件基线决定 |
| P2 自动保存让用户感到步骤更多 | 内部草稿都显示、普通问答也先存文档 | 保存是后台必要事实；仅成果显式交付，普通文本保持现有流式；评测用户可见步骤数 |
| P2 API 成功被误当质量达标 | 图片已返回但主体改变，文件已写却不可用 | 确定性产物检查与视觉任务检查分别统计；不要通过无限自动重生成追求主观完美 |

缓存需单独审查：现有 cache 的身份、作用域、资源版本和 TTL 不能在统一入口时丢失；不能因为函数名像读取就自动添加缓存。现有 code_execute 的缓存与共享工作区规则也不能静默改变。

## 6 实施顺序与每批验收

| 批次 | 具体交付 | 进入下一批的证据 |
|---|---|---|
| B0 基线 | 固定模型/合同/附件的回放集；新增任务级指标；整理旧恢复样本 | 有首次成功、纠错轮数、来源正确率、延迟、费用和未知受理状态统计；不凭感觉比较 |
| B1 明确缺陷 | 修保存说明/字段错误；发现精确工具与控制合同；消除静默语义丢失；修同目标换字段循环 | 本文已复现失败全部有正确分流；合法旧调用回归；不改数据库和原生图片受理 |
| B2 合同边界 | 共享模型合同投影/校验，先离线对比与回归，交付时全量上线内测 | Actor 与子 Agent 一致；供应商投影保真；审批、作用域、结果配对、旧 replay 不回退 |
| B3 上下文与来源 | 当前附件、历史成图、提示词版本统一可见目录和选择 | 同义表达选同图；analysis-only 图不外发；跨轮/多组/不可见来源正确 resolve |
| B4 即时入口 | 单次模型语义请求，后台保存→编译→原生提交 | 与旧入口冻结出的关键请求一致；保存后失败/受理响应丢失/重启均不重复执行；新版本解码可回滚 |
| B5 交付与质量 | 状态统一、前端重放、实际产物检查、真实多轮评测 | 旧卡片不重复、异步归属正确；新旧入口同条件比较，质量和链路可靠性分别报告 |

推荐 B1–B2 先交付，确认收益后再引入 B4 新输入形式。B1 也不能只改单条提示词掩盖问题；其结果必须是正确合同和可修复错误，而不是增加另一组换背景关键词。

不在本轮承诺具体工期。逐批工作量的关键变量是历史合同变体、供应商约束和是否确需持久协议升级；只按文件数量估时会漏掉恢复与兼容测试。

## 7 验证如何对应真实风险

复用而非重造现有验证设施：

| 验证面 | 现有入口/新增场景 |
|---|---|
| 参数合同 | `test_tool_args_validator.py`、`test_content_objects.py`；补嵌套未知字段、错类型、互斥选择器、domain/filter 不丢失 |
| 来源和权限 | `test_chat_image_sources.py`、`test_chat_image_sources_postgres.py`、`test_tool_policy.py`；补别名伪造、版本改变、跨 scope |
| 保存与全文 | `test_content_verbatim_real_replay.py`、`test_chat_content_real_replay.py`、`test_content_objects_postgres.py`；补即时入口与旧方案同请求证明 |
| 受理和恢复 | `test_task_execution_image_operations.py`、`test_task_execution_pending_batch.py`、`test_replay_checkpoint_integration.py`；补受理已提交但响应丢失、部分 sibling 成功、旧版本恢复 |
| 状态格式 | `test_replay_checkpoint_migration.py`、`test_replay_checkpoint_store.py`；补新旧 reader/write 组合及回滚前排空 |
| 前端 | 现有测试及浏览器流程按实际修改选择；覆盖切图、重试、刷新、断线、迟到完成、同一 artifact 重放 |
| 用户目标 | 固定模型真实任务评测，允许不同正确工具路径；中文同义表达、跨轮指代、工具发现、缺能力、权限、供应商故障分别归因 |

本轮证据仍是总体方案记录的 112 项上下文/续接测试通过、此前同业务基线工具测试及离线复现。上述“新增场景”是实施验收要求，不冒充已经执行。两项 python-docx 环境失败仍未解决；未做真实付费请求、生产故障注入、Rust 构建和前端浏览器验收。

## 8 本轮可以决定与仍待实测的事项

现在可以采用：现有 Actor 主线；保留原生账本；一个模型合同边界；显式素材与版本；状态驱动纠错；按批兼容迁移。没有发现必须换语言、重建数据库、引入向量平台或再做一套 Agent runtime 的依据。

实施前需要把 B4 的示意参数转为受当前工具能力约束的真实合同，并确定保存与受理之间的续接方式；如新增持久状态，再提交版本/RPC/迁移设计。模型成功率、中文发现的最佳算法、视觉质量收益、token 成本与最终延迟必须同条件实测，公开源码不能替代这些证据。

公开大厂资料也没有“任何任务都好用”的统一认证标准。目标应落实为：支持范围内正确完成，范围外明确反馈，出错可修复，未知副作用先核实，多轮来源不混乱。达到这些，需要合同改造和持续真实任务评测一起完成。
