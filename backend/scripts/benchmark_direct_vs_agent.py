"""
对比测试：ERPAgent 子Agent vs 主LLM直接调ERP工具

验证问题：千问/Gemini 能否不用 ERPAgent，直接持有全部 ERP 工具完成查询？
如果能，架构可以简化为纯工具模式（类似 Claude）。

两种模式对比：
  Mode A（子Agent）: 主LLM 持有 7 核心工具 → 调 erp_agent → 内部多步处理
  Mode B（直接）:    主LLM 持有全部 ERP 工具（19个）→ 自己选工具 → 多步查询

运行：
  cd backend && source venv/bin/activate
  python scripts/benchmark_direct_vs_agent.py [--limit 5] [--model qwen3.5-plus]
"""

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Set

sys.path.insert(0, str(Path(__file__).parent.parent))


# ============================================================
# Mock 工具返回
# ============================================================

MOCK_RESULTS: Dict[str, str] = {
    # 主 Agent 视角：erp_agent 返回的结论
    "erp_agent": (
        "ERP 查询结果:\n"
        "商品 TEST-001 库存充足\n"
        "可售: 128件 | 锁定: 12件 | 在途: 50件\n"
        "今日订单: 42笔 | 待发货: 15笔"
    ),
    # ERP 各工具的 mock
    "local_product_identify": "商品识别:\n商家编码: TEST-001\n名称: 测试商品\n类型: 单品",
    "local_stock_query": "库存: 可售128件, 锁定12件, 在途50件",
    "local_order_query": "订单: 今日42笔, 待发货15笔, 已发货27笔",
    "local_purchase_query": "采购: PO001已到货200件, PO002在途",
    "local_aftersale_query": "售后: 退货3笔, 退款2笔, 换货1笔",
    "local_doc_query": "单据: sid=5759422420146938, order_no=126036803257340376",
    "local_product_stats": "统计: 销售额¥52,380, 订单156笔, 退货率2.3%",
    "local_product_flow": "供应链: 采购→收货→上架→销售→售后 各环节正常",
    "local_global_stats": "全局: 今日328笔, 销售额¥186,420, 退货率1.8%",
    "local_platform_map_query": "平台映射: TEST-001→天猫/京东/拼多多均有售",
    "trigger_erp_sync": "同步已触发",
    "erp_info_query": "基础信息: 仓库3个, 店铺5个",
    "erp_product_query": "商品数据: TEST-001 售价¥99, 成本¥45",
    "erp_trade_query": "订单数据: 最近30天1,200笔",
    "erp_aftersales_query": "售后数据: 本月退货28笔",
    "erp_warehouse_query": "仓储: 调拨单DB001状态OUTING",
    "erp_purchase_query": "采购: 本月采购单15笔, 总额¥280,000",
    "erp_taobao_query": "淘宝数据: 本月退款12笔",
    "erp_execute": "操作已执行",
    # 非 ERP 工具
    "erp_api_search": "搜索结果: 推荐使用 local_stock_query",
    "web_search": "搜索结果: 今日杭州气温 25°C",
    "search_knowledge": "知识库: 退货流程详见《售后处理手册》",
    "generate_image": "图片生成需要专用通道",
    "generate_video": "视频生成需要专用通道",
    "code_execute": "代码执行完成",
    "route_to_chat": "OK",
    "ask_user": "OK",
}

MAX_TURNS = 3

# ERP 工具名集合
_ERP_TOOL_NAMES: Set[str] = {
    "local_product_identify", "local_stock_query", "local_order_query",
    "local_purchase_query", "local_aftersale_query", "local_doc_query",
    "local_product_stats", "local_product_flow", "local_global_stats",
    "local_platform_map_query", "trigger_erp_sync",
    "erp_info_query", "erp_product_query", "erp_trade_query",
    "erp_aftersales_query", "erp_warehouse_query", "erp_purchase_query",
    "erp_taobao_query", "erp_execute",
}


# ============================================================
# 通用 LLM 调用（多轮工具循环）
# ============================================================

