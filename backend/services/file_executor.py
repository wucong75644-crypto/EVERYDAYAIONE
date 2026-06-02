"""
文件操作执行器

提供安全的本地文件系统访问能力（读取/写入/列目录/搜索/元信息）。
所有操作限制在 workspace 目录内，防止路径穿越。
"""

import hashlib
import mimetypes
import uuid
from pathlib import Path
from typing import Optional

from loguru import logger

from services.file_query_extensions import FileQueryExtensionsMixin
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

# 文件读取硬上限（10MB，防 OOM；file_search 内容扫描用）
_MAX_READ_SIZE = 10 * 1024 * 1024


# 文件写入大小上限（5MB）
_MAX_WRITE_SIZE = 5 * 1024 * 1024

# 目录列出条目上限
_MAX_LIST_ENTRIES = 200

# 搜索结果上限
_MAX_SEARCH_RESULTS = 100


class FileExecutor(FileQueryExtensionsMixin, FileWriteExtensionsMixin):
    """安全文件操作执行器

    所有路径操作都限制在 workspace_root/{tenant}/{user_id}/ 内。
    支持 NAS 挂载目录，文件变动显式同步到 OSS 生成 CDN URL 供前端下载。
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

    @staticmethod
    def extract_user_relative_path(
        file_path: Path,
        ws_base: Path,
        user_id: str,
        org_id: Optional[str] = None,
    ) -> str:
        """从绝对路径计算相对于用户 workspace 根的路径。

        Args:
            file_path: 文件绝对路径
            ws_base: workspace 基础目录（resolve 后）
            user_id: 用户 ID
            org_id: 企业 ID

        Returns:
            相对路径字符串（如 "staging/output.xlsx"）
        """
        if org_id:
            user_root = ws_base / "org" / org_id / user_id
        elif user_id:
            user_hash = hashlib.md5(user_id.encode()).hexdigest()[:8]
            user_root = ws_base / "personal" / user_hash
        else:
            user_root = ws_base
        resolved = file_path.resolve()
        return str(resolved.relative_to(user_root))

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
        # 计算相对于 NAS workspace 根的路径 = OSS object_key
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
