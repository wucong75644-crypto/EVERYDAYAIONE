"""Read and validate SKILL.md exclusively from the platform-managed NAS root."""

import hashlib
import hmac
import os
from pathlib import Path, PurePosixPath
import stat

import yaml
from yaml.events import AliasEvent

from services.skills.contracts import (
    PackageCreate, PublishRevision, SkillError, SkillPackage, ValidatedSkill,
    revision_path,
)

MAX_SKILL_BYTES = 1024 * 1024


class _FrontmatterLoader(yaml.SafeLoader):
    def compose_node(self, parent, index):
        if self.check_event(AliasEvent):
            raise SkillError("SKILL_FRONTMATTER_ALIAS_FORBIDDEN")
        return super().compose_node(parent, index)

    def construct_mapping(self, node, deep=False):
        mapping = {}
        for key_node, value_node in node.value:
            key = self.construct_object(key_node, deep=deep)
            if not isinstance(key, str) or key in mapping:
                raise SkillError("SKILL_FRONTMATTER_DUPLICATE_OR_INVALID_KEY")
            mapping[key] = self.construct_object(value_node, deep=deep)
        return mapping


class SkillStorage:
    """Read-only storage; publication never writes or overwrites a NAS package.

    The platform publisher owns this directory; backend processes only need read
    access. No fallback to a user workspace is permitted, even when misconfigured.
    """

    def __init__(self, root: str | None, *, workspace_root: str):
        if not root or not Path(root).is_absolute() or not Path(workspace_root).is_absolute():
            raise SkillError("SKILL_STORAGE_ROOT_INVALID")
        try:
            self.root = Path(root).resolve(strict=True)
            self.workspace_root = Path(workspace_root).resolve()
        except (OSError, RuntimeError) as error:
            raise SkillError("SKILL_STORAGE_ROOT_INVALID") from error
        if not self.root.is_dir() or (
            self.root.is_relative_to(self.workspace_root)
            or self.workspace_root.is_relative_to(self.root)
        ):
            raise SkillError("SKILL_STORAGE_WORKSPACE_OVERLAP")

    def _read(self, nas_path: str) -> bytes:
        relative = PurePosixPath(nas_path)
        if (not nas_path or "\\" in nas_path or "\x00" in nas_path
                or relative.is_absolute() or ".." in relative.parts
                or str(relative) != nas_path or relative.name != "SKILL.md"):
            raise SkillError("SKILL_PATH_INVALID")
        try:
            target = (self.root / nas_path).resolve(strict=True)
            if not target.is_relative_to(self.root) or target.is_relative_to(self.workspace_root):
                raise SkillError("SKILL_PATH_OUTSIDE_STORAGE")
            # realpath establishes containment; dir_fd + O_NOFOLLOW prevents a
            # directory/file symlink swap between validation and opening.
            directory_fd = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try:
                for component in relative.parts[:-1]:
                    child_fd = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                                       dir_fd=directory_fd)
                    os.close(directory_fd)
                    directory_fd = child_fd
                file_fd = os.open(relative.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                                  dir_fd=directory_fd)
                with os.fdopen(file_fd, "rb") as stream:
                    info = os.fstat(stream.fileno())
                    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                        raise SkillError("SKILL_FILE_NOT_REGULAR_OR_LINKED")
                    content = stream.read(MAX_SKILL_BYTES + 1)
            finally:
                os.close(directory_fd)
        except (OSError, RuntimeError) as error:
            raise SkillError("SKILL_STORAGE_READ_REJECTED") from error
        if len(content) > MAX_SKILL_BYTES:
            raise SkillError("SKILL_FILE_TOO_LARGE")
        return content

    def validate(
        self, package: SkillPackage | PackageCreate, publication: PublishRevision,
        *, nas_path: str | None = None,
    ) -> ValidatedSkill:
        expected_path = revision_path(package, publication.revision)
        path = nas_path if nas_path is not None else expected_path
        if path != expected_path:
            raise SkillError("SKILL_PATH_IDENTITY_MISMATCH")
        raw = self._read(path)
        content_hash = hashlib.sha256(raw).hexdigest()
        if not hmac.compare_digest(content_hash, publication.content_sha256):
            raise SkillError("SKILL_CONTENT_HASH_MISMATCH")
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError as error:
            raise SkillError("SKILL_UTF8_INVALID") from error
        lines = text.splitlines(keepends=True)
        if not lines or lines[0].rstrip("\r\n") != "---":
            raise SkillError("SKILL_FRONTMATTER_REQUIRED")
        end = next((i for i in range(1, len(lines)) if lines[i].rstrip("\r\n") == "---"), None)
        if end is None:
            raise SkillError("SKILL_FRONTMATTER_UNCLOSED")
        try:
            metadata = yaml.load("".join(lines[1:end]), Loader=_FrontmatterLoader)
        except SkillError:
            raise
        except (yaml.YAMLError, ValueError, TypeError, RecursionError) as error:
            raise SkillError("SKILL_FRONTMATTER_INVALID") from error
        if not isinstance(metadata, dict):
            raise SkillError("SKILL_FRONTMATTER_NOT_MAPPING")
        if metadata.get("skill_key") != package.skill_key or metadata.get("revision") != publication.revision:
            raise SkillError("SKILL_FRONTMATTER_IDENTITY_MISMATCH")
        summary = metadata.get("description")
        if not isinstance(summary, str) or not summary.strip() or len(summary) > 2000:
            raise SkillError("SKILL_DESCRIPTION_INVALID")
        body = "".join(lines[end + 1:])
        if not body.strip():
            raise SkillError("SKILL_BODY_EMPTY")
        body_hash = hashlib.sha256(body.encode("utf-8")).hexdigest()
        if not hmac.compare_digest(body_hash, publication.body_sha256):
            raise SkillError("SKILL_BODY_HASH_MISMATCH")
        return ValidatedSkill(path, package.skill_key, publication.revision,
                              content_hash, body_hash, summary.strip(), body)
