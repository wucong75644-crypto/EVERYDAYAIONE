"""Turn-owned Skill control state. Never dispatches business tools or grants IO."""

from __future__ import annotations

import asyncio
import json
from typing import Any, Literal

from pydantic import Field, ValidationError

from services.skills.contracts import Contract, Sha256, SkillError, SkillKey, RevisionKey
from services.skills.renderer import (
    MAX_ACTIVE_SKILLS, MAX_ARGS_BYTES, MAX_ARGUMENTS, MAX_DIRECTORY_BYTES,
    MAX_DIRECTORY_ENTRIES, MAX_RENDERED_BYTES, MAX_TURN_RENDERED_BYTES,
    SkillTemplateArgsError, argument_summary, bounded, digest, encoded,
    asset_manifest_digest, prepare_resources, referenced_assets, render_resources,
)
from services.skills.resolver import SkillCandidate, skill_tool_ceiling
from services.skills.context import instruction_message, model_messages
from services.skills.selection import SkillSelection


ACTIVATE_SKILL = "activate_skill"
ACTIVATE_SKILL_SCHEMA = {
    "type": "function",
    "function": {
        "name": ACTIVATE_SKILL,
        "description": (
            "显式激活本 Turn 的 Skill。skill_id 使用目录中的稳定 ID；模板值由服务端提供。"
            "只影响本 Turn，不能创建、升级或移除会话固定绑定。"
            "此调用是批次屏障，请在下一轮请求业务工具。"
        ),
        "parameters": {
            "type": "object", "additionalProperties": False,
            "properties": {
                "skill_id": {"type": "string"},
            },
            "required": ["skill_id"],
        },
    },
}


class SkillReplayError(RuntimeError):
    """A replay must stop; callers must not fall back to a fresh/latest skill."""


class SkillBindingError(RuntimeError):
    """A mandatory session method cannot be skipped, including on discovery failure."""


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
    asset_manifest_sha256: Sha256 | None = None
    loaded_asset_ids: tuple[SkillKey, ...] = Field(default=(), max_length=16)


class RuntimeCheckpoint(Contract):
    version: int = Field(default=1, ge=1, le=1)
    turn_id: str
    directory: tuple[SkillCandidate, ...] = Field(max_length=MAX_DIRECTORY_ENTRIES)
    active: tuple[ActiveSkill, ...] = Field(max_length=MAX_ACTIVE_SKILLS)
    effective_allowed_tool_names: frozenset[str]
    manual_skill_id: SkillKey | None = None
    session_skill_ids: tuple[SkillKey, ...] = Field(default=(), max_length=MAX_ACTIVE_SKILLS)
    context_version: Literal[1, 2] = 1


def control_result(code: str, *, ok: bool = False, **details) -> dict:
    return {"ok": ok, "code": code, **details}


