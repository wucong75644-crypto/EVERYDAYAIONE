"""Read and validate SKILL.md exclusively from the platform-managed NAS root."""

import errno
import hashlib
import hmac
import os
from pathlib import Path, PurePosixPath
import stat
from uuid import uuid4

import yaml
from pydantic import ValidationError
from yaml.events import AliasEvent

from services.skills.contracts import (
    PackageCreate, PublishRevision, SkillCatalogMetadata, SkillError, SkillPackage, ValidatedSkill,
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
    """Controlled immutable files, with exclusive creation for reviewed releases.

    No fallback to a user workspace or overwrite of an existing file is allowed.
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
        return self.validate_bytes(package, publication, self._read(path))

    @staticmethod
    def validate_bytes(package, publication: PublishRevision, raw: bytes) -> ValidatedSkill:
        path = revision_path(package, publication.revision)
        if len(raw) > MAX_SKILL_BYTES:
            raise SkillError("SKILL_FILE_TOO_LARGE")
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
        try:
            catalog_metadata = SkillCatalogMetadata.model_validate(metadata.get("catalog", {}))
        except ValidationError:
            raise SkillError("SKILL_CATALOG_METADATA_INVALID") from None
        body = "".join(lines[end + 1:])
        if not body.strip():
            raise SkillError("SKILL_BODY_EMPTY")
        body_hash = hashlib.sha256(body.encode("utf-8")).hexdigest()
        if not hmac.compare_digest(body_hash, publication.body_sha256):
            raise SkillError("SKILL_BODY_HASH_MISMATCH")
        return ValidatedSkill(path, package.skill_key, publication.revision,
                              content_hash, body_hash, summary.strip(), body, catalog_metadata)

    def publish(self, package, publication: PublishRevision, raw: bytes) -> ValidatedSkill:
        """Install a complete revision directory, never replace a published file.

        POSIX directory rename refuses to replace a nonempty revision directory.
        Crashes can leave only an ignored staging directory or a complete release;
        there is no final file with a temporary second hard link to impede retry.
        """
        validated = self.validate_bytes(package, publication, raw)
        parts = PurePosixPath(validated.nas_path).parts
        directory_fd = staging_fd = None
        temporary = f".publishing-{uuid4().hex}"
        staged = False
        try:
            directory_fd = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            for component in parts[:-2]:
                try:
                    os.mkdir(component, mode=0o750, dir_fd=directory_fd)
                    os.fsync(directory_fd)
                except FileExistsError:
                    pass
                child = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                                dir_fd=directory_fd)
                os.close(directory_fd)
                directory_fd = child
            os.mkdir(temporary, mode=0o750, dir_fd=directory_fd)
            staged = True
            staging_fd = os.open(temporary, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                                 dir_fd=directory_fd)
            fd = os.open("SKILL.md", os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                         0o640, dir_fd=staging_fd)
            with os.fdopen(fd, "wb") as stream:
                stream.write(raw)
                stream.flush()
                os.fchmod(stream.fileno(), 0o440)
                os.fsync(stream.fileno())
            os.fsync(staging_fd)
            try:
                os.rename(temporary, parts[-2], src_dir_fd=directory_fd, dst_dir_fd=directory_fd)
                staged = False
            except OSError as error:
                if error.errno not in (errno.EEXIST, errno.ENOTEMPTY):
                    raise
                # An identical orphan from a failed commit is a valid retry;
                # readback below rejects any different or unsafe existing file.
            os.fsync(directory_fd)
        except OSError as error:
            raise SkillError("SKILL_STORAGE_WRITE_REJECTED") from error
        finally:
            try:
                if staged and directory_fd is not None:
                    try:
                        pending = os.stat(temporary, dir_fd=directory_fd, follow_symlinks=False)
                    except FileNotFoundError:
                        pending = None
                    # A NAS rename may succeed but lose its acknowledgement.
                    # Never clean via an fd that now refers to the final release.
                    if pending is not None:
                        if not stat.S_ISDIR(pending.st_mode):
                            raise SkillError("SKILL_STORAGE_WRITE_REJECTED")
                        if staging_fd is not None:
                            opened = os.fstat(staging_fd)
                            if (pending.st_dev, pending.st_ino) != (opened.st_dev, opened.st_ino):
                                raise SkillError("SKILL_STORAGE_WRITE_REJECTED")
                            try:
                                os.unlink("SKILL.md", dir_fd=staging_fd)
                            except FileNotFoundError:
                                pass
                        os.rmdir(temporary, dir_fd=directory_fd)
            except OSError as error:
                raise SkillError("SKILL_STORAGE_WRITE_REJECTED") from error
            finally:
                if staging_fd is not None:
                    os.close(staging_fd)
                if directory_fd is not None:
                    os.close(directory_fd)
        return self.validate(package, publication)
