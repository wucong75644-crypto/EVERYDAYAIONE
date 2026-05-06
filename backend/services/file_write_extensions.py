"""
file_write / file_delete / file_mkdir / file_rename / file_move 扩展（FileExecutor mixin）

将文件写入和管理操作从 FileExecutor 中拆出。
FileExecutor 通过继承 FileWriteExtensionsMixin 获得这些能力。

假设宿主类（FileExecutor）提供：
- resolve_safe_path(path: str) -> Path
- _format_size(size: int) -> str
- _root: Path（workspace 根目录）
- _MAX_WRITE_SIZE: int
"""

from pathlib import Path

from loguru import logger


class FileWriteExtensionsMixin:
    """文件写入 + 管理操作扩展"""

    async def file_write(
        self,
        path: str,
        content: str,
        mode: str = "overwrite",
        encoding: str = "utf-8",
        max_write_size: int = 5 * 1024 * 1024,
    ) -> str:
        """写入文件"""
        target = self.resolve_safe_path(path)

        if len(content.encode(encoding)) > max_write_size:
            return f"内容过大，超过 {max_write_size / 1024 / 1024:.0f}MB 上限"

        if mode == "create_only" and target.exists():
            return f"文件已存在: {path}（mode=create_only 不覆盖）"

        target.parent.mkdir(parents=True, exist_ok=True)
        existed = target.exists()

        if mode == "append":
            with open(target, "a", encoding=encoding) as f:
                f.write(content)
            action = "追加" if existed else "创建"
        else:
            target.write_text(content, encoding=encoding)
            action = "覆盖写入" if existed else "创建"

        size = target.stat().st_size
        logger.info(f"FileExecutor write | path={path} | mode={mode} | size={size}")
        return f"已{action}: {path}（{self._format_size(size)}）"

    async def file_delete(self, path: str) -> str:
        """删除文件或空目录"""
        target = self.resolve_safe_path(path)

        if not target.exists():
            return f"路径不存在: {path}"

        if target.is_file():
            target.unlink()
            logger.info(f"FileExecutor delete file | path={path}")
            return f"已删除文件: {path}"

        if target.is_dir():
            children = list(target.iterdir())
            if children:
                return f"目录不为空（{len(children)} 项），请先清空内容: {path}"
            target.rmdir()
            logger.info(f"FileExecutor delete dir | path={path}")
            return f"已删除目录: {path}"

        return f"无法删除: {path}"

    async def file_mkdir(self, path: str) -> str:
        """创建目录（含中间路径）"""
        target = self.resolve_safe_path(path)

        if target.exists():
            if target.is_dir():
                return f"目录已存在: {path}"
            return f"同名文件已存在，无法创建目录: {path}"

        target.mkdir(parents=True, exist_ok=True)
        logger.info(f"FileExecutor mkdir | path={path}")
        return f"已创建目录: {path}"

    async def file_rename(self, old_path: str, new_path: str) -> str:
        """重命名文件或目录（同目录下改名，不允许跨目录）"""
        old_target = self.resolve_safe_path(old_path)
        new_target = self.resolve_safe_path(new_path)

        if not old_target.exists():
            return f"源路径不存在: {old_path}"

        if old_target.parent != new_target.parent:
            return f"重命名不允许跨目录，请使用移动功能"

        if new_target.exists():
            return f"目标已存在: {new_path}"

        old_target.rename(new_target)
        logger.info(f"FileExecutor rename | {old_path} → {new_path}")
        return f"已重命名: {old_path} → {new_path}"

    async def file_move(self, src_path: str, dest_dir: str) -> str:
        """移动文件到目标目录"""
        src_target = self.resolve_safe_path(src_path)
        dest_target = self.resolve_safe_path(dest_dir)

        if not src_target.exists():
            return f"源路径不存在: {src_path}"

        if not dest_target.exists() or not dest_target.is_dir():
            return f"目标目录不存在: {dest_dir}"

        new_target = dest_target / src_target.name

        if new_target.exists():
            return f"目标位置已有同名文件: {dest_dir}/{src_target.name}"

        src_target.rename(new_target)
        new_rel = str(new_target.relative_to(self._root))
        logger.info(f"FileExecutor move | {src_path} → {new_rel}")
        return f"已移动: {src_path} → {new_rel}"
