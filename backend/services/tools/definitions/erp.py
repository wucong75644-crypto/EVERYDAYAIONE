"""erp ToolSpec ownership. Business handlers are unchanged."""

from ..spec import Exposure, ToolAvailability, ToolPolicyRules, ToolSpec
from . import erp_local_schemas
from . import erp_schemas

def _build_erp_agent_description() -> str:
    """从 ERPAgent.build_tool_description() 自动生成描述（运行时调用）。"""
    from services.agent.erp_agent import ERPAgent
    return ERPAgent.build_tool_description()

def _schema_erp_agent():
    return {
        "type": "function",
        "function": {
            "name": "erp_agent",
            "description": _build_erp_agent_description(),
            "parameters": {
                "type": "object",
                "required": ["task"],
                "properties": {
                    "task": {
                        "type": "string",
                        "description": (
                            "用户本次查询的完整描述。写法：复述用户原话，只做两处替换——"
                            "时间词→具体日期（如'今天'→'2026-05-03 00:00~15:30'），"
                            "指代词→具体名称。其他一字不动，不要添加额外说明。"
                            "e.g. '2026-05-02 00:00~23:59 淘宝退货按店铺统计'；"
                            "'导出2026-04-28~2026-05-03的订单明细'"
                        ),
                    },
                    "conversation_context": {
                        "type": "string",
                        "description": (
                            "追问时传上轮的查询条件（时间范围/平台/对象/筛选条件），"
                            "让专家理解上文。不传结果数字，不传你的推测。首轮不传。"
                            "e.g. '上轮查了2026-05-02淘宝退货，按店铺分组'"
                        ),
                    },
                },
            },
        },
    }


def _schema_erp_analyze():
    return {
        "type": "function",
        "function": {
            "name": "erp_analyze",
            "description": (
                "ERP 查询任务拆解——只分析不执行，不查数据库不调 API，毫秒级返回。"
                "将复杂查询拆解为多步计划（数据域、参数、步骤依赖）。"
                "仅计划模式下使用：分析后展示方案，等用户确认后再执行，不要分析完直接调 erp_agent。"
                "不要用于：参数已明确的查询 → 直接调 erp_agent；非 ERP 分析 → code_execute。"
            ),
            "parameters": {
                "type": "object",
                "required": ["task"],
                "properties": {
                    "task": {
                        "type": "string",
                        "description": (
                            "用户的完整查询原文，不要拆分或改写。"
                            "e.g. '对比上月和本月各平台退货率，找出退货率上升最多的平台'"
                        ),
                    },
                    "conversation_context": {
                        "type": "string",
                        "description": (
                            "对话背景补充（可选）。追问时传上轮的查询条件，首轮不传"
                        ),
                    },
                },
            },
        },
    }


def _schema_erp_api_search():
    return {
        "type": "function",
        "function": {
            "name": "erp_api_search",
            "description": (
                "搜索 ERP API 文档的语义搜索工具。"
                "不确定用哪个工具、action 或参数格式时先调此工具，返回结果可直接用于下一步调用。"
                "支持关键词（如'退货'）和精确查询（如'erp_trade_query:order_list'）。"
                "不要用于：查询实际数据 → erp_agent；搜索知识库 → search_knowledge。"
            ),
            "parameters": {
                "type": "object",
                "required": ["query"],
                "properties": {
                    "query": {
                        "type": "string",
                        "description": (
                            "搜索关键词或 tool:action 精确查询。"
                            "e.g. '退货'、'库存盘点'、'erp_trade_query:order_list'"
                        ),
                    },
                },
            },
        },
    }


