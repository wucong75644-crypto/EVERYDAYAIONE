"""
快麦ERP API 客户端

职责：签名计算、HTTP 请求、Token 自动刷新。
遵循项目 httpx 懒初始化 + tenacity 重试模式。
"""

import hashlib
import hmac as hmac_mod
from datetime import datetime
from typing import Any, Dict, Optional

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

# Token 过期相关的错误码
_TOKEN_EXPIRED_CODES = {"27", "105", "106"}
_SIGNATURE_ERROR_CODE = "25"


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
    ) -> None:
        self._app_key = app_key or settings.kuaimai_app_key or ""
        self._app_secret = app_secret or settings.kuaimai_app_secret or ""
        self._access_token = access_token or settings.kuaimai_access_token or ""
        self._refresh_token = refresh_token or settings.kuaimai_refresh_token or ""
        self._base_url = base_url or settings.kuaimai_base_url
        self._timeout = timeout or settings.kuaimai_timeout
        self._client: Optional[httpx.AsyncClient] = None
        self._org_id = org_id

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
                "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
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
                "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
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
        """刷新 accessToken

        调用 open.token.refresh 接口。
        成功后更新内存中的 token，并尝试写入 Redis 缓存。

        Returns:
            是否刷新成功
        """
        if not self._refresh_token:
            logger.error("KuaiMai token refresh failed | reason=no_refresh_token")
            return False

        try:
            # Token 刷新不经过 self.request()，避免循环
            common_params: Dict[str, Any] = {
                "method": "open.token.refresh",
                "appKey": self._app_key,
                "session": self._access_token,
                "refreshToken": self._refresh_token,
                "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
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
                logger.error(
                    f"KuaiMai token refresh failed | code={data.get('code')} | msg={data.get('msg')}"
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

            # 写入 Redis 缓存（29天 TTL）
            await self._cache_token()

            logger.info("KuaiMai token refreshed successfully")
            return True

        except Exception as e:
            logger.error(f"KuaiMai token refresh exception | error={e}")
            return False

    def _token_cache_key(self, kind: str) -> str:
        """生成按企业隔离的 token 缓存键"""
        org = self._org_id or "default"
        return f"kuaimai:{kind}:{org}"

    async def _cache_token(self) -> None:
        """将 Token ���入 Redis 缓存"""
        try:
            from core.redis import get_redis

            redis = await get_redis()
            if redis:
                ttl = 29 * 24 * 3600  # 29天
                await redis.set(self._token_cache_key("token"), self._access_token, ex=ttl)
                if self._refresh_token:
                    await redis.set(self._token_cache_key("refresh"), self._refresh_token, ex=ttl)
        except Exception as e:
            logger.debug(f"KuaiMai cache token skipped | error={e}")

    async def load_cached_token(self) -> None:
        """启动时从 Redis 加载缓存的 Token"""
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
            logger.debug(f"KuaiMai load cached token skipped | error={e}")

    _NETWORK_MAX_RETRIES = 3
    _NETWORK_RETRY_DELAY = 2.0  # 首次重试等待秒数，指数退避
    _RATE_LIMIT_MAX_RETRIES = 3
    _RATE_LIMIT_BASE_DELAY = 5.0  # 429 退避起始秒数（5s → 10s → 20s）

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
        for attempt in range(1, self._NETWORK_MAX_RETRIES + 1):
            try:
                return await self._request_with_token_retry(method, biz_params, **kwargs)
            except httpx.HTTPStatusError as e:
                # 429 Too Many Requests → 指数退避重试
                if e.response.status_code == 429 and attempt < self._RATE_LIMIT_MAX_RETRIES:
                    delay = self._RATE_LIMIT_BASE_DELAY * (2 ** (attempt - 1))
                    logger.warning(
                        f"KuaiMai 429 rate limited, backing off | method={method} | "
                        f"attempt={attempt}/{self._RATE_LIMIT_MAX_RETRIES} | "
                        f"delay={delay}s"
                    )
                    await _asyncio.sleep(delay)
                    last_exc = e
                    continue
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
