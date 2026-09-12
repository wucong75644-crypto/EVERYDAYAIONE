# 工具统一板块 07：最终目录与兼容映射

更新：2026-09-11。基准 `ca4c3d7e` 加板块 07 未提交差异。完整 schema/顺序/旧校验表基准见 `backend/tests/fixtures/tool_catalog_07_baseline.json`；实际 handler 全限定路径、资格/功能标志及源指纹见 [源码报告](tool-unification-evidence/07-source-checks.json)。

33 个公开工具、2 个 handler-only，共 35 个显式 Spec；未注册、重复注册和缺失 handler 均为 0。权限域计数：general 14（含个人上下文 internal）、erp 20（含 fetch_all_pages）、shared 1。所有 name 与 handler_key 相同，无新增工具名别名注册。

本表为全目录，不是每个用户每轮看到的列表。general 只向 general Agent 开放，erp 只向 ERP Agent 开放，shared 跨域；实际仍与组织、个人/群身份、feature flags、模式和授权快照取交集。

## 35 项运行映射

模式 I/S/P 分别为 interactive/scheduled/preflight 的既有 Spec 声明；不等于危险操作批准。并行/缓存是独立字段。可见 internal 只允许 legacy_internal 入口，永不加入主模型 advertised 集合。

| 工具名 | Spec 所有者 | 原 handler | 域 | 风险 | 并行/缓存 | 模式 | 可见/资格 |
|---|---|---|---|---|---|---|---|
| `erp_info_query` | [ERP](../../backend/services/tools/definitions/erp.py) | `_make_erp_handler → _erp_dispatch` | erp | safe | 是/是 | I | public；需组织 |
| `erp_product_query` | [ERP](../../backend/services/tools/definitions/erp.py) | `_make_erp_handler → _erp_dispatch` | erp | safe | 是/是 | I | public；需组织 |
| `erp_trade_query` | [ERP](../../backend/services/tools/definitions/erp.py) | `_make_erp_handler → _erp_dispatch` | erp | safe | 是/是 | I | public；需组织 |
| `erp_aftersales_query` | [ERP](../../backend/services/tools/definitions/erp.py) | `_make_erp_handler → _erp_dispatch` | erp | safe | 是/是 | I | public；需组织 |
| `erp_warehouse_query` | [ERP](../../backend/services/tools/definitions/erp.py) | `_make_erp_handler → _erp_dispatch` | erp | safe | 是/是 | I | public；需组织 |
| `erp_purchase_query` | [ERP](../../backend/services/tools/definitions/erp.py) | `_make_erp_handler → _erp_dispatch` | erp | safe | 是/是 | I | public；需组织 |
| `erp_taobao_query` | [ERP](../../backend/services/tools/definitions/erp.py) | `_make_erp_handler → _erp_dispatch` | erp | safe | 是/是 | I | public；需组织 |
| `erp_execute` | [ERP](../../backend/services/tools/definitions/erp.py) | `_make_erp_handler → _erp_dispatch` | erp | dangerous | 否/否 | I | public；需组织 |
| `local_data` | [ERP](../../backend/services/tools/definitions/erp.py) | `_make_local_handler → _local_dispatch` | erp | safe | 是/是 | I | public；需组织 |
| `local_product_stats` | [ERP](../../backend/services/tools/definitions/erp.py) | `_make_local_handler → _local_dispatch` | erp | safe | 是/是 | I | public；需组织 |
| `local_stock_query` | [ERP](../../backend/services/tools/definitions/erp.py) | `_make_local_handler → _local_dispatch` | erp | safe | 是/是 | I | public；需组织 |
| `local_product_identify` | [ERP](../../backend/services/tools/definitions/erp.py) | `_make_local_handler → _local_dispatch` | erp | safe | 是/是 | I | public；需组织 |
| `local_platform_map_query` | [ERP](../../backend/services/tools/definitions/erp.py) | `_make_local_handler → _local_dispatch` | erp | safe | 是/是 | I | public；需组织 |
| `local_compare_stats` | [ERP](../../backend/services/tools/definitions/erp.py) | `_make_local_handler → _local_dispatch` | erp | safe | 是/是 | I | public；需组织 |
| `local_shop_list` | [ERP](../../backend/services/tools/definitions/erp.py) | `_make_local_handler → _local_dispatch` | erp | safe | 是/是 | I | public；需组织 |
| `local_warehouse_list` | [ERP](../../backend/services/tools/definitions/erp.py) | `_make_local_handler → _local_dispatch` | erp | safe | 是/是 | I | public；需组织 |
| `local_supplier_list` | [ERP](../../backend/services/tools/definitions/erp.py) | `_make_local_handler → _local_dispatch` | erp | safe | 是/是 | I | public；需组织 |
| `trigger_erp_sync` | [ERP](../../backend/services/tools/definitions/erp.py) | `_make_local_handler → _local_dispatch` | erp | dangerous | 否/否 | I | public；需组织 |
| `social_crawler` | [通用](../../backend/services/tools/definitions/general.py) | `_social_crawler` | general | safe | 是/是 | I/S/P | public；crawler_enabled |
| `file_search` | [文件/沙盒](../../backend/services/tools/definitions/file_sandbox.py) | `_make_file_handler → _file_dispatch` | general | safe | 是/是 | I/S/P | public；file_workspace_enabled |
| `file_analyze` | [文件/沙盒](../../backend/services/tools/definitions/file_sandbox.py) | `_make_file_handler → _file_dispatch` | general | safe | 是/是 | I/S/P | public；file_workspace_enabled |
| `file_delete` | [文件/沙盒](../../backend/services/tools/definitions/file_sandbox.py) | `_make_file_handler → _file_dispatch` | general | dangerous | 否/否 | I/S | public；file_workspace_enabled |
| `restore_file` | [文件/沙盒](../../backend/services/tools/definitions/file_sandbox.py) | `_make_file_handler → _file_dispatch` | general | safe | 否/否 | I/S | public；file_workspace_enabled |
| `code_execute` | [文件/沙盒](../../backend/services/tools/definitions/file_sandbox.py) | `_code_execute` | shared | confirm | 是/是 | I/S/P | public；sandbox_enabled |
| `erp_agent` | [ERP](../../backend/services/tools/definitions/erp.py) | `_erp_agent` | general | safe | 是/是 | I/S/P | public |
| `erp_analyze` | [ERP](../../backend/services/tools/definitions/erp.py) | `_erp_analyze` | general | safe | 是/是 | I/S/P | public |
| `erp_api_search` | [ERP](../../backend/services/tools/definitions/erp.py) | `_erp_api_search` | erp | safe | 是/是 | I | public |
| `search_knowledge` | [通用](../../backend/services/tools/definitions/general.py) | `_search_knowledge` | general | safe | 是/是 | I/S/P | public |
| `web_search` | [通用](../../backend/services/tools/definitions/general.py) | `_web_search` | general | safe | 是/是 | I/S/P | public |
| `generate_image` | [媒体](../../backend/services/tools/definitions/media.py) | `_generate_image` | general | confirm | 否/否 | I | public |
| `generate_video` | [媒体](../../backend/services/tools/definitions/media.py) | `_generate_video` | general | confirm | 否/否 | I | public |
| `image_agent` | [媒体](../../backend/services/tools/definitions/media.py) | `_image_agent` | general | confirm | 否/否 | I/S | public |
| `manage_scheduled_task` | [任务](../../backend/services/tools/definitions/task.py) | `_manage_scheduled_task` | general | safe | 是/是 | I/S | public；需组织；个人上下文 |
| `fetch_all_pages` | [ERP](../../backend/services/tools/definitions/erp.py) | `_fetch_all_pages` | erp | safe | 否/否 | I | internal；需组织 |
| `get_conversation_context` | [通用](../../backend/services/tools/definitions/general.py) | `_get_conversation_context` | general | safe | 否/否 | I | internal；个人上下文 |

