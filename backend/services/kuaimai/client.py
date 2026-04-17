"""
快麦ERP API 客户端

职责：签名计算、HTTP 请求、Token 自动刷新。
遵循项目 httpx 懒初始化 + tenacity 重试模式。
"""

import asyncio
import hashlib
import hmac as hmac_mod
from typing import Any, Awaitable, Callable, Dict, Optional

import httpx
from loguru import logger
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from core.config import settings
from services.kuaimai.errors import (
    KuaiMaiBusinessError,
    KuaiMaiError,
    KuaiMaiRateLimitError,
    KuaiMaiSignatureError,
    KuaiMaiTokenExpiredError,
)
from utils.time_context import now_cn

# Token 过期相关的错误码
# - 27/105/106: 快麦数字错误码（历史沿用）
# - invalid_session: 快麦实际返回的字符串错误码（生产 24h 内 20+ 个 API 方法均返回此码），
#   缺失此项会导致自动刷新从未触发（Bug 3）
_TOKEN_EXPIRED_CODES = {"27", "105", "106", "invalid_session"}
_SIGNATURE_ERROR_CODE = "25"

# refresh 并发互斥锁的 TTL（秒）。refresh 实测 1~2s 完成，给 30s 容忍
# 网络抖动 + DB 持久化慢的边界场景。10s 在 worker 高并发场景偶发不够。
_REFRESH_LOCK_TTL = 30
# 拿不到锁时等待对方完成的间隔（秒）
_REFRESH_LOCK_WAIT = 2.0

# 类型别名：token 持久化回调
# 签名: (org_id, access_token, refresh_token) -> Awaitable[None]
# 由调用方注入，client 不感知 DB 实现细节，保持单测友好。
TokenPersister = Callable[[str, str, str], Awaitable[None]]


