"""Prepare file operations once, before confirmation and invocation registration."""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from pathlib import Path

from services.file_resources import FileTarget, FileTargetError, FileTargetResolver, content_digest
from services.workspace_coordination import finish_file_io
from .spec import thaw

_active = ContextVar("prepared_file_operation", default=None)
FILE_TOOLS = frozenset({"file_search", "file_analyze", "file_delete", "restore_file"})


def active_file_call(name):
    value = _active.get()
    return value if value is not None and value.name == name else None


@dataclass
class PreparedFileCall:
    name: str
    resolver: FileTargetResolver
    arguments: dict
    targets: tuple[FileTarget, ...] = ()
    labels: tuple[str, ...] = ()
    record: dict | None = None
    versions: tuple = ()
    backup_etag: str | None = None
    check: object = field(default=lambda: None, repr=False)

    @property
    def write(self):
        return self.name in {"file_delete", "restore_file"}

    @property
    def binding(self):
        return {"files": self.versions, "restore": self.record, "backup_etag": self.backup_etag}

    async def _backup_version(self):
        from services.oss_service import get_oss_service
        metadata = await finish_file_io(get_oss_service().bucket.get_object_meta, self.record["oss_object_key"])
        etag = metadata.etag
        if not isinstance(etag, str) or not etag:
            raise FileTargetError("RESOURCE_VERSION_UNAVAILABLE", "无法核验备份版本，工具未执行")
        return etag.strip('"')

    def _inventory(self):
        from services.file_resources import workspace_entries
        resolver = self.resolver
        if resolver.manifest is not None and resolver.scope != "workspace":
            paths = [resolver.guarded(asset.workspace_path) for asset in resolver.manifest.assets]
        else:
            search_root = resolver.root
            if self.arguments.get("path"):
                selected = resolver.files.resolve_safe_path(self.arguments["path"])
                if selected.is_dir():
                    search_root = selected
            paths = [search_root, *workspace_entries(resolver.files, str(search_root), check=self.check)]
        values = []
        for path in sorted(set(paths)):
            self.check()
            try:
                st = path.stat()
                values.append((str(path), st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns))
            except FileNotFoundError:
                values.append((str(path), None))
        return tuple(values)

    async def prepare(self):
        if self.name == "restore_file":
            if self.record is None:
                raise FileTargetError("RESOURCE_NOT_FOUND", "未找到可恢复的删除记录")
            if self.targets[0].path.exists():
                raise FileTargetError("RESOURCE_DESTINATION_EXISTS", "恢复位置已有文件，未覆盖；请先处理目的地冲突")
            self.backup_etag = await self._backup_version()
            return
        if self.name == "file_search":
            # Search results are cacheable only for the same current inventory.
            self.versions = self._inventory()
            return
        values = []
        for target in self.targets:
            self.check()
            version = target.validate()
            if self.name == "file_analyze" and target.path.suffix.lower() not in {".xls", ".xlsx", ".csv", ".tsv"}:
                raise FileTargetError("RESOURCE_TYPE_UNSUPPORTED", "file_analyze 仅支持 Excel/CSV/TSV")
            digest = await finish_file_io(content_digest, target.path, self.check)
            values.append((str(target.path), version, digest))
        self.versions = tuple(values)

    async def verify(self):
        self.check()
        if self.name == "restore_file":
            current = await self.resolver.owner._find_deleted_record("", record_id=self.record["id"])
            if current != self.record:
                raise FileTargetError("RESOURCE_CHANGED", "删除记录已变化，工具未执行")
            if self.targets[0].path.exists():
                raise FileTargetError("RESOURCE_DESTINATION_EXISTS", "恢复位置已有文件，未覆盖")
            if await self._backup_version() != self.backup_etag:
                raise FileTargetError("RESOURCE_CHANGED", "备份版本已变化，工具未执行")
        elif self.name == "file_search":
            if self._inventory() != self.versions:
                raise FileTargetError("RESOURCE_CHANGED", "搜索范围内文件已变化，请重新搜索")
        else:
            for path, version, digest in self.versions:
                target = FileTarget(self.resolver.guarded(path), tuple(version))
                target.validate()
                if await finish_file_io(content_digest, target.path, self.check) != digest:
                    raise FileTargetError("RESOURCE_CHANGED", "文件内容已变化，请重新确认")
        self.check()

    @contextmanager
    def activate(self):
        token = _active.set(self)
        try:
            yield self
        finally:
            _active.reset(token)


def _values(value):
    return [value] if isinstance(value, str) else list(value or [])


