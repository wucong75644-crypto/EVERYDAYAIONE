"""
继续推 stock_status 上限：1000+
用skuOuterId测试（有2300个可用）
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

    # 收集尽量多的SKU编码
    print("收集编码中...")
    all_sku = []
    for page in range(1, 101):
        resp = await safe_query(client, "stock.api.status.query", {
            "pageNo": page, "pageSize": 100,
        })
        items = resp.get("stockStatusVoList") or []
        if not items:
            break
        for item in items:
            sid = item.get("skuOuterId", "")
            if sid and sid not in all_sku:
                all_sku.append(sid)
        if len(all_sku) >= 5000:
            break
    print(f"收集到 {len(all_sku)} 个SKU编码\n")

    # 测试上限
    print("=" * 65)
    print("stock_status skuOuterId 上限探测（1000+）")
    print("=" * 65)

    test_counts = [1000, 1500, 2000, 2500, 3000, 4000, 5000]
    for count in test_counts:
        if count > len(all_sku):
            print(f"  {count:5d}个 → 编码不足({len(all_sku)})，跳过")
            continue
        codes = ",".join(all_sku[:count])
        code_len = len(codes)
        resp = await safe_query(client, "stock.api.status.query", {
            "skuOuterId": codes, "pageNo": 1, "pageSize": 20,
        })
        if "_error" in resp:
            err = resp['_error'][:100]
            print(f"  {count:5d}个 ({code_len:>7,}字节) → ERROR: {err}")
            # 细化
            prev = test_counts[test_counts.index(count) - 1] if test_counts.index(count) > 0 else 0
            print(f"\n  细化 {prev}~{count}...")
            for fine in range(prev, count + 1, 100):
                if fine > len(all_sku) or fine == 0:
                    continue
                codes2 = ",".join(all_sku[:fine])
                resp2 = await safe_query(client, "stock.api.status.query", {
                    "skuOuterId": codes2, "pageNo": 1, "pageSize": 20,
                })
                if "_error" in resp2:
                    print(f"    {fine:5d}个 → ERROR: {resp2['_error'][:80]}")
                    # 更精细 10步
                    for fine2 in range(fine - 100, fine + 1, 10):
                        if fine2 <= 0 or fine2 > len(all_sku):
                            continue
                        codes3 = ",".join(all_sku[:fine2])
                        resp3 = await safe_query(client, "stock.api.status.query", {
                            "skuOuterId": codes3, "pageNo": 1, "pageSize": 20,
                        })
                        if "_error" in resp3:
                            print(f"      {fine2:5d}个 → ERROR")
                            # 更精细 1步
                            for fine3 in range(fine2 - 10, fine2 + 1):
                                if fine3 <= 0 or fine3 > len(all_sku):
                                    continue
                                codes4 = ",".join(all_sku[:fine3])
                                resp4 = await safe_query(client, "stock.api.status.query", {
                                    "skuOuterId": codes4, "pageNo": 1, "pageSize": 20,
                                })
                                status = "ERROR" if "_error" in resp4 else f"OK (total={resp4.get('total','?')})"
                                print(f"        {fine3:5d}个 → {status}")
                            break
                        else:
                            print(f"      {fine2:5d}个 → OK (total={resp2.get('total','?')})")
                    break
                else:
                    total = resp2.get("total", "?")
                    print(f"    {fine:5d}个 → OK (total={total})")
            break
        else:
            total = resp.get("total", "?")
            print(f"  {count:5d}个 ({code_len:>7,}字节) → OK (total={total})")

    await client.close()
    print(f"\n{'=' * 65}")
    print("测试完成")
    print(f"{'=' * 65}")


if __name__ == "__main__":
    asyncio.run(main())
