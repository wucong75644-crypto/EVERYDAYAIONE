"""
Phase 3 端到端路由验证脚本

直接调用 ERPAgent，检查 10 个典型场景的工具调用链。
需要在有 .env 配置的环境中运行（本地或服务器）。

用法:
    cd backend
    source venv/bin/activate
    python scripts/test_agent_routing.py
"""

import asyncio
import sys
from pathlib import Path

# 添加 backend 到 sys.path
backend_dir = Path(__file__).parent.parent
sys.path.insert(0, str(backend_dir))

from dotenv import load_dotenv
load_dotenv(backend_dir / ".env")


# 10 个测试场景
SCENARIOS = [
    {
        "name": "统计今天订单数",
        "query": "今天一共多少单",
        "expect_tools": ["local_global_stats"],
        "reject_tools": ["code_execute", "erp_trade_query", "fetch_all_pages"],
    },
    {
        "name": "时间段付款订单涨跌幅",
        "query": "统计一下今天和昨天到下午七点钟付款订单的涨跌幅",
        "expect_tools": ["local_global_stats"],
        "reject_tools": ["code_execute", "erp_trade_query", "fetch_all_pages"],
    },
    {
        "name": "各店铺销量排名",
        "query": "各店铺本月销量排名",
        "expect_tools": ["local_global_stats"],
        "reject_tools": ["code_execute", "fetch_all_pages"],
    },
    {
        "name": "查库存",
        "query": "查一下编码 HZ0010101 的库存",
        "expect_tools": ["local_stock_query"],
        "reject_tools": ["code_execute", "fetch_all_pages"],
    },
    {
        "name": "导出订单到Excel",
        "query": "帮我导出今天的订单到Excel",
        "expect_tools": ["fetch_all_pages", "code_execute"],
        "reject_tools": [],
    },
    {
        "name": "各平台退货对比",
        "query": "对比各平台本月退货统计",
        "expect_tools": ["local_global_stats"],
        "reject_tools": ["code_execute", "fetch_all_pages"],
    },
    {
        "name": "商品编码识别",
        "query": "查一下花洒的编码",
        "expect_tools": ["local_product_identify"],
        "reject_tools": ["code_execute", "fetch_all_pages"],
    },
    {
        "name": "采购到货进度",
        "query": "查一下编码 HZ0010101 的采购到货进度",
        "expect_tools": ["local_purchase_query"],
        "reject_tools": ["code_execute", "fetch_all_pages"],
    },
    {
        "name": "店铺列表",
        "query": "我们有哪些店铺",
        "expect_tools": ["local_shop_list"],
        "reject_tools": ["code_execute", "fetch_all_pages"],
    },
    {
        "name": "平台映射",
        "query": "编码 HZ0010101 在哪些平台有售",
        "expect_tools": ["local_platform_map_query"],
        "reject_tools": ["code_execute", "fetch_all_pages"],
    },
]


async def run_scenario(agent_cls, db, scenario, org_id):
    """执行单个场景，返回 (通过, 工具链, 耗时, 错误)"""
    import time

    agent = agent_cls(
        db=db,
        user_id="routing-test",
        conversation_id=f"routing-test-{int(time.time())}",
        org_id=org_id,
    )

    start = time.monotonic()
    try:
        result = await agent.execute(scenario["query"])
    except Exception as e:
        return False, [], 0, str(e)
    elapsed = time.monotonic() - start

    tools = result.tools_called or []

    # 检查期望工具是否出现
    missing = [t for t in scenario["expect_tools"] if t not in tools]
    # 检查拒绝工具是否出现
    unwanted = [t for t in scenario["reject_tools"] if t in tools]

    passed = not missing and not unwanted
    error = ""
    if missing:
        error += f"缺少: {missing}"
    if unwanted:
        error += f" 不应出现: {unwanted}"

    return passed, tools, elapsed, error


async def main():
    from unittest.mock import MagicMock
    from core.config import get_settings

    settings = get_settings()

    # 检查必要配置
    if not settings.dashscope_api_key:
        print("DASHSCOPE_API_KEY 未配置，无法运行")
        sys.exit(1)

    # 获取 org_id（使用蓝创的）
    org_id = "eadc4c11-7e83-4279-a849-cfe0cbf6982b"

    # 初始化数据库（本地查询需要）
    from core.database import get_db
    db = get_db()

    from services.agent.erp_agent import ERPAgent

    print("=" * 60)
    print("Phase 3: Agent 路由验证（10 个场景）")
    print("=" * 60)
    print()

    results = []
    for i, scenario in enumerate(SCENARIOS, 1):
        name = scenario["name"]
        print(f"[{i}/10] {name}: {scenario['query'][:30]}...")

        passed, tools, elapsed, error = await run_scenario(
            ERPAgent, db, scenario, org_id,
        )

        status = "✅ PASS" if passed else "❌ FAIL"
        tools_str = " → ".join(tools) if tools else "(无)"
        print(f"       {status} | {elapsed:.1f}s | {tools_str}")
        if error:
            print(f"       原因: {error}")
        print()

        results.append({
            "name": name, "passed": passed,
            "tools": tools, "elapsed": elapsed, "error": error,
        })

    # 汇总
    print("=" * 60)
    passed_count = sum(1 for r in results if r["passed"])
    total = len(results)
    print(f"结果: {passed_count}/{total} 通过")
    print()

    for r in results:
        status = "✅" if r["passed"] else "❌"
        print(f"  {status} {r['name']}: {' → '.join(r['tools']) or '(无)'}")
        if r["error"]:
            print(f"     {r['error']}")

    print()
    if passed_count == total:
        print("所有场景通过，Phase 3 验证完成！")
    else:
        failed = [r for r in results if not r["passed"]]
        print(f"{len(failed)} 个场景需要调整提示词：")
        for r in failed:
            print(f"  - {r['name']}: {r['error']}")

    return passed_count == total


if __name__ == "__main__":
    success = asyncio.run(main())
    sys.exit(0 if success else 1)