async def run_llm_loop(
    user_text: str,
    tools: List[Dict[str, Any]],
    system_prompt: str,
    model_id: str,
    max_turns: int = MAX_TURNS,
) -> Dict[str, Any]:
    """多轮工具循环，mock 工具返回"""
    from services.adapters.factory import create_chat_adapter

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_text},
    ]

    all_selected: List[str] = []
    turns_used = 0

    for turn in range(max_turns):
        turns_used = turn + 1
        adapter = create_chat_adapter(model_id)
        tc_acc: Dict[int, Dict[str, Any]] = {}
        turn_text = ""

        try:
            async for chunk in adapter.stream_chat(
                messages=messages, tools=tools,
            ):
                if chunk.content:
                    turn_text += chunk.content
                if chunk.tool_calls:
                    for tc in chunk.tool_calls:
                        idx = tc.index
                        if idx not in tc_acc:
                            tc_acc[idx] = {"id": "", "name": "", "arguments": ""}
                        entry = tc_acc[idx]
                        if tc.id:
                            entry["id"] = tc.id
                        if tc.name:
                            entry["name"] = tc.name
                        if tc.arguments_delta:
                            entry["arguments"] += tc.arguments_delta
        finally:
            await adapter.close()

        if not tc_acc:
            break

        turn_tools = sorted(tc_acc.values(), key=lambda x: x.get("id", ""))
        for tc in turn_tools:
            name = tc["name"]
            if name not in ("route_to_chat", "ask_user"):
                all_selected.append(name)

        # 构建 messages
        asst_msg: Dict[str, Any] = {"role": "assistant", "content": turn_text or None}
        asst_msg["tool_calls"] = [
            {"id": tc["id"], "type": "function",
             "function": {"name": tc["name"], "arguments": tc["arguments"]}}
            for tc in turn_tools
        ]
        messages.append(asst_msg)

        for tc in turn_tools:
            mock = MOCK_RESULTS.get(tc["name"], f"{tc['name']} 执行成功")
            messages.append({"role": "tool", "tool_call_id": tc["id"], "content": mock})

        # 退出条件
        if any(tc["name"] in ("route_to_chat", "ask_user") for tc in turn_tools):
            break

    return {"selected_tools": all_selected, "turns": turns_used}


# ============================================================
# Mode A：主Agent + erp_agent 封装
# ============================================================

async def run_mode_a(user_text: str, model_id: str) -> Dict[str, Any]:
    """Mode A: 7 个核心工具，ERP 通过 erp_agent 封装"""
    from config.chat_tools import get_core_tools, get_tool_system_prompt

    tools = get_core_tools(org_id="benchmark")
    system_prompt = (
        "你是一个ERP智能助手，根据用户问题选择合适的工具查询数据。"
        "如果不需要工具直接回答即可。\n"
        + get_tool_system_prompt()
    )
    return await run_llm_loop(user_text, tools, system_prompt, model_id, max_turns=2)


# ============================================================
# Mode B：主Agent 直接持有全部 ERP 工具
# ============================================================

async def run_mode_b(user_text: str, model_id: str) -> Dict[str, Any]:
    """Mode B: 主LLM 直接持有全部 ERP 工具（去掉 erp_agent 封装）"""
    from config.chat_tools import get_chat_tools
    from config.phase_tools import build_domain_prompt

    # 获取全部工具，移除 erp_agent（因为本模式直接用底层工具）
    all_tools = get_chat_tools(org_id="benchmark")
    tools = [t for t in all_tools if t["function"]["name"] != "erp_agent"]

    # 系统提示词：通用 + ERP 域专业提示
    erp_prompt = build_domain_prompt("erp")
    system_prompt = (
        "你是一个ERP智能助手，根据用户问题选择合适的工具查询数据。"
        "如果不需要工具直接回答即可。\n"
        + erp_prompt
    )
    return await run_llm_loop(user_text, tools, system_prompt, model_id, max_turns=MAX_TURNS)


# ============================================================
# 评估
# ============================================================

