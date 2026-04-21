"""
Session 级文件注册表。

跟踪当前会话中工具写入的 staging 文件。

key = "{domain}:{tool_name}:{timestamp}"
防止多个部门 Agent 都调 local_data 时互相覆盖。
"""
from __future__ import annotations

import time as _time
from dataclasses import dataclass, field

from .tool_output import FileRef


@dataclass
class SessionFileRegistry:
    """会话级文件注册表。

    生命周期与单次 ERPAgent.execute() 调用一致。
    冻结时序列化到 loop_snapshot，恢复时重建。
    """
    _files: dict[str, FileRef] = field(default_factory=dict)
    _access_counts: dict[str, int] = field(default_factory=dict)

    def register(self, domain: str, tool_name: str, file_ref: FileRef) -> None:
        """注册文件。key = domain:tool_name:timestamp，不会覆盖。"""
        key = f"{domain}:{tool_name}:{int(_time.time())}"
        self._files[key] = file_ref

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
            result.append({
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
            })
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
            registry._files[entry.get("key", "")] = ref
        return registry
