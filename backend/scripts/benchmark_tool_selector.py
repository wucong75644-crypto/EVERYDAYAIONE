"""
工具筛选器基准测试 — AI 生成场景 + 自动化验证

流程：
1. 用 qwen-turbo 生成 N 组（用户输入, 期望工具, 期望action）
2. 跑 tool_selector，计算命中率
3. 输出未命中的 case 供调优

用法：
    python scripts/benchmark_tool_selector.py --count 200
"""

import asyncio
import json
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

# 项目路径
backend_dir = Path(__file__).parent.parent
sys.path.insert(0, str(backend_dir))

import httpx
from loguru import logger

from config.tool_registry import TOOL_REGISTRY
from services.tool_selector import select_and_filter_tools, select_tools
from config.phase_tools import build_domain_tools


# ============================================================
# Step 1: AI 生成测试用例
# ============================================================

GENERATE_PROMPT = """你是 ERP 系统的测试用例生成器。请生成 {count} 个用户可能对 ERP 系统说的查询，覆盖以下场景：

## 工具列表
本地工具（优先）：
- local_product_identify: 编码识别（商品编码/SKU/条码/名称搜索）
- local_stock_query: 库存查询（可售/锁定/预占）
- local_order_query: 按商品查订单
- local_purchase_query: 按商品查采购
- local_aftersale_query: 按商品查售后
- local_doc_query: 按单号/快递号/供应商/店铺查单据
- local_product_stats: 统计报表（销量趋势/对比）
- local_product_flow: 全链路流转
- local_global_stats: 全局统计（今天多少单/排名/平台对比）
- local_platform_map_query: 平台映射查询

远程工具（本地查不到时）：
- erp_product_query: 商品/SKU/库存/品牌
- erp_trade_query: 订单/出库/物流/快递
- erp_purchase_query: 采购/收货/上架/供应商
- erp_aftersales_query: 售后/退货/维修
- erp_warehouse_query: 仓库/调拨/盘点
- erp_info_query: 店铺/仓库列表/标签
- erp_taobao_query: 淘宝/天猫/奇门
- erp_execute: 写操作

## 要求
1. 涵盖正式用语和口语（如"卖了多少"、"库存够不够"、"到哪了"）
2. 包含容易混淆的场景（如"发票"不是"发货"，"卖点"不是"卖了"）
3. 包含多任务查询（如"库存多少顺便看下退货"）
4. 包含带编码的查询（如"ABC123 库存"）
5. 包含模糊表述（如"帮我看下这个"）
6. 每个用例包含 1-3 个期望被选中的核心工具

## 输出格式（严格 JSON 数组）
[
  {{"input": "用户输入", "expected_tools": ["tool1", "tool2"], "category": "分类标签"}},
  ...
]

分类标签：stock(库存) / order(订单) / purchase(采购) / aftersale(售后) / logistics(物流) / stats(统计) / multi(多任务) / ambiguous(模糊) / negative(反例-不应命中)

只输出 JSON，不要其他文字。"""


async def generate_test_cases(count: int = 200) -> List[Dict[str, Any]]:
    """用 qwen-turbo 生成测试用例"""
    from core.config import get_settings
    settings = get_settings()

    # 分批生成（每批 50 个，避免输出太长截断）
    all_cases = []
    batch_size = 50
    batches = (count + batch_size - 1) // batch_size

    async with httpx.AsyncClient(
        base_url=settings.dashscope_base_url,
        timeout=httpx.Timeout(connect=10.0, read=120.0, write=10.0, pool=10.0),
    ) as client:
        for batch_idx in range(batches):
            remaining = min(batch_size, count - len(all_cases))
            if remaining <= 0:
                break

            logger.info(f"Generating batch {batch_idx + 1}/{batches} ({remaining} cases)")
            resp = await client.post(
                "/chat/completions",
                headers={"Authorization": f"Bearer {settings.dashscope_api_key}"},
                json={
                    "model": "qwen-plus",
                    "messages": [{"role": "user", "content": GENERATE_PROMPT.format(count=remaining)}],
                    "max_tokens": 8000,
                    "temperature": 0.9,
                },
            )
            data = resp.json()
            text = data["choices"][0]["message"]["content"]

            # 提取 JSON
            text = text.strip()
            if text.startswith("```"):
                text = text.split("\n", 1)[1].rsplit("```", 1)[0]

            try:
                cases = json.loads(text)
                all_cases.extend(cases)
                logger.info(f"  Got {len(cases)} cases (total: {len(all_cases)})")
            except json.JSONDecodeError as e:
                logger.warning(f"  JSON parse error: {e}")
                logger.debug(f"  Raw text: {text[:500]}")

    return all_cases[:count]


