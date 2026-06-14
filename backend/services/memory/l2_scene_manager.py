"""
L2 场景管理器

将 L1 原子记忆整合为语义场景文档。
LLM 输出结构化 JSON 操作指令，代码层执行 DB 操作。

移植自腾讯 TencentDB-Agent-Memory scene-extractor.ts
改造：文件操作 Agent → JSON 指令 + 代码执行（更可控）
"""

from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timezone

from loguru import logger

from .config import get_memory_config
from .l1_extractor import _call_qianwen
from .prompts.l2_scene import (
    SCENE_EXTRACTION_SYSTEM_PROMPT,
    format_scene_extraction_prompt,
)




class L2SceneManager:
    """L2 场景管理器"""

    def __init__(self, db_pool=None):
        self._db = db_pool
        self._cfg = get_memory_config()

    async def extract_scenes(
        self,
        user_id: str,
        org_id: str,
    ) -> dict:
        """
        从新的 L1 记忆中提取/更新场景

        流程：
        1. 读取最近未处理的 atoms
        2. 加载已有场景索引
        3. 构建 prompt，调 LLM
        4. 解析操作指令，执行 DB 操作
        """
        cfg = self._cfg

        # 1. 读取最近的 atoms（L2 未处理的）
        atoms = await self._get_unprocessed_atoms(user_id, org_id)
        if not atoms:
            logger.debug("L2: no new atoms to process")
            return {"success": True, "scenes_updated": 0}

        # 2. 加载已有场景
        scenes = await self._get_active_scenes(user_id, org_id)
        scene_count = len(scenes)

        # 3. 构建 prompt
        memories_json = json.dumps(
            [{"content": a["content"], "type": a["type"], "created_at": a["created_at"]}
             for a in atoms],
            ensure_ascii=False, indent=2,
        )

        scene_summaries = self._build_scene_summaries(scenes) if scenes else ""

        user_prompt = format_scene_extraction_prompt(
            memories_json=memories_json,
            scene_summaries=scene_summaries,
            scene_count=scene_count,
            max_scenes=cfg.l2_max_scenes,
        )

        # 4. 调 LLM
        try:
            result = await _call_qianwen(
                system_prompt=SCENE_EXTRACTION_SYSTEM_PROMPT,
                user_prompt=user_prompt,
                model=cfg.l2_scene_model,
                timeout=cfg.l2_scene_timeout,
            )
        except Exception as e:
            logger.error(f"L2: LLM call failed: {e}")
            return {"success": False, "error": str(e)}

        # 5. 解析并执行
        operations = self._parse_operations(result)
        if not operations:
            logger.debug("L2: no operations from LLM")
            return {"success": True, "scenes_updated": 0}

        executed = await self._execute_operations(operations, user_id, org_id)

        logger.info(f"L2: executed {executed} operations from {len(atoms)} atoms")
        return {"success": True, "scenes_updated": executed}

    # ============================
    # 数据读取
    # ============================

    async def _get_unprocessed_atoms(self, user_id: str, org_id: str, limit: int = 50) -> list[dict]:
        """读取最近的原子记忆（供 L2 处理）"""
        try:
            rows = await self._db.fetch(
                """SELECT id::text, content, type, priority, scene_name,
                          created_at::text
                   FROM memory_atoms
                   WHERE org_id = $1 AND user_id = $2 AND NOT is_deleted
                   ORDER BY created_at DESC
                   LIMIT $3""",
                uuid.UUID(org_id), uuid.UUID(user_id), limit,
            )
            return [dict(r) for r in rows]
        except Exception as e:
            logger.warning(f"L2: failed to read atoms: {e}")
            return []

    async def _get_active_scenes(self, user_id: str, org_id: str) -> list[dict]:
        """读取用户的活跃场景"""
        try:
            rows = await self._db.fetch(
                """SELECT id::text as scene_id, title, summary, content, heat,
                          created_at::text, updated_at::text
                   FROM memory_scenes
                   WHERE org_id = $1 AND user_id = $2 AND status = 'active'
                   ORDER BY heat DESC""",
                uuid.UUID(org_id), uuid.UUID(user_id),
            )
            return [dict(r) for r in rows]
        except Exception as e:
            logger.warning(f"L2: failed to read scenes: {e}")
            return []

    def _build_scene_summaries(self, scenes: list[dict]) -> str:
        """构建场景摘要列表"""
        lines = [f"**当前场景总数：{len(scenes)}**\n"]
        for s in scenes:
            lines.append(f"### {s['title']} (ID: {s['scene_id']})")
            lines.append(f"**热度**: {s['heat']} | **更新**: {s.get('updated_at', '?')}")
            lines.append(f"**summary**: {s['summary']}")
            lines.append("")
        return "\n".join(lines)

    # ============================
    # 解析 LLM 输出
    # ============================

    def _parse_operations(self, raw: str) -> list[dict]:
        """解析 LLM 输出的操作指令"""
        try:
            cleaned = raw.strip()
            if cleaned.startswith("```"):
                cleaned = re.sub(r"^```(?:json)?\s*\n?", "", cleaned)
                cleaned = re.sub(r"\n?```\s*$", "", cleaned)

            match = re.search(r"\[[\s\S]*\]", cleaned)
            if not match:
                return []

            parsed = json.loads(match.group())
            if not isinstance(parsed, list):
                return []

            valid_actions = {"create", "update", "merge", "delete"}
            operations = []
            for item in parsed:
                if not isinstance(item, dict):
                    continue
                action = item.get("action", "")
                if action not in valid_actions:
                    continue
                operations.append(item)

            return operations
        except Exception as e:
            logger.warning(f"L2: failed to parse operations: {e}")
            return []

    # ============================
    # 执行操作
    # ============================

    async def _execute_operations(
        self,
        operations: list[dict],
        user_id: str,
        org_id: str,
    ) -> int:
        """执行场景操作"""
        executed = 0
        for op in operations:
            try:
                action = op["action"]
                if action == "create":
                    await self._create_scene(op, user_id, org_id)
                    executed += 1
                elif action == "update":
                    await self._update_scene(op)
                    executed += 1
                elif action == "merge":
                    await self._merge_scenes(op, user_id, org_id)
                    executed += 1
                elif action == "delete":
                    await self._delete_scene(op)
                    executed += 1
            except Exception as e:
                logger.warning(f"L2: operation failed ({op.get('action')}): {e}")
        return executed

    async def _create_scene(self, op: dict, user_id: str, org_id: str) -> None:
        """创建场景"""
        await self._db.execute(
            """INSERT INTO memory_scenes (id, org_id, user_id, title, summary, content, heat)
               VALUES ($1, $2, $3, $4, $5, $6, $7)""",
            uuid.uuid4(), uuid.UUID(org_id), uuid.UUID(user_id),
            op.get("title", "未命名场景"),
            op.get("summary", ""),
            op.get("content", ""),
            op.get("heat", 1),
        )
        logger.debug(f"L2: created scene '{op.get('title')}'")

    async def _update_scene(self, op: dict) -> None:
        """更新场景"""
        scene_id = op.get("scene_id")
        if not scene_id:
            return
        await self._db.execute(
            """UPDATE memory_scenes SET
                   title = COALESCE($2, title),
                   summary = COALESCE($3, summary),
                   content = COALESCE($4, content),
                   heat = COALESCE($5, heat + 1),
                   updated_at = NOW()
               WHERE id = $1""",
            uuid.UUID(scene_id),
            op.get("title"),
            op.get("summary"),
            op.get("content"),
            op.get("heat"),
        )
        logger.debug(f"L2: updated scene {scene_id}")

    async def _merge_scenes(self, op: dict, user_id: str, org_id: str) -> None:
        """合并场景：创建新场景 + 归档旧场景"""
        source_ids = op.get("source_scene_ids", [])

        # 创建合并后的新场景
        await self._db.execute(
            """INSERT INTO memory_scenes (id, org_id, user_id, title, summary, content, heat)
               VALUES ($1, $2, $3, $4, $5, $6, $7)""",
            uuid.uuid4(), uuid.UUID(org_id), uuid.UUID(user_id),
            op.get("title", "合并场景"),
            op.get("summary", ""),
            op.get("content", ""),
            op.get("heat", 1),
        )

        # 归档旧场景
        for sid in source_ids:
            try:
                await self._db.execute(
                    "UPDATE memory_scenes SET status = 'archived', updated_at = NOW() WHERE id = $1",
                    uuid.UUID(sid),
                )
            except Exception:
                pass

        logger.debug(f"L2: merged {len(source_ids)} scenes → '{op.get('title')}'")

    async def _delete_scene(self, op: dict) -> None:
        """删除场景（归档）"""
        scene_id = op.get("scene_id")
        if not scene_id:
            return
        await self._db.execute(
            "UPDATE memory_scenes SET status = 'archived', updated_at = NOW() WHERE id = $1",
            uuid.UUID(scene_id),
        )
        logger.debug(f"L2: archived scene {scene_id}")
