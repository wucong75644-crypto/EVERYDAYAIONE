"""
本地数据库导出工具

从 erp_document_items 表分批查询明细数据，以 JSONL 格式流式写入 staging 文件。
供 code_execute 沙盒用 pd.read_json(path, lines=True) 读取后生成 Excel/CSV。
"""

import json
import time as _time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import aiofiles
from loguru import logger

# 中国时间（与 erp_document_items.doc_created_at 对齐）
CN_TZ = timezone(timedelta(hours=8))

# 时间列映射（防注入，只允许白名单列名）
_TIME_COL_MAP = {
    "doc_created_at": "doc_created_at",
    "pay_time": "pay_time",
    "consign_time": "consign_time",
}

# 导出字段（不导出 id/org_id/extra_json/synced_at 等内部字段）
_EXPORT_COLUMNS = (
    "doc_type,doc_id,doc_code,doc_status,"
    "doc_created_at,doc_modified_at,"
    "outer_id,sku_outer_id,item_name,"
    "quantity,quantity_received,price,amount,"
    "supplier_name,warehouse_name,shop_name,platform,"
    "order_no,order_status,express_no,express_company,"
    "cost,pay_time,consign_time,"
    "post_fee,gross_profit,pay_amount,"
    "aftersale_type,refund_money,raw_refund_money,"
    "order_type,remark"
)

# PII 脱敏字段
_PII_FIELDS = ("receiver_name", "receiver_phone")

# 分批查询配置
BATCH_SIZE = 5000
MAX_ROWS_LIMIT = 10000
DEFAULT_MAX_ROWS = 5000


def _mask_phone(phone: str) -> str:
    """手机号脱敏：138****1234"""
    if phone and len(phone) >= 7:
        return phone[:3] + "****" + phone[-4:]
    return phone or ""


def _mask_pii(row: dict) -> dict:
    """脱敏 PII 字段（就地修改，不复制）"""
    if "receiver_phone" in row:
        row["receiver_phone"] = _mask_phone(row.get("receiver_phone", ""))
    if "receiver_name" in row:
        name = row.get("receiver_name", "")
        if name and len(name) >= 2:
            row["receiver_name"] = name[0] + "*" * (len(name) - 1)
    return row


def _validate_keyword(keyword: str, field_name: str) -> tuple[str | None, str | None]:
    """验证模糊搜索关键词长度，返回 (error_msg, match_pattern)

    1 字符：拒绝
    2 字符：前缀匹配（keyword%）
    3+ 字符：模糊匹配（%keyword%）
    """
    if len(keyword) < 2:
        return f"❌ {field_name}关键词至少2个字符", None
    if len(keyword) == 2:
        return None, f"{keyword}%"  # 前缀匹配
    return None, f"%{keyword}%"  # 模糊匹配


async def local_db_export(
    db: Any,
    doc_type: str,
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
    """从本地数据库分批导出明细数据到 JSONL staging 文件"""
    from services.kuaimai.erp_local_helpers import _apply_org, check_sync_health

    # 参数校验
    max_rows = min(max_rows or DEFAULT_MAX_ROWS, MAX_ROWS_LIMIT)
    time_col = _TIME_COL_MAP.get(time_type or "doc_created_at", "doc_created_at")
    cutoff = (datetime.now(CN_TZ) - timedelta(days=max(days, 1))).isoformat()

    # 匹配精度校验
    if shop_name:
        err, pattern = _validate_keyword(shop_name, "店铺名")
        if err:
            return err
        shop_pattern = pattern
    else:
        shop_pattern = None

    # 准备 staging 文件路径
    from core.config import get_settings
    settings = get_settings()

    conv_id = conversation_id or "default"
    staging_dir = Path(settings.file_workspace_root) / "staging" / conv_id
    staging_dir.mkdir(parents=True, exist_ok=True)

    ts = int(_time.time())
    filename = f"local_{doc_type}_{ts}.jsonl"
    staging_path = staging_dir / filename
    rel_path = f"staging/{conv_id}/{filename}"

    start = _time.monotonic()
    total_rows = 0
    preview_rows: list[dict] = []

    try:
        async with aiofiles.open(staging_path, "w", encoding="utf-8") as f:
            offset = 0
            while offset < max_rows:
                batch_limit = min(BATCH_SIZE, max_rows - offset)

                # 构建查询
                q = (
                    db.table("erp_document_items")
                    .select(_EXPORT_COLUMNS)
                    .eq("doc_type", doc_type)
                    .gte(time_col, cutoff)
                )
                q = _apply_org(q, org_id)

                # 可选筛选（SQL 层执行）
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

                # 逐行写入 JSONL（流式，不占内存）
                for row in batch:
                    _mask_pii(row)
                    line = json.dumps(row, ensure_ascii=False, default=str)
                    await f.write(line + "\n")

                # 保存前 3 条作为预览
                if not preview_rows:
                    preview_rows = batch[:3]

                total_rows += len(batch)
                offset += len(batch)

                # 最后一批不满 → 数据已查完
                if len(batch) < batch_limit:
                    break

    except Exception as e:
        logger.error(f"local_db_export failed | error={e}")
        # 清理不完整的文件
        if staging_path.exists():
            staging_path.unlink(missing_ok=True)
        return f"导出查询失败: {e}"

    elapsed = _time.monotonic() - start

    if total_rows == 0:
        # 无数据，清理空文件
        staging_path.unlink(missing_ok=True)
        health = check_sync_health(db, [doc_type], org_id=org_id)
        return f"无数据（{doc_type}，近{days}天）\n{health}".strip()

    # 预览
    preview_lines = []
    for row in preview_rows:
        preview_lines.append(
            json.dumps(row, ensure_ascii=False, default=str)[:200]
        )
    preview = "\n".join(preview_lines)

    logger.info(
        f"local_db_export | doc_type={doc_type} | rows={total_rows} | "
        f"batches={offset // BATCH_SIZE + 1} | "
        f"elapsed={elapsed:.3f}s | path={rel_path}"
    )

    return (
        f"[数据已暂存] {rel_path}\n"
        f"共 {total_rows} 条记录（JSONL格式），查询耗时 {elapsed:.3f}秒。\n"
        f"如需处理请调 code_execute，"
        f"用 raw=await read_file(\"{rel_path}\"); "
        f"df=pd.read_json(io.StringIO(raw), lines=True) 读取。\n\n"
        f"前3条预览：\n{preview}"
    )
