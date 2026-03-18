"""
API审计验证脚本（只读，不改业务代码）

验证方案中发现的6个HIGH问题：
1. outstock_order_query timeBegin/timeEnd 格式（日期字符串 vs 毫秒时间戳）
2. outstock_order_query statusList 枚举值含义
3. aftersale_list sid 是否为幽灵参数
4. stock_in_out skuOuterId 是否有效
5. stock_in_out order_type=2 销售出库过滤
"""

import asyncio
import json
import sys
import os
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.kuaimai.client import KuaiMaiClient


async def safe_query(client: KuaiMaiClient, method: str, biz_params: dict) -> dict:
    """安全调用API，异常返回错误信息"""
    try:
        return await client.request_with_retry(method=method, biz_params=biz_params)
    except Exception as e:
        return {"_error": str(e)}


def print_section(title: str) -> None:
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}")


def print_result(label: str, result: dict, show_keys: list = None) -> None:
    if "_error" in result:
        print(f"  [{label}] ❌ ERROR: {result['_error'][:100]}")
        return

    total = result.get("total", "?")
    items = result.get("list", [])
    print(f"  [{label}] total={total}, items={len(items) if isinstance(items, list) else '?'}")

    if show_keys and isinstance(items, list) and items:
        first = items[0]
        for key in show_keys:
            print(f"    {key}: {first.get(key, 'N/A')}")


async def test_outstock_order_time_format(client: KuaiMaiClient) -> None:
    """H3: 验证outstock_order_query的timeBegin/timeEnd格式"""
    print_section("H3: outstock_order_query 时间格式（日期字符串 vs ms时间戳）")

    # 用最近7天的数据
    now = datetime.now()
    seven_days_ago = datetime(now.year, now.month, now.day - 7 if now.day > 7 else 1)

    date_str_begin = seven_days_ago.strftime("%Y-%m-%d 00:00:00")
    date_str_end = now.strftime("%Y-%m-%d 23:59:59")
    ms_begin = int(seven_days_ago.timestamp() * 1000)
    ms_end = int(now.timestamp() * 1000)

    method = "erp.wave.logistics.order.query"

    # 测试1: 用日期字符串
    print(f"\n  测试A: 日期字符串 timeBegin={date_str_begin}")
    r1 = await safe_query(client, method, {
        "timeType": 1,
        "timeBegin": date_str_begin,
        "timeEnd": date_str_end,
        "pageNo": 1,
        "pageSize": 20,
    })
    print_result("日期字符串", r1)

    # 测试2: 用毫秒时间戳
    print(f"\n  测试B: 毫秒时间戳 timeBegin={ms_begin}")
    r2 = await safe_query(client, method, {
        "timeType": 1,
        "timeBegin": ms_begin,
        "timeEnd": ms_end,
        "pageNo": 1,
        "pageSize": 20,
    })
    print_result("毫秒时间戳", r2)

    # 对比
    t1 = r1.get("total", -1) if "_error" not in r1 else -1
    t2 = r2.get("total", -1) if "_error" not in r2 else -1
    print(f"\n  结论: 日期字符串total={t1}, 毫秒时间戳total={t2}")
    if t1 == 0 and t2 > 0:
        print("  ✅ 确认：API只接受毫秒时间戳，日期字符串返回0条")
    elif t1 > 0 and t2 > 0:
        print("  ⚠️ 两种格式都返回数据，需进一步对比")
    elif t1 > 0 and t2 == 0:
        print("  ⚠️ 与文档矛盾：日期字符串有结果，毫秒时间戳反而0条")
    else:
        print(f"  ❓ 无法确定（可能没有数据）")


async def test_outstock_order_status(client: KuaiMaiClient) -> None:
    """H1: 验证outstock_order_query的statusList含义"""
    print_section("H1: outstock_order_query statusList 枚举含义")

    method = "erp.wave.logistics.order.query"
    now = datetime.now()
    ms_begin = int(datetime(now.year, now.month, 1).timestamp() * 1000)
    ms_end = int(now.timestamp() * 1000)

    # 按文档正确枚举逐个查
    status_map = {
        10: "待处理(文档) / 待打印(当前)",
        20: "预处理完成(文档) / 待称重(当前)",
        30: "发货中(文档) / 待出库(当前)",
        50: "已发货(文档) / 部分发货(当前)",
        70: "已关闭(文档) / 已发货(当前)",
        90: "已作废(文档) / 已签收(当前)",
    }

    for status, label in status_map.items():
        r = await safe_query(client, method, {
            "statusList": str(status),
            "timeType": 1,
            "timeBegin": ms_begin,
            "timeEnd": ms_end,
            "pageNo": 1,
            "pageSize": 5,
        })
        total = r.get("total", 0) if "_error" not in r else "ERROR"
        items = r.get("list", []) if "_error" not in r else []
        # 检查返回数据中的status字段
        actual_status = items[0].get("status", "?") if items else "无数据"
        print(f"  statusList={status:>2} ({label}): total={total}, 首条status={actual_status}")


