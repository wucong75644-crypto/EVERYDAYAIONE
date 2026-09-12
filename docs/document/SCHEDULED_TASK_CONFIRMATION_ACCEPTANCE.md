# ST-27：聊天创建任务必须先确认表单

## 范围、基准与根因

2026-09-12，任务分支 `codex/task/20260911215117-tool-unification-07`，基准/当前生产版本 `80cb123e505434557b718796fc5fbfc34147b482`；本批是未提交差异，尚未部署。

用户明确纠正创建顺序：先展示模型整理好的确认表单，用户点击确认以后，才开始生成任务。ST-26 将字段完整解释为可以直接提交，偏离此要求。`ChatTaskManager._create_from_request` 在 `not missing` 时直接调用 `_begin_request`，从而触发 ChangeSet 检查及自动创建，前端拿到的已经是提交结果。此前测试也把该行为当作正确预期，测试通过没有证明产品行为符合用户期望。

先建立每天、每周、单次三个完整请求的真实 ToolExecutor 入口测试：修改前均失败，证明确认前已经调用 `ScheduledTaskChangeSetService.begin`；不是前端丢失表单。

## 当前创建流程

用户输入 → 主模型一次整理 definition/recipient → 原 Registry/Policy/Dispatcher → ChatTaskManager 校验 → **始终返回可编辑 FormPart** → 用户核对名称、内容、时间和渠道 → 点击“确认创建” → 原 WS 表单状态及幂等检查 → 原提交服务/计划/授权/预检 → 实际创建与结果卡片。

- 字段完整时也没有 ChangeSet、规划或任务创建；缺项表单保留已提取内容。普通确认后检查通过生效，额外风险确认继续按原规则。
- 名称、频率、时间不再因为已经提取而隐藏；单次执行时间以北京时间显示并可修改。按频率显示周几/月几号/完整日期，相关字段必填。
- 取消只改变表单状态，不创建任务；已取消表单不能通过重提交创建。重复确认继续使用原 WS 状态锁和同一表单幂等键。
- ToolSpec 指导说明同步要求先确认，旧 config 仍派生该说明。旧 description-only 的直接提交模式创建也先出表单，避免另一个聊天创建分支绕过确认。
- 保留 ST-26 主模型一次整理、结构化字段、修改补丁、暂停/恢复/删除、通知和调度能力。面板原本就在点击创建后提交，本批不改变面板流程。
- 不修改 ERP/文件/沙盒/媒体、调度器、执行器、数据库 schema/RPC、权限或风险规则。现有持久化 FormPart/ChangeSet 载荷不变，无新权限入口。

## 逐项验收

| 编号 | 场景及预期 | 实际结果 | 状态 | 证据 |
|---|---|---|---|---|
| ST-27-01 | 每天/每周/单次完整请求先出表单，不规划、不提交 | 修复前 3 失败，修复后全部通过；名称、内容、时间、渠道可编辑 | 通过 | `test_scheduled_task_creation_confirmation.py`；[修改前](scheduled-task-confirmation-evidence/before.txt)、[确认链复验](scheduled-task-confirmation-evidence/confirmation.txt) |
| ST-27-02 | 用户确认后原服务按表单值创建，同一表单不重复创建 | 真实工具 → FormBlockResult → handle_form_submit → 原 ChangeSet/校验/预检/RPC 边界通过；同幂等键重复确认重放结果 | 通过 | `test_scheduled_task_structured_input.py::test_real_tool_changeset_authorization_commit_and_replay[create]` |
| ST-27-03 | 取消和已取消表单重提交不进入创建服务 | 原 WS 取消/状态冲突两组通过；前端 cancel 只发取消事件 | 通过 | `test_ws_form_submit.py::test_cancelled_creation_form_never_reaches_task_submission`、FormBlockChangeSet.test.tsx |
| ST-27-04 | 实际后端表单在前端完整展示，等待点击，重复点击不再发送 | 共用后端 fixture 验证名称和 08:00 可见，初始化无提交，确认/取消事件正确 | 通过 | `scheduled_task_creation_confirmation.json` 由后端测试精确对照；前端 FormBlockChangeSet.test.tsx 两组 |
| G-01 | 范围及明确契约修正 | 本批恢复用户明确的先确认顺序；只改聊天创建分支和派生说明、测试及文档 | 通过 | 实际 diff |
| G-02 | 原始问题及成功/取消/失败/重复边界 | 上述各项通过；完整和缺项均保留业务内容；既有授权/失败/回放仍通过 | 通过 | [后端日志](scheduled-task-confirmation-evidence/backend.txt) |
| G-03 | 相关回归通过，旧错误预期有替代验证 | 后端 1319 通过，前端 37 通过，TS/定向 ESLint 通过；另外 9 项最终确认链测试通过 | 通过 | [前端](scheduled-task-confirmation-evidence/frontend.txt)、[命令/指纹](scheduled-task-confirmation-evidence/validation.json) |
| G-04 / A-07-01～05 | 目录、旧参数和授权/执行链兼容 | 全量 Spec/目录、旧 helper、Planner/preflight、Policy、生产工具入口与回放对照通过；只有任务说明按此明确需求更新 | 通过 | test_tool_definitions_07.py 等；原 schema 字段/枚举/required 不变 |
| G-05 / A-07-06 | 架构、回退及当前状态可交接 | 本文和原 07 架构/交接同步；01～07 记录保留，08 不启动 | 通过 | 本文及 HANDOFF |
| ST-27-05 | 生产用户输入先显示完整表单，再确认创建 | 尚未部署本批，不冒充生产验证通过 | 未验证 | 待用户“提交部署”后的指定候选测试 |

## 测试环境与限制

使用任务工作树、Python 3.12、testing 配置、现有 Vitest 依赖。后端数据库/外部调用使用替身，未连接生产、未调用 Qwen、未发送通知。初始三个失败为真实创建分支越过确认的复现；旧测试的“完整请求直接提交”断言按用户纠正改为“确认前无提交，确认后原链执行”。前端测试使用既有无动画测试方式隔离 LazyMotion，不改变产品渲染代码或削弱可见性断言。

没有数据库/执行状态迁移，本批不重复运行此前已通过的临时 PostgreSQL 生命周期测试。前端生产代码未变，变更是后端 FormPart；已验证实际字段渲染、事件与 TS。ST-26 未完成的真实 Qwen 联调仍属未验证，本批未擅自豁免。

## 用户验证、回退与结论

部署后发：“创建一个定时任务。查询昨天的付款订单数按照平台划分，每天八点钟发给我看”。应先显示“确认创建定时任务”，可编辑业务内容、每日 08:00 和通知渠道。此时任务列表没有新增；取消仍无新增。重新发起后点击“确认创建”，才开始检查并最终显示创建成功；重复点击不能创建第二条任务。

本批确定性创建确认行为已完成本地技术复验，待提交部署/生产用户验收。07 整体验收仍未关闭，08 不启动。回退应用代码至 80cb123e 无数据迁移，但会恢复完整请求直接提交的已知问题；已有任务/表单/ChangeSet 使用原字段载荷。生产版本仍为 80cb123e，本次没有修改生产已创建任务。