迁移顺序：ERP 22 → 文件/沙盒 5 → 媒体 3 → 任务 1，随后补齐通用/爬虫/内部上下文 4。首批 file_search/file_delete/search_knowledge 的已显式事实也一并移至各所属定义文件，没有重新实现业务。分组与最终结果见 [验收记录](TOOL_UNIFICATION_ACCEPTANCE_07.md)。

## 旧目录和 factory 视图

| 兼容入口 | 保留结果 |
|---|---|
| get_chat_tools(org) | 有组织 33 schema，无组织 15；完整内容和原顺序逐项一致 |
| get_core_tools / get_tools_for_mode | 原 12 核心选择与旧 helper 的 plan 过滤保持；生产展示再经 Registry/Policy，不把 helper 结果视为授权 |
| build_erp_tools / build_local_tools | 18 个 direct ERP（8 remote + 10 local，其中 local 含同步）；旧函数和集合/partial validator re-export 保留 |
| build_common_tools | 原 9 个 common schema 原序返回，定义分归 ERP/通用/媒体/任务 Spec |
| build_file_tools / build_code_tools / build_crawler_tools | 4 / 1 / 1 个原 schema；code include_workspace=False/True 均保留原相同描述 |
| build_erp_search_tool | Spec.schema_variants["erp_search"] 保留历史 Phase 描述；common 目录 erp_api_search 的原描述保持，各自参数全文可对照 |
| build_fetch_all_pages_tool / build_domain_tools | 保留历史内部 factory/Phase 组合；fetch_all_pages 不转为 public。组合内文件/出口并不扩大当前域访问权限 |

