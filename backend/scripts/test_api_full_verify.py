"""
全量验证：用真实系统数据逐个测试每个registry param_map中的参数
要求：每个API先拿全量数据，从中提取真实值，再用真实值回查验证参数是否生效
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


def extract_items(resp, response_key=None):
    """从响应中提取列表数据"""
    for k in ([response_key] if response_key else []) + ["list", "data", "items",
              "stockStatusVoList", "itemSkus", "sellerCats", "classifies",
              "itemOuterIdInfos", "suppliers"]:
        if k and resp.get(k) and isinstance(resp[k], list):
            return resp[k]
    return []


async def verify_param(client, method, param_name, param_value, baseline_count,
                       response_key=None, label=""):
    """验证单个参数是否生效"""
    params = {param_name: param_value, "pageNo": 1, "pageSize": 50}
    resp = await safe_query(client, method, params)

    if "_error" in resp:
        status = "ERROR"
        count = 0
        detail = resp["_error"][:60]
    else:
        items = extract_items(resp, response_key)
        count = len(items)
        total = resp.get("total", count)
        if count == 0 and total == 0:
            status = "0条"
            detail = "无结果(参数不识别或无匹配数据)"
        elif total < baseline_count:
            status = f"✅{total}条"
            detail = f"有过滤效果(baseline={baseline_count})"
        elif total == baseline_count:
            status = f"⚠️{total}条"
            detail = f"=baseline，可能被忽略"
        else:
            status = f"?{total}条"
            detail = f"多于baseline={baseline_count}"

    val_str = str(param_value)[:25]
    print(f"  {status:12s} {param_name:25s} = 「{val_str}」  {label:15s} {detail}")
    return status


async def main():
    client = KuaiMaiClient()

    # ════════════════════════════════════════════════════════════
    # 预取所有需要的真实数据
    # ════════════════════════════════════════════════════════════
    print("=" * 70)
    print("预取真实系统数据...")
    print("=" * 70)

    # --- basic ---
    wh_resp = await safe_query(client, "erp.warehouse.list.query",
                               {"pageNo": 1, "pageSize": 500})
    warehouses = extract_items(wh_resp)

    shop_resp = await safe_query(client, "erp.shop.list.query",
                                 {"pageNo": 1, "pageSize": 500})
    shops = extract_items(shop_resp)

    cust_resp = await safe_query(client, "erp.query.customers.list",
                                 {"pageNo": 1, "pageSize": 50})
    customers = extract_items(cust_resp)
    cust_total = cust_resp.get("total", len(customers))

    dist_resp = await safe_query(client, "erp.distributor.list.query",
                                 {"pageNo": 1, "pageSize": 500})
    distributors = extract_items(dist_resp)

    # --- product/stock ---
    stock_resp = await safe_query(client, "stock.api.status.query",
                                  {"pageNo": 1, "pageSize": 50})
    stocks = extract_items(stock_resp, "stockStatusVoList")
    stock_total = stock_resp.get("total", len(stocks))

    prod_resp = await safe_query(client, "item.list.query",
                                 {"pageNo": 1, "pageSize": 50})
    products = extract_items(prod_resp, "items")
    prod_total = prod_resp.get("total", len(products))

    # --- warehouse ops ---
    alloc_resp = await safe_query(client, "erp.allocate.task.query",
                                  {"pageNo": 1, "pageSize": 50})
    allocates = extract_items(alloc_resp)
    alloc_total = alloc_resp.get("total", len(allocates))

    alloc_in_resp = await safe_query(client, "allocate.in.task.query",
                                     {"pageNo": 1, "pageSize": 50})
    alloc_ins = extract_items(alloc_in_resp)
    alloc_in_total = alloc_in_resp.get("total", len(alloc_ins))

    alloc_out_resp = await safe_query(client, "allocate.out.task.query",
                                      {"pageNo": 1, "pageSize": 50})
    alloc_outs = extract_items(alloc_out_resp)
    alloc_out_total = alloc_out_resp.get("total", len(alloc_outs))

    other_in_resp = await safe_query(client, "other.in.order.query",
                                     {"pageNo": 1, "pageSize": 50})
    other_ins = extract_items(other_in_resp)
    other_in_total = other_in_resp.get("total", len(other_ins))

    other_out_resp = await safe_query(client, "other.out.order.query",
                                      {"pageNo": 1, "pageSize": 50})
    other_outs = extract_items(other_out_resp)
    other_out_total = other_out_resp.get("total", len(other_outs))

    inv_resp = await safe_query(client, "inventory.sheet.query",
                                {"pageNo": 1, "pageSize": 50})
    inventories = extract_items(inv_resp)
    inv_total = inv_resp.get("total", len(inventories))

    proc_resp = await safe_query(client, "erp.stock.product.order.query",
                                 {"pageNo": 1, "pageSize": 50})
    processes = extract_items(proc_resp)
    proc_total = proc_resp.get("total", len(processes))

    # --- purchase ---
    po_resp = await safe_query(client, "purchase.order.query",
                               {"pageNo": 1, "pageSize": 50})
    purchases = extract_items(po_resp)
    po_total = po_resp.get("total", len(purchases))

    pr_resp = await safe_query(client, "purchase.return.query",
                               {"pageNo": 1, "pageSize": 50})
    returns = extract_items(pr_resp)
    pr_total = pr_resp.get("total", len(returns))

    we_resp = await safe_query(client, "warehouse.entry.list.query",
                               {"pageNo": 1, "pageSize": 50})
    entries = extract_items(we_resp)
    we_total = we_resp.get("total", len(entries))

    shelf_resp = await safe_query(client, "erp.purchase.shelf.query",
                                  {"pageNo": 1, "pageSize": 50})
    shelves = extract_items(shelf_resp)
    shelf_total = shelf_resp.get("total", len(shelves))

    # 虚拟仓
    vw_resp = await safe_query(client, "erp.virtual.warehouse.query",
                               {"pageNo": 1, "pageSize": 500})
    vwarehouses = extract_items(vw_resp, "list")
    vw_total = len(vwarehouses)

    print(f"  仓库={len(warehouses)} 店铺={len(shops)} 客户={cust_total} 分销商={len(distributors)}")
    print(f"  库存={stock_total} 商品={prod_total} 虚拟仓={vw_total}")
    print(f"  调拨={alloc_total} 调拨入={alloc_in_total} 调拨出={alloc_out_total}")
    print(f"  其他入={other_in_total} 其他出={other_out_total} 盘点={inv_total} 加工={proc_total}")
    print(f"  采购={po_total} 采退={pr_total} 收货={we_total} 上架={shelf_total}")

    # 选第2个样本（不同于之前测试的第1个）
    def pick(lst, idx=1):
        return lst[idx] if len(lst) > idx else (lst[0] if lst else {})

    # ════════════════════════════════════════════════════════════
    # 1. basic.py — 仓库
    # ════════════════════════════════════════════════════════════
    print(f"\n{'═' * 70}")
    print(f"  【仓库】baseline={len(warehouses)}")
    print(f"{'═' * 70}")
    w = pick(warehouses)
    if w:
        wname = w.get("name", "")
        wcode = w.get("code", "")
        wid = str(w.get("id", ""))
        print(f"  样本: name=「{wname}」 code=「{wcode}」 id={wid}")
        await verify_param(client, "erp.warehouse.list.query", "name", wname,
                           len(warehouses), label="精确?")
        if wname and len(wname) >= 2:
            await verify_param(client, "erp.warehouse.list.query", "name", wname[:2],
                               len(warehouses), label="前2字模糊?")
        await verify_param(client, "erp.warehouse.list.query", "code", wcode,
                           len(warehouses), label="精确?")
        await verify_param(client, "erp.warehouse.list.query", "id", wid,
                           len(warehouses), label="ID精确?")

    # ════════════════════════════════════════════════════════════
    # 2. basic.py — 店铺（用不同的店铺样本）
    # ════════════════════════════════════════════════════════════
    print(f"\n{'═' * 70}")
    print(f"  【店铺】baseline={len(shops)}")
    print(f"{'═' * 70}")
    # 找title不同于"蓝恩集美优品"的店铺
    s = None
    for shop in shops:
        t = shop.get("title", "")
        if t and t != "蓝恩集美优品" and len(t) >= 3:
            s = shop
            break
    if not s:
        s = pick(shops, 2)
    if s:
        stitle = s.get("title", "")
        sname = s.get("name", "")
        sid = str(s.get("shopId", ""))
        sshort = s.get("shortName", "")
        print(f"  样本: title=「{stitle}」 name=「{sname}」 shopId={sid} shortName=「{sshort}」")
        await verify_param(client, "erp.shop.list.query", "name", stitle,
                           len(shops), label="name传title")
        if stitle and len(stitle) >= 2:
            await verify_param(client, "erp.shop.list.query", "name", stitle[:2],
                               len(shops), label="前2字模糊?")
        await verify_param(client, "erp.shop.list.query", "id", sid,
                           len(shops), label="id参数")
        if sshort:
            await verify_param(client, "erp.shop.list.query", "shortName", sshort,
                               len(shops), label="shortName")

    # ════════════════════════════════════════════════════════════
    # 3. basic.py — 客户（用不同样本）
    # ════════════════════════════════════════════════════════════
    print(f"\n{'═' * 70}")
    print(f"  【客户】baseline={cust_total}")
    print(f"{'═' * 70}")
    c = pick(customers, 2)
    if c:
        cname = c.get("name", "")
        cnick = c.get("nick", "")
        ccode = c.get("code", "")
        clevel = str(c.get("level", ""))
        cstatus = str(c.get("enableStatus", ""))
        print(f"  样本: name=「{cname}」 nick=「{cnick}」 code=「{ccode}」 level={clevel} enableStatus={cstatus}")
        await verify_param(client, "erp.query.customers.list", "name", cname,
                           cust_total, label="name(已知不生效)")
        await verify_param(client, "erp.query.customers.list", "nick", cnick,
                           cust_total, label="nick精确?")
        await verify_param(client, "erp.query.customers.list", "code", ccode,
                           cust_total, label="code精确?")
        await verify_param(client, "erp.query.customers.list", "level", clevel,
                           cust_total, label="level枚举")
        await verify_param(client, "erp.query.customers.list", "enableStatus", cstatus,
                           cust_total, label="status枚举")

    # ════════════════════════════════════════════════════════════
    # 4. basic.py — 分销商（用不同样本）
    # ════════════════════════════════════════════════════════════
    print(f"\n{'═' * 70}")
    print(f"  【分销商】baseline={len(distributors)}")
    print(f"{'═' * 70}")
    d = pick(distributors, 2)
    if d:
        dname = d.get("distributorCompanyName", "")
        did = str(d.get("distributorCompanyId", ""))
        print(f"  样本: name=「{dname}」 id={did}")
        await verify_param(client, "erp.distributor.list.query", "distributorName",
                           dname, len(distributors), label="完整name")
        if dname and len(dname) >= 2:
            await verify_param(client, "erp.distributor.list.query", "distributorName",
                               dname[:2], len(distributors), label="前2字模糊?")
        await verify_param(client, "erp.distributor.list.query", "state", "1",
                           len(distributors), label="state=1(所有)")
        await verify_param(client, "erp.distributor.list.query", "state", "2",
                           len(distributors), label="state=2(有效)")
        await verify_param(client, "erp.distributor.list.query",
                           "distributorCompanyIds", did, len(distributors), label="按ID")

    # ════════════════════════════════════════════════════════════
    # 5. product.py — 库存 status参数
    # ════════════════════════════════════════════════════════════
    print(f"\n{'═' * 70}")
    print(f"  【库存状态】baseline={stock_total}")
    print(f"{'═' * 70}")
    st = pick(stocks, 3)
    if st:
        oid = st.get("mainOuterId", "")
        skuid = st.get("skuOuterId", "")
        print(f"  样本: mainOuterId=「{oid}」 skuOuterId=「{skuid}」")
        await verify_param(client, "stock.api.status.query", "mainOuterId", oid,
                           stock_total, "stockStatusVoList", "outer_id精确?")
        if oid and len(oid) >= 3:
            await verify_param(client, "stock.api.status.query", "mainOuterId",
                               oid[:3], stock_total, "stockStatusVoList", "前3字模糊?")
        await verify_param(client, "stock.api.status.query", "skuOuterId", skuid,
                           stock_total, "stockStatusVoList", "sku精确?")
        # itemType
        await verify_param(client, "stock.api.status.query", "itemType", "0",
                           stock_total, "stockStatusVoList", "itemType=0")
        await verify_param(client, "stock.api.status.query", "itemType", "1",
                           stock_total, "stockStatusVoList", "itemType=1套件")
        # stockStatuses
        await verify_param(client, "stock.api.status.query", "stockStatuses", "1",
                           stock_total, "stockStatusVoList", "status=1正常")
        await verify_param(client, "stock.api.status.query", "stockStatuses", "3",
                           stock_total, "stockStatusVoList", "status=3无货")

    # ════════════════════════════════════════════════════════════
    # 6. product.py — multicode_query
    # ════════════════════════════════════════════════════════════
    print(f"\n{'═' * 70}")
    print(f"  【multicode_query】编码类型测试")
    print(f"{'═' * 70}")
    # 用另一个商品的编码测试
    if len(stocks) > 5:
        for idx in [5, 4, 3, 2]:
            test_oid = stocks[idx].get("mainOuterId", "")
            test_skuid = stocks[idx].get("skuOuterId", "")
            if test_oid:
                print(f"  样本{idx}: outer=「{test_oid}」 sku=「{test_skuid}」")
                await verify_param(client, "erp.item.multicode.query", "code",
                                   test_oid, 999, "list", "主编码")
                if test_skuid:
                    await verify_param(client, "erp.item.multicode.query", "code",
                                       test_skuid, 999, "list", "规格编码")

    # ════════════════════════════════════════════════════════════
    # 7. product.py — 虚拟仓
    # ════════════════════════════════════════════════════════════
    print(f"\n{'═' * 70}")
    print(f"  【虚拟仓】baseline={vw_total}")
    print(f"{'═' * 70}")
    vw = pick(vwarehouses)
    if vw:
        vname = vw.get("name", "")
        print(f"  样本: name=「{vname}」")
        await verify_param(client, "erp.virtual.warehouse.query", "name", vname,
                           vw_total, "list", "完整name")
        if vname and len(vname) >= 2:
            await verify_param(client, "erp.virtual.warehouse.query", "name",
                               vname[:2], vw_total, "list", "前2字模糊?")

    # ════════════════════════════════════════════════════════════
    # 8. warehouse.py — 调拨单
    # ════════════════════════════════════════════════════════════
    print(f"\n{'═' * 70}")
    print(f"  【调拨单】baseline={alloc_total}")
    print(f"{'═' * 70}")
    a = pick(allocates)
    if a:
        acode = a.get("code", "")
        astatus = a.get("status", "")
        alabel = a.get("labelName", "")
        print(f"  样本: code=「{acode}」 status=「{astatus}」 labelName=「{alabel}」")
        await verify_param(client, "erp.allocate.task.query", "code", acode,
                           alloc_total, label="code精确?")
        if astatus:
            await verify_param(client, "erp.allocate.task.query", "status", astatus,
                               alloc_total, label=f"status={astatus}")
        if alabel:
            await verify_param(client, "erp.allocate.task.query", "labelName", alabel,
                               alloc_total, label="labelName")
    # 测试文档里的status枚举值
    for sv in ["REATED", "OUTING", "AUDITED", "ALLOCATE", "FINISHED", "CANCELED"]:
        await verify_param(client, "erp.allocate.task.query", "status", sv,
                           alloc_total, label=f"枚举{sv}")

    # ════════════════════════════════════════════════════════════
    # 9. warehouse.py — 调拨入库单
    # ════════════════════════════════════════════════════════════
    print(f"\n{'═' * 70}")
    print(f"  【调拨入库】baseline={alloc_in_total}")
    print(f"{'═' * 70}")
    ai = pick(alloc_ins)
    if ai:
        ai_code = ai.get("code", "")
        ai_status = ai.get("status", "")
        print(f"  样本: code=「{ai_code}」 status=「{ai_status}」")
        await verify_param(client, "allocate.in.task.query", "code", ai_code,
                           alloc_in_total, label="code精确?")
        if ai_status:
            await verify_param(client, "allocate.in.task.query", "status", ai_status,
                               alloc_in_total, label=f"status={ai_status}")
    for sv in ["CREATED", "ALLOCATE_OUT", "SHELVED", "FINISHED", "CANCELED"]:
        await verify_param(client, "allocate.in.task.query", "status", sv,
                           alloc_in_total, label=f"枚举{sv}")

    # ════════════════════════════════════════════════════════════
    # 10. warehouse.py — 调拨出库单
    # ════════════════════════════════════════════════════════════
    print(f"\n{'═' * 70}")
    print(f"  【调拨出库】baseline={alloc_out_total}")
    print(f"{'═' * 70}")
    ao = pick(alloc_outs)
    if ao:
        ao_code = ao.get("code", "")
        ao_status = ao.get("status", "")
        print(f"  样本: code=「{ao_code}」 status=「{ao_status}」")
        await verify_param(client, "allocate.out.task.query", "code", ao_code,
                           alloc_out_total, label="code精确?")
        if ao_status:
            await verify_param(client, "allocate.out.task.query", "status", ao_status,
                               alloc_out_total, label=f"status={ao_status}")
    for sv in ["CREATED", "OUTING", "FINISHED", "CANCELED"]:
        await verify_param(client, "allocate.out.task.query", "status", sv,
                           alloc_out_total, label=f"枚举{sv}")
    # timeType
    for tv in ["create", "out", "gm_modified"]:
        await verify_param(client, "allocate.out.task.query", "timeType", tv,
                           alloc_out_total, label=f"timeType={tv}")

    # ════════════════════════════════════════════════════════════
    # 11. warehouse.py — 其他入库单
    # ════════════════════════════════════════════════════════════
    print(f"\n{'═' * 70}")
    print(f"  【其他入库】baseline={other_in_total}")
    print(f"{'═' * 70}")
    oi = pick(other_ins)
    if oi:
        oi_code = oi.get("code", "")
        oi_status = oi.get("status", "")
        print(f"  样本: code=「{oi_code}」 status=「{oi_status}」")
        await verify_param(client, "other.in.order.query", "code", oi_code,
                           other_in_total, label="code精确?")
    for sv in ["NOT_FINISH", "FINISHED", "CLOSED"]:
        await verify_param(client, "other.in.order.query", "status", sv,
                           other_in_total, label=f"枚举{sv}")

    # ════════════════════════════════════════════════════════════
    # 12. warehouse.py — 其他出库单
    # ════════════════════════════════════════════════════════════
    print(f"\n{'═' * 70}")
    print(f"  【其他出库】baseline={other_out_total}")
    print(f"{'═' * 70}")
    oo = pick(other_outs)
    if oo:
        oo_code = oo.get("code", "")
        oo_status = oo.get("status", "")
        print(f"  样本: code=「{oo_code}」 status=「{oo_status}」(type={type(oo_status).__name__})")
        await verify_param(client, "other.out.order.query", "code", oo_code,
                           other_out_total, label="code精确?")
    for sv in ["0", "1", "3", "4", "5"]:
        await verify_param(client, "other.out.order.query", "status", sv,
                           other_out_total, label=f"枚举{sv}")

    # ════════════════════════════════════════════════════════════
    # 13. warehouse.py — 盘点单
    # ════════════════════════════════════════════════════════════
    print(f"\n{'═' * 70}")
    print(f"  【盘点单】baseline={inv_total}")
    print(f"{'═' * 70}")
    iv = pick(inventories)
    if iv:
        iv_code = iv.get("code", "")
        iv_status = iv.get("status", "")
        print(f"  样本: code=「{iv_code}」 status=「{iv_status}」(type={type(iv_status).__name__})")
        await verify_param(client, "inventory.sheet.query", "code", iv_code,
                           inv_total, label="code精确?")
    for sv in ["1", "2", "3", "4"]:
        await verify_param(client, "inventory.sheet.query", "status", sv,
                           inv_total, label=f"枚举{sv}")

    # ════════════════════════════════════════════════════════════
    # 14. warehouse.py — 加工单
    # ════════════════════════════════════════════════════════════
    print(f"\n{'═' * 70}")
    print(f"  【加工单】baseline={proc_total}")
    print(f"{'═' * 70}")
    pr = pick(processes)
    if pr:
        pr_code = pr.get("code", "")
        pr_status = pr.get("status", "")
        pr_type = str(pr.get("type", ""))
        print(f"  样本: code=「{pr_code}」 status=「{pr_status}」 type={pr_type}")
        await verify_param(client, "erp.stock.product.order.query", "code", pr_code,
                           proc_total, label="code精确?")
        await verify_param(client, "erp.stock.product.order.query", "type", pr_type,
                           proc_total, label=f"type={pr_type}")
    for sv in ["WAIT_VERIFY", "WAIT_PRODUCT", "PRODUCING", "FINISHED", "CLOSED"]:
        await verify_param(client, "erp.stock.product.order.query", "status", sv,
                           proc_total, label=f"枚举{sv}")

    # ════════════════════════════════════════════════════════════
    # 15. purchase.py — 采购单
    # ════════════════════════════════════════════════════════════
    print(f"\n{'═' * 70}")
    print(f"  【采购单】baseline={po_total}")
    print(f"{'═' * 70}")
    po = pick(purchases, 2)
    if po:
        po_code = po.get("code", "")
        po_status = po.get("status", "")
        print(f"  样本: code=「{po_code}」 status=「{po_status}」")
        await verify_param(client, "purchase.order.query", "code", po_code,
                           po_total, label="code精确?")
    for sv in ["WAIT_VERIFY", "VERIFYING", "GOODS_NOT_ARRIVED",
               "GOODS_PART_ARRIVED", "FINISHED", "GOODS_CLOSED"]:
        await verify_param(client, "purchase.order.query", "status", sv,
                           po_total, label=f"枚举{sv}")
    # timeType
    for tv in ["1", "2"]:
        await verify_param(client, "purchase.order.query", "timeType", tv,
                           po_total, label=f"timeType={tv}")

    # ════════════════════════════════════════════════════════════
    # 16. purchase.py — 采退单
    # ════════════════════════════════════════════════════════════
    print(f"\n{'═' * 70}")
    print(f"  【采退单】baseline={pr_total}")
    print(f"{'═' * 70}")
    rt = pick(returns)
    if rt:
        rt_code = rt.get("code", "")
        rt_status = rt.get("status", "")
        rt_fin = rt.get("financeStatus", "")
        print(f"  样本: code=「{rt_code}」 status=「{rt_status}」(type={type(rt_status).__name__}) financeStatus=「{rt_fin}」")
        await verify_param(client, "purchase.return.query", "code", rt_code,
                           pr_total, label="code精确?")
    for sv in ["0", "1", "3", "4", "5"]:
        await verify_param(client, "purchase.return.query", "status", sv,
                           pr_total, label=f"枚举{sv}")
    for fv in ["WAIT_FINANCE", "FINANCED"]:
        await verify_param(client, "purchase.return.query", "financeStatus", fv,
                           pr_total, label=f"finance={fv}")
    for tv in ["1", "2"]:
        await verify_param(client, "purchase.return.query", "timeType", tv,
                           pr_total, label=f"timeType={tv}")

    # ════════════════════════════════════════════════════════════
    # 17. purchase.py — 收货单
    # ════════════════════════════════════════════════════════════
    print(f"\n{'═' * 70}")
    print(f"  【收货单】baseline={we_total}")
    print(f"{'═' * 70}")
    we = pick(entries)
    if we:
        we_code = we.get("code", "")
        we_status = we.get("status", "")
        we_fin = we.get("financeStatus", "")
        print(f"  样本: code=「{we_code}」 status=「{we_status}」 financeStatus=「{we_fin}」")
        await verify_param(client, "warehouse.entry.list.query", "code", we_code,
                           we_total, label="code精确?")
    for sv in ["WAIT_IN", "PART_IN", "FINISHED", "CLOSE"]:
        await verify_param(client, "warehouse.entry.list.query", "status", sv,
                           we_total, label=f"枚举{sv}")
    for fv in ["WAIT_FINANCE", "FINANCED"]:
        await verify_param(client, "warehouse.entry.list.query", "financeStatus", fv,
                           we_total, label=f"finance={fv}")
    for tv in ["1", "2"]:
        await verify_param(client, "warehouse.entry.list.query", "timeType", tv,
                           we_total, label=f"timeType={tv}")

    # ════════════════════════════════════════════════════════════
    # 18. purchase.py — 上架单
    # ════════════════════════════════════════════════════════════
    print(f"\n{'═' * 70}")
    print(f"  【上架单】baseline={shelf_total}")
    print(f"{'═' * 70}")
    sh = pick(shelves)
    if sh:
        sh_status = sh.get("status", "")
        sh_we = sh.get("weCode", "")
        print(f"  样本: status=「{sh_status}」(type={type(sh_status).__name__}) weCode=「{sh_we}」")
    for sv in ["WAIT_SHELF", "FINISHED", "CLOSE"]:
        await verify_param(client, "erp.purchase.shelf.query", "status", sv,
                           shelf_total, label=f"枚举{sv}")
    for tv in ["1", "2"]:
        await verify_param(client, "erp.purchase.shelf.query", "timeType", tv,
                           shelf_total, label=f"timeType={tv}")

    # ════════════════════════════════════════════════════════════
    # 19. product.py — 商品标签（模糊搜索？）
    # ════════════════════════════════════════════════════════════
    print(f"\n{'═' * 70}")
    print(f"  【商品标签】模糊搜索测试")
    print(f"{'═' * 70}")
    tag_resp = await safe_query(client, "erp.item.tag.list",
                                {"pageNo": 1, "pageSize": 500})
    tags = extract_items(tag_resp)
    print(f"  baseline={len(tags)}")
    tg = pick(tags)
    if tg:
        tname = tg.get("name", "")
        print(f"  样本: name=「{tname}」")
        await verify_param(client, "erp.item.tag.list", "name", tname,
                           len(tags), label="完整name")
        if tname and len(tname) >= 2:
            await verify_param(client, "erp.item.tag.list", "name", tname[0],
                               len(tags), label="前1字模糊?")

    await client.close()
    print(f"\n{'═' * 70}")
    print("✅ 全量验证完成")
    print(f"{'═' * 70}")


if __name__ == "__main__":
    asyncio.run(main())
