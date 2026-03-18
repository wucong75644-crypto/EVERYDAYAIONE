"""
深度验证 6 个失败 entry 的实际 response_key

这些 API 空参数调用时只返回 {traceId, success}，
需要传有效参数来看实际数据结构。
"""

import asyncio
import json
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from services.kuaimai.client import KuaiMaiClient


async def verify():
    client = KuaiMaiClient()

    tests = [
        # (名称, method, 当前response_key, 测试参数)
        (
            "PRODUCT.virtual_warehouse",
            "erp.virtual.warehouse.query",
            "list",
            {"pageNo": 1, "pageSize": 20},
        ),
        (
            "PRODUCT.brand_list",
            "brand.list.get",
            "items",
            {"pageNo": 1, "pageSize": 20},
        ),
        (
            "TRADE.unique_code_query",
            "erp.item.unique.code.query",
            "list",
            {"pageNo": 1, "pageSize": 20},
        ),
        (
            "TRADE.logistics_company_list",
            "erp.trade.logistics.company.user.list",
            "list",
            {"pageNo": 1, "pageSize": 20},
        ),
        (
            "AFTERSALES.repair_list",
            "erp.aftersale.repair.list.query",
            "list",
            {"pageNo": 1, "pageSize": 20, "status": "0"},
        ),
        (
            "WAREHOUSE.unshelve_list",
            "erp.wms.unshelve.order.query",
            "list",
            {"pageNo": 1, "pageSize": 20},
        ),
    ]

    for name, method, configured_rk, params in tests:
        print(f"\n{'=' * 60}")
        print(f"  {name} | method={method}")
        print(f"  配置 response_key = '{configured_rk}'")
        print(f"  测试参数: {params}")
        print(f"{'=' * 60}")

        try:
            data = await client.request_with_retry(method, params)

            # 分析响应结构
            all_keys = list(data.keys())
            list_keys = {k: len(v) for k, v in data.items() if isinstance(v, list)}
            dict_keys = {k: list(v.keys())[:5] for k, v in data.items()
                         if isinstance(v, dict)}

            print(f"  响应所有key: {all_keys}")
            print(f"  total = {data.get('total', '不存在')}")

            if list_keys:
                print(f"  list类型字段: {list_keys}")
                for k in list_keys:
                    items = data[k]
                    if items:
                        print(f"    {k}[0] keys = {list(items[0].keys())[:10]}")
            else:
                print(f"  ⚠ 无list类型字段!")

            if dict_keys:
                print(f"  dict类型字段: {dict_keys}")

            # 检查配置的 response_key
            if configured_rk in data:
                print(f"  ✅ 配置的 '{configured_rk}' 存在于响应中")
            else:
                if list_keys:
                    actual = list(list_keys.keys())[0]
                    print(f"  ❌ 配置的 '{configured_rk}' 不存在!")
                    print(f"     建议改为: '{actual}'")
                else:
                    print(f"  ⚠ 配置的 '{configured_rk}' 不存在，且无list字段")
                    print(f"     可能是空结果，或该API不返回列表")

            # 打印完整响应（限制大小）
            raw = json.dumps(data, ensure_ascii=False, indent=2)
            if len(raw) > 1000:
                raw = raw[:1000] + "\n  ... (truncated)"
            print(f"\n  完整响应:\n{raw}")

        except Exception as e:
            print(f"  ❌ 异常: {e}")

    await client.close()


asyncio.run(verify())
