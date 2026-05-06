"""
file_list / file_search / file_info / file_edit 扩展（FileExecutor mixin）

将文件查询和编辑操作从 FileExecutor 中拆出。
FileExecutor 通过继承 FileQueryExtensionsMixin 获得这些能力。

假设宿主类（FileExecutor）提供：
- resolve_safe_path(path: str) -> Path
- _format_size(size: int) -> str
- _is_text_file(path: Path) -> bool
- _root: Path（workspace 根目录）

常量由本模块从 file_executor 导入。
"""

import mimetypes
import stat
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from loguru import logger


# 搜索时跳过的目录（从 file_executor 搬过来，搜索专用）
_SKIP_SEARCH_DIRS = frozenset({
    "staging", "__pycache__", "node_modules",
    ".git", ".svn", ".hg",
})


class FileQueryExtensionsMixin:
    """文件查询 + 编辑操作扩展"""

    async def file_list_entries(
        self,
        path: str = ".",
        show_hidden: bool = False,
    ) -> Dict[str, Any]:
        """列出目录内容（结构化数据）

        Returns:
            {"path": str, "dirs": [...], "files": [...], "error": str|None,
             "truncated": bool}
        """
        from services.file_executor import _BLOCKED_NAMES, _MAX_LIST_ENTRIES

        target = self.resolve_safe_path(path)

        if not target.exists():
            return {"path": path, "dirs": [], "files": [], "error": f"目录不存在: {path}", "truncated": False}
        if not target.is_dir():
            return {"path": path, "dirs": [], "files": [], "error": f"不是目录: {path}", "truncated": False}

        dirs: List[Dict[str, Any]] = []
        files: List[Dict[str, Any]] = []
        truncated = False
        try:
            count = 0
            for item in sorted(target.iterdir()):
                if not show_hidden and item.name.startswith("."):
                    continue
                if item.name in _BLOCKED_NAMES or item.name == "staging":
                    continue
                try:
                    st = item.stat()
                    entry = {
                        "name": item.name,
                        "size": st.st_size,
                        "modified": datetime.fromtimestamp(
                            st.st_mtime, tz=timezone.utc,
                        ).strftime("%Y-%m-%d %H:%M"),
                        "abs_path": str(item),
                    }
                    if item.is_dir():
                        dirs.append(entry)
                    else:
                        files.append(entry)
                except (PermissionError, OSError):
                    continue
                count += 1
                if count >= _MAX_LIST_ENTRIES:
                    truncated = True
                    break
        except PermissionError:
            return {"path": path, "dirs": [], "files": [], "error": f"无权限访问目录: {path}", "truncated": False}

        return {"path": path, "dirs": dirs, "files": files, "error": None, "truncated": truncated}

    async def file_list(
        self,
        path: str = ".",
        show_hidden: bool = False,
    ) -> str:
        """列出目录内容（格式化文本，供 API route 和无句柄场景使用）"""
        from services.file_executor import _MAX_LIST_ENTRIES

        data = await self.file_list_entries(path, show_hidden)

        if data["error"]:
            return data["error"]
        if not data["dirs"] and not data["files"]:
            return f"目录为空: {path}"

        total = len(data["dirs"]) + len(data["files"])
        lines = [f"目录: {path} | 共 {total} 项"]
        lines.append("─" * 60)
        for d in data["dirs"]:
            lines.append(f"  [目录] {d['name']}/\t\t{d['modified']}")
        for f in data["files"]:
            size_str = self._format_size(f["size"])
            lines.append(f"  [文件] {f['name']}\t{size_str}\t{f['modified']}")
            lines.append(f"         abs: {f['abs_path']}")

        if data["truncated"]:
            lines.append(f"\n已达显示上限（{_MAX_LIST_ENTRIES}项），部分条目未显示")

        return "\n".join(lines)

    async def file_search(
        self,
        keyword: str,
        path: str = ".",
        search_content: bool = False,
        file_pattern: Optional[str] = None,
    ) -> str:
        """搜索文件（按文件名或内容）"""
        from services.file_executor import (
            _BLOCKED_EXTENSIONS, _BLOCKED_NAMES,
            _MAX_READ_SIZE, _MAX_SEARCH_RESULTS,
        )

        target = self.resolve_safe_path(path)

        if not target.exists() or not target.is_dir():
            return f"目录不存在: {path}"

        results: List[str] = []
        keyword_lower = keyword.lower()

        for item in target.rglob(file_pattern or "*"):
            if len(results) >= _MAX_SEARCH_RESULTS:
                break
            if item.name in _BLOCKED_NAMES:
                continue
            if item.suffix.lower() in _BLOCKED_EXTENSIONS:
                continue
            rel_parts = item.relative_to(target).parts
            if any(p.startswith(".") or p in _SKIP_SEARCH_DIRS for p in rel_parts):
                continue

            rel_path = str(item.relative_to(self._root))

            if keyword_lower in item.name.lower():
                type_tag = "[目录]" if item.is_dir() else "[文件]"
                results.append(f"  {type_tag} {rel_path}")
                continue

            if search_content and item.is_file() and self._is_text_file(item):
                try:
                    if item.stat().st_size > _MAX_READ_SIZE:
                        continue
                    text = item.read_text(encoding="utf-8", errors="ignore")
                    for line_no, line in enumerate(text.splitlines(), 1):
                        if keyword_lower in line.lower():
                            preview = line.strip()[:100]
                            results.append(f"  [文件] {rel_path}:{line_no} | {preview}")
                            break
                except (PermissionError, OSError):
                    continue

        if not results:
            mode = "文件名+内容" if search_content else "文件名"
            return f"未找到匹配「{keyword}」的结果（{mode}搜索）"

        header = f"搜索「{keyword}」| 找到 {len(results)} 项"
        if len(results) >= _MAX_SEARCH_RESULTS:
            header += f"（已达上限 {_MAX_SEARCH_RESULTS}）"

        return f"{header}\n{'─' * 60}\n" + "\n".join(results)

    async def file_info(self, path: str) -> str:
        """获取文件/目录元信息"""
        target = self.resolve_safe_path(path)

        if not target.exists():
            return f"路径不存在: {path}"

        st = target.stat()
        info_lines = [
            f"路径: {path}",
            f"类型: {'目录' if target.is_dir() else '文件'}",
            f"大小: {self._format_size(st.st_size)}",
            f"修改时间: {datetime.fromtimestamp(st.st_mtime, tz=timezone.utc).strftime('%Y-%m-%d %H:%M:%S')}",
            f"创建时间: {datetime.fromtimestamp(st.st_ctime, tz=timezone.utc).strftime('%Y-%m-%d %H:%M:%S')}",
        ]

        if target.is_file():
            mime, _ = mimetypes.guess_type(str(target))
            info_lines.append(f"MIME: {mime or '未知'}")
            info_lines.append(f"可读文本: {'是' if self._is_text_file(target) else '否'}")

        if target.is_dir():
            try:
                count = sum(1 for _ in target.iterdir())
                info_lines.append(f"子项数量: {count}")
            except PermissionError:
                info_lines.append("子项数量: 无权限")

        mode = stat.filemode(st.st_mode)
        info_lines.append(f"权限: {mode}")

        return "\n".join(info_lines)

    async def file_edit(
        self,
        path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,
    ) -> str:
        """精确字符串替换（对标 Claude Code Edit 工具）"""
        from services.file_executor import FileOperationError

        target = self.resolve_safe_path(path)

        if not target.exists():
            raise FileOperationError(f"文件不存在: {path}")
        if not target.is_file():
            raise FileOperationError(f"不是文件: {path}")
        if not self._is_text_file(target):
            raise FileOperationError(f"二进制文件不支持编辑: {path}")
        if old_string == new_string:
            raise FileOperationError("old_string 和 new_string 相同，无需修改")

        try:
            content = target.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            try:
                content = target.read_text(encoding="gbk")
            except Exception:
                raise FileOperationError(f"无法解码文件 {path}")

        count = content.count(old_string)
        if count == 0:
            raise FileOperationError(
                f"未找到匹配内容。old_string 在文件中不存在。\n"
                f"请确认 old_string 与文件中的文本完全一致（包括缩进和空格）。"
            )

        if not replace_all and count > 1:
            raise FileOperationError(
                f"找到 {count} 处匹配，但 replace_all=false。\n"
                f"请提供更多上下文使 old_string 唯一，或设置 replace_all=true 替换全部。"
            )

        if replace_all:
            new_content = content.replace(old_string, new_string)
            replaced_count = count
        else:
            new_content = content.replace(old_string, new_string, 1)
            replaced_count = 1

        target.write_text(new_content, encoding="utf-8")

        logger.info(
            f"FileExecutor edit | path={path} | "
            f"replaced={replaced_count} | replace_all={replace_all}"
        )
        return f"已替换 {replaced_count} 处 | 文件: {path}"