async def test_aftersale_sid(client: KuaiMaiClient) -> None:
    """H4: 验证aftersale_list的sid参数是否有效"""
    print_section("H4: aftersale_list sid 参数验证")

    method = "erp.aftersale.list.query"

    # 先用时间范围查出一些数据，获取真实的tid和sid
    print("\n  Step 1: 先查最近的售后工单获取真实数据...")
    r0 = await safe_query(client, method, {
        "asVersion": 2,
        "startModified": "2026-03-15",
        "endModified": "2026-03-18",
        "pageNo": 1,
        "pageSize": 5,
    })

    if "_error" in r0 or not r0.get("list"):
        print(f"  ❌ 无法获取基线数据: {r0.get('_error', '列表为空')}")
        return

    items = r0["list"]
    sample = items[0]
    real_tid = sample.get("tid", "")
    real_sid = sample.get("sid", "")
    print(f"  找到样本: tid={real_tid}, sid={real_sid}")

    # Step 2: 用tid查（应该能查到）
    print(f"\n  Step 2: 用 tid={real_tid} 查...")
    r_tid = await safe_query(client, method, {
        "tid": real_tid,
        "asVersion": 2,
        "pageNo": 1,
        "pageSize": 20,
    })
    t_tid = r_tid.get("total", 0) if "_error" not in r_tid else "ERROR"
    print(f"  tid查询: total={t_tid}")

    # Step 3: 用sid查（如果幽灵参数，应该返回全量而非过滤）
    print(f"\n  Step 3: 用 sid={real_sid} 查（验证是否为幽灵参数）...")
    r_sid = await safe_query(client, method, {
        "sid": real_sid,
        "asVersion": 2,
        "pageNo": 1,
        "pageSize": 20,
    })
    t_sid = r_sid.get("total", 0) if "_error" not in r_sid else "ERROR"
    print(f"  sid查询: total={t_sid}")

    # 对比
    print(f"\n  结论: tid查total={t_tid}, sid查total={t_sid}")
    if isinstance(t_tid, int) and isinstance(t_sid, int):
        if t_sid > t_tid * 5:
            print("  ✅ 确认sid是幽灵参数：sid查返回全量(无过滤效果)，tid查有过滤")
        elif t_sid == t_tid:
            print("  ⚠️ 结果数相同，可能sid恰好有效或数据量小")
        else:
            print(f"  ❓ 需人工判断")


async def test_stock_in_out_sku_outer_id(client: KuaiMaiClient) -> None:
    """M2: 验证stock_in_out是否支持skuOuterId"""
    print_section("M2: stock_in_out skuOuterId 支持验证")

    method = "erp.item.stock.in.out.list"

    # 用已知的商品编码查（避免不传outerId报"超过2000个SKU"）
    test_outer_id = "SEVENTEENLSG01-01"

    print(f"\n  Step 1: 用 outerId={test_outer_id} 查基线...")
    r0 = await safe_query(client, method, {
        "outerId": test_outer_id,
        "pageNo": 1,
        "pageSize": 5,
    })

    if "_error" in r0 or not r0.get("list"):
        print(f"  ❌ 无法获取基线数据: {r0.get('_error', '列表为空')}")
        # 尝试另一个编码
        test_outer_id = "SEVENTEENLSG01"
        print(f"  重试: outerId={test_outer_id}")
        r0 = await safe_query(client, method, {
            "outerId": test_outer_id,
            "pageNo": 1,
            "pageSize": 5,
        })
        if "_error" in r0 or not r0.get("list"):
            print(f"  ❌ 仍然失败: {r0.get('_error', '列表为空')}")
            return

    items = r0["list"]
    t0 = r0.get("total", 0)
    sample = items[0]
    real_outer_id = sample.get("outerId", "")
    real_sku_outer_id = sample.get("skuOuterId", "")
    print(f"  基线: total={t0}, outerId={real_outer_id}, skuOuterId={real_sku_outer_id}")

    # 用skuOuterId查
    if real_sku_outer_id and real_sku_outer_id != real_outer_id:
        print(f"\n  Step 2: 用 skuOuterId={real_sku_outer_id} 查...")
        r2 = await safe_query(client, method, {
            "skuOuterId": real_sku_outer_id,
            "pageNo": 1,
            "pageSize": 20,
        })
        t2 = r2.get("total", 0) if "_error" not in r2 else "ERROR"
        print(f"  skuOuterId查询: total={t2}")

        if isinstance(t2, int) and t2 > 0:
            print("  ✅ skuOuterId有效，可以添加到param_map")
        elif t2 == 0:
            print("  ⚠️ skuOuterId返回0条，API可能不支持此参数")
        else:
            print(f"  ❌ skuOuterId查询出错: {r2.get('_error', '')[:80]}")
    elif real_sku_outer_id == real_outer_id:
        print(f"\n  ⚠️ skuOuterId={real_sku_outer_id} 等于outerId，无法区分测试")
        print("  尝试用规格编码格式查...")
        r2 = await safe_query(client, method, {
            "skuOuterId": real_sku_outer_id,
            "pageNo": 1,
            "pageSize": 20,
        })
        t2 = r2.get("total", 0) if "_error" not in r2 else "ERROR"
        print(f"  skuOuterId查询: total={t2} (outerId查询={t0})")
    else:
        print("\n  ⚠️ 样本无skuOuterId字段，跳过验证")


