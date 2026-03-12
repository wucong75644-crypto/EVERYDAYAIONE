"""
ERP统一调度引擎

根据 tool_name + action 查注册表 → 映射参数 → 调API → 格式化返回。
替代原有service.py中的独立query方法。
"""

import json
from typing import Any, Dict, Optional

from loguru import logger

from services.kuaimai.client import KuaiMaiClient
from services.kuaimai.formatters import get_formatter
from services.kuaimai.param_mapper import map_params
from services.kuaimai.registry import TOOL_REGISTRIES
from services.kuaimai.registry.base import ApiEntry


class ErpDispatcher:
    """ERP API统一调度器"""

    def __init__(self, client: KuaiMaiClient) -> None:
        self._client = client

    async def execute(
        self,
        tool_name: str,
        action: str,
        params: Dict[str, Any],
    ) -> str:
        """执行ERP API调用

        Args:
            tool_name: 工具名（如 erp_trade_query）
            action: 操作名（如 order_list）
            params: 用户参数

        Returns:
            格式化后的结果文本
        """
        # 1. 查注册表
        registry = TOOL_REGISTRIES.get(tool_name)
        if not registry:
            return f"未知的ERP工具: {tool_name}"

        entry: Optional[ApiEntry] = registry.get(action)
        if not entry:
            available = ", ".join(sorted(registry.keys()))
            return f"未知的操作「{action}」，可选: {available}"

        # 2. 校验必填参数
        missing = [p for p in entry.required_params if not params.get(p)]
        if missing:
            return f"缺少必填参数: {', '.join(missing)}"

        # 3. 映射参数
        api_params = map_params(entry, params)
        logger.info(
            f"ErpDispatcher | tool={tool_name} action={action} "
            f"method={entry.method} params={api_params}"
        )

        # 4. 构建网关参数 + 调用API
        base_url, system_params = self._build_gateway_params(entry)
        try:
            data = await self._client.request_with_retry(
                entry.method,
                api_params,
                base_url=base_url,
                extra_system_params=system_params,
            )
        except Exception as e:
            logger.error(
                f"ErpDispatcher API error | tool={tool_name} "
                f"action={action} error={e}"
            )
            return f"ERP接口调用失败: {e}"

        # 5. 格式化返回
        return self._format_response(data, entry, action)

    @staticmethod
    def _build_gateway_params(
        entry: ApiEntry,
    ) -> tuple[str | None, dict[str, Any] | None]:
        """构建网关地址和系统参数（奇门接口需要 customerId 路由）"""
        if not entry.base_url:
            return None, None

        from core.config import settings

        system_params = dict(entry.system_params)
        customer_id = settings.qimen_customer_id
        if customer_id:
            system_params["customerId"] = customer_id

        return entry.base_url, system_params or None

    def _format_response(
        self,
        data: Any,
        entry: ApiEntry,
        action: str,
    ) -> str:
        """格式化API响应"""
        # 获取格式化函数
        formatter = get_formatter(entry.formatter)
        if formatter:
            try:
                return formatter(data, entry)
            except Exception as e:
                logger.warning(
                    f"Formatter error: {entry.formatter} | {e}, "
                    f"falling back to generic"
                )

        # 兜底：通用格式化
        return self._generic_format(data, entry, action)

    def _generic_format(
        self,
        data: Any,
        entry: ApiEntry,
        action: str,
    ) -> str:
        """通用格式化：提取列表数据，转JSON摘要"""
        if isinstance(data, dict):
            # 提取列表
            items = data.get(entry.response_key) if entry.response_key else None
            total = data.get("total", "")

            if isinstance(items, list):
                count = len(items)
                header = f"查询到 {total or count} 条结果"
                if not items:
                    return f"{entry.description}：暂无数据"

                # 截取前5条，JSON格式
                preview = items[:5]
                text = json.dumps(preview, ensure_ascii=False, indent=2)
                if len(text) > 2000:
                    text = text[:2000] + "\n..."

                suffix = ""
                if count < int(total or count):
                    suffix = f"\n（显示前{count}条，共{total}条）"
                return f"{header}{suffix}\n\n{text}"

            # 非列表响应（详情类）
            text = json.dumps(data, ensure_ascii=False, indent=2)
            if len(text) > 2000:
                text = text[:2000] + "\n..."
            return f"{entry.description}结果:\n{text}"

        # 非dict响应
        return str(data)[:2000]

    async def close(self) -> None:
        """关闭底层客户端"""
        await self._client.close()
