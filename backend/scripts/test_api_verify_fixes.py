"""
验证修复后的param_docs是否与API实际行为一致
重点测试4个疑点：
1. 店铺name是否模糊/精确
2. 店铺id参数名
3. multicode_query传outer_id是否生效
4. 采购单status正确值验证
"""

import asyncio
import json
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.kuaimai.client import KuaiMaiClient


async def safe_query(client, method, biz_params):
    try:
        return await client.request_with_retry(method=method, biz_params=biz_params)
    except Exception as e:
        return {"_error": str(e)}


async def test_and_print(client, label, method, params, response_key=None):
    resp = await safe_query(client, method, params)
    if "_error" in resp:
        print(f"  {label:50s} → ERROR: {resp['_error'][:80]}")
        return []
    items = []
    for k in ([response_key] if response_key else []) + ["list", "data", "items"]:
        if k and resp.get(k) and isinstance(resp[k], list):
            items = resp[k]
            break
    total = resp.get("total", len(items))
    print(f"  {label:50s} → {len(items)}条 (total={total})")
    return items


async def main():
    client = KuaiMaiClient()

    # ════════════════════════════════════════════════════════
    # 1. 店铺name — 精确还是模糊？
    # ════════════════════════════════════════════════════════
    print("=" * 65)
    print("【1】店铺name参数模糊度测试")
    print("=" * 65)

    resp = await safe_query(client, "erp.shop.list.query", {"pageNo": 1, "pageSize": 500})
    shops = resp.get("list") or []
    print(f"全量: {len(shops)}条")

    # 找一个title较长的店铺
    sample = None
    for s in shops:
        title = s.get("title", "")
        if len(title) >= 4:
            sample = s
            break
    if not sample and shops:
        sample = shops[0]

    if sample:
        title = sample.get("title", "")
        sid = sample.get("shopId", "")
        print(f"\n  测试店铺: title=「{title}」 shopId={sid}")

        half = len(title) // 2
        for label, val in [
            ("完整title", title),
            (f"前{half}字符", title[:half]),
            ("前2字符", title[:2]),
            ("后2字符", title[-2:]),
            ("加多余ZZ", title + "ZZ"),
        ]:
            await test_and_print(client, f"name=「{val}」 ({label})",
                                 "erp.shop.list.query",
                                 {"name": val, "pageNo": 1, "pageSize": 500})

    # ════════════════════════════════════════════════════════
    # 2. 店铺id参数名 — id vs shopId
    # ════════════════════════════════════════════════════════
    print("\n" + "=" * 65)
    print("【2】店铺id参数名测试")
    print("=" * 65)

    if sample:
        sid = str(sample.get("shopId", ""))
        print(f"  测试shopId={sid}")
        await test_and_print(client, f"id={sid} (registry映射)",
                             "erp.shop.list.query",
                             {"id": sid, "pageNo": 1, "pageSize": 500})
        await test_and_print(client, f"shopId={sid} (响应字段名)",
                             "erp.shop.list.query",
                             {"shopId": sid, "pageNo": 1, "pageSize": 500})

    # ════════════════════════════════════════════════════════
    # 3. multicode_query — 传outer_id是否也能查到
    # ════════════════════════════════════════════════════════
    print("\n" + "=" * 65)
    print("【3】multicode_query 编码类型测试")
    print("=" * 65)

    # 先从库存获取一个真实的outer_id
    stock_resp = await safe_query(client, "stock.api.status.query",
                                  {"pageNo": 1, "pageSize": 20})
    stocks = stock_resp.get("stockStatusVoList") or []

    if stocks:
        oid = stocks[0].get("mainOuterId", "")
        skuid = stocks[0].get("skuOuterId", "")
        print(f"  测试编码: mainOuterId=「{oid}」 skuOuterId=「{skuid}」")

        if oid:
            items = await test_and_print(client, f"code=「{oid}」 (主商家编码)",
                                         "erp.item.multicode.query",
                                         {"code": oid, "pageNo": 1, "pageSize": 50},
                                         "list")
            if items:
                print(f"    返回: outerId={items[0].get('outerId','')} title={items[0].get('title','')[:30]}")

        if skuid and skuid != oid:
            items = await test_and_print(client, f"code=「{skuid}」 (规格编码)",
                                         "erp.item.multicode.query",
                                         {"code": skuid, "pageNo": 1, "pageSize": 50},
                                         "list")
            if items:
                print(f"    返回: outerId={items[0].get('outerId','')} title={items[0].get('title','')[:30]}")
    else:
        print("  ⚠️ 无库存数据，跳过")

    # ════════════════════════════════════════════════════════
    # 4. 采购单status值验证
    # ════════════════════════════════════════════════════════
    print("\n" + "=" * 65)
    print("【4】采购单status值验证")
    print("=" * 65)

    # 先拿基线
    base = await test_and_print(client, "无status筛选(基线)",
                                "purchase.order.query",
                                {"pageNo": 1, "pageSize": 50})

    # 测试各个status值
    for status_val in ["WAIT_VERIFY", "VERIFYING", "GOODS_NOT_ARRIVED",
                       "GOODS_PART_ARRIVED", "FINISHED", "GOODS_CLOSED"]:
        await test_and_print(client, f"status={status_val}",
                             "purchase.order.query",
                             {"status": status_val, "pageNo": 1, "pageSize": 50})

    await client.close()
    print(f"\n{'═' * 65}")
    print("✅ 补测完成")
    print(f"{'═' * 65}")


if __name__ == "__main__":
    asyncio.run(main())
