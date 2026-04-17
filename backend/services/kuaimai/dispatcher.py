"""
ERP统一调度引擎

根据 tool_name + action 查注册表 → 映射参数 → 调API → 格式化返回。
替代原有service.py中的独立query方法。

execute() 返回 ToolOutput（Phase 0 改造）。

重构文档: docs/document/TECH_多Agent单一职责重构.md §4.3
"""

import asyncio
from typing import Any, Dict, Optional

from loguru import logger

from services.agent.tool_output import OutputStatus, ToolOutput
from services.kuaimai.client import KuaiMaiClient
from services.kuaimai.formatters import get_formatter
from services.kuaimai.formatters.common import format_generic_list
from services.kuaimai.param_guardrails import (
    apply_code_broadening,
    diagnose_empty_result,
    preprocess_params,
    try_batch_dual_query,
    try_broadened_queries,
)
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
    ) -> ToolOutput:
        """执行ERP API调用

        Args:
            tool_name: 工具名（如 erp_trade_query）
            action: 操作名（如 order_list）
            params: 用户参数

        Returns:
            ToolOutput 结构化结果
        """
        # 1. 查注册表
        registry = TOOL_REGISTRIES.get(tool_name)
        if not registry:
            return ToolOutput(
                summary=f"未知的ERP工具: {tool_name}",
                source="erp",
                status=OutputStatus.ERROR,
                error_message=f"unknown tool: {tool_name}",
            )

        entry: Optional[ApiEntry] = registry.get(action)
        if not entry:
            available = ", ".join(sorted(registry.keys()))
            return ToolOutput(
                summary=f"未知的操作「{action}」，可选: {available}",
                source="erp",
                status=OutputStatus.ERROR,
                error_message=f"unknown action: {action}",
            )

        # 2. 校验必填参数（增强错误信息：列出支持的参数）
        missing = [
            p for p in entry.required_params
            if not params.get(p)
            and not params.get(entry.param_map.get(p, ""))
        ]
        if missing:
            valid = sorted(entry.param_map.keys())
            self._record_param_knowledge(
                tool_name, action,
                f"缺少必填参数: {', '.join(missing)}，支持: {', '.join(valid)}",
            )
            return ToolOutput(
                summary=(
                    f"缺少必填参数: {', '.join(missing)}。"
                    f"该操作支持的参数: {', '.join(valid)}"
                ),
                source="erp",
                status=OutputStatus.ERROR,
                error_message=f"missing params: {', '.join(missing)}",
            )

        # 2.5 参数预处理（格式规则自动纠正）
        params, corrections = preprocess_params(entry, params)

        # 3. 映射参数（白名单校验）
        api_params, param_warnings = map_params(entry, params)
        if param_warnings:
            logger.warning(
                f"ErpDispatcher invalid params | tool={tool_name} "
                f"action={action} invalid={param_warnings}"
            )
            self._record_param_knowledge(
                tool_name, action,
                f"无效参数: {', '.join(param_warnings)}，"
                f"支持: {', '.join(sorted(entry.param_map.keys()))}",
            )
        logger.info(
            f"ErpDispatcher | tool={tool_name} action={action} "
            f"method={entry.method} params={api_params}"
        )

        # 4. 构建网关参数 + 编码宽泛化预处理 + 调用API
        base_url, system_params = self._build_gateway_params(entry)
        broadening = apply_code_broadening(entry, params, api_params)

        broadened_note = ""
        if broadening:
            original_codes, packed_codes, api_keys, is_batch = broadening
            if is_batch:
                data, broadened_note = await try_batch_dual_query(
                    entry, api_params, original_codes, packed_codes,
                    api_keys, self._client, base_url, system_params,
                )
            else:
                data, broadened_note = await try_broadened_queries(
                    entry, api_params, original_codes, packed_codes,
                    api_keys, self._client, base_url, system_params,
                )
        else:
            # 正常模式（无编码参数、或写操作）
            try:
                if entry.fetch_all:
                    data = await self._fetch_all_pages(
                        entry, api_params, base_url, system_params,
                    )
                else:
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
                return ToolOutput(
                    summary=f"ERP接口调用失败: {e}",
                    source="erp",
                    status=OutputStatus.ERROR,
                    error_message=str(e),
                )

        # 5. 格式化返回（附带无效参数警告）
        result = self._format_response(data, entry, action)

        # 5.3 宽泛查询说明
        if broadened_note:
            result = broadened_note + "\n\n" + result

        # 5.5 参数自动纠正记录
        if corrections:
            result = (
                "⚙ 参数自动纠正: "
                + "; ".join(corrections) + "\n\n"
                + result
            )

        # 5.6 零结果诊断建议
        suggestion = diagnose_empty_result(entry, params, data)
        if suggestion:
            result += suggestion

        if param_warnings:
            valid = sorted(entry.param_map.keys())
            result += (
                f"\n\n⚠ 忽略了无效参数: {', '.join(param_warnings)}。"
                f"该操作支持的参数: {', '.join(valid)}"
            )
        return ToolOutput(
            summary=result,
            source="erp",
            metadata={"tool_name": tool_name, "action": action},
        )

    @staticmethod
    def _record_param_knowledge(
        tool_name: str, action: str, error_message: str,
    ) -> None:
        """Fire-and-forget 记录参数错误知识"""
        try:
            from services.knowledge_extractor import extract_and_save
            asyncio.create_task(
                extract_and_save(
                    task_type="param_validation",
                    model_id=f"{tool_name}:{action}",
                    status="failed",
                    error_message=error_message,
                )
            )
        except Exception as e:
            logger.debug(f"Param knowledge recording skipped | error={e}")

    async def _fetch_all_pages(
        self,
        entry: ApiEntry,
        api_params: Dict[str, Any],
        base_url: str | None,
        system_params: dict[str, Any] | None,
    ) -> Dict[str, Any]:
        """自动翻页拉取全量数据（店铺、仓库等配置列表）

        终止算法：当页返回数 < pageSize 即表示最后一页。
        """
        page_size = int(api_params.get("pageSize", 100))
        response_key = entry.response_key or "list"
        all_items: list = []
        last_data: Dict[str, Any] = {}
        page = 0

        while True:
            page += 1
            api_params["pageNo"] = page
            data = await self._client.request_with_retry(
                entry.method,
                api_params,
                base_url=base_url,
                extra_system_params=system_params,
            )
            last_data = data
            items = data.get(response_key) or []
            all_items.extend(items)

            # API 返回数 < pageSize → 已到最后一页
            if len(items) < page_size:
                break

        logger.info(
            f"ErpDispatcher fetch_all | method={entry.method} "
            f"pages={page} total_items={len(all_items)}"
        )
        last_data[response_key] = all_items
        return last_data

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

    # 全局安全网：所有 formatter 输出的绝对上限（按行截断，不破坏数据）
    _GLOBAL_CHAR_BUDGET = 4000

    def _format_response(
        self,
        data: Any,
        entry: ApiEntry,
        action: str,
    ) -> str:
        """格式化API响应 + 全局字符预算安全网"""
        formatter = get_formatter(entry.formatter)
        if formatter:
            try:
                result = formatter(data, entry)
            except Exception as e:
                logger.warning(
                    f"Formatter error: {entry.formatter} | {e}, "
                    f"falling back to generic"
                )
                result = format_generic_list(data, entry)
        else:
            result = format_generic_list(data, entry)

        # 全局安全网：超预算时按行截断
        if len(result) > self._GLOBAL_CHAR_BUDGET:
            lines = result.split("\n")
            truncated: list[str] = []
            used = 0
            for line in lines:
                if used + len(line) + 1 > self._GLOBAL_CHAR_BUDGET:
                    truncated.append("...(输出过长，已截断)")
                    break
                truncated.append(line)
                used += len(line) + 1
            result = "\n".join(truncated)

        return result

    async def execute_raw(
        self,
        tool_name: str,
        action: str,
        params: Dict[str, Any],
    ) -> Dict[str, Any]:
        """执行 ERP API 调用，返回原始 dict（供沙盒代码处理）

        与 execute() 共享步骤 1-3（注册表查找 → 参数校验 → API 调用），
        但跳过步骤 4（格式化），直接返回 API 原始响应数据。
        写操作被拦截，返回 error dict。

        Args:
            tool_name: 工具名（如 erp_trade_query）
            action: 操作名（如 order_list）
            params: 用户参数

        Returns:
            API 原始响应 dict，或包含 error 字段的 dict
        """
        # 1. 查注册表
        registry = TOOL_REGISTRIES.get(tool_name)
        if not registry:
            return {"error": f"未知的ERP工具: {tool_name}"}

        entry: Optional[ApiEntry] = registry.get(action)
        if not entry:
            available = ", ".join(sorted(registry.keys()))
            return {"error": f"未知操作「{action}」，可选: {available}"}

        # 拦截写操作（沙盒只允许查询）
        if entry.is_write:
            return {"error": f"沙盒内禁止写操作: {action}"}

        # 2. 校验必填参数
        missing = [p for p in entry.required_params if not params.get(p)]
        if missing:
            return {"error": f"缺少必填参数: {', '.join(missing)}"}

        # 3. 映射参数
        api_params, _ = map_params(entry, params)
        logger.info(
            f"ErpDispatcher execute_raw | tool={tool_name} action={action} "
            f"method={entry.method} params={api_params}"
        )

        # 4. 调用 API（不格式化）
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
                f"ErpDispatcher execute_raw error | tool={tool_name} "
                f"action={action} error={e}"
            )
            return {"error": f"ERP接口调用失败: {e}"}

        return data

    async def close(self) -> None:
        """关闭底层客户端"""
        await self._client.close()
