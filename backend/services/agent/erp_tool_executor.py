"""
ERP 工具调度 Mixin

ERP 远程 API 两步调度 + 本地查询调度。
从 ToolExecutor 拆分出来，通过 Mixin 继承组合。

依赖宿主类提供：self.db, self.user_id, self.org_id
"""

from typing import Any, Callable, Coroutine, Dict

from loguru import logger

from services.agent.agent_result import AgentResult


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
        """按写入或两步查询模式调度 ERP 工具。"""
        if tool_name == "erp_execute":
            return await self._execute_erp_write(args)
        return await self._execute_erp_query(tool_name, args)

    async def _execute_erp_query(
        self, tool_name: str, args: Dict[str, Any],
    ) -> str:
        """执行 ERP 两步查询。"""
        action = args.get("action", "")
        if not action:
            return AgentResult(
                summary="缺少 action 参数",
                status="error",
                error_message="Validation: action is required",
                metadata={"retryable": True},
            )

        params = args.get("params")

        # Step 1: 无 params → 返回参数文档（纯本地，无需 dispatcher）
        # 注意：params={} 是合法的 Step 2（查全部），不能用 `not params`
        if params is None:
            from services.kuaimai.param_doc import generate_param_doc
            doc = generate_param_doc(tool_name, action)
            return AgentResult(summary=doc, status="success")

        # Step 2: 有 params → 注入分页参数 → 执行查询 → 附带精简参数提示
        if args.get("page") is not None:
            params["page"] = args["page"]
        if args.get("page_size") is not None:
            params["page_size"] = args["page_size"]

        dispatcher = await self._get_erp_dispatcher()
        if isinstance(dispatcher, AgentResult):
            return dispatcher
        try:
            return await dispatcher.execute(tool_name, action, params)
        except Exception as e:
            logger.error(
                f"ToolExecutor erp_dispatch | tool={tool_name} | error={e}"
            )
            return AgentResult(
                summary=f"ERP操作失败：{e}",
                status="error",
                error_message=str(e),
                metadata={"retryable": True},
            )
        finally:
            await dispatcher.close()

    async def _execute_erp_write(self, args: Dict[str, Any]) -> str:
        """执行带 Redis 幂等锁的 ERP 写操作。"""
        import hashlib
        import json

        dispatcher = await self._get_erp_dispatcher()
        if isinstance(dispatcher, AgentResult):
            return dispatcher

        category = args.get("category", "")
        action = args.get("action", "")
        params = args.get("params") or {}
        payload = json.dumps(
            {"c": category, "a": action, "p": params},
            sort_keys=True, ensure_ascii=False,
        )
        operation_hash = hashlib.md5(payload.encode()).hexdigest()[:16]
        result_key = f"erp_write_done:{self.user_id}:{operation_hash}"
        lock_key = f"erp_write:{self.user_id}:{operation_hash}"
        lock_token = None
        redis = None

        from core.redis import RedisClient, get_redis

        try:
            redis = await get_redis()
            if not redis:
                return AgentResult(
                    summary="系统缓存服务暂时不可用，写操作已暂停。请稍后重试。",
                    status="error",
                    error_message="Redis unavailable",
                    metadata={"retryable": True},
                )
            if await redis.get(result_key):
                return AgentResult(
                    summary=(
                        f"该写操作（{category}/{action}）10 分钟内已执行过，"
                        f"避免重复执行。如需再次执行请稍后重试。"
                    ),
                    status="error",
                    error_message="Idempotency: duplicate write within 10min",
                    metadata={"retryable": False},
                )
            lock_token = await RedisClient.acquire_lock(
                lock_key, timeout=120,
            )
            if not lock_token:
                return AgentResult(
                    summary=f"相同操作（{category}/{action}）正在执行中，请稍候再试。",
                    status="error",
                    error_message="Concurrent write lock",
                    metadata={"retryable": True},
                )

            tool_map = {
                "basic": "erp_info_query",
                "product": "erp_product_query",
                "trade": "erp_trade_query",
                "aftersales": "erp_aftersales_query",
                "warehouse": "erp_warehouse_query",
                "purchase": "erp_purchase_query",
                "distribution": "erp_execute",
            }
            result = await dispatcher.execute(
                tool_map.get(category, "erp_execute"), action, params,
            )
            try:
                await redis.set(result_key, "1", ex=600)
            except Exception:
                pass
            return result
        except Exception as exc:
            logger.error(
                f"ToolExecutor erp_dispatch | "
                f"tool=erp_execute | error={exc}"
            )
            return AgentResult(
                summary=f"ERP操作失败：{exc}",
                status="error",
                error_message=str(exc),
                metadata={"retryable": True},
            )
        finally:
            await dispatcher.close()
            if lock_token and redis:
                try:
                    await RedisClient.release_lock(lock_key, lock_token)
                except Exception:
                    pass

    async def _get_erp_dispatcher(self):
        """获取ERP调度器实例，企业用户优先用企业凭证（带 token 双写闭环）"""
        from services.kuaimai.client import KuaiMaiClient
        from services.kuaimai.dispatcher import ErpDispatcher

        if self.org_id:
            try:
                from services.org.config_resolver import OrgConfigResolver
                resolver = OrgConfigResolver(self.db)
                creds = resolver.get_erp_credentials(self.org_id)

                # token 双写闭环：refresh 后回写 DB
                # 注意：这里的 resolver 是同步版，但 client 调 persister 是 async，
                # 用 asyncio.to_thread 把同步 set 包装成 async 调用
                import asyncio as _asyncio
                async def _persist(oid: str, access: str, refresh: str) -> None:
                    await _asyncio.to_thread(
                        resolver.update_erp_token, oid, access, refresh,
                    )

                client = KuaiMaiClient(
                    app_key=creds["kuaimai_app_key"],
                    app_secret=creds["kuaimai_app_secret"],
                    access_token=creds["kuaimai_access_token"],
                    refresh_token=creds["kuaimai_refresh_token"],
                    org_id=self.org_id,
                    token_persister=_persist,
                )
                await client.load_cached_token()  # 从 Redis 拿最新热缓存
                return ErpDispatcher(client, db_source=self.db)
            except ValueError as e:
                return AgentResult(
                    summary=str(e),
                    status="error",
                    error_message=str(e),
                    metadata={"retryable": False},
                )

        client = KuaiMaiClient()
        if not client.is_configured:
            await client.close()
            return AgentResult(
                summary="ERP系统未配置，请联系管理员设置快麦ERP的AppKey和AccessToken",
                status="error",
                error_message="ERP not configured",
                metadata={"retryable": False},
            )
        await client.load_cached_token()
        return ErpDispatcher(client, db_source=self.db)

    # ========================================
    # 本地查询工具
    # ========================================

    async def _local_dispatch(
        self, tool_name: str, args: Dict[str, Any],
    ) -> str:
        """本地查询工具统一调度（直接查DB，毫秒级响应）"""
        from services.kuaimai.erp_local_compare_stats import local_compare_stats
        from services.kuaimai.erp_local_identify import local_product_identify
        from services.kuaimai.erp_local_query import (
            local_platform_map_query,
            local_shop_list,
            local_stock_query,
            local_supplier_list,
            local_warehouse_list,
        )
        from services.kuaimai.erp_local_sync_trigger import trigger_erp_sync
        from services.kuaimai.erp_stats_query import local_product_stats

        # local_data → 统一查询引擎（替代 7 个碎片工具）
        if tool_name == "local_data":
            return await self._dispatch_local_data(args)

        dispatch: Dict[str, Any] = {
            "local_product_stats": local_product_stats,
            "local_stock_query": local_stock_query,
            "local_product_identify": local_product_identify,
            "local_platform_map_query": local_platform_map_query,
            "local_compare_stats": local_compare_stats,
            "local_shop_list": local_shop_list,
            "local_warehouse_list": local_warehouse_list,
            "local_supplier_list": local_supplier_list,
            "trigger_erp_sync": trigger_erp_sync,
        }

        func = dispatch.get(tool_name)
        if not func:
            return AgentResult(
                summary=f"Unknown local tool: {tool_name}",
                status="error",
                error_message=f"Unknown tool: {tool_name}",
                metadata={"retryable": False},
            )
        try:
            request_ctx = getattr(self, "request_ctx", None)
            _TIME_AWARE_TOOLS = {
                "local_product_stats", "local_compare_stats",
            }
            if tool_name in _TIME_AWARE_TOOLS:
                return await func(
                    self.db, **args,
                    org_id=self.org_id,
                    request_ctx=request_ctx,
                )
            return await func(self.db, **args, org_id=self.org_id)
        except Exception as e:
            logger.error(
                f"ToolExecutor local_dispatch | tool={tool_name} | error={e}"
            )
            return AgentResult(
                summary=f"本地查询失败: {e}",
                status="error",
                error_message=str(e),
                metadata={"retryable": True},
            )

    async def _dispatch_local_data(self, args: Dict[str, Any]) -> str:
        """统一查询引擎调度入口"""
        from services.kuaimai.erp_unified_query import UnifiedQueryEngine

        logger.info(
            f"local_data dispatch | doc_type={args.get('doc_type')} "
            f"mode={args.get('mode')} filters={args.get('filters', [])!r} "
            f"group_by={args.get('group_by')} time_type={args.get('time_type')}"
        )

        request_ctx = getattr(self, "request_ctx", None)
        engine = UnifiedQueryEngine(db=self.db, org_id=self.org_id)
        try:
            return await engine.execute(
                doc_type=args.get("doc_type", "order"),
                mode=args.get("mode", "summary"),
                filters=args.get("filters", []),
                group_by=args.get("group_by"),
                sort_by=args.get("sort_by"),
                sort_dir=args.get("sort_dir", "desc"),
                extra_fields=args.get("extra_fields") or args.get("fields"),
                limit=args.get("limit", 20),
                time_type=args.get("time_type"),
                include_invalid=args.get("include_invalid", False),
                user_id=self.user_id,
                conversation_id=self.conversation_id,
                request_ctx=request_ctx,
            )
        except Exception as e:
            logger.error(f"ToolExecutor local_data | error={e}", exc_info=True)
            return AgentResult(
                summary=f"统一查询失败: {e}",
                status="error",
                error_message=str(e),
                metadata={"retryable": True},
            )
