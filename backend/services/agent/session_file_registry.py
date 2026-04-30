"""
Session 级文件注册表。

跟踪当前会话中工具写入的 staging 文件。

key = "{domain}:{tool_name}:{timestamp}"
防止多个部门 Agent 都调 local_data 时互相覆盖。

B2 扩展：schema_text / embedding / last_used 支持 schema 智能过滤注入。
"""
from __future__ import annotations

import time as _time
from dataclasses import dataclass, field
from typing import Optional

from .tool_output import FileRef

# LRU 上限：超过此数量淘汰 last_used 最旧的条目
_MAX_ENTRIES = 20


@dataclass
class SessionFileRegistry:
    """会话级文件注册表。

    生命周期与单次 ERPAgent.execute() 调用一致。
    冻结时序列化到 loop_snapshot，恢复时重建。

    B2 新增字段（平行 dict，因 FileRef 是 frozen 不可变）：
    - _schemas:    key → data_profile 完整文本
    - _embeddings: key → 预计算的 embedding 向量
    - _last_used:  key → 最近一次被引用的时间戳
    """
    _files: dict[str, FileRef] = field(default_factory=dict)
    _access_counts: dict[str, int] = field(default_factory=dict)
    _schemas: dict[str, str] = field(default_factory=dict)
    _embeddings: dict[str, list[float]] = field(default_factory=dict)
    _last_used: dict[str, float] = field(default_factory=dict)

    def register(
        self,
        domain: str,
        tool_name: str,
        file_ref: FileRef,
        schema_text: str = "",
    ) -> str:
        """注册文件。key = domain:tool_name:timestamp，不会覆盖。

        Args:
            schema_text: data_profile 完整文本（可选，后续供 schema 注入用）

        Returns:
            生成的 key（供外部 set_embedding / touch 使用）
        """
        now = _time.time()
        key = f"{domain}:{tool_name}:{int(now)}"
        self._files[key] = file_ref
        self._last_used[key] = now
        if schema_text:
            self._schemas[key] = schema_text
        self._enforce_lru()
        return key

    def _enforce_lru(self) -> None:
        """超过 _MAX_ENTRIES 时淘汰 last_used 最旧的条目。"""
        if len(self._files) <= _MAX_ENTRIES:
            return
        # 按 last_used 升序排列，淘汰最旧的
        sorted_keys = sorted(
            self._files.keys(),
            key=lambda k: self._last_used.get(k, 0.0),
        )
        evict_count = len(self._files) - _MAX_ENTRIES
        for key in sorted_keys[:evict_count]:
            self._files.pop(key, None)
            self._schemas.pop(key, None)
            self._embeddings.pop(key, None)
            self._last_used.pop(key, None)
            self._access_counts.pop(key, None)

    def touch(self, key: str) -> None:
        """更新 last_used 时间戳（标记为"最近使用"）。"""
        if key in self._files:
            self._last_used[key] = _time.time()

    def remove(self, key: str) -> None:
        """移除一个条目（供外部清理模块调用）。"""
        ref = self._files.pop(key, None)
        self._schemas.pop(key, None)
        self._embeddings.pop(key, None)
        self._last_used.pop(key, None)
        # _access_counts 以 ref.id 为 key（与 record_access 一致）
        if ref and ref.id:
            self._access_counts.pop(ref.id, None)

    def entries_count(self) -> int:
        """当前条目数。"""
        return len(self._files)

    def get_oldest_keys(self, n: int) -> list[str]:
        """按 last_used 升序返回最旧的 n 个 key。"""
        sorted_keys = sorted(
            self._files.keys(),
            key=lambda k: self._last_used.get(k, 0.0),
        )
        return sorted_keys[:n]

    def set_embedding(self, key: str, embedding: list[float]) -> None:
        """设置预计算的 embedding 向量（异步回写）。"""
        if key in self._files:
            self._embeddings[key] = embedding

    async def precompute_embedding(self, key: str, text: str) -> None:
        """异步预计算 schema embedding 并存入 registry。

        fire-and-forget 调用，失败静默降级（无 embedding → 后续过滤走 LLM 降级链）。
        """
        try:
            from services.knowledge_config import compute_embedding
            embedding = await compute_embedding(text[:2000])
            if embedding:
                self.set_embedding(key, embedding)
        except Exception as e:
            from loguru import logger
            logger.debug(f"Schema embedding precompute failed | key={key} | error={e}")

    def get_schema_entries(self) -> list[tuple[str, FileRef, str, Optional[list[float]]]]:
        """返回所有有 schema 的条目：[(key, file_ref, schema_text, embedding), ...]"""
        result = []
        for key, ref in self._files.items():
            schema = self._schemas.get(key)
            if schema:
                result.append((key, ref, schema, self._embeddings.get(key)))
        return result

    def get_recent_schema_entries(
        self, n: int = 3,
    ) -> list[tuple[str, FileRef, str]]:
        """返回最近 n 个有 schema 的条目（按 last_used 降序）。"""
        entries = [
            (key, ref, self._schemas[key])
            for key, ref in self._files.items()
            if key in self._schemas
        ]
        entries.sort(key=lambda e: self._last_used.get(e[0], 0.0), reverse=True)
        return entries[:n]

    def get_by_domain(self, domain: str) -> list[FileRef]:
        """按域查文件（一个域可能有多个文件）。"""
        return [
            ref for key, ref in self._files.items()
            if key.startswith(f"{domain}:")
        ]

    def get_latest(self) -> FileRef | None:
        """获取最新注册的文件。"""
        if not self._files:
            return None
        return list(self._files.values())[-1]

    def list_all(self) -> list[tuple[str, FileRef]]:
        """列出所有文件（key, FileRef）。"""
        return list(self._files.items())

    def get_by_id(self, file_id: str) -> FileRef | None:
        """按 FileRef.id 查找（v6 新增）。"""
        if not file_id:
            return None
        for ref in self._files.values():
            if ref.id == file_id:
                return ref
        return None

    def record_access(self, file_id: str) -> None:
        """记录文件被读取一次（外部计数器，因 FileRef 是 frozen 不可变）。"""
        if file_id:
            self._access_counts[file_id] = self._access_counts.get(file_id, 0) + 1

    def get_access_count(self, file_id: str) -> int:
        """查询文件访问次数。"""
        return self._access_counts.get(file_id, 0)

    def to_prompt_text(self) -> str:
        """生成文件清单文本。"""
        if not self._files:
            return "当前会话无暂存文件。"
        lines = ["当前会话暂存文件："]
        for key, ref in self._files.items():
            domain = key.split(":")[0]
            col_names = (
                [c.name for c in ref.columns[:8]] if ref.columns else []
            )
            lines.append(
                f"  - {ref.filename}（来自 {domain}，"
                f"{ref.row_count}行，列: {', '.join(col_names)}）"
            )
        return "\n".join(lines)

    # ----------------------------------------------------------
    # 序列化 / 反序列化（供 pending_interaction 冻结恢复）
    # ----------------------------------------------------------

    def to_snapshot(self) -> list[dict]:
        """序列化为可 JSON 的列表（写入 loop_snapshot）。"""
        result = []
        for key, ref in self._files.items():
            entry: dict = {
                "key": key,
                "file_ref": {
                    "path": ref.path,
                    "filename": ref.filename,
                    "format": ref.format,
                    "row_count": ref.row_count,
                    "size_bytes": ref.size_bytes,
                    "columns": [
                        {"name": c.name, "dtype": c.dtype, "label": c.label}
                        for c in ref.columns
                    ],
                    "preview": ref.preview,
                    "created_at": ref.created_at,
                    # v6 新增
                    "id": ref.id,
                    "mime_type": ref.mime_type,
                    "created_by": ref.created_by,
                    "ttl_seconds": ref.ttl_seconds,
                    "derived_from": list(ref.derived_from),
                },
            }
            # B2 新增（向后兼容：老版本反序列化时 .get 拿不到直接跳过）
            if key in self._schemas:
                entry["schema_text"] = self._schemas[key]
            if key in self._embeddings:
                entry["embedding"] = self._embeddings[key]
            entry["last_used"] = self._last_used.get(key, ref.created_at)
            result.append(entry)
        return result

    @classmethod
    def from_snapshot(cls, data: list[dict]) -> SessionFileRegistry:
        """从 loop_snapshot 反序列化重建。

        兼容老格式：data 为空列表时返回空 Registry。
        """
        from .tool_output import ColumnMeta

        registry = cls()
        for entry in data or []:
            fr_data = entry.get("file_ref", {})
            columns = [
                ColumnMeta(
                    name=c.get("name", ""),
                    dtype=c.get("dtype", "text"),
                    label=c.get("label", ""),
                )
                for c in fr_data.get("columns", [])
            ]
            # path 统一为绝对路径（v7 协议）；历史数据可能是相对路径，
            # is_valid() 会返回 False，不影响正确性。
            ref = FileRef(
                path=fr_data.get("path", ""),
                filename=fr_data.get("filename", ""),
                format=fr_data.get("format", "parquet"),
                row_count=fr_data.get("row_count", 0),
                size_bytes=fr_data.get("size_bytes", 0),
                columns=columns,
                preview=fr_data.get("preview", ""),
                created_at=fr_data.get("created_at", 0.0),
                # v6 新增（.get 兼容旧数据）
                id=fr_data.get("id", ""),
                mime_type=fr_data.get("mime_type", ""),
                created_by=fr_data.get("created_by", ""),
                ttl_seconds=fr_data.get("ttl_seconds", 86400),
                derived_from=tuple(fr_data.get("derived_from", [])),
            )
            key = entry.get("key", "")
            registry._files[key] = ref
            # B2 新增（.get 兼容旧 snapshot 缺这些字段的情况）
            schema_text = entry.get("schema_text")
            if schema_text:
                registry._schemas[key] = schema_text
            embedding = entry.get("embedding")
            if embedding:
                registry._embeddings[key] = embedding
            registry._last_used[key] = entry.get(
                "last_used", fr_data.get("created_at", 0.0),
            )
        return registry

    def merge_into(self, other: SessionFileRegistry) -> list[str]:
        """将当前 registry 的条目合并到 other（用于跨消息累积）。

        新增条目写入 other，已存在的条目跳过（不覆盖）。
        合并后执行 LRU 淘汰。

        Returns:
            新增到 other 中的 key 列表
        """
        added_keys: list[str] = []
        for key, ref in self._files.items():
            if key not in other._files:
                other._files[key] = ref
                if key in self._schemas:
                    other._schemas[key] = self._schemas[key]
                if key in self._embeddings:
                    other._embeddings[key] = self._embeddings[key]
                other._last_used[key] = self._last_used.get(key, 0.0)
                added_keys.append(key)
        other._enforce_lru()
        return added_keys


