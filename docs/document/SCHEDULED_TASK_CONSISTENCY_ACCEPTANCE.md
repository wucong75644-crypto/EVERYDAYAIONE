# 定时任务输入与状态一致性修复验收

## 1. 范围与版本

2026-09-12，用户复查后明确授权修复五项问题。任务仍为 `codex/task/20260911215117-tool-unification-07`，基准提交 `eeccf3bd9485f1e91d253fdacc2c3828700efdd6`；候选为该提交上的未提交差异，包含先前 [执行内容整理](SCHEDULED_TASK_CONTENT_EXTRACTION_ACCEPTANCE.md)。本批文件见 [源码指纹](scheduled-task-consistency-evidence/source-checks.json)。未部署、未合并、未关闭用户验收，不进入板块 08。

只修改任务请求输入、字段一致性检查和现有 ChangeSet 卡片状态恢复。Registry/Policy/Dispatcher、工具 schema/别名、权限快照、Planner、任务提交事务、扫描/领取/执行/暂停/恢复和通知内核保持原实现，无数据库迁移。

## 2. 问题、根因与复验

| 编号 | 复查事实 / 根因 | 修复后行为 | 本地证据 |
|---|---|---|---|
| ST-14 | 创建面板连续解析 A/B 请求，仅覆盖非空字段，B 缺指令时提交旧 A 指令 | 新建请求完整替换草稿；清空缺失指令、频率、时间、周/月日期；缺项不能提交。解析期间不能提交上一份草稿；编辑已有任务仍保持补丁语义 | TaskForm 组件实际操作与提交 DTO 断言 |
| ST-15 | parse 返回 recipient，面板仅显示提示；点击创建仍发送到默认自己 | 使用现有权限下联系人/群选项做唯一精确匹配；重复、无权限或未找到时保留原收件描述并阻止提交。用户明确选择可用对象后继续，后端照常授权 | 同名群、无权限、未知对象、明确改选自己、销售群 DTO 回归 |
| ST-16 | 工具入口用最后一条用户消息覆盖创建描述，丢掉前面的业务要求 | 从当前调用的消息中恢复紧邻创建请求的连续用户/澄清问答；仅合并用户原文，不采用模型改写。新建请求、取消、工具执行/表单产出等边界停止追溯；不维护全局草稿 | 真实 ToolExecutor → ChatTaskManager → strict parser → `_begin_request`；A店/付款口径/平台分组和后补时间一起保留；新请求/取消/工具边界与调用隔离 |
| ST-17 | 时间校验只检查引用在原文及 HH:MM 形状，“9点”引用可支持错误的 08:00 | 严格创建/更新解析核验明确时钟值，包含中文数字、上午/下午、分钟/半/刻；短引用不能省略相邻时段/分钟。单次任务核验时区、时钟和显式年月日，常见明确频率核验一致性。矛盾字段移出 changes 并加入 missing_fields，保留可识别的业务内容 | 故障注入 9→8、9点半→9、下午9→上午9、错误日期/时区/频率、创建提交阻断及正常值 |
| ST-18 | 后台完成依赖单次 WS 通知；卡片未收到事件就不再读真实状态 | 处理中每 5 秒补查；上线/窗口重新聚焦时补查未结束卡片；待人工确认不持续轮询，终态停止，卸载清理。请求去重、序列和修订检查防止晚到响应覆盖新卡片；暂时失败保留已有展示 | 无 WS 完成事件、一次离线失败后恢复、终态停止、online、卸载、切换卡片后旧响应晚到 |

ST-14～17 在修复前通过只读定向探针复现（ST-17 是模拟错误模型输出，不宣称观察到生产模型错误）。ST-18 首先由代码路径确认缺少补查，本批用实际组件计时器/事件测试验证恢复；尚未做生产浏览器真实断网复验。

上下文恢复仅面向紧邻且可识别的当前创建澄清，不任意拼接历史会话。跨话题、已经产出工具表单后的输入继续使用现有表单/新请求路径。时间核验是现有解析器的一致性检查，不另建自然语言调度器；无法识别的时钟表达留给现有补全表单，不外推任意语言/日期句式均支持。

## 3. 共同约束与技术验收

