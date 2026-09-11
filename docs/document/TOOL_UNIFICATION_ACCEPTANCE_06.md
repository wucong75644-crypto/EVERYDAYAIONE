# 工具统一板块 06 验收记录

日期：2026-09-11。**技术验收通过，待提交部署及用户验收。** 新写入开关默认 0；本结论包含 reader-first 发布约束，不代表生产已启用 v1，不代表可无损回退到旧 reader。

## 1. 范围与版本

- 基准/当前 HEAD：`cdba58f9018ff45be2ebde0b802471ca634d8727`（最新 origin/main 创建时）；与 05 最终 `87b07718` tree 相同，01–05 提交均为祖先。用户明确确认 1–5 已验收进入 main；交接文件顶部旧未关闭状态为历史快照。
- 分支：`codex/task/20260911165104-tool-unification-06`。
- 执行目录：`/Users/wucong/EVERYDAYAIONE/worktrees/tool-unification-06`。
- 被测版本：上述 HEAD **加本工作树未提交差异**，不是基准 SHA 本身；[06-source-checks.json](tool-unification-evidence/06-source-checks.json) 列出每个源码/测试文件 SHA256 及整体 source_identity。发布时需核对最终候选源码与这些指纹，实际候选 SHA 尚未产生。
- 本块修改 13 个生产文件（包括新增 result_payload.py）及 3 个测试文件；没有数据库迁移、工具定义收拢、ERP/文件/媒体 Handler 重写、计费实现修改或 WS 协议变化。

| 文件/范围 | 与本块目标的对应关系 |
|---|---|
| tools/result_payload.py；tool_invocation_store.py；core/config.py | v1 受限编解码、旧外壳、旧载荷读取、writer 0→1 的显式发布次序 |
| chat/tool_lifecycle.py | 当前鉴权后的完整恢复；完成结果直接交 versioned writer；保留原状态/RPC/资格 |
| tools/result.py；tools/runtime.py；runtime_context.py；agent/tool_result_cache.py | 复用状态和零新增 token、隔离缓存快照/完整大小限制、缓存身份 key、恢复前 FileRef/emit 路径包含检查 |
| chat_tool_mixin.py；chat_tool_result_mixin.py | token 不重复累计；原审计写入与投递失败收口 |
| agent/tool_audit.py；loop_hooks.py；tool_loop_executor.py | 原审计表契约、关联执行日志、每轮模型 token 只归属一次、展示失败补交尚未审计的已完成结果 |
| tests/test_tool_result_persistence_06.py | 76 个新隔离/真实消费入口用例；本节 A/G 对应证据 |
| tests/test_tool_result.py；test_tool_result_consumption.py | 增补审计执行字段、缓存快照语义、默认旧写入的精确替代断言 |

完整字段、默认值、失败边界及发布顺序见 [06 持久化接口](TOOL_UNIFICATION_PERSISTENCE_06.md)。

## 2. 逐项证据表

以下用例均位于 [新增 06 测试](../../backend/tests/test_tool_result_persistence_06.py)，除另标外；准确命令由 [run-06.sh](tool-unification-evidence/run-06.sh) 保存。`06-integration.txt` 的每个用例均有独立 PASSED 行，不以总数代替各项证据。

