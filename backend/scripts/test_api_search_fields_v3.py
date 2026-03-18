"""
V3: 补测 — 店铺name是否搜title？库存模糊度（pageSize≤100）？分销商state？
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


async def test_and_print(client, label, method, params, response_key=None):
    resp = await safe_query(client, method, params)
    if "_error" in resp:
        print(f"  {label:40s} → ERROR: {resp['_error'][:60]}")
        return []
    items = []
    for k in ([response_key] if response_key else []) + ["list", "data"]:
        if k and resp.get(k) and isinstance(resp[k], list):
            items = resp[k]
            break
    print(f"  {label:40s} → {len(items)}条")
    return items


async def main():
    client = KuaiMaiClient()

    # ════════════════════════════════════════════════════════
    # 1. 店铺 — name参数传title值？传不同值测试
    # ════════════════════════════════════════════════════════
    print("=" * 60)
    print("【1】店铺: name参数到底搜什么？")
    print("=" * 60)

    # 先拿真实数据
    resp = await safe_query(client, "erp.shop.list.query", {"pageNo": 1, "pageSize": 500})
    shops = resp.get("list") or []
    print(f"全量: {len(shops)}条")

    # 找几个不同类型的店铺
    samples = {}
    for s in shops:
        src = s.get("source", "")
        if src not in samples:
            samples[src] = s
    print(f"平台类型: {list(samples.keys())}")
    for src, s in samples.items():
        print(f"  {src}: title=「{s.get('title','')}」 nick=「{s.get('nick','')}」 name=「{s.get('name','')}」 shopId={s.get('shopId','')}")

    # 测试 name 参数传不同值
    for s in [shops[0]] + [v for v in samples.values()]:
        title = s.get("title", "")
        nick = s.get("nick", "")
        name = s.get("name", "")
        sid = s.get("shopId", "")
        src = s.get("source", "")

        print(f"\n  --- 店铺「{title}」({src}) ---")
        await test_and_print(client, f"name={name} (name字段值)", "erp.shop.list.query",
                             {"name": name, "pageNo": 1, "pageSize": 500})
        await test_and_print(client, f"name={title} (title字段值)", "erp.shop.list.query",
                             {"name": title, "pageNo": 1, "pageSize": 500})
        await test_and_print(client, f"id={sid} (shopId字段值)", "erp.shop.list.query",
                             {"id": str(sid), "pageNo": 1, "pageSize": 500})
        await test_and_print(client, f"nick={nick}", "erp.shop.list.query",
                             {"nick": nick, "pageNo": 1, "pageSize": 500})
        await test_and_print(client, f"shopId={sid}", "erp.shop.list.query",
                             {"shopId": str(sid), "pageNo": 1, "pageSize": 500})

        # 只测第一个就够说明问题
        break

    # ════════════════════════════════════════════════════════
    # 2. 库存模糊度（pageSize=50）
    # ════════════════════════════════════════════════════════
    print("\n" + "=" * 60)
    print("【2】库存编码模糊度测试（pageSize=50）")
    print("=" * 60)

    resp = await safe_query(client, "stock.api.status.query", {"pageNo": 1, "pageSize": 50})
    stocks = resp.get("stockStatusVoList") or []
    total = resp.get("total", 0)
    print(f"基线: {len(stocks)}条 (total={total})")

    if stocks:
        oid = stocks[0].get("mainOuterId", "")
        skuid = stocks[0].get("skuOuterId", "")

        if oid and len(oid) >= 4:
            half = len(oid) // 2
            print(f"\n  mainOuterId 测试 (基准=「{oid}」):")
            for label, val in [
                ("完整", oid),
                (f"前{half}字符", oid[:half]),
                ("前3字符", oid[:3]),
                ("前2字符", oid[:2]),
                (f"后{len(oid)-half}字符", oid[half:]),
                ("加多余", oid + "ZZ"),
            ]:
                await test_and_print(client, f"mainOuterId=「{val}」 ({label})",
                                     "stock.api.status.query",
                                     {"mainOuterId": val, "pageNo": 1, "pageSize": 50},
                                     "stockStatusVoList")

        if skuid and len(skuid) >= 4:
            base = skuid.split("-")[0] if "-" in skuid else skuid[:3]
            print(f"\n  skuOuterId 测试 (基准=「{skuid}」, base=「{base}」):")
            for label, val in [
                ("完整SKU", skuid),
                ("去后缀base", base),
                ("前3字符", skuid[:3]),
                ("加多余", skuid + "ZZ"),
            ]:
                await test_and_print(client, f"skuOuterId=「{val}」 ({label})",
                                     "stock.api.status.query",
                                     {"skuOuterId": val, "pageNo": 1, "pageSize": 50},
                                     "stockStatusVoList")

    # ════════════════════════════════════════════════════════
    # 3. 分销商 state 值测试
    # ════════════════════════════════════════════════════════
    print("\n" + "=" * 60)
    print("【3】分销商 state 参数测试")
    print("=" * 60)

    resp = await safe_query(client, "erp.distributor.list.query", {"pageNo": 1, "pageSize": 500})
    dists = resp.get("list") or []
    print(f"全量: {len(dists)}条")
    # 看有哪些showState值
    states = set()
    for d in dists:
        states.add(d.get("showState", ""))
        states.add(d.get("type", ""))
    print(f"showState值: {states}")

    for val in ["1", "2", "0"]:
        await test_and_print(client, f"state={val}",
                             "erp.distributor.list.query",
                             {"state": val, "pageNo": 1, "pageSize": 500})

    # 分销商name模糊度
    if dists:
        dname = ""
        for d in dists:
            n = d.get("distributorCompanyName", "")
            if n and len(n) >= 3:
                dname = n
                break
        if dname:
            print(f"\n  distributorName 模糊度 (基准=「{dname}」):")
            half = len(dname) // 2
            for label, val in [
                ("完整", dname),
                (f"前{half}字", dname[:half]),
                ("前2字", dname[:2]),
                ("后2字", dname[-2:]),
                ("加多余", dname + "ZZ"),
            ]:
                await test_and_print(client, f"distributorName=「{val}」 ({label})",
                                     "erp.distributor.list.query",
                                     {"distributorName": val, "pageNo": 1, "pageSize": 500})

    # ════════════════════════════════════════════════════════
    # 4. 客户 nick 模糊度 + 用另一个更长的名字测
    # ════════════════════════════════════════════════════════
    print("\n" + "=" * 60)
    print("【4】客户 nick 模糊度")
    print("=" * 60)

    resp = await safe_query(client, "erp.query.customers.list", {"pageNo": 1, "pageSize": 50})
    custs = resp.get("list") or []
    # 找名字最长的
    longest_nick = ""
    for c in custs:
        n = c.get("nick", "")
        if len(n) > len(longest_nick):
            longest_nick = n
    print(f"最长nick: 「{longest_nick}」(len={len(longest_nick)})")

    if longest_nick and len(longest_nick) >= 2:
        for label, val in [
            ("完整", longest_nick),
            ("前1字", longest_nick[0]),
            ("加多余", longest_nick + "ZZ"),
        ]:
            await test_and_print(client, f"nick=「{val}」 ({label})",
                                 "erp.query.customers.list",
                                 {"nick": val, "pageNo": 1, "pageSize": 50})

    # ════════════════════════════════════════════════════════
    # 5. 仓库库存 — 换一个有库存数据的编码测试
    # ════════════════════════════════════════════════════════
    print("\n" + "=" * 60)
    print("【5】仓库库存 — 用库存状态中确认有数据的编码")
    print("=" * 60)
    if stocks:
        # 找库存不为0的
        for s in stocks:
            avail = s.get("totalAvailableStock", 0) or s.get("sellableNum", 0)
            oid = s.get("mainOuterId", "")
            skuid = s.get("skuOuterId", "")
            if oid:
                print(f"  测试 outerId=「{oid}」 skuOuterId=「{skuid}」 avail={avail}")
                items = await test_and_print(client, f"outerId=「{oid}」",
                                             "erp.item.warehouse.list.get",
                                             {"outerId": oid, "pageNo": 1, "pageSize": 100})
                if items:
                    print(f"    第1条: {json.dumps({k:v for k,v in items[0].items() if isinstance(v,(str,int,float)) and len(str(v))<50}, ensure_ascii=False)[:200]}")
                items2 = await test_and_print(client, f"skuOuterId=「{skuid}」",
                                              "erp.item.warehouse.list.get",
                                              {"skuOuterId": skuid, "pageNo": 1, "pageSize": 100})
                if items2:
                    print(f"    第1条: {json.dumps({k:v for k,v in items2[0].items() if isinstance(v,(str,int,float)) and len(str(v))<50}, ensure_ascii=False)[:200]}")
                # 只测3个编码
                break

    await client.close()
    print(f"\n{'═' * 60}")
    print("✅ 补测完成")
    print(f"{'═' * 60}")


if __name__ == "__main__":
    asyncio.run(main())
