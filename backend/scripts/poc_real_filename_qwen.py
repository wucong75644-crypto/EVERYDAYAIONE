#!/usr/bin/env python3
"""POC: qwen-plus 是否会美化中英文混排文件名(加空格)

复现今天用户场景:
- 真实文件名: 4月销售主题分析-按订单商品明细-20260508134809_1d1705a783dab9d1_bb4aa2.parquet
- 真实 attachments XML(b38fc4d 之后的格式)
- 真实 chat_tools.py 主 prompt + code_tools.py description
- 5 次跑,看 LLM 是否字面 copy 还是美化加空格
"""
from __future__ import annotations

import asyncio
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

# 真实文件名(从生产 staging 目录直接拷)
REAL_PARQUET = "staging/_cache_v3.0_037237fcf9f7_sheet0_4月销售主题分析-按订单商品明细-20260508134809_1d1705a783dab9d1_bb4aa2.parquet"
REAL_PATH = "4月销售主题分析-按订单商品明细-20260508134809_1d1705a783dab9d1_bb4aa2.xlsx"
# 第二个文件(运营名下店铺分组)
PARQUET_2 = "staging/_cache_v3.0_6db4d68a87b8_sheet0_运营名下店铺分组_2c5340.parquet"
PATH_2 = "运营名下店铺分组_2c5340.xlsx"


# 真实 attachments XML(b38fc4d 之后的标准格式,attachments.py 没动过)
ATTACHMENTS_XML = f"""<attachments count="2">
  <file>
    <name>4月销售主题分析-按订单商品明细-20260508134809_1d1705a783dab9d1_bb4aa2.xlsx</name>
    <path>{REAL_PATH}</path>
    <size>67.7MB</size>
    <parquet>{REAL_PARQUET}</parquet>
    <status>analyzed</status>
  </file>
  <file>
    <name>运营名下店铺分组_2c5340.xlsx</name>
    <path>{PATH_2}</path>
    <size>13.8KB</size>
    <parquet>{PARQUET_2}</parquet>
    <status>analyzed</status>
  </file>
</attachments>"""


# 真实用户 prompt
USER_PROMPT = "通过刚才读取的两个表格做一下运营和店铺对应计算一下每个运营的交易额情况,再帮我画一个柱形图"


# 用当前生产 chat_tools + code_tools(精简版)
async def fetch_current_prompt():
    """从当前生产 config 加载真实的 system prompt"""
    from config.code_tools import build_code_tools

    # 主 system prompt 提取 code_execute 段
    # 简化: 直接读 chat_tools.py 文件内容
    chat_tools_path = Path(__file__).parent.parent / "config/chat_tools.py"
    chat_content = chat_tools_path.read_text()
    # 抠出"工具说明"段(到 file_search 之前)
    code_desc_match = re.search(
        r"### code_execute.*?(?=### file_search)",
        chat_content,
        re.DOTALL,
    )
    code_desc_in_chat = code_desc_match.group(0) if code_desc_match else ""

    # 字面 copy 提示行(173 行)
    literal_copy_hint = "attachments XML 给完整路径(path/parquet 字段),代码里直接字面 copy。"

    # tool description
    code_tools = build_code_tools()
    desc = code_tools[0]["function"]["description"]

    return literal_copy_hint, code_desc_in_chat, desc


_PARQUET_USE_RE = re.compile(
    r"['\"]([^'\"]*4\s*月销售[^'\"]+\.parquet)['\"]"
)


def detect_path_used(code: str) -> dict:
    """检测代码里使用的 parquet 路径是不是字面字符串"""
    # 找代码里出现的"4*月销售*"路径
    matches = _PARQUET_USE_RE.findall(code)
    if not matches:
        return {"path_found": False}

    used = matches[0]
    expected = REAL_PARQUET
    # 字面相同 = 没美化
    if used == expected or expected.endswith(used.split("/")[-1]):
        # 严格对比 basename
        used_base = used.split("/")[-1]
        expected_base = expected.split("/")[-1]
        return {
            "path_found": True,
            "used": used,
            "matches_literal": used_base == expected_base,
            "has_space_in_chinese": " " in used_base.split("_")[3] if len(used_base.split("_")) > 3 else False,
        }
    return {"path_found": True, "used": used, "matches_literal": False}


async def run_one(run_idx: int) -> dict:
    from services.adapters.factory import create_chat_adapter

    literal_hint, code_desc_in_chat, tool_desc = await fetch_current_prompt()

    # 模拟生产: chat_tools 主 prompt 片段 + tool description
    system = (
        "你是 Python 数据分析助手,有 code_execute 工具可调用。\n\n"
        f"{literal_hint}\n\n"
        f"## chat_tools.py 主 prompt code_execute 段\n{code_desc_in_chat}\n\n"
        f"## code_execute tool description\n{tool_desc}\n\n"
        "用户提问后,直接输出 Python 代码块(```python ... ```),不要解释。"
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

        code = code_match.group(1)
        analysis = detect_path_used(code)
        return {"run": run_idx, **analysis, "code_head": code.split("\n")[:5]}
    except Exception as e:
        return {"run": run_idx, "error": str(e)}


async def main():
    print("=" * 70)
    print("POC: qwen-plus 在当前 prompt 下是否美化中英混排文件名")
    print("=" * 70)
    print(f"真实 parquet: {REAL_PARQUET}")
    print(f"预期字面 copy: 不加空格")
    print(f"踩坑模式: 4 月 / - 按订单 / - 20260508(中文/连字符旁加空格)\n")

    results = []
    for i in range(5):
        r = await run_one(i + 1)
        results.append(r)
        if "error" in r:
            print(f"Run {i+1}: ERROR: {r['error']}")
            continue
        if not r.get("path_found"):
            print(f"Run {i+1}: 代码里没找到 parquet 路径")
            continue
        ok = r.get("matches_literal", False)
        flag = "✅ 字面 copy" if ok else "❌ 美化加空格"
        print(f"Run {i+1}: {flag}")
        if not ok:
            print(f"   实际:   {r['used']}")
            print(f"   预期: {REAL_PARQUET}")

    ok_count = sum(1 for r in results if r.get("matches_literal"))
    bad_count = sum(1 for r in results if r.get("path_found") and not r.get("matches_literal"))
    err_count = sum(1 for r in results if "error" in r)
    print(f"\n汇总: {ok_count}/5 字面 copy, {bad_count}/5 美化加空格, {err_count}/5 错误")

    out_file = Path(__file__).parent / "poc_real_filename_results.json"
    out_file.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    asyncio.run(main())
