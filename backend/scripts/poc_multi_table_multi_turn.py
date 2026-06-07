#!/usr/bin/env python3
"""POC 严苛版: 5 个中文表 + 多轮对话 + 跨轮语义指代

复现真实场景:
- 用户先上传 5 个中文文件名的表格
- 都做了 file_analyze 治理(都有 ASCII parquet cache 路径)
- 多轮对话依次问不同表的问题(语义指代:"销售表"/"库存"/"那个采购的")
- 验证 LLM 跨轮始终用对的 ASCII parquet 路径
"""
from __future__ import annotations
import asyncio, json, re, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

# 5 个真实场景中文文件名 + ASCII cache 路径
FILES = [
    {
        "name": "4月销售主题分析-按订单商品明细-20260508134809.xlsx",
        "parquet": "staging/_cache_v3.0_037237fcf9f7_sheet0.parquet",
        "semantic": "销售",
        "cols": ["平台订单号", "店铺名称", "所属平台", "销售金额", "商品名称", "数量"],
    },
    {
        "name": "运营名下店铺分组_2c5340.xlsx",
        "parquet": "staging/_cache_v3.0_6db4d68a87b8_sheet0.parquet",
        "semantic": "运营/店铺分组",
        "cols": ["店铺", "运营人员", "平台", "状态"],
    },
    {
        "name": "5月库存盘点-按SKU明细.xlsx",
        "parquet": "staging/_cache_v3.0_a1b2c3d4e5f6_sheet0.parquet",
        "semantic": "库存",
        "cols": ["商品编码", "SKU", "仓库", "当前库存", "在途库存", "更新时间"],
    },
    {
        "name": "采购订单明细表-2026Q2.xlsx",
        "parquet": "staging/_cache_v3.0_9876fedcba98_sheet0.parquet",
        "semantic": "采购",
        "cols": ["采购单号", "供应商", "商品", "数量", "采购金额", "到货时间"],
    },
    {
        "name": "售后退款记录-近30天.xlsx",
        "parquet": "staging/_cache_v3.0_111122223333_sheet0.parquet",
        "semantic": "售后/退款",
        "cols": ["订单号", "平台", "退款原因", "退款金额", "处理状态", "时间"],
    },
]


def build_attachments_xml() -> str:
    lines = [f'<attachments count="{len(FILES)}">']
    for f in FILES:
        lines.append("  <file>")
        lines.append(f"    <name>{f['name']}</name>")
        lines.append(f"    <path>{f['name']}</path>")
        lines.append(f"    <parquet>{f['parquet']}</parquet>")
        lines.append(f"    <status>analyzed</status>")
        lines.append("  </file>")
    lines.append("</attachments>")
    return "\n".join(lines)


# 4 轮对话:每轮用语义指代,验证 LLM 跨轮映射
TURNS = [
    {
        "user": "看看销售表里前 5 个店铺的销售额",
        "expected_parquets": [FILES[0]["parquet"]],
        "desc": "T1: 销售",
    },
    {
        "user": "刚才那个库存表,告诉我哪些 SKU 当前库存为 0",
        "expected_parquets": [FILES[2]["parquet"]],
        "desc": "T2: 跨轮指代库存",
    },
    {
        "user": "把采购订单按供应商汇总一下采购金额",
        "expected_parquets": [FILES[3]["parquet"]],
        "desc": "T3: 采购",
    },
    {
        "user": "用销售表和店铺分组关联一下,算每个运营的销售额。再画柱形图",
        "expected_parquets": [FILES[0]["parquet"], FILES[1]["parquet"]],
        "desc": "T4: 多表关联",
    },
    {
        "user": "看看售后退款的总金额,按平台拆分",
        "expected_parquets": [FILES[4]["parquet"]],
        "desc": "T5: 售后",
    },
]


_PARQUET_RE = re.compile(r"['\"]([^'\"]*\.parquet)['\"]")


