"""
erp.item.sku.list.get (V2) — 用主编码查所有 SKU，看是否返回套件子单品
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

    for main_code in ["TJ-LBXXTXL03", "TJ-CCNNTXL01"]:
        print("=" * 60)
        print(f"erp.item.sku.list.get(outerId={main_code})")
        print("=" * 60)
        try:
            data = await client.request_with_retry(
                "erp.item.sku.list.get", {"outerId": main_code}
            )
            print(json.dumps(data, ensure_ascii=False, indent=2)[:5000])
        except Exception as e:
            print(f"错误: {e}")
        print()

    await client.close()


if __name__ == "__main__":
    asyncio.run(main())
