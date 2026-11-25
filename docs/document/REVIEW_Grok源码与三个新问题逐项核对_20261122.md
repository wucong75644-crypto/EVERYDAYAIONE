# Grok Build 源码与三个新问题逐项核对

本次只研究与核对，不修改业务代码，不部署。

## 证据范围

2026-11-22通过 GitHub 官方仓库接口重新查询 `xai-org/grok-build`。公开 main 为 `2bdd1d6a6369de0e8c68132ea4539e9abd9e14a8`，与前次固定版本相同。原33个文件之外补取14个文件，并按 Git blob SHA 校验。源码、清单和仓库说明保存在 `/private/tmp/grok-three-issue-audit-20261122/`。

[仓库说明][readme]将其定义为编码代理 CLI/TUI 及代理运行时，公开代码从内部仓库定期同步。它不是我们电商聊天产品的同构实现；源码公开、存在测试，并不等于已验证我们的商品图片、千问接口和业务数据库。此次读取了相关 Rust 测试，**没有编译或运行上游 Rust 测试**。该提交公开 check-runs 查询仅看到一项成功的安全扫描，不能据此宣称其全部功能测试或生产稳定性已被我们验证。

前一轮对三个新问题的建议没有逐项完成此次核对，不能把前一轮建议统称为“Grok 已验证的完整方案”。以下明确区分源码事实和项目适配。

## 三个问题的直接结论

| 问题 | Grok 源码实际提供的机制 | 我们的差异与能采用的部分 | 不能冒充已有保证的部分 |
|---|---|---|---|
| 多套方案迟迟不显示 | [流式事件][sampling]将正文增量直接投影为页面消息；正文与思考分开统计，思考首 token 不算首个有效交付。[长度续接][length]对达到输出长度上限的回答提供受开关和预算限制的续写。 | 我们存在可见正文自动登记入口，但手动提示词方法仍可能选择长 `save_task_text` 参数；展示格式校验失败后重新输出整份文档。应优先让完整写作正文流出，再由后台原子登记，并保留原文恢复。 | Grok 的长度续接不能修复我们的保存参数错误。我们的稿件条目、稳定编号、数据库原子登记和方法展示约束是项目业务契约，不能声称是 Grok 同名现成功能。 |
| 图片问答答错 | [图片描述分支][describe]把真实图片、当前问题和有界历史交给视觉模型；部分 harness 使用该分支，描述失败时不会悄悄假装看过图。[读图片][read_image]处理解码、尺寸和字节预算，并显式报告无法提供图片的原因。 | 采用真实原图、明确当前问题、有限历史、不可用时如实报告等输入治理原则。我们当前是千问直接接收图片，不能为了照搬而强制叠加一轮图片描述模型。 | 这些模块没有提供已核验的“条纹宽度/倍数必然正确”的专用测量流程。把数量、宽度、面积区分开，必要时调用测量工具，是我们的适配方案；不能称为照搬 Grok 后就能保证视觉答案准确。 |
| 供应商拒绝图片，页面只给泛化错误 | [Responses 事件解析][responses]保留失败事件的供应商 code 和 message，转为结构化失败；[错误类型][errors]与[重试判断][retry]区分可重试状态、请求拒绝和无效图片，并受单步、整条请求和时间预算限制。 | 我们 `responses.py` 对 `response.failed` 等事件抛通用异常，丢掉具体拒绝原因。应在供应商适配层保留并归一化错误，再沿原任务/事件链送到页面、日志和恢复状态。 | xAI 的错误码不能原样套给千问。此次千问返回外层 `server_error`，内部却是 `DataInspectionFailed`；若只按外层500形状处理，仍可能错误重试。审核拒绝不能靠我们的程序保证放行。 |

## 必须连起来核对的链路

