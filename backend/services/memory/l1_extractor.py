"""
L1 原子事实提取器

从 L0 对话消息中提取结构化记忆，写入 memory_atoms 表。
移植自腾讯 TencentDB-Agent-Memory l1-extractor.ts，适配 Python + PostgreSQL。

流程：
1. 质量过滤
2. 分割背景+新消息
3. LLM 提取（情境切分+记忆提取）
4. 冲突检测
5. 写入 PostgreSQL（向量+全文双索引）
"""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import jieba

from .config import get_memory_config
from .prompts.l1_extraction import (
from loguru import logger
    EXTRACT_MEMORIES_SYSTEM_PROMPT,
    format_extraction_prompt,
)



# ============================================================
# Types
# ============================================================

VALID_TYPES = {"persona", "episodic", "instruction"}
TYPE_ALIASES = {"episode": "episodic", "instruct": "instruction", "preference": "persona"}


@dataclass
class MemoryAtom:
    """提取出的原子记忆（写入前）"""
    content: str
    type: str                           # persona / episodic / instruction
    priority: int = 50
    scene_name: str = ""
    source_message_ids: list[str] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)
    record_id: str = field(default_factory=lambda: f"m_{uuid.uuid4().hex[:12]}")


@dataclass
class SceneSegment:
    """LLM 输出的情境分段"""
    scene_name: str
    message_ids: list[str]
    memories: list[MemoryAtom]


@dataclass
class L1ExtractionResult:
    """提取结果"""
    success: bool
    extracted_count: int = 0
    stored_count: int = 0
    atom_ids: list[str] = field(default_factory=list)
    scene_names: list[str] = field(default_factory=list)
    last_scene_name: str | None = None


# ============================================================
# Core Extractor
# ============================================================

class L1Extractor:
    """L1 原子事实提取器"""

    def __init__(self, db_pool=None):
        self._db = db_pool
        self._cfg = get_memory_config()

    async def extract(
        self,
        messages: list[dict],
        session_id: str,
        user_id: str,
        org_id: str,
        previous_scene_name: str = "",
    ) -> L1ExtractionResult:
        """
        完整提取管道：过滤 → 提取 → 去重 → 存储

        Args:
            messages: L0 对话消息 [{id, role, content, timestamp}]
            session_id: 会话 ID
            user_id: 用户 ID
            org_id: 组织 ID
            previous_scene_name: 上次情境名（连续性）
        """
        if not messages:
            return L1ExtractionResult(success=True)

        cfg = self._cfg

        # Step 1: 质量过滤
        qualified = [m for m in messages if _should_extract(m.get("content", ""))]
        if not qualified:
            logger.debug("L1: all messages filtered by quality gate")
            return L1ExtractionResult(success=True)

        # Step 2: 分割背景+新消息
        max_new = cfg.l1_max_messages_per_extraction
        max_bg = cfg.l1_max_background_messages
        new_messages = qualified[-max_new:]
        bg_end = len(qualified) - len(new_messages)
        background = qualified[max(0, bg_end - max_bg):bg_end] if bg_end > 0 else []

        logger.info(f"L1: extracting from {len(new_messages)} new + {len(background)} bg messages")

        # Step 3: LLM 提取
        try:
            scenes = await self._call_llm_extraction(
                new_messages, background, previous_scene_name
            )
        except Exception as e:
            logger.error(f"L1: LLM extraction failed: {e}")
            return L1ExtractionResult(success=False)

        # Step 4: 扁平化所有记忆
        all_atoms: list[MemoryAtom] = []
        scene_names: list[str] = []
        for scene in scenes:
            scene_names.append(scene.scene_name)
            all_atoms.extend(scene.memories)

        if not all_atoms:
            return L1ExtractionResult(
                success=True, scene_names=scene_names,
                last_scene_name=scene_names[-1] if scene_names else None,
            )

        # 限制每 session 最大提取数
        if len(all_atoms) > cfg.l1_max_memories_per_session:
            all_atoms = all_atoms[:cfg.l1_max_memories_per_session]

        logger.info(f"L1: extracted {len(all_atoms)} atoms across {len(scenes)} scenes")

        # Step 5: 冲突检测 + 写入
        from .l1_dedup import L1DedupService
        dedup = L1DedupService(db_pool=self._db)
        try:
            stored_ids = await dedup.dedup_and_store(
                new_atoms=all_atoms,
                user_id=user_id,
                org_id=org_id,
                session_id=session_id,
            )
        except Exception as e:
            logger.warning(f"L1: dedup failed, storing all directly: {e}")
            stored_ids = await self._store_all_directly(
                all_atoms, user_id, org_id, session_id
            )

        return L1ExtractionResult(
            success=True,
            extracted_count=len(all_atoms),
            stored_count=len(stored_ids),
            atom_ids=stored_ids,
            scene_names=scene_names,
            last_scene_name=scene_names[-1] if scene_names else None,
        )

    # ============================
    # LLM 调用
    # ============================

    async def _call_llm_extraction(
        self,
        new_messages: list[dict],
        background: list[dict],
        previous_scene_name: str,
    ) -> list[SceneSegment]:
        """调千问提取"""
        user_prompt = format_extraction_prompt(
            new_messages=new_messages,
            background_messages=background,
            previous_scene_name=previous_scene_name or "无",
        )

        result = await _call_qianwen(
            system_prompt=EXTRACT_MEMORIES_SYSTEM_PROMPT,
            user_prompt=user_prompt,
            model=self._cfg.l1_extraction_model,
            timeout=self._cfg.l1_extraction_timeout,
        )

        return _parse_extraction_result(result)

    # ============================
    # 直接存储（降级路径）
    # ============================

    async def _store_all_directly(
        self,
        atoms: list[MemoryAtom],
        user_id: str,
        org_id: str,
        session_id: str,
    ) -> list[str]:
        """跳过去重，直接全部存储"""
        stored = []
        for atom in atoms:
            atom_id = await _insert_atom(self._db, atom, user_id, org_id, session_id)
            if atom_id:
                stored.append(atom_id)
        return stored


