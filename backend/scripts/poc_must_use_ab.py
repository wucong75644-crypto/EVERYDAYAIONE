#!/usr/bin/env python3
"""POC A/B 对照测试 — 验证 MUST USE 反偷懒提示词的实际效果

对照组(A):当前 _DESCRIPTION,无 MUST USE 区块,无工具使用纪律
实验组(B):加 MUST USE / MUST NOT 区块 + 工具使用纪律段落(调研给的标准模板)

测试场景:
  S1-S5 同 POC A(基础场景)
  S6 关键场景:模拟今天用户截图问题 — 上文有数据汇总,问"画柱形图"

判定:
  PASS = LLM 调了 code_execute(代码块里有 emit_chart/emit_file 等)
  FAIL = LLM 用文字"假装"回答(说"已生成"但实际没调代码)
"""
from __future__ import annotations

import asyncio
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))


# ============================================================
# 当前 description(对照组,从 config/code_tools.py 复制)
# ============================================================

DESC_A_CURRENT = """Python 沙盒 (有状态,变量跨调用保留)。沙盒 cwd=/workspace,所有路径用相对字符串。
预装: pandas/duckdb/matplotlib/plotly/altair/openpyxl/pdfplumber/python-docx 等

路径协议(全部相对):
  读用户上传: pd.read_excel('上传/2026-06/x.xlsx')
  写产物给用户: df.to_excel('下载/x.xlsx') 然后 emit_file('下载/x.xlsx')

【产物输出协议 — 当你想给用户看的内容时必须调用】
  emit_chart(option, title='')   ECharts 图表
  emit_file(path, label=None)    文件下载卡片
  emit_image(path)               静态图片
  emit_table(df, title='')       交互式表格"""


# ============================================================
# 实验组 description(加 MUST USE / MUST NOT,按调研推荐格式)
# ============================================================

DESC_B_MUST_USE = """Python 沙盒,用于执行计算 / 数据处理 / 文件生成 / 图表渲染。

【MUST USE — 以下场景必须调用本工具,禁止用文字回答】
  - 用户要求生成图表(柱形图/折线图/饼图/任何可视化) → 必须 emit_chart
  - 用户要求导出文件(Excel/CSV/PDF) → 必须 df.to_excel + emit_file
  - 用户要求计算 / 统计 / 聚合 / 排序 → 必须 SQL 或 pandas 计算
  - 用户要求查看具体数据表格 → 必须 emit_table

【MUST NOT — 反偷懒】
  - 禁止只用文字描述"已生成柱形图""数据如下"等假装完成
  - 禁止"根据上文数据,大致情况是..." — 数据已在上文不代表不用算,要算就调本工具
  - 用户问"画图"=要看图,不是要读你描述图长什么样
  - 上文有数据汇总 ≠ 用户不要图。用户说"画柱形图",必须 emit_chart 让前端渲染卡片

Python 沙盒 (有状态,变量跨调用保留)。沙盒 cwd=/workspace,所有路径用相对字符串。
预装: pandas/duckdb/matplotlib/plotly/altair/openpyxl/pdfplumber/python-docx 等

路径协议(全部相对):
  读用户上传: pd.read_excel('上传/2026-06/x.xlsx')
  写产物给用户: df.to_excel('下载/x.xlsx') 然后 emit_file('下载/x.xlsx')

【产物输出协议 — 当你想给用户看的内容时必须调用】
  emit_chart(option, title='')   ECharts 图表
  emit_file(path, label=None)    文件下载卡片
  emit_image(path)               静态图片
  emit_table(df, title='')       交互式表格"""


# ============================================================
# 测试场景
# ============================================================

# 模拟"上文有数据汇总"的助手消息(今天截图的真实情况)
_PRIOR_DATA_SUMMARY = """根据查询,今天(2026-06-06)各平台的有效订单数:
- 淘宝:824 单(最高)
- 抖音:727 单
- 1688:577 单
- 京东:406 单
- 快手:181 单
- 小红书:58 单
- 系统:3 单

若包含拼多多(10,516 单,金额¥0),今日全平台有效订单为 13,292 单。"""


