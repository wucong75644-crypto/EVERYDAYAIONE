# 07 顺序迁移阶段命令

目录：`/Users/wucong/EVERYDAYAIONE/worktrees/tool-unification-07`。以下是各组完成后、进入下一组前实际执行的命令；对应日志保留当时测试集合（初版 07 契约为 76 项）的结果。后续新增覆盖与共同兼容投影的最终状态由 `run-07.sh` 及最终 contracts/integration/regression/erp 日志证明，不把各阶段重复计数。

每条 pytest 命令均使用以下共同前缀：

```bash
env APP_ENV=testing \
 DATABASE_URL=postgresql://tool_test:tool_test@127.0.0.1:1/tool_test \
 JWT_SECRET_KEY=tool-unification-test-only REDIS_PORT=1 \
 PYTHONPATH=/private/tmp/tool05-testdeps:backend \
 /Users/wucong/EVERYDAYAIONE/.venv/bin/python -m pytest
```

ERP：

```text
backend/tests/test_tool_definitions_07.py backend/tests/test_erp_tool_mixin_unit.py
backend/tests/test_erp_local.py backend/tests/test_planner_framework.py
-o addopts='' -v --tb=short > docs/document/tool-unification-evidence/07-group-erp.txt 2>&1
```

文件/沙盒：

```text
backend/tests/test_tool_definitions_07.py backend/tests/test_file_tools.py backend/tests/test_code_tools.py
backend/tests/test_file_tool_mixin.py backend/tests/test_sandbox_tool_mixin.py
backend/tests/test_file_target_execution.py backend/tests/test_resource_scope_continuity.py
-o addopts='' -v --tb=short > docs/document/tool-unification-evidence/07-group-file-sandbox.txt 2>&1
```

媒体：

```text
backend/tests/test_tool_definitions_07.py backend/tests/test_media_tool_executor.py
backend/tests/test_common_tools.py backend/tests/test_tool_result_consumption.py
-o addopts='' -v --tb=short > docs/document/tool-unification-evidence/07-group-media.txt 2>&1
```

任务：

```text
backend/tests/test_tool_definitions_07.py backend/tests/test_chat_task_manager.py
backend/tests/test_scheduled_task_workflow.py backend/tests/test_scheduled_task_agent.py
backend/tests/test_scheduled_task_agent_integration.py backend/tests/test_planner_framework.py
-o addopts='' -v --tb=short > docs/document/tool-unification-evidence/07-group-task.txt 2>&1
```

各参数块在实际执行时连接为一条命令。迁移阶段结果依次为 147/296/173/159 passed，全部退出码 0；通用定义补齐及旧 API 共同派生后继续最终回归。没有为了通过改变业务 Handler。
