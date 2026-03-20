"""
v2 真实 LLM 集成测试 — Phase 1 用真千问路由，Phase 2 mock executor

测试策略：
- _call_brain: 真实调用千问 API（验证 Phase 1 意图分类 + Phase 2 工具选择）
- executor.execute: mock 返回（避免真调 ERP API 修改数据）
- _get_recent_history / _fetch_knowledge: mock（无需真 DB）

运行：
  source backend/venv/bin/activate
  python scripts/test_v2_real_llm.py
"""

import asyncio
import json
import sys
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, MagicMock, patch

# 将 backend 加入 path
sys.path.insert(0, ".")

from core.config import settings
from schemas.message import GenerationType, TextPart
from services.agent_loop import AgentLoop


# ============================================================
# 配置
# ============================================================

# 用于 Phase 2 executor 的通用 mock 返回
MOCK_ERP_RESULTS: Dict[str, str] = {
    # local_product_identify
    "local_product_identify": json.dumps({
        "type": "product", "outer_id": "TEST-001",
        "name": "测试商品",
    }),
    # 各种查询默认返回
    "erp_product_query": json.dumps({
        "total": 5, "items": [
            {"outer_id": "SKU-001", "name": "商品A", "available": 100},
        ],
    }),
    "erp_trade_query": json.dumps({
        "total": 42, "orders": [
            {"order_id": "126036803257340376", "status": "WAIT_SEND_GOODS"},
        ],
    }),
    "erp_aftersales_query": json.dumps({
        "total": 3, "items": [{"type": 2, "status": "processing"}],
    }),
    "erp_info_query": json.dumps({
        "shops": [{"id": 1, "name": "天猫旗舰店"}, {"id": 2, "name": "京东自营"}],
        "warehouses": [{"id": 1, "name": "北京仓"}, {"id": 2, "name": "上海仓"}],
    }),
    "erp_warehouse_query": json.dumps({
        "total": 2, "items": [{"code": "DB20260319001", "status": "OUTING"}],
    }),
    "erp_purchase_query": json.dumps({
        "total": 5, "items": [{"code": "CG20260319001", "status": "GOODS_NOT_ARRIVED"}],
    }),
    "erp_taobao_query": json.dumps({
        "total": 25, "trades": [{"tid": "126036803257340376", "status": "paid"}],
    }),
    "erp_execute": json.dumps({"success": True, "message": "操作成功"}),
    "erp_api_search": json.dumps({
        "results": [
            {"tool": "erp_trade_query", "action": "order_list", "desc": "订单查询"},
        ],
    }),
    "code_execute": json.dumps({"result": "计算完成", "output": "统计结果"}),
    # crawler
    "social_crawler": json.dumps({
        "total": 10, "notes": [{"title": "防晒霜推荐", "likes": 5000}],
    }),
}


@dataclass
class TestCase:
    """测试用例"""
    name: str
    user_text: str
    expected_domain: str  # chat/erp/crawler/image/video/ask_user
    expected_gen_type: GenerationType
    has_image: bool = False
    description: str = ""


# ============================================================
# 测试用例（覆盖各种提问方式）
# ============================================================

