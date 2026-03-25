"""
v2 真实 LLM 深度测试 — 模糊语义 + 深度用法 + 多功能组合

测试策略同 test_v2_real_llm.py：
- _call_brain: 真实千问 API
- executor.execute: mock 返回
- 三大测试板块：
  A. 模糊语义（20 场景）：意图不明/多义/省略/错字/混合语言
  B. 深度用法（15 场景）：复杂ERP链路/高级参数/条件组合
  C. 多功能组合（15 场景）：跨域混合/先查后画/先搜后分析

运行：
  cd backend
  source venv/bin/activate
  python scripts/test_v2_real_llm_deep.py
"""

import asyncio
import json
import sys
import time
from dataclasses import dataclass
from typing import Any, Dict, List
from unittest.mock import AsyncMock, patch

sys.path.insert(0, ".")

from core.config import settings
from schemas.message import GenerationType, TextPart
from services.agent_loop import AgentLoop


# ============================================================
# Mock 数据
# ============================================================

MOCK_RESULTS: Dict[str, str] = {
    "local_product_identify": json.dumps({
        "type": "product", "outer_id": "TEST-001",
        "name": "测试商品",
    }),
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
        "total": 2, "items": [{"code": "DB20260319001"}],
    }),
    "erp_purchase_query": json.dumps({
        "total": 5, "items": [{"code": "CG20260319001"}],
    }),
    "erp_taobao_query": json.dumps({
        "total": 25, "trades": [{"tid": "126036803257340376"}],
    }),
    "erp_execute": json.dumps({"success": True}),
    "erp_api_search": json.dumps({
        "results": [{"tool": "erp_trade_query", "action": "order_list"}],
    }),
    "code_execute": json.dumps({"result": "done", "output": "统计结果"}),
    "social_crawler": json.dumps({
        "total": 10, "notes": [{"title": "推荐", "likes": 5000}],
    }),
}


@dataclass
class TestCase:
    name: str
    user_text: str
    expected_domain: str  # chat/erp/crawler/image/video/ask_user
    expected_gen_type: GenerationType
    has_image: bool = False
    # 允许多个可接受的 domain（模糊场景用）
    also_accept: str = ""
    description: str = ""


# ============================================================
# A. 模糊语义测试（20 场景）
# ============================================================

AMBIGUOUS_CASES: List[TestCase] = [
    # ── 极短/无意义输入 ──
    TestCase("amb_single_char", "?",
             "chat", GenerationType.CHAT, also_accept="ask_user",
             description="单个问号"),
    TestCase("amb_emoji_only", "👍",
             "chat", GenerationType.CHAT, also_accept="ask_user",
             description="纯表情"),
    TestCase("amb_ellipsis", "...",
             "chat", GenerationType.CHAT, also_accept="ask_user",
             description="省略号"),
    TestCase("amb_ok", "好的",
             "chat", GenerationType.CHAT,
             description="确认词"),
    TestCase("amb_hmm", "嗯嗯",
             "chat", GenerationType.CHAT,
             description="语气词"),

    # ── 多义表达 ──
    TestCase("amb_check_it", "帮我看看这个",
             "chat", GenerationType.CHAT, also_accept="ask_user",
             description="缺少宾语——看什么？"),
    TestCase("amb_handle_it", "处理一下",
             "chat", GenerationType.CHAT, also_accept="ask_user",
             description="缺少对象——处理什么？"),
    TestCase("amb_number_only", "12345",
             "chat", GenerationType.CHAT, also_accept="erp",
             description="纯数字（可能是编码也可能是随手打）"),
    TestCase("amb_how_much", "多少钱",
             "chat", GenerationType.CHAT, also_accept="erp",
             description="多少钱——可能问商品也可能闲聊"),
    TestCase("amb_send_it", "发一下",
             "chat", GenerationType.CHAT, also_accept="ask_user",
             description="发什么？快递？文件？"),

    # ── 近似域混淆 ──
    TestCase("amb_draw_vs_search", "柴犬",
             "chat", GenerationType.CHAT, also_accept="image",
             description="单词——可能想画也可能想聊"),
    TestCase("amb_sunset_topic", "日落真美",
             "chat", GenerationType.CHAT,
             description="感叹句——不是画图请求"),
    TestCase("amb_product_chat", "防晒霜哪个好",
             "chat", GenerationType.CHAT, also_accept="crawler",
             description="可能问推荐也可能想搜小红书"),
    TestCase("amb_order_chat", "我的订单呢",
             "erp", GenerationType.CHAT, also_accept="ask_user",
             description="口语化问订单但没给单号"),
    TestCase("amb_stock_vague", "还有没有",
             "chat", GenerationType.CHAT, also_accept="ask_user",
             description="极度模糊的库存问题"),

    # ── 错字/口语/网络用语 ──
    TestCase("amb_typo_order", "帮我查一下丁单",
             "erp", GenerationType.CHAT, also_accept="ask_user",
             description="订单打成丁单"),
    TestCase("amb_slang_pic", "搞张图",
             "image", GenerationType.IMAGE, also_accept="chat",
             description="口语化图片请求"),
    TestCase("amb_mixed_lang", "help我查个order",
             "erp", GenerationType.CHAT, also_accept="chat",
             description="中英混合"),
    TestCase("amb_voice_typo", "库村还有多少",
             "erp", GenerationType.CHAT, also_accept="ask_user",
             description="语音识别错误：库存→库村"),
    TestCase("amb_repeat", "再来",
             "chat", GenerationType.CHAT, also_accept="ask_user",
             description="再来——但没有上下文不知道来什么"),
]

