# 工具统一 01：技术验收记录

## 1. 范围与版本

状态：**技术验收通过，待部署/用户验收**。仅完成板块 01，没有生产入口切换或板块 02 实现。

| 项目 | 记录 |
|---|---|
| 基准提交 | `051b24c5715c6c0d0086dcc90ef44a2344296793`，受控 start 同步的最新 origin/main；未沿用调研基准 80198c91 |
| 分支 | `codex/task/20260909142551-tool-unification-01` |
| 当前 HEAD | `051b24c5715c6c0d0086dcc90ef44a2344296793`，此 HEAD 尚不含新增实现 |
| 执行目录 | `/Users/wucong/EVERYDAYAIONE/worktrees/tool-unification-01` |
| 被测版本 | 基准加当前未提交新增文件，代码/测试指纹见 [01-source-checks.txt](tool-unification-evidence/01-source-checks.txt) |
| 差异范围 | 新增 backend/services/tools/{spec,context,registry,legacy,__init__}.py、backend/tests/test_tool_registry.py，以及本块交接/清单/验收和证据文件；没有既有生产或测试文件修改 |
| 初始前置核验 | 未找到等效 ToolSpec/ToolContext/ToolRegistry，也无已有 TOOL_UNIFICATION_HANDOFF.md；旧 config.tool_registry 为语义筛选表、planner.CapabilityRegistry 为规划能力表，不重复用作运行目录 |
| 发布候选 | 尚不存在；用户提交部署后才记录实际 SHA，需核对候选与此处被测文件指纹及基准差异 |

