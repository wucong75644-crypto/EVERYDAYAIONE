"""
ERP 数据校验脚本 — 逐表对比 API 与本地数据库

⚠️ 多租户警告：此脚本全局 COUNT 所有 ERP 表，不区分企业。
多企业环境下统计结果是所有企业的合计，不反映单个企业的数据准确性。

用法：
    cd /var/www/everydayai/backend
    source venv/bin/activate
    python scripts/verify_erp_data.py
"""

import asyncio
import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

# Python path
backend_dir = Path(__file__).parent.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

from dotenv import load_dotenv
load_dotenv(backend_dir / ".env")

import psycopg
from psycopg.rows import dict_row
from loguru import logger

from services.kuaimai.client import KuaiMaiClient
from core.config import get_settings


settings = get_settings()
DB_URL = settings.database_url


# ============================================================
# 工具函数
# ============================================================

def get_db():
    return psycopg.connect(DB_URL, row_factory=dict_row)


def log_result(table: str, check: str, passed: bool, detail: str = ""):
    icon = "✅" if passed else "❌"
    print(f"  {icon} [{table}] {check}: {detail}")


async def get_client() -> KuaiMaiClient:
    client = KuaiMaiClient(settings)
    return client


# ============================================================
# 1. 商品表校验
# ============================================================

async def verify_products(client: KuaiMaiClient):
    print("\n" + "=" * 60)
    print("📦 商品表 (erp_products + erp_product_skus)")
    print("=" * 60)

    conn = get_db()

    # DB 行数
    db_spu = conn.execute("SELECT count(*) as c FROM erp_products").fetchone()["c"]
    db_sku = conn.execute("SELECT count(*) as c FROM erp_product_skus").fetchone()["c"]
    log_result("erp_products", "SPU总数", True, f"{db_spu} 条")
    log_result("erp_product_skus", "SKU总数", True, f"{db_sku} 条")

    # API 采样对比：取最近修改的5个商品
    rows = conn.execute(
        "SELECT outer_id, title, purchase_price, selling_price FROM erp_products ORDER BY modified_at DESC NULLS LAST LIMIT 5"
    ).fetchall()

    for row in rows:
        try:
            data = await client.request_with_retry(
                "item.list.query",
                {"outerIds": row["outer_id"], "pageNo": 1, "pageSize": 1}
            )
            items = data.get("items") or []
            if items:
                api_item = items[0]
                title_match = api_item.get("title", "")[:20] == (row["title"] or "")[:20]
                log_result("erp_products", f"商品 {row['outer_id']}", title_match,
                          f"DB='{(row['title'] or '')[:20]}' API='{api_item.get('title', '')[:20]}'")
            else:
                log_result("erp_products", f"商品 {row['outer_id']}", False, "API 未返回")
        except Exception as e:
            log_result("erp_products", f"商品 {row['outer_id']}", False, f"API错误: {e}")

    conn.close()


# ============================================================
# 2. 库存表校验
# ============================================================

async def verify_stock(client: KuaiMaiClient):
    print("\n" + "=" * 60)
    print("📊 库存表 (erp_stock_status)")
    print("=" * 60)

    conn = get_db()
    db_count = conn.execute("SELECT count(*) as c FROM erp_stock_status").fetchone()["c"]
    log_result("erp_stock_status", "库存记录总数", True, f"{db_count} 条")

    # 抽样5个SKU，对比API库存
    rows = conn.execute(
        "SELECT outer_id, sku_outer_id, sellable_num, total_stock FROM erp_stock_status WHERE sku_outer_id != '' ORDER BY synced_at DESC LIMIT 5"
    ).fetchall()

    for row in rows:
        try:
            data = await client.request_with_retry(
                "stock.api.status.query",
                {"outerIds": row["sku_outer_id"], "pageNo": 1, "pageSize": 1}
            )
            items = data.get("stockStatusVoList") or []
            if items:
                api_stock = items[0]
                api_sellable = float(api_stock.get("sellableNum", 0))
                db_sellable = float(row["sellable_num"])
                match = abs(api_sellable - db_sellable) < 1  # 允许1的误差（同步延迟）
                log_result("erp_stock_status", f"SKU {row['sku_outer_id']}", match,
                          f"可售: DB={db_sellable} API={api_sellable}")
            else:
                log_result("erp_stock_status", f"SKU {row['sku_outer_id']}", False, "API 未返回")
        except Exception as e:
            log_result("erp_stock_status", f"SKU {row['sku_outer_id']}", False, f"API错误: {e}")

    conn.close()


