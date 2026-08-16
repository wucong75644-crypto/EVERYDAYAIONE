"""Discover Skill metadata without loading Skill instructions into context."""

from __future__ import annotations

import hashlib
import html
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import yaml


_FRONTMATTER_LIMIT = 4096
_SKILL_FILE_LIMIT = 5 * 1024 * 1024
_MAX_SCAN_DEPTH = 5
_MAX_NAME_LENGTH = 64
_MAX_DESCRIPTION_LENGTH = 1024
_MAX_CATALOG_SKILLS = 50
_MAX_CATALOG_CHARS = 12_000
_NAME_PATTERN = re.compile(r"^[^/\\\x00-\x1f\x7f]+$")
SKILLS_DIRECTORY = "Skills"


@dataclass(frozen=True, kw_only=True)
class SkillMetadata:
    """The small metadata record exposed during progressive discovery."""

    name: str
    description: str
    when_to_use: str | None
    relative_path: str
    content_hash: str
    source: str


@dataclass(frozen=True, kw_only=True)
class SkillDiscoveryIssue:
    """A malformed or unsafe Skill that was ignored by discovery."""

    relative_path: str
    code: str


@dataclass(frozen=True, kw_only=True)
class SkillDiscoveryResult:
    """Deterministic discovery output; no Skill body is returned."""

    skills: tuple[SkillMetadata, ...]
    issues: tuple[SkillDiscoveryIssue, ...]


def discover_skill_metadata(
    root: str | Path,
    *,
    source: str = "workspace",
    max_depth: int = _MAX_SCAN_DEPTH,
) -> SkillDiscoveryResult:
    """Discover ``SKILL.md`` metadata below one trusted Skill root.

    The function only parses bounded frontmatter and hashes the file. It does
    not return the instruction body, references, scripts, or assets. A missing
    ``Skills`` directory is treated as an empty catalog so a new workspace
    does not need a bootstrap write.
    """
    if max_depth < 0:
        raise ValueError("SKILL_DISCOVERY_DEPTH_INVALID")

    root_path = Path(root).resolve()
    if not root_path.exists():
        return SkillDiscoveryResult(skills=(), issues=())
    if not root_path.is_dir():
        return SkillDiscoveryResult(
            skills=(),
            issues=(SkillDiscoveryIssue(relative_path=".", code="ROOT_NOT_DIRECTORY"),),
        )

    skills: list[SkillMetadata] = []
    issues: list[SkillDiscoveryIssue] = []
    for skill_file in sorted(root_path.rglob("SKILL.md"), key=lambda item: str(item)):
        relative_path = _relative_path(skill_file, root_path)
        if _directory_depth(relative_path) > max_depth:
            continue
        if _has_symlink_component(skill_file, root_path):
            issues.append(SkillDiscoveryIssue(
                relative_path=relative_path,
                code="SYMLINK_NOT_ALLOWED",
            ))
            continue
        try:
            metadata = _parse_skill_file(
                skill_file, relative_path=relative_path, source=source,
            )
        except _SkillMetadataError as error:
            issues.append(SkillDiscoveryIssue(
                relative_path=relative_path,
                code=error.code,
            ))
            continue
        skills.append(metadata)

    skills.sort(key=lambda item: (item.name, item.relative_path, item.source))
    issues.sort(key=lambda item: (item.relative_path, item.code))
    return SkillDiscoveryResult(skills=tuple(skills), issues=tuple(issues))


def workspace_skill_root(workspace_root: str | Path) -> Path:
    """Return the user-visible Skill directory below one workspace root."""
    return Path(workspace_root).resolve() / SKILLS_DIRECTORY


def discover_workspace_skill_metadata(
    workspace_root: str | Path,
    *,
    max_depth: int = _MAX_SCAN_DEPTH,
) -> SkillDiscoveryResult:
    """Discover personal or organization Skills from ``<workspace>/Skills``."""
    return discover_skill_metadata(
        workspace_skill_root(workspace_root),
        source="workspace",
        max_depth=max_depth,
    )