| 验收编号 | 场景及预期 | 实际结果 | 状态 | 证据 |
|---|---|---|---|---|
| A-06-01 | 新写→新读；旧载荷→新读；拟回退 reader 读新写；先读后写，不清空历史 | 9 类结果全部投影/产物对照；6 类旧载荷明示缺省；执行 R0 的原 writer/reader AST，对成功/error/timeout/partial、FileRef/图片/表单/string 做跨版本落盘读取。R0 外壳可读但必要扩展有损，因此默认写 0，完整回退只保留 R1 reader；测试中 0→1→0 后 v1 仍完整可读 | 通过 | `test_new_write_new_read_all_projections_and_artifacts`、`test_legacy_payload_defaults_do_not_invent_history`、`test_cross_version_rollback_shell_and_information_limits`、`test_rollback_reader_handles_new_non_text_shell_with_documented_type_loss`；[集成日志](tool-unification-evidence/06-integration.txt)；[回退顺序](TOOL_UNIFICATION_PERSISTENCE_06.md#1-版本和发布顺序) |
| A-06-02 | 模型/展示、FileRef/emit/图片/表单、retry/error、审计/token/thinking 保留；非法对象/超限明确处理 | 9 类正常 round-trip、全字段 FileRef 和数据库标量投影一致；普通异常/超时/拒绝分别保留描述和执行事实；数据库/锁/异常 runtime metadata 不 stringify，省略路径可查；retry/产物非法值、大文本/行数/总量/深度/nonfinite 拒绝存储；无伪造旧历史 origin | 通过 | 上述 round-trip；`test_file_ref_all_fields_and_database_cell_types`、`test_exception_payload_contains_descriptors_only`、`test_safe_metadata_normalization_and_omission_without_repr`、`test_unsafe_or_oversized_payload_explicitly_rejected`、`test_reader_first_writer_also_excludes_runtime_handles`、`test_legacy_raw_writer_cannot_bypass_payload_safety`、`test_unknown_version_is_not_downgraded_to_shell_success`；[集成日志](tool-unification-evidence/06-integration.txt) |
| A-06-03 | 授权先于缓存；缓存错误不变成功；不重复 Handler/扣费/token；资格不扩大 | plan 拒绝和成员撤权时 get=0；u1/u2 同参缓存隔离；success/error/timeout 快照保留，原对象修改不污染缓存，Handler=1、命中 chargeable_tokens=0；坏缓存/读异常 Handler=0；大小含产物。旧资格文件逐字相同 | 通过 | `test_policy_denial_before_any_cache_ledger_payload_read`、`test_cache_scope_isolation_and_permission_revocation`、`test_cache_snapshot_preserves_business_state_and_no_handler_or_tokens`、`test_corrupt_cache_never_falls_back_to_business`、`test_cache_bounds_include_artifacts_even_with_small_summary`；[源检查](tool-unification-evidence/06-source-checks.json)；原 cache TTL/容量/资格测试均通过 |
| A-06-04 | succeeded 是调用完成；业务错误可恢复；回放额外 Handler=0；uncertain/running 不重试；拒绝不 uncertain；取消不成功 | Actor 经真实 Chat/Runtime/lifecycle/serializer 两次消费仅 Handler=1、complete=1；成功和业务 error 均恢复产物/retry/token/thinking。running/in_progress/uncertain 两次尝试 Handler=0。写入/序列化故障保留 running，换新 executor 模拟恢复仍 uncertain，Handler=0。取消继续抛出，complete/成功审计=0 | 通过 | `test_actor_completed_roundtrip_handler_zero_extra_and_current_audit`、`test_actor_unknown_effects_do_not_retry`、`test_failures_observable_and_never_redo_business[completion/payload]`、`test_cancellation_payload_remains_cancelled_and_actor_does_not_complete`、`test_legacy_shell_of_cancelled_result_is_never_success`；[集成日志](tool-unification-evidence/06-integration.txt) |
| A-06-05 | 正常/拒绝/cache/replay 原审计状态、字段、次数；展示/审计异常不重做业务，故障可观察 | 实际原 writer 的 mock DB 收到 error/error/denied，身份/关联字段准确，无新增数据库列；回放通过同 writer 关联日志区别，收费 token 为 13/0/0。ToolLoop 四次消费两次 Handler，cache/error 状态一致，模型 token 按轮累计而非按工具倍增。Chat 审计调度/数据库/展示异常和 ToolLoop 产物异常均可观察；本批两个已完成结果在首个展示失败时各交一次审计 | 通过 | `test_actual_audit_writer_preserves_database_contract_and_replay_log`、`test_loop_audit_once_per_consumption_and_model_tokens_once_per_turn`、`test_failures_observable_and_never_redo_business`、`test_loop_delivery_failure_still_audits_completed_calls_once`；[修复前复现](tool-unification-evidence/06-loop-delivery-before.txt)、[最终集成日志](tool-unification-evidence/06-integration.txt) |
| A-06-06 | 具体回退版本、读写顺序、限制、隔离载荷恢复演练 | R0 明确为 cdba58f9018ff45be2ebde0b802471ca634d8727，AST 实测可降级读取且确认字段丢失；R1 为当前源码指纹对应的兼容版本，完整恢复在 writer 回到 0 时仍通过，正式 SHA 待提交部署记录。JSON 文件写入 pytest tmp_path；未运行生产迁移或故障注入 | 通过 | `test_cross_version_rollback_shell_and_information_limits` 实際 file write/read + 旧函数执行；[接口与限制](TOOL_UNIFICATION_PERSISTENCE_06.md)、[指纹](tool-unification-evidence/06-source-checks.json) |
| G-01 | 限于本块，无隐含公共协议/业务重写 | 文件对应表与 Git 源检查通过；schema、risk/cache qualification、Handler、Actor lease/状态/RPC/表结构均无变化；仅本块配置/结果/缓存/审计增量 | 通过 | 本记录第 1 节；[check-06.py](tool-unification-evidence/check-06.py)、[检查结果](tool-unification-evidence/06-source-checks.json) |
| G-02 | 本块 A 项全部执行，含失败/拒绝/必要边界 | A-06-01～06 全部有独立用例，新增 76 个场景无失败/跳过；未用 mock 成功替代生产验证 | 通过 | [集成日志](tool-unification-evidence/06-integration.txt) |
| G-03 | 相关回归通过；旧预期变更有依据，不删除/跳过正确断言 | 469 集成 + 1555 扩大回归 + 11 独立 ERP = 2035 passed，0 failed/error/xfail；2 个旧字体平台检查未验证。初轮 31 个旧目标断言已按新契约补全；1 个新增异常分支漏 import 已修复；未增加 skip | 通过 | [集成](tool-unification-evidence/06-integration.txt)、[回归](tool-unification-evidence/06-regression.txt)、[ERP](tool-unification-evidence/06-erp.txt)、[未改基座环境对照](tool-unification-evidence/06-baseline-environment.txt)；问题清单 |
| G-04 | 工具名/schema/合法别名、旧 API/两种投影和 WS/Actor content blocks 保持 | Registry/Policy、工具 schema 和旧 executor API 文件逐字未变；旧 API 默认仍返回原类型/投影；18 份既有 WS/Actor 完整协议 golden 对照及当前消费、表单/图片/ERP/媒体测试通过。新 audit 字段仅内部可选增量，不进入 WS/表结构 | 通过 | [源检查](tool-unification-evidence/06-source-checks.json)；[test_tool_result_consumption.py](../../backend/tests/test_tool_result_consumption.py)、原 schema/文件/媒体/Actor 测试 [回归日志](tool-unification-evidence/06-regression.txt) |
| G-05 | 接口、调用点、证据、限制/回退及下一块前置可交接 | 本记录、06 接口及共同交接均已更新；R0 有损/R1 完整回读边界与 0→1→0 顺序明确。当前未提交/部署/关闭，07 仍须等待用户验收关闭并确认 main | 通过 | [06 接口](TOOL_UNIFICATION_PERSISTENCE_06.md)、[共同交接](TOOL_UNIFICATION_HANDOFF.md) |

## 3. 环境、命令与摘要

Python 3.14.2 / pytest 9.0.3，使用现有项目 `.venv`。运行器拒绝任务目录的 `.env`、`backend/.env`，设置 APP_ENV=testing、测试占位 JWT，数据库/Redis 指向 `127.0.0.1:1`。复用 `/private/tmp/tool05-testdeps` 中前置任务为测试安装的已声明依赖，未修改共享 venv 或项目依赖。

```bash
cd /Users/wucong/EVERYDAYAIONE/worktrees/tool-unification-06
bash docs/document/tool-unification-evidence/run-06.sh
/Users/wucong/EVERYDAYAIONE/.venv/bin/python docs/document/tool-unification-evidence/check-06.py \
  > docs/document/tool-unification-evidence/06-source-checks.json
git diff --check
```

最终一次受影响集成组复验（去除旧载荷伪造 origin、并统一 raw writer 安全边界后）沿用 run-06.sh 第一条 pytest 的全部 13 个测试文件及环境，覆盖原文件中的 76 个新增测试：**469 passed**；未受这一修改影响的扩大组 **1555 passed、2 skipped**，独立 ERP **11 passed**。共 **2035 passed、0 failed/error/xfail、2 skipped**。中间运行和基座对照不累加进最终通过数。

未验证的平台项：原 `test_emit_three_engines.py` 的 `test_chinese_aliases_registered_to_real_font` 与 `test_alias_registration_dedupes_on_second_call`，本机没有 Linux 文泉驿字体；属于未修改字体机制，不是本块必需 A 项。原 `test_tool_invocation_uncertain_integration.py` 要求显式隔离 PostgreSQL，初轮及基座对照均按原条件 skipped；本轮未创建该数据库，**不计其为通过**。本块使用 LocalLedger/mock DB、隔离 JSON 文件验证恢复；数据库 RPC/枚举不变有 AST/源码证据。

警告：pytest-env 未安装的 `env` 配置提示（脚本已显式设环境）；KIE Pydantic 弃用；旧 AsyncMock 未 await、Matplotlib 缺中文字形及 IPython 弃用。未改 R0 的 git archive 在独立 `/private/tmp/tool06-baseline-*` 对照为 **69 passed、3 skipped、13 warnings**，准确命令和路径见 [基座环境日志](tool-unification-evidence/06-baseline-environment.txt)。没有把这些警告称作生产验证结果。

## 4. 问题清单

| 问题编号 | 复现 | 根因/影响 | 所属板块 | 处理结果 | 复验证据 |
|---|---|---|---|---|---|
| P-06-01 | R0 新扩展回读 tokens/thinking/metadata 为缺省，FileRef/form/image 类型丢失 | 原 reader 只认识摘要外壳，不能支持无损代码回退 | 06 | 保留外壳并证明其限度；默认旧写，完整回退必须保留 R1 reader，禁止宣称可直接完整回退 R0 | A-06-01/06；跨版本测试全部通过 |
| P-06-02 | 原 Chat 累加每个 ToolResult 的 tokens；ToolLoop 每条 hook 重复传本轮 prompt/completion | 复用结果和本次执行的计费事实未区分 | 06 | chargeable_tokens 对复用为 0，原 token 留存；模型轮次 token 只归属第一条审计 | Actor 两次消费累计 33；Loop 两轮总 prompt=14/completion=6，与实际模型轮次一致 |
| P-06-03 | 2 个 Handler 已完成，模拟首个 `_register_result_files` 抛错，审计 entries=0 | 展示阶段先失败，后续审计未执行 | 06 | 异常收口记录失败，为未尝试审计的已完成结果各提交一次原 writer；不重跑 Handler | [修复前](tool-unification-evidence/06-loop-delivery-before.txt)；最终 `test_loop_delivery_failure_still_audits_completed_calls_once` 通过 |
| P-06-04 | 初轮相关组 31 failed / 362 passed / 1 skipped | 21 项完整 audit 字典缺新执行字段，9 项要求旧 serializer 拒 ToolResult，1 项要求命中返回同一对象；均与本块目标冲突 | 06 验证 | 保留全部业务、投影和协议断言；精确新增 execution 字典、默认 v0 payload 相等断言、快照隔离和状态/metadata/token 相等断言；未删测试/新增 skip | [初轮日志](tool-unification-evidence/06-first-regression.txt)；最终 469 全通过 |
| P-06-05 | 首次异常收口复验 1 failed / 451 passed | 新增 CancelledError except 分支漏 import asyncio | 06 实现 | 补 import；同失败场景及全部受影响集成复验通过 | [失败日志](tool-unification-evidence/06-integration-before-fix.txt)；最终集成日志 |

未关闭的本块代码问题：**无**。生产写入开关、真实会话用户验收、生产日志关联可见性尚待下节执行，不冒充已完成；它们不由本地 mock 自动证明。

## 5. 用户验证单

待记录字段：R1 候选 SHA、全部 reader 部署版本、写入设置 0/1、指定测试会话/任务 ID、tool_call_id、用户观察结果。由实际“提交部署”交付消息及用户验收补齐，不用基座 SHA 代替。

1. 先发布 R1 保持 writer=0，确认 Web/Actor reader 均为相同兼容版本。使用指定测试会话和已授权小文件检查原正常调用、业务失败及产物显示。
2. 全部 reader 到位后，在受控后续配置发布中启用 writer=1。对指定无危险副作用样本或获准测试资源保留一次已完成结果；按照既有可控会话恢复流程观察原产物/错误/重试信息，核对原 tool_call_id 的 invocation 是 succeeded、业务 status 仍正确，Handler/业务动作没有新增。
3. 对照 tool_audit_log 与 `Tool audit execution` 关联日志：正常、拒绝、缓存和回放状态一致；回放日志 replayed=True、is_cached=False，新增收费 token=0；ToolLoop 多工具本轮模型 token 只计一次。缓存命中 error 仍显示失败。
4. 恢复演练先使用本记录的本地测试命令（会读写隔离 JSON，不改数据库）；生产数据不用于回退迁移试验。无需做生产故障注入，也不要人为重试 uncertain。真实写入、删除或付费生成仅在另有明确授权和指定测试资源时验证。

可观察限制：writer=0 时新调用仍只按旧格式恢复；R0 reader 不支持 v1 的完整字段；原表无 replay 列，须结合日志；审计/ledger 故障仍可能丢记录或阻止恢复；引用文件/URL 可能过期。不能将 best-effort 写入失败解释成零丢失保障。

## 6. 结论与交接

A-06-01～06、G-01～05 的本地技术标准均通过。当前 **技术验收通过，待提交部署及用户验收**；未推送、部署、合并、清理工作树或改生产数据。

板块 07 的代码接口前置已准备好；流程前置尚未满足。必须按 reader-first 次序部署并由用户核验指定会话 → 用户明确验收关闭 → 受控入口确认 main 包含最终测试代码树，才允许启动 07。本任务只完成 06。
