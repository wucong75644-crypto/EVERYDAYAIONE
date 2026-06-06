#!/usr/bin/env python3
"""POC A: 测试 LLM 在 emit 协议下的代码生成行为(独立脚本,脱离项目链路)

目的:
  - 验证 LLM 看到 emit_xxx 提示词后,会不会在生成的代码里主动调用
  - 跨模型对比(gemini-3-pro / qwen-plus / claude-opus-4-7)
  - 不掺杂项目特殊机制(意图路由/工具循环/上下文压缩/etc)

跑法:
  source backend/venv/bin/activate
  cd backend && python scripts/poc_emit_protocol.py

输出:
  - 控制台:每个 (模型 x 场景) 的判定 + 触发次数
  - 总结报表:emit 触发率 / 参数正确率 / 漏喊率 / 跨模型对比
"""
from __future__ import annotations

import asyncio
import json
import re
import sys
from pathlib import Path
from typing import Any

# 让 backend 可导入
sys.path.insert(0, str(Path(__file__).parent.parent))


SYSTEM_PROMPT = """你是 Python 数据分析助手。你写的代码会在 Python 沙盒里执行。

【产物输出协议 — 必须遵守】
沙盒里预装了 4 个"产物声明"函数。当你产生想给用户看的内容时,必须调用对应函数:

  emit_chart(option: dict, title: str = '')   # ECharts 交互式图表,前端按 option 渲染
  emit_file(path: str, label: str = None)     # 文件下载卡片,前端展示下载链接
  emit_image(path: str)                       # 静态图片(PNG/JPG等)
  emit_table(df, title: str = '')             # 交互式表格

【规则】
1. 不要 print 数据让用户翻日志看,要 emit_xxx 让前端渲染
2. 写文件后必须 emit_file 才能让用户下载到(写了但不 emit 等于丢)
3. matplotlib/plotly 的 fig 通过 emit_image(path) 或 emit_chart(option) 输出
4. 处理表格数据时优先 emit_table

【示例】
用户:画一个柱形图比较 ABC 三家销售额
你写:
```python
option = {
    "title": {"text": "各店铺销售额"},
    "xAxis": {"data": ["A店", "B店", "C店"]},
    "yAxis": {},
    "series": [{"type": "bar", "data": [125, 87, 153]}],
}
emit_chart(option, title="各店铺销售额")
```

用户:把数据导出 Excel
你写:
```python
import pandas as pd
df = pd.DataFrame({"店铺": ["A","B","C"], "销售额(万)": [125,87,153]})
df.to_excel("下载/销售数据.xlsx", index=False)
emit_file("下载/销售数据.xlsx", label="销售数据")
```

【现在请回答用户问题。只输出 Python 代码块,不要解释】
"""


TEST_CASES: list[dict[str, Any]] = [
    {
        "name": "1_用户主动要图表",
        "user": "画一个柱形图比较 A/B/C 三家店本月销售额。A: 12.5 万, B: 8.7 万, C: 15.3 万",
        "expected": {"chart": 1},
    },
    {
        "name": "2_AI自主判断要图表",
        "user": "分析这三家店本月业绩,A 12.5 万, B 8.7 万, C 15.3 万。给我说说情况",
        # 期望:模型自主判断可视化更好,主动 emit_chart
        "expected": {"chart": 1},
        "soft": True,  # 模型可以选择只 print 文字也算合理,但 emit_chart 是优秀表现
    },
    {
        "name": "3_用户主动要下载",
        "user": "把这三家店的销售数据整理成 Excel 给我下载。A: 12.5 万, B: 8.7 万, C: 15.3 万",
        "expected": {"file": 1},
    },
    {
        "name": "4_AI自主判断给下载",
        "user": "把销售数据合并去重整理好,我后续要看。A: 12.5 万, A: 13 万(重复), B: 8.7 万, C: 15.3 万",
        # 期望:模型处理完应主动给个文件让用户下载
        "expected": {"file": 1},
        "soft": True,
    },
    {
        "name": "5_多产物混合",
        "user": "画个销售趋势柱形图,顺便把数据也导出 csv 给我下载。A: 12.5 万, B: 8.7 万, C: 15.3 万",
        "expected": {"chart": 1, "file": 1},
    },
]


MODELS = [
    "gemini-3-pro",
    "qwen-plus",
    "claude-opus-4-7",
]


# ============================================================
# 代码静态分析:识别 emit_xxx 调用
# ============================================================

_EMIT_RE = re.compile(
    r"emit_(?P<kind>chart|file|image|table)\s*\("
)


def parse_emits(generated_text: str) -> dict[str, int]:
    """从 LLM 生成的文本中找出 emit_xxx 调用次数(按 kind 分类)"""
    counts: dict[str, int] = {"chart": 0, "file": 0, "image": 0, "table": 0}
    for m in _EMIT_RE.finditer(generated_text):
        counts[m.group("kind")] += 1
    return counts