# ============================================================
# 存储函数
# ============================================================

async def _insert_atom(
    db_pool,
    atom: MemoryAtom,
    user_id: str,
    org_id: str,
    session_id: str,
) -> str | None:
    """写入单条 memory_atom（含 embedding + tsvector）"""
    try:
        atom_id = str(uuid.uuid4())

        # jieba 分词生成 tsvector
        tokens = " ".join(jieba.cut_for_search(atom.content))

        # 生成 embedding
        embedding = await _get_embedding(atom.content)

        # 解析时间字段
        activity_start = atom.metadata.get("activity_start_time")
        activity_end = atom.metadata.get("activity_end_time")

        now = datetime.now(timezone.utc).isoformat()

        # psycopg 兼容：UUID 用字符串，vector 用字符串格式，数组用 list
        source_ids_str = [sid for sid in atom.source_message_ids if _is_uuid(sid)]
        session_id_val = session_id if session_id else None

        # embedding → pgvector 字符串格式 "[0.1, 0.2, ...]"
        embedding_str = f"[{','.join(str(x) for x in embedding)}]" if embedding else None

        # merge_timestamps → PostgreSQL TIMESTAMPTZ[] 直接用 list
        merge_ts = [datetime.now(timezone.utc)]

        sql = """
            INSERT INTO memory_atoms (
                id, org_id, user_id, content, type, priority, scene_name,
                source_message_ids, session_id,
                activity_start_time, activity_end_time, merge_timestamps,
                embedding, content_tsv, metadata,
                created_at, updated_at
            ) VALUES (
                $1::uuid, $2::uuid, $3::uuid, $4, $5, $6, $7,
                $8::uuid[], $9::uuid,
                $10, $11, $12,
                $13::vector, to_tsvector('simple', $14), $15::jsonb,
                $16, $17
            )
        """

        await db_pool.execute(
            sql,
            atom_id, org_id, user_id,
            atom.content, atom.type, atom.priority, atom.scene_name,
            source_ids_str, session_id_val,
            _parse_iso_time(activity_start), _parse_iso_time(activity_end),
            merge_ts,
            embedding_str, tokens, json.dumps(atom.metadata, ensure_ascii=False),
            now, now,
        )

        logger.debug(f"L1: stored atom {atom_id}: {atom.content[:60]}...")
        return atom_id

    except Exception as e:
        logger.error(f"L1: failed to insert atom: {e}")
        return None


# ============================================================
# LLM 调用
# ============================================================

