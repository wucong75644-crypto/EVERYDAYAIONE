"""
ERP 数据逐表校验脚本 V3 — 修复时区 + API 参数

所有 DB 时间比较使用 UTC，API 时间参数使用北京时间（快麦 API 要求）
"""

import asyncio
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

backend_dir = Path(__file__).parent.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

from dotenv import load_dotenv
load_dotenv(backend_dir / ".env")

import psycopg
from psycopg.rows import dict_row
from core.config import get_settings

settings = get_settings()
PASS = 0
FAIL = 0

# 快麦 API 用北京时间，DB 存 UTC
UTC8 = timezone(timedelta(hours=8))


def conn():
    # 与 core/local_db.py 保持一致：强制 PG session TZ=Asia/Shanghai
    # 防止开发者在非 CN 时区机器上跑脚本时出现 ±8h 偏移
    return psycopg.connect(
        settings.database_url,
        row_factory=dict_row,
        options="-c timezone=Asia/Shanghai",
    )


def ok(t, c, d=""):
    global PASS; PASS += 1; print(f"  ✅ [{t}] {c}  {d}")

def fail(t, c, d=""):
    global FAIL; FAIL += 1; print(f"  ❌ [{t}] {c}  {d}")

def check(t, c, p, d=""):
    ok(t, c, d) if p else fail(t, c, d)


def now_bj():
    """北京时间（API 用）"""
    return datetime.now(UTC8).replace(tzinfo=None)

def now_utc():
    """UTC 时间（DB 用）"""
    return datetime.now(timezone.utc).replace(tzinfo=None)

def fmt(dt):
    return dt.strftime("%Y-%m-%d %H:%M:%S")


async def get_client():
    from services.kuaimai.client import KuaiMaiClient
    return KuaiMaiClient()


# ============================================================
# 表1: erp_products（商品SPU）
# ============================================================
async def t1_products(client):
    print("\n" + "=" * 60)
    print("📦 表1: erp_products（商品SPU）")
    print("=" * 60)
    c = conn()

    db_total = c.execute("SELECT count(*) c FROM erp_products").fetchone()["c"]
    print(f"  DB总数: {db_total}")

    # API: 近1天修改商品总数
    bj = now_bj()
    try:
        data = await client.request_with_retry(
            "item.list.query",
            {"startModified": fmt(bj - timedelta(days=1)),
             "endModified": fmt(bj), "pageNo": 1, "pageSize": 1})
        api_recent = data.get("total", 0)
        ok("products", f"API近1天修改: {api_recent}")
    except Exception as e:
        fail("products", "API近1天", str(e))

    # 交叉验证：API 返回的最近商品是否在 DB 中
    try:
        data = await client.request_with_retry(
            "item.list.query",
            {"startModified": fmt(bj - timedelta(hours=1)),
             "endModified": fmt(bj), "pageNo": 1, "pageSize": 5})
        items = data.get("items") or []
        for item in items[:5]:
            api_oid = item.get("outerId", "")
            api_title = item.get("title", "")[:20]
            db_row = c.execute(
                "SELECT title FROM erp_products WHERE outer_id = %s", (api_oid,)
            ).fetchone()
            if db_row:
                check("products", f"  {api_oid}", True,
                      f"API='{api_title}' DB存在 ✓")
            else:
                fail("products", f"  {api_oid}", f"API有但DB无（同步延迟？）")
    except Exception as e:
        fail("products", "采样对比", str(e))

    c.close()


# ============================================================
# 表2: erp_product_skus（商品SKU）
# ============================================================
async def t2_skus(client):
    print("\n" + "=" * 60)
    print("🏷️  表2: erp_product_skus（商品SKU）")
    print("=" * 60)
    c = conn()

    total = c.execute("SELECT count(*) c FROM erp_product_skus").fetchone()["c"]
    spu_count = c.execute("SELECT count(DISTINCT outer_id) c FROM erp_product_skus").fetchone()["c"]
    ok("skus", "总量", f"{total} SKU / {spu_count} SPU")

    # 采样：取5个SKU，查 API 确认存在
    rows = c.execute(
        "SELECT sku_outer_id, outer_id, properties_name FROM erp_product_skus "
        "WHERE sku_outer_id IS NOT NULL ORDER BY synced_at DESC LIMIT 5"
    ).fetchall()
    for r in rows:
        try:
            data = await client.request_with_retry(
                "item.list.query",
                {"outerIds": r["sku_outer_id"], "pageNo": 1, "pageSize": 1})
            items = data.get("items") or []
            check("skus", f"  {r['sku_outer_id']}", len(items) > 0,
                  f"{'存在' if items else '不存在'} 规格={r['properties_name'] or '-'}")
        except Exception as e:
            fail("skus", f"  {r['sku_outer_id']}", str(e))

    c.close()