TEST_CASES: List[TestCase] = [
    # ── Chat 域 ──
    TestCase("chat_greeting", "你好", "chat", GenerationType.CHAT,
             description="简单问候"),
    TestCase("chat_code", "帮我写一个Python快排算法", "chat", GenerationType.CHAT,
             description="代码请求"),
    TestCase("chat_translate", "翻译成英文：今天天气真好", "chat", GenerationType.CHAT,
             description="翻译"),
    TestCase("chat_search", "今天杭州天气怎么样", "chat", GenerationType.CHAT,
             description="搜索类（weather）"),
    TestCase("chat_math", "帮我算一下 235 × 47", "chat", GenerationType.CHAT,
             description="数学计算"),

    # ── Image 域 ──
    TestCase("image_single", "画一只可爱的柴犬", "image", GenerationType.IMAGE,
             description="单图生成"),
    TestCase("image_batch", "画4张不同风格的日落照片", "image", GenerationType.IMAGE,
             description="批量图片"),

    # ── Video 域 ──
    TestCase("video_gen", "帮我生成一段猫咪玩耍的视频", "video", GenerationType.VIDEO,
             description="视频生成"),

    # ── ERP 域：订单查询 ──
    TestCase("erp_today_orders", "今天多少订单", "erp", GenerationType.CHAT,
             description="今天订单数（统计类）"),
    TestCase("erp_pending_ship", "有多少待发货的", "erp", GenerationType.CHAT,
             description="待发货订单"),
    TestCase("erp_order_by_id", "查一下淘宝单126036803257340376", "erp", GenerationType.CHAT,
             description="按订单号查询"),
    TestCase("erp_shipped_today", "今天发了多少货", "erp", GenerationType.CHAT,
             description="今日发货量"),

    # ── ERP 域：库存查询 ──
    TestCase("erp_stock_check", "SHOE-001还有多少库存", "erp", GenerationType.CHAT,
             description="库存查询"),
    TestCase("erp_low_stock", "哪些商品快没货了", "erp", GenerationType.CHAT,
             description="库存预警"),
    TestCase("erp_warehouse_stock", "北京仓和上海仓各有多少货", "erp", GenerationType.CHAT,
             description="分仓库存"),

    # ── ERP 域：售后 ──
    TestCase("erp_aftersales", "最近三天有多少退货", "erp", GenerationType.CHAT,
             description="退货统计"),
    TestCase("erp_refund", "今天淘宝退款多少", "erp", GenerationType.CHAT,
             description="淘宝退款"),

    # ── ERP 域：采购 ──
    TestCase("erp_purchase", "这个月有多少采购单还没到", "erp", GenerationType.CHAT,
             description="采购单查询"),

    # ── ERP 域：基础信息 ──
    TestCase("erp_shop_list", "我们有几个店铺", "erp", GenerationType.CHAT,
             description="店铺列表"),
    TestCase("erp_warehouse_list", "仓库有哪些", "erp", GenerationType.CHAT,
             description="仓库列表"),

    # ── ERP 域：口语化 ──
    TestCase("erp_casual_sales", "今天卖了多少钱", "erp", GenerationType.CHAT,
             description="口语化成交额"),
    TestCase("erp_urgent", "急！货发不出去了怎么回事", "erp", GenerationType.CHAT,
             description="紧急口语"),

    # ── Crawler 域 ──
    TestCase("crawler_xhs", "帮我搜一下小红书上防晒霜推荐", "crawler", GenerationType.CHAT,
             description="小红书搜索"),
    TestCase("crawler_douyin", "搜一下抖音上最火的穿搭视频", "crawler", GenerationType.CHAT,
             description="抖音搜索"),

    # ── 边界 ──
    TestCase("edge_ambiguous", "帮我看看这个", "chat", GenerationType.CHAT,
             description="模糊表达（应走chat或ask_user）"),
]


# ============================================================
# 执行器
# ============================================================


def _make_real_loop() -> AgentLoop:
    """创建带真实 _call_brain 的 AgentLoop"""
    loop = AgentLoop(db=None, user_id="real_test", conversation_id="real_conv")
    # 使用真实 settings
    loop._settings = settings
    # 强制开启 v2
    loop._settings.agent_loop_v2_enabled = True
    loop._has_image = False
    loop._thinking_mode = None
    loop._user_location = None
    loop._task_id = None
    loop._phase1_model = ""
    return loop


async def run_single_test(tc: TestCase) -> Dict[str, Any]:
    """运行单个测试用例"""
    loop = _make_real_loop()
    loop._has_image = tc.has_image

    async def mock_executor(name, args):
        return MOCK_ERP_RESULTS.get(name, '{"result":"ok"}')

    start = time.time()
    try:
        with patch.object(loop, "_get_recent_history",
                          new_callable=AsyncMock, return_value=None), \
             patch.object(loop, "_fetch_knowledge",
                          new_callable=AsyncMock, return_value=None), \
             patch.object(loop, "_notify_progress",
                          new_callable=AsyncMock), \
             patch.object(loop, "_fire_and_forget_knowledge"), \
             patch.object(loop, "_record_ask_user_context"), \
             patch.object(loop.executor, "execute",
                          new_callable=AsyncMock, side_effect=mock_executor):
            result = await loop._execute_loop_v2([TextPart(text=tc.user_text)])

        elapsed = time.time() - start
        actual_domain = _infer_domain(result)
        domain_ok = _check_domain(tc.expected_domain, actual_domain)
        gen_ok = result.generation_type == tc.expected_gen_type

        return {
            "name": tc.name,
            "user_text": tc.user_text,
            "expected_domain": tc.expected_domain,
            "actual_domain": actual_domain,
            "expected_gen": tc.expected_gen_type.value,
            "actual_gen": result.generation_type.value,
            "model": result.model,
            "turns": result.turns_used,
            "tokens": result.total_tokens,
            "domain_ok": domain_ok,
            "gen_ok": gen_ok,
            "passed": domain_ok and gen_ok,
            "elapsed": round(elapsed, 2),
            "error": None,
            "direct_reply": result.direct_reply[:80] if result.direct_reply else None,
        }
    except Exception as e:
        elapsed = time.time() - start
        return {
            "name": tc.name,
            "user_text": tc.user_text,
            "expected_domain": tc.expected_domain,
            "actual_domain": "ERROR",
            "expected_gen": tc.expected_gen_type.value,
            "actual_gen": "ERROR",
            "model": "",
            "turns": 0,
            "tokens": 0,
            "domain_ok": False,
            "gen_ok": False,
            "passed": False,
            "elapsed": round(elapsed, 2),
            "error": str(e),
            "direct_reply": None,
        }


