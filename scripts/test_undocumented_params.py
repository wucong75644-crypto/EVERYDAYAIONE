"""
测试快麦API未文档化参数是否有效

对比 "带未文档化参数" vs "不带" 的结果，判断参数是否被API识别。
策略：
1. 先用已知参数查一批数据（基准）
2. 再加上未文档化参数做过滤，看结果是否变化
"""

import asyncio
import json
import os
import sys

# 添加 backend 到 path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from services.kuaimai.client import KuaiMaiClient


async def test_param(
    client: KuaiMaiClient,
    method: str,
    base_params: dict,
    test_param_name: str,
    test_param_value: str,
    label: str,
) -> dict:
    """测试单个参数：对比有/无该参数时的结果"""
    print(f"\n{'='*60}")
    print(f"测试: {label}")
    print(f"API: {method}")
    print(f"参数: {test_param_name}={test_param_value}")
    print(f"{'='*60}")

    # 1. 不带该参数的基准查询
    try:
        result_without = await client.request_with_retry(method, {**base_params})
        total_without = result_without.get("total", result_without.get("totalCount", "N/A"))
        items_without = len(result_without.get("list", result_without.get("items", [])))
        print(f"  [不带参数] total={total_without}, 返回条数={items_without}")
    except Exception as e:
        print(f"  [不带参数] 错误: {e}")
        return {"label": label, "status": "base_error", "error": str(e)}

    # 2. 带上未文档化参数
    test_params = {**base_params, test_param_name: test_param_value}
    try:
        result_with = await client.request_with_retry(method, test_params)
        total_with = result_with.get("total", result_with.get("totalCount", "N/A"))
        items_with = len(result_with.get("list", result_with.get("items", [])))
        print(f"  [带参数]   total={total_with}, 返回条数={items_with}")
    except Exception as e:
        print(f"  [带参数]   错误: {e}")
        return {"label": label, "status": "test_error", "error": str(e)}

    # 3. 判断结果
    if total_without != total_with:
        status = "EFFECTIVE"
        print(f"  ✅ 参数有效! total从 {total_without} 变为 {total_with}")
    else:
        status = "NO_EFFECT"
        print(f"  ❌ 参数无效 (total未变化: {total_without})")

    return {
        "label": label,
        "method": method,
        "param": test_param_name,
        "value": test_param_value,
        "total_without": total_without,
        "total_with": total_with,
        "status": status,
    }


