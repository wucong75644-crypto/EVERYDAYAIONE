"""
测试脚本：模拟真实编码经过别名解析 + 参数映射的完整流程

用真实编码模拟 LLM 可能传入的各种参数名，验证每种情况的映射结果。
"""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from services.kuaimai.param_mapper import (
    PARAM_ALIASES, _resolve_aliases, map_params, _COMMON_PARAMS,
)
from services.kuaimai.registry.product import PRODUCT_REGISTRY

# ── 真实编码 ─────────────────────────────────────
REAL_CODES = [
    "TJ-LZTTDMGKC",       # 主商家编码（前缀-字母）
    "MSKMB01",             # 主商家编码（字母+数字）
    "MSKMB01-01",          # 规格商家编码（主编码-序号）
    "LZTTDMGKC-01",        # 规格商家编码（字母-序号）
    "TJ-LZTTDMGKC-04",    # 规格商家编码（前缀-字母-序号）
    "YXTXL",               # 主商家编码（纯字母）
    "TJ-MMTPWY01",         # 主商家编码（前缀-字母+数字）
]


def separator(title: str) -> None:
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}")


def test_alias_resolution():
    """测试1：别名解析（_resolve_aliases 层）"""
    separator("测试1：别名解析 — 中文参数名 → 标准参数名")

    # stock_status 的有效参数集
    stock_entry = PRODUCT_REGISTRY["stock_status"]
    valid_keys = set(stock_entry.param_map.keys()) | _COMMON_PARAMS

    test_cases = [
        # (描述, 输入, 期望的标准key)
        ("商品编码→outer_id", {"商品编码": "TJ-LZTTDMGKC"}, "outer_id"),
        ("商家编码→outer_id", {"商家编码": "MSKMB01"}, "outer_id"),
        ("编码→outer_id", {"编码": "YXTXL"}, "outer_id"),
        ("货号→outer_id", {"货号": "TJ-MMTPWY01"}, "outer_id"),
        ("规格商家编码→sku_outer_id", {"规格商家编码": "MSKMB01-01"}, "sku_outer_id"),
        ("SKU编码→sku_outer_id", {"SKU编码": "LZTTDMGKC-01"}, "sku_outer_id"),
        ("条码→code", {"条码": "6901234567890"}, "code"),
        ("标准名直接保留", {"outer_id": "TJ-LZTTDMGKC"}, "outer_id"),
    ]

    for desc, input_params, expected_key in test_cases:
        result = _resolve_aliases(input_params, valid_keys)
        value = list(input_params.values())[0]
        actual_key = list(result.keys())[0]
        status = "✅" if actual_key == expected_key else "❌"
        print(f"  {status} {desc}")
        print(f"     输入: {input_params}")
        print(f"     解析: {result}")
        if actual_key != expected_key:
            print(f"     ⚠️ 期望 key={expected_key}, 实际={actual_key}")
        print()


def test_conflict_resolution():
    """测试2：冲突处理 — 别名和标准名同时存在"""
    separator("测试2：冲突处理 — 标准名优先")

    stock_entry = PRODUCT_REGISTRY["stock_status"]
    valid_keys = set(stock_entry.param_map.keys()) | _COMMON_PARAMS

    # 同时传中文别名和标准名
    result = _resolve_aliases(
        {"编码": "MSKMB01", "outer_id": "TJ-LZTTDMGKC"}, valid_keys,
    )
    print(f"  输入: {{'编码': 'MSKMB01', 'outer_id': 'TJ-LZTTDMGKC'}}")
    print(f"  解析: {result}")
    status = "✅" if result.get("outer_id") == "TJ-LZTTDMGKC" else "❌"
    print(f"  {status} outer_id 保留标准名的值 'TJ-LZTTDMGKC'（不被别名覆盖）")


