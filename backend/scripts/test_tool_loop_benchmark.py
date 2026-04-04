"""
单循环 Agent 工具循环基准测试

用 benchmark_cases.json 的 990 个真实用例，验证：
- LLM 直接拿到所有工具后，能否正确选择工具（不经过 Phase1 路由）
- 工具选择准确率 vs 旧架构的 expected_tools

策略：
- _call_brain: 真实调用 LLM（验证工具选择）
- executor: 不执行（只验证第一轮选了什么工具）
- 对比 expected_tools vs AI 实际选择

运行：
  cd backend && source venv/bin/activate
  python scripts/test_tool_loop_benchmark.py [--limit 20] [--category stock]
"""

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).parent.parent))

from config.chat_tools import get_chat_tools
from core.config import settings


# ============================================================
# LLM 调用（真实）
# ============================================================

MOCK_TOOL_RESULTS: Dict[str, str] = {
    # ERP Agent 返回模拟（主 Agent 视角只看到 erp_agent 的结论）
    "erp_agent": (
        "ERP 查询结果:\n"
        "商品 TEST-001 库存充足\n"
        "可售: 128件 | 锁定: 12件 | 在途: 50件\n"
        "今日订单: 42笔 | 待发货: 15笔"
    ),
    # 其他工具 mock
    "erp_api_search": "找到 3 个匹配:\n- erp_trade_query:order_list — 订单查询",
    "web_search": "搜索结果: 今日杭州气温 25°C，晴转多云",
    "search_knowledge": "知识库匹配: 退货流程详见《售后处理手册》",
    "generate_image": "图片生成需要专用通道处理",
    "generate_video": "视频生成需要专用通道处理",
    "code_execute": "代码执行完成，结果: 平均金额 ¥256.8",
}

MAX_BENCHMARK_TURNS = 2  # 主 Agent 只做路由，1-2 轮足够


