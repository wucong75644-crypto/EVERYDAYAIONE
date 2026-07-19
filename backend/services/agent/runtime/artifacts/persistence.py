"""把 Run-local ArtifactDraft 转为 Actor 原子提交参数。"""

from __future__ import annotations

import asyncio
from typing import Any, Iterable

from loguru import logger

from .normalizer import canonical_json
from .types import ArtifactDraft


_INLINE_BYTES = 64 * 1024


async def materialize_artifacts(
    drafts: Iterable[ArtifactDraft],
    *,
    task_id: str,
    user_id: str,
    org_id: str | None,
) -> list[dict[str, Any]]:
    """小结果内联，大结果上传 OSS；任一失败则终止本次 Actor 提交。"""
    materialized: list[dict[str, Any]] = []
    try:
        for draft in drafts:
            materialized.append(await _materialize_one(
                draft,
                task_id=task_id,
                user_id=user_id,
                org_id=org_id,
            ))
        return materialized
    except BaseException:
        await cleanup_materialized_artifacts(
            materialized,
            task_id=task_id,
        )
        raise


async def cleanup_materialized_artifacts(
    artifacts: Iterable[dict[str, Any]],
    *,
    task_id: str,
) -> None:
    """Best-effort 删除未进入成功 fenced commit 的新 OSS 对象。"""
    object_keys = [
        str(reference["object_key"])
        for artifact in artifacts
        if artifact.get("storage_kind") == "oss"
        and isinstance((reference := artifact.get("storage_ref")), dict)
        and reference.get("object_key")
    ]
    if not object_keys:
        return
    from services.oss_service import get_oss_service

    oss = get_oss_service()
    for object_key in object_keys:
        try:
            await asyncio.to_thread(oss.delete, object_key)
        except Exception as error:
            logger.warning(
                "artifact_orphan_cleanup_failed | "
                f"task_id={task_id} | object_key={object_key} | "
                f"error={type(error).__name__}"
            )


async def _materialize_one(
    draft: ArtifactDraft,
    *,
    task_id: str,
    user_id: str,
    org_id: str | None,
) -> dict[str, Any]:
    encoded = canonical_json(draft.content).encode("utf-8")
    base = {
        "id": draft.artifact_id,
        "tool_call_id": draft.tool_call_id,
        "tool_name": draft.tool_name,
        "artifact_type": draft.artifact_type,
        "model_view": draft.model_view,
        "history_view": draft.history_view,
        "content_hash": draft.content_hash,
        "byte_size": draft.byte_size,
        "metadata": draft.metadata,
        "sensitivity": draft.sensitivity,
        "expires_at": None,
    }
    if len(encoded) <= _INLINE_BYTES:
        return {
            **base,
            "storage_kind": "inline",
            "inline_content": draft.content,
        }

    from services.oss_service import get_oss_service

    oss = get_oss_service()
    uploaded = await asyncio.to_thread(
        oss.upload_bytes,
        encoded,
        user_id,
        "json",
        f"artifacts/{task_id}",
        "application/json",
        org_id,
    )
    return {
        **base,
        "storage_kind": "oss",
        "storage_ref": {
            "object_key": uploaded["object_key"],
            "url": uploaded["url"],
            "content_type": "application/json",
        },
    }
