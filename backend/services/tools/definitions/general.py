"""general ToolSpec ownership. Business handlers are unchanged."""

from ..spec import Exposure, ToolAvailability, ToolPolicyRules, ToolSpec
from . import crawler_schemas

def _schema_search_knowledge():
    return {
        "type": "function",
        "function": {
            "name": "search_knowledge",
            "description": (
                "搜索企业知识库，查找业务规则、操作流程、SOP、历史经验、"
                "培训文档等非数据类信息。基于语义相似度检索，返回最相关的文档片段。\n\n"
                "返回：匹配的文档片段列表（含来源和相关度），无匹配时返回空列表。\n\n"
                "不要用于：查询业务数据（订单/库存/销售额）→ erp_agent；"
                "查询实时信息（天气/新闻）→ web_search；"
                "查看工作区文件内容 → file_search + file_analyze + code_execute。"
            ),
            "parameters": {
                "type": "object",
                "required": ["query"],
                "properties": {
                    "query": {
                        "type": "string",
                        "description": (
                            "搜索关键词或自然语言问题。"
                            "e.g. '退货流程'、'新员工入职操作指南'、'淘宝发货超时规则'"
                        ),
                    },
                },
            },
        },
    }


def _schema_web_search():
    return {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": (
                "搜索互联网获取实时公开信息：天气、新闻、行业资讯、"
                "政策法规、技术文档、公司公开信息等。\n\n"
                "返回：基于 Google Search 的搜索结果摘要（含来源URL引用），"
                "回答中会标注信息来源。无结果时返回空。\n\n"
                "不要用于：查询企业内部业务数据（订单/库存）→ erp_agent；"
                "查询企业知识库 → search_knowledge；"
                "爬取社交平台内容（小红书/抖音帖子）→ social_crawler。"
            ),
            "parameters": {
                "type": "object",
                "required": ["query"],
                "properties": {
                    "query": {
                        "type": "string",
                        "description": (
                            "搜索关键词，简洁精准。"
                            "e.g. '杭州今天天气'、'2026年跨境电商政策变化'、'快递停发地区最新通知'"
                        ),
                    },
                },
            },
        },
    }


def build_specs():
    schemas = {}
    schemas.update((s["function"]["name"], s) for s in crawler_schemas.build_crawler_tools())
    return (
        ToolSpec(
            name='social_crawler', schema=schemas['social_crawler'],
            domain='general', availability=ToolAvailability(feature_flags=('crawler_enabled',)),
            risk_level='safe', parallelizable=True, cacheable=True,
            effects=('unknown',), executor_type="legacy", handler_key='social_crawler',
            exposure=Exposure.PUBLIC,
            source="services.tools.definitions.general.build_specs", definition_kind="explicit",
            catalog_order=18, catalog_groups=('crawler_tools',), core=True, legacy_plan_visible=False,
            compatibility_notes=(),
            legacy_validation_schema=crawler_schemas.CRAWLER_TOOL_SCHEMAS.get('social_crawler'),
            policy_rules=ToolPolicyRules(
                operation='read',
                plan_allowed=False,
                execution_modes=('interactive', 'scheduled', 'preflight'),
                action_rule=None,
                required_permissions=(),
            ),
        ),
        ToolSpec(
            name='search_knowledge', schema=_schema_search_knowledge(),
            domain='general', availability=ToolAvailability(),
            risk_level='safe', parallelizable=True, cacheable=True,
            effects=('none',), executor_type="legacy", handler_key='search_knowledge',
            exposure=Exposure.PUBLIC,
            source="services.tools.definitions.general.build_specs", definition_kind="explicit",
            catalog_order=27, catalog_groups=('common_tools',), core=True, legacy_plan_visible=True,
            compatibility_notes=(),
            legacy_validation_schema={'required': ['query'], 'properties': {'query': {'type': 'string'}}},
            policy_rules=ToolPolicyRules(
                operation='read',
                plan_allowed=True,
                execution_modes=('interactive', 'scheduled', 'preflight'),
                action_rule=None,
                required_permissions=(),
            ),
        ),
        ToolSpec(
            name='web_search', schema=_schema_web_search(),
            domain='general', availability=ToolAvailability(),
            risk_level='safe', parallelizable=True, cacheable=True,
            effects=('unknown',), executor_type="legacy", handler_key='web_search',
            exposure=Exposure.PUBLIC,
            source="services.tools.definitions.general.build_specs", definition_kind="explicit",
            catalog_order=28, catalog_groups=('common_tools',), core=True, legacy_plan_visible=True,
            compatibility_notes=(),
            policy_rules=ToolPolicyRules(
                operation='read',
                plan_allowed=True,
                execution_modes=('interactive', 'scheduled', 'preflight'),
                action_rule=None,
                required_permissions=(),
            ),
        ),
        ToolSpec(
            name='get_conversation_context', schema=None,
            domain='general', availability=ToolAvailability(requires_personal_context=True),
            risk_level='safe', parallelizable=False, cacheable=False,
            effects=('none',), executor_type="legacy", handler_key='get_conversation_context',
            exposure=Exposure.LEGACY_INTERNAL,
            source="services.tools.definitions.general.build_specs", definition_kind="explicit",
            catalog_order=34, catalog_groups=(), core=False, legacy_plan_visible=True,
            compatibility_notes=(),
            legacy_validation_schema={'properties': {'limit': {'type': 'integer'}}, 'required': []},
            policy_rules=ToolPolicyRules(
                operation='read',
                plan_allowed=True,
                execution_modes=('interactive',),
                action_rule=None,
                required_permissions=(),
            ),
        ),
    )
