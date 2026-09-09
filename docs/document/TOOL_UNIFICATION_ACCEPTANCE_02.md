# 工具统一 02：技术验收记录

## 1. 范围与版本

状态：**技术验收通过，待部署/用户验收**。仅完成纯 ToolPolicy 与分批基础，没有生产循环切换。

| 项目 | 记录 |
|---|---|
| 基准提交 | `2e8fdb2db242366b790cc623703d5a65dd574632`，受控 start 同步的最新 origin/main |
| 分支 | `codex/task/20260909155118-tool-unification-policy` |
| 当前 HEAD | 同基准；尚不含本块未提交实现，不冒充被测候选 |
| 工作树 / 测试执行目录 | `/Users/wucong/EVERYDAYAIONE/worktrees/tool-unification-policy` |
| 前置核验 | main 的 `2e8fdb2d` 合入板块 01 `b4c854ac`，祖先关系成立，两个 tree 均为 `b266cdaf38643f4517c07f8091bfcbdb483e780b`；用户明确确认已验收。已读交接与实际 ToolSpec/Context/Registry、ERP ApiEntry、任务授权/提案接口，未沿用旧调查符号 |
| 被测版本 | 基准加本任务未提交的 8 个 tools 模块（其中 3 个新增、5 个修改）、新增 test_tool_policy.py；各文件 SHA256 见 [源检查记录](tool-unification-evidence/02-source-checks.txt) |
| 文档差异 | 更新 TOOL_UNIFICATION_HANDOFF.md；新增本记录、TOOL_UNIFICATION_POLICY_02.md、02 测试日志/复验脚本/检查证据。板块 01 历史记录保留 |
| 发布候选 | 未提交/未部署。用户明确提交部署后记录实际候选 SHA，并核验候选工具源码/测试与此处指纹一致；本次没有推送、合并或清理 |