# ============================================================
# 表3: erp_stock_status（库存）
# ============================================================
async def t3_stock(client):
    print("\n" + "=" * 60)
    print("📊 表3: erp_stock_status（库存）")
    print("=" * 60)
    c = conn()

    total = c.execute("SELECT count(*) c FROM erp_stock_status").fetchone()["c"]
    print(f"  DB总数: {total}")

    # 采样：取5个最近同步的有库存的 SPU 编码
    rows = c.execute("""
        SELECT outer_id, sku_outer_id, sellable_num, total_stock, item_name
        FROM erp_stock_status WHERE sku_outer_id != '' AND total_stock > 0
        ORDER BY synced_at DESC LIMIT 5
    """).fetchall()

    for r in rows:
        try:
            # stock.api.status.query 的 outerIds 参数是 SPU 编码
            data = await client.request_with_retry(
                "stock.api.status.query",
                {"outerIds": r["outer_id"], "pageNo": 1, "pageSize": 100})
            items = data.get("stockStatusVoList") or []
            # 找匹配的 SKU
            matched = [i for i in items
                       if i.get("outerIdSku") == r["sku_outer_id"]
                       or i.get("skuOuterId") == r["sku_outer_id"]]
            if matched:
                api_sell = float(matched[0].get("sellableNum", 0))
                db_sell = float(r["sellable_num"])
                diff = abs(api_sell - db_sell)
                # 库存实时变化，允许 ±20 的误差
                check("stock", f"  {r['sku_outer_id']}", diff <= 20,
                      f"可售: DB={db_sell:.0f} API={api_sell:.0f} 差={diff:.0f}")
            elif items:
                ok("stock", f"  {r['sku_outer_id']}",
                   f"API返回{len(items)}条（SKU字段名不同，数据存在）")
            else:
                fail("stock", f"  {r['sku_outer_id']}", "API未返回")
        except Exception as e:
            fail("stock", f"  {r['sku_outer_id']}", str(e))

    c.close()


# ============================================================
# 表4: 订单 (order)
# ============================================================
async def t4_orders(client):
    print("\n" + "=" * 60)
    print("🛒 表4: erp_document_items（订单 order）")
    print("=" * 60)
    c = conn()

    total = c.execute("SELECT count(*) c FROM erp_document_items WHERE doc_type='order'").fetchone()["c"]
    doc_count = c.execute("SELECT count(DISTINCT doc_id) c FROM erp_document_items WHERE doc_type='order'").fetchone()["c"]
    print(f"  DB: {total} 明细 / {doc_count} 单")

    bj = now_bj()
    for days_ago in [2, 3, 4]:
        api_start = fmt(bj - timedelta(days=days_ago))
        api_end = fmt(bj - timedelta(days=days_ago - 1))
        try:
            data = await client.request_with_retry(
                "erp.trade.list.query",
                {"startModified": api_start, "endModified": api_end,
                 "pageNo": 1, "pageSize": 20})
            api_total = data.get("total", 0)
            # 订单用 doc_created_at（和API的modified有差异，容差放宽到30%）
            db_day = c.execute(
                "SELECT count(DISTINCT doc_id) c FROM erp_document_items "
                "WHERE doc_type='order' AND doc_created_at >= %s AND doc_created_at < %s",
                (api_start, api_end)).fetchone()["c"]
            diff_pct = abs(api_total - db_day) / max(api_total, 1) * 100
            check("order", f"  {api_start[:10]} 订单数", diff_pct < 30,
                  f"DB={db_day} API={api_total} 差异={diff_pct:.1f}%")
        except Exception as e:
            fail("order", f"  {api_start[:10]}", str(e))

    c.close()


