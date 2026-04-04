"""
ERP Agent 内部准确率基准测试

测试 ERPAgent 内部的工具选择准确率：
- 同义词预处理（expand_synonyms）
- 工具预过滤（select_and_filter_tools 3 级算法）
- LLM 工具选择（真实 LLM 调用，mock 工具执行）

对比 expected_tools vs ERPAgent 实际选择的工具。

运行：
  cd backend && source venv/bin/activate
  python scripts/test_erp_agent_benchmark.py [--limit 20] [--category stock]
"""

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Set

sys.path.insert(0, str(Path(__file__).parent.parent))

from config.tool_registry import expand_synonyms

# Mock 工具返回（不真正执行，只看 LLM 选了什么工具）
MOCK_RESULTS: Dict[str, str] = {
    "local_product_identify": "商品识别:\n商家编码: TEST-001\n名称: 测试商品\n类型: 单品",
    "local_stock_query": "库存: 可售128件, 锁定12件, 在途50件",
    "local_order_query": "订单: 今日42笔, 待发货15笔, 已发货27笔",
    "local_purchase_query": "采购: PO001已到货200件, PO002未到货",
    "local_aftersale_query": "售后: 退货3笔, 退款2笔, 换货1笔",
    "local_doc_query": "单据: sid=5759422420146938, order_no=126036803257340376",
    "local_product_stats": "统计: 销售额¥52,380, 订单156笔, 退货率2.3%",
    "local_product_flow": "供应链: 采购→收货→上架→销售→售后 各环节正常",
    "local_global_stats": "全局: 今日328笔, 销售额¥186,420, 退货率1.8%",
    "local_platform_map_query": "平台映射: TEST-001→天猫/京东/拼多多均有售",
    "trigger_erp_sync": "同步已触发",
    "erp_warehouse_query": "仓储: 调拨单DB001状态OUTING",
    "erp_trade_query": "订单数据已返回",
    "erp_product_query": "商品数据已返回",
    "erp_purchase_query": "采购数据已返回",
    "erp_aftersales_query": "售后数据已返回",
    "erp_info_query": "基础信息已返回",
    "erp_taobao_query": "淘宝数据已返回",
    "erp_api_search": "搜索结果: 推荐使用 local_stock_query",
    "code_execute": "代码执行完成",
    "route_to_chat": "OK",
    "ask_user": "OK",
}

MAX_TURNS = 3