def _infer_domain(result) -> str:
    """从 AgentResult 反推 domain"""
    if result.generation_type == GenerationType.IMAGE:
        return "image"
    if result.generation_type == GenerationType.VIDEO:
        return "video"
    # CHAT 可能来自 chat/erp/crawler/ask_user
    if result.direct_reply and result.tool_params.get("_ask_reason"):
        return "ask_user"
    if result.turns_used > 1:
        # 多轮说明走了 Phase 2（erp 或 crawler）
        return "erp_or_crawler"
    return "chat"


def _check_domain(expected: str, actual: str) -> bool:
    """检查 domain 是否匹配"""
    if expected == actual:
        return True
    # erp 和 crawler 都可能被检测为 erp_or_crawler
    if expected in ("erp", "crawler") and actual == "erp_or_crawler":
        return True
    # ask_user 被当作 chat 也算可接受（模糊表达时）
    if expected == "chat" and actual == "ask_user":
        return True
    if expected == "ask_user" and actual == "chat":
        return True
    return False


# ============================================================
# Main
# ============================================================


async def main():
    print("=" * 70)
    print("  v2 真实 LLM 集成测试 — Phase 1 真千问 + Phase 2 Mock Executor")
    print("=" * 70)
    print(f"  模型: {settings.agent_loop_model}")
    print(f"  Base URL: {settings.dashscope_base_url}")
    print(f"  测试数量: {len(TEST_CASES)}")
    print("=" * 70)

    results = []
    for i, tc in enumerate(TEST_CASES):
        print(f"\n[{i+1}/{len(TEST_CASES)}] {tc.name}: \"{tc.user_text}\"")
        print(f"  期望: domain={tc.expected_domain}, gen={tc.expected_gen_type.value}")

        r = await run_single_test(tc)
        results.append(r)

        status = "✅ PASS" if r["passed"] else "❌ FAIL"
        print(f"  结果: domain={r['actual_domain']}, gen={r['actual_gen']}, "
              f"model={r['model']}, turns={r['turns']}, "
              f"tokens={r['tokens']}, {r['elapsed']}s")
        if r["error"]:
            print(f"  错误: {r['error']}")
        if r["direct_reply"]:
            print(f"  回复: {r['direct_reply']}")
        print(f"  {status}")

    # ── 汇总 ──
    print("\n" + "=" * 70)
    print("  测试汇总")
    print("=" * 70)

    passed = sum(1 for r in results if r["passed"])
    failed = sum(1 for r in results if not r["passed"])
    total_tokens = sum(r["tokens"] for r in results)
    total_time = sum(r["elapsed"] for r in results)

    print(f"\n  通过: {passed}/{len(results)}")
    print(f"  失败: {failed}/{len(results)}")
    print(f"  总Token: {total_tokens}")
    print(f"  总耗时: {total_time:.1f}s")
    print(f"  平均耗时: {total_time/len(results):.1f}s/case")

    if failed:
        print("\n  ❌ 失败用例:")
        for r in results:
            if not r["passed"]:
                print(f"    - {r['name']}: expected={r['expected_domain']}/{r['expected_gen']}"
                      f" actual={r['actual_domain']}/{r['actual_gen']}")
                if r["error"]:
                    print(f"      error: {r['error']}")

    # ── 按域统计 ──
    print("\n  按域统计:")
    domains = {}
    for r in results:
        d = r["expected_domain"]
        if d not in domains:
            domains[d] = {"pass": 0, "fail": 0, "tokens": 0}
        if r["passed"]:
            domains[d]["pass"] += 1
        else:
            domains[d]["fail"] += 1
        domains[d]["tokens"] += r["tokens"]

    for d, stats in sorted(domains.items()):
        total = stats["pass"] + stats["fail"]
        print(f"    {d:12s}: {stats['pass']}/{total} passed | "
              f"avg tokens={stats['tokens']//total if total else 0}")

    print("\n" + "=" * 70)
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
