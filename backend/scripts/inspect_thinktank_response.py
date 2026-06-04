#!/usr/bin/env python3
"""
拉一次真实 JSON 响应，打印结构，对比我之前设计的表字段。

用法（用你之前粘贴的 cookie）：
    cd backend && KUAIMAI_WEB_COOKIE='...' venv/bin/python scripts/inspect_thinktank_response.py
"""

import json
import os
import sys
import time

import requests


COOKIE = os.environ.get("KUAIMAI_WEB_COOKIE", "")

URL = "https://erp.superboss.cc/kmzk/profit/report/shop"

PAYLOAD = {
    "api_name": "ttps%3A__erp.superboss.cc_kmzk_profit_report_shop",
    "groupTypeSum": "",
    "sysStatus": "1",
    "startTime": "1779552000000",
    "endTime": "1780156799000",
    "shopUniIds": "",  # 留空 = 全部
    "sortFieldName": "insert_date",
    "sortFieldOrder": "asc",
    "appointReportRecordId": "",
    "formulaId": "658",
    "ruleId": "230290901203812352",
    "showDimension": "0",
    "dateShowType": "0",
    "showSuit": "0",
    "excludeNonConsign": "0",
    "excludeVirtual": "0",
    "excludeClosedRefund": "0",
    "excludeUnSysConsignRefund": "0",
    "refundSumType": "0",
    "consignBeforeRate": "",
    "consignAfterRate": "",
    "consignBeforeCostRate": "",
    "consignAfterCostRate": "",
    "freightCalType": "0",
    "freightEstimateCost": "",
    "costEstimateRuleId": "",
    "costType": "0",
    "isTrusted": "true",
}

HEADERS = {
    "accept": "application/json, text/javascript, */*; q=0.01",
    "accept-language": "zh-CN,zh;q=0.9",
    "bx-v": "2.5.11",
    "companyid": "65109",
    "content-type": "application/x-www-form-urlencoded; charset=UTF-8",
    "module-path": "/think_tank/profit_shop/",
    "origin": "https://erp.superboss.cc",
    "referer": "https://erp.superboss.cc/index.html",
    "trackid": f"trackid{int(time.time() * 1000)}_99999",
    "user-agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/148.0.0.0 Safari/537.36",
    "x-requested-with": "XMLHttpRequest",
    "cookie": COOKIE,
}


def main() -> int:
    if not COOKIE:
        print("❌ 缺 KUAIMAI_WEB_COOKIE 环境变量")
        return 2

    print(f"调用: POST {URL}")
    resp = requests.post(URL, headers=HEADERS, data=PAYLOAD, timeout=30)
    print(f"HTTP {resp.status_code}")
    data = resp.json()

    print("\n" + "=" * 70)
    print("【顶层结构】")
    print("=" * 70)
    print(f"keys: {list(data.keys())}")
    print(f"suc: {data.get('suc')}  result: {data.get('result')}")
    print(f"qTime: {data.get('qTime')}")
    print(f"message: {data.get('message')}")

    print("\n" + "=" * 70)
    print("【data 结构】")
    print("=" * 70)
    inner = data.get("data", {})
    if isinstance(inner, dict):
        print(f"data keys: {list(inner.keys())}")
        for k, v in inner.items():
            if isinstance(v, list):
                print(f"  data.{k}: list[{len(v)}]")
            elif isinstance(v, dict):
                print(f"  data.{k}: dict({list(v.keys())[:10]})")
            else:
                print(f"  data.{k}: {type(v).__name__} = {str(v)[:80]}")

    rows = inner.get("list") or inner.get("rows") or []
    if rows and isinstance(rows[0], dict):
        print("\n" + "=" * 70)
        print("【单行字段】（这才是真正的列名！）")
        print("=" * 70)
        first = rows[0]
        print(f"字段数: {len(first)}")
        print(f"字段名（全部）:")
        for i, key in enumerate(sorted(first.keys()), 1):
            val = first[key]
            val_str = str(val)[:60]
            tname = type(val).__name__
            print(f"  {i:3d}. {key:40s} ({tname:8s}) = {val_str}")

        print("\n" + "=" * 70)
        print("【完整第一行（JSON）】")
        print("=" * 70)
        print(json.dumps(first, ensure_ascii=False, indent=2)[:3000])

    return 0


if __name__ == "__main__":
    sys.exit(main())