def test_e2e_map_params():
    """测试3：端到端 map_params — 从中文别名到最终 API 参数"""
    separator("测试3：端到端 map_params — 中文别名 → API 参数")

    # 用 stock_status action 测试（查库存最常见的场景）
    stock_entry = PRODUCT_REGISTRY["stock_status"]

    scenarios = [
        {
            "desc": "用户说「商品编码 TJ-LZTTDMGKC 查库存」",
            "input": {"商品编码": "TJ-LZTTDMGKC"},
            "expect_api_key": "mainOuterId",
            "expect_value": "TJ-LZTTDMGKC",
        },
        {
            "desc": "用户说「MSKMB01 查库存」LLM 传 outer_id（标准名）",
            "input": {"outer_id": "MSKMB01"},
            "expect_api_key": "mainOuterId",
            "expect_value": "MSKMB01",
        },
        {
            "desc": "用户说「SKU编码 MSKMB01-01 查库存」",
            "input": {"SKU编码": "MSKMB01-01"},
            "expect_api_key": "skuOuterId",
            "expect_value": "MSKMB01-01",
        },
        {
            "desc": "用户说「规格编码 TJ-LZTTDMGKC-04」",
            "input": {"规格编码": "TJ-LZTTDMGKC-04"},
            "expect_api_key": "skuOuterId",
            "expect_value": "TJ-LZTTDMGKC-04",
        },
        {
            "desc": "用户说「编码 YXTXL 库存多少」",
            "input": {"编码": "YXTXL"},
            "expect_api_key": "mainOuterId",
            "expect_value": "YXTXL",
        },
    ]

    for s in scenarios:
        result, warnings = map_params(stock_entry, s["input"])
        actual_value = result.get(s["expect_api_key"])
        ok = actual_value == s["expect_value"] and not warnings
        status = "✅" if ok else "❌"

        print(f"  {status} {s['desc']}")
        print(f"     LLM params: {s['input']}")
        print(f"     API params: {s['expect_api_key']}={actual_value}")
        if warnings:
            print(f"     ⚠️ 无效参数警告: {warnings}")
        print()


def test_unknown_params():
    """测试4：未知参数 — 不在别名表也不在 param_map 中"""
    separator("测试4：未知参数 → 进入 warning")

    stock_entry = PRODUCT_REGISTRY["stock_status"]

    # LLM 胡乱传了一个参数名
    result, warnings = map_params(stock_entry, {"产品代码": "TJ-LZTTDMGKC"})
    status = "✅" if "产品代码" in warnings else "❌"
    print(f"  {status} 未知参数'产品代码'进入 warning 列表")
    print(f"     warnings: {warnings}")
    print(f"     API params 中不含该值: {'产品代码' not in str(result)}")


def test_product_detail_action():
    """测试5：product_detail action（另一个常见场景）"""
    separator("测试5：product_detail — 商品详情查询")

    detail_entry = PRODUCT_REGISTRY["product_detail"]
    print(f"  param_map: {detail_entry.param_map}")
    print()

    scenarios = [
        ("商品编码→outer_id", {"商品编码": "TJ-MMTPWY01"}, "outerId"),
        ("货号→outer_id", {"货号": "MSKMB01"}, "outerId"),
    ]

    for desc, input_params, expect_api_key in scenarios:
        result, warnings = map_params(detail_entry, input_params)
        value = list(input_params.values())[0]
        status = "✅" if result.get(expect_api_key) == value else "❌"
        print(f"  {status} {desc}")
        print(f"     LLM: {input_params} → API: {expect_api_key}={result.get(expect_api_key)}")
        if warnings:
            print(f"     ⚠️ warnings: {warnings}")
        print()


def test_multicode_query():
    """测试6：multicode_query — 条码查询"""
    separator("测试6：multicode_query — 条码查询")

    mc_entry = PRODUCT_REGISTRY["multicode_query"]
    print(f"  param_map: {mc_entry.param_map}")
    print()

    scenarios = [
        ("条码→code", {"条码": "6901234567890"}, "code"),
        ("商品条码→code", {"商品条码": "6901234567890"}, "code"),
        ("code直传", {"code": "6901234567890"}, "code"),
    ]

    for desc, input_params, expect_api_key in scenarios:
        result, warnings = map_params(mc_entry, input_params)
        value = list(input_params.values())[0]
        status = "✅" if result.get(expect_api_key) == value else "❌"
        print(f"  {status} {desc}")
        print(f"     LLM: {input_params} → API: {expect_api_key}={result.get(expect_api_key)}")
        if warnings:
            print(f"     ⚠️ warnings: {warnings}")
        print()


