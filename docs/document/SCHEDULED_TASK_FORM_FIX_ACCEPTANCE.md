# 定时任务创建表单缺失：生产复验修复记录

## 1. 范围与版本

2026-09-12，继续原任务 `codex/task/20260911215117-tool-unification-07`。生产及当前 HEAD 为 `8773b8b77ed0932445b9ba8b724fa52498cdbd51`，本次被测代码为此 HEAD 加未提交修复；[文件指纹](scheduled-task-form-fix-evidence/source-checks.json) 固定实际版本。本次尚未重新提交部署、合并或关闭，不进入 08。

8773b8b7 已于本日完成受控发布、迁移 254/255、服务切换和健康检查；随后用户在 15:33 报告创建后只有工具完成提示、没有表单。此前验收文档内“未部署”是开发快照，本记录补充之后发生的事实。

生产只读证据：截图对应助手消息已持久化 `tool_step + form`，表单状态 open，包含 `datetime-local` 与 `visible_when.not`；该次请求没有进入创建 ChangeSet，工具成功不等于任务已创建。模型工具参数把占位店铺改写成“按实际店铺名汇总”；解析器实际返回带顶层 schedule_type 的旧格式，新读取方只读取 changes/evidence，导致已给出的信息全部未采纳。只记录这些结构事实，不复制生产联系人、收件人选项、消息 UUID 或完整日志。

修复仅涉及消息协议、原任务解析与提交适配：前端接纳已存在的日期字段并保留否定显示条件；空选择明确显示“请选择”；直接创建使用本轮 user 原文，解析失败保留原文供补全；识别明确的店铺模板标记并阻止未填标记提交。旧 proposal 模式、原 schema、别名、风险/并发/缓存元数据和原执行引擎不变。没有改迁移、任务启动/暂停/完成 RPC、ERP/沙盒/媒体或通知链。

## 2. 逐项证据

| 验收编号 | 场景及预期 | 实际结果 | 状态 | 证据 |
|---|---|---|---|---|
| G-01 / A-07-04 | 在现有机制修复本次现象，保留业务内核 | 7 个产品源码文件局部修复；无迁移/调度/结算/引擎重写，未操作生产任务 | 通过 | source-checks.json、当前相对 8773b8b7 的 diff |
| G-02 / ST-D / A-07-03 | WS、历史消息 JSON 均保留完整表单；每日与一次性时间条件正确 | 修复前 4 个入口断言失败，修复后均通过；历史消息经过 normalizeMessage 后实际组件可显示并提交一次性日期 | 通过 | frontend-before.txt → frontend-final.txt；`messageProtocol`、`wsMessageHandlers`、`StructuredConsumers`、`MessageContentBlocks` 新增用例 |
| G-02 / ST-B / ST-D | 保留真实创建要求；未填写店铺不得直接创建；旧入口兼容 | 真实 ToolExecutor/Policy/Dispatcher/ChatToolMixin 链验证，原文保留、表单进入 sink/checkpoint；直接提交适配器拒绝未填标记，proposal 原行为保持；同实例连续请求不沿用上轮原文 | 通过 | backend-before.txt → backend-final.txt；`test_scheduled_task_form_incident.py` 13 个用例 |
| G-03 / A-07-05 | 失败补齐复验，受影响链路回归无失败 | 后端 675 passed、前端 193 passed；类型检查、生产构建通过，无新增跳过 | 通过 | backend-final.txt、frontend-final.txt、typescript.txt、build.txt |
| G-04 / A-07-01/02/03 | 完整工具目录/别名/旧 API、Spec/Planner 权限契约保持 | 35 工具全字段/合法参数/导入/生产入口对照及旧 helper/结果消费用例通过；日期字段属于已有服务端载荷，修复读取兼容 | 通过 | `test_tool_definitions_07`、`test_tool_definitions_07_entries`、`test_tool_production_integration`、`test_tool_result_consumption` |
| G-02 / ST-D | 后端实际表单经过 JSON 边界在浏览器可见 | 3 份合成实际后端表单经原 normalizeMessage/MessageContentBlocks/FormBlock 显示；切换 once 后时间字段换为日期字段；刷新仍可见 | 通过 | [命令与浏览器记录](scheduled-task-form-fix-evidence/COMMANDS.md)、synthetic-backend-forms.json；本会话 CUA 截图/AX 记录 |
| G-05 / A-07-06 | 接口、剩余兼容与回退可交接 | 本记录、共同交接及技术文档均补充当前状态，历史证据保留 | 通过 | 第 6 节及共同交接链接 |
| U-PROD | 修复候选重新部署后，由真实模型完成创建与用户观察 | 尚未重新发布本修复；没有为测试创建生产任务、查询 ERP 或发送通知 | 未验证 | 第 5 节 |

## 3. 测试环境与摘要