def validate_selectors(arguments):
    from services.agent.file_id import is_valid_fid
    from services.file_resources import FileReferenceCodec
    for value in _values(arguments.get("file_ids")) + _values(arguments.get("file_id")):
        if not isinstance(value, str) or not is_valid_fid(value):
            raise FileTargetError("RESOURCE_REFERENCE_INVALID", "file_id 格式错误，请从搜索结果复制")
    for value in _values(arguments.get("resource_refs")) + _values(arguments.get("resource_ref")):
        if not isinstance(value, str) or not value.startswith(FileReferenceCodec.prefix):
            raise FileTargetError("RESOURCE_REFERENCE_INVALID", "resource_ref 格式无效，请从搜索结果复制")


def resolve_file_call(owner, name, arguments, *, files=None, check=lambda: None):
    if name not in FILE_TOOLS:
        return None
    args = thaw(arguments)
    validate_selectors(args)
    scope = args.get("scope") or ("current" if name in {"file_search", "file_analyze"} else "workspace")
    resolver = FileTargetResolver(owner, files, scope=scope, check=check)
    operation = PreparedFileCall(name, resolver, args, check=check)
    if name == "file_delete":
        references = _values(args.pop("resource_refs", None))
        legacy = _values(args.get("files")) + _values(args.pop("file_ids", None))
        targets = [resolver.resolve(value, allow_missing=True) for value in references or legacy]
        if references and legacy:
            old = {resolver.resolve(value, allow_missing=True).path for value in legacy}
            if old != {target.path for target in targets}:
                raise FileTargetError("RESOURCE_SELECTOR_CONFLICT", "资源引用和旧参数指向不同文件")
        if not targets:
            raise FileTargetError("RESOURCE_TARGET_REQUIRED", "file_ids、files 或 resource_refs 至少传一个")
        by_path = {}
        labels = {}
        for value, target in zip(references or legacy, targets):
            previous = by_path.get(target.path)
            if previous and previous.expected_version != target.expected_version:
                raise FileTargetError("RESOURCE_SELECTOR_CONFLICT", "同一文件的引用版本不一致")
            by_path[target.path] = target
            labels.setdefault(target.path, str(target.path) if value.startswith(("fid_", "fref1_")) else value)
        operation.targets = tuple(by_path.values())
        operation.labels = tuple(labels[path] for path in by_path)
        args["files"] = [str(target.path) for target in operation.targets]
    elif name == "file_analyze":
        values = [args.pop("resource_ref", None), args.pop("file_id", None), args.get("path")]
        targets = [resolver.resolve(value, allow_missing=True) for value in values if value]
        if not targets:
            raise FileTargetError("RESOURCE_TARGET_REQUIRED", "请提供 resource_ref、file_id 或 path")
        if len({target.path for target in targets}) != 1:
            raise FileTargetError("RESOURCE_SELECTOR_CONFLICT", "文件参数指向不同目标")
        operation.targets = (targets[0],)
        args["path"] = str(targets[0].path)
    elif name == "file_search":
        # Validate containment, but a name remains a search condition in Handler.
        if args.get("path") and Path(args["path"]).parent != Path("."):
            resolver.files.resolve_safe_path(args["path"])
        if resolver.manifest is not None and scope != "workspace":
            for asset in resolver.manifest.assets:
                resolver.guarded(asset.workspace_path)
    elif args.get("filename") and Path(args["filename"]).parent != Path("."):
        resolver.files.resolve_safe_path(args["filename"])
    return operation


async def resolve_restore_record(operation):
    if operation is None or operation.name != "restore_file":
        return
    args = operation.arguments
    if "record_id" in args and (type(args["record_id"]) is not int or args["record_id"] <= 0):
        raise FileTargetError("RESOURCE_REFERENCE_INVALID", "record_id 必须是删除记录的正整数 ID")
    record = await operation.resolver.owner._find_deleted_record(
        str(args.get("filename") or "").strip(), **({"record_id": args["record_id"]} if args.get("record_id") else {}))
    if record:
        filename = str(args.get("filename") or "").strip()
        if args.get("record_id") and filename and not (record["relative_path"] == filename or record["relative_path"].endswith("/" + filename)):
            raise FileTargetError("RESOURCE_SELECTOR_CONFLICT", "record_id 和 filename 指向不同删除记录")
        path = operation.resolver.guarded(str(Path(operation.resolver.files._workspace_base) / record["relative_path"]))
        operation.record = dict(record)
        operation.targets = (FileTarget(path),)
