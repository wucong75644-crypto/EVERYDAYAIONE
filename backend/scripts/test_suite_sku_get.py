"""
用 erp.item.single.sku.get 查 SKU 编码，打印完整响应
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

    for code in ["TJ-LBXXTXL03-15", "TJ-CCNNTXL01-01"]:
        print("=" * 60)
        print(f"erp.item.single.sku.get(skuOuterId={code})")
        print("=" * 60)
        try:
            data = await client.request_with_retry(
                "erp.item.single.sku.get", {"skuOuterId": code}
            )
            # 完整 JSON
            print(json.dumps(data, ensure_ascii=False, indent=2))
        except Exception as e:
            print(f"错误: {e}")
        print()

    await client.close()


if __name__ == "__main__":
    asyncio.run(main())
