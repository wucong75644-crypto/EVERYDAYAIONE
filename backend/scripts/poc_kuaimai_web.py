#!/usr/bin/env python3
"""
Phase 1 POC: 快麦 Web 后端 API 抓取可行性验证

复刻 erp.superboss.cc 店铺利润表 cURL，验证 4 件事：
  1. 能否拿到 JSON 数据（基线）
  2. tfstk cookie 缺失/伪造能否通过鉴权
  3. shopUniIds 留空 / 部分传入的行为
  4. 时间范围参数和复合维度切换是否正常

运行方式：
    export KUAIMAI_WEB_COOKIE_AUTH='N6OWBFODPVQBQYJV4OZ5SRHHIRNKQS45OR7YLDZM3KB6HDSYABFAESQPEC3YWHJKKLPV7EFL3ADGTVBD5KKSOIKQAY'
    export KUAIMAI_WEB_COMPANY_ID=65109
    cd backend && venv/bin/python scripts/poc_kuaimai_web.py

  可选：
    export KUAIMAI_WEB_TFSTK='gWDt-4tcFHIt...'   # 完整 tfstk，用于对比实验
"""

import json
import os
import sys
import time
from datetime import datetime
from typing import Any

import requests


# ───────────────────────── 配置区 ─────────────────────────

PROFIT_REPORT_URL = "https://erp.superboss.cc/kmzk/profit/report/shop"

FULL_COOKIE = os.environ.get("KUAIMAI_WEB_COOKIE", "")
COMPANY_ID = os.environ.get("KUAIMAI_WEB_COMPANY_ID", "65109")

SHOP_UNI_IDS_FULL = (
    "65109_-1001,65109_900585629,65109_900262478,65109_900589997,65109_900277823,"
    "65109_900063404,65109_900278518,65109_900287965,65109_900223467,65109_900120626"
)

