"""
V2: 针对性测试 registry param_map 映射的API参数名 + 可能的替代参数名
重点：找出每个API真正生效的搜索参数
"""

import asyncio
import json
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.kuaimai.client import KuaiMaiClient


async def safe_query(client, method, biz_params):
    try:
        return await client.request_with_retry(method=method, biz_params=biz_params)
    except Exception as e:
        return {"_error": str(e)}


async def test_param(client, method, param_name, param_value, baseline_count,
                     response_key=None):
    """测试单个参数是否生效，和baseline对比"""
    params = {param_name: param_value, "pageNo": 1, "pageSize": 200}
    resp = await safe_query(client, method, params)
    if "_error" in resp:
        return "error", 0, resp["_error"][:60]

    items = []
    for k in ([response_key] if response_key else []) + ["list", "data", "items"]:
        if k and resp.get(k) and isinstance(resp[k], list):
            items = resp[k]
            break

    count = len(items)
    if count == 0:
        return "no_result", 0, ""
    elif count < baseline_count:
        return "filtered", count, ""
    else:
        return "ignored", count, "(可能被忽略，结果数=baseline)"


async def test_api(client, title, method, response_key, test_cases,
                   extra_base_params=None):
    """
    test_cases: [(param_name, param_value, description), ...]
    """
    print(f"\n{'═' * 70}")
    print(f"  {title} | method={method}")
    print(f"{'═' * 70}")

    # 先拿baseline（无过滤条件）
    base_params = {"pageNo": 1, "pageSize": 200}
    if extra_base_params:
        base_params.update(extra_base_params)
    resp = await safe_query(client, method, base_params)
    if "_error" in resp:
        print(f"  ❌ 基线查询失败: {resp['_error'][:80]}")
        return

    items = []
    for k in ([response_key] if response_key else []) + ["list", "data", "items"]:
        if k and resp.get(k) and isinstance(resp[k], list):
            items = resp[k]
            break
    baseline = len(items)
    total = resp.get("total", baseline)
    print(f"  基线: {baseline}条 (total={total})")

    if baseline == 0:
        print("  ⚠️ 无数据")
        return

    # 逐个测试参数
    for param_name, param_value, desc in test_cases:
        status, count, note = await test_param(
            client, method, param_name, param_value, baseline, response_key
        )
        if status == "error":
            mark = "⚠️ERR"
            detail = note
        elif status == "no_result":
            mark = "❌ 0条"
            detail = "API不认此参数名"
        elif status == "filtered":
            mark = f"✅ {count}条"
            detail = f"有过滤效果 (baseline={baseline})"
        else:
            mark = f"🔍{count}条"
            detail = "和baseline相同，可能被忽略"

        print(f"  {mark:10s} {param_name:25s} = 「{str(param_value)[:25]}」  {desc:20s} {detail}")


