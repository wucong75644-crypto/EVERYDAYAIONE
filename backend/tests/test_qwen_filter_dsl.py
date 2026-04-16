"""测试千问构造 Filter DSL 的能力

直接调 DashScope API，用 function calling 让千问构造 filters 数组，
验证准确率。覆盖：简单/中等/复杂场景各5个，共15个用例。

用法：
  source backend/venv/bin/activate
  python backend/tests/test_qwen_filter_dsl.py
"""

import json
import os
import sys
import time
from typing import Any

import httpx

# ── 配置 ──
API_KEY = os.environ.get("DASHSCOPE_API_KEY", "")
BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
MODEL = "qwen3.5-plus"

# ── Filter DSL 工具定义 ──
TOOL_DEF = {
    "type": "function",
    "function": {
        "name": "local_data",
        "description": (
            "本地数据库统一查询工具。支持查询/统计/导出 erp_document_items 表的所有单据数据。\n"
            "用 filters 数组指定过滤条件，任意字段组合均可。\n\n"
            "常用字段：\n"
            "- doc_type: 单据类型(order/purchase/aftersale/receipt/shelf/purchase_return)\n"
            "- order_status/doc_status: 状态(WAIT_AUDIT/WAIT_SEND_GOODS/SELLER_SEND_GOODS/FINISHED/CLOSED)\n"
            "- consign_time: 发货时间\n"
            "- pay_time: 付款时间\n"
            "- doc_created_at: 创建时间\n"
            "- shop_name: 店铺名称\n"
            "- platform: 平台(tb/jd/pdd/fxg/kuaishou/xhs/1688)\n"
            "- outer_id: 商品主编码\n"
            "- sku_outer_id: SKU编码\n"
            "- order_no: 平台订单号\n"
            "- express_no: 快递单号\n"
            "- supplier_name: 供应商\n"
            "- warehouse_name: 仓库\n"
            "- amount: 金额\n"
            "- quantity: 数量\n"
            "- buyer_nick: 买家昵称\n"
            "- is_refund: 是否退款(0/1)\n"
            "- is_cancel: 是否取消(0/1)\n"
            "- refund_status: 退款状态\n"
            "- status_name: 状态中文名(已发货/已完成/...)\n\n"
            "op 操作符：eq(等于) ne(不等于) gt(大于) gte(大于等于) lt(小于) lte(小于等于) "
            "in(在列表中) like(模糊匹配) is_null(是否为空) between(区间)\n\n"
            "示例：查4月14日已发货订单\n"
            "filters: [\n"
            '  {"field": "order_status", "op": "eq", "value": "SELLER_SEND_GOODS"},\n'
            '  {"field": "consign_time", "op": "gte", "value": "2026-04-14 00:00:00"},\n'
            '  {"field": "consign_time", "op": "lt", "value": "2026-04-15 00:00:00"}\n'
            "]\n\n"
            "示例：淘宝平台金额超500的订单按店铺统计\n"
            "filters: [\n"
            '  {"field": "platform", "op": "eq", "value": "tb"},\n'
            '  {"field": "amount", "op": "gt", "value": 500}\n'
            "]\n"
            "mode: summary, group_by: [\"shop_name\"]"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "doc_type": {
                    "type": "string",
                    "enum": ["order", "purchase", "aftersale", "receipt",
                             "shelf", "purchase_return"],
                    "description": "单据类型",
                },
                "mode": {
                    "type": "string",
                    "enum": ["summary", "detail", "export"],
                    "description": "输出模式：summary=聚合统计，detail=明细列表，export=导出文件",
                    "default": "summary",
                },
                "filters": {
                    "type": "array",
                    "description": "过滤条件数组，每个元素 {field, op, value}",
                    "items": {
                        "type": "object",
                        "properties": {
                            "field": {
                                "type": "string",
                                "description": "列名",
                            },
                            "op": {
                                "type": "string",
                                "enum": ["eq", "ne", "gt", "gte", "lt", "lte",
                                         "in", "like", "is_null", "between"],
                                "description": "操作符",
                            },
                            "value": {
                                "description": "过滤值（类型根据字段和op决定）",
                            },
                        },
                        "required": ["field", "op", "value"],
                    },
                },
                "group_by": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "分组字段（mode=summary时生效）",
                },
                "aggregations": {
                    "type": "array",
                    "description": "聚合计算",
                    "items": {
                        "type": "object",
                        "properties": {
                            "field": {"type": "string"},
                            "func": {
                                "type": "string",
                                "enum": ["count", "sum", "avg", "min", "max"],
                            },
                        },
                        "required": ["field", "func"],
                    },
                },
                "sort_by": {
                    "type": "string",
                    "description": "排序字段",
                },
                "sort_dir": {
                    "type": "string",
                    "enum": ["asc", "desc"],
                },
                "fields": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "返回字段列表（mode=detail/export时生效）",
                },
                "limit": {
                    "type": "integer",
                    "description": "返回行数上限",
                    "default": 20,
                },
            },
            "required": ["doc_type", "filters"],
        },
    },
}