实际接口和完整权限预期表见 [权限矩阵与决策接口](TOOL_UNIFICATION_POLICY_02.md)，下一板块约束见 [交接](TOOL_UNIFICATION_HANDOFF.md#板块-02-实际增量)。

## 2. 逐项证据表

`P::` 指 [backend/tests/test_tool_policy.py](../../backend/tests/test_tool_policy.py)，`R::` 指 [backend/tests/test_tool_registry.py](../../backend/tests/test_tool_registry.py)。所有参数化用例名称与结果保存在 [02-isolated.txt](tool-unification-evidence/02-isolated.txt)，准确文件集合/参数/测试专用环境见 [run-02.sh](tool-unification-evidence/run-02.sh)。以下状态仅针对本板块技术要求。

| 验收编号 | 场景及预期 | 实际结果 | 状态 | 证据 |
|---|---|---|---|---|
| A-02-01 | 明确预期表验证 ask/auto/plan × 读取、业务写入、生成、提案，叠加 scheduled/preflight 授权；auto 仍需危险确认，plan 禁业务写和生成 | 9 代表调用 × 3 模式 × 3 场景共 81 行逐行断言；同一行校验 Registry allowed/advertised。safe restore 写仍按旧风险允许交互但 plan/preflight 拒绝；任务六 action 独立验证，无重复确认；48 行验证非交互上下界缺失/畸形/空/越界，另验证名字不能新增 scheduled 能力 | 通过 | P::test_mode_authorization_matrix；P::test_noninteractive_requires_both_trusted_scope_bounds；P::test_task_actions_only_propose_without_second_confirmation；P::test_confirm_risk_is_resource_notice_not_approval；P::test_no_mode_can_override_reviewed_operation_or_danger；P::test_authorized_names_do_not_expand_existing_scheduled_capabilities；[完整预期表](TOOL_UNIFICATION_POLICY_02.md#完整自动断言表) |
| A-02-02 | 危险未确认需确认/拒绝；批准只绑定调用/参数/作用域；缺确认设施不得批准 | ask/auto 未批均 require_confirmation；批准精确匹配才 allow；拒绝、布尔/JSON 伪造、缺设施、缺作用域均 deny。调用/会话/任务/用户/组织/群工作区/模式/入口/授权/资源/开关改变不可复用；参数对象键顺序不影响，目标/参数/Spec 变化失效；等待后的权限收紧仍优先拒绝 | 通过 | P::test_danger_confirmation_matrix；P::test_confirmation_cannot_cross_call_user_parameters_or_scope；P::test_confirmation_parameters_are_canonical_and_snapshot_is_immutable；P::test_current_access_rechecked_even_with_approval；P::test_batches_bind_each_confirmation_and_freeze_arguments；P::test_same_dangerous_spec_new_and_legacy_have_identical_confirmed_decisions；[确认约束](TOOL_UNIFICATION_POLICY_02.md#3-确认约束)。未执行真实超时/断连：属于板块 04，不作为本块纯规则证据 |
| A-02-03 | query 携写 action 必拒；读 action 合法时允许；未知工具/动作/分类拒绝 | 遍历实际 query 注册表 166 个 action，每个验证仅 action 文档请求及 action+params 执行形态；写项均 deny，读项 allow。修改 ApiEntry.is_write 后立即拒绝，无需改 Policy 名单。逐分类遍历 erp_execute 读/写及批准后参数变更；fetch_all_pages 全动作读保护；有效 execute 批准不能用于 query 写 | 通过 | P::test_every_erp_query_action_uses_api_entry_write_fact；P::test_action_fact_changes_are_used_without_updating_policy_list；P::test_erp_execute_category_and_known_writes_require_bound_confirmation；P::test_erp_query_write_is_denied_even_with_confirmation；P::test_fetch_all_pages_retains_read_only_action_guard；P::test_erp_unknown_and_write_actions_never_default_allow；P::test_unknown_task_action_is_denied；P::test_unreviewed_spec_and_dangerous_action_fail_closed；P::test_unknown_action_in_custom_dangerous_enum_is_denied |
| A-02-04 | A/B 读、C 写、D 读、E 写 => [A,B]→[C]→[D]→[E]，不越屏障；Spec 不可并行不能合批 | 批次数、每批 ID、完整展开顺序、全部 allow 状态均断言；写故意声明 parallelizable=True 仍串行。看似读但 False、未知、待确认、生成、提案分别单批；拒绝/待批仍保留位置；重复 ID 拒绝、空输入为空 | 通过 | P::test_batch_order_and_write_barriers_even_if_write_declares_parallel；P::test_nonparallel_pending_denied_unknown_and_generation_are_barriers；P::test_batches_bind_each_confirmation_and_freeze_arguments。只验证纯分组，没有宣称真实运行的并发/锁已切换 |
| A-02-05 | 新/Legacy 同规则；可信上下文不能被模型 JSON 覆盖；Policy 不调用 WS/Handler/重试，生产未切；缓存/副作用不从并发推导 | 81 行完整矩阵分别用 explicit/legacy 重验（162 行），并比较危险确认后的完整结果。模型模式、身份、授权、确认字段均不能扩权；两路径共享 check_access 的同一目录拒绝理由。Handler/ERP/缓存陷阱无调用；权限快照异常传播且只调用一次。并发×缓存四组合独立断言 effects/replay 原值；生产目录无反向导入且代码差异为空 | 通过 | P::test_equivalent_new_and_legacy_specs_share_all_matrix_rows；P::test_same_dangerous_spec_new_and_legacy_have_identical_confirmed_decisions；P::test_model_json_cannot_override_trusted_context_or_confirm；P::test_business_permission_snapshot_is_explicit_and_fail_closed；P::test_registry_and_policy_share_all_unavailability_facts；P::test_policy_never_executes_ws_handler_retry_or_swallows_errors；P::test_cache_effects_replay_are_never_inferred_from_concurrency；R::test_catalog_import_has_no_reverse_production_dependency；[源检查](tool-unification-evidence/02-source-checks.txt) |
| G-01 | 变更逐项对应本块，无公共业务协议漂移、重写或后续混入 | 3 个新增规则/决策模块；5 个已有基础模块只加元数据、上下文确认标志、共享目录判定/导出；测试、矩阵、交接。没有 Dispatcher、ToolResult、业务 Handler、模型循环、UI、缓存、审计、数据库或持久化修改。默认 unknown 规则拒绝等内部增量均有说明 | 通过 | 第 1 节范围、[接口](TOOL_UNIFICATION_POLICY_02.md#2-对外接口与事实来源)、[源检查中的 TRACKED_BACKEND_DIFF/PRODUCTION_ENTRYPOINT_DIFF](tool-unification-evidence/02-source-checks.txt) |
| G-02 | 本块 A 项成功/拒绝/边界均执行 | 上述 A-02-01～05 均有逐用例有效证据；非 JSON 参数、未知声明也拒绝。无仅正常路径或必需项证据缺失 | 通过 | A 表及 P::test_invalid_normalized_arguments_fail_closed、P::test_invalid_policy_declarations_are_rejected；[逐用例日志](tool-unification-evidence/02-isolated.txt) |
| G-03 | 新增与现有相关测试通过，不删除/弱化正确断言 | 552 新 + 98 Registry + 516 相关旧入口 + 11 独立 ERP = 1177 passed；0 failed/error/skipped。既有测试文件零差异。ERP 收集隔离沿用板块 01 已复现证据，完整独立进程复验 | 通过 | [复验脚本](tool-unification-evidence/run-02.sh)、[新层](tool-unification-evidence/02-isolated.txt)、[旧入口](tool-unification-evidence/02-regression.txt)、[ERP](tool-unification-evidence/02-regression-erp.txt)、[源检查 EXISTING_TEST_DIFF](tool-unification-evidence/02-source-checks.txt) |
| G-04 | 工具名/schema/合法旧参数别名、旧 API/投影与 WS 协议兼容 | 98 个 Registry 用例含全目录 schema 深比较、3 代表逐字段、全工具参数读取与 files/file_ids/query/task 别名；旧 executor、args、chat、loop、result/output、WS confirmation 测试通过。原 config/agent/handlers/ERP/scheduler 代码树零差异，未改 WS content blocks 或载荷 | 通过 | R::test_every_legacy_definition_readable_by_name_and_complete_schema；R::test_three_representative_parameter_contracts_field_by_field；R::test_parameter_reading_and_legacy_aliases_unchanged；R::test_every_public_tool_parameter_reader_matches_original；[相关旧入口日志](tool-unification-evidence/02-regression.txt)、[源检查](tool-unification-evidence/02-source-checks.txt) |
| G-05 | 接口、调用点、证据、限制、回退、下一块前置可交接 | Policy/Registry 调用关系、确认凭据的服务端信任前提、scope/参数绑定、业务权限边界、矩阵及日志均落盘。无新载荷；回退增量到 2e8fdb2d 保留板块 01。仅代码前置具备，用户验收关闭前不启动 03 | 通过 | [交接](TOOL_UNIFICATION_HANDOFF.md#板块-02-实际增量)、[决策接口](TOOL_UNIFICATION_POLICY_02.md)、本记录第 5/6 节 |

## 3. 测试环境与摘要

准确执行命令（在第 1 节工作树根目录）：

```bash
bash docs/document/tool-unification-evidence/run-02.sh
```

脚本保存三个实际 pytest 命令及完整文件清单，各用 `-o addopts='' -v --tb=short`，未禁用警告。解释器 `/Users/wucong/EVERYDAYAIONE/.venv/bin/python`（Python 3.14）；测试目录没有 .env/backend/.env；APP_ENV=testing，数据库测试占位地址为 127.0.0.1:1，Redis 端口 1，JWT 为测试占位。没有加载生产配置、运行外部 ERP、真实删除或付费生成。

| 测试组 | 结果 | 日志 |
|---|---|---|
| 新 Policy + 板块 01 Registry | 650 passed，0 failed/error/skipped，2 warnings | [02-isolated.txt](tool-unification-evidence/02-isolated.txt) |
| 相关旧入口、权限/任务、WS、结果/审计/缓存契约 | 516 passed，0 failed/error/skipped，4 warnings | [02-regression.txt](tool-unification-evidence/02-regression.txt) |
| 既有 ERP Mixin（独立进程） | 11 passed，0 failed/error/skipped，2 warnings | [02-regression-erp.txt](tool-unification-evidence/02-regression-erp.txt) |

初轮新层 639 passed（[初轮日志](tool-unification-evidence/02-isolated-initial.txt)）。自审补充 11 项已授权边界断言后最终为 650；初轮无失败，未删除或削弱任何旧断言。最终总计 **1177 passed**。历史 437/712 只作上下文，不作为本块阈值。

警告：环境未安装 pytest-env，env 配置项不被识别（本轮显式 export，不依赖该插件）；KIE Pydantic class Config 弃用；引入旧 WS 测试时另有 FastAPI/Starlette 的 asyncio.iscoroutinefunction 弃用警告。都在日志保留，未影响断言结果。现有 ERP 测试的收集级 sys.modules stub 污染已有板块 01 基准复现，本轮未改该文件，按已证实的独立进程方式执行，见问题清单。

静态/自审：`git diff --check`、`bash -n docs/document/tool-unification-evidence/run-02.sh`、新层与新增测试 AST 解析/空白检查通过；检查所有生产 config/services 下的反向导入；核对 Git 基准与源 SHA256；检查信任边界、ERP 动作路由、确认失效、分批和独立 metadata 声明。结果见 [02-source-checks.txt](tool-unification-evidence/02-source-checks.txt)。未发现未处理的高置信问题。

## 4. 问题清单

本块新增实现遗留问题：**无**。没有必需技术验收项未验证。

| 问题编号 | 复现 | 根因/影响 | 所属板块 | 处理结果 | 复验证据 |
|---|---|---|---|---|---|
| P-01（继承） | 板块 01 已在无新实现基准联合收集 test_tool_executor 与 test_erp_tool_mixin_unit，复现 16 个 fixture errors | 旧 ERP 测试在收集时用空 sys.modules stub 替换组织配置模块；属于测试进程隔离问题，不是新 Policy 行为。当前该测试内容与已复现版本一致 | 既有测试，01 发现 | 保留原测试与断言，本轮仍分进程完整执行。不声称修复测试框架；不阻塞本块，因为所有必需用例都有通过证据 | [01 原问题及基准命令](TOOL_UNIFICATION_ACCEPTANCE_01.md#4-问题清单)、[原始复现日志](tool-unification-evidence/01-baseline-reproduction.txt)、[02 ERP 11 passed](tool-unification-evidence/02-regression-erp.txt) |

真实确认事件认证/超时/断连、运行并发和生产业务权限源接入均为已明确的板块 04 范围，不作为本块已完成项；没有将它们以 mock 通过关闭。

## 5. 用户验证单

部署版本：**待用户“提交部署”后记录实际候选 SHA**。本块为内部基础能力，生产界面/运行行为应与部署前一致；用户主要核对规则本身，不用危险操作验证纯 Policy。

| 步骤 | 前提/操作 | 预期结果 | 用户实际记录 |
|---|---|---|---|
| 1 权限矩阵 | 阅读 [权限矩阵](TOOL_UNIFICATION_POLICY_02.md#1-用户核对矩阵)，检查 ask/auto/plan 与提案含义 | auto 不免危险确认；plan 禁业务写与生成；任务表单/提案不多弹一次确认；资源消耗通知保持原含义 | 待用户核对 |
| 2 场景与确认 | 检查 scheduled/preflight 表、确认绑定及失效表 | 名字上界不产生危险写授权；缺设施拒绝；改变参数、用户或作用域不能复用批准；restore 风险兼容行为已明示 | 待用户核对 |
| 3 纯规则复验（可选重跑） | 在隔离工作树执行 run-02.sh，查看逐用例日志与 [A,B]→[C]→[D]→[E] 断言 | 本记录全部断言通过；不连接真实危险业务 | 待用户核对证据 |
| 4 部署后普通只读检查 | 仅在用户指令提交部署后，于原测试工作区列文件/搜索已有文件 | 原入口与展示正常；不声称这是新 Policy 的生产效果 | 待确定候选及用户结果 |
| 5 验收关闭 | 记录确定候选和核对结果；用户确认通过后明确“清理工作树” | 受控关闭验证 main 最终代码树与生产测试候选一致；完成后才允许进入板块 03 | 待用户指令 |

未执行真实删除、ERP 写入、付费生成、生产配置/部署操作。真实外部副作用不是本块用户验证必需项；如后续确需执行，使用对应授权与指定测试资源。

## 6. 结论与交接

**A-02-01～05 与 G-01～05 技术验收通过，待部署/用户验收。** 实现、定向矩阵、既有回归、自审和交接资料齐备；没有生产入口切换，没有提交/推送/部署/合并/清理。

回退：撤回本块新增 policy/action_rules/legacy_policy、测试与本块文档增量，恢复 services/tools 的五个修改文件到 `2e8fdb2d`。不撤回已验收板块 01；无数据库、持久化、WS 新载荷或缓存变更，无需数据迁移或额外开关。

板块 03 的隔离代码前置已具备；流程仍缺本块确定候选部署、用户验收、受控关闭及 main 包含成果的核验。用户验收关闭之前，不启动下一块。
