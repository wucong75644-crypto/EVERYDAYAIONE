# 定时任务升级验证命令

工作树 `/Users/wucong/EVERYDAYAIONE/worktrees/tool-unification-07`。以下结果对应本目录 source-checks.json 的 HEAD + 未提交文件指纹。Python 为 `/Users/wucong/EVERYDAYAIONE/backend/venv/bin/python` 3.12.12；pytest 8.3.4。前端 Node 为 `/Users/wucong/.local/bin/node`，Vitest 3.2.7，TypeScript 5.9.3，Vite 7.3.6。测试依赖复用本机环境，未修改依赖锁文件。

后端每条 pytest 命令均在 `backend/` 执行，前缀：

```bash
DATABASE_URL=postgresql://localhost/scheduled_upgrade_test JWT_SECRET_KEY=offline-scheduled-upgrade-tests /Users/wucong/EVERYDAYAIONE/backend/venv/bin/python -m pytest -q
```

主机权限只用于启动临时 Unix socket PostgreSQL 集群，测试中为每个用例创建并销毁临时库。没有连接上述占位 URL、生产 DB、ERP 或付费模型。pytest 的 SQL fixture 自己注入临时 DSN。

各批次在通过后才继续；以下文件名均相对 backend：

| 批次 | pytest 的文件参数 | 结果日志 |
| --- | --- | --- |
| A | `tests/test_scheduled_task_chat_results.py tests/test_tool_result_consumption.py tests/test_chat_execution_engine.py tests/test_tool_result_persistence_06.py` | batch-a.txt：180 passed |
| B | `tests/test_scheduled_task_direct_submission.py tests/test_scheduled_task_upgrade_postgres.py tests/test_scheduled_task_changeset_adapter.py tests/test_scheduled_tasks_routes.py tests/test_changeset_core.py tests/test_tool_execution.py` | batch-b.txt：132 passed |
| C | `tests/test_scheduled_task_upgrade_postgres.py tests/test_scheduler_scanner.py tests/test_scheduled_task_executor_delivery.py tests/test_scheduled_task_direct_submission.py tests/test_scheduled_task_changeset_adapter.py` | batch-c.txt：72 passed |
| D | `tests/test_scheduled_task_simplified_creation.py tests/test_chat_task_manager.py tests/test_task_nl_parser.py tests/test_scheduled_task_direct_submission.py tests/test_scheduled_task_changeset_adapter.py tests/test_ws_form_submit.py` | batch-d.txt：104 passed |
| 最终契约/边界 | `tests/test_scheduled_task_direct_submission.py tests/test_scheduled_task_simplified_creation.py tests/test_scheduled_tasks_routes.py tests/test_chat_task_manager.py tests/test_scheduled_task_changeset_adapter.py tests/test_tool_definitions_07.py tests/test_tool_definitions_07_entries.py` | final-contracts.txt：469 passed |
| 最终事务/生命周期 | `tests/test_scheduled_task_upgrade_postgres.py tests/test_scheduler_scanner.py tests/test_scheduled_task_executor_delivery.py` | final-postgres.txt：42 passed，含 19 个真实 PostgreSQL 用例 |

最后一次全链回归使用完整 [backend-test-files.txt](backend-test-files.txt)，准确调用：

```bash
DATABASE_URL=postgresql://localhost/scheduled_upgrade_test JWT_SECRET_KEY=offline-scheduled-upgrade-tests /Users/wucong/EVERYDAYAIONE/backend/venv/bin/python -c 'import pathlib, subprocess, sys; files=pathlib.Path("../docs/document/scheduled-task-upgrade-evidence/backend-test-files.txt").read_text().splitlines(); sys.exit(subprocess.call([sys.executable,"-m","pytest","-q",*files]))'
```

final-backend.txt：1963 passed，1 skipped，0 failed。该 skip 是原 `test_tool_invocation_uncertain_integration.py` 的 DSN 环境开关；原测试函数已由新增临时数据库 fixture 原样执行通过，见 upgrade_postgres 的 `test_existing_actor_uncertain_and_replay_contract_on_disposable_database`，不把 skip 计为通过。

前端在 `frontend/` 执行：

```bash
/Users/wucong/.local/bin/node node_modules/vitest/vitest.mjs run src/components/chat/message/__tests__/ChangeSetCard.test.tsx src/components/chat/message/__tests__/FormBlockChangeSet.test.tsx src/components/scheduled-tasks/__tests__/TaskCardChangeSet.test.tsx src/components/scheduled-tasks/__tests__/ScheduledTaskPanel.test.tsx src/contexts/__tests__/wsMessageHandlers.test.ts src/contexts/__tests__/WebSocketContext.test.tsx
/Users/wucong/.local/bin/node node_modules/typescript/bin/tsc -b
/Users/wucong/.local/bin/node node_modules/vite/bin/vite.js build
```

final-frontend.txt：122 passed；TypeScript、生产构建 exit 0。日志中的 motion mock DOM 属性警告和打包大 chunk 提示保留，未通过屏蔽警告制造通过。

源码清单在任务根执行：

```bash
DATABASE_URL=postgresql://localhost/scheduled_upgrade_test JWT_SECRET_KEY=offline-scheduled-upgrade-tests PYTHONPATH=backend /Users/wucong/EVERYDAYAIONE/backend/venv/bin/python docs/document/scheduled-task-upgrade-evidence/check-source.py
DATABASE_URL=postgresql://localhost/scheduled_upgrade_test JWT_SECRET_KEY=offline-scheduled-upgrade-tests PYTHONPATH=backend /Users/wucong/EVERYDAYAIONE/backend/venv/bin/python docs/document/tool-unification-evidence/capture-07-catalog.py docs/document/scheduled-task-upgrade-evidence/catalog.json
git diff --check
```

`implementation.patch` 包括全部 backend/frontend/deploy 修改和新增文件，没有暂存代码。check-source.py 对基准设显式断言，后续提交后不要覆盖本次未提交版本证据，应另记录候选 SHA/代码树一致性。

发布前必要补充：在 backend 下使用同一测试前缀运行 `tests/test_scheduled_task_deploy_drain.py tests/test_deploy_service_contract.py`，11 passed；在任务根用同一 Python 运行 `scripts/testing/test_release_coordination.py`，10 tests OK；`bash -n deploy/deploy.sh`、`bash -n deploy/release.sh` 通过。输出在 deploy-cutover-checks.txt、release-coordination.txt。此补充只验证发布切换，不重跑已通过且未变化的业务回归。

`reproduce-before.py` 是修改前离线复现，依赖基准测试 helper，原 handler 返回合成 ChangeSet。对 ca4c3d7e 和 75fced91 两个隔离源码树运行，结果分别在 pause-before-base/current.json；不应在新代码上期待“缺卡”断言继续成立。未复制生产会话、日志或任务数据。

## 本地界面记录

2026-09-12，在仅 localhost:5187 的临时 Vite 页面加载真实 TaskCard、ChangeSetCard、FormBlock，使用合成 A 店日报、QA 用户和替身 service，启用原 LazyMotion。通过 Codex 浏览器查看截图和可访问性树：

- 暂停前按钮常驻；点击后原卡从“暂停”切为“恢复”、去掉下次时间；“立即执行”保留，提示“运行一次，保持定时暂停”。
- running 且 schedule_enabled=false 显示“本次执行中 · 后续定时已暂停”，不提供重复立即执行。
- applied 回执显示“已创建”、每天 09:00、网页通知、10 积分，检查和变更记录折叠；无二次确认按钮。
- 缺时间表单保留原执行内容，只展示待补时间及创建/取消，没有四阶段进度。

这是实际组件的本地展示及状态交互验证，不是生产端到端或 ERP 结果验证。临时 QA 页面、浏览器标签和 Vite 进程均已删除/关闭。
