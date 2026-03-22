"""
验证：用 SKU 编码直接查 item.single.get 是否返回 suitSingleList
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
    main_code = "TJ-LBXXTXL03"

    # 1. 用 SKU 编码直接查 item.single.get（不拆分）
    print("=" * 60)
    print(f"1. item.single.get(outerId={sku_code}) — SKU编码直接查")
    print("=" * 60)
    try:
        data = await client.request_with_retry(
            "item.single.get", {"outerId": sku_code}
        )
        item = data.get("item") or data
        print(f"  sysItemId = {item.get('sysItemId', '【不存在】')}")
        print(f"  type = {item.get('type', '【不存在】')}")
        print(f"  title = {item.get('title', '【不存在】')}")
        suit = item.get("suitSingleList")
        print(f"  suitSingleList = {suit}")
        if suit:
            print(f"  子单品数量: {len(suit)}")
            for s in suit[:10]:
                print(f"    - outerId={s.get('outerId','')} | sku={s.get('skuOuterId','')} | {s.get('title','')} x{s.get('ratio',1)}")
        print(f"  所有keys: {sorted(item.keys())}")
    except Exception as e:
        print(f"  错误: {e}")

    # 2. 用 SKU 的 sysSkuId 查
    print(f"\n{'=' * 60}")
    print(f"2. 先获取 sysSkuId，再用它查 item.single.get")
    print("=" * 60)
    try:
        sku_data_resp = await client.request_with_retry(
            "erp.item.single.sku.get", {"skuOuterId": sku_code}
        )
        sku_list = sku_data_resp.get("itemSku")
        if isinstance(sku_list, list) and sku_list:
            sku_d = sku_list[0]
        else:
            sku_d = sku_data_resp
        sys_sku_id = sku_d.get("sysSkuId")
        sys_item_id = sku_d.get("sysItemId")
        print(f"  sysSkuId = {sys_sku_id}")
        print(f"  sysItemId = {sys_item_id}")

        # 用 sysItemId 查（SPU级别）
        if sys_item_id:
            data2 = await client.request_with_retry(
                "item.single.get", {"sysItemId": str(sys_item_id)}
            )
            item2 = data2.get("item") or data2
            suit2 = item2.get("suitSingleList")
            print(f"\n  item.single.get(sysItemId={sys_item_id}):")
            print(f"    suitSingleList = {suit2}")
    except Exception as e:
        print(f"  错误: {e}")

    # 3. 用 erp.item.single.sku.get 完整字段看有没有子单品信息
    print(f"\n{'=' * 60}")
    print(f"3. erp.item.single.sku.get 完整响应")
    print("=" * 60)
    try:
        data = await client.request_with_retry(
            "erp.item.single.sku.get", {"skuOuterId": sku_code}
        )
        sku_list = data.get("itemSku")
        if isinstance(sku_list, list) and sku_list:
            sku_item = sku_list[0]
            print(f"  所有keys: {sorted(sku_item.keys())}")
            # 找任何可能包含子单品的字段
            for key in sorted(sku_item.keys()):
                val = sku_item[key]
                if isinstance(val, (list, dict)) and val:
                    print(f"  {key} = {json.dumps(val, ensure_ascii=False)[:200]}")
                elif "suit" in key.lower() or "single" in key.lower() or "sub" in key.lower() or "child" in key.lower():
                    print(f"  {key} = {val}")
    except Exception as e:
        print(f"  错误: {e}")

    # 4. 换另一个产品验证：TJ-CCNNTXL01-01
    print(f"\n{'=' * 60}")
    print(f"4. item.single.get(outerId=TJ-CCNNTXL01-01) — 另一个SKU")
    print("=" * 60)
    try:
        data = await client.request_with_retry(
            "item.single.get", {"outerId": "TJ-CCNNTXL01-01"}
        )
        item = data.get("item") or data
        print(f"  sysItemId = {item.get('sysItemId', '【不存在】')}")
        print(f"  type = {item.get('type', '【不存在】')}")
        print(f"  title = {item.get('title', '【不存在】')}")
        suit = item.get("suitSingleList")
        print(f"  suitSingleList = {suit}")
        if suit:
            print(f"  子单品数量: {len(suit)}")
            for s in suit[:10]:
                print(f"    - outerId={s.get('outerId','')} | sku={s.get('skuOuterId','')} | {s.get('title','')} x{s.get('ratio',1)}")
    except Exception as e:
        print(f"  错误: {e}")

    await client.close()


if __name__ == "__main__":
    asyncio.run(main())