def evaluate_mode_a(expected: List[str], actual: List[str]) -> Dict[str, Any]:
    """Mode A 评估：expected 有 ERP 工具 + actual 有 erp_agent → 正确"""
    expected_set = set(expected)
    actual_set = set(actual)

    expected_has_erp = bool(expected_set & _ERP_TOOL_NAMES)
    actual_has_erp_agent = "erp_agent" in actual_set

    if expected_has_erp and actual_has_erp_agent:
        return {"match": "correct", "score": 1.0}
    if not expected and not actual:
        return {"match": "correct", "score": 1.0}
    if not expected and actual:
        return {"match": "false_positive", "score": 0.0}
    if expected and not actual:
        return {"match": "miss", "score": 0.0}

    # 非 ERP 工具精确匹配
    hit = expected_set & actual_set
    return {
        "match": "correct" if hit else "wrong",
        "score": len(hit) / len(expected_set) if expected_set else 0,
    }


def evaluate_mode_b(expected: List[str], actual: List[str]) -> Dict[str, Any]:
    """Mode B 评估：直接对比具体工具名"""
    expected_set = set(expected)
    actual_set = set(actual)

    if not expected and not actual:
        return {"match": "correct", "score": 1.0}
    if not expected and actual:
        return {"match": "false_positive", "score": 0.0}
    if expected and not actual:
        return {"match": "miss", "score": 0.0}

    hit = expected_set & actual_set
    recall = len(hit) / len(expected_set)
    precision = len(hit) / len(actual_set) if actual_set else 0

    if recall >= 0.8:
        match = "correct"
    elif hit:
        match = "partial"
    else:
        match = "wrong"

    return {
        "match": match,
        "score": recall,
        "precision": precision,
        "hit": list(hit),
        "missing": list(expected_set - actual_set),
        "extra": list(actual_set - expected_set),
    }


# ============================================================
# 主流程
# ============================================================