| 编号 | 证据 / 结果 | 状态 |
|---|---|---|
| G-01 | 同任务工作树增量修改；无 ERP、文件/沙盒、媒体结算、任务执行内核重写；本轮无发布/生产写入 | 通过 |
| G-02 | 五项问题逐项映射 ST-14～18；原内容整理仍有效；明确目标不能默默退回自己；新旧创建/编辑调用约定保留 | 本地通过 |
| G-03 | 后端 1005 passed + 独立临时 PG 27 passed（合计 1032，无重复计数）；前端 141 passed；TS/构建通过；6 条保存的真实模型输出重放结果一致 | 本地通过；日志见下 |
| G-04 | ToolExecutor/Policy/Planner、旧 helper、ChangeSet、任务路由、结果及回放回归；上下文辅助模块只有源文纯函数，不依赖 Registry，不产生全局请求状态 | 本地通过 |
| G-05 | 本记录、技术架构、问题清单、共同交接和前一批验收相互关联；代码前置/未部署状态明确 | 通过 |
| U-PROD | 新候选的生产浏览器、真实目标通知、实际到点执行和断网复验 | 未验证，用户验收不关闭 |

[后端 1005](scheduled-task-consistency-evidence/backend-final.txt)、[临时 PostgreSQL 27](scheduled-task-consistency-evidence/postgres-retest.txt)、[前端 141](scheduled-task-consistency-evidence/frontend-final.txt)、[类型检查与构建](scheduled-task-consistency-evidence/build-final.txt)、[模型输出重放](scheduled-task-consistency-evidence/model-output-replay.json)。前端构建存在原有大包体积提示，构建退出 0。

初始扩大回归有 27 项 fixture setup error：沙箱内 initdb 无法启动临时 PG；没有测试断言失败。使用受控权限运行原有 socket-only 临时数据库 fixture 后，同 27 项全部通过；使用合成数据并自动销毁，不连接业务数据库。[初始错误记录](scheduled-task-consistency-evidence/backend-initial-environment-errors.txt) 保留。最初本地命令还遇到执行目录和 Settings 缺测试环境值的收集错误，修正目录并提供显式测试值后正常收集，不将这些尝试计为通过。

本轮没有重新请求线上模型；6 条是先前真实模型原始输出在新代码上的离线重放，不作为新的真实模型调用或生产 E2E 证据。

## 4. 验证入口

后端在 `backend` 运行 Python 3.12 / pytest。非 PG 文件完整清单位于 backend-final.txt 首行，覆盖 `test_scheduled_task*.py`（排除单独执行的两个 PG 模块），以及 chat_task_manager、task_nl_parser、tool_executor、tool_policy、planner_framework、unified_result_e2e、tool_result_05_production_regressions、tool_result_persistence_06。测试显式设置 APP_ENV=testing、合成 DATABASE_URL 和 JWT_SECRET_KEY，不载入生产凭据。

真实数据库单独执行 `tests/test_scheduled_task_draft_delete_integration.py tests/test_scheduled_task_upgrade_postgres.py`，复用原有临时 PostgreSQL fixture。

前端运行 `vitest run`：scheduled-tasks/__tests__、ChangeSetCard、StructuredConsumers、MessageContentBlocks、useScheduledTaskStore、wsMessageHandlers；之后 `npm run build`（包含 `tsc -b`）。`git diff --check` 通过。

## 5. 发布后用户验证

1. 新建表单先解析 A 店，再输入一个需要补全的 B 店请求：不得出现 A 店旧指令；补全后卡片只显示 B 店内容。
2. 对有权限的真实群输入“发到销售群”：表单显示匹配群；不存在或同名对象需明确选择，不能直接发送给自己。
3. 聊天先描述任务业务，若助手追问时间，回答“每天上午8点，发给我”：应同时保留前文业务和后补时间。
4. 使用“每天上午9点”“每天下午两点半”的代表任务检查卡片时间；暂停/恢复各一次，确认任务列表状态一致。
5. 创建检查期间短暂断网再恢复：卡片应自动更新为后端真实结果，无需重新创建。

错误时间模型响应等全量边界由自动化对照覆盖，不要求用户手工构造。现有已创建任务的旧 prompt/收件人不自动改写。

## 6. 最终交接与回退

本批本地技术验证通过，待用户另行“提交部署”发布候选。旧工具 API、旧 `parse_task_nl` 预填和旧表单调用仍为兼容/业务适配；权限继续由原链路维护，没有第二条策略路径。01～07 的验收链见 [TOOL_UNIFICATION_HANDOFF](TOOL_UNIFICATION_HANDOFF.md)，本次不推进板块 08。

回退本批为代码回退，无新迁移或数据回滚；会重新暴露草稿串值、收件对象遗漏、补充上下文丢失和卡片漏更新问题。前一批内容整理证据按各自代码指纹保留，不能与本批混作同一测试结果。
