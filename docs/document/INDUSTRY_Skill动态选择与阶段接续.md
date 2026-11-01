# Skill 动态选择与阶段接续：源码调查

日期：2026-10-29。性质：研究结论与建议，不代表新方案已实施或生产验收通过。

## 问题与当前事实

目标是让主 Agent 理解任务，选择适用方法，完成规划并按已有用户授权接续执行；避免把方法不匹配升级为整任务终止，也避免生图成功掩盖规划遗漏。

当前任务代码基线为 `6fce2e641760b8eff62adad512f4096fb1502fed`。已部署的统一协调机制有所有权、权限交集、持久化和回放保护，但尚无完整的阶段成果交接。

- `backend/services/agent/image/ecommerce_planner/workflow.py` 的 `coordinate` 只为 `image.planning/ecommerce.inline` 切换路径；`image.prompting/image.prompt` 不在该分支。
- `backend/services/handlers/chat/execution_engine.py` 在主模型执行循环前，对 main_images 强制激活服务端主图 Skill，失败抛出绑定错误。
- 生图工具尚未以完整策划成果就绪为前置条件；`ActionContext.stage` 存在不等于阶段完成状态已经实现。
- 实测 reference-image-prompts 任务 `e7b0303f-7a89-52ee-b0ee-974e052c1874` 因能力不匹配终止；旧完整融合 Skill 任务 `b66e8705-4362-5daa-87a4-3fb3d6abd03d` 完成五张图，却没有交付要求的完整规划。

## 调查来源与可复现版本

仅静态阅读，没有运行外部源码或安装依赖，没有调用付费生图。

| 来源 | 固定版本、位置 | 来源限制 |
|---|---|---|
| Grok Build 官方源码 | `xai-org/grok-build`，`2bdd1d6a6369de0e8c68132ea4539e9abd9e14a8`，Apache-2.0 | CLI/TUI 与 runtime，不等于 grok.com 完整服务 |
| OpenCode 官方源码 | `anomalyco/opencode`，`726cf096396d5014c3b1845e231cc01efe0421ee`，MIT | 本次 dev 快照；与旧版路径、交互不同 |
| Deep Agents 官方源码 | `langchain-ai/deepagents`，`89dc4ebc4523b543fd5aef1fd4f034f6802947d6`，MIT | 框架能力不等于应用已正确配置或有业务完成校验 |
| 本地 Claude 参考树 | `/Users/wucong/claude项目参考/claude-code-source`，`430502e7bbe7e502d08619722ebc869b92bdd826` | 第三方修复版，不能宣称是当前官方开源实现；不复制来源和授权不明代码 |

本地 Claude 目录位于主仓库外。本次检索主仓库和任务树（包括忽略文件）未找到其中完整源码；项目旧设计文档记录了该外部参考路径。

## 1. Grok Build：动态发现与显式计划状态

源码：

