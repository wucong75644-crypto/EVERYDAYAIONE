"""
验证指定套件编码的 suitSingleList 返回情况
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

    sku_code = "TJ-LBXXTXL03-15"

    # 1. 先用 SKU 编码查 erp.item.single.sku.get
    print("=" * 60)
    print(f"1. erp.item.single.sku.get (skuOuterId={sku_code})")
    print("=" * 60)
    try:
        data = await client.request_with_retry(
            "erp.item.single.sku.get", {"skuOuterId": sku_code}
        )
        sku_data = data.get("itemSku")
        if isinstance(sku_data, list) and sku_data:
            sku_data = sku_data[0]
        elif not isinstance(sku_data, dict):
            sku_data = data

        print(f"  type = {sku_data.get('type', '【不存在】')}")
        print(f"  itemOuterId = {sku_data.get('itemOuterId', '【不存在】')}")
        print(f"  outerId = {sku_data.get('outerId', '【不存在】')}")
        print(f"  sysItemId = {sku_data.get('sysItemId', '【不存在】')}")
        print(f"  propertiesName = {sku_data.get('propertiesName', '【不存在】')}")
        main_id = sku_data.get("itemOuterId") or sku_data.get("outerId", "")
        print(f"  → 推断主编码: {main_id}")
    except Exception as e:
        print(f"  错误: {e}")
        main_id = sku_code.rsplit("-", 1)[0]
        print(f"  → 手动推断主编码: {main_id}")

    # 2. 用主编码查 item.single.get
    print(f"\n{'=' * 60}")
    print(f"2. item.single.get (outerId={main_id})")
    print("=" * 60)
    try:
        data = await client.request_with_retry(
            "item.single.get", {"outerId": main_id}
        )
        item = data.get("item") or data
        print(f"  type = {item.get('type', '【不存在】')}")
        print(f"  title = {item.get('title', '【不存在】')}")
        print(f"  sysItemId = {item.get('sysItemId', '【不存在】')}")

        suit_items = item.get("suitSingleList")
        print(f"  suitSingleList = {suit_items}")
        if suit_items:
            print(f"  子单品数量: {len(suit_items)}")
            for s in suit_items[:10]:
                print(f"    - outerId={s.get('outerId', '')} | sku={s.get('skuOuterId', '')} | {s.get('title', '')} x{s.get('ratio', 1)} | {s.get('propertiesName', '')}")
        else:
            print("  ⚠ suitSingleList 为空或不存在")

        # 打印完整 item keys
        print(f"\n  item 所有 keys: {sorted(item.keys())}")
    except Exception as e:
        print(f"  错误: {e}")

    # 3. 用 sysItemId 再查一次
    print(f"\n{'=' * 60}")
    print(f"3. item.single.get (sysItemId)")
    print("=" * 60)
    try:
        data = await client.request_with_retry(
            "item.single.get", {"outerId": main_id}
        )
        item = data.get("item") or data
        sys_id = item.get("sysItemId")
        if sys_id:
            data2 = await client.request_with_retry(
                "item.single.get", {"sysItemId": str(sys_id)}
            )
            item2 = data2.get("item") or data2
            suit2 = item2.get("suitSingleList")
            print(f"  sysItemId={sys_id}")
            print(f"  suitSingleList = {suit2}")
            if suit2:
                print(f"  子单品数量: {len(suit2)}")
                for s in suit2[:10]:
                    print(f"    - {s.get('outerId', '')} | {s.get('title', '')} x{s.get('ratio', 1)}")
    except Exception as e:
        print(f"  错误: {e}")

    # 4. 完整 JSON（截取前3000字符看是否有其他子单品字段）
    print(f"\n{'=' * 60}")
    print(f"4. 完整 JSON 响应")
    print("=" * 60)
    try:
        data = await client.request_with_retry(
            "item.single.get", {"outerId": main_id}
        )
        print(json.dumps(data, ensure_ascii=False, indent=2)[:4000])
    except Exception as e:
        print(f"  错误: {e}")

    await client.close()


if __name__ == "__main__":
    asyncio.run(main())