TEST_CASES = [
    {
        "name": "S1_直接要图表",
        "messages_extra": [],
        "user": "画一个柱形图比较 A/B/C 三家店本月销售额。A: 12.5 万, B: 8.7 万, C: 15.3 万",
        "expected_tool_call": True,
    },
    {
        "name": "S2_AI自主判断要图表",
        "messages_extra": [],
        "user": "分析这三家店本月业绩,A 12.5 万, B 8.7 万, C 15.3 万。给我说说情况",
        "expected_tool_call": True,
        "soft": True,
    },
    {
        "name": "S3_直接要下载",
        "messages_extra": [],
        "user": "把这三家店的销售数据整理成 Excel 给我下载。A: 12.5 万, B: 8.7 万, C: 15.3 万",
        "expected_tool_call": True,
    },
    {
        "name": "S4_多产物混合",
        "messages_extra": [],
        "user": "画个销售趋势柱形图,顺便把数据也导出 csv 给我下载。A: 12.5 万, B: 8.7 万, C: 15.3 万",
        "expected_tool_call": True,
    },
    {
        "name": "S5_★关键_上文有数据汇总_问画图",
        "messages_extra": [
            {"role": "assistant", "content": _PRIOR_DATA_SUMMARY},
        ],
        "user": "你可以把查询到的今天的有效订单和平台 做一个柱形图给我?",
        "expected_tool_call": True,  # ← 这是今天用户截图的真实场景
    },
    {
        "name": "S6_★关键_上文有汇总_要表格",
        "messages_extra": [
            {"role": "assistant", "content": _PRIOR_DATA_SUMMARY},
        ],
        "user": "把这些数据整理成表格给我看",
        "expected_tool_call": True,
    },
]


MODELS = [
    "qwen-plus",         # 生产默认 chat
    "claude-opus-4-7",   # 行业上限
]


# ============================================================
# 判定:LLM 是否真的调用了工具(代码块含 emit_xxx 或 import + 数据处理)
# ============================================================

_EMIT_RE = re.compile(r"emit_(chart|file|image|table)\s*\(")
_CODE_BLOCK_RE = re.compile(r"```(?:python)?\s*(.+?)```", re.DOTALL)


def judge(output: str, expected_tool_call: bool, soft: bool = False) -> tuple[str, str]:
    """判定:
       PASS = 输出含 ```python 代码块且代码里有 emit_xxx 调用
       FAIL = 没有代码块或代码里没 emit(纯文字假装回答)
       SOFT_PASS(仅 soft 场景) = 有代码块但没 emit(算合理)
    """
    code_match = _CODE_BLOCK_RE.search(output)
    if not code_match:
        # 没代码块 = LLM 没决定调工具
        return ("FAIL_no_code", "无代码块,纯文字回答")

    code = code_match.group(1)
    emit_matches = _EMIT_RE.findall(code)

    if emit_matches:
        return ("PASS", f"emit={emit_matches}")

    # 有代码但没 emit
    if soft:
        return ("SOFT_PASS", "有代码无 emit")
    return ("FAIL_no_emit", "有代码块但没 emit_xxx")


# ============================================================
# 跑 LLM
# ============================================================


async def run_one(model_id: str, description: str, test_case: dict) -> dict:
    """对单个 model + test_case 跑一次"""
    from services.adapters.factory import create_chat_adapter

    system_prompt = (
        "你是 Python 数据分析助手。下面是 code_execute 工具的说明:\n\n"
        f"{description}\n\n"
        "用户提问后,只输出一段 Python 代码块(```python ... ```),不要解释。"
    )

    messages = [{"role": "system", "content": system_prompt}]
    messages.extend(test_case.get("messages_extra", []))
    messages.append({"role": "user", "content": test_case["user"]})

    try:
        adapter = create_chat_adapter(model_id=model_id)
        response = await adapter.chat_sync(messages, reasoning_effort="minimal")
        await adapter.close()
        output = response.content or ""
        verdict, reason = judge(output, test_case["expected_tool_call"], test_case.get("soft", False))
        snippet = output.strip()[:200].replace("\n", " ")
        return {
            "verdict": verdict,
            "reason": reason,
            "snippet": snippet,
        }
    except Exception as e:
        return {"verdict": "ERROR", "reason": str(e), "snippet": ""}


