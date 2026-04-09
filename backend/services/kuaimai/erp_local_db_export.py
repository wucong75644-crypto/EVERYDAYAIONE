"""
本地数据库导出工具（两步协议）

Step 1: 只传 doc_type → 返回可用字段文档，Agent 选择需要的列
Step 2: 传 doc_type + columns → 按字段从 erp_document_items 查询，Parquet 写入 staging

Parquet 格式：类型/null/日期零解析问题，pandas read_parquet 100% 可靠。

与远程 erp_* 工具的两步协议统一：
  远程: action 无 params → 参数文档 | action + params → 执行查询
  本地: doc_type 无 columns → 字段文档 | doc_type + columns → 执行导出
"""

import json
import time as _time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pandas as pd
from loguru import logger

# 中国时间（与 erp_document_items.doc_created_at 对齐）
CN_TZ = timezone(timedelta(hours=8))

# 时间列映射（防注入，只允许白名单列名）
_TIME_COL_MAP = {
    "doc_created_at": "doc_created_at",
    "pay_time": "pay_time",
    "consign_time": "consign_time",
}

# 全量可导出字段（排除 id/org_id/extra_json/synced_at 等内部字段）
# 按业务分组，用于生成字段文档
_ALL_COLUMNS: dict[str, list[tuple[str, str]]] = {
    "单据基础": [
        ("doc_type", "单据类型(order/purchase/aftersale/receipt/shelf/purchase_return)"),
        ("doc_id", "单据ID"),
        ("doc_code", "单据编号"),
        ("doc_status", "单据状态"),
        ("item_index", "明细行序号（同一单据内多SKU区分）"),
        ("short_id", "短ID"),
    ],
    "时间": [
        ("doc_created_at", "创建时间"),
        ("doc_modified_at", "修改时间"),
        ("pay_time", "付款时间（仅订单）"),
        ("consign_time", "发货时间（仅订单）"),
        ("finished_at", "完成时间"),
        ("delivery_date", "预计到货日期（仅采购）"),
    ],
    "商品": [
        ("outer_id", "主商家编码（SPU级）"),
        ("sku_outer_id", "SKU编码"),
        ("item_name", "商品名称"),
    ],
    "数量金额": [
        ("quantity", "数量"),
        ("quantity_received", "已到货数量（仅采购）"),
        ("real_qty", "实际数量"),
        ("price", "单价"),
        ("amount", "金额"),
        ("cost", "成本"),
        ("pay_amount", "实付金额"),
        ("post_fee", "运费"),
        ("discount_fee", "优惠金额"),
        ("gross_profit", "毛利"),
    ],
    "关联方": [
        ("supplier_name", "供应商名称（采购/收货/采退）"),
        ("warehouse_name", "仓库名称"),
        ("shop_name", "店铺名称（订单/售后）"),
        ("platform", "来源平台(tb/jd/pdd/dy/xhs/1688)"),
        ("creator_name", "创建人"),
    ],
    "订单物流": [
        ("order_no", "平台订单号"),
        ("order_status", "订单状态"),
        ("order_type", "订单类型"),
        ("express_no", "快递单号"),
        ("express_company", "快递公司"),
        ("purchase_order_code", "采购单号"),
    ],
    "状态标记": [
        ("is_cancel", "是否取消"),
        ("is_refund", "是否退款"),
        ("is_exception", "是否异常"),
        ("is_halt", "是否拦截"),
        ("is_urgent", "是否加急"),
        ("good_status", "货物状态"),
    ],
    "售后": [
        ("aftersale_type", "售后类型(0=其他/1=已发货仅退款/2=退货/3=补发/4=换货/5=未发货仅退款)"),
        ("refund_status", "退款状态"),
        ("refund_money", "系统退款金额"),
        ("raw_refund_money", "平台实退金额"),
        ("actual_return_qty", "实际退货数量"),
        ("text_reason", "退货原因"),
        ("refund_warehouse_name", "退货仓库"),
        ("refund_express_company", "退货快递公司"),
        ("refund_express_no", "退货快递单号"),
        ("reissue_sid", "补发单号"),
        ("platform_refund_id", "平台退款单号"),
        ("good_item_count", "良品数"),
        ("bad_item_count", "次品数"),
    ],
    "备注": [
        ("remark", "备注"),
        ("sys_memo", "系统备注"),
        ("buyer_message", "买家留言"),
    ],
}

# 全部可导出列名（白名单，防注入）
_ALL_COLUMN_NAMES: set[str] = {
    col for group in _ALL_COLUMNS.values() for col, _ in group
}

# 分批查询配置
BATCH_SIZE = 5000
MAX_ROWS_LIMIT = 10000
DEFAULT_MAX_ROWS = 5000


def _generate_column_doc(doc_type: str) -> str:
    """生成字段文档（Step 1 返回给 Agent）"""
    lines = [
        f"## local_db_export 可导出字段（doc_type={doc_type}）\n",
        "在 columns 参数中传入需要的字段名（逗号分隔），不传则导出全部字段。\n",
    ]
    for group_name, columns in _ALL_COLUMNS.items():
        lines.append(f"\n### {group_name}")
        for col_name, col_desc in columns:
            lines.append(f"  - `{col_name}`: {col_desc}")

    lines.append(f"\n### 示例")
    lines.append(
        f'local_db_export(doc_type="{doc_type}", '
        f'columns="order_no,shop_name,amount,pay_time", days=1)'
    )
    return "\n".join(lines)


def _mask_phone(phone: str) -> str:
    """手机号脱敏：138****1234"""
    if phone and len(phone) >= 7:
        return phone[:3] + "****" + phone[-4:]
    return phone or ""