def build_specs():
    schemas = {}
    schemas.update((s["function"]["name"], s) for s in erp_schemas.build_erp_tools())
    return (
        ToolSpec(
            name='erp_info_query', schema=schemas['erp_info_query'],
            domain='erp', availability=ToolAvailability(requires_org=True),
            risk_level='safe', parallelizable=True, cacheable=True,
            effects=('unknown',), executor_type="legacy", handler_key='erp_info_query',
            exposure=Exposure.PUBLIC,
            source="services.tools.definitions.erp.build_specs", definition_kind="explicit",
            catalog_order=0, catalog_groups=('erp_tools',), core=False, legacy_plan_visible=True,
            compatibility_notes=(),
            legacy_validation_schema=erp_schemas.ERP_TOOL_SCHEMAS.get('erp_info_query'),
            policy_rules=ToolPolicyRules(
                operation='read',
                plan_allowed=True,
                execution_modes=('interactive',),
                action_rule='erp_query',
                required_permissions=(),
            ),
        ),
        ToolSpec(
            name='erp_product_query', schema=schemas['erp_product_query'],
            domain='erp', availability=ToolAvailability(requires_org=True),
            risk_level='safe', parallelizable=True, cacheable=True,
            effects=('unknown',), executor_type="legacy", handler_key='erp_product_query',
            exposure=Exposure.PUBLIC,
            source="services.tools.definitions.erp.build_specs", definition_kind="explicit",
            catalog_order=1, catalog_groups=('erp_tools',), core=False, legacy_plan_visible=True,
            compatibility_notes=(),
            legacy_validation_schema=erp_schemas.ERP_TOOL_SCHEMAS.get('erp_product_query'),
            policy_rules=ToolPolicyRules(
                operation='read',
                plan_allowed=True,
                execution_modes=('interactive',),
                action_rule='erp_query',
                required_permissions=(),
            ),
        ),
        ToolSpec(
            name='erp_trade_query', schema=schemas['erp_trade_query'],
            domain='erp', availability=ToolAvailability(requires_org=True),
            risk_level='safe', parallelizable=True, cacheable=True,
            effects=('unknown',), executor_type="legacy", handler_key='erp_trade_query',
            exposure=Exposure.PUBLIC,
            source="services.tools.definitions.erp.build_specs", definition_kind="explicit",
            catalog_order=2, catalog_groups=('erp_tools',), core=False, legacy_plan_visible=True,
            compatibility_notes=(),
            legacy_validation_schema=erp_schemas.ERP_TOOL_SCHEMAS.get('erp_trade_query'),
            policy_rules=ToolPolicyRules(
                operation='read',
                plan_allowed=True,
                execution_modes=('interactive',),
                action_rule='erp_query',
                required_permissions=(),
            ),
        ),
        ToolSpec(
            name='erp_aftersales_query', schema=schemas['erp_aftersales_query'],
            domain='erp', availability=ToolAvailability(requires_org=True),
            risk_level='safe', parallelizable=True, cacheable=True,
            effects=('unknown',), executor_type="legacy", handler_key='erp_aftersales_query',
            exposure=Exposure.PUBLIC,
            source="services.tools.definitions.erp.build_specs", definition_kind="explicit",
            catalog_order=3, catalog_groups=('erp_tools',), core=False, legacy_plan_visible=True,
            compatibility_notes=(),
            legacy_validation_schema=erp_schemas.ERP_TOOL_SCHEMAS.get('erp_aftersales_query'),
            policy_rules=ToolPolicyRules(
                operation='read',
                plan_allowed=True,
                execution_modes=('interactive',),
                action_rule='erp_query',
                required_permissions=(),
            ),
        ),
        ToolSpec(
            name='erp_warehouse_query', schema=schemas['erp_warehouse_query'],
            domain='erp', availability=ToolAvailability(requires_org=True),
            risk_level='safe', parallelizable=True, cacheable=True,
            effects=('unknown',), executor_type="legacy", handler_key='erp_warehouse_query',
            exposure=Exposure.PUBLIC,
            source="services.tools.definitions.erp.build_specs", definition_kind="explicit",
            catalog_order=4, catalog_groups=('erp_tools',), core=False, legacy_plan_visible=True,
            compatibility_notes=(),
            legacy_validation_schema=erp_schemas.ERP_TOOL_SCHEMAS.get('erp_warehouse_query'),
            policy_rules=ToolPolicyRules(
                operation='read',
                plan_allowed=True,
                execution_modes=('interactive',),
                action_rule='erp_query',
                required_permissions=(),
            ),
        ),
        ToolSpec(
            name='erp_purchase_query', schema=schemas['erp_purchase_query'],
            domain='erp', availability=ToolAvailability(requires_org=True),
            risk_level='safe', parallelizable=True, cacheable=True,
            effects=('unknown',), executor_type="legacy", handler_key='erp_purchase_query',
            exposure=Exposure.PUBLIC,
            source="services.tools.definitions.erp.build_specs", definition_kind="explicit",
            catalog_order=5, catalog_groups=('erp_tools',), core=False, legacy_plan_visible=True,
            compatibility_notes=(),
            legacy_validation_schema=erp_schemas.ERP_TOOL_SCHEMAS.get('erp_purchase_query'),
            policy_rules=ToolPolicyRules(
                operation='read',
                plan_allowed=True,
                execution_modes=('interactive',),
                action_rule='erp_query',
                required_permissions=(),
            ),
        ),
        ToolSpec(
            name='erp_taobao_query', schema=schemas['erp_taobao_query'],
            domain='erp', availability=ToolAvailability(requires_org=True),
            risk_level='safe', parallelizable=True, cacheable=True,
            effects=('unknown',), executor_type="legacy", handler_key='erp_taobao_query',
            exposure=Exposure.PUBLIC,
            source="services.tools.definitions.erp.build_specs", definition_kind="explicit",
            catalog_order=6, catalog_groups=('erp_tools',), core=False, legacy_plan_visible=True,
            compatibility_notes=(),
            legacy_validation_schema=erp_schemas.ERP_TOOL_SCHEMAS.get('erp_taobao_query'),
            policy_rules=ToolPolicyRules(
                operation='read',
                plan_allowed=True,
                execution_modes=('interactive',),
                action_rule='erp_query',
                required_permissions=(),
            ),
        ),
        ToolSpec(
            name='erp_execute', schema=schemas['erp_execute'],
            domain='erp', availability=ToolAvailability(requires_org=True),
            risk_level='dangerous', parallelizable=False, cacheable=False,
            effects=('unknown',), executor_type="legacy", handler_key='erp_execute',
            exposure=Exposure.PUBLIC,
            source="services.tools.definitions.erp.build_specs", definition_kind="explicit",
            catalog_order=7, catalog_groups=('erp_tools',), core=False, legacy_plan_visible=True,
            compatibility_notes=(),
            legacy_validation_schema=erp_schemas.ERP_TOOL_SCHEMAS.get('erp_execute'),
            policy_rules=ToolPolicyRules(
                operation='business_write',
                plan_allowed=False,
                execution_modes=('interactive',),
                action_rule='erp_write',
                required_permissions=(),
            ),
        ),
        ToolSpec(
            name='local_data', schema=schemas['local_data'],
            domain='erp', availability=ToolAvailability(requires_org=True),
            risk_level='safe', parallelizable=True, cacheable=True,
            effects=('unknown',), executor_type="legacy", handler_key='local_data',
            exposure=Exposure.PUBLIC,
            source="services.tools.definitions.erp.build_specs", definition_kind="explicit",
            catalog_order=8, catalog_groups=('erp_tools', 'erp_local_tools'), core=False, legacy_plan_visible=True,
            compatibility_notes=('handler extra_fields falls back to fields; no new public field is added',),
            legacy_validation_schema=erp_schemas.ERP_TOOL_SCHEMAS.get('local_data'),
            policy_rules=ToolPolicyRules(
                operation='read',
                plan_allowed=True,
                execution_modes=('interactive',),
                action_rule=None,
                required_permissions=(),
            ),
        ),
        ToolSpec(
            name='local_product_stats', schema=schemas['local_product_stats'],
            domain='erp', availability=ToolAvailability(requires_org=True),
            risk_level='safe', parallelizable=True, cacheable=True,
            effects=('unknown',), executor_type="legacy", handler_key='local_product_stats',
            exposure=Exposure.PUBLIC,
            source="services.tools.definitions.erp.build_specs", definition_kind="explicit",
            catalog_order=9, catalog_groups=('erp_tools', 'erp_local_tools'), core=False, legacy_plan_visible=True,
            compatibility_notes=(),
            legacy_validation_schema=erp_schemas.ERP_TOOL_SCHEMAS.get('local_product_stats'),
            policy_rules=ToolPolicyRules(
                operation='read',
                plan_allowed=True,
                execution_modes=('interactive',),
                action_rule=None,
                required_permissions=(),
            ),
        ),
        ToolSpec(
            name='local_stock_query', schema=schemas['local_stock_query'],
            domain='erp', availability=ToolAvailability(requires_org=True),
            risk_level='safe', parallelizable=True, cacheable=True,
            effects=('unknown',), executor_type="legacy", handler_key='local_stock_query',
            exposure=Exposure.PUBLIC,
            source="services.tools.definitions.erp.build_specs", definition_kind="explicit",
            catalog_order=10, catalog_groups=('erp_tools', 'erp_local_tools'), core=False, legacy_plan_visible=True,
            compatibility_notes=(),
            legacy_validation_schema=erp_schemas.ERP_TOOL_SCHEMAS.get('local_stock_query'),
            policy_rules=ToolPolicyRules(
                operation='read',
                plan_allowed=True,
                execution_modes=('interactive',),
                action_rule=None,
                required_permissions=(),
            ),
        ),
        ToolSpec(
            name='local_product_identify', schema=schemas['local_product_identify'],
            domain='erp', availability=ToolAvailability(requires_org=True),
            risk_level='safe', parallelizable=True, cacheable=True,
            effects=('unknown',), executor_type="legacy", handler_key='local_product_identify',
            exposure=Exposure.PUBLIC,
            source="services.tools.definitions.erp.build_specs", definition_kind="explicit",
            catalog_order=11, catalog_groups=('erp_tools', 'erp_local_tools'), core=False, legacy_plan_visible=True,
            compatibility_notes=(),
            legacy_validation_schema=erp_schemas.ERP_TOOL_SCHEMAS.get('local_product_identify'),
            policy_rules=ToolPolicyRules(
                operation='read',
                plan_allowed=True,
                execution_modes=('interactive',),
                action_rule=None,
                required_permissions=(),
            ),
        ),
        ToolSpec(
            name='local_platform_map_query', schema=schemas['local_platform_map_query'],
            domain='erp', availability=ToolAvailability(requires_org=True),
            risk_level='safe', parallelizable=True, cacheable=True,
            effects=('unknown',), executor_type="legacy", handler_key='local_platform_map_query',
            exposure=Exposure.PUBLIC,
            source="services.tools.definitions.erp.build_specs", definition_kind="explicit",
            catalog_order=12, catalog_groups=('erp_tools', 'erp_local_tools'), core=False, legacy_plan_visible=True,
            compatibility_notes=(),
            legacy_validation_schema=erp_schemas.ERP_TOOL_SCHEMAS.get('local_platform_map_query'),
            policy_rules=ToolPolicyRules(
                operation='read',
                plan_allowed=True,
                execution_modes=('interactive',),
                action_rule=None,
                required_permissions=(),
            ),
        ),
        ToolSpec(
            name='local_compare_stats', schema=schemas['local_compare_stats'],
            domain='erp', availability=ToolAvailability(requires_org=True),
            risk_level='safe', parallelizable=True, cacheable=True,
            effects=('unknown',), executor_type="legacy", handler_key='local_compare_stats',
            exposure=Exposure.PUBLIC,
            source="services.tools.definitions.erp.build_specs", definition_kind="explicit",
            catalog_order=13, catalog_groups=('erp_tools', 'erp_local_tools'), core=False, legacy_plan_visible=True,
            compatibility_notes=(),
            legacy_validation_schema=erp_schemas.ERP_TOOL_SCHEMAS.get('local_compare_stats'),
            policy_rules=ToolPolicyRules(
                operation='read',
                plan_allowed=True,
                execution_modes=('interactive',),
                action_rule=None,
                required_permissions=(),
            ),
        ),
        ToolSpec(
            name='local_shop_list', schema=schemas['local_shop_list'],
            domain='erp', availability=ToolAvailability(requires_org=True),
            risk_level='safe', parallelizable=True, cacheable=True,
            effects=('unknown',), executor_type="legacy", handler_key='local_shop_list',
            exposure=Exposure.PUBLIC,
            source="services.tools.definitions.erp.build_specs", definition_kind="explicit",
            catalog_order=14, catalog_groups=('erp_tools', 'erp_local_tools'), core=False, legacy_plan_visible=True,
            compatibility_notes=(),
            legacy_validation_schema=erp_schemas.ERP_TOOL_SCHEMAS.get('local_shop_list'),
            policy_rules=ToolPolicyRules(
                operation='read',
                plan_allowed=True,
                execution_modes=('interactive',),
                action_rule=None,
                required_permissions=(),
            ),
        ),
        ToolSpec(
            name='local_warehouse_list', schema=schemas['local_warehouse_list'],
            domain='erp', availability=ToolAvailability(requires_org=True),
            risk_level='safe', parallelizable=True, cacheable=True,
            effects=('unknown',), executor_type="legacy", handler_key='local_warehouse_list',
            exposure=Exposure.PUBLIC,
            source="services.tools.definitions.erp.build_specs", definition_kind="explicit",
            catalog_order=15, catalog_groups=('erp_tools', 'erp_local_tools'), core=False, legacy_plan_visible=True,
            compatibility_notes=(),
            legacy_validation_schema=erp_schemas.ERP_TOOL_SCHEMAS.get('local_warehouse_list'),
            policy_rules=ToolPolicyRules(
                operation='read',
                plan_allowed=True,
                execution_modes=('interactive',),
                action_rule=None,
                required_permissions=(),
            ),
        ),
        ToolSpec(
            name='local_supplier_list', schema=schemas['local_supplier_list'],
            domain='erp', availability=ToolAvailability(requires_org=True),
            risk_level='safe', parallelizable=True, cacheable=True,
            effects=('unknown',), executor_type="legacy", handler_key='local_supplier_list',
            exposure=Exposure.PUBLIC,
            source="services.tools.definitions.erp.build_specs", definition_kind="explicit",
            catalog_order=16, catalog_groups=('erp_tools', 'erp_local_tools'), core=False, legacy_plan_visible=True,
            compatibility_notes=(),
            legacy_validation_schema=erp_schemas.ERP_TOOL_SCHEMAS.get('local_supplier_list'),
            policy_rules=ToolPolicyRules(
                operation='read',
                plan_allowed=True,
                execution_modes=('interactive',),
                action_rule=None,
                required_permissions=(),
            ),
        ),
        ToolSpec(
            name='trigger_erp_sync', schema=schemas['trigger_erp_sync'],
            domain='erp', availability=ToolAvailability(requires_org=True),
            risk_level='dangerous', parallelizable=False, cacheable=False,
            effects=('unknown',), executor_type="legacy", handler_key='trigger_erp_sync',
            exposure=Exposure.PUBLIC,
            source="services.tools.definitions.erp.build_specs", definition_kind="explicit",
            catalog_order=17, catalog_groups=('erp_tools', 'erp_local_tools'), core=False, legacy_plan_visible=True,
            compatibility_notes=(),
            legacy_validation_schema=erp_schemas.ERP_TOOL_SCHEMAS.get('trigger_erp_sync'),
            policy_rules=ToolPolicyRules(
                operation='business_write',
                plan_allowed=False,
                execution_modes=('interactive',),
                action_rule=None,
                required_permissions=(),
            ),
        ),
        ToolSpec(
            name='erp_agent', schema=_schema_erp_agent(),
            domain='general', availability=ToolAvailability(),
            risk_level='safe', parallelizable=True, cacheable=True,
            effects=('unknown',), executor_type="legacy", handler_key='erp_agent',
            exposure=Exposure.PUBLIC,
            source="services.tools.definitions.erp.build_specs", definition_kind="explicit",
            catalog_order=24, catalog_groups=('common_tools',), core=True, legacy_plan_visible=False,
            compatibility_notes=('query -> task in existing validator and handler; task takes precedence',),
            policy_rules=ToolPolicyRules(
                operation='read',
                plan_allowed=False,
                execution_modes=('interactive', 'scheduled', 'preflight'),
                action_rule=None,
                required_permissions=(),
            ),
        ),
        ToolSpec(
            name='erp_analyze', schema=_schema_erp_analyze(),
            domain='general', availability=ToolAvailability(),
            risk_level='safe', parallelizable=True, cacheable=True,
            effects=('unknown',), executor_type="legacy", handler_key='erp_analyze',
            exposure=Exposure.PUBLIC,
            source="services.tools.definitions.erp.build_specs", definition_kind="explicit",
            catalog_order=25, catalog_groups=('common_tools',), core=True, legacy_plan_visible=True,
            compatibility_notes=('query fallback exists in handler only; validator still uses public task schema',),
            policy_rules=ToolPolicyRules(
                operation='analysis',
                plan_allowed=True,
                execution_modes=('interactive', 'scheduled', 'preflight'),
                action_rule=None,
                required_permissions=(),
            ),
        ),
        ToolSpec(
            name='erp_api_search', schema=_schema_erp_api_search(),
            domain='erp', availability=ToolAvailability(),
            risk_level='safe', parallelizable=True, cacheable=True,
            effects=('unknown',), executor_type="legacy", handler_key='erp_api_search',
            exposure=Exposure.PUBLIC,
            source="services.tools.definitions.erp.build_specs", definition_kind="explicit",
            catalog_order=26, catalog_groups=('common_tools',), core=False, legacy_plan_visible=True,
            compatibility_notes=(),
            legacy_validation_schema={'required': ['query'], 'properties': {'query': {'type': 'string'}}},
            policy_rules=ToolPolicyRules(
                operation='read',
                plan_allowed=True,
                execution_modes=('interactive',),
                action_rule=None,
                required_permissions=(),
            ),
            schema_variants={'erp_search': erp_schemas.build_erp_search_tool()},
        ),
        ToolSpec(
            name='fetch_all_pages', schema=erp_schemas.build_fetch_all_pages_tool(),
            domain='erp', availability=ToolAvailability(requires_org=True),
            risk_level='safe', parallelizable=False, cacheable=False,
            effects=('workspace_artifacts', 'file_index'), executor_type="legacy", handler_key='fetch_all_pages',
            exposure=Exposure.LEGACY_INTERNAL,
            source="services.tools.definitions.erp.build_specs", definition_kind="explicit",
            catalog_order=33, catalog_groups=(), core=False, legacy_plan_visible=True,
            compatibility_notes=(),
            policy_rules=ToolPolicyRules(
                operation='analysis',
                plan_allowed=True,
                execution_modes=('interactive',),
                action_rule='erp_raw_read',
                required_permissions=(),
            ),
        ),
    )