async def run_group(model_id: str, description: str, group_name: str) -> list[dict]:
    """跑一组(model x 所有场景)"""
    print(f"\n{'='*70}\n{group_name}: model={model_id}\n{'='*70}")
    results = []
    for tc in TEST_CASES:
        result = await run_one(model_id, description, tc)
        print(f"  [{tc['name']:<40}] {result['verdict']:<15} {result['reason'][:40]}")
        results.append({"name": tc["name"], **result})
    return results


# ============================================================
# 主报告
# ============================================================


def calc_pass_rate(results: list[dict]) -> tuple[int, int, str]:
    """PASS + SOFT_PASS 算通过"""
    total = len(results)
    passed = sum(1 for r in results if r["verdict"] in ("PASS", "SOFT_PASS"))
    return passed, total, f"{passed}/{total} ({passed/total*100:.0f}%)"


async def main():
    print("=" * 70)
    print("POC A/B 对照:MUST USE 反偷懒提示词效果验证")
    print("=" * 70)
    print(f"\n场景数:{len(TEST_CASES)} | 模型数:{len(MODELS)} | 总调用:{2 * len(TEST_CASES) * len(MODELS)} 次")
    print(f"对照组(A):当前 description,无 MUST USE")
    print(f"实验组(B):加 MUST USE/MUST NOT + 反偷懒纪律")

    all_results = {}

    for model_id in MODELS:
        a_results = await run_group(model_id, DESC_A_CURRENT, f"对照组 A (无 MUST USE)")
        b_results = await run_group(model_id, DESC_B_MUST_USE, f"实验组 B (有 MUST USE)")
        all_results[model_id] = {"A": a_results, "B": b_results}

    # 总报告
    print(f"\n\n{'='*70}\n最终对比报告\n{'='*70}\n")
    print(f"{'模型':<22} {'对照组 A':<20} {'实验组 B':<20} {'提升'}")
    print("-" * 75)
    for model_id in MODELS:
        a_passed, a_total, a_str = calc_pass_rate(all_results[model_id]["A"])
        b_passed, b_total, b_str = calc_pass_rate(all_results[model_id]["B"])
        diff = b_passed - a_passed
        diff_str = f"+{diff}" if diff > 0 else f"{diff}"
        print(f"  {model_id:<20} {a_str:<20} {b_str:<20} {diff_str} 个")

    # 关键场景 S5(模拟今天用户截图问题)
    print(f"\n关键场景 S5_★关键_上文有数据汇总_问画图 (今天用户截图的真实问题):")
    print("-" * 75)
    for model_id in MODELS:
        a_s5 = next((r for r in all_results[model_id]["A"] if "S5" in r["name"]), None)
        b_s5 = next((r for r in all_results[model_id]["B"] if "S5" in r["name"]), None)
        print(f"  {model_id:<20}")
        print(f"    对照 A: {a_s5['verdict']:<15} ({a_s5['reason'][:50]})")
        print(f"    实验 B: {b_s5['verdict']:<15} ({b_s5['reason'][:50]})")

    print(f"\n{'='*70}")
    print("结论:")
    total_diff = sum(
        calc_pass_rate(all_results[m]["B"])[0] - calc_pass_rate(all_results[m]["A"])[0]
        for m in MODELS
    )
    if total_diff > 0:
        print(f"  ✅ MUST USE 提示词跨模型净提升 {total_diff} 个 PASS,值得正式上线")
    elif total_diff == 0:
        print(f"  ⚠ MUST USE 提示词无净提升,需重新设计或考虑 tool_choice 强制")
    else:
        print(f"  ❌ MUST USE 反而降低 PASS 率({total_diff}),需排查")

    out_file = Path(__file__).parent / "poc_must_use_ab_results.json"
    out_file.write_text(json.dumps(all_results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n原始结果: {out_file}")


if __name__ == "__main__":
    asyncio.run(main())