| 环节 | 上游源码依据 | 我们需要保留或补齐的约束 |
|---|---|---|
| 用户输入与本轮身份 | [采样事件][sampling]、[事件测试][sampling_tests]按 request 所有权消费事件，旧请求不能串入下一轮。 | 继续使用原 Actor、用户/组织/会话范围和输入版本；旧完成事件不能覆盖新一轮消息。 |
| Skill 选择与方法职责 | 前次核对的 `prompt/skills.rs` 和 `implementations/skills/skill.rs` 是发现、加载和注入机制。 | 手动选择、固定版本、工具上限、方法阶段仍按既有规则执行。优化文本交付不能跳过原方法必须交付的内容。 |
| 真实素材进入模型 | [图片描述][describe]、[读图][read_image]、[图片预算][image_budget]处理真实输入和预算。 | 保留原图身份、文件版本、哈希与引用用途。用户已提供的关键原图不可被无声替换成摘要。 |
| 正文、思考和工具参数分流 | [采样事件][sampling]为不同通道采用不同展示和指标。 | 关闭思考仍是真正关闭；文本首 token、完整正文、持久登记各记各的状态，不能把“工具正在生成参数”报成已交付方案。 |
| 正文流出与后台登记 | [流式正文][sampling]与会话处理是可参考机制；我们已存在 `content_delivery.py` 的原文 journal。 | 研究让已确认的文字方法采用现有正文链路；保存唯一原文，校验后原子登记。若保存失败，从 journal 恢复原文，不能要求模型重新创作。 |
| 编号供下一轮执行 | Grok 没有与我们业务契约同构的稿件编号数据库链路证据。 | 这是项目适配：页面展示的版本、后台条目与下一轮编译原文必须一致。缺失或歧义必须明确指出，不能猜编号。 |
| 工具执行授权 | 前次核对的工具分派、permission hub 与取消机制提供职责参考。 | 原 ToolPolicy、素材核验、确认、额度、操作账本与防重复派发不变。流式正文及登记成功不授予生图权限。 |
| 中途截断与恢复 | [长度续接][length]有受控续写；[采样测试][sampling_tests]覆盖失败捕获、重启片段累计和过期事件。 | 只在真正达到长度限制时评估续接，不把格式错误、审核拒绝和网络错误一概当成“继续写”。不完整方案不能标为可执行的完整条目。 |
| 失败原因传递 | [Responses 失败事件][responses]、[供应商错误呈现][provider_error]保留并约束错误信息。 | 错误类别、供应商原因与关联标识沿任务失败、页面及恢复一致传递；对外展示受控内容，不直接透传任意 HTML、内部路径或凭据。 |
| 自动重试 | [分层重试][retry]、[对应测试][retry_tests]覆盖资格、总预算、时间窗、空响应及无效图片拒绝。 | 采用分类和有界原则，不照抄3次/10次/10分钟等参数。保持当前已输出/已有工具效果时不得盲目重放的保护；不重复收费或派发。 |
| 被拒绝的历史图片 | [图片剥离][strip]区分本次请求与存储历史；只有能明确归因的拒绝才考虑持久修改，并受备份、终态和写入确认约束。 | 不能照抄删除业务历史图片。我们的原图、资产与后续改图仍需保留；本轮不可用和下轮是否重试必须显式记录。当前任务依赖该原图时，不能丢图后继续假装完成。 |
| 新追加、取消与跨轮接续 | 前次核对的 `interjection.rs`、`cancel.rs`，以及[过期事件测试][sampling_tests]。 | 持久接收回执、输入版本、取消优先、终态窗口关闭与未知回执保持原 request_id 等已有机制继续保留。 |

## 特别需要避免的照抄

1. **把保存编号交给模型。** 我们需要后台保证原文、版本和编号一致；Grok 的普通流式聊天不替我们完成这份数据库契约。
2. **把视觉描述当成精确测量。** 上游存在视觉描述与缓存，不代表输出数值经过像素测量。当前千问已能直接读图，增加描述模型还会增加耗时与信息损失。
3. **把所有 `server_error` 都重试。** 必须识别供应商的真实拒绝语义，特别是此次千问的包装格式。
4. **拒绝图片后直接删历史。** Grok 有严格限定的请求剥离/历史修复策略；我们连续改图依赖不可变素材来源，需要按业务语义适配，不能牺牲历史引用换取请求成功。
5. **把上游测试存在当成本项目通过。** Rust 测试定义是设计证据；本项目仍必须用原始失败会话验证模型输出、数据库、页面和下一轮引用。

## 后续实现和验收边界

