# 显式Skill选择、自动发现、阶段协调与错误恢复：官方机制对照

日期：2026-10-31。范围：公开官方文档、Google Gemini CLI公开源码及当前任务树。结论属于研究与修改建议，未成为新的用户确认设计，未改运行代码、配置或权限，未执行付费模型/产图测试或部署。不推断各厂未公开的内部实现，也不宣称存在统一行业强制标准。

## 结论与重要区别

公开实现将显式调用、自动发现、完整指令加载、权限、领域执行及结果反馈分别处理。没有可靠证据支持“用户调用一个Skill，整个任务便自动禁止其它Skill”或“使用大厂机制就保证永不冲突”。建议本产品明确采用：有效用户手选确定当前方法意图，自动推荐只形成候选；仍需判断动作适用性、阶段及权限。它是适合本产品的策略，并非所有厂商都保证的统一优先级。

### 已核实的厂商行为

1. **OpenAI Codex**：支持显式指定与按description隐式匹配；渐进披露，先给名称/描述，使用时加载全文。agents/openai.yaml的allow_implicit_invocation=false保留显式调用。它证明调用通道可以分开控制，不证明多个不同Skill必有“手选绝对独占”仲裁。
来源：[OpenAI官方Skills文档](https://learn.chatgpt.com/docs/build-skills)。

2. **Anthropic Claude Code**：显式命令和模型自动调用可分别配置；disable-model-invocation控制自动调用，user-invocable控制菜单入口。该页还区分指令在上下文中持续存在与工具授权的本轮生命周期。allowed-tools是预授权而非排他白名单；不能把此字段理解成工具只能来自该列表。Claude Code、claude.ai上传/API的配置能力也有差别，不能把不同产品视为同一权限实现。
来源：[Claude Code官方Skills文档](https://code.claude.com/docs/en/skills)。

3. **Google Gemini CLI**：先发现元数据，通过activate_skill请求加载正文与资源；文档说明了激活确认。该工具由模型调用，与Codex/Claude显式命令界面并不相同。源码中的未知Skill处理向模型返回可理解的参数错误及可用名称；成功时把正文和目录作为工具结果返回，并登记资源目录访问。
来源：[Skills生命周期](https://geminicli.com/docs/cli/skills/)、[activate_skill工具](https://geminicli.com/docs/tools/activate-skill/)、[官方源码](https://github.com/google-gemini/gemini-cli/blob/main/packages/core/src/tools/activate-skill.ts)。本轮读取源blob SHA：17e0b84f2f3c1760e3137cc19bb9b16d1d5c2d4d，原始副本/private/tmp/skill-research-gemini-activate-skill.ts。源码仅证明该文件中的激活行为，不证明整套内部仲裁逻辑。

4. **专业Agent与固定工作流**：OpenAI公开区分manager调用专业Agent工具与handoff移交主导权；Google ADK支持按程序控制顺序、循环和并行的工作流。由此可借鉴“语义选择由模型提出，明确顺序与约束由宿主执行”，无需把每个Skill都改造成独立Agent。
来源：[OpenAI编排与交接](https://developers.openai.com/api/docs/guides/agents/orchestration)、[Google ADK工作流](https://adk.dev/agents/workflow-agents/)。

5. **错误反馈与安全重试**：Anthropic文档示范错误tool_result可继续交给模型补参数；Google ADK建议返回清晰status/error_message。它们支持模型纠错的反馈闭环，不保证权限/欠费/供应商故障都能修复。AWS明确讨论调用方请求标识、参数一致性、原子记录以及迟到请求；本次请求超时不证明未执行。参数纠正和副作用重发必须分开。
来源：[Claude工具错误处理](https://platform.claude.com/docs/en/agents-and-tools/tool-use/handle-tool-calls)、[Google ADK函数工具](https://adk.dev/tools-custom/function-tools/)、[AWS幂等重试](https://aws.amazon.com/builders-library/making-retries-safe-with-idempotent-APIs/)。

6. **上下文与暂停**：OpenAI运行文档强调选定一种主要会话状态策略，避免未经协调地混合本地重放与服务端状态造成重复；批准等待/中途取消应按原state续接。该机制支持保留本项目原Actor/CP，不构成重写上下文系统的理由。
来源：[OpenAI Agent运行与续接](https://developers.openai.com/api/docs/guides/agents/running-agents)。

## 与本项目实际代码对照

| 职责 | 当前可见实现 | 需要判断的具体差异 |
|---|---|---|
| 手选身份保护 | skills/runtime.py:349优先装入手选候选；:511禁止随意解除手选/固定绑定 | 保护绑定并不等于已实现所有冲突下的来源优先级裁决 |
| 同阶段职责竞争 | skills/coordination.py:63 owners_for按action/claim确定owner，:78重复owner抛冲突；Participant虽记录sources，owner决策未按sources自动选手选胜出 | 应核实候选冲突能否保留手选、跳过自动候选并继续，而非只返回调整选择；不能删除真实任务交付要求 |
| 方法与工具上限 | skills/resolver.py:147旧列表语义为严格求交；空集合可限制全部工具 | 与Claude Code预授权语义不同。保留旧协议及全局权限，使用已核验阶段方法交接，不能把旧空集合一律放宽 |
| 旧关键词工作流 | chat/execution_engine.py:234仅无bootstrap/新state时初始化旧电商流程；:393旧自动激活失败可在主循环前抛异常 | 新版入口已有绕开旧强制工作流的边界。必须分别验新建与旧恢复，不能把旧分支删掉，或认为新框架自动证明线上错误已消失 |
| 工具与计划门禁 | task_execution/dispatch.py与Controller核对计划依赖，原ToolPolicy继续授权 | 与模型选择+宿主约束方向相符，不应为模仿某SDK移除已有安全边界 |
| 纠错与恢复 | 原错误结构、TaskState纠错/轮次上限、failed_before_dispatch/uncertain、原生回执和Actor消费者 | 保留并验证错误是否真的回到模型；主循环外宿主异常不是普通工具错误，模型无法自行修复 |
| 上下文与成果 | 原CP、固定版本、已有图复用、事件续接已开发并有本地证据 | 沿用当前权威来源，避免重复注入Skill正文、重复创建用户回合和重新生成已有成果 |

上述代码位置以当前任务树为准，未对整个224文件差异作“无问题”保证。代码机制与真实模型在原样本上的行为仍须分开验证。

## 建议下一阶段采用的产品规则（未实施）

- 保留用户手选及固定版本。自动发现先生成候选，不因主图/详情页关键词直接取得另一方法的执行主导权。无手选时仅从实际可见、允许模型选择的方法中选择；简单任务可以不用Skill。
- 先判断本轮动作是规划、执行已存方案、执行最终提示词、编辑还是解释。手选方法也需判断是否适用于动作；选择方法不代表可以修改用户交付数量或新增要求。
- 同阶段竞争优先保留适用的用户方法，自动候选可不激活或等待后续阶段。互补方法按依赖协作；真正无法解释的用户目标冲突才补问。固定会话/组织/权限上限保持，不能自动覆盖以追求无中断。
- 把“阶段输出约束”和“权限上限”明确分开；原两已精确登记Skill沿现有核验/交接机制，未知Skill不能推断成可自动解除限制。
- 可恢复错误给模型清晰原因、缺失参数/前置条件及允许的恢复动作；权限、欠费、未知副作用分别等待恢复或核实。限制修正次数与总预算，已成功成果复用，未知原请求不盲目重发。
- 先做精确调用链差距评审，再决定修改。不要因厂商文档更复杂而另造调度器，也不要依据“已做编排”跳过原问题验收。

建议验收矩阵：手选原提示词Skill+主图/详情页词；无手选自动匹配；只要提示词；执行已有完整提示词；兼容方法组合；同阶段竞争；工具参数错误；受理丢确认；权限撤销；已有图复用和断线恢复。先使用现有本地证据，付费真实测试独立报价获确认后才执行。

## 研究边界

本轮没有调用付费模型/图片/视频/ERP工具，没有改Skill或执行代码、配置、依赖和生产服务。agent-reach仅用于公开网页阅读及官方仓库获取路径；无付费Exa调用。版本检查发现Agent Reach v1.6.0可用，本机v1.5.0，本轮未更新。当前方案仍以保留原ERP Agent、延期ERP新写、保留上下文/成果及整套通过后部署为约束。