async def _call_qianwen(
    system_prompt: str,
    user_prompt: str,
    model: str = "qwen-plus",
    timeout: float = 30.0,
) -> str:
    """调千问 API（通过 DashScope）"""
    import asyncio
    from openai import AsyncOpenAI

    cfg = get_memory_config()

    client = AsyncOpenAI(
        api_key=cfg.dashscope_api_key,
        base_url=cfg.dashscope_base_url or "https://dashscope.aliyuncs.com/compatible-mode/v1",
    )

    response = await asyncio.wait_for(
        client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.3,
            max_tokens=4096,
        ),
        timeout=timeout,
    )

    return response.choices[0].message.content or ""


async def _get_embedding(text: str) -> list[float] | None:
    """获取文本 embedding（text-embedding-v3）"""
    try:
        from openai import AsyncOpenAI

        cfg = get_memory_config()
        client = AsyncOpenAI(
            api_key=cfg.dashscope_api_key,
            base_url=cfg.dashscope_base_url or "https://dashscope.aliyuncs.com/compatible-mode/v1",
        )

        response = await client.embeddings.create(
            model=cfg.embedding_model,
            input=text[:5000],  # 截断保护
            dimensions=cfg.embedding_dimensions,
        )
        return response.data[0].embedding

    except Exception as e:
        logger.warning(f"L1: embedding failed: {e}")
        return None


# ============================================================
# 解析 LLM 输出
# ============================================================

def _parse_extraction_result(raw: str) -> list[SceneSegment]:
    """解析 LLM 的 JSON 输出为 SceneSegment 列表"""
    try:
        cleaned = raw.strip()
        if cleaned.startswith("```"):
            cleaned = re.sub(r"^```(?:json)?\s*\n?", "", cleaned)
            cleaned = re.sub(r"\n?```\s*$", "", cleaned)

        # 提取 JSON 数组
        match = re.search(r"\[[\s\S]*\]", cleaned)
        if not match:
            logger.warning(f"L1: no JSON array in extraction response (len={len(raw)})")
            return []

        parsed = json.loads(match.group())
        if not isinstance(parsed, list):
            return []

        scenes: list[SceneSegment] = []
        for item in parsed:
            if not isinstance(item, dict):
                continue

            memories: list[MemoryAtom] = []
            for m in item.get("memories", []):
                if not isinstance(m, dict) or not m.get("content"):
                    continue
                mem_type = _normalize_type(m.get("type", "episodic"))
                if not mem_type:
                    continue
                memories.append(MemoryAtom(
                    content=str(m["content"]),
                    type=mem_type,
                    priority=int(m.get("priority", 50)),
                    source_message_ids=[str(s) for s in m.get("source_message_ids", [])],
                    metadata=m.get("metadata", {}),
                    scene_name=item.get("scene_name", "未知情境"),
                ))

            scenes.append(SceneSegment(
                scene_name=item.get("scene_name", "未知情境"),
                message_ids=[str(mid) for mid in item.get("message_ids", [])],
                memories=memories,
            ))

        return scenes

    except (json.JSONDecodeError, KeyError, TypeError) as e:
        logger.warning(f"L1: failed to parse extraction result: {e}")
        return []


# ============================================================
# 工具函数
# ============================================================

def _normalize_type(raw: str) -> str | None:
    """标准化记忆类型"""
    lower = raw.lower().strip()
    if lower in VALID_TYPES:
        return lower
    return TYPE_ALIASES.get(lower)


def _should_extract(content: str) -> bool:
    """L1 质量门：决定消息是否值得提取"""
    if not content:
        return False
    stripped = content.strip()
    if not stripped:
        return False
    if len(stripped) > 50000:
        return False
    # 跳过纯命令
    if stripped.startswith("/") and len(stripped) < 50:
        return False
    # 含触发词的短消息直接通过
    trigger_keywords = ["喜欢", "习惯", "记住", "以后", "从现在", "必须"]
    if any(kw in stripped for kw in trigger_keywords):
        return True
    # 跳过超短消息（中文信息密度高，阈值设低）
    if len(stripped) < 8:
        return False
    return True


def _is_uuid(s: str) -> bool:
    """检查字符串是否为合法 UUID"""
    try:
        uuid.UUID(s)
        return True
    except (ValueError, AttributeError):
        return False


def _parse_iso_time(s: str | None) -> datetime | None:
    """解析 ISO 8601 时间字符串"""
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None