async def test_stock_in_out_order_type(client: KuaiMaiClient) -> None:
    """验证stock_in_out的orderType=2（销售出库）过滤"""
    print_section("stock_in_out orderType=2 销售出库过滤")

    method = "erp.item.stock.in.out.list"
    test_outer_id = "SEVENTEENLSG01-01"

    # 不带orderType查（需要outerId避免超2000限制）
    print(f"\n  Step 1: outerId={test_outer_id} 全类型...")
    r0 = await safe_query(client, method, {
        "outerId": test_outer_id,
        "pageNo": 1,
        "pageSize": 20,
    })
    t0 = r0.get("total", 0) if "_error" not in r0 else "ERROR"
    print(f"  全类型: total={t0}")

    # 带orderType=2只查销售出库
    print(f"\n  Step 2: outerId={test_outer_id} + orderType=2 销售出库...")
    r1 = await safe_query(client, method, {
        "outerId": test_outer_id,
        "orderType": 2,
        "pageNo": 1,
        "pageSize": 20,
    })
    t1 = r1.get("total", 0) if "_error" not in r1 else "ERROR"
    items = r1.get("list", []) if "_error" not in r1 else []
    print(f"  销售出库: total={t1}")

    if items:
        # 检查返回的数据是否都是销售出库
        types = set(item.get("orderType", "?") for item in items)
        print(f"  返回数据中的orderType值: {types}")
        if types == {2}:
            print("  ✅ 过滤生效，全部为销售出库")
        else:
            print(f"  ⚠️ 包含非销售出库类型: {types}")

    if isinstance(t0, int) and isinstance(t1, int) and t0 > 0:
        print(f"\n  结论: 全类型={t0}, 销售出库={t1}, 过滤比={t1/t0*100:.1f}%")


async def test_stock_in_out_pagination(client: KuaiMaiClient) -> None:
    """验证stock_in_out的分页和fetch_all需求"""
    print_section("stock_in_out 分页验证（fetch_all需求）")

    method = "erp.item.stock.in.out.list"
    test_outer_id = "SEVENTEENLSG01-01"

    # 查特定商品的销售出库
    r0 = await safe_query(client, method, {
        "outerId": test_outer_id,
        "orderType": 2,
        "pageNo": 1,
        "pageSize": 20,
    })

    if "_error" in r0 or not r0.get("list"):
        print(f"  ❌ 无法获取数据: {r0.get('_error', '列表为空')}")
        return

    total = r0.get("total", 0)
    page_items = len(r0["list"])
    print(f"  outerId={test_outer_id}, orderType=2")
    print(f"  pageSize=20时: total={total}, 本页={page_items}条")

    if total > 20:
        print(f"  ⚠️ total={total} > pageSize=20，如果不开fetch_all会丢失{total - 20}条记录！")
        print(f"  → 需要开启 fetch_all=True")
    else:
        print(f"  ✅ 当前数据量{total}≤20，不会截断（但应预防未来数据增长）")


async def main():
    print("=" * 60)
    print("  路由提示词审计 — API实测验证")
    print("=" * 60)

    client = KuaiMaiClient()

    # 按优先级执行测试
    await test_outstock_order_time_format(client)
    await test_outstock_order_status(client)
    await test_aftersale_sid(client)
    await test_stock_in_out_sku_outer_id(client)
    await test_stock_in_out_order_type(client)
    await test_stock_in_out_pagination(client)

    print("\n" + "=" * 60)
    print("  全部测试完成")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