# ============================================================
# Step 2: 运行 selector 并验证
# ============================================================


async def run_benchmark(cases: List[Dict[str, Any]]) -> Dict[str, Any]:
    """对每个 case 跑 selector，统计命中率"""
    all_tools = build_domain_tools("erp")

    results = {
        "total": len(cases),
        "tool_hit": 0,          # 期望工具至少 1 个被选中
        "tool_all_hit": 0,      # 期望工具全部被选中
        "tool_miss": 0,         # 期望工具完全未命中
        "misses": [],           # 未命中详情
        "by_category": {},      # 按分类统计
        "latency_ms": [],       # 每次筛选耗时
    }

    for case in cases:
        user_input = case["input"]
        expected = set(case.get("expected_tools", []))
        category = case.get("category", "unknown")

        if category not in results["by_category"]:
            results["by_category"][category] = {"total": 0, "hit": 0, "all_hit": 0}
        results["by_category"][category]["total"] += 1

        start = time.monotonic()
        filtered = await select_and_filter_tools("erp", user_input, all_tools)
        elapsed = (time.monotonic() - start) * 1000
        results["latency_ms"].append(elapsed)

        selected_names = {t["function"]["name"] for t in filtered}
        hit = expected & selected_names
        miss = expected - selected_names

        if hit:
            results["tool_hit"] += 1
            results["by_category"][category]["hit"] += 1
        else:
            results["tool_miss"] += 1

        if not miss:
            results["tool_all_hit"] += 1
            results["by_category"][category]["all_hit"] += 1
        elif miss:
            results["misses"].append({
                "input": user_input,
                "expected": list(expected),
                "selected": list(selected_names - {
                    "route_to_chat", "ask_user", "code_execute",
                    "erp_api_search", "search_knowledge",
                    "get_conversation_context",
                }),
                "missed": list(miss),
                "category": category,
            })

    return results


def print_report(results: Dict[str, Any]) -> None:
    """打印基准测试报告"""
    total = results["total"]
    latencies = results["latency_ms"]

    print("\n" + "=" * 60)
    print("工具筛选器基准测试报告")
    print("=" * 60)

    print(f"\n总用例数: {total}")
    print(f"至少命中 1 个: {results['tool_hit']}/{total} ({results['tool_hit']/total*100:.1f}%)")
    print(f"全部命中:     {results['tool_all_hit']}/{total} ({results['tool_all_hit']/total*100:.1f}%)")
    print(f"完全未命中:   {results['tool_miss']}/{total} ({results['tool_miss']/total*100:.1f}%)")

    print(f"\n延迟 P50: {sorted(latencies)[len(latencies)//2]:.1f}ms")
    print(f"延迟 P99: {sorted(latencies)[int(len(latencies)*0.99)]:.1f}ms")

    print("\n--- 按分类统计 ---")
    for cat, stats in sorted(results["by_category"].items()):
        t = stats["total"]
        h = stats["hit"]
        a = stats["all_hit"]
        print(f"  {cat:15s}: {h}/{t} hit ({h/t*100:.0f}%), {a}/{t} all-hit ({a/t*100:.0f}%)")

    misses = results["misses"]
    if misses:
        print(f"\n--- 未命中 TOP 20 ---")
        for m in misses[:20]:
            print(f"  [{m['category']}] \"{m['input']}\"")
            print(f"    期望: {m['missed']} | 实际选了: {m['selected'][:5]}")

    # 保存完整结果
    output_path = backend_dir / "scripts" / "benchmark_results.json"
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(f"\n完整结果已保存: {output_path}")


# ============================================================
# Main
# ============================================================


async def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--count", type=int, default=200)
    parser.add_argument("--load", type=str, help="加载已有用例文件（跳过生成）")
    args = parser.parse_args()

    if args.load:
        with open(args.load, encoding="utf-8") as f:
            cases = json.load(f)
        logger.info(f"Loaded {len(cases)} cases from {args.load}")
    else:
        logger.info(f"Generating {args.count} test cases via qwen-plus...")
        cases = await generate_test_cases(args.count)
        # 保存生成的用例
        cases_path = backend_dir / "scripts" / "benchmark_cases.json"
        with open(cases_path, "w", encoding="utf-8") as f:
            json.dump(cases, f, ensure_ascii=False, indent=2)
        logger.info(f"Cases saved to {cases_path}")

    logger.info(f"Running benchmark on {len(cases)} cases...")
    results = await run_benchmark(cases)
    print_report(results)


if __name__ == "__main__":
    asyncio.run(main())