# ============================================================
# 3. 订单表校验
# ============================================================

async def verify_orders(client: KuaiMaiClient):
    print("\n" + "=" * 60)
    print("🛒 订单表 (erp_document_items: order)")
    print("=" * 60)

    conn = get_db()
    db_count = conn.execute(
        "SELECT count(*) as c FROM erp_document_items WHERE doc_type = 'order'"
    ).fetchone()["c"]
    log_result("orders", "订单明细总数", True, f"{db_count} 条")

    # 日期分布检查
    date_dist = conn.execute("""
        SELECT doc_created_at::date as dt, count(*) as c
        FROM erp_document_items WHERE doc_type = 'order'
        GROUP BY dt ORDER BY dt DESC LIMIT 7
    """).fetchall()
    for d in date_dist:
        log_result("orders", f"日期 {d['dt']}", True, f"{d['c']} 条")

    # API 抽样：最近3天的订单数对比
    now = datetime.now()
    start = (now - timedelta(days=3)).strftime("%Y-%m-%d")
    end = now.strftime("%Y-%m-%d %H:%M:%S")
    try:
        data = await client.request_with_retry(
            "trade.order.list.query",
            {"startModified": start, "endModified": end, "pageNo": 1, "pageSize": 1}
        )
        api_total = data.get("total", 0)
        db_recent = conn.execute(
            "SELECT count(DISTINCT doc_id) as c FROM erp_document_items WHERE doc_type = 'order' AND doc_modified_at >= %s",
            (start,)
        ).fetchone()["c"]
        diff_pct = abs(api_total - db_recent) / max(api_total, 1) * 100
        match = diff_pct < 10  # 10%以内算正常（有同步延迟）
        log_result("orders", f"近3天订单数", match,
                  f"DB={db_recent} API={api_total} 差异={diff_pct:.1f}%")
    except Exception as e:
        log_result("orders", "近3天订单数", False, f"API错误: {e}")

    conn.close()


# ============================================================
# 4. 售后表校验
# ============================================================

async def verify_aftersales(client: KuaiMaiClient):
    print("\n" + "=" * 60)
    print("🔄 售后表 (erp_document_items: aftersale)")
    print("=" * 60)

    conn = get_db()
    db_count = conn.execute(
        "SELECT count(*) as c FROM erp_document_items WHERE doc_type = 'aftersale'"
    ).fetchone()["c"]
    log_result("aftersale", "售后明细总数", True, f"{db_count} 条")

    # 按类型分布
    type_dist = conn.execute("""
        SELECT aftersale_type, count(*) as c
        FROM erp_document_items WHERE doc_type = 'aftersale'
        GROUP BY aftersale_type ORDER BY c DESC
    """).fetchall()
    type_names = {0: "其他", 1: "仅退款", 2: "退货", 3: "补发", 4: "换货", 5: "退款", 7: "拒收", 9: "维修"}
    for t in type_dist:
        name = type_names.get(t["aftersale_type"], f"type={t['aftersale_type']}")
        log_result("aftersale", f"类型: {name}", True, f"{t['c']} 条")

    conn.close()


# ============================================================
# 5. 采购表校验
# ============================================================

