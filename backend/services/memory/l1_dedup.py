"""
L1 冲突检测服务

两阶段去重：
1. 候选召回（pgvector 余弦相似度 或 tsvector BM25 降级）
2. 批量 LLM 判断（store/update/merge/skip）

移植自腾讯 TencentDB-Agent-Memory l1-dedup.ts
"""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Literal

import jieba

from .config import get_memory_config
from .l1_extractor import MemoryAtom, _call_qianwen, _get_embedding, _insert_atom
from .prompts.l1_dedup import (
from loguru import logger
    CONFLICT_DETECTION_SYSTEM_PROMPT,
    format_batch_conflict_prompt,
)



VALID_TYPES = {"persona", "episodic", "instruction"}


# ============================================================
# Types
# ============================================================

@dataclass
class DedupDecision:
    """冲突检测决策"""
    record_id: str
    action: Literal["store", "update", "merge", "skip"]
    target_ids: list[str] = field(default_factory=list)
    merged_content: str | None = None
    merged_type: str | None = None
    merged_priority: int | None = None
    merged_timestamps: list[str] = field(default_factory=list)


@dataclass
class CandidateMatch:
    """新记忆 + 候选列表"""
    new_memory: MemoryAtom
    candidates: list[dict]  # [{record_id, content, type, priority, scene_name, timestamps}]


# ============================================================
# Service
# ============================================================

