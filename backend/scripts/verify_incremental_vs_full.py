"""对比验证：增量时间查询 vs 全量编码查询，逐行逐字段对比

模拟真实同步场景：
1. 用时间查询（新增量逻辑）遍历所有仓库获取库存数据
2. 从时间查询结果中提取所有编码
3. 用编码查询（全量逻辑）获取相同编码的库存数据
4. 逐行对比每个字段，验证数据一致性
"""

import asyncio
import sys
from datetime import datetime, timedelta
from typing import Any

sys.path.insert(0, "/Users/wucong/EVERYDAYAIONE/backend")

from services.kuaimai.client import KuaiMaiClient

STOCK_FIELDS = [
    "mainOuterId", "skuOuterId", "wareHouseId",
    "sellableNum", "totalAvailableStock", "totalAvailableStockSum",
    "totalLockStock", "purchaseNum", "onTheWayNum", "allocateNum",
    "totalDefectiveStock", "virtualStock", "refundStock", "purchaseStock",
    "stockStatus", "stockModifiedTime",
    "title", "propertiesName",
]

PRICE_FIELDS = [
    "purchasePrice", "sellingPrice", "marketPrice",
]

KEY_FIELDS = STOCK_FIELDS + PRICE_FIELDS


def _row_key(item: dict) -> str:
    return f"{item.get('mainOuterId')}|{item.get('skuOuterId')}|{item.get('wareHouseId')}"


async def _fetch_by_time(
    client: KuaiMaiClient, wh_ids: list[str], start: str, end: str,
) -> dict[str, dict]:
    """模拟增量同步：遍历仓库 + 时间查询"""
    result: dict[str, dict] = {}
    for wh_id in wh_ids:
        page = 0
        while page < 500:
            page += 1
            data = await client.request_with_retry(
                "stock.api.status.query",
                {
                    "warehouseId": int(wh_id),
                    "startStockModified": start,
                    "endStockModified": end,
                    "pageSize": 100,
                    "pageNo": page,
                },
            )
            items = data.get("stockStatusVoList") or []
            for item in items:
                key = _row_key(item)
                result[key] = item
            if len(items) < 100:
                break
    return result


async def _fetch_by_codes(
    client: KuaiMaiClient, codes: list[str],
) -> dict[str, dict]:
    """模拟全量同步：按编码批量查"""
    result: dict[str, dict] = {}
    for i in range(0, len(codes), 100):
        batch = codes[i : i + 100]
        batch_str = ",".join(batch)
        page = 0
        while page < 500:
            page += 1
            data = await client.request_with_retry(
                "stock.api.status.query",
                {"mainOuterId": batch_str, "pageSize": 100, "pageNo": page},
            )
            items = data.get("stockStatusVoList") or []
            for item in items:
                key = _row_key(item)
                result[key] = item
            if len(items) < 100:
                break
    return result


def _compare_values(v1: Any, v2: Any) -> bool:
    """宽松比较：处理 API 返回类型不一致（str "0" vs int 0）"""
    if v1 == v2:
        return True
    try:
        return float(v1) == float(v2)
    except (TypeError, ValueError):
        return str(v1) == str(v2)