def _mask_pii(row: dict) -> dict:
    """脱敏 PII 字段（就地修改，防御性设计）"""
    if "receiver_phone" in row:
        row["receiver_phone"] = _mask_phone(row.get("receiver_phone", ""))
    if "receiver_name" in row:
        name = row.get("receiver_name", "")
        if name and len(name) >= 2:
            row["receiver_name"] = name[0] + "*" * (len(name) - 1)
    return row


def _validate_keyword(keyword: str, field_name: str) -> tuple[str | None, str | None]:
    """验证模糊搜索关键词长度，返回 (error_msg, match_pattern)"""
    if len(keyword) < 2:
        return f"❌ {field_name}关键词至少2个字符", None
    if len(keyword) == 2:
        return None, f"{keyword}%"
    return None, f"%{keyword}%"


def _parse_columns(columns: str) -> str:
    """解析并验证 columns 参数，返回安全的 SELECT 列表"""
    requested = [c.strip() for c in columns.split(",") if c.strip()]
    # 白名单过滤，防止 SQL 注入
    safe = [c for c in requested if c in _ALL_COLUMN_NAMES]
    if not safe:
        return ",".join(col for col, _ in _ALL_COLUMNS["单据基础"])
    return ",".join(safe)


async def local_db_export(
    db: Any,
    doc_type: str,
    columns: str | None = None,
    days: int = 1,
    time_type: str | None = None,
    shop_name: str | None = None,
    platform: str | None = None,
    product_code: str | None = None,
    status: str | None = None,
    max_rows: int = DEFAULT_MAX_ROWS,
    org_id: str | None = None,
    conversation_id: str | None = None,
) -> str:
    """本地数据库导出（两步协议）

    Step 1: columns=None → 返回字段文档
    Step 2: columns="col1,col2,..." → 按字段导出到 JSONL staging
    """
    # Step 1: 无 columns → 返回字段文档
    if columns is None:
        return _generate_column_doc(doc_type)

    # Step 2: 有 columns → 执行导出
    from services.kuaimai.erp_local_helpers import _apply_org, check_sync_health

    select_cols = _parse_columns(columns)
    max_rows = min(max_rows or DEFAULT_MAX_ROWS, MAX_ROWS_LIMIT)
    time_col = _TIME_COL_MAP.get(time_type or "doc_created_at", "doc_created_at")
    cutoff = (datetime.now(CN_TZ) - timedelta(days=max(days, 1))).isoformat()

    # 匹配精度校验
    shop_pattern = None
    if shop_name:
        err, pattern = _validate_keyword(shop_name, "店铺名")
        if err:
            return err
        shop_pattern = pattern

    # 准备 staging 文件路径
    from core.config import get_settings
    settings = get_settings()

    conv_id = conversation_id or "default"
    staging_dir = Path(settings.file_workspace_root) / "staging" / conv_id
    staging_dir.mkdir(parents=True, exist_ok=True)

    ts = int(_time.time())
    filename = f"local_{doc_type}_{ts}.parquet"
    staging_path = staging_dir / filename
    rel_path = f"staging/{conv_id}/{filename}"

    start = _time.monotonic()
    all_rows: list[dict] = []

    try:
        offset = 0
        while offset < max_rows:
            batch_limit = min(BATCH_SIZE, max_rows - offset)

            q = (
                db.table("erp_document_items")
                .select(select_cols)
                .eq("doc_type", doc_type)
                .gte(time_col, cutoff)
            )
            q = _apply_org(q, org_id)

            if shop_pattern:
                q = q.ilike("shop_name", shop_pattern)
            if platform:
                q = q.eq("platform", platform)
            if product_code:
                q = q.or_(
                    f"outer_id.eq.{product_code},"
                    f"sku_outer_id.eq.{product_code}"
                )
            if status:
                status_col = (
                    "order_status" if doc_type == "order" else "doc_status"
                )
                q = q.eq(status_col, status)

            q = (
                q.order(time_col, desc=True)
                .range(offset, offset + batch_limit - 1)
            )

            result = q.execute()
            batch = result.data or []

            if not batch:
                break

            for row in batch:
                _mask_pii(row)
            all_rows.extend(batch)

            offset += len(batch)
            if len(batch) < batch_limit:
                break

    except Exception as e:
        logger.error(f"local_db_export failed | error={e}")
        return f"导出查询失败: {e}"

    elapsed = _time.monotonic() - start

    if not all_rows:
        health = check_sync_health(db, [doc_type], org_id=org_id)
        return f"无数据（{doc_type}，近{days}天）\n{health}".strip()

    # 写 Parquet（类型/null/日期零解析问题）
    df = pd.DataFrame(all_rows)
    df.to_parquet(staging_path, index=False, engine="pyarrow")

    # 预览前 3 条
    preview = df.head(3).to_string(index=False, max_colwidth=30)

    file_size_kb = staging_path.stat().st_size / 1024

    logger.info(
        f"local_db_export | doc_type={doc_type} | rows={len(all_rows)} | "
        f"cols={select_cols[:50]} | size={file_size_kb:.0f}KB | "
        f"elapsed={elapsed:.3f}s | path={rel_path}"
    )

    return (
        f"[数据已暂存] {rel_path}\n"
        f"共 {len(all_rows)} 条记录（Parquet格式，{file_size_kb:.0f}KB），"
        f"查询耗时 {elapsed:.3f}秒。\n"
        f"如需处理请调 code_execute，"
        f"用 df = pd.read_parquet(STAGING_DIR + '/{filename}') 读取。\n\n"
        f"前3条预览：\n{preview}"
    )