async def run_erp_agent_test(
    query: str,
    model_id: str = "qwen3.5-plus",
) -> Dict[str, Any]:
    """模拟 ERPAgent 内部执行，记录选了哪些工具"""
    from config.phase_tools import build_domain_tools, build_domain_prompt
    from services.tool_selector import select_and_filter_tools
    from services.adapters.factory import create_chat_adapter

    # 1. 同义词
    expanded = expand_synonyms(query)

    # 2. 工具过滤
    all_tools = build_domain_tools("erp")
    selected_tools = await select_and_filter_tools("erp", query, all_tools)

    # 3. 构建 messages
    system_prompt = build_domain_prompt("erp")
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": query},
    ]

    # 4. 多轮 LLM 调用
    all_selected: List[str] = []
    turns_used = 0

    for turn in range(MAX_TURNS):
        turns_used = turn + 1
        adapter = create_chat_adapter(model_id)
        tc_acc: Dict[int, Dict[str, Any]] = {}
        turn_text = ""

        try:
            async for chunk in adapter.stream_chat(
                messages=messages, tools=selected_tools,
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

        # 构建 messages 继续循环
        asst_msg: Dict[str, Any] = {"role": "assistant", "content": turn_text or None}
        asst_msg["tool_calls"] = [
            {"id": tc["id"], "type": "function",
             "function": {"name": tc["name"], "arguments": tc["arguments"]}}
            for tc in turn_tools
        ]
        messages.append(asst_msg)

        for tc in turn_tools:
            mock = MOCK_RESULTS.get(tc["name"], f"{tc['name']} OK")
            messages.append({"role": "tool", "tool_call_id": tc["id"], "content": mock})

        # route_to_chat / ask_user → 退出
        if any(tc["name"] in ("route_to_chat", "ask_user") for tc in turn_tools):
            break

    return {
        "selected_tools": all_selected,
        "turns": turns_used,
        "synonyms": sorted(expanded)[:5],
        "tools_offered": len(selected_tools),
    }


def evaluate(expected: List[str], actual: List[str]) -> Dict[str, Any]:
    """评估工具选择准确率"""
    expected_set = set(expected)
    actual_set = set(actual)

    if not expected and not actual:
        return {"match": "exact", "score": 1.0}
    if not expected and actual:
        return {"match": "false_positive", "score": 0.0}
    if expected and not actual:
        return {"match": "miss", "score": 0.0, "missing": list(expected_set)}

    hit = expected_set & actual_set
    recall = len(hit) / len(expected_set)

    if expected_set == actual_set:
        match = "exact"
    elif expected_set <= actual_set:
        match = "superset"
    elif hit:
        match = "partial"
    else:
        match = "wrong"

    return {
        "match": match,
        "score": recall,
        "hit": list(hit),
        "missing": list(expected_set - actual_set),
        "extra": list(actual_set - expected_set),
    }


async def run_benchmark(cases, model_id, limit, category):
    if category:
        cases = [c for c in cases if c.get("category") == category]
    # 只测 ERP 相关（有 expected_tools 的）
    cases = [c for c in cases if c.get("expected_tools")]
    if limit:
        cases = cases[:limit]

    print(f"\n{'=' * 60}")
    print(f"ERP Agent 内部准确率测试")
    print(f"模型: {model_id} | 用例: {len(cases)}")
    print(f"{'=' * 60}\n")

    results = []
    for i, case in enumerate(cases):
        query = case["input"]
        expected = case["expected_tools"]
        cat = case.get("category", "")

        start = time.time()
        try:
            resp = await run_erp_agent_test(query, model_id)
            elapsed = round(time.time() - start, 2)
            actual = resp["selected_tools"]
            ev = evaluate(expected, actual)

            icon = "✅" if ev["score"] >= 0.8 else "⚠️" if ev["score"] > 0 else "❌"
            print(
                f"  [{i+1}/{len(cases)}] {icon} [{cat}] \"{query[:40]}\" "
                f"→ {actual or '(无工具)'} "
                f"| {ev['match']} | {resp['turns']}轮 | {elapsed}s"
            )
            if ev.get("missing"):
                print(f"         缺少: {ev['missing']}")

            results.append({
                "input": query, "category": cat,
                "expected": expected, "actual": actual,
                **ev, "elapsed": elapsed,
                "turns": resp["turns"],
                "tools_offered": resp["tools_offered"],
            })
        except Exception as e:
            elapsed = round(time.time() - start, 2)
            print(f"  [{i+1}/{len(cases)}] 💥 [{cat}] \"{query[:40]}\" → ERROR: {e}")
            results.append({
                "input": query, "category": cat,
                "expected": expected, "actual": [],
                "match": "error", "score": 0, "elapsed": elapsed,
            })

    # 汇总
    print(f"\n{'=' * 60}")
    total = len(results)
    by_cat = {}
    total_score = 0

    for r in results:
        cat = r.get("category", "")
        if cat not in by_cat:
            by_cat[cat] = {"total": 0, "score_sum": 0}
        by_cat[cat]["total"] += 1
        by_cat[cat]["score_sum"] += r.get("score", 0)
        total_score += r.get("score", 0)

    print(f"\n分类准确率:")
    for cat, info in sorted(by_cat.items()):
        avg = info["score_sum"] / info["total"] if info["total"] else 0
        print(f"  {cat}: {avg*100:.1f}% ({info['total']}例)")

    avg_score = total_score / total if total else 0
    print(f"\n总体 recall: {avg_score*100:.1f}%")
    return results


def main():
    parser = argparse.ArgumentParser(description="ERP Agent 内部准确率测试")
    parser.add_argument("--limit", type=int, default=10, help="用例数")
    parser.add_argument("--category", type=str, default="", help="分类过滤")
    parser.add_argument("--model", type=str, default="qwen3.5-plus", help="LLM 模型")
    parser.add_argument("--all", action="store_true", help="全量测试")
    args = parser.parse_args()

    with open(Path(__file__).parent / "benchmark_cases.json") as f:
        cases = json.load(f)

    limit = 0 if args.all else args.limit
    asyncio.run(run_benchmark(cases, args.model, limit, args.category))


if __name__ == "__main__":
    main()