class KuaiMaiClient:
    """快麦ERP API HTTP 客户端"""

    def __init__(
        self,
        app_key: Optional[str] = None,
        app_secret: Optional[str] = None,
        access_token: Optional[str] = None,
        refresh_token: Optional[str] = None,
        base_url: Optional[str] = None,
        timeout: Optional[float] = None,
        org_id: Optional[str] = None,
        token_persister: Optional[TokenPersister] = None,
    ) -> None:
        self._app_key = app_key or settings.kuaimai_app_key or ""
        self._app_secret = app_secret or settings.kuaimai_app_secret or ""
        self._access_token = access_token or settings.kuaimai_access_token or ""
        self._refresh_token = refresh_token or settings.kuaimai_refresh_token or ""
        self._base_url = base_url or settings.kuaimai_base_url
        self._timeout = timeout or settings.kuaimai_timeout
        self._client: Optional[httpx.AsyncClient] = None
        self._org_id = org_id
        # token 持久化回调（多租户必须注入，否则 refresh 后只写 Redis 不写 DB，
        # 一旦 Redis 丢失就回到初始 token，导致历史性 bug 重演）
        self._token_persister: Optional[TokenPersister] = token_persister

        # ── 咽喉处不变量保护 ──────────────────────────
        # 多租户场景（org_id 非空）必须传 token_persister，否则 refresh
        # 后的新 token 不会写回 org_configs，等于回到 2026-04-10 雪崩前
        # 的架构。这条 warning 让任何漏注入都立刻在日志可见，配合
        # tests/test_kuaimai.py::TestMultiTenantInvariant 形成 CI 拦截。
        if org_id and not token_persister:
            logger.warning(
                f"KuaiMaiClient created with org_id but no token_persister | "
                f"org={org_id} — refresh-time DB write-back will be skipped, "
                f"causing token persistence loss. This is the 2026-04-10 "
                f"outage pattern. Caller MUST inject token_persister."
            )

    @property
    def is_configured(self) -> bool:
        """检查快麦配置是否完整"""
        return bool(self._app_key and self._app_secret and self._access_token)

    # ========================================
    # 签名算法
    # ========================================

    def generate_sign(
        self,
        params: Dict[str, Any],
        sign_method: str = "hmac",
    ) -> str:
        """生成签名（使用实例的 app_secret）"""
        return self._compute_sign(params, sign_method, self._app_secret)

    @staticmethod
    def _compute_sign(
        params: Dict[str, Any],
        sign_method: str,
        secret: str,
    ) -> str:
        """计算API签名

        步骤：
        1. 过滤 sign、null 值参数
        2. 按参数名 ASCII 排序
        3. 拼接 key+value 字符串
        4. 按 sign_method 计算摘要
        5. 转为32位大写HEX
        """
        filtered = {
            k: str(v) for k, v in params.items()
            if v is not None and k != "sign"
        }
        sorted_params = sorted(filtered.items())
        param_str = "".join(f"{k}{v}" for k, v in sorted_params)

        if sign_method == "md5":
            sign_str = secret + param_str + secret
            return hashlib.md5(sign_str.encode("utf-8")).hexdigest().upper()
        elif sign_method == "hmac-sha256":
            return hmac_mod.new(
                secret.encode("utf-8"),
                param_str.encode("utf-8"),
                hashlib.sha256,
            ).hexdigest().upper()
        else:
            # 默认 hmac (HMAC_MD5)
            return hmac_mod.new(
                secret.encode("utf-8"),
                param_str.encode("utf-8"),
                hashlib.md5,
            ).hexdigest().upper()

    # ========================================
    # HTTP 请求
    # ========================================

    async def _get_client(self) -> httpx.AsyncClient:
        """懒初始化 httpx 客户端"""
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(
                    connect=5.0,
                    read=self._timeout,
                    write=5.0,
                    pool=5.0,
                ),
            )
        return self._client

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=4),
        retry=retry_if_exception_type((httpx.TimeoutException, httpx.NetworkError)),
    )
    async def request(
        self,
        method: str,
        biz_params: Optional[Dict[str, Any]] = None,
        sign_method: str = "hmac",
        *,
        base_url: Optional[str] = None,
        extra_system_params: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """发送快麦API请求

        Args:
            method: API方法名（如 erp.trade.list.query）
            biz_params: 业务参数
            sign_method: 签名算法
            base_url: 网关地址覆盖（奇门等走不同网关时使用）
            extra_system_params: 额外系统参数（如奇门的 target_app_key / customerId）

        Returns:
            API 响应数据（已解析 JSON）

        Raises:
            KuaiMaiError: 配置未完成
            KuaiMaiSignatureError: 签名错误
            KuaiMaiTokenExpiredError: Token 过期且刷新失败
            KuaiMaiBusinessError: 业务错误
        """
        if not self.is_configured:
            raise KuaiMaiError(message="快麦ERP未配置，请设置KUAIMAI_APP_KEY等环境变量")

        is_qimen = base_url is not None

        if is_qimen:
            # 淘宝奇门网关：独立凭证 + 淘宝参数规范
            qimen_key = settings.qimen_app_key
            qimen_secret = settings.qimen_app_secret
            if not qimen_key or not qimen_secret:
                raise KuaiMaiError(
                    message="奇门未配置，请在 .env 中设置 QIMEN_APP_KEY 和 QIMEN_APP_SECRET"
                )
            sign_method = "md5"  # 淘宝标准签名方式
            common_params: Dict[str, Any] = {
                "method": method,
                "app_key": qimen_key,
                "session": self._access_token,
                # 快麦/淘宝奇门 API 签名时间戳必须用北京时间，否则签名校验失败 (P0)
                # 参见 docs/document/TECH_ERP时间准确性架构.md §17 N1
                "timestamp": now_cn().strftime("%Y-%m-%d %H:%M:%S"),
                "v": "2.0",
                "format": "json",
                "sign_method": sign_method,
            }
        else:
            # 快麦ERP网关：原有参数规范
            common_params = {
                "method": method,
                "appKey": self._app_key,
                "session": self._access_token,
                # 快麦 API 签名时间戳必须用北京时间，否则签名校验失败 (P0)
                "timestamp": now_cn().strftime("%Y-%m-%d %H:%M:%S"),
                "version": "1.0",
                "format": "json",
                "sign_method": sign_method,
            }

        # 合并额外系统参数（奇门的 target_app_key / customerId 等）
        if extra_system_params:
            common_params.update(extra_system_params)

        # 合并业务参数
        all_params = {**common_params, **(biz_params or {})}

        # 计算签名（奇门用 qimen_app_secret，ERP用 self._app_secret）
        sign_secret = settings.qimen_app_secret if is_qimen else self._app_secret
        all_params["sign"] = self._compute_sign(all_params, sign_method, sign_secret)

        client = await self._get_client()
        url = base_url or self._base_url

        logger.info(f"KuaiMai request | method={method} | url={url}")
        response = await client.post(url, data=all_params)
        response.raise_for_status()

        data = response.json()

        # 淘宝奇门响应嵌套在 "response" key 下
        if is_qimen and "response" in data:
            data = data["response"]

        return self._handle_response(data, method)

    def _handle_response(self, data: Dict[str, Any], method: str) -> Dict[str, Any]:
        """处理快麦API响应，检查错误码"""
        # ERP 用 success=true，奇门用 flag="success"
        success = data.get("success", False) or data.get("flag") == "success"
        if success:
            return data

        code = str(data.get("code", ""))
        msg = data.get("msg") or data.get("message") or "未知错误"
        trace_id = data.get("trace_id") or data.get("request_id") or ""

        logger.warning(
            f"KuaiMai error | method={method} | code={code} | msg={msg} | trace_id={trace_id}"
        )

        if code == _SIGNATURE_ERROR_CODE:
            raise KuaiMaiSignatureError(original_error=msg)

        if code in _TOKEN_EXPIRED_CODES:
            raise KuaiMaiTokenExpiredError(original_error=msg)

        raise KuaiMaiBusinessError(message=msg, code=code, original_error=msg)

    # ========================================
    # Token 刷新
    # ========================================

    async def refresh_token(self) -> bool:
        """刷新 accessToken（带并发互斥锁 + DB 持久化）

        调用 open.token.refresh 接口。成功后三层写入：
        1. 内存（self._access_token / self._refresh_token）
        2. Redis 热缓存（_cache_token，29 天 TTL）
        3. DB org_configs（_token_persister，多租户持久化）← 关键修复

        并发保护：用 Redis SETNX 锁 per-org 互斥，防止多 worker 同时
        请求快麦的 refresh 接口（每次 refresh 会作废前一次的 refreshToken，
        无锁会导致后到的 worker 拿到的新 token 被先到的覆盖作废）。
        拿不到锁的 worker 等待 2s 后从 Redis 读最新 token 即可。

        Returns:
            是否刷新成功
        """
        if not self._refresh_token:
            logger.error("KuaiMai token refresh failed | reason=no_refresh_token")
            return False

        # ── 并发互斥：per-org 锁 ──────────────────────
        from core.redis import RedisClient
        lock_key = f"kuaimai:refresh_lock:{self._org_id or 'default'}"
        lock_token = await RedisClient.acquire_lock(lock_key, timeout=_REFRESH_LOCK_TTL)
        if not lock_token:
            # 已有人在 refresh，等待对方完成后从 Redis 读最新 token
            # 关键：必须比对 token 是否真的变了，不能只判断 access_token 非空
            # （否则 stale token 也会返回 True 误导调用方）
            logger.info(
                f"KuaiMai refresh skipped, another worker is refreshing | "
                f"org={self._org_id} | waiting {_REFRESH_LOCK_WAIT}s"
            )
            old_token = self._access_token
            await asyncio.sleep(_REFRESH_LOCK_WAIT)
            await self.load_cached_token()
            # 只有当 Redis 里读到的 token 与原值不同时，才视为 refresh 成功
            return self._access_token != old_token and bool(self._access_token)

        try:
            return await self._do_refresh_token()
        finally:
            await RedisClient.release_lock(lock_key, lock_token)

    async def _do_refresh_token(self) -> bool:
        """实际执行 refresh（已在并发锁内）"""
        try:
            # Token 刷新不经过 self.request()，避免循环
            common_params: Dict[str, Any] = {
                "method": "open.token.refresh",
                "appKey": self._app_key,
                "session": self._access_token,
                "refreshToken": self._refresh_token,
                # P0：签名时间戳必须用北京时间
                "timestamp": now_cn().strftime("%Y-%m-%d %H:%M:%S"),
                "version": "1.0",
                "format": "json",
                "sign_method": "hmac",
            }
            common_params["sign"] = self.generate_sign(common_params, "hmac")

            client = await self._get_client()
            response = await client.post(self._base_url, data=common_params)
            response.raise_for_status()
            data = response.json()

            if not data.get("success"):
                err_summary = (
                    f"code={data.get('code')} msg={data.get('msg')}"
                )
                logger.error(
                    f"KuaiMai token refresh failed | "
                    f"org={self._org_id} | {err_summary}"
                )
                # 快档告警：refresh API 返回失败 → 立即推企微，不等 healthcheck
                # best-effort，任何异常都不影响主流程
                try:
                    from services.kuaimai.erp_sync_healthcheck import (
                        push_token_refresh_alert,
                    )
                    await push_token_refresh_alert(self._org_id, err_summary)
                except Exception as e:
                    logger.warning(
                        f"KuaiMai refresh alert dispatch failed | "
                        f"org={self._org_id} | error={e}"
                    )
                return False

            # 响应结构: {"session": {"accessToken": "...", "refreshToken": "..."}}
            session = data.get("session") or {}
            new_token = session.get("accessToken", "")
            new_refresh = session.get("refreshToken", "")

            if new_token:
                self._access_token = new_token
            if new_refresh:
                self._refresh_token = new_refresh

            # 1) 写入 Redis 热缓存（29 天 TTL，跨进程共享）
            await self._cache_token()

            # 2) 持久化到 DB org_configs（关键修复 — 防止 Redis 丢失后回退到死态 token）
            await self._persist_token_to_db()

            logger.info(
                f"KuaiMai token refreshed successfully | org={self._org_id}"
            )
            return True

        except Exception as e:
            logger.error(
                f"KuaiMai token refresh exception | org={self._org_id} | error={e}"
            )
            # 快档告警：refresh 调用本身抛异常（网络/签名/解析失败等）
            try:
                from services.kuaimai.erp_sync_healthcheck import (
                    push_token_refresh_alert,
                )
                await push_token_refresh_alert(
                    self._org_id, f"exception: {type(e).__name__}: {e}"
                )
            except Exception as alert_err:
                logger.warning(
                    f"KuaiMai refresh alert dispatch failed | "
                    f"org={self._org_id} | error={alert_err}"
                )
            return False

    async def _persist_token_to_db(self) -> None:
        """通过注入的回调将最新 token 持久化到 DB org_configs。

        失败不抛异常（refresh 主流程已经成功，Redis 已写入；DB 写失败下次还能补救）。
        但有两层可见性保护：
        1. logger.error（人工排查日志）
        2. Redis 状态位 `kuaimai:persist_failure:{org_id}`（healthcheck 感知）

        Why 需要 Redis 状态位:
        如果 DB 长期不可写但 Redis 正常 → ERP 同步用 Redis 缓存的新 token 工作正常
        → erp_sync_state 的 error_count 不会增长 → healthcheck 看不到任何异常
        → 等到 Redis 失效（重启/驱逐）才会暴露 → 此时已经回到雪崩前的危险状态
        所以必须有独立的"持久化失败"信号让 healthcheck 能感知。
        """
        if not self._token_persister:
            # 单租户/散客模式没有 persister，跳过 DB 写入
            logger.debug(
                f"KuaiMai token persister not configured, skip DB write | "
                f"org={self._org_id}"
            )
            return
        if not self._org_id:
            logger.debug(
                "KuaiMai token persister set but no org_id, skip DB write"
            )
            return
        try:
            await self._token_persister(
                self._org_id, self._access_token, self._refresh_token,
            )
            logger.info(
                f"KuaiMai token persisted to DB | org={self._org_id}"
            )
            # 成功后清除失败状态位（如果之前有的话）
            await self._clear_persist_failure_marker()
        except Exception as e:
            logger.error(
                f"KuaiMai token DB persist FAILED | org={self._org_id} | error={e}"
            )
            # 写入 Redis 失败状态位，供 healthcheck 感知
            await self._set_persist_failure_marker(str(e))

    async def _set_persist_failure_marker(self, error_msg: str) -> None:
        """记录 token 持久化失败状态位，TTL 1 小时（自动恢复）"""
        try:
            from core.redis import get_redis
            redis = await get_redis()
            if redis:
                key = f"kuaimai:persist_failure:{self._org_id}"
                await redis.set(key, error_msg[:200], ex=3600)
        except Exception as e:
            logger.warning(
                f"KuaiMai persist failure marker write failed | "
                f"org={self._org_id} | error={e}"
            )

    async def _clear_persist_failure_marker(self) -> None:
        """token 持久化恢复成功后清除失败标记"""
        try:
            from core.redis import get_redis
            redis = await get_redis()
            if redis:
                await redis.delete(f"kuaimai:persist_failure:{self._org_id}")
        except Exception:
            pass  # 清除失败不重要

    def _token_cache_key(self, kind: str) -> str:
        """生成按企业隔离的 token 缓存键"""
        org = self._org_id or "default"
        return f"kuaimai:{kind}:{org}"

    async def _cache_token(self) -> None:
        """将 Token 写入 Redis 缓存（29 天 TTL）

        失败用 warning 级别（不是 debug）— 历史上这个吞掉的异常导致了 12 天
        无人察觉的 token 雪崩，必须保证可见。
        """
        try:
            from core.redis import get_redis

            redis = await get_redis()
            if redis:
                ttl = 29 * 24 * 3600  # 29天
                await redis.set(self._token_cache_key("token"), self._access_token, ex=ttl)
                if self._refresh_token:
                    await redis.set(self._token_cache_key("refresh"), self._refresh_token, ex=ttl)
        except Exception as e:
            logger.warning(
                f"KuaiMai Redis cache token FAILED | org={self._org_id} | error={e}"
            )

    async def load_cached_token(self) -> None:
        """从 Redis 加载缓存的 Token（client 创建时调用，与 _cache_token 配对）

        失败日志用 warning 级别（不是 debug）— 必须可见。
        历史教训：12 天 token 雪崩部分原因就是这条 IO 失败被静默吞掉。
        """
        try:
            from core.redis import get_redis

            redis = await get_redis()
            if redis:
                cached = await redis.get(self._token_cache_key("token"))
                if cached:
                    self._access_token = cached
                    logger.info(f"KuaiMai token loaded from Redis cache | org={self._org_id}")
                cached_refresh = await redis.get(self._token_cache_key("refresh"))
                if cached_refresh:
                    self._refresh_token = cached_refresh
        except Exception as e:
            logger.warning(
                f"KuaiMai Redis load cached token FAILED | "
                f"org={self._org_id} | error={e}"
            )

    _NETWORK_MAX_RETRIES = 3
    _NETWORK_RETRY_DELAY = 2.0  # 首次重试等待秒数，指数退避
    _RATE_LIMIT_MAX_RETRIES = 3
    _RATE_LIMIT_BASE_DELAY = 5.0  # 429 退避起始秒数（5s → 10s → 20s）

    # 429 监控计数器（进程级，用于周期性汇总日志）
    _rate_limit_hits: int = 0       # 触发 429 的总次数
    _rate_limit_recovered: int = 0  # 退避后恢复成功的次数
    _rate_limit_exhausted: int = 0  # 退避耗尽仍失败的次数

    async def request_with_retry(
        self,
        method: str,
        biz_params: Optional[Dict[str, Any]] = None,
        *,
        base_url: Optional[str] = None,
        extra_system_params: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """带 Token 自动刷新 + 网络层重试 + 429 退避的请求

        1. 网络错误（断连/超时/连接重置）→ 指数退避重试最多3次
        2. HTTP 429（API 限流）→ 指数退避重试最多3次（5s/10s/20s）
        3. Token 过期 → 刷新后重试一次
        """
        import asyncio as _asyncio

        kwargs = {"base_url": base_url, "extra_system_params": extra_system_params}
        network_errors = (httpx.RemoteProtocolError, httpx.ConnectError, httpx.ReadTimeout)

        last_exc: Exception | None = None
        had_429 = False
        for attempt in range(1, self._NETWORK_MAX_RETRIES + 1):
            try:
                result = await self._request_with_token_retry(method, biz_params, **kwargs)
                if had_429:
                    KuaiMaiClient._rate_limit_recovered += 1
                    logger.info(
                        f"KuaiMai 429 recovered | method={method} | "
                        f"attempt={attempt}/{self._RATE_LIMIT_MAX_RETRIES}"
                    )
                return result
            except httpx.HTTPStatusError as e:
                # 429 Too Many Requests → 指数退避重试
                if e.response.status_code == 429 and attempt < self._RATE_LIMIT_MAX_RETRIES:
                    if not had_429:
                        KuaiMaiClient._rate_limit_hits += 1
                        had_429 = True
                    delay = self._RATE_LIMIT_BASE_DELAY * (2 ** (attempt - 1))
                    logger.warning(
                        f"KuaiMai 429 rate limited, backing off | method={method} | "
                        f"attempt={attempt}/{self._RATE_LIMIT_MAX_RETRIES} | "
                        f"delay={delay}s"
                    )
                    await _asyncio.sleep(delay)
                    last_exc = e
                    continue
                if e.response.status_code == 429:
                    KuaiMaiClient._rate_limit_exhausted += 1
                    if not had_429:
                        KuaiMaiClient._rate_limit_hits += 1
                raise  # 非 429 的 HTTP 错误直接抛出
            except network_errors as e:
                last_exc = e
                if attempt < self._NETWORK_MAX_RETRIES:
                    delay = self._NETWORK_RETRY_DELAY * (2 ** (attempt - 1))
                    logger.warning(
                        f"KuaiMai network error, retrying | method={method} | "
                        f"attempt={attempt}/{self._NETWORK_MAX_RETRIES} | "
                        f"delay={delay}s | error={e}"
                    )
                    await _asyncio.sleep(delay)

        raise last_exc  # type: ignore[misc]

    @classmethod
    def get_rate_limit_stats(cls) -> dict[str, int]:
        """返回 429 监控计数器（供周期性日志/健康检查使用）"""
        return {
            "hits": cls._rate_limit_hits,
            "recovered": cls._rate_limit_recovered,
            "exhausted": cls._rate_limit_exhausted,
        }

    async def _request_with_token_retry(
        self,
        method: str,
        biz_params: Optional[Dict[str, Any]] = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """Token 过期自动刷新重试（内部方法）"""
        try:
            return await self.request(method, biz_params, **kwargs)
        except KuaiMaiTokenExpiredError:
            logger.info("KuaiMai token expired, attempting refresh")
            refreshed = await self.refresh_token()
            if not refreshed:
                raise KuaiMaiTokenExpiredError(
                    original_error="Token刷新失败，请重新授权"
                )
            return await self.request(method, biz_params, **kwargs)

    # ========================================
    # 生命周期
    # ========================================

    async def close(self) -> None:
        """关闭 httpx 客户端"""
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    async def __aenter__(self) -> "KuaiMaiClient":
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        await self.close()