def judge(emits: dict[str, int], expected: dict[str, int], soft: bool) -> str:
    """判定:PASS / SOFT_PASS(soft 模式下没 emit 但有合理输出) / FAIL"""
    all_hit = all(emits.get(k, 0) >= v for k, v in expected.items())
    if all_hit:
        return "PASS"
    if soft:
        # soft 场景下,模型选择文字输出也算合理但不够优秀
        return "SOFT_FAIL"
    return "FAIL"


# ============================================================
# 跑 LLM
# ============================================================


async def run_one(model_id: str, user_text: str) -> str:
    """调一次 LLM,返回纯文本结果"""
    from services.adapters.factory import create_chat_adapter

    adapter = create_chat_adapter(model_id=model_id)
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_text},
    ]
    response = await adapter.chat_sync(messages, reasoning_effort="minimal")
    await adapter.close()
    return response.content or ""


async def run_all() -> dict[str, Any]:
    """跑所有模型 x 所有场景"""
    results: dict[str, Any] = {}
    for model_id in MODELS:
        print(f"\n{'='*60}\n模型: {model_id}\n{'='*60}")
        model_results: list[dict[str, Any]] = []
        for tc in TEST_CASES:
            print(f"\n[{tc['name']}] 用户:{tc['user'][:50]}...")
            try:
                output = await run_one(model_id, tc["user"])
                emits = parse_emits(output)
                verdict = judge(emits, tc["expected"], tc.get("soft", False))
                print(f"   emits={emits} 期望={tc['expected']} → {verdict}")
                # 打印生成代码前 200 字符方便判断
                snippet = output.strip()[:200].replace("\n", " ")
                print(f"   代码片段: {snippet}...")
                model_results.append({
                    "name": tc["name"],
                    "user": tc["user"],
                    "emits": emits,
                    "expected": tc["expected"],
                    "verdict": verdict,
                    "output": output,
                })
            except Exception as e:
                print(f"   ❌ ERROR: {e}")
                model_results.append({
                    "name": tc["name"],
                    "error": str(e),
                    "verdict": "ERROR",
                })
        results[model_id] = model_results
    return results


# ============================================================
# 报告
# ============================================================


def report(results: dict[str, Any]) -> None:
    print(f"\n\n{'='*70}\nPOC A 报告 — emit 协议跨模型测试\n{'='*70}\n")

    # 跨模型汇总表
    print(f"{'场景':<28} ", end="")
    for m in MODELS:
        print(f"{m:<20}", end="")
    print()
    print("-" * 90)

    for tc in TEST_CASES:
        print(f"{tc['name']:<28} ", end="")
        for m in MODELS:
            verdicts = [r for r in results[m] if r.get("name") == tc["name"]]
            if not verdicts:
                print(f"{'N/A':<20}", end="")
            else:
                v = verdicts[0]
                if v.get("verdict") == "ERROR":
                    print(f"{'ERROR':<20}", end="")
                else:
                    e = v.get("emits", {})
                    summary = "+".join(
                        f"{k}:{e[k]}" for k in ["chart", "file", "image", "table"] if e.get(k, 0) > 0
                    ) or "none"
                    print(f"{v['verdict']}({summary}) {' '*(20-len(v['verdict'])-len(summary)-3)}", end="")
        print()

    # 每模型统计
    print("\n\n模型分项统计")
    print("-" * 60)
    for m in MODELS:
        rs = results[m]
        pass_n = sum(1 for r in rs if r.get("verdict") == "PASS")
        fail_n = sum(1 for r in rs if r.get("verdict") == "FAIL")
        soft_n = sum(1 for r in rs if r.get("verdict") == "SOFT_FAIL")
        err_n = sum(1 for r in rs if r.get("verdict") == "ERROR")
        total = len(rs)
        print(f"  {m:<25}  PASS {pass_n}/{total}  SOFT_FAIL {soft_n}  FAIL {fail_n}  ERROR {err_n}")

    # 结论建议
    print("\n\n结论建议")
    print("-" * 60)
    all_pass_rate = sum(
        1 for m in MODELS for r in results[m] if r.get("verdict") == "PASS"
    ) / (len(MODELS) * len(TEST_CASES))
    print(f"  跨模型整体硬通过率: {all_pass_rate:.1%}")
    if all_pass_rate >= 0.7:
        print("  ✅ emit 协议可行,可进入 POC B(生产环境真实场景验证)")
    elif all_pass_rate >= 0.5:
        print("  ⚠️ emit 协议部分可行,需要更强提示词约束才能进 POC B")
    else:
        print("  ❌ emit 协议触发率低,需要重新设计")


# ============================================================
# 主入口
# ============================================================


async def main():
    print("POC A: emit 协议纯模型测试启动\n")
    print(f"测试场景: {len(TEST_CASES)} 个")
    print(f"测试模型: {', '.join(MODELS)}")
    print(f"总调用次数: {len(MODELS) * len(TEST_CASES)}")

    results = await run_all()

    # 保存原始结果
    out_file = Path(__file__).parent / "poc_emit_protocol_results.json"
    out_file.write_text(
        json.dumps(results, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"\n原始结果已保存: {out_file}")

    report(results)


if __name__ == "__main__":
    asyncio.run(main())
