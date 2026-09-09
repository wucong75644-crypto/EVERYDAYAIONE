"""Policy-only declarations for the existing catalog, consumed by both paths.

No schema ownership/cache/effect migration. New legacy names must be reviewed;
they cannot inherit 'read' from their name, safety level or concurrency flag.
"""

from .spec import ToolPolicyRules


# operation, plan eligibility, domain action resolver
_RULES = {
    "search_knowledge": ("read", True, None),
    "web_search": ("read", True, None),
    "file_search": ("read", True, None),
    "file_analyze": ("analysis", True, None),
    "code_execute": ("analysis", True, None),
    "erp_analyze": ("analysis", True, None),
    "erp_agent": ("read", False, None),
    "social_crawler": ("read", False, None),
    "file_delete": ("business_write", False, None),
    "restore_file": ("business_write", False, None),
    "trigger_erp_sync": ("business_write", False, None),
    "generate_image": ("generation", False, None),
    "generate_video": ("generation", False, None),
    "image_agent": ("generation", False, None),
    "manage_scheduled_task": ("proposal", True, "scheduled_task"),
    "erp_api_search": ("read", True, None),
    "erp_info_query": ("read", True, "erp_query"),
    "erp_product_query": ("read", True, "erp_query"),
    "erp_trade_query": ("read", True, "erp_query"),
    "erp_aftersales_query": ("read", True, "erp_query"),
    "erp_warehouse_query": ("read", True, "erp_query"),
    "erp_purchase_query": ("read", True, "erp_query"),
    "erp_taobao_query": ("read", True, "erp_query"),
    "erp_execute": ("business_write", False, "erp_write"),
    "local_data": ("read", True, None),
    "local_product_stats": ("read", True, None),
    "local_stock_query": ("read", True, None),
    "local_product_identify": ("read", True, None),
    "local_platform_map_query": ("read", True, None),
    "local_compare_stats": ("read", True, None),
    "local_shop_list": ("read", True, None),
    "local_warehouse_list": ("read", True, None),
    "local_supplier_list": ("read", True, None),
    "fetch_all_pages": ("analysis", True, "erp_raw_read"),
    "get_conversation_context": ("read", True, None),
}


def legacy_policy_rules(name: str, scheduled_names: frozenset[str]) -> ToolPolicyRules:
    try:
        operation, plan_allowed, action_rule = _RULES[name]
    except KeyError:
        raise ValueError(f"Legacy tool missing policy review: {name}") from None
    # Reuse the current scheduler's core capability boundary, never expand it.
    modes = ("interactive",)
    if name in scheduled_names:
        modes += ("scheduled",)
        if operation in {"read", "analysis"}:
            modes += ("preflight",)
    return ToolPolicyRules(
        operation=operation, plan_allowed=plan_allowed,
        execution_modes=modes, action_rule=action_rule,
    )
