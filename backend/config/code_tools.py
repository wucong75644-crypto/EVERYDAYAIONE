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
                    "在安全沙盒中执行 Python 代码。"
                    "当其他工具无法满足需求时使用（跨数据源关联计算、"
                    "自定义公式、批量数据处理、生成文件等）。\n\n"
                    "沙盒内可用函数：\n"
                    "- erp_query(tool_name, action, params): ERP单页查询，"
                    "返回原始API响应dict（含 total 字段=总记录数，"
                    "无需翻页即可获取总数）\n"
                    "- erp_query_all(tool_name, action, params): "
                    "ERP全量翻页查询，逐页拉取所有记录，"
                    "数据量大时耗时较长（8000条约60秒），"
                    "仅在需要逐条遍历原始数据时使用\n"
                    "- web_search(query): 互联网搜索\n"
                    "- search_knowledge(query): 知识库查询\n"
                    "- read_file(path, encoding='utf-8'): 读取workspace内文件\n"
                    "- write_file(path, content, mode='overwrite'): "
                    "写入workspace内文件\n"
                    "- list_dir(path='.'): 列出workspace内目录\n"
                    "- 标准库: math, json, datetime, Decimal, Counter, "
                    "pandas(pd), pathlib(Path)\n\n"
                    "注意：\n"
                    "- 顶层可直接 await（如 data = await erp_query(...)）\n"
                    "- 用 print() 输出结果\n"
                    "- 禁止 import os/sys 等系统模块\n"
                    "- ERP 查询仅支持读操作\n\n"
                    "ERP 数据结构示例：\n"
                    "订单: {tid, sid, sysStatus, payment, shopName, buyerNick, "
                    "created, payTime, source}\n"
                    "店铺: {userId, title, type, platformName}\n"
                    "商品: {goodsNo, title, price, stockNum}\n"
                    "仓库: {id, name, type, status}"
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
    "- code_execute 是代码沙盒，适合其他工具无法完成的场景"
    "（跨数据源关联计算、自定义公式、批量数据处理、生成文件等）\n"
    "- 沙盒内可用：erp_query（单页）/ erp_query_all（全量翻页）/"
    " web_search / search_knowledge / read_file / write_file\n"
    "- erp_query 返回原始 API dict，包含 total 字段（总记录数），"
    "只需查 1 页即可获取总数\n"
    "- erp_query_all 会逐页翻完所有数据，数据量大时耗时较长"
    "（如8000条需约60秒），仅在需要逐条处理原始数据时使用\n"
    "- 顶层可直接 await，用 print() 输出结果\n"
    "- 沙盒代码结果返回后，用 route_to_chat 总结分析回复用户\n\n"
)