# ============================================================
# 对话级 registry 缓存（进程内，跨消息持久化）
# 进程重启后丢失 → 可接受（Agent 调 data_query 重查 schema）
# LRU 淘汰：超过 _MAX_CONVERSATIONS 时删最久未访问的对话
# ============================================================

# 对话级缓存上限。20 文件/对话 × 8KB embedding/文件 ≈ 160KB/对话
# 200 个对话 ≈ 32MB 内存上限，足够长运行服务
_MAX_CONVERSATIONS = 200

_conversation_registries: dict[str, SessionFileRegistry] = {}
_conversation_access_time: dict[str, float] = {}


def get_conversation_registry(conversation_id: str) -> SessionFileRegistry:
    """获取对话级 registry（不存在则创建空的）。"""
    _conversation_access_time[conversation_id] = _time.time()
    if conversation_id not in _conversation_registries:
        _conversation_registries[conversation_id] = SessionFileRegistry()
        _enforce_conversation_lru()
    return _conversation_registries[conversation_id]


def save_conversation_registry(
    conversation_id: str, registry: SessionFileRegistry,
) -> list[str]:
    """将工具循环中的 registry 合并到对话级缓存。

    Returns:
        新增到对话级缓存中的 key 列表
    """
    conv_reg = get_conversation_registry(conversation_id)
    return registry.merge_into(conv_reg)


def remove_conversation_registry(conversation_id: str) -> None:
    """对话结束时主动释放缓存。"""
    _conversation_registries.pop(conversation_id, None)
    _conversation_access_time.pop(conversation_id, None)


def _enforce_conversation_lru() -> None:
    """超过 _MAX_CONVERSATIONS 时淘汰最久未访问的对话。"""
    if len(_conversation_registries) <= _MAX_CONVERSATIONS:
        return
    sorted_ids = sorted(
        _conversation_registries.keys(),
        key=lambda cid: _conversation_access_time.get(cid, 0.0),
    )
    evict_count = len(_conversation_registries) - _MAX_CONVERSATIONS
    for cid in sorted_ids[:evict_count]:
        _conversation_registries.pop(cid, None)
        _conversation_access_time.pop(cid, None)
