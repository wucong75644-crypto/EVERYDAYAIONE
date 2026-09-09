# 工具统一 01：当前代码清单及代表契约

基准：`051b24c5715c6c0d0086dcc90ef44a2344296793`。这是验收事实快照，权威 schema 仍在旧工厂；后续代码变化应重新核对。

## 目录边界

当前 `get_chat_tools(None)` 15 个 schema，组织目录 33 个；ToolExecutor 个人 16 个 handler，组织 35 个。组织群工作区移除两个个人 handler 后为 33 个。数量仅用于定位，验收按每个名称、完整 schema、旧部分校验 schema 和真实 handler key 比较。

“公开”指旧完整工具池中有模型 schema，不表示主模型可见：general 与 erp 域互相隔离，shared 可跨这两个合法域；未知域拒绝。所有 Spec 的 `executor_type=legacy`、`handler_key=name`，当前不存在运行时工具名别名。三代表为 explicit，其余为 legacy。所有工具的执行授权仍需 Policy；本表的目录条件不能充当业务执行批准。

工厂缩写：ERP=`config.erp_tools.build_erp_tools`（含 `build_local_tools`）；Crawler=`config.crawler_tools.build_crawler_tools`；File=`config.file_tools.build_file_tools`；Code=`config.code_tools.build_code_tools(include_workspace=True)`；Common=`config.common_tools.build_common_tools`。

| 工具 / handler key | 旧 schema 范围 / 工厂 | 域 | 新目录可用条件（另叠加 Policy 与授权上界） | 风险 / 并行 / 缓存 | 副作用声明 | 定义 |
|---|---|---|---|---|---|---|
| `erp_info_query` | 仅组织 / ERP | erp | 可信 actor、workspace；有组织 | safe / True / True | unknown | legacy |
| `erp_product_query` | 仅组织 / ERP | erp | 可信 actor、workspace；有组织 | safe / True / True | unknown | legacy |
| `erp_trade_query` | 仅组织 / ERP | erp | 可信 actor、workspace；有组织 | safe / True / True | unknown | legacy |
| `erp_aftersales_query` | 仅组织 / ERP | erp | 可信 actor、workspace；有组织 | safe / True / True | unknown | legacy |
| `erp_warehouse_query` | 仅组织 / ERP | erp | 可信 actor、workspace；有组织 | safe / True / True | unknown | legacy |
| `erp_purchase_query` | 仅组织 / ERP | erp | 可信 actor、workspace；有组织 | safe / True / True | unknown | legacy |
| `erp_taobao_query` | 仅组织 / ERP | erp | 可信 actor、workspace；有组织 | safe / True / True | unknown | legacy |
| `erp_execute` | 仅组织 / ERP | erp | 可信 actor、workspace；有组织 | dangerous / False / False | unknown | legacy |
| `local_data` | 仅组织 / ERP | erp | 可信 actor、workspace；有组织 | safe / True / True | unknown | legacy |
| `local_product_stats` | 仅组织 / ERP | erp | 可信 actor、workspace；有组织 | safe / True / True | unknown | legacy |
| `local_stock_query` | 仅组织 / ERP | erp | 可信 actor、workspace；有组织 | safe / True / True | unknown | legacy |
| `local_product_identify` | 仅组织 / ERP | erp | 可信 actor、workspace；有组织 | safe / True / True | unknown | legacy |
| `local_platform_map_query` | 仅组织 / ERP | erp | 可信 actor、workspace；有组织 | safe / True / True | unknown | legacy |
| `local_compare_stats` | 仅组织 / ERP | erp | 可信 actor、workspace；有组织 | safe / True / True | unknown | legacy |
| `local_shop_list` | 仅组织 / ERP | erp | 可信 actor、workspace；有组织 | safe / True / True | unknown | legacy |
| `local_warehouse_list` | 仅组织 / ERP | erp | 可信 actor、workspace；有组织 | safe / True / True | unknown | legacy |
| `local_supplier_list` | 仅组织 / ERP | erp | 可信 actor、workspace；有组织 | safe / True / True | unknown | legacy |
| `trigger_erp_sync` | 仅组织 / ERP | erp | 可信 actor、workspace；有组织 | dangerous / False / False | unknown | legacy |
| `social_crawler` | 个人及组织 / Crawler | general | 可信 actor、workspace；crawler_enabled | safe / True / True | unknown | legacy |
| `file_search` | 个人及组织 / File | general | 可信 actor、workspace；file_workspace_enabled | safe / True / True | file_index | explicit |
| `file_analyze` | 个人及组织 / File | general | 可信 actor、workspace；file_workspace_enabled | safe / True / True | workspace_artifacts, file_index | legacy |
| `file_delete` | 个人及组织 / File | general | 可信 actor、workspace；file_workspace_enabled | dangerous / False / False | workspace_delete, deletion_record | explicit |
| `restore_file` | 个人及组织 / File | general | 可信 actor、workspace；file_workspace_enabled | safe / False / False | workspace_write, deletion_record | legacy |
| `code_execute` | 个人及组织 / Code | shared | 可信 actor、workspace；sandbox_enabled | confirm / True / True | kernel_state, workspace_artifacts | legacy |
| `erp_agent` | 个人及组织 / Common | general | 可信 actor、workspace | safe / True / True | unknown | legacy |
| `erp_analyze` | 个人及组织 / Common | general | 可信 actor、workspace | safe / True / True | unknown | legacy |
| `erp_api_search` | 个人及组织 / Common | erp | 可信 actor、workspace | safe / True / True | unknown | legacy |
| `search_knowledge` | 个人及组织 / Common | general | 可信 actor、workspace | safe / True / True | none | explicit |
| `web_search` | 个人及组织 / Common | general | 可信 actor、workspace | safe / True / True | unknown | legacy |
| `generate_image` | 个人及组织 / Common | general | 可信 actor、workspace | confirm / False / False | unknown | legacy |
| `generate_video` | 个人及组织 / Common | general | 可信 actor、workspace | confirm / False / False | unknown | legacy |
| `image_agent` | 个人及组织 / Common | general | 可信 actor、workspace | confirm / False / False | unknown | legacy |
| `manage_scheduled_task` | 个人及组织 / Common | general | 可信 actor、workspace；有组织；个人上下文 | safe / True / True | proposal_or_form | legacy |
| `fetch_all_pages` | 仅内部独立工厂 `build_fetch_all_pages_tool` | erp | 可信 actor、workspace；有组织；legacy_internal 入口 | safe / False / False | workspace_artifacts, file_index | legacy |
| `get_conversation_context` | 无公开 schema；保留 TOOL_SCHEMAS 部分校验表 | general | 可信 actor、workspace；个人上下文；legacy_internal 入口 | safe / False / False | none | legacy |