# ── 测试用例 ──

TEST_CASES = [
    # ── 简单（1-2个filter） ──
    {
        "name": "S1_今天订单数",
        "query": "今天一共有多少订单？",
        "check": lambda args: (
            args.get("doc_type") == "order"
            and args.get("mode") in ("summary", None)
            and isinstance(args.get("filters"), list)
            and len(args["filters"]) >= 1
            and any(f.get("field") in ("doc_created_at", "pay_time") for f in args["filters"])
        ),
    },
    {
        "name": "S2_已发货订单",
        "query": "查一下已发货的订单",
        "check": lambda args: (
            args.get("doc_type") == "order"
            and isinstance(args.get("filters"), list)
            and any(
                f.get("field") in ("order_status", "status_name", "doc_status")
                and "SEND" in str(f.get("value", "")).upper()
                or "发货" in str(f.get("value", ""))
                for f in args["filters"]
            )
        ),
    },
    {
        "name": "S3_淘宝平台订单",
        "query": "淘宝平台的订单有多少？",
        "check": lambda args: (
            args.get("doc_type") == "order"
            and isinstance(args.get("filters"), list)
            and any(f.get("field") == "platform" and f.get("value") == "tb"
                    for f in args["filters"])
        ),
    },
    {
        "name": "S4_某商品库存",
        "query": "帮我查一下编码 ABC123 的采购单",
        "check": lambda args: (
            args.get("doc_type") == "purchase"
            and isinstance(args.get("filters"), list)
            and any(f.get("field") in ("outer_id", "sku_outer_id")
                    and f.get("value") == "ABC123"
                    for f in args["filters"])
        ),
    },
    {
        "name": "S5_退款售后",
        "query": "最近有退款的售后单",
        "check": lambda args: (
            args.get("doc_type") == "aftersale"
            and isinstance(args.get("filters"), list)
        ),
    },

    # ── 中等（2-3个filter + 输出控制） ──
    {
        "name": "M1_日期+状态",
        "query": "4月14号已发货的订单列表",
        "check": lambda args: (
            args.get("doc_type") == "order"
            and args.get("mode") in ("detail", None)
            and isinstance(args.get("filters"), list)
            and len(args["filters"]) >= 2
            and any(f.get("field") in ("order_status", "doc_status", "status_name")
                    for f in args["filters"])
            and any(f.get("field") in ("consign_time", "doc_created_at")
                    for f in args["filters"])
        ),
    },
    {
        "name": "M2_平台+金额+统计",
        "query": "淘宝平台金额超过500的订单，按店铺统计数量",
        "check": lambda args: (
            args.get("doc_type") == "order"
            and isinstance(args.get("filters"), list)
            and any(f.get("field") == "platform" for f in args["filters"])
            and any(f.get("field") == "amount" and f.get("op") in ("gt", "gte")
                    for f in args["filters"])
            and args.get("group_by") is not None
        ),
    },
    {
        "name": "M3_店铺+时间+导出",
        "query": "导出蓝创旗舰店上周的全部订单",
        "check": lambda args: (
            args.get("doc_type") == "order"
            and args.get("mode") == "export"
            and isinstance(args.get("filters"), list)
            and any(f.get("field") == "shop_name" for f in args["filters"])
            and any(f.get("field") in ("doc_created_at", "pay_time")
                    for f in args["filters"])
        ),
    },
    {
        "name": "M4_供应商+采购到货",
        "query": "供应商包含'华东'的采购单，只看已到货的",
        "check": lambda args: (
            args.get("doc_type") == "purchase"
            and isinstance(args.get("filters"), list)
            and any(f.get("field") == "supplier_name"
                    and f.get("op") in ("like", "eq")
                    for f in args["filters"])
            and any(f.get("field") in ("doc_status", "order_status")
                    for f in args["filters"])
        ),
    },
    {
        "name": "M5_选列+排序",
        "query": "最近7天的订单，只要订单号、金额和店铺名，按金额从高到低",
        "check": lambda args: (
            args.get("doc_type") == "order"
            and isinstance(args.get("filters"), list)
            and args.get("fields") is not None
            and args.get("sort_by") in ("amount", None)
            and args.get("sort_dir") in ("desc", None)
        ),
    },

    # ── 复杂（3+个filter + 聚合 + 嵌套逻辑） ──
    {
        "name": "C1_多条件+聚合",
        "query": "4月份淘宝和京东平台已完成的订单，按平台统计总金额和订单数",
        "check": lambda args: (
            args.get("doc_type") == "order"
            and args.get("mode") == "summary"
            and isinstance(args.get("filters"), list)
            and len(args["filters"]) >= 2
            and args.get("group_by") is not None
        ),
    },
    {
        "name": "C2_排除+区间",
        "query": "本月非取消、非退款的订单，金额在100到1000之间的",
        "check": lambda args: (
            args.get("doc_type") == "order"
            and isinstance(args.get("filters"), list)
            and len(args["filters"]) >= 3
        ),
    },
    {
        "name": "C3_全量导出复杂条件",
        "query": "导出3月份拼多多平台发货的订单，要订单号、商品编码、数量、金额、快递单号、收件人",
        "check": lambda args: (
            args.get("doc_type") == "order"
            and args.get("mode") == "export"
            and isinstance(args.get("filters"), list)
            and len(args["filters"]) >= 2
            and args.get("fields") is not None
            and len(args.get("fields", [])) >= 4
        ),
    },
    {
        "name": "C4_售后多维度",
        "query": "上个月退货类型的售后单，退款金额超过200的，按供应商统计退款总额",
        "check": lambda args: (
            args.get("doc_type") == "aftersale"
            and args.get("mode") == "summary"
            and isinstance(args.get("filters"), list)
            and len(args["filters"]) >= 2
            and args.get("group_by") is not None
        ),
    },
    {
        "name": "C5_跨字段复合",
        "query": "查一下4月10日到4月14日期间，淘宝平台发货但买家申请退款的订单明细",
        "check": lambda args: (
            args.get("doc_type") == "order"
            and args.get("mode") in ("detail", None)
            and isinstance(args.get("filters"), list)
            and len(args["filters"]) >= 3
        ),
    },
]

