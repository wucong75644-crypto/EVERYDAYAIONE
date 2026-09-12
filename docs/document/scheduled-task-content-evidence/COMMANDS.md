# 内容整理复验命令与边界

工作树 `/Users/wucong/EVERYDAYAIONE/worktrees/tool-unification-07`，基准 eeccf3bd。测试使用 DATABASE_URL=postgresql://test、JWT_SECRET_KEY=test、APP_ENV=testing、DASHSCOPE_API_KEY=test。

后端从 backend 运行 `/Users/wucong/EVERYDAYAIONE/backend/venv/bin/python -m pytest`，选择：

```
tests/test_scheduled_task_request_content.py
tests/test_scheduled_task_form_incident.py
tests/test_scheduled_task_simplified_creation.py
tests/test_chat_task_manager.py
tests/test_task_nl_parser.py
tests/test_scheduled_task_direct_submission.py
tests/test_scheduled_task_changeset_adapter.py
tests/test_scheduled_task_chat_results.py
tests/test_tool_production_integration.py
tests/test_tool_result_consumption.py
tests/test_tool_definitions_07.py
tests/test_tool_definitions_07_entries.py
tests/test_chat_execution_engine.py
tests/test_scheduled_tasks_routes.py::TestParseNL
```

修复前从 `git show HEAD:...` 读入原 task_nl_parser/chat_task_manager，在当前测试环境运行首版 13 个新增行为断言，13 failed。之后新增边界/HTTP 入口用例，最终 backend-final.txt 为 696 passed；不把不同轮次相加。

前端运行 `node node_modules/vitest/vitest.mjs run`，选择 messageProtocol、wsMessageHandlers、StructuredConsumers、MessageContentBlocks、FormBlockChangeSet、ChangeSetCard、WebSocketContext、messageUtils 和 scheduled-tasks 测试目录；10 文件/194 passed。`node node_modules/typescript/bin/tsc -b` 退出 0、无输出。无前端产品代码更改，不重复此前通过的产品构建。

跨端 fixture 由真实 ChatTaskManager.handle 生成，仅替换模型返回值与推送目标为合成数据；后端测试逐字段对照实际产物，前端从 JSON 经 normalizeMessage 再渲染/编辑/提交。没有提交生产 API。

真实模型：现有 qwen-turbo，六条合成请求；读取本机已有凭据但不输出/落盘凭据。model-initial.json 保留初轮缺口，期间定向检查原始分段；model-verified.json 是最终同组六例原始结构、最终结果及预期断言。模型调用只做文字解析，不含数据库写入、ERP 查询、任务创建/运行或通知。

生产只读在限定时间段定位用户截图对应的任务、ChangeSet 与持久化 form。事务 READ ONLY、statement_timeout 10s。证实 prompt 为整段原话、字段 hidden，原时间/频率为空，任务来自 chat-form 提交；未把生产身份、消息 UUID、联系人或完整日志复制进仓库。
