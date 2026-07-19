"""手动 Curated Memory 服务。"""

from __future__ import annotations

import asyncio
import hashlib
import json
import unicodedata
import uuid
from typing import Any

from loguru import logger

from core.exceptions import AppException, NotFoundError
from services.memory.embedding import get_embedding
from services.memory_settings import MemorySettingsService


class ManualMemoryService(MemorySettingsService):
    """在 memory_atoms 中管理用户显式保存的原文记忆。"""

    async def get_all_memories(
        self,
        user_id: str,
        org_id: str | None = None,
    ) -> list[dict[str, Any]]:
        try:
            query = (
                self.db.table("memory_atoms")
                .select(
                    "id,content,source_kind,metadata,created_at,updated_at"
                )
                .eq("user_id", user_id)
                .eq("status", "active")
                .eq("is_deleted", False)
            )
            query = (
                query.eq("org_id", org_id)
                if org_id
                else query.is_("org_id", "null")
            )
            response = await asyncio.to_thread(
                query.order("updated_at", desc=True).limit(100).execute
            )
            return [_format_memory(row) for row in (response.data or [])]
        except Exception as exc:
            logger.error(
                "Manual memory list failed | user_id={} | org_id={} | "
                "error_type={}",
                user_id,
                org_id,
                type(exc).__name__,
            )
            raise _unavailable() from exc

    async def get_memory_count(
        self,
        user_id: str,
        org_id: str | None = None,
    ) -> int:
        return len(await self.get_all_memories(user_id, org_id=org_id))

    async def add_memory(
        self,
        user_id: str,
        content: str,
        source: str = "manual",
        org_id: str | None = None,
    ) -> list[dict[str, Any]]:
        normalized = _normalize_content(content)
        embedding = await get_embedding(normalized)
        if not embedding:
            raise _unavailable()
        outcome = await self._rpc(
            "create_manual_memory",
            {
                "p_org_id": org_id,
                "p_user_id": user_id,
                "p_content": normalized,
                "p_content_hash": _content_hash(normalized),
                "p_embedding": json.dumps(embedding, separators=(",", ":")),
                "p_priority": 70,
            },
            user_id=user_id,
            org_id=org_id,
        )
        if outcome.get("outcome") == "limit_reached":
            raise AppException(
                code="MEMORY_LIMIT_REACHED",
                message="记忆数量已达上限（100条），请先清理旧记忆",
                status_code=400,
            )
        if outcome.get("outcome") not in {"created", "existing"}:
            raise _unavailable()
        return [{
            "id": str(outcome["id"]),
            "memory": normalized,
            "metadata": {"source": "manual"},
            "created_at": _text(outcome.get("created_at")),
            "updated_at": _text(outcome.get("updated_at")),
        }]

    async def update_memory(
        self,
        memory_id: str,
        content: str,
        user_id: str = "",
        org_id: str | None = None,
    ) -> dict[str, Any]:
        _validate_uuid(memory_id)
        normalized = _normalize_content(content)
        embedding = await get_embedding(normalized)
        if not embedding:
            raise _unavailable()
        outcome = await self._rpc(
            "update_manual_memory",
            {
                "p_org_id": org_id,
                "p_user_id": user_id,
                "p_memory_id": memory_id,
                "p_content": normalized,
                "p_content_hash": _content_hash(normalized),
                "p_embedding": json.dumps(embedding, separators=(",", ":")),
            },
            user_id=user_id,
            org_id=org_id,
        )
        if outcome.get("outcome") == "duplicate":
            raise AppException(
                code="MEMORY_DUPLICATE",
                message="相同记忆已存在",
                status_code=409,
            )
        if outcome.get("outcome") != "updated":
            raise NotFoundError("记忆", memory_id)
        return {
            "id": memory_id,
            "memory": normalized,
            "updated_at": _text(outcome.get("updated_at")),
        }

    async def delete_memory(
        self,
        memory_id: str,
        user_id: str = "",
        org_id: str | None = None,
    ) -> None:
        _validate_uuid(memory_id)
        outcome = await self._rpc(
            "delete_memory_atom",
            {
                "p_org_id": org_id,
                "p_user_id": user_id,
                "p_memory_id": memory_id,
            },
            user_id=user_id,
            org_id=org_id,
        )
        if outcome.get("outcome") != "deleted":
            raise NotFoundError("记忆", memory_id)

    async def delete_all_memories(
        self,
        user_id: str,
        org_id: str | None = None,
    ) -> None:
        outcome = await self._rpc(
            "clear_memory_atoms",
            {"p_org_id": org_id, "p_user_id": user_id},
            user_id=user_id,
            org_id=org_id,
        )
        if outcome.get("outcome") != "cleared":
            raise _unavailable()

    async def _rpc(
        self,
        name: str,
        params: dict[str, Any],
        *,
        user_id: str,
        org_id: str | None,
    ) -> dict[str, Any]:
        try:
            response = await asyncio.to_thread(
                self.db.rpc(name, params).execute
            )
            return response.data if isinstance(response.data, dict) else {}
        except AppException:
            raise
        except Exception as exc:
            logger.error(
                "Manual memory RPC failed | action={} | user_id={} | "
                "org_id={} | error_type={}",
                name,
                user_id,
                org_id,
                type(exc).__name__,
            )
            raise _unavailable() from exc


def _normalize_content(content: str) -> str:
    normalized = unicodedata.normalize("NFKC", content).strip()
    if not normalized or len(normalized) > 500:
        raise AppException(
            code="MEMORY_CONTENT_INVALID",
            message="记忆内容不能为空且不能超过500字",
            status_code=400,
        )
    return normalized


def _content_hash(content: str) -> str:
    return hashlib.sha256(content.casefold().encode("utf-8")).hexdigest()


def _validate_uuid(memory_id: str) -> None:
    try:
        uuid.UUID(memory_id)
    except (TypeError, ValueError, AttributeError):
        raise NotFoundError("记忆", memory_id) from None


def _format_memory(row: dict[str, Any]) -> dict[str, Any]:
    metadata = row.get("metadata")
    source = "manual" if row.get("source_kind") == "manual" else "auto"
    conversation_id = (
        metadata.get("conversation_id")
        if isinstance(metadata, dict)
        else None
    )
    return {
        "id": str(row["id"]),
        "memory": str(row["content"]),
        "metadata": {
            "source": source,
            "conversation_id": conversation_id,
        },
        "created_at": _text(row.get("created_at")),
        "updated_at": _text(row.get("updated_at")),
    }


def _text(value: Any) -> str | None:
    return str(value) if value is not None else None


def _unavailable() -> AppException:
    return AppException(
        code="MEMORY_UNAVAILABLE",
        message="记忆功能暂不可用",
        status_code=503,
    )