async def call_llm_with_tools(
    user_text: str,
    tools: List[Dict[str, Any]],
    model_id: str = "qwen3-30b-a3b",
) -> Dict[str, Any]:
    """多轮工具循环：模拟真实 ChatHandler 行为，mock 工具返回"""
    from services.adapters.factory import create_chat_adapter
    from config.chat_tools import get_tool_system_prompt

    messages = [
        {"role": "system", "content": (
            "你是一个ERP智能助手，根据用户问题选择合适的工具查询数据。"
            "如果不需要工具直接回答即可。"
        )},
        {"role": "system", "content": get_tool_system_prompt()},
        {"role": "user", "content": user_text},
    ]

    all_selected: List[str] = []
    text_acc = ""
    current_tools = list(tools)
    turns_used = 0

    for turn in range(MAX_BENCHMARK_TURNS):
        turns_used = turn + 1

        adapter = create_chat_adapter(model_id)
        tc_acc: Dict[int, Dict[str, Any]] = {}
        turn_text = ""

        try:
            async for chunk in adapter.stream_chat(
                messages=messages, tools=current_tools,
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
            text_acc = turn_text
            break

        turn_tools = sorted(tc_acc.values(), key=lambda x: x.get("id", ""))
        for tc in turn_tools:
            all_selected.append(tc["name"])

        # 构建 assistant + tool_result messages
        asst_msg: Dict[str, Any] = {"role": "assistant", "content": turn_text or None}
        asst_msg["tool_calls"] = [
            {"id": tc["id"], "type": "function",
             "function": {"name": tc["name"], "arguments": tc["arguments"]}}
            for tc in turn_tools
        ]
        messages.append(asst_msg)

        for tc in turn_tools:
            mock = MOCK_TOOL_RESULTS.get(tc["name"], f"{tc['name']} 执行成功")
            messages.append({"role": "tool", "tool_call_id": tc["id"], "content": mock})

    return {
        "selected_tools": all_selected,
        "text": text_acc[:200] if text_acc else None,
        "turns": turns_used,
    }


# ============================================================
# 准确率评估
# ============================================================

# ERP 相关工具名（expected 里出现这些 → 应该路由到 erp_agent）
_ERP_TOOL_NAMES = {
    "local_product_identify", "local_stock_query", "local_order_query",
    "local_purchase_query", "local_aftersale_query", "local_doc_query",
    "local_product_stats", "local_product_flow", "local_global_stats",
    "local_platform_map_query", "trigger_erp_sync",
    "erp_info_query", "erp_product_query", "erp_trade_query",
    "erp_aftersales_query", "erp_warehouse_query", "erp_purchase_query",
    "erp_taobao_query", "erp_execute",
}


def evaluate(expected: List[str], actual: List[str]) -> Dict[str, Any]:
    """评估工具选择准确率（ERP Agent 模式）

    评估逻辑：
    - expected 含 ERP 工具 + actual 含 erp_agent → exact（路由正确）
    - expected 为空 + actual 为空 → exact（正确不调工具）
    - expected 为空 + actual 有工具 → false_positive
    - expected 有工具 + actual 为空 → miss
    """
    expected_set = set(expected)
    actual_set = set(actual)

    # ERP Agent 路由评估：expected 里有 ERP 工具，actual 里有 erp_agent → 正确
    expected_has_erp = bool(expected_set & _ERP_TOOL_NAMES)
    actual_has_erp_agent = "erp_agent" in actual_set

    if expected_has_erp and actual_has_erp_agent:
        return {"match": "exact", "score": 1.0}

    if not expected and not actual:
        return {"match": "exact", "score": 1.0}
    if not expected and actual:
        return {"match": "false_positive", "score": 0.0, "extra": list(actual_set)}
    if expected and not actual:
        return {"match": "miss", "score": 0.0, "missing": list(expected_set)}

    # 非 ERP 工具的精确匹配
    hit = expected_set & actual_set
    recall = len(hit) / len(expected_set)
    precision = len(hit) / len(actual_set) if actual_set else 0

    if expected_set == actual_set:
        match = "exact"
    elif expected_set <= actual_set:
        match = "superset"  # AI 多选了（可接受）
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

async def run_benchmark(
    cases: List[Dict],
    tools: List[Dict],
    model_id: str,
    limit: int = 0,
    category: str = "",
) -> List[Dict]:
    """运行基准测试"""
    # 过滤
    if category:
        cases = [c for c in cases if c.get("category") == category]
    if limit:
        cases = cases[:limit]

    print(f"\n{'=' * 60}")
    print(f"单循环 Agent 工具循环基准测试")
    print(f"模型: {model_id} | 工具数: {len(tools)} | 用例数: {len(cases)}")
    print(f"{'=' * 60}\n")

    results = []
    for i, case in enumerate(cases):
        user_text = case["input"]
        expected = case.get("expected_tools", [])
        cat = case.get("category", "")

        start = time.time()
        try:
            resp = await call_llm_with_tools(user_text, tools, model_id)
            elapsed = round(time.time() - start, 2)
            actual = resp["selected_tools"]
            eval_result = evaluate(expected, actual)

            turns = resp.get("turns", 1)
            icon = "✅" if eval_result["score"] >= 0.8 else "⚠️" if eval_result["score"] > 0 else "❌"
            print(
                f"  [{i+1}/{len(cases)}] {icon} [{cat}] \"{user_text[:40]}\" "
                f"→ {actual or '(纯文字)'} "
                f"| {eval_result['match']} | {turns}轮 | {elapsed}s"
            )
            if eval_result.get("missing"):
                print(f"         缺少: {eval_result['missing']}")
            if eval_result.get("extra"):
                print(f"         多选: {eval_result['extra']}")

            results.append({
                "input": user_text,
                "category": cat,
                "expected": expected,
                "actual": actual,
                "text": resp["text"],
                **eval_result,
                "elapsed": elapsed,
            })
        except Exception as e:
            elapsed = round(time.time() - start, 2)
            print(f"  [{i+1}/{len(cases)}] 💥 [{cat}] \"{user_text[:40]}\" → ERROR: {e} | {elapsed}s")
            results.append({
                "input": user_text,
                "category": cat,
                "expected": expected,
                "actual": [],
                "match": "error",
                "score": 0,
                "elapsed": elapsed,
                "error": str(e),
            })

    # 汇总
    print(f"\n{'=' * 60}")
    print(f"汇总")
    print(f"{'=' * 60}")

    total = len(results)
    by_match = {}
    by_category = {}
    total_score = 0
    total_elapsed = 0

    for r in results:
        m = r.get("match", "error")
        by_match[m] = by_match.get(m, 0) + 1
        cat = r.get("category", "")
        if cat not in by_category:
            by_category[cat] = {"total": 0, "score_sum": 0}
        by_category[cat]["total"] += 1
        by_category[cat]["score_sum"] += r.get("score", 0)
        total_score += r.get("score", 0)
        total_elapsed += r.get("elapsed", 0)

    print(f"\n匹配分布:")
    for m, cnt in sorted(by_match.items(), key=lambda x: -x[1]):
        print(f"  {m}: {cnt} ({cnt/total*100:.1f}%)")

    print(f"\n分类准确率:")
    for cat, info in sorted(by_category.items()):
        avg = info["score_sum"] / info["total"] if info["total"] else 0
        print(f"  {cat}: {avg*100:.1f}% ({info['total']}例)")

    avg_score = total_score / total if total else 0
    avg_elapsed = total_elapsed / total if total else 0
    print(f"\n总体: recall={avg_score*100:.1f}% | 平均耗时={avg_elapsed:.2f}s | 总耗时={total_elapsed:.1f}s")

    return results


def main():
    parser = argparse.ArgumentParser(description="单循环 Agent 工具循环基准测试")
    parser.add_argument("--limit", type=int, default=20, help="测试用例数（0=全部，默认20）")
    parser.add_argument("--category", type=str, default="", help="只测特定分类")
    parser.add_argument("--model", type=str, default="qwen3-30b-a3b", help="LLM 模型")
    parser.add_argument("--all", action="store_true", help="全量测试（990例）")
    args = parser.parse_args()

    cases_path = Path(__file__).parent / "benchmark_cases.json"
    with open(cases_path) as f:
        cases = json.load(f)

    from config.chat_tools import get_core_tools
    tools = get_core_tools(org_id="benchmark")
    limit = 0 if args.all else args.limit

    results = asyncio.run(run_benchmark(cases, tools, args.model, limit, args.category))

    # 保存结果
    out_path = Path(__file__).parent / "benchmark_tool_loop_results.json"
    with open(out_path, "w") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(f"\n结果已保存: {out_path}")


if __name__ == "__main__":
    main()
