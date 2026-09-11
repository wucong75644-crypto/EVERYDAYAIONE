# 工具统一板块 07：验收记录

日期：2026-09-11。**技术验收通过，待提交部署及用户验收。** 本记录仅覆盖板块 07，不代表板块 08 已完成，不代表已部署或合入 main。

## 1. 范围与版本

- 工作树：`/Users/wucong/EVERYDAYAIONE/worktrees/tool-unification-07`。
- 分支：`codex/task/20260911215117-tool-unification-07`。
- 创建基准及当前 HEAD：`ca4c3d7e6a89ef34412cf405efc2d4fb0c1350e2`；**被测版本是该 HEAD 加本块未提交差异**，不是单独基准 SHA。准确 backend 文件指纹见 [07-source-checks.json](tool-unification-evidence/07-source-checks.json)。发布候选 SHA 尚未产生。
- 用户确认 01–06 已验收进入 main；六次关闭合并均为当前基准祖先、各合并 tree 与候选 tree 相同，逐项 SHA 记录于源码报告。
- 实际范围：schema 资源和元数据进入五组显式 Spec；旧 config/helper/cache 查询派生；Planner 描述派生；契约/入口测试、架构、目录、验收和交接文档。原业务 Handler、ERP/文件/沙盒内核/媒体结算/任务提交函数不变。
- [最终架构](TOOL_UNIFICATION_ARCHITECTURE_07.md)、[完整工具及别名目录](TOOL_UNIFICATION_CATALOG_07.md)。Planner 模式描述的 24 项对齐已明确记录，未扩展运行权限；无组织 ERP、code_execute 缓存、restore_file 风险/回放语义不变。

## 2. 逐项证据

测试文件以下均位于 `backend/tests/`。准确执行目录、命令和完整逐用例日志见第 3 节；不以总 passed 数代替各项证据。

| 验收编号 | 场景及预期 | 实际结果 | 状态 | 证据 |
|---|---|---|---|---|
| A-07-01 | 全公开/schema 变体/handler-only 映射；未注册/重复 0；按组推进 | 33 public + 2 internal = 35；35 handler key 一一对应；ERP 22、文件/沙盒 5、媒体 3、任务 1，通用补齐 4 | 通过 | `test_tool_definitions_07::test_full_spec_contract_unchanged`、`test_complete_helpers_handlers_and_schema_order`、`test_each_schema_resource_has_exactly_one_spec`；`test_tool_registry::test_coverage_detects_duplicate_missing_extra_and_field_drift`、`test_duplicate_registration_and_unknown_tool_fail_explicitly`；分组日志及 [source check](tool-unification-evidence/07-source-checks.txt) |
| A-07-02 | Spec 唯一持有 schema/域/风险/并行；兼容投影和 Planner 同源；无另一执行权限路径 | 所有运行 Spec 为 explicit；旧散表仅派生；cacheable 独立；Planner 用 Spec；原 Handler adapter 继续同一 Dispatcher | 通过 | `test_risk_concurrency_cache_and_partial_validator_helpers`、`test_definition_projections_cannot_mutate_runtime_or_other_requests`、`test_cache_eligibility_does_not_infer_from_concurrency`、`test_planner_uses_spec_and_preserves_snapshot_contract`；源码报告 `preserved_functions/unchanged_sources` |
| A-07-03 | 名字、完整 schema、合法 JSON/类型规范化/参数别名、旧导入/API、授权快照和域/用户/内部可见性保持 | 全量基准深对照、9 模块旧导入/常量、144 组上下文一致；旧危险确认 binding 可用；Planner 与预检集合一致；两用户同 call_id 状态独立 | 通过 | `test_all_json_parameters_and_existing_coercions`、`test_old_imports_signatures_and_constant_values`、`test_entire_context_matrix_preserves_authorization`、`test_prior_confirmation_binding_remains_valid`、`test_planner_preflight_and_frozen_authorization_agree`；`test_tool_definitions_07_entries` 的 ERP task/query、local fields/extra_fields 用例；`test_file_target_execution::test_search_then_delete_real_handlers` 覆盖 3 入口 × 5 selector |
| A-07-04 | 不重写业务、不混入缓存/风险/可见性变更 | ERP、文件、沙盒、媒体、任务 Handler 无 diff；Cache 仅 is_cacheable 来源变更，其他方法 AST 相同；任务 workflow 原函数 AST 相同 | 通过 | [完整代码 diff](tool-unification-evidence/07-backend.patch)（含未跟踪新增文件，无暂存）、[source-checks.json](tool-unification-evidence/07-source-checks.json) 的保护路径/函数证明；全量 Spec 事实对照；4 组迁移日志 |
| A-07-05 | Registry/Policy/Dispatcher/旧 helper/ToolExecutor、结果/回放/审计全链回归；入口无漏；无循环导入/全局请求泄漏 | 2479 passed；必需项无失败/跳过；2 个无关字体测试同基准跳过；2 个 ToolExecutor 构造、1 个 ToolLoop 构造保持；9 种独立进程导入通过 | 通过 | `test_tool_definitions_07_entries::test_each_migrated_group_reaches_original_handler_once`（3 入口 × 5 代表）；同用户隔离用例；`test_fresh_process_import_orders_have_no_initialization_cycle`；[integration](tool-unification-evidence/07-integration.txt)、[regression](tool-unification-evidence/07-regression.txt)、[ERP](tool-unification-evidence/07-erp.txt) |
| A-07-06 | 最终架构/接口真实；legacy 边界、01–07 验收、回退和 08 前置可查 | 最终架构与 35 项目录、别名/独立通道映射齐全；交接顶部明确当前与历史状态；08 代码前置具备，流程等待本块用户验收关闭 | 通过 | [架构](TOOL_UNIFICATION_ARCHITECTURE_07.md)、[目录](TOOL_UNIFICATION_CATALOG_07.md)、[交接](TOOL_UNIFICATION_HANDOFF.md)、源码祖先/tree/接口证明 |
| G-01 | 本块范围清楚，无未说明公共契约或业务重写/后续板块 | 仅定义所有权与派生；明确 Planner 描述对齐和保留语义；无部署/生产写/合并/08 | 通过 | 第 1 节、架构的实际接口/边界、[完整代码 diff](tool-unification-evidence/07-backend.patch)、源码保护检查 |
| G-02 | A 项逐条成功、拒绝/失败、必要边界均验证 | 未知工具/重复/缺失、域与组织、plan、危险确认、文件目标、预检、缓存/回放失败均覆盖 | 通过 | 本表 A 项；`test_tool_policy`、`test_tool_production_integration::test_denial_precedes_handler_cache_and_ledger`；结果/回放失败用例日志 |
| G-03 | 相关测试通过；失败不能隐藏，历史跳过有基准证据 | 2 个新增测试采集/fixture 问题已修复复验；0 failed/error/xfail；字体缺失原样复现，不计通过 | 通过 | 第 3、4 节；[字体基准复现](tool-unification-evidence/07-baseline-font-skips.txt) |
| G-04 | 工具/schema/参数别名/旧 API/返回及 WS 协议兼容 | 完整冻结基准、旧 API/常量/顺序通过；结果/WS/delivery/checkpoint 实际消费回归通过；相关生产源未改 | 通过 | `test_tool_definitions_07`、`test_tool_definitions_07_entries`、`test_tool_result_consumption`、`test_chat_generate_mixin`、`test_emit_protocol` 等日志；原基准重新采集字节一致 |
| G-05 | 接口、证据、限制、回退、后续前置可交接 | 回退基准含 06 reader/writer；持久化源码与基准逐字相同；无数据迁移；真实业务/用户观感明确待验证 | 通过 | 最终架构回退段、交接、source report、`test_tool_result_persistence_06` 的读写/回退用例 |

