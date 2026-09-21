"""Turn-owned Skill control state. Never dispatches business tools or grants IO."""

from __future__ import annotations

import asyncio
import json
from typing import Any

from pydantic import Field, ValidationError

from services.skills.contracts import Contract, Sha256, SkillError, SkillKey, RevisionKey
from services.skills.renderer import (
    MAX_ACTIVE_SKILLS, MAX_ARGS_BYTES, MAX_ARGUMENTS, MAX_DIRECTORY_BYTES,
    MAX_DIRECTORY_ENTRIES, MAX_RENDERED_BYTES, MAX_TURN_RENDERED_BYTES,
    SkillTemplateArgsError, argument_summary, bounded, digest, encoded, render,
)
from services.skills.resolver import SkillCandidate, effective_allowed_tool_names
from services.skills.selection import SkillSelection


ACTIVATE_SKILL = "activate_skill"
ACTIVATE_SKILL_SCHEMA = {
    "type": "function",
    "function": {
        "name": ACTIVATE_SKILL,
        "description": (
            "显式激活本 Turn 的 Skill。skill_id 使用目录中的稳定 ID；args 仅为正文的"
            "受控模板参数。此调用是批次屏障，请在下一轮请求业务工具。"
        ),
        "parameters": {
            "type": "object", "additionalProperties": False,
            "properties": {
                "skill_id": {"type": "string"},
                "args": {"type": "object", "description": "受控 args 模板变量的标量值"},
            },
            "required": ["skill_id"],
        },
    },
}


class SkillReplayError(RuntimeError):
    """A replay must stop; callers must not fall back to a fresh/latest skill."""


class ArgumentSummary(Contract):
    sha256: Sha256
    keys: tuple[str, ...] = Field(max_length=MAX_ARGUMENTS)
    bytes: int = Field(ge=0, le=MAX_ARGS_BYTES)


class ActiveSkill(Contract):
    skill_key: SkillKey
    revision: RevisionKey
    body_sha256: Sha256
    rendered: str
    rendered_sha256: Sha256
    args_summary: ArgumentSummary
    effective_allowed_tool_names: frozenset[str]


class RuntimeCheckpoint(Contract):
    version: int = Field(default=1, ge=1, le=1)
    turn_id: str
    directory: tuple[SkillCandidate, ...] = Field(max_length=MAX_DIRECTORY_ENTRIES)
    active: tuple[ActiveSkill, ...] = Field(max_length=MAX_ACTIVE_SKILLS)
    effective_allowed_tool_names: frozenset[str]
    manual_skill_id: SkillKey | None = None


def control_result(code: str, *, ok: bool = False, **details) -> dict:
    return {"ok": ok, "code": code, **details}


