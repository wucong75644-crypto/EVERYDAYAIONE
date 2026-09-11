# 05：第 1、2 项修复与 Grok Build Actor 对照

> 后续进展：用户随后已授权恢复上下文相关架构逻辑，现已实现并完成 986 项相关回归。最新实际接口、压缩链路补充、A/G 状态与限制见 [上下文结果恢复](TECH_05_上下文结果恢复.md)。下文“本轮未实施上下文”和 608 项数字是第 1、2 项阶段的历史快照；真实模型复验仍未完成。

更新：2026-09-11。用户本轮授权修复任务删除、文件调用契约；上下文与结果状态先对照 Grok Build 和本项目历史，不实施该部分。此前未验证的附件位置调整、额外目标提示已撤回，PromptBuilder 与线上 HEAD 一致。

## 范围与版本

- 工作树：`/Users/wucong/EVERYDAYAIONE/worktrees/tool-unification-05`；分支 `codex/task/20260910221341-tool-unification-05`。
- HEAD / 上次完整部署：`887b28ae4aedb5d4dde5f0cbbd1bcb4484fda9ba`；本次被测版本为该 HEAD 加未提交差异，不能称为已部署修复。
- 第 1、2 项本地实现与相关自动化完成：**600 + 8 = 608 passed，0 failed/error/skipped/xfail**。文件参数生成正确不等于真实模型永远不会填错；生产复验尚未执行。
- 05 整块技术验收仍未通过：上下文缺失、失败提示失真及真实模型目标误用没有关闭。未提交、推送、部署、合并、清理或启动 06。

## 1. 本次实际修复

**任务删除：修正数据库生命周期。** 已确认草稿仍以 `confirmed_task_id` 引用正式任务，244 的默认 NO ACTION 外键阻止 249 的删除 RPC。253 把该外键改为 ON DELETE CASCADE，与 245 已有的 `source_task_id` 一致。任务经既有 ChangeSet 确认删除时，相关草稿和预检中间记录一同清理；独立 ChangeSet 事件、幂等回执保留。没有绕过权限、revision 校验或运行中限制，也没有把失败响应改成成功。

真实临时 PostgreSQL 应用 069/071/244/245/249，再执行 253；覆盖旧草稿真实确认创建任务 → 删除、重复确认、重复删除、错误组织、旧 revision、运行中拒绝、审计回执保留、约束回退。旧确认 token 在任务删除后返回 missing，不重新创建任务。

**文件读取：统一模型拿到的调用参数。** 附件、搜索结果与工具描述共享 `file_call_contract.py`：附件原始数据文件提供 `read_call` JSON，仅含 `file_id`；搜索结果的数据文件提供仅含正式 `resource_ref` 的 JSON。保留原 ID/路径/引用字段供旧调用使用，并删除搜索结果中误导性的 `file_analyze('路径')` 位置参数示例。调用方复制现成参数，不需要再拿文件名拼出引用。

这修复的是系统提供给模型的调用信息不一致。执行端仍严格校验签名、版本、范围和多选择器一致性；模型若继续提交非法参数，仍如实报错。合法旧 JSON 参数及别名不变。没有增加忽略无效引用的兜底，没有更换 Actor、模型或文件解析引擎。

| 边界 | 本次适配 | 兼容证据 |
|---|---|---|
| 模型输入 | `format_attachments`、`FileDescribeMixin._file_reference_line`、`build_file_tools` 共享选择器说明与参数生成 | 附件和四种搜索入口进入真实 Runtime；合法引用解码到同一文件，真实 CSV 分析执行成功 |
| 模型结果 | 原 Chat `to_message_content`、Loop `to_tool_content` | 两消费者逐值相等，不把 Chat blocks 当 Loop 字符串 |
| 前端 | 无新 block/字段/事件；删除仍经过原 ChangeSet | 原 18 份全字段 WS/delivery 对照和表单终止等消费集成重跑通过 |
| 审计 | 不改原统一结果取值；删除保留独立事件与回执 | 消费集成不重复记账；PG 删除生命周期断言回执仅一次 |
| 持久化 | ToolResult 仍显式转旧兼容载荷；253 仅改业务草稿外键 | 原 ledger/checkpoint 兼容集成通过，原 reader/serializer 未改；PG 验证约束回退 |

