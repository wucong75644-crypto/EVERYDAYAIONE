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
                    "在安全沙盒中执行 Python 代码并返回结果。适用于需要计算、"
                    "数据聚合、统计分析的场景。沙盒内可用：\n"
                    "- erp_query(tool_name, action, params): ERP单页查询，"
                    "返回原始dict\n"
                    "- erp_query_all(tool_name, action, params): ERP全量翻页查询\n"
                    "- web_search(query): 互联网搜索\n"
                    "- search_knowledge(query): 知识库查询\n"
                    "- read_file(path, encoding='utf-8'): 读取workspace内文件\n"
                    "- write_file(path, content, mode='overwrite'): 写入workspace内文件\n"
                    "- list_dir(path='.'): 列出workspace内目录\n"
                    "- 标准库: math, json, datetime, Decimal, Counter, "
                    "pandas(pd), pathlib(Path)\n\n"
                    "使用场景：\n"
                    "1. 数据聚合统计（如「今天各店铺成交多少」「库存汇总」）\n"
                    "2. 数学/财务计算（如「毛利率」「成本核算」）\n"
                    "3. 日期计算（如「距离某日多少天」「最近7天统计」）\n"
                    "4. 数据处理（如「按条件筛选排序」「生成报表」）\n\n"
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
    "## 代码执行沙盒规则\n"
    "- 需要数据聚合/统计/计算/排序/汇总 → code_execute\n"
    "- 典型场景：「今天成交多少」「各店铺销量」「毛利率计算」「库存汇总」"
    "「最近7天趋势」\n"
    "- 简单单条查询（如「查订单12345」）→ 仍用 erp_*_query\n"
    "- code_execute 内查 ERP 数据用 erp_query/erp_query_all\n"
    "- erp_query_all 自动翻页拉全量，适合统计聚合场景\n"
    "- 沙盒代码结果返回后，用 route_to_chat 总结分析回复用户\n\n"
)
