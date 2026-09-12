# 表单生产缺陷复验命令

基准：8773b8b77ed0932445b9ba8b724fa52498cdbd51 + source-checks.json 所列未提交修复。所有下面的相对路径从任务工作树 `/Users/wucong/EVERYDAYAIONE/worktrees/tool-unification-07` 起算。

后端最终执行目录 `backend`：

```bash
DATABASE_URL=postgresql://localhost/scheduled_upgrade_test JWT_SECRET_KEY=offline-scheduled-upgrade-tests /Users/wucong/EVERYDAYAIONE/backend/venv/bin/python -m pytest tests/test_scheduled_task_form_incident.py tests/test_scheduled_task_simplified_creation.py tests/test_chat_task_manager.py tests/test_task_nl_parser.py tests/test_scheduled_task_direct_submission.py tests/test_scheduled_task_changeset_adapter.py tests/test_scheduled_task_chat_results.py tests/test_tool_production_integration.py tests/test_tool_result_consumption.py tests/test_tool_definitions_07.py tests/test_tool_definitions_07_entries.py tests/test_chat_execution_engine.py -q
```

修复前后端只执行 `tests/test_scheduled_task_form_incident.py -q`，当时为 7 个断言：6 failed/1 passed；后续增加的兼容/全链用例使该文件最终共 13 passed。测试原文和 UUID 都是合成，未连真实 DB 或模型。

前端最终执行目录 `frontend`：

```bash
node node_modules/vitest/vitest.mjs run src/schemas/__tests__/messageProtocol.test.ts src/contexts/__tests__/wsMessageHandlers.test.ts src/components/chat/message/__tests__/StructuredConsumers.test.tsx src/components/chat/message/__tests__/MessageContentBlocks.test.tsx src/components/chat/message/__tests__/FormBlockChangeSet.test.tsx src/components/chat/message/__tests__/ChangeSetCard.test.tsx src/contexts/__tests__/WebSocketContext.test.tsx src/utils/__tests__/messageUtils.test.ts src/components/scheduled-tasks/__tests__
node node_modules/typescript/bin/tsc -b
npm run build
```

修复前只执行前三个文件（当时 4 failed/95 passed），新增断言通过真实 parseContentPart、WS handler、normalizeMessage，不是直接将 FormPart 类型强转后交给组件。最终 build 在移除临时 QA 入口后执行，日志来自 `npm run build`，包括 TypeScript 与 Vite。

## 生产证据范围

经原 deploy/config.env 的 SSH 定位生产版本为 8773b8b7；只读日志发现 2026-09-12 15:33 目标请求。数据库使用 `SET TRANSACTION READ ONLY`，statement_timeout 10s，限定目标原文及一分钟时间窗口，确认助手消息已有 tool_step/form，且未产生该次 create ChangeSet。原始生产读取只保留在本会话工具记录，仓库没有保存生产收件人清单或完整消息。

## 浏览器记录（2026-09-12，本地）

用真实 `ChatTaskManager.handle('create', …)` 构造三份表单，替换的只有 `_call_llm` 与 `_load_push_targets`（合成解析结果/一个虚拟本人收件人）。产物为 synthetic-backend-forms.json：完整时间但店铺是模板标记、解析返回空对象、一次性任务缺具体执行时刻。

临时 Vite QA 页在 127.0.0.1:5187：把这三份 JSON 编成历史消息字符串，经原 `normalizeMessage` → `MessageContentBlocks` → `FormBlock`，使用原 index.css 和 LazyMotion。

1. CUA 浏览器 AX 与截图确认工具完成条下方显示补全表单、执行原文和“创建任务”按钮；店铺标记仍可编辑，已知时间/频率不重复让用户填写。
2. 解析失败场景保留原话，“执行频率”明确显示“请选择”，出现时间输入。
3. 选择 once 后，AX 确认每日时间字段消失、具体日期时间字段出现；一次性示例只有具体日期时间输入。
4. reload 后三份表单仍显示，证明历史 JSON 读取边界有效。

本地 QA 未装配生产 API 或发送提交请求。日期填写/提交事件由自动化组件测试验证，实际任务创建与通知待生产验收。截图保留于本会话 CUA 工具输出；临时页面、TSX/JSON 副本已删除，Vite 已停止，浏览器标签已关闭。

## 最终差异核对

在任务根目录运行 `git diff --check`，确认没有空白错误。source-checks.json 记录 backend/frontend 受影响文件 SHA256；不修改历史 scheduled-task-upgrade-evidence 或工具目录冻结基准。发布需重新确定候选并核验对应指纹。
