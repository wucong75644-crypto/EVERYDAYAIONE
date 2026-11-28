"""Pinned read-only Skill resources; no business dispatch or authority expansion."""
import json
from services.tools.argument_validation import decode_tool_arguments

from services.skills.contracts import SkillError
from services.skills.renderer import (
    MAX_ARGS_BYTES, asset_header, bounded, deferred_assets, digest, referenced_assets,
    render_resources, server_arguments, argument_summary,
)

READ_SKILL_ASSET = 'read_skill_asset'
READ_SKILL_ASSET_SCHEMA = {'type': 'function', 'function': {
    'name': READ_SKILL_ASSET,
    'description': ('读取当前已激活 Skill 明确引用的按需附件，固定原版本并重新校验权限。'
                    '传 Skill 摘要中的 skill_id 和附件 asset_id，不能传路径或版本。'
                    '只在当前任务需要该资料时调用；本调用构成批次屏障。'
                    '加载后的完整正文出现在下一轮 Skill 指令中，回执不重复返回正文。'),
    'parameters': {'type': 'object', 'additionalProperties': False,
                   'required': ['skill_id', 'asset_id'],
                   'properties': {'skill_id': {'type': 'string'}, 'asset_id': {'type': 'string'}}},
}}


def validate_resource_snapshot(saved, validated, *, allow_deferred):
    referenced = referenced_assets(validated)
    if not saved.deferred_asset_ids:
        if saved.loaded_asset_ids != referenced:
            raise SkillError('SKILL_REPLAY_ASSET_MISMATCH')
        return
    deferred = deferred_assets(validated)
    eager = tuple(key for key in referenced if key not in deferred)
    loaded = saved.loaded_asset_ids
    if (not allow_deferred or saved.deferred_asset_ids != deferred
            or len(loaded) != len(set(loaded)) or loaded[:len(eager)] != eager
            or not set(loaded[len(eager):]) <= set(deferred)):
        raise SkillError('SKILL_REPLAY_ASSET_MISMATCH')


async def read_skill_asset(state, raw):
    state._check_cancelled()
    try:
        if not state.allow_deferred_assets or not state.allows_dynamic_activation:
            raise SkillError('SKILL_ASSET_READ_UNAVAILABLE')
        if not isinstance(raw, str):
            raise SkillError('SKILL_ASSET_REQUEST_INVALID')
        bounded(raw, MAX_ARGS_BYTES, 'SKILL_ARGS_BUDGET_EXCEEDED')
        request = decode_tool_arguments(raw)
        if (not isinstance(request, dict) or set(request) != {'skill_id', 'asset_id'}
                or not all(isinstance(value, str) for value in request.values())):
            raise SkillError('SKILL_ASSET_REQUEST_INVALID')
        key, asset_id = request['skill_id'], request['asset_id']
        active = state.active.get(key)
        if active is None or state._retired(key):
            raise SkillError('SKILL_NOT_ACTIVE')
        if asset_id not in active.deferred_asset_ids:
            raise SkillError('SKILL_ASSET_NOT_DECLARED')
        candidate = state.directory[key]
        # This resource belongs to an already active exact revision. Match
        # replay semantics for deprecated versions, while rechecking grants.
        validated = await state.source.load(candidate, restoring=True)
        state._check_cancelled()
        state._validate_identity(candidate, validated)
        if (validated.body_sha256 != active.body_sha256
                or deferred_assets(validated) != active.deferred_asset_ids):
            raise SkillError('SKILL_PINNED_METADATA_MISMATCH')
        from services.skills.renderer import asset_manifest_digest
        if asset_manifest_digest(validated.resources) != active.asset_manifest_sha256:
            raise SkillError('SKILL_ASSET_HASH_MISMATCH')
        if asset_id in active.loaded_asset_ids:
            return {'ok': True, 'code': 'SKILL_ASSET_ALREADY_LOADED', 'skill_id': key, 'asset_id': asset_id}
        # Saved template values cannot silently change after replay.
        values = server_arguments(validated.resources, state.template_context)
        if argument_summary(values) != active.args_summary.model_dump(mode='json'):
            raise SkillError('SKILL_TEMPLATE_SERVER_VALUE_UNAVAILABLE')
        others = sum(len(a.rendered.encode('utf-8')) for a in (*state.active.values(), *state.resident.values())
                     if a.skill_key != key)
        maximum = min(state.maximum_rendered, state.maximum_turn_rendered - others)
        entry = next(a for a in validated.resources.assets if a.id == asset_id)
        # Reject before expensive original-file reads; expansion is checked again below.
        if len(active.rendered.encode('utf-8')) + len(asset_header(entry).encode('utf-8')) + entry.bytes > maximum:
            raise SkillError('SKILL_ASSET_BUDGET_EXCEEDED')
        texts = await state.source.load_assets(candidate, validated, (asset_id,))
        state._check_cancelled()
        rendered = render_resources(validated, (asset_id,), active.rendered, values, texts, maximum)
        state.active = {**state.active, key: active.model_copy(update={
            'rendered': rendered, 'rendered_sha256': digest(rendered),
            'loaded_asset_ids': (*active.loaded_asset_ids, asset_id),
        })}
        return {'ok': True, 'code': 'SKILL_ASSET_LOADED', 'skill_id': key, 'asset_id': asset_id,
                'bytes': len(rendered.encode('utf-8')) - len(active.rendered.encode('utf-8'))}
    except SkillError as error:
        code = 'SKILL_ASSET_BUDGET_EXCEEDED' if str(error) == 'SKILL_RENDER_BUDGET_EXCEEDED' else str(error)
        return {'ok': False, 'code': code}
    except (TypeError, ValueError, UnicodeError, RecursionError):
        return {'ok': False, 'code': 'SKILL_ASSET_REQUEST_INVALID'}
    except Exception:
        return {'ok': False, 'code': 'SKILL_LOAD_UNAVAILABLE'}
