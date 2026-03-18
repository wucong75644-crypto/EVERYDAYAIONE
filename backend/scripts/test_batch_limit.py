"""
测试批量查询API的编码数量上限
逐步增加编码数量，找到API报错的临界值
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

    # ═══════════════════════════════════════════════════
    # 第1步：收集真实编码
    # ═══════════════════════════════════════════════════
    print("=" * 65)
    print("【准备】收集真实商品编码")
    print("=" * 65)

    # 从库存接口批量拿编码
    all_outer_ids = []
    all_sku_outer_ids = []
    for page in range(1, 6):
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

    print(f"  收集到 {len(all_outer_ids)} 个主编码, {len(all_sku_outer_ids)} 个SKU编码")

    if len(all_outer_ids) < 30:
        print("  编码数量不足30，无法测试上限")
        await client.close()
        return

    # ═══════════════════════════════════════════════════
    # 第2步：测试 multi_product (erp.item.list.get) outerIds 上限
    # ═══════════════════════════════════════════════════
    print("\n" + "=" * 65)
    print("【1】multi_product (erp.item.list.get) outerIds 上限")
    print("=" * 65)

    for count in [5, 10, 15, 20, 25, 30, 40, 50]:
        if count > len(all_outer_ids):
            break
        codes = ",".join(all_outer_ids[:count])
        resp = await safe_query(client, "erp.item.list.get", {
            "outerIds": codes, "returnSkus": 1,
        })
        if "_error" in resp:
            print(f"  {count:3d}个编码 → ERROR: {resp['_error'][:80]}")
        else:
            items = resp.get("items") or []
            print(f"  {count:3d}个编码 → {len(items)}条结果")

    # ═══════════════════════════════════════════════════
    # 第3步：测试 item_supplier_list (erp.item.supplier.list.get)
    #        outerIds 和 skuOuterIds 上限
    # ═══════════════════════════════════════════════════
    print("\n" + "=" * 65)
    print("【2】item_supplier_list outerIds 上限")
    print("=" * 65)

    for count in [5, 10, 15, 20, 25, 30, 40, 50]:
        if count > len(all_outer_ids):
            break
        codes = ",".join(all_outer_ids[:count])
        resp = await safe_query(client, "erp.item.supplier.list.get", {
            "outerIds": codes,
        })
        if "_error" in resp:
            print(f"  outerIds {count:3d}个 → ERROR: {resp['_error'][:80]}")
        else:
            items = resp.get("suppliers") or []
            print(f"  outerIds {count:3d}个 → {len(items)}条结果")

    print("\n" + "-" * 65)
    print("【2b】item_supplier_list skuOuterIds 上限")
    print("-" * 65)

    for count in [5, 10, 15, 20, 25, 30, 40, 50]:
        if count > len(all_sku_outer_ids):
            break
        codes = ",".join(all_sku_outer_ids[:count])
        resp = await safe_query(client, "erp.item.supplier.list.get", {
            "skuOuterIds": codes,
        })
        if "_error" in resp:
            print(f"  skuOuterIds {count:3d}个 → ERROR: {resp['_error'][:80]}")
        else:
            items = resp.get("suppliers") or []
            print(f"  skuOuterIds {count:3d}个 → {len(items)}条结果")

    # ═══════════════════════════════════════════════════
    # 第4步：测试 stock_status (stock.api.status.query)
    #        mainOuterId 单值 vs 多值（确认是否支持批量）
    # ═══════════════════════════════════════════════════
    print("\n" + "=" * 65)
    print("【3】stock_status mainOuterId 是否支持逗号分隔多值")
    print("=" * 65)

    # 单个编码基线
    single = all_outer_ids[0]
    resp = await safe_query(client, "stock.api.status.query", {
        "mainOuterId": single, "pageNo": 1, "pageSize": 50,
    })
    if "_error" not in resp:
        items = resp.get("stockStatusVoList") or []
        print(f"  单个 mainOuterId={single} → {len(items)}条")

    # 逗号分隔多个
    for count in [2, 3, 5]:
        if count > len(all_outer_ids):
            break
        codes = ",".join(all_outer_ids[:count])
        resp = await safe_query(client, "stock.api.status.query", {
            "mainOuterId": codes, "pageNo": 1, "pageSize": 50,
        })
        if "_error" in resp:
            print(f"  {count}个逗号分隔 → ERROR: {resp['_error'][:80]}")
        else:
            items = resp.get("stockStatusVoList") or []
            print(f"  {count}个逗号分隔 → {len(items)}条")

    # skuOuterId 多值
    print("\n" + "-" * 65)
    print("【3b】stock_status skuOuterId 逗号分隔多值")
    print("-" * 65)

    single_sku = all_sku_outer_ids[0]
    resp = await safe_query(client, "stock.api.status.query", {
        "skuOuterId": single_sku, "pageNo": 1, "pageSize": 50,
    })
    if "_error" not in resp:
        items = resp.get("stockStatusVoList") or []
        print(f"  单个 skuOuterId={single_sku} → {len(items)}条")

    for count in [2, 3, 5]:
        if count > len(all_sku_outer_ids):
            break
        codes = ",".join(all_sku_outer_ids[:count])
        resp = await safe_query(client, "stock.api.status.query", {
            "skuOuterId": codes, "pageNo": 1, "pageSize": 50,
        })
        if "_error" in resp:
            print(f"  {count}个逗号分隔 → ERROR: {resp['_error'][:80]}")
        else:
            items = resp.get("stockStatusVoList") or []
            print(f"  {count}个逗号分隔 → {len(items)}条")

    # ═══════════════════════════════════════════════════
    # 第5步：测试 outer_id_list (erp.item.outerid.list.get) outerIds 上限
    # ═══════════════════════════════════════════════════
    print("\n" + "=" * 65)
    print("【4】outer_id_list (erp.item.outerid.list.get) outerIds 上限")
    print("=" * 65)

    for count in [5, 10, 15, 20, 25, 30, 40, 50]:
        if count > len(all_outer_ids):
            break
        codes = ",".join(all_outer_ids[:count])
        resp = await safe_query(client, "erp.item.outerid.list.get", {
            "outerIds": codes, "pageNo": 1, "pageSize": 100,
        })
        if "_error" in resp:
            print(f"  {count:3d}个编码 → ERROR: {resp['_error'][:80]}")
        else:
            items = resp.get("itemOuterIdInfos") or []
            total = resp.get("total", "?")
            print(f"  {count:3d}个编码 → {len(items)}条 (total={total})")

    # ═══════════════════════════════════════════════════
    # 第6步：测试 warehouse_stock (erp.item.warehouse.list.get)
    #        outerId 是否支持逗号分隔
    # ═══════════════════════════════════════════════════
    print("\n" + "=" * 65)
    print("【5】warehouse_stock outerId 是否支持逗号分隔")
    print("=" * 65)

    single = all_outer_ids[1]
    resp = await safe_query(client, "erp.item.warehouse.list.get", {
        "outerId": single,
    })
    if "_error" in resp:
        print(f"  单个 outerId={single} → ERROR: {resp['_error'][:80]}")
    else:
        items = resp.get("list") or resp.get("data") or []
        print(f"  单个 outerId={single} → {len(items)}条")

    codes = ",".join(all_outer_ids[1:3])
    resp = await safe_query(client, "erp.item.warehouse.list.get", {
        "outerId": codes,
    })
    if "_error" in resp:
        print(f"  2个逗号分隔 → ERROR: {resp['_error'][:80]}")
    else:
        items = resp.get("list") or resp.get("data") or []
        print(f"  2个逗号分隔 → {len(items)}条")

    await client.close()
    print(f"\n{'=' * 65}")
    print("测试完成")
    print(f"{'=' * 65}")


if __name__ == "__main__":
    asyncio.run(main())
