"""
诊断库存数据：对比本地 erp_stock_status 与 API 实时数据

用法：
    source backend/venv/bin/activate
    python backend/scripts/diagnose_stock.py LBXXTXL02 KCDB01 JYYN01 SGJL01 HDKC01-04

也可查套件：
    python backend/scripts/diagnose_stock.py --suite TJ-LBXXTXL02
"""

import asyncio
import json
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.chdir(os.path.join(os.path.dirname(__file__), ".."))

from dotenv import load_dotenv
load_dotenv()

from core.database import get_db
from services.kuaimai.client import KuaiMaiClient


def query_local_stock(db, code: str) -> list[dict]:
    """查本地 erp_stock_status"""
    result = (
        db.table("erp_stock_status")
        .select("outer_id,sku_outer_id,warehouse_id,sellable_num,total_stock,"
                "lock_stock,purchase_num,stock_status,stock_modified_time")
        .or_(f"outer_id.eq.{code},sku_outer_id.eq.{code}")
        .limit(100)
        .execute()
    )
    return result.data or []


def query_local_product(db, code: str) -> dict | None:
    """查本地 erp_products（获取套件子单品等信息）"""
    result = (
        db.table("erp_products")
        .select("outer_id,title,item_type,suit_singles")
        .eq("outer_id", code)
        .limit(1)
        .execute()
    )
    return result.data[0] if result.data else None


async def query_api_stock(client: KuaiMaiClient, code: str) -> list[dict]:
    """调 API 实时查库存"""
    try:
        data = await client.request_with_retry(
            "stock.api.status.query",
            {"mainOuterId": code, "pageSize": 50, "pageNo": 1},
        )
        items = data.get("stockStatusVoList") or []
        if not items:
            # 尝试用 skuOuterId
            data = await client.request_with_retry(
                "stock.api.status.query",
                {"skuOuterId": code, "pageSize": 50, "pageNo": 1},
            )
            items = data.get("stockStatusVoList") or []
        return items
    except Exception as e:
        print(f"  API 查询失败: {e}")
        return []


STATUS_MAP = {0: "未知", 1: "正常", 2: "警戒", 3: "无货", 4: "超卖", 6: "有货"}


def print_comparison(code: str, local_rows: list[dict], api_rows: list[dict]) -> None:
    """对比输出"""
    print(f"\n{'='*60}")
    print(f"编码: {code}")
    print(f"{'='*60}")

    # 本地数据
    print(f"\n  📦 本地数据 ({len(local_rows)} 条):")
    if not local_rows:
        print("    ❌ 无记录")
    for r in local_rows:
        st = STATUS_MAP.get(r.get("stock_status", 0), "?")
        print(f"    SKU={r.get('sku_outer_id', '-'):20s} | "
              f"可售={r.get('sellable_num', 0):>6} | "
              f"总库存={r.get('total_stock', 0):>6} | "
              f"锁定={r.get('lock_stock', 0):>4} | "
              f"在途={r.get('purchase_num', 0):>4} | "
              f"状态={st} | "
              f"仓库={r.get('warehouse_id', '-')} | "
              f"更新={r.get('stock_modified_time', '-')}")

    # API 数据
    print(f"\n  🌐 API实时数据 ({len(api_rows)} 条):")
    if not api_rows:
        print("    ❌ 无记录")
    for r in api_rows:
        st = STATUS_MAP.get(r.get("stockStatus", 0), "?")
        print(f"    SKU={r.get('skuOuterId', r.get('outerId', '-')):20s} | "
              f"可售={r.get('sellableNum', 0):>6} | "
              f"总库存={r.get('totalAvailableStockSum', 0):>6} | "
              f"锁定={r.get('totalLockStock', 0):>4} | "
              f"在途={r.get('purchaseNum', 0):>4} | "
              f"状态={st} | "
              f"仓库={r.get('wareHouseId', '-')}")

    # 差异汇总
    local_total = sum(r.get("sellable_num", 0) for r in local_rows)
    api_total = sum(r.get("sellableNum", 0) for r in api_rows)
    if local_total != api_total:
        print(f"\n  ⚠️  差异! 本地可售合计={local_total}, API可售合计={api_total}")
    elif local_rows or api_rows:
        print(f"\n  ✅ 一致: 可售合计={local_total}")


async def main() -> None:
    args = sys.argv[1:]
    if not args:
        print("用法: python diagnose_stock.py [--suite SUITE_CODE | CODE1 CODE2 ...]")
        sys.exit(1)

    db = get_db()
    client = KuaiMaiClient()

    if not client.is_configured:
        print("❌ KuaiMai API 未配置")
        sys.exit(1)

    await client.load_cached_token()

    codes: list[str] = []

    if args[0] == "--suite":
        suite_code = args[1]
        print(f"查询套件: {suite_code}")
        product = query_local_product(db, suite_code)
        if not product:
            print(f"❌ 本地未找到商品 {suite_code}")
            await client.close()
            sys.exit(1)

        print(f"商品: {product.get('title', '')} | 类型: {product.get('item_type', 0)}")
        singles = product.get("suit_singles") or []
        if not singles:
            print("⚠️  无套件子单品信息，直接查该编码库存")
            codes = [suite_code]
        else:
            print(f"子单品({len(singles)}个):")
            for s in singles:
                oid = s.get("outerId", "")
                title = s.get("title", "")
                ratio = s.get("ratio", 1)
                print(f"  - {oid} ({title}) x{ratio}")
                codes.append(oid)
        # 也查套件本身
        codes.insert(0, suite_code)
    else:
        codes = args

    # 逐个对比
    for code in codes:
        local = query_local_stock(db, code)
        api = await query_api_stock(client, code)
        print_comparison(code, local, api)

    await client.close()

    # 同步健康检查
    print(f"\n{'='*60}")
    print("同步状态:")
    try:
        result = (
            db.table("erp_sync_state")
            .select("sync_type,last_sync_time,last_error,is_initial_done")
            .eq("sync_type", "stock")
            .limit(1)
            .execute()
        )
        if result.data:
            s = result.data[0]
            print(f"  stock同步 | 最后同步: {s.get('last_sync_time', '-')} | "
                  f"初始完成: {s.get('is_initial_done', False)} | "
                  f"错误: {s.get('last_error', '无')}")
        else:
            print("  ❌ 无stock同步状态记录")
    except Exception as e:
        print(f"  查询失败: {e}")


if __name__ == "__main__":
    asyncio.run(main())
