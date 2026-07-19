"""Grok Dream 式 Session Memory → Curated Memory 晋升。"""

from __future__ import annotations

import hashlib
import json
import asyncio
import weakref
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from .candidate_validator import validate_memory_candidate
from .config import get_memory_config
from .contracts import MemoryCandidate, parse_memory_candidate
from .embedding import get_embeddings
from .l1_extractor import _call_qianwen
from .prompts.consolidation import (
    CONSOLIDATION_SYSTEM_PROMPT,
    format_consolidation_prompt,
)


PROMPT_VERSION = "generic-consolidation-v1"
MIN_SOURCE_LOGS = 3
MAX_SOURCE_LOGS = 25
MIN_INTERVAL = timedelta(hours=4)
MAX_CURATED_MEMORIES = 100
_RELATIONS = {"novel", "duplicate", "supersedes", "conflicts"}
_LEGACY_TYPE = {
    "user_profile": "persona",
    "preference": "persona",
    "instruction": "instruction",
    "decision": "episodic",
    "reusable_context": "persona",
    "problem_solution": "episodic",
    "tracked_plan": "episodic",
    "skill_defined": "instruction",
}


@dataclass(frozen=True)
class ConsolidationResult:
    outcome: str
    input_count: int = 0
    promoted_count: int = 0
    run_id: str | None = None


@dataclass(frozen=True)
class _SessionCandidate:
    ref: str
    source_log_id: str
    candidate: MemoryCandidate
    content_hash: str


