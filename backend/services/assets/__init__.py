"""统一文件资产识别与查询索引能力。"""

from services.assets.file_identity import AssetIdentity, identify_file
from services.assets.asset_identity import (
    AssetIdentityError,
    CanonicalAssetIdentity,
    configured_asset_hosts,
    is_allowed_asset_url,
    resolve_asset_identity,
)
from services.assets.asset_registry import (
    AssetRefDraft,
    AssetRegistryService,
    ReadyAssetDraft,
    register_message_media_best_effort,
    register_task_media_best_effort,
    register_wecom_attachment_best_effort,
    register_web_upload_best_effort,
)

__all__ = [
    "AssetIdentity",
    "AssetIdentityError",
    "AssetRefDraft",
    "AssetRegistryService",
    "CanonicalAssetIdentity",
    "ReadyAssetDraft",
    "configured_asset_hosts",
    "identify_file",
    "is_allowed_asset_url",
    "register_message_media_best_effort",
    "register_task_media_best_effort",
    "register_wecom_attachment_best_effort",
    "register_web_upload_best_effort",
    "resolve_asset_identity",
]
