# 工具统一 08：F08-01 / F08-02 修复复验

日期：2026-09-13。基准 `41732e4f352809f000e34c28789eb71a07000618`，任务分支 `codex/task/20260913102153-tool-unification-08-fixes`。R1 确认修复提交 `f5017b29`；R2 与本记录共同组成最终本地修复候选，完整 SHA 由提交后的交付回执和仓库外整体验收报告记录。这里没有用基准冒充修复版本。

**两个已确认 Bug 的修复技术验证通过；工具统一整体验收仍未通过，U08-01 真实模型新协议联调未验证。用户生产验收未完成，未部署、未合并 main、未受控关闭。**

## 1. 范围与实际接口

F08-01 归属板块 04：`api/routes/ws.py` 只接受字面布尔 approved；所有网络确认走认证 actor 与任务/会话归属校验。`WebSocketManager.wait_for_confirm` 增加 actor_user_id，服务端保存完整绑定和本地 deadline；`confirmation_scope` 只从合法本地绑定补齐旧格式缺失字段，不能因省略 scope 关闭校验。非 Actor 任务也检查会话和 running 状态；Actor 保留原持久控制事件。`resolve_confirm` 严格比较绑定，首个有效响应胜出，非法响应不消耗合法等待，取消/超时后不唤醒旧等待器。Chat 普通与 Actor 分支以及旧 ToolLoop 确认调用点均传 actor；ToolLoop 先注册等待器再发布确认请求，并在失败时清理。

当前前端已发送 task_id、conversation_id、布尔 approved，不增加 API/WS 字段。不完整旧响应只有当前进程存在完整服务端绑定且认证 actor 匹配时才补齐；跨进程无本地绑定时必须提供完整 scope 并经过 Actor 持久归属验证。无绑定的内部 manager API 仍可用于旧进程内调用，WS 不允许用它获得批准。审批后现有 Policy 继续复核权限和资源版本。

F08-02 归属板块 05 主责、06 配合：`ToolResult.artifact_source` 从原调用身份或已恢复的 audit.origin 提取来源；`ToolLoopExecutor._register_result_files` 在当前输出容器内登记一次，`run()` 与产物列表共同重置。`ToolResultCache.source_id` 给无历史来源的旧缓存条目提供进程内稳定标识，过期/替换产生新来源，保留原 get/put 返回契约。`ToolRuntime` 只在旧缓存结果适配处携带该标识，不伪造历史 audit。`encode_result` 再编码复用结果时保留原来源及参数 hash，v1 格式和旧兼容壳不变。

同一来源的模型响应、模型图像投影和每次消费的审计仍正常执行，仅不重复追加最终产物。新真实调用的同名文件保留；新输出容器第一次消费缓存/回放仍展示。未改 ERP/文件/沙盒/媒体 Handler、收费结算、code_execute 缓存资格、TABLE/表单或模型循环规则，无新依赖/迁移。

## 2. 逐项复验与问题闭环

| 问题/场景 | 修复前证据 | 修复后实际结果 | 状态 |
|---|---|---|---|
| F08-01：字符串 false、另一 actor 持有有效 ID 且省略 scope | 两场景 Handler 各 1 次，预期 0；数字 1 也错误执行 | 三场景均 Handler=0、执行账本 begin=0、cache get=0 | 通过 |
| 旧格式兼容与归属检查 | 合法旧格式绕过归属适配器 | 服务端补齐后检查真实 task 查询条件；错误 owner/conversation、已结束任务、DB 故障均不执行 | 通过 |
| 审批一次性与失败 | 旧实现可覆盖内存决议 | 首响应胜出，同值重试幂等，冲突响应不能翻转；缺/错绑定及非 bool 不消耗合法等待 | 通过 |
| Actor 分进程审批 | 保留原路径，不新增审批存储 | 无本地等待器时，完整 scope+认证身份才能写控制事件；批准/拒绝通过原持久消费返回；缺 scope/错误 actor/失败不批准 | 通过 |
| F08-02：Loop 及真实 ScheduledTaskAgent 两轮复用 | Handler=1、第二轮新增 token=0，文件/图片各出现两次，最终 4 blocks | Handler=1、第二轮新增 token=0、最终恰好 2 blocks；两轮模型消息及两次消费审计保留 | 通过 |
| 首次展示与合法新产物 | 原测试未覆盖 | 新容器首次消费缓存仍展示；不同执行同名/同 URL 均保留；旧缓存命中稳定、过期后新执行不吞产物 | 通过 |
| 来源、旧载荷与回放 | 原测试未覆盖再编码来源 | 文件/FileRef、图片、TABLE、失败卡片再编码回放来源稳定；旧 writer/reader 契约保留，失败状态不变成功 | 通过 |
| 展示故障、取消与 uncertain | 既有恢复测试 | 产物收集失败不提前登记；原完成/审计/展示异常、取消和 uncertain 防重做回归通过 | 通过 |
| U08-01 / ST-26 新模型 definition/recipient | 最终新协议缺六个真实模型合成样本证据 | 本次未调用收费模型或生产业务，仍缺真实模型证据；不是已确认第三个 Bug | 未验证 |