def test_dual_alias_same_target():
    """测试7：两个中文别名指向同一标准名 — 先到先得"""
    separator("测试7：两个别名→同一标准名（先到先得）")

    stock_entry = PRODUCT_REGISTRY["stock_status"]
    valid_keys = set(stock_entry.param_map.keys()) | _COMMON_PARAMS

    # "商品编码"和"编码"都→outer_id，dict 保序，第一个赢
    result = _resolve_aliases(
        {"商品编码": "MSKMB01", "编码": "YXTXL"}, valid_keys,
    )
    winner = result.get("outer_id")
    status = "✅" if winner == "MSKMB01" else "❌"
    print(f"  输入: {{'商品编码': 'MSKMB01', '编码': 'YXTXL'}}")
    print(f"  解析: {result}")
    print(f"  {status} outer_id = '{winner}'（第一个别名的值赢）")
    print(f"     '编码' 被丢弃（同目标不覆盖）")


def test_alias_to_unsupported_action():
    """测试8：别名解析后标准名不在当前 action 的 param_map 里 → warning"""
    separator("测试8：别名解析到不支持的参数 → warning")

    stock_entry = PRODUCT_REGISTRY["stock_status"]

    # stock_status 没有 express_no 参数
    result, warnings = map_params(stock_entry, {"快递单号": "SF1234567890"})
    # 别名解析: "快递单号" → "express_no"
    # express_no 不在 stock_status.param_map → warning
    has_warning = "express_no" in warnings
    no_value = "SF1234567890" not in str(result)
    status = "✅" if has_warning and no_value else "❌"
    print(f"  输入: {{'快递单号': 'SF1234567890'}} → stock_status action")
    print(f"  {status} express_no 进入 warning（该 action 不支持此参数）")
    print(f"     warnings: {warnings}")
    print(f"     API params 不含该值: {no_value}")


def test_trade_order_alias():
    """测试9：跨注册表 — 交易订单查询（订单号别名）"""
    separator("测试9：交易注册表 — 订单号别名")

    from services.kuaimai.registry.trade import TRADE_REGISTRY
    order_entry = TRADE_REGISTRY["order_list"]
    print(f"  param_map 片段: order_id→{order_entry.param_map.get('order_id')}, "
          f"system_id→{order_entry.param_map.get('system_id')}")
    print()

    scenarios = [
        ("订单号→order_id→tid", {"订单号": "126036803257340376"}, "tid"),
        ("平台订单号→order_id→tid", {"平台订单号": "126036803257340376"}, "tid"),
        ("系统单号→system_id→sid", {"系统单号": "1234567890123456"}, "sid"),
        ("ERP单号→system_id→sid", {"ERP单号": "1234567890123456"}, "sid"),
        ("order_id直传", {"order_id": "126036803257340376"}, "tid"),
    ]

    for desc, input_params, expect_api_key in scenarios:
        result, warnings = map_params(order_entry, input_params)
        value = list(input_params.values())[0]
        ok = result.get(expect_api_key) == value and not warnings
        status = "✅" if ok else "❌"
        print(f"  {status} {desc}")
        print(f"     LLM: {input_params} → API: {expect_api_key}={result.get(expect_api_key)}")
        if warnings:
            print(f"     ⚠️ warnings: {warnings}")
        print()


def test_none_value_with_alias():
    """测试10：别名 + None 值 — 应该被跳过"""
    separator("测试10：别名 + None 值 → 跳过")

    stock_entry = PRODUCT_REGISTRY["stock_status"]
    result, warnings = map_params(stock_entry, {"商品编码": None})
    has_outer = "mainOuterId" in result
    status = "✅" if not has_outer and not warnings else "❌"
    print(f"  输入: {{'商品编码': None}}")
    print(f"  {status} None 值被跳过，API params 中无 mainOuterId")
    print(f"     warnings: {warnings}")


def test_barcode_in_param_map_priority():
    """测试11：barcode 同时在 param_map 和别名表 — param_map 优先"""
    separator("测试11：param_map 已有 key vs 别名表 — param_map 优先")

    mc_entry = PRODUCT_REGISTRY["multicode_query"]
    print(f"  multicode_query param_map: {mc_entry.param_map}")
    print(f"  别名表: 'barcode' 不在别名表中（已由 param_map 直接处理）")
    print()

    # barcode 在 multicode_query 的 param_map 中: "barcode": "code"
    result, warnings = map_params(mc_entry, {"barcode": "6901234567890"})
    ok = result.get("code") == "6901234567890" and not warnings
    status = "✅" if ok else "❌"
    print(f"  {status} barcode 通过 param_map 直接映射到 code")
    print(f"     API: code={result.get('code')}")