注意：legacy get_chat_tools 原本不是完整可用性检查，因此无组织仍包含 erp_agent/erp_analyze/erp_api_search/manage_scheduled_task 的 schema；本块保留这个 API。Runtime 仍要求任务工具组织/个人资格，并按 ERP 复合 Handler 原约定返回未开通信息。

## 参数别名与规范化

| 工具/参数 | 既有约定和本块证明 |
|---|---|
| erp_agent：query/task | validator 仅在缺 task 时 query→task；原 Handler 按非空 task 优先、否则回退 query。完整 schema、原 validator 和旧 execute/原 Handler 测试通过 |
| erp_analyze：query/task | query 回退仅在原 Handler；公开 schema 仍要求 task，不因迁移加 query 字段或改 validator |
| local_data：fields/extra_fields | 原 Handler extra_fields 优先、否则 fields；不额外增加公开字段；原调度入口实测通过 |
| file_analyze：resource_ref/file_id/path/scope | 原签名引用、fid、路径与作用域选择保留；目标/权限/version 仍由原 FileTargetResolver 与 PreparedFileCall 处理 |
| file_delete：resource_refs/file_ids/files | 三种入口及字符串/列表旧 Handler 接受方式保留；最终解析到确定目标，不覆盖原多字段组合/去重行为。3 个实际入口 × 名称/部分名称/路径/fid/fref 的原 Handler 回归通过 |
| restore_file：filename/record_id | 精确恢复记录/名称与冲突保护保留；safe 风险、串行、不可缓存/既有 replay 资格不变 |
| ERP params、整数/布尔、合法 JSON | 原 gateway 的 object-string、integer-string、boolean-string 转换继续使用原代码；34 个具有 schema 的工具按完整/最小/转换参数逐一与基准对照 |
| 旧 SYNC_TOOLS / ToolOutput / services.tool_executor 导入 | 保持导入和原 API；SYNC_TOOLS 为 INFO_TOOLS 的原兼容别名，ToolOutput/AgentResult/ToolExecutor 返回门面不改 |

## 不属于运行 Registry 的独立协议

| 名字/系列 | 可追溯位置 | 保留原因 |
|---|---|---|
| conversation_control | config/conversation_control_tools.py；conversation control router | 暂停/恢复/取消控制通道，不能变成普通业务 Handler；本块未改 schema/控制流程 |
| route_to_chat | config/phase_tools.py::_build_phase2_route_to_chat_tool；config/agent_tools.py 旧校验条目 | 旧 Phase 出口 schema，当前无 ToolExecutor handler；保留原 build_domain_tools 与域分类，不赋予执行权限 |
| route_to_image / route_to_video | config/agent_tools.py::ROUTING_TOOLS/TOOL_SCHEMAS | 仅旧分类和部分验证条目；当前公共目录和 ToolExecutor 均未注册，不能以遗留名字恢复运行能力 |
| Provider 内置 Google Search | 原模型接入层/搜索 Handler 内部 | 由 Provider 协议处理，不是本地同名工具注册 |
| emit_* / read_file / ERP 内部 SQL/IO | 原沙盒 API、部门 Agent、领域引擎 | 工具内部业务接口；不新增为模型公共 function 工具 |

上述排除都有代码归属，不把它们计成“漏注册工具”或新能力。语义选择表可能含历史标签；它只产生候选选择，不能注册 Handler 或改变权限域。