async def verify_purchases(client: KuaiMaiClient):
    print("\n" + "=" * 60)
    print("📋 采购表 (erp_document_items: purchase/receipt/shelf)")
    print("=" * 60)

    conn = get_db()
    for doc_type in ["purchase", "receipt", "shelf", "purchase_return"]:
        count = conn.execute(
            "SELECT count(*) as c FROM erp_document_items WHERE doc_type = %s", (doc_type,)
        ).fetchone()["c"]
        archive_count = conn.execute(
            "SELECT count(*) as c FROM erp_document_items_archive WHERE doc_type = %s", (doc_type,)
        ).fetchone()["c"]
        log_result(doc_type, "热表+冷表", True, f"热表={count} 归档={archive_count} 合计={count + archive_count}")

    conn.close()


# ============================================================
# 6. 供应商校验
# ============================================================

async def verify_suppliers(client: KuaiMaiClient):
    print("\n" + "=" * 60)
    print("🏭 供应商表 (erp_suppliers)")
    print("=" * 60)

    conn = get_db()
    db_count = conn.execute("SELECT count(*) as c FROM erp_suppliers").fetchone()["c"]
    log_result("erp_suppliers", "供应商总数", True, f"{db_count} 条")

    # API 对比
    try:
        data = await client.request_with_retry(
            "supplier.list.query", {"pageNo": 1, "pageSize": 1}
        )
        api_total = data.get("total", 0)
        match = abs(api_total - db_count) <= 2
        log_result("erp_suppliers", "API对比", match, f"DB={db_count} API={api_total}")
    except Exception as e:
        log_result("erp_suppliers", "API对比", False, f"API错误: {e}")

    conn.close()


# ============================================================
# 7. 增量同步验证
# ============================================================

async def verify_incremental():
    print("\n" + "=" * 60)
    print("⏱️  增量同步状态")
    print("=" * 60)

    conn = get_db()
    states = conn.execute(
        "SELECT sync_type, status, is_initial_done, total_synced, last_sync_time, error_count, last_error FROM erp_sync_state ORDER BY sync_type"
    ).fetchall()

    now = datetime.now()
    for s in states:
        last_sync = s["last_sync_time"]
        if last_sync:
            age_min = (now - last_sync).total_seconds() / 60
            fresh = age_min < 5  # 5分钟内算新鲜
            log_result(s["sync_type"], "增量同步", fresh and s["error_count"] == 0,
                      f"状态={s['status']} 初始完成={s['is_initial_done']} 累计={s['total_synced']} "
                      f"最后同步={age_min:.0f}分钟前 错误={s['error_count']}")
        else:
            log_result(s["sync_type"], "增量同步", False, "从未同步")

    conn.close()


# ============================================================
# 8. 聚合统计校验
# ============================================================

async def verify_daily_stats():
    print("\n" + "=" * 60)
    print("📈 每日统计表 (erp_product_daily_stats)")
    print("=" * 60)

    conn = get_db()
    total = conn.execute("SELECT count(*) as c FROM erp_product_daily_stats").fetchone()["c"]
    log_result("daily_stats", "统计记录总数", True, f"{total} 条")

    # 检查最近7天是否有数据
    recent = conn.execute("""
        SELECT stat_date, count(*) as products,
               sum(order_count) as orders, sum(order_amount) as amount
        FROM erp_product_daily_stats
        WHERE stat_date >= CURRENT_DATE - 7
        GROUP BY stat_date ORDER BY stat_date DESC
    """).fetchall()
    for r in recent:
        log_result("daily_stats", f"{r['stat_date']}", True,
                  f"商品数={r['products']} 订单={r['orders']} 金额=¥{r['amount'] or 0:,.0f}")

    conn.close()


# ============================================================
# 主流程
# ============================================================

async def main():
    print("=" * 60)
    print("🔍 ERP 数据全面校验")
    print(f"   时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)

    client = await get_client()

    try:
        await verify_products(client)
        await verify_stock(client)
        await verify_orders(client)
        await verify_aftersales(client)
        await verify_purchases(client)
        await verify_suppliers(client)
        await verify_incremental()
        await verify_daily_stats()
    finally:
        await client.close()

    print("\n" + "=" * 60)
    print("校验完成")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
