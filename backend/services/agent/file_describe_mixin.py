"""文件搜索命中单文件后的描述与多模态返回。"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from loguru import logger


class FileDescribeMixin:
    def _file_reference_line(self, executor, path: Path) -> str:
        import json
        from services.file_resources import FileTargetResolver
        from services.agent.file_id import compute_fid
        from services.agent.file_path_cache import get_file_cache
        resolver = FileTargetResolver(self, executor, action="list")
        reference = resolver.reference(path)
        relative = str(path.relative_to(Path(executor.workspace_root)))
        cache = get_file_cache(self.conversation_id)
        cache.register(relative, workspace=str(path))
        line = (f"  [文件] [{compute_fid(self.org_id, relative)}] {json.dumps(relative, ensure_ascii=False)}"
                f"\n    resource_ref: {reference}")
        if path.suffix.lower() in self._ANALYZE_EXTENSIONS:
            from config.file_call_contract import file_analyze_arguments
            arguments = file_analyze_arguments(resource_ref=reference)
            line += f"\n    read_call (file_analyze): {json.dumps(arguments, ensure_ascii=False)}"
        return line

    async def _describe_single_file(
        self,
        executor: Any,
        abs_path: str,
    ) -> Any:
        """描述单文件，图片直接返回多模态引用。"""
        from services.agent.agent_result import AgentResult
        from services.agent.file_path_cache import get_file_cache

        name = Path(abs_path).name
        reference_line = self._file_reference_line(executor, Path(abs_path))
        size_text = self._fmt_size(os.path.getsize(abs_path))
        try:
            relative_path = str(
                Path(abs_path).relative_to(Path(executor.workspace_root))
            )
        except ValueError:
            relative_path = name
        cache = get_file_cache(self.conversation_id)
        cache.register(name, workspace=abs_path)
        cache.register(relative_path, workspace=abs_path)
        extension = (
            "." + name.rsplit(".", 1)[-1].lower()
            if "." in name else ""
        )
        if extension in {
            ".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp",
        }:
            from schemas.multimodal import FileReadResult

            cdn_url = (
                executor.get_cdn_url(relative_path)
                if hasattr(executor, "get_cdn_url") else ""
            )
            if cdn_url:
                return FileReadResult(
                    type="image",
                    text=f"{name} ({size_text}) — 图片已注入视觉，可直接观察。\n{reference_line}",
                    image_url=cdn_url,
                )
            logger.warning(f"file_search image | no CDN URL for {abs_path}")
        if extension in self._ANALYZE_EXTENSIONS:
            hint = (
                "数据文件的 file_analyze 参数直接复制上方 read_call；"
                "治理后用返回的 Parquet 路径读取。"
            )
        else:
            hint = (
                f"在 code_execute 中用相对路径 "
                f"'{relative_path}' 直接读取"
            )
        return AgentResult(
            summary="\n".join([f"{name} ({size_text})", reference_line, "", hint]),
            status="success",
        )

    @staticmethod
    def _fmt_size(size: int) -> str:
        if size < 1024:
            return f"{size} B"
        if size < 1024 * 1024:
            return f"{size / 1024:.1f} KB"
        if size < 1024 * 1024 * 1024:
            return f"{size / (1024 * 1024):.1f} MB"
        return f"{size / (1024 * 1024 * 1024):.1f} GB"
