"""测试 trade API 未文档化参数"""
import asyncio
import hashlib
import hmac as hmac_mod
import sys
import os
from datetime import datetime, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
from core.config import settings
import httpx


def make_signed_params(biz_params: dict, method: str = "erp.trade.list.query") -> dict:
    app_key = settings.kuaimai_app_key
    app_secret = settings.kuaimai_app_secret
    access_token = settings.kuaimai_access_token

    all_params = {
        "method": method,
        "appKey": app_key,
        "session": access_token,
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "version": "1.0",
        "format": "json",
        "sign_method": "hmac",
        **biz_params,
    }
    filtered = {k: str(v) for k, v in all_params.items() if v is not None and k != "sign"}
    sorted_params = sorted(filtered.items())
    param_str = "".join(f"{k}{v}" for k, v in sorted_params)
    sign = hmac_mod.new(
        app_secret.encode(), param_str.encode(), hashlib.md5
    ).hexdigest().upper()
    all_params["sign"] = sign
    return all_params


async def main():
    base_url = settings.kuaimai_base_url
    now = datetime.now()
    start = (now - timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S")
    end = now.strftime("%Y-%m-%d %H:%M:%S")

    base_biz = {
        "pageSize": "20",
        "pageNo": "1",
        "timeType": "pay_time",
        "startTime": start,
        "endTime": end,
    }

    tests = [
        ("基准 (无过滤)", {}),
        ("shopName (不存在)", {"shopName": "ZZZZZ_不存在_99999"}),
        ("outerId (TJ-DBTXL01)", {"outerId": "TJ-DBTXL01"}),
        ("receiverName (不存在)", {"receiverName": "ZZZZZ_不存在_99999"}),
        ("receiverPhone (00000000000)", {"receiverPhone": "00000000000"}),
        ("warehouseName (不存在)", {"warehouseName": "ZZZZZ_不存在_99999"}),
    ]

    async with httpx.AsyncClient(timeout=15) as client:
        base_total = None

        print("\n=== erp.trade.list.query 未文档化参数测试 ===\n")
        for label, extra in tests:
            biz = {**base_biz, **extra}
            params = make_signed_params(biz, "erp.trade.list.query")
            resp = await client.post(base_url, data=params)
            d = resp.json()
            if d.get("success"):
                total = d.get("total", "?")
                if base_total is None:
                    base_total = total
                effective = "有效" if total != base_total else "无效(total不变)"
                print(f"  {label}: total={total}  -> {effective}")
            else:
                print(f"  {label}: 错误 - {d.get('msg', d.get('code'))}")

        # outstock tests
        print("\n=== erp.trade.outstock.simple.query 未文档化参数测试 ===\n")
        outstock_tests = [
            ("基准 (无过滤)", {}),
            ("shopName (不存在)", {"shopName": "ZZZZZ_不存在_99999"}),
            ("warehouseName (不存在)", {"warehouseName": "ZZZZZ_不存在_99999"}),
        ]
        base_total2 = None
        for label, extra in outstock_tests:
            biz = {**base_biz, **extra}
            params = make_signed_params(biz, "erp.trade.outstock.simple.query")
            resp = await client.post(base_url, data=params)
            d = resp.json()
            if d.get("success"):
                total = d.get("total", "?")
                if base_total2 is None:
                    base_total2 = total
                effective = "有效" if total != base_total2 else "无效(total不变)"
                print(f"  {label}: total={total}  -> {effective}")
            else:
                print(f"  {label}: 错误 - {d.get('msg', d.get('code'))}")


if __name__ == "__main__":
    asyncio.run(main())
