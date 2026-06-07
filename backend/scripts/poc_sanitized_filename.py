#!/usr/bin/env python3
"""POC: cache 文件名 sanitize 为全英文 hash 后,qwen-plus 是否 0/5 失败

对照 POC poc_real_filename_qwen.py:
- 同样真实场景(2 个文件,attachments XML,用户 prompt)
- 唯一差异:cache 文件名替换为全英文 hash
"""
from __future__ import annotations

import asyncio
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

# sanitize 后的全英文 hash 文件名(模拟新架构)
SANITIZED_PARQUET = "staging/_cache_v3.0_037237fcf9f7_sheet0_xls_1d1705a783dab9d1_bb4aa2.parquet"
REAL_PATH = "4月销售主题分析-按订单商品明细-20260508134809_1d1705a783dab9d1_bb4aa2.xlsx"
SANITIZED_PARQUET_2 = "staging/_cache_v3.0_6db4d68a87b8_sheet0_xls_2c5340.parquet"
PATH_2 = "运营名下店铺分组_2c5340.xlsx"


ATTACHMENTS_XML = f"""<attachments count="2">
  <file>
    <name>4月销售主题分析-按订单商品明细-20260508134809_1d1705a783dab9d1_bb4aa2.xlsx</name>
    <path>{REAL_PATH}</path>
    <size>67.7MB</size>
    <parquet>{SANITIZED_PARQUET}</parquet>
    <status>analyzed</status>
  </file>
  <file>
    <name>运营名下店铺分组_2c5340.xlsx</name>
    <path>{PATH_2}</path>
    <size>13.8KB</size>
    <parquet>{SANITIZED_PARQUET_2}</parquet>
    <status>analyzed</status>
  </file>
</attachments>"""

USER_PROMPT = "通过刚才读取的两个表格做一下运营和店铺对应计算一下每个运营的交易额情况,再帮我画一个柱形图"


async def fetch_current_prompt():
    from config.code_tools import build_code_tools

    chat_tools_path = Path(__file__).parent.parent / "config/chat_tools.py"
    chat_content = chat_tools_path.read_text()
    code_desc_match = re.search(r"### code_execute.*?(?=### file_search)", chat_content, re.DOTALL)
    code_desc_in_chat = code_desc_match.group(0) if code_desc_match else ""

    literal_copy_hint = "attachments XML 给完整路径(path/parquet 字段),代码里直接字面 copy。"
    code_tools = build_code_tools()
    desc = code_tools[0]["function"]["description"]
    return literal_copy_hint, code_desc_in_chat, desc


_PARQUET_RE = re.compile(r"['\"]([^'\"]*\.parquet)['\"]")


def detect(code: str) -> dict:
    matches = _PARQUET_RE.findall(code)
    if not matches:
        return {"path_found": False}
    used = matches[0]
    expected = SANITIZED_PARQUET
    used_base = used.split("/")[-1]
    expected_base = expected.split("/")[-1]
    return {"path_found": True, "used": used, "matches_literal": used_base == expected_base}


async def run_one(run_idx: int) -> dict:
    from services.adapters.factory import create_chat_adapter
    literal_hint, code_desc, tool_desc = await fetch_current_prompt()

    system = (
        "你是 Python 数据分析助手,有 code_execute 工具可调用。\n\n"
        f"{literal_hint}\n\n"
        f"## chat_tools.py 主 prompt code_execute 段\n{code_desc}\n\n"
        f"## code_execute tool description\n{tool_desc}\n\n"
        "用户提问后,直接输出 Python 代码块,不要解释。"
    )
    messages = [
        {"role": "system", "content": system},
        {"role": "system", "content": ATTACHMENTS_XML},
        {"role": "user", "content": USER_PROMPT},
    ]

    try:
        adapter = create_chat_adapter(model_id="qwen-plus")
        response = await adapter.chat_sync(messages, reasoning_effort="minimal")
        await adapter.close()
        output = response.content or ""
        code_match = re.search(r"```(?:python)?\s*(.+?)```", output, re.DOTALL)
        if not code_match:
            return {"run": run_idx, "error": "no_code_block"}
        return {"run": run_idx, **detect(code_match.group(1))}
    except Exception as e:
        return {"run": run_idx, "error": str(e)}


async def main():
    print("=" * 70)
    print("POC 对照: sanitize 后的全英文 hash 文件名是否 0/5 失败")
    print("=" * 70)
    print(f"全英文路径: {SANITIZED_PARQUET}\n")

    results = []
    for i in range(20):
        r = await run_one(i + 1)
        results.append(r)
        if "error" in r:
            print(f"Run {i+1}: ERROR: {r['error']}")
            continue
        if not r.get("path_found"):
            print(f"Run {i+1}: 代码里没找到 parquet")
            continue
        ok = r.get("matches_literal", False)
        flag = "✅ 字面 copy" if ok else "❌ 不匹配"
        print(f"Run {i+1}: {flag}")
        if not ok:
            print(f"   used: {r['used']}")

    ok_count = sum(1 for r in results if r.get("matches_literal"))
    total = len(results)
    print(f"\n汇总: {ok_count}/{total} 字面 copy ({ok_count/total*100:.0f}%)")
    print(f"\n对比 (含中英混排文件名: 4/5 = 80%)")
    print(f"sanitize 全英文 hash : {ok_count}/{total} = {ok_count/total*100:.0f}%")

if __name__ == "__main__":
    asyncio.run(main())
