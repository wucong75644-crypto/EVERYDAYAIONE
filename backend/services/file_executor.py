"""
文件操作执行器

提供安全的本地文件系统访问能力（读取/写入/列目录/搜索/元信息）。
所有操作限制在 workspace 目录内，防止路径穿越。
"""

import hashlib
import mimetypes
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from loguru import logger

from services.file_query_extensions import FileQueryExtensionsMixin
from services.file_read_extensions import (
    DOCX_EXTENSIONS,
    FileReadExtensionsMixin,
    FileReadResult,
    IMAGE_EXTENSIONS,
    PDF_EXTENSIONS,
    PPTX_EXTENSIONS,
)
from services.file_write_extensions import FileWriteExtensionsMixin


class FileOperationError(Exception):
    """文件操作业务校验失败（文件不存在/格式错误/页码无效等）。

    与 PermissionError 区分：PermissionError 是权限问题（不可重试），
    FileOperationError 是参数/路径问题（LLM 可换参数重试）。
    """
    pass


# 禁止访问的文件/目录名（安全敏感）
_BLOCKED_NAMES = frozenset({
    ".env", ".env.local", ".env.production",
    ".git", ".gitignore",
    "credentials.json", "service_account.json",
    "id_rsa", "id_ed25519", "authorized_keys",
    ".ssh", ".gnupg", ".aws", ".docker",
})

# 禁止访问的文件扩展名
_BLOCKED_EXTENSIONS = frozenset({
    ".pem", ".key", ".p12", ".pfx", ".jks",
})

# 文本文件扩展名（用于判断是否可读取内容）
_TEXT_EXTENSIONS = frozenset({
    ".txt", ".md", ".csv", ".json", ".yaml", ".yml", ".toml",
    ".xml", ".html", ".htm", ".css", ".js", ".ts", ".tsx", ".jsx",
    ".py", ".java", ".go", ".rs", ".c", ".cpp", ".h", ".hpp",
    ".sh", ".bash", ".zsh", ".fish",
    ".sql", ".graphql", ".proto",
    ".ini", ".cfg", ".conf", ".env.example",
    ".log", ".tsv", ".rst", ".tex", ".org", ".jsonl",
    ".dockerfile", ".gitignore", ".editorconfig",
    ".vue", ".svelte", ".astro",
})

# ── file_read 三级防线常量（对齐 Claude Code Read 工具） ──

# L1: 无分页读取时的文件大小上限
# 对齐 Claude Code MAX_OUTPUT_SIZE = 0.25 * 1024 * 1024
_MAX_FILE_READ_BYTES = 256 * 1024  # 256KB

# L2: 单次读取行数硬上限
# 对齐 Claude Code MAX_LINES_TO_READ = 2000
_MAX_READ_LINES = 2000

# L3: 输出 token 估算上限
# 对齐 Claude Code DEFAULT_MAX_OUTPUT_TOKENS = 25000
_MAX_OUTPUT_TOKENS = 25000

# token 估算比例
# 对齐 Claude Code bytesPerTokenForFileType()
_BYTES_PER_TOKEN = 4          # 普通文件：4 字节 ≈ 1 token
_JSON_BYTES_PER_TOKEN = 2     # JSON/JSONL：密集标点，2 字节 ≈ 1 token

# 文件读取硬上限（10MB，防 OOM；file_search 内容扫描也用）
_MAX_READ_SIZE = 10 * 1024 * 1024


# 文件写入大小上限（5MB）
_MAX_WRITE_SIZE = 5 * 1024 * 1024

# 目录列出条目上限
_MAX_LIST_ENTRIES = 200

# 搜索结果上限
_MAX_SEARCH_RESULTS = 100