SYSTEM_PROMPT = (
    "你是 ERP 数据查询助手。用户问数据相关问题时，调用 local_data 工具查询。\n"
    "当前日期：2026-04-15（周三）。\n"
    "构造 filters 时：\n"
    "- 日期用 ISO 格式如 2026-04-14 00:00:00\n"
    "- 状态用英文枚举如 SELLER_SEND_GOODS\n"
    "- 平台用缩写如 tb/jd/pdd\n"
    "- op 用规定的枚举值如 eq/gte/lt/like/in\n"
)


def call_qwen(query: str) -> dict[str, Any] | None:
    """调用千问 function calling，返回工具参数 dict 或 None"""
    resp = httpx.post(
        f"{BASE_URL}/chat/completions",
        headers={"Authorization": f"Bearer {API_KEY}"},
        json={
            "model": MODEL,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": query},
            ],
            "tools": [TOOL_DEF],
            "tool_choice": "auto",
        },
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()

    choices = data.get("choices", [])
    if not choices:
        return None

    msg = choices[0].get("message", {})
    tool_calls = msg.get("tool_calls", [])
    if not tool_calls:
        return None

    raw_args = tool_calls[0].get("function", {}).get("arguments", "{}")

    # 尝试解析（可能是 string 或 dict）
    if isinstance(raw_args, str):
        try:
            return json.loads(raw_args)
        except json.JSONDecodeError:
            return None
    return raw_args


