"""
代码执行沙盒工具定义

为 Agent Loop 提供 code_execute 工具定义。
由 agent_tools.py 导入合并。
"""

from typing import Any, Dict, List, Set


# 代码执行工具名集合（INFO 类型：结果回传大脑）
CODE_INFO_TOOLS: Set[str] = {
    "code_execute",
}

# 工具 Schema（参数验证）
CODE_TOOL_SCHEMAS: Dict[str, Dict[str, Any]] = {
    "code_execute": {
        "required": ["code", "description"],
        "properties": {
            "code": {"type": "string"},
            "description": {"type": "string"},
        },
    },
}


def build_code_tools() -> List[Dict[str, Any]]:
    """构建代码执行工具定义（1个 INFO 工具）"""
    return [
        {
            "type": "function",
            "function": {
                "name": "code_execute",
                "description": (
                    "纯计算沙盒。对 staging 数据做计算、转换、导出文件。\n"
                    "⚠ 沙盒内不能查询数据！数据必须先通过工具获取"
                    "（local_* / erp_* / fetch_all_pages），"
                    "大数据会自动存到 staging 文件。\n\n"
                    "沙盒内可用函数：\n"
                    "- read_file(path): 读取 staging 目录下的数据文件"
                    "（仅限 staging/ 路径）\n"
                    "- upload_file(content_bytes, filename): "
                    "上传文件到OSS，返回可下载URL。"
                    "用法: buf=io.BytesIO(); df.to_excel(buf,index=False); "
                    "print(await upload_file(buf.getvalue(),'报表.xlsx'))\n"
                    "- 标准库: math, json, datetime, Decimal, Counter, "
                    "pandas(pd), io\n\n"
                    "典型流程：\n"
                    "1. fetch_all_pages 获取数据 → staging/xxx.json\n"
                    "2. code_execute 读取并处理：\n"
                    "   data = json.loads(await read_file('staging/xxx.json'))\n"
                    "   df = pd.DataFrame(data)\n"
                    "   # 计算/筛选/转换...\n"
                    "   buf = io.BytesIO(); df.to_excel(buf, index=False)\n"
                    "   print(await upload_file(buf.getvalue(), '报表.xlsx'))\n\n"
                    "注意：\n"
                    "- 顶层可直接 await\n"
                    "- 用 print() 输出结果\n"
                    "- 禁止 import os/sys 等系统模块"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "code": {
                            "type": "string",
                            "description": (
                                "Python 代码。顶层可直接 await 调用异步函数。"
                                "用 print() 输出最终结果。"
                            ),
                        },
                        "description": {
                            "type": "string",
                            "description": (
                                "代码功能描述（一句话，如「统计各店铺今日成交额」），"
                                "用于执行日志审计。"
                            ),
                        },
                    },
                    "required": ["code", "description"],
                },
            },
        },
    ]


# 路由提示词片段
CODE_ROUTING_PROMPT = (
    "## code_execute 使用协议\n"
    "- code_execute 是纯计算沙盒，只能处理已获取的数据，不能查询数据\n"
    "- 沙盒内只有 read_file（限staging目录）和 upload_file，"
    "没有 erp_query / web_search 等数据获取函数\n"
    "- 数据获取必须先通过工具层完成（local_* / erp_* / fetch_all_pages），"
    "大数据会自动存到 staging 文件\n"
    "- 典型流程：fetch_all_pages 拿数据 → code_execute 用 read_file 读取 → "
    "pandas 计算 → upload_file 导出\n"
    "- 顶层可直接 await，用 print() 输出结果\n\n"
    "## fetch_all_pages 使用协议\n"
    "- 全量翻页工具，包装任意 erp_* 远程查询工具，自动翻页拉全部数据\n"
    "- 结果自动存 staging 文件，返回文件路径\n"
    "- 使用前需先通过 erp_* 工具的两步协议确认参数格式\n"
    "- 导出Excel/全量数据分析/跨数据源关联 → 先 fetch_all_pages 再 code_execute\n\n"
)