async def fetch_prompt():
    from config.code_tools import build_code_tools
    chat_path = Path(__file__).parent.parent / "config/chat_tools.py"
    chat = chat_path.read_text()
    m = re.search(r"### code_execute.*?(?=### file_search)", chat, re.DOTALL)
    code_desc = m.group(0) if m else ""
    literal = "attachments XML 给完整路径(path/parquet 字段),代码里直接字面 copy。"
    tool_desc = build_code_tools()[0]["function"]["description"]
    return literal, code_desc, tool_desc


async def run_session(session_idx: int) -> list[dict]:
    """跑一次完整多轮对话,返回每轮结果"""
    from services.adapters.factory import create_chat_adapter
    literal, code_desc, tool_desc = await fetch_prompt()

    system = (
        "你是 Python 数据分析助手,有 code_execute 工具可调用。\n\n"
        f"{literal}\n\n"
        f"## chat_tools.py 主 prompt code_execute 段\n{code_desc}\n\n"
        f"## code_execute tool description\n{tool_desc}\n\n"
        "用户提问后,直接输出 Python 代码块,不要解释。"
    )

    # 累积上下文(模拟多轮)
    messages = [
        {"role": "system", "content": system},
        {"role": "system", "content": build_attachments_xml()},
    ]

    adapter = create_chat_adapter(model_id="qwen-plus")
    results = []

    for turn in TURNS:
        messages.append({"role": "user", "content": turn["user"]})
        try:
            response = await adapter.chat_sync(messages, reasoning_effort="minimal")
            output = response.content or ""
            messages.append({"role": "assistant", "content": output})

            code_match = re.search(r"```(?:python)?\s*(.+?)```", output, re.DOTALL)
            if not code_match:
                results.append({"turn": turn["desc"], "error": "no_code_block"})
                continue
            code = code_match.group(1)
            paths_used = _PARQUET_RE.findall(code)
            expected = set(turn["expected_parquets"])
            used = set(paths_used)
            correct = expected.issubset(used)
            extra_wrong = used - expected - {p["parquet"] for p in []}  # 计算多余的
            # 是否含"美化版"(中文)
            wrote_chinese = any(any('一' <= c <= '鿿' for c in p) for p in paths_used)
            results.append({
                "turn": turn["desc"],
                "expected": list(expected),
                "used": paths_used,
                "correct": correct,
                "wrote_chinese": wrote_chinese,
            })
        except Exception as e:
            results.append({"turn": turn["desc"], "error": str(e)})

    await adapter.close()
    return results


async def main():
    print("=" * 70)
    print("POC 严苛版: 5 表 + 5 轮对话 + 语义指代 (ASCII cache)")
    print("=" * 70)
    print(f"\n模型: qwen-plus | 跑 3 次完整 session\n")

    all_session_results = []
    for sess in range(3):
        print(f"\n{'='*60}\n## Session {sess + 1}\n{'='*60}")
        results = await run_session(sess + 1)
        all_session_results.append(results)
        for r in results:
            if "error" in r:
                print(f"  {r['turn']}: ERROR {r['error']}")
                continue
            flag = "✅" if r["correct"] and not r["wrote_chinese"] else "❌"
            chinese_flag = " (写中文)" if r["wrote_chinese"] else ""
            print(f"  {r['turn']}: {flag}{chinese_flag}")
            if not r["correct"] or r["wrote_chinese"]:
                print(f"    expected: {r['expected']}")
                print(f"    used:     {r['used']}")

    # 汇总
    total = sum(len(s) for s in all_session_results)
    ok = sum(1 for s in all_session_results for r in s if r.get("correct") and not r.get("wrote_chinese"))
    chinese = sum(1 for s in all_session_results for r in s if r.get("wrote_chinese"))
    print(f"\n{'='*70}\n## 总汇")
    print(f"总轮次: {total}")
    print(f"正确映射: {ok}/{total} ({ok/total*100:.0f}%)")
    print(f"美化写中文: {chinese}/{total}")


if __name__ == "__main__":
    asyncio.run(main())