async def main():
    async with KuaiMaiClient() as client:
        if not client.is_configured:
            print("❌ 快麦客户端未配置")
            return

        wh_ids = ["87227", "436208", "444522"]
        end = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        start = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S")

        print(f"时间范围: {start} → {end}")
        print(f"仓库: {wh_ids}")
        print()

        # Step 1: 增量查询
        print("=" * 70)
        print("Step 1: 增量时间查询（遍历3仓库）...")
        incr_data = await _fetch_by_time(client, wh_ids, start, end)
        print(f"  增量结果: {len(incr_data)} 条 SKU×仓库 记录")

        if not incr_data:
            print("⚠️ 时间范围内无变动数据，换更大范围试试")
            start = (datetime.now() - timedelta(days=3)).strftime("%Y-%m-%d %H:%M:%S")
            print(f"  扩大范围: {start} → {end}")
            incr_data = await _fetch_by_time(client, wh_ids, start, end)
            print(f"  增量结果: {len(incr_data)} 条")

        if not incr_data:
            print("❌ 无数据可对比")
            return

        # 诊断：增量结果的仓库分布 + key 样本
        incr_wh_dist: dict[str, int] = {}
        for key, item in incr_data.items():
            wh = str(item.get("wareHouseId", "?"))
            incr_wh_dist[wh] = incr_wh_dist.get(wh, 0) + 1
        print(f"  增量仓库分布: {incr_wh_dist}")
        sample_keys = list(incr_data.keys())[:3]
        print(f"  增量 key 样本: {sample_keys}")

        # 提取所有不重复的主编码
        codes = list({
            item.get("mainOuterId")
            for item in incr_data.values()
            if item.get("mainOuterId")
        })
        print(f"  涉及 {len(codes)} 个不重复商品编码")
        print()

        # Step 2: 全量查询 — 也按仓库查（消除仓库覆盖差异，苹果对苹果）
        print("Step 2: 全量编码查询（按编码+仓库逐个查）...")
        full_data: dict[str, dict] = {}
        for wh_id in wh_ids:
            for i in range(0, len(codes), 100):
                batch = codes[i : i + 100]
                batch_str = ",".join(batch)
                page = 0
                while page < 500:
                    page += 1
                    data = await client.request_with_retry(
                        "stock.api.status.query",
                        {
                            "mainOuterId": batch_str,
                            "warehouseId": int(wh_id),
                            "pageSize": 100,
                            "pageNo": page,
                        },
                    )
                    items = data.get("stockStatusVoList") or []
                    for item in items:
                        key = _row_key(item)
                        full_data[key] = item
                    if len(items) < 100:
                        break
        print(f"  全量结果: {len(full_data)} 条 SKU×仓库 记录")
        full_wh_dist: dict[str, int] = {}
        for key, item in full_data.items():
            wh = str(item.get("wareHouseId", "?"))
            full_wh_dist[wh] = full_wh_dist.get(wh, 0) + 1
        print(f"  全量仓库分布: {full_wh_dist}")
        print()

        # Step 3: 对比
        print("=" * 70)
        print("Step 3: 逐行逐字段对比")
        print("=" * 70)

        # 增量有但全量没有
        only_incr = set(incr_data.keys()) - set(full_data.keys())
        # 全量有但增量没有（同编码但不同仓库/SKU）
        only_full = set(full_data.keys()) - set(incr_data.keys())
        # 两边都有的
        common = set(incr_data.keys()) & set(full_data.keys())

        print(f"\n共同记录: {len(common)} 条")
        print(f"仅增量有: {len(only_incr)} 条（增量按仓库查，可能多覆盖）")
        print(f"仅全量有: {len(only_full)} 条（全量按编码查，返回所有仓库）")

        # 逐字段对比共同记录（分库存字段和价格字段）
        stock_diff_count = 0
        price_diff_count = 0
        diff_records = 0
        diff_details: list[tuple[str, list[str]]] = []

        for key in sorted(common):
            incr_item = incr_data[key]
            full_item = full_data[key]
            record_diffs = []
            has_stock_diff = False

            for field in KEY_FIELDS:
                v_incr = incr_item.get(field)
                v_full = full_item.get(field)
                if not _compare_values(v_incr, v_full):
                    is_price = field in PRICE_FIELDS
                    if is_price:
                        price_diff_count += 1
                    else:
                        stock_diff_count += 1
                        has_stock_diff = True
                    tag = "[价格]" if is_price else "[库存]"
                    record_diffs.append(f"{tag} {field}: 增量={v_incr} vs 全量={v_full}")

            if record_diffs:
                diff_records += 1
                diff_details.append((key, record_diffs))

        # 结果输出
        print(f"\n{'─' * 70}")
        total_fields = len(common) * len(KEY_FIELDS)
        print(f"对比 {len(common)} 条共同记录 × {len(KEY_FIELDS)} 字段 = {total_fields} 个字段值")
        print(f"{'─' * 70}")

        stock_clean = len(common) - sum(1 for _, diffs in diff_details if any("[库存]" in d for d in diffs))
        print(f"\n📊 库存数量字段（{len(STOCK_FIELDS)}个）:")
        print(f"   ✅ 一致: {stock_clean}/{len(common)} ({stock_clean/len(common)*100:.1f}%)")
        print(f"   ❌ 差异: {stock_diff_count} 个字段")

        price_clean = len(common) - sum(1 for _, diffs in diff_details if any("[价格]" in d for d in diffs))
        print(f"\n💰 价格字段（{len(PRICE_FIELDS)}个）:")
        print(f"   ✅ 一致: {price_clean}/{len(common)} ({price_clean/len(common)*100:.1f}%)")
        print(f"   ❌ 差异: {price_diff_count} 个字段")

        if diff_details:
            # 只显示库存字段有差异的（价格差异可忽略）
            stock_diffs = [(k, d) for k, d in diff_details if any("[库存]" in x for x in d)]
            if stock_diffs:
                print(f"\n⚠️ 库存字段差异详情（{len(stock_diffs)} 条）:")
                for key, diffs in stock_diffs[:10]:
                    parts = key.split("|")
                    print(f"  {parts[0]} | {parts[1]} | 仓库{parts[2]}:")
                    for d in diffs:
                        if "[库存]" in d:
                            print(f"    → {d}")

            price_only = [(k, d) for k, d in diff_details if all("[价格]" in x for x in d)]
            if price_only:
                # 统计价格差异的仓库分布
                price_wh: dict[str, int] = {}
                for key, _ in price_only:
                    wh = key.split("|")[2]
                    price_wh[wh] = price_wh.get(wh, 0) + 1
                print(f"\n💰 价格差异按仓库分布（共 {len(price_only)} 条仅价格差异）:")
                for wh, cnt in sorted(price_wh.items(), key=lambda x: -x[1]):
                    print(f"   仓库 {wh}: {cnt} 条")

        # 分析仅全量有的记录
        if only_full:
            print(f"\n{'─' * 70}")
            print(f"仅全量有的 {len(only_full)} 条记录分析（增量按仓库+时间过滤，可能覆盖面不同）:")
            wh_dist: dict[str, int] = {}
            for key in only_full:
                wh = key.split("|")[2]
                wh_dist[wh] = wh_dist.get(wh, 0) + 1
            for wh, cnt in sorted(wh_dist.items()):
                in_list = "✅ 在增量仓库列表" if wh in wh_ids else "❌ 不在增量仓库列表"
                print(f"  仓库 {wh}: {cnt} 条 — {in_list}")

        # 汇总
        accuracy = (len(common) - diff_records) / len(common) * 100 if common else 0
        print(f"\n{'=' * 70}")
        print(f"最终结论:")
        print(f"  共同记录准确率: {accuracy:.1f}% ({len(common) - diff_records}/{len(common)})")
        print(f"  增量覆盖率: {len(common)}/{len(common) + len(only_full)} = "
              f"{len(common) / (len(common) + len(only_full)) * 100:.1f}%"
              if (len(common) + len(only_full)) > 0 else "  N/A")
        print(f"{'=' * 70}")


if __name__ == "__main__":
    asyncio.run(main())