- [Skill 发现、来源优先级与禁用过滤](https://github.com/xai-org/grok-build/blob/2bdd1d6a6369de0e8c68132ea4539e9abd9e14a8/crates/codegen/xai-grok-agent/src/prompt/skills.rs)
- [正文加载与传递](https://github.com/xai-org/grok-build/blob/2bdd1d6a6369de0e8c68132ea4539e9abd9e14a8/crates/codegen/xai-grok-tools/src/implementations/skills/skill.rs)
- [计划状态与恢复快照](https://github.com/xai-org/grok-build/blob/2bdd1d6a6369de0e8c68132ea4539e9abd9e14a8/crates/codegen/xai-grok-shell/src/session/plan_mode.rs)
- [退出计划工具](https://github.com/xai-org/grok-build/blob/2bdd1d6a6369de0e8c68132ea4539e9abd9e14a8/crates/codegen/xai-grok-tools/src/implementations/grok_build/exit_plan_mode/mod.rs)
- [阶段门禁、反馈与批次边界](https://github.com/xai-org/grok-build/blob/2bdd1d6a6369de0e8c68132ea4539e9abd9e14a8/crates/codegen/xai-grok-shell/src/session/acp_session_impl/tool_calls.rs#L145)
- [同批编辑与计划退出一致性测试](https://github.com/xai-org/grok-build/blob/2bdd1d6a6369de0e8c68132ea4539e9abd9e14a8/crates/codegen/xai-grok-shell/src/session/acp_session_tests/plan_exit_batch_barrier_tests.rs)

观察：发现目录支持多个来源，同名覆盖是来源解析规则，不是业务方法冲突仲裁。正文可按调用加载，也有显式选择后在请求组装时预加载的路径，不能概括成所有 Skill 都必须额外调用一次工具。

PlanModeTracker 有 Inactive/Pending/Active/ExitPending 和持久状态；编辑入口检查计划阶段允许的目标。退出计划读取真实计划文件，避免模型参数里的计划与交付文件不同。退出调用被安排在同批其他操作之后；测试验证前后两种调用顺序都得到新计划内容。

取消、修改反馈可写入工具结果并返回 ToolLoop::Continue；不是所有不接受都终止任务。API/权限等不同失败另有路径和重试边界，不代表任何失败都应重试。

适用：借鉴阶段状态、真实成果交接、同批边界、反馈接续。不照搬 CLI 文件路径、客户端审批和 Rust Actor；我们已有 PostgreSQL checkpoint 与 Actor。

限制：退出工具包含空或缺失计划文件的处理，部分分支可继续。该机制不是“计划内容完整、正确”的通用证明，更不能证明电商逐图提示词已满足要求。

## 2. OpenCode：Skill 加载与 Agent 权限分离

- [Skill 工具](https://github.com/anomalyco/opencode/blob/726cf096396d5014c3b1845e231cc01efe0421ee/packages/core/src/tool/plugin/skill.ts#L21)：根据可用 Skill ID 加载正文，调用前独立执行 permission.assert，再把正文返回会话。
- [Plan 插件](https://github.com/anomalyco/opencode/blob/726cf096396d5014c3b1845e231cc01efe0421ee/packages/core/src/plugin/plan.ts#L12)：Plan Agent 禁止普通文件编辑，只允许指定计划目录；进入/离开有上下文提醒。

适用：方法知识、执行身份与工具权限属于不同职责；不能把一个文字方法不具备生图权限，直接等同于用户整个任务没有生成授权。

不适用：本次版本明确要求用户切换 Agent 才离开 Plan，不支持把它当作自动规划后执行的直接范本。我们已有生成授权时应自动交接，无须新增重复确认。

## 3. Deep Agents：框架组合与按需工具披露

- [Agent 组装入口](https://github.com/langchain-ai/deepagents/blob/89dc4ebc4523b543fd5aef1fd4f034f6802947d6/libs/deepagents/deepagents/graph.py)：组合 middleware，支持 checkpointer、条件 interrupt 和自定义工具。
- [SkillsMiddleware](https://github.com/langchain-ai/deepagents/blob/89dc4ebc4523b543fd5aef1fd4f034f6802947d6/libs/deepagents/deepagents/middleware/skills.py#L873)：先公开目录，再按需 read_file。pinned_skills 可显式注入正文快照，两种入口在同一机制内工作。
- [Skill 工具披露](https://github.com/langchain-ai/deepagents/blob/89dc4ebc4523b543fd5aef1fd4f034f6802947d6/libs/deepagents/deepagents/middleware/skills.py#L970)：include_tools 可在相关 Skill 被读取或 pinned 后披露；状态记录最近一次模型实际看到的工具，调用门禁据此检查。

适用：用户明确选择与模型自行发现可以共存；工具列表与调用校验需要一致，旧披露状态不能无限生效。披露工具不等于授予业务权限，也不等于规划已完成。

不建议仅因框架有这些模块就整体替换当前 runtime。其 checkpoint、消息、工具接口与现有 Actor、组织权限、计费幂等和回放协议均需适配；当前没有证明迁移成本低于补齐现有机制。

## 4. 本地 Claude 参考树：仅作为结构观察

- `src/tools/SkillTool/prompt.ts:20`：目录有预算，正文调用时展开。
- `src/tools/SkillTool/SkillTool.ts:580`：校验后执行，可 inline 展开，也有 context=fork 分支；fork 不是所有任务必需机制。
- `src/query.ts:307`：多轮 queryLoop，工具结果进入后续上下文。
- `src/query.ts:1267`：停止 hook 可返回阻塞信息并继续；同时保留终止、API 错误和循环保护路径。

说明：这些是该参考树可读到的实现，不证明当前 Claude 官方产品完全相同，也不证明其自动防止一切互斥 Skill。仅独立借鉴公开可验证的工程思路。

## 对当前项目的建议

结论是机制可借鉴，不是某框架已经提供能直接套用的完整业务修复。

1. 主 Agent 结合最新目标、已选方法、现有成果判断当前阶段和下一步；自动发现不抢占用户指定的方法职责。
2. 协调层负责同一阶段的所有权、权限和激活结果；不把未知方法或可修正的选择失败一律变成主模型之前的异常终止。
3. 阶段执行层保存成果、验证前置条件、完成交接；需要规划的任务在成果就绪前不能付费生图。简单编辑、已有提示词执行和解释任务不强制重做整套规划。
4. 产品事实、视觉定位、逐图提示词、引用素材与规格是电商方法的业务成果，不能由普通 TODO、Skill 已激活或模型自称完成代替。开放文本质量仍需评估，结构校验不能证明视觉正确。
5. 提示词方法完成后，权限受限的规划阶段结束；在既有用户授权和全局权限约束内选择执行方法。不能删除锁定方法来绕过限制，也不能让 Skill 自行授予权限。
6. 阶段状态与成果引用沿用现有 Actor/checkpoint 单一写入；生成沿用现有计费幂等和请求接收协议，旧已接收任务保持原冻结状态与回放语义。
7. 可恢复失败向模型返回具体原因、可用选择与当前进度；真实缺素材或授权才等待用户，安全拒绝、取消、不可恢复存储错误仍应停止。限制修正次数，防止无限切换与重复收费。

这三个职责是现有运行时内的逻辑分工，不意味着新增三个服务或重新搭建整套框架。最终能否满足目标，必须以原两个 Skill 的实际端到端任务和恢复测试确认。

## 验证边界

后续至少覆盖：手选提示词+已授权生成先规划后接续；仅提示词不生图；自动选择完整策划不漏阶段；已有提示词直接执行不重规划；同阶段互斥方法有可恢复反馈且不双主控；重启/重试继续正确成果版本、不重复付费；原服务端规划、手选/固定/自动来源、素材角色、编辑与解释保持原功能。

不能承诺“今后所有 Skill 永远没有问题”。合理目标是让冲突有确定边界、可恢复路径和可观测证据，并把业务完整性纳入验收。
