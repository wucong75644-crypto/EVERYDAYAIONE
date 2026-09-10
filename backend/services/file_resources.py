"""Scoped file identities. Names discover candidates; paths never become aliases.

References are signed locators, not grants. Every consumer must use the current
workspace guard and resource scope. Neither URLs nor conversation caches own a file.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import stat
import tempfile
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path


class FileTargetError(ValueError):
    def __init__(self, code: str, message: str, candidates=()):
        self.code = code
        self.scope = None
        self.recovery = "search_again" if code in {"RESOURCE_CHANGED", "RESOURCE_REFERENCE_INVALID", "RESOURCE_NOT_FOUND"} else "select_resource"
        self.candidates = tuple(candidates)
        detail = "\n".join(f"- {value}" for value in self.candidates)
        super().__init__(f"{code}: {message}" + (f"\n{detail}" if detail else ""))


def file_version(path: Path) -> tuple[int, ...]:
    info = path.stat()
    if not stat.S_ISREG(info.st_mode):
        raise FileTargetError("RESOURCE_NOT_FILE", "目标不是普通文件")
    return (info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def content_digest(path: str | Path, check=lambda: None) -> str:
    """Read the entire source; errors must never produce a cache identity."""
    path = Path(path)
    before = file_version(path)
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            check()
            digest.update(chunk)
    check()
    if before != file_version(path):
        raise FileTargetError("RESOURCE_CHANGED", "文件读取期间发生变化，请重新选择")
    return digest.hexdigest()


def workspace_entries(files, path=".", *, check=lambda: None):
    """The shared complete traversal underlying discovery and name resolution."""
    from services.file_executor import _BLOCKED_EXTENSIONS, _BLOCKED_NAMES
    from services.file_query_extensions import _SKIP_SEARCH_DIRS
    root = files.resolve_safe_path(path)
    if not root.exists():
        return
    if not root.is_dir():
        raise FileTargetError("RESOURCE_NOT_DIRECTORY", "请提供搜索目录")
    count = 0
    def error(exc):
        raise FileTargetError("RESOURCE_SEARCH_INCOMPLETE", "目录不可读取，无法确定唯一目标") from exc
    for directory, dirs, names in os.walk(root, onerror=error, followlinks=False):
        check()
        dirs[:] = sorted(d for d in dirs if not d.startswith(".") and d not in _SKIP_SEARCH_DIRS
                         and d not in _BLOCKED_NAMES and not (Path(directory) / d).is_symlink())
        for name in [*dirs, *sorted(names)]:
            check()
            count += 1
            if count > 20000:
                raise FileTargetError("RESOURCE_SEARCH_INCOMPLETE", "搜索超过上限，请提供目录限定路径")
            if name.startswith(".") or name in _BLOCKED_NAMES or Path(name).suffix.lower() in _BLOCKED_EXTENSIONS:
                continue
            target = files.resolve_safe_path(str(Path(directory) / name))
            if target.exists():
                yield target


@asynccontextmanager
async def source_snapshot(source: str, staging_dir: str):
    """Only immutable bytes are converted; a changed live source is rejected."""
    from services.workspace_coordination import finish_file_io
    source = Path(source)
    Path(staging_dir).mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".source-", dir=staging_dir) as directory:
        snapshot = Path(directory) / source.name
        def copy():
            before = file_version(source)
            digest = hashlib.sha256()
            with source.open("rb") as reader, snapshot.open("xb") as writer:
                while chunk := reader.read(1024 * 1024):
                    digest.update(chunk)
                    writer.write(chunk)
            if before != file_version(source):
                raise FileTargetError("RESOURCE_CHANGED", "源文件在复制期间发生变化")
            return digest.hexdigest()
        digest = await finish_file_io(copy)
        yield str(snapshot)
        if await finish_file_io(content_digest, source) != digest:
            raise FileTargetError("RESOURCE_CHANGED", "源文件在转换期间发生变化，请重新分析")


@dataclass(frozen=True)
class FileTarget:
    path: Path
    expected_version: tuple[int, ...] | None = None

    def validate(self):
        try:
            current = file_version(self.path)
        except FileNotFoundError as exc:
            raise FileTargetError("RESOURCE_NOT_FOUND", f"文件不存在: {self.path.name}") from exc
        if self.expected_version is not None and current != self.expected_version:
            raise FileTargetError("RESOURCE_CHANGED", "文件已变化，请重新搜索并确认")
        return current


def _encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode().rstrip("=")


def _decode(value: str) -> bytes:
    return base64.b64decode(value + "=" * (-len(value) % 4), altchars=b"-_", validate=True)


class FileReferenceCodec:
    prefix = "fref1_"

    def __init__(self, *, org_id, owner_id, scope="user"):
        from core.config import get_settings
        self.identity = [org_id, owner_id, scope]
        # Purpose separation: a resource reference cannot be used as a JWT.
        self.key = hmac.digest(get_settings().jwt_secret_key.encode(), b"workspace-resource-reference:v1", "sha256")

    def issue(self, relative_path: str, version: tuple[int, ...]) -> str:
        payload = json.dumps([*self.identity, relative_path, version], ensure_ascii=False, separators=(",", ":")).encode()
        return self.prefix + _encode(payload) + "." + _encode(hmac.digest(self.key, payload, "sha256"))

    def decode(self, value: str) -> tuple[str, tuple[int, ...]]:
        try:
            if not value.startswith(self.prefix) or len(value) > 16384:
                raise ValueError()
            body, signature = value[len(self.prefix):].split(".")
            payload = _decode(body)
            if not hmac.compare_digest(_decode(signature), hmac.digest(self.key, payload, "sha256")):
                raise ValueError()
            data = json.loads(payload)
            if (not isinstance(data, list) or len(data) != 5 or data[:3] != self.identity
                    or not isinstance(data[3], str) or not data[3]
                    or not isinstance(data[4], list) or len(data[4]) != 4
                    or any(type(part) is not int for part in data[4])):
                raise ValueError()
            return data[3], tuple(data[4])
        except (ValueError, TypeError, UnicodeError) as exc:
            from services.tools.resource_access import ResourceAccessError
            raise ResourceAccessError("RESOURCE_REFERENCE_INVALID",
                "文件引用无效或不属于当前工作区。请在当前获准范围重新搜索并复制引用，不要拼接或猜测。",
                recovery="search_again") from exc


class FileTargetResolver:
    def __init__(self, owner, files=None, *, scope="workspace", check=lambda: None, action="read"):
        from services.tools.resource_access import resource_boundary
        self.access = resource_boundary(owner)
        self.action = action
        from core.config import get_settings
        from services.file_executor import FileExecutor
        self.owner = owner
        self.files = files or FileExecutor(get_settings().file_workspace_root,
            getattr(owner, "workspace_user_id", owner.user_id), owner.org_id, create_root=False)
        self.root = Path(self.files.workspace_root)
        self.scope = scope
        self.check = check
        self.manifest = getattr(owner, "resource_manifest", None)
    @property
    def codec(self):
        return FileReferenceCodec(org_id=getattr(self.owner, "org_id", None),
            owner_id=getattr(self.owner, "workspace_user_id", self.owner.user_id),
            scope=getattr(self.owner, "context_scope", "user"))

    def guarded(self, value: str) -> Path:
        target = self.files.resolve_safe_path(value)
        relative = str(target.relative_to(self.root))
        self.access.require(self.action, relative, browse=self.action == "list")
        if self.scope != "workspace" and self.manifest is not None:
            if relative not in self.manifest.allowed_paths:
                from services.tools.resource_access import ResourceAccessError
                raise ResourceAccessError("RESOURCE_PATH_NOT_IN_MANIFEST",
                    "本次范围为 current（当前任务附件），目标不在附件中。若要操作工作区文件，请选择此前获准的搜索结果，"
                    "或明确请求 workspace 范围并由系统重新检查权限；不要换文件或改用 ID 猜测。",
                    recovery="select_scope", scope="current")
        return target

    def candidates(self) -> list[Path]:
        """Complete, bounded authorized enumeration; partial sets cannot select."""
        if self.scope != "workspace" and self.manifest is not None:
            paths = [self.guarded(a.workspace_path) for a in self.manifest.assets
                     if self.access.permits(self.action, a.workspace_path)]
            return sorted({p for p in paths if p.is_file()})
        return sorted({path for path in workspace_entries(self.files, check=self.check)
                       if self.access.permits(self.action, str(path.relative_to(self.root))) and path.is_file()})

    def _unique(self, paths, *, missing: str) -> Path:
        paths = sorted(set(paths))
        if len(paths) > 1:
            raise FileTargetError("RESOURCE_AMBIGUOUS", "找到多个候选，请使用完整相对路径选择", [str(p.relative_to(self.root)) for p in paths[:30]])
        if not paths:
            raise FileTargetError("RESOURCE_NOT_FOUND", f"未找到文件: {missing}")
        return paths[0]

    def resolve(self, value: str, *, allow_missing=False) -> FileTarget:
        from services.agent.file_id import compute_fid, is_valid_fid
        from services.agent.file_path_cache import get_file_cache, normalize_filename
        self.check()
        if not isinstance(value, str) or not value:
            raise FileTargetError("RESOURCE_TARGET_REQUIRED", "请提供文件名、路径或资源引用")
        if value.startswith(FileReferenceCodec.prefix):
            path, version = self.codec.decode(value)
            return FileTarget(self.guarded(path), version)
        if value.startswith("fid_"):
            if not is_valid_fid(value):
                raise FileTargetError("RESOURCE_REFERENCE_INVALID", "file_id 格式无效")
            paths = self.candidates()
            if allow_missing and self.manifest is not None:
                paths += [self.guarded(asset.workspace_path) for asset in self.manifest.assets
                          if self.access.permits(self.action, asset.workspace_path)]
            matches = {p for p in paths if value in {
                compute_fid(self.owner.org_id, str(p.relative_to(self.root))),
                compute_fid(self.owner.org_id, str(p)), compute_fid(self.owner.org_id, p.name)}}
            cache = get_file_cache(self.owner.conversation_id)
            for key, entry in cache.registered_paths():
                if entry.workspace and compute_fid(self.owner.org_id, key) == value:
                    matches.add(self.guarded(entry.workspace))
            return FileTarget(self._unique(matches, missing=value))
        # Explicit paths must never degrade to basename or fuzzy aliases.
        if os.path.dirname(value):
            return FileTarget(self.guarded(value))
        paths = self.candidates()
        exact = [p for p in paths if p.name == value]
        if exact:
            return FileTarget(self._unique(exact, missing=value))
        normalized = normalize_filename(value)
        matches = [p for p in paths if normalize_filename(p.name) == normalized]
        if not matches and not Path(value).suffix:
            stem = os.path.splitext(normalized)[0]
            matches = [p for p in paths if stem and stem in os.path.splitext(normalize_filename(p.name))[0]]
        if matches:
            return FileTarget(self._unique(matches, missing=value))
        if allow_missing:
            # Identity-only replay may refer to an already deleted file. This
            # path is never approved until prepare() verifies live existence.
            cache = get_file_cache(self.owner.conversation_id)
            old = {self.guarded(entry.workspace) for key, entry in cache.registered_paths()
                   if key == value and entry.workspace}
            missing = {path for path in old if not path.exists()}
            if len(missing) == 1:
                return FileTarget(missing.pop())
            return FileTarget(self.guarded(value))
        raise FileTargetError("RESOURCE_NOT_FOUND", f"未找到文件: {value}")

    def reference(self, path: Path) -> str:
        path = self.guarded(str(path))
        return self.codec.issue(str(path.relative_to(self.root)), file_version(path))