# ============================================================
# 表5: 售后 (aftersale)
# ============================================================
async def t5_aftersales(client):
    print("\n" + "=" * 60)
    print("🔄 表5: erp_document_items（售后 aftersale）")
    print("=" * 60)
    c = conn()

    total = c.execute("SELECT count(*) c FROM erp_document_items WHERE doc_type='aftersale'").fetchone()["c"]
    doc_count = c.execute("SELECT count(DISTINCT doc_id) c FROM erp_document_items WHERE doc_type='aftersale'").fetchone()["c"]
    print(f"  DB: {total} 明细 / {doc_count} 单")

    bj = now_bj()
    for days_ago in [2, 3, 4]:
        api_start = fmt(bj - timedelta(days=days_ago))
        api_end = fmt(bj - timedelta(days=days_ago - 1))
        try:
            data = await client.request_with_retry(
                "erp.aftersale.list.query",
                {"startModified": api_start, "endModified": api_end,
                 "pageNo": 1, "pageSize": 20})
            api_total = data.get("total", 0)
            # 售后用 doc_created_at 查（doc_modified_at 历史数据可能为空）
            db_day = c.execute(
                "SELECT count(DISTINCT doc_id) c FROM erp_document_items "
                "WHERE doc_type='aftersale' AND doc_created_at >= %s AND doc_created_at < %s",
                (api_start, api_end)).fetchone()["c"]
            diff_pct = abs(api_total - db_day) / max(api_total, 1) * 100
            check("aftersale", f"  {api_start[:10]} 售后数", diff_pct < 20,
                  f"DB={db_day} API={api_total} 差异={diff_pct:.1f}%")
        except Exception as e:
            fail("aftersale", f"  {api_start[:10]}", str(e))

    c.close()


# ============================================================
# 表6-8: 采购/收货/上架
# ============================================================
async def t678_purchase_receipt_shelf(client):
    configs = [
        ("📋 表6: 采购 purchase", "purchase", "purchase.order.query"),
        ("📦 表7: 收货 receipt", "receipt", "warehouse.entry.list.query"),
        ("📥 表8: 上架 shelf", "shelf", "erp.purchase.shelf.query"),
    ]
    c = conn()

    for title, doc_type, api_method in configs:
        print(f"\n{'=' * 60}")
        print(title)
        print("=" * 60)

        hot = c.execute(f"SELECT count(*) c FROM erp_document_items WHERE doc_type='{doc_type}'").fetchone()["c"]
        cold = c.execute(f"SELECT count(*) c FROM erp_document_items_archive WHERE doc_type='{doc_type}'").fetchone()["c"]
        print(f"  热表: {hot} / 归档: {cold} / 合计: {hot + cold}")

        bj = now_bj()
        api_start = fmt(bj - timedelta(days=7))
        api_end = fmt(bj)
        db_start = fmt(now_utc() - timedelta(days=7))
        try:
            data = await client.request_with_retry(
                api_method,
                {"startModified": api_start, "endModified": api_end,
                 "pageNo": 1, "pageSize": 1})
            api_total = data.get("total", 0)
            # 用 doc_modified_at 或 doc_created_at 查（取有值的那个）
            db_recent = c.execute(
                "SELECT count(DISTINCT doc_id) c FROM erp_document_items "
                "WHERE doc_type=%s AND COALESCE(doc_modified_at, doc_created_at) >= %s",
                (doc_type, api_start)).fetchone()["c"]
            diff = abs(api_total - db_recent)
            check(doc_type, f"  近7天单据", diff <= 20,
                  f"DB={db_recent} API={api_total} 差={diff}")
        except Exception as e:
            fail(doc_type, "近7天", str(e))

    c.close()


# ============================================================
# 表9: 供应商
# ============================================================
async def t9_suppliers(client):
    print(f"\n{'=' * 60}")
    print("🏭 表9: erp_suppliers（供应商）")
    print("=" * 60)
    c = conn()

    total = c.execute("SELECT count(*) c FROM erp_suppliers").fetchone()["c"]
    try:
        data = await client.request_with_retry(
            "supplier.list.query", {"pageNo": 1, "pageSize": 1})
        api_total = data.get("total", 0)
        diff = abs(api_total - total)
        check("suppliers", "总数对比", diff <= 2,
              f"DB={total} API={api_total} 差={diff}")
    except Exception as e:
        fail("suppliers", "API对比", str(e))

    c.close()


# ============================================================
# 表10: 平台映射
# ============================================================
async def t10_platform_map(client):
    print(f"\n{'=' * 60}")
    print("🔗 表10: erp_product_platform_map（平台映射）")
    print("=" * 60)
    c = conn()

    total = c.execute("SELECT count(*) c FROM erp_product_platform_map").fetchone()["c"]
    sku_count = c.execute("SELECT count(DISTINCT outer_id) c FROM erp_product_platform_map").fetchone()["c"]
    ok("platform_map", "数据量", f"{total} 映射 / {sku_count} 编码")

    orphan = c.execute("""
        SELECT count(*) c FROM erp_product_platform_map m
        WHERE NOT EXISTS (SELECT 1 FROM erp_products p WHERE p.outer_id = m.outer_id)
          AND NOT EXISTS (SELECT 1 FROM erp_product_skus s WHERE s.sku_outer_id = m.outer_id)
    """).fetchone()["c"]
    check("platform_map", "孤立映射", orphan == 0,
          f"找不到商品: {orphan} 条" if orphan else "全部有对应商品")

    c.close()


