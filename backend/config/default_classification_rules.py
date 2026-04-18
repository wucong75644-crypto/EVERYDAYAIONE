"""
订单分类默认规则模板

基于蓝创业务实践，其他租户可通过 erp_classification_rules 表自定义。

规则模型：
- 互斥分类：每个订单只属于一个分类，所有分类数量之和 = 总数
- 排除优先：先匹配排除规则（刷单/补发/已关闭），剩余全部归入有效订单
- priority 数字小 = 优先匹配
- 同名规则 = OR 语义（匹配任一条都归入该分类）
- 有效订单 conditions=[] 永远匹配（兜底）

设计文档: docs/document/TECH_ERP数据完整性与查询准确性.md §5.3
"""

DEFAULT_ORDER_RULES: list[dict] = [
    {
        "rule_name": "空包/刷单",
        "rule_icon": "🔸",
        "priority": 10,
        "conditions": [{"field": "order_type", "op": "list_has", "value": [10]}],
        "is_valid_order": False,
    },
    {
        "rule_name": "空包/刷单",
        "rule_icon": "🔸",
        "priority": 10,
        "conditions": [{"field": "is_scalping", "op": "eq", "value": 1}],
        "is_valid_order": False,
    },
    {
        "rule_name": "补发单",
        "rule_icon": "🔸",
        "priority": 20,
        "conditions": [{"field": "order_type", "op": "list_has", "value": [14]}],
        "is_valid_order": False,
    },
    {
        "rule_name": "已关闭/取消",
        "rule_icon": "🔸",
        "priority": 30,
        "conditions": [
            {"field": "order_status", "op": "in", "value": ["CLOSED", "CANCEL"]},
        ],
        "is_valid_order": False,
    },
    {
        "rule_name": "有效订单",
        "rule_icon": "✅",
        "priority": 99,
        "conditions": [],
        "is_valid_order": True,
    },
]