BASE_PAYLOAD = {
    "api_name": "ttps%3A__erp.superboss.cc_kmzk_profit_report_shop",
    "groupTypeSum": "",
    "sysStatus": "1",
    "startTime": "1779552000000",
    "endTime": "1780156799000",
    "shopUniIds": SHOP_UNI_IDS_FULL,
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

BASE_HEADERS = {
    "accept": "application/json, text/javascript, */*; q=0.01",
    "accept-language": "zh-CN,zh;q=0.9",
    "bx-v": "2.5.11",
    "content-type": "application/x-www-form-urlencoded; charset=UTF-8",
    "module-path": "/think_tank/profit_shop/",
    "origin": "https://erp.superboss.cc",
    "referer": "https://erp.superboss.cc/index.html",
    "sec-ch-ua": '"Chromium";v="148", "Google Chrome";v="148", "Not/A)Brand";v="99"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"macOS"',
    "sec-fetch-dest": "empty",
    "sec-fetch-mode": "cors",
    "sec-fetch-site": "same-origin",
    "user-agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36",
    "x-requested-with": "XMLHttpRequest",
}


# ───────────────────────── 工具函数 ─────────────────────────


def parse_cookie_string(raw: str) -> dict[str, str]:
    """把浏览器复制的 cookie 字符串解析成 dict。"""
    out: dict[str, str] = {}
    for kv in raw.split(";"):
        kv = kv.strip()
        if not kv or "=" not in kv:
            continue
        k, v = kv.split("=", 1)
        out[k.strip()] = v.strip()
    return out


def build_cookie_string(
    exclude: list[str] | None = None,
    keep_only: list[str] | None = None,
) -> str:
    """构造 Cookie 字符串。可以排除某些 cookie 或只保留某些 cookie。"""
    full = parse_cookie_string(FULL_COOKIE)
    if keep_only is not None:
        items = [(k, v) for k, v in full.items() if k in keep_only]
    else:
        excl = set(exclude or [])
        items = [(k, v) for k, v in full.items() if k not in excl]
    return "; ".join(f"{k}={v}" for k, v in items)


def make_trackid() -> str:
    ts = int(time.time() * 1000)
    return f"trackid{ts}_{ts % 100000:05d}"


def call_profit_api(
    *,
    payload_override: dict | None = None,
    cookie_exclude: list[str] | None = None,
    cookie_keep_only: list[str] | None = None,
) -> tuple[int, dict | str]:
    """发起一次 POST 请求，返回 (status_code, json_or_text)。"""
    payload = dict(BASE_PAYLOAD)
    if payload_override:
        payload.update(payload_override)

    headers = dict(BASE_HEADERS)
    headers["companyid"] = COMPANY_ID
    headers["trackid"] = make_trackid()
    headers["cookie"] = build_cookie_string(
        exclude=cookie_exclude,
        keep_only=cookie_keep_only,
    )

    resp = requests.post(
        PROFIT_REPORT_URL,
        headers=headers,
        data=payload,
        timeout=30,
    )
    try:
        return resp.status_code, resp.json()
    except json.JSONDecodeError:
        return resp.status_code, resp.text[:1000]


def is_authenticated(data: Any) -> bool:
    """判断响应是否通过了鉴权（不是会话异常）。"""
    if not isinstance(data, dict):
        return False
    msg = (data.get("message") or "") + (data.get("msg") or "")
    if "会话" in msg or "登录" in msg or "未授权" in msg:
        return False
    return True


def shape_summary(data: Any) -> str:
    """对 JSON 响应做形状摘要：顶层 keys / 行数 / 关键字段示例。"""
    if isinstance(data, str):
        return f"  └─ NOT_JSON (前200字符): {data[:200]!r}"

    lines = []
    if isinstance(data, dict):
        lines.append(f"  └─ top-level keys: {sorted(data.keys())}")
        success = data.get("success") if "success" in data else data.get("code")
        msg = data.get("message") or data.get("msg") or data.get("errmsg")
        lines.append(f"  └─ success/code: {success!r}  msg: {msg!r}")

        for key in ("data", "result", "rows", "list", "records"):
            val = data.get(key)
            if val is None:
                continue
            if isinstance(val, list):
                lines.append(f"  └─ {key}[] 行数: {len(val)}")
                if val:
                    sample = val[0]
                    if isinstance(sample, dict):
                        lines.append(
                            f"  └─ {key}[0] keys (前10): {list(sample.keys())[:10]}"
                        )
            elif isinstance(val, dict):
                lines.append(f"  └─ {key} keys: {sorted(val.keys())[:15]}")
                rows = val.get("rows") or val.get("list") or val.get("records")
                if isinstance(rows, list):
                    lines.append(f"  └─ {key}.rows 行数: {len(rows)}")
    return "\n".join(lines)


# ───────────────────────── 实验函数 ─────────────────────────


def test_baseline() -> dict | None:
    """实验 1：完全复刻 cURL（带所有 cookie），验证基线能拿到数据。"""
    print("\n" + "=" * 70)
    print("【实验 1】基线：带所有 cookie")
    print("=" * 70)

    status, data = call_profit_api()
    print(f"HTTP {status}")
    print(shape_summary(data))

    if status == 200 and is_authenticated(data):
        print("✅ 基线鉴权通过——拿到真实数据")
        return data
    print("❌ 基线鉴权失败（会话异常）")
    print("   → cookie 可能整体已过期，需要重新登录复制")
    return None


def test_cookie_minimum():
    """实验 2：逐个去掉 cookie，找出最小必需集合。"""
    print("\n" + "=" * 70)
    print("【实验 2】Cookie 减法测试——找最小必需集合")
    print("=" * 70)

    full = parse_cookie_string(FULL_COOKIE)
    print(f"  完整 cookie 字段数: {len(full)}")
    print(f"  字段名: {list(full.keys())}")

    print("\n  逐个去掉每个 cookie，看哪些去掉后仍可通过鉴权：")
    necessary: list[str] = []
    optional: list[str] = []
    for name in full.keys():
        status, data = call_profit_api(cookie_exclude=[name])
        ok = status == 200 and is_authenticated(data)
        marker = "✅ 可去掉" if ok else "❌ 必需"
        print(f"    {marker}  {name}")
        if ok:
            optional.append(name)
        else:
            necessary.append(name)
        time.sleep(0.3)

    print(f"\n  → 必需 cookie: {necessary}")
    print(f"  → 可去掉 cookie: {optional}")


def test_empty_shop_ids():
    """实验 3：shopUniIds 留空，看返回是否=全部。"""
    print("\n" + "=" * 70)
    print("【实验 3】shopUniIds 留空")
    print("=" * 70)

    status, data = call_profit_api(payload_override={"shopUniIds": ""})
    print(f"HTTP {status}")
    print(shape_summary(data))


def test_time_range_change():
    """实验 4a：改时间范围（昨天 1 天），看是否生效。"""
    print("\n" + "=" * 70)
    print("【实验 4a】只查昨天 1 天 vs 默认 7 天")
    print("=" * 70)

    # 昨天 0 点 - 今天 0 点（毫秒）
    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    yesterday_start = int((today.timestamp() - 86400) * 1000)
    yesterday_end = int(today.timestamp() * 1000) - 1
    print(f"  时间范围（昨天）：{yesterday_start} - {yesterday_end}")

    status, data = call_profit_api(
        payload_override={
            "startTime": str(yesterday_start),
            "endTime": str(yesterday_end),
        }
    )
    print(f"HTTP {status}")
    print(shape_summary(data))


def test_dimension_switch():
    """实验 4b：切换维度（按日 vs 按店铺）。"""
    print("\n" + "=" * 70)
    print("【实验 4b】切换维度参数 dateShowType=1（按日展开）")
    print("=" * 70)

    status, data = call_profit_api(
        payload_override={"dateShowType": "1"},
    )
    print(f"HTTP {status}")
    print(shape_summary(data))


# ───────────────────────── 主入口 ─────────────────────────


def main() -> int:
    if not FULL_COOKIE:
        print("❌ 缺少环境变量 KUAIMAI_WEB_COOKIE", file=sys.stderr)
        print(
            "   请从浏览器 DevTools 的 Network → 任意请求 → Request Headers 复制完整 cookie 字符串",
            file=sys.stderr,
        )
        return 2

    print(f"目标接口: {PROFIT_REPORT_URL}")
    print(f"companyid: {COMPANY_ID}")
    print(f"cookie 总长度: {len(FULL_COOKIE)} 字符")
    print(f"cookie 字段数: {len(parse_cookie_string(FULL_COOKIE))}")

    baseline = test_baseline()
    if baseline is None:
        print("\n基线失败，跳过后续实验（cookie 整体过期，请重新登录复制）")
        return 1

    test_cookie_minimum()
    test_empty_shop_ids()
    test_time_range_change()
    test_dimension_switch()

    print("\n" + "=" * 70)
    print("POC 全部实验完成")
    print("=" * 70)
    return 0


if __name__ == "__main__":
    sys.exit(main())
