#!/usr/bin/env python3
"""
完整 dump viperp 销售主题报表的所有字段。

涉及两个接口：
  - /report/sale/dimensions/finance/list           ← 明细数据（多行）
  - /report/sale/dimensions/finance/getFinanceAmount ← 汇总（一行）

用法：
    cd backend && KUAIMAI_WEB_COOKIE='...' venv/bin/python scripts/dump_viperp_fields.py
"""

import json
import os
import sys
import time
from collections import defaultdict
from pathlib import Path

import requests


COOKIE = os.environ.get("KUAIMAI_WEB_COOKIE", "")
OUTPUT_DIR = Path(__file__).parent.parent.parent / "tmp"

LIST_URL = "https://erp.superboss.cc/report/sale/dimensions/finance/list"
AMOUNT_URL = "https://erp.superboss.cc/report/sale/dimensions/finance/getFinanceAmount"

# 共用 payload（按 user 给的 cURL 复刻）
PAYLOAD = {
    "pageNo": "1",
    "pageSize": "50",
    "pageId": "1123",
    "queryFlag": "shop",
    "startTime": "1779552000000",
    "endTime": "1780156799999",
    "vipSign": "false",
    "sysStatus": "sys_consign",
    "sellerFlags": "",
    "tradeTypes": "",
    "excludeTradeTypes": "",
    "containTagIds": "",
    "exceptTagIds": "",
    "containType": "1",
    "exceptType": "1",
    "subTagIdsQueryFlag": "false",
    "userIds": "",
    "shopUkList": "",
    "warehouseIds": "",
    "isAccurate": "",
    "itemFlag": "0",
    "tradeSysStatus": "",
    "scalping": "",
    "sysSkuIds": "",
    "sysItemIds": "",
    "outerIds": "",
    "numIids": "",
    "platformItemNames": "",
    "platformSkuIdFlag": "0",
    "platformSkuIds": "",
    "cids": "",
    "itemBrandIds": "",
    "skuBrandIds": "",
    "containTradeOut": "true",
    "onlyTradeOut": "false",
    "containNonConsign": "true",
    "containCancel": "false",
    "destIds": "",
    "sourceIds": "",
    "taobaoIds": "",
    "supplyIds": "",
    "buyerNicks": "",
    "buyerNickSelectAll": "false",
    "expressIds": "",
    "logisticCompanyIds": "",
    "templateIds": "",
    "showProcessItemDetail": "0",
    "showGroupItemDetail": "0",
    "isOuterIdFuzzy": "0",
    "shipper": "",
    "queryByCake": "",
    "matchFlag": "1",
    "virtualFlag": "1",
    "excludeWorkOrderCloseAndNoneRefundWarehouse": "false",
    "showSuit": "0",
    "asTypes": "",
    "createdStartTime": "",
    "createdEndTime": "",
    "buyerNick": "",
    "classifyIds": "",
    "classifySkuIds": "",
    "itemTagIds": "",
    "itemTagQueryType": "0",
    "afterSaleTimeType": "finish",
    "authorType": "name",
    "authorText": "",
    "sysConsigned": "",
    "definedSearch": "",
    "skuCids": "",
    "categoryFilterType": "0",
    "provinceNames": "",
    "cityNames": "",
    "areaNames": "",
    "provinceCityAreaFilter": "{}",
    "street": "",
    "itemAttribute": "",
    "showSysItem": "0",
    "shouldSort": "false",
    "sortField": "",
    "sortType": "",
}

HEADERS = {
    "accept": "application/json, text/plain, */*",
    "accept-language": "zh-CN,zh;q=0.9",
    "bx-v": "2.5.11",
    "companyid": "65109",
    "content-type": "application/x-www-form-urlencoded",
    "module-path": "/report/sale_multidimension_finance_next/",
    "origin": "https://erp.superboss.cc",
    "referer": "https://erp.superboss.cc/index.html",
    "trackid": f"trackid{int(time.time() * 1000)}_99997",
    "user-agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/148.0.0.0 Safari/537.36",
    "cookie": COOKIE,
}


def infer_value_type(val) -> str:
    if val is None:
        return "null"
    if isinstance(val, bool):
        return "bool"
    if isinstance(val, int):
        return "integer"
    if isinstance(val, float):
        return "numeric"
    if isinstance(val, str):
        s = val.strip()
        if not s:
            return "string(empty)"
        try:
            time.strptime(s, "%Y-%m-%d")
            return "date"
        except ValueError:
            pass
        try:
            float(s)
            if "." in s:
                return "numeric(str)"
            return "integer(str)"
        except ValueError:
            return "string"
    if isinstance(val, list):
        return f"array[{len(val)}]"
    if isinstance(val, dict):
        return "object"
    return type(val).__name__