正式新增回归为 [确认边界 54 项](../../backend/tests/test_tool_confirmation_boundary_08.py) 与 [产物复用 14 项](../../backend/tests/test_tool_artifact_reuse_08.py)。前者贯通真实 WS、归属查询、等待器、Runtime/Policy/Dispatcher，数据库和业务 IO 使用替身；后者含真实 ScheduledTaskAgent.execute。原测试只修改两处直接向等待器注入响应的测试调用，以显式传入真实 actor 并保留 timeout 包装的全部参数，未删除/放宽正确断言。

独立只读审查分别核对确认信任边界和产物来源/回放，未发现高置信阻塞问题。证据边界：本次强化本地审批 deadline/取消；跨进程 Actor 继续使用既有持久事件和 Runtime 生命周期保护，**没有新增持久审批 TTL**，不宣称已经实现该机制或做过生产崩溃时序演练。

## 3. 当前执行记录

执行目录为本任务工作树。使用已有 Python 3.12 虚拟环境，干净测试环境：APP_ENV=testing，数据库指向 `127.0.0.1:1/tool_test`，Redis 指向本地端口 1，测试 JWT 占位，v1 writer。没有读取项目生产 .env。临时 PostgreSQL 使用独立目录和 Unix socket，仅测试合成数据，用后停止；没有连接现有数据库。

所有命令的 cwd、argv、环境及完整输出保存在 [08 修复证据目录](tool-unification-evidence/08-fixes/)。命令均为项目既有 Python 执行 `-m pytest <对应文件> -o addopts= -q --tb=short -ra --junitxml=…`；每组 `*-command.json` 列出精确测试文件，不能用总数量代替各场景证据。

| 执行组 | 结果 | 命令/日志 |
|---|---|---|
| 修复前新反例 | 6 failed / 8 passed，包含原 4 条反例、数字误批准及旧格式漏校验 | [命令](tool-unification-evidence/08-fixes/before-command.json)、[日志](tool-unification-evidence/08-fixes/before.log) |
| 定义/目录/Planner/参数 | 444 passed | [命令](tool-unification-evidence/08-fixes/group-contracts-command.json)、[日志](tool-unification-evidence/08-fixes/group-contracts.log) |
| Result/持久化/入口/审计/循环 | 472 passed | [命令](tool-unification-evidence/08-fixes/group-integration-command.json)、[日志](tool-unification-evidence/08-fixes/group-integration.log) |
| Registry/Policy/ToolExecutor/文件/媒体/产物/恢复 | 1557 passed / 2 skipped | [命令](tool-unification-evidence/08-fixes/group-regression-command.json)、[日志](tool-unification-evidence/08-fixes/group-regression.log) |
| ERP 原 Handler | 11 passed | [命令](tool-unification-evidence/08-fixes/group-erp-command.json)、[日志](tool-unification-evidence/08-fixes/group-erp.log) |
| 附件/作用域/定时任务/确认/协议 | 588 passed | [命令](tool-unification-evidence/08-fixes/group-expanded-command.json)、[日志](tool-unification-evidence/08-fixes/group-expanded.log) |
| 模型网关/上下文/文件系统等受影响周边 | 452 passed / 3 skipped / 4 xfailed | [命令](tool-unification-evidence/08-fixes/group-affected-extra-command.json)、[日志](tool-unification-evidence/08-fixes/group-affected-extra.log) |
| 新增 68 项 + WS 周边 | 132 passed，无跳过 | [命令](tool-unification-evidence/08-fixes/group-ws-boundary-command.json)、[日志](tool-unification-evidence/08-fixes/group-ws-boundary.log) |
| 临时 PG 与原 uncertain/replay 测试体 | 27 passed / 1 原 opt-in 节点 skipped | [命令](tool-unification-evidence/08-fixes/postgres-command.json)、[日志](tool-unification-evidence/08-fixes/postgres.log) |
| 前端产物/表单/消息协议/定时展示 | 153 passed；TypeScript exit 0 | [命令](tool-unification-evidence/08-fixes/frontend-command.json)、[日志](tool-unification-evidence/08-fixes/frontend.log)、[类型检查](tool-unification-evidence/08-fixes/frontend-types-command.json) |
| 全 35 Handler × 3 入口 × 允许/拒绝 | 210/210；另有 Chat/Loop 两条真实读重叠与写屏障轨迹，确认 4 场景通过 | [执行命令](tool-unification-evidence/08-fixes/entry-probe-command.json)、[原始记录](tool-unification-evidence/08-fixes/entry-policy-probe-results.json) |
| 新进程 write v1 → reader/write 0 回退读取 | success/error 各两进程：写 Handler=1，回放 Handler=0、额外计费=0、历史 token=33，产物/错误/重试元数据保留 | [进程记录](tool-unification-evidence/08-fixes/process-recovery.json) |

