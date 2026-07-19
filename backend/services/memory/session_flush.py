"""固定 revision 的 Grok 风格 Session Memory Flush。"""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import re
import unicodedata
import weakref
from dataclasses import dataclass
from typing import Any

from loguru import logger

from services.handlers.chat_context.content_extractors import (
    extract_text_from_content,
)

from .config import get_memory_config
from .embedding import get_embeddings
from .l1_extractor import L1Extractor, MemoryAtom


PROMPT_VERSION = "generic-flush-v1"
MAX_FLUSH_MESSAGES = 20
MAX_COMPARISON_LOGS = 25
MAX_COMPARISON_ATOMS = 50
SEMANTIC_DUPLICATE_THRESHOLD = 0.92


@dataclass(frozen=True)
class SessionFlushResult:
    """一次增量 Flush 的确定性结果。"""

    outcome: str
    from_revision: int
    through_revision: int
    decision: str = "NO_MEMORY"
    session_log_id: str | None = None


@dataclass(frozen=True)
class _ComparisonMemory:
    source: str
    content: str
    content_hash: str


class SessionFlushService:
    """读取闭合窗口、提议候选并通过数据库 CAS 原子推进 cursor。"""

    def __init__(self, db_pool: Any):
        self._db = db_pool
        self._cfg = get_memory_config()
        self._locks: weakref.WeakValueDictionary[str, asyncio.Lock] = (
            weakref.WeakValueDictionary()
        )

    async def flush(
        self,
        *,
        user_id: str,
        org_id: str,
        conversation_id: str,
        through_revision: int,
        trigger: str = "turn_committed",
    ) -> SessionFlushResult:
        if through_revision <= 0:
            raise ValueError("through_revision must be positive")
        lock = self._locks.setdefault(conversation_id, asyncio.Lock())
        async with lock:
            return await self._flush_locked(
                user_id=user_id,
                org_id=org_id,
                conversation_id=conversation_id,
                through_revision=through_revision,
                trigger=trigger,
            )

    async def _flush_locked(
        self,
        *,
        user_id: str,
        org_id: str,
        conversation_id: str,
        through_revision: int,
        trigger: str,
    ) -> SessionFlushResult:
        cursor = await self._load_cursor(user_id, org_id, conversation_id)
        if cursor >= through_revision:
            return SessionFlushResult(
                outcome="already_committed",
                from_revision=cursor,
                through_revision=through_revision,
            )

        messages = await self._load_message_window(
            conversation_id,
            cursor,
            through_revision,
        )
        if not messages:
            logger.warning(
                "Memory Flush window empty | user_id={} | conversation_id={} | "
                "from_revision={} | through_revision={}",
                user_id,
                conversation_id,
                cursor,
                through_revision,
            )
            return SessionFlushResult(
                outcome="empty_window",
                from_revision=cursor,
                through_revision=through_revision,
            )

        window_through = max(int(message["revision"]) for message in messages)
        proposal = await L1Extractor(db_pool=self._db).propose(messages)
        if not proposal.success:
            return SessionFlushResult(
                outcome="rejected",
                from_revision=cursor,
                through_revision=window_through,
                decision="INVALID",
            )

        atoms = [
            atom
            for scene in proposal.scenes
            for atom in scene.memories
        ]
        dedup = await self._deduplicate(
            atoms,
            user_id=user_id,
            org_id=org_id,
        )
        if dedup is None:
            return SessionFlushResult(
                outcome="dedup_failed",
                from_revision=cursor,
                through_revision=window_through,
                decision=proposal.decision,
            )
        accepted_atoms, receipt = dedup
        content = _build_session_content(
            proposal.decision,
            accepted_atoms,
            receipt,
        )
        source_refs = _build_source_refs(accepted_atoms)
        commit = await self._commit(
            user_id=user_id,
            org_id=org_id,
            conversation_id=conversation_id,
            expected_revision=cursor,
            through_revision=window_through,
            trigger=trigger,
            content=content,
            source_refs=source_refs,
        )
        return SessionFlushResult(
            outcome=str(commit.get("outcome") or "failed"),
            from_revision=cursor,
            through_revision=int(
                commit.get("cursor_revision") or window_through
            ),
            decision=proposal.decision,
            session_log_id=(
                str(commit["session_log_id"])
                if commit.get("session_log_id") else None
            ),
        )

    async def _load_cursor(
        self,
        user_id: str,
        org_id: str,
        conversation_id: str,
    ) -> int:
        row = await self._db.fetchrow(
            """SELECT l1_cursor_revision
               FROM memory_pipeline_state
               WHERE org_id = $1::uuid
                 AND user_id = $2::uuid
                 AND session_id = $3::uuid""",
            org_id,
            user_id,
            conversation_id,
        )
        if row is None:
            raise RuntimeError("MEMORY_FLUSH_STATE_NOT_FOUND")
        return int(row.get("l1_cursor_revision") or 0)

    async def _load_message_window(
        self,
        conversation_id: str,
        from_revision: int,
        through_revision: int,
    ) -> list[dict[str, Any]]:
        rows = await self._db.fetch(
            """SELECT id::text, role, content, context_revision
               FROM messages
               WHERE conversation_id = $1::uuid
                 AND context_revision > $2
                 AND context_revision <= $3
                 AND role IN ('user', 'assistant')
               ORDER BY context_revision ASC, created_at ASC, id ASC
               LIMIT $4""",
            conversation_id,
            from_revision,
            through_revision,
            MAX_FLUSH_MESSAGES + 1,
        )
        if len(rows) > MAX_FLUSH_MESSAGES:
            boundary_revision = int(
                rows[MAX_FLUSH_MESSAGES - 1]["context_revision"]
            )
            next_revision = int(rows[MAX_FLUSH_MESSAGES]["context_revision"])
            if boundary_revision == next_revision:
                rows = [
                    row for row in rows[:MAX_FLUSH_MESSAGES]
                    if int(row["context_revision"]) < boundary_revision
                ]
            else:
                rows = rows[:MAX_FLUSH_MESSAGES]
        messages: list[dict[str, Any]] = []
        for row in rows:
            text = extract_text_from_content(row.get("content"))
            if not text:
                continue
            messages.append({
                "id": str(row["id"]),
                "role": str(row["role"]),
                "content": text,
                "revision": int(row["context_revision"]),
            })
        return messages

    async def _deduplicate(
        self,
        atoms: list[MemoryAtom],
        *,
        user_id: str,
        org_id: str,
    ) -> tuple[list[MemoryAtom], dict[str, Any]] | None:
        comparisons = await self._load_comparison_memories(user_id, org_id)
        exact_hashes = {item.content_hash for item in comparisons}
        accepted: list[MemoryAtom] = []
        pending: list[tuple[MemoryAtom, str]] = []
        outcomes: list[dict[str, Any]] = []
        for atom in atoms:
            content_hash = _claim_hash(atom.content)
            atom.metadata["content_hash"] = content_hash
            if content_hash in exact_hashes:
                outcomes.append({
                    "content_hash": content_hash,
                    "outcome": "duplicate_exact",
                })
                continue
            exact_hashes.add(content_hash)
            pending.append((atom, content_hash))

        if pending and comparisons:
            texts = [
                atom.content for atom, _ in pending
            ] + [item.content for item in comparisons]
            embeddings = await get_embeddings(texts)
            if embeddings is None:
                return None
            pending_vectors = embeddings[:len(pending)]
            comparison_vectors = embeddings[len(pending):]
            accepted_vectors: list[list[float]] = []
            for (atom, content_hash), vector in zip(
                pending,
                pending_vectors,
                strict=True,
            ):
                best_score = max(
                    (
                        _cosine_similarity(vector, candidate)
                        for candidate in [
                            *comparison_vectors,
                            *accepted_vectors,
                        ]
                    ),
                    default=0.0,
                )
                if best_score >= SEMANTIC_DUPLICATE_THRESHOLD:
                    outcomes.append({
                        "content_hash": content_hash,
                        "outcome": "duplicate_semantic",
                        "score": round(best_score, 6),
                    })
                    continue
                accepted.append(atom)
                accepted_vectors.append(vector)
                outcomes.append({
                    "content_hash": content_hash,
                    "outcome": "accepted",
                })
        else:
            accepted.extend(atom for atom, _ in pending)
            outcomes.extend({
                "content_hash": content_hash,
                "outcome": "accepted",
            } for _, content_hash in pending)

        receipt = {
            "input_count": len(atoms),
            "accepted_count": len(accepted),
            "duplicate_exact_count": sum(
                item["outcome"] == "duplicate_exact" for item in outcomes
            ),
            "duplicate_semantic_count": sum(
                item["outcome"] == "duplicate_semantic" for item in outcomes
            ),
            "compared_session_count": sum(
                item.source == "session" for item in comparisons
            ),
            "compared_legacy_count": sum(
                item.source == "legacy_atom" for item in comparisons
            ),
            "semantic_threshold": SEMANTIC_DUPLICATE_THRESHOLD,
            "outcomes": outcomes,
        }
        return accepted, receipt

    async def _load_comparison_memories(
        self,
        user_id: str,
        org_id: str,
    ) -> list[_ComparisonMemory]:
        log_rows = await self._db.fetch(
            """SELECT content
               FROM memory_session_logs
               WHERE user_id = $1::uuid AND status = 'ready'
               ORDER BY created_at DESC
               LIMIT $2""",
            user_id,
            MAX_COMPARISON_LOGS,
        )
        atom_rows = await self._db.fetch(
            """SELECT content, content_hash
               FROM memory_atoms
               WHERE org_id = $1::uuid
                 AND user_id = $2::uuid
                 AND status = 'active'
                 AND NOT is_deleted
               ORDER BY updated_at DESC
               LIMIT $3""",
            org_id,
            user_id,
            MAX_COMPARISON_ATOMS,
        )
        comparisons: list[_ComparisonMemory] = []
        for row in log_rows:
            payload = row.get("content")
            if isinstance(payload, str):
                payload = json.loads(payload)
            items = payload.get("items", []) if isinstance(payload, dict) else []
            for item in items:
                if not isinstance(item, dict) or not item.get("claim"):
                    continue
                content = str(item["claim"])
                comparisons.append(_ComparisonMemory(
                    source="session",
                    content=content,
                    content_hash=str(
                        item.get("content_hash") or _claim_hash(content)
                    ),
                ))
        for row in atom_rows:
            content = str(row.get("content") or "")
            if not content:
                continue
            comparisons.append(_ComparisonMemory(
                source="legacy_atom",
                content=content,
                content_hash=str(
                    row.get("content_hash") or _claim_hash(content)
                ),
            ))
        return comparisons

    async def _commit(
        self,
        *,
        user_id: str,
        org_id: str,
        conversation_id: str,
        expected_revision: int,
        through_revision: int,
        trigger: str,
        content: dict[str, Any],
        source_refs: list[dict[str, str]],
    ) -> dict[str, Any]:
        canonical = json.dumps(
            content,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        row = await self._db.fetchrow(
            """SELECT commit_memory_session_flush(
                   $1::uuid, $2::uuid, $3::uuid, $4, $5, $6,
                   $7::jsonb, $8::jsonb, $9, $10, $11
               ) AS result""",
            org_id,
            user_id,
            conversation_id,
            expected_revision,
            through_revision,
            trigger,
            canonical,
            json.dumps(source_refs, ensure_ascii=False),
            hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
            self._cfg.l1_extraction_model,
            PROMPT_VERSION,
        )
        result = row.get("result") if row else None
        if isinstance(result, str):
            result = json.loads(result)
        if not isinstance(result, dict):
            raise RuntimeError("MEMORY_FLUSH_COMMIT_RESULT_INVALID")
        return result


def _build_session_content(
    model_decision: str,
    atoms: list[MemoryAtom],
    receipt: dict[str, Any],
) -> dict[str, Any]:
    if not atoms:
        return {
            "decision": "NO_MEMORY",
            "model_decision": model_decision,
            "receipt": receipt,
        }
    return {
        "decision": "CANDIDATES",
        "model_decision": model_decision,
        "items": [
            {
                "claim": atom.content,
                **atom.metadata,
            }
            for atom in atoms
        ],
        "receipt": receipt,
    }


def _build_source_refs(atoms: list[MemoryAtom]) -> list[dict[str, str]]:
    refs: dict[tuple[str, str], dict[str, str]] = {}
    for atom in atoms:
        for evidence in atom.metadata.get("evidence", []):
            if not isinstance(evidence, dict):
                continue
            message_id = str(evidence.get("message_id") or "")
            quote = str(evidence.get("quote") or "")
            if message_id and quote:
                refs[(message_id, quote)] = {
                    "message_id": message_id,
                    "quote": quote,
                }
    return list(refs.values())


def _claim_hash(content: str) -> str:
    normalized = unicodedata.normalize("NFKC", content).casefold()
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _cosine_similarity(left: list[float], right: list[float]) -> float:
    if len(left) != len(right) or not left:
        return 0.0
    dot = sum(a * b for a, b in zip(left, right, strict=True))
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if left_norm == 0 or right_norm == 0:
        return 0.0
    return dot / (left_norm * right_norm)
