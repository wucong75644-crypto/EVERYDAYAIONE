"""
系统性测试快麦API搜索字段
方法：先拉数据 → 取真实值 → 用每个字段名作为入参回查 → 看哪些字段真正生效
"""

import asyncio
import json
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.kuaimai.client import KuaiMaiClient

# 跳过这些字段（明显不是搜索参数）
SKIP_FIELDS = {
    "created", "modified", "gmtCreate", "gmtModified", "deadline",
    "picPath", "skuPicPath", "pic", "url", "imgUrl", "image",
    "traceId", "success", "total", "pageNo", "pageSize",
}


async def safe_query(client, method, biz_params):
    try:
        return await client.request_with_retry(method=method, biz_params=biz_params)
    except Exception as e:
        return {"_error": str(e)}


def extract_list(resp, known_keys=None):
    """从响应中提取列表数据"""
    if known_keys:
        for k in known_keys:
            items = resp.get(k)
            if isinstance(items, list) and items:
                return items, k
    for k, v in resp.items():
        if isinstance(v, list) and v and isinstance(v[0], dict):
            return v, k
    return [], None


def get_searchable_fields(record):
    """提取可能是搜索字段的 (field_name, value) 对"""
    candidates = []
    for k, v in record.items():
        if k in SKIP_FIELDS:
            continue
        if k.startswith("_"):
            continue
        # 字符串值且不像URL/路径
        if isinstance(v, str) and v and len(v) < 100:
            if not v.startswith("http") and not v.startswith("/"):
                candidates.append((k, v))
        # 数字ID也试一下（可能是id筛选）
        elif isinstance(v, (int, float)) and v and "id" in k.lower():
            candidates.append((k, str(int(v))))
    return candidates


async def test_api_fields(client, title, method, extra_params=None, response_keys=None):
    """测试单个API的所有可能搜索字段"""
    print(f"\n{'═' * 70}")
    print(f"  {title}")
    print(f"  method: {method}")
    print(f"{'═' * 70}")

    # 1. 先拉数据
    params = {"pageNo": 1, "pageSize": 20}
    if extra_params:
        params.update(extra_params)
    resp = await safe_query(client, method, params)
    if "_error" in resp:
        print(f"  ❌ 拉数据失败: {resp['_error'][:80]}")
        return {}

    items, resp_key = extract_list(resp, response_keys)
    print(f"  响应keys: {list(resp.keys())} | 列表在: {resp_key} | 条数: {len(items)}")

    if not items:
        print(f"  ⚠️ 无数据")
        return {}

    # 2. 打印第一条的所有字段
    sample = items[0]
    print(f"  第1条字段名: {list(sample.keys())}")
    # 打印前3条的关键字段
    for i, item in enumerate(items[:3]):
        fields = {k: v for k, v in item.items()
                  if isinstance(v, str) and v and len(v) < 60
                  and not v.startswith("http")}
        print(f"  #{i}: {json.dumps(fields, ensure_ascii=False)[:200]}")

    # 3. 逐个字段尝试搜索
    candidates = get_searchable_fields(sample)
    print(f"\n  待测字段({len(candidates)}个):")

    results = {}  # field_name -> (works, count, value_used)

    for field_name, field_value in candidates:
        search_params = {field_name: field_value, "pageNo": 1, "pageSize": 20}
        if extra_params:
            search_params.update(extra_params)
        resp2 = await safe_query(client, method, search_params)

        if "_error" in resp2:
            err = resp2["_error"]
            # 签名错误可能是参数名本身导致的
            if "签名" in err:
                mark = "⚠️签名错"
            else:
                mark = f"⚠️{err[:40]}"
            results[field_name] = ("error", 0, field_value)
            print(f"    {mark:6s}  {field_name:25s} = 「{field_value[:30]}」")
            continue

        items2, _ = extract_list(resp2, [resp_key] if resp_key else None)
        count = len(items2)

        # 判断是否生效：查到的结果中包含我们搜索的值
        found_exact = False
        if items2:
            for item in items2:
                val = item.get(field_name, "")
                if str(val) == str(field_value):
                    found_exact = True
                    break

        if found_exact and count < 20:
            # 查到了且结果数少于全量，说明字段确实起了筛选作用
            mark = "✅生效"
            results[field_name] = ("works", count, field_value)
        elif found_exact and count >= 20:
            # 查到了但结果数和不带参数一样多，可能字段被忽略
            mark = "🔍待定"
            results[field_name] = ("maybe", count, field_value)
        elif count == 0:
            mark = "❌无结果"
            results[field_name] = ("no_result", 0, field_value)
        else:
            mark = "🔍有结果但没匹配到原值"
            results[field_name] = ("mismatch", count, field_value)

        print(f"    {mark:6s}  {field_name:25s} = 「{field_value[:30]}」 → {count}条")

    # 4. 总结
    working = [k for k, v in results.items() if v[0] == "works"]
    not_working = [k for k, v in results.items() if v[0] == "no_result"]
    print(f"\n  📊 总结: ✅生效={working or '无'} | ❌无效={not_working or '无'}")

    return results


async def test_all():
    client = KuaiMaiClient()

    # ── 基础信息类 ──
    await test_api_fields(client, "【仓库列表】", "erp.warehouse.list.query",
                          response_keys=["list"])

    await test_api_fields(client, "【店铺列表】", "erp.shop.list.query",
                          response_keys=["list"])

    await test_api_fields(client, "【客户列表】", "erp.query.customers.list",
                          response_keys=["list"])

    await test_api_fields(client, "【分销商列表】", "erp.distributor.list.query",
                          response_keys=["list"])

    # ── 库存类 ──
    await test_api_fields(client, "【库存状态】", "stock.api.status.query",
                          response_keys=["stockStatusVoList"])

    await test_api_fields(client, "【仓库库存】", "erp.item.warehouse.list.get",
                          response_keys=["list"])

    # ── 商品类 ──
    # SKU列表需要outerId，先拿一个真实的
    resp = await safe_query(client, "stock.api.status.query", {"pageNo": 1, "pageSize": 5})
    stocks = resp.get("stockStatusVoList") or []
    if stocks:
        oid = stocks[0].get("mainOuterId", "")
        if oid:
            await test_api_fields(client, f"【SKU列表】(outerId={oid})",
                                  "erp.item.sku.list.get",
                                  extra_params={"outerId": oid},
                                  response_keys=["itemSkus"])

    await test_api_fields(client, "【商品详情】", "item.single.get",
                          extra_params={"outerId": oid} if stocks else None,
                          response_keys=["item"])

    # ── 采购类 ──
    await test_api_fields(client, "【采购单列表】", "erp.purchase.order.list",
                          response_keys=["list"])

    # ── 订单类 ──
    await test_api_fields(client, "【订单列表】", "erp.trade.list.get",
                          response_keys=["tradeOrders"])

    # ── 出入库记录 ──
    await test_api_fields(client, "【出入库记录】", "erp.item.stock.in.out.list",
                          response_keys=["list"])

    await client.close()
    print(f"\n{'═' * 70}")
    print("✅ 全部API搜索字段测试完成")
    print(f"{'═' * 70}")


if __name__ == "__main__":
    asyncio.run(test_all())
