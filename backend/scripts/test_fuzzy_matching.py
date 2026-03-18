"""
测试快麦API模糊匹配行为（V2：先看真实数据结构，再用真实值回查）
"""

import asyncio
import json
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.kuaimai.client import KuaiMaiClient


async def safe_query(client, method, biz_params):
    """安全调用，出错返回空dict"""
    try:
        return await client.request_with_retry(method=method, biz_params=biz_params)
    except Exception as e:
        return {"_error": str(e)}


def truncate(obj, max_len=200):
    """截断JSON输出"""
    s = json.dumps(obj, ensure_ascii=False)
    return s[:max_len] + "..." if len(s) > max_len else s


async def test_search(client, title, method, search_field, query, response_key=None):
    """测试单次搜索，返回(items, error)"""
    params = {search_field: query, "pageNo": 1, "pageSize": 100}
    resp = await safe_query(client, method, params)
    if "_error" in resp:
        return None, resp["_error"]
    # 尝试多种response key
    items = []
    if response_key:
        items = resp.get(response_key) or []
    if not items:
        for key in ["list", "data", "items"]:
            items = resp.get(key) or []
            if items:
                break
    return items, None


async def run_fuzzy_test(client, title, method, search_field, base_value,
                         response_key=None, display_fields=None):
    """用真实值做模糊测试"""
    print(f"\n  --- 测试 {search_field} 模糊行为 (基准值=「{base_value}」) ---")

    if len(base_value) < 2:
        print(f"  ⚠️ 基准值太短({len(base_value)}字符)，跳过部分匹配测试")
        # 只测完整匹配
        cases = [("完整值", base_value)]
    else:
        half = max(1, len(base_value) // 2)
        cases = [
            ("完整值", base_value),
            ("前1字符", base_value[0]),
            ("前半", base_value[:half]),
            ("后半", base_value[half:]),
            ("后1字符", base_value[-1]),
        ]
        if len(base_value) > 3:
            cases.append(("中间部分", base_value[1:-1]))
        cases.append(("值+多余", base_value + "ZZ"))

    for label, query in cases:
        items, err = await test_search(client, title, method, search_field, query, response_key)
        if err:
            print(f"    ⚠️  {label:10s} 「{query}」 → ERROR: {err[:80]}")
        else:
            # 检查基准值是否在结果中
            found = False
            if display_fields:
                for item in (items or []):
                    for df in display_fields:
                        if item.get(df, "") == base_value:
                            found = True
                            break
            count = len(items or [])
            mark = "✅" if found else ("🔍" if count > 0 else "❌")
            vals = []
            if display_fields and items:
                for item in items[:3]:
                    v = " | ".join(f"{df}={item.get(df, '')}" for df in display_fields)
                    vals.append(v)
            print(f"    {mark} {label:10s} 「{query}」 → {count}条 {' / '.join(vals) if vals else ''}")


async def test_fuzzy():
    client = KuaiMaiClient()

    # ══════════════════════════════════════════════════════════
    # 1. 仓库
    # ══════════════════════════════════════════════════════════
    print("=" * 70)
    print("【1】仓库 erp.warehouse.list.query")
    print("=" * 70)
    resp = await safe_query(client, "erp.warehouse.list.query", {"pageNo": 1, "pageSize": 500})
    print(f"原始响应keys: {list(resp.keys())}")
    warehouses = resp.get("list") or []
    print(f"总数: {len(warehouses)}")
    if warehouses:
        print(f"第1条完整字段: {truncate(warehouses[0], 300)}")
        print("所有仓库:")
        for w in warehouses:
            print(f"  name=「{w.get('name','')}」 code=「{w.get('code','')}」 id={w.get('id','')}")

        # 用真实值回查
        for w in warehouses:
            wname = w.get("name", "")
            if wname and len(wname) >= 3:
                await run_fuzzy_test(client, "仓库", "erp.warehouse.list.query",
                                     "name", wname, display_fields=["name", "code"])
                break

    # ══════════════════════════════════════════════════════════
    # 2. 店铺
    # ══════════════════════════════════════════════════════════
    print("\n" + "=" * 70)
    print("【2】店铺 erp.shop.list.query")
    print("=" * 70)
    resp = await safe_query(client, "erp.shop.list.query", {"pageNo": 1, "pageSize": 500})
    print(f"原始响应keys: {list(resp.keys())}")
    shops = resp.get("list") or []
    print(f"总数: {len(shops)}")
    if shops:
        print(f"第1条完整字段: {truncate(shops[0], 400)}")
        # 打印前10条
        for s in shops[:10]:
            print(f"  name=「{s.get('name','')}」 shortName=「{s.get('shortName','')}」 id={s.get('id','')}")

        # 先用完整name精确回查，确认search field是否生效
        test_shop = shops[0]
        sname = test_shop.get("name", "")
        print(f"\n  >> 先验证: 用完整name「{sname}」能否回查到")
        items, err = await test_search(client, "", "erp.shop.list.query", "name", sname)
        if err:
            print(f"  >> ERROR: {err[:80]}")
        else:
            print(f"  >> 结果: {len(items or [])}条")
            if items:
                print(f"  >> 第1条: name={items[0].get('name','')}")

        # 如果完整name能查到，再做模糊测试
        if items:
            await run_fuzzy_test(client, "店铺", "erp.shop.list.query",
                                 "name", sname, display_fields=["name", "shortName"])
        else:
            print("  >> 完整name都查不到，说明name字段可能不是搜索字段，或API字段名不对")
            # 尝试用shortName
            sshort = test_shop.get("shortName", "")
            if sshort:
                print(f"  >> 尝试用shortName「{sshort}」回查")
                items2, err2 = await test_search(client, "", "erp.shop.list.query", "shortName", sshort)
                if not err2 and items2:
                    await run_fuzzy_test(client, "店铺shortName", "erp.shop.list.query",
                                         "shortName", sshort, display_fields=["name", "shortName"])

    # ══════════════════════════════════════════════════════════
    # 3. 客户
    # ══════════════════════════════════════════════════════════
    print("\n" + "=" * 70)
    print("【3】客户 erp.query.customers.list")
    print("=" * 70)
    resp = await safe_query(client, "erp.query.customers.list", {"pageNo": 1, "pageSize": 50})
    print(f"原始响应keys: {list(resp.keys())}")
    # 试多种key
    customers = []
    for key in ["list", "data", "customers", "items"]:
        customers = resp.get(key) or []
        if customers:
            print(f"数据在key=「{key}」")
            break
    if not customers and isinstance(resp, dict):
        # 可能整个resp就是列表？或者有其他结构
        print(f"未找到列表数据，完整响应: {truncate(resp, 500)}")

    print(f"总数: {len(customers)}")
    if customers:
        print(f"第1条完整字段: {truncate(customers[0], 400)}")
        for c in customers[:5]:
            # 打印所有字段名
            print(f"  字段keys: {list(c.keys())[:15]}")
            print(f"  name=「{c.get('name','')}」 cmName=「{c.get('cmName','')}」 "
                  f"nick=「{c.get('nick','')}」 code=「{c.get('code','')}」 "
                  f"cmCode=「{c.get('cmCode','')}」")

        # 用真实值测试 - 先找到正确的字段名
        ct = customers[0]
        for try_field, try_val in [
            ("name", ct.get("name", "")),
            ("name", ct.get("cmName", "")),
            ("nick", ct.get("nick", "")),
            ("code", ct.get("code", "")),
            ("code", ct.get("cmCode", "")),
        ]:
            if not try_val:
                continue
            print(f"\n  >> 验证: field={try_field} value=「{try_val}」")
            items, err = await test_search(client, "", "erp.query.customers.list", try_field, try_val)
            if err:
                print(f"  >> ERROR: {err[:80]}")
            else:
                print(f"  >> 结果: {len(items or [])}条")
                if items:
                    print(f"  >> ✅ 找到了！用 field={try_field} 可查")
                    await run_fuzzy_test(client, "客户", "erp.query.customers.list",
                                         try_field, try_val,
                                         display_fields=["name", "cmName", "nick", "code"])
                    break

    # ══════════════════════════════════════════════════════════
    # 4. 库存 mainOuterId / skuOuterId
    # ══════════════════════════════════════════════════════════
    print("\n" + "=" * 70)
    print("【4】库存 stock.api.status.query")
    print("=" * 70)
    resp = await safe_query(client, "stock.api.status.query", {"pageNo": 1, "pageSize": 30})
    print(f"原始响应keys: {list(resp.keys())}")
    stocks = resp.get("stockStatusVoList") or []
    print(f"总数: {len(stocks)}")
    if stocks:
        print(f"第1条完整字段: {truncate(stocks[0], 400)}")
        for s in stocks[:10]:
            print(f"  mainOuterId=「{s.get('mainOuterId','')}」 "
                  f"skuOuterId=「{s.get('skuOuterId','')}」 "
                  f"title=「{s.get('title','')}」")

        # 用真实mainOuterId回查
        for s in stocks:
            oid = s.get("mainOuterId", "")
            if oid and len(oid) >= 4:
                await run_fuzzy_test(client, "库存mainOuterId", "stock.api.status.query",
                                     "mainOuterId", oid,
                                     response_key="stockStatusVoList",
                                     display_fields=["mainOuterId", "skuOuterId"])
                break

        # 用真实skuOuterId回查
        for s in stocks:
            sku = s.get("skuOuterId", "")
            if sku and len(sku) >= 4:
                await run_fuzzy_test(client, "库存skuOuterId", "stock.api.status.query",
                                     "skuOuterId", sku,
                                     response_key="stockStatusVoList",
                                     display_fields=["mainOuterId", "skuOuterId"])
                break

    # ══════════════════════════════════════════════════════════
    # 5. 分销商 name
    # ══════════════════════════════════════════════════════════
    print("\n" + "=" * 70)
    print("【5】分销商 erp.distributor.list.query")
    print("=" * 70)
    resp = await safe_query(client, "erp.distributor.list.query", {"pageNo": 1, "pageSize": 100})
    print(f"原始响应keys: {list(resp.keys())}")
    distributors = resp.get("list") or resp.get("data") or []
    print(f"总数: {len(distributors)}")
    if distributors:
        print(f"第1条完整字段: {truncate(distributors[0], 400)}")
        for d in distributors[:5]:
            print(f"  字段keys: {list(d.keys())[:10]}")
            # 打印所有可能的name字段
            for k, v in d.items():
                if "name" in k.lower() or "Name" in k:
                    print(f"    {k}=「{v}」")

    # ══════════════════════════════════════════════════════════
    # 6. 商品列表 (用item.list.get看outerId搜索行为)
    # ══════════════════════════════════════════════════════════
    print("\n" + "=" * 70)
    print("【6】商品列表 erp.item.list.get (用outerIds查)")
    print("=" * 70)
    if stocks:
        # 用库存里拿到的真实编码
        test_oid = ""
        for s in stocks:
            oid = s.get("mainOuterId", "")
            if oid and len(oid) >= 4:
                test_oid = oid
                break
        if test_oid:
            print(f"用真实编码「{test_oid}」查商品列表")
            resp = await safe_query(client, "erp.item.list.get",
                                    {"outerIds": test_oid, "pageNo": 1, "pageSize": 20})
            items = resp.get("items") or resp.get("list") or []
            print(f"结果: {len(items)}条")
            if items:
                print(f"第1条: {truncate(items[0], 300)}")

            # 测试部分编码
            half = test_oid[:len(test_oid) // 2]
            print(f"\n用部分编码「{half}」查商品列表")
            resp2 = await safe_query(client, "erp.item.list.get",
                                     {"outerIds": half, "pageNo": 1, "pageSize": 20})
            items2 = resp2.get("items") or resp2.get("list") or []
            print(f"结果: {len(items2)}条")

    await client.close()
    print("\n" + "=" * 70)
    print("✅ 全部测试完成")
    print("=" * 70)


if __name__ == "__main__":
    asyncio.run(test_fuzzy())
