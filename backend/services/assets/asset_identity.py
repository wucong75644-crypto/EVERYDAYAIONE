"""Canonical 用户资产存储身份解析。"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import AbstractSet, Literal, Optional
from urllib.parse import unquote, urlsplit

StorageProvider = Literal["workspace", "oss"]
StorageScope = Literal["user", "channel"]

_USER_OWNER_PATTERN = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-"
    r"[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
    re.IGNORECASE,
)
_CHANNEL_OWNER_PATTERN = re.compile(r"^channels/wecom/[0-9a-f]{24}$")


class AssetIdentityError(ValueError):
    """资产无法映射为可信持久存储身份。"""


@dataclass(frozen=True)
class CanonicalAssetIdentity:
    storage_provider: StorageProvider
    storage_key: str


def configured_asset_hosts() -> frozenset[str]:
    """返回配置中的精确 CDN/OSS 主机集合。"""
    from core.config import settings

    candidates = [settings.oss_cdn_domain]
    if settings.oss_bucket_name and settings.oss_endpoint:
        endpoint_host = _config_host(settings.oss_endpoint)
        if endpoint_host:
            candidates.append(f"{settings.oss_bucket_name}.{endpoint_host}")
    return frozenset(
        host
        for value in candidates
        if (host := _config_host(value))
    )


def is_allowed_asset_url(
    url: str,
    *,
    allowed_hosts: Optional[AbstractSet[str]] = None,
) -> bool:
    """仅接受配置主机上的 HTTPS 对象 URL。"""
    try:
        _object_key_from_url(url, allowed_hosts=allowed_hosts)
        return True
    except AssetIdentityError:
        return False


def resolve_asset_identity(
    *,
    original_url: str,
    workspace_path: Optional[str],
    org_id: Optional[str],
    storage_scope: StorageScope,
    storage_owner_key: str,
    allowed_hosts: Optional[AbstractSet[str]] = None,
) -> CanonicalAssetIdentity:
    """把 Workspace/OSS 地址解析为不受域名和 query 影响的唯一身份。"""
    _validate_owner(storage_scope, storage_owner_key, org_id)
    object_key = _object_key_from_url(
        original_url,
        allowed_hosts=allowed_hosts,
    )
    workspace_prefix = _workspace_object_prefix(
        org_id=org_id,
        storage_owner_key=storage_owner_key,
    )

    if workspace_path:
        relative_key = _normalize_key(workspace_path)
        if object_key != f"{workspace_prefix}{relative_key}":
            raise AssetIdentityError("ASSET_WORKSPACE_URL_MISMATCH")
        return CanonicalAssetIdentity("workspace", relative_key)

    if object_key.startswith(workspace_prefix):
        relative_key = _normalize_key(object_key[len(workspace_prefix):])
        return CanonicalAssetIdentity("workspace", relative_key)
    return CanonicalAssetIdentity("oss", object_key)


def _validate_owner(
    storage_scope: StorageScope,
    storage_owner_key: str,
    org_id: Optional[str],
) -> None:
    if storage_scope == "user":
        valid = bool(_USER_OWNER_PATTERN.fullmatch(storage_owner_key))
    elif storage_scope == "channel":
        valid = bool(
            org_id
            and _CHANNEL_OWNER_PATTERN.fullmatch(storage_owner_key)
        )
    else:
        valid = False
    if not valid:
        raise AssetIdentityError("ASSET_STORAGE_OWNER_INVALID")


def _workspace_object_prefix(
    *,
    org_id: Optional[str],
    storage_owner_key: str,
) -> str:
    if org_id:
        return f"workspace/org/{org_id}/{storage_owner_key}/"
    owner_hash = hashlib.md5(
        storage_owner_key.encode("utf-8"),
        usedforsecurity=False,
    ).hexdigest()[:8]
    return f"workspace/personal/{owner_hash}/"


def _object_key_from_url(
    url: str,
    *,
    allowed_hosts: Optional[AbstractSet[str]],
) -> str:
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except (TypeError, ValueError) as exc:
        raise AssetIdentityError("ASSET_URL_INVALID") from exc
    hosts = configured_asset_hosts() if allowed_hosts is None else {
        host.lower().rstrip(".") for host in allowed_hosts
    }
    host = (parsed.hostname or "").lower().rstrip(".")
    if (
        parsed.scheme != "https"
        or not host
        or host not in hosts
        or parsed.username is not None
        or parsed.password is not None
        or port not in (None, 443)
    ):
        raise AssetIdentityError("ASSET_URL_NOT_PERSISTED")
    return _normalize_key(unquote(parsed.path).lstrip("/"))


def _normalize_key(value: str) -> str:
    candidate = value.strip()
    if (
        not candidate
        or candidate.startswith("/")
        or candidate.endswith("/")
        or "\\" in candidate
        or "\x00" in candidate
    ):
        raise AssetIdentityError("ASSET_STORAGE_KEY_INVALID")
    parts = candidate.split("/")
    if any(part in ("", ".", "..") for part in parts):
        raise AssetIdentityError("ASSET_STORAGE_KEY_INVALID")
    return "/".join(parts)


def _config_host(value: object) -> str:
    text = str(value or "").strip().lower().rstrip(".")
    if not text:
        return ""
    parsed = urlsplit(text if "://" in text else f"//{text}")
    return (parsed.hostname or "").lower().rstrip(".")
