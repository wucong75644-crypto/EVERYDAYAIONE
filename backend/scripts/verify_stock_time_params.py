"""
验证 stock.api.status.query 的 4 个时间参数是否真正生效

方法：
1. 基线：无时间参数，获取 total
2. 分别传 created / modified / startStockModified / endStockModified
3. 对比 total，如果有变化说明生效
"""

import asyncio
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.kuaimai.client import KuaiMaiClient

METHOD = "stock.api.status.query"


async def query(client: KuaiMaiClient, label: str, params: dict) -> dict:
    """执行查询并返回 total + 第一条记录"""
    full = {"pageNo": 1, "pageSize": 20, **params}
    print(f"\n{'='*60}")
    print(f"测试: {label}")
    print(f"参数: {params}")
    try:
        resp = await client.request_with_retry(METHOD, full)
        total = resp.get("total", "N/A")
        items = resp.get("stockStatusVoList") or []
        print(f"✅ total={total}, 返回条数={len(items)}")
        if items:
            first = items[0]
            # 打印时间相关字段
            for k in ["created", "modified", "stockModified", "gmtCreate", "gmtModified"]:
                if k in first:
                    print(f"   {k} = {first[k]}")
        return {"total": total, "count": len(items), "ok": True}
    except Exception as e:
        print(f"❌ 错误: {e}")
        return {"total": None, "count": 0, "ok": False, "error": str(e)}


async def main():
    client = KuaiMaiClient()

    # 1. 基线
    baseline = await query(client, "基线（无时间参数）", {})

    # 2. created — 很早的时间，应该包含所有
    await query(client, "created=2020-01-01（很早，应≈基线）", {
        "created": "2020-01-01 00:00:00",
    })

    # 3. created — 未来时间，如果生效应该 total=0
    await query(client, "created=2099-01-01（未来，如果生效应=0）", {
        "created": "2099-01-01 00:00:00",
    })

    # 4. modified — 很早的时间
    await query(client, "modified=2020-01-01（很早，应≈基线）", {
        "modified": "2020-01-01 00:00:00",
    })

    # 5. modified — 未来时间
    await query(client, "modified=2099-01-01（未来，如果生效应=0）", {
        "modified": "2099-01-01 00:00:00",
    })

    # 6. startStockModified + endStockModified — 最近 7 天
    await query(client, "库存变动：最近7天", {
        "startStockModified": "2026-04-15 00:00:00",
        "endStockModified": "2026-04-22 23:59:59",
    })

    # 7. startStockModified + endStockModified — 远古时间段（不应该有数据）
    await query(client, "库存变动：2000年（应=0）", {
        "startStockModified": "2000-01-01 00:00:00",
        "endStockModified": "2000-01-02 00:00:00",
    })

    # 8. 只传 startStockModified 不传 end
    await query(client, "只传startStockModified（不传end）", {
        "startStockModified": "2026-04-20 00:00:00",
    })

    # 9. 只传 endStockModified 不传 start
    await query(client, "只传endStockModified（不传start）", {
        "endStockModified": "2026-04-20 00:00:00",
    })

    await client.close()
    print(f"\n{'='*60}")
    print("验证完成")


if __name__ == "__main__":
    asyncio.run(main())