正式后端分组合计 **3683 passed、6 skipped、4 xfailed、无剩余意外失败**；不重复累加中间 310/68 次定向复验。6 个 skipped 分别为 2 个 wqy 字体环境场景、3 个已被 PromptBuilder 替代的 V1 gather 场景和 1 个 opt-in Actor 节点；最后一个原测试体已经通过隔离 PG 的 `test_existing_actor_uncertain_and_replay_contract_on_disposable_database` 实际执行。4 个 xfailed 是原附件工具描述长度/图片模态提示文案基线。本次未更改这些测试或豁免其行为，旧字体/V1 场景不标为执行通过。既有 pytest-env 未识别、依赖弃用等警告保留。

首次把原独立分组全部拼进一个进程时收集失败：`test_erp_tool_mixin_unit.py` 的模块级 `sys.modules` 占位污染后续 AsyncOrgConfigResolver 导入。该测试文件与基准相同，按原有进程隔离分组运行后所有项通过；保留 [失败命令](tool-unification-evidence/08-fixes/affected-command.json) 和 [原错误](tool-unification-evidence/08-fixes/affected.log)，没有修改业务代码或跳过失败文件来掩盖它。

## 4. 原验收项恢复与限制

| 原始要求/编号 | 本次增量证据 | 判定 |
|---|---|---|
| 新旧工具同策略，A-04-01 / A-08-02 | 六层和所有原 Handler 不变；修复网络确认边界；210 条三入口矩阵 + 生产集成回归 | 通过 |
| 危险确认失败拒绝，A-04-05 / A-08-03 | 54 条确认边界、原五类确认结果三入口矩阵、权限/资源再校验、读写轨迹 | 通过 |
| 产物不重复，A-05-03 / A-05-05 | 14 条产物来源与真实定时交付回归，模型回填/消费审计不省略 | 通过 |
| 缓存真实状态/计费，A-06-03 | 缓存错误仍为错误，复用新增计费为 0，原 token 保留 | 通过 |
| 旧载荷/恢复/取消，A-08-05 | 原持久化和回退测试、4 个新进程记录、临时 PG；无新格式/迁移 | 通过 |
| 完整模型协议与必需证据，A-08-04 / A-08-06 | 两 Bug 反例已通过，当前自动测试完成；U08-01 真实模型样本尚缺 | 未通过 |
| 问题闭环，A-08-07 | F08-01/F08-02 已有修复与复验；U08-01 尚未补证，且最终生产版本未用户验收 | 未通过 |

01–07 的原前置代码/验收记录均继承自上述 main 基准。未受本次差异影响的完整差异/需求到 A-01～A-08 映射，沿用 08 仓库外整体验收报告的逐项来源核验；最终报告顶部记录本轮新候选及状态，历史 main 反例结论作为修复前证据保留。本任务不把本地修复提交描述成已进入 main。

## 5. 最小用户验证单与回退

验证前记录最终部署 SHA、模型/配置、时间、操作者和测试资源；当前均待用户在明确部署后执行。

1. 代表只读查询正常返回，与此前权限范围一致。
2. 在指定测试账号/群作用域中请求无权资源，确认明确拒绝，目标不变。
3. 在可丢弃测试目标上验证危险确认拒绝/超时；合法批准执行需用户明确授权具体资源，预期只执行一次。
4. 定时任务两轮复用同一个合成报告结果，最终文件和图片各一份、均可打开；新执行的同名文件仍保留。
5. 指定失败样本保留错误和重试提示，不显示成功；恢复已完成调用不重做、不重复计费。
6. 只读定时任务按固化授权运行；暂停/恢复行为不因修复改变。
7. 获得真实模型调用授权后，按 ST-26 六个既定合成样本补 definition/recipient、缺项表单、按 ID 更新等原始 tool calls 证据；业务 Handler 陷阱阻止真实创建、发送、ERP 写入和删除。

回退无数据库迁移，v1 新旧 reader 和兼容外壳保留。两个本地提交可按职责撤回，但撤回会重新打开相应验收问题。生产确认异常时应保持拒绝执行，不得把恢复无绑定批准作为合格回退。现有 254/255 调度迁移的排空/回退约束不因本修复失效，不能直接回退到不支持现有持久化的旧版本。

只有全部必需技术项通过后才能写“技术通过，待用户验收”；最终确定版本经用户明确验收且受控关闭完成后，才能写“整体已闭环”。本记录不触发推送、部署、合并或 `accept-and-close`。
