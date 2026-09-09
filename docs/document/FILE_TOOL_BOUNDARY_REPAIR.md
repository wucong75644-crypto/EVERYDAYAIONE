# 文件工具资源边界修复与兼容验收（2026-09-09）

状态：**本地技术验证通过，待提交部署与用户验收**。用户要求先核清代码再执行，授权修复上一轮确认的删除与输出登记边界缺口。当前工作树仍为 `tool-unification-03`，分支 `codex/task/20260909195904-tool-unification-03`；HEAD 为已部署候选 `e243ba2c0d545d8afca3ae930a40389276b7c123`。被测版本是该提交加未提交修复，不能把 HEAD 当作包含修复的生产版本。

## 修改前核实的调用链与设计

- Chat 的 `_prepare_tool_arguments` 仍负责危险操作确认；随后 `resolve_file_ids` 曾按名字缓存改写删除参数，再进入旧 ToolExecutor → FileToolMixin → FileDeleteMixin。提前改写会丢失原始目录，因此删除参数现在保留到 Handler 再统一解析、校验。文件分析的原参数转换保持不变。
- `FileExecutor.resolve_safe_path` 已包含当前用户/组织工作区、禁止文件、符号链接与普通文件入口禁止 staging 的规则，直接复用，不复制规则表。
- **不能替换成 FileExecutor.file_delete**：该方法会立即删除 OSS 副本；AI 删除工具的业务流程则只删本地文件并写原 deleted_files 记录，以保留 30 天恢复能力。本次保留原删除、记录和 restore_file 实现，不更换 OSS 行为。
- `_code_execute` 只在结果成功后从 stdout 登记文件；登记是可选附加工作，不能因目录不可用把成功结果转为错误。`_get_workspace_dir` 使用 workspace_user_id，与 actor_user_id 不一定相同，不能再次追加用户目录。
- `_register_staging_files` 由 ERPAgent 返回后调用。DepartmentAgent 和 ERP 导出通过既有 resolve_staging_dir/resolve_export_path 写入当前会话 staging；FileRef 是内部绝对路径，模型使用 `staging/filename`。内部登记要以当前会话 staging 为边界，不能套普通文件入口的整段 staging 禁令，也不能允许跨会话路径。

## 实施范围和行为

1. `file_delete_mixin.py`：复制 files 参数，保留两种参数并存的实际兼容调用；相同实际目标只执行一次。带目录的输入直接按原路径校验，裸文件名保留唯一缓存匹配。所有 ID/缓存/相对/绝对来源在删除前统一经过原工作区守卫。整批存在越界、禁止或不可校验路径时，返回旧 AgentResult 错误类型、retryable=False，零删除；不存在的文件仍按原规则跳过。
2. 先完成整批校验，再调用原 os.remove；不新增自动重试。中途文件系统失败/取消时，用 finally 将已完成的删除交给原记录器；异常仍由原入口处理，取消继续传播，不声称批量删除具备文件系统事务或可靠审计投递。
3. `chat_tool_helpers.py`：删除不再预先转换文件路径，保留原输入到 Handler；其他工具行为不变。原 basename 删除仍可通过 Handler 正确定位。
4. `sandbox_tool_mixin.py`：stdout 路径先经可信工作区守卫且确认为文件，再登记完整相对路径；不先登记 basename。内部 FileRef、新 staging 文本和旧 STAGING_DIR 文本均以当前会话 staging 为边界校验后登记，源路径/Parquet 双字段语义保留。缺失或不可用目录跳过可选登记，不新建缺失根目录、不改返回对象。

没有改变工具 schema、ID 哈希、确认 UI、ToolExecutor 公共入口、services/tools 新链、AgentResult/WS/数据库格式；没有提前切换板块 04。前面的附件 ID 修复仍保留。

## 验收证据

`B::` 为 [test_file_tool_boundaries.py](../../backend/tests/test_file_tool_boundaries.py)。