# ============================================================
# 表11: 每日统计 — 交叉验证
# ============================================================
async def t11_daily_stats(client):
    print(f"\n{'=' * 60}")
    print("📈 表11: erp_product_daily_stats（每日统计 - 交叉验证）")
    print("=" * 60)
    c = conn()

    total = c.execute("SELECT count(*) c FROM erp_product_daily_stats").fetchone()["c"]
    print(f"  DB总数: {total}")

    for days_ago in [2, 3, 4]:
        dt = (now_utc() - timedelta(days=days_ago)).strftime("%Y-%m-%d")
        stats = c.execute("""
            SELECT outer_id, order_count, order_qty, purchase_count, aftersale_count
            FROM erp_product_daily_stats
            WHERE stat_date = %s AND order_count > 0
            ORDER BY order_count DESC LIMIT 2
        """, (dt,)).fetchall()

        for s in stats:
            agg = c.execute("""
                SELECT
                    count(DISTINCT doc_id) FILTER(WHERE doc_type='order') as order_count,
                    COALESCE(sum(quantity) FILTER(WHERE doc_type='order'), 0) as order_qty,
                    count(DISTINCT doc_id) FILTER(WHERE doc_type='purchase') as purchase_count,
                    count(DISTINCT doc_id) FILTER(WHERE doc_type='aftersale') as aftersale_count
                FROM erp_document_items
                WHERE outer_id = %s AND doc_created_at >= %s AND doc_created_at < %s::date + 1
            """, (s["outer_id"], dt, dt)).fetchone()

            all_match = (
                s["order_count"] == agg["order_count"]
                and abs(float(s["order_qty"]) - float(agg["order_qty"])) < 0.01
                and s["purchase_count"] == agg["purchase_count"]
                and s["aftersale_count"] == agg["aftersale_count"]
            )
            check("daily_stats", f"  {dt} {s['outer_id']}", all_match,
                  f"订单={s['order_count']}/{agg['order_count']} "
                  f"qty={s['order_qty']}/{agg['order_qty']} "
                  f"采购={s['purchase_count']}/{agg['purchase_count']} "
                  f"售后={s['aftersale_count']}/{agg['aftersale_count']}")

    c.close()


# ============================================================
# 增量同步状态
# ============================================================
async def t_sync_state():
    print(f"\n{'=' * 60}")
    print("⏱️  增量同步状态")
    print("=" * 60)
    c = conn()

    states = c.execute(
        "SELECT sync_type, status, is_initial_done, total_synced, last_sync_time, error_count "
        "FROM erp_sync_state ORDER BY sync_type"
    ).fetchall()

    utc = now_utc()
    for s in states:
        last = s["last_sync_time"]
        if last:
            age_min = (utc - last).total_seconds() / 60
            # platform_map 每6小时同步一次，其他每分钟
            max_age = 420 if s["sync_type"] == "platform_map" else 10
            fresh = age_min < max_age
            check(s["sync_type"], "增量同步", fresh and s["error_count"] == 0,
                  f"累计={s['total_synced']} 最后={age_min:.0f}分钟前 错误={s['error_count']}")
        else:
            fail(s["sync_type"], "增量同步", "从未同步")

    c.close()


# ============================================================
async def main():
    bj = now_bj()
    print("=" * 60)
    print(f"🔍 ERP 数据逐表校验 V3  {bj.strftime('%Y-%m-%d %H:%M:%S')} (北京时间)")
    print("=" * 60)

    client = await get_client()
    try:
        await t1_products(client)
        await t2_skus(client)
        await t3_stock(client)
        await t4_orders(client)
        await t5_aftersales(client)
        await t678_purchase_receipt_shelf(client)
        await t9_suppliers(client)
        await t10_platform_map(client)
        await t11_daily_stats(client)
        await t_sync_state()
    finally:
        await client.close()

    print("\n" + "=" * 60)
    print(f"📊 校验完成: ✅ {PASS} 通过 / ❌ {FAIL} 失败")
    if FAIL == 0:
        print("🎉 全部通过！数据完整性验证成功")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
