# 工具可见性与发现盘点

2026-11-13 · 本地代码基线 cf0c1625。逐项表由现有 ToolSpec、Registry、ToolPolicy 和聊天 prepare_tool_turn 生成。使用隔离的合成账号、显式功能开关和内存参数，未访问生产、未调用模型或业务工具。✓ 表示在该场景的注册工具投影中有真实参数定义。后续主循环仍能因知识直答、任务阶段、Skill 协议及恢复状态缩小投影。本表不代表生产账号的实时清单。

- 个人/组织/频道使用相同测试开关。组织询问与自动的工具名单在此场景相同，实际确认规则分别判断。
- 知识场景为组织上下文，带已引用知识和自动发现标记。知识直答分支仍可将工具置空。
- 主图 Skill 使用仓库 v4 的三个工具声明模拟激活后投影。假设 Skill 上限额外加入视频工具，仅用于证实可注入性。
- ERP 域探索上界通过向现有解析器喂入全部 ERP 工具名，再运行权限投影获得。没有调用 ERP 搜索，也不等同于线上主 ERP Agent 的模型请求或业务权限。当前主 ERP Agent 由主聊天的 erp_agent 进入，模型提取部门和语义参数，部门适配器按 doc_type/action 路由。主聊天模型不接收各部门 ERP 函数定义。
- 预检上界将已登记工具作为输入上限并由 Registry/Policy 过滤。定时示例快照只批准 erp_agent、file_search、code_execute。实际定时名单取决于任务快照、Skill 固定版本和运行时权限。
- 独立图片、视频入口由宿主选择原生生成流程，无通用工具目录供生成模型选择。媒体 Skill 的文本处理调用使用空 tools。电商专用规划入口按已有工作流提供领域结构，单独保留。
- 表外的模型控制由执行循环按状态注入。包括 activate_skill、deactivate_skill、update_task_plan、resume_task_execution、save_task_image_plan，及 find_task_text、read_task_text、read_task_result、read_task_write_result、read_task_scheduled_result、read_task_code_result。旧编排还包含 start_task_execution、propose_task_revision 及 controller.CONTROL_INPUTS 的保存、完成、复用、导入、绑定、复核和读取操作。以实际状态对应的 schemas() 为准。供应商内置搜索通过模型适配器独立配置。

## 逐项矩阵

| 工具 | 域 / 公开级别 | 个人自动 | 组织自动 | 组织询问 | 组织计划 | 频道 | 知识场景 | 假设 Skill 上限投影 | 主图 Skill 工具投影 | ERP 域探索上界 | 预检上界 | 定时示例 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `agent_reach` | general / public | ✓ | ✓ | ✓ | — | ✓ | ✓ | — | — | — | ✓ | — |
| `code_execute` | shared / public | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | — | — | — | ✓ | ✓ |
| `erp_aftersales_query` | erp / public | — | — | — | — | — | — | — | — | ✓ | — | — |
| `erp_agent` | general / public | ✓ | ✓ | ✓ | — | ✓ | ✓ | — | — | — | ✓ | ✓ |
| `erp_analyze` | general / public | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | — | — | — | ✓ | — |
| `erp_api_search` | erp / public | — | — | — | — | — | — | — | — | ✓ | — | — |
| `erp_execute` | erp / public | — | — | — | — | — | — | — | — | ✓ | — | — |
| `erp_info_query` | erp / public | — | — | — | — | — | — | — | — | ✓ | — | — |
| `erp_product_query` | erp / public | — | — | — | — | — | — | — | — | ✓ | — | — |
| `erp_purchase_query` | erp / public | — | — | — | — | — | — | — | — | ✓ | — | — |
| `erp_taobao_query` | erp / public | — | — | — | — | — | — | — | — | ✓ | — | — |
| `erp_trade_query` | erp / public | — | — | — | — | — | — | — | — | ✓ | — | — |
| `erp_warehouse_query` | erp / public | — | — | — | — | — | — | — | — | ✓ | — | — |
| `fetch_all_pages` | erp / legacy_internal | — | — | — | — | — | — | — | — | — | — | — |
| `file_analyze` | general / public | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | — | — | — | ✓ | — |
| `file_delete` | general / public | ✓ | ✓ | ✓ | — | ✓ | ✓ | — | — | — | — | — |
| `file_search` | general / public | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | — | — | — | ✓ | ✓ |
| `generate_image` | general / public | ✓ | ✓ | ✓ | — | — | ✓ | ✓ | ✓ | — | — | — |
| `generate_video` | general / public | — | — | — | — | — | — | ✓ | — | — | — | — |
| `get_conversation_context` | general / public | — | — | — | — | — | — | ✓ | ✓ | — | — | — |
| `get_personal_skill_for_edit` | general / public | ✓ | ✓ | ✓ | — | — | ✓ | — | — | — | — | — |
| `image_agent` | general / legacy_internal | — | — | — | — | — | — | — | — | — | — | — |
| `knowledge_discover` | general / public | — | — | — | — | — | ✓ | — | — | — | — | — |
| `knowledge_follow_reference` | general / public | — | — | — | — | — | ✓ | — | — | — | — | — |
| `knowledge_outline` | general / public | — | — | — | — | — | ✓ | — | — | — | — | — |
| `knowledge_read` | general / public | — | — | — | — | — | ✓ | — | — | — | — | — |
| `knowledge_search` | general / public | — | — | — | — | — | ✓ | — | — | — | — | — |
| `list_personal_skills_for_edit` | general / public | ✓ | ✓ | ✓ | — | — | ✓ | — | — | — | — | — |
| `local_compare_stats` | erp / public | — | — | — | — | — | — | — | — | ✓ | — | — |
| `local_data` | erp / public | — | — | — | — | — | — | — | — | ✓ | — | — |
| `local_platform_map_query` | erp / public | — | — | — | — | — | — | — | — | ✓ | — | — |
| `local_product_identify` | erp / public | — | — | — | — | — | — | — | — | ✓ | — | — |
| `local_product_stats` | erp / public | — | — | — | — | — | — | — | — | ✓ | — | — |
| `local_shop_list` | erp / public | — | — | — | — | — | — | — | — | ✓ | — | — |
| `local_stock_query` | erp / public | — | — | — | — | — | — | — | — | ✓ | — | — |
| `local_supplier_list` | erp / public | — | — | — | — | — | — | — | — | ✓ | — | — |
| `local_warehouse_list` | erp / public | — | — | — | — | — | — | — | — | ✓ | — | — |
| `manage_scheduled_task` | general / public | — | ✓ | ✓ | ✓ | — | ✓ | — | — | — | — | — |
| `mcp_test_lookup` | general / public | — | — | — | — | — | — | — | — | — | — | — |
| `plan_ecommerce_images` | general / public | — | — | — | — | — | — | ✓ | ✓ | — | — | — |
| `prepare_skill_draft` | general / public | ✓ | ✓ | ✓ | — | — | ✓ | — | — | — | — | — |
| `restore_file` | general / public | ✓ | ✓ | ✓ | — | ✓ | ✓ | — | — | — | — | — |
| `search_knowledge` | general / legacy_internal | — | — | — | — | — | — | — | — | — | — | — |
| `social_crawler` | general / public | ✓ | ✓ | ✓ | — | ✓ | ✓ | — | — | — | ✓ | — |
| `trigger_erp_sync` | erp / public | — | — | — | — | — | — | — | — | ✓ | — | — |
| `web_search` | general / public | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | — | — | — | ✓ | — |