`unknown` 是未在本块独立审定的旧工具副作用，不能解释为无副作用。已有缓存资格通过 `ToolResultCache.is_cacheable` 独立读取；该旧实现内部仍依赖并行标志。本块只保存兼容事实，不将这种耦合复制成新 Spec 的推断规则。`code_execute` 仍可缓存/并行但带 kernel_state，`restore_file` 仍为 safe/串行但写工作区；语义调整留给明确工具迁移批次。

`manage_scheduled_task` 的旧公开池对个人仍有 schema，但实际 handler 会拒绝无组织调用，因此新层 eligibility 标记 requires_org。旧 `get_chat_tools` 输出完全未改。功能开关在新层必须明确为 True；缺少快照按不可用处理。没有读取进程 Settings 并缓存用户状态。

## 别名、旧校验表与外部边界

- `search_knowledge`：无参数别名；query 必填，handler 固定 limit=5 并传入可信 org_id。
- `file_search`：path / keyword / file_pattern / scope 原样保留；scope 默认 current，现有 handler 做 strip/lower。带冻结资源时 current 走 manifest，workspace 走工作区；本块不重新实现此规则。
- `file_delete`：公开 file_ids 和兼容 files 均保留。handler 接受单字符串或列表；同时传两者时把解析出的 file_ids 路径追加到 files，并非忽略 files。公共 schema 未添加 required/anyOf；空请求仍由旧 handler 报错。旧 `FILE_TOOL_SCHEMAS` 仍要求 files，两者原本不同，不混合为新契约。
- `file_analyze`：优先 file_id，兼容 path 和 scope；旧部分校验表 path 必填，公开 schema 无此必填项，分别保存。
- `erp_agent`：既有参数校验器把 query 迁到 task；handler 也支持 task 优先、query 后备。`erp_analyze` 的 query 后备仅在 handler，公共校验器未增加该别名。
- `local_data`：handler 中 extra_fields 回退 fields，公共 schema 不新增字段。
- ERP 远程 query：action + params 原契约，包括 params=None 的参数文档路径、params={} 的执行路径，以及 page/page_size 注入；action 内的参数别名仍归 ApiEntry.param_map 与业务规范化器。本块对整个嵌套 schema（含枚举、描述）比较，不重建 action 表。
- `config.agent_tools.SYNC_TOOLS = INFO_TOOLS` 是 Python 集合别名，不是可调用工具名；`services.tool_executor.ToolExecutor` 仍是旧 re-export。
- `get_conversation_context` 没有 OpenAI function schema，但有 `config.agent_tools.TOOL_SCHEMAS` 中 limit:integer 的部分校验条目，保存在 `legacy_validation_schema`；不包成新公开 schema。允许旧内部个人入口，不允许模型或群入口。
- `fetch_all_pages` 使用已有完整工厂，schema 可读取但 exposure=legacy_internal，模型动态发现仍不能公开；旧 execute_raw 读保护留在业务层。