实际接口与职责见 [交接文档](TOOL_UNIFICATION_HANDOFF.md#板块-01-实际接口)，全部工具逐项清单见 [目录和三代表契约](TOOL_UNIFICATION_CATALOG_01.md)。

## 2. 逐项证据表

以下 `T::` 均指 [backend/tests/test_tool_registry.py](../../backend/tests/test_tool_registry.py) 的完整用例名；每个参数化用例的结果见 [98 项隔离日志](tool-unification-evidence/01-isolated.txt)。准确 pytest 文件集合与选项保存于 [run-01.sh](tool-unification-evidence/run-01.sh)，本轮实际分别执行其中相同的三条 pytest 命令。

| 验收编号 | 场景及预期 | 实际结果 | 状态 | 证据 |
|---|---|---|---|---|
| A-01-01 | 当前完整 schema/真实 handler 一一绑定；明确公开/内部范围；拒绝重复、遗漏、缺字段；不公开内部工具 | 33 公开 schema + 2 handler-only 均有 Spec；旧个人目录 15 项逐名完整比较。故意注入重复 schema、缺少 file_delete、额外名称、丢失 handler 与缺字段均得到明确错误。fetch 内部工厂保留，conversation 仅保留部分校验条目 | 通过 | T::test_catalog_matches_actual_full_directory_and_handler_bindings；T::test_every_legacy_definition_readable_by_name_and_complete_schema；T::test_coverage_detects_duplicate_missing_extra_and_field_drift；T::test_duplicate_registration_and_unknown_tool_fail_explicitly；T::test_required_spec_fields_are_validated；T::test_missing_domain_is_reported_during_catalog_build；[35 项目录](TOOL_UNIFICATION_CATALOG_01.md) |
| A-01-02 | 三代表 name、逐参数 type/required/enum/items/pattern/description 一致；保留合法 files/file_ids、query/task 旧读取；其余旧定义完整目录验证 | 三代表逐字段和完整 schema 通过；全 33 schema 按名称深比较并以完整/空参数分别对照旧读取器；每个旧部分验证 schema 独立比较，未将 files 必填错误合入公开 file_delete；未新增内部公开 schema | 通过 | T::test_three_representative_parameter_contracts_field_by_field；T::test_parameter_reading_and_legacy_aliases_unchanged；T::test_every_public_tool_parameter_reader_matches_original；T::test_partial_legacy_validation_directory_is_preserved_separately；T::test_internal_existing_schema_is_preserved_without_new_schema；[代表表](TOOL_UNIFICATION_CATALOG_01.md#三代表公开参数逐字段对照) |
| A-01-03 | 个人/组织/群工作区、general/erp/shared、功能开关、授权上界分别允许和拒绝；连续上下文不串用户/组织/模式 | 个人无组织 ERP 被拒；组织 ERP 允许；群允许工作区查询、拒绝个人任务/会话；双向域隔离/shared 共用；False/缺失开关拒绝；空授权集拒绝、名单不能越过 Policy；用户/组织/工作区/模式变化均重新决议，不复用旧授权快照结果 | 通过 | T::test_scope_and_domain_allow_deny_matrix；T::test_feature_snapshots_are_required_and_isolated；T::test_untrusted_or_inconsistent_context_rejected；T::test_authorization_names_are_only_a_restriction；T::test_sequential_requests_do_not_reuse_user_org_workspace_or_mode；T::test_mode_changes_recompute_policy_access_not_just_advertisement；T::test_schema_context_and_result_snapshots_cannot_leak_mutations |
| A-01-04 | advertised 为 allowed 的当前展示子集；动态发现不扩权；内部边界不因持有 schema 改变 | 旧 ask/auto/plan 核心展示结果在测试允许策略下完全一致；拒绝的媒体、跨域 ERP、未知名称、handler-only 均不能发现扩入；ERP 初始/动态集合受交集约束；scheduled/preflight 不动态扩展；内部允许入口也不输出内部 schema | 通过 | T::test_advertisement_reuses_current_core_rules；T::test_discovery_cannot_expand_permission_or_expose_internal_tools；T::test_handler_only_entry_boundary；T::test_erp_initial_and_dynamic_selection_is_filtered_by_allowed；T::test_frozen_scenarios_do_not_discover_more_tools |
| A-01-05 | Spec/Context/Registry 输入输出与 Policy 边界明确；四类元数据独立；旧生产 get_chat_tools/execute 及相关测试原样 | 必需 ToolAccessPolicy.resolve_access 接口，名单本身不授执行权限，异常无默认放行；上下文全部透传；独立风险/并行/缓存/副作用断言通过。原生产文件字节不变，无反向导入；相关旧测试全通过 | 通过 | T::test_mode_and_authorization_decisions_have_one_policy_owner；T::test_policy_required_and_failures_never_fall_back；T::test_risk_parallel_cache_and_effects_are_independent；T::test_catalog_import_has_no_reverse_production_dependency；[源检查](tool-unification-evidence/01-source-checks.txt)；[603 项既有回归](tool-unification-evidence/01-regression.txt)；[11 项 ERP 回归](tool-unification-evidence/01-regression-erp.txt)；[接口文档](TOOL_UNIFICATION_HANDOFF.md#板块-01-实际接口) |
| G-01 | 实际修改与板块目标对应，无未说明公共契约、业务重写或后续板块实现 | 仅新增定义/快照/目录/权限协议/兼容读取和隔离测试；未实现生产 Policy/Dispatcher/Result，也未切入口、迁移数据库或改 UI | 通过 | 本文范围表；[源检查](tool-unification-evidence/01-source-checks.txt)；交接文件职责表 |
| G-02 | 本块所有 A 项覆盖成功、拒绝/失败及必要边界 | A-01-01～05 各有定向用例，包含畸形定义、未知工具、策略异常、空授权、伪造发现、请求切换、内部入口、完整字段比较 | 通过 | 上述逐项映射；[隔离日志](tool-unification-evidence/01-isolated.txt)全部 98 passed |
| G-03 | 新增与既有相关测试通过；失败有基准复现和处理，不能删/跳/削弱断言 | 98 新增 + 603 主组 + 11 ERP 组通过，0 failed/error/skipped。首次联合收集出现旧 sys.modules 污染，在无本次代码的基准复现；隔离进程完整复验通过，没有修改任何旧断言 | 通过 | [初次联合运行](tool-unification-evidence/01-regression-combined-initial.txt)；[基准复现](tool-unification-evidence/01-baseline-reproduction.txt)；[最终主组](tool-unification-evidence/01-regression.txt)；[最终 ERP 组](tool-unification-evidence/01-regression-erp.txt)；问题 P-01 |
| G-04 | 名称、合法 schema/参数及旧 API/返回/WS 兼容；未涉及协议说明边界 | 逐名称完整定义及旧 validator 比较；现有 ToolExecutor/Chat/ToolLoop/文件 ID/多模态/结果信封/ToolOutput/invocation/audit 用例全过。返回/WS/持久化实现均未修改，本块没有新协议或真实入口接入效果 | 通过 | A-01-02；[回归文件与命令](tool-unification-evidence/run-01.sh)；最终回归日志；源检查 |
| G-05 | 接口、调用点、验收证据、限制、回退、下一块前置可交接 | 交接与本记录已建立，列明 schema/validation 差别、Policy 必需、可信输入边界、旧语义保留及后续前置。无持久化变化，撤回新增项可回到基准生产代码树 | 通过 | [交接文档](TOOL_UNIFICATION_HANDOFF.md)；[目录](TOOL_UNIFICATION_CATALOG_01.md)；本文问题、用户验证和结论 |

## 3. 测试环境与摘要

- Python 3.14.2，pytest 9.0.3，pytest-asyncio 1.3.0，现有 `/Users/wucong/EVERYDAYAIONE/.venv/bin/python`；未安装新依赖。
- 工作树 root 与 backend 均无 `.env`，未复制/读取生产配置。显式 `APP_ENV=testing`，DATABASE_URL 指向 `127.0.0.1:1` 测试库、JWT 为测试占位、REDIS_PORT=1；业务服务使用既有 mock，不访问生产 ERP/数据库。
- 对所有测试覆盖的 Mock/临时工作区行为只声称本地证据，不等同外部服务/生产验证。
- 最终命令（工作树根）：[run-01.sh](tool-unification-evidence/run-01.sh) 完整保存三条实际 pytest 命令、测试文件参数、环境和输出位置；可用 `bash docs/document/tool-unification-evidence/run-01.sh` 复现。`TOOL_TEST_PYTHON` 可指向另一个已有测试解释器。脚本拒绝有 .env 的 checkout，防止误用业务配置。

| 运行 | 通过 | 失败 / 错误 / 跳过 | 证据 |
|---|---:|---|---|
| 新层最终隔离 | 98 | 0 / 0 / 0 | [01-isolated.txt](tool-unification-evidence/01-isolated.txt) |
| 既有相关主组，27 模块 | 603 | 0 / 0 / 0 | [01-regression.txt](tool-unification-evidence/01-regression.txt) |
| 既有 ERP Mixin 单独进程 | 11 | 0 / 0 / 0 | [01-regression-erp.txt](tool-unification-evidence/01-regression-erp.txt) |
| 首次联合 28 模块（问题定位记录，不作为通过证据） | 598 | 0 / 16 / 0 | [01-regression-combined-initial.txt](tool-unification-evidence/01-regression-combined-initial.txt) |
| 无本次代码的基准最小复现（问题定位记录） | 39 | 0 / 16 / 0 | [01-baseline-reproduction.txt](tool-unification-evidence/01-baseline-reproduction.txt) |

每个最终进程均有 2 个警告：pytest-env 未安装导致配置项 env 未识别（本轮显式环境不依赖该插件）；现有 KIE Pydantic class Config 弃用。没有为通过而抑制这些最终警告。历史 437 passed 不作为本块验收阈值。

静态/结构验证：原有 tracked 文件无差异，新层无生产反向导入；不同导入顺序都可构建目录；新文件无尾随空白，复验脚本 bash 语法通过。准确输出及被测代码 SHA256 见 [01-source-checks.txt](tool-unification-evidence/01-source-checks.txt)。

## 4. 问题清单

| 问题编号 | 复现 | 根因 / 影响 | 所属板块 | 处理结果 | 复验证据 |
|---|---|---|---|---|---|
| P-01 | 同进程收集 test_tool_executor.py 与 test_erp_tool_mixin_unit.py，16 个 ERP 测试初始化失败；用 git archive HEAD backend 导出无本次实现的基准到 `/private/tmp/tool-unification-01-baseline.FNJoCX`，以相同测试配置执行 `python -m pytest backend/tests/test_tool_executor.py backend/tests/test_erp_tool_mixin_unit.py -o addopts='' -v --tb=short`，相同失败 | 旧 test_erp_tool_mixin_unit.py:19–21 在收集阶段注入空 services.org/config_resolver 模块；test_tool_executor fixture patch 真实 OrgConfigResolver 失败。不是新层执行/导入引入 | 既有测试隔离问题，板块 01 发现 | 保留旧文件和全部断言，按独立进程完成全部相关测试；问题仍可在旧联合收集方式复现，已记录限制，不声称修复全仓测试框架。不阻塞本块：所有必需旧测试均有不受模块污染的完整通过证据 | 基准日志 39 passed/16 errors；最终主组 603 passed 与独立 ERP 11 passed |

本块新增实现无未解决失败、无必需技术项未验证。真实生产/用户核验待完成，不能把此状态标为用户验收通过。

## 5. 用户验证单

待部署候选 SHA：**未部署，待用户“提交部署”后填入实际版本**。本块新目录没有接入生产，用户可观察功能应保持原样。

| 步骤 | 前提 / 操作 | 预期 | 用户实际记录 |
|---|---|---|---|
| 1 清单核对 | 打开 TOOL_UNIFICATION_CATALOG_01.md，核对 35 个 handler 对应项、三代表逐字段表、别名与独立旧验证表 | 工具名/合法参数不变；fetch_all_pages/get_conversation_context 不意外公开；旧路由/控制协议未伪装成新业务工具 | 待用户核对 |
| 2 现有可见性 | 用户指令部署后，在惯用的个人、组织和已有群会话使用原入口 | 工具展示与部署前一致；群不会增加个人会话/任务工具；不会出现新工具统一 UI | 待用户记录候选及结果 |
| 3 普通只读调用 | 在已有授权的测试工作区列文件或搜索一个已知文件；如有合适知识库资源，可用普通知识检索 | 原文件/知识检索行为和结果展示正常；新 Registry 未参与调用，不宣称验证新 Policy | 待用户执行 |
| 4 确定候选验收 | 记录版本和结果；不通过继续本块修复/复验/重新提交部署，通过后用户明确“清理工作树” | 受控验收关闭确认 main 与候选代码树一致后，才允许下一块 | 待用户指令 |

真实删除、业务写入、付费生成均不是本块用户验证必需项，未执行。需要这些外部行为时须有相应用户授权与指定测试资源；本块不以 mock 声称其生产验证通过。

## 6. 结论与交接

A-01-01～05 与 G-01～05 技术证据全部通过。实现、必要隔离/既有回归、问题定位复验、工具清单和交接已完成；状态为**技术验收通过，待部署/用户验收**。

板块 02 的代码接口前置已具备，但流程前置未满足：尚缺用户指令提交部署、用户验证、受控验收关闭和 main 包含成果的核验。没有自动提交、推送、部署、合并、清理或启动下一块。
