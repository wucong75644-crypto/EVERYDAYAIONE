"""
深度测试套件子单品 API 返回

目标：找出正确获取 suitSingleList 的方式
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

    code = "TJ-CCNNTXL01"
    sku_code = "TJ-CCNNTXL01-01"

    # 1. item.single.get 完整响应（看有没有其他子单品字段）
    print("=" * 60)
    print("1. item.single.get 完整响应")
    print("=" * 60)
    try:
        data = await client.request_with_retry(
            "item.single.get", {"outerId": code}
        )
        item = data.get("item") or data
        # 打印所有 key
        print(f"  顶层 keys: {list(data.keys())}")
        if "item" in data:
            print(f"  item keys: {list(item.keys())}")
        # 重点找子单品相关字段
        for key in sorted(item.keys()):
            val = item[key]
            if isinstance(val, (list, dict)) and val:
                print(f"  {key} = {type(val).__name__}({len(val) if isinstance(val, list) else 'dict'})")
                if isinstance(val, list) and len(val) <= 5:
                    for i, v in enumerate(val):
                        print(f"    [{i}] = {json.dumps(v, ensure_ascii=False)[:200]}")
            elif "suit" in key.lower() or "single" in key.lower() or "sub" in key.lower() or "child" in key.lower() or "combo" in key.lower():
                print(f"  {key} = {val}")
        # 打印 type 相关
        print(f"\n  type = {item.get('type')}")
        print(f"  title = {item.get('title')}")
        print(f"  suitSingleList = {item.get('suitSingleList')}")
        print(f"  skus count = {len(item.get('skus') or item.get('items') or [])}")
    except Exception as e:
        print(f"  错误: {e}")

    # 2. 用 sysItemId 查（可能 outerId 查不到子单品）
    print("\n" + "=" * 60)
    print("2. item.single.get 用 sysItemId")
    print("=" * 60)
    try:
        # 先拿到 sysItemId
        data = await client.request_with_retry(
            "item.single.get", {"outerId": code}
        )
        item = data.get("item") or data
        sys_item_id = item.get("sysItemId")
        print(f"  sysItemId = {sys_item_id}")

        if sys_item_id:
            data2 = await client.request_with_retry(
                "item.single.get", {"sysItemId": str(sys_item_id)}
            )
            item2 = data2.get("item") or data2
            print(f"  type = {item2.get('type')}")
            print(f"  suitSingleList = {item2.get('suitSingleList')}")
            suit = item2.get("suitSingleList") or []
            print(f"  子单品数量: {len(suit)}")
            for s in suit[:10]:
                print(f"    - {json.dumps(s, ensure_ascii=False)[:200]}")
    except Exception as e:
        print(f"  错误: {e}")

    # 3. 试试 erp.item.list.query 能否返回子单品
    print("\n" + "=" * 60)
    print("3. erp.item.list.query 查套件")
    print("=" * 60)
    try:
        data = await client.request_with_retry(
            "erp.item.list.query", {"outerId": code, "pageNo": 1, "pageSize": 5}
        )
        items = data.get("list") or data.get("items") or []
        print(f"  返回 {len(items)} 条")
        for it in items[:3]:
            print(f"  type={it.get('type')} | title={it.get('title')}")
            print(f"  suitSingleList={it.get('suitSingleList')}")
            suit = it.get("suitSingleList") or []
            if suit:
                for s in suit[:5]:
                    print(f"    子: {json.dumps(s, ensure_ascii=False)[:200]}")
    except Exception as e:
        print(f"  错误: {e}")

    # 4. 直接查快麦 API 文档中的套件相关接口
    # 尝试 erp.item.suite.get / item.suite.query 等
    print("\n" + "=" * 60)
    print("4. 尝试套件专用接口")
    print("=" * 60)

    suite_methods = [
        ("erp.item.suite.get", {"outerId": code}),
        ("erp.item.suite.query", {"outerId": code}),
        ("item.suite.get", {"outerId": code}),
        ("item.suite.query", {"outerId": code}),
        ("erp.item.single.get", {"outerId": code}),
    ]

    for method, params in suite_methods:
        try:
            data = await client.request_with_retry(method, params)
            print(f"\n  {method} → 成功!")
            print(f"  keys: {list(data.keys())}")
            # 查找任何像子单品的字段
            for key in data:
                val = data[key]
                if isinstance(val, list) and val:
                    print(f"  {key}: {len(val)} items")
                    if len(val) <= 5:
                        for v in val:
                            print(f"    {json.dumps(v, ensure_ascii=False)[:200]}")
        except Exception as e:
            err_str = str(e)
            if len(err_str) > 100:
                err_str = err_str[:100]
            print(f"  {method} → {err_str}")

    # 5. 完整打印 item.single.get 响应
    print("\n" + "=" * 60)
    print("5. item.single.get 完整 JSON（找子单品字段）")
    print("=" * 60)
    try:
        data = await client.request_with_retry(
            "item.single.get", {"outerId": code}
        )
        print(json.dumps(data, ensure_ascii=False, indent=2)[:3000])
    except Exception as e:
        print(f"  错误: {e}")

    await client.close()


if __name__ == "__main__":
    asyncio.run(main())