class MemoryConsolidator:
    """严格复验来源并通过单 RPC 原子晋升 Curated Memory。"""

    def __init__(self, db_pool: Any):
        self._db = db_pool
        self._cfg = get_memory_config()
        self._locks: weakref.WeakValueDictionary[str, asyncio.Lock] = (
            weakref.WeakValueDictionary()
        )

    async def consolidate(
        self,
        *,
        user_id: str,
        org_id: str,
    ) -> ConsolidationResult:
        lock = self._locks.get(user_id)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[user_id] = lock
        async with lock:
            return await self._consolidate_locked(user_id=user_id, org_id=org_id)

    async def _consolidate_locked(
        self,
        *,
        user_id: str,
        org_id: str,
    ) -> ConsolidationResult:
        if not await self._interval_elapsed(user_id):
            return ConsolidationResult(outcome="not_due")
        logs = await self._load_ready_logs(user_id)
        if len(logs) < MIN_SOURCE_LOGS:
            return ConsolidationResult(outcome="not_enough_logs")
        try:
            candidates = _parse_session_candidates(logs)
        except (AttributeError, TypeError, ValueError, json.JSONDecodeError):
            return ConsolidationResult(outcome="invalid_session_log")
        if not candidates:
            return await self._commit(
                user_id=user_id,
                org_id=org_id,
                logs=logs,
                candidates=[],
                relations={},
                embeddings=[],
            )
        if any(item.candidate.explicitness != "explicit" for item in candidates):
            return ConsolidationResult(
                outcome="explicitness_rejected",
                input_count=len(candidates),
            )
        messages = await self._load_evidence_messages(candidates)
        if any(
            not validate_memory_candidate(item.candidate, messages).accepted
            for item in candidates
        ):
            return ConsolidationResult(
                outcome="evidence_rejected",
                input_count=len(candidates),
            )
        curated = await self._load_curated_memories(user_id, org_id)
        relations = await self._classify_relations(candidates, curated)
        if relations is None:
            return ConsolidationResult(
                outcome="relation_rejected",
                input_count=len(candidates),
            )
        promotable = [
            item for item in candidates
            if relations[item.ref]["relation"] != "duplicate"
        ]
        embeddings = await get_embeddings([
            item.candidate.claim for item in promotable
        ])
        if embeddings is None:
            return ConsolidationResult(
                outcome="embedding_failed",
                input_count=len(candidates),
            )
        return await self._commit(
            user_id=user_id,
            org_id=org_id,
            logs=logs,
            candidates=candidates,
            relations=relations,
            embeddings=embeddings,
        )

    async def _interval_elapsed(self, user_id: str) -> bool:
        row = await self._db.fetchrow(
            """SELECT completed_at
               FROM memory_consolidation_runs
               WHERE user_id = $1::uuid AND status = 'completed'
               ORDER BY completed_at DESC
               LIMIT 1""",
            user_id,
        )
        if not row or not row.get("completed_at"):
            return True
        value = row["completed_at"]
        if isinstance(value, str):
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return datetime.now(timezone.utc) - value >= MIN_INTERVAL

    async def _load_ready_logs(self, user_id: str) -> list[dict[str, Any]]:
        rows = await self._db.fetch(
            """SELECT id::text, content
               FROM memory_session_logs
               WHERE user_id = $1::uuid AND status = 'ready'
               ORDER BY created_at ASC
               LIMIT $2""",
            user_id,
            MAX_SOURCE_LOGS,
        )
        return [dict(row) for row in rows]

    async def _load_evidence_messages(
        self,
        candidates: list[_SessionCandidate],
    ) -> list[dict[str, Any]]:
        message_ids = sorted({
            evidence.message_id
            for item in candidates
            for evidence in item.candidate.evidence
        })
        rows = await self._db.fetch(
            """SELECT id::text, role, content
               FROM messages
               WHERE id = ANY($1::uuid[])""",
            message_ids,
        )
        return [dict(row) for row in rows]

    async def _load_curated_memories(
        self,
        user_id: str,
        org_id: str,
    ) -> list[dict[str, Any]]:
        rows = await self._db.fetch(
            """SELECT id::text, content, metadata, valid_from, valid_until
               FROM memory_atoms
               WHERE org_id = $1::uuid AND user_id = $2::uuid
                 AND status = 'active' AND NOT is_deleted
               ORDER BY updated_at DESC
               LIMIT $3""",
            org_id,
            user_id,
            MAX_CURATED_MEMORIES,
        )
        return [dict(row) for row in rows]

    async def _classify_relations(
        self,
        candidates: list[_SessionCandidate],
        curated: list[dict[str, Any]],
    ) -> dict[str, dict[str, Any]] | None:
        if not curated:
            return {
                item.ref: {"relation": "novel", "related_memory_ids": []}
                for item in candidates
            }
        raw = await _call_qianwen(
            system_prompt=CONSOLIDATION_SYSTEM_PROMPT,
            user_prompt=format_consolidation_prompt(
                [
                    {
                        "session_candidate_ref": item.ref,
                        "claim": item.candidate.claim,
                        "kind": item.candidate.kind,
                    }
                    for item in candidates
                ],
                [
                    {
                        "curated_memory_id": str(item["id"]),
                        "content": str(item["content"]),
                    }
                    for item in curated
                ],
            ),
            model=self._cfg.consolidation_model,
            timeout=self._cfg.consolidation_timeout,
        )
        return _parse_relations(raw, candidates, curated)

    async def _commit(
        self,
        *,
        user_id: str,
        org_id: str,
        logs: list[dict[str, Any]],
        candidates: list[_SessionCandidate],
        relations: dict[str, dict[str, Any]],
        embeddings: list[list[float]],
    ) -> ConsolidationResult:
        vector_by_ref = {
            item.ref: vector
            for item, vector in zip(
                [
                    item for item in candidates
                    if relations[item.ref]["relation"] != "duplicate"
                ],
                embeddings,
                strict=True,
            )
        }
        operations = [
            _build_operation(item, relations[item.ref], vector_by_ref.get(item.ref))
            for item in candidates
        ]
        source_log_ids = [str(row["id"]) for row in logs]
        source_hash = _source_hash(source_log_ids, candidates)
        receipt = {
            "relations": {
                relation: sum(
                    item["relation"] == relation for item in relations.values()
                )
                for relation in sorted(_RELATIONS)
            },
            "input_count": len(candidates),
        }
        row = await self._db.fetchrow(
            """SELECT commit_memory_consolidation(
                   $1::uuid, $2::uuid, $3::uuid[], $4, $5::jsonb,
                   $6, $7, $8::jsonb
               ) AS result""",
            org_id,
            user_id,
            source_log_ids,
            source_hash,
            json.dumps(operations, ensure_ascii=False),
            self._cfg.consolidation_model,
            PROMPT_VERSION,
            json.dumps(receipt, ensure_ascii=False),
        )
        result = row.get("result") if row else None
        if isinstance(result, str):
            result = json.loads(result)
        if not isinstance(result, dict):
            raise RuntimeError("MEMORY_CONSOLIDATION_COMMIT_RESULT_INVALID")
        return ConsolidationResult(
            outcome=str(result.get("outcome") or "failed"),
            input_count=len(candidates),
            promoted_count=int(result.get("promoted_count") or 0),
            run_id=str(result["run_id"]) if result.get("run_id") else None,
        )