| 编号 | 场景及预期 | 实际结果 | 状态 | 证据 |
|---|---|---|---|---|
| B-01 | 绝对越界、相对越界、其他 owner、受限文件、staging、文件/父目录符号链接、污染缓存及污染 ID：整批拒绝、零删除零记录 | 9 种拒绝场景均通过，合法文件与拒绝目标混合也未先执行合法项 | 通过 | B::test_entire_batch_is_rejected_before_any_delete |
| B-02 | 相对/绝对/裸名/ID/两种参数并存：正确目标、一次执行、输入不变、保留恢复记录 | 5 场景通过；重复目标去重，缺失目标仍跳过；没有转调会删除 OSS 的实现 | 通过 | B::test_safe_delete_uses_original_recording_and_does_not_mutate_args；test_missing_and_duplicate_targets_keep_skip_and_once_semantics |
| B-03 | Chat 不在守卫前改写目录；公共入口使用 workspace owner | 原始外部同名路径保持不变并拒绝；actor 与 workspace owner 不同的合法删除定位正确 | 通过 | B::test_chat_keeps_explicit_delete_path_until_scoped_handler；test_public_delete_uses_workspace_owner_not_actor |
| B-04 | stdout 不登记越界/违规路径；合法相对/绝对文件可解析 ID | 8 类非法输入不入库；2 类合法输入可解析 ID | 通过 | B::test_stdout_does_not_register_disallowed_paths；test_stdout_registers_valid_files_and_ids |
| B-05 | FileRef、新/旧 staging 文本继续可用，同时拒绝跨会话 | 3 种合法产物路径可按 code/analyze 用途取得原文件，3 种跨会话输入不登记 | 通过 | B::test_staging_artifacts_still_register_for_code；test_staging_registration_cannot_escape_current_conversation |
| B-06 | 真实部门产物仍可读，登记不改变结果投影；可选登记不能改变代码执行结果 | 原 DepartmentAgent 写出的 Parquet 与 JSON 降级文件均读回原行；返回对象、FileRef、to_message_content 保持；目录不可用时 _code_execute 仍返回原成功对象、只执行一次 | 通过 | B::test_real_department_artifact_remains_readable；test_code_execute_preserves_result_when_registering_output；test_optional_registration_does_not_create_missing_roots |
| B-07 | IO 失败/取消不新重试，已完成部分继续记录 | PermissionError 返回原错误行为，CancelledError 传播；已成功部分记录一次，未增加执行次数 | 通过 | B::test_partial_io_failure_keeps_records_without_retry；test_cancellation_propagates_and_records_completed_deletions |
| G-01 | 范围清楚、无未说明契约变化 | 三处生产文件增量及必要测试；参数转换归属、去重、整批权限预检与部分失败记录均已说明 | 通过 | 上述设计；源码范围检查 |
| G-02 | 成功、拒绝/失败与边界独立验证 | B-01～07，共 41 个边界/兼容测试 | 通过 | 最终逐用例日志 |
| G-03 | 修改前复现、修改后相关新旧回归通过 | 初轮边界测试 24 failed/9 passed；最后 41 passed。整体主组 1481 passed，独立 ERP 11 passed，4 原有 xfailed | 通过 | 下列日志；不将重复定向复验加入总数 |
| G-04 | 文件 ID、旧 files、结果、恢复、staging、确认和隔离兼容 | 上述用例及旧 restore_file、FileExecutor、文件分析、附件/图片、媒体、确认、部门/ERP、工具统一测试通过；原 Chat helper 单测按“原始输入交给 Handler”更新，由真实 Handler 链覆盖最终行为 | 通过 | 新旧测试日志与源码指纹 |
| G-05 | 调用点、证据、问题、回退和后续前置可交接 | 本文及 HANDOFF、CURRENT_ISSUES、旧核验记录已更新；未部署或验收关闭 | 通过 | 本记录及链接 |

### 执行与日志

在当前任务工作树执行（脚本拒绝加载 `.env`/`backend/.env`）：

```bash
bash docs/document/tool-unification-evidence/run-file-cache-regression.sh
APP_ENV=testing DATABASE_URL=postgresql://test:test@127.0.0.1:1/test JWT_SECRET_KEY=test-only REDIS_PORT=1 \
  /Users/wucong/EVERYDAYAIONE/.venv/bin/python -m pytest backend/tests/test_erp_tool_mixin_unit.py -o addopts='' -v --tb=short
```

