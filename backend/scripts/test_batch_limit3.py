"""
追测 stock_status mainOuterId/skuOuterId 的真实上限
从100开始往上加，找到报错的临界值
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

    # 收集尽量多的编码
    print("收集编码中...")
    all_outer_ids = []
    all_sku_outer_ids = []
    for page in range(1, 51):  # 最多50页 x 100 = 5000条
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
        if len(all_outer_ids) >= 1000 and len(all_sku_outer_ids) >= 1000:
            break
    print(f"收集到 {len(all_outer_ids)} 个主编码, {len(all_sku_outer_ids)} 个SKU编码\n")

    # ═══════════════════════════════════════════════════
    # 1. mainOuterId 上限探测
    # ═══════════════════════════════════════════════════
    print("=" * 65)
    print("【1】stock_status mainOuterId 上限探测")
    print("=" * 65)

    test_counts = [50, 100, 150, 200, 300, 400, 500, 600, 800, 1000]
    for count in test_counts:
        if count > len(all_outer_ids):
            print(f"  mainOuterId {count:4d}个 → 编码不足，跳过")
            break
        codes = ",".join(all_outer_ids[:count])
        resp = await safe_query(client, "stock.api.status.query", {
            "mainOuterId": codes, "pageNo": 1, "pageSize": 20,
        })
        if "_error" in resp:
            err = resp['_error'][:100]
            print(f"  mainOuterId {count:4d}个 → ERROR: {err}")
            # 找到临界值，细化测试
            prev_count = test_counts[test_counts.index(count) - 1] if test_counts.index(count) > 0 else 0
            print(f"\n  细化测试 {prev_count}~{count} ...")
            for fine in range(prev_count, count + 1, 10):
                if fine > len(all_outer_ids) or fine == 0:
                    continue
                codes2 = ",".join(all_outer_ids[:fine])
                resp2 = await safe_query(client, "stock.api.status.query", {
                    "mainOuterId": codes2, "pageNo": 1, "pageSize": 20,
                })
                if "_error" in resp2:
                    print(f"    {fine:4d}个 → ERROR: {resp2['_error'][:80]}")
                    # 更精细
                    for fine2 in range(fine - 10, fine + 1):
                        if fine2 <= 0 or fine2 > len(all_outer_ids):
                            continue
                        codes3 = ",".join(all_outer_ids[:fine2])
                        resp3 = await safe_query(client, "stock.api.status.query", {
                            "mainOuterId": codes3, "pageNo": 1, "pageSize": 20,
                        })
                        if "_error" in resp3:
                            print(f"    {fine2:4d}个 → ERROR")
                        else:
                            total = resp3.get("total", "?")
                            print(f"    {fine2:4d}个 → OK (total={total})")
                    break
                else:
                    total = resp2.get("total", "?")
                    print(f"    {fine:4d}个 → OK (total={total})")
            break
        else:
            total = resp.get("total", "?")
            items = resp.get("stockStatusVoList") or []
            print(f"  mainOuterId {count:4d}个 → {len(items)}条 (total={total})")

    # ═══════════════════════════════════════════════════
    # 2. skuOuterId 上限探测
    # ═══════════════════════════════════════════════════
    print("\n" + "=" * 65)
    print("【2】stock_status skuOuterId 上限探测")
    print("=" * 65)

    for count in test_counts:
        if count > len(all_sku_outer_ids):
            print(f"  skuOuterId {count:4d}个 → 编码不足，跳过")
            break
        codes = ",".join(all_sku_outer_ids[:count])
        resp = await safe_query(client, "stock.api.status.query", {
            "skuOuterId": codes, "pageNo": 1, "pageSize": 20,
        })
        if "_error" in resp:
            err = resp['_error'][:100]
            print(f"  skuOuterId {count:4d}个 → ERROR: {err}")
            prev_count = test_counts[test_counts.index(count) - 1] if test_counts.index(count) > 0 else 0
            print(f"\n  细化测试 {prev_count}~{count} ...")
            for fine in range(prev_count, count + 1, 10):
                if fine > len(all_sku_outer_ids) or fine == 0:
                    continue
                codes2 = ",".join(all_sku_outer_ids[:fine])
                resp2 = await safe_query(client, "stock.api.status.query", {
                    "skuOuterId": codes2, "pageNo": 1, "pageSize": 20,
                })
                if "_error" in resp2:
                    print(f"    {fine:4d}个 → ERROR: {resp2['_error'][:80]}")
                    for fine2 in range(fine - 10, fine + 1):
                        if fine2 <= 0 or fine2 > len(all_sku_outer_ids):
                            continue
                        codes3 = ",".join(all_sku_outer_ids[:fine2])
                        resp3 = await safe_query(client, "stock.api.status.query", {
                            "skuOuterId": codes3, "pageNo": 1, "pageSize": 20,
                        })
                        if "_error" in resp3:
                            print(f"    {fine2:4d}个 → ERROR")
                        else:
                            total = resp3.get("total", "?")
                            print(f"    {fine2:4d}个 → OK (total={total})")
                    break
                else:
                    total = resp2.get("total", "?")
                    print(f"    {fine:4d}个 → OK (total={total})")
            break
        else:
            total = resp.get("total", "?")
            items = resp.get("stockStatusVoList") or []
            print(f"  skuOuterId {count:4d}个 → {len(items)}条 (total={total})")

    # ═══════════════════════════════════════════════════
    # 3. 顺便测一下请求体大小限制
    #    如果编码数量没触发限制，可能是请求体大小限制
    # ═══════════════════════════════════════════════════
    print("\n" + "=" * 65)
    print("【3】请求体大小参考")
    print("=" * 65)
    for count in [100, 500, 1000]:
        if count > len(all_outer_ids):
            break
        codes = ",".join(all_outer_ids[:count])
        print(f"  {count}个mainOuterId → 字符串长度={len(codes)}字节")

    await client.close()
    print(f"\n{'=' * 65}")
    print("测试完成")
    print(f"{'=' * 65}")


if __name__ == "__main__":
    asyncio.run(main())