def check_filters_structure(args: dict) -> list[str]:
    """检查 filters 的结构合法性，返回问题列表"""
    issues = []
    filters = args.get("filters")

    if filters is None:
        issues.append("filters 缺失")
        return issues

    if isinstance(filters, str):
        # 双重序列化？
        try:
            filters = json.loads(filters)
            issues.append("filters 被双重序列化为 string（可自动修复）")
        except json.JSONDecodeError:
            issues.append("filters 是无法解析的 string")
            return issues

    if not isinstance(filters, list):
        issues.append(f"filters 类型错误: {type(filters).__name__}，期望 list")
        return issues

    for i, f in enumerate(filters):
        if not isinstance(f, dict):
            issues.append(f"filters[{i}] 类型错误: {type(f).__name__}，期望 dict")
            continue
        if "field" not in f:
            issues.append(f"filters[{i}] 缺少 field")
        if "op" not in f:
            issues.append(f"filters[{i}] 缺少 op")
        elif f["op"] not in ("eq", "ne", "gt", "gte", "lt", "lte",
                             "in", "like", "is_null", "between"):
            issues.append(f"filters[{i}] op 非法: {f['op']}")
        if "value" not in f and f.get("op") != "is_null":
            issues.append(f"filters[{i}] 缺少 value")

    return issues


def main():
    if not API_KEY:
        print("❌ 请设置 DASHSCOPE_API_KEY 环境变量")
        sys.exit(1)

    print(f"模型: {MODEL}")
    print(f"测试用例: {len(TEST_CASES)} 个")
    print(f"{'='*70}\n")

    results = {"pass": 0, "fail": 0, "error": 0}
    details = []

    for tc in TEST_CASES:
        name = tc["name"]
        query = tc["query"]
        print(f"▶ {name}: {query}")

        try:
            args = call_qwen(query)
            if args is None:
                print(f"  ❌ 未返回工具调用\n")
                results["error"] += 1
                details.append({"name": name, "status": "ERROR", "reason": "no tool call"})
                continue

            # 结构检查
            struct_issues = check_filters_structure(args)

            # 语义检查
            semantic_ok = tc["check"](args)

            status = "PASS" if (not struct_issues and semantic_ok) else "FAIL"
            if struct_issues and semantic_ok:
                status = "PARTIAL"  # 结构有小问题但语义对

            # 输出
            filters_preview = json.dumps(args.get("filters", []),
                                         ensure_ascii=False, indent=2)
            other_params = {k: v for k, v in args.items()
                          if k not in ("filters",)}

            print(f"  params: {json.dumps(other_params, ensure_ascii=False)}")
            print(f"  filters: {filters_preview}")
            if struct_issues:
                print(f"  ⚠ 结构问题: {struct_issues}")
            print(f"  语义检查: {'✅' if semantic_ok else '❌'}")
            print(f"  结果: {'✅ PASS' if status == 'PASS' else '⚠️ PARTIAL' if status == 'PARTIAL' else '❌ FAIL'}")
            print()

            if status == "PASS":
                results["pass"] += 1
            else:
                results["fail"] += 1

            details.append({
                "name": name,
                "status": status,
                "struct_issues": struct_issues,
                "semantic_ok": semantic_ok,
                "args": args,
            })

        except Exception as e:
            print(f"  ❌ 异常: {e}\n")
            results["error"] += 1
            details.append({"name": name, "status": "ERROR", "reason": str(e)})

        time.sleep(0.5)  # 避免限流

    # ── 汇总 ──
    total = len(TEST_CASES)
    print(f"{'='*70}")
    print(f"汇总: {results['pass']}/{total} PASS | "
          f"{results['fail']} FAIL | {results['error']} ERROR")
    print(f"准确率: {results['pass']/total*100:.0f}%")

    # 按难度分组统计
    simple = [d for d in details if d["name"].startswith("S")]
    medium = [d for d in details if d["name"].startswith("M")]
    complex_ = [d for d in details if d["name"].startswith("C")]

    for label, group in [("简单", simple), ("中等", medium), ("复杂", complex_)]:
        passed = sum(1 for d in group if d["status"] == "PASS")
        print(f"  {label}: {passed}/{len(group)}")

    # 常见失败模式
    fail_patterns = {}
    for d in details:
        for issue in d.get("struct_issues", []):
            key = issue.split("（")[0].split(":")[0].strip()
            fail_patterns[key] = fail_patterns.get(key, 0) + 1
    if fail_patterns:
        print(f"\n常见结构问题:")
        for pattern, count in sorted(fail_patterns.items(), key=lambda x: -x[1]):
            print(f"  - {pattern}: {count}次")


if __name__ == "__main__":
    main()