# ============================================================
# B. 深度用法测试（15 场景）
# ============================================================

DEEP_USAGE_CASES: List[TestCase] = [
    # ── ERP 复杂查询 ──
    TestCase("deep_cross_time", "对比一下上周和这周的订单量",
             "erp", GenerationType.CHAT,
             description="跨时间对比——需两次查询"),
    TestCase("deep_multi_status", "待审核和待发货的单子各有多少",
             "erp", GenerationType.CHAT,
             description="多状态并行查询"),
    TestCase("deep_sales_rank", "这周销量前10的商品是什么",
             "erp", GenerationType.CHAT,
             description="销量排行需聚合计算"),
    TestCase("deep_refund_rate", "这个月退货率多少",
             "erp", GenerationType.CHAT,
             description="需要订单总量+退货量两次查询再算比率"),
    TestCase("deep_slow_moving", "哪些商品30天没动销",
             "erp", GenerationType.CHAT,
             description="滞销商品分析"),
    TestCase("deep_supplier_perf", "供应商A的采购到货率怎么样",
             "erp", GenerationType.CHAT,
             description="供应商绩效——需采购+收货交叉查"),
    TestCase("deep_platform_compare", "淘宝和京东今天哪个卖的多",
             "erp", GenerationType.CHAT,
             description="跨平台对比"),
    TestCase("deep_daily_report", "给我出一个今天的运营日报",
             "erp", GenerationType.CHAT,
             description="综合日报——订单+发货+退货+销售额"),

    # ── 高级参数用法 ──
    TestCase("deep_archived_purchase", "去年双11的采购单",
             "erp", GenerationType.CHAT,
             description="归档数据——需用history action"),
    TestCase("deep_order_log", "126036803257340376 这单的操作记录",
             "erp", GenerationType.CHAT,
             description="订单日志——先查system_id再查order_log"),
    TestCase("deep_batch_stock", "A001到A020这20个商品的库存",
             "erp", GenerationType.CHAT,
             description="批量编码查库存"),

    # ── Chat 深度用法 ──
    TestCase("deep_code_review", "帮我review这段代码看有没有bug：def add(a,b): return a-b",
             "chat", GenerationType.CHAT,
             description="代码审查请求"),
    TestCase("deep_long_translate", "把以下内容翻译成日语并保持格式：第一章 开始\n1.1 简介\n1.2 目标",
             "chat", GenerationType.CHAT,
             description="复杂翻译请求"),
    TestCase("deep_explain_concept", "用小学生能理解的话解释量子计算",
             "chat", GenerationType.CHAT,
             description="知识解释"),
    TestCase("deep_debate", "AI会不会取代程序员？正反两方面分析",
             "chat", GenerationType.CHAT,
             description="辩论/分析类"),
]

# ============================================================
# C. 多功能组合测试（15 场景）
# ============================================================