工作目录为原任务工作树。Python 3.12.12 / pytest 8.3.4；Node 本机既有版本，Vitest 3.2.7、TypeScript 5.9.3、Vite 7.3.6。后端使用测试占位 DATABASE_URL/JWT，不连生产；定向用例没有运行真实模型、ERP、数据库写入或通知。

修复前前端 4 failed/95 passed，后端新用例 6 failed/1 passed；修复后扩展到最终后端 675 passed/0 failed/0 skipped、前端 193 passed/0 failed。前后端统计分别包含不同测试，不累加重叠轮次。构建保留既有大 chunk 提示；前端测试保留 motion mock 的 DOM 属性提示，不属于失败。

此前 8773b8b7 全量发布测试通过没有覆盖本次缺口：后端测试证明了表单已写入，前端测试直接把类型断言后的 FormPart 交给组件，跳过了运行时 Zod 校验。新增测试明确经过 WS/历史消息协议读取边界；另用后端实际生成的合成载荷浏览器验证。不能把旧测试数量当作此缺口已被验证。

## 4. 问题清单与复验

| 编号 | 复现 | 根因/影响 | 处理结果 | 复验证据 |
|---|---|---|---|---|
| ST-07 | 页面只剩完成的工具步骤，刷新也没有表单 | 服务端与组件已支持 datetime-local，前端 formFieldSchema 枚举未更新，导致整块丢弃；visible_when 子对象又剥离 not，单独补枚举仍会显示错误字段 | 在统一读取边界补齐两个已有契约字段 | 4 项修复前失败、实时与历史链复验通过 |
| ST-08 | 已说出频率、时间，生成的表单却空白 | 严格解析 prompt 拼接旧版平铺输出说明及例子；本次真实模型输出旧格式，与 changes/evidence 读取方冲突；失败补全分支还清空了原始执行内容 | 严格 prompt 使用一致的嵌套示例，不接受无证据字段；失败保留原文供用户编辑，未解析选择显示“请选择” | prompt 示例契约、旧格式拒绝/原文保留及组件用例 |
| ST-09 | 原文店铺占位标记被模型改写成分组要求 | 提交前解析读取模型生成的 description，原文约束已在更早阶段丢失 | 可信直接创建从当前上下文最后一条 user 取原文；多模态使用 UserLayer 的首个原文 text，不混入后续附件引用；明确模板标记要求补全并在同一提交适配器拒绝 | 真实 Runtime、Chat sink/checkpoint、连续请求/旧入口和正常店铺名用例 |

本次已定位缺口均已修复并通过本地复验。真实模型新 prompt 的实际表现仍需部署后验证；本记录不声称真实模型每次都会成功解析，也不把模板识别当成新的店铺授权系统。

## 5. 用户验证单

待下一次用户“提交部署”确定新候选后：

1. 刷新原对话，原来只显示工具完成的消息应出现补全表单。旧消息保留当时的数据，本修复不改写历史表单值。
2. 新发“创建测试日报，每天上午9点，把昨天【实际店铺名】的销售汇总发给我”：显示可编辑执行内容，并提示替换店铺占位文字，未填时不能提交成正式任务。这里的占位文字用于验证缺信息处理。
3. 使用有权限的真实店铺名重新发送：检查普通完整请求的创建回执及任务列表。按已确认方案，这类完整请求检查后直接创建；缺信息才显示补全表单，不新增重复确认步骤。
4. 缺时间时补时间；选择“仅一次”时必须填写日期和时间，不能同时要求另一个每日时间字段。提交失败要显示原因，刷新后仍能查看对应状态。

记录部署 SHA、操作时间和实际结果即可，无需手动遍历所有工具。定时到点、真实 ERP 与通知仍按主验收记录代表流程执行。

## 6. 结论与交接

本次局部修复本地技术复验通过；修复尚未提交部署，用户验收未关闭，整体不进入下一块。当前部署仍是有本次表单缺陷的 8773b8b7。

实际接口不新增工具参数、不修改旧导入：`ToolExecutor._manage_scheduled_task` 仅在可信直接 create 分支取当前请求原文；`parse_task_request` 与旧 `parse_task_nl` 分别维护严格解析与旧预填行为；`unfilled_shop_placeholder` 仅供任务补全/原适配器校验复用；schema/risk/domain/parallel 仍由 ToolSpec 维护。旧 adapter/helper 保留业务和兼容职责，未引入第二条权限或提交链。

无数据迁移，生产历史 form JSON 原样由修复后的读取方接纳。代码可回退到 8773b8b7，但会恢复本次已知展示缺陷；不需要回退 254/255 或改任务数据。回退载荷不变，`synthetic-backend-forms.json` 及旧消息 normalizeMessage 用例证明此次仅修正读取兼容。后续需受控发布新候选并由用户重新验证，不能沿用 8773b8b7 的部署当作本修复已上线。