## 2. Grok Build 怎么处理

对照的是 xAI 官方公开源码，固定仓库提交 `37949780c144e37df692e3d669051a21fec24f20`（其 `SOURCE_REV` 为 `c4ea71cfdbcdb21e32e41bc25a0043d7d4836714`）。只做源码对照，没有编译运行 Grok、没有声称它不会误判用户意图；也没有证据证明本项目最初直接派生于这个版本。

| 环节 | Grok Build 源码行为 | 本项目实际情况 |
|---|---|---|
| 新输入 | `UserItem` 区分真实用户与 synthetic_reason，记录 prompt_index；注入提醒、恢复、调度消息有不同来源 | 本次 Actor 的 input_message_id、历史 revision、新附件 manifest 均正确，没有把旧用户请求重新派发 |
| 保存结果 | Assistant、ToolResult 分立；工具结果带 tool_call_id，可带图片；push 时保存并加入 conversation，不要求 assistant 有正文 | 当前轮工具协议与 ledger 有记录，但下一轮历史从 messages 的展示 blocks 重新投影，完成 assistant 无 text 时可整条丢失 |
| 生成请求 | 从 typed conversation 构造；按预算裁剪旧工具正文，仍保留结果记录；摘要路径另有简化规则 | 完成轮次默认只取 text；表单/文件独占结果为空，tool_digest 随后也无处附加 |
| 状态与继续 | 每次工具输出分别发 ToolCallUpdate，并写对应调用结果；无工具调用时还检查待办、插入消息等，再结束 | ToolResult 实际错误和成功都回填；另外的 ToolLoopContext 只累计失败工具名，会在后续成功后仍提示“上轮失败” |