- [主组](tool-unification-evidence/03-file-cache-regression.txt)：1481 passed、0 failed/error/skipped、4 xfailed、4 warnings。
- [独立 ERP](tool-unification-evidence/03-other-tools-erp.txt)：11 passed、0 failed/error/skipped、2 warnings。合计 **1492 passed，4 原有 xfailed**。
- [源码检查/指纹](tool-unification-evidence/03-file-cache-checks.txt)：累计十二个授权 backend 文件，AST、diff 空白、shell 语法通过；核心 schema、ToolExecutor 公共入口、新工具统一层、WS 和持久化源码不变。
- [修改前边界测试](tool-unification-evidence/03-file-boundaries-before.txt)：当时工作树为 e243ba2c 加前轮文件缓存修复，删除/沙盒/Chat helper 仍是原源码；33 项中 24 failed、9 passed，确认不是仅有正常路径测试。
- [中间定向复验](tool-unification-evidence/03-file-boundaries-targeted.txt)：98 passed，含修正可选登记目录缺失后的旧测试复验；后续新增的完整代码执行、真实 Parquet/JSON 和取消测试均纳入最终主组。
- [同一诊断修复后对照](tool-unification-evidence/03-file-boundaries-after-audit.txt)：使用 [脚本](tool-unification-evidence/audit-other-file-tools.py) 的 `--expect-fixed` 断言模式；越界目标由“mock 删除 1 / 守卫 0”变为“mock 删除 0 / 守卫 1”，合法 ID 为“1 / 1”，未知/错组织 ID 为“0 / 0”，外部 stdout 不再入库。fixture 文件仍存在。

Python 3.14.2，数据库/Redis 使用不可连接的测试端口，JWT 使用测试占位；无外部数据库、ERP、OSS 写入或付费服务。新增删除测试全部 mock，只在临时目录创建测试数据；旧文件单测也只操作各自临时 fixture。

### 失败、警告与复验

- 初次实施新守卫时直接构造 FileExecutor，目录不可用会抛错，两个旧 stdout 测试失败（94 passed/2 failed）。已补“目录不存在不创建、初始化不可用跳过登记”，保留旧不抛错断言，并补完整 _code_execute 返回对象对照。没有通过改旧断言把失败隐藏。
- 4 个 xfail 已在前轮已部署基准复现，仍为工具 description/模态文案问题，非本次范围。
- 新扩大 ERP 组发现 AsyncMock 未 await 警告，已用相同三文件、`python -X tracemalloc=5 -m pytest ... -o addopts='' -q --tb=short` 对照：旧基准与当前均 249 passed，警告分配点同为未改动 `erp_agent.py:233`。见 [基准](tool-unification-evidence/03-erp-warning-baseline.txt)、[当前](tool-unification-evidence/03-erp-warning-current.txt)。其余为既有 pytest env、Pydantic Config 与 asyncio.iscoroutinefunction 弃用警告。不声称无警告。

## 差异审查与发布边界

按 Review 的正确性、信任边界、兼容和副作用检查完成同轮自审：保护原始删除输入；实际目标统一校验；整批权限预检；输入列表不突变；去重；保持 OSS 副本和原记录器；取消传播；可选登记失败不污染主结果；当前会话 staging 不误拦正常 ERP 产物。审查范围内未发现阻塞问题，不代表全产品不存在其他缺陷。

仅本地修复，未提交、推送、部署、合并或清理工作树。后续需部署确定候选并由用户验证。建议生产仅先核对已有文件读取、ERP 产物和图片流程；若验证删除，必须使用专门可丢弃的测试文件，并核对取消/批准和恢复。此次未进行任何真实业务删除或恢复演练。

回退本次边界修复可撤回 file_delete_mixin、sandbox_tool_mixin、chat_tool_helpers 及相应测试，保留前轮附件 ID 修复；撤回全部本任务追加修复可恢复到 e243ba2c，但会重新带回已确认缺陷。无持久化格式或数据迁移。板块 04 仍须本任务用户验收关闭后才能启动。
