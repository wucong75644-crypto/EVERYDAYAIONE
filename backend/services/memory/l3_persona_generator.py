"""
L3 用户画像生成器

综合 L2 场景数据，生成/增量更新用户画像。
四层深度扫描：基础锚点→兴趣图谱→交互协议→认知内核

移植自腾讯 TencentDB-Agent-Memory persona-generator.ts
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

from .config import get_memory_config
from .l1_extractor import _call_qianwen
from .prompts.l3_persona import (
    PERSONA_GENERATION_SYSTEM_PROMPT,
    format_persona_prompt,
)

logger = logging.getLogger(__name__)


class L3PersonaGenerator:
    """L3 用户画像生成器"""

    def __init__(self, db_pool=None):
        self._db = db_pool
        self._cfg = get_memory_config()

    async def generate(
        self,
        user_id: str,
        org_id: str,
        trigger_reason: str = "",
    ) -> bool:
        """
        生成或增量更新用户画像

        流程：
        1. 读取现有 persona（如有）
        2. 读取变化的场景
        3. 构建 prompt（四层扫描）
        4. 调千问生成
        5. upsert memory_personas
        """
        cfg = self._cfg

        # 1. 读取现有 persona
        existing = await self._get_persona(user_id, org_id)
        existing_content = existing["content"] if existing else None
        mode = "incremental" if existing_content else "first"

        # 2. 读取场景
        scenes = await self._get_active_scenes(user_id, org_id)
        if not scenes and not existing_content:
            logger.debug("L3: no scenes and no persona, skipping")
            return False

        # 3. 找出变化的场景
        last_persona_at = existing.get("updated_at") if existing else None
        if last_persona_at and mode == "incremental":
            changed_scenes = [
                s for s in scenes
                if s.get("updated_at", "") > str(last_persona_at)
            ]
        else:
            changed_scenes = scenes

        if not changed_scenes and existing_content:
            logger.debug("L3: no scene changes, skipping")
            return False

        # 4. 构建场景内容
        changed_content = self._format_scenes_content(changed_scenes)

        # 5. 统计
        total_atoms = await self._count_atoms(user_id, org_id)

        # 6. 构建 prompt
        user_prompt = format_persona_prompt(
            mode=mode,
            current_time=datetime.now(timezone.utc).isoformat(),
            total_atoms=total_atoms,
            scene_count=len(scenes),
            changed_scene_count=len(changed_scenes),
            changed_scenes_content=changed_content,
            existing_persona=existing_content,
            trigger_reason=trigger_reason,
        )

        # 7. 调 LLM
        try:
            persona_text = await _call_qianwen(
                system_prompt=PERSONA_GENERATION_SYSTEM_PROMPT,
                user_prompt=user_prompt,
                model=cfg.l3_persona_model,
                timeout=cfg.l3_persona_timeout,
            )
        except Exception as e:
            logger.error(f"L3: LLM call failed: {e}")
            return False

        if not persona_text or len(persona_text.strip()) < 50:
            logger.warning("L3: LLM returned empty/too-short persona")
            return False

        # 清理
        persona_text = persona_text.strip()
        if persona_text.startswith("```"):
            persona_text = persona_text.lstrip("`").lstrip("markdown").lstrip("\n")
        if persona_text.endswith("```"):
            persona_text = persona_text.rstrip("`").rstrip("\n")

        # 截断保护
        if len(persona_text) > cfg.l3_max_chars:
            persona_text = persona_text[:cfg.l3_max_chars]

        # 提取 archetype
        archetype = self._extract_archetype(persona_text)

        # 8. upsert
        await self._upsert_persona(
            user_id=user_id,
            org_id=org_id,
            content=persona_text,
            archetype=archetype,
            trigger_reason=trigger_reason,
            total_atoms=total_atoms,
            total_scenes=len(scenes),
            version=(existing.get("version", 0) + 1) if existing else 1,
        )

        logger.info(f"L3: persona {'updated' if existing else 'created'} ({len(persona_text)} chars)")
        return True

    # ============================
    # 数据读取
    # ============================

    async def _get_persona(self, user_id: str, org_id: str) -> dict | None:
        """读取现有画像"""
        try:
            row = await self._db.fetchrow(
                """SELECT content, archetype, version, updated_at::text
                   FROM memory_personas
                   WHERE org_id = $1 AND user_id = $2""",
                uuid.UUID(org_id), uuid.UUID(user_id),
            )
            return dict(row) if row else None
        except Exception:
            return None

    async def _get_active_scenes(self, user_id: str, org_id: str) -> list[dict]:
        """读取活跃场景"""
        try:
            rows = await self._db.fetch(
                """SELECT title, summary, content, heat, updated_at::text
                   FROM memory_scenes
                   WHERE org_id = $1 AND user_id = $2 AND status = 'active'
                   ORDER BY heat DESC""",
                uuid.UUID(org_id), uuid.UUID(user_id),
            )
            return [dict(r) for r in rows]
        except Exception:
            return []

    async def _count_atoms(self, user_id: str, org_id: str) -> int:
        """统计用户记忆数"""
        try:
            row = await self._db.fetchrow(
                "SELECT COUNT(*) as cnt FROM memory_atoms WHERE org_id = $1 AND user_id = $2 AND NOT is_deleted",
                uuid.UUID(org_id), uuid.UUID(user_id),
            )
            return row["cnt"] if row else 0
        except Exception:
            return 0

    # ============================
    # 写入
    # ============================

    async def _upsert_persona(
        self,
        user_id: str,
        org_id: str,
        content: str,
        archetype: str | None,
        trigger_reason: str,
        total_atoms: int,
        total_scenes: int,
        version: int,
    ) -> None:
        """upsert 画像"""
        await self._db.execute(
            """INSERT INTO memory_personas
                   (id, org_id, user_id, content, archetype, version,
                    trigger_reason, total_atoms_processed, total_scenes)
               VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
               ON CONFLICT (org_id, user_id) DO UPDATE SET
                   content = EXCLUDED.content,
                   archetype = EXCLUDED.archetype,
                   version = EXCLUDED.version,
                   trigger_reason = EXCLUDED.trigger_reason,
                   total_atoms_processed = EXCLUDED.total_atoms_processed,
                   total_scenes = EXCLUDED.total_scenes,
                   updated_at = NOW()""",
            uuid.uuid4(), uuid.UUID(org_id), uuid.UUID(user_id),
            content, archetype, version,
            trigger_reason, total_atoms, total_scenes,
        )

    # ============================
    # 工具
    # ============================

    def _format_scenes_content(self, scenes: list[dict]) -> str:
        """格式化场景内容供 LLM 阅读"""
        if not scenes:
            return "（无变化场景）"

        parts = []
        for i, s in enumerate(scenes):
            parts.append(
                f"### [{i+1}] {s['title']}\n"
                f"**热度**: {s.get('heat', 0)} | **更新**: {s.get('updated_at', '?')}\n\n"
                f"{s.get('content', '')}"
            )
        return "\n\n---\n\n".join(parts)

    def _extract_archetype(self, persona_text: str) -> str | None:
        """从 persona 文本中提取 archetype"""
        import re
        match = re.search(r"\*\*Archetype[^*]*\*\*[:\s]*(.+?)(?:\n|$)", persona_text)
        if match:
            return match.group(1).strip()[:300]
        return None