## 3. 测试环境、命令与结果

执行目录为上述任务工作树。Python `/Users/wucong/EVERYDAYAIONE/.venv/bin/python`，3.14.2；pytest 9.0.3。工作树无 `.env`；数据库为测试占位 `127.0.0.1:1`，Redis 端口 1，JWT 为明确测试字符串，`APP_ENV=testing`。未读取生产配置/调用真实 ERP/付费 Provider/生产 DB。

`PYTHONPATH=/private/tmp/tool05-testdeps:backend` 沿用本机已存在的测试依赖目录；补充 PIL/matplotlib/IPython 等，不是生产代码。完整回归按当前默认 `TOOL_RESULT_PAYLOAD_WRITE_VERSION=1`；06 用例同时显式覆盖 0/1 和旧载荷。

最终命令：

```bash
bash docs/document/tool-unification-evidence/run-07.sh
```

该脚本列出全部测试文件及占位环境，严格失败即停止。最终追加旧 binding 及诊断导入对照后，仅重跑受影响的 contracts 组，精确文件集合/参数与脚本第一条 pytest 命令一致，最终日志已更新。

| 验证阶段 | 实际结果 | 日志 |
|---|---|---|
| ERP 定义迁移（先完成后继续） | 147 passed | [07-group-erp.txt](tool-unification-evidence/07-group-erp.txt) |
| 文件/沙盒定义迁移（ERP 通过后） | 296 passed | [07-group-file-sandbox.txt](tool-unification-evidence/07-group-file-sandbox.txt) |
| 媒体定义迁移（文件/沙盒通过后） | 173 passed | [07-group-media.txt](tool-unification-evidence/07-group-media.txt) |
| 任务定义迁移（媒体通过后） | 159 passed | [07-group-task.txt](tool-unification-evidence/07-group-task.txt) |
| 最终目录/新增契约/Planner/任务变更/参数/ERP local | 441 passed，其中新增必需用例 334 个 | [07-contracts.txt](tool-unification-evidence/07-contracts.txt) |
| 最终执行/结果/回放/缓存/审计/入口集成 | 472 passed | [07-integration.txt](tool-unification-evidence/07-integration.txt) |
| 最终 Registry/Policy/旧 API/文件/沙盒/媒体/任务/WS 回归 | 1555 passed、2 字体 skipped | [07-regression.txt](tool-unification-evidence/07-regression.txt) |
| 独立进程 ERP Mixin 回归 | 11 passed | [07-erp.txt](tool-unification-evidence/07-erp.txt) |

