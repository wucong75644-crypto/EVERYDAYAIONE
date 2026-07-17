"""文件搜索命中单文件后的描述与多模态返回。"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from loguru import logger


class FileDescribeMixin:
    async def _describe_single_file(
        self,
        executor: Any,
        abs_path: str,
    ) -> Any:
        """描述单文件，图片直接返回多模态引用。"""
        from services.agent.agent_result import AgentResult
        from services.agent.file_path_cache import get_file_cache

        name = Path(abs_path).name
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
                    text=f"{name} ({size_text}) — 图片已注入视觉，可直接观察。",
                    image_url=cdn_url,
                )
            logger.warning(f"file_search image | no CDN URL for {abs_path}")
        if extension in self._ANALYZE_EXTENSIONS:
            hint = (
                f"数据文件需先 file_analyze('{relative_path}') "
                "治理后用 pd.read_parquet 读"
            )
        else:
            hint = (
                f"在 code_execute 中用相对路径 "
                f"'{relative_path}' 直接读取"
            )
        return AgentResult(
            summary="\n".join([f"{name} ({size_text})", "", hint]),
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