def fetch(url: str, label: str) -> dict:
    payload = dict(PAYLOAD)
    payload["api_name"] = f"report_sale_dimensions_finance_{label}"

    print(f"调用 {label}: POST {url}")
    resp = requests.post(url, headers=HEADERS, data=payload, timeout=30)
    print(f"  HTTP {resp.status_code}")
    return resp.json()


def dump_fields(label: str, data: dict) -> dict | None:
    """从响应里挖出单行字段。返回第一行 dict 或 None。"""
    # 顶层结构
    print(f"\n【{label} 顶层 keys】: {list(data.keys())[:10]}")

    # 找 list 数据所在路径
    rows = None
    for path in (("data", "list"), ("data", "rows"), ("data",), ("rows",)):
        cur = data
        ok = True
        for k in path:
            if isinstance(cur, dict) and k in cur:
                cur = cur[k]
            else:
                ok = False
                break
        if ok and isinstance(cur, list) and cur:
            rows = cur
            print(f"  → 数据在路径 {'.'.join(path)}（{len(cur)} 行）")
            break
        if ok and isinstance(cur, dict) and cur:
            # getFinanceAmount 可能是 dict 不是 list
            rows = [cur]
            print(f"  → 数据在路径 {'.'.join(path)}（单 dict）")
            break

    if not rows:
        print(f"  ⚠️ {label} 找不到数据")
        return None
    return rows[0]


def write_fields_file(label: str, sample: dict) -> None:
    """写字段清单文件。"""
    OUTPUT_DIR.mkdir(exist_ok=True)
    fields_file = OUTPUT_DIR / f"viperp_{label}_fields.txt"
    json_file = OUTPUT_DIR / f"viperp_{label}_sample.json"

    with fields_file.open("w", encoding="utf-8") as f:
        f.write(f"# viperp {label} - 完整字段清单（共 {len(sample)} 个）\n\n")
        f.write(f"{'序号':<5}{'字段名':<40}{'类型':<18}样例值\n")
        f.write("─" * 100 + "\n")
        for i, (k, v) in enumerate(sorted(sample.items()), 1):
            vtype = infer_value_type(v)
            sample_str = str(v)[:50] if not isinstance(v, (dict, list)) else f"<{type(v).__name__}>"
            f.write(f"{i:<5}{k:<40}{vtype:<18}{sample_str}\n")
    print(f"📝 字段清单: {fields_file}")

    json_file.write_text(json.dumps(sample, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"📝 样例 JSON: {json_file}")


def main() -> int:
    if not COOKIE:
        print("❌ 缺 KUAIMAI_WEB_COOKIE 环境变量")
        return 2

    # 1. list 接口（明细）
    print("\n" + "=" * 70)
    print("【接口 1: list - 明细数据】")
    print("=" * 70)
    list_data = fetch(LIST_URL, "list")
    list_sample = dump_fields("list", list_data)
    if list_sample:
        write_fields_file("list", list_sample)
        print(f"  ✅ list 字段数: {len(list_sample)}")

    # 2. getFinanceAmount 接口（汇总）
    print("\n" + "=" * 70)
    print("【接口 2: getFinanceAmount - 汇总】")
    print("=" * 70)
    amount_data = fetch(AMOUNT_URL, "getFinanceAmount")
    amount_sample = dump_fields("getFinanceAmount", amount_data)
    if amount_sample:
        write_fields_file("amount", amount_sample)
        print(f"  ✅ amount 字段数: {len(amount_sample)}")

    # 3. 字段交集 / 差集
    if list_sample and amount_sample:
        list_keys = set(list_sample.keys())
        amount_keys = set(amount_sample.keys())
        common = list_keys & amount_keys
        list_only = list_keys - amount_keys
        amount_only = amount_keys - list_keys
        print("\n" + "=" * 70)
        print("【字段对比】")
        print("=" * 70)
        print(f"  公共字段: {len(common)} 个")
        print(f"  list 独有: {len(list_only)} → {sorted(list_only)[:10]}{'...' if len(list_only) > 10 else ''}")
        print(f"  amount 独有: {len(amount_only)} → {sorted(amount_only)[:10]}{'...' if len(amount_only) > 10 else ''}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
