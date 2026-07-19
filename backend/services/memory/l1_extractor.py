"""
L1 原子事实提取器

从闭合对话窗口生成并验证无数据库副作用的通用候选。
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from typing import Any

from loguru import logger

from .config import get_memory_config
from .candidate_validator import validate_memory_candidate
from .contracts import MemoryCandidate, parse_memory_candidate
from .prompts.l1_extraction import (
    EXTRACT_MEMORIES_SYSTEM_PROMPT,
    format_extraction_prompt,
)

# ============================================================
# Types
# ============================================================

_LEGACY_TYPE_BY_KIND = {
    "user_profile": "persona",
    "preference": "persona",
    "instruction": "instruction",
    "decision": "episodic",
    "reusable_context": "persona",
    "problem_solution": "episodic",
    "tracked_plan": "episodic",
}


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
class L1ProposalResult:
    """未写库的通用 Flush 提议；解析失败与 NO_MEMORY 明确区分。"""

    success: bool
    decision: str = "NO_MEMORY"
    scenes: list[SceneSegment] = field(default_factory=list)


# ============================================================
# Core Extractor
# ============================================================

class L1Extractor:
    """L1 原子事实提取器"""

    def __init__(self, db_pool=None):
        self._db = db_pool
        self._cfg = get_memory_config()

    async def propose(
        self,
        messages: list[dict],
        *,
        background: list[dict] | None = None,
        previous_scene_name: str = "",
    ) -> L1ProposalResult:
        """只生成并验证候选，不产生数据库副作用。"""
        try:
            raw = await self._call_llm_extraction(
                messages,
                background or [],
                previous_scene_name,
            )
        except Exception as e:
            logger.error(f"L1: LLM extraction failed: {e}")
            return L1ProposalResult(success=False)
        valid, decision, scenes = _parse_extraction_decision(raw, messages)
        return L1ProposalResult(
            success=valid,
            decision=decision,
            scenes=scenes,
        )

    async def _call_llm_extraction(
        self,
        new_messages: list[dict],
        background: list[dict],
        previous_scene_name: str,
    ) -> str:
        """调用模型并返回原始 Flush 输出。"""
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

        return result


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


def _parse_extraction_result(
    raw: str,
    source_messages: list[dict[str, Any]],
) -> list[SceneSegment]:
    """严格解析并验证通用候选；任一格式错误时整批拒绝。"""
    valid, _, scenes = _parse_extraction_decision(raw, source_messages)
    return scenes if valid else []


def _parse_extraction_decision(
    raw: str,
    source_messages: list[dict[str, Any]],
) -> tuple[bool, str, list[SceneSegment]]:
    """返回协议有效性、决策和候选，供 Session cursor 判断是否推进。"""
    try:
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            return False, "INVALID", []
        decision = payload.get("decision")
        if decision == "NO_MEMORY":
            return True, "NO_MEMORY", []
        items = payload.get("items")
        if decision != "CANDIDATES" or not isinstance(items, list) or not items:
            return False, "INVALID", []

        candidates = [parse_memory_candidate(item) for item in items]
        if any(
            not validate_memory_candidate(candidate, source_messages).accepted
            for candidate in candidates
        ):
            logger.warning("L1: candidate batch rejected by evidence validator")
            return False, "INVALID", []

        atoms = [_candidate_to_atom(candidate) for candidate in candidates]
        if any(atom is None for atom in atoms):
            logger.warning("L1: candidate batch contains unsupported legacy kind")
            return False, "INVALID", []
        return True, "CANDIDATES", [SceneSegment(
            scene_name="通用会话记忆",
            message_ids=[
                str(message.get("id"))
                for message in source_messages
                if message.get("id")
            ],
            memories=[atom for atom in atoms if atom is not None],
        )]
    except (json.JSONDecodeError, TypeError, ValueError) as e:
        logger.warning(f"L1: failed to parse extraction result: {e}")
        return False, "INVALID", []


# ============================================================
# 工具函数
# ============================================================

def _candidate_to_atom(candidate: MemoryCandidate) -> MemoryAtom | None:
    """将已验证通用候选映射到迁移期旧表协议。"""
    legacy_type = _LEGACY_TYPE_BY_KIND.get(candidate.kind)
    if legacy_type is None:
        return None
    return MemoryAtom(
        content=candidate.claim,
        type=legacy_type,
        priority=80 if candidate.scope == "long_term" else 60,
        source_message_ids=[
            evidence.message_id for evidence in candidate.evidence
        ],
        metadata={
            "kind": candidate.kind,
            "scope": candidate.scope,
            "explicitness": candidate.explicitness,
            "evidence": [
                {
                    "message_id": evidence.message_id,
                    "quote": evidence.quote,
                }
                for evidence in candidate.evidence
            ],
            "valid_from": (
                candidate.valid_from.isoformat()
                if candidate.valid_from is not None else None
            ),
            "valid_until": (
                candidate.valid_until.isoformat()
                if candidate.valid_until is not None else None
            ),
            "attributes": dict(candidate.attributes),
        },
        scene_name="",
    )