def _parse_session_candidates(
    logs: list[dict[str, Any]],
) -> list[_SessionCandidate]:
    candidates: list[_SessionCandidate] = []
    for log in logs:
        payload = log.get("content")
        if isinstance(payload, str):
            payload = json.loads(payload)
        items = payload.get("items", []) if isinstance(payload, dict) else []
        for index, raw in enumerate(items):
            candidate = parse_memory_candidate(raw)
            candidates.append(_SessionCandidate(
                ref=f"{log['id']}:{index}",
                source_log_id=str(log["id"]),
                candidate=candidate,
                content_hash=str(raw.get("content_hash") or _hash(candidate.claim)),
            ))
    return candidates


def _parse_relations(
    raw: str,
    candidates: list[_SessionCandidate],
    curated: list[dict[str, Any]],
) -> dict[str, dict[str, Any]] | None:
    try:
        payload = json.loads(raw)
        items = payload.get("items")
        if payload.get("decision") != "RELATIONS" or not isinstance(items, list):
            return None
        expected = {item.ref for item in candidates}
        curated_ids = {str(item["id"]) for item in curated}
        parsed: dict[str, dict[str, Any]] = {}
        for item in items:
            ref = str(item.get("session_candidate_ref") or "")
            relation = str(item.get("relation") or "")
            related = item.get("related_memory_ids")
            if (
                ref not in expected
                or ref in parsed
                or relation not in _RELATIONS
                or not isinstance(related, list)
            ):
                return None
            related_ids = [str(value) for value in related]
            if any(value not in curated_ids for value in related_ids):
                return None
            if (relation == "novel") != (not related_ids):
                return None
            parsed[ref] = {
                "relation": relation,
                "related_memory_ids": related_ids,
            }
        return parsed if set(parsed) == expected else None
    except (AttributeError, TypeError, ValueError, json.JSONDecodeError):
        return None


def _build_operation(
    item: _SessionCandidate,
    relation: dict[str, Any],
    embedding: list[float] | None,
) -> dict[str, Any]:
    candidate = item.candidate
    return {
        "session_candidate_ref": item.ref,
        "source_session_log_id": item.source_log_id,
        "relation": relation["relation"],
        "related_memory_ids": relation["related_memory_ids"],
        "content": candidate.claim,
        "kind": candidate.kind,
        "legacy_type": _LEGACY_TYPE[candidate.kind],
        "priority": 80 if candidate.scope == "long_term" else 60,
        "explicitness": candidate.explicitness,
        "valid_from": candidate.valid_from.isoformat() if candidate.valid_from else None,
        "valid_until": candidate.valid_until.isoformat() if candidate.valid_until else None,
        "content_hash": item.content_hash,
        "source_message_ids": [
            evidence.message_id for evidence in candidate.evidence
        ],
        "metadata": {
            "kind": candidate.kind,
            "scope": candidate.scope,
            "evidence": [
                {
                    "message_id": evidence.message_id,
                    "quote": evidence.quote,
                }
                for evidence in candidate.evidence
            ],
            "attributes": dict(candidate.attributes),
            "session_candidate_ref": item.ref,
        },
        "embedding": embedding,
    }


def _source_hash(
    source_log_ids: list[str],
    candidates: list[_SessionCandidate],
) -> str:
    value = {
        "logs": sorted(source_log_ids),
        "candidates": sorted(item.content_hash for item in candidates),
    }
    canonical = json.dumps(value, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def _hash(content: str) -> str:
    return hashlib.sha256(content.strip().casefold().encode()).hexdigest()