async def test_all():
    client = KuaiMaiClient()

    # ── 先获取真实数据用于测试 ──
    print("获取测试数据...")
    # 仓库
    wh_resp = await safe_query(client, "erp.warehouse.list.query", {"pageNo": 1, "pageSize": 500})
    warehouses = wh_resp.get("list") or []
    wh_sample = warehouses[0] if warehouses else {}
    print(f"  仓库: {len(warehouses)}条, sample={json.dumps({k: v for k, v in wh_sample.items() if isinstance(v, str) and len(str(v)) < 30}, ensure_ascii=False)[:200]}")

    # 店铺
    shop_resp = await safe_query(client, "erp.shop.list.query", {"pageNo": 1, "pageSize": 500})
    shops = shop_resp.get("list") or []
    # 找一个有中文名的店铺
    shop_sample = {}
    for s in shops:
        title = s.get("title", "")
        if title and not title.startswith("pdd") and len(title) > 2:
            shop_sample = s
            break
    if not shop_sample and shops:
        shop_sample = shops[0]
    print(f"  店铺: {len(shops)}条, sample title=「{shop_sample.get('title','')}」 nick=「{shop_sample.get('nick','')}」 name=「{shop_sample.get('name','')}」 shopId={shop_sample.get('shopId','')} id={shop_sample.get('id','')} source={shop_sample.get('source','')}")

    # 客户
    cust_resp = await safe_query(client, "erp.query.customers.list", {"pageNo": 1, "pageSize": 50})
    customers = cust_resp.get("list") or []
    cust_sample = customers[0] if customers else {}
    print(f"  客户: {len(customers)}条, name=「{cust_sample.get('name','')}」 nick=「{cust_sample.get('nick','')}」 code=「{cust_sample.get('code','')}」")

    # 分销商
    dist_resp = await safe_query(client, "erp.distributor.list.query", {"pageNo": 1, "pageSize": 500})
    distributors = dist_resp.get("list") or []
    dist_sample = distributors[0] if distributors else {}
    print(f"  分销商: {len(distributors)}条, 字段keys={list(dist_sample.keys())}")
    for d in distributors[:3]:
        print(f"    distributorCompanyName=「{d.get('distributorCompanyName','')}」 distributorCompanyId={d.get('distributorCompanyId','')} id={d.get('id','')} showState={d.get('showState','')}")

    # 库存
    stock_resp = await safe_query(client, "stock.api.status.query", {"pageNo": 1, "pageSize": 30})
    stocks = stock_resp.get("stockStatusVoList") or []
    stock_sample = stocks[0] if stocks else {}

    # ════════════════════════════════════════════════════════
    # 1. 店铺 — 最需要搞清楚
    # ════════════════════════════════════════════════════════
    if shop_sample:
        s = shop_sample
        await test_api(client, "【店铺列表】", "erp.shop.list.query", "list", [
            # registry里的映射
            ("name", s.get("name", ""), "registry映射"),
            ("id", str(s.get("shopId", "")), "registry(shop_id→id)"),
            ("shortName", s.get("shortName", ""), "registry映射"),
            # 尝试响应字段名
            ("title", s.get("title", ""), "响应字段title"),
            ("nick", s.get("nick", ""), "响应字段nick"),
            ("shopId", str(s.get("shopId", "")), "响应字段shopId"),
            ("source", s.get("source", ""), "响应字段source"),
            ("groupName", s.get("groupName", ""), "响应字段groupName"),
            # 猜测可能的参数名
            ("shopName", s.get("title", ""), "猜测shopName"),
            ("shopNick", s.get("nick", ""), "猜测shopNick"),
            ("keyword", s.get("title", ""), "猜测keyword"),
            ("active", str(s.get("active", "")), "响应字段active"),
            ("state", str(s.get("state", "")), "响应字段state"),
        ])

    # ════════════════════════════════════════════════════════
    # 2. 客户 — name不生效需要找替代
    # ════════════════════════════════════════════════════════
    if cust_sample:
        c = cust_sample
        await test_api(client, "【客户列表】", "erp.query.customers.list", "list", [
            # registry映射
            ("name", c.get("name", ""), "registry映射"),
            ("nick", c.get("nick", ""), "registry映射"),
            ("code", c.get("code", ""), "registry映射"),
            ("level", str(c.get("level", "")), "registry映射"),
            ("enableStatus", "1", "registry(status→enableStatus)"),
            # 猜测
            ("cmName", c.get("name", ""), "猜测cmName"),
            ("cmNick", c.get("nick", ""), "猜测cmNick"),
            ("cmCode", c.get("code", ""), "猜测cmCode"),
            ("keyword", c.get("name", ""), "猜测keyword"),
            ("id", str(c.get("id", "")), "响应字段id"),
        ])

    # ════════════════════════════════════════════════════════
    # 3. 分销商 — 全部被忽略需要找正确参数
    # ════════════════════════════════════════════════════════
    if dist_sample:
        d = dist_sample
        await test_api(client, "【分销商列表】", "erp.distributor.list.query", "list", [
            # registry映射
            ("distributorName", d.get("distributorCompanyName", ""), "registry映射"),
            ("state", str(d.get("showState", "")), "registry(state→state)"),
            ("distributorCompanyIds", str(d.get("distributorCompanyId", "")), "registry映射"),
            # 响应字段名直接用
            ("distributorCompanyName", d.get("distributorCompanyName", ""), "响应字段名"),
            ("distributorCompanyId", str(d.get("distributorCompanyId", "")), "响应字段名"),
            ("showState", str(d.get("showState", "")), "响应字段名"),
            ("distributorLevel", str(d.get("distributorLevel", "")), "响应字段名"),
            ("id", str(d.get("id", "")), "响应字段id"),
            # 猜测
            ("name", d.get("distributorCompanyName", ""), "猜测name"),
            ("keyword", d.get("distributorCompanyName", ""), "猜测keyword"),
        ])

    # ════════════════════════════════════════════════════════
    # 4. 仓库 — 确认name/code/id，再测模糊
    # ════════════════════════════════════════════════════════
    if wh_sample:
        w = wh_sample
        wname = w.get("name", "")
        await test_api(client, "【仓库列表】确认字段", "erp.warehouse.list.query", "list", [
            ("name", wname, "registry映射"),
            ("code", w.get("code", ""), "registry映射"),
            ("id", str(w.get("id", "")), "registry映射"),
        ])

        # 对生效字段做模糊测试
        if wname and len(wname) >= 3:
            half = len(wname) // 2
            await test_api(client, "【仓库name模糊度】", "erp.warehouse.list.query", "list", [
                ("name", wname, "完整"),
                ("name", wname[:half], f"前{half}字"),
                ("name", wname[half:], f"后{len(wname)-half}字"),
                ("name", wname[0], "前1字"),
                ("name", wname[-1], "后1字"),
                ("name", wname + "ZZ", "加多余"),
            ])

    # ════════════════════════════════════════════════════════
    # 5. 库存 — 确认后做编码模糊测试
    # ════════════════════════════════════════════════════════
    if stock_sample:
        oid = stock_sample.get("mainOuterId", "")
        skuid = stock_sample.get("skuOuterId", "")
        if oid:
            half = max(2, len(oid) // 2)
            await test_api(client, "【库存mainOuterId模糊度】", "stock.api.status.query",
                           "stockStatusVoList", [
                ("mainOuterId", oid, "完整"),
                ("mainOuterId", oid[:half], f"前{half}字符"),
                ("mainOuterId", oid[:3], "前3字符"),
                ("mainOuterId", oid[half:], f"后半"),
                ("mainOuterId", oid + "ZZ", "加多余"),
            ])
        if skuid:
            base = skuid.split("-")[0] if "-" in skuid else skuid[:3]
            await test_api(client, "【库存skuOuterId模糊度】", "stock.api.status.query",
                           "stockStatusVoList", [
                ("skuOuterId", skuid, "完整"),
                ("skuOuterId", base, "去后缀base"),
                ("skuOuterId", skuid[:3], "前3字符"),
                ("skuOuterId", skuid + "ZZ", "加多余"),
            ])

    # ════════════════════════════════════════════════════════
    # 6. 出入库记录 — 需要编码参数
    # ════════════════════════════════════════════════════════
    if stock_sample:
        oid = stock_sample.get("mainOuterId", "")
        if oid:
            await test_api(client, "【出入库记录】", "erp.item.stock.in.out.list", None, [
                ("outerId", oid, "registry映射"),
                ("mainOuterId", oid, "猜测mainOuterId"),
            ], extra_base_params={"outerId": oid})

    # ════════════════════════════════════════════════════════
    # 7. 仓库库存 — 需要编码参数
    # ════════════════════════════════════════════════════════
    if stock_sample:
        oid = stock_sample.get("mainOuterId", "")
        skuid = stock_sample.get("skuOuterId", "")
        if oid:
            print(f"\n{'═' * 70}")
            print(f"  【仓库库存】测试outerId vs skuOuterId")
            print(f"{'═' * 70}")
            # 用outerId
            resp1 = await safe_query(client, "erp.item.warehouse.list.get",
                                     {"outerId": oid, "pageNo": 1, "pageSize": 100})
            if "_error" not in resp1:
                items1 = resp1.get("list") or []
                print(f"  outerId=「{oid}」 → {len(items1)}条")
                if items1:
                    print(f"  第1条keys: {list(items1[0].keys())[:10]}")
                    print(f"  第1条: {json.dumps({k:v for k,v in items1[0].items() if isinstance(v,(str,int,float)) and len(str(v))<50}, ensure_ascii=False)[:200]}")
            else:
                print(f"  outerId=「{oid}」 → ERROR: {resp1['_error'][:80]}")

            # 用skuOuterId
            resp2 = await safe_query(client, "erp.item.warehouse.list.get",
                                     {"skuOuterId": skuid, "pageNo": 1, "pageSize": 100})
            if "_error" not in resp2:
                items2 = resp2.get("list") or []
                print(f"  skuOuterId=「{skuid}」 → {len(items2)}条")
            else:
                print(f"  skuOuterId=「{skuid}」 → ERROR: {resp2['_error'][:80]}")

            # 用部分编码
            if len(oid) > 3:
                resp3 = await safe_query(client, "erp.item.warehouse.list.get",
                                         {"outerId": oid[:3], "pageNo": 1, "pageSize": 100})
                if "_error" not in resp3:
                    items3 = resp3.get("list") or []
                    print(f"  outerId=「{oid[:3]}」(前3字符) → {len(items3)}条")
                else:
                    print(f"  outerId=「{oid[:3]}」 → ERROR: {resp3['_error'][:80]}")

    await client.close()
    print(f"\n{'═' * 70}")
    print("✅ 全部测试完成")
    print(f"{'═' * 70}")


if __name__ == "__main__":
    asyncio.run(test_all())
