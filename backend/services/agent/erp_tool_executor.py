"""
ERP 工具调度 Mixin

ERP 远程 API 两步调度 + 本地查询调度。
从 ToolExecutor 拆分出来，通过 Mixin 继承组合。

依赖宿主类提供：self.db, self.user_id, self.org_id
"""

from typing import Any, Callable, Coroutine, Dict

from loguru import logger


class ErpToolMixin:
    """ERP 远程/本地工具调度 Mixin"""

    def _make_erp_handler(
        self, tool_name: str,
    ) -> Callable[..., Coroutine[Any, Any, str]]:
        """为指定ERP工具创建handler"""
        async def handler(args: Dict[str, Any]) -> str:
            return await self._erp_dispatch(tool_name, args)
        return handler

    def _make_local_handler(
        self, tool_name: str,
    ) -> Callable[..., Coroutine[Any, Any, str]]:
        """为指定本地查询工具创建handler"""
        async def handler(args: Dict[str, Any]) -> str:
            return await self._local_dispatch(tool_name, args)
        return handler

    # ========================================
    # ERP 远程 API 统一调度
    # ========================================

    async def _erp_dispatch(
        self, tool_name: str, args: Dict[str, Any],
    ) -> str:
        """ERP工具统一调度（两步模式）

        查询工具：
        - Step 1: 只传 action → 返回参数文档（纯本地，无 API 调用）
        - Step 2: 传 action + params → 映射参数 → 调API → 格式化
        写入工具(erp_execute)：保持原逻辑不变
        """
        # erp_execute 用 category 查找注册表（不走两步模式）
        if tool_name == "erp_execute":
            dispatcher = await self._get_erp_dispatcher()
            if isinstance(dispatcher, str):
                return dispatcher
            try:
                category = args.get("category", "")
                action = args.get("action", "")
                params = args.get("params") or {}

                # [B5] 写操作幂等保护（10 分钟内相同操作不重复执行）
                import hashlib as _hl
                import json as _json
                _idempotency_payload = _json.dumps(
                    {"c": category, "a": action, "p": params},
                    sort_keys=True, ensure_ascii=False,
                )
                _idempotency_hash = _hl.md5(_idempotency_payload.encode()).hexdigest()[:16]
                _result_key = f"erp_write_done:{self.user_id}:{_idempotency_hash}"
                _lock_key = f"erp_write:{self.user_id}:{_idempotency_hash}"
                _lock_token = None

                from core.redis import get_redis, RedisClient
                _redis = await get_redis()
                if _redis:
                    # 1. 先查是否已完成（10 分钟内）
                    _done = await _redis.get(_result_key)
                    if _done:
                        return (
                            f"⚠ 该写操作（{category}/{action}）10 分钟内已执行过，"
                            f"避免重复执行。如需再次执行请稍后重试。"
                        )
                    # 2. 尝试获取锁（防止并发重复）
                    _lock_token = await RedisClient.acquire_lock(_lock_key, timeout=120)
                    if not _lock_token:
                        return f"⚠ 相同操作（{category}/{action}）正在执行中，请稍候再试。"

                cat_tool_map = {
                    "basic": "erp_info_query",
                    "product": "erp_product_query",
                    "trade": "erp_trade_query",
                    "aftersales": "erp_aftersales_query",
                    "warehouse": "erp_warehouse_query",
                    "purchase": "erp_purchase_query",
                    "distribution": "erp_execute",
                }
                actual_tool = cat_tool_map.get(category, "erp_execute")
                result = await dispatcher.execute(actual_tool, action, params)

                # [B5] 执行成功，标记完成（10 分钟 TTL）
                if _redis:
                    try:
                        await _redis.set(_result_key, "1", ex=600)
                    except Exception:
                        pass  # 标记失败不影响结果返回

                return result
            except Exception as e:
                logger.error(
                    f"ToolExecutor erp_dispatch | tool={tool_name} | error={e}"
                )
                return f"ERP操作失败：{e}"
            finally:
                await dispatcher.close()
                # [B5] 释放锁
                if _lock_token and _redis:
                    try:
                        await RedisClient.release_lock(_lock_key, _lock_token)
                    except Exception:
                        pass

        # 查询工具：两步模式
        action = args.get("action", "")
        if not action:
            return "缺少 action 参数"

        params = args.get("params")

        # Step 1: 无 params → 返回参数文档（纯本地，无需 dispatcher）
        # 注意：params={} 是合法的 Step 2（查全部），不能用 `not params`
        if params is None:
            from services.kuaimai.param_doc import generate_param_doc
            return generate_param_doc(tool_name, action)

        # Step 2: 有 params → 注入分页参数 → 执行查询 → 附带精简参数提示
        if args.get("page") is not None:
            params["page"] = args["page"]
        if args.get("page_size") is not None:
            params["page_size"] = args["page_size"]

        dispatcher = await self._get_erp_dispatcher()
        if isinstance(dispatcher, str):
            return dispatcher
        try:
            result = await dispatcher.execute(tool_name, action, params)
            from services.kuaimai.param_doc import generate_param_hints
            hints = generate_param_hints(tool_name, action, params)
            if hints:
                return f"{result}\n\n---\n{hints}"
            return result
        except Exception as e:
            logger.error(
                f"ToolExecutor erp_dispatch | tool={tool_name} | error={e}"
            )
            return f"ERP操作失败：{e}"
        finally:
            await dispatcher.close()

    async def _get_erp_dispatcher(self):
        """获取ERP调度器实例，企业用户优先用企业凭证"""
        from services.kuaimai.client import KuaiMaiClient
        from services.kuaimai.dispatcher import ErpDispatcher

        if self.org_id:
            try:
                from services.org.config_resolver import OrgConfigResolver
                resolver = OrgConfigResolver(self.db)
                creds = resolver.get_erp_credentials(self.org_id)
                client = KuaiMaiClient(
                    app_key=creds["kuaimai_app_key"],
                    app_secret=creds["kuaimai_app_secret"],
                    access_token=creds["kuaimai_access_token"],
                    refresh_token=creds["kuaimai_refresh_token"],
                    org_id=self.org_id,
                )
                return ErpDispatcher(client)
            except ValueError as e:
                return str(e)

        client = KuaiMaiClient()
        if not client.is_configured:
            await client.close()
            return "ERP系统未配置，请联系管理员设置快麦ERP的AppKey和AccessToken"
        await client.load_cached_token()
        return ErpDispatcher(client)

    # ========================================
    # 本地查询工具
    # ========================================

    async def _local_dispatch(
        self, tool_name: str, args: Dict[str, Any],
    ) -> str:
        """本地查询工具统一调度（直接查DB，毫秒级响应）"""
        from services.kuaimai.erp_local_doc_query import local_doc_query
        from services.kuaimai.erp_local_global_stats import local_global_stats
        from services.kuaimai.erp_local_identify import local_product_identify
        from services.kuaimai.erp_local_query import (
            local_aftersale_query,
            local_order_query,
            local_platform_map_query,
            local_product_flow,
            local_purchase_query,
            local_shop_list,
            local_stock_query,
            local_warehouse_list,
        )
        from services.kuaimai.erp_local_db_export import local_db_export
        from services.kuaimai.erp_local_sync_trigger import trigger_erp_sync
        from services.kuaimai.erp_stats_query import local_product_stats

        dispatch: Dict[str, Any] = {
            "local_purchase_query": local_purchase_query,
            "local_aftersale_query": local_aftersale_query,
            "local_order_query": local_order_query,
            "local_product_stats": local_product_stats,
            "local_product_flow": local_product_flow,
            "local_stock_query": local_stock_query,
            "local_product_identify": local_product_identify,
            "local_platform_map_query": local_platform_map_query,
            "local_doc_query": local_doc_query,
            "local_global_stats": local_global_stats,
            "local_shop_list": local_shop_list,
            "local_warehouse_list": local_warehouse_list,
            "local_db_export": local_db_export,
            "trigger_erp_sync": trigger_erp_sync,
        }

        func = dispatch.get(tool_name)
        if not func:
            return f"Unknown local tool: {tool_name}"
        try:
            # local_db_export 需要额外的 conversation_id 确定 staging 路径
            if tool_name == "local_db_export":
                return await func(
                    self.db, **args,
                    org_id=self.org_id,
                    conversation_id=self.conversation_id,
                )
            return await func(self.db, **args, org_id=self.org_id)
        except Exception as e:
            logger.error(
                f"ToolExecutor local_dispatch | tool={tool_name} | error={e}"
            )
            return f"本地查询失败: {e}"
