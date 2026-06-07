#!/usr/bin/env python3
"""POC: ASCII cache 路径 + 用户语义化提问("销售表") LLM 能否正确映射

测试: 用户用模糊语义"销售表",看 LLM 能否从 <name> 字段找到对应文件
然后写代码用正确的 <parquet> ASCII 路径
"""
from __future__ import annotations
import asyncio, json, re, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

# 模拟新架构: parquet 路径纯 ASCII hash, name 字段保留中文
SALES_PARQUET = "staging/_cache_v3.0_037237fcf9f7_sheet0.parquet"  # ASCII
DIM_PARQUET = "staging/_cache_v3.0_6db4d68a87b8_sheet0.parquet"   # ASCII
SALES_NAME = "4月销售主题分析-按订单商品明细-20260508134809.xlsx"
DIM_NAME = "运营名下店铺分组_2c5340.xlsx"

ATTACHMENTS_XML = f"""<attachments count="2">
  <file>
    <name>{SALES_NAME}</name>
    <path>{SALES_NAME}</path>
    <size>67.7MB</size>
    <parquet>{SALES_PARQUET}</parquet>
    <status>analyzed</status>
  </file>
  <file>
    <name>{DIM_NAME}</name>
    <path>{DIM_NAME}</path>
    <size>13.8KB</size>
    <parquet>{DIM_PARQUET}</parquet>
    <status>analyzed</status>
  </file>
</attachments>"""

# 4 种用户提问方式
TEST_PROMPTS = [
    "从刚才读取的销售表分析一下数据,看看销售额最高的店铺",
    "把销售数据按平台聚合,画个柱形图",
    "你刚才那个销售明细文件,统计一下不同商品的销售数量",
    "用销售表和店铺分组表关联,算每个运营的销售额",
]


async def fetch_prompt():
    from config.code_tools import build_code_tools
    chat_path = Path(__file__).parent.parent / "config/chat_tools.py"
    chat = chat_path.read_text()
    m = re.search(r"### code_execute.*?(?=### file_search)", chat, re.DOTALL)
    code_desc = m.group(0) if m else ""
    literal = "attachments XML 给完整路径(path/parquet 字段),代码里直接字面 copy。"
    tool_desc = build_code_tools()[0]["function"]["description"]
    return literal, code_desc, tool_desc


_PARQUET_RE = re.compile(r"['\"]([^'\"]*\.parquet)['\"]")


def detect(code: str) -> dict:
    """检测代码里用了哪个 parquet 路径"""
    paths = _PARQUET_RE.findall(code)
    used_sales = any(SALES_PARQUET in p for p in paths)
    used_dim = any(DIM_PARQUET in p for p in paths)
    # 是否用了某个不存在的"美化版"
    bogus = [p for p in paths if "销售" in p or "运营" in p]
    return {
        "paths_used": paths,
        "used_sales_parquet": used_sales,
        "used_dim_parquet": used_dim,
        "wrote_chinese_path": len(bogus) > 0,
        "bogus_paths": bogus,
    }


async def run_one(user_prompt: str, run_idx: int) -> dict:
    from services.adapters.factory import create_chat_adapter
    literal, code_desc, tool_desc = await fetch_prompt()
    system = (
        "你是 Python 数据分析助手,有 code_execute 工具可调用。\n\n"
        f"{literal}\n\n"
        f"## chat_tools.py 主 prompt code_execute 段\n{code_desc}\n\n"
        f"## code_execute tool description\n{tool_desc}\n\n"
        "用户提问后,直接输出 Python 代码块,不要解释。"
    )
    messages = [
        {"role": "system", "content": system},
        {"role": "system", "content": ATTACHMENTS_XML},
        {"role": "user", "content": user_prompt},
    ]

    try:
        adapter = create_chat_adapter(model_id="qwen-plus")
        response = await adapter.chat_sync(messages, reasoning_effort="minimal")
        await adapter.close()
        output = response.content or ""
        code_match = re.search(r"```(?:python)?\s*(.+?)```", output, re.DOTALL)
        if not code_match:
            return {"run": run_idx, "error": "no_code_block"}
        return {"run": run_idx, "prompt": user_prompt[:30], **detect(code_match.group(1))}
    except Exception as e:
        return {"run": run_idx, "error": str(e)}


async def main():
    print("=" * 70)
    print("POC: ASCII cache 路径 + 用户语义化提问 LLM 是否能映射")
    print("=" * 70)
    print(f"销售 parquet (ASCII): {SALES_PARQUET}")
    print(f"店铺 parquet (ASCII): {DIM_PARQUET}\n")

    results = []
    for prompt in TEST_PROMPTS:
        print(f"\n--- '{prompt[:30]}...' ---")
        for i in range(3):  # 每个 prompt 跑 3 次
            r = await run_one(prompt, i + 1)
            results.append(r)
            if "error" in r:
                print(f"  Run {i+1}: ERROR")
                continue
            used_correct = r["used_sales_parquet"] or r["used_dim_parquet"]
            wrote_bogus = r["wrote_chinese_path"]
            if wrote_bogus:
                print(f"  Run {i+1}: ❌ 写了中文路径 {r['bogus_paths']}")
            elif used_correct:
                tag = []
                if r["used_sales_parquet"]: tag.append("销售✓")
                if r["used_dim_parquet"]: tag.append("店铺✓")
                print(f"  Run {i+1}: ✅ {' '.join(tag)}")
            else:
                print(f"  Run {i+1}: ⚠ 没找到任何已知 parquet | {r['paths_used']}")

    total = len([r for r in results if "error" not in r])
    ok = sum(1 for r in results if not r.get("wrote_chinese_path") and (r.get("used_sales_parquet") or r.get("used_dim_parquet")))
    bogus = sum(1 for r in results if r.get("wrote_chinese_path"))
    print(f"\n{'='*70}\n汇总: {ok}/{total} 正确映射,{bogus}/{total} 写中文路径错")

if __name__ == "__main__":
    asyncio.run(main())
