"""Trusted file access bounds, independent of model-selected search scope.

Selection history is a locator hint, never a grant. No new persistence format:
Actor may rebuild hints from its own completed tool-step inputs.
"""
from dataclasses import asdict, dataclass
from pathlib import PurePosixPath, Path
import math
import time


FILE_ACTIONS = {"file_search": "list", "file_analyze": "read",
                "file_delete": "delete", "restore_file": "restore"}


class ResourceAccessError(PermissionError):
    def __init__(self, code, message, *, recovery="stop", scope=None):
        self.code, self.recovery, self.scope = code, recovery, scope
        super().__init__(f"{code}: 工具未执行。{message}")


def relative_path(value):
    if not isinstance(value, str) or not value or "\\" in value:
        raise ValueError("Invalid resource boundary path")
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError("Resource boundary must be workspace relative")
    return str(path)


@dataclass(frozen=True)
class ResourceRule:
    actions: tuple[str, ...]
    paths: tuple[str, ...] = ()
    directories: tuple[str, ...] = ()

    def __post_init__(self):
        if not self.actions or not set(self.actions) <= set(FILE_ACTIONS.values()):
            raise ValueError("Invalid file actions")
        object.__setattr__(self, "actions", tuple(self.actions))
        for key in ("paths", "directories"):
            object.__setattr__(self, key, tuple(relative_path(p) for p in getattr(self, key)))


@dataclass(frozen=True)
class ResourceAccessBoundary:
    rules: tuple[ResourceRule, ...] = ()
    source: str = "unavailable"
    known: bool = False
    expires_at: float | None = None

    def __post_init__(self):
        if type(self.known) is not bool or any(not isinstance(r, ResourceRule) for r in self.rules):
            raise ValueError("Invalid resource boundary")
        if self.expires_at is not None and (type(self.expires_at) not in {int, float} or not math.isfinite(self.expires_at)):
            raise ValueError("Invalid resource authorization expiry")
        object.__setattr__(self, "rules", tuple(self.rules))

    @property
    def unavailable_reason(self):
        if not self.known:
            return "RESOURCE_AUTHORIZATION_UNAVAILABLE"
        if self.expires_at is not None and time.time() >= self.expires_at:
            return "RESOURCE_AUTHORIZATION_EXPIRED"
        return None

    def permits(self, action, path=None, *, browse=False):
        if self.unavailable_reason:
            return False
        for rule in self.rules:
            if action not in rule.actions:
                continue
            if path is None:
                if rule.paths or rule.directories:
                    return True
                continue
            target = PurePosixPath(relative_path(path))
            if str(target) in rule.paths or any(target.is_relative_to(d) for d in rule.directories):
                return True
            if browse and action == "list" and any(
                PurePosixPath(p).is_relative_to(target) for p in (*rule.paths, *rule.directories)
            ):
                return True
        return False

    def require(self, action, path=None, *, browse=False):
        if self.unavailable_reason:
            raise ResourceAccessError(self.unavailable_reason,
                "当前任务没有可验证的文件授权范围。请先指定获准资源；不要改 scope 或换文件重试。")
        if not self.permits(action, path, browse=browse):
            raise ResourceAccessError("RESOURCE_ACTION_DENIED",
                f"当前资源授权不允许此文件的 {action} 操作。搜索命中不代表获得该动作权限；请停止并确认授权。")

    def as_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, value):
        return cls(tuple(ResourceRule(tuple(r["actions"]), tuple(r["paths"]), tuple(r["directories"]))
                         for r in value["rules"]), value["source"], value["known"], value.get("expires_at"))


def resource_boundary(owner):
    explicit = getattr(owner, "resource_access_boundary", None)
    if explicit is not None:
        if not isinstance(explicit, ResourceAccessBoundary):
            raise ValueError("Resource boundary must be supplied by a trusted adapter")
        return explicit
    if getattr(owner, "execution_mode", "interactive") == "interactive":
        # Preserve existing interactive workspace eligibility. Runtime identity,
        # mode, business policy and dangerous confirmation still limit it.
        return ResourceAccessBoundary((ResourceRule(tuple(FILE_ACTIONS.values()), directories=(".",)),),
                                      "interactive_workspace", True)
    manifest = getattr(owner, "resource_manifest", None)
    if manifest is None:
        return ResourceAccessBoundary()
    return ResourceAccessBoundary((ResourceRule(("list", "read"),
        paths=tuple(a.workspace_path for a in manifest.assets)),), "scheduled_resources", True)