async def run_benchmark(cases: List[Dict], model_id: str, limit: int):
    """对比运行两种模式"""
    # 每个分类抽样
    by_cat: Dict[str, List] = {}
    for c in cases:
        cat = c.get("category", "other")
        if cat not in by_cat:
            by_cat[cat] = []
        by_cat[cat].append(c)

    # 均匀抽样：每分类取 limit 个
    sampled = []
    for cat in sorted(by_cat.keys()):
        sampled.extend(by_cat[cat][:limit])

    print(f"\n{'=' * 70}")
    print(f"对比测试：ERPAgent 子Agent vs 主LLM直接调工具")
    print(f"模型: {model_id} | 每分类: {limit}例 | 总计: {len(sampled)}例")
    print(f"分类: {', '.join(sorted(by_cat.keys()))}")
    print(f"{'=' * 70}")

    results = []
    for i, case in enumerate(sampled):
        user_text = case["input"]
        expected = case.get("expected_tools", [])
        cat = case.get("category", "")

        # --- Mode A ---
        start_a = time.time()
        try:
            resp_a = await run_mode_a(user_text, model_id)
            elapsed_a = round(time.time() - start_a, 2)
            eval_a = evaluate_mode_a(expected, resp_a["selected_tools"])
        except Exception as e:
            elapsed_a = round(time.time() - start_a, 2)
            resp_a = {"selected_tools": [], "turns": 0}
            eval_a = {"match": "error", "score": 0}
            print(f"  Mode A error: {e}")

        # --- Mode B ---
        start_b = time.time()
        try:
            resp_b = await run_mode_b(user_text, model_id)
            elapsed_b = round(time.time() - start_b, 2)
            eval_b = evaluate_mode_b(expected, resp_b["selected_tools"])
        except Exception as e:
            elapsed_b = round(time.time() - start_b, 2)
            resp_b = {"selected_tools": [], "turns": 0}
            eval_b = {"match": "error", "score": 0}
            print(f"  Mode B error: {e}")

        icon_a = "✅" if eval_a["score"] >= 0.8 else "❌"
        icon_b = "✅" if eval_b["score"] >= 0.8 else "❌"

        print(
            f"  [{i+1}/{len(sampled)}] [{cat:10s}] \"{user_text[:35]}\"\n"
            f"      A(Agent): {icon_a} → {resp_a['selected_tools']} | {resp_a['turns']}轮 {elapsed_a}s\n"
            f"      B(Direct): {icon_b} → {resp_b['selected_tools']} | {resp_b['turns']}轮 {elapsed_b}s\n"
            f"      Expected: {expected}"
        )
        if eval_b.get("missing"):
            print(f"      B缺少: {eval_b['missing']}")

        results.append({
            "input": user_text,
            "category": cat,
            "expected": expected,
            "mode_a": {
                "tools": resp_a["selected_tools"],
                "turns": resp_a["turns"],
                "elapsed": elapsed_a,
                **eval_a,
            },
            "mode_b": {
                "tools": resp_b["selected_tools"],
                "turns": resp_b["turns"],
                "elapsed": elapsed_b,
                **eval_b,
            },
        })

    # ============================================================
    # 汇总对比
    # ============================================================
    print(f"\n{'=' * 70}")
    print(f"汇总对比")
    print(f"{'=' * 70}")

    total = len(results)
    a_score_sum = sum(r["mode_a"]["score"] for r in results)
    b_score_sum = sum(r["mode_b"]["score"] for r in results)
    a_time_sum = sum(r["mode_a"]["elapsed"] for r in results)
    b_time_sum = sum(r["mode_b"]["elapsed"] for r in results)

    print(f"\n{'指标':<20} {'Mode A (Agent)':<20} {'Mode B (Direct)':<20}")
    print(f"{'-' * 60}")
    print(f"{'总准确率':<20} {a_score_sum/total*100:.1f}%{'':<14} {b_score_sum/total*100:.1f}%")
    print(f"{'平均耗时':<20} {a_time_sum/total:.2f}s{'':<14} {b_time_sum/total:.2f}s")

    # 按分类对比
    cat_stats: Dict[str, Dict] = {}
    for r in results:
        cat = r["category"]
        if cat not in cat_stats:
            cat_stats[cat] = {"a_sum": 0, "b_sum": 0, "count": 0}
        cat_stats[cat]["a_sum"] += r["mode_a"]["score"]
        cat_stats[cat]["b_sum"] += r["mode_b"]["score"]
        cat_stats[cat]["count"] += 1

    print(f"\n{'分类':<12} {'Mode A':<12} {'Mode B':<12} {'差值':<10} {'结论':<10}")
    print(f"{'-' * 56}")
    for cat in sorted(cat_stats.keys()):
        s = cat_stats[cat]
        a_pct = s["a_sum"] / s["count"] * 100
        b_pct = s["b_sum"] / s["count"] * 100
        diff = b_pct - a_pct
        verdict = "B胜" if diff > 5 else "A胜" if diff < -5 else "持平"
        print(f"{cat:<12} {a_pct:>5.1f}%{'':<6} {b_pct:>5.1f}%{'':<6} {diff:>+5.1f}%{'':<4} {verdict}")

    # 结论
    a_avg = a_score_sum / total * 100
    b_avg = b_score_sum / total * 100
    print(f"\n{'=' * 70}")
    if b_avg >= 85 and b_avg >= a_avg - 5:
        print(f"✅ 结论：Mode B 准确率 {b_avg:.1f}% ≥ 85%，可以去掉 ERPAgent 子Agent")
        print(f"   架构可简化为：主LLM + 纯工具（类似 Claude）")
    elif b_avg >= 70:
        print(f"⚠️ 结论：Mode B 准确率 {b_avg:.1f}%，差一点，优化工具描述后可能达标")
        print(f"   建议：优化 ERP 工具描述 + 系统提示词后重测")
    else:
        print(f"❌ 结论：Mode B 准确率 {b_avg:.1f}% < 70%，模型能力不足，需要保留 ERPAgent")
        print(f"   ERPAgent 子Agent 是必要的工程补偿")
    print(f"{'=' * 70}")

    return results


def main():
    parser = argparse.ArgumentParser(description="对比: ERPAgent vs 直接工具")
    parser.add_argument("--limit", type=int, default=5, help="每分类测试数（默认5）")
    parser.add_argument("--model", type=str, default="qwen3.5-plus", help="LLM 模型")
    args = parser.parse_args()

    with open(Path(__file__).parent / "benchmark_cases.json") as f:
        cases = json.load(f)

    results = asyncio.run(run_benchmark(cases, args.model, args.limit))

    out_path = Path(__file__).parent / "benchmark_direct_vs_agent_results.json"
    with open(out_path, "w") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(f"\n结果已保存: {out_path}")


if __name__ == "__main__":
    main()
