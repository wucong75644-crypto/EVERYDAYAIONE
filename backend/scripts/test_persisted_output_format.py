"""
测试千问是否理解 <persisted-output> 标签格式

对比三种信号格式，看千问的行为：
1. Claude Code 格式（<persisted-output> 标签 + preview）
2. 我们当前格式（摘要 + STAGING_DIR 路径）
3. 无 preview 强制格式（只给元数据）

判断标准：LLM 是否调用 code_execute 读文件，还是直接用 preview 数据回答
"""

import asyncio
import json
import os

# 模拟的店铺数据（截取前几行作为 preview）
FULL_DATA = """共 198 个店铺：

【淘宝】(41个)
  1. 蓝创文具旗舰店 (ID:001)
  2. 真彩文具旗舰店 (ID:002)
  3. 国誉文具专营店 (ID:003)

【拼多多】(114个)
  1. 小么小二郎儿 (ID:101)
  2. 纸间奇想集 (ID:102)
  3. 背着书包上学堂 (ID:103)

【抖音】(19个)
  1. 三天饿九顿呀 (ID:201)
  2. 信的第七序 (ID:202)

【京东】(12个)
  1. 知樱文创用品店 (ID:301)
  2. 得立文具经营部 (ID:302)

... 还有 快手(6个)、1688(3个)、dangkou(2个)、alibabac2m(1个)"""

# 三种信号格式
FORMAT_CLAUDE = """<persisted-output>
Output too large (6138 chars). Full output saved to: STAGING_DIR/tool_result_local_shop_list.txt

Preview (first 2KB):
共 198 个店铺：

【淘宝】(41个)
  1. 蓝创文具旗舰店 (ID:001)
  2. 真彩文具旗舰店 (ID:002)
  3. 国誉文具专营店 (ID:003)

【拼多多】(114个)
  1. 小么小二郎儿 (ID:101)
  2. 纸间奇想集 (ID:102)
...
</persisted-output>"""

FORMAT_CURRENT = """[数据来源: local_shop_list | 获取时间: 2026-04-15 16:19:10]
共 198 个店铺：

【淘宝】(41个)
  1. 蓝创文具旗舰店 (ID:001)
  2. 真彩文具旗舰店 (ID:002)
  3. 国誉文具专营店 (ID:003)

【拼多多】(114个)
  1. 小么小二郎儿 (ID:101)
... 共 58 行数据

完整数据（6138 字符）[STAGED→ tool_result_local_shop_list.txt]，可用 code_execute 中 data = open(STAGING_DIR + "/tool_result_local_shop_list.txt").read() 读取。"""

FORMAT_NO_PREVIEW = """[数据来源: local_shop_list | 获取时间: 2026-04-15 16:19:10]
共 198 条数据（6138 字符），已存入文件。
必须用 code_execute 读取完整数据后再回答：data = open(STAGING_DIR + "/tool_result_local_shop_list.txt").read()"""

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "code_execute",
            "description": "在沙盒中执行 Python 代码",
            "parameters": {
                "type": "object",
                "properties": {
                    "code": {"type": "string", "description": "要执行的 Python 代码"},
                    "description": {"type": "string", "description": "代码功能描述"},
                },
                "required": ["code"],
            },
        },
    }
]


async def test_format(name: str, tool_result: str):
    """测试一种格式，看 LLM 是否调用 code_execute"""
    from openai import AsyncOpenAI

    client = AsyncOpenAI(
        api_key=os.environ.get("DASHSCOPE_API_KEY", "sk-b3b721bda334488fbdcb87fb45ce5ef5"),
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
    )

    messages = [
        {"role": "system", "content": "你是一个 ERP 数据分析助手。你可以调用 code_execute 工具执行代码来处理数据。STAGING_DIR 是数据文件所在目录。"},
        {"role": "user", "content": "列出我们所有店铺的详细信息，包括店铺名称、平台、状态"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call_shop_list",
                    "type": "function",
                    "function": {
                        "name": "local_shop_list",
                        "arguments": "{}",
                    },
                }
            ],
        },
        {
            "role": "tool",
            "tool_call_id": "call_shop_list",
            "content": tool_result,
        },
    ]

    response = await client.chat.completions.create(
        model="qwen3.5-plus",
        messages=messages,
        tools=TOOLS,
        temperature=0,
    )

    choice = response.choices[0]
    called_tool = bool(choice.message.tool_calls)
    tool_name = choice.message.tool_calls[0].function.name if called_tool else None

    # 检查是否直接输出了店铺数据（偷懒行为）
    content = choice.message.content or ""
    has_shop_data = "蓝创" in content or "旗舰店" in content or "拼多多" in content

    print(f"\n{'='*60}")
    print(f"格式: {name}")
    print(f"{'='*60}")
    print(f"调用了工具: {'✅ 是' if called_tool else '❌ 否'}")
    if called_tool:
        print(f"工具名: {tool_name}")
        args = json.loads(choice.message.tool_calls[0].function.arguments)
        code = args.get("code", "")
        print(f"代码预览: {code[:200]}...")
    else:
        print(f"直接输出: {'有店铺数据（偷懒了）' if has_shop_data else '无店铺数据'}")
        print(f"回复前200字: {content[:200]}...")
    print()

    return {
        "format": name,
        "called_tool": called_tool,
        "tool_name": tool_name,
        "has_shop_data": has_shop_data,
    }


async def main():
    print("测试千问对三种信号格式的行为差异\n")
    print("期望：LLM 应该调用 code_execute 读取完整数据，而非直接用 preview 回答\n")

    results = []
    for name, fmt in [
        ("Claude Code 格式", FORMAT_CLAUDE),
        ("当前格式（带预览）", FORMAT_CURRENT),
        ("无预览强制格式", FORMAT_NO_PREVIEW),
    ]:
        try:
            r = await test_format(name, fmt)
            results.append(r)
        except Exception as e:
            print(f"\n{name} 测试失败: {e}")

    print("\n" + "=" * 60)
    print("汇总")
    print("=" * 60)
    for r in results:
        status = "✅ 调了 code_execute" if r["called_tool"] else "❌ 直接输出（偷懒）"
        print(f"  {r['format']}: {status}")


if __name__ == "__main__":
    asyncio.run(main())
