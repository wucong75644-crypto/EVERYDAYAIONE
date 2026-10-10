"""Turn-owned Skill control state. Never dispatches business tools or grants IO."""

from __future__ import annotations

import asyncio
import json
from typing import Any, Literal

from pydantic import Field, ValidationError, field_serializer

from services.skills.contracts import Contract, Sha256, SkillError, SkillKey, RevisionKey
from services.skills.renderer import (
    MAX_ACTIVE_SKILLS, MAX_ARGS_BYTES, MAX_ARGUMENTS, MAX_DIRECTORY_BYTES,
    MAX_DIRECTORY_ENTRIES, MAX_BODY_BYTES, MAX_RENDERED_BYTES, MAX_TURN_RENDERED_BYTES,
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

    @field_serializer("effective_allowed_tool_names", when_used="json")
    def serialize_effective_allowed_tool_names(self, value: frozenset[str]) -> list[str]:
        return sorted(value)


class RuntimeCheckpoint(Contract):
    version: int = Field(default=1, ge=1, le=1)
    turn_id: str
    directory: tuple[SkillCandidate, ...] = Field(max_length=MAX_DIRECTORY_ENTRIES)
    active: tuple[ActiveSkill, ...] = Field(max_length=MAX_ACTIVE_SKILLS)
    effective_allowed_tool_names: frozenset[str]
    manual_skill_id: SkillKey | None = None
    session_skill_ids: tuple[SkillKey, ...] = Field(default=(), max_length=MAX_ACTIVE_SKILLS)
    context_version: Literal[1, 2] = 1
    scheduled_snapshot: dict | None = None

    @field_serializer("effective_allowed_tool_names", when_used="json")
    def serialize_effective_allowed_tool_names(self, value: frozenset[str]) -> list[str]:
        return sorted(value)


def control_result(code: str, *, ok: bool = False, **details) -> dict:
    return {"ok": ok, "code": code, **details}


class SkillRuntime:
    def __init__(self, *, turn_id: str, source, platform_tool_names, authorized_tool_names,
                 cancellation_event: asyncio.Event, template_context: dict | None = None,
                 execution_mode: str = "interactive", available_tool_names=None, maximum_body=MAX_BODY_BYTES,
                 maximum_rendered=MAX_RENDERED_BYTES, maximum_turn_rendered=MAX_TURN_RENDERED_BYTES):
        if min(maximum_body, maximum_rendered, maximum_turn_rendered) <= 0:
            raise ValueError("Skill byte budgets must be positive")
        self.maximum_body = maximum_body
        self.maximum_rendered = maximum_rendered
        self.maximum_turn_rendered = maximum_turn_rendered
        self.turn_id, self.source = turn_id, source
        self.execution_mode = execution_mode
        self.scheduled_snapshot = None
        self.platform_tool_names = frozenset(platform_tool_names)
        self.available_tool_names = (self.platform_tool_names if available_tool_names is None else
                                     self.platform_tool_names & frozenset(available_tool_names))
        # None is ToolContext's existing unrestricted ceiling, not missing identity.
        self._initial_ceiling = (self.platform_tool_names if authorized_tool_names is None
                                 else self.platform_tool_names & frozenset(authorized_tool_names))
        self._initial_ceiling &= self.available_tool_names
        self.effective_allowed_tool_names = self._initial_ceiling
        self.cancellation_event = cancellation_event
        self.directory: dict[str, SkillCandidate] = {}
        self.active: dict[str, ActiveSkill] = {}
        self.manual_skill_id: str | None = None
        self.session_skill_ids: tuple[str, ...] = ()
        self.template_context = dict(template_context or {})
        self.context_version = 2
        self.recommendation_batch = None

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
                         *, load_session_bindings: bool = True, discover_catalog: bool = True):
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
            candidates = await self.source.discover() if discover_catalog or selection else []
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

    async def initialize_scheduled(self, snapshot, checkpoint=None):
        from services.skills.scheduled import ScheduledSkillSnapshot, snapshot_ceiling
        parsed = ScheduledSkillSnapshot.parse(snapshot)
        self.scheduled_snapshot = parsed.model_dump(mode="json")
        self._initial_ceiling = snapshot_ceiling(snapshot, self._initial_ceiling)
        self.effective_allowed_tool_names = self._initial_ceiling
        if checkpoint is not None:
            if checkpoint.get("scheduled_snapshot") != self.scheduled_snapshot:
                raise SkillReplayError("SKILL_SCHEDULED_CHECKPOINT_MISMATCH")
            await self._restore(checkpoint)
        else:
            self.directory = {p.candidate.skill_key: p.candidate for p in parsed.skills}
        for pin in parsed.skills:
            if pin.candidate.skill_key not in self.active:
                result = await self.activate(encoded({"skill_id": pin.candidate.skill_key}), scheduled=True)
                if not result["ok"]:
                    raise SkillReplayError(result["code"])

    @property
    def allows_dynamic_activation(self):
        return self.execution_mode == "interactive"

    def messages(self) -> list[dict[str, str]]:
        messages = []
        advertised = [c for c in self.directory.values() if c.catalog_metadata.model_selectable and self.allows_dynamic_activation]
        if advertised:
            messages.append({"role": "system", "content": self._directory_text(advertised)})
        for active in self.active.values():
            messages.append(instruction_message(
                active, self.directory[active.skill_key],
                manual=active.skill_key == self.manual_skill_id, context_version=self.context_version,
                session=active.skill_key in self.session_skill_ids,
                scheduled=self.scheduled_snapshot is not None,
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
            context_version=self.context_version, scheduled_snapshot=self.scheduled_snapshot,
        ).model_dump(mode="json", exclude=(
            ({'manual_skill_id'} if self.manual_skill_id is None else set())
            | ({'context_version'} if self.context_version == 1 else set())
            | ({'session_skill_ids'} if not self.session_skill_ids else set())
            | ({'scheduled_snapshot'} if self.scheduled_snapshot is None else set())
        ))

    def model_messages(self, messages, tools):
        if self.recommendation_batch is not None and self.allows_dynamic_activation:
            suggestions = [{"skill_id": c.skill_id, "revision": c.revision,
                            "reasons": [r.model_dump(mode="json") for r in c.reasons]}
                           for c in self.recommendation_batch.candidates
                           if c.skill_id not in self.active and c.skill_id in self.directory
                           and self.directory[c.skill_id].revision == c.revision
                           and self.directory[c.skill_id].catalog_metadata.model_selectable]
            if suggestions:
                messages = list(messages)
                boundary = next((i for i, m in enumerate(messages) if m.get("role") != "system"), len(messages))
                messages.insert(boundary, {"role": "system", "content": (
                    "[Skill suggestions — metadata only]\n这些是可忽略的候选，不代表已加载或获得工具权限。"
                    "需要使用时仍须调用 activate_skill；不合适时继续普通流程。\n" + encoded(suggestions))})
        # Rebuild from current filtered schemas; do not persist stale capabilities.
        if self.context_version == 1:
            return messages
        if not self.has_active_skills:
            names = [tool['function']['name'] for tool in tools]
            advertised = [c for c in self.directory.values() if c.catalog_metadata.model_selectable]
            if not self.allows_dynamic_activation or ACTIVATE_SKILL not in names or not advertised:
                return messages
            catalog = self._directory_text(advertised)
            # The leading catalog can be overshadowed by history on "confirm".
            # Project current facts next to this request; never inherit a binding.
            content = ('[Current Skill selection]\n'
                       + encoded({'active_skills': [], 'available_tools': names})
                       + '\n当前轮尚未激活 Skill。历史“已启用”不是当前轮的激活状态。'
                       '当前新请求匹配目录中某个方法的用途时，先调用 activate_skill 读取正文，再执行业务工具。'
                       '用户确认或继续上一轮 Skill 任务时，先从下面当前目录选择匹配方法并调用 activate_skill，'
                       '读取正文后在下一轮按实际提供的 tools schema 处理当前请求。'
                       '准备或列出工具参数也应先加载该方法，不能从历史代码块猜参数。'
                       '新请求与旧方法无关时不继续旧任务；目录不授予工具权限。\n' + catalog)
            view = [message for message in messages
                    if message != {'role': 'system', 'content': catalog}]
            boundary = next((i for i in range(len(view) - 1, -1, -1)
                             if view[i].get('role') == 'user'), len(view))
            return [*view[:boundary], {'role': 'system', 'content': content}, *view[boundary:]]
        return model_messages(messages, tools, [
            {'skill_id': a.skill_key, 'revision': a.revision,
             'selection': ('scheduled' if self.scheduled_snapshot is not None else 'session' if a.skill_key in self.session_skill_ids
                           else 'user' if a.skill_key == self.manual_skill_id else 'model')}
            for a in self.active.values()
        ], [instruction_message(
            a, self.directory[a.skill_key], manual=a.skill_key == self.manual_skill_id,
            context_version=self.context_version,
            session=a.skill_key in self.session_skill_ids, scheduled=self.scheduled_snapshot is not None,
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

    async def activate(self, raw_arguments: str, *, manual: bool = False, session: bool = False, scheduled: bool = False) -> dict:
        result = await self._activate(raw_arguments, manual=manual, session=session, scheduled=scheduled)
        if (self.recommendation_batch is not None and not (manual or session or scheduled)
                and isinstance(raw_arguments, str) and len(raw_arguments) <= MAX_ARGS_BYTES + 256):
            try:
                request = json.loads(raw_arguments)
                key = request.get("skill_id") if isinstance(request, dict) else None
                candidate = self.directory.get(key) if isinstance(key, str) else None
            except (ValueError, TypeError):
                candidate = None
            if candidate is not None:
                from services.skills.recommendation_service import model_feedback
                await model_feedback(self, candidate, result["ok"])
        return result

    async def _activate(self, raw_arguments: str, *, manual: bool = False, session: bool = False, scheduled: bool = False) -> dict:
        self._check_cancelled()
        if not self.allows_dynamic_activation and not (scheduled and self.scheduled_snapshot is not None):
            return control_result("SKILL_SCHEDULED_ACTIVATION_FORBIDDEN")
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
                and not (scheduled and self.scheduled_snapshot is not None)
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
            remaining = self.maximum_turn_rendered - sum(len(a.rendered.encode('utf-8')) for a in self.active.values())
            maximum = min(self.maximum_rendered, remaining)
            ids, base, values = prepare_resources(validated, self.template_context, maximum,
                maximum_body=self.maximum_body, maximum_rendered=self.maximum_rendered)
            texts = await self.source.load_assets(candidate, validated, ids) if ids else {}
            self._check_cancelled()
            rendered = render_resources(validated, ids, base, values, texts, maximum)
            summary = ArgumentSummary.model_validate(argument_summary(values))
            bounded(rendered + "".join(a.rendered for a in self.active.values()),
                    self.maximum_turn_rendered, "SKILL_TURN_BUDGET_EXCEEDED")
            ceiling = skill_tool_ceiling(
                validated.catalog_metadata, self.platform_tool_names, self.effective_allowed_tool_names,
                available_tool_names=self.available_tool_names,
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
            if checkpoint.scheduled_snapshot != self.scheduled_snapshot:
                raise SkillError("SKILL_SCHEDULED_CHECKPOINT_MISMATCH")
            if checkpoint.turn_id != self.turn_id:
                raise SkillError("SKILL_REPLAY_TURN_MISMATCH")
            directory = {c.skill_key: c for c in checkpoint.directory}
            if len(directory) != len(checkpoint.directory) or any(
                not c.catalog_metadata.model_selectable and c.skill_key != checkpoint.manual_skill_id
                and c.skill_key not in checkpoint.session_skill_ids
                and self.scheduled_snapshot is None
                for c in directory.values()
            ):
                raise SkillError("SKILL_REPLAY_DIRECTORY_INVALID")
            if (len(set(checkpoint.session_skill_ids)) != len(checkpoint.session_skill_ids)
                    or not set(checkpoint.session_skill_ids) <= directory.keys()):
                raise SkillError("SKILL_REPLAY_DIRECTORY_INVALID")
            if self.scheduled_snapshot is not None:
                from services.skills.scheduled import ScheduledSkillSnapshot
                expected = {p.candidate.skill_key: p.candidate for p in ScheduledSkillSnapshot.parse(self.scheduled_snapshot).skills}
                if directory != expected or checkpoint.manual_skill_id or checkpoint.session_skill_ids:
                    raise SkillError("SKILL_SCHEDULED_CHECKPOINT_MISMATCH")
            bounded(self._directory_text(directory.values()), MAX_DIRECTORY_BYTES,
                    "SKILL_REPLAY_DIRECTORY_INVALID")
            bounded(encoded([c.model_dump(mode="json") for c in directory.values()]), 65_536,
                    "SKILL_REPLAY_DIRECTORY_INVALID")
            bounded(''.join(a.rendered for a in checkpoint.active), self.maximum_turn_rendered,
                    'SKILL_REPLAY_RENDER_INVALID')
            active = {}
            ceiling = self._initial_ceiling & checkpoint.effective_allowed_tool_names
            for saved in checkpoint.active:
                bounded(saved.rendered, self.maximum_rendered, 'SKILL_REPLAY_RENDER_INVALID')
                candidate = directory.get(saved.skill_key)
                if candidate is None or saved.skill_key in active or saved.revision != candidate.revision:
                    raise SkillError("SKILL_REPLAY_IDENTITY_INVALID")
                validated = await self.source.load(candidate, restoring=True)
                self._check_cancelled()
                self._validate_identity(candidate, validated)
                bounded(validated.body, self.maximum_body, "SKILL_REPLAY_RENDER_INVALID")
                if saved.body_sha256 != validated.body_sha256:
                    raise SkillError("SKILL_REPLAY_HASH_MISMATCH")
                if (saved.asset_manifest_sha256 != asset_manifest_digest(validated.resources)
                        or saved.loaded_asset_ids != referenced_assets(validated)):
                    raise SkillError('SKILL_REPLAY_ASSET_MISMATCH')
                if saved.rendered_sha256 != digest(saved.rendered):
                    raise SkillError("SKILL_REPLAY_RENDER_MISMATCH")
                bounded(saved.rendered, self.maximum_rendered, "SKILL_REPLAY_RENDER_INVALID")
                if saved.loaded_asset_ids:
                    if sum(a.bytes for a in validated.resources.assets if a.id in saved.loaded_asset_ids) > self.maximum_rendered:
                        raise SkillError('SKILL_REPLAY_RENDER_INVALID')
                    await self.source.load_assets(candidate, validated, saved.loaded_asset_ids)
                    self._check_cancelled()
                metadata = validated.catalog_metadata
                declared_ceiling = None
                if metadata.required_capabilities or metadata.allowed_capabilities:
                    declared_ceiling = skill_tool_ceiling(
                        metadata, self.platform_tool_names, self.platform_tool_names,
                    )
                elif metadata.tool_policy == 'restricted':
                    declared_ceiling = frozenset(metadata.allowed_tool_names)
                if (declared_ceiling is not None
                        and not saved.effective_allowed_tool_names <= declared_ceiling):
                    raise SkillError("SKILL_REPLAY_TOOL_SCOPE_INVALID")
                if metadata.required_capabilities:
                    skill_tool_ceiling(metadata, self.platform_tool_names, ceiling)
                # Platform policy has no Skill-level tool declaration. Its saved
                # ceiling is narrowed below by the current host authorization.
                if not checkpoint.effective_allowed_tool_names <= saved.effective_allowed_tool_names:
                    raise SkillError("SKILL_REPLAY_TOOL_SCOPE_INVALID")
                # Preserve the saved ceiling even if deployment now offers more tools.
                ceiling &= saved.effective_allowed_tool_names
                active[saved.skill_key] = saved
            bounded("".join(a.rendered for a in active.values()), self.maximum_turn_rendered,
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


def skill_budget_options(settings) -> dict:
    """Shared production budgets for interactive and scheduled execution."""
    return {
        "maximum_body": getattr(settings, "skill_max_body_bytes", 262_144),
        "maximum_rendered": getattr(settings, "skill_max_rendered_bytes", 393_216),
        "maximum_turn_rendered": getattr(settings, "skill_max_turn_rendered_bytes", 1_048_576),
    }


async def create_skill_runtime(*, handler, context, runtime, replay_context=None, selection=None, task_mode='smart', recommendations=True, explicit_only=False, intent=None, retry=False):
    """Feature gate precedes repository/storage construction, including old Actors."""
    from core.config import get_settings

    settings = get_settings()
    from services.skills.retry import parse_intent, PinnedIntentSource
    intent = parse_intent(intent) if intent is not None else None
    checkpoint = (replay_context or {}).get("skill_runtime")
    if checkpoint is not None and not isinstance(checkpoint, dict):
        raise SkillReplayError("SKILL_REPLAY_CHECKPOINT_INVALID")
    scheduled = getattr(context, "execution_mode", "interactive") in {"scheduled", "preflight"}
    from services.tools.spec import thaw
    snapshot = ((checkpoint or {}).get("scheduled_snapshot") if checkpoint is not None else
                thaw(context.authorization_snapshot).get("skill_revision_snapshot")) if scheduled else None
    if scheduled and checkpoint and snapshot is None and (checkpoint.get("active") or checkpoint.get("directory")):
        raise SkillReplayError("SKILL_SCHEDULED_CHECKPOINT_MISMATCH")
    enabled = settings.skill_runtime_enabled is True and settings.skill_catalog_enabled is True
    if not enabled or runtime is None:
        if intent is not None and intent.required:
            raise SkillBindingError('原任务的 Skill 当前不可用，无法按原方法重新生成。')
        if (snapshot and snapshot.get("skills")) or (checkpoint and (checkpoint.get("active") or checkpoint.get("session_skill_ids"))):
            raise SkillReplayError("SKILL_REPLAY_RUNTIME_DISABLED")
        return None
    from services.skills.runtime_source import ActorSkillSource
    from services.skills.capability_state import available_tool_names as policy_available_tool_names
    from services.tools.catalog import build_capability_catalog

    source = (ActorSkillSource(handler, context, settings, task_mode=task_mode)
              if task_mode != 'smart' else ActorSkillSource(handler, context, settings))
    if scheduled:
        from dataclasses import replace
        from services.skills.scheduled import ScheduledSkillSource, EMPTY_SNAPSHOT
        snapshot = snapshot if snapshot is not None else dict(EMPTY_SNAPSHOT)
        try:
            source = ScheduledSkillSource(handler, replace(context, execution_mode="scheduled"), settings, snapshot)
        except SkillError as exc:
            raise SkillReplayError(str(exc)) from None
    if intent is not None and not scheduled and checkpoint is None:
        if intent.task_mode != task_mode or intent.selected_skill != selection:
            raise SkillBindingError('原任务 Skill 的模式或版本记录不一致。')
        source = PinnedIntentSource(source, intent, retry=retry)
    workflow_pin = getattr(handler, "_ecom_workflow_pin", None)
    if workflow_pin and not scheduled and checkpoint is None:
        from services.agent.image.ecommerce_planner.workflow import WorkflowSkillSource
        source = WorkflowSkillSource(source, workflow_pin)
    capability_registry = build_capability_catalog()
    currently_authorized = policy_available_tool_names(capability_registry, context)
    state = SkillRuntime(
        turn_id=runtime.turn_id, source=source, execution_mode=context.execution_mode,
        **skill_budget_options(settings),
        platform_tool_names=(s.name for s in capability_registry.specs()),
        authorized_tool_names=currently_authorized,
        available_tool_names=currently_authorized,
        cancellation_event=runtime.cancellation_event,
        template_context={
            'actor_user_id': context.actor_user_id, 'org_id': context.org_id,
            'conversation_scope': context.context_scope, 'agent_domain': context.agent_domain,
            'execution_mode': context.execution_mode, 'is_channel': context.context_scope == 'channel',
        },
    )
    # A historical Actor checkpoint without Skill state predates bindings; it
    # must not acquire new session configuration while resuming that old Turn.
    if scheduled:
        try:
            await state.initialize_scheduled(snapshot, checkpoint)
        except SkillError as exc:
            raise SkillReplayError(str(exc)) from None
    else:
        await state.initialize(checkpoint, selection, load_session_bindings=not bool(replay_context),
                               **({"discover_catalog": False} if explicit_only else {}))
    if (recommendations and not scheduled and checkpoint is None and not replay_context
            and getattr(settings, "skill_recommendations_enabled", False) is True):
        from services.skills.recommendation_service import model_recommendations
        await model_recommendations(state)
    runtime.skill_runtime = state
    return state