def test_alias_with_pagination():
    """测试12：别名 + 分页参数混合 — 互不干扰"""
    separator("测试12：别名 + 分页 — 互不干扰")

    stock_entry = PRODUCT_REGISTRY["stock_status"]
    result, warnings = map_params(
        stock_entry, {"商品编码": "TJ-LZTTDMGKC", "page": 3, "page_size": 50},
    )
    ok = (
        result.get("mainOuterId") == "TJ-LZTTDMGKC"
        and result.get("pageNo") == 3
        and result.get("pageSize") == 50
        and not warnings
    )
    status = "✅" if ok else "❌"
    print(f"  输入: {{'商品编码': 'TJ-LZTTDMGKC', 'page': 3, 'page_size': 50}}")
    print(f"  {status} 别名正确映射，分页独立生效")
    print(f"     mainOuterId={result.get('mainOuterId')}")
    print(f"     pageNo={result.get('pageNo')}, pageSize={result.get('pageSize')}")


def test_empty_string_value():
    """测试13：空字符串值 — 传入 API（不同于 None）"""
    separator("测试13：空字符串值")

    stock_entry = PRODUCT_REGISTRY["stock_status"]
    result, warnings = map_params(stock_entry, {"商品编码": ""})
    # 空字符串不是 None，会被传入
    has_key = "mainOuterId" in result
    print(f"  输入: {{'商品编码': ''}}")
    print(f"  {'✅' if has_key else '❌'} 空字符串传入 API（mainOuterId='{result.get('mainOuterId', 'N/A')}'）")
    print(f"     注意：空字符串可能导致 API 报错，但不是 mapper 的职责")


def test_product_name_alias():
    """测试14：商品名称别名 → keyword（product_list 模糊搜索）"""
    separator("测试14：商品名称别名 → keyword")

    product_list_entry = PRODUCT_REGISTRY["product_list"]
    print(f"  product_list param_map keyword: {product_list_entry.param_map.get('keyword')}")
    print()

    scenarios = [
        ("商品名称→keyword", {"商品名称": "蓝牙耳机"}, "keyword", "蓝牙耳机"),
        ("商品名→keyword", {"商品名": "手机壳"}, "keyword", "手机壳"),
        ("产品名称→keyword", {"产品名称": "数据线"}, "keyword", "数据线"),
        ("产品名→keyword", {"产品名": "充电宝"}, "keyword", "充电宝"),
        ("keyword直传（标准名优先）", {"keyword": "耳机"}, "keyword", "耳机"),
    ]

    for desc, input_params, expect_api_key, expect_value in scenarios:
        result, warnings = map_params(product_list_entry, input_params)
        ok = result.get(expect_api_key) == expect_value and not warnings
        status = "✅" if ok else "❌"
        print(f"  {status} {desc}")
        print(f"     LLM: {input_params} → API: {expect_api_key}={result.get(expect_api_key)}")
        if warnings:
            print(f"     ⚠️ warnings: {warnings}")
        print()


def test_product_name_on_stock():
    """测试15：商品名称别名用于 stock_status → warning（该 action 不支持 keyword）"""
    separator("测试15：商品名称在不支持的 action 上 → warning")

    stock_entry = PRODUCT_REGISTRY["stock_status"]

    # stock_status 的 param_map 里没有 keyword
    result, warnings = map_params(stock_entry, {"商品名称": "蓝牙耳机"})
    # 别名解析: "商品名称" → "keyword"
    # keyword 不在 stock_status.param_map → warning
    has_warning = "keyword" in warnings
    status = "✅" if has_warning else "❌"
    print(f"  输入: {{'商品名称': '蓝牙耳机'}} → stock_status action")
    print(f"  {status} keyword 进入 warning（stock_status 不支持 keyword 搜索）")
    print(f"     warnings: {warnings}")
    print(f"     提示：LLM 应引导用户先用 product_list 按名称搜索，再用编码查库存")


