"""
验证 erp.item.single.sku.get 返回字段（特别是 type 字段）

运行: cd backend && source venv/bin/activate && python scripts/test_sku_api_fields.py
"""

import asyncio
import json
import sys

sys.path.insert(0, ".")

from core.config import settings
from services.kuaimai.client import KuaiMaiClient


async def main():
    client = KuaiMaiClient(
        app_key=settings.kuaimai_app_key,
        app_secret=settings.kuaimai_app_secret,
        access_token=settings.kuaimai_access_token,
    )

    # 测试编码：TJ-CCNNTXL01-01（用户实际查询的 SKU 编码）
    test_codes = ["TJ-CCNNTXL01-01"]

    for code in test_codes:
        print(f"\n{'='*60}")
        print(f"测试 SKU 编码: {code}")
        print("="*60)

        # 1. erp.item.single.sku.get 返回字段
        print("\n--- erp.item.single.sku.get ---")
        try:
            data = await client.request_with_retry(
                "erp.item.single.sku.get", {"skuOuterId": code}
            )
            # 打印完整响应
            print(json.dumps(data, ensure_ascii=False, indent=2))

            # 重点检查 type 字段
            sku_data = data.get("itemSku")
            if isinstance(sku_data, list) and sku_data:
                sku_data = sku_data[0]
            elif not isinstance(sku_data, dict):
                sku_data = data

            print(f"\n关键字段:")
            print(f"  type = {sku_data.get('type', '【不存在】')}")
            print(f"  sysSkuId = {sku_data.get('sysSkuId', '【不存在】')}")
            print(f"  outerId = {sku_data.get('outerId', '【不存在】')}")
            print(f"  itemOuterId = {sku_data.get('itemOuterId', '【不存在】')}")
            print(f"  skuOuterId = {sku_data.get('skuOuterId', '【不存在】')}")
        except Exception as e:
            print(f"  错误: {e}")

        # 2. 用主编码查 item.single.get
        print("\n--- item.single.get (主编码) ---")
        try:
            # 从 SKU 数据推断主编码
            main_id = code.rsplit("-", 1)[0] if "-" in code else code
            print(f"  推断主编码: {main_id}")
            data2 = await client.request_with_retry(
                "item.single.get", {"outerId": main_id}
            )
            item = data2.get("item") or data2
            print(f"  type = {item.get('type', '【不存在】')}")
            print(f"  title = {item.get('title', '【不存在】')}")
            print(f"  sysItemId = {item.get('sysItemId', '【不存在】')}")

            suit_items = item.get("suitSingleList") or []
            print(f"  suitSingleList = {len(suit_items)} 个子单品")
            for s in suit_items[:5]:
                print(f"    - {s.get('outerId', '')} | sku={s.get('skuOuterId', '')} | {s.get('title', '')} x{s.get('ratio', 1)}")
        except Exception as e:
            print(f"  错误: {e}")

    await client.close()


if __name__ == "__main__":
    asyncio.run(main())
