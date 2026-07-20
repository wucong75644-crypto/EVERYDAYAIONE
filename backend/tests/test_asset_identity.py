"""Canonical 用户资产存储身份测试。"""
from __future__ import annotations

import hashlib

import pytest

from services.assets.asset_identity import (
    AssetIdentityError,
    StorageScope,
    is_allowed_asset_url,
    resolve_asset_identity,
)

HOSTS = {"cdn.example.com", "bucket.oss-cn.example.com"}
USER_ID = "00000000-0000-4000-8000-000000000001"
ORG_ID = "00000000-0000-4000-8000-000000000002"
CHANNEL_OWNER = "channels/wecom/0123456789abcdef01234567"


def test_resolves_org_workspace_path_independent_of_cdn_query() -> None:
    identity = resolve_asset_identity(
        original_url=(
            f"https://cdn.example.com/workspace/org/{ORG_ID}/{USER_ID}/"
            "%E4%B8%8A%E4%BC%A0/a.png?token=secret"
        ),
        workspace_path="上传/a.png",
        org_id=ORG_ID,
        storage_scope="user",
        storage_owner_key=USER_ID,
        allowed_hosts=HOSTS,
    )

    assert identity.storage_provider == "workspace"
    assert identity.storage_key == "上传/a.png"


def test_derives_personal_workspace_path_from_url_only() -> None:
    owner_hash = hashlib.md5(
        USER_ID.encode(),
        usedforsecurity=False,
    ).hexdigest()[:8]
    identity = resolve_asset_identity(
        original_url=(
            f"https://bucket.oss-cn.example.com/workspace/personal/"
            f"{owner_hash}/下载/result.png"
        ),
        workspace_path=None,
        org_id=None,
        storage_scope="user",
        storage_owner_key=USER_ID,
        allowed_hosts=HOSTS,
    )

    assert identity.storage_provider == "workspace"
    assert identity.storage_key == "下载/result.png"


def test_resolves_wecom_channel_workspace_without_uuid_conversion() -> None:
    identity = resolve_asset_identity(
        original_url=(
            f"https://cdn.example.com/workspace/org/{ORG_ID}/"
            f"{CHANNEL_OWNER}/上传/a.xlsx"
        ),
        workspace_path="上传/a.xlsx",
        org_id=ORG_ID,
        storage_scope="channel",
        storage_owner_key=CHANNEL_OWNER,
        allowed_hosts=HOSTS,
    )

    assert identity.storage_provider == "workspace"
    assert identity.storage_key == "上传/a.xlsx"


def test_keeps_non_workspace_object_as_oss_identity() -> None:
    identity = resolve_asset_identity(
        original_url="https://cdn.example.com/generated/2026/a.png?v=2",
        workspace_path=None,
        org_id=None,
        storage_scope="user",
        storage_owner_key=USER_ID,
        allowed_hosts=HOSTS,
    )

    assert identity.storage_provider == "oss"
    assert identity.storage_key == "generated/2026/a.png"


@pytest.mark.parametrize("url", [
    "http://cdn.example.com/workspace/a.png",
    "https://evil.example.com/workspace/a.png",
    "https://cdn.example.com:444/workspace/a.png",
    "https://user@cdn.example.com/workspace/a.png",
    "https://cdn.example.com/workspace/%2e%2e/secret.png",
    "https://cdn.example.com/workspace/a%5cb.png",
])
def test_rejects_untrusted_or_unsafe_url(url: str) -> None:
    assert is_allowed_asset_url(url, allowed_hosts=HOSTS) is False


def test_rejects_workspace_path_url_mismatch() -> None:
    with pytest.raises(
        AssetIdentityError,
        match="ASSET_WORKSPACE_URL_MISMATCH",
    ):
        resolve_asset_identity(
            original_url=(
                f"https://cdn.example.com/workspace/org/{ORG_ID}/"
                f"{USER_ID}/上传/other.png"
            ),
            workspace_path="上传/a.png",
            org_id=ORG_ID,
            storage_scope="user",
            storage_owner_key=USER_ID,
            allowed_hosts=HOSTS,
        )


@pytest.mark.parametrize(
    ("scope", "owner", "org_id"),
    [
        ("user", "not-a-uuid", None),
        ("channel", "../channels/wecom/0123456789abcdef01234567", ORG_ID),
        ("channel", CHANNEL_OWNER, None),
    ],
)
def test_rejects_invalid_storage_owner(
    scope: StorageScope,
    owner: str,
    org_id: str | None,
) -> None:
    with pytest.raises(AssetIdentityError, match="ASSET_STORAGE_OWNER_INVALID"):
        resolve_asset_identity(
            original_url="https://cdn.example.com/generated/a.png",
            workspace_path=None,
            org_id=org_id,
            storage_scope=scope,
            storage_owner_key=owner,
            allowed_hosts=HOSTS,
        )