COMBO_CASES: List[TestCase] = [
    # ── 查+画 ──
    TestCase("combo_search_then_draw", "帮我搜一下最流行的logo设计风格，然后画一个",
             "chat", GenerationType.CHAT, also_accept="image,crawler",
             description="先搜索后画图——看LLM怎么拆解"),
    TestCase("combo_erp_then_chart", "查一下这周每天的订单量，画个趋势图",
             "erp", GenerationType.CHAT, also_accept="image",
             description="ERP数据+可视化"),

    # ── 查+分析 ──
    TestCase("combo_crawl_analyze", "搜一下小红书上竞品的评价，帮我分析优缺点",
             "crawler", GenerationType.CHAT,
             description="爬虫+分析"),
    TestCase("combo_erp_advice", "库存不够的商品帮我生成采购建议",
             "erp", GenerationType.CHAT,
             description="库存查询+采购建议"),
    TestCase("combo_aftersales_report", "统计这个月的退货原因并给出改进建议",
             "erp", GenerationType.CHAT,
             description="售后统计+分析建议"),

    # ── 跨域请求 ──
    TestCase("combo_erp_and_search", "查一下我们SHOE-001的库存，顺便搜搜行业均价",
             "erp", GenerationType.CHAT, also_accept="crawler",
             description="ERP库存+行业搜索"),
    TestCase("combo_translate_and_draw", "用英文描述一个赛博朋克城市，然后生成图片",
             "image", GenerationType.IMAGE, also_accept="chat",
             description="翻译+画图"),
    TestCase("combo_code_and_explain", "写一个二分查找算法，然后用图解释原理",
             "chat", GenerationType.CHAT, also_accept="image",
             description="代码+图解"),

    # ── 条件句/if-then ──
    TestCase("combo_if_stock_low", "如果SHOE-001库存低于50就帮我下采购单",
             "erp", GenerationType.CHAT,
             description="条件判断+写操作"),
    TestCase("combo_if_order_late", "如果有超过3天没发货的订单就列出来",
             "erp", GenerationType.CHAT,
             description="条件筛选"),

    # ── 比较/对比 ──
    TestCase("combo_compare_models", "GPT和Claude哪个写代码更好",
             "chat", GenerationType.CHAT,
             description="AI对比讨论"),
    TestCase("combo_product_compare", "A001和B002哪个卖得好",
             "erp", GenerationType.CHAT,
             description="商品销量对比"),

    # ── 多步指令 ──
    TestCase("combo_multi_step", "先查一下今天退了多少单，然后找到退货最多的商品，最后看它的库存",
             "erp", GenerationType.CHAT,
             description="三步串联指令"),
    TestCase("combo_report_and_send", "帮我统计一下今天的销售数据，整理成表格",
             "erp", GenerationType.CHAT,
             description="统计+格式化"),
    TestCase("combo_draw_product", "帮我设计一张我们SHOE-001的宣传海报",
             "image", GenerationType.IMAGE, also_accept="chat",
             description="需知道产品信息才能画——但可能直接画"),
]


# ============================================================
# 执行器（复用 test_v2_real_llm.py 逻辑）
# ============================================================


def _make_loop() -> AgentLoop:
    loop = AgentLoop(db=None, user_id="deep_test", conversation_id="deep_conv")
    loop._settings = settings
    loop._has_image = False
    loop._thinking_mode = None
    loop._user_location = None
    loop._task_id = None
    loop._phase1_model = ""
    return loop


def _infer_domain(result) -> str:
    if result.generation_type == GenerationType.IMAGE:
        return "image"
    if result.generation_type == GenerationType.VIDEO:
        return "video"
    if result.direct_reply and result.tool_params.get("_ask_reason"):
        return "ask_user"
    if result.turns_used > 1:
        return "erp_or_crawler"
    return "chat"


def _check_domain(expected: str, actual: str, also_accept: str = "") -> bool:
    if expected == actual:
        return True
    if expected in ("erp", "crawler") and actual == "erp_or_crawler":
        return True
    if expected == "chat" and actual == "ask_user":
        return True
    if expected == "ask_user" and actual == "chat":
        return True
    # 检查 also_accept
    if also_accept:
        for alt in also_accept.split(","):
            alt = alt.strip()
            if alt == actual:
                return True
            if alt in ("erp", "crawler") and actual == "erp_or_crawler":
                return True
    return False


async def run_test(tc: TestCase) -> Dict[str, Any]:
    loop = _make_loop()
    loop._has_image = tc.has_image

    async def mock_exec(name, args):
        return MOCK_RESULTS.get(name, '{"result":"ok"}')

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
                          new_callable=AsyncMock, side_effect=mock_exec):
            result = await loop._execute_loop_v2([TextPart(text=tc.user_text)])

        elapsed = time.time() - start
        actual_domain = _infer_domain(result)
        domain_ok = _check_domain(tc.expected_domain, actual_domain, tc.also_accept)
        gen_ok = result.generation_type == tc.expected_gen_type
        # 对于 also_accept 包含不同 gen_type 的场景放宽
        if not gen_ok and tc.also_accept:
            for alt in tc.also_accept.split(","):
                alt = alt.strip()
                if alt == "image" and result.generation_type == GenerationType.IMAGE:
                    gen_ok = True
                elif alt == "video" and result.generation_type == GenerationType.VIDEO:
                    gen_ok = True
                elif alt in ("chat", "erp", "crawler", "ask_user") and result.generation_type == GenerationType.CHAT:
                    gen_ok = True

        return {
            "name": tc.name,
            "text": tc.user_text,
            "expected": tc.expected_domain,
            "actual": actual_domain,
            "also_accept": tc.also_accept,
            "gen_expected": tc.expected_gen_type.value,
            "gen_actual": result.generation_type.value,
            "model": result.model,
            "turns": result.turns_used,
            "tokens": result.total_tokens,
            "domain_ok": domain_ok,
            "gen_ok": gen_ok,
            "passed": domain_ok and gen_ok,
            "elapsed": round(elapsed, 2),
            "error": None,
            "reply": (result.direct_reply or "")[:100],
            "desc": tc.description,
        }
    except Exception as e:
        return {
            "name": tc.name, "text": tc.user_text,
            "expected": tc.expected_domain, "actual": "ERROR",
            "also_accept": tc.also_accept,
            "gen_expected": tc.expected_gen_type.value, "gen_actual": "ERROR",
            "model": "", "turns": 0, "tokens": 0,
            "domain_ok": False, "gen_ok": False, "passed": False,
            "elapsed": round(time.time() - start, 2),
            "error": str(e)[:200], "reply": "", "desc": tc.description,
        }


