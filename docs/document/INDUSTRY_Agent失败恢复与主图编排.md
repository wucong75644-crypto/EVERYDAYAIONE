# Agent 失败恢复与主图编排调研

日期：2026-10-07。状态：调研结论与项目建议，尚未作为新的实施决策；本轮未继续发布代码或发起付费生产测试。

## 研究问题

专业子 Agent 失败后，主 Agent 应准确反馈错误，或在规定范围内修正调用与重试，不能自行替代专业流程编写主图提示词并生图。已有原文、真实图片身份、顺序、方案正文和积分边界均保持有效。

## 官方资料与适用边界

- [LangGraph：Thinking in LangGraph](https://docs.langchain.com/oss/python/langgraph/thinking-in-langgraph) 将临时错误、LLM 可修复错误、用户可修复错误和意外程序错误分开处理，并提供重试耗尽后的声明式恢复分支。这是框架官方实践，不是所有 Agent 必须采用的统一标准。
- [MCP 2025-11-25：Tools / Error Handling](https://modelcontextprotocol.io/specification/2025-11-25/server/tools#error-handling) 区分协议错误和工具执行错误；后者以 isError 和可操作的反馈返回，支持模型修正调用。规范没有规定本项目的 recovery 字段或重试次数，也不要求将内部工具改造成 MCP 服务。
- [AWS：Control and limit retry calls](https://docs.aws.amazon.com/wellarchitected/latest/reliability-pillar/rel_mitigate_interaction_failure_limit_retries.html) 建议按错误决定是否重试，设置退避、抖动及次数或时间上限，避免多层叠加重试和非幂等重复调用。这是可靠性最佳实践。
- [LangGraph：Persistence](https://docs.langchain.com/oss/python/langgraph/persistence) 说明 checkpoint 支持中断、失败恢复和会话延续。对应项目可复用现有 PostgreSQL 方案状态和 Actor checkpoint，无需因为本问题新增 LangGraph 依赖。

## 推荐职责划分（待实施设计确认）

1. **子 Agent / 服务**：保存阶段结果；返回统一结构的成功或错误回执。错误包含稳定 code、失败阶段、可恢复性、允许的下一步、方案标识和安全的用户提示。原始供应商错误不直接成为控制指令。
2. **主图 Skill**：规定主 Agent 按回执分支；只能执行允许的修正、续跑、提问或错误反馈。不得重新解释为“缺商品信息”，不得自行代写执行稿、改风格、减张、换图、重排图片或更换模型与密钥。
3. **程序编排与工具边界**：控制状态转换、调用权限和共享重试预算。主图任务识别后绑定流程状态；重试、跨轮继续仍沿用该任务的规则。只有有效 ready 方案可通过 plan_source 提交图片。普通图片任务和用户明确改变任务的请求应保留各自入口。

| 失败类型 | 推荐处理者与动作 |
| --- | --- |
| 临时网络故障、限流 | 服务在同一失败步骤内有限重试；尊重 Retry-After、退避和剩余预算。 |
| 主 Agent 的工具参数格式错误 | 返回具体字段、要求和允许调整范围；主 Agent 修正后重调同一工具，不改用户意图和真实素材身份。 |
| 子 Agent 输出结构或编号错误、completed 空正文 | 子 Agent 根据具体验证反馈重写失败阶段；主 Agent 不复制或修补专业正文。 |
| 商品事实或规格确实缺失 | 返回 needs_input 与具体问题；主 Agent 提问，保存等待状态。 |
| 密钥、权限、余额、程序或数据库错误 | 准确反馈并停止；已授权且预先配置的恢复分支才可执行，不能临时创造替代方案。 |
| 生图是否已提交不明确 | 查既有任务及供应商回执并协调状态；确认可安全重试前不重新提交，防止重复出图与扣费。 |

重试由统一策略计数。建议每个失败步骤首次调用加最多两次自动重试，并受时间和积分预算限制；这个具体上限是项目建议。主 Agent、阶段修复和 HTTP 层不能各自重新计算一套额度。用户明确重试与系统自动重试须区分。

恢复优先复用已验证阶段；例如阶段三失败时，在原文、素材版本、顺序和上游有效性再次核验后，从阶段三继续。数据变更使上游失效时重跑受影响阶段，不无条件复用旧结果。

## 当前代码事实

- `examples/skills/catalog/platform/ecommerce-main-images/v2/SKILL.md` 已禁止 error 后自行代写与生图，但将服务 error 一律处理为停止，没有按类别描述可恢复动作。
- `services/agent/image/ecommerce_planner/service.py` 的阶段异常主要回传安全摘要、异常类型、plan_id 和 stop_workflow，缺少主 Agent 可执行的统一恢复契约。
- 同一服务的 continue_plan_id 当前仅接受 needs_input / insufficient，failed 方案不能按该入口跨轮续跑。保存了阶段输出不等于已实现完整的失败恢复。
- `services/handlers/image_handler.py` 的保护依赖本轮已激活主图 Skill 或本轮已有方案；未激活的新轮次可以绕过。生产回归已观察到无 active Skill、无新方案而直接接受普通 generate_image 的情况，不能宣布整链已通过。

因此改动范围应同时覆盖入口绑定、错误回执、Skill 分支、失败恢复与生图状态校验。仅追加 Skill 文案或将全部 error 硬停止，均不足以完整满足目标。

## 后续验证重点

用可重复故障验证：鉴权失败准确反馈；临时故障在上限内恢复；错误参数可修正但不改变原文与素材；第三阶段失败不重付前两阶段；跨轮继续不跳过 Skill；未 ready 无法生图；提交状态不明不产生第二个付费任务。
