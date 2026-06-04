#!/usr/bin/env python3
"""
完整 dump 智库响应的所有字段到文件，便于人工审查后设计 schema。

输出文件：
  - tmp/thinktank_fields.txt   ← 每行一个字段：name | type | sample
  - tmp/thinktank_sample.json  ← 完整第一行 JSON（用于参考）
  - tmp/thinktank_classified.md ← 按规则分类后的字段清单（dl_/dy_/数值/字符串）

用法：
    cd backend && KUAIMAI_WEB_COOKIE='...' venv/bin/python scripts/dump_thinktank_fields.py
"""

import json
import os
import sys
import time
from collections import defaultdict
from pathlib import Path

import requests


COOKIE = os.environ.get("KUAIMAI_WEB_COOKIE", "")
URL = "https://erp.superboss.cc/kmzk/profit/report/shop"
OUTPUT_DIR = Path(__file__).parent.parent.parent / "tmp"

PAYLOAD = {
    "api_name": "ttps%3A__erp.superboss.cc_kmzk_profit_report_shop",
    "groupTypeSum": "",
    "sysStatus": "1",
    "startTime": "1779552000000",
    "endTime": "1780156799000",
    "shopUniIds": "",
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
    "trackid": f"trackid{int(time.time() * 1000)}_99998",
    "user-agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/148.0.0.0 Safari/537.36",
    "x-requested-with": "XMLHttpRequest",
    "cookie": COOKIE,
}


def infer_value_type(val) -> str:
    """推断字段语义类型（用于 schema 设计）。"""
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
        # 尝试解析为日期
        try:
            time.strptime(s, "%Y-%m-%d")
            return "date"
        except ValueError:
            pass
        # 尝试解析为数字
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


def classify_field(key: str, vtype: str) -> str:
    """对字段做粗分类（用于 schema 设计参考）。"""
    if key.startswith("dl_"):
        return "🟡 dl_*动态(物流/快递公司明细)"
    if key.startswith("dy_"):
        return "🟡 dy_*动态(平台费用项？)"
    if key in {
        "shop_uni_id", "shop_id", "shop_name",
        "platform_name", "platformName", "platform",
        "date_range", "stat_date", "itemId", "item_id",
        "id", "sortField",
    }:
        return "🟦 维度/标识"
    if vtype in {"numeric(str)", "integer(str)", "numeric", "integer"}:
        return "🟢 业务数值"
    if vtype == "date":
        return "🟦 日期"
    return "🔘 其他/字符串"


def main() -> int:
    if not COOKIE:
        print("❌ 缺 KUAIMAI_WEB_COOKIE 环境变量")
        return 2

    OUTPUT_DIR.mkdir(exist_ok=True)

    print(f"调用: POST {URL}")
    resp = requests.post(URL, headers=HEADERS, data=PAYLOAD, timeout=30)
    data = resp.json()
    rows = data.get("data", {}).get("list", [])
    if not rows:
        print("❌ 响应为空")
        return 1
    sample = rows[0]
    print(f"✅ 拿到 {len(rows)} 行，第一行有 {len(sample)} 个字段")

    # 1. 完整字段表
    fields_file = OUTPUT_DIR / "thinktank_fields.txt"
    with fields_file.open("w", encoding="utf-8") as f:
        f.write(f"# 智库利润表 - 完整字段清单（共 {len(sample)} 个）\n")
        f.write(f"# 数据来源: companyid=65109, 时间范围 2026-05-24~2026-05-30\n\n")
        f.write(f"{'序号':<5}{'字段名':<40}{'类型':<18}{'分类':<35}样例值\n")
        f.write("─" * 130 + "\n")
        for i, (k, v) in enumerate(sorted(sample.items()), 1):
            vtype = infer_value_type(v)
            category = classify_field(k, vtype)
            sample_str = str(v)[:40]
            f.write(f"{i:<5}{k:<40}{vtype:<18}{category:<35}{sample_str}\n")
    print(f"📝 字段清单: {fields_file}")

    # 2. 完整 JSON 样例
    json_file = OUTPUT_DIR / "thinktank_sample.json"
    json_file.write_text(json.dumps(sample, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"📝 样例 JSON: {json_file}")

    # 3. 按分类的 Markdown
    grouped: dict[str, list[tuple[str, str, str]]] = defaultdict(list)
    for k, v in sample.items():
        vtype = infer_value_type(v)
        category = classify_field(k, vtype)
        grouped[category].append((k, vtype, str(v)[:30]))

    md_file = OUTPUT_DIR / "thinktank_classified.md"
    with md_file.open("w", encoding="utf-8") as f:
        f.write(f"# 智库字段分类（{len(sample)} 个）\n\n")
        for cat in sorted(grouped.keys()):
            items = sorted(grouped[cat])
            f.write(f"## {cat}（{len(items)} 个）\n\n")
            f.write("| 字段名 | 类型 | 样例 |\n")
            f.write("|---|---|---|\n")
            for k, vtype, sample_v in items:
                f.write(f"| `{k}` | {vtype} | `{sample_v}` |\n")
            f.write("\n")
    print(f"📝 分类清单: {md_file}")

    print("\n" + "=" * 70)
    print("分类摘要:")
    for cat, items in sorted(grouped.items()):
        print(f"  {cat:<40} {len(items):>4} 个")
    return 0


if __name__ == "__main__":
    sys.exit(main())