async def run_section(name: str, cases: List[TestCase]) -> List[Dict]:
    print(f"\n{'─' * 70}")
    print(f"  {name}（{len(cases)} 场景）")
    print(f"{'─' * 70}")

    results = []
    for i, tc in enumerate(cases):
        print(f"\n  [{i+1}/{len(cases)}] {tc.name}: \"{tc.user_text}\"")
        print(f"    期望: {tc.expected_domain}"
              f"{' (也可:'+tc.also_accept+')' if tc.also_accept else ''}"
              f" | {tc.description}")

        r = await run_test(tc)
        results.append(r)

        status = "✅" if r["passed"] else "❌"
        print(f"    结果: domain={r['actual']}, gen={r['gen_actual']}, "
              f"model={r['model']}, turns={r['turns']}, "
              f"tokens={r['tokens']}, {r['elapsed']}s  {status}")
        if r["reply"]:
            print(f"    回复: {r['reply']}")
        if r["error"]:
            print(f"    错误: {r['error']}")

    return results


async def main():
    print("=" * 70)
    print("  v2 深度测试 — 模糊语义 + 深度用法 + 多功能组合")
    print("=" * 70)
    print(f"  模型: {settings.agent_loop_model}")
    total = len(AMBIGUOUS_CASES) + len(DEEP_USAGE_CASES) + len(COMBO_CASES)
    print(f"  总测试: {total} 场景")
    print("=" * 70)

    all_results = []

    r1 = await run_section("A. 模糊语义", AMBIGUOUS_CASES)
    all_results.extend(r1)

    r2 = await run_section("B. 深度用法", DEEP_USAGE_CASES)
    all_results.extend(r2)

    r3 = await run_section("C. 多功能组合", COMBO_CASES)
    all_results.extend(r3)

    # ── 汇总 ──
    print(f"\n{'=' * 70}")
    print("  总汇总")
    print(f"{'=' * 70}")

    passed = sum(1 for r in all_results if r["passed"])
    failed = sum(1 for r in all_results if not r["passed"])
    total_tokens = sum(r["tokens"] for r in all_results)
    total_time = sum(r["elapsed"] for r in all_results)

    sections = [
        ("A. 模糊语义", r1),
        ("B. 深度用法", r2),
        ("C. 多功能组合", r3),
    ]
    for sec_name, sec_results in sections:
        sec_pass = sum(1 for r in sec_results if r["passed"])
        sec_total = len(sec_results)
        print(f"  {sec_name}: {sec_pass}/{sec_total}")

    print(f"\n  总通过: {passed}/{len(all_results)}")
    print(f"  总失败: {failed}/{len(all_results)}")
    print(f"  总Token: {total_tokens}")
    print(f"  总耗时: {total_time:.1f}s")
    print(f"  平均: {total_time/len(all_results):.1f}s/case")

    if failed:
        print(f"\n  ❌ 失败详情:")
        for r in all_results:
            if not r["passed"]:
                print(f"    {r['name']}: \"{r['text']}\"")
                print(f"      期望={r['expected']}"
                      f"{'(+'+r['also_accept']+')' if r['also_accept'] else ''}"
                      f" → 实际={r['actual']}/{r['gen_actual']}")
                print(f"      {r['desc']}")
                if r["error"]:
                    print(f"      error: {r['error']}")

    # 按实际 domain 分布
    print(f"\n  实际 domain 分布:")
    dist = {}
    for r in all_results:
        d = r["actual"]
        dist[d] = dist.get(d, 0) + 1
    for d, cnt in sorted(dist.items(), key=lambda x: -x[1]):
        print(f"    {d:18s}: {cnt}")

    print(f"\n{'=' * 70}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