def render_skill_catalog(
    skills: Iterable[SkillMetadata],
    *,
    max_skills: int = _MAX_CATALOG_SKILLS,
    max_chars: int = _MAX_CATALOG_CHARS,
) -> str | None:
    """Render bounded Skill metadata for the first model context.

    The catalog contains paths and descriptions only. It does not grant
    permissions and it deliberately does not include the body of any Skill.
    """
    if max_skills <= 0 or max_chars <= 0:
        raise ValueError("SKILL_CATALOG_LIMIT_INVALID")
    entries: list[str] = []
    for skill in sorted(skills, key=lambda item: (item.name, item.relative_path)):
        if len(entries) >= max_skills:
            break
        path = skill.relative_path
        if skill.source == "workspace":
            path = f"{SKILLS_DIRECTORY}/{path}"
        when_to_use = (
            f"    <when_to_use>{html.escape(skill.when_to_use)}</when_to_use>\n"
            if skill.when_to_use else ""
        )
        entry = "".join((
            "  <skill>\n",
            f"    <name>{html.escape(skill.name)}</name>\n",
            f"    <description>{html.escape(skill.description)}</description>\n",
            when_to_use,
            f"    <path>{html.escape(path)}</path>\n",
            f"    <source>{html.escape(skill.source)}</source>\n",
            "  </skill>",
        ))
        candidate = "<available_skills>\n" + "\n".join(entries + [entry]) + "\n</available_skills>"
        if len(candidate) > max_chars:
            break
        entries.append(entry)
    if not entries:
        return None
    return (
        "<available_skills>\n"
        "  <!-- Metadata only; Skill content must be read through existing workspace tools. -->\n"
        + "\n".join(entries)
        + "\n</available_skills>"
    )


def discover_skill_metadata_from_roots(
    roots: Iterable[tuple[str | Path, str]],
    *,
    max_depth: int = _MAX_SCAN_DEPTH,
) -> SkillDiscoveryResult:
    """Merge deterministic metadata from ordered Skill roots.

    Source precedence and conflict policy are intentionally left to the caller;
    this phase only discovers files and keeps every candidate visible.
    """
    all_skills: list[SkillMetadata] = []
    all_issues: list[SkillDiscoveryIssue] = []
    for root, source in roots:
        result = discover_skill_metadata(root, source=source, max_depth=max_depth)
        all_skills.extend(result.skills)
        all_issues.extend(result.issues)
    all_skills.sort(key=lambda item: (item.name, item.source, item.relative_path))
    all_issues.sort(key=lambda item: (item.relative_path, item.code))
    return SkillDiscoveryResult(
        skills=tuple(all_skills), issues=tuple(all_issues),
    )


class _SkillMetadataError(ValueError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _parse_skill_file(
    path: Path, *, relative_path: str, source: str,
) -> SkillMetadata:
    try:
        size = path.stat().st_size
    except OSError as error:
        raise _SkillMetadataError("FILE_UNAVAILABLE") from error
    if size > _SKILL_FILE_LIMIT:
        raise _SkillMetadataError("SKILL_FILE_TOO_LARGE")

    try:
        with path.open("rb") as handle:
            prefix = handle.read(_FRONTMATTER_LIMIT)
            content_hash = _hash_stream(handle, prefix)
    except OSError as error:
        raise _SkillMetadataError("FILE_UNAVAILABLE") from error

    try:
        frontmatter = _extract_frontmatter(prefix)
    except UnicodeDecodeError as error:
        raise _SkillMetadataError("FRONTMATTER_INVALID_ENCODING") from error
    if frontmatter is None:
        raise _SkillMetadataError("FRONTMATTER_MISSING")
    try:
        document = yaml.safe_load(frontmatter)
    except yaml.YAMLError as error:
        raise _SkillMetadataError("FRONTMATTER_INVALID") from error
    if not isinstance(document, dict):
        raise _SkillMetadataError("FRONTMATTER_NOT_MAPPING")

    name = _bounded_text(document.get("name"), _MAX_NAME_LENGTH)
    description = _bounded_text(
        document.get("description"), _MAX_DESCRIPTION_LENGTH,
    )
    if name is None or not _NAME_PATTERN.fullmatch(name):
        raise _SkillMetadataError("NAME_INVALID")
    if description is None:
        raise _SkillMetadataError("DESCRIPTION_INVALID")
    when_to_use = _bounded_text(
        document.get("when_to_use"), _MAX_DESCRIPTION_LENGTH,
    )
    return SkillMetadata(
        name=name,
        description=description,
        when_to_use=when_to_use,
        relative_path=relative_path,
        content_hash=content_hash,
        source=source,
    )


def _extract_frontmatter(prefix: bytes) -> str | None:
    text = prefix.decode("utf-8", errors="strict")
    if not (text.startswith("---\n") or text.startswith("---\r\n")):
        return None
    lines = text.splitlines(keepends=True)
    if not lines:
        return None
    for index, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            return "".join(lines[1:index])
    return None


def _hash_stream(handle, prefix: bytes) -> str:
    digest = hashlib.sha256()
    digest.update(prefix)
    while chunk := handle.read(1024 * 1024):
        digest.update(chunk)
    return digest.hexdigest()


def _bounded_text(value: object, limit: int) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    if not normalized or len(normalized) > limit:
        return None
    return normalized


def _relative_path(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def _directory_depth(relative_path: str) -> int:
    return max(0, len(Path(relative_path).parts) - 1)


def _has_symlink_component(path: Path, root: Path) -> bool:
    current = path
    while current != root:
        if current.is_symlink():
            return True
        current = current.parent
    return root.is_symlink()