官方固定源码：[消息类型](https://github.com/xai-org/grok-build/blob/37949780c144e37df692e3d669051a21fec24f20/crates/codegen/xai-grok-sampling-types/src/conversation.rs#L97)、[Actor 写入](https://github.com/xai-org/grok-build/blob/37949780c144e37df692e3d669051a21fec24f20/crates/codegen/xai-chat-state/src/actor/mutations.rs#L230)、[请求与裁剪](https://github.com/xai-org/grok-build/blob/37949780c144e37df692e3d669051a21fec24f20/crates/codegen/xai-chat-state/src/actor/request_builder.rs#L20)、[工具结果回填](https://github.com/xai-org/grok-build/blob/37949780c144e37df692e3d669051a21fec24f20/crates/codegen/xai-grok-shell/src/session/acp_session_impl/tool_calls.rs#L2768)、[轮次结束](https://github.com/xai-org/grok-build/blob/37949780c144e37df692e3d669051a21fec24f20/crates/codegen/xai-grok-shell/src/session/acp_session_impl/turn.rs#L3449)。它的摘要压缩也会移除工具详情并保留工具名注记，不能概括成“永远保存全部上下文”。

## 3. 我们在哪次改动后出现差异

| 提交 / 日期（北京时间） | 修改 | 已证明的影响 |
|---|---|---|
| `af583544f7c5ef4779c9a14df29a2012fa5251fe` / 04-04 | ToolLoopContext 累加失败工具名 | 失败后同工具成功，旧失败提示仍存在；这是旧逻辑，不是 05 新增 |
| `c4053fef7999e28fecafde0f00a4bffffa7eca8f` / 07-18 | 已结束 assistant 轮次仅保留正文；只有最新中断轮次保留工具协议 | 同一条“工具步骤 + 表单、无正文”输入：此前得到 assistant/tool 两条，提交后变成空数组 |
| `c12ea01ebd54db75a14623b7f9855f4cd9b087d2` / 08-29 | 表单展示后直接结束模型循环，避免重复确认文案 | 表单可成为无正文的最终回复；与 7 月规则组合，下一轮丢失该回复。表单终止本身符合既有产品语义，应保留 |
| `887b28ae` / 板块 05 | 统一结果消费，保留原表单终止；新增 uncertain 停止等边界 | 上述历史加载、失败摘要、Actor 输入和 PromptBuilder 与 01–04 验收基座相同；未发现 05 把旧输入换成本轮输入 |

[历史探针](tool-unification-evidence/05-actor-history-probe.py) 用同一份合成输入调用 7 月修改前、修改后与线上版本的实际投影函数；依赖提取器逐字节相同。[结果](tool-unification-evidence/05-actor-history-observations.json) 确认 2 条 → 0 条 → 0 条。本次生产上一轮确实只有表单和工具步骤，无正文；已有只读轨迹确认它在下一轮历史中消失。

因此：**历史改动叠加造成结果信息丢失已经证实；这是否导致那次“读取文件”继续统计导出，尚未证明。** 实际轨迹是读取成功后模型新发起统计和导出，不是回放旧执行、重复 UI 展示或 Actor 取错当前消息。恢复 7 月以前的全部工具输入会重新带回旧代码干扰，不能直接回滚解决。

## 4. 上下文与结果状态的后续建议（本轮未实施）

用具体例子说明两处改法：

- **历史结果：** 上一轮只展示了任务表单，下一轮也应保留“已展示任务配置表单”这条事实，不能因没有文字而当作没发生；同理保留生成文件的可识别引用。表单展示不等于任务已创建，不能凭旧卡片倒推业务成功。不恢复旧代码和完整表格到历史，不改变 Actor 输入锚点或表单终止。实现时同步历史缓存投影版本，旧 ledger/checkpoint 格式继续保留。
- **当前状态：** 一次 file_analyze 参数错误，纠正后同一读取动作成功，提示应体现“首次失败，随后成功”，不能继续说“上轮失败，换工具”。仅改派生给模型的状态摘要；原错误、调用 ID、重试轨迹和审计保留。不能用“同名工具后来成功”清掉其他文件的失败，也不能把 cancelled/uncertain 归入普通成功或盲目重试。

这两项使模型看到的事实更准确，不能单独保证它按最新目标行动。目标误用需要在固定历史、固定新附件下对照“读取文件”和“明确继续旧统计”两个请求，记录实际调用序列；不能用 Mock 模型回答证明意图修复。继续以原技术方案的验证约束为准，当前不增加新的 Actor 架构或持久化协议。

## 5. 本轮逐项验收与证据

`R`：[生产回归测试](../../backend/tests/test_tool_result_05_production_regressions.py)；`D`：[删除数据库集成](../../backend/tests/test_scheduled_task_draft_delete_integration.py)；`I`：[原 05 消费集成](../../backend/tests/test_tool_result_consumption.py)。下表是此次增量后的状态，不以历史测试通过掩盖生产未关闭问题。

| 验收编号 | 场景及预期 | 实际结果 | 状态 | 证据 |
|---|---|---|---|---|
| A-05-01 | Chat/Loop 保持各自旧结果投影 | 附件 + 四搜索入口真实分析后两投影逐值相等；原六状态/多模态用例复验 | 通过 | R 两消费者用例；I |
| A-05-02 | 原 WS/delivery/事件顺序 | 18 份全字段协议用例重跑；无动态字段删减或新增归一化 | 通过 | I `test_live_chat_protocol_matches_main`；600 项日志 |
| A-05-03 | 文件、图片、表格不丢不重，表单原流程 | 原产物/表单消费用例通过；真实用户读取目标仍有误执行未关闭 | 未验证 | I；R 仅证明调用参数与输入透传，生产复验待执行 |
| A-05-04 | 错误、成功、取消、uncertain 等语义一致 | 原 ToolResult 状态断言通过；额外失败摘要失真未修复 | 未通过 | I；05-diagnosis-probe.py 的后续成功仍提示失败 |
| A-05-05 | 旧 ledger/checkpoint、审计不重复 | 旧投影与 serializer 集成通过；删除事务保留独立回执且重复调用不重复删除 | 通过 | I 持久化用例；D |
| G-01 | 仅授权第 1、2 项和诊断 | 迁移 + 三个调用信息出口 + 共享 helper；未改 Actor/循环/历史状态/06 | 通过 | 当前 diff、源码指纹 |
| G-02 | 所有验收行为已验证 | 上下文与真实模型行为证据仍缺 | 未通过 | 上述 A-05-03/04 |
| G-03 | 本次新增与相关回归 | 最终 608 通过，无跳过；修复测试搭建问题后复验，未弱化生产契约 | 通过 | 当前 runner 与两份完整日志 |
| G-04 | 合法参数、原模型/WS/ledger 兼容 | JSON 参数结构/别名不变，模型提示说明有意调整；原投影和协议通过 | 通过 | 源码检查；R/I/D |
| G-05 | 范围、证据、回退和限制可交接 | 当前记录与验收/交接/问题表同步 | 通过 | 本文及链接文档 |

环境：Python 3.12.12，现有完整 backend/venv；工作树没有应用 .env，数据库/Redis 使用不可用占位地址；D 自行建立 socket-only 临时 PostgreSQL，结束删除。仅合成 CSV，无生产表格、模型网络调用或付费媒体。

命令：`bash docs/document/tool-unification-evidence/run-05-items12.sh`。结果：[600 项回归](tool-unification-evidence/05-items12-regression.txt)、[8 项 PostgreSQL](tool-unification-evidence/05-items12-postgres.txt)、[边界与指纹](tool-unification-evidence/05-items12-source-checks.json)。配置插件及 Pydantic/FastAPI/Supabase 弃用警告保留，没有失败或跳过。

中间测试问题如实记录：搜索 Handler spy 最初在 Runtime 已捕获绑定后安装，后改为初始化前安装并执行真实 CSV→Parquet；随后断言误把 Chat 的 list 投影当字符串，改查原 summary，并继续严格比较两种完整投影；PG 生命周期 fixture 最初漏加载真实 071 迁移，补齐后通过。没有为通过而更改产品代码或跳过用例。旧位置/目标提示断言随用户明确“上下文只调查”及对应补丁撤回而撤回；最新用户/附件透传和 checkpoint 完整相等断言仍在。此前 731 项是旧候选记录，不累计进本轮数字。

| 问题编号 | 复现与根因/影响 | 所属板块 | 处理结果 | 复验证据 |
|---|---|---|---|---|
| P05-1 | 旧创建草稿 FK 阻止删除 | 05 验收修复 | 253 本地完成，生产未执行 | D 8 项，包括修复前真实拒绝 |
| P05-2 | 文件名被误填进签名引用；调用说明与示例不一致 | 05 验收修复 | 共享可复制 JSON 与一致说明完成；真实模型待复验 | R 22 项，真实 Runtime/CSV；严格拒绝仍有效 |
| P05-3 | 无正文完成回复在历史丢失；读取后误执行旧目标 | 05 验收调查 | 前者历史变更可复现，二者因果关系未证实；未改上下文 | 历史探针、固定源码及既有生产轨迹 |
| P05-4 | 后续成功后仍显示旧失败提示 | 05 验收调查 | 缺陷已复现，按本轮范围保留待决定 | 05-diagnosis-observations.json |

## 6. 发布后最少用户验证与回退

待用户新指令“提交部署”后记录新候选 SHA，并按受控发布入口执行 253；本轮没有代用户删除生产任务。

1. 对原先删除失败的测试任务重新发起删除并确认：任务应消失；重复点击不重复执行，旧失败 ChangeSet 仍显示失败事实。
2. 插入新表，只说“读取文件”：观察首次调用是否只用提供的参数、是否读取正确文件。再分别验证从工作区搜索、单文件和目录搜索后读取；拒绝过期引用仍应提示真实原因。
3. 在含旧统计要求的聊天中复验最新读取目标，另发明确继续统计作为对照。此项用来补齐尚未关闭的上下文问题，不能预先标通过。文件/图片打开、表单可操作、表格不重复等观感需记录实际候选与人工结果；本轮没有新人工记录或付费样本。

应用可回到 `887b28ae`；253 与旧应用兼容，但代码回退不会自动恢复旧外键。提供 [253 回退 SQL](../../backend/migrations/rollback/253_scheduled_task_confirmed_draft_delete_rollback.sql)，D 已验证恢复旧约束行为。已确认删除的数据不会被回退 SQL 恢复。

结论：第 1、2 项本地改动可审查；上下文/状态仅完成诊断与建议。05 整块仍未通过，用户验收未关闭，06 前置未满足。