实现应依托现有链路，优先补正文交付/登记职责、错误归一化和事件呈现，不新增一套并行调度器，不改变默认思考设置，不擅自更换供应商。视觉数值准确性要另设真实标注样本验收，不能混进“任务状态成功率”。

早间多轮报错必须独立重跑：普通聊天、输出方案、按编号执行、运行中追加素材、连续改图、取消/恢复、刷新页面与下一轮引用。每轮检查正文、稿件版本、原文哈希、素材、尺寸、权限、实际副作用次数及页面状态；测试快照不完整造成的错误不得归为产品缺陷。此前底层回归与单次供应商闭环不能替代重复完整链路的稳定性证据。

**本次结论是源码对照后的实现依据，不是这三个问题已经修复，也不是本项目或上游所有真实场景已通过验收。**

调研工具环境：Agent Reach 当前v1.5.0，更新检查提示v1.6.1，未自动更新。需要更新时可使用“帮我更新 Agent Reach：https://raw.githubusercontent.com/Panniantong/agent-reach/main/docs/update.md”；此工具更新与本项目业务修复无关。

[readme]: https://github.com/xai-org/grok-build/blob/2bdd1d6a6369de0e8c68132ea4539e9abd9e14a8/README.md
[sampling]: https://github.com/xai-org/grok-build/blob/2bdd1d6a6369de0e8c68132ea4539e9abd9e14a8/crates/codegen/xai-grok-shell/src/session/acp_session_impl/sampling_events.rs#L175
[length]: https://github.com/xai-org/grok-build/blob/2bdd1d6a6369de0e8c68132ea4539e9abd9e14a8/crates/codegen/xai-grok-shell/src/session/acp_session_impl/length_salvage.rs
[describe]: https://github.com/xai-org/grok-build/blob/2bdd1d6a6369de0e8c68132ea4539e9abd9e14a8/crates/codegen/xai-grok-shell/src/session/image_describe.rs
[read_image]: https://github.com/xai-org/grok-build/blob/2bdd1d6a6369de0e8c68132ea4539e9abd9e14a8/crates/codegen/xai-grok-tools/src/implementations/read_file/image.rs
[responses]: https://github.com/xai-org/grok-build/blob/2bdd1d6a6369de0e8c68132ea4539e9abd9e14a8/crates/codegen/xai-grok-sampler/src/stream/responses.rs#L438
[errors]: https://github.com/xai-org/grok-build/blob/2bdd1d6a6369de0e8c68132ea4539e9abd9e14a8/crates/codegen/xai-grok-sampling-types/src/error.rs
[retry]: https://github.com/xai-org/grok-build/blob/2bdd1d6a6369de0e8c68132ea4539e9abd9e14a8/crates/codegen/xai-grok-shell/src/session/acp_session_impl/sampler_turn.rs#L16
[sampling_tests]: https://github.com/xai-org/grok-build/blob/2bdd1d6a6369de0e8c68132ea4539e9abd9e14a8/crates/codegen/xai-grok-shell/src/session/acp_session_impl/sampling_events_tests.rs
[provider_error]: https://github.com/xai-org/grok-build/blob/2bdd1d6a6369de0e8c68132ea4539e9abd9e14a8/crates/codegen/xai-grok-sampling-types/src/provider_error.rs
[retry_tests]: https://github.com/xai-org/grok-build/blob/2bdd1d6a6369de0e8c68132ea4539e9abd9e14a8/crates/codegen/xai-grok-shell/src/session/acp_session_tests/transient_retry_tests.rs
[strip]: https://github.com/xai-org/grok-build/blob/2bdd1d6a6369de0e8c68132ea4539e9abd9e14a8/crates/codegen/xai-grok-shell/src/session/acp_session_impl/image_strip.rs
[image_budget]: https://github.com/xai-org/grok-build/blob/2bdd1d6a6369de0e8c68132ea4539e9abd9e14a8/crates/codegen/xai-chat-state/src/image_budget.rs


## 后续本地修复验证

本记录保留当时研究/抽样结论。后续实现、真实模型时延、再次真实供应商发现与修复、完整回归及未验收边界见 [Skill可靠性修复与早间多轮复测](TEST_Skill可靠性修复与早间多轮复测_20261122.md)。该后续记录不代表原20组全部端到端通过。
