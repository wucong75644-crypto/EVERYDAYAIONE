"""
工具循环上下文管理器

在 ChatHandler 工具循环中跨轮次累积信息：
- 已识别的编码映射
- 同步警告
- 已使用/失败的工具
- 通过 erp_api_search 发现的新工具名

每轮结束后 update_from_result()，下一轮开始前 build_context_prompt()。
"""

import re
from typing import Dict, List, Optional, Set

from loguru import logger


class ToolLoopContext:
    """工具循环上下文，跨轮次累积信息"""

    def __init__(
        self,
        org_id: Optional[str] = None,
        agent_domain: str = "general",
    ) -> None:
        self.org_id = org_id
        self.agent_domain = agent_domain              # 当前 Agent 所属域（域隔离过滤用）
        self.identified_codes: Dict[str, str] = {}    # 模糊编码 → 精确编码
        self.sync_warnings: List[str] = []            # 同步警告
        self.used_tools: List[str] = []               # 已使用的工具
        self.failed_tools: List[str] = []             # 执行失败的工具
        self.discovered_tools: Set[str] = set()       # 通过搜索发现的新工具名

    def update_from_result(
        self, tool_name: str, result: str, is_error: bool,
    ) -> None:
        """从工具执行结果中提取上下文信息"""
        self.used_tools.append(tool_name)
        if is_error:
            self.failed_tools.append(tool_name)

        # 提取 identify 结果中的编码映射
        if tool_name == "local_product_identify" and not is_error:
            self._extract_identified_codes(result)

        # 提取同步警告
        if "⚠" in result and "同步" in result:
            warning = result.split("⚠")[-1].strip()[:80]
            if warning and warning not in self.sync_warnings:
                self.sync_warnings.append(warning)

        # 提取 erp_api_search 发现的工具名（域感知过滤）
        if tool_name == "erp_api_search" and not is_error:
            from config.chat_tools import extract_tool_names_from_result
            new_tools = extract_tool_names_from_result(
                result, org_id=self.org_id, agent_domain=self.agent_domain,
            )
            if new_tools:
                self.discovered_tools.update(new_tools)
                logger.info(
                    f"ToolLoopContext discovered tools | "
                    f"domain={self.agent_domain} | tools={sorted(new_tools)}"
                )

    def build_context_prompt(self) -> Optional[str]:
        """生成当前轮次的上下文提示，注入到 messages 中

        Returns:
            上下文提示文本，无内容时返回 None
        """
        lines: List[str] = []

        if self.identified_codes:
            codes = ", ".join(
                f"{k}→{v}" for k, v in self.identified_codes.items()
            )
            lines.append(
                f"已识别编码: {codes}（直接使用精确编码，无需再次识别）"
            )

        if self.sync_warnings:
            lines.append(
                "⚠ 数据同步延迟中，如需实时数据请用远程 erp_* 工具"
            )

        if self.failed_tools:
            unique_failed = list(dict.fromkeys(self.failed_tools))[-3:]
            lines.append(
                f"上轮失败工具: {', '.join(unique_failed)}，考虑换其他工具或参数"
            )

        return "\n".join(lines) if lines else None

    def _extract_identified_codes(self, result: str) -> None:
        """从 local_product_identify 结果中提取编码映射"""
        # 结果格式通常包含 "编码: XXX" 或 "outer_id: XXX"
        patterns = [
            r'商家编码[：:]\s*(\S+)',
            r'outer_id[：:]\s*(\S+)',
            r'编码[：:]\s*(\S+)',
        ]
        for pattern in patterns:
            match = re.search(pattern, result)
            if match:
                code = match.group(1).strip()
                # 用结果的前 20 字符作为 key（用户原始输入的近似）
                key = result[:20].strip()
                self.identified_codes[key] = code
                break