async def main():
    client = KuaiMaiClient()

    if not client.is_configured:
        print("错误: 快麦API未配置，请检查 .env 中的 KUAIMAI_* 环境变量")
        return

    results = []

    # ── 测试 1: item.list.query 的未文档化参数 ──
    # 先查一下有多少商品
    try:
        baseline = await client.request_with_retry(
            "item.list.query", {"pageNo": 1, "pageSize": 1}
        )
        total_items = baseline.get("total", 0)
        print(f"\n商品总数: {total_items}")

        # 取第一个商品的信息用于过滤测试
        items = baseline.get("items", [])
        if items:
            first_item = items[0]
            sample_title = first_item.get("title", "")
            sample_outer_id = first_item.get("outerId", "")
            sample_barcode = first_item.get("barcode", "")
            print(f"样本商品: title={sample_title}, outerId={sample_outer_id}, barcode={sample_barcode}")
        else:
            sample_title = "测试"
            sample_outer_id = "TEST_NOT_EXIST_12345"
            sample_barcode = "0000000000000"
    except Exception as e:
        print(f"基准查询失败: {e}")
        await client.close()
        return

    # 测试 title（keyword → title）
    if sample_title and len(sample_title) >= 2:
        keyword = sample_title[:2]  # 取前两个字
        r = await test_param(
            client, "item.list.query",
            {"pageNo": 1, "pageSize": 20},
            "title", keyword,
            f"item.list.query - title(关键词搜索: '{keyword}')"
        )
        results.append(r)

    # 测试 outerId
    if sample_outer_id:
        r = await test_param(
            client, "item.list.query",
            {"pageNo": 1, "pageSize": 20},
            "outerId", sample_outer_id,
            f"item.list.query - outerId(商家编码: '{sample_outer_id}')"
        )
        results.append(r)

    # 测试 barcode
    if sample_barcode:
        r = await test_param(
            client, "item.list.query",
            {"pageNo": 1, "pageSize": 20},
            "barcode", sample_barcode,
            f"item.list.query - barcode(条码: '{sample_barcode}')"
        )
        results.append(r)

    # 测试一个一定不存在的值，确认参数确实在过滤
    r = await test_param(
        client, "item.list.query",
        {"pageNo": 1, "pageSize": 20},
        "title", "ZZZZZ_不可能存在的商品名_99999",
        "item.list.query - title(不存在的关键词)"
    )
    results.append(r)

    # 测试 tagName
    r = await test_param(
        client, "item.list.query",
        {"pageNo": 1, "pageSize": 20},
        "tagName", "ZZZZZ_不存在的标签_99999",
        "item.list.query - tagName(不存在的标签)"
    )
    results.append(r)

    # ── 测试 2: stock.api.status.query 的 title 参数 ──
    if sample_title and len(sample_title) >= 2:
        r = await test_param(
            client, "stock.api.status.query",
            {"pageNo": 1, "pageSize": 20},
            "title", keyword,
            f"stock.api.status.query - title(关键词: '{keyword}')"
        )
        results.append(r)

    # ── 测试 3: erp.item.warehouse.list.get 的 sysItemId / warehouseId ──
    # 先取一个商品的 sysItemId
    try:
        detail_baseline = await client.request_with_retry(
            "item.list.query", {"pageNo": 1, "pageSize": 1}
        )
        test_items = detail_baseline.get("items", [])
        if test_items:
            sys_item_id = test_items[0].get("sysItemId", "")
            test_outer = test_items[0].get("outerId", "")
            if test_outer:
                r = await test_param(
                    client, "erp.item.warehouse.list.get",
                    {"outerId": test_outer, "pageNo": 1, "pageSize": 20},
                    "sysItemId", str(sys_item_id),
                    f"erp.item.warehouse.list.get - sysItemId({sys_item_id})"
                )
                results.append(r)
    except Exception as e:
        print(f"warehouse.list.get 测试跳过: {e}")

    # ── 测试 4: erp.trade.list.query 的未文档化参数 ──
    trade_base = {"pageNo": 1, "pageSize": 5, "timeType": 1}

    # 测试 outerId
    if sample_outer_id:
        r = await test_param(
            client, "erp.trade.list.query",
            trade_base,
            "outerId", sample_outer_id,
            f"erp.trade.list.query - outerId('{sample_outer_id}')"
        )
        results.append(r)

    # 测试 receiverName (用不存在的名字)
    r = await test_param(
        client, "erp.trade.list.query",
        trade_base,
        "receiverName", "ZZZZZ不可能的收件人99999",
        "erp.trade.list.query - receiverName(不存在的收件人)"
    )
    results.append(r)

    # 测试 shopName (用不存在的店铺名)
    r = await test_param(
        client, "erp.trade.list.query",
        trade_base,
        "shopName", "ZZZZZ不可能的店铺名99999",
        "erp.trade.list.query - shopName(不存在的店铺名)"
    )
    results.append(r)

    # 测试 warehouseName
    r = await test_param(
        client, "erp.trade.list.query",
        trade_base,
        "warehouseName", "ZZZZZ不可能的仓库名99999",
        "erp.trade.list.query - warehouseName(不存在的仓库名)"
    )
    results.append(r)

    # 测试 receiverPhone
    r = await test_param(
        client, "erp.trade.list.query",
        trade_base,
        "receiverPhone", "00000000000",
        "erp.trade.list.query - receiverPhone('00000000000')"
    )
    results.append(r)

    # ── 测试 5: erp.trade.outstock.simple.query 的未文档化参数 ──
    outstock_base = {"pageNo": 1, "pageSize": 5, "timeType": 1}

    r = await test_param(
        client, "erp.trade.outstock.simple.query",
        outstock_base,
        "shopName", "ZZZZZ不可能的店铺名99999",
        "erp.trade.outstock.simple.query - shopName(不存在的店铺名)"
    )
    results.append(r)

    r = await test_param(
        client, "erp.trade.outstock.simple.query",
        outstock_base,
        "warehouseName", "ZZZZZ不可能的仓库名99999",
        "erp.trade.outstock.simple.query - warehouseName(不存在的仓库名)"
    )
    results.append(r)

    # ── 汇总 ──
    await client.close()

    print("\n" + "=" * 70)
    print("汇总结果")
    print("=" * 70)
    print(f"{'参数':<50} {'状态':<15}")
    print("-" * 65)
    for r in results:
        label = r.get("label", "")[:48]
        status = r.get("status", "UNKNOWN")
        icon = "✅" if status == "EFFECTIVE" else "❌" if status == "NO_EFFECT" else "⚠️"
        print(f"{icon} {label:<48} {status}")

    # 保存详细结果
    output_path = os.path.join(os.path.dirname(__file__), "undocumented_params_results.json")
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(f"\n详细结果已保存: {output_path}")


if __name__ == "__main__":
    asyncio.run(main())