class FileExecutor(FileReadExtensionsMixin, FileQueryExtensionsMixin, FileWriteExtensionsMixin):
    """安全文件操作执行器

    所有路径操作都限制在 workspace_root/{tenant}/{user_id}/ 内。
    支持 ossfs 挂载目录，自动生成 CDN URL 供前端下载。
    """

    def __init__(
        self,
        workspace_root: str,
        user_id: str = "",
        org_id: Optional[str] = None,
    ) -> None:
        base = Path(workspace_root).resolve()

        # 按用户/企业隔离目录
        if org_id:
            self._root = base / "org" / org_id / user_id
        elif user_id:
            user_hash = hashlib.md5(user_id.encode()).hexdigest()[:8]
            self._root = base / "personal" / user_hash
        else:
            self._root = base

        self._root.mkdir(parents=True, exist_ok=True)

        # workspace 基础路径（用于计算 OSS object_key）
        self._workspace_base = base
        logger.info(f"FileExecutor initialized | root={self._root}")

    @property
    def workspace_root(self) -> str:
        return str(self._root)

    def get_cdn_url(self, relative_path: str) -> Optional[str]:
        """获取文件的 CDN 下载 URL

        Args:
            relative_path: 相对于用户目录的路径

        Returns:
            CDN URL 或 None（未配置 CDN）
        """
        from core.config import get_settings
        from urllib.parse import quote

        settings = get_settings()
        if not settings.oss_cdn_domain:
            return None

        target = self.resolve_safe_path(relative_path)
        # 计算相对于 ossfs 挂载根的路径 = OSS object_key
        try:
            object_key = str(target.relative_to(self._workspace_base)).replace("\\", "/")
            encoded_key = quote(object_key, safe="/")
            return f"https://{settings.oss_cdn_domain}/workspace/{encoded_key}"
        except ValueError:
            return None

    # ========================================
    # 路径安全校验
    # ========================================

    def resolve_safe_path(self, path_input: str) -> Path:
        """解析并验证路径安全性（公有方法，供外部调用）

        支持两种输入格式：
        1. 绝对路径：已在 workspace 内的绝对路径 → 直接使用
        2. 相对路径：相对于 workspace 根目录 → 拼接 _root

        注：文件句柄（F1, F2...）由 ToolExecutor 在调度层统一翻译为绝对路径，
        本方法不处理句柄。

        Raises:
            PermissionError: 路径越界、符号链接、或访问被禁止的文件
        """
        path_str = path_input.strip()

        # ① 绝对路径（已在 workspace 内）
        if Path(path_str).is_absolute():
            raw_path = Path(path_str)
            target = raw_path.resolve()
            try:
                target.relative_to(self._root)
            except ValueError:
                raise PermissionError("路径越界：不允许访问 workspace 外的文件")
        # ② 相对路径（原逻辑）
        else:
            cleaned = path_str.lstrip("/").lstrip("\\")
            raw_path = self._root / cleaned
            target = raw_path.resolve()
            try:
                target.relative_to(self._root)
            except ValueError:
                raise PermissionError("路径越界：不允许访问 workspace 外的文件")

        # 符号链接检查（用未 resolve 的原始路径，防止 symlink 攻击）
        if raw_path.is_symlink():
            raise PermissionError("安全限制：不允许访问符号链接")

        # 公共安全检查
        rel_parts = target.relative_to(self._root)

        if target.name in _BLOCKED_NAMES:
            raise PermissionError(f"安全限制：不允许访问 {target.name}")

        if target.suffix.lower() in _BLOCKED_EXTENSIONS:
            raise PermissionError(f"安全限制：不允许访问 {target.suffix} 类型文件")

        for part in rel_parts.parts:
            if part in _BLOCKED_NAMES:
                raise PermissionError(f"安全限制：不允许访问包含 {part} 的路径")

        # staging 目录由 data_query/code_execute 内部管理，file 工具不可直接访问
        if rel_parts.parts and rel_parts.parts[0] == "staging":
            raise PermissionError("安全限制：staging 目录由系统管理，不可直接访问")

        return target

    def generate_unique_filename(self, filename: str) -> str:
        """生成唯一文件名（防止覆盖）

        Args:
            filename: 原始文件名

        Returns:
            唯一文件名（如 report_a1b2c3.csv）
        """
        stem = Path(filename).stem
        suffix = Path(filename).suffix
        short_id = uuid.uuid4().hex[:6]
        return f"{stem}_{short_id}{suffix}"

    def _is_text_file(self, path: Path) -> bool:
        """判断文件是否为文本文件"""
        if path.suffix.lower() in _TEXT_EXTENSIONS:
            return True
        mime, _ = mimetypes.guess_type(str(path))
        if mime and mime.startswith("text/"):
            return True
        if path.name.lower() in {"dockerfile", "makefile", "rakefile", "gemfile"}:
            return True
        return False

    # ========================================
    # 文件操作
    # ========================================

    async def file_read(
        self,
        path: str,
        offset: int = 1,
        limit: int | None = None,
        encoding: str = "utf-8",
        pages: str | None = None,
    ) -> Union[str, "FileReadResult"]:
        """读取文件内容（对齐 Claude Code Read 工具，支持 PDF + 图片）

        三级防线（文本文件）：
        - L1: limit=None 时，文件 > 256KB 拒绝（防盲读大文件）
        - L2: 行数硬上限 2000（防单次读太多）
        - L3: token 估算 > 25000 拒绝（最终兜底）

        PDF 文件：PyPDF2 按页提取文本，pages 参数指定页范围
        图片文件：返回 FileReadResult(type="image")，供上层注入多模态消息

        Args:
            path:     文件相对路径（相对于 workspace）
            offset:   起始行号（1-based，默认1=第一行）
            limit:    读取行数（None=读整个文件，触发 L1 字节检查）
            encoding: 编码（默认 utf-8，自动 fallback GBK）
            pages:    PDF 页码范围（如 '3'、'1-5'、'3,7,10'），仅 PDF 文件有效
        """
        target = self.resolve_safe_path(path)

        # ── 基础校验 ──
        if not target.exists():
            raise FileOperationError(f"文件不存在: {path}")
        if not target.is_file():
            raise FileOperationError(f"不是文件: {path}")

        size = target.stat().st_size
        ext = target.suffix.lower()

        # ── DOCX 直读 ──
        if ext in DOCX_EXTENSIONS:
            return await self._read_docx(
                path, target, size,
                max_read_size=_MAX_READ_SIZE,
                bytes_per_token=_BYTES_PER_TOKEN,
                max_output_tokens=_MAX_OUTPUT_TOKENS,
            )

        # ── PPTX 直读 ──
        if ext in PPTX_EXTENSIONS:
            return await self._read_pptx(
                path, target, size,
                max_read_size=_MAX_READ_SIZE,
                bytes_per_token=_BYTES_PER_TOKEN,
                max_output_tokens=_MAX_OUTPUT_TOKENS,
            )

        # ── PDF 直读 ──
        if ext in PDF_EXTENSIONS:
            return await self._read_pdf(
                path, target, size, pages,
                max_read_size=_MAX_READ_SIZE,
                bytes_per_token=_BYTES_PER_TOKEN,
                max_output_tokens=_MAX_OUTPUT_TOKENS,
            )

        # ── 图片直读（返回 FileReadResult） ──
        if ext in IMAGE_EXTENSIONS:
            return await self._read_image(path, target, size)

        # ── L1: 字节预检（仅无分页时，文本文件） ──
        # 对齐 Claude Code: limit === undefined ? maxSizeBytes : undefined
        if limit is None and size > _MAX_FILE_READ_BYTES:
            return (
                f"文件过大（{self._format_size(size)}），"
                f"超过 {self._format_size(_MAX_FILE_READ_BYTES)} 上限。"
                "请用 offset/limit 分页读取特定部分，"
                "或用 code_execute 处理整个文件。"
            )

        # 超大文件硬上限（防 OOM，无论是否分页）
        if size > _MAX_READ_SIZE:
            return (
                f"文件过大（{self._format_size(size)}），"
                f"超过 {self._format_size(_MAX_READ_SIZE)} 硬上限。"
                "建议使用 code_execute 处理。"
            )

        # ── 二进制检查（PDF/图片已在上面处理，这里只拦其他二进制） ──
        if not self._is_text_file(target):
            _data_exts = {".xlsx", ".xls", ".csv", ".tsv", ".parquet"}
            if ext in _data_exts:
                return f"数据文件请用 data_query 读取: data_query(file=\"{path}\")"
            return (
                f"二进制文件: {path}（{self._format_size(size)}）\n"
                f"类型: {mimetypes.guess_type(str(target))[0] or '未知'}\n"
                "请用 code_execute 处理。"
            )

        # ── 读文件 ──
        try:
            content = target.read_text(encoding=encoding)
        except UnicodeDecodeError:
            try:
                content = target.read_text(encoding="gbk")
            except Exception:
                raise FileOperationError(f"无法解码文件 {path}，请指定正确的编码")

        # ── BOM 剥离（对齐 Claude Code readFileInRange） ──
        if content and content[0] == "\ufeff":
            content = content[1:]

        lines = content.splitlines()
        total_lines = len(lines)

        # ── 空文件（对齐 Claude Code） ──
        if total_lines == 0:
            return "文件存在但内容为空。"

        # ── offset 转换：1-indexed → 0-indexed ──
        # 对齐 Claude Code: offset === 0 ? 0 : offset - 1
        line_offset = 0 if offset <= 0 else offset - 1

        # ── offset 超界（对齐 Claude Code） ──
        if line_offset >= total_lines:
            return (
                f"文件只有 {total_lines} 行，"
                f"起始行号 {offset} 超出范围。"
            )

        # ── L2: 行数切片（硬上限 2000 行） ──
        effective_limit = min(limit or _MAX_READ_LINES, _MAX_READ_LINES)
        selected = lines[line_offset: line_offset + effective_limit]

        # ── 格式化（cat -n 格式，行号 1-indexed） ──
        result_lines = []
        for i, line in enumerate(selected, start=line_offset + 1):
            result_lines.append(f"{i:>5}\t{line}")
        output = "\n".join(result_lines)

        # ── L3: token 估算（对齐 Claude Code validateContentTokens） ──
        ext = target.suffix.lower().lstrip(".")
        bpt = _JSON_BYTES_PER_TOKEN if ext in ("json", "jsonl") else _BYTES_PER_TOKEN
        estimated_tokens = len(output.encode("utf-8")) / bpt

        if estimated_tokens > _MAX_OUTPUT_TOKENS:
            return (
                f"文件内容（约 {int(estimated_tokens)} tokens）"
                f"超过上限（{_MAX_OUTPUT_TOKENS} tokens）。"
                "请用 offset/limit 读取特定部分，"
                "或用 code_execute 处理。"
            )

        # ── header ──
        end_line = line_offset + len(selected)
        header = f"文件: {path} | 共 {total_lines} 行"
        if line_offset > 0 or end_line < total_lines:
            header += f" | 显示: {line_offset + 1}-{end_line}"

        return f"{header}\n{'─' * 60}\n{output}"

    # ========================================
    # 辅助方法
    # ========================================

    @staticmethod
    def _format_size(size: int) -> str:
        """格式化文件大小"""
        if size < 1024:
            return f"{size}B"
        if size < 1024 * 1024:
            return f"{size / 1024:.1f}KB"
        if size < 1024 * 1024 * 1024:
            return f"{size / 1024 / 1024:.1f}MB"
        return f"{size / 1024 / 1024 / 1024:.1f}GB"