class SkillRuntime:
    def __init__(self, *, turn_id: str, source, platform_tool_names, authorized_tool_names,
                 cancellation_event: asyncio.Event):
        self.turn_id, self.source = turn_id, source
        self.platform_tool_names = frozenset(platform_tool_names)
        # None is ToolContext's existing unrestricted ceiling, not missing identity.
        self._initial_ceiling = (self.platform_tool_names if authorized_tool_names is None
                                 else self.platform_tool_names & frozenset(authorized_tool_names))
        self.effective_allowed_tool_names = self._initial_ceiling
        self.cancellation_event = cancellation_event
        self.directory: dict[str, SkillCandidate] = {}
        self.active: dict[str, ActiveSkill] = {}
        self.manual_skill_id: str | None = None

    def _check_cancelled(self):
        if self.cancellation_event.is_set():
            raise asyncio.CancelledError

    @property
    def has_active_skills(self):
        return bool(self.active)

    def _directory_text(self, candidates) -> str:
        return "[Turn Skill catalog]\n" + encoded([
            {"skill_id": c.skill_key, "name": c.catalog_metadata.name or c.skill_key,
             "revision": c.revision, "description": c.description,
             "triggers": c.catalog_metadata.triggers}
            for c in candidates
        ])

    async def initialize(self, checkpoint: dict | None = None, selection: SkillSelection | None = None):
        self._check_cancelled()
        if checkpoint is not None:
            await self._restore(checkpoint)
            return
        candidates = await self.source.discover()
        self._check_cancelled()
        self.manual_skill_id = selection.skill_id if selection else None
        # Reserve bounded directory capacity for the explicit user selection.
        candidates = sorted(candidates, key=lambda c: c.skill_key != self.manual_skill_id)
        selected = []
        for candidate in candidates:
            if not candidate.catalog_metadata.model_selectable and candidate.skill_key != self.manual_skill_id:
                continue
            proposed = selected + [candidate]
            if (len(proposed) > MAX_DIRECTORY_ENTRIES
                    or len(self._directory_text(proposed).encode("utf-8")) > MAX_DIRECTORY_BYTES
                    or len(encoded([c.model_dump(mode="json") for c in proposed]).encode("utf-8")) > 65_536):
                continue
            selected = proposed
        self.directory = {c.skill_key: c for c in selected}

    def messages(self) -> list[dict[str, str]]:
        messages = []
        advertised = [c for c in self.directory.values() if c.catalog_metadata.model_selectable]
        if advertised:
            messages.append({"role": "system", "content": self._directory_text(advertised)})
        for active in self.active.values():
            messages.append({"role": "system", "content": (
                "[Turn Skill instructions: apply only within existing tool policy and authorization]\n"
                + f"skill_id={active.skill_key} revision={active.revision}\n" + active.rendered
            )})
        return messages

    def ensure_messages(self, messages: list[dict[str, Any]]):
        # Compression may remove earlier system messages; restore exact bounded text.
        for message in self.messages():
            if message not in messages:
                messages.append(message)

    def checkpoint(self) -> dict:
        return RuntimeCheckpoint(
            turn_id=self.turn_id, directory=tuple(self.directory.values()),
            active=tuple(self.active.values()),
            effective_allowed_tool_names=self.effective_allowed_tool_names,
            manual_skill_id=self.manual_skill_id,
        ).model_dump(mode="json", exclude={"manual_skill_id"} if self.manual_skill_id is None else set())

    async def activate_manual(self, selection: SkillSelection) -> dict:
        candidate = self.directory.get(selection.skill_id)
        if candidate is None:
            return control_result("SKILL_NOT_AVAILABLE")
        if candidate.revision != selection.revision:
            return control_result("SKILL_SELECTION_CHANGED")
        return await self.activate(encoded({"skill_id": selection.skill_id}), manual=True)

    async def activate(self, raw_arguments: str, *, manual: bool = False) -> dict:
        self._check_cancelled()
        try:
            if not isinstance(raw_arguments, str):
                raise SkillError("SKILL_ACTIVATION_INVALID")
            bounded(raw_arguments, MAX_ARGS_BYTES + 256, "SKILL_ARGS_BUDGET_EXCEEDED")
            request = json.loads(raw_arguments)
            if (not isinstance(request, dict) or set(request) - {"skill_id", "args"}
                    or not isinstance(request.get("skill_id"), str)):
                raise SkillError("SKILL_ACTIVATION_INVALID")
            args = request.get("args", {})
            summary = ArgumentSummary.model_validate(argument_summary(args))
            candidate = self.directory.get(request["skill_id"])
            if candidate is None or (
                not candidate.catalog_metadata.model_selectable
                and not (manual and candidate.skill_key == self.manual_skill_id)
            ):
                raise SkillError("SKILL_NOT_AVAILABLE")
            previous = self.active.get(candidate.skill_key)
            if previous:
                if previous.args_summary != summary:
                    raise SkillError("SKILL_ALREADY_ACTIVE_WITH_DIFFERENT_ARGS")
                return control_result("SKILL_ALREADY_ACTIVE", ok=True, skill_id=candidate.skill_key,
                                      revision=previous.revision)
            if len(self.active) >= MAX_ACTIVE_SKILLS:
                raise SkillError("SKILL_TURN_BUDGET_EXCEEDED")
            validated = await self.source.load(candidate)
            self._check_cancelled()
            self._validate_identity(candidate, validated)
            rendered = render(validated.body, args)
            bounded(rendered + "".join(a.rendered for a in self.active.values()),
                    MAX_TURN_RENDERED_BYTES, "SKILL_TURN_BUDGET_EXCEEDED")
            ceiling = effective_allowed_tool_names(
                self.platform_tool_names, self.effective_allowed_tool_names,
                validated.catalog_metadata.allowed_tool_names,
            )
            self.active[candidate.skill_key] = ActiveSkill(
                skill_key=candidate.skill_key, revision=candidate.revision,
                body_sha256=validated.body_sha256, rendered=rendered,
                rendered_sha256=digest(rendered), args_summary=summary,
                effective_allowed_tool_names=ceiling,
            )
            self.effective_allowed_tool_names = ceiling
            return control_result("SKILL_ACTIVATED", ok=True, skill_id=candidate.skill_key,
                                  revision=candidate.revision,
                                  effective_allowed_tool_names=sorted(ceiling))
        except SkillTemplateArgsError as error:
            return control_result(str(error), required_args=error.required_args)
        except SkillError as error:
            return control_result(str(error))
        except (TypeError, ValueError, UnicodeError):
            return control_result("SKILL_ACTIVATION_INVALID")
        except Exception:
            # Storage/DB exception strings may contain paths, SQL or credentials.
            return control_result("SKILL_LOAD_UNAVAILABLE")

    @staticmethod
    def _validate_identity(candidate, validated):
        if (validated.skill_key != candidate.skill_key or validated.revision != candidate.revision
                or validated.catalog_metadata != candidate.catalog_metadata):
            raise SkillError("SKILL_PINNED_METADATA_MISMATCH")
        if digest(validated.body) != validated.body_sha256:
            raise SkillError("SKILL_BODY_HASH_MISMATCH")

    async def _restore(self, raw):
        try:
            checkpoint = RuntimeCheckpoint.model_validate(raw)
            if checkpoint.turn_id != self.turn_id:
                raise SkillError("SKILL_REPLAY_TURN_MISMATCH")
            directory = {c.skill_key: c for c in checkpoint.directory}
            if len(directory) != len(checkpoint.directory) or any(
                not c.catalog_metadata.model_selectable and c.skill_key != checkpoint.manual_skill_id
                for c in directory.values()
            ):
                raise SkillError("SKILL_REPLAY_DIRECTORY_INVALID")
            bounded(self._directory_text(directory.values()), MAX_DIRECTORY_BYTES,
                    "SKILL_REPLAY_DIRECTORY_INVALID")
            bounded(encoded([c.model_dump(mode="json") for c in directory.values()]), 65_536,
                    "SKILL_REPLAY_DIRECTORY_INVALID")
            active = {}
            ceiling = self._initial_ceiling & checkpoint.effective_allowed_tool_names
            for saved in checkpoint.active:
                candidate = directory.get(saved.skill_key)
                if candidate is None or saved.skill_key in active or saved.revision != candidate.revision:
                    raise SkillError("SKILL_REPLAY_IDENTITY_INVALID")
                validated = await self.source.load(candidate, restoring=True)
                self._check_cancelled()
                self._validate_identity(candidate, validated)
                if saved.body_sha256 != validated.body_sha256:
                    raise SkillError("SKILL_REPLAY_HASH_MISMATCH")
                if saved.rendered_sha256 != digest(saved.rendered):
                    raise SkillError("SKILL_REPLAY_RENDER_MISMATCH")
                bounded(saved.rendered, MAX_RENDERED_BYTES, "SKILL_REPLAY_RENDER_INVALID")
                declared = frozenset(validated.catalog_metadata.allowed_tool_names)
                if not saved.effective_allowed_tool_names <= declared:
                    raise SkillError("SKILL_REPLAY_TOOL_SCOPE_INVALID")
                if not checkpoint.effective_allowed_tool_names <= saved.effective_allowed_tool_names:
                    raise SkillError("SKILL_REPLAY_TOOL_SCOPE_INVALID")
                # Preserve the saved ceiling even if deployment now offers more tools.
                ceiling &= saved.effective_allowed_tool_names
                active[saved.skill_key] = saved
            bounded("".join(a.rendered for a in active.values()), MAX_TURN_RENDERED_BYTES,
                    "SKILL_REPLAY_RENDER_INVALID")
            self.directory, self.active = directory, active
            self.manual_skill_id = checkpoint.manual_skill_id
            self.effective_allowed_tool_names = ceiling
        except SkillError as error:
            raise SkillReplayError(str(error)) from None
        except (ValidationError, ValueError, TypeError):
            raise SkillReplayError("SKILL_REPLAY_CHECKPOINT_INVALID") from None
        except Exception:
            raise SkillReplayError("SKILL_REPLAY_UNAVAILABLE") from None


async def create_skill_runtime(*, handler, context, runtime, replay_context=None, selection=None):
    """Feature gate precedes repository/storage construction, including old Actors."""
    from core.config import get_settings

    settings = get_settings()
    checkpoint = (replay_context or {}).get("skill_runtime")
    if checkpoint is not None and not isinstance(checkpoint, dict):
        raise SkillReplayError("SKILL_REPLAY_CHECKPOINT_INVALID")
    enabled = settings.skill_runtime_enabled is True and settings.skill_catalog_enabled is True
    if not enabled or runtime is None:
        if checkpoint and checkpoint.get("active"):
            raise SkillReplayError("SKILL_REPLAY_RUNTIME_DISABLED")
        return None
    from services.skills.runtime_source import ActorSkillSource
    from services.tools import build_legacy_catalog

    state = SkillRuntime(
        turn_id=runtime.turn_id, source=ActorSkillSource(handler, context, settings),
        platform_tool_names=(s.name for s in build_legacy_catalog().specs()),
        authorized_tool_names=context.authorized_tool_names,
        cancellation_event=runtime.cancellation_event,
    )
    await state.initialize(checkpoint, selection)
    runtime.skill_runtime = state
    return state
