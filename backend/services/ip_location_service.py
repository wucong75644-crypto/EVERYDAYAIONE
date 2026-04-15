"""
IP 地理位置解析服务

通过高德 IP 定位 API 将用户 IP 解析为城市级位置，
注入 LLM 上下文供天气/本地查询使用。

降级策略：Redis 缓存 → 高德 API → 静默跳过（不影响主流程）
"""

import ipaddress
from typing import Optional

import httpx
from fastapi import Request
from loguru import logger

from core.config import settings

# 高德 IP 定位 API 地址
_AMAP_IP_URL = "https://restapi.amap.com/v3/ip"

# Redis 缓存键前缀
_CACHE_PREFIX = "ip_loc:"


def extract_client_ip(request: Request) -> str:
    """从 FastAPI Request 提取真实客户端 IP

    优先级：X-Real-IP → X-Forwarded-For 首个 → request.client.host
    """
    real_ip = request.headers.get("x-real-ip")
    if real_ip:
        return real_ip.strip()

    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()

    if request.client:
        return request.client.host

    return ""


def _is_public_ip(ip: str) -> bool:
    """判断是否为公网 IP（内网/保留地址无法定位）"""
    try:
        addr = ipaddress.ip_address(ip)
        return addr.is_global
    except ValueError:
        return False


async def get_location_by_ip(ip: str) -> Optional[str]:
    """IP → 城市名（如"广东省深圳市"），失败返回 None

    流程：检查公网 IP → Redis 缓存 → 高德 API → 缓存结果
    """
    if not settings.amap_api_key:
        return None

    if not ip or not _is_public_ip(ip):
        return None

    # 1. 查 Redis 缓存
    cached = await _get_cached(ip)
    if cached is not None:
        return cached if cached else None  # 空字符串表示之前查过无结果

    # 2. 调高德 API
    location = await _fetch_from_amap(ip)

    # 3. 写缓存（包括空结果，避免重复查询）
    await _set_cached(ip, location or "")

    return location


async def _fetch_from_amap(ip: str) -> Optional[str]:
    """调用高德 IP 定位 API"""
    try:
        async with httpx.AsyncClient(timeout=settings.ip_location_timeout) as client:
            resp = await client.get(
                _AMAP_IP_URL,
                params={"ip": ip, "key": settings.amap_api_key},
            )
            resp.raise_for_status()
            data = resp.json()

        if data.get("status") != "1":
            logger.debug(f"Amap IP API error | ip={ip} | info={data.get('info')}")
            return None

        province = data.get("province", "")
        city = data.get("city", "")

        # 高德对无法识别的 IP 返回空数组 []
        if isinstance(province, list):
            province = ""
        if isinstance(city, list):
            city = ""

        if not province and not city:
            return None

        # 直辖市去重（如 province="北京市" city="北京市" → "北京市"）
        if province and city and province == city:
            return city

        return f"{province}{city}".strip() or None

    except Exception as e:
        logger.warning(f"Amap IP location failed | ip={ip} | error={type(e).__name__}: {e or 'no detail'}")
        return None


async def _get_cached(ip: str) -> Optional[str]:
    """从 Redis 获取缓存的位置"""
    try:
        from core.redis import get_redis

        redis = await get_redis()
        if redis is None:
            return None
        return await redis.get(f"{_CACHE_PREFIX}{ip}")
    except Exception:
        return None


async def _set_cached(ip: str, location: str) -> None:
    """写入 Redis 缓存"""
    try:
        from core.redis import get_redis

        redis = await get_redis()
        if redis is None:
            return
        await redis.set(
            f"{_CACHE_PREFIX}{ip}",
            location,
            ex=settings.ip_location_cache_ttl,
        )
    except Exception:
        pass