## 走查与缺口

| 场景 | 实际本地路径 | 结论 |
|---|---|---|
| 按刚改的第二项做文件 | 文件搜索、分析和沙盒代码已在组织自动场景提供。目录没有专用 Word 导出工具。 | 补通用来源后可评估使用现有沙盒的文档交付能力。工具发现不能生成尚未注册的能力。 |
| 上传参考图，请求普通出图或明确选服务器主图策划 | 普通聊天看到 generate_image。仓库主图 Skill 的上限投影提供 generate_image、get_conversation_context、plan_ecommerce_images。 | 服务器策划已有 Skill 路径。保留方法与业务前置条件，单独显示 schema 也不能替代它。 |
| 把上一轮的第二项做成视频 | generate_video 注册且在合成组织自动上下文通过名字级权限，基础聊天没有其定义。假设 Skill 上限可披露它。现有 ERP 搜索解析也不会返回它。 | 无 Skill 的通用聊天存在明确的发现缺口。独立视频入口仍有自身执行路径。 |
| 读取旧回复或原参考图 | get_conversation_context 在异步图片开关打开时是公开定义。已有主图/图片编排 Skill 可带入。该工具已有按固定历史边界查找、text_exact 精确选择和 source_prompt 绑定。 | 一般聊天缺少通用发现路径。复用精确读取逻辑时需把通用消息引用与图片专用资源定位、字段和开关拆开核对。 |
| 查询 ERP 订单 | 主模型看到 erp_agent、erp_analyze。ERP 直接工具在主模型域过滤，合成 ERP 搜索结果在 general 域解析为空。 | 域隔离按现有架构工作。保留业务查询的部门和能力路由。 |
| 创建定时任务、再执行 | 组织交互可见 manage_scheduled_task。运行时只展示批准快照中的工具，示例得出三个。 | 自然语言可提出任务，定时执行沿已确认范围。 |

知识工具有引用或场景注入路径。Skill 创作工具有功能开关和交互入口路径。内部兼容工具、未开启的 MCP 测试能力及被现有规则拒绝的工具按各自边界处理。ERP 域探索上界出现 erp_execute 只证明名字级投影，写操作仍由调用级策略约束。

本地定向回归 603 项通过，覆盖工具政策、主图路由与定时 Skill 快照。上述场景是代码投影模拟。生产开关、实际分配的 Skill、供应商最终请求及真实模型选法待后续核验。

## 对架构的补充

先把 generate_video 和旧内容读取作为通用发现验收样本。前者验证能力查找与执行，后者验证准确回读来源。再用现有文件能力验证同一来源跨域复用。发现入口从当前策略可见的候选集取数，再应用最终主循环的阶段约束。成功匹配后按轮披露原始完整定义。业务适配器、审批与恢复仍走原链。普通聊天可以在发现层直接回答无可用能力，模型无需猜测函数名。主图策划需沿现有方法路径核实，定时和预检保持快照边界。