class SkillRuntime:
    def __init__(self, *, turn_id: str, source, platform_tool_names, authorized_tool_names,
                 cancellation_event: asyncio.Event, template_context: dict | None = None):
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
        self.session_skill_ids: tuple[str, ...] = ()
        self.template_context = dict(template_context or {})
        self.context_version = 2

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

    async def initialize(self, checkpoint: dict | None = None, selection: SkillSelection | None = None,
                         *, load_session_bindings: bool = True):
        self._check_cancelled()
        if checkpoint is not None:
            await self._restore(checkpoint)
            return
        try:
            bindings = await self.source.session_bindings() if load_session_bindings else []
        except Exception:
            raise SkillBindingError("无法加载会话固定的 Skill，请检查会话设置后重试。") from None
        self._check_cancelled()
        if len(bindings) > MAX_ACTIVE_SKILLS or len({c.skill_key for c in bindings}) != len(bindings):
            raise SkillBindingError("会话固定的 Skill 超过容量或配置无效，请检查会话设置。")
        self.session_skill_ids = tuple(c.skill_key for c in bindings)
        try:
            candidates = await self.source.discover()
        except Exception:
            if bindings:
                raise SkillBindingError("无法加载会话固定的 Skill，请检查会话设置后重试。") from None
            raise
        self._check_cancelled()
        self.manual_skill_id = selection.skill_id if selection else None
        # Reserve bounded directory capacity for the explicit user selection.
        candidates = sorted(candidates, key=lambda c: c.skill_key != self.manual_skill_id)
        selected = list(bindings)
        if (len(self._directory_text(selected).encode("utf-8")) > MAX_DIRECTORY_BYTES
                or len(encoded([c.model_dump(mode="json") for c in selected]).encode("utf-8")) > 65_536):
            raise SkillBindingError("会话固定的 Skill 超过容量，请减少绑定后重试。")
        for candidate in candidates:
            if candidate.skill_key in self.session_skill_ids:
                continue
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
            messages.append(instruction_message(
                active, self.directory[active.skill_key],
                manual=active.skill_key == self.manual_skill_id, context_version=self.context_version,
                session=active.skill_key in self.session_skill_ids,
            ))
        return messages

    def ensure_messages(self, messages: list[dict[str, Any]]):
        # Compression may remove earlier system messages; restore exact bounded text.
        missing = [message for message in self.messages() if message not in messages]
        if self.context_version == 1:
            messages.extend(missing)
        else:
            # Keep task instructions in the leading system context. Never split
            # an assistant/tool-result pair or mutate the user's original text.
            boundary = next((i for i, message in enumerate(messages)
                             if message.get('role') != 'system'), len(messages))
            messages[boundary:boundary] = missing

    def checkpoint(self) -> dict:
        return RuntimeCheckpoint(
            turn_id=self.turn_id, directory=tuple(self.directory.values()),
            active=tuple(self.active.values()),
            effective_allowed_tool_names=self.effective_allowed_tool_names,
            manual_skill_id=self.manual_skill_id,
            session_skill_ids=self.session_skill_ids,
            context_version=self.context_version,
        ).model_dump(mode="json", exclude=(
            ({'manual_skill_id'} if self.manual_skill_id is None else set())
            | ({'context_version'} if self.context_version == 1 else set())
            | ({'session_skill_ids'} if not self.session_skill_ids else set())
        ))

    def model_messages(self, messages, tools):
        # Rebuild from current filtered schemas; do not persist stale capabilities.
        if self.context_version == 1 or not self.has_active_skills:
            return messages
        return model_messages(messages, tools, [
            {'skill_id': a.skill_key, 'revision': a.revision,
             'selection': ('session' if a.skill_key in self.session_skill_ids
                           else 'user' if a.skill_key == self.manual_skill_id else 'model')}
            for a in self.active.values()
        ], [instruction_message(
            a, self.directory[a.skill_key], manual=a.skill_key == self.manual_skill_id,
            context_version=self.context_version,
            session=a.skill_key in self.session_skill_ids,
        ) for a in self.active.values()])

    async def activate_manual(self, selection: SkillSelection) -> dict:
        candidate = self.directory.get(selection.skill_id)
        if candidate is None:
            return control_result("SKILL_NOT_AVAILABLE")
        if candidate.revision != selection.revision:
            if candidate.skill_key in self.session_skill_ids:
                return control_result("SKILL_SESSION_REVISION_LOCKED")
            return control_result("SKILL_SELECTION_CHANGED")
        return await self.activate(encoded({"skill_id": selection.skill_id}), manual=True)

    async def activate_session(self, skill_id: str) -> dict:
        if skill_id not in self.session_skill_ids:
            raise SkillBindingError("会话 Skill 配置无效，请检查会话设置。")
        return await self.activate(encoded({"skill_id": skill_id}), session=True)

    async def activate(self, raw_arguments: str, *, manual: bool = False, session: bool = False) -> dict:
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
            argument_summary(args)
            if args:
                raise SkillError('SKILL_TEMPLATE_ARGS_SERVER_ONLY')
            candidate = self.directory.get(request["skill_id"])
            if candidate is None or (
                not candidate.catalog_metadata.model_selectable
                and not (manual and candidate.skill_key == self.manual_skill_id)
                and not (session and candidate.skill_key in self.session_skill_ids)
            ):
                raise SkillError("SKILL_NOT_AVAILABLE")
            previous = self.active.get(candidate.skill_key)
            if previous:
                return control_result("SKILL_ALREADY_ACTIVE", ok=True, skill_id=candidate.skill_key,
                                      revision=previous.revision)
            if len(self.active) >= MAX_ACTIVE_SKILLS:
                raise SkillError("SKILL_TURN_BUDGET_EXCEEDED")
            validated = await self.source.load(candidate)
            self._check_cancelled()
            self._validate_identity(candidate, validated)
            remaining = MAX_TURN_RENDERED_BYTES - sum(len(a.rendered.encode('utf-8')) for a in self.active.values())
            maximum = min(MAX_RENDERED_BYTES, remaining)
            ids, base, values = prepare_resources(validated, self.template_context, maximum)
            texts = await self.source.load_assets(candidate, validated, ids) if ids else {}
            self._check_cancelled()
            rendered = render_resources(validated, ids, base, values, texts, maximum)
            summary = ArgumentSummary.model_validate(argument_summary(values))
            bounded(rendered + "".join(a.rendered for a in self.active.values()),
                    MAX_TURN_RENDERED_BYTES, "SKILL_TURN_BUDGET_EXCEEDED")
            ceiling = skill_tool_ceiling(
                validated.catalog_metadata, self.platform_tool_names, self.effective_allowed_tool_names,
            )
            self.active[candidate.skill_key] = ActiveSkill(
                skill_key=candidate.skill_key, revision=candidate.revision,
                body_sha256=validated.body_sha256, rendered=rendered,
                rendered_sha256=digest(rendered), args_summary=summary,
                effective_allowed_tool_names=ceiling,
                asset_manifest_sha256=asset_manifest_digest(validated.resources), loaded_asset_ids=ids,
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
                and c.skill_key not in checkpoint.session_skill_ids
                for c in directory.values()
            ):
                raise SkillError("SKILL_REPLAY_DIRECTORY_INVALID")
            if (len(set(checkpoint.session_skill_ids)) != len(checkpoint.session_skill_ids)
                    or not set(checkpoint.session_skill_ids) <= directory.keys()):
                raise SkillError("SKILL_REPLAY_DIRECTORY_INVALID")
            bounded(self._directory_text(directory.values()), MAX_DIRECTORY_BYTES,
                    "SKILL_REPLAY_DIRECTORY_INVALID")
            bounded(encoded([c.model_dump(mode="json") for c in directory.values()]), 65_536,
                    "SKILL_REPLAY_DIRECTORY_INVALID")
            bounded(''.join(a.rendered for a in checkpoint.active), MAX_TURN_RENDERED_BYTES,
                    'SKILL_REPLAY_RENDER_INVALID')
            active = {}
            ceiling = self._initial_ceiling & checkpoint.effective_allowed_tool_names
            for saved in checkpoint.active:
                bounded(saved.rendered, MAX_RENDERED_BYTES, 'SKILL_REPLAY_RENDER_INVALID')
                candidate = directory.get(saved.skill_key)
                if candidate is None or saved.skill_key in active or saved.revision != candidate.revision:
                    raise SkillError("SKILL_REPLAY_IDENTITY_INVALID")
                validated = await self.source.load(candidate, restoring=True)
                self._check_cancelled()
                self._validate_identity(candidate, validated)
                if saved.body_sha256 != validated.body_sha256:
                    raise SkillError("SKILL_REPLAY_HASH_MISMATCH")
                if (saved.asset_manifest_sha256 != asset_manifest_digest(validated.resources)
                        or saved.loaded_asset_ids != referenced_assets(validated)):
                    raise SkillError('SKILL_REPLAY_ASSET_MISMATCH')
                if saved.rendered_sha256 != digest(saved.rendered):
                    raise SkillError("SKILL_REPLAY_RENDER_MISMATCH")
                bounded(saved.rendered, MAX_RENDERED_BYTES, "SKILL_REPLAY_RENDER_INVALID")
                if saved.loaded_asset_ids:
                    if sum(a.bytes for a in validated.resources.assets if a.id in saved.loaded_asset_ids) > MAX_RENDERED_BYTES:
                        raise SkillError('SKILL_REPLAY_RENDER_INVALID')
                    await self.source.load_assets(candidate, validated, saved.loaded_asset_ids)
                    self._check_cancelled()
                if (validated.catalog_metadata.tool_policy == 'restricted'
                        and not saved.effective_allowed_tool_names <= set(validated.catalog_metadata.allowed_tool_names)):
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
            self.session_skill_ids = checkpoint.session_skill_ids
            self.context_version = checkpoint.context_version
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
        if checkpoint and (checkpoint.get("active") or checkpoint.get("session_skill_ids")):
            raise SkillReplayError("SKILL_REPLAY_RUNTIME_DISABLED")
        return None
    from services.skills.runtime_source import ActorSkillSource
    from services.tools import build_legacy_catalog

    state = SkillRuntime(
        turn_id=runtime.turn_id, source=ActorSkillSource(handler, context, settings),
        platform_tool_names=(s.name for s in build_legacy_catalog().specs()),
        authorized_tool_names=context.authorized_tool_names,
        cancellation_event=runtime.cancellation_event,
        template_context={
            'actor_user_id': context.actor_user_id, 'org_id': context.org_id,
            'conversation_scope': context.context_scope, 'agent_domain': context.agent_domain,
            'execution_mode': context.execution_mode, 'is_channel': context.context_scope == 'channel',
        },
    )
    # A historical Actor checkpoint without Skill state predates bindings; it
    # must not acquire new session configuration while resuming that old Turn.
    await state.initialize(checkpoint, selection, load_session_bindings=not bool(replay_context))
    runtime.skill_runtime = state
    return state
