"""
订单分类引擎

- 互斥分类：每个订单只属于一个分类
- 排除优先：先匹配排除规则，剩余归入有效
- 内存缓存 5 分钟 TTL（单进程异步架构，无多 worker 一致性问题）
- 懒加载：第一次查询时自动写入默认规则

设计文档: docs/document/TECH_ERP数据完整性与查询准确性.md §5.4
"""

from __future__ import annotations

import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from loguru import logger


@dataclass(frozen=True)
class ClassificationResult:
    """分类结果（不可变）"""
    total: dict[str, Any]
    categories: dict[str, dict[str, Any]]
    valid: dict[str, Any]

    @property
    def categories_list(self) -> list[dict[str, Any]]:
        """返回排除类别列表（不含有效订单）"""
        return [
            {"name": name, **data}
            for name, data in self.categories.items()
            if name != "有效订单"
        ]

    def to_display_text(self, *, show_recommendation: bool = True) -> str:
        """生成树形展示文本。

        show_recommendation: 是否显示"后续计算请默认使用有效订单数据"提示。
            include_invalid=true 时不显示（用户明确要全量）。
        """
        lines = ["📊 订单统计", ""]
        total_count = self.total.get("doc_count", 0)
        lines.append(f"总订单数：{total_count:,} 笔")

        valid_count = self.valid.get("doc_count", 0)
        valid_amount = self.valid.get("total_amount", 0)
        lines.append(f"├── ✅ 有效订单：{valid_count:,} 笔 | ¥{valid_amount:,.2f}")

        for name, data in self.categories.items():
            if name == "有效订单":
                continue
            count = data.get("doc_count", 0)
            pct = f"（{count / total_count * 100:.1f}%）" if total_count else ""
            lines.append(f"├── 🔸 {name}：{count:,} 笔{pct}")

        lines.append("")
        lines.append(
            f"结论：实际成交 {valid_count:,} 笔，销售金额 ¥{valid_amount:,.2f}"
        )
        if show_recommendation:
            lines.append("（后续计算请默认使用有效订单数据）")
        return "\n".join(lines)


_ZERO = {"doc_count": 0, "total_qty": 0, "total_amount": 0}


def _sql_lit(value: Any) -> str:
    """值转 SQL 字面量（字符串加引号+转义单引号，数字原样）。"""
    if isinstance(value, (int, float)):
        return str(value)
    escaped = str(value).replace("'", "''")
    return f"'{escaped}'"


