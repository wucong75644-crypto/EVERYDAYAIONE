"""Match and load only the Skills relevant to the current user request."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from services.agent.runtime.context.skill_discovery import (
    SkillMetadata,
    discover_workspace_skill_metadata,
    render_skill_catalog,
    workspace_skill_root,
)


_MAX_ACTIVE_SKILLS = 3
_MAX_SKILL_BODY_BYTES = 128 * 1024
_MAX_ACTIVE_CONTEXT_CHARS = 240_000
_ASCII_TERM = re.compile(r"[a-z0-9][a-z0-9._-]*")
_CJK_SPAN = re.compile(r"[\u3400-\u9fff]{2,}")


@dataclass(frozen=True, kw_only=True)
class ActivatedSkill:
    """One Skill body fixed for the current context build."""

    metadata: SkillMetadata
    content: str


@dataclass(frozen=True, kw_only=True)
class SkillContext:
    """Metadata catalog and selected instruction block for one request."""

    catalog: str | None
    instructions: str | None
    issue_count: int


def build_skill_context(
    workspace_root: str | Path,
    query: str,
) -> SkillContext:
    """Discover, select, and load Skills for one initial model context."""
    result = discover_workspace_skill_metadata(workspace_root)
    catalog = render_skill_catalog(result.skills)
    selected = select_skill_metadata(result.skills, query)
    active = []
    for metadata in selected:
        try:
            active.append(load_skill_content(
                workspace_skill_root(workspace_root), metadata,
            ))
        except SkillActivationError:
            continue
    return SkillContext(
        catalog=catalog,
        instructions=render_active_skill_instructions(active),
        issue_count=len(result.issues),
    )


class SkillActivationError(ValueError):
    """A selected Skill cannot be safely loaded for this context."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def select_skill_metadata(
    skills: Iterable[SkillMetadata],
    query: str,
    *,
    max_skills: int = _MAX_ACTIVE_SKILLS,
) -> tuple[SkillMetadata, ...]:
    """Select a small deterministic set from metadata, never from Skill bodies."""
    if max_skills <= 0:
        raise ValueError("SKILL_SELECTION_LIMIT_INVALID")
    query_text = query.strip().casefold()
    if not query_text:
        return ()
    explicit = [
        skill for skill in skills
        if skill.name.casefold() in query_text
    ]
    if explicit:
        explicit.sort(key=lambda item: (item.name, item.relative_path))
        return tuple(explicit[:max_skills])
    ranked: list[tuple[int, SkillMetadata]] = []
    query_terms = _terms(query_text)
    for skill in skills:
        score = _score_skill(skill, query_text, query_terms)
        if score > 0:
            ranked.append((score, skill))
    ranked.sort(key=lambda item: (-item[0], item[1].name, item[1].relative_path))
    return tuple(skill for _, skill in ranked[:max_skills])


def load_skill_content(
    skill_root: str | Path,
    metadata: SkillMetadata,
) -> ActivatedSkill:
    """Read one selected ``SKILL.md`` and verify its discovery hash."""
    root = Path(skill_root).resolve()
    path = (root / metadata.relative_path).resolve()
    try:
        path.relative_to(root)
    except ValueError as error:
        raise SkillActivationError("SKILL_PATH_OUTSIDE_ROOT") from error
    if _has_symlink_component(path, root):
        raise SkillActivationError("SYMLINK_NOT_ALLOWED")
    try:
        content = path.read_bytes()
    except OSError as error:
        raise SkillActivationError("SKILL_CONTENT_UNAVAILABLE") from error
    if len(content) > _MAX_SKILL_BODY_BYTES:
        raise SkillActivationError("SKILL_BODY_TOO_LARGE")
    if hashlib.sha256(content).hexdigest() != metadata.content_hash:
        raise SkillActivationError("SKILL_CONTENT_CHANGED")
    try:
        decoded = content.decode("utf-8")
    except UnicodeDecodeError as error:
        raise SkillActivationError("SKILL_CONTENT_INVALID_ENCODING") from error
    return ActivatedSkill(metadata=metadata, content=decoded)


def render_active_skill_instructions(
    skills: Iterable[ActivatedSkill],
    *,
    max_chars: int = _MAX_ACTIVE_CONTEXT_CHARS,
) -> str | None:
    """Render loaded Skill bodies as bounded, clearly delimited instructions."""
    if max_chars <= 0:
        raise ValueError("SKILL_CONTEXT_LIMIT_INVALID")
    blocks: list[str] = []
    for skill in skills:
        block = (
            f'<skill_instructions name="{_xml_escape(skill.metadata.name)}" '
            f'path="{_xml_escape(skill.metadata.relative_path)}">\n'
            f"{skill.content.rstrip()}\n"
            "</skill_instructions>"
        )
        candidate = "<active_skills>\n" + "\n".join(blocks + [block]) + "\n</active_skills>"
        if len(candidate) > max_chars:
            break
        blocks.append(block)
    if not blocks:
        return None
    return "<active_skills>\n" + "\n".join(blocks) + "\n</active_skills>"


def _score_skill(
    skill: SkillMetadata, query: str, query_terms: set[str],
) -> int:
    name = skill.name.casefold()
    if name in query:
        return 1000
    score = 0
    for value, weight in (
        (skill.name, 40),
        (skill.description, 8),
        (skill.when_to_use or "", 12),
    ):
        value_terms = _terms(value.casefold())
        score += weight * len(value_terms & query_terms)
    return score


def _terms(value: str) -> set[str]:
    terms = set(_ASCII_TERM.findall(value))
    for span in _CJK_SPAN.findall(value):
        terms.add(span)
        terms.update(span[index:index + 2] for index in range(len(span) - 1))
    return terms


def _has_symlink_component(path: Path, root: Path) -> bool:
    current = path
    while current != root:
        if current.is_symlink():
            return True
        current = current.parent
    return root.is_symlink()


def _xml_escape(value: str) -> str:
    return (
        value.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )
