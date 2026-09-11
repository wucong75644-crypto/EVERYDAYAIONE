# 板块 06：新载荷写入发布及验收补充

日期：2026-09-11。本文件覆盖首次 reader-first 记录中默认值和发布状态的旧时态；原 A/G 证据和协议边界继续有效。

## 1. 范围与版本

- R0：`cdba58f9018ff45be2ebde0b802471ca634d8727`，旧格式 reader。
- R1：`e097e392fd8229cffda363fc85113c31fb995179`，已于 20:51 完整部署，默认 writer=0；后端、同步、企微和 Actor 四服务均已替换。13 个生产源码指纹与 R1 一致。用户在指定会话完成正常、错误、文件操作回归。
- R2：本任务 R1 加当前写入阶段差异；默认 writer 改为 1，reader/缓存/Actor/审计业务实现不变。最终 SHA 在受控发布交付消息记录；[R2 指纹](tool-unification-evidence/06-writer-source-checks.json) 标识精确被测源码。
- 任务：`codex/task/20260911165104-tool-unification-06`；工作树 `worktrees/tool-unification-06`。用户授权验证完成后执行验收关闭；本记录编写时尚未执行最终发布/关闭，成功与否以受控结果为准。

仅修改配置默认值及相关测试、证据和交接。旧投影测试显式指定 writer=0；Actor 完成/展示异常测试同时覆盖 0 和 1，保留原状态/次数断言并检查新格式恢复字段。无工具资格、Handler、RPC、数据库枚举或基础设施变更。

## 2. 发布顺序和回退

1. R1 保持旧写入，先覆盖所有 reader；已在生产核验，用户回归通过。
2. 本地临时 JSON/CSV 完成跨进程演练：进程一写 v1 并退出，另一个进程使用 writer=0 恢复 v1，成功/错误两类均通过。业务和数据库使用 mock，没有生产副作用或生产迁移试验。
3. 通过 `deploy/release.sh --message ... --file ...` 发布 R2。无环境覆盖时写入默认 1；有覆盖时实际值优先，发布后须核验四服务的生效值，不以源码默认值代替运行配置证据。
4. 停止新格式写入时显式设置 `TOOL_RESULT_PAYLOAD_WRITE_VERSION=0`，保留 R2 或 R1 reader。**完整回退的具体代码目标为 R1 `e097e392fd8229cffda363fc85113c31fb995179`**，其 writer 默认 0 且与 R2 使用完全相同的 v1 reader。跨进程演练验证该配置回退读法；R1 reader 文件逐字不变。
5. R0 只能读取兼容外壳，不能完整保留 FileRef/类型/retry/token/thinking，不承诺无损回退 R0。不清空历史、不将 uncertain 改为 running、不重做未知外部效果。

## 3. 逐项复核

| 编号 | 场景及预期 | 实际结果 | 状态 | 证据 |
|---|---|---|---|---|
| A-06-01 | 新/旧/回退矩阵、先读后写 | R1 已先部署；原新旧矩阵通过；跨进程 v1→writer=0 恢复通过；R0 有损边界保持 | 通过 | [472 项集成日志](tool-unification-evidence/06-writer-integration.txt)、[跨进程结果](tool-unification-evidence/06-writer-process-recovery.json) |
| A-06-02 | 产物、模型/展示、错误/retry/audit/token/thinking 和边界 | 原完整矩阵仍通过；独立进程保留临时 FileRef/emit、错误、retry、thinking 和历史 token=33 | 通过 | 同上，`test_new_write_new_read_all_projections_and_artifacts`、`process-recovery-06.py` |
| A-06-03 | 授权先于缓存，原成功/失败状态，零重复计量 | success/error/timeout 均保留；拒绝时缓存 get=0；命中业务不重做、chargeable_tokens=0；资格无变化 | 通过 | `test_cache_snapshot_preserves_business_state_and_no_handler_or_tokens`、`test_cache_scope_isolation_and_permission_revocation` |
| A-06-04 | 完成恢复、uncertain/拒绝/取消 | 新进程 replayed=true、Handler=0；error 仍 error；原 uncertain/拒绝/取消测试全部通过 | 通过 | 跨进程结果及 `test_actor_unknown_effects_do_not_retry`、`test_policy_denial_before_any_cache_ledger_payload_read`、取消用例 |
| A-06-05 | 原审计约定、故障不重做业务 | 新旧写入均完成/记审计一次，展示错误不重做；原审计写入异常用例通过。生产用户测试审计与调用吻合 | 通过 | `test_actor_ledger_and_audit_written_once_without_business_redo`、`test_actual_audit_writer_preserves_database_contract_and_replay_log`、故障用例 |
| A-06-06 | 具体回退版本、读写顺序、隔离恢复 | R1 精确 SHA 已固定；R2 reader 不变，writer=0 新进程可完整恢复 v1；无生产数据试验 | 通过 | 本文第 2 节、跨进程 JSON |
| G-01 | 本块范围 | 仅默认写入切换、测试及交接；原边界逐字检查保持 | 通过 | R2 源检查、Git diff |
| G-02 | 必需行为含失败边界 | 本块场景复验无失败/跳过；新增默认值与显式回退测试通过 | 通过 | 472 项日志 |
| G-03 | 受影响回归 | writer=1 下 13 个相关测试文件 472 passed；原未受影响扩大回归证据仍有效 | 通过 | 472 项日志、首次验收记录 |
| G-04 | 协议及旧 API | 原 schema/WS/投影对照通过；旧写入测试明确设 0，未弱化断言 | 通过 | 472 项日志、R2 源检查 |
| G-05 | 可交接 | 本补充、原接口/验收/共同交接已关联，明确 R1/R2/R0 与关闭前置 | 通过 | 本文与三个交接文档的顶部更新 |

## 4. 环境和命令

本地 Python 3.14.2，测试库来自原项目 `.venv` 和 `/private/tmp/tool05-testdeps`。数据库指向 `127.0.0.1:1`，Redis 端口 1，JWT 为测试占位；目录无 `.env` 或 `backend/.env`。三项既有警告（pytest-env 配置提示、Pydantic 弃用、AsyncMock 未 await）与首次记录一致，不当作生产结果。

```bash
APP_ENV=testing DATABASE_URL=postgresql://tool_test:tool_test@127.0.0.1:1/tool_test JWT_SECRET_KEY=tool-unification-test-only REDIS_PORT=1 TOOL_RESULT_PAYLOAD_WRITE_VERSION=1 PYTHONPATH=/private/tmp/tool05-testdeps:backend MPLCONFIGDIR=/private/tmp/tool06-matplotlib IPYTHONDIR=/private/tmp/tool06-ipython /Users/wucong/EVERYDAYAIONE/.venv/bin/python -m pytest backend/tests/test_tool_result_persistence_06.py backend/tests/test_tool_result_consumption.py backend/tests/test_tool_execution.py backend/tests/test_tool_result.py backend/tests/test_tool_production_integration.py backend/tests/test_tool_invocation_store.py backend/tests/test_tool_result_cache.py backend/tests/test_tool_audit.py backend/tests/test_loop_hooks.py backend/tests/test_tool_loop_helpers.py backend/tests/test_tool_loop_parallel.py backend/tests/test_tool_loop_usage.py backend/tests/test_chat_tool_mixin.py -o addopts='' -v --tb=short > docs/document/tool-unification-evidence/06-writer-integration.txt 2>&1
/Users/wucong/EVERYDAYAIONE/.venv/bin/python docs/document/tool-unification-evidence/run-06-writer.py
/Users/wucong/EVERYDAYAIONE/.venv/bin/python docs/document/tool-unification-evidence/check-06.py > docs/document/tool-unification-evidence/06-writer-source-checks.json
```

## 5. 问题和用户验证

无未修复代码问题。原生产停留 writer=0 的发布缺口由本次第二阶段处理，必须在发布后核验实际值才算完成，不能提前冒充上线成功。

指定用户会话 `49c500e6-5ac3-4a4b-89c9-89e4f5579935` 的 20:55–20:57 五条工具审计为 success×3/error×1/empty×1；21:10 code_execute 成功一次、审计一条，均不是缓存/回放。应用角色可写不可读审计表，通过既有管理员只读事务核验指定记录；未改 RLS。此次生产回归不冒充缓存和回放命中。

发布后自动验证不触发付费工具、外部通知或真实业务变更：确认四服务版本/实际写入设置，使用临时本地样本验证已部署序列化器写 v1 并恢复完整字段；缓存/回放/故障路径依据相同最终源码的隔离集成证据。无需用户制造生产故障，未知副作用不得重试。审计和 ledger 仍为 best-effort，不承诺零丢失。

## 6. 结论

本次写入阶段技术验证通过。受控发布成功、运行配置/无副作用验证通过后，按用户授权执行 `--accept-and-close`；该关闭阶段不重复部署。最终主分支代码树必须与已测试候选相同；失败则保留工作树。只有关闭成功后才允许开始 07，本任务不实现 07。