class ResourceSelections:
    def __init__(self):
        self.browses = set()

    def snapshot(self):
        value = ResourceSelections()
        value.browses = set(self.browses)
        return value

    def scope(self, owner, name, args, files):
        explicit = args.get("scope")
        if explicit is not None:
            if explicit not in {"current", "workspace"}:
                raise ResourceAccessError("RESOURCE_SCOPE_INVALID", "scope 只能是 current 或 workspace。",
                                          recovery="select_scope")
            return explicit
        if name not in {"file_search", "file_analyze"}:
            return "workspace"  # legacy write arguments/ledger hashes unchanged
        from services.file_resources import FileTargetResolver, FileReferenceCodec
        reference = args.get("resource_ref")
        if isinstance(reference, str) and reference.startswith(FileReferenceCodec.prefix):
            resolver = FileTargetResolver(owner, files)
            path, _ = resolver.codec.decode(reference)
            # A verified locator supplies identity only; current authorization
            # is always enforced later, including finite scheduled boundaries.
            relative = str(files.resolve_safe_path(path).relative_to(Path(files.workspace_root)))
            manifest = getattr(owner, "resource_manifest", None)
            return "current" if manifest is not None and relative in manifest.allowed_paths else "workspace"
        path = args.get("path")
        manifest = getattr(owner, "resource_manifest", None)
        if path and manifest is not None:
            relative = str(files.resolve_safe_path(path).relative_to(Path(files.workspace_root)))
            if relative in manifest.allowed_paths:
                return "current"
        if path:
            path = str(files.resolve_safe_path(path).relative_to(Path(files.workspace_root)))
        origins = set()
        for scope, directory in self.browses:
            if scope == "current" and manifest is not None:
                if not manifest.assets:
                    continue
                if path and not any(path == a.workspace_path or path == a.name
                                    or PurePosixPath(a.workspace_path).is_relative_to(path)
                                    for a in manifest.assets):
                    continue
            if not path or PurePosixPath(path).parent == PurePosixPath(".") or PurePosixPath(path).is_relative_to(directory):
                origins.add(scope)
        if len(origins) > 1:
            raise ResourceAccessError("RESOURCE_SCOPE_AMBIGUOUS",
                "本轮存在多个浏览范围，请明确选择 current（本轮附件）或 workspace（工作区）；没有扩大权限。",
                recovery="select_scope")
        return next(iter(origins), "current")

    def record(self, operation):
        if operation.name != "file_search":
            return
        if operation.browse_directory is not None:
            self.browses.add((operation.resolver.scope, operation.browse_directory))

    def restore(self, owner, records):
        """Only the authenticated caller's checkpoint blocks, never chat text."""
        import json
        from services.file_resources import FileTargetResolver
        files = FileTargetResolver(owner).files
        for block in records:
            if block.get("type") != "tool_step" or block.get("tool_name") != "file_search" or block.get("status") != "completed":
                continue
            try:
                args = json.loads(block["input"])
                # Only explicit original browsing scopes are recoverable from
                # old blocks; do not infer ordering among parallel completions.
                if not isinstance(args, dict) or args.get("scope") not in {"current", "workspace"}:
                    continue
                scope = self.scope(owner, "file_search", args, files)
                path = files.resolve_safe_path(args.get("path") or ".")
                # Restoring a locator hint performs no Handler or content IO.
                # Current permissions are rechecked before using the hint.
                relative = str(path.relative_to(Path(files.workspace_root)))
                if path.is_dir():
                    self.browses.add((scope, relative))
            except (KeyError, TypeError, ValueError, PermissionError):
                continue


def manifest_matches(manifest, args, access):
    """One selector implementation for preflight and the legacy search handler."""
    from fnmatch import fnmatch
    path = str(args.get("path") or "").strip()
    keyword = str(args.get("keyword") or "").strip().lower()
    pattern = str(args.get("file_pattern") or "").strip()
    assets = [a for a in manifest.assets if access.permits("list", a.workspace_path)]
    if path and path != ".":
        matched = [a for a in assets if path in {a.workspace_path, a.name}
                   or (path.endswith("/") and a.workspace_path.startswith(path))]
        if not matched and PurePosixPath(path).parent == PurePosixPath(".") and not path.endswith("/"):
            matched = [a for a in assets if path.lower() in a.name.lower()]
        assets = matched
    if keyword:
        assets = [a for a in assets if keyword in a.name.lower() or keyword in a.workspace_path.lower()]
    if pattern:
        assets = [a for a in assets if fnmatch(a.name, pattern) or fnmatch(a.workspace_path, pattern)]
    return assets