def test_spec_name_alias():
    """测试16：规格名称别名 → keyword（product_list 模糊搜索兜底）"""
    separator("测试16：规格名称别名 → keyword")

    product_list_entry = PRODUCT_REGISTRY["product_list"]

    scenarios = [
        ("规格名称→keyword", {"规格名称": "红色"}, "keyword", "红色"),
        ("规格名→keyword", {"规格名": "XL码"}, "keyword", "XL码"),
    ]

    for desc, input_params, expect_api_key, expect_value in scenarios:
        result, warnings = map_params(product_list_entry, input_params)
        ok = result.get(expect_api_key) == expect_value and not warnings
        status = "✅" if ok else "❌"
        print(f"  {status} {desc}")
        print(f"     LLM: {input_params} → API: {expect_api_key}={result.get(expect_api_key)}")
        if warnings:
            print(f"     ⚠️ warnings: {warnings}")
        print()

    print("  ℹ️  注意：规格名称 → keyword 只是兜底模糊搜索")
    print("     正确流程：先用 keyword 查 product_list → 再用 sku_list 查具体规格")


def test_spec_name_vs_spec_code():
    """测试17：规格名称 vs 规格编码 — 走不同路径"""
    separator("测试17：规格名称 vs 规格编码 — 不同参数路径")

    stock_entry = PRODUCT_REGISTRY["stock_status"]
    valid_keys = set(stock_entry.param_map.keys()) | _COMMON_PARAMS

    # 规格编码 → sku_outer_id（精确查询）
    result1 = _resolve_aliases({"规格编码": "MSKMB01-01"}, valid_keys)
    ok1 = result1.get("sku_outer_id") == "MSKMB01-01"
    print(f"  {'✅' if ok1 else '❌'} 规格编码 → sku_outer_id（精确查询）")
    print(f"     输入: {{'规格编码': 'MSKMB01-01'}} → {result1}")
    print()

    # 规格名称 → keyword（模糊搜索兜底）
    result2 = _resolve_aliases({"规格名称": "红色XL"}, valid_keys)
    ok2 = result2.get("keyword") == "红色XL"
    print(f"  {'✅' if ok2 else '❌'} 规格名称 → keyword（模糊搜索兜底）")
    print(f"     输入: {{'规格名称': '红色XL'}} → {result2}")
    print()

    # 规格名称用于 stock_status → warning（keyword 不在 stock_status param_map）
    result3, warnings3 = map_params(stock_entry, {"规格名称": "红色XL"})
    ok3 = "keyword" in warnings3
    print(f"  {'✅' if ok3 else '❌'} 规格名称在 stock_status → warning（需先查商品再查规格）")
    print(f"     warnings: {warnings3}")


def test_name_and_code_together():
    """测试18：商品名称 + 编码同时传入 — product_list 只支持 keyword"""
    separator("测试18：商品名称 + 编码同时传入")

    product_list_entry = PRODUCT_REGISTRY["product_list"]

    # product_list 的 param_map 没有 outer_id，只有 keyword
    # 商品编码→outer_id 别名解析正确，但 outer_id 不在 product_list 的 param_map 里
    result, warnings = map_params(
        product_list_entry, {"商品名称": "手机壳", "商品编码": "MSKMB01"},
    )
    ok_keyword = result.get("keyword") == "手机壳"
    ok_warning = "outer_id" in warnings  # outer_id 不是 product_list 的合法参数
    ok = ok_keyword and ok_warning
    print(f"  输入: {{'商品名称': '手机壳', '商品编码': 'MSKMB01'}}")
    print(f"  {'✅' if ok else '❌'} keyword 正确映射，outer_id 进入 warning")
    print(f"     keyword={result.get('keyword')}（商品名称→keyword ✓）")
    print(f"     warnings: {warnings}（outer_id 不在 product_list param_map 中）")
    print(f"     提示：用编码查商品应走 product_detail/stock_status，不走 product_list")


if __name__ == "__main__":
    print("🔍 ERP 参数别名映射完整测试")
    print(f"   别名表共 {len(PARAM_ALIASES)} 个别名")

    # 基础测试
    test_alias_resolution()
    test_conflict_resolution()
    test_e2e_map_params()
    test_unknown_params()
    test_product_detail_action()
    test_multicode_query()

    # 边界场景
    test_dual_alias_same_target()
    test_alias_to_unsupported_action()
    test_trade_order_alias()
    test_none_value_with_alias()
    test_barcode_in_param_map_priority()
    test_alias_with_pagination()
    test_empty_string_value()

    # 商品名称 + 规格名称匹配
    test_product_name_alias()
    test_product_name_on_stock()
    test_spec_name_alias()
    test_spec_name_vs_spec_code()
    test_name_and_code_together()

    separator("全部测试完成（18 项）")