以下符号不是本次公共执行器的既有 handler，不注册成可执行 Spec，也不补造 handler：

| 符号 | 当前来源与入口 | 处理 |
|---|---|---|
| route_to_chat | config.phase_tools 的旧域工厂与 config.agent_tools 校验表；ToolExecutor 无 handler | 保留旧配置，Registry 查找拒绝；不能动态展示 |
| route_to_image / route_to_video | config.agent_tools 的旧路由验证条目；v1 builder 已移除 | 不新增工具；旧验证表不改 |
| conversation_control | config.conversation_control_tools → services.conversation_control_router | 独立控制协议，按共同约束排除，pause/resume/cancel 不迁为业务工具 |
| 模型内置 Google Search | provider 接入能力，无本地 ToolExecutor handler | 不伪装成本地工具 |
| 部门 Agent、查询引擎内部 SQL/IO | ERPAgent → 部门 Agent → UnifiedQueryEngine | 复合工具内部实现，不逐次注册 |

## 三代表公开参数逐字段对照

每个工具的完整 function.name / description / parameters，以及参数 description 均与对应旧工厂深比较；以下列出供用户核对的结构契约，不是第二份运行时定义。

| 工具 | 字段 | 原契约与新 Spec 读取结果 | 证据 |
|---|---|---|---|
| search_knowledge | name / root | 名称不变；parameters.type=object；required=[query] | test_three_representative_parameter_contracts_field_by_field |
| search_knowledge | query | string；原 description 完整一致 | 同上；test_parameter_reading_and_legacy_aliases_unchanged |
| file_search | name / root | 名称不变；object；无 required | 同上 |
| file_search | path | string，可选；目录或文件路径 | 同上 |
| file_search | keyword | string，可选 | 同上 |
| file_search | file_pattern | string，可选 | 同上 |
| file_search | scope | string，可选，enum=[current, workspace] | 同上 |
| file_delete | name / root | 名称不变；object；无 required / 新增 anyOf | 同上 |
| file_delete | file_ids | array；items.type=string；pattern=^fid_[a-z0-9]{8}$ | 同上 |
| file_delete | files | array；items.type=string；原兼容字段保留 | 同上；旧参数读取、file ID 既有回归 |

所有原 schema 的 additionalProperties 是否存在、enum 顺序、参数描述、嵌套字段与 required 是否省略均以完整相等断言为准；未趁迁移修正 file_search 的旧描述中“自动转 Parquet”等文字。实际 handler 是搜索/定位并注册路径，图片可返回 FileReadResult。

验收测试：[test_tool_registry.py](../../backend/tests/test_tool_registry.py)；完整记录见 [TOOL_UNIFICATION_ACCEPTANCE_01.md](TOOL_UNIFICATION_ACCEPTANCE_01.md)。
