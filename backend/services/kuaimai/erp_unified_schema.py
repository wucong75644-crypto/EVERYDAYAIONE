"""
统一查询引擎 — 列白名单 + 常量 + 格式化

从 erp_unified_query.py 拆出，保持引擎文件 < 500 行。
设计文档: docs/document/TECH_统一查询引擎FilterDSL.md §6.2
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


# ── 列白名单 ──────────────────────────────────────────


@dataclass(frozen=True)
class ColumnMeta:
    col_type: str   # "text" | "integer" | "numeric" | "timestamp" | "boolean"


# 新增列时：1. 写迁移加列  2. 在此加一行  3. 重启生效
COLUMN_WHITELIST: dict[str, ColumnMeta] = {
    # 单据基础
    "doc_type": ColumnMeta("text"),
    "doc_id": ColumnMeta("text"),
    "doc_code": ColumnMeta("text"),
    "doc_status": ColumnMeta("text"),
    "order_status": ColumnMeta("text"),
    "status_name": ColumnMeta("text"),
    # 时间
    "doc_created_at": ColumnMeta("timestamp"),
    "doc_modified_at": ColumnMeta("timestamp"),
    "pay_time": ColumnMeta("timestamp"),
    "consign_time": ColumnMeta("timestamp"),
    # 商品
    "outer_id": ColumnMeta("text"),
    "sku_outer_id": ColumnMeta("text"),
    "item_name": ColumnMeta("text"),
    # 数量金额
    "quantity": ColumnMeta("numeric"),
    "amount": ColumnMeta("numeric"),
    "cost": ColumnMeta("numeric"),
    "pay_amount": ColumnMeta("numeric"),
    "post_fee": ColumnMeta("numeric"),
    "discount_fee": ColumnMeta("numeric"),
    "gross_profit": ColumnMeta("numeric"),
    "refund_money": ColumnMeta("numeric"),
    # 关联方
    "shop_name": ColumnMeta("text"),
    "platform": ColumnMeta("text"),
    "supplier_name": ColumnMeta("text"),
    "warehouse_name": ColumnMeta("text"),
    # 订单物流
    "order_no": ColumnMeta("text"),
    "express_no": ColumnMeta("text"),
    "express_company": ColumnMeta("text"),
    "order_type": ColumnMeta("text"),
    # 买家
    "buyer_nick": ColumnMeta("text"),
    # 状态标记
    "is_cancel": ColumnMeta("integer"),
    "is_refund": ColumnMeta("integer"),
    "is_exception": ColumnMeta("integer"),
    "is_halt": ColumnMeta("integer"),
    "is_urgent": ColumnMeta("integer"),
    # 售后
    "aftersale_type": ColumnMeta("text"),
    "refund_status": ColumnMeta("text"),
}

# op 与列类型兼容表
OP_COMPAT: dict[str, set[str]] = {
    "text": {"eq", "ne", "like", "in", "is_null"},
    "integer": {"eq", "ne", "gt", "gte", "lt", "lte", "in", "is_null", "between"},
    "numeric": {"eq", "ne", "gt", "gte", "lt", "lte", "in", "is_null", "between"},
    "timestamp": {"eq", "ne", "gt", "gte", "lt", "lte", "is_null", "between"},
    "boolean": {"eq", "ne", "is_null"},
}


# ── 常量 ──────────────────────────────────────────────


TIME_COLUMNS = {"doc_created_at", "doc_modified_at", "pay_time", "consign_time"}
VALID_TIME_COLS = {"doc_created_at", "pay_time", "consign_time"}
VALID_DOC_TYPES = {"order", "purchase", "aftersale", "receipt", "shelf", "purchase_return"}

DOC_TYPE_CN = {
    "order": "订单", "purchase": "采购", "aftersale": "售后",
    "receipt": "收货", "shelf": "上架", "purchase_return": "采退",
}

PLATFORM_CN = {
    "tb": "淘宝", "jd": "京东", "pdd": "拼多多",
    "fxg": "抖音", "kuaishou": "快手", "xhs": "小红书",
    "1688": "1688", "sys": "系统", "wd": "微店",
}

# detail 模式默认字段
DEFAULT_DETAIL_FIELDS: dict[str, list[str]] = {
    "order": [
        "order_no", "shop_name", "platform", "order_status",
        "outer_id", "item_name", "quantity", "amount",
        "pay_time", "consign_time",
    ],
    "purchase": [
        "doc_code", "supplier_name", "doc_status",
        "outer_id", "item_name", "quantity",
        "quantity_received", "amount", "doc_created_at",
    ],
    "aftersale": [
        "doc_code", "aftersale_type", "refund_status",
        "outer_id", "item_name", "quantity",
        "refund_money", "doc_created_at",
    ],
    "receipt": [
        "doc_code", "supplier_name", "doc_status",
        "outer_id", "item_name", "quantity",
        "quantity_received", "doc_created_at",
    ],
    "shelf": [
        "doc_code", "warehouse_name", "doc_status",
        "outer_id", "item_name", "quantity", "doc_created_at",
    ],
    "purchase_return": [
        "doc_code", "supplier_name", "doc_status",
        "outer_id", "item_name", "quantity",
        "amount", "doc_created_at",
    ],
}

# RPC group_by 映射（filter DSL 字段名 → RPC 枚举值）
GROUP_BY_MAP = {
    "outer_id": "product", "item_name": "product",
    "shop_name": "shop", "platform": "platform",
    "supplier_name": "supplier", "warehouse_name": "warehouse",
    "doc_status": "status", "order_status": "status",
}


# ── export 常量 ───────────────────────────────────────


EXPORT_BATCH = 5000
EXPORT_MAX = 10000
EXPORT_DEFAULT = 5000

EXPORT_COLUMNS: dict[str, list[tuple[str, str]]] = {
    "单据基础": [
        ("doc_type", "单据类型"), ("doc_id", "单据ID"), ("doc_code", "单据编号"),
        ("doc_status", "单据状态"), ("item_index", "明细行序号"), ("short_id", "短ID"),
    ],
    "时间": [
        ("doc_created_at", "创建时间"), ("doc_modified_at", "修改时间"),
        ("pay_time", "付款时间"), ("consign_time", "发货时间"),
        ("finished_at", "完成时间"), ("delivery_date", "预计到货日期"),
    ],
    "商品": [
        ("outer_id", "主商家编码"), ("sku_outer_id", "SKU编码"), ("item_name", "商品名称"),
    ],
    "数量金额": [
        ("quantity", "数量"), ("quantity_received", "已到货数量"),
        ("real_qty", "实际数量"), ("price", "单价"), ("amount", "金额"),
        ("cost", "成本"), ("pay_amount", "实付金额"), ("post_fee", "运费"),
        ("discount_fee", "优惠金额"), ("gross_profit", "毛利"),
    ],
    "关联方": [
        ("supplier_name", "供应商"), ("warehouse_name", "仓库"),
        ("shop_name", "店铺"), ("platform", "来源平台"), ("creator_name", "创建人"),
    ],
    "商品规格": [
        ("sku_properties_name", "SKU规格"), ("diff_stock_num", "缺货数量"),
    ],
    "订单物流": [
        ("order_no", "平台订单号"), ("order_status", "订单状态"),
        ("order_type", "订单类型"), ("status_name", "状态中文名"),
        ("express_no", "快递单号"), ("express_company", "快递公司"),
        ("purchase_order_code", "采购单号"),
    ],
    "买家收件人": [
        ("buyer_nick", "买家昵称"), ("receiver_name", "收件人"),
        ("receiver_mobile", "手机号"), ("receiver_phone", "电话"),
        ("receiver_state", "省"), ("receiver_city", "市"),
        ("receiver_district", "区"), ("receiver_address", "详细地址"),
    ],
    "状态标记": [
        ("is_cancel", "是否取消"), ("is_refund", "是否退款"),
        ("is_exception", "是否异常"), ("is_halt", "是否拦截"),
        ("is_urgent", "是否加急"), ("good_status", "货物状态"),
    ],
    "售后": [
        ("aftersale_type", "售后类型"), ("refund_status", "退款状态"),
        ("refund_money", "系统退款金额"), ("raw_refund_money", "平台实退金额"),
        ("actual_return_qty", "实际退货数量"), ("text_reason", "退货原因"),
        ("refund_warehouse_name", "退货仓库"),
    ],
    "备注": [
        ("remark", "备注"), ("sys_memo", "系统备注"), ("buyer_message", "买家留言"),
    ],
}
EXPORT_COLUMN_NAMES: set[str] = {c for g in EXPORT_COLUMNS.values() for c, _ in g}


# ── 数据类型 ──────────────────────────────────────────


@dataclass
class ValidatedFilter:
    field: str
    op: str
    value: Any
    col_type: str


@dataclass
class TimeRange:
    start_iso: str
    end_iso: str
    time_col: str
    date_range: Any   # DateRange
    label: str


# ── 格式化函数 ────────────────────────────────────────


def fmt_summary_total(
    data: dict, type_name: str, label: str,
    db: Any, doc_type: str, org_id: str | None,
) -> str:
    """格式化总计统计"""
    from services.kuaimai.erp_local_helpers import check_sync_health

    doc_count = data.get("doc_count", 0)
    total_qty = data.get("total_qty", 0)
    total_amount = float(data.get("total_amount", 0))

    lines = [
        f"{label} {type_name}统计：\n",
        f"总计: {doc_count}笔 | 数量 {total_qty}件 | 金额 ¥{total_amount:,.2f}",
    ]
    health = check_sync_health(db, [doc_type], org_id=org_id)
    if health:
        lines.append(f"\n{health}")
    return "\n".join(lines)


def fmt_summary_grouped(
    data: list, group_by: str, type_name: str, label: str,
) -> str:
    """格式化分组统计"""
    lines = [f"{label} {type_name}按{group_by}分组：\n"]
    total_docs = 0

    sorted_data = sorted(data, key=lambda x: -(float(x.get("total_amount", 0))))
    for item in sorted_data:
        key = item.get("group_key", "未知")
        doc_count = item.get("doc_count", 0)
        qty = item.get("total_qty", 0)
        amt = float(item.get("total_amount", 0))
        total_docs += doc_count

        plat = item.get("platform")
        if group_by == "shop" and plat:
            key = f"{key}[{PLATFORM_CN.get(plat, plat)}]"

        name = item.get("item_name", "")
        name_suffix = f"({name})" if name and group_by == "product" else ""

        lines.append(f"  {key}{name_suffix}: {doc_count}笔 | {qty}件 | ¥{amt:,.2f}")

    lines.append(f"\n📊 总计：{total_docs}笔")
    return "\n".join(lines)


def fmt_detail_rows(
    rows: list[dict], fields: list[str], type_name: str, limit: int,
) -> str:
    """明细行格式化"""
    total = len(rows)
    lines = [f"{type_name}明细（共{total}条）：\n"]

    for i, row in enumerate(rows, 1):
        parts = []
        for f in fields:
            v = row.get(f)
            if v is not None:
                sv = str(v)
                if len(sv) > 40:
                    sv = sv[:37] + "..."
                parts.append(f"{f}={sv}")
        lines.append(f"  {i}. {' | '.join(parts)}")

    if total >= limit:
        lines.append(f"\n⚠ 仅显示前{limit}条，如需全量请用 mode=export 导出")

    return "\n".join(lines)


def generate_field_doc(doc_type: str) -> str:
    """生成字段文档（export Step 1）"""
    lines = [
        f"## local_data(mode=export) 可导出字段（doc_type={doc_type}）\n",
        "在 fields 参数中传入需要的字段名列表。\n",
    ]
    for group_name, columns in EXPORT_COLUMNS.items():
        lines.append(f"\n### {group_name}")
        for col_name, col_desc in columns:
            lines.append(f"  - `{col_name}`: {col_desc}")

    lines.append(f"\n### 示例")
    lines.append(
        f'local_data(doc_type="{doc_type}", mode="export", '
        f'filters=[...], fields=["order_no","shop_name","amount","pay_time"])'
    )
    return "\n".join(lines)


def mask_pii(row: dict) -> dict:
    """脱敏 PII 字段（就地修改）"""
    for phone_col in ("receiver_phone", "receiver_mobile"):
        if phone_col in row:
            phone = row.get(phone_col, "")
            if phone and len(phone) >= 7:
                row[phone_col] = phone[:3] + "****" + phone[-4:]
    if "receiver_name" in row:
        name = row.get("receiver_name", "")
        if name and len(name) >= 2:
            row["receiver_name"] = name[0] + "*" * (len(name) - 1)
    return row