最终合计 **2479 passed、0 failed/error/xfail、2 skipped**；阶段验证不重复累加。[各组准确命令](tool-unification-evidence/07-group-commands.md) 保留迁移阶段的执行记录。两项跳过为 `test_emit_three_engines::TestMatplotlibChineseFont::{test_chinese_aliases_registered_to_real_font,test_alias_registration_dedupes_on_second_call}`；在 `git archive ca4c3d7e backend` 导出的未修改基准执行同一节点，同样因缺少 Linux wqy 字体跳过。它们与本块定义/权限必需验收无关，不声称本机字体验证通过。

警告：pytest-env 未安装导致配置项 env 未识别（环境已显式设置）；Pydantic class Config/IPython source 参数弃用；matplotlib 缺少中文 glyph。未修改或跳过正确断言。

源码/基准再采集检查（相同占位环境和 PYTHONPATH）：

```bash
/Users/wucong/EVERYDAYAIONE/.venv/bin/python docs/document/tool-unification-evidence/check-07-source.py
git diff --check
```

源码脚本自动从确定基准 git archive 到临时目录，重新执行 capture 脚本并逐字核对 fixture；也检查 01–06 祖先/tree、全目录、保护代码、入口清单和最终文件指纹。结果：[文本](tool-unification-evidence/07-source-checks.txt)、[结构化证据](tool-unification-evidence/07-source-checks.json)。

## 4. 问题清单及复验

| 问题编号 | 复现 | 根因/影响 | 所属板块 | 处理结果 | 复验证据 |
|---|---|---|---|---|---|
| P07-01 | 首次新增契约 3 failed、319 passed | 测试采集器把 Python 3.14 自动 `__annotate__` 纳入旧 API，且常量比对未转换 ApiEntry；未发现业务回归 | 07 测试 | 排除解释器自动钩子，保留全部项目函数签名；采用与基准相同 dataclass/Enum 转换，不删减契约断言 | [首次日志](tool-unification-evidence/07-contract-first.txt)、[322 项复验](tool-unification-evidence/07-contract-retest.txt)、最终 441 passed；基准重新采集相同 |
| P07-02 | 首次追加 binding 用例 2 failed | 新 fixture 缺少 ToolContext 必填 personal_context_allowed；生产代码未执行到 | 07 测试 | 补齐真实个人上下文及确认通道 fixture；旧 binding 哈希/批准对照均通过 | [首次日志](tool-unification-evidence/07-binding.txt)、最终 contracts 中 2 个 binding PASSED |
| E07-01 | 两项中文字体测试 skipped | 本机无 wqy，未修改基准同样跳过；不阻塞本块定义/权限验收 | 环境 | 保留跳过及生产 Linux 字体验证边界，不计通过 | [基准复现](tool-unification-evidence/07-baseline-font-skips.txt) |

未关闭的本板块技术缺陷：**无**。真实服务与用户生产验收尚未执行，状态见下节，不伪称 mock 验证等于生产成功。

## 5. 用户验证单

部署版本：**待用户“提交部署”后记录确定候选 SHA**。无需手动遍历全部工具；全量兼容由自动化基准对照证明。建议在已有获准测试资源上做代表回归：

1. 对照完整目录：general 只显示主工具，ERP 直接查询保持 ERP 域，code_execute 为 shared；fetch_all_pages/get_conversation_context 不新增到主模型。无组织 ERP 原可见性/原未开通返回不变，群工作区个人上下文及任务工具不可越权。
2. ERP：一个已知订单/库存只读查询，结果与原流程一致。不要用查询入口执行写 action；越权调用应被拒绝。
3. 文件/沙盒：在指定测试 CSV 上搜索→分析→计算/表格，检查文件引用、输出及跨用户隔离。删除/恢复仅在用户另行授权的测试文件进行，确认对象和既有恢复行为应保持。
4. 媒体：在获准额度内选择一个图片/视频代表生成，核对原结果和扣费/失败卡片；没有付费授权时保留该生产项未验证。plan 不应执行业务生成。
5. 任务：查看列表、生成配置表单，核对表单不会被称为已经生效；在指定测试任务按原提交/预检/确认流程执行，现有授权范围不扩张。

待生产环境记录：确定 SHA、账号/领域（不记录敏感身份）、测试资源、步骤、实际结果、通过/未通过/未验证。外部业务、真实 WS/浏览器观感及付费操作均未在本地代替用户验证。

## 6. 结论与交接

A-07-01～06 和 G-01～05 技术项全部通过。仅 2 个非必需字体环境项未验证；本块必需场景无跳过。当前状态为**技术验收通过，待提交部署及用户验收**，工作树和任务分支保留。

08 的代码前置已具备；启动仍缺本块确定候选部署、用户验收、受控验收关闭及 main 一致性核验。未执行推送、部署、合并、清理或板块 08。发布后交付消息须记录实际候选 SHA 并对照本记录源码指纹，不能拿基准 SHA 冒充被测候选。