class L1DedupService:
    """L1 冲突检测 + 存储"""

    def __init__(self, db_pool=None):
        self._db = db_pool
        self._cfg = get_memory_config()

    async def dedup_and_store(
        self,
        new_atoms: list[MemoryAtom],
        user_id: str,
        org_id: str,
        session_id: str,
    ) -> list[str]:
        """
        去重 + 存储：返回最终存储的 atom ID 列表

        流程：
        1. 检查是否有候选（DB 是否有数据）
        2. 向量召回候选 或 BM25 降级
        3. LLM 批量判断
        4. 执行决策（store/update/merge/skip）
        """
        if not new_atoms:
            return []

        # 检查是否有已有记忆
        existing_count = await self._count_atoms(user_id, org_id)
        if existing_count == 0:
            logger.debug("L1 dedup: no existing atoms, storing all directly")
            return await self._store_all(new_atoms, user_id, org_id, session_id)

        # Phase 1: 候选召回
        matches = await self._recall_candidates(new_atoms, user_id, org_id)

        # 检查是否有候选
        has_candidates = any(m.candidates for m in matches)
        if not has_candidates:
            logger.debug("L1 dedup: no candidates found, storing all directly")
            return await self._store_all(new_atoms, user_id, org_id, session_id)

        # Phase 2: LLM 批量判断
        decisions = await self._llm_judgment(matches)

        # Phase 3: 执行决策
        return await self._apply_decisions(
            decisions, new_atoms, user_id, org_id, session_id
        )

    # ============================
    # 候选召回
    # ============================

    async def _recall_candidates(
        self,
        atoms: list[MemoryAtom],
        user_id: str,
        org_id: str,
    ) -> list[CandidateMatch]:
        """向量召回（主路径）+ BM25（降级路径）"""
        top_k = 5
        matches: list[CandidateMatch] = []

        # 批量 embedding
        embeddings = []
        for atom in atoms:
            emb = await _get_embedding(atom.content)
            embeddings.append(emb)

        for i, atom in enumerate(atoms):
            query_emb = embeddings[i]
            candidates = []

            if query_emb:
                # 向量召回
                candidates = await self._vector_recall(
                    query_emb, user_id, org_id, top_k, exclude_ids=[]
                )

            if not candidates:
                # BM25 降级
                candidates = await self._bm25_recall(
                    atom.content, user_id, org_id, top_k
                )

            matches.append(CandidateMatch(new_memory=atom, candidates=candidates))

        return matches

    async def _vector_recall(
        self,
        query_embedding: list[float],
        user_id: str,
        org_id: str,
        top_k: int,
        exclude_ids: list[str],
    ) -> list[dict]:
        """pgvector 余弦相似度召回"""
        try:
            embedding_str = f"[{','.join(str(x) for x in query_embedding)}]"
            sql = """
                SELECT id::text as record_id, content, type, priority,
                       scene_name, created_at::text as timestamp_str
                FROM memory_atoms
                WHERE org_id = $1 AND user_id = $2 AND NOT is_deleted
                      AND embedding IS NOT NULL
                ORDER BY embedding <=> $3::vector
                LIMIT $4
            """
            rows = await self._db.fetch(
                sql,
                org_id, user_id, embedding_str, top_k,
            )
            return [
                {
                    "record_id": r["record_id"],
                    "content": r["content"],
                    "type": r["type"],
                    "priority": r["priority"],
                    "scene_name": r["scene_name"] or "",
                    "timestamps": [r["timestamp_str"]] if r["timestamp_str"] else [],
                }
                for r in rows
            ]
        except Exception as e:
            logger.warning(f"L1 dedup: vector recall failed: {e}")
            return []

    async def _bm25_recall(
        self,
        content: str,
        user_id: str,
        org_id: str,
        top_k: int,
    ) -> list[dict]:
        """tsvector 全文搜索降级召回"""
        try:
            tokens = " & ".join(
                t for t in jieba.cut_for_search(content) if len(t) > 1
            )
            if not tokens:
                return []

            sql = """
                SELECT id::text as record_id, content, type, priority,
                       scene_name, created_at::text as timestamp_str,
                       ts_rank_cd(content_tsv, to_tsquery('simple', $1::text)) as rank
                FROM memory_atoms
                WHERE org_id = $2 AND user_id = $3 AND NOT is_deleted
                      AND content_tsv @@ to_tsquery('simple', $4::text)
                ORDER BY rank DESC
                LIMIT $5
            """
            rows = await self._db.fetch(
                sql,
                tokens, org_id, user_id, tokens, top_k,
            )
            return [
                {
                    "record_id": r["record_id"],
                    "content": r["content"],
                    "type": r["type"],
                    "priority": r["priority"],
                    "scene_name": r["scene_name"] or "",
                    "timestamps": [r["timestamp_str"]] if r["timestamp_str"] else [],
                }
                for r in rows
            ]
        except Exception as e:
            logger.warning(f"L1 dedup: BM25 recall failed: {e}")
            return []

    # ============================
    # LLM 判断
    # ============================

    async def _llm_judgment(
        self,
        matches: list[CandidateMatch],
    ) -> list[DedupDecision]:
        """调千问做批量冲突检测"""
        try:
            prompt_input = [
                {
                    "new_memory": {
                        "record_id": m.new_memory.record_id,
                        "content": m.new_memory.content,
                        "type": m.new_memory.type,
                        "priority": m.new_memory.priority,
                        "scene_name": m.new_memory.scene_name,
                    },
                    "candidates": m.candidates,
                }
                for m in matches
            ]

            user_prompt = format_batch_conflict_prompt(prompt_input)

            result = await _call_qianwen(
                system_prompt=CONFLICT_DETECTION_SYSTEM_PROMPT,
                user_prompt=user_prompt,
                model=self._cfg.l1_dedup_model,
                timeout=self._cfg.l1_dedup_timeout,
            )

            return self._parse_decisions(result, matches)

        except Exception as e:
            logger.warning(f"L1 dedup: LLM judgment failed: {e}")
            return [
                DedupDecision(record_id=m.new_memory.record_id, action="store")
                for m in matches
            ]

    def _parse_decisions(
        self,
        raw: str,
        matches: list[CandidateMatch],
    ) -> list[DedupDecision]:
        """解析 LLM 输出的决策 JSON"""
        try:
            cleaned = raw.strip()
            if cleaned.startswith("```"):
                cleaned = re.sub(r"^```(?:json)?\s*\n?", "", cleaned)
                cleaned = re.sub(r"\n?```\s*$", "", cleaned)

            array_match = re.search(r"\[[\s\S]*\]", cleaned)
            if not array_match:
                return self._fallback_store_all(matches)

            parsed = json.loads(array_match.group())
            if not isinstance(parsed, list):
                return self._fallback_store_all(matches)

            decisions: list[DedupDecision] = []
            valid_actions = {"store", "update", "merge", "skip"}

            for item in parsed:
                if not isinstance(item, dict):
                    continue
                rid = str(item.get("record_id", ""))
                if not rid:
                    continue
                action = str(item.get("action", "store"))
                if action not in valid_actions:
                    action = "store"

                decisions.append(DedupDecision(
                    record_id=rid,
                    action=action,
                    target_ids=[str(t) for t in item.get("target_ids", [])],
                    merged_content=item.get("merged_content"),
                    merged_type=item.get("merged_type") if item.get("merged_type") in VALID_TYPES else None,
                    merged_priority=item.get("merged_priority") if isinstance(item.get("merged_priority"), int) else None,
                    merged_timestamps=[str(t) for t in item.get("merged_timestamps", [])],
                ))

            # 补全缺失决策
            decided_ids = {d.record_id for d in decisions}
            for m in matches:
                if m.new_memory.record_id not in decided_ids:
                    decisions.append(DedupDecision(
                        record_id=m.new_memory.record_id, action="store"
                    ))

            return decisions

        except Exception as e:
            logger.warning(f"L1 dedup: parse failed: {e}")
            return self._fallback_store_all(matches)

    def _fallback_store_all(self, matches: list[CandidateMatch]) -> list[DedupDecision]:
        return [
            DedupDecision(record_id=m.new_memory.record_id, action="store")
            for m in matches
        ]

    # ============================
    # 执行决策
    # ============================

    async def _apply_decisions(
        self,
        decisions: list[DedupDecision],
        atoms: list[MemoryAtom],
        user_id: str,
        org_id: str,
        session_id: str,
    ) -> list[str]:
        """根据决策执行存储/更新/合并/跳过"""
        atom_map = {a.record_id: a for a in atoms}
        stored_ids: list[str] = []

        for decision in decisions:
            atom = atom_map.get(decision.record_id)
            if not atom:
                continue

            if decision.action == "skip":
                logger.debug(f"L1 dedup: skip {atom.content[:40]}...")
                continue

            if decision.action in ("update", "merge") and decision.target_ids:
                # 软删除旧记忆
                await self._soft_delete_atoms(decision.target_ids)
                # 用合并后的内容创建新记忆
                if decision.merged_content:
                    atom.content = decision.merged_content
                if decision.merged_type:
                    atom.type = decision.merged_type
                if decision.merged_priority is not None:
                    atom.priority = decision.merged_priority

            # 存储
            atom_id = await _insert_atom(self._db, atom, user_id, org_id, session_id)
            if atom_id:
                stored_ids.append(atom_id)

        return stored_ids

    async def _soft_delete_atoms(self, atom_ids: list[str]) -> None:
        """软删除旧记忆"""
        if not atom_ids:
            return
        try:
            valid_ids = [uuid.UUID(aid) for aid in atom_ids]
            await self._db.execute(
                "UPDATE memory_atoms SET is_deleted = TRUE, updated_at = NOW() WHERE id = ANY($1)",
                valid_ids,
            )
            logger.debug(f"L1 dedup: soft-deleted {len(valid_ids)} atoms")
        except Exception as e:
            logger.warning(f"L1 dedup: soft delete failed: {e}")

    # ============================
    # 工具
    # ============================

    async def _store_all(
        self,
        atoms: list[MemoryAtom],
        user_id: str,
        org_id: str,
        session_id: str,
    ) -> list[str]:
        """直接全部存储"""
        stored = []
        for atom in atoms:
            atom_id = await _insert_atom(self._db, atom, user_id, org_id, session_id)
            if atom_id:
                stored.append(atom_id)
        return stored

    async def _count_atoms(self, user_id: str, org_id: str) -> int:
        """统计用户已有记忆数"""
        try:
            row = await self._db.fetchrow(
                "SELECT COUNT(*) as cnt FROM memory_atoms WHERE org_id = $1 AND user_id = $2 AND NOT is_deleted",
                uuid.UUID(org_id), uuid.UUID(user_id),
            )
            return row["cnt"] if row else 0
        except Exception:
            return 0
