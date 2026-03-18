"""
追测：
1. stock_status mainOuterId/skuOuterId 逗号分隔的上限
2. warehouse_stock 加pageNo后重测
3. outer_id_list 用少量编码排查是数据问题还是参数问题
"""

import asyncio
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.kuaimai.client import KuaiMaiClient


async def safe_query(client, method, biz_params):
    try:
        return await client.request_with_retry(method=method, biz_params=biz_params)
    except Exception as e:
        return {"_error": str(e)}


async def main():
    client = KuaiMaiClient()

    # 收集编码
    all_outer_ids = []
    all_sku_outer_ids = []
    for page in range(1, 11):
        resp = await safe_query(client, "stock.api.status.query", {
            "pageNo": page, "pageSize": 100,
        })
        items = resp.get("stockStatusVoList") or []
        if not items:
            break
        for item in items:
            oid = item.get("mainOuterId", "")
            sid = item.get("skuOuterId", "")
            if oid and oid not in all_outer_ids:
                all_outer_ids.append(oid)
            if sid and sid not in all_sku_outer_ids:
                all_sku_outer_ids.append(sid)
    print(f"收集到 {len(all_outer_ids)} 个主编码, {len(all_sku_outer_ids)} 个SKU编码\n")

    # ═══════════════════════════════════════════════════
    # 1. stock_status mainOuterId 逗号分隔上限
    # ═══════════════════════════════════════════════════
    print("=" * 65)
    print("【1】stock_status mainOuterId 逗号分隔上限")
    print("=" * 65)

    for count in [10, 20, 25, 30, 40, 50, 80, 100]:
        if count > len(all_outer_ids):
            break
        codes = ",".join(all_outer_ids[:count])
        resp = await safe_query(client, "stock.api.status.query", {
            "mainOuterId": codes, "pageNo": 1, "pageSize": 100,
        })
        if "_error" in resp:
            print(f"  mainOuterId {count:3d}个 → ERROR: {resp['_error'][:80]}")
        else:
            items = resp.get("stockStatusVoList") or []
            total = resp.get("total", "?")
            print(f"  mainOuterId {count:3d}个 → {len(items)}条 (total={total})")

    # ═══════════════════════════════════════════════════
    # 2. stock_status skuOuterId 逗号分隔上限
    # ═══════════════════════════════════════════════════
    print("\n" + "=" * 65)
    print("【2】stock_status skuOuterId 逗号分隔上限")
    print("=" * 65)

    for count in [10, 20, 25, 30, 40, 50, 80, 100]:
        if count > len(all_sku_outer_ids):
            break
        codes = ",".join(all_sku_outer_ids[:count])
        resp = await safe_query(client, "stock.api.status.query", {
            "skuOuterId": codes, "pageNo": 1, "pageSize": 100,
        })
        if "_error" in resp:
            print(f"  skuOuterId {count:3d}个 → ERROR: {resp['_error'][:80]}")
        else:
            items = resp.get("stockStatusVoList") or []
            total = resp.get("total", "?")
            print(f"  skuOuterId {count:3d}个 → {len(items)}条 (total={total})")

    # ═══════════════════════════════════════════════════
    # 3. warehouse_stock 加 pageNo 重测
    # ═══════════════════════════════════════════════════
    print("\n" + "=" * 65)
    print("【3】warehouse_stock outerId 单值 vs 逗号分隔")
    print("=" * 65)

    single = all_outer_ids[1]
    resp = await safe_query(client, "erp.item.warehouse.list.get", {
        "outerId": single, "pageNo": 1, "pageSize": 50,
    })
    if "_error" in resp:
        print(f"  单个 outerId={single} → ERROR: {resp['_error'][:80]}")
    else:
        items = resp.get("list") or resp.get("data") or []
        # 看看response里有什么key
        keys = [k for k in resp.keys() if k not in ("code", "message", "sign")]
        print(f"  单个 outerId={single} → 响应keys={keys}, 数据={len(items)}条")

    codes = ",".join(all_outer_ids[1:4])
    resp = await safe_query(client, "erp.item.warehouse.list.get", {
        "outerId": codes, "pageNo": 1, "pageSize": 50,
    })
    if "_error" in resp:
        print(f"  3个逗号分隔 → ERROR: {resp['_error'][:80]}")
    else:
        items = resp.get("list") or resp.get("data") or []
        keys = [k for k in resp.keys() if k not in ("code", "message", "sign")]
        print(f"  3个逗号分隔 → 响应keys={keys}, 数据={len(items)}条")

    # skuOuterId
    single_sku = all_sku_outer_ids[1]
    resp = await safe_query(client, "erp.item.warehouse.list.get", {
        "skuOuterId": single_sku, "pageNo": 1, "pageSize": 50,
    })
    if "_error" in resp:
        print(f"  单个 skuOuterId={single_sku} → ERROR: {resp['_error'][:80]}")
    else:
        items = resp.get("list") or resp.get("data") or []
        print(f"  单个 skuOuterId={single_sku} → {len(items)}条")

    codes = ",".join(all_sku_outer_ids[1:4])
    resp = await safe_query(client, "erp.item.warehouse.list.get", {
        "skuOuterId": codes, "pageNo": 1, "pageSize": 50,
    })
    if "_error" in resp:
        print(f"  3个sku逗号分隔 → ERROR: {resp['_error'][:80]}")
    else:
        items = resp.get("list") or resp.get("data") or []
        print(f"  3个sku逗号分隔 → {len(items)}条")

    # ═══════════════════════════════════════════════════
    # 4. outer_id_list 排查：用单个已知编码测试
    # ═══════════════════════════════════════════════════
    print("\n" + "=" * 65)
    print("【4】outer_id_list 单个编码测试")
    print("=" * 65)

    for oid in all_outer_ids[:3]:
        resp = await safe_query(client, "erp.item.outerid.list.get", {
            "outerIds": oid, "pageNo": 1, "pageSize": 50,
        })
        if "_error" in resp:
            print(f"  outerIds={oid} → ERROR: {resp['_error'][:60]}")
        else:
            items = resp.get("itemOuterIdInfos") or []
            print(f"  outerIds={oid} → {len(items)}条")

    # ═══════════════════════════════════════════════════
    # 5. stock_in_out outerId 逗号分隔测试
    # ═══════════════════════════════════════════════════
    print("\n" + "=" * 65)
    print("【5】stock_in_out outerId 逗号分隔测试")
    print("=" * 65)

    single = all_outer_ids[0]
    resp = await safe_query(client, "erp.item.stock.in.out.list", {
        "outerId": single, "pageNo": 1, "pageSize": 20,
    })
    if "_error" in resp:
        print(f"  单个 outerId={single} → ERROR: {resp['_error'][:80]}")
    else:
        items = resp.get("list") or resp.get("data") or []
        total = resp.get("total", "?")
        print(f"  单个 outerId={single} → {len(items)}条 (total={total})")

    codes = ",".join(all_outer_ids[:3])
    resp = await safe_query(client, "erp.item.stock.in.out.list", {
        "outerId": codes, "pageNo": 1, "pageSize": 20,
    })
    if "_error" in resp:
        print(f"  3个逗号分隔 → ERROR: {resp['_error'][:80]}")
    else:
        items = resp.get("list") or resp.get("data") or []
        total = resp.get("total", "?")
        print(f"  3个逗号分隔 → {len(items)}条 (total={total})")

    await client.close()
    print(f"\n{'=' * 65}")
    print("追测完成")
    print(f"{'=' * 65}")


if __name__ == "__main__":
    asyncio.run(main())