class OrderClassifier:
    """订单分类引擎"""

    _cache: dict[str, tuple[list[dict], float]] = {}
    CACHE_TTL = 300

    def __init__(self, rules: list[dict]) -> None:
        self.rules = rules

    @classmethod
    def for_org(cls, db: Any, org_id: str) -> OrderClassifier:
        """加载指定租户的分类规则（带缓存）

        同步调用 db.table().execute()，与查询链路 LocalDBClient 一致。
        """
        cached = cls._cache.get(org_id)
        if cached and time.time() < cached[1]:
            return cls(cached[0])

        rules = (
            db.table("erp_classification_rules")
            .select("*")
            .eq("org_id", org_id)
            .eq("doc_type", "order")
            .eq("enabled", True)
            .is_("shop_id", "null")
            .order("priority")
            .order("created_at")
            .execute()
        )

        if not rules.data:
            cls._init_default_rules(db, org_id)
            rules = (
                db.table("erp_classification_rules")
                .select("*")
                .eq("org_id", org_id)
                .eq("doc_type", "order")
                .eq("enabled", True)
                .is_("shop_id", "null")
                .order("priority")
                .order("created_at")
                .execute()
            )

        cls._cache[org_id] = (rules.data, time.time() + cls.CACHE_TTL)
        return cls(rules.data)

    def classify(self, rows: list[dict]) -> ClassificationResult:
        """对 RPC 返回的分组数据做分类汇总。

        每个 row = {"order_type": "2,3,10,0", "order_status": "...",
                    "is_scalping": 0/1,
                    "doc_count": N, "total_qty": N, "total_amount": N}
        """
        categories: dict[str, dict[str, Any]] = {}
        total = {**_ZERO}

        for row in rows:
            doc_count = int(row.get("doc_count", 0))
            total_qty = float(row.get("total_qty", 0))
            total_amount = float(row.get("total_amount", 0))

            total["doc_count"] += doc_count
            total["total_qty"] += total_qty
            total["total_amount"] += total_amount

            matched_name = None
            for rule in self.rules:
                if self._match_all_conditions(row, rule.get("conditions", [])):
                    matched_name = rule["rule_name"]
                    break

            if not matched_name:
                matched_name = "有效订单"

            cat = categories.setdefault(matched_name, {**_ZERO})
            cat["doc_count"] += doc_count
            cat["total_qty"] += total_qty
            cat["total_amount"] += total_amount

        # 未知 order_type 监控
        known_types = {"0", "2", "3", "7", "8", "10", "14", "33", "99"}
        seen_types: set[str] = set()
        for row in rows:
            parts = [x.strip() for x in (row.get("order_type") or "").split(",")]
            seen_types.update(p for p in parts if p)
        unknown = seen_types - known_types
        if unknown:
            logger.warning(f"未知 order_type 出现: {unknown}，请检查是否需要新增排除规则")

        valid = categories.get("有效订单", {**_ZERO})
        return ClassificationResult(total=total, categories=categories, valid=valid)

    def classify_grouped(
        self, rows: list[dict],
    ) -> dict[str, ClassificationResult]:
        """按 group_key 分桶，每桶独立分类。

        RPC 返回 p_group_by 模式时，每行多一个 group_key 字段。
        按 group_key 分桶后，复用 classify() 逐桶分类。
        """
        buckets: dict[str, list[dict]] = defaultdict(list)
        for row in rows:
            buckets[row.get("group_key") or "未知"].append(row)
        return {key: self.classify(bucket) for key, bucket in buckets.items()}

    def to_case_sql(self) -> str:
        """从规则表动态生成 SQL CASE WHEN 表达式。

        用于 DuckDB 导出时给每行订单打分类标签列 order_class。
        单一规则源：规则改了，导出标签自动一致。
        """
        when_clauses: list[str] = []
        for rule in self.rules:
            name = rule["rule_name"]
            conditions = rule.get("conditions", [])
            if not conditions:
                continue  # 兜底规则（有效订单）用 ELSE
            sql_parts: list[str] = []
            for cond in conditions:
                fld = cond["field"]
                op = cond["op"]
                val = cond["value"]
                sql_parts.append(self._cond_to_sql(fld, op, val))
            if sql_parts:
                when_clauses.append(
                    f"WHEN {' AND '.join(sql_parts)} THEN '{name}'"
                )
        fallback = "有效订单"
        return f"CASE {' '.join(when_clauses)} ELSE '{fallback}' END"

    @staticmethod
    def _cond_to_sql(field: str, op: str, value: Any) -> str:
        """单个条件转 SQL 片段（DuckDB 兼容）。"""
        if op == "list_has":
            # order_type 是逗号分隔字符串，用 string_split + list_contains
            vals = [str(v) for v in value]
            or_parts = [
                f"list_contains(string_split({field}, ','), '{v}')"
                for v in vals
            ]
            return f"({' OR '.join(or_parts)})"
        elif op == "eq":
            return f"{field} = {_sql_lit(value)}"
        elif op == "ne":
            return f"{field} != {_sql_lit(value)}"
        elif op == "in":
            vals = ", ".join(_sql_lit(v) for v in value)
            return f"{field} IN ({vals})"
        elif op == "not_in":
            vals = ", ".join(_sql_lit(v) for v in value)
            return f"{field} NOT IN ({vals})"
        elif op == "list_not_has":
            vals = [str(v) for v in value]
            or_parts = [
                f"list_contains(string_split({field}, ','), '{v}')"
                for v in vals
            ]
            return f"(NOT ({' OR '.join(or_parts)}))"
        return "TRUE"

    @classmethod
    def invalidate_cache(cls, org_id: str | None = None) -> None:
        """手动清缓存（管理员改规则后调用）"""
        if org_id:
            cls._cache.pop(org_id, None)
        else:
            cls._cache.clear()

    @staticmethod
    def _match_all_conditions(row: dict, conditions: list[dict]) -> bool:
        """条件列表内部 AND。空条件 = 永远匹配（兜底规则）。"""
        if not conditions:
            return True
        return all(
            OrderClassifier._match_condition(row, c)
            for c in conditions
        )

    @staticmethod
    def _match_condition(row: dict, cond: dict) -> bool:
        value = row.get(cond["field"])
        op = cond["op"]
        target = cond["value"]

        # NULL 处理：正向匹配→False，反向匹配→True
        if value is None:
            return op.startswith("not_") or op == "ne"

        if op == "list_has":
            parts = [x.strip() for x in str(value).split(",")]
            return any(str(t) in parts for t in target)
        elif op == "list_not_has":
            parts = [x.strip() for x in str(value).split(",")]
            return not any(str(t) in parts for t in target)
        elif op == "in":
            return value in target
        elif op == "not_in":
            return value not in target
        elif op == "eq":
            return value == target
        elif op == "ne":
            return value != target
        return False

    @classmethod
    def _init_default_rules(cls, db: Any, org_id: str) -> None:
        """懒加载：首次查询时写入默认规则。

        先清理可能的残留（部分写入场景），再全量写入，确保规则集完整。
        同步调用，与查询链路 LocalDBClient 一致。
        """
        from config.default_classification_rules import DEFAULT_ORDER_RULES

        # 清理残留（防止上次中途失败留下不完整规则集）
        (
            db.table("erp_classification_rules")
            .delete()
            .eq("org_id", org_id)
            .eq("doc_type", "order")
            .execute()
        )

        for rule in DEFAULT_ORDER_RULES:
            (
                db.table("erp_classification_rules")
                .insert({
                    "org_id": org_id,
                    "doc_type": "order",
                    "rule_name": rule["rule_name"],
                    "rule_icon": rule.get("rule_icon", "🔸"),
                    "priority": rule.get("priority", 0),
                    "conditions": rule["conditions"],
                    "is_valid_order": rule.get("is_valid_order", False),
                })
                .execute()
            )
        logger.info(f"默认分类规则已初始化 | org_id={org_id} count={len(DEFAULT_ORDER_RULES)}")
