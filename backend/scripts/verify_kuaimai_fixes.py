"""严格验证：库存时间查询 vs 编码直查，多条数据逐一对比"""

import asyncio
import json
import sys
from datetime import datetime, timedelta

sys.path.insert(0, "/Users/wucong/EVERYDAYAIONE/backend")

from services.kuaimai.client import KuaiMaiClient

KEY_FIELDS = [
    "sellableNum", "totalAvailableStock", "totalAvailableStockSum",
    "totalLockStock", "purchaseNum", "onTheWayNum", "allocateNum",
    "totalDefectiveStock", "virtualStock", "refundStock", "purchaseStock",
    "stockStatus", "wareHouseId", "stockModifiedTime",
]


async def verify():
    async with KuaiMaiClient() as client:
        if not client.is_configured:
            print("❌ 快麦客户端未配置")
            return

        end_date = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        start_date = (datetime.now() - timedelta(days=3)).strftime("%Y-%m-%d %H:%M:%S")

        # Step 1: 时间查询取 20 条
        print(f"时间范围: {start_date} → {end_date}")
        print("=" * 70)
        data = await client.request_with_retry(
            "stock.api.status.query",
            {
                "startStockModified": start_date,
                "endStockModified": end_date,
                "pageSize": 20,
                "pageNo": 1,
            },
        )
        time_items = data.get("stockStatusVoList") or []
        print(f"时间查询返回 {len(time_items)} 条\n")

        if not time_items:
            print("⚠️ 无数据")
            return

        # Step 2: 收集所有不重复的 mainOuterId，按编码直查
        codes = list({
            item.get("mainOuterId") or item.get("outerId")
            for item in time_items
            if item.get("mainOuterId") or item.get("outerId")
        })
        print(f"涉及 {len(codes)} 个不重复编码: {codes}\n")

        # 按编码直查拿实时数据
        direct_map: dict[str, dict] = {}  # key = "mainOuterId|skuOuterId|wareHouseId"
        for code in codes:
            page = 1
            while page <= 10:
                d = await client.request_with_retry(
                    "stock.api.status.query",
                    {"mainOuterId": code, "pageSize": 100, "pageNo": page},
                )
                items = d.get("stockStatusVoList") or []
                for it in items:
                    key = f"{it.get('mainOuterId')}|{it.get('skuOuterId')}|{it.get('wareHouseId')}"
                    direct_map[key] = it
                if len(items) < 100:
                    break
                page += 1

        print(f"直查共获取 {len(direct_map)} 条 SKU×仓库 记录\n")

        # Step 3: 逐条对比
        total = 0
        match_count = 0
        mismatch_count = 0
        missing_count = 0

        print(f"{'编码':<20} {'SKU编码':<25} {'仓库':<10} {'结果'}")
        print("-" * 90)

        for item in time_items:
            mid = item.get("mainOuterId") or ""
            sid = item.get("skuOuterId") or ""
            wid = item.get("wareHouseId") or ""
            key = f"{mid}|{sid}|{wid}"
            total += 1

            direct = direct_map.get(key)
            if not direct:
                missing_count += 1
                print(f"{mid:<20} {sid:<25} {wid:<10} ⚠️ 直查未找到匹配记录")
                continue

            diffs = []
            for f in KEY_FIELDS:
                v_time = item.get(f)
                v_direct = direct.get(f)
                # 统一数值类型比较（API有时返回字符串"0"有时返回数字0）
                try:
                    if float(v_time) != float(v_direct):
                        diffs.append(f"{f}: {v_time} vs {v_direct}")
                except (TypeError, ValueError):
                    if v_time != v_direct:
                        diffs.append(f"{f}: {v_time} vs {v_direct}")

            if diffs:
                mismatch_count += 1
                print(f"{mid:<20} {sid:<25} {wid:<10} ❌ 不一致")
                for d in diffs:
                    print(f"    → {d}")
            else:
                match_count += 1
                print(f"{mid:<20} {sid:<25} {wid:<10} ✅")

        # 汇总
        print("\n" + "=" * 70)
        print(f"总计: {total} 条")
        print(f"  ✅ 完全一致: {match_count}")
        print(f"  ❌ 有差异:   {mismatch_count}")
        print(f"  ⚠️ 未匹配:  {missing_count}")
        accuracy = match_count / total * 100 if total else 0
        print(f"\n准确率: {accuracy:.1f}%")
        if accuracy == 100:
            print("✅ 时间查询数据与直查完全一致，可以放心使用！")
        elif accuracy >= 95:
            print("⚠️ 基本准确但有少量差异，需排查")
        else:
            print("❌ 差异较大，不建议直接使用")


if __name__ == "__main__":
    asyncio.run(verify())
