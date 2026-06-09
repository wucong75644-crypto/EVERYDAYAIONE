"""
快麦 Web 后端 HTTP 调用基类

职责：
  - 统一构造请求（cookie + companyid + 标准 header）
  - 自动检测会话异常（"会话异常，请重新登录"）
  - 网络错误重试（tenacity）

不感知具体业务（不知道是智库还是 viperp），调用方传 url + payload。
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

import httpx
from loguru import logger
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)


# ──────────────────────── 异常 ────────────────────────


class KuaimaiWebError(Exception):
    """快麦 Web 调用基类异常"""


class CookieExpiredError(KuaimaiWebError):
    """Cookie 失效（"会话异常，请重新登录"）—— 上层应触发 credential 标记 expired + 告警"""


class KuaimaiWebHttpError(KuaimaiWebError):
    """HTTP 层错误（非 2xx / 网络异常）"""


# 服务器返回的会话失效消息关键词（POC 验证过的真实文案）
_SESSION_INVALID_KEYWORDS = ("会话异常", "请重新登录", "未授权", "未登录")


# ──────────────────────── 请求构造 ────────────────────────


def _make_trackid() -> str:
    """构造 trackid header（仿浏览器格式，服务器实际不严格校验）"""
    ts = int(time.time() * 1000)
    return f"trackid{ts}_{ts % 100000:05d}"


# 通用 header 模板（POC 实测够用）
_BASE_HEADERS = {
    "accept": "application/json, text/javascript, */*; q=0.01",
    "accept-language": "zh-CN,zh;q=0.9",
    "bx-v": "2.5.11",
    "sec-ch-ua": '"Chromium";v="148", "Google Chrome";v="148", "Not/A)Brand";v="99"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"macOS"',
    "sec-fetch-dest": "empty",
    "sec-fetch-mode": "cors",
    "sec-fetch-site": "same-origin",
    "user-agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/148.0.0.0 Safari/537.36"
    ),
    "x-requested-with": "XMLHttpRequest",
}


def _build_headers(
    *,
    companyid: int,
    cookie: str,
    referer: str,
    module_path: str,
    origin: str,
    extra: dict[str, str] | None = None,
) -> dict[str, str]:
    """构造完整请求 header。"""
    headers = dict(_BASE_HEADERS)
    headers["companyid"] = str(companyid)
    headers["cookie"] = cookie
    headers["referer"] = referer
    headers["module-path"] = module_path
    headers["origin"] = origin
    headers["trackid"] = _make_trackid()
    if extra:
        headers.update(extra)
    return headers


# ──────────────────────── 响应判断 ────────────────────────


def _is_session_invalid(data: Any) -> bool:
    """判断响应是否表示会话失效。"""
    if not isinstance(data, dict):
        return False
    msg = str(data.get("message", "") or data.get("msg", "") or "")
    return any(k in msg for k in _SESSION_INVALID_KEYWORDS)


# ──────────────────────── 客户端 ────────────────────────


@dataclass
class HttpResult:
    status_code: int
    json_body: dict | None
    text_body: str | None


class KuaimaiWebClient:
    """
    快麦 Web 后端通用 HTTP 客户端。

    用法：
        client = KuaimaiWebClient(companyid=65109, cookie="_censeid=xxx; ...")
        result = await client.post(
            url="https://erp.superboss.cc/kmzk/profit/report/shop",
            payload={"startTime": "...", ...},
            module_path="/think_tank/profit_shop/",
            origin="https://erp.superboss.cc",
            referer="https://erp.superboss.cc/index.html",
        )

    出错时抛 CookieExpiredError 或 KuaimaiWebHttpError。
    """

    def __init__(
        self,
        *,
        companyid: int,
        cookie: str,
        timeout: float = 30.0,
    ) -> None:
        if not cookie:
            raise ValueError("cookie 不能为空")
        if not companyid:
            raise ValueError("companyid 不能为空")
        self.companyid = companyid
        self.cookie = cookie
        self.timeout = timeout
        # 内部 client 懒初始化（避免事件循环 binding 问题）
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self.timeout)
        return self._client

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        retry=retry_if_exception_type((httpx.HTTPError, httpx.NetworkError)),
        reraise=True,
    )
    async def post(
        self,
        *,
        url: str,
        payload: dict,
        module_path: str,
        origin: str,
        referer: str,
        content_type: str = "application/x-www-form-urlencoded",
        extra_headers: dict[str, str] | None = None,
    ) -> HttpResult:
        """
        POST 请求（form-urlencoded 默认）。

        Raises:
            CookieExpiredError: 服务器返回会话异常
            KuaimaiWebHttpError: HTTP 非 2xx 或网络错误
        """
        client = await self._get_client()
        headers = _build_headers(
            companyid=self.companyid,
            cookie=self.cookie,
            referer=referer,
            module_path=module_path,
            origin=origin,
            extra={"content-type": f"{content_type}; charset=UTF-8"},
        )
        if extra_headers:
            headers.update(extra_headers)

        try:
            resp = await client.post(url, headers=headers, data=payload)
        except httpx.HTTPError as e:
            logger.error(f"KuaimaiWeb POST 网络错误 | url={url} | err={e}")
            raise

        return self._handle_response(resp, url)

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        retry=retry_if_exception_type((httpx.HTTPError, httpx.NetworkError)),
        reraise=True,
    )
    async def get(
        self,
        *,
        url: str,
        module_path: str,
        origin: str,
        referer: str,
        params: dict | None = None,
        extra_headers: dict[str, str] | None = None,
    ) -> HttpResult:
        client = await self._get_client()
        headers = _build_headers(
            companyid=self.companyid,
            cookie=self.cookie,
            referer=referer,
            module_path=module_path,
            origin=origin,
            extra=extra_headers,
        )

        try:
            resp = await client.get(url, headers=headers, params=params)
        except httpx.HTTPError as e:
            logger.error(f"KuaimaiWeb GET 网络错误 | url={url} | err={e}")
            raise

        return self._handle_response(resp, url)

    def _handle_response(self, resp: httpx.Response, url: str) -> HttpResult:
        """统一处理响应：解析 JSON + 检测会话失效。"""
        if resp.status_code >= 400:
            text_preview = resp.text[:200] if resp.text else ""
            logger.warning(
                f"KuaimaiWeb HTTP {resp.status_code} | url={url} | "
                f"body={text_preview!r}"
            )
            raise KuaimaiWebHttpError(
                f"HTTP {resp.status_code} from {url}: {text_preview}"
            )

        try:
            json_body = resp.json()
        except ValueError:
            return HttpResult(
                status_code=resp.status_code,
                json_body=None,
                text_body=resp.text,
            )

        if _is_session_invalid(json_body):
            msg = str(json_body.get("message") or json_body.get("msg"))
            logger.warning(f"KuaimaiWeb 会话失效 | url={url} | msg={msg}")
            raise CookieExpiredError(f"快麦 Web cookie 失效: {msg}")

        return HttpResult(
            status_code=resp.status_code,
            json_body=json_body,
            text_body=None,
        )
